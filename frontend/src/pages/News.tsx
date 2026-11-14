import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type Favourite, type Story, type StoryDetail, type StoryList, type StorylineItem, type StorylineList, useSettings, useStatus } from "../api";
import { ArticleReader, StoryBrief } from "../components/AiViews";
import { Briefing } from "../components/Briefing";
import { Jump } from "../components/Jump";
import { ScopeBar } from "../components/ScopeBar";
import { SourceInfo } from "../components/SourceInfo";
import { Back, Bookmark, Chevron, SearchIcon, Star } from "../components/icons";
import { ago, BAND_LABEL, BAND_NOTE, BAND_ORDER, bandText, dayOf, eventLine, headline, plural, TYPE_LABEL, VERIFY_LABEL, when } from "../format";

const TABS = [["top", "Top"], ["trending", "Trending"], ["latest", "Latest"], ["storylines", "Storylines"], ["saved", "Saved"]] as const;
const RANGE_LABEL: Record<number, string> = { 6: "6 h", 12: "12 h", 24: "24 h", 48: "2 days", 72: "3 days", 168: "7 days", 336: "14 days", 720: "30 days" };
const RANGES = [6, 12, 24, 48, 72, 168, 336, 720];
const VIEWS = [["all", "Aggregated", "Fresh and ongoing stories together, fused by rank so that neither hides the other"],
  ["fresh", "Fresh", "Stories whose event was first reported in the last 24 hours: new events, or new developments of a storyline that had been quiet"],
  ["ongoing", "Ongoing", "Stories that began earlier and are still being reported in the last 24 hours"]] as const;
const isTouchWidth = () => window.innerWidth <= 1100;

