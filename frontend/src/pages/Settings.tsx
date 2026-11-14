import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useEffect, useMemo, useState } from "react";
import { api, type AiProvider, type AiStatus, type Favourite, type IndexResp, type Resources, type Setting, type SocialStatus, type StorageStatus, type TopicNode, useSettings } from "../api";
import { Check, Star } from "../components/icons";
import { ago, bytes, inFuture, mb, plural } from "../format";

const SECTIONS: { id: string; title: string; desc: string; groups?: string[] }[] = [
  { id: "storage", title: "Storage", desc: "How much disk News Anchor may use and how long it keeps articles. The server is shared, so these are limits, not suggestions.", groups: ["Storage"] },
  { id: "resources", title: "Memory", desc: "News Anchor keeps its memory small and slows its own work down before it would crowd out other apps.", groups: ["Resources"] },
  { id: "topics", title: "Topics", desc: "Every topic exists, but your menus show the ones you enable here. A topic with its own specialist sources is only polled while it is enabled." },
  { id: "feed", title: "Lists", desc: "How stories are ranked and shown.", groups: ["Feed", "Latest", "Storylines", "Breaking news"] },
  { id: "sources", title: "Sources and trust", desc: "Languages, automatic discovery of new sources (with your Media Cloud token), an optional reliability filter, and the weights the trust crawler uses. Everything is shown by default; changes apply at once.", groups: ["Sources", "Trust"] },
  { id: "social", title: "Social", desc: "Posts from Bluesky, Mastodon, YouTube, Reddit and X, from the accounts you list or your own timelines, read at the news refresh interval. Posts are always marked Social and never reach Top or Trending without a news outlet carrying the story too. Logins and keys stay on this server.", groups: ["Social"] },
  { id: "search", title: "Search and reading", desc: "How reworded reports of one story are merged, how search understands meaning, and how article text is fetched for the reading pane.", groups: ["Search"] },
  { id: "ai", title: "AI", desc: "Free AI services, tried in your order, write the cited readings and briefings. Nothing is sent anywhere until you add a key. Prompts are editable; the reply format and the citation checks are not.", groups: ["AI"] },
  { id: "refresh", title: "Refresh", desc: "How often feeds are checked and how politely.", groups: ["Refresh"] },
  { id: "home", title: "Home", desc: "Your default places.", groups: ["Home"] },
  { id: "security", title: "Password", desc: "The one password for this app. Change it here; you stay signed in on this browser." },
  { id: "appearance", title: "Appearance", desc: "Theme and reading comfort.", groups: ["Appearance"] },
  { id: "readme", title: "Read me", desc: "What the lists, words and marks in News Anchor mean, in plain language. The numbers quoted are the defaults; you can change them in the sections below." },
];

export default function Settings() {
  const s = useSettings();
  useEffect(() => { if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView(); }, [s.isSuccess]);
  const qc = useQueryClient();
  const reset = useMutation({ mutationFn: (b: { group?: string }) => api("/settings/reset", { method: "POST", body: b }), onSuccess: () => qc.invalidateQueries() });
  if (!s.data) return <div className="settings"><p className="muted">Loading…</p></div>;
  return (
    <div className="page"><div className="settings">
      <h1>Settings</h1>
      <p className="lede">Everything you can change lives here. A value that differs from its default shows a Reset link; changes save as you make them.</p>
      <nav className="snav" aria-label="Sections">{SECTIONS.map((x) => <button key={x.id} onClick={() => document.getElementById(x.id)?.scrollIntoView({ behavior: "smooth" })}>{x.title}</button>)}</nav>
      {SECTIONS.map((sec) => (
        <section key={sec.id} id={sec.id} className="srow">
          <div>
            <h2>{sec.title}</h2><p className="desc">{sec.desc}</p>
            {sec.groups && <button className="link" style={{ marginTop: 10, fontSize: 13 }} onClick={() => sec.groups!.forEach((g) => reset.mutate({ group: g }))}>Reset {sec.title === "AI" ? "AI" : sec.title.toLowerCase()} to defaults</button>}
          </div>
          <div className="panel">
            {sec.id === "readme" && <ReadmePanel />}
            {sec.id === "storage" && <StoragePanel />}
            {sec.id === "resources" && <ResourcesPanel />}
            {sec.id === "topics" && <TopicPicker />}
            {sec.id === "search" && <SearchPanel />}
            {sec.id === "security" && <PasswordPanel />}
            {sec.id === "ai" && <AiPanel />}
            {sec.id === "social" && <SocialPanel />}
            {sec.id !== "storage" && sec.id !== "resources" && sec.groups?.map((g) => <GroupFields key={g} group={g} />)}
          </div>
        </section>
      ))}
    </div></div>
  );
}

function GroupFields({ group }: { group: string }) {
  const s = useSettings();
  const items = s.data?.settings.filter((x) => x.group === group) ?? [];
  return <>{items.map((x) => <SettingRow key={x.key} s={x} />)}</>;
}

