# -*- coding: utf-8 -*-
"""工作流装配：Multi-Agent Roles + Deterministic Workflow。

图拓扑：

    START → planner → schema_retrieval(语义层+混合检索+Join路径)
          → sql_gen → validate ─┬─ ok ─→ execute ─┬─ ok ─→ analyze
                                │                 │           │
                                │                 │      diagnosis?──否──→ answer → END
                                │                 │           │是
                                │                 │      drilldown_plan ──有下一跳──→ sql_gen（循环，深度≤3）
                                │                 │           └──无/终止──────────→ answer
                                ├─ fail/超限 → fail_notice → END
                                └─ 可修复 ←(repair)──┘

条件路由集中在 build_graph()，节点之间不互相调用。
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from config.config import get_settings
from graph.nodes import (
    analyze_node,
    answer_node,
    drilldown_plan_node,
    execute_node,
    planner_node,
    schema_retrieval_node,
    sql_gen_node,
    validate_node,
)
from graph.state import AgentState


def _route_after_validate(state: AgentState) -> str:
    if state.get("validation_ok"):
        return "execute"
    s = get_settings()
    if state.get("repair_count", 0) < s.sql_repair_max_attempts:
        return "repair"
    return "fail"


def _route_after_execute(state: AgentState) -> str:
    if state.get("success"):
        return "analyze"
    s = get_settings()
    if state.get("repair_count", 0) < s.sql_repair_max_attempts:
        return "repair"
    return "fail"


def _route_after_analyze(state: AgentState) -> str:
    """只有归因类问题进入下钻循环；普通查询直接出结论。"""
    intent = state.get("intent") or {}
    if intent.get("intent_type") == "diagnosis":
        return "drilldown_plan"
    return "answer"


def _route_after_drilldown(state: AgentState) -> str:
    if state.get("pending_drill"):
        return "sql_gen"
    return "answer"


def _repair_node(state: AgentState) -> dict:
    """修复包装：计数 +1 后复用生成节点（错误信息已回灌到 prompt）。"""
    out = sql_gen_node(state)
    out["repair_count"] = state.get("repair_count", 0) + 1
    return out


def _fail_node(state: AgentState) -> dict:
    reason = state.get("fail_reason") or (
        f"多次尝试后仍未能得到有效查询结果：{state.get('error') or '; '.join(state.get('validation_errors', []))}"
    )
    return {
        "success": False,
        "fail_reason": reason,
        "trace": [{"step": "fail", "reason": reason}],
    }


def build_graph():
    # 【技术栈：LangGraph】add_node 注册各角色节点，add_conditional_edges 实现条件路由
    # （校验失败→修复循环、归因类问题→下钻循环），节点之间禁止直接互调
    g = StateGraph(AgentState)
    g.add_node("planner", planner_node)
    g.add_node("schema_retrieval", schema_retrieval_node)
    g.add_node("sql_gen", sql_gen_node)
    g.add_node("validate", validate_node)
    g.add_node("execute", execute_node)
    g.add_node("analyze", analyze_node)
    g.add_node("drilldown_plan", drilldown_plan_node)
    g.add_node("repair", _repair_node)
    g.add_node("answer", answer_node)
    g.add_node("fail_notice", _fail_node)

    g.add_edge(START, "planner")

    # 非数据问题直接结束（planner 已写 success=False）
    def _route_after_planner(state: AgentState) -> str:
        intent = state.get("intent", {})
        if intent and not intent.get("is_data_question", True):
            return "fail"
        return "schema_retrieval"

    g.add_conditional_edges("planner", _route_after_planner,
                            {"schema_retrieval": "schema_retrieval", "fail": "fail_notice"})
    g.add_edge("schema_retrieval", "sql_gen")

    def _route_after_sqlgen(state: AgentState) -> str:
        # sql_gen 可能因模型服务不可用而优雅失败
        if state.get("success") is False and not state.get("sql"):
            return "fail"
        return "validate"

    g.add_conditional_edges("sql_gen", _route_after_sqlgen, {"validate": "validate", "fail": "fail_notice"})
    g.add_conditional_edges(
        "validate",
        _route_after_validate,
        {"execute": "execute", "repair": "repair", "fail": "fail_notice"},
    )
    g.add_conditional_edges(
        "execute",
        _route_after_execute,
        {"analyze": "analyze", "repair": "repair", "fail": "fail_notice"},
    )
    g.add_conditional_edges(
        "analyze",
        _route_after_analyze,
        {"drilldown_plan": "drilldown_plan", "answer": "answer"},
    )
    g.add_conditional_edges(
        "drilldown_plan",
        _route_after_drilldown,
        {"sql_gen": "sql_gen", "answer": "answer"},
    )
    g.add_edge("repair", "validate")
    g.add_edge("answer", END)
    g.add_edge("fail_notice", END)
    return g.compile()


_compiled = None


def get_app():
    """进程内单例（Streamlit session / FastAPI 复用）。"""
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled


def run_query(question: str) -> AgentState:
    """同步执行一次完整问答，返回最终 State。"""
    app = get_app()
    # 每跳约5个节点（gen→validate→execute→analyze→drilldown），4跳+首查 ≈ 25
    final: AgentState = app.invoke({"question": question}, {"recursion_limit": 50})
    return final