export default function News() {
  const qc = useQueryClient();
  const settings = useSettings();
  const [sp, setSp] = useSearchParams();
  const place = sp.get("place") ?? settings.get<string>("home.landing", "world");
  const topic = sp.get("topic") ?? "";
  const tab = sp.get("tab") ?? "top";
  const view = VIEWS.some(([k]) => k === sp.get("view")) ? sp.get("view")! : "all";
  const hasViews = tab === "top" || tab === "trending";
  const isLines = tab === "storylines";
  const range = Number(sp.get("range")) || Number(settings.get<string>("storylines.hours", "24")) || 24;
  const storyId = Number(sp.get("story")) || null;
  const [brief, setBrief] = useState(false);
  const [jump, setJump] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [min, setMin] = useState<boolean>(() => {
    const saved = localStorage.getItem("na.pane.min");
    return saved !== null ? saved === "1" : isTouchWidth() || settings.get<string>("appearance.pane", "open") === "minimised";
  });
  const dividerRef = useRef<HTMLDivElement>(null);

  const topN = settings.get<number>("feed.top_n", 10);
  const list = useInfiniteQuery({
    queryKey: ["stories", place, topic, tab, hasViews ? view : "", topN],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => api<StoryList>(`/stories?place=${encodeURIComponent(place)}&topic=${encodeURIComponent(topic)}&tab=${tab}&view=${hasViews ? view : "all"}&limit=${topN}&offset=${pageParam}`),
    getNextPageParam: (last, all) => (last.more ? all.length * topN : undefined),
    placeholderData: keepPreviousData,
    enabled: !isLines,
  });
  const lines = useInfiniteQuery({
    queryKey: ["stories", "storylines", place, topic, range, topN],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => api<StorylineList>(`/storylines?place=${encodeURIComponent(place)}&topic=${encodeURIComponent(topic)}&hours=${range}&limit=${topN}&offset=${pageParam}`),
    getNextPageParam: (last, all) => (last.more ? all.length * topN : undefined),
    placeholderData: keepPreviousData,
    enabled: isLines,
  });
  const detail = useQuery({ queryKey: ["story", storyId], queryFn: () => api<StoryDetail>(`/stories/${storyId}`), enabled: storyId !== null, retry: false });
  const favs = useQuery({ queryKey: ["favourites"], queryFn: () => api<{ favourites: Favourite[] }>("/favourites") });
  const status = useStatus();

  const go = useCallback((patch: Record<string, string | null>, replace = false) => {
    const n = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(patch)) (v === null || v === "" ? n.delete(k) : n.set(k, v));
    setSp(n, { replace });
  }, [sp, setSp]);

  const setScope = (p: { place?: string; topic?: string }) => { go({ ...("place" in p ? { place: p.place ?? null } : {}), ...("topic" in p ? { topic: p.topic ?? null } : {}), story: null }); };
  const setTab = (t: string) => { go({ tab: t === "top" ? null : t, story: null }); };
  const setView = (v: string) => { go({ view: v === "all" ? null : v, story: null }); };
  const setRange = (h: number) => { go({ range: String(h), story: null }); };
  const openStory = (id: number | null) => { go({ story: id === null ? null : String(id) }); if (id !== null) setPane(false); };
  const setPane = (minimised: boolean) => { setMin(minimised); localStorage.setItem("na.pane.min", minimised ? "1" : "0"); };

  // live updates: the server announces new stories (a debounced refetch), and the lists are also refetched when the connection comes back after a
  // break (a sleeping laptop), when the tab becomes visible again, and every minute while it is visible, so they never stay stale
  useEffect(() => {
    const es = new EventSource("/api/events");
    let t: number | undefined;
    const reload = () => { qc.invalidateQueries({ queryKey: ["stories"] }); qc.invalidateQueries({ queryKey: ["status"] }); qc.invalidateQueries({ queryKey: ["placeMenu"] }); qc.invalidateQueries({ queryKey: ["topicMenu"] }); };
    let dropped = false;
    es.onerror = () => { dropped = true; };
    es.onopen = () => { if (dropped) { dropped = false; reload(); } };
    es.onmessage = (m) => {
      const e = JSON.parse(m.data);
      if (e.type === "progress") setProgress({ done: e.feeds_done, total: e.feeds_total });
      if (e.type === "refreshed") setProgress(null);
      if (["stories", "refreshed", "breaking"].includes(e.type)) {
        window.clearTimeout(t);
        t = window.setTimeout(reload, 1500);
      }
    };
    const visible = () => { if (document.visibilityState === "visible") reload(); };
    document.addEventListener("visibilitychange", visible);
    const tick = window.setInterval(visible, 60_000);
    return () => { es.close(); window.clearTimeout(t); window.clearInterval(tick); document.removeEventListener("visibilitychange", visible); };
  }, [qc]);

  const refresh = useMutation({
    mutationFn: () => api("/jobs/refresh-news", { method: "POST" }),
    onSuccess: () => { setProgress({ done: 0, total: 0 }); qc.invalidateQueries({ queryKey: ["status"] }); },
  });
  const toggleSaved = useMutation({
    mutationFn: (s: { id: number; saved: boolean }) => api(`/stories/${s.id}/saved`, { method: "PUT", body: { saved: s.saved } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["stories"] }); qc.invalidateQueries({ queryKey: ["story"] }); },
  });
  const isFav = favs.data?.favourites.some((f) => f.place === place && f.topic === topic) ?? false;
  const toggleFav = useMutation({
    mutationFn: () => isFav ? api(`/favourites?place=${encodeURIComponent(place)}&topic=${encodeURIComponent(topic)}`, { method: "DELETE" }) : api("/favourites", { method: "POST", body: { place, topic } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["favourites"] }),
  });

  // pane width: drag the divider (arrow keys too); double-click resets
  useEffect(() => { const w = localStorage.getItem("na.pane.w"); if (w) document.documentElement.style.setProperty("--pane-w", w); }, []);
  const setWidth = (px: number, total: number) => {
    const w = Math.round(Math.max(340, Math.min(px, total * 0.68)));
    document.documentElement.style.setProperty("--pane-w", w + "px");
    localStorage.setItem("na.pane.w", w + "px");
  };
  const startDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = e.currentTarget, work = el.parentElement!;
    el.setPointerCapture(e.pointerId); el.classList.add("drag");
    const right = work.getBoundingClientRect().right, total = work.clientWidth;
    const move = (ev: PointerEvent) => setWidth(right - ev.clientX - 4, total);
    const up = () => { el.classList.remove("drag"); el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up); };
    el.addEventListener("pointermove", move); el.addEventListener("pointerup", up);
  };

  const storylines: StorylineItem[] = lines.data?.pages.flatMap((p) => p.storylines) ?? [];
  const stories: Story[] = isLines ? storylines : (list.data?.pages.flatMap((p) => p.stories) ?? []);
  const cur = isLines ? lines : list;
  const switching = cur.isPlaceholderData;          // the list you asked for is loading and the previous one is still shown (not a background refresh of the same list)
  const idx = stories.findIndex((s) => s.id === storyId);

  // keyboard
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.matches("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "/") { e.preventDefault(); setJump(true); }
      else if (e.key === "j" || e.key === "k") { const n = stories[Math.max(0, Math.min(stories.length - 1, idx + (e.key === "j" ? 1 : -1)))]; if (n) openStory(n.id); }
      else if (e.key === "b") setPane(!min);
      else if (e.key === "s" && storyId) { const s = stories.find((x) => x.id === storyId); if (s) toggleSaved.mutate({ id: s.id, saved: !s.saved }); }
      else if (e.key === "[" || e.key === "]") { const i = TABS.findIndex(([k]) => k === tab); const n = TABS[(i + (e.key === "]" ? 1 : TABS.length - 1)) % TABS.length][0]; setTab(n); }
      else if (/^[1-3]$/.test(e.key) && hasViews) setView(VIEWS[Number(e.key) - 1][0]);
      else if (e.key === "Escape" && storyId) openStory(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const running = refresh.isPending || status.data?.running || progress !== null;
  const empty = !cur.isPending && stories.length === 0;
  const noData = (status.data?.counts.stories ?? 0) === 0;

  const paneTitle = storyId ? <><b>Story</b><span>{detail.data?.topic?.name ?? ""}</span></> : <><b>Overview</b><span>{scopeLabel(place, topic, list.data?.pages[0], stories.length)}</span></>;
  const peek = storyId ? (detail.data?.title ?? "") : "Open the overview for this list.";

  return (
    <>
      <header className="top">
        <h1 className="sr">News Anchor</h1>
        <div className="scopebar">
          <ScopeBar place={place} topic={topic} onScope={setScope} />
          <button className="jump" onClick={() => setJump(true)} aria-label="Jump to a place or topic"><SearchIcon /><span>Jump to a place or topic</span><kbd>/</kbd></button>
        </div>
        <div className="favs" aria-label="Favourites">
          {favs.data?.favourites.map((f) => (
            <button key={f.place + f.topic} aria-current={f.place === place && f.topic === topic} onClick={() => setScope({ place: f.place, topic: f.topic })}>{f.label}</button>
          ))}
          <button className="star" onClick={() => toggleFav.mutate()} aria-pressed={isFav} title={isFav ? "Remove this scope from favourites" : "Add this scope to favourites"}><Star on={isFav} />{isFav ? "In favourites" : "Add to favourites"}</button>
        </div>
        <div className="tabrow">
          <div className="tabs" role="tablist">
            {TABS.map(([k, label]) => <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}><span>{label}</span></button>)}
          </div>
          {hasViews && <div className="views ranges" role="group" aria-label="View">
            {VIEWS.map(([k, label, tip]) => <button key={k} aria-pressed={view === k} title={tip} onClick={() => setView(k)}>{label}</button>)}
          </div>}
          {isLines && <div className="views ranges" role="group" aria-label="Period">
            {RANGES.map((h) => <button key={h} aria-pressed={range === h} title={`Storylines ranked by the coverage they drew in the last ${RANGE_LABEL[h]}`} onClick={() => setRange(h)}>{RANGE_LABEL[h]}</button>)}
          </div>}
          <div className="status">
            <span className="muted">{running ? (progress && progress.total ? `Checking feeds ${progress.done}/${progress.total}` : "Checking feeds…") : `Updated ${ago(status.data?.counts.last_poll)}`}</span>
            <button className="ghost" aria-pressed={brief} onClick={() => setBrief((b) => !b)}>Briefing</button>
            <button className="ghost" disabled={running} onClick={() => refresh.mutate()}>{running ? "Refreshing" : "Refresh"}</button>
          </div>
        </div>
      </header>
      {status.data?.paused && <div className="banner warn">Saving new articles is paused because the disk cap or free-space floor is reached. <Link className="link" to="/settings#storage">Open storage settings</Link></div>}

      <div className={`work${min ? " min" : ""}`} style={{ "--read": settings.get<number>("appearance.font_px", 17) + "px" } as React.CSSProperties}>
        <section className={`list${settings.get("feed.density") === "compact" ? " compact" : ""}${switching ? " busy" : ""}`} aria-label="Stories" aria-busy={switching}>
          {switching && <div className="loadbar" role="progressbar" aria-label="Loading the list"><i /></div>}
          {cur.isError && <div className="empty"><b>Could not load stories</b>{(cur.error as Error).message}</div>}
          {empty && noData && <div className="empty"><b>Collecting the first stories</b>News Anchor checks {status.data?.counts.feeds ?? "its"} feeds when it starts. The first stories appear within a few minutes; press Refresh to start now.</div>}
          {empty && !noData && <div className="empty"><b>{emptyTitle(tab)}</b>{emptyHelp(tab, place, topic)}</div>}
          {isLines ? storylines.map((s, i) => (
            <LineRow key={s.storyline.id} s={s} rank={i + 1} hours={range} selected={s.id === storyId || s.storyline.timeline.some((d) => d.id === storyId)} onOpen={(id) => openStory(id)}
                     showPlace={place === "world" || topic !== ""} />
          )) : stories.map((s, i) => (
            <Row key={s.id} s={s} rank={i + 1} selected={s.id === storyId} onOpen={() => openStory(s.id)} onSave={() => toggleSaved.mutate({ id: s.id, saved: !s.saved })}
                 showLead={settings.get<boolean>("feed.show_lead", true)} showPlace={place === "world" || topic !== ""} view={hasViews ? view : ""} openId={(id) => openStory(id)} />
          ))}
          {cur.hasNextPage && <button className="ghost more" disabled={cur.isFetchingNextPage} onClick={() => cur.fetchNextPage()}>{cur.isFetchingNextPage ? "Loading…" : `Show ${topN} more`}</button>}
          {!cur.hasNextPage && stories.length > 0 && <p className="endlist">{isLines ? `That is every storyline for this scope in the last ${RANGE_LABEL[range] ?? range + " h"} (${plural(stories.length, "storyline")}). Choose a longer period above.` : `That is everything for this scope in this period (${plural(stories.length, "story", "stories")}). Open Explore for older articles.`}</p>}
        </section>
        <div ref={dividerRef} className="divider" role="separator" aria-orientation="vertical" aria-label="Resize reading pane" tabIndex={0} onPointerDown={startDrag}
             onDoubleClick={() => { document.documentElement.style.setProperty("--pane-w", "42%"); localStorage.removeItem("na.pane.w"); }}
             onKeyDown={(e) => { const w = dividerRef.current!.nextElementSibling!.getBoundingClientRect().width, tot = dividerRef.current!.parentElement!.clientWidth;
               if (e.key === "ArrowLeft") { setWidth(w + 32, tot); e.preventDefault(); } if (e.key === "ArrowRight") { setWidth(w - 32, tot); e.preventDefault(); } }} />
        <aside className="pane" aria-label="Reading pane" onClick={(e) => { if (min && window.innerWidth > 760 && e.target === e.currentTarget) setPane(false); }}>
          <div className="pane-head" onClick={() => { if (min && window.innerWidth > 760) setPane(false); }}>
            <button className="pane-grip" aria-label="Expand or minimise the reading pane" onClick={() => setPane(!min)} />
            <div className="pane-title">{paneTitle}</div>
            <button className="icon-btn" aria-expanded={!min} aria-label={min ? "Expand reading pane" : "Minimise reading pane"} onClick={(e) => { e.stopPropagation(); setPane(!min); }}><Chevron /></button>
          </div>
          <div className="pane-peek">{peek}</div>
          <div className="pane-body">
            {storyId ? <StoryView d={detail.data} error={detail.error as Error | null} onBack={() => openStory(null)} onScope={setScope} onSave={(s) => toggleSaved.mutate({ id: s.id, saved: !s.saved })} onOpen={(id) => openStory(id)} />
                     : <Overview stories={stories} place={place} topic={topic} onBrief={() => setBrief(true)} />}
          </div>
        </aside>
      </div>
      {brief && <Briefing place={place} topic={topic} scopeName={briefName(place, topic, list.data?.pages[0])} onClose={() => setBrief(false)} />}
      {jump && <Jump onPick={(p) => setScope({ ...(p.place !== undefined ? { place: p.place } : {}), ...(p.topic !== undefined ? { topic: p.topic } : {}) })} onClose={() => setJump(false)} />}
    </>
  );
}

