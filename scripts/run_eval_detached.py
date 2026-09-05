# -*- coding: utf-8 -*-
"""以分离进程方式运行全量评测（不受终端会话超时影响）。

用法：
    python scripts/run_eval_detached.py [--limit N]

日志： logs/eval_run_*.log（进度） / evaluation/output/report_*.md（记分卡）
查看进度： Get-Content logs/eval_run_*.log -Tail 5 -Wait
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

args = [sys.executable, "-m", "evaluation.evaluator"]
if "--limit" in sys.argv:
    i = sys.argv.index("--limit")
    args += ["--limit", sys.argv[i + 1]]
else:
    args += ["--level", "all"]

stamp = time.strftime("%H%M%S")
out = LOG_DIR / f"eval_run_{stamp}.log"
err = LOG_DIR / f"eval_err_{stamp}.log"

env = dict(os.environ)
env["PYTHONIOENCODING"] = "utf-8"
env["PYTHONUTF8"] = "1"

proc = subprocess.Popen(
    args,
    cwd=str(ROOT),
    env=env,
    stdout=open(out, "w", encoding="utf-8"),
    stderr=open(err, "w", encoding="utf-8"),
)
print(f"评测已启动(pid={proc.pid})，本窗口可关闭，评测继续在后台运行")
print(f"进度日志: {out}")

