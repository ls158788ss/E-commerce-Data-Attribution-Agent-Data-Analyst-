import type { ChartSpec } from "../types";

/**
 * 无第三方依赖的迷你 SVG 图表：
 * rows 第一列作 X 轴、最后一列作 Y 轴（与 Streamlit 版 px.line/px.bar 的取列规则一致）。
 */
export default function Chart({ spec }: { spec: ChartSpec }) {
  const rows = spec.rows ?? [];
  if (rows.length < 2) return null;

  const keys = Object.keys(rows[0]);
  if (keys.length < 2) return null;
  const xKey = keys[0];
  const yKey = keys[keys.length - 1];

  const pts = rows
    .map((r) => ({ x: String(r[xKey] ?? ""), y: Number(r[yKey] ?? 0) }))
    .filter((p) => Number.isFinite(p.y));
  if (pts.length < 2) return null;

  const W = 640;
  const H = 240;
  const L = 56;
  const R = 16;
  const T = 14;
  const B = 40;

  const ys = pts.map((p) => p.y);
  const yMin = Math.min(0, ...ys);
  const yMax = Math.max(...ys);
  const span = yMax - yMin || 1;

  const sx = (i: number) => L + (i * (W - L - R)) / (pts.length - 1);
  const sy = (v: number) => T + (1 - (v - yMin) / span) * (H - T - B);

  const barW = Math.max(4, Math.min(28, (W - L - R) / pts.length - 4));
  const gridY = [0, 1, 2, 3, 4].map((k) => yMin + (span * k) / 4);
  const labelEvery = Math.max(1, Math.ceil(pts.length / 6)); // X 轴最多 6 个标签

  return (
    <figure className="chart">
      <figcaption>{spec.title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img">
        {gridY.map((v, k) => (
          <g key={k}>
            <line x1={L} x2={W - R} y1={sy(v)} y2={sy(v)} className="grid" />
            <text x={L - 6} y={sy(v) + 4} className="tick" textAnchor="end">
              {fmtNum(v)}
            </text>
          </g>
        ))}

        {spec.kind === "bar"
          ? pts.map((p, i) => (
              <rect
                key={i}
                x={sx(i) - barW / 2}
                y={sy(p.y)}
                width={barW}
                height={Math.max(0, H - B - sy(p.y))}
                className="bar"
              />
            ))
          : (
            <polyline
              points={pts.map((p, i) => `${sx(i)},${sy(p.y)}`).join(" ")}
              className="line"
            />
          )}

        {pts.map((p, i) => (
          <circle key={i} cx={sx(i)} cy={sy(p.y)} r={3} className="dot" />
        ))}

        {pts.map((p, i) =>
          i % labelEvery === 0 || i === pts.length - 1 ? (
            <text key={i} x={sx(i)} y={H - B + 16} className="tick" textAnchor="middle">
              {p.x}
            </text>
          ) : null,
        )}
      </svg>
    </figure>
  );
}

function fmtNum(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
  if (abs >= 1_000) return (v / 1_000).toFixed(1) + "k";
  return String(Math.round(v * 100) / 100);
}