function scopeLabel(place: string, topic: string, l: StoryList | undefined, n: number): string {
  return [topic ? "topic" : "all topics", place === "world" ? "worldwide" : "this place"].join(", ") + (l ? ` (${plural(n, "story", "stories")})` : "");
}
function briefName(place: string, topic: string, _l?: StoryList): string {
  return `${place === "world" ? "Worldwide" : place}${topic ? ` · ${topic}` : ""}`;
}
const emptyTitle = (tab: string) => tab === "saved" ? "No saved stories" : tab === "trending" ? "Nothing is picking up speed right now" : tab === "latest" ? "No story has been confirmed by enough outlets yet" : tab === "storylines" ? "No storylines here in this period" : "No stories here in this window";
function emptyHelp(tab: string, place: string, topic: string) {
  if (tab === "saved") return "Press S on a story, or use the bookmark on its row, to keep it here.";
  if (tab === "latest") return "Latest lists a story once several different outlets have reported it at different times. Settings → Lists → Latest sets how many, and how far back to look.";
  return topic || place !== "world" ? "Try zooming out with the breadcrumbs, or look at Aggregated. A topic that is not enabled in Settings → Topics has no dedicated sources polled." : "Press Refresh to check the feeds now.";
}

function Row({ s, rank, selected, onOpen, onSave, showLead, showPlace, view, child, openId }: { s: Story; rank: number; selected: boolean; onOpen: () => void; onSave: () => void; showLead: boolean; showPlace: boolean; view: string; child?: boolean; openId?: (id: number) => void }) {
  const [open, setOpen] = useState(false);
  const inPeriod = s.outlets_period != null;                       // Top and Trending count the outlets that reported inside the period
  const indep = s.outlets_period ?? s.indep ?? s.outlets;
  const more = indep - 1, copies = inPeriod ? 0 : s.outlets - indep;
  const h = headline(s);
  return (
    <article className={`story${selected ? " sel" : ""}${s.breaking ? " brk" : ""}`} tabIndex={0} aria-label={(s.breaking ? "Breaking: " : "") + s.title}
             onClick={onOpen} onKeyDown={(e) => { if (e.key === "Enter" && e.target === e.currentTarget) onOpen(); }}>
      <span className="rank">{child ? "" : rank}</span>
      <div>
        <h3 title={h.original || headlineNote(s)}>{s.breaking && <span className="brk-label">Breaking</span>}{h.text}{h.from && <span className="xlat">Translated from {h.from}</span>}</h3>
        <p className="when">{eventLine(s)}</p>
        {showLead && s.lead && <p className="lead">{s.lead}</p>}
        <div className="meta" onClick={(e) => e.stopPropagation()}>
          <SourceInfo skey={s.lead_source.key} name={s.lead_source.name} />
          {s.lead_source.type !== "news" && <span>{TYPE_LABEL[s.lead_source.type] ?? s.lead_source.type}</span>}
          <span title={copies > 0 ? `${plural(s.outlets, "outlet")} carry it; ${copies} of them print another outlet's text word for word (wire copy), so they count once` : undefined}>
            {s.single_source ? (copies > 0 ? `One report, ${plural(s.outlets, "outlet")}` : "Single source") : more > 0 ? `and ${plural(more, "more outlet")}${copies > 0 ? `, ${plural(copies, "reprint")}` : ""}` : ""}</span>
          {s.qualified_at && <span title={`First reported ${when(s.first_seen)}; confirmed by ${plural(s.indep, "different outlet")} at ${when(s.qualified_at)}`}>listed {ago(s.qualified_at)}</span>}
          <span title={`Last report ${when(s.last_update)}`}>updated {ago(s.last_update)}</span>
          <span className={`vtag ${s.verification.level}`} title={s.verification.label}>{VERIFY_LABEL[s.verification.level]}</span>
          {s.developing && <span className="tag-dev">Developing</span>}
          {view === "all" && s.bucket && <span className={`bucket ${s.bucket}`}>{s.bucket === "fresh" ? "Fresh" : "Ongoing"}</span>}
          {s.topic && <span>{s.topic.name}</span>}
          {showPlace && s.place && <span>{s.place.name}</span>}
        </div>
        {s.group && s.group.length > 0 && (<div onClick={(e) => e.stopPropagation()}>
          <button className="ghost grp" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"} {plural(s.group.length, "more development")} of this storyline in this list</button>
          {open && <div className="children">{s.group.map((c) => (
            <Row key={c.id} s={c} rank={0} child openId={openId} selected={false} onOpen={() => openId?.(c.id)} onSave={() => undefined} showLead={false} showPlace={showPlace} view={view} />
          ))}</div>}
        </div>)}
      </div>
      <button className={`save${s.saved ? " on" : ""}`} aria-label={s.saved ? "Remove from saved" : "Save story"} aria-pressed={s.saved} onClick={(e) => { e.stopPropagation(); onSave(); }}><Bookmark on={s.saved} /></button>
    </article>
  );
}

