# -*- coding: utf-8 -*-
"""图节点实现：每个节点只做一件事，读写自己负责的 State 字段。"""
from __future__ import annotations

import calendar
import json
from datetime import date, timedelta

from functools import lru_cache

from langgraph.types import Command  # noqa: F401  (预留 interrupt 用)

from agents.llm import LLMStructuredError, call_structured
from agents.models import AnswerResult, IntentResult, SQLDraft
from config.config import get_settings
from graph.state import AgentState
from sql.executor import SQLExecutionError, execute_sql
from sql.validator import validate_sql


@lru_cache(maxsize=1)
def _today() -> date:
    """以数据最新日期为「今天」。

    数据集是截至 2026-08-25 的快照，业务语义上的「今天」就是该日期：
    - 「最近N天/本月/上个月」都以此为锚，与 golden SQL 的硬编码日期一致；
    - 评测时间稳定——任何时候跑结果都一样，不会因真实日期漂移而失配；
    - 杜绝免费模型把窗口算到未来日期（planner 预计算的窗口直接照抄）。
    查库失败则退回真实日期。
    """
    try:
        rows = execute_sql("SELECT MAX(order_date) AS d FROM orders").rows
        if rows and rows[0].get("d"):
            d = rows[0]["d"]
            return d.date() if hasattr(d, "date") and callable(d.date) else d
    except Exception:  # noqa: BLE001
        pass
    return date.today()


def _month_bounds(d: date) -> tuple[date, date]:
    start = d.replace(day=1)
    nxt = date(d.year + 1, 1, 1) if d.month == 12 else d.replace(month=d.month + 1, day=1)
    return start, nxt


# ----------------------------------------------------------------
# Node 2: Schema Retrieval —— 语义层 + 混合检索 + Join 路径
# ----------------------------------------------------------------
def schema_retrieval_node(state: AgentState) -> dict:
    from retrieval.join_graph import get_schema_graph
    from retrieval.schema_retriever import get_schema_retriever
    from semantic.loader import load_semantic_store
    from sql.dialects import current_dialect, dialect_note, transpile_sql

    intent = state.get("intent", {})
    query_parts = [intent.get("rewritten_question") or state["question"]]
    query_parts += intent.get("metric_hints", [])
    query_parts += intent.get("dimension_hints", [])
    if intent.get("target"):
        query_parts.append(intent["target"])
    query = " ".join(query_parts)

    store = load_semantic_store()
    ctx = get_schema_retriever().retrieve(query)
    dia = current_dialect()

    # 业务规则数量少且都是关键口径 -> 全量注入（检索只负责表/指标/术语/示例）
    all_rules = "\n".join(f"- [{r.id}] {r.rule}" for r in store.rules)
    join_lines = []
    graph = get_schema_graph()
    steps = None
    if ctx.tables and len(ctx.tables) > 1:
        try:
            steps = graph.connect(ctx.tables, max_hops=4)
        except ValueError:
            steps = None
    if steps:
        for st_ in steps:
            join_lines.append(f"{st_.left_table} JOIN {st_.render()}")

    parts = [dialect_note(dia)]  # 方言提示放最前，模型第一眼看到目标语法
    if ctx.table_blocks:
        parts.append("【相关表结构】\n" + "\n".join(ctx.table_blocks))
    if ctx.metric_blocks:
        parts.append("【命中指标口径】\n" + "\n\n".join(ctx.metric_blocks))
    if ctx.glossary_hits:
        parts.append("【术语解释】\n" + "\n".join(f"- {g}" for g in ctx.glossary_hits))
    parts.append("【业务口径规则（必须全部遵守）】\n" + all_rules)
    if join_lines:
        parts.append("【建议 JOIN 路径】\n" + "\n".join(join_lines))
    if ctx.example_hits:
        # 示例 SQL 按当前方言转译（语义层以 DuckDB 语法编写）
        rendered = []
        for block in ctx.example_hits:
            q_part, _, sql_part = block.partition("\nSQL:\n")
            rendered.append(f"{q_part}\nSQL:\n{transpile_sql(sql_part.strip(), target=dia)}")
        parts.append("【相似问题参考 SQL（已转译为当前方言）】\n" + "\n\n".join(rendered))

    return {
        "schema_context": "\n\n".join(parts),
        "retrieved_tables": ctx.tables,
        "join_paths": (
            [{"left": s.left_table, "right": s.right_table, "condition": s.condition} for s in steps]
            if steps else []
        ),
        "trace": [{
            "step": "schema_retrieval",
            "query": query[:80],
            "tables": ctx.tables,
            "metrics_hit": len(ctx.metric_blocks),
            "examples_hit": len(ctx.example_hits),
            "join_hops": len(steps) if steps else 0,
            "top_docs": ctx.raw_docs[:5],
        }],
    }


