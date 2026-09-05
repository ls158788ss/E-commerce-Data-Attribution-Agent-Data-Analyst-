# -*- coding: utf-8 -*-
"""Agent 节点间的结构化 IO 模型（Pydantic v2）。

所有 LLM 输出都必须落到这些模型上，禁止裸字符串在节点间传递。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TimeRange(BaseModel):
    """规划器解析出的显式时间范围（ISO 日期，无法推断则为 null）。"""

    start_date: str | None = Field(None, description="起始日期 YYYY-MM-DD，无法推断时为 null")
    end_date: str | None = Field(None, description="结束日期 YYYY-MM-DD，无法推断时为 null")


class IntentResult(BaseModel):
    """Intent / Planner Agent 的输出。"""

    intent_type: Literal["lookup", "trend", "ranking", "compare", "diagnosis", "chitchat"] = Field(
        description="lookup=查数 trend=趋势 ranking=排名 compare=对比 diagnosis=归因分析 chitchat=闲聊/无关"
    )
    rewritten_question: str = Field(description="补全指代后的规范业务问题")
    metric_hints: list[str] = Field(default_factory=list, description="涉及的业务指标关键词，如 销售额/退款率/GMV")
    dimension_hints: list[str] = Field(default_factory=list, description="涉及的维度，如 地区/类目/颜色/尺码")
    target: str = Field(default="", description="分析对象，如 红色女装")
    time_range: TimeRange = Field(default_factory=TimeRange)
    is_data_question: bool = Field(description="是否是需要查询数据库的业务问题")


class SQLDraft(BaseModel):
    """SQL Agent 的生成结果。"""

    sql: str = Field(description="可执行的 DuckDB SQL（只读 SELECT）")
    explanation: str = Field(default="", description="一句话说明这条 SQL 在算什么")


class AnswerResult(BaseModel):
    """结果总结节点的输出。"""

    answer: str = Field(description="基于查询结果的中文结论，直接回答用户问题，引用具体数字")
    caveats: list[str] = Field(default_factory=list, description="数据口径或覆盖范围的提醒")


class ConclusionStep(BaseModel):
    """归因结论链中的一步。"""

    step: int = Field(description="推理顺序，从1开始")
    statement: str = Field(description="本步结论，需引用具体数字")
    evidence_ref: str = Field(default="", description="依据的数据来源说明（如：第2跳SKU拆解）")


class DiagnosisReport(BaseModel):
    """多跳下钻后的最终归因报告。"""

    conclusion_chain: list[ConclusionStep] = Field(description="按推理顺序排列的结论链")
    root_cause: str = Field(default="", description="一句话根因结论")
    caveats: list[str] = Field(default_factory=list, description="口径提醒与不确定性说明")