// ---------- one setting ----------
export function SettingRow({ s }: { s: Setting }) {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const idx = useQuery({ queryKey: ["index"], queryFn: () => api<IndexResp>("/index"), enabled: s.kind === "place", staleTime: 300_000 });
  const save = useMutation({
    mutationFn: (v: unknown) => api("/settings", { method: "PUT", body: { changes: { [s.key]: v } } }),
    onSuccess: () => { setErr(null); setSaved(true); window.setTimeout(() => setSaved(false), 1500); qc.invalidateQueries({ queryKey: ["settings"] }); qc.invalidateQueries({ queryKey: ["storage"] }); qc.invalidateQueries({ queryKey: ["resources"] }); qc.invalidateQueries({ queryKey: ["stories"] }); },
    onError: (e: Error) => setErr(e.message),
  });
  const reset = useMutation({ mutationFn: () => api("/settings/reset", { method: "POST", body: { key: s.key } }), onSuccess: () => { setErr(null); qc.invalidateQueries(); } });
  const put = (v: unknown) => save.mutate(v);
  const level = s.key === "home.country" ? "country" : s.key === "home.state" ? "state" : s.key === "home.city" ? "city" : null;

  let control: React.ReactNode = null;
  if (s.kind === "bool") control = <button role="switch" className="toggle" aria-checked={!!s.value} aria-label={s.label} onClick={() => put(!s.value)} />;
  else if (s.kind === "enum") control = <select className="select" value={String(s.value)} onChange={(e) => put(e.target.value)} aria-label={s.label}>{s.choices.map((c) => <option key={c} value={c}>{c[0].toUpperCase() + c.slice(1)}</option>)}</select>;
  else if (s.kind === "int" || s.kind === "float") control = <NumberInput s={s} onCommit={put} />;
  else if (s.kind === "secret") control = <SecretInput s={s} onCommit={put} />;
  else if (s.kind === "text") control = s.multiline ? <TextArea s={s} onCommit={put} /> : <input className="input" style={{ width: 280 }} defaultValue={String(s.value)} aria-label={s.label} onBlur={(e) => e.target.value !== s.value && put(e.target.value)} />;
  else if (s.kind === "multi") control = <div className="multi">{s.choices.map((c) => { const on = (s.value as string[]).includes(c); return <button key={c} aria-pressed={on} onClick={() => put(on ? (s.value as string[]).filter((x) => x !== c) : [...(s.value as string[]), c])}>{c.replace(/_/g, " ")}</button>; })}</div>;
  else if (s.kind === "place") {
    const opts = (idx.data?.places ?? []).filter((p) => (level ? p.level === level : true)).sort((a, b) => a.path.localeCompare(b.path));
    control = <select className="select" style={{ width: 240 }} value={String(s.value)} onChange={(e) => put(e.target.value)} aria-label={s.label}>{opts.map((p) => <option key={p.code} value={p.code}>{level ? p.name : p.path}</option>)}</select>;
  }
  return (
    <div className={`setting${s.multiline ? " wide" : ""}`}>
      <span className="lbl">{s.label}{saved && <span className="muted" style={{ marginLeft: 8, fontWeight: 400 }}><Check /> Saved</span>}</span>
      <div className="ctl">{!s.is_default && <button className="reset" onClick={() => reset.mutate()}>Reset{s.default !== null && s.default !== undefined && s.kind !== "multi" ? ` to ${String(s.default)}` : ""}</button>}{control}{s.unit && <span className="unit">{s.unit}</span>}</div>
      <span className="hlp">{s.help}</span>
      {err && <span className="err">{err}</span>}
    </div>
  );
}

function SecretInput({ s, onCommit }: { s: Setting; onCommit: (v: unknown) => void }) {
  const [v, setV] = useState("");
  return (
    <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
      <input className="input" type="password" autoComplete="off" style={{ width: 260 }} placeholder={s.value ? "A token is set. Type to replace it" : "Paste your token"} value={v} onChange={(e) => setV(e.target.value)} aria-label={s.label} />
      <button className="ghost" disabled={!v.trim()} onClick={() => { onCommit(v.trim()); setV(""); }}>Save</button>
    </span>
  );
}

function TextArea({ s, onCommit }: { s: Setting; onCommit: (v: unknown) => void }) {
  const [v, setV] = useState(String(s.value));
  useEffect(() => setV(String(s.value)), [s.value]);
  return <textarea className="input area" rows={Math.min(14, Math.max(4, v.split("\n").length + 1))} value={v} onChange={(e) => setV(e.target.value)} onBlur={() => v !== s.value && v.trim() && onCommit(v)} aria-label={s.label} spellCheck />;
}