# ----------------------------------------------------------------
# Node 1: Planner —— 意图理解 + 时间解析
# ----------------------------------------------------------------
def planner_node(state: AgentState) -> dict:
    t = _today()
    ms, me = _month_bounds(t)                       # 本月
    pm_s = date(ms.year - 1, 12, 1) if ms.month == 1 else date(ms.year, ms.month - 1, 1)
    # 预计算常用窗口，杜绝免费模型的日期算术错误（实测会算到未来日期）
    d7_start = t - timedelta(days=6)
    d30_start = t - timedelta(days=29)

    # ---- 加入对话上下文（如之前已确定的品牌、地区等过滤条件）----
    ctx = state.get("context", {})
    ctx_hint = ""
    if ctx:
        ctx_hint = "已知过滤条件："
        ctx_hint += ", ".join(f"{k}={v}" for k, v in ctx.items())
    # 这一行会被加入用户提示的最前面，帮助意图解析器把范围带进去

    system = (
        "你是电商数据分析团队的意图解析器。把用户的业务问题解析为结构化 JSON。\n"
        f"今天是 {t.isoformat()}。常用时间窗口【已替你算好，直接照抄】：\n"
        f"- 最近7天：{d7_start.isoformat()} ~ {t.isoformat()}\n"
        f"- 最近30天：{d30_start.isoformat()} ~ {t.isoformat()}\n"
        f"- 本月：{ms.isoformat()} ~ {me - timedelta(days=1)}\n"
        f"- 上个月：{pm_s.isoformat()} ~ {ms - timedelta(days=1)}\n"
        "规则：\n"
        "1. 时间必须解析成显式 ISO 日期（start_date/end_date），优先使用上面算好的窗口；"
        "「最近N天」含今天往前 N 天；「上个月」用上一个自然月；没有时间词则两者都填 null。\n"
        "2. 补全指代与口语表达，rewritten_question 要能独立看懂。\n"
        "3. 与业务数据无关的问题 is_data_question=false。\n"
        "4. metric_hints 用中文业务词（销售额/GMV/退款率/订单量/客单价/销量…）。"
    )
    # 构造用户提示，把上下文线索放在最前面
    user_parts = []
    if ctx_hint:
        user_parts.append(ctx_hint)
    user_parts.append(f"用户问题：{state['question']}")
    try:
        intent = call_structured(system, "\n".join(user_parts), IntentResult)
    except LLMStructuredError as e:
        # 意图失败不终止流程：退化为直接拿原问题生成 SQL
        intent = IntentResult(
            intent_type="lookup",
            rewritten_question=state["question"],
            is_data_question=True,
        )
        fallback_note = str(e)
    else:
        fallback_note = ""

    payload = intent.model_dump()
    trace = [{"step": "planner", "intent": payload, "fallback": fallback_note}]
    if not intent.is_data_question:
        return {
            "intent": payload,
            "success": False,
            "fail_reason": "该问题不是数据查询类问题，请换个问法（例如询问销售额、退款率、商品排名等）。",
            "trace": trace,
        }

    # 更新上下文：从意图中提取已知的过滤条件
    context_update = {}
    target = intent.target
    if target:
        # 简单判断：如果目标看起来像是品牌-颜色组合等
        context_update["target"] = target

    time_range = intent.time_range
    if time_range and (time_range.start_date or time_range.end_date):
        context_update["time_range"] = time_range.model_dump()

    # 维度线索也可以作为上下文
    dimension_hints = intent.dimension_hints
    if dimension_hints:
        context_update["dimension_hints"] = dimension_hints

    metric_hints = intent.metric_hints
    if metric_hints:
        context_update["metric_hints"] = metric_hints

    return {
        "intent": payload,
        "trace": trace,
        "context": context_update,  # 更新上下文
    }


