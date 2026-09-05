# -*- coding: utf-8 -*-
"""异常检测：对时序查询结果做统计判断，输出"是否存在异常"的证据。

S1 链路不经过本模块；S4 的 analyze 节点开始使用。
方法：
    * 环比跳变：相邻期差值超过阈值（百分点）
    * z-score：当前值相对历史均值/标准差的偏离
两种证据都给出，供 Drill-down Policy 结构化决策。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class AnomalyReport:
    detected: bool = False
    metric_name: str = ""
    series: list[dict] = field(default_factory=list)      # [{label, value}]
    jump_points: list[dict] = field(default_factory=list)  # [{from_label,to_label,delta}]
    current_value: float | None = None
    baseline_mean: float | None = None
    z_score: float | None = None
    direction: str = ""   # up / down / ""
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "detected": self.detected,
            "metric_name": self.metric_name,
            "jump_points": self.jump_points,
            "current_value": self.current_value,
            "baseline_mean": round(self.baseline_mean, 3) if self.baseline_mean is not None else None,
            "z_score": round(self.z_score, 2) if self.z_score is not None else None,
            "direction": self.direction,
            "summary": self.summary,
        }


def detect_series_anomaly(
    labels: list[str],
    values: list[float],
    *,
    metric_name: str = "",
    jump_threshold: float = 1.0,     # 环比跳变阈值（与指标同单位，如百分点）
    z_threshold: float = 2.0,
    baseline_fraction: float = 0.6,  # 序列前 60% 作为基线期
) -> AnomalyReport:
    report = AnomalyReport(metric_name=metric_name, series=[{"label": l, "value": v} for l, v in zip(labels, values)])
    if len(values) < 4:
        return report

    arr = np.asarray(values, dtype=float)
    split = max(2, int(len(arr) * baseline_fraction))
    baseline, recent = arr[:split], arr[split:]

    # ---- 证据1：环比跳变 ----
    for i in range(split - 1, len(arr) - 1):
        delta = float(arr[i + 1] - arr[i])
        if abs(delta) >= jump_threshold:
            report.jump_points.append({
                "from_label": labels[i], "to_label": labels[i + 1],
                "delta": round(delta, 3),
            })

    # ---- 证据2：z-score（近期均值 vs 基线）----
    base_mean = float(baseline.mean())
    base_std = float(baseline.std()) or 1e-9
    recent_mean = float(recent.mean())
    z = (recent_mean - base_mean) / base_std

    report.baseline_mean = base_mean
    report.current_value = float(arr[-1])
    report.z_score = z
    report.detected = bool(abs(z) >= z_threshold or report.jump_points)
    if report.detected:
        report.direction = "up" if recent_mean > base_mean else "down"
        report.summary = (
            f"{metric_name or '指标'}近期均值({recent_mean:.2f})相对基线({base_mean:.2f}) "
            f"z={z:.2f}，方向{report.direction}；检出 {len(report.jump_points)} 个跳变点"
        )
    return report


def detect_table_anomaly(
    rows: list[dict],
    label_col: str,
    value_col: str,
    **kwargs,
) -> AnomalyReport:
    """便捷入口：直接吃 SQL 结果行。"""
    labels = [str(r[label_col]) for r in rows]
    values = [float(r[value_col]) for r in rows]
    return detect_series_anomaly(labels, values, **kwargs)