/** One storyline: its main development in the period, how coverage rose and fell, and its latest developments. */
function LineRow({ s, rank, hours, selected, onOpen, showPlace }: { s: StorylineItem; rank: number; hours: number; selected: boolean; onOpen: (id: number) => void; showPlace: boolean }) {
  const L = s.storyline;
  const h = headline(s);
  const peak = Math.max(1, ...L.activity);
  const multi = L.developments > 1;
  return (
    <article className={`story line${selected ? " sel" : ""}`} tabIndex={0} aria-label={"Storyline: " + s.title}
             onClick={() => onOpen(s.id)} onKeyDown={(e) => { if (e.key === "Enter" && e.target === e.currentTarget) onOpen(s.id); }}>
      <span className="rank">{rank}</span>
      <div>
        <h3 title={h.original || undefined}>{h.text}{h.from && <span className="xlat">Translated from {h.from}</span>}</h3>
        <p className="when">{multi ? `Since ${dayOf(L.began)} · ${plural(L.developments, "development")}${L.developments_in_range !== L.developments ? `, ${L.developments_in_range} in this period` : ""}` : `One development, first reported ${when(s.first_seen)}`} · latest report {ago(L.latest)}</p>
        <div className="spark" role="img" aria-label={`Outlets reporting over the last ${RANGE_LABEL[hours] ?? hours + " h"}, oldest first: ${L.activity.join(", ")}`} title="Outlets reporting in each part of the period, oldest on the left">
          {L.activity.map((n, i) => <i key={i} style={{ height: `${Math.round((n / peak) * 100)}%` }} className={n ? "" : "z"} />)}
        </div>
        {L.latest_title && <p className="latest">Latest: <button className="link" onClick={(e) => { e.stopPropagation(); onOpen(L.latest_id); }}>{L.latest_title}</button></p>}
        {multi && <ol className="timeline mini" onClick={(e) => e.stopPropagation()}>
          {L.timeline.map((d) => (
            <li key={d.id} className={d.id === s.id ? "cur" : d.in_range ? "" : "old"}>
              <span className="t-when">{dayOf(d.first_seen)}</span>
              <button className="link" onClick={() => onOpen(d.id)}>{headline(d).text}</button>
              <span className="muted"> · {plural(d.indep, "outlet")}</span>
            </li>))}
          {(L.minor ?? 0) > 0 && <li className="more"><button className="link" onClick={() => onOpen(s.id)}>and {plural(L.minor ?? 0, "smaller report")}</button></li>}
        </ol>}
        <div className="meta" onClick={(e) => e.stopPropagation()}>
          <span>{plural(L.outlets_in_range, "outlet")} from {plural(L.countries_in_range, "country", "countries")} in this period</span>
          <span className={`vtag ${s.verification.level}`} title={s.verification.label}>{VERIFY_LABEL[s.verification.level]}</span>
          {s.topic && <span>{s.topic.name}</span>}
          {showPlace && s.place && <span>{s.place.name}</span>}
        </div>
      </div>
    </article>
  );
}