function NumberInput({ s, onCommit }: { s: Setting; onCommit: (v: unknown) => void }) {
  const [v, setV] = useState(s.value === null || s.value === undefined ? "" : String(s.value));
  useEffect(() => setV(s.value === null || s.value === undefined ? "" : String(s.value)), [s.value]);
  const commit = () => {
    if (v.trim() === "") { if (s.nullable) onCommit(null); return; }
    const n = Number(v);
    if (!Number.isNaN(n) && n !== s.value) onCommit(n);
  };
  return <input className="input" type="number" inputMode="decimal" step={s.kind === "float" ? "any" : 1} min={s.min ?? undefined} max={s.max ?? undefined} value={v} aria-label={s.label}
                placeholder={s.nullable ? "Proposed" : undefined} onChange={(e) => setV(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />;
}

const sel = (all: Setting[] | undefined, key: string) => all?.find((x) => x.key === key);

// ---------- storage ----------
function StoragePanel() {
  const qc = useQueryClient();
  const settings = useSettings();
  const q = useQuery({ queryKey: ["storage"], queryFn: () => api<StorageStatus>("/storage"), refetchInterval: 30_000 });
  const enforce = useMutation({ mutationFn: () => api<{ actions: string[] }>("/storage/enforce", { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["storage"] }) });
  const useProposed = useMutation({ mutationFn: (gb: number) => api("/settings", { method: "PUT", body: { changes: { "storage.disk_cap_gb": gb } } }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["storage"] }); qc.invalidateQueries({ queryKey: ["settings"] }); } });
  const st = q.data;
  const all = settings.data?.settings;
  if (!st) return <p className="muted">Reading the disk…</p>;
  const ours = st.usage.total, others = Math.max(0, st.disk_total - st.disk_free - ours);
  const pct = (n: number) => `${(100 * n) / st.disk_total}%`;
  const retention = st.retention_days, perDay = st.per_day_bytes, fixed = st.fixed_bytes;
  const need = fixed + retention * perDay;
  const holds = perDay > 0 ? Math.max(0, Math.floor((st.cap - fixed) / perDay)) : null;
  const young = st.days_with_data < 3;
  return (
    <>
      <div className="nums">
        <div><b>{bytes(st.disk_total)}</b><span>Disk total</span></div>
        <div><b>{bytes(st.disk_free)}</b><span>Free now</span></div>
        <div><b>{bytes(ours)}</b><span>News Anchor uses</span></div>
        <div><b>{bytes(st.cap)}</b><span>{st.cap_confirmed ? "Your disk cap" : "Proposed disk cap"}</span></div>
      </div>
      <div>
        <div className="bar" role="img" aria-label={`News Anchor uses ${bytes(ours)} of a ${bytes(st.cap)} cap on a ${bytes(st.disk_total)} disk`}>
          <i className="db" style={{ width: pct(st.usage.database) }} /><i className="wal" style={{ width: pct(st.usage.wal) }} /><i className="img" style={{ width: pct(st.usage.images_allowance) }} />
          <i className="oth" style={{ width: pct(st.usage.files) }} /><i className="other-apps" style={{ width: pct(others) }} />
          <span className="cap" style={{ left: pct(Math.min(st.cap, st.disk_total)) }} title="Disk cap" />
        </div>
        <div className="legend" style={{ marginTop: 8 }}>
          <span><i style={{ background: "var(--accent)" }} />Database {bytes(st.usage.database)}</span>
          <span><i style={{ background: "#7aa7de" }} />Write-ahead log {st.usage.wal_known ? bytes(st.usage.wal) : "unknown"}</span>
          <span><i style={{ background: "#5c7aa3" }} />Container images (allowance) {bytes(st.usage.images_allowance)}</span>
          <span><i style={{ background: "#8a8c92" }} />Other files {bytes(st.usage.files)}</span>
          <span><i style={{ background: "var(--control)" }} />Other apps {bytes(others)}</span>
        </div>
      </div>
      {!st.cap_confirmed && (
        <div className="est">No cap is confirmed yet, so News Anchor uses the proposal of <b>{st.proposed_cap_gb} GB</b> (a quarter of the disk, at most 120 GB).{" "}
          <button className="link" onClick={() => useProposed.mutate(st.proposed_cap_gb)}>Confirm {st.proposed_cap_gb} GB</button></div>)}
      <div className={`est${need > st.cap ? " bad" : ""}`}>
        Keeping {plural(retention, "day")} needs about <b>{bytes(need)}</b> at the current rate ({bytes(perDay)} a day plus {bytes(fixed)} fixed). Your cap holds about <b>{holds === null ? "–" : holds >= 3650 ? "more than 10 years" : plural(holds, "day")}</b>.
        {young && <span className="muted"> Measured from {plural(st.days_with_data, "day")} of data, so the estimate is rough until a few days have collected.</span>}
        {need > st.cap && <span> The oldest days will be removed early to stay inside the cap.</span>}
      </div>
      {st.kept_days_note && <div className="est bad">{st.kept_days_note}</div>}
      {st.paused && <div className="est bad">Saving new articles is paused because a limit is reached. Free some space or raise the cap.</div>}
      {["storage.retention_days", "storage.disk_cap_gb", "storage.min_free_gb", "storage.image_reserve_gb"].map((k) => { const x = sel(all, k); return x ? <SettingRow key={k} s={x} /> : null; })}
      <div className="setting"><span className="lbl">Check limits now</span><div className="ctl"><button className="ghost" disabled={enforce.isPending} onClick={() => enforce.mutate()}>{enforce.isPending ? "Checking" : "Free space now"}</button></div>
        <span className="hlp">Limits are checked every 10 minutes. {enforce.data?.actions.length ? enforce.data.actions.join(" ") : enforce.data ? "Nothing needed removing." : ""}</span></div>
      <p className="muted" style={{ fontSize: 12.5 }}>Backups are not built yet. The database lives in <code>data/postgres</code>; copy it while the app is stopped, or use <code>pg_dump</code>.</p>
    </>
  );
}