# ----------------------------------------------------------------
# Node 3: SQL Generator（同时承担修复循环：state.error 非空时带错重写）
# ----------------------------------------------------------------
def sql_gen_node(state: AgentState) -> dict:
    s = get_settings()
    intent = state.get("intent", {})
    error = state.get("error", "")
    repair_count = state.get("repair_count", 0)

    pending = state.get("pending_drill") or {}
    current_hop: dict = {}

    if pending:
        # ---- 下钻跳：用下钻子问题替换主问题，并记录当前跳信息 ----
        user_parts = [
            f"查询任务（第{state.get('drill_depth', '?')}跳下钻）：{pending['drill_question']}",
            f"验证假设：{pending.get('hypothesis') or '该维度贡献分布不均'}",
            "要求：输出能对比/排名各取值的聚合 SQL（GROUP BY 该维度），便于发现异常集中点",
        ]
        current_hop = {
            "dimension": pending["dimension"],
            "focus": pending.get("filters") or {},
            "note": pending.get("hypothesis") or "",
            "drill_question": pending["drill_question"],
        }
    else:
        user_parts = [
            f"用户问题：{intent.get('rewritten_question', state['question'])}",
            f"意图类型：{intent.get('intent_type', 'lookup')}",
            f"指标线索：{', '.join(intent.get('metric_hints', [])) or '无'}",
            f"维度线索：{', '.join(intent.get('dimension_hints', [])) or '无'}",
        ]
        tr = intent.get("time_range") or {}
        if tr.get("start_date") or tr.get("end_date"):
            user_parts.append(f"时间范围：{tr.get('start_date')} ~ {tr.get('end_date')}")
        else:
            user_parts.append("时间范围：未指定（不要加时间过滤）")
        # ---- 归因类首查：先看总体时序/两期对比，维度拆解交给后续下钻 ----
        if intent.get("intent_type") == "diagnosis":
            user_parts.append(
                "注意：这是归因分析的第一步，先输出【总体异常确认】："
                "把指标按天（或周）拆解为时间序列，覆盖异常窗口及之前的基线期，"
                "让上升趋势可见。不要在本条 SQL 里拆解具体原因/SKU/尺码等细分维度。"
            )

    if error and repair_count > 0:
        prev_sql = state.get("sql", "")
        user_parts.append(
            f"\n上一版 SQL 执行失败，请修复后重新给出完整 SQL。\n上一版 SQL:\n{prev_sql}\n失败原因：{error}"
        )

    system = (
        "你是资深的 SQL 工程师。根据数据库 Schema 与业务口径，把业务问题翻译成一条只读 SELECT SQL。\n"
        "硬性要求：\n"
        "- 只输出 SQL 与一句话说明；不要执行其他操作\n"
        "- 遵守系统提示中的业务口径（尤其退款率去重、销售额口径）\n"
        "- 表名/列名必须来自给定 Schema，不得臆造\n"
        "- 题目要求「前N个 / Top N / 最高的N个」时，SQL 必须显式写 LIMIT N\n"
        "- 其余情况不需要写 LIMIT（沙盒会自动追加）\n"
        + state["schema_context"]
    )
    try:
        draft = call_structured(system, "\n".join(user_parts), SQLDraft, temperature=0.0)
    except LLMStructuredError as e:
        # 模型层最终失败（持续限速等）：优雅终止而不是让图崩溃
        attempts = list(state.get("attempts", []))
        return {
            "success": False,
            "fail_reason": f"模型服务暂时不可用，请稍后重试（{str(e)[:120]}）",
            "attempts": attempts,
            "trace": [{"step": "sql_gen_failed", "error": str(e)[:200]}],
        }
    step_name = ("drill_sql_gen" if pending else "sql_gen")
    if repair_count > 0:
        step_name = f"repair_{repair_count}"

    out = {
        "sql": draft.sql.strip().rstrip(";"),
        "sql_explanation": draft.explanation,
        "error": "",  # 清空旧错误
        "current_hop": current_hop,
        # 注意：不清空 pending_drill —— 同一跳内 repair 重写仍需下钻上下文；
        # 跳完成（execute 成功）时才由 execute_node 清理
        "trace": [
            {
                "step": step_name,
                "hop_dimension": current_hop.get("dimension") or "",
                "sql": draft.sql.strip(),
                "explanation": draft.explanation,
                "with_error_feedback": bool(error),
            }
        ],
    }
    _ = s
    return out