/** Whose headline this is: it is chosen from the story's reports, so it may not be the first outlet's. */
function headlineNote(s: Story): string {
  const f = s.first_source;
  return `Headline from ${s.lead_source.name}` + (f && f.key !== s.lead_source.key ? `; first reported by ${f.name}` : "");
}

function Overview({ stories, place, topic, onBrief }: { stories: Story[]; place: string; topic: string; onBrief: () => void }) {
  const outlets = new Set(stories.flatMap((s) => s.strip.map((x) => x.k)));
  const types = new Map<string, Set<string>>();
  stories.forEach((s) => s.strip.forEach((x) => { if (!types.has(x.t)) types.set(x.t, new Set()); types.get(x.t)!.add(x.k); }));
  return (
    <div className="reading">
      <h2>{topic ? "Topic overview" : place === "world" ? "Global overview" : "Place overview"}</h2>
      <p className="sub">{plural(stories.length, "story", "stories")} in this list, from {plural(outlets.size, "outlet")}.</p>
      <h4>AI briefing</h4>
      <p className="muted">A cited, neutral briefing of the stories in this list, with every statement linked to the outlet that said it.</p>
      <div className="ai-actions"><button className="ghost" onClick={onBrief}>Open the briefing</button></div>
      {types.size > 0 && (<><h4>Who is reporting</h4>
        <dl className="kv">{[...types.entries()].map(([t, k]) => <Fragment key={t}><dt>{TYPE_LABEL[t] ?? t}</dt><dd>{plural(k.size, "outlet")}</dd></Fragment>)}</dl></>)}
      <h4>Shortcuts</h4>
      <p className="muted">J and K move through stories, B minimises this pane, S saves, [ and ] switch tabs, 1 to 3 switch between Aggregated, Fresh and Ongoing, / jumps anywhere.</p>
    </div>
  );
}