// ---------- resources ----------
function ResourcesPanel() {
  const qc = useQueryClient();
  const settings = useSettings();
  const q = useQuery({ queryKey: ["resources"], queryFn: () => api<Resources>("/resources"), refetchInterval: 10_000 });
  const profile = useMutation({ mutationFn: (name: string) => api("/settings/profile", { method: "POST", body: { name } }), onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }) });
  const r = q.data, all = settings.data?.settings;
  return (
    <>
      {r && (<div className="nums">
        <div><b>{mb(r.app_mb)}</b><span>App process now (peak {mb(r.app_peak_24h_mb)} in 24 h)</span></div>
        <div><b>{mb(r.db_mb)}</b><span>Database{r.db_mb === null ? " (not reported)" : ""}</span></div>
        <div><b>{mb(r.server_available_mb)}</b><span>Server memory available of {mb(r.server_total_mb)}</span></div>
        <div><b>{mb(r.budget_mb)}</b><span>Your RAM budget</span></div>
      </div>)}
      <div className="setting"><span className="lbl">Profile</span>
        <div className="ctl profiles">{settings.data?.profiles.map((p) => <button key={p} onClick={() => profile.mutate(p)}>{p[0].toUpperCase() + p.slice(1)}</button>)}</div>
        <span className="hlp">Light: 8 parallel fetches, 1 GB budget. Balanced: 16, 2 GB. Fast: 32, 3 GB. A profile only sets the three values below; change any of them on its own afterwards.</span></div>
      {["resources.ram_budget_mb", "resources.leave_available_mb", "refresh.concurrency"].map((k) => { const x = sel(all, k); return x ? <SettingRow key={k} s={x} /> : null; })}
      <p className="muted" style={{ fontSize: 12.5 }}>The budget covers the app process. Each container also has a hard limit set in <code>.env</code> (<code>NA_APP_MEM</code>, <code>NA_DB_MEM</code>), a backstop the app should never reach.</p>
    </>
  );
}

// ---------- topic picker ----------
function TopicPicker() {
  const qc = useQueryClient();
  const t = useQuery({ queryKey: ["topicTree"], queryFn: () => api<{ topics: TopicNode[] }>("/topics") });
  const favs = useQuery({ queryKey: ["favourites"], queryFn: () => api<{ favourites: Favourite[] }>("/favourites") });
  const [cat, setCat] = useState<string>("sports");
  const [q, setQ] = useState("");
  const [only, setOnly] = useState(false);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["topicTree"] }); qc.invalidateQueries({ queryKey: ["topicMenu"] }); qc.invalidateQueries({ queryKey: ["index"] }); };
  const set = useMutation({ mutationFn: (v: { code: string; enabled: boolean }) => api(`/topics/${v.code}`, { method: "PUT", body: { enabled: v.enabled } }), onSuccess: refresh });
  const bulk = useMutation({ mutationFn: (v: { codes: string[]; enabled: boolean }) => api("/topics/bulk", { method: "POST", body: v }), onSuccess: refresh });
  const resetAll = useMutation({ mutationFn: () => api("/topics/reset", { method: "POST" }), onSuccess: refresh });
  const fav = useMutation({
    mutationFn: (v: { code: string; on: boolean }) => v.on ? api(`/favourites?place=world&topic=${encodeURIComponent(v.code)}`, { method: "DELETE" }) : api("/favourites", { method: "POST", body: { place: "world", topic: v.code } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["favourites"] }),
  });

  const all = t.data?.topics ?? [];
  const byParent = useMemo(() => { const m = new Map<string, TopicNode[]>(); all.forEach((n) => { const k = n.parent ?? ""; m.set(k, [...(m.get(k) ?? []), n]); }); return m; }, [all]);
  const roots = byParent.get("") ?? [];
  useEffect(() => { if (roots.length && !roots.some((r) => r.code === cat)) setCat(roots[0].code); }, [roots, cat]);

  const flat = useMemo(() => {
    const out: TopicNode[] = [];
    const walk = (code: string) => (byParent.get(code) ?? []).forEach((n) => { out.push(n); walk(n.code); });
    walk(cat);
    return out;
  }, [byParent, cat]);
  const ql = q.trim().toLowerCase();
  const byCode = new Map(all.map((n) => [n.code, n]));
  const matchSet = useMemo(() => {
    const keep = new Set<string>();
    flat.forEach((n) => {
      const hit = (!ql || n.name.toLowerCase().includes(ql) || n.sources.some((s) => s.toLowerCase().includes(ql))) && (!only || n.enabled);
      if (hit) { keep.add(n.code); let p = n.parent; while (p && p !== cat) { keep.add(p); p = byCode.get(p)?.parent ?? null; } }
    });
    return keep;
  }, [flat, ql, only]);   // eslint-disable-line react-hooks/exhaustive-deps
  const shown = flat.filter((n) => matchSet.has(n.code));
  const favSet = new Set((favs.data?.favourites ?? []).filter((f) => f.place === "world" && f.topic).map((f) => f.topic));
  const count = (root: string) => { let e = 0, n = 0; const walk = (c: string) => (byParent.get(c) ?? []).forEach((x) => { n++; if (x.enabled) e++; walk(x.code); }); walk(root); return [e, n]; };
  const catNode = byCode.get(cat);

  if (!t.data) return <p className="muted">Loading topics…</p>;
  return (
    <div className="picker">
      <nav aria-label="Categories">
        {roots.map((r) => { const [e, n] = count(r.code); return <button key={r.code} aria-current={r.code === cat} onClick={() => { setCat(r.code); setQ(""); }}><span>{r.name}</span><span className="muted">{e}/{n}</span></button>; })}
        <button className="link" style={{ marginTop: 10, padding: "6px 10px" }} onClick={() => resetAll.mutate()}>Reset topics to defaults</button>
      </nav>
      <div>
        <div className="tools">
          <input className="input" placeholder={`Search ${catNode?.name ?? "topics"} or a source`} value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search topics" />
          <label style={{ display: "flex", gap: 8, alignItems: "center" }}><button role="switch" className="toggle" aria-checked={only} onClick={() => setOnly(!only)} aria-label="Show only enabled" /> Only enabled</label>
          <button className="link" onClick={() => bulk.mutate({ codes: shown.map((n) => n.code), enabled: true })}>Enable {ql ? "these" : "all"}</button>
          <button className="link" onClick={() => bulk.mutate({ codes: shown.map((n) => n.code), enabled: false })}>Disable {ql ? "these" : "all"}</button>
        </div>
        <p className="muted" style={{ fontSize: 13, marginBottom: 6 }}>{plural(shown.length, "topic")} shown in {catNode?.name}. The star adds a topic to your favourites row.</p>
        {shown.map((n) => (
          <div className="trow" key={n.code} style={{ paddingLeft: (n.depth - 2) * 22 }}>
            <button className="box" role="checkbox" aria-checked={n.enabled} aria-label={`Show ${n.name}`} onClick={() => set.mutate({ code: n.code, enabled: !n.enabled })}>{n.enabled && <Check />}</button>
            <span className="nm">{n.name}{n.default_enabled && <span className="muted" style={{ fontWeight: 400 }}> · on by default</span>}</span>
            <button className={`star${favSet.has(n.code) ? " on" : ""}`} aria-label={favSet.has(n.code) ? `Remove ${n.name} from favourites` : `Add ${n.name} to favourites`} aria-pressed={favSet.has(n.code)} onClick={() => fav.mutate({ code: n.code, on: favSet.has(n.code) })}><Star on={favSet.has(n.code)} /></button>
            <span className="srcs">{n.sources.length ? `Sources: ${n.sources.slice(0, 6).join(", ")}${n.sources.length > 6 ? ` and ${n.sources.length - 6} more` : ""}` : "No dedicated sources yet. Covered by general feeds; discovery adds more in phase 2."}</span>
          </div>
        ))}
        {!shown.length && <p className="muted">Nothing matches.</p>}
      </div>
    </div>
  );
}


