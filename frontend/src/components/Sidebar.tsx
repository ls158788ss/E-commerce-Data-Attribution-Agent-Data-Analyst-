import type { SemanticSummary } from "../types";

/** 左栏：数据资产（语义层摘要，来自 GET /api/semantic/summary） */
export default function Sidebar({ summary }: { summary: SemanticSummary | null }) {
  return (
    <aside className="sidebar">
      <h2>📊 数据资产</h2>

      {summary === null && (
        <p className="empty">加载中…（若长时间空白，请确认 API 服务已启动）</p>
      )}

      {summary && (
        <>
          <details open>
            <summary>
              指标定义（语义层）· {summary.metrics.length}
            </summary>
            <ul className="asset-list">
              {summary.metrics.map((m) => (
                <li key={m.business_name}>
                  <code>{m.business_name}</code>
                  <span>{clip(m.definition, 42)}</span>
                </li>
              ))}
            </ul>
          </details>

          <details>
            <summary>数据表 · {summary.tables.length}</summary>
            <ul className="asset-list">
              {summary.tables.map((t) => (
                <li key={t.table}>
                  <b>{t.table}</b>
                  <span>
                    {t.column_count} 列 · {clip(t.description, 40)}
                  </span>
                </li>
              ))}
            </ul>
          </details>

          <details>
            <summary>业务术语 · {summary.glossary.length}</summary>
            <ul className="asset-list">
              {summary.glossary.slice(0, 8).map((g) => (
                <li key={g.term}>
                  <code>{g.term}</code>
                  <span>{clip(g.definition, 36)}</span>
                </li>
              ))}
            </ul>
          </details>
        </>
      )}

      <div className="sidebar-foot">
        后端 FastAPI（REST + SSE）<br />
        前端 React + TypeScript
      </div>
    </aside>
  );
}

function clip(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}