function StoryView({ d, error, onBack, onScope, onSave, onOpen }: { d?: StoryDetail; error: Error | null; onBack: () => void; onScope: (p: { place?: string; topic?: string }) => void; onSave: (s: Story) => void; onOpen: (id: number) => void }) {
  const [read, setRead] = useState<number | null>(null);
  if (error) return <div className="reading"><button className="back" onClick={onBack}><Back /> Back to overview</button><p className="note">{error.message}</p></div>;
  if (!d) return <div className="reading"><p className="muted">Loading…</p></div>;
  const bySource = d.articles_list;
  const h = headline(d);
  const indep = d.indep ?? d.outlets;
  return (
    <div className="reading">
      <button className="back" onClick={onBack}><Back /> Back to overview</button>
      <h2 title={h.original || undefined}>{h.text}</h2>
      {h.from && <p className="xlat-note">Translated from {h.from} on this server. Original: <span lang={d.lang ?? undefined}>{h.original}</span></p>}
      <p className="when big">{eventLine(d)}</p>
      <p className="sub">{plural(d.outlets, "outlet")}{indep < d.outlets ? ` (${indep} independent; the rest reprint the same text)` : ""} in this development, last report {ago(d.last_update)}</p>
      <Bands bands={d.bands} />
      <div className="chips">
        {d.topics.slice(0, 4).map((t) => <button key={t.code} className="chip" onClick={() => onScope({ topic: t.code })}>{t.name}</button>)}
        {d.places.slice(0, 3).map((p) => <button key={p.code} className="chip" onClick={() => onScope({ place: p.code })}>{p.name}</button>)}
        <button className="chip" onClick={() => onSave(d)} aria-pressed={d.saved}>{d.saved ? "Saved" : "Save"}</button>
      </div>
      <p className={`vtag big ${d.verification.level}`}><b>{VERIFY_LABEL[d.verification.level]}.</b> {d.verification.label}.</p>
      <h4>AI brief</h4>
      <StoryBrief key={d.id} storyId={d.id} />
      {d.timeline.length > 1 && (<><h4>Timeline of this storyline</h4>
        <ol className="timeline">{d.timeline.map((x) => (
          <li key={x.id} className={x.current ? "cur" : ""}>
            <span className="t-when">{when(x.first_seen)}</span>
            {x.current ? <b>{headline(x).text}</b> : <button className="link" onClick={() => onOpen(x.id)}>{headline(x).text}</button>}
            <span className="muted"> · {plural(x.indep, "outlet")}{x.breaking ? " · Breaking" : ""}</span>
          </li>))}
          {(d.thread.minor ?? 0) > 0 && <li className="more muted">and {plural(d.thread.minor ?? 0, "smaller report")} of one or two outlets</li>}</ol></>)}
      {d.subevents && d.subevents.length > 0 && (<><h4>Within this story</h4>
        <ul className="subevents">{d.subevents.map((e, i) => (
          <li key={i}>
            {e.url ? <a className="link" href={e.url} target="_blank" rel="noreferrer">{e.headline}</a> : <span>{e.headline}</span>}
            <span className="muted"> · {plural(e.outlets, "outlet")}, {plural(e.reports, "report")} · from {when(e.first)}</span>
          </li>))}</ul></>)}
      <h4>Coverage of this development, newest first</h4>
      <ul className="coverage">
        {bySource.map((a, i) => (<Fragment key={a.id}>
          {(i === 0 || dayOf(a.published_at) !== dayOf(bySource[i - 1].published_at)) && <li className="dayhead">{dayOf(a.published_at)}</li>}
          <li>
            <div className="who">
              <SourceInfo skey={a.source.key} name={a.source.name} />
              <span>{TYPE_LABEL[a.source.type] ?? a.source.type}</span>
              <span className={`band ${a.source.band}${a.source.basis === "estimated" ? " est" : ""}`}>{bandText(a.source.band, a.source.basis)}</span>
              <span title={when(a.published_at)}>{ago(a.published_at)}</span>
            </div>
            <a className="t" href={a.url} target="_blank" rel="noopener noreferrer" title={headline(a).original || undefined}>{headline(a).text}</a>
            {headline(a).from && <span className="xlat">Translated from {headline(a).from}</span>}
            <span className="ext">Opens {a.source.domain} <button className="link" onClick={() => setRead(read === a.id ? null : a.id)}>{read === a.id ? "Hide" : "Read here"}</button></span>
            {read === a.id && <ArticleReader articleId={a.id} onClose={() => setRead(null)} />}
          </li></Fragment>
        ))}
      </ul>
      {(d.reprints?.length ?? 0) > 0 && (<>
        <h4>Same text also published by</h4>
        <p className="muted">These outlets printed one of the reports above word for word (wire copy or republishing). Only who, when and the link are kept, not the text again.</p>
        <ul className="reprints">{d.reprints!.map((r) => (
          <li key={r.url}><SourceInfo skey={r.source.key} name={r.source.name} /> <span className={`band ${r.source.band}${r.source.basis === "estimated" ? " est" : ""}`}>{bandText(r.source.band, r.source.basis)}</span> <a className="link" href={r.url} target="_blank" rel="noopener noreferrer">{ago(r.published_at)}</a></li>))}</ul></>)}
      {d.other_coverage.length > 0 && (<>
        <h4>Other coverage, hidden by the reliability filter you chose</h4>
        <ul className="coverage">{d.other_coverage.map((a) => (
          <li key={a.id}>
            <div className="who"><SourceInfo skey={a.source.key} name={a.source.name} /><span>{TYPE_LABEL[a.source.type] ?? a.source.type}</span><span className={`band ${a.source.band}${a.source.basis === "estimated" ? " est" : ""}`}>{bandText(a.source.band, a.source.basis)}</span><span>{ago(a.published_at)}</span></div>
            <a className="t" href={a.url} target="_blank" rel="noopener noreferrer">{a.title}</a>
            <span className="ext">Opens {a.source.domain}</span>
          </li>))}</ul></>)}
      {d.hidden_articles > 0 && <p className="muted">{plural(d.hidden_articles, "article")} from sources hidden by your reliability filter or by hidden source types are not shown (Settings → Sources).</p>}
    </div>
  );
}

/** How reliable the outlets carrying a story are: one bar, one segment per band, labelled with counts. */
function Bands({ bands }: { bands?: Record<string, number> }) {
  const parts = BAND_ORDER.filter((b) => (bands?.[b] ?? 0) > 0).map((b) => [b, bands![b]] as const);
  const total = parts.reduce((a, [, n]) => a + n, 0);
  if (!total) return null;
  return (
    <figure className="bands" aria-label="Reliability of the outlets carrying this story">
      <figcaption>Reliability of the {plural(total, "outlet")} carrying it</figcaption>
      <div className="bandbar" aria-hidden="true">{parts.map(([b, n]) => <i key={b} className={b} style={{ flexGrow: n }} />)}</div>
      <ul className="bandkey">{parts.map(([b, n]) => <li key={b} title={BAND_NOTE[b]}><i className={b} />{BAND_LABEL[b] ?? b} <b>{n}</b></li>)}</ul>
    </figure>
  );
}
