# -*- coding: utf-8 -*-
"""结构化轨迹：每次问答的完整执行链路落 JSONL，供 UI Trace 面板与评测回放。

Langfuse 为可选通道：配置了 key 才启用，未配置不影响任何主链路。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.config import get_settings

logger = logging.getLogger(__name__)

TRACE_DIR = Path(__file__).resolve().parent.parent / "logs"
TRACE_DIR.mkdir(exist_ok=True)


class TraceRecorder:
    """一次问答 = 一条 trace（trace_id + 有序 events）。"""

    def __init__(self, question: str) -> None:
        self.trace_id = uuid.uuid4().hex[:12]
        self.question = question
        self.events: list[dict[str, Any]] = []
        self._t0 = time.perf_counter()
        self._langfuse = self._try_langfuse()

    # ------------------------------------------------------------------
    def _try_langfuse(self):
        s = get_settings()
        if not (s.langfuse_public_key and s.langfuse_secret_key):
            return None
        try:
            from langfuse import Langfuse

            return Langfuse(public_key=s.langfuse_public_key,
                            secret_key=s.langfuse_secret_key,
                            host=s.langfuse_host)
        except Exception as e:  # noqa: BLE001  可选组件失败静默降级
            logger.warning("Langfuse 初始化失败，仅使用本地 trace: %s", e)
            return None

    # ------------------------------------------------------------------
    def add(self, step: str, **fields: Any) -> dict[str, Any]:
        ev = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "offset_ms": int((time.perf_counter() - self._t0) * 1000),
            "step": step,
            **fields,
        }
        self.events.append(ev)
        if self._langfuse is not None:
            try:
                self._langfuse.trace(
                    id=self.trace_id, name="data-agent-query",
                    input={"question": self.question},
                ).event(name=step, metadata=fields)
            except Exception:  # noqa: BLE001
                pass
        return ev

    def finish(self, success: bool, answer: str | None = None) -> str:
        record = {
            "trace_id": self.trace_id,
            "question": self.question,
            "success": success,
            "answer": (answer or "")[:2000],
            "duration_ms": int((time.perf_counter() - self._t0) * 1000),
            "events": self.events,
        }
        path = TRACE_DIR / "traces.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return self.trace_id


if __name__ == "__main__":
    t = TraceRecorder("演示问题")
    t.add("planner", intent={"intent_type": "lookup"})
    t.add("execute", ok=True, row_count=3)
    print("trace_id =", t.finish(success=True, answer="演示结论"))