// ---------- search status ----------
function SearchPanel() {
  const q = useQuery({ queryKey: ["searchStatus"], queryFn: () => api<{ embedded: number; stories: number; backend: string | null; mode: string; model_loaded: boolean; model_error: string }>("/search/status"), refetchInterval: 20_000 });
  const d = q.data;
  if (!d) return null;
  return (
    <div className="nums">
      <div><b>{d.embedded.toLocaleString()}</b><span>of {d.stories.toLocaleString()} stories have a meaning vector</span></div>
      <div><b>{d.backend ?? "–"}</b><span>method in use ({d.mode} chosen)</span></div>
      <div><b>{d.model_loaded ? "loaded" : "not loaded"}</b><span>MiniLM model (loaded when needed, released after two idle minutes)</span></div>
      <div><b>{d.model_error ? "see below" : "ok"}</b><span>{d.model_error || "no model error"}</span></div>
    </div>
  );
}

// ---------- AI providers ----------
function AiPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["aiStatus"], queryFn: () => api<AiStatus>("/ai/status"), refetchInterval: 15_000 });
  const move = useMutation({ mutationFn: (order: string[]) => api("/ai/order", { method: "PUT", body: { order } }), onSuccess: () => qc.invalidateQueries({ queryKey: ["aiStatus"] }) });
  const st = q.data;
  if (!st) return <p className="muted">Loading…</p>;
  const ids = st.providers.map((p) => p.id);
  const shift = (i: number, d: number) => { const o = [...ids]; [o[i], o[i + d]] = [o[i + d], o[i]]; move.mutate(o); };
  return (
    <>
      <div className="nums">
        <div><b>{st.usage.calls_today}</b><span>AI calls today (cap {st.daily_cap})</span></div>
        <div><b>{st.providers.filter((p) => p.enabled).length}</b><span>services ready</span></div>
        <div><b>{st.usage.today.reduce((a, b) => a + b.tin + b.tout, 0).toLocaleString()}</b><span>tokens today</span></div>
        <div><b>{st.ready ? "ready" : "not set up"}</b><span>{st.ready ? "readings and briefings work" : "add a key below"}</span></div>
      </div>
      <p className="muted" style={{ fontSize: 13 }}>Services are tried top to bottom. When one is rate limited or out of daily quota, the next one answers, and the first is left alone until it recovers. Keys are stored in this server's database and never sent to the browser or anywhere but the service itself. Free tiers change often: the model is “auto” (picked from what the service offers) unless you choose one.</p>
      {st.providers.map((p, i) => <ProviderRow key={p.id} p={p} first={i === 0} last={i === st.providers.length - 1} onShift={(d) => shift(i, d)} />)}
      {st.usage.recent.length > 0 && (
        <details className="method"><summary>Recent calls</summary>
          <table className="table"><thead><tr><th>When</th><th>Service</th><th>Task</th><th className="num">Tokens in/out</th><th className="num">Time</th><th>Result</th></tr></thead>
            <tbody>{st.usage.recent.map((r, i) => <tr key={i}><td>{ago(r.at)}</td><td>{r.provider} <span className="muted">{r.model}</span></td><td>{r.task}</td><td className="num">{r.tokens_in}/{r.tokens_out}</td><td className="num">{(r.ms / 1000).toFixed(1)} s</td><td>{r.ok ? <span className="pill ok">ok</span> : <span className="pill bad" title={r.error}>{r.error.slice(0, 50)}</span>}</td></tr>)}</tbody></table>
        </details>)}
    </>
  );
}

