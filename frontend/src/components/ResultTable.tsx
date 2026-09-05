/** 通用结果表：columns 决定列顺序（对应后端 result_columns） */
export default function ResultTable({
  columns,
  rows,
}: {
  columns?: string[];
  rows?: Record<string, unknown>[];
}) {
  if (!rows || rows.length === 0) return null;
  const cols = columns && columns.length > 0 ? columns : Object.keys(rows[0]);

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c}>{fmtCell(r[c])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function fmtCell(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "number") {
    const abs = Math.abs(v);
    if (abs >= 1_000_000) return (v / 1_000_000).toFixed(2) + "M";
    if (abs >= 1_000) return (v / 1_000).toFixed(2) + "k";
    return String(Math.round(v * 100) / 100);
  }
  return String(v);
}
