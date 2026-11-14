import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type DiscoveryStatus, type SourceDetail, type SourcesPage, type TrustMethod } from "../api";
import { ago, BAND_LABEL, bandText, BAND_NOTE, confLabel, countryName, plural, STATUS_LABEL, STATUS_NOTE, TYPE_LABEL, TYPE_NOTE } from "../format";

const PAGE = 50;
const STATUSES = ["active", "candidate", "degraded", "dormant", "parked", "swapped", "no_feed", "retired"];

export default function Sources() {
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const [f, setF] = useState({ q: "", status: sp.get("status") ?? "", band: sp.get("band") ?? "", type: sp.get("type") ?? "", country: sp.get("country") ?? "", language: sp.get("language") ?? "", origin: sp.get("origin") ?? "", sort: sp.get("band") ? "score" : "name" });
  const [text, setText] = useState("");
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState<string | null>(sp.get("open"));
  useEffect(() => { const t = window.setTimeout(() => { setF((o) => ({ ...o, q: text })); setPage(0); }, 300); return () => window.clearTimeout(t); }, [text]);
  useEffect(() => { if (sp.get("open")) setOpen(sp.get("open")); }, [sp]);
  const qs = new URLSearchParams({ ...f, limit: String(PAGE), offset: String(page * PAGE), facets: "true" });
  const q = useQuery({ queryKey: ["sources", f, page], queryFn: () => api<SourcesPage>(`/sources?${qs}`), refetchInterval: 60_000, placeholderData: (p) => p });
  const method = useQuery({ queryKey: ["trustMethod"], queryFn: () => api<TrustMethod>("/trust/method"), refetchInterval: (qq) => (qq.state.data?.running ? 3000 : 60_000) });
  const disc = useQuery({ queryKey: ["discovery"], queryFn: () => api<DiscoveryStatus>("/discovery"), refetchInterval: (qq) => (qq.state.data?.running ? 3000 : 20_000) });
  const refresh = useMutation({ mutationFn: () => api("/jobs/refresh-trust", { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["trustMethod"] }) });
  const find = useMutation({ mutationFn: () => api("/jobs/discover-sources", { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["discovery"] }) });
  const web = useMutation({ mutationFn: () => api("/jobs/discover-web", { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["discovery"] }) });
  const sourcesChanged = () => { qc.invalidateQueries({ queryKey: ["sources"] }); qc.invalidateQueries({ queryKey: ["discovery"] }); };
  const requeue = useMutation({ mutationFn: () => api<{ requeued: number }>("/sources/no-feed/requeue", { method: "POST" }), onSuccess: sourcesChanged });
  const clearNoFeed = useMutation({ mutationFn: () => api<{ cleared: number }>("/sources/no-feed/clear", { method: "POST" }), onSuccess: sourcesChanged });
  const feeds = useMutation({ mutationFn: () => api("/jobs/find-feeds", { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["discovery"] }) });
  useEffect(() => { if (method.data && !method.data.running) qc.invalidateQueries({ queryKey: ["sources"] }); }, [method.data?.running, method.data?.last_run?.at, qc]);
  useEffect(() => { if (disc.data && !disc.data.running) qc.invalidateQueries({ queryKey: ["sources"] }); }, [disc.data?.running, qc]);
  useEffect(() => { if (open && q.data) window.setTimeout(() => document.getElementById(`src-${open}`)?.scrollIntoView({ block: "start" }), 60); }, [open, q.data]);

  const rows = q.data?.sources ?? [];
  const total = q.data?.total ?? 0;
  const fac = q.data?.facets;
  const m = method.data, d = disc.data;
  const running = refresh.isPending || m?.running;
  const searching = requeue.isPending || !!d?.batch_busy || (d?.pending_feed_search ?? 0) > 0;     // until every source waiting for a feed search has been searched
  const set = (k: string, v: string) => { setF((o) => ({ ...o, [k]: v })); setPage(0); };
  const sum = (o?: Record<string, number>) => Object.values(o ?? {}).reduce((a, b) => a + b, 0);
  const pinned = open && !rows.some((r) => r.key === open);

  return (
    <div className="page"><div className="sources">
      <h1 style={{ fontSize: 26, lineHeight: "32px", color: "var(--head)", fontWeight: 600 }}>Sources</h1>
      <p className="muted" style={{ marginTop: 6, maxWidth: "74ch" }}>
        News Anchor shows every story from every source it collects, each marked with how reliable its publisher is rated, so you decide what to trust. The registry starts from a hand-checked list and keeps growing by itself from Media Cloud's directory, Wikidata and web searches for the subjects and places it covers thinnest; each new source's feed is checked before anything is collected from it.
      </p>
      <div className="tiles">
        <div><b>{sum(fac?.status).toLocaleString()}</b><span>sources known</span></div>
        <div><b>{(fac?.status.active ?? 0).toLocaleString()}</b><span>collecting now</span></div>
        <div><b>{(d?.pending_feed_search ?? fac?.status.candidate ?? 0).toLocaleString()}</b><span>waiting for a feed check</span></div>
        <div><b>{(fac?.status.no_feed ?? 0).toLocaleString()}</b><span>no feed found yet</span></div>
        <div><b>{((fac?.status.dormant ?? 0) + (fac?.status.degraded ?? 0)).toLocaleString()}</b><span>failing or dormant</span></div>
      </div>
      <div className="toolbar">
        <button className="ghost" disabled={!!d?.directory_busy || find.isPending} onClick={() => find.mutate()}>{d?.directory_busy ? "Reading directories…" : "Find new sources now"}</button>
        <button className="ghost" disabled={!!d?.web_running || web.isPending} onClick={() => web.mutate()}>{d?.web_running ? "Searching the web…" : "Search the web for sources"}</button>
        <button className="ghost" disabled={!!d?.batch_busy || feeds.isPending || !(d?.pending_feed_search)} onClick={() => feeds.mutate()}>Check feeds of waiting sources</button>
        <button className="ghost" disabled={searching || !(fac?.status.no_feed)} title="Make every source without a feed due for a fresh feed search now: its advertised and usual feed addresses, Media Cloud's list, its Google News sitemap, then its home page. They are searched in turn by the ordinary feed checks, and this button stays off until the searching is done."
          onClick={() => { if (window.confirm(`Search again for the feeds of all ${(fac?.status.no_feed ?? 0).toLocaleString()} sources without a feed?`)) requeue.mutate(); }}>
          {requeue.isPending ? "Queuing…" : searching ? `Searching… ${(d?.pending_feed_search ?? 0).toLocaleString()} left` : "Search again for all without a feed"}</button>
        <button className="ghost" disabled={clearNoFeed.isPending || !(fac?.status.no_feed)} title="Delete every discovered source that has no feed. Their sites are remembered, so discovery does not add them again."
          onClick={() => { if (window.confirm(`Delete all ${(fac?.status.no_feed ?? 0).toLocaleString()} sources without a feed? Their sites will not be added again by discovery.`)) clearNoFeed.mutate(); }}>
          {clearNoFeed.isPending ? "Clearing…" : "Clear all without a feed"}</button>
        <span className="muted">{d?.last_directory ? `Directories read ${ago(d.last_directory.at)}${d.last_directory.added ? `: ${d.last_directory.added.mediacloud} from Media Cloud, ${d.last_directory.added.wikidata} from Wikidata added` : ""}.` : "Directories not read yet."}
          {d?.last_web && !d.last_web.error ? ` Web search ${ago(d.last_web.at)}: ${d.last_web.queries} questions, ${d.last_web.promoted} new publishers (${d.web?.seen ?? 0} seen so far, ${d.web?.waiting ?? 0} waiting for a second sighting).` : ""}
          {requeue.data ? ` ${requeue.data.requeued.toLocaleString()} sources queued for a new feed search.` : ""}{clearNoFeed.data ? ` ${clearNoFeed.data.cleared.toLocaleString()} sources cleared.` : ""}
          {d?.last_batch && d.last_batch.searched !== undefined ? ` Last feed check ${ago(d.last_batch.at)}: ${d.last_batch.found} found of ${d.last_batch.searched}.` : ""}</span>
      </div>
      {d && !d.mediacloud_token && <p className="note">Media Cloud's directory is not used because no API token is set. Add a free token in <Link className="link" to="/settings#sources">Settings → Sources</Link> to bring in thousands of outlets; Wikidata works without one.</p>}
      {d?.last_error && <p className="note">The last discovery run failed: {d.last_error}. It is retried within the hour.</p>}
      {!!d?.last_directory?.notes?.length && (
        <details className="note"><summary>{d.last_directory.notes.length === 1 ? d.last_directory.notes[0] : `Last directory read: ${d.last_directory.notes.length} notes`}</summary>
          {d.last_directory.notes.length > 1 && d.last_directory.notes.map((n) => <p key={n} className="muted small">{n}</p>)}</details>)}
      <div className="toolbar">
        <button className="ghost" disabled={!!running} onClick={() => refresh.mutate()}>{running ? "Checking sources…" : "Refresh trust now"}</button>
        <span className="muted">{m?.last_run ? `Last trust crawl ${ago(m.last_run.at)}, ${m.last_run.seconds} s` : "Trust not crawled yet"}. {m && m.floor !== "all" ? <>Your view hides sources rated below {BAND_LABEL[m.floor]}. <Link className="link" to="/settings#sources">Change</Link></> : "Nothing is hidden."}</span>
      </div>
      <div className="toolbar">
        <input className="input" placeholder="Search name or site" value={text} onChange={(e) => setText(e.target.value)} aria-label="Search sources" />
        <select className="select" value={f.status} onChange={(e) => set("status", e.target.value)} aria-label="Stage"><option value="">Any stage</option>{STATUSES.map((t) => <option key={t} value={t}>{STATUS_LABEL[t]} ({fac?.status[t] ?? 0})</option>)}</select>
        <select className="select" value={f.band} onChange={(e) => set("band", e.target.value)} aria-label="Reliability band"><option value="">Any reliability</option>{Object.keys(BAND_LABEL).map((b) => <option key={b} value={b}>{BAND_LABEL[b]} ({fac?.band[b] ?? 0})</option>)}</select>
        <select className="select" value={f.type} onChange={(e) => set("type", e.target.value)} aria-label="Source type"><option value="">Any type</option>{Object.keys(TYPE_LABEL).map((t) => <option key={t} value={t}>{TYPE_LABEL[t]} ({fac?.type[t] ?? 0})</option>)}</select>
        <select className="select" value={f.country} onChange={(e) => set("country", e.target.value)} aria-label="Country"><option value="">Any country</option>{Object.entries(fac?.country ?? {}).map(([c, n]) => <option key={c} value={c}>{countryName(c)} ({n})</option>)}</select>
        <select className="select" value={f.origin} onChange={(e) => set("origin", e.target.value)} aria-label="Found through"><option value="">Any origin</option><option value="starter">Hand-checked list</option><option value="mediacloud">Media Cloud</option><option value="wikidata">Wikidata</option><option value="web">Web search</option></select>
        <select className="select" value={f.language} onChange={(e) => set("language", e.target.value)} aria-label="Language"><option value="">Any language</option>{Object.entries(fac?.language ?? {}).map(([c, n]) => <option key={c} value={c}>{c} ({n})</option>)}</select>
        <select className="select" value={f.sort} onChange={(e) => set("sort", e.target.value)} aria-label="Sort"><option value="name">Sort by name</option><option value="score">Most reliable first</option><option value="volume">Busiest first</option><option value="recent">Most recent first</option></select>
        <span className="muted">{total.toLocaleString()} match</span>
      </div>
      {pinned && <div className="subtle" style={{ margin: "10px 0" }}><h4>Selected source</h4><Detail skey={open!} /></div>}
      <table className="table">
        <thead><tr><th>Source</th><th>Reliability</th><th>Type</th><th className="hide-sm">Country</th><th>Stage</th><th className="num">Feeds working</th><th className="hide-sm">Last fetched</th></tr></thead>
        <tbody>
          {rows.map((s) => (
            <Fragment key={s.key}>
              <tr id={`src-${s.key}`} className="click" style={{ scrollMarginTop: 12 }} onClick={() => { const o = open === s.key ? null : s.key; setOpen(o); setSp(o ? { open: o } : {}, { replace: true }); }} aria-expanded={open === s.key}>
                <td><b>{s.name}</b><br /><span className="muted">{s.domain}</span></td>
                <td>
                  <span className={`band ${s.band}${s.trust_basis === "estimated" ? " est" : ""}`}>{bandText(s.band, s.trust_basis)}</span>{s.override_band && <span className="muted"> · set by you</span>}
                  <br /><span className="muted">{s.score != null && s.band !== "unrated" ? `${s.score.toFixed(2)} · ${confLabel(s.confidence)} confidence` : s.band === "unrated" ? "No rating yet" : ""}{!s.visible ? ` · hidden by your filter` : ""}</span>
                </td>
                <td title={TYPE_NOTE[s.type]}>{TYPE_LABEL[s.type] ?? s.type}</td>
                <td className="hide-sm">{countryName(s.country)}</td>
                <td title={STATUS_NOTE[s.status]}><span className={`pill ${s.status === "active" ? "ok" : s.status === "candidate" ? "" : "bad"}`}>{STATUS_LABEL[s.status] ?? s.status}</span></td>
                <td className="num">{s.feeds ? <span className={`pill ${s.feeds_active === s.feeds ? "ok" : "bad"}`}>{s.feeds_active} of {s.feeds}</span> : <span className="muted">none</span>}</td>
                <td className="hide-sm">{ago(s.last_ok_at)}</td>
              </tr>
              {open === s.key && <tr><td colSpan={7}><Detail skey={s.key} /></td></tr>}
            </Fragment>
          ))}
          {!rows.length && !q.isPending && <tr><td colSpan={7} className="muted">No source matches these filters.</td></tr>}
        </tbody>
      </table>
      <div className="toolbar">
        <button className="ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
        <span className="muted">Page {page + 1} of {Math.max(1, Math.ceil(total / PAGE))}</span>
        <button className="ghost" disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)}>Next</button>
      </div>
      {m && (
        <details className="method" id="method">
          <summary>How a reliability band is worked out</summary>
          <p className="muted" style={{ margin: "10px 0", maxWidth: "74ch" }}>
            Each piece of evidence becomes a value between 0 and 1 with a weight. The score is the weighted average, starting from a neutral 0.5 that counts as one piece of evidence, so thin evidence cannot produce extreme scores.
            Confidence rises as total weight grows. {m.learned ? "Each weight is learned daily from how well that kind of evidence agrees with all the others across every source (a one-factor model, as in truth discovery), shrunk toward its starting weight when measured on few sources; measured evidence is put on the experts' scale first. Turn this off in Settings → Trust to set the weights yourself." : "Weights and thresholds are settings."} The band is a label to help you decide, never a reason to hide a story: everything is shown unless you choose a filter.
          </p>
          <table className="table">
            <thead><tr><th>Evidence</th><th>Weight</th>{m.learned && <th>How well it agrees with the rest</th>}<th>What it is</th></tr></thead>
            <tbody>{m.signals.map((x) => <tr key={x.key}><td><b>{x.label}</b>{x.expert && <span className="muted"> · expert</span>}</td><td>{x.weight.toFixed(2)}</td>
              {m.learned && <td className="muted">{x.learned?.reliability != null ? `${Math.round(x.learned.reliability * 100)}% of its spread is shared with the other evidence (${x.learned.n} sources; starting weight ${x.learned.prior})` : "Not measured yet"}</td>}
              <td className="muted">{x.help}</td></tr>)}</tbody>
          </table>
          <p style={{ margin: "10px 0 4px" }}>Bands: Highly reliable from {m.thresholds.highly} (and medium confidence), Reliable from {m.thresholds.reliable}, Mixed from {m.thresholds.mixed}, Low below that.</p>
          <ul className="muted" style={{ paddingLeft: 18, listStyle: "disc" }}>{m.rules.map((r) => <li key={r}>{r}</li>)}</ul>
          {m.estimate && <p style={{ margin: "10px 0 4px" }}>Estimates ({ago(m.estimate.at)}): {m.estimate.estimated} sources without an expert rating have an estimated band.
            {m.estimate.eval?.sources ? ` Tested on ${m.estimate.eval.sources} sources that experts did rate, using only the measured evidence: the estimated band matches the experts' band for ${Math.round((m.estimate.eval.band_agreement ?? 0) * 100)}%${m.estimate.eval.spearman != null ? `, rank correlation ${m.estimate.eval.spearman}` : ""}.` : ""}
            {" "}<button className="link" onClick={() => api("/jobs/estimate-trust", { method: "POST" })}>Estimate again now</button></p>}
          <p className="muted" style={{ marginTop: 8 }}>Not used: Journalism Trust Initiative and IFCN lists (no machine-readable list), fact-check verdicts about a source's content, and any attempt to detect plagiarism or AI-written text, which no detector does reliably enough to put against a publisher's name.</p>
        </details>
      )}
    </div></div>
  );
}

