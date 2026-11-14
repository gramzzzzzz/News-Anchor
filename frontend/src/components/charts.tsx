import { BAND_LABEL } from "../format";

const fmtT = (iso: string, bucket: string) => {
  const d = new Date(iso);
  return bucket === "hour" ? d.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
};

/** Articles over time. Each bar is split by how reliable the publishers are rated. */
export function Timeline({ points, bucket, height = 150 }: { points: { t: string; n: number; reliable: number; weak: number; unrated: number }[]; bucket: string; height?: number }) {
  const W = 1000, H = height, pad = 22;
  const max = Math.max(1, ...points.map((p) => p.n));
  const bw = W / Math.max(1, points.length);
  const y = (n: number) => ((H - pad) * n) / max;
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={`Articles per ${bucket}, most ${max}`}>
        <line x1="0" x2={W} y1={H - pad} y2={H - pad} className="axis" />
        {points.map((p, i) => {
          const x = i * bw + bw * 0.12, w = bw * 0.76;
          const a = y(p.reliable), b = y(p.unrated), c = y(p.weak);
          const other = Math.max(0, y(p.n) - a - b - c);
          return (
            <g key={p.t}>
              <title>{`${fmtT(p.t, bucket)}: ${p.n} articles (${p.reliable} reliable, ${p.unrated} unrated, ${p.weak} mixed or low)`}</title>
              <rect x={x} y={H - pad - a} width={w} height={a} className="b-rel" />
              <rect x={x} y={H - pad - a - b} width={w} height={b} className="b-unr" />
              <rect x={x} y={H - pad - a - b - c} width={w} height={c} className="b-weak" />
              {other > 0 && <rect x={x} y={H - pad - a - b - c - other} width={w} height={other} className="b-unr" />}
            </g>
          );
        })}
      </svg>
      <div className="axis-l"><span>{points.length ? fmtT(points[0].t, bucket) : ""}</span><span>peak {max} per {bucket}</span><span>{points.length ? fmtT(points[points.length - 1].t, bucket) : ""}</span></div>
      <div className="legend small"><span><i className="b-rel" />Reliable or highly reliable</span><span><i className="b-unr" />Unrated</span><span><i className="b-weak" />Mixed, low or blocked</span></div>
    </figure>
  );
}

const COLORS = ["#4a8ad6", "#d9a441", "#5fc39c", "#e5566f"];
export function Lines({ series, bucket }: { series: { q: string; total: number; points: { t: string; n: number }[] }[]; bucket: string }) {
  const W = 1000, H = 170, pad = 22;
  const max = Math.max(1, ...series.flatMap((s) => s.points.map((p) => p.n)));
  const n = Math.max(1, (series[0]?.points.length ?? 1) - 1);
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Comparison of article volume over time">
        <line x1="0" x2={W} y1={H - pad} y2={H - pad} className="axis" />
        {series.map((s, k) => (
          <polyline key={s.q} fill="none" stroke={COLORS[k % 4]} strokeWidth="2.5" vectorEffect="non-scaling-stroke"
                    points={s.points.map((p, i) => `${(i / n) * W},${H - pad - ((H - pad) * p.n) / max}`).join(" ")} />
        ))}
      </svg>
      <div className="axis-l"><span>{series[0]?.points.length ? fmtT(series[0].points[0].t, bucket) : ""}</span><span>peak {max} per {bucket}</span><span>{series[0]?.points.length ? fmtT(series[0].points[series[0].points.length - 1].t, bucket) : ""}</span></div>
      <div className="legend small">{series.map((s, k) => <span key={s.q}><i style={{ background: COLORS[k % 4] }} />{s.q} ({s.total})</span>)}</div>
    </figure>
  );
}

export function Bars({ rows, onPick, tone }: { rows: { key: string; label: string; n: number; note?: string; cls?: string }[]; onPick?: (key: string) => void; tone?: string }) {
  const max = Math.max(1, ...rows.map((r) => r.n));
  if (!rows.length) return <p className="muted">Nothing in this selection.</p>;
  return (
    <ul className="bars">
      {rows.map((r) => (
        <li key={r.key}>
          <button disabled={!onPick} onClick={() => onPick?.(r.key)} title={onPick ? `Only ${r.label}` : undefined}>
            <span className="lbl">{r.label}{r.note && <em>{r.note}</em>}</span>
            <span className="track"><i className={r.cls ?? tone} style={{ width: `${(100 * r.n) / max}%` }} /></span>
            <span className="n">{r.n}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

export const bandRows = (rows: { band: string; n: number }[]) => rows.map((r) => ({ key: r.band, label: BAND_LABEL[r.band] ?? r.band, n: r.n, cls: `bd ${r.band}` }));

export function Spark({ values }: { values: number[] }) {
  const max = Math.max(1, ...values), W = 300, H = 46;
  return <svg viewBox={`0 0 ${W} ${H}`} className="spark" preserveAspectRatio="none" aria-hidden="true"><polyline fill="none" stroke="var(--accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" points={values.map((v, i) => `${(i / Math.max(1, values.length - 1)) * W},${H - 2 - ((H - 4) * v) / max}`).join(" ")} /></svg>;
}