function ProviderRow({ p, first, last, onShift }: { p: AiProvider; first: boolean; last: boolean; onShift: (d: number) => void }) {
  const qc = useQueryClient();
  const [key, setKey] = useState("");
  const [model, setModel] = useState(p.model);
  const [url, setUrl] = useState(p.base_url);
  const [limit, setLimit] = useState(String(p.daily_limit));
  const [models, setModels] = useState<string[]>([]);
  const refresh = () => qc.invalidateQueries({ queryKey: ["aiStatus"] });
  const save = useMutation({ mutationFn: (b: Record<string, unknown>) => api(`/ai/providers/${p.id}`, { method: "PUT", body: b }), onSuccess: refresh });
  const reset = useMutation({ mutationFn: () => api(`/ai/providers/${p.id}`, { method: "DELETE" }), onSuccess: () => { setKey(""); refresh(); } });
  const test = useMutation({ mutationFn: () => api<{ ok: boolean; model?: string; models?: string[]; error?: string; ms?: number }>(`/ai/providers/${p.id}/test`, { method: "POST" }), onSuccess: (r) => { if (r.models) setModels(r.models); refresh(); } });
  const configured = p.has_key || (!p.needs_key && p.enabled);
  return (
    <div className={`prov${p.enabled ? " on" : ""}`}>
      <div className="phead">
        <b>{p.label}</b>
        <span className={`pill ${p.enabled ? "ok" : ""}`}>{p.enabled ? "ready" : "off"}</span>
        {p.blocked_until && <span className="pill bad" title={p.last_error}>waiting {inFuture(p.blocked_until)}</span>}
        <span className="muted">{p.calls_today} calls today</span>
        <span className="sp" />
        <button className="icon-btn" disabled={first} aria-label={`Try ${p.label} earlier`} onClick={() => onShift(-1)}>↑</button>
        <button className="icon-btn" disabled={last} aria-label={`Try ${p.label} later`} onClick={() => onShift(1)}>↓</button>
      </div>
      <p className="muted small">{p.note} {p.key_url && <a className="link" href={p.key_url} target="_blank" rel="noopener noreferrer">Get a key</a>}</p>
      <div className="pgrid">
        {p.needs_key && <label>Key<input className="input" type="password" autoComplete="off" placeholder={p.has_key ? "A key is set. Type to replace" : "Paste key"} value={key} onChange={(e) => setKey(e.target.value)} /></label>}
        {p.editable_url && <label>Address<input className="input" value={url} onChange={(e) => setUrl(e.target.value)} /></label>}
        <label>Model{models.length ? <select className="select" value={model} onChange={(e) => setModel(e.target.value)}><option value="auto">auto</option>{models.map((m) => <option key={m} value={m}>{m}</option>)}</select> : <input className="input" value={model} onChange={(e) => setModel(e.target.value)} placeholder="auto" />}</label>
        <label>Most calls a day (0 = no limit)<input className="input" type="number" min={0} value={limit} onChange={(e) => setLimit(e.target.value)} /></label>
      </div>
      <div className="ai-actions">
        <button className="ghost" disabled={save.isPending} onClick={() => save.mutate({ ...(key.trim() ? { key: key.trim() } : {}), ...(p.editable_url ? { base_url: url } : {}), model: model || "auto", daily_limit: Number(limit) || 0, enabled: true })}>Save</button>
        <button className="ghost" disabled={!configured || test.isPending} onClick={() => test.mutate()}>{test.isPending ? "Testing…" : "Test"}</button>
        {(p.has_key || p.base_url !== "") && <button className="link" onClick={() => reset.mutate()}>Remove and reset</button>}
        {p.enabled && <button className="link" onClick={() => save.mutate({ enabled: false })}>Switch off</button>}
        {!p.enabled && (p.has_key || !p.needs_key) && <button className="link" onClick={() => save.mutate({ enabled: true })}>Switch on</button>}
      </div>
      {test.data && <p className={test.data.ok ? "aimeta" : "err"}>{test.data.ok ? `Works. Using ${test.data.model} (${((test.data.ms ?? 0) / 1000).toFixed(1)} s). ${models.length} models available; pick one above to pin it.` : test.data.error}</p>}
      {save.isError && <p className="err">{(save.error as Error).message}</p>}
      {p.last_error && !test.data && <p className="aimeta">Last problem: {p.last_error}</p>}
    </div>
  );
}

function PasswordPanel() {
  const [cur, setCur] = useState(""), [n1, setN1] = useState(""), [n2, setN2] = useState("");
  const change = useMutation({ mutationFn: () => api("/auth/password", { method: "POST", body: { current: cur, new: n1 } }), onSuccess: () => { setCur(""); setN1(""); setN2(""); } });
  const bad = n1.length > 0 && n1.length < 8 ? "At least 8 characters." : n2 && n1 !== n2 ? "The two new passwords differ." : "";
  return (
    <form onSubmit={(e) => { e.preventDefault(); if (!bad && cur && n1) change.mutate(); }} style={{ display: "grid", gap: 10, maxWidth: 360 }}>
      <label className="pgrid"><span>Current password</span><input className="input" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} /></label>
      <label className="pgrid"><span>New password (8 characters or more)</span><input className="input" type="password" autoComplete="new-password" value={n1} onChange={(e) => setN1(e.target.value)} /></label>
      <label className="pgrid"><span>New password again</span><input className="input" type="password" autoComplete="new-password" value={n2} onChange={(e) => setN2(e.target.value)} /></label>
      {bad && <span className="err">{bad}</span>}
      {change.isError && <span className="err">{(change.error as Error).message}</span>}
      {change.isSuccess && <span className="aimeta">Password changed.</span>}
      <div><button className="ghost" type="submit" disabled={change.isPending || !cur || !n1 || !!bad || n1 !== n2}>Change password</button></div>
    </form>
  );
}

