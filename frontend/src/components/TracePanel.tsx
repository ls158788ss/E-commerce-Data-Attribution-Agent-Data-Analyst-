import type { TraceEvent } from "../types";
import type { ReactNode } from "react";

/** 事件里字段类型不确定时的安全取值工具 */
const asStr = (v: unknown): string => (v == null ? "" : String(v));
const asRec = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" ? (v as Record<string, unknown>) : {};
const asList = (v: unknown): string[] =>
  Array.isArray(v) ? v.map(asStr) : [];

/**
 * 单条 trace 事件 → 时间线节点。
 * 与后端 graph/nodes.py 写入的字段一一对应（移植自 Streamlit 版 render_trace_events）。
 */
function Step({ ev, index }: { ev: TraceEvent; index: number }) {
  const step = ev.step;
  let body: ReactNode = <b>{step}</b>;

  if (step === "planner") {
    const intent = asRec(ev.intent);
    const tr = asRec(intent.time_range);
    body = (
      <>
        <b>🧭 意图理解</b>
        <p>
          类型 <code>{asStr(intent.intent_type)}</code> · 对象{" "}
          {asStr(intent.target) || "—"} · 指标 {asList(intent.metric_hints).join("、") || "—"}
        </p>
        <p>
          时间：{asStr(tr.start_date) || "不限"} ~ {asStr(tr.end_date) || "不限"}
        </p>
      </>
    );
  } else if (step === "schema_retrieval") {
    body = (
      <>
        <b>🔎 Schema 检索</b>
        <p>
          表：{asList(ev.tables).slice(0, 6).join("、")} · 命中口径 {asStr(ev.metrics_hit)} 条 ·
          参考示例 {asStr(ev.examples_hit)} 个 · JOIN 建议 {asStr(ev.join_hops)} 跳
        </p>
      </>
    );
  } else if (step === "sql_gen" || step === "drill_sql_gen" || step.startsWith("repair")) {
    let tag = "⚙️ SQL 生成";
    if (step.startsWith("repair")) tag = `🔧 SQL 修复 #${step.split("_")[1]}`;
    else if (step === "drill_sql_gen") tag = `⚙️ 下钻 SQL（${asStr(ev.hop_dimension)}）`;
    const note = ev.with_error_feedback ? "（带错误反馈重写）" : "";
    body = (
      <>
        <b>{tag}</b>
        {note && <span className="note">{note}</span>}
        {asStr(ev.explanation) && <p>{asStr(ev.explanation)}</p>}
      </>
    );
  } else if (step === "validate") {
    const ok = Boolean(ev.ok);
    const errs = ok ? "" : "：" + asList(ev.errors).join("；");
    body = (
      <b className={ok ? "ok" : "bad"}>
        🛡️ SQL 校验 {ok ? "✅" : `❌${errs}`}
      </b>
    );
  } else if (step === "execute") {
    body = ev.ok ? (
      <b className="ok">
        ⚡ 执行成功 — {asStr(ev.row_count)} 行 / {asStr(ev.elapsed_ms)}ms
      </b>
    ) : (
      <b className="bad">⚡ 执行失败 — {asStr(ev.error)}</b>
    );
  } else if (step === "analyze") {
    const detected = Boolean(ev.detected);
    body = (
      <b>
        {detected ? "🚨" : "📈"} 异常检测 — {asStr(ev.summary) || "未检出显著异常"}
      </b>
    );
  } else if (step === "drilldown_decision") {
    body =
      ev.action === "drill" ? (
        <>
          <b>
            🕵️ 下钻决策 → {asStr(ev.dimension)}（深度 {asStr(ev.depth)}，信息增益{" "}
            {asStr(ev.info_gain)}）
          </b>
          <p>假设：{asStr(ev.hypothesis) || "—"}</p>
        </>
      ) : (
        <b>🕵️ 下钻决策 → 停止（{asStr(ev.reason)}）</b>
      );
  } else if (step === "answer") {
    body = <b>💡 结论生成</b>;
  } else if (step === "fail") {
    body = <b className="bad">🛑 流程终止 — {asStr(ev.reason)}</b>;
  }

  return (
    <li className="step">
      <span className="idx">{index}</span>
      <div className="step-body">{body}</div>
    </li>
  );
}

/** 右栏：Agent Trace 实时执行时间线（SSE node 事件逐条追加） */
export default function TracePanel({
  events,
  running,
}: {
  events: TraceEvent[];
  running: boolean;
}) {
  return (
    <aside className="trace-panel">
      <h2>
        🧭 Agent Trace {running && <span className="live">● 执行中</span>}
      </h2>
      {events.length === 0 && (
        <p className="empty">提问后，这里按节点实时展示执行轨迹</p>
      )}
      <ol className="trace-list">
        {events.map((ev, i) => (
          <Step key={i} ev={ev} index={i + 1} />
        ))}
      </ol>
    </aside>
  );
}