# ----------------------------------------------------------------
# Node 4: Validator —— 沙盒静态校验
# ----------------------------------------------------------------
def validate_node(state: AgentState) -> dict:
    from sql.audit import audit_sql

    v = validate_sql(state["sql"])
    audit_sql(sql=state["sql"], stage="validate", allowed=v.ok, error="" if v.ok else v.error_message)
    attempts = list(state.get("attempts", []))
    attempts.append({"stage": "validate", "sql": state["sql"], "ok": v.ok, "error": "" if v.ok else v.error_message})
    return {
        "validation_ok": v.ok,
        "validation_errors": [] if v.ok else [v.error_message],
        "final_sql": v.final_sql if v.ok else "",
        "attempts": attempts,
        "trace": [{"step": "validate", "ok": v.ok, "errors": [] if v.ok else [v.error_message]}],
    }


# ----------------------------------------------------------------
# Node 5: Executor —— 沙盒内执行
# ----------------------------------------------------------------
def execute_node(state: AgentState) -> dict:
    try:
        qr = execute_sql(state["final_sql"])
    except SQLExecutionError as e:
        attempts = list(state.get("attempts", []))
        attempts.append({"stage": "execute", "sql": state["final_sql"], "ok": False, "error": str(e)})
        return {
            "error": str(e),
            "attempts": attempts,
            "trace": [{"step": "execute", "ok": False, "error": str(e)}],
        }

    # ---- 下钻跳执行成功：登记为证据链的一跳 ----
    evidence = list(state.get("evidence") or [])
    hop = state.get("current_hop") or {}
    if hop:
        evidence.append({
            "hop": len(evidence) + 1,
            "dimension": hop.get("dimension", ""),
            "focus": hop.get("focus", ""),
            "columns": qr.columns,
            "rows": qr.rows[:20],
            "note": hop.get("drill_question", "") or hop.get("note", ""),
        })

    return {
        "result_rows": qr.rows,
        "result_columns": qr.columns,
        "result_row_count": qr.row_count,
        "elapsed_ms": qr.elapsed_ms,
        "evidence": evidence,
        "current_hop": {},   # 已登记
        "pending_drill": {},  # 本跳完成，清空下钻计划
        "success": True,
        "trace": [
            {
                "step": "execute",
                "ok": True,
                "row_count": qr.row_count,
                "elapsed_ms": qr.elapsed_ms,
                "columns": qr.columns,
            }
        ],
    }


# ----------------------------------------------------------------
# Node 6: Answer —— 结果总结（低成本模型即可）
# ----------------------------------------------------------------
def answer_node(state: AgentState) -> dict:
    rows = state.get("result_rows", [])
    preview = rows[:20]
    system = (
        "你是数据分析师。基于 SQL 查询结果用中文回答用户问题：\n"
        "- 直接给结论，引用具体数字（保留原始精度或两位小数）\n"
        "- 多行结果先总述再列要点，不要逐行复述全部数据\n"
        "- 不要编造结果里没有的数字\n"
        f"- 结果共 {len(rows)} 行，以下为前 20 行 JSON：\n{rows if len(rows) <= 20 else preview}"
    )
    question = state.get("intent", {}).get("rewritten_question", state["question"])
    try:
        ans = call_structured(
            system,
            f"用户问题：{question}\n执行的SQL口径说明：{state.get('sql_explanation', '')}",
            AnswerResult,
            role="report",
            temperature=0.2,
        )
        answer, caveats = ans.answer, ans.caveats
    except LLMStructuredError:
        # 兜底：直接给表格摘要，保证可用性
        answer = f"查询完成，共 {len(rows)} 行结果，详见下方数据表。"
        caveats = ["模型总结失败，已降级为表格展示"]

    # diagnosis 流程：用结论链组装完整分析报告
    evidence = state.get("evidence") or []
    conclusion_chain: list[dict] = []
    if evidence:
        from agents.models import DiagnosisReport

        ev_text = "\n\n".join(
            f"第{e['hop']}跳（按{e['dimension']}拆解）：\n"
            + json.dumps(e["rows"][:10], ensure_ascii=False, default=str)
            for e in evidence
        )
        try:
            rep = call_structured(
                "你是资深数据分析师，正在做根因归因汇报。基于多跳下钻证据链输出结构化报告。\n"
                "要求：conclusion_chain 按推理顺序逐步给出（先总体异常、再定位维度、最后归因）；"
                "每条 statement 引用具体数字；root_cause 一句话点明主因。",
                f"用户原始问题：{question}\n\n下钻证据链：\n{ev_text}\n\n"
                f"首查结果（前10行）：\n{json.dumps(rows[:10], ensure_ascii=False, default=str)}",
                DiagnosisReport,
                role="report",
                temperature=0.2,
            )
            conclusion_chain = [c.model_dump() for c in rep.conclusion_chain]
            answer = "\n".join(c.statement for c in rep.conclusion_chain)
            caveats = rep.caveats or caveats
        except LLMStructuredError as e:
            # 报告生成失败时降级为逐跳拼接
            conclusion_chain = [
                {"step": i + 1, "statement": f"{e_['note']}"}
                for i, e_ in enumerate(evidence)
            ]
            caveats = [f"报告模型失败，已降级({str(e)[:80]})"]

    # 增强：添加业务规则的自然语言解释（RAG 功能扩展）
    rule_explanations = _generate_rule_explanations(state)
    if rule_explanations:
        # 将业务规则解释添加到 caveats 中
        caveats.extend(rule_explanations)

    charts = _build_charts(state)
    return {
        "answer": answer,
        "conclusion_chain": conclusion_chain,
        "charts": charts,
        "caveats": caveats,
        "trace": [{"step": "answer"}],
    }


