import { useEffect, useRef, useState } from "react";
import type { ChatMessage } from "../types";
import Chart from "./Chart";
import ResultTable from "./ResultTable";

/** 示例问题（空状态展示，点击直接提问） */
const SAMPLES = [
  "红色女装最近退款率为什么上升？",
  "7 月总销售额是多少？",
  "华东地区女装上个月的 GMV？",
];

/**
 * 助手消息的详情区（移植自 Streamlit 版 show_detail）：
 * 归因结论链 → 图表 → SQL → 结果表 → 下钻证据 → 口径提醒
 */
function Detail({ detail }: { detail?: ChatMessage["detail"] }) {
  if (!detail) return null;

  const chain = detail.conclusion_chain ?? [];
  const charts = detail.charts ?? [];
  const sql = detail.final_sql ?? "";
  const caveats = detail.caveats ?? [];
  const evidence = detail.evidence ?? [];
  const drillEvidence = evidence.length > 1 ? evidence.slice(1) : [];
  const rowCount = detail.result_rows?.length ?? 0;

  return (
    <div className="detail">
      {chain.length > 0 && (
        <details open className="block chain">
          <summary>🧾 归因结论链</summary>
          <ol>
            {chain.map((c, i) => (
              <li key={i}>
                <b>{String(c.step)}.</b> {c.statement}
                {c.evidence_ref && <span className="ref">依据：{c.evidence_ref}</span>}
              </li>
            ))}
          </ol>
        </details>
      )}

      {charts.map((ch, i) => (
        <Chart key={i} spec={ch} />
      ))}

      {sql && (
        <details className="block">
          <summary>🔍 查看 SQL</summary>
          <pre className="sql">{sql}</pre>
        </details>
      )}

      {rowCount > 0 && (
        <details open={chain.length === 0} className="block">
          <summary>📋 查询结果（{rowCount} 行）</summary>
          <ResultTable columns={detail.result_columns} rows={detail.result_rows} />
        </details>
      )}

      {drillEvidence.map((e, i) => (
        <details key={i} className="block">
          <summary>
            🗂️ 下钻证据 · 第{e.hop ?? i + 1}跳（{e.dimension ?? ""}）
          </summary>
          <ResultTable columns={e.columns} rows={e.rows} />
          {e.note && <p className="note">{e.note}</p>}
        </details>
      ))}

      {caveats.length > 0 && <div className="caveats">口径提醒：{caveats.join("；")}</div>}
    </div>
  );
}

/** 中间聊天主区：消息列表 + 输入框 */
export default function Chat({
  messages,
  running,
  onAsk,
}: {
  messages: ChatMessage[];
  running: boolean;
  onAsk: (question: string) => void;
}) {
  const [text, setText] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  // 新消息时滚动到底部
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  function submit() {
    const q = text;
    setText("");
    onAsk(q);
  }

  return (
    <main className="chat">
      <div className="msg-list" ref={listRef}>
        {messages.length === 0 && (
          <div className="hero">
            <h2>向 Agent 提一个业务问题</h2>
            <p>试试这些：</p>
            <div className="samples">
              {SAMPLES.map((s) => (
                <button key={s} onClick={() => onAsk(s)} disabled={running}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role} ${m.error ? "error" : ""}`}>
            <div className="bubble">
              {m.content}
              {m.role === "assistant" && m.content === "Agent 分析中…" && (
                <span className="dots">…</span>
              )}
            </div>
            {m.role === "assistant" && <Detail detail={m.detail} />}
          </div>
        ))}
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="请输入你的业务问题…"
          disabled={running}
        />
        <button type="submit" disabled={running || !text.trim()}>
          {running ? "分析中…" : "发送"}
        </button>
      </form>
    </main>
  );
}