// ---------- social connections ----------
const PLATFORM: Record<string, string> = { bluesky: "Bluesky", mastodon: "Mastodon", youtube: "YouTube", reddit: "Reddit", x: "X" };
function SocialPanel() {
  const qc = useQueryClient();
  const st = useQuery({ queryKey: ["social"], queryFn: () => api<SocialStatus>("/social"), refetchInterval: 30_000 });
  const sync = useMutation({ mutationFn: () => api("/social/sync", { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["social"] }) });
  const official = useMutation({ mutationFn: () => api("/social/official", { method: "POST" }), onSuccess: () => window.setTimeout(() => qc.invalidateQueries({ queryKey: ["social"] }), 8000) });
  const logout = useMutation({ mutationFn: () => api("/social/x/logout", { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["social"] }) });
  const params = new URLSearchParams(location.search);
  const xmsg = params.get("social") === "x-ok" ? `Connected to X as @${params.get("user")}.` : params.get("social") === "x-error" ? `X login did not complete: ${params.get("msg")}` : "";
  const d = st.data;
  if (!d) return <p className="muted">Loading…</p>;
  const x = d.x;
  return (
    <div className="socialpanel">
      {xmsg && <p className={params.get("social") === "x-ok" ? "ok" : "note"}>{xmsg}</p>}
      <dl className="kv">
        {Object.keys(PLATFORM).map((p) => {
          const v = d.platforms[p];
          return <Fragment key={p}><dt>{PLATFORM[p]}</dt><dd>{v ? `${plural(v.feeds, "feed")}, ${v.working} working, ${plural(v.posts, "post")} collected` : "Nothing followed yet"}
            {v?.errors.map((e, i) => <span key={i} className="note-inline"><br />{e.name}: {e.error}</span>)}</dd></Fragment>;
        })}
        <dt>Official accounts</dt><dd>{d.official_accounts ? `${plural(d.official_accounts, "outlet")} with accounts on record (Wikidata), checked ${ago(d.official_at)}` : "Not looked up yet"} <button className="link" disabled={official.isPending} onClick={() => official.mutate()}>Look up now</button></dd>
        <dt>YouTube key</dt><dd>{d.youtube_key ? "Set (channels are read through the YouTube Data API)" : "Not set: YouTube is not read (its robots.txt asks programs not to read channel feeds)"}</dd>
        <dt>Bluesky login</dt><dd>{d.bluesky_login ? "Set (timeline and search are read)" : "Not set (public accounts only)"}</dd>
        <dt>Reddit login</dt><dd>{d.reddit_login ? "Set (read through Reddit's API)" : `Not set: subreddits are read from RSS until Reddit ends it on ${d.reddit_rss_ends}`}</dd>
        <dt>X</dt><dd>{x.connected ? <>Connected as <b>@{x.username}</b> <button className="link" onClick={() => logout.mutate()}>Disconnect</button></> : x.bearer ? "App bearer token set (listed accounts only)" : "Not connected"}
          <br /><span className="muted">{x.used.toLocaleString()} of {x.budget.toLocaleString()} posts read in {x.month} (about ${x.cost_usd.toFixed(2)} billed by X)</span></dd>
      </dl>
      <div className="ai-actions">
        {!x.connected && <a className={`ghost${x.client_id ? "" : " disabled"}`} href={x.client_id ? "/api/social/x/login" : undefined} aria-disabled={!x.client_id}
            title={x.client_id ? "" : "Add your X app's client ID below first"}>Log in with X</a>}
        <button className="ghost" disabled={sync.isPending} onClick={() => sync.mutate()}>{sync.isPending ? "Applying…" : "Apply changes now"}</button>
        <span className="muted">Changes below are picked up within a minute anyway. X login needs your app's callback set to <code>{location.origin}/api/social/x/callback</code>.</span>
      </div>
    </div>
  );
}

