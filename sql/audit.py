# -*- coding: utf-8 -*-
"""SQL 审计日志：每次执行尝试（含被沙盒拦截的）都落盘，可回溯可追责。

格式：JSONL，一行一条。生产环境应接入集中式审计系统。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "sql_audit.jsonl"


def audit_sql(
    *,
    sql: str,
    stage: str,            # validate / execute / repair
    allowed: bool,
    error: str = "",
    row_count: int | None = None,
    elapsed_ms: int | None = None,
) -> str:
    LOG_PATH.parent.mkdir(exist_ok=True)
    entry_id = uuid.uuid4().hex[:10]
    record = {
        "id": entry_id,
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "stage": stage,
        "allowed": allowed,
        "error": error[:500],
        "row_count": row_count,
        "elapsed_ms": elapsed_ms,
        "sql": sql[:2000],
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return entry_id
