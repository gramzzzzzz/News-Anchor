import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type SourceDetail } from "../api";
import { ago, bandText, BAND_NOTE, confLabel, TYPE_LABEL, TYPE_NOTE } from "../format";
import { Float } from "./Float";
import { InfoIcon } from "./icons";

/** Source name with an information icon. Hover, focus or tap opens the source card. */
export function SourceInfo({ skey, name, plain }: { skey: string; name: string; plain?: boolean }) {
  const btn = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const show = () => { window.clearTimeout(timer.current); timer.current = window.setTimeout(() => setOpen(true), 120); };
  const hide = () => { window.clearTimeout(timer.current); timer.current = window.setTimeout(() => setOpen(false), 160); };
  return (
    <span className="src" onMouseEnter={show} onMouseLeave={hide}>
      {!plain && name}
      <button ref={btn} className="info" aria-label={`About ${name}`} aria-expanded={open} onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }} onFocus={show} onBlur={hide}>
        <InfoIcon />
      </button>
      {open && <Float anchor={btn.current} className="card" onEnter={() => window.clearTimeout(timer.current)} onLeave={hide}><SourceCard skey={skey} /></Float>}
    </span>
  );
}

function SourceCard({ skey }: { skey: string }) {
  const q = useQuery({ queryKey: ["source", skey], queryFn: () => api<SourceDetail>(`/sources/${skey}`), staleTime: 120_000 });
  if (!q.data) return <p className="muted">Loading…</p>;
  const { source: s, signals: sig, feeds: f } = q.data;
  const band = s.band;
  return (
    <>
      <h5>{s.name}</h5>
      <span className="dom">{s.domain}</span>
      <dl>
        <dt>Reliability</dt>
        <dd>
          <span className={`band ${band}${s.trust_basis === "estimated" ? " est" : ""}`}>{bandText(band, s.trust_basis)}</span>
          {s.score != null && band !== "unrated" && <> · score {s.score.toFixed(2)} of 1 · {confLabel(s.confidence)} confidence</>}
          {s.override_band && <> · set by you</>}
          <br /><span className="muted">{s.override_band ? "You chose this band; it overrides the evidence." : s.trust_basis === "estimated" ? "No expert rating covers this source: the band is estimated from measured evidence (below) and is not counted for Confirmed." : BAND_NOTE[band]}</span>
        </dd>
        <dt>Source type</dt><dd><b>{TYPE_LABEL[s.type] ?? s.type}</b><br /><span className="muted">{TYPE_NOTE[s.type]}</span></dd>
        {s.owner && (<><dt>Owner</dt><dd>{s.owner}</dd></>)}
        <dt>Based in</dt><dd>{s.country === "UN" ? "International organisation" : s.country}</dd>
        <dt>Feeds</dt><dd>{f.filter((x) => x.state === "active" && x.fail_count === 0).length} of {f.length} working. Last fetched {ago(f.map((x) => x.last_ok_at).filter(Boolean).sort().pop())}.</dd>
      </dl>
      {sig.length > 0 && (
        <div className="ev">
          {sig.slice(0, 5).map((x) => (
            <div key={x.signal}>
              <b>{x.label}</b>{" "}
              {x.evidence_url ? <a className="link" href={x.evidence_url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>source</a> : null}
              <div className="d">{x.detail}</div>
            </div>
          ))}
        </div>
      )}
      {s.provisional && <p className="prov">Not evaluated yet. The trust crawler has not run since this source was added.</p>}
      <p className="muted" style={{ marginTop: 12, fontSize: 12.5 }}>Checked {ago(s.trust_updated_at)}. <Link className="link" to={`/sources?open=${skey}`}>All evidence and how it is weighed</Link></p>
    </>
  );
}
