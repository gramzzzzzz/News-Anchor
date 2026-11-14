import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type CompareResp, type ExploreResp, type IndexResp, type MediaCloudCtx, type SearchResp } from "../api";
import { Bars, bandRows, Lines, Spark, Timeline } from "../components/charts";
import { ago, BAND_LABEL, countryName, plural, TYPE_LABEL, VERIFY_LABEL } from "../format";

const PERIODS = [["1", "Last 24 hours"], ["3", "Last 3 days"], ["7", "Last 7 days"], ["30", "Last 30 days"], ["90", "Last 90 days"], ["365", "Last year"]] as const;
const BANDS = ["highly_reliable", "reliable", "mixed", "low", "unrated", "blocked"];
const TYPES = Object.keys(TYPE_LABEL);

interface Filters { q: string; days: string; place: string; topic: string; types: string[]; bands: string[]; countries: string[]; sources: string[] }
const EMPTY: Filters = { q: "", days: "7", place: "", topic: "", types: [], bands: [], countries: [], sources: [] };

const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

export default function Explore() {
  const [sp, setSp] = useSearchParams();
  const [f, setF] = useState<Filters>(() => ({ ...EMPTY, q: sp.get("q") ?? "", days: sp.get("days") ?? "7", topic: sp.get("topic") ?? "", place: sp.get("place") ?? "" }));
  const [applied, setApplied] = useState<Filters | null>(sp.get("q") ? f : null);
  const [offset, setOffset] = useState(0);
  const [compare, setCompare] = useState(false);
  const [cq, setCq] = useState(["", ""]);
  const [mc, setMc] = useState(false);
  const idx = useQuery({ queryKey: ["index"], queryFn: () => api<IndexResp>("/index"), staleTime: 300_000 });

  const body = (x: Filters, off = 0) => ({ q: x.q, days: Number(x.days), place: x.place, topic: x.topic, types: x.types, bands: x.bands, countries: x.countries, sources: x.sources, limit: 25, offset: off });
  const ex = useQuery({ queryKey: ["explore", applied, offset], enabled: applied !== null, placeholderData: (p) => p,
    queryFn: () => api<ExploreResp>("/explore", { method: "POST", body: body(applied!, offset) }) });
  const matches = useQuery({ queryKey: ["search", applied?.q, applied?.place, applied?.topic, applied?.days], enabled: !!applied?.q && applied.q.trim().length >= 2 && !compare,
    queryFn: () => api<SearchResp>(`/search?q=${encodeURIComponent(applied!.q)}&place=${encodeURIComponent(applied!.place)}&topic=${encodeURIComponent(applied!.topic)}&days=${Math.min(365, Number(applied!.days))}&limit=8`), retry: false });
  const cmp = useMutation({ mutationFn: (x: Filters) => api<CompareResp>("/explore/compare", { method: "POST", body: { ...body(x), queries: cq } }) });
  const mcq = useQuery({ queryKey: ["mc", applied?.q, applied?.days], enabled: mc && !!applied?.q,
    queryFn: () => api<MediaCloudCtx>(`/mediacloud/context?q=${encodeURIComponent(applied!.q)}&days=${Math.min(365, Number(applied!.days))}`), retry: false,
    refetchInterval: (q) => (q.state.data?.state === "running" ? 6000 : false) });

  const run = (next: Filters = f) => { setOffset(0); setApplied(next); setF(next); const n = new URLSearchParams(); if (next.q) n.set("q", next.q); if (next.days !== "7") n.set("days", next.days); if (next.place) n.set("place", next.place); if (next.topic) n.set("topic", next.topic); setSp(n, { replace: true }); if (compare && cq.filter((x) => x.trim()).length >= 2) cmp.mutate(next); };
  const narrow = (patch: Partial<Filters>) => run({ ...f, ...patch });
  const d = ex.data;
  const placeName = (c: string) => idx.data?.places.find((p) => p.code === c)?.name ?? c;
  const topicName = (c: string) => idx.data?.topics.find((t) => t.code === c)?.name ?? c;
  const active = [
    ...f.types.map((x) => ({ k: "types", v: x, l: TYPE_LABEL[x] ?? x })), ...f.bands.map((x) => ({ k: "bands", v: x, l: BAND_LABEL[x] ?? x })),
    ...f.countries.map((x) => ({ k: "countries", v: x, l: `Outlets in ${countryName(x)}` })), ...f.sources.map((x) => ({ k: "sources", v: x, l: `Only ${d?.sources.find((s) => s.key === x)?.name ?? x}` })),
  ];
  const drop = (k: string, v: string) => run({ ...f, [k]: (f as unknown as Record<string, string[]>)[k].filter((x) => x !== v) });
  const dl = useMemo(() => () => {
    if (!d) return;
    const rows = [["published_at", "source", "reliability", "type", "country", "title", "url"], ...d.articles.map((a) => [a.published_at, a.source_name, a.band, a.type, a.country, a.title.replace(/"/g, '""'), a.url])];
    const csv = rows.map((r) => r.map((c) => `"${c}"`).join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const a = document.createElement("a"); a.href = url; a.download = "news-anchor-explore.csv"; a.click(); URL.revokeObjectURL(url);
  }, [d]);
  useEffect(() => { if (applied) document.title = applied.q ? `${applied.q} · Explore` : "Explore"; }, [applied]);

  return (
    <div className="page"><div className="explore">
      <h1 className="h1">Explore</h1>
      <p className="muted lede2">Ask anything of everything News Anchor has collected: how much was written, when, by whom, where, and how reliable those publishers are rated. Click any bar to narrow the question.</p>
      <form className="qform" onSubmit={(e) => { e.preventDefault(); run(); }}>
        <div className="qrow">
          {!compare ? <input className="input q" placeholder='Search words, a name or a phrase, for example  "interest rates"  or  cyclone AND chennai' value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} aria-label="Query" autoFocus />
            : <div className="cq">{cq.map((v, i) => <input key={i} className="input" placeholder={`Query ${i + 1}`} value={v} onChange={(e) => setCq(cq.map((x, j) => (j === i ? e.target.value : x)))} aria-label={`Query ${i + 1}`} />)}
              {cq.length < 4 && <button type="button" className="link" onClick={() => setCq([...cq, ""])}>Add a query</button>}</div>}
          <button className="primary" type="submit">{compare ? "Compare" : "Explore"}</button>
        </div>
        <div className="frow">
          <select className="select" value={f.days} onChange={(e) => setF({ ...f, days: e.target.value })} aria-label="Period">{PERIODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          <input className="input" list="places" placeholder="Any place" defaultValue={f.place ? placeName(f.place) : ""} aria-label="Place"
                 onChange={(e) => { const m = idx.data?.places.find((p) => p.path === e.target.value || p.name === e.target.value); setF((o) => ({ ...o, place: m ? m.code : e.target.value === "" ? "" : o.place })); }} />
          <datalist id="places">{(idx.data?.places ?? []).filter((p) => p.level !== "world").slice(0, 400).map((p) => <option key={p.code} value={p.path}>{p.name}</option>)}</datalist>
          <input className="input" list="topics" placeholder="Any topic" defaultValue={f.topic ? topicName(f.topic) : ""} aria-label="Topic"
                 onChange={(e) => { const m = idx.data?.topics.find((t) => t.path === e.target.value || t.name === e.target.value); setF((o) => ({ ...o, topic: m ? m.code : e.target.value === "" ? "" : o.topic })); }} />
          <datalist id="topics">{(idx.data?.topics ?? []).map((t) => <option key={t.code} value={t.path}>{t.name}</option>)}</datalist>
          <label className="chk"><input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} /> Compare queries</label>
        </div>
        <div className="frow chips2" aria-label="Reliability of sources">
          {BANDS.map((b) => <button type="button" key={b} aria-pressed={f.bands.includes(b)} onClick={() => setF({ ...f, bands: toggle(f.bands, b) })}>{BAND_LABEL[b]}</button>)}
          <span className="sep" />
          {TYPES.map((t) => <button type="button" key={t} aria-pressed={f.types.includes(t)} onClick={() => setF({ ...f, types: toggle(f.types, t) })}>{TYPE_LABEL[t]}</button>)}
        </div>
        {active.length > 0 && <div className="frow">{active.map((a) => <button type="button" className="chip on" key={a.k + a.v} onClick={() => drop(a.k, a.v)}>{a.l} ×</button>)}</div>}
      </form>

      {applied === null && <div className="empty"><b>Ask a question</b>Try a name, a place or a phrase. With nothing typed, Explore summarises everything in the period and filters you set. Everything is counted from the articles stored on this server; the oldest day kept depends on your retention setting.</div>}
      {ex.isError && <div className="empty"><b>Could not run that</b>{(ex.error as Error).message}</div>}

      {compare && cmp.data && <section className="card2"><h3>Volume over time, by query</h3><Lines series={cmp.data.series} bucket={cmp.data.bucket} /></section>}
      {compare && cmp.isError && <p className="note">{(cmp.error as Error).message}</p>}

      {d && !compare && (
        <>
          {d.filters.retention_note && <p className="note">{d.filters.retention_note}</p>}
          <div className="tiles">
            <div><b>{d.totals.articles.toLocaleString()}</b><span>articles</span></div>
            <div><b>{d.totals.stories.toLocaleString()}</b><span>stories</span></div>
            <div><b>{d.totals.sources.toLocaleString()}</b><span>sources with articles in this period</span></div>
            <div><b>{d.totals.countries.toLocaleString()}</b><span>countries of those sources</span></div>
            <div><b>{d.share_reliable === null ? "–" : `${Math.round(d.share_reliable * 100)}%`}</b><span>from reliable sources</span></div>
          </div>
          <p className="muted small">Counts cover articles published in the period chosen above (the last 7 days by default), from the sources that published any in it. Status counts everything stored, and every source known, so its numbers are larger.</p>
          {d.totals.articles === 0 ? <div className="empty"><b>Nothing matches</b>Widen the period, remove a filter, or check the spelling. Search matches whole words and their stems (cyclones finds cyclone).</div> : (
            <>
              {applied?.q && matches.data && matches.data.stories.length > 0 && (
                <section className="card2"><h3>Best matching stories <span className="muted small">{matches.data.mode}</span></h3>
                  <ul className="mlist">{matches.data.stories.map((s) => (
                    <li key={s.id}><Link to={`/?story=${s.id}&place=world`}>{s.hit ? <span dangerouslySetInnerHTML={{ __html: s.hit.replace(/[<>&]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c] as string)).replace(/«/g, "<mark>").replace(/»/g, "</mark>") }} /> : s.title}</Link>
                      <span className="muted small"> {plural(s.outlets, "outlet")} · {ago(s.last_update)} · <span className={`vtag inline ${s.verification.level}`}>{VERIFY_LABEL[s.verification.level]}</span></span></li>))}</ul></section>)}
              <section className="card2"><h3>Articles over time</h3><Timeline points={d.timeline} bucket={d.filters.bucket} /></section>
              <div className="cols">
                <section className="card2"><h3>Who published</h3><Bars rows={d.sources.map((s) => ({ key: s.key, label: s.name, n: s.n, note: BAND_LABEL[s.band], cls: `bd ${s.band}` }))} onPick={(k) => narrow({ sources: toggle(f.sources, k) })} /></section>
                <section className="card2"><h3>Where sources are based (bars: articles)</h3><Bars rows={d.countries.map((c) => ({ key: c.code, label: countryName(c.code), n: c.n, note: `${c.sources} source${c.sources === 1 ? "" : "s"}` }))} onPick={(k) => narrow({ countries: toggle(f.countries, k) })} tone="acc" /></section>
                <section className="card2"><h3>How reliable are they</h3><Bars rows={bandRows(d.bands)} onPick={(k) => narrow({ bands: toggle(f.bands, k) })} />
                  <h3 style={{ marginTop: 18 }}>What kind of publisher</h3><Bars rows={d.types.map((t) => ({ key: t.type, label: TYPE_LABEL[t.type] ?? t.type, n: t.n }))} onPick={(k) => narrow({ types: toggle(f.types, k) })} tone="acc" /></section>
                <section className="card2"><h3>Topics covered</h3><Bars rows={d.topics.map((t) => ({ key: t.code, label: t.name, n: t.n }))} onPick={(k) => narrow({ topic: k })} tone="acc" /></section>
                <section className="card2"><h3>Places mentioned</h3><Bars rows={d.places.map((t) => ({ key: t.code, label: t.name, n: t.n }))} onPick={(k) => narrow({ place: k })} tone="acc" /></section>
                <section className="card2"><h3>Words that stand out</h3>
                  <div className="terms">{d.terms.map((t) => <button key={t.term} onClick={() => narrow({ q: f.q ? `${f.q} "${t.term}"` : `"${t.term}"` })} title={`${t.n} headlines. Add to the query.`} style={{ fontSize: `${12 + Math.min(10, Math.log2(t.n + 1) * 1.6)}px` }}>{t.term}</button>)}</div></section>
              </div>
              {d.stories_top.length > 0 && <section className="card2"><h3>Most widely covered stories</h3>
                <ul className="mlist">{d.stories_top.map((s) => <li key={s.id}><Link to={`/?story=${s.id}&place=world`}>{s.title}</Link> <span className="muted small">{plural(s.outlets, "outlet")}, {plural(s.articles, "article")}</span></li>)}</ul></section>}
              <section className="card2"><h3>Articles</h3>
                <table className="table"><tbody>{d.articles.map((a) => (
                  <tr key={a.id}><td className="muted nowrap">{ago(a.published_at)}</td><td><a className="link" href={a.url} target="_blank" rel="noopener noreferrer">{a.title}</a><br /><span className="muted small">{a.source_name} · <span className={`band ${a.band}`}>{BAND_LABEL[a.band]}</span> · {countryName(a.country)}</span></td>
                    <td>{a.story_id && <Link className="link small" to={`/?story=${a.story_id}&place=world`}>story</Link>}</td></tr>))}</tbody></table>
                <div className="ai-actions">
                  {offset > 0 && <button className="ghost" onClick={() => setOffset(Math.max(0, offset - 25))}>Newer</button>}
                  {d.articles_more && <button className="ghost" onClick={() => setOffset(offset + 25)}>Older</button>}
                  <button className="link" onClick={dl}>Download this page as CSV</button>
                </div></section>
              {applied?.q && (
                <section className="card2"><h3>The wider world, from Media Cloud</h3>
                  <p className="muted small">Media Cloud's archive covers far more outlets and years than this server collects. Each request uses a few of your weekly searches and takes about a minute and a half.</p>
                  {!mc ? <button className="ghost" onClick={() => setMc(true)}>Add Media Cloud context</button> : <McBlock q={mcq.data} loading={mcq.isFetching} err={mcq.isError ? (mcq.error as Error).message : ""} />}
                </section>)}
            </>
          )}
        </>
      )}
    </div></div>
  );
}

function McBlock({ q, loading, err }: { q?: MediaCloudCtx; loading: boolean; err: string }) {
  if (err) return <p className="note">{err} <Link className="link" to="/settings#sources">Settings → Sources</Link></p>;
  if (!q || q.state === "running") return <p className="muted">{loading ? "Asking Media Cloud… this takes a minute or two." : "Waiting…"}</p>;
  if (q.error) return <p className="note">{q.error}</p>;
  const count = typeof q.count === "object" && q.count ? q.count : null;
  return (
    <>
      {count && <p><b>{(count.relevant ?? 0).toLocaleString()}</b> of {(count.total ?? 0).toLocaleString()} stories in the period matched{q.quota ? <span className="muted"> · {q.quota.hits.toLocaleString()} of {q.quota.limit.toLocaleString()} weekly searches used</span> : null}</p>}
      {q.timeline && q.timeline.length > 1 && <Spark values={q.timeline.map((p) => p.count)} />}
      {q.sources && q.sources.length > 0 && <Bars rows={q.sources.map((s, i) => ({ key: String(i), label: String(s.source ?? s.name ?? "?"), n: Number(s.count ?? 0) }))} tone="acc" />}
    </>
  );
}