function ReadmePanel() {
  return (
    <div className="readme">
      <h3>The lists (tabs on the News page)</h3>
      <dl>
        <dt>Top</dt>
        <dd>The biggest stories of the last 24 hours, in three views. <b>Fresh</b>: stories whose event was first reported in the last 24 hours, a new event or a new development of a storyline that had been quiet. <b>Ongoing</b>: stories that began earlier and are still being reported. <b>Aggregated</b> (the default): both, fused by rank (each list's first story shares the top, then the seconds, and so on), so a giant ongoing story cannot push all the fresh news out and fresh news cannot hide the giants. A story is ranked by how widely outlets reported it in the last 24 hours, an outlet counting less the longer ago it joined (decay: set the scale and the speed in Settings, Feed). An ongoing story (one that began earlier) is scaled by how many outlets joined it in the last 6 hours: with none it sinks to the bottom of Aggregated, with 12 it counts for half, with 60 for 83% (Settings, Feed); new stories are not scaled. At least 2 independent outlets must carry a story to enter; if fewer qualify, single-outlet stories fill the list and are marked. Breaking is only a mark and does not change the order. A story that is reported again after its time limit is joined to its earlier part, so one event is one row. Developments of one storyline that are all in the list share one row that opens to show them. A story that repeats a better one above it (same event, different wording) is dropped from the list.</dd>
        <dt>Trending</dt>
        <dd>What is picking up speed right now, with the same Fresh, Ongoing and Aggregated views. A story's score is the number of outlets that joined it in the last 2 hours minus their usual number for such a window (read from the day before), over the square root of the usual number; at least 3 outlets are needed. A new story has no usual number, so all its outlets count, and a big story that has been steady for a day scores nothing.</dd>
        <dt>Latest</dt>
        <dd>The newest news that is confirmed. A story is listed when enough different outlets have reported it (3 by default), at different times: outlets printing the same wire text count once, and reports published within the same 3 minutes count once, because posts placed at one moment are how spam networks look. A story reported by 10 different outlets or more is listed at once. The list is ordered by when a story earned its place, not by its first report, and a headline that is an advertisement is never listed. One outlet's post is never listed here (it stays in Explore until it is picked up or deleted). Settings → Lists → Latest.</dd>
        <dt>Storylines</dt>
        <dd>Matters followed over days, such as an election, a trial or a war. They are ranked by the coverage all their developments drew in the period you pick (6 hours to 30 days).</dd>
        <dt>Saved</dt>
        <dd>Stories you bookmarked (press S on a story).</dd>
      </dl>

      <h3>How the lists rank</h3>
      <p>Top counts fair coverage: each outlet counts once, outlets printing the same wire text count once, and the outlets of one country add up slowly (a new country adds more than the hundredth outlet of one). In Top an outlet counts less the longer ago it joined the story (decay, Settings, Feed). Trending ignores size and ranks the surge above a story's own usual rate. Storylines has no fading: the period you pick decides what counts as recent, and a row shows how many outlets reported it inside the period. Feeds are checked continuously (Status shows how many were checked in the last 30 minutes and how many are late), and the lists are recomputed from the newest stories at most 20 seconds old. The News page refreshes its lists by itself when new stories arrive, when its connection comes back after a break, when you return to the tab, and every minute while it is open. Latest does not rank by size: it lists stories in the order they were confirmed.</p>

      <h3>Words</h3>
      <dl>
        <dt>Story or development</dt>
        <dd>One event as several outlets report it. Reworded reports are grouped by meaning, not by exact words. A story takes reports while they keep coming (by default it closes after 8 hours with none, or after 48 hours; both are settings); the same matter reported after that is a new development. Open a story to see the parts of it that many outlets reported on their own (for example a statement or a resignation inside a larger story).</dd>
        <dt>Storyline</dt>
        <dd>A chain of developments about the same matter. Open one to see its timeline, one entry per development.</dd>
        <dt>Independent outlets</dt>
        <dd>Outlets that wrote their own report. Twenty stations running the same wire text count once. Coverage is also counted fairly across countries, so many outlets from one country count less than outlets from several.</dd>
        <dt>Breaking</dt>
        <dd>A mark for a story that 5 or more outlets picked up within 30 minutes (settings). It lasts 3 hours and does not change the ranking.</dd>
        <dt>Developing</dt>
        <dd>A story older than 6 hours that got a new report in the last 3 hours.</dd>
      </dl>

      <h3>Marks that show how far to trust a story</h3>
      <dl>
        <dt>Confirmed</dt><dd>At least 2 independent outlets rated Reliable or better carry it.</dd>
        <dt>Reported</dt><dd>At least 2 independent outlets, fewer than 2 of them rated reliable.</dd>
        <dt>One reliable outlet</dt><dd>Only one outlet so far, and it is rated Reliable or better.</dd>
        <dt>Check before sharing</dt><dd>One outlet that is not rated reliable, or only outlets rated Mixed or lower.</dd>
      </dl>

      <h3>Outlet ratings (Sources page)</h3>
      <p>Each outlet is rated from published evidence, never guessed: <b>Highly reliable</b>, <b>Reliable</b>, <b>Mixed</b>, <b>Low</b>. <b>Unrated</b> means no expert has rated it, not that it is bad. <b>Estimated</b> means the band comes from measured evidence, not an expert rating, and never counts toward Confirmed. <b>Blocked</b> means Wikipedia's editors deprecate the source, or you blocked it. Everything is shown by default; the marks let you decide.</p>

      <h3>Where to look</h3>
      <dl>
        <dt>Place and topic bar</dt><dd>Narrows every list to a place, a topic, or both. Star a combination to keep it as a favourite.</dd>
        <dt>Briefing</dt><dd>A short written summary of the list you are looking at, with citations (needs an AI key in Settings).</dd>
        <dt>Explore</dt><dd>Search and statistics over everything collected: how much was published in a period you choose (7 days by default), by whom, where and how reliable. Its counts cover articles published in that period; Status counts everything stored.</dd>
        <dt>Sources</dt><dd>Every outlet, its rating and the evidence behind it, and how new ones are found.</dd>
        <dt>Storage clean-up</dt><dd>A story that only one outlet reported is most often an advertisement, spam or a post nobody else carried. After a wait (84 hours by default, never less than the feed item age limit plus 12 hours) it is deleted with its articles, unless you saved it or it belongs to a storyline with a bigger story. A story with an advertisement's headline that no second independent outlet wrote is deleted the same way. Stories two or more outlets reported are kept. Settings → Storage; Status shows what was deleted and what is waiting.</dd>
        <dt>Status</dt><dd>What the app is doing now: how many feeds are collected, checked in the last 30 minutes and late, when each job last ran, storage, memory.</dd>
      </dl>
    </div>
  );
}