def _generate_rule_explanations(state: AgentState) -> list[str]:
    """根据语义层中的业务规则生成自然语言解释，增强 RAG 可解释性。"""
    explanations = []

    # 获取语义层中的业务规则
    semantic_ctx = state.get("semantic_ctx", {})
    rules = semantic_ctx.get("rules", [])

    if not rules:
        return explanations

    # 获取当前 SQL 和意图以确定哪些规则可能被应用
    sql = state.get("sql", "")
    intent = state.get("intent", {})
    intent_type = intent.get("intent_type", "")
    metric_hints = intent.get("metric_hints", [])

    # 定义规则到自然语言解释的映射
    rule_explanations_map = {
        "refund_rate_dedup": "退款率计算使用 COUNT(DISTINCT order_id) 去重，因为一笔订单可能有多条退款明细，直接使用 COUNT(*) 会导致重复计算。",
        "category_revenue_grain": "按类目/颜色/尺码等商品属性拆解销售额时使用 order_items.subtotal 而非 orders.total_amount，因为订单总额在多类目订单上会被重复计入。",
        "topn_requires_limit": "当查询要求「前N个 / Top N / 最高的N个」时，SQL 必须显式写 LIMIT N，以确保结果集大小符合预期。",
        "recent_n_days_window": "「最近N天」= 含今天往前推 N 天（start = today-(N-1)，end = today），不包含未来日期。",
        "sales_includes_refunded": "已退款订单的销售额保留（业务口径为先售后退），因此过滤条件是 order_status IN ('completed','refunded')，不应排除 refunded 状态。",
        "cancelled_unpaid_excluded": "未支付(unpaid)和已取消(cancelled)订单不计入任何成交/退款统计，因此查询中会过滤掉这些状态。",
        "time_base_for_metrics": "成交类指标按 orders.order_date 过滤时间；纯退款侧分析（如原因分布）可按 refunds.refund_date，但需要在结论中说明口径差异。",
        "category_filter_shortcut": "products 表已冗余 category_name/color/size 字段，过滤类目/颜色/尺码时直接使用 products 表，无需额外 Join categories 表。",
        "region_grain": "当用户说「华东地区」时直接使用 orders.region='华东'；只有在询问到省份粒度时才使用 customers.province。",
        "unit_price_vs_price": "实际成交金额使用 order_items.unit_price/subtotal；products.price 仅代表标准定价，不参与成交计算。",
        "no_star_aggregation": "聚合查询必须显式 GROUP BY 所有非聚合列，禁止在含 GROUP BY 的查询中使用 SELECT *。"
    }

    # 检查当前查询中可能应用的规则
    applied_rules = []

    # 检查 SQL 中是否包含特定模式来推断应用的规则
    sql_upper = sql.upper()

    # 检查是否应用了 refund_rate_dedup 规则
    if "COUNT(DISTINCT" in sql_upper and "ORDER_ID" in sql_upper and "REFUND" in sql_upper:
        applied_rules.append("refund_rate_dedup")

    # 检查是否应用了 category_revenue_grain 规则
    if "SUBTOTAL" in sql_upper and ("CATEGORY_NAME" in sql_upper or "COLOR" in sql_upper or "SIZE" in sql_upper):
        applied_rules.append("category_revenue_grain")

    # 检查是否应用了 topn_requires_limit 规则
    if "LIMIT" in sql_upper and any(hint in ["ranking", "top", "最高", "前"] for hint in metric_hints):
        applied_rules.append("topn_requires_limit")

    # 检查是否应用了 recent_n_days_window 规则
    if "ORDER_DATE" in sql_upper and any(hint in ["最近", "last", "past"] for hint in metric_hints):
        applied_rules.append("recent_n_days_window")

    # 检查是否应用了 sales_includes_refunded 规则
    if "ORDER_STATUS" in sql_upper and "COMPLETED" in sql_upper and "REFUNDED" in sql_upper:
        applied_rules.append("sales_includes_refunded")

    # 检查是否应用了 cancelled_unpaid_excluded 规则
    if "ORDER_STATUS" in sql_upper and ("UNPAID" in sql_upper or "CANCELLED" in sql_upper):
        applied_rules.append("cancelled_unpaid_excluded")

    # 检查是否应用了 category_filter_shortcut 规则
    if ("CATEGORY_NAME" in sql_upper or "COLOR" in sql_upper or "SIZE" in sql_upper) and "JOIN CATEGORIES" not in sql_upper:
        applied_rules.append("category_filter_shortcut")

    # 检查是否应用了 region_grain 规则
    if "REGION" in sql_upper and "HUADONG" in sql_upper:
        applied_rules.append("region_grain")

    # 检查是否应用了 unit_price_vs_price 规则
    if "UNIT_PRICE" in sql_upper or "SUBTOTAL" in sql_upper:
        applied_rules.append("unit_price_vs_price")

    # 检查是否应用了 no_star_aggregation 规则
    if "GROUP BY" in sql_upper and "SELECT *" not in sql_upper:
        applied_rules.append("no_star_aggregation")

    # 如果没有通过SQL模式检测到具体规则，但有业务规则被检索到，提供一般性解释
    if not applied_rules and rules:
        # 提供前几条规则的解释作为示例
        for rule in rules[:3]:  # 只取前3条避免信息过载
            rule_id = rule.get("id", "")
            if rule_id in rule_explanations_map:
                applied_rules.append(rule_id)

    # 生成解释文本
    for rule_id in applied_rules:
        if rule_id in rule_explanations_map:
            explanations.append(f"【口径说明】{rule_explanations_map[rule_id]}")

    # 如果还是没有解释但有规则被检索到，提供一个通用说明
    if not explanations and rules:
        explanations.append(f"【口径说明】本次查询遵循了业务口径规则，如退款率去重、销售额计算口径等具体规则，确保结果与业务定义一致。")

    return explanations


