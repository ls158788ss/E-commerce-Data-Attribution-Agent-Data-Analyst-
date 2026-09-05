# -*- coding: utf-8 -*-
"""智能问数 - Streamlit 三栏前端。

【技术栈：Streamlit】进程内直调 Agent（get_app().invoke()）的快速调试 UI；
前后端分离演示版见 frontend/（React + TypeScript，走 SSE）。

布局：
    左栏   数据源 / 表 / 指标清单（语义层）
    中间   聊天区（问题 → 结论 + SQL/结果表折叠面板）
    右栏   Agent Trace 实时执行轨迹

启动： streamlit run ui/streamlit_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.config import get_settings  # noqa: E402
from graph.workflow import get_app  # noqa: E402

st.set_page_config(page_title="智能问数 · Data Analyst Agent", page_icon="📊", layout="wide")


@st.cache_resource(show_spinner=False)
def load_app():
    return get_app()


@st.cache_resource(show_spinner=False)
def load_semantic_summary():
    from semantic.loader import load_semantic_store

    return load_semantic_store()


def render_trace_events(events: list[dict], container=None) -> None:
    """把 trace 事件渲染成步骤时间线。"""
    c = container or st
    for i, ev in enumerate(events, 1):
        step = ev.get("step", "?")
        if step == "planner":
            intent = ev.get("intent", {})
            tr = intent.get("time_range") or {}
            c.markdown(
                f"**{i}. 🧭 意图理解**  \n"
                f"类型 `{intent.get('intent_type')}` · 对象 {intent.get('target') or '—'} · "
                f"指标 {intent.get('metric_hints') or '—'}  \n"
                f"时间：{tr.get('start_date') or '不限'} ~ {tr.get('end_date') or '不限'}"
            )
        elif step == "schema_retrieval":
            c.markdown(
                f"**{i}. 🔎 Schema 检索** — 表：{'、'.join(ev.get('tables', [])[:6])} · "
                f"命中口径 {ev.get('metrics_hit')} 条 · 参考示例 {ev.get('examples_hit')} 个 · "
                f"JOIN 建议 {ev.get('join_hops')} 跳"
            )
        elif step in ("sql_gen", "drill_sql_gen") or step.startswith("repair"):
            if step.startswith("repair"):
                tag = f"🔧 SQL 修复 #{str(step).split('_')[1]}"
            elif step == "drill_sql_gen":
                tag = f"⚙️ 下钻 SQL（第…跳·{ev.get('hop_dimension') or ''}）"
            else:
                tag = "⚙️ SQL 生成"
            note = "（带错误反馈重写）" if ev.get("with_error_feedback") else ""
            c.markdown(f"**{i}. {tag}**{note}  \n{ev.get('explanation', '')}")
        elif step == "validate":
            icon = "✅" if ev.get("ok") else "❌"
            errs = "" if ev.get("ok") else "：" + "；".join(ev.get("errors", []))
            c.markdown(f"**{i}. 🛡️ SQL 校验** {icon}{errs}")
        elif step == "execute":
            if ev.get("ok"):
                c.markdown(f"**{i}. ⚡ 执行成功** — {ev.get('row_count')} 行 / {ev.get('elapsed_ms')}ms")
            else:
                c.markdown(f"**{i}. ⚡ 执行失败** — {ev.get('error')}")
        elif step == "analyze":
            detected = ev.get("detected")
            icon = "🚨" if detected else "📈"
            c.markdown(f"**{i}. {icon} 异常检测** — {ev.get('summary') or '未检出显著异常'}")
        elif step == "drilldown_decision":
            if ev.get("action") == "drill":
                c.markdown(
                    f"**{i}. 🕵️ 下钻决策 → {ev.get('dimension')}**（深度 {ev.get('depth')}，"
                    f"信息增益 {ev.get('info_gain')}）  \n假设：{ev.get('hypothesis') or '—'}"
                )
            else:
                c.markdown(
                    f"**{i}. 🕵️ 下钻决策 → 停止**（{ev.get('reason', '')}）"
                )
        elif step == "answer":
            c.markdown(f"**{i}. 💡 结论生成**")
        elif step == "fail":
            c.markdown(f"**{i}. 🛑 流程终止** — {ev.get('reason', '')}")


# ================= 左栏：数据资产 =================
with st.sidebar:
    s = get_settings()
    st.title("📊 数据资产")
    store = load_semantic_summary()

    with st.expander("**指标定义（语义层）**", expanded=True):
        for m in store.metrics:
            st.markdown(f"`{m.business_name}` — {m.definition[:40]}{'…' if len(m.definition) > 40 else ''}")

    with st.expander("**数据表**"):
        for t in store.tables:
            st.markdown(f"**{t.table}** · {len(t.columns)} 列  \n{t.description}")

    with st.expander("**业务术语**"):
        for gterm in store.glossary[:8]:
            st.markdown(f"`{gterm.term}` {gterm.definition[:36]}{'…' if len(gterm.definition) > 36 else ''}")

    st.divider()
    db_ok = (PROJECT_ROOT / "data" / "ecommerce.duckdb").exists()
    st.markdown(f"数据库：{'✅ 已就绪' if db_ok else '❌ 未生成'}")
    if not db_ok:
        st.code("python data/generate_data.py", language="bash")
    st.caption(f"模型：`{s.llm_model}`")

# ================= 右栏：Agent Trace =================
trace_col_holder = st.container()


# ================= 中间：聊天主区 =================
st.title("📊 智能问数 Data Analyst Agent")
st.caption("自然语言 → 业务理解 → Text2SQL → 沙盒校验 → 执行 → 结论")

if "messages" not in st.session_state:
    st.session_state.messages = []


def show_detail(detail: dict) -> None:
    sql, rows, cols = detail.get("sql", ""), detail.get("rows"), detail.get("columns")
    trace, caveats = detail.get("trace", []), detail.get("caveats", [])
    chain = detail.get("conclusion_chain") or []
    charts = detail.get("charts") or []
    evidence = detail.get("evidence") or []

    if trace:
        with st.expander("🧭 Agent 执行轨迹", expanded=bool(chain)):
            render_trace_events(trace)
    if chain:
        with st.expander("🧾 归因结论链", expanded=True):
            for step_info in chain:
                st.markdown(f"**{step_info.get('step')}.** {step_info.get('statement')}")
                if step_info.get("evidence_ref"):
                    st.caption(f"依据：{step_info['evidence_ref']}")
    for ch in charts:
        try:
            import plotly.express as px

            df = pd.DataFrame(ch["rows"])
            xcol, ycol = df.columns[0], df.columns[-1]
            fig = px.line(df, x=xcol, y=ycol) if ch["kind"] == "line" else px.bar(df, x=xcol, y=ycol)
            fig.update_layout(title=ch.get("title", ""), height=320,
                              font={"family": "Microsoft YaHei"})
            st.plotly_chart(fig, use_container_width=True)
        except Exception:  # noqa: BLE001  图表失败不影响主展示
            pass
    if sql:
        with st.expander("🔍 查看 SQL", expanded=False):
            st.code(sql, language="sql")
    if rows:
        with st.expander(f"📋 查询结果（{len(rows)} 行）", expanded=not chain):
            st.dataframe(pd.DataFrame(rows, columns=cols), use_container_width=True)
    for e in evidence[1:] if len(evidence) > 1 else []:
        with st.expander(f"🗂️ 下钻证据 · 第{e.get('hop')}跳（{e.get('dimension')}）", expanded=False):
            df = pd.DataFrame(e["rows"], columns=e["columns"]) if e["rows"] else pd.DataFrame()
            st.dataframe(df, use_container_width=True)
            if e.get("note"):
                st.caption(e["note"])
    if caveats:
        st.info("口径提醒：" + "；".join(caveats))


for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and msg.get("detail"):
            show_detail(msg["detail"])

if question := st.chat_input("请输入你的业务问题…"):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Agent 分析中…"):
            try:
                final = load_app().invoke({"question": question}, {"recursion_limit": 30})
            except Exception as e:  # noqa: BLE001
                final = {"success": False, "fail_reason": f"系统异常：{e}", "trace": []}

        answer = final["answer"] if final.get("success") else f"⚠️ {final.get('fail_reason', '未能完成本次分析')}"
        st.markdown(answer)

        if final.get("success"):
            detail = {
                "sql": final.get("final_sql") or final.get("sql", ""),
                "rows": final.get("result_rows", []),
                "columns": final.get("result_columns", []),
                "trace": final.get("trace", []),
                "caveats": final.get("caveats", []),
                "conclusion_chain": final.get("conclusion_chain", []),
                "charts": final.get("charts", []),
                "evidence": final.get("evidence", []),
            }
        else:
            detail = {"trace": final.get("trace", [])}
        show_detail(detail)

    st.session_state.messages.append({"role": "assistant", "content": answer, "detail": detail})
