import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type Stats } from "../api";
import { Bars } from "../components/charts";
import { ago, bytes, BAND_LABEL, countryName, inFuture, plural, STATUS_LABEL, TYPE_LABEL } from "../format";

const ORIGIN: Record<string, string> = { starter: "Hand-checked list", mediacloud: "Media Cloud directory", wikidata: "Wikidata", web: "Web (GDELT stream and search, Google News editions)", owner: "Added by you", social: "Social accounts" };
const lname = new Intl.DisplayNames(["en"], { type: "language" });
const langName = (c: string) => { try { return lname.of(c) ?? c; } catch { return c; } };
const when = (iso: string | null | undefined) => (iso ? `${ago(iso)}` : "never");
const abs = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–");

function Day({ rows, label }: { rows: { day: string; n: number }[]; label: string }) {
  const max = Math.max(1, ...rows.map((r) => r.n));
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${Math.max(1, rows.length) * 40} 90`} preserveAspectRatio="none" role="img" aria-label={label}>
        {rows.map((r, i) => <g key={r.day}><title>{`${r.day}: ${r.n.toLocaleString()}`}</title><rect x={i * 40 + 5} y={80 - (70 * r.n) / max} width={30} height={(70 * r.n) / max} className="b-rel" /></g>)}
        <line x1="0" x2={rows.length * 40} y1="80" y2="80" className="axis" />
      </svg>
      <div className="axis-l"><span>{rows[0]?.day ?? ""}</span><span>peak {max.toLocaleString()}</span><span>{rows[rows.length - 1]?.day ?? ""}</span></div>
    </figure>
  );
}

function Row({ label, value, note }: { label: string; value: React.ReactNode; note?: React.ReactNode }) {
  return <tr><td>{label}</td><td className="num"><b>{value}</b></td><td className="muted">{note}</td></tr>;
}

export default function Status() {
  const q = useQuery({ queryKey: ["stats"], queryFn: () => api<Stats>("/stats"), refetchInterval: 15_000 });
  const d = q.data;
  if (!d) return <div className="page"><div className="explore"><h1 className="h1">Status</h1><p className="muted">{q.isError ? (q.error as Error).message : "Reading everything…"}</p></div></div>;
  const s = d.sources, n = d.news, g = d.geography, disc = d.discovery;
  const bandOrder = ["highly_reliable", "reliable", "mixed", "low", "unrated", "blocked"];
  return (
    <div className="page"><div className="explore">
      <h1 className="h1">Status</h1>
      <p className="muted lede2">Everything News Anchor has collected and when each job last ran. Updates every 15 seconds. Running since {abs(d.app_started)}.</p>

      <div className="tiles">
        <div><b>{s.total.toLocaleString()}</b><span>sources known ({(s.by_status.active ?? 0).toLocaleString()} collecting)</span></div>
        <div><b>{n.articles.toLocaleString()}</b><span>articles stored</span></div>
        <div><b>{n.stories.toLocaleString()}</b><span>stories ({n.stories_multi.toLocaleString()} with 2+ outlets)</span></div>
        <div><b>{g.countries_with_stories_7d} of {g.countries_total}</b><span>countries in the news this week</span></div>
        <div><b>{d.feeds.collected.toLocaleString()}</b><span>feeds collected ({d.feeds.late.toLocaleString()} late)</span></div>
      </div>

      <section className="card2"><h3>When things last happened</h3>
        <table className="table"><tbody>
          <Row label="Newest article collected" value={when(n.last_article_seen)} note={`published ${when(n.last_article_published)}; ${plural(n.articles_24h, "article")} and ${plural(d.news.sources_reporting_24h, "source")} in the last 24 hours`} />
          <Row label="Feeds last checked" value={when(d.refresh.last_poll)} note={`${d.feeds.collected.toLocaleString()} feeds collected: ${d.feeds.polled_last_30m.toLocaleString()} checked in the last 30 minutes, ${d.refresh.polled_last_hour.toLocaleString()} in the last hour, ${d.feeds.late.toLocaleString()} more than 15 minutes late, ${d.feeds.never_checked.toLocaleString()} never checked; each feed is checked on its own schedule (about every ${d.refresh.interval_minutes} minutes by default, quiet feeds less often); ${d.feeds.not_collected.toLocaleString()} other feeds are not collected (other languages, blocked or retired); next check ${d.refresh.next_poll ? inFuture(d.refresh.next_poll) : "–"}${d.refresh.paused ? "; PAUSED (disk limit)" : ""}`} />
          <Row label="GDELT world news stream" value={when(disc.last_stream?.at)} note={disc.last_stream?.error ?? (disc.last_stream ? `${disc.last_stream.files ?? 0} quarter-hour files read, ${(disc.last_stream.publishers ?? 0).toLocaleString()} websites in ${disc.last_stream.languages ?? 0} languages, ${disc.last_stream.promoted ?? 0} new candidates${disc.busy.stream ? "; running now" : ""}` : "not run yet")} />
          <Row label="Google News country editions" value={when(disc.last_editions?.at)} note={disc.last_editions?.error ?? (disc.last_editions ? `${disc.last_editions.pages ?? 0} pages, ${disc.last_editions.cards ?? 0} story cards, ${disc.last_editions.articles_new ?? 0} new articles from publishers you collect, ${disc.last_editions.publishers ?? 0} websites, ${disc.last_editions.promoted ?? 0} new candidates${disc.last_editions.notes?.length ? `; ${disc.last_editions.notes[0]}` : ""}; sweep at page ${disc.last_editions.sweep_position ?? "–"}${disc.busy.editions ? "; running now" : ""}` : "not run yet")} />
          <Row label="Wikidata, country by country" value={when(disc.last_wikidata?.at)} note={disc.last_wikidata?.error ?? (disc.last_wikidata ? `${disc.last_wikidata.countries ?? 0} countries asked, ${disc.last_wikidata.added ?? 0} outlets added; ${disc.last_wikidata.countries_left ?? "?"} countries under the target still to ask` : "not run yet")} />
          <Row label="Country balance" value={when(disc.last_rebalance?.at)} note={disc.last_rebalance ? `${disc.last_rebalance.parked ?? 0} parked, ${disc.last_rebalance.unparked ?? 0} brought back (limit ${disc.coverage.cap} per country)` : "not run yet"} />
          <Row label="Reliability, keeping breadth" value={when(disc.last_rebalance?.at)} note={(() => { const u = disc.last_rebalance?.upgrade; if (!u) return "not run yet"; if (u.error) return u.error; if (u.off) return `off; ${u.restored ?? 0} brought back`; const a = u.swap?.after; return `${u.seed?.added ?? 0} rated newcomers added${u.seed?.note ? ` (${u.seed.note})` : ""}; ${u.swap?.swapped ?? 0} weaker sources swapped out, ${u.swap?.protected_for_breadth ?? 0} kept for breadth` + (a ? `; ${a.countries} countries, ${a.cells.toLocaleString()} place/subject/language cells (${a.cells_thin.toLocaleString()} with fewer than 3 sources), reliable or better: ${Math.round(a.reliable_share_sources * 100)}% of sources, ${Math.round(a.reliable_share_articles * 100)}% of articles` : ""); })()} />
          <Row label="Web search for sources (GDELT)" value={when(disc.last_web?.at)} note={disc.last_web ? `${disc.last_web.queries ?? 0} searches, ${disc.last_web.promoted ?? 0} new candidates; ${disc.web.seen.toLocaleString()} websites seen so far (not sources yet), ${disc.web.waiting} waiting for a second sighting${disc.running.web ? "; running now" : ""}` : "not run yet"} />
          <Row label="Media Cloud and Wikidata read" value={when(disc.last_directory?.at)} note={(disc.running.directory ? "running now (a full read takes hours); " : "") + (disc.directory_resume ? `${disc.directory_resume} collections read in this pass, resumes after a restart; ` : "") + (disc.last_directory?.added ? `last pass added ${disc.last_directory.added.mediacloud} + ${disc.last_directory.added.wikidata}` : disc.mediacloud_token ? "" : "no Media Cloud token set")} />
          <Row label="Feeds of new sources checked" value={when(disc.last_batch?.at)} note={`${disc.pending_feed_search.toLocaleString()} candidates still waiting${disc.last_batch?.searched ? `; last batch found ${disc.last_batch.found} of ${disc.last_batch.searched}` : ""}`} />
          <Row label="Source health sweep" value={when(disc.lifecycle_last)} note="moves failing sources to degraded, dormant and retired" />
          <Row label="Trust crawl" value={when(d.trust.last_run?.at)} note={`${d.trust.last_run ? `${d.trust.last_run.sources} sources rated in ${Math.round(d.trust.last_run.seconds)} s` : "not run yet"}; ${d.trust.waiting_to_be_rated} waiting to be rated`} />
        </tbody></table>
      </section>

      <section className="card2" style={{ marginTop: 14 }}><h3>Clean-up of stories only one outlet reported</h3>
        <table className="table"><tbody>
          <Row label="Status" value={d.cleanup.enabled ? "On" : "Off"} note={d.cleanup.enabled ? `A story that one outlet reported, that is in no storyline with a bigger story and is not saved, is deleted ${d.cleanup.wait_hours} hours after its last report, with its articles (and so is an advertisement's headline that no second independent outlet wrote). Settings → Storage.` : "Nothing is deleted for having one outlet. Settings → Storage."} />
          <Row label="Deleted so far" value={d.cleanup.stories.toLocaleString()} note={`stories and ${d.cleanup.articles.toLocaleString()} articles, ${d.cleanup.ads.toLocaleString()} of the stories because of an advertisement's headline${d.cleanup.since ? `; counting since ${abs(d.cleanup.since)}` : ""}`} />
          <Row label="Last run" value={when(d.cleanup.last_at)} note={d.cleanup.last ? `${d.cleanup.last.stories.toLocaleString()} stories and ${d.cleanup.last.articles.toLocaleString()} articles deleted (a run is limited to 45 seconds; the rest follows at the next 10-minute check)` : "not run yet"} />
          <Row label="Waiting for a second outlet" value={d.cleanup.waiting.toLocaleString()} note={`stories with one outlet that are inside their ${d.cleanup.wait_hours}-hour wait; those still unconfirmed after it are deleted. The ${d.news.stories_multi.toLocaleString()} stories with 2 or more outlets are kept.`} />
          <Row label="Deleted per day" value="" note={Object.entries(d.cleanup.per_day).slice(-7).map(([k, v]) => `${k.slice(5)}: ${v.stories.toLocaleString()}`).join(", ") || "nothing yet"} />
        </tbody></table>
      </section>

      <section className="card2" style={{ marginTop: 14 }}><h3>Sources, websites and feeds: what the numbers count</h3>
        <p className="muted small"><b>Sources</b> ({s.total.toLocaleString()}) are the outlets in the registry: the ones collected and the ones waiting or retired. Each has one or more <b>feeds</b> (RSS, a sitemap or a page of links) that are checked for new articles. <b>Websites seen</b> are every site that appeared in the news streams the finders read (GDELT, Google News, web searches), {disc.web.seen.toLocaleString()} so far. A website becomes a candidate source only after it is seen a second time, and a source only after a working feed is found for it, so there are always many more websites seen than sources. Sources are tagged with the country of the outlet when it is known ({(s.by_language.en ?? 0).toLocaleString()} are English); the country of many web finds is not known yet.</p>
      </section>

      <div className="cols">
        <section className="card2"><h3>Articles collected per day</h3><Day rows={n.per_day} label="Articles per day" />
          <p className="muted small">Kept for {d.storage.retention_days} days; oldest stored day {n.oldest_day ?? "–"}; database {bytes(d.storage.database_bytes)}.</p></section>
        <section className="card2"><h3>New sources discovered per day</h3><Day rows={s.discovered_per_day} label="Sources discovered per day" />
          <p className="muted small">{s.curated} hand-picked, {(s.total - s.curated).toLocaleString()} found automatically. Ceiling {disc.ceiling.toLocaleString()} found sources.</p></section>
      </div>

      <div className="cols" style={{ marginTop: 14 }}>
        <section className="card2"><h3>Sources by stage</h3><Bars rows={Object.entries(s.by_status).map(([k, v]) => ({ key: k, label: STATUS_LABEL[k] ?? k, n: v }))} tone="acc" />
          <p className="small"><Link className="link" to="/sources">Open the full list</Link></p></section>
        <section className="card2"><h3>Sources by reliability</h3><Bars rows={bandOrder.filter((b) => s.by_band[b]).map((b) => ({ key: b, label: BAND_LABEL[b], n: s.by_band[b], cls: `bd ${b}` }))} onPick={() => undefined} />
          <p className="small">{d.trust.last_run ? `Last full crawl covered ${d.trust.last_run.sources} sources. ` : ""}<Link className="link" to="/sources?band=unrated">Unrated list</Link> · <Link className="link" to="/sources?band=mixed">Mixed</Link> · <Link className="link" to="/sources?band=low">Low</Link></p></section>
        <section className="card2"><h3>Where sources came from</h3><Bars rows={Object.entries(s.by_origin).map(([k, v]) => ({ key: k, label: ORIGIN[k] ?? k, n: v }))} tone="acc" />
          <h3 style={{ marginTop: 16 }}>By type</h3><Bars rows={Object.entries(s.by_type).map(([k, v]) => ({ key: k, label: TYPE_LABEL[k] ?? k, n: v }))} tone="acc" /></section>
        <section className="card2"><h3>Busiest sources, last 24 hours</h3><Bars rows={s.top_by_volume.map((x) => ({ key: x.key, label: x.name, n: x.n, note: BAND_LABEL[x.band], cls: `bd ${x.band}` }))} /></section>
      </div>

      <section className="card2" style={{ marginTop: 14 }}><h3>Categories: stories in the last 7 days</h3>
        <Bars rows={d.categories.top.map((c) => ({ key: c.code, label: c.name, n: c.stories, note: `${c.subtopics_active} of ${c.subtopics} sub-topics active` }))} tone="acc" />
        <p className="muted small" style={{ marginTop: 10 }}>Busiest sub-topics: {d.categories.busiest_subtopics.slice(0, 12).map((x) => `${x.name} (${x.stories})`).join(", ")}.</p>
        {d.categories.enabled_but_quiet_count > 0 && <p className="muted small">{d.categories.enabled_but_quiet_count} enabled sub-topics had no story this week, for example {d.categories.enabled_but_quiet.slice(0, 10).join(", ")}. The web search looks for sources for exactly these first.</p>}
      </section>

      <section className="card2"><h3>Balance of the registry</h3>
        <p><b>{disc.coverage.with_sources}</b> of {disc.coverage.countries} countries have a working or waiting source; <b>{disc.coverage.under_target}</b> are under the target of {disc.coverage.target} and are served first by every finder; <b>{disc.coverage.at_cap}</b> are at the limit of {disc.coverage.cap}. {disc.coverage.unknown_country.toLocaleString()} sources have no known country (treated as international).</p>
        <p className="muted small">Most sources: {disc.coverage.top.map(([c, n]) => `${countryName(c)} ${n}`).join(", ")}. Languages collected: {disc.all_languages ? "all" : disc.languages.join(", ")}; sources by language: {Object.entries(s.by_language).sort((a, b) => b[1] - a[1]).slice(0, 14).map(([l, k]) => `${langName(l)} ${k}`).join(", ")}.</p>
        {d.translate && <p className="muted small">Translation on this server: {d.translate.enabled ? `${Object.keys(d.translate.installed).length} language models installed (${d.translate.disk_mb} of ${d.translate.budget_mb} MB)${d.translate.last?.at ? `, last run ${ago(d.translate.last.at)}` : ""}` : "off"}{d.translate.last_error ? `; last error: ${d.translate.last_error}` : ""}.</p>}
      </section>

      <section className="card2"><h3>Coverage of the world</h3>
        <p><b>{g.countries_with_stories_7d}</b> of {g.countries_total} countries appeared in stories this week; <b>{g.countries_with_active_source}</b> have at least one collecting source based there; <b>{g.countries_without_source_count}</b> have none. This is the honest measure of how far the "view of the world" reaches today.</p>
        <div className="cols" style={{ marginTop: 10 }}>
          <div><h3>Regions</h3><Bars rows={g.regions.map((r) => ({ key: r.code, label: r.name, n: r.stories }))} tone="acc" /></div>
          <div><h3>Countries in the news, with the sources based there</h3><Bars rows={g.top_countries.slice(0, 20).map((c) => ({ key: c.code, label: c.name, n: c.stories, note: `${c.sources} sources` }))} tone="acc" /></div>
        </div>
        <details className="srclist"><summary>{g.countries_without_source_count} countries with no collecting source</summary><p>{g.countries_without_source.join(", ")}{g.countries_without_source_count > g.countries_without_source.length ? " …" : ""}</p></details>
        <p className="muted small">Collected sources by country: {Object.entries(s.active_by_country).slice(0, 15).map(([c, k]) => `${countryName(c)} ${k}`).join(", ")}.</p>
      </section>

      <div className="cols">
        <section className="card2"><h3>Feeds</h3><Bars rows={Object.entries(d.feeds.by_state).map(([k, v]) => ({ key: k, label: k, n: v }))} tone="acc" />
          <p className="muted small">{d.feeds.failing} failing, {d.feeds.due_now} due now among the {d.feeds.collected.toLocaleString()} collected feeds ({d.feeds.not_collected.toLocaleString()} of the {d.feeds.total.toLocaleString()} feeds in the bars are not collected: other languages, blocked or retired). Kinds: {Object.entries(d.feeds.by_kind).map(([k, v]) => `${k} ${v}`).join(", ")}.</p></section>
        <section className="card2"><h3>Search and AI</h3>
          <p>{d.search.embedded.toLocaleString()} of {d.search.stories.toLocaleString()} stories have a meaning vector ({d.search.backend ?? "none yet"}; model {d.search.model_loaded ? "loaded now" : "idle"}).</p>
          <p>{d.ai.providers_ready} AI services ready, {d.ai.calls_today} calls today. <Link className="link" to="/settings#ai">AI settings</Link></p>
          {d.prefetch && <p className="muted small">Prepared ahead {d.prefetch.last ? `${ago(d.prefetch.last.at)}: ${d.prefetch.last.scopes} scopes, ${d.prefetch.last.texts} article texts, ${d.prefetch.last.briefings} briefings, ${d.prefetch.last.digests} digests, ${d.prefetch.last.reused} reused${d.prefetch.last.stopped ? `; stopped: ${d.prefetch.last.stopped}` : ""}` : "not yet"}{d.prefetch.ai_ready ? "" : " (no AI provider: only article texts are prepared)"}.</p>}
          {d.social && <p className="muted small">Social: {Object.entries(d.social.platforms).map(([p, v]) => `${p} ${v.feeds} feeds, ${v.posts} posts`).join("; ") || "nothing followed yet"}. <Link className="link" to="/settings#social">Social settings</Link></p>}</section>
      </div>

      <section className="card2" style={{ marginTop: 14 }}><h3>Recent jobs</h3>
        <table className="table"><thead><tr><th>Job</th><th>State</th><th>Started</th><th>Result</th></tr></thead>
          <tbody>{d.jobs.map((j) => <tr key={j.id}><td>{j.kind}</td><td><span className={`pill ${j.state === "done" ? "ok" : j.state === "failed" ? "bad" : ""}`}>{j.state}</span></td><td>{ago(j.created_at)}</td><td className="muted small" style={{ wordBreak: "break-all" }}>{j.result}</td></tr>)}</tbody></table></section>
    </div></div>
  );
}