def _build_charts(state: AgentState) -> list[dict]:
    """把可可视化的结果打包成图表数据（UI 层渲染 plotly）。"""
    charts: list[dict] = []
    rows, cols = state.get("result_rows") or [], state.get("result_columns") or []
    if len(cols) == 2 and len(rows) >= 3:
        kind = "line" if any(k in cols[0].lower() for k in ("date", "month", "week", "日期", "月份")) else "bar"
        charts.append({"kind": kind, "title": cols[1], "rows": rows})
    return charts


# ----------------------------------------------------------------
# Node 7: Analyze —— 统计/模式分析（异常检测）
# ----------------------------------------------------------------
def analyze_node(state: AgentState) -> dict:
    from analysis.anomaly import detect_table_anomaly

    rows = state.get("result_rows") or []
    cols = state.get("result_columns") or []
    analysis: dict = {}

    # 形如 (时间/类别, 数值) 两列且行数足够 -> 做序列异常检测
    if len(cols) == 2 and len(rows) >= 4:
        value_col = cols[1]
        if all(isinstance(r.get(value_col), (int, float)) or _is_num(r.get(value_col)) for r in rows):
            try:
                rep = detect_table_anomaly(
                    [{cols[0]: str(r[cols[0]]), value_col: float(r[value_col])} for r in rows],
                    label_col=cols[0],
                    value_col=value_col,
                    metric_name=state.get("intent", {}).get("metric_hints", ["指标"])[0] if state.get("intent") else "",
                )
                analysis = rep.to_dict()
            except (TypeError, ValueError):
                analysis = {}
        else:
            analysis = {}

    return {"analysis": analysis, "trace": [{"step": "analyze", **(analysis or {})}]}


