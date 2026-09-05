# -*- coding: utf-8 -*-
"""LangGraph 全局状态定义。

State 是唯一的事实来源：节点只读入所需字段、写回自己负责的字段，
节点之间不允许直接互调 —— 所有流转由 workflow.py 的边和条件路由决定。
"""
from __future__ import annotations

from typing import Annotated, Any, TypedDict

# 【技术栈：LangGraph】Annotated + reducer 是 LangGraph 的状态合并机制：
# 指定 trace 字段在各节点间「追加合并」而非覆盖，配合 TypedDict(total=False)
# 实现每个节点只写自己负责的字段。
def merge_trace(left: list[dict], right: list[dict]) -> list[dict]:
    """trace 字段的 reducer：各节点追加事件而不是覆盖。"""
    return (left or []) + (right or [])


class AgentState(TypedDict, total=False):
    # ---- 输入 ----
    question: str

    # ---- 上下文（用于多轮对话，存储应继承的过滤条件如品牌、地区等）----
    context: dict[str, Any]  # e.g. {'category_name': '女装', 'color': '红色'} 或时间范围

    # ---- 规划 ----
    intent: dict[str, Any]          # IntentResult.model_dump()

    # ---- 上下文（语义层 + 混合检索结果）----
    schema_context: str             # 渲染后的检索上下文（表/口径/术语/规则/示例）
    retrieved_tables: list[str]     # 检索到的相关表
    join_paths: list[dict]          # Join Graph 给出的连接步骤 [{left,right,condition}]

    # ---- SQL 循环 ----
    sql: str                        # 当前候选 SQL
    sql_explanation: str
    validation_ok: bool
    validation_errors: list[str]
    error: str                      # 最近一次失败原因（修复循环回灌）
    repair_count: int               # 已修复次数
    attempts: list[dict]            # 每次 SQL 尝试审计记录 [{sql, ok, error, stage}]
    final_sql: str                  # 校验通过并执行成功的 SQL

    # ---- 结果 ----
    result_rows: list[dict]
    result_columns: list[str]
    result_row_count: int
    elapsed_ms: int

    # ---- 下钻分析（S4）----
    analysis: dict                  # AnomalyReport.to_dict()（时序证据）
    evidence: list[dict]            # 每一跳下钻的证据 [{dimension, focus, columns, rows, note}]
    drill_depth: int                # 已完成的下钻跳数（≤3）
    explored_dimensions: list[str]  # 已探索过的维度，防止重复
    pending_drill: dict             # 非空 = 下一跳计划 {dimension, filters, drill_question}
    drill_decision: dict            # 最近一次 DrillDecision

    # ---- 输出 ----
    answer: str
    conclusion_chain: list[dict]    # 结论链 [{step, statement}]（diagnosis 报告用）
    charts: list[dict]              # 图表数据 [{kind, title, rows}]
    caveats: list[str]
    success: bool                   # 整体是否成功拿到数据结论
    fail_reason: str                # success=False 时的人话解释

    # ---- 可观测 ----
    trace: Annotated[list[dict], merge_trace]
