# -*- coding: utf-8 -*-
"""S0/S1 一键验证脚本：冒烟 -> 单测 -> 造数 -> 端到端提问。

用法（在 data-agent 环境）：
    python scripts/run_s0_checks.py [skip_smoke]
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def step(name: str, args: list[str]) -> bool:
    print(f"\n{'=' * 60}\n>>> {name}\n{'=' * 60}", flush=True)
    r = subprocess.run([PY, *args], cwd=str(ROOT))
    return r.returncode == 0


def main() -> None:
    results: list[tuple[str, bool]] = []

    if "skip_smoke" not in sys.argv:
        results.append(("LLM 冒烟", step("LLM 冒烟测试", ["tests/test_llm_smoke.py"])))

    results.append(("沙盒单测", step(
        "SQL 校验器单元测试",
        ["-m", "pytest", "tests/test_validator.py", "-q"])))

    results.append(("语义层加载", step("语义层加载自检", ["-m", "semantic.loader"])))
    results.append(("JoinGraph", step("Join 路径搜索自检", ["-m", "retrieval.join_graph"])))

    if not (ROOT / "data" / "ecommerce.duckdb").exists():
        results.append(("数据生成", step("生成模拟数据", ["data/generate_data.py"])))
    else:
        print("\n(数据库已存在，跳过造数；如需重造请删除 data/ecommerce.duckdb)", flush=True)

    results.append(("端到端提问", step("端到端：7月销售额", ["ask.py", "2026年7月销售额是多少？"])))
    results.append(("端到端旗舰", step("端到端：旗舰问题", ["ask.py", "红色女装最近退款率为什么上升？"])))

    print(f"\n{'=' * 60}\n验证汇总", flush=True)
    for name, ok in results:
        print(f"  {'✅' if ok else '❌'} {name}")
    sys.exit(0 if all(ok for _, ok in results) else 1)


if __name__ == "__main__":
    main()