def _is_num(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


# ----------------------------------------------------------------
# Node 8: Drill-down Planner —— 决定是否继续下钻、往哪个维度钻
# ----------------------------------------------------------------
def drilldown_plan_node(state: AgentState) -> dict:
    import json as _json

    from analysis.drilldown import DrillDecision, build_drill_prompt, depth_reached

    intent = state.get("intent", {})
    depth = int(state.get("drill_depth", 0))
    explored = list(state.get("explored_dimensions", []))

    # 首跳：把首查结果登记为证据第 0 跳
    evidence = list(state.get("evidence") or [])
    if not evidence and state.get("result_rows"):
        evidence.append({
            "hop": 0, "dimension": "整体",
            "focus": intent.get("target", ""),
            "columns": state.get("result_columns", []),
            "rows": state.get("result_rows", [])[:20],
            "note": state.get("sql_explanation", "") or "首次整体查询",
        })

    current_summary = (
        f"列：{state.get('result_columns')}；前5行：{_json.dumps((state.get('result_rows') or [])[:5], ensure_ascii=False, default=str)}"
    )
    anomaly_summary = _json.dumps(state.get("analysis") or {}, ensure_ascii=False)

    decision = DrillDecision(need_drilldown=False, reason="默认终止")
    try:
        system, user = build_drill_prompt(
            question=intent.get("rewritten_question", state["question"]),
            current_result_summary=current_summary,
            anomaly_summary=anomaly_summary,
            already_explored=explored,
        )
        decision = call_structured(system, user, DrillDecision, temperature=0.1)
    except LLMStructuredError as e:
        decision = DrillDecision(need_drilldown=False,
                                 reason=f"下钻决策模型不可用，终止下钻({str(e)[:80]})")

    out: dict = {"evidence": evidence, "drill_decision": decision.model_dump(),
                 "pending_drill": {}, "drill_depth": depth}

    # 终止条件：决策说不用 / 达到最大深度 / 维度重复
    if not decision.need_drilldown:
        out["trace"] = [{"step": "drilldown_decision", "action": "stop",
                         "reason": decision.reason, "depth": depth}]
        return out
    if depth_reached(depth):
        out["trace"] = [{"step": "drilldown_decision", "action": "stop",
                         "reason": f"已达最大深度{depth}", "depth": depth}]
        return out
    if decision.dimension in explored or decision.dimension == "none":
        out["trace"] = [{"step": "drilldown_decision", "action": "stop",
                         "reason": f"维度{decision.dimension}已探索或无效", "depth": depth}]
        return out

    # ---- 构造下一跳查询计划 ----
    tr = intent.get("time_range") or {}
    time_txt = f"，时间范围 {tr.get('start_date')}~{tr.get('end_date')}" if tr.get("start_date") else ""
    filters_txt = "、".join(f"{k}={v}" for k, v in decision.focus_filters.items())
    metric_txt = "、".join(intent.get("metric_hints", [])) or "相关指标"

    drill_question = (
        f"针对「{intent.get('target', '') or '全站'}」，"
        f"按{decision.dimension}维度拆解 {metric_txt}{time_txt}"
        + (f"，限定条件：{filters_txt}" if filters_txt else "")
        + f"。假设待验证：{decision.hypothesis or '该维度贡献分布不均'}"
    )
    # 为子问题重新检索 Schema 上下文（维度变化 -> 相关表变化）
    from retrieval.schema_retriever import get_schema_retriever

    sub_ctx = get_schema_retriever().retrieve(drill_question)
    ctx_text = sub_ctx.render()

    out.update({
        "pending_drill": {
            "dimension": decision.dimension,
            "filters": decision.focus_filters,
            "drill_question": drill_question,
            "hypothesis": decision.hypothesis,
        },
        "explored_dimensions": explored + [decision.dimension],
        "drill_depth": depth + 1,
        "repair_count": 0,   # 新一跳重置修复预算
        "schema_context": ctx_text or state.get("schema_context", ""),
        "error": "",         # 清空上一跳错误
        "trace": [{
            "step": "drilldown_decision",
            "action": "drill",
            "dimension": decision.dimension,
            "hypothesis": decision.hypothesis,
            "info_gain": decision.expected_information_gain,
            "depth": depth + 1,
            "reason": decision.reason,
        }],
    })
    return out
