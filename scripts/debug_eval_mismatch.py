# -*- coding: utf-8 -*-
"""对比可疑低分题的 Golden/Pred 实际行集，定位评分差异根因。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.metrics import result_multiset  # noqa: E402
from sql.executor import execute_sql  # noqa: E402

MYSTERY = ["Q018", "Q019", "Q022", "Q029", "Q034", "Q010"]

traj = {}
with open(Path(__file__).resolve().parent.parent / "evaluation/output/trajectory.jsonl", encoding="utf-8") as f:
    for line in f:
        r = json.loads(line)
        traj[r["record"]["id"]] = r

out_lines = []
for qid in MYSTERY:
    item = traj[qid]["item"]
    final_sql = traj[qid].get("final_sql") or ""
    out_lines.append("=" * 70)
    out_lines.append(f"{qid} {item['question']}")
    try:
        g = execute_sql(item["golden_sql"])
        p = execute_sql(final_sql)
        gm, pm = result_multiset(g.rows), result_multiset(p.rows)
        out_lines.append(f"golden rows({len(gm)}): {gm[:6]}")
        out_lines.append(f"pred   rows({len(pm)}): {pm[:6]}")
        out_lines.append(f"equal={gm == pm}")
    except Exception as e:  # noqa: BLE001
        out_lines.append(f"EXEC ERROR: {e}")

report = "\n".join(out_lines)
print(report[:3000])
with open("logs/diag_mystery.txt", "w", encoding="utf-8") as f:
    f.write(report)
