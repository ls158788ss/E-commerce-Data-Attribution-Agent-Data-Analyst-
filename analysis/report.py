# -*- coding: utf-8 -*-
"""图表生成：把查询结果转成 plotly 图（供 UI / 报告使用）。

设计原则：按"数据形状"自动选图 ——
    时间序列   -> 折线
    类别分布   -> 横向条形
    两列(类别+数值+分组) -> 分组条形
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go

# 简洁的中文友好配色（与 Streamlit 主题兼容）
COLOR_MAIN = "#6366f1"
COLOR_ALERT = "#ef4444"
COLOR_BASE = "#94a3b8"

TIME_HINTS = ("日期", "月份", "周", "date", "month", "week", "时间")


def _is_time_col(col: str) -> bool:
    low = col.lower()
    return any(h in low for h in TIME_HINTS)


def pick_chart(df: pd.DataFrame) -> str:
    """根据列形状选图类型：line / bar / none。"""
    cols = list(df.columns)
    if len(cols) < 2 or len(cols) > 3 or df.empty:
        return "none"
    label_col, value_col = cols[0], cols[-1]
    if pd.api.types.is_numeric_dtype(df[value_col]):
        return "line" if any(_is_time_col(c) for c in cols[:-1]) else "bar"
    return "none"


def make_figure(
    rows: list[dict[str, Any]],
    title: str = "",
    highlight_max: bool = False,
) -> go.Figure | None:
    """把结果行渲染成图表；无法可视化时返回 None。"""
    if not rows:
        return None
    df = pd.DataFrame(rows)
    kind = pick_chart(df)
    if kind == "none":
        return None

    cols = list(df.columns)
    x, y = cols[0], cols[-1]
    fig = go.Figure()

    if kind == "line":
        fig.add_trace(go.Scatter(
            x=df[x].astype(str), y=df[y], mode="lines+markers",
            line={"color": COLOR_MAIN, "width": 3},
            marker={"size": 8},
            name=y,
        ))
        # 标注最大跳变点
        if len(df) >= 3 and pd.api.types.is_numeric_dtype(df[y]):
            deltas = df[y].diff().abs()
            peak_idx = int(deltas.idxmax()) if deltas.notna().any() else None
            if peak_idx and peak_idx > 0:
                fig.add_trace(go.Scatter(
                    x=[str(df.loc[peak_idx, x])], y=[df.loc[peak_idx, y]],
                    mode="markers", marker={"color": COLOR_ALERT, "size": 13,
                                            "symbol": "diamond"},
                    name="显著变化点",
                ))
    else:
        colors = [COLOR_MAIN] * len(df)
        if highlight_max and pd.api.types.is_numeric_dtype(df[y]) and len(df):
            top_idx = df[y].astype(float).idxmax()
            colors[top_idx] = COLOR_ALERT
        fig.add_trace(go.Bar(
            x=df[x].astype(str), y=df[y],
            marker_color=colors, name=y,
        ))

    fig.update_layout(
        title=title or f"{y} by {x}",
        template="plotly_white",
        margin={"l": 40, "r": 20, "t": 50, "b": 30},
        height=340,
        showlegend=False,
        font={"family": "Microsoft YaHei, SimHei, sans-serif"},
    )
    return fig


if __name__ == "__main__":
    demo_rows = [
        {"月份": "2026-06", "退款率": 8.2},
        {"月份": "2026-07", "退款率": 9.4},
        {"月份": "2026-08", "退款率": 14.8},
    ]
    fig = make_figure(demo_rows, title="退款率趋势")
    assert fig is not None
    print("figure OK:", type(fig).__name__)
