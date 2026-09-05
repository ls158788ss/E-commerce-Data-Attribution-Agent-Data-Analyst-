import { useEffect, useState } from "react";
import { fetchSemanticSummary, streamQuery } from "./api";
import type { ChatMessage, SemanticSummary, TraceEvent } from "./types";
import Sidebar from "./components/Sidebar";
import Chat from "./components/Chat";
import TracePanel from "./components/TracePanel";

/**
 * 【技术栈：React 18 + TypeScript + Vite】前后端分离前端入口。
 *
 * 三栏布局（与 Streamlit 版对齐）：
 *   左栏  数据资产（语义层摘要）
 *   中间  聊天区（问题 → 结论 + SQL/结果表折叠面板）
 *   右栏  Agent Trace 实时执行轨迹（SSE 按节点步进推送）
 */
export default function App() {
  const [summary, setSummary] = useState<SemanticSummary | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [trace, setTrace] = useState<TraceEvent[]>([]);
  const [running, setRunning] = useState(false);

  // 挂载时拉取语义层摘要（左栏数据）
  useEffect(() => {
    fetchSemanticSummary()
      .then(setSummary)
      .catch(() => setSummary(null));
  }, []);

  // 只修改最后一条助手消息（流式结束后回填结果）
  const patchLast = (patch: Partial<ChatMessage>) => {
    setMessages((prev) =>
      prev.map((m, i) => (i === prev.length - 1 ? { ...m, ...patch } : m)),
    );
  };

  async function ask(question: string) {
    const q = question.trim();
    if (!q || running) return;

    setRunning(true);
    setTrace([]); // 右栏 Trace 每次提问重新累计
    setMessages((prev) => [
      ...prev,
      { role: "user", content: q },
      { role: "assistant", content: "Agent 分析中…" },
    ]);

    try {
      await streamQuery(q, (ev) => {
        if (ev.type === "node") {
          // LangGraph 每完成一个节点推送一次，携带该节点新增的 trace 事件
          setTrace((prev) => [...prev, ...ev.trace]);
        } else if (ev.type === "final") {
          const p = ev.payload;
          patchLast({
            content: p.success
              ? p.answer ?? ""
              : `⚠️ ${p.fail_reason ?? "未能完成本次分析"}`,
            detail: p,
            error: !p.success,
          });
        } else if (ev.type === "error") {
          patchLast({ content: `⚠️ 系统异常：${ev.message}`, error: true });
        }
      });
    } catch (e) {
      patchLast({
        content: `⚠️ 请求失败：${e instanceof Error ? e.message : String(e)}`,
        error: true,
      });
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="layout">
      <header className="topbar">
        <h1>📊 智能问数 Data Analyst Agent</h1>
        <span className="sub">
          自然语言 → 业务理解 → Text2SQL → 沙盒校验 → 执行 → 结论
        </span>
      </header>
      <Sidebar summary={summary} />
      <Chat messages={messages} running={running} onAsk={ask} />
      <TracePanel events={trace} running={running} />
    </div>
  );
}
