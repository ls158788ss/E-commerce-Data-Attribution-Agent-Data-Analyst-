# -*- coding: utf-8 -*-
"""评测运行器：跑数据集 -> 记分卡。

用法：
    python -m evaluation.evaluator --level all          # 全量
    python -m evaluation.evaluator --level simple,join  # 指定类别
    python -m evaluation.evaluator --limit 10           # 只跑前10题（冒烟）

输出：
    evaluation/output/report_<date>.md   记分卡
    evaluation/output/trajectory.jsonl   每题完整轨迹（供回放分析）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.metrics import (  # noqa: E402
    execution_accuracy,
    extract_tables,
    join_accuracy,
    schema_recall_precision,
)
from sql.executor import SQLExecutionError, execute_sql  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output"


def load_dataset(levels: list[str] | None = None) -> list[dict]:
    items = []
    with open(HERE / "dataset.jsonl", encoding="utf-8") as f:
        for line in f:
            q = json.loads(line)
            if not levels or "all" in levels or q["level"] in levels:
                items.append(q)
    return items


def evaluate_one(item: dict, result: dict) -> dict:
    """对单题计算各项指标。"""
    scores = {"execution": None, "schema_recall": None, "schema_precision": None,
              "join": None, "validity": None}

    if item["scoring"] == "execution":
        try:
            golden = execute_sql(item["golden_sql"])
        except SQLExecutionError:
            return {**scores, "note": "golden_sql 自身执行失败，题目需修复"}
        if result.get("success"):
            scores["execution"] = execution_accuracy(
                golden.rows, result.get("result_rows", []), item.get("compare"))
            scores["validity"] = 1.0
        else:
            scores["execution"] = 0.0
            scores["validity"] = 0.0
    else:  # lenient：开放题只要求成功产出结论
        scores["execution"] = 1.0 if result.get("success") else 0.0
        scores["validity"] = 1.0 if result.get("success") else 0.0

    pred_sql = result.get("final_sql") or ""
    if item["level"] not in ("multi_hop",):  # 多跳分析不按单条 SQL 评 join
        if result.get("success") and pred_sql:
            pred_tables = extract_tables(pred_sql)
            golden_tables = set(item["golden_tables"])
            r, p = schema_recall_precision(pred_tables, golden_tables)
            scores["schema_recall"], scores["schema_precision"] = r, p
            scores["join"] = join_accuracy(pred_sql, golden_tables)
        elif not result.get("success"):
            scores["schema_recall"], scores["schema_precision"], scores["join"] = 0.0, 0.0, 0.0

    # metric accuracy：意图/语义层是否命中了正确指标口径
    if item.get("metric"):
        trace_text = json.dumps(result.get("trace", []), ensure_ascii=False)
        intent_metrics = " ".join((result.get("intent") or {}).get("metric_hints", []))
        from semantic.loader import load_semantic_store

        m = load_semantic_store().metric(item["metric"])
        target_name = m.business_name if m else item["metric"]
        scores["metric_hit"] = 1.0 if (target_name in intent_metrics or target_name in trace_text) else 0.0

    return scores


def main() -> None:
    parser = argparse.ArgumentParser(description="智能问数评测")
    parser.add_argument("--level", default="all", help="逗号分隔的类别，或 all")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题")
    parser.add_argument("--no-cache", action="store_true",
                        help="忽略 LLM 响应缓存，强制重新调用（prompt 改动后用）")
    args = parser.parse_args()

    # 评测模式：开启 LLM 响应磁盘缓存——免费档 50 次/天，38 题约需 150 次，
    # 缓存让重跑零额度消耗，可跨多个日窗口累计跑完全量后稳定复现。
    # --no-cache 时关闭，便于 prompt/规则改动后强制刷新。
    if not args.no_cache:
        os.environ["LLM_EVAL_CACHE"] = "1"

    levels = [x.strip() for x in args.level.split(",")]
    dataset = load_dataset(levels)
    if args.limit:
        dataset = dataset[: args.limit]

    OUT_DIR.mkdir(exist_ok=True)
    traj_path = OUT_DIR / "trajectory.jsonl"

    from graph.workflow import run_query

    rows_out = []
    t0 = time.time()
    with open(traj_path, "w", encoding="utf-8") as traj_f:
        for i, item in enumerate(dataset, 1):
            print(f"[{i}/{len(dataset)}] {item['id']} {item['question']}", flush=True)
            try:
                final = run_query(item["question"])
                final = dict(final)  # TypedDict -> plain dict
            except Exception as e:  # noqa: BLE001
                final = {"success": False, "fail_reason": f"异常: {e}", "trace": []}
            scores = evaluate_one(item, final)
            rec = {
                "id": item["id"], "level": item["level"], "question": item["question"],
                "success": bool(final.get("success")), **scores,
                "repair_count": _count_repairs(final),
                "drill_depth": 0,  # S4 接入后由 state 填充
                "elapsed_ms": int((time.time() - t0) * 1000),
                "answer": (final.get("answer") or final.get("fail_reason") or "")[:300],
            }
            rows_out.append(rec)
            traj_f.write(json.dumps({"item": item, "record": rec,
                                     "final_sql": final.get("final_sql", "")},
                                    ensure_ascii=False) + "\n")

    _write_report(rows_out)


def _count_repairs(final: dict) -> int:
    return sum(1 for ev in final.get("trace", []) if str(ev.get("step", "")).startswith("repair"))


def _write_report(rows: list[dict]) -> None:
    def agg(key: str) -> tuple[float, int]:
        vals = [r[key] for r in rows if r.get(key) is not None]
        return (sum(vals) / len(vals) if vals else 0.0), len(vals)

    by_level: dict[str, list[dict]] = {}
    for r in rows:
        by_level.setdefault(r["level"], []).append(r)

    lines = [
        "# 智能问数 评测记分卡",
        f"\n生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}　|　题数：{len(rows)}\n",
        "## 总体指标\n",
        "| 指标 | 得分 | 样本数 |",
        "|---|---|---|",
    ]
    for key, name in [("execution", "Execution Accuracy"), ("validity", "SQL Validity"),
                      ("schema_recall", "Schema Recall"), ("schema_precision", "Schema Precision"),
                      ("join", "Join Accuracy"), ("metric_hit", "Metric Accuracy")]:
        score, n = agg(key)
        lines.append(f"| {name} | {score * 100:.1f}% | {n} |")
    success_rate = sum(1 for r in rows if r["success"]) / len(rows) if rows else 0
    avg_repairs = sum(r["repair_count"] for r in rows) / len(rows) if rows else 0
    lines += [
        f"| Query Success Rate | {success_rate * 100:.1f}% | {len(rows)} |",
        f"| 平均修复次数 | {avg_repairs:.2f} | {len(rows)} |",
        "\n## 分类别得分（Execution Accuracy）\n",
        "| 类别 | 题数 | 平均分 |",
        "|---|---|---|",
    ]
    for lv, rs in by_level.items():
        execs = [r["execution"] for r in rs if r.get("execution") is not None]
        avg = sum(execs) / len(execs) if execs else 0
        lines.append(f"| {lv} | {len(rs)} | {avg * 100:.1f}% |")

    report = "\n".join(lines)
    path = OUT_DIR / f"report_{datetime.now():%Y%m%d_%H%M}.md"
    path.write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"\n✅ 记分卡: {path}")


if __name__ == "__main__":
    main()
