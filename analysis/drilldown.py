# -*- coding: utf-8 -*-
"""动态下钻策略：Agent 看完当前结果后，决定"要不要继续查、往哪个维度查"。

核心思想（区别于固定查三次的假下钻）：
    每一跳都是一次结构化决策 ——
    need_drilldown / dimension / hypothesis / expected_information_gain
    深度上限 DRILLDOWN_MAX_DEPTH=3，超过必须重新规划或终止。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from config.config import get_settings

# 电商归因的常用下钻维度（按典型信息增益排序）
DEFAULT_DRILL_DIMENSIONS = [
    "category",      # 类目
    "product",       # SKU/商品
    "size",          # 尺码
    "color",         # 颜色
    "region",        # 地区
    "channel",       # 渠道
    "refund_reason", # 退款原因
]


class DrillDecision(BaseModel):
    """Drill-down Planner 的结构化输出。"""

    need_drilldown: bool = Field(description="是否有必要继续下钻")
    reason: str = Field(default="", description="做出该判断的依据（引用数据中的具体数字）")
    dimension: Literal[
        "category", "product", "size", "color",
        "region", "channel", "refund_reason", "none",
    ] = Field(default="none", description="下一跳要拆分的维度")
    hypothesis: str = Field(default="", description="待验证的假设，如：异常集中在少数SKU")
    expected_information_gain: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="预期信息增益 0~1：<0.4 不值得再查；>0.7 强烈建议下钻",
    )
    focus_filters: dict[str, str] = Field(
        default_factory=dict,
        description="收窄范围的过滤条件（继承上一跳结论），如 {'color':'红色','category':'女装'}",
    )


def depth_reached(depth: int) -> bool:
    """是否已到最大深度。超过后要求重新规划或人工确认（面试高频点）。"""
    return depth >= get_settings().drilldown_max_depth


def build_drill_prompt(
    question: str,
    current_result_summary: str,
    anomaly_summary: str,
    already_explored: list[str],
) -> tuple[str, str]:
    """构造 Drill-down 决策的系统/用户提示词。"""
    system = (
        "你是数据分析下钻决策器。给定当前查询结果与异常证据，判断是否值得继续下钻。\n"
        f"可用维度：{', '.join(DEFAULT_DRILL_DIMENSIONS)}。\n"
        "硬性要求：\n"
        f"- need_drilldown=true 时，dimension 必须从 {DEFAULT_DRILL_DIMENSIONS} 中选一个具体值，禁止填 none\n"
        "- 只有当某维度的贡献分布可能显著不均匀时才继续（信息增益 > 0.4）\n"
        "- 已经探索过的维度不要重复\n"
        "- 如果当前结果已经能解释用户问题，need_drilldown=false 并说明结论\n"
        "- focus_filters 继承已确认的范围（如已定位红色女装就带上）\n"
        "- 推荐下钻顺序：先 category/product 定位异常集中点，再 size/color 等属性，最后 refund_reason 归因"
    )
    user = (
        f"用户问题：{question}\n\n"
        f"当前查询结果摘要：\n{current_result_summary}\n\n"
        f"异常检测结果：\n{anomaly_summary}\n\n"
        f"已探索维度：{already_explored or '无'}"
    )
    return system, user


if __name__ == "__main__":
    s, u = build_drill_prompt(
        "红色女装最近退款率为什么上升？",
        "近30天退款率14.7%，此前基线8.2%",
        "z=3.1 方向up，检出3个跳变点",
        [],
    )
    print(s, "\n---\n", u)
