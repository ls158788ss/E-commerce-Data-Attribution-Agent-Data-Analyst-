# -*- coding: utf-8 -*-
"""FastAPI 后端：REST + SSE 流式接口。

【技术栈：FastAPI + Uvicorn】前后端分离架构的后端服务，
REST 接口 + SSE 流式推送，自动生成 OpenAPI 文档（/docs）。

启动：
    uvicorn api.main:app --host 0.0.0.0 --port 8000

接口一览（对应文档第 29 节）：
    POST /api/query           同步问答
    POST /api/query/stream    SSE 流式（按节点步进推送）
    GET  /api/semantic/summary 语义层摘要（前端左栏）
    GET  /api/query/{id}      查询历史结果
    GET  /api/trace/{id}      查询执行轨迹
    POST /api/evaluation/run  运行评测集（后台任务）
    GET  /api/metrics         服务运行指标
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.config import get_settings  # noqa: E402
from graph.workflow import get_app  # noqa: E402
from observability.tracing import TraceRecorder  # noqa: E402

app = FastAPI(title="Data Analyst Agent API", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

_HISTORY: deque[dict[str, Any]] = deque(maxlen=200)
_STARTED_AT = time.time()


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


@app.post("/api/query")
def query(req: QueryRequest) -> dict:
    """同步执行一次问答，返回完整结果 + 轨迹。"""
    recorder = TraceRecorder(req.question)
    try:
        final = get_app().invoke({"question": req.question}, {"recursion_limit": 30})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"agent 执行失败: {e}") from e

    success = bool(final.get("success"))
    trace_id = recorder.finish(success, final.get("answer") or final.get("fail_reason"))
    payload = _build_payload(final, trace_id)
    _HISTORY.appendleft(payload)
    return payload


async def query_stream(req: QueryRequest) -> AsyncIterator[dict]:
    """按节点粒度流式产出事件（供 SSE）。"""
    app_graph = get_app()
    queue: asyncio.Queue[dict] = asyncio.Queue()

    async def _drive() -> None:
        final_state: dict = {}
        try:
            async for chunk in app_graph.astream({"question": req.question}, {"recursion_limit": 30}):
                for node, update in chunk.items():
                    await queue.put({"type": "node", "node": node,
                                     "trace": update.get("trace", [])})
                    final_state.update(update)
            await queue.put({"type": "final", "payload": final_state})
        except Exception as e:  # noqa: BLE001
            await queue.put({"type": "error", "message": str(e)})
        finally:
            await queue.put({"type": "_done"})

    task = asyncio.create_task(_drive())
    while True:
        ev = await queue.get()
        if ev["type"] == "_done":
            break
        yield ev
    await task


@app.post("/api/query/stream")
async def query_stream_sse(req: QueryRequest):
    """SSE 封装。【技术栈：SSE】LangGraph astream 按节点粒度产出 -> sse-starlette 推送，
    前端（React/TS）用 fetch + ReadableStream 逐节点实时渲染 Trace。"""
    from sse_starlette.sse import EventSourceResponse

    async def gen():
        async for ev in query_stream(req):
            yield {"event": ev["type"], "data": json.dumps(ev, ensure_ascii=False, default=str)}

    return EventSourceResponse(gen())


@app.get("/api/query/{trace_id}")
def get_query(trace_id: str) -> dict:
    for item in _HISTORY:
        if item["trace_id"] == trace_id:
            return item
    raise HTTPException(status_code=404, detail="未找到该查询记录")


@app.get("/api/trace/{trace_id}")
def get_trace(trace_id: str) -> dict:
    """从 JSONL 落盘文件中检索轨迹（跨重启可查）。"""
    path = PROJECT_ROOT / "logs" / "traces.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="暂无轨迹文件")
    with open(path, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("trace_id") == trace_id:
                return rec
    raise HTTPException(status_code=404, detail="未找到该轨迹")


@app.post("/api/evaluation/run")
async def run_evaluation(levels: str = "all"):
    """触发评测（阻塞版；数据量大时建议 CLI）。"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "evaluation.evaluator", "--level", levels,
        cwd=str(PROJECT_ROOT), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return {"returncode": proc.returncode, "output": out.decode("utf-8", errors="replace")[-4000:]}


@app.get("/api/semantic/summary")
def semantic_summary() -> dict:
    """语义层摘要（指标/表/术语），供前端左栏展示。"""
    from semantic.loader import load_semantic_store

    store = load_semantic_store()  # lru_cache 单例
    return {
        "metrics": [
            {"business_name": m.business_name, "definition": m.definition, "unit": m.unit}
            for m in store.metrics
        ],
        "tables": [
            {"table": t.table, "description": t.description, "column_count": len(t.columns)}
            for t in store.tables
        ],
        "glossary": [
            {"term": g.term, "definition": g.definition} for g in store.glossary
        ],
    }


@app.get("/api/metrics")
def metrics() -> dict:
    ok = sum(1 for h in _HISTORY if h.get("success"))
    total = len(_HISTORY)
    return {
        "uptime_seconds": int(time.time() - _STARTED_AT),
        "queries_total": total,
        "queries_success": ok,
        "success_rate": round(ok / total, 3) if total else None,
        "model": get_settings().llm_model,
    }


def _build_payload(final: dict, trace_id: str) -> dict:
    return {
        "trace_id": trace_id,
        "question": final.get("question"),
        "success": bool(final.get("success")),
        "answer": final.get("answer") or final.get("fail_reason"),
        "sql": final.get("final_sql") or "",
        "columns": final.get("result_columns", []),
        "rows": final.get("result_rows", []),
        "row_count": final.get("result_row_count", 0),
        "elapsed_ms": final.get("elapsed_ms", 0),
        "caveats": final.get("caveats", []),
        "intent": final.get("intent"),
        "attempts": final.get("attempts", []),
        "trace": final.get("trace", []),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