function Detail({ skey }: { skey: string }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["source", skey], queryFn: () => api<SourceDetail>(`/sources/${skey}`) });
  const override = useMutation({
    mutationFn: (band: string | null) => api(`/sources/${skey}/override`, { method: "PUT", body: { band } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["source", skey] }); qc.invalidateQueries({ queryKey: ["sources"] }); qc.invalidateQueries({ queryKey: ["stories"] }); },
  });
  const recheck = useMutation({ mutationFn: () => api(`/sources/${skey}/recheck`, { method: "POST" }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["source", skey] }); qc.invalidateQueries({ queryKey: ["sources"] }); qc.invalidateQueries({ queryKey: ["discovery"] }); } });
  if (!q.data) return <p className="muted">Loading…</p>;
  const { source: s, signals, feeds, history } = q.data;
  return (
    <div className="subtle">
      {(s.status !== "active" || s.origin !== "starter") && (
        <div>
          <h4>Stage: {STATUS_LABEL[s.status] ?? s.status}</h4>
          <p className="muted">{STATUS_NOTE[s.status]} {s.status_note} {s.origin && s.origin !== "starter" ? `Found through ${s.origin === "mediacloud" ? "Media Cloud" : s.origin === "wikidata" ? "Wikidata" : s.origin === "web" ? "a web search" : s.origin}.` : ""}</p>
          {!s.curated && ["retired", "dormant", "no_feed", "degraded", "swapped"].includes(s.status) && <button className="ghost" disabled={recheck.isPending} onClick={() => recheck.mutate()}>Look for its feed again</button>}
        </div>)}
      <div>
        <h4>Evidence</h4>
        {signals.length === 0 ? <p className="muted">No evidence collected yet. Press “Refresh trust now”.</p> : (
          <table className="table">
            <thead><tr><th>Evidence</th><th>Value</th><th className="num">Weight</th><th>What was found</th></tr></thead>
            <tbody>{signals.map((x) => (
              <tr key={x.signal}>
                <td><b>{x.label}</b>{x.expert && <span className="muted"> · expert</span>}</td>
                <td><span className="vbar"><i style={{ width: `${x.value * 100}%` }} /></span>{x.value.toFixed(2)}</td>
                <td className="num">{x.weight.toFixed(2)}</td>
                <td>{x.detail} {x.evidence_url && <a className="link" href={x.evidence_url} target="_blank" rel="noopener noreferrer">source</a>}{x.evidence_date && <span className="muted"> · {x.evidence_date}</span>}</td>
              </tr>))}</tbody>
          </table>)}
        <p className="muted" style={{ marginTop: 8 }}>{BAND_NOTE[s.band]} {s.trust_note} {s.owner ? `Owner on record: ${s.owner}.` : ""}</p>
      </div>
      <div>
        <h4>Your override</h4>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <select className="select" style={{ width: 220 }} value={s.override_band ?? ""} onChange={(e) => override.mutate(e.target.value || null)} aria-label={`Override reliability of ${s.name}`}>
            <option value="">Follow the evidence</option>
            {["highly_reliable", "reliable", "mixed", "low", "blocked"].map((b) => <option key={b} value={b}>{BAND_LABEL[b]}</option>)}
          </select>
          <span className="muted">Your choice beats the evidence and is logged below. It changes the label and the ranking weight; it never removes a story.</span>
        </div>
      </div>
      {history.length > 0 && (<div><h4>Changes</h4><ul>{history.slice(0, 5).map((h, i) => <li key={i} className="muted">{ago(h.at)}: {h.change} ({h.reason})</li>)}</ul></div>)}
      <div>
        <h4>{plural(feeds.length, "feed")}</h4>
        <table className="table">
          <thead><tr><th>Feed</th><th>Topic hints</th><th>Status</th><th className="num">Articles</th><th>Last new item</th></tr></thead>
          <tbody>{feeds.map((f) => (
            <tr key={f.id}>
              <td style={{ wordBreak: "break-all" }}><a className="link" href={f.url} target="_blank" rel="noopener noreferrer">{f.url.replace(/^https?:\/\/(www\.)?/, "")}</a></td>
              <td>{f.topics.join(", ") || "General"}{f.places.length ? ` · ${f.places.join(", ")}` : ""}</td>
              <td>{f.fail_count > 0 ? <span className="pill bad" title={f.last_error ?? ""}>{f.last_error ?? `HTTP ${f.last_status}`}</span> : f.last_status ? <span className="pill ok">Working</span> : <span className="pill">Not fetched yet</span>}</td>
              <td className="num">{f.items_total}</td>
              <td>{ago(f.last_new_at)}</td>
            </tr>))}</tbody>
        </table>
      </div>
    </div>
  );
}
