import type { SemanticSummary, StreamEvent } from "./types";

/**
 * 消费 SSE：POST /api/query/stream
 *
 * 浏览器原生 EventSource 只支持 GET，无法带 JSON 请求体，
 * 因此用 fetch + ReadableStream 手动解析 SSE 文本协议：
 *
 *     event: node            ← 事件名（可忽略，类型已含在 data JSON 的 type 字段里）
 *     data: {"type": ...}    ← JSON 数据（长数据可能拆成多行 data:）
 *     （空行 = 一条事件结束）
 */
export async function streamQuery(
  question: string,
  onEvent: (ev: StreamEvent) => void,
): Promise<void> {
  const resp = await fetch("/api/query/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`HTTP ${resp.status}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  let dataLines: string[] = [];

  // 把攒到的 data 行拼成完整 JSON 并回调
  const dispatch = () => {
    if (dataLines.length === 0) return;
    const raw = dataLines.join("\n");
    dataLines = [];
    try {
      onEvent(JSON.parse(raw) as StreamEvent);
    } catch {
      // 忽略无法解析的帧（如心跳注释行）
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    const lines = buf.split("\n");
    buf = lines.pop() ?? ""; // 末行可能被 TCP 分段截断，留到下一轮拼接
    for (const raw of lines) {
      const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
      if (line === "") {
        dispatch(); // 空行 = 事件边界
      } else if (line.startsWith("data:")) {
        // 去掉 "data:" 以及 SSE 规范允许的一个前导空格
        const payload = line.slice(5);
        dataLines.push(payload.startsWith(" ") ? payload.slice(1) : payload);
      }
    }
  }
  dispatch(); // 兜底：流结束时处理残留的最后一帧
}

/** 拉取语义层摘要（左栏数据源） */
export async function fetchSemanticSummary(): Promise<SemanticSummary> {
  const resp = await fetch("/api/semantic/summary");
  if (!resp.ok) throw new Error(`语义层加载失败: HTTP ${resp.status}`);
  return resp.json();
}
