import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type PlaceMenu, type TopicMenu } from "../api";
import { Float } from "./Float";
import { Check, Chevron, ChevronDown } from "./icons";

const LEVEL_HEAD: Record<string, string> = { world: "Continents", region: "Regions", subregion: "Countries", group: "Members", country: "States and cities", state: "Cities" };
const split = (v: string) => v.split(",").filter((x) => x && x !== "world");
const join = (xs: string[], none: string) => (xs.length ? xs.join(",") : none);

/** The "Where" and "What" selectors. One place or topic shows as a breadcrumb (a crumb zooms out); several show as chips.
 *  In the menus, the box beside each entry adds it to the selection or takes it out; the name shows only that entry;
 *  the arrow opens the level below without changing anything. A story is shown when it is about any chosen place and any chosen topic. */
export function ScopeBar({ place, topic, onScope }: { place: string; topic: string; onScope: (p: { place?: string; topic?: string }) => void }) {
  const [open, setOpen] = useState<"where" | "what" | null>(null);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [filter, setFilter] = useState("");
  const places = split(place), topics = split(topic);
  const onePlace = places.length <= 1 ? (places[0] ?? "world") : null, oneTopic = topics.length <= 1 ? (topics[0] ?? "") : null;
  const [browseP, setBrowseP] = useState("world");
  const [browseT, setBrowseT] = useState("");
  // the bar: breadcrumbs of a single place and topic, and the names of chosen ones
  const cur = useQuery({ queryKey: ["placeMenu", onePlace ?? places[0], topic], queryFn: () => api<PlaceMenu>(`/places/menu?place=${encodeURIComponent(onePlace ?? places[0])}&topic=${encodeURIComponent(topic)}`), placeholderData: (p) => p });
  const curT = useQuery({ queryKey: ["topicMenu", oneTopic ?? topics[0], place], queryFn: () => api<TopicMenu>(`/topics/menu?parent=${encodeURIComponent(oneTopic ?? topics[0])}&place=${encodeURIComponent(place)}`), placeholderData: (p) => p });
  const idx = useQuery({ queryKey: ["index"], queryFn: () => api<{ places: { code: string; name: string }[]; topics: { code: string; name: string }[] }>("/index"), staleTime: 300_000, enabled: places.length > 1 || topics.length > 1 });
  const nameOf = (code: string, kind: "places" | "topics") => idx.data?.[kind].find((x) => x.code === code)?.name ?? code;
  // the menus, browsed separately from the selection
  const pm = useQuery({ queryKey: ["placeMenu", browseP, topic], queryFn: () => api<PlaceMenu>(`/places/menu?place=${encodeURIComponent(browseP)}&topic=${encodeURIComponent(topic)}`), enabled: open === "where", placeholderData: (p) => p });
  const tm = useQuery({ queryKey: ["topicMenu", browseT, place], queryFn: () => api<TopicMenu>(`/topics/menu?parent=${encodeURIComponent(browseT)}&place=${encodeURIComponent(place)}`), enabled: open === "what", placeholderData: (p) => p });

  const toggle = (which: "where" | "what", el: HTMLElement) => {
    setFilter(""); setAnchor(el);
    if (which === "where") setBrowseP(onePlace ?? "world"); else setBrowseT(oneTopic ?? "");
    setOpen((o) => (o === which ? null : which));
  };
  const close = () => setOpen(null);
  const f = filter.trim().toLowerCase();
  const flipPlace = (c: string) => onScope({ place: join(places.includes(c) ? places.filter((x) => x !== c) : [...places, c], "world") });
  const flipTopic = (c: string) => onScope({ topic: join(topics.includes(c) ? topics.filter((x) => x !== c) : [...topics, c], "") });
  const drillable = (level: string) => level !== "city";

  const PlaceRow = ({ c }: { c: { code: string; name: string; level: string; count: number } }) => (
    <div className="mrow">
      <button className={`box${places.includes(c.code) ? " on" : ""}`} role="checkbox" aria-checked={places.includes(c.code)} aria-label={`Include ${c.name}`} onClick={() => flipPlace(c.code)}>{places.includes(c.code) && <Check />}</button>
      <button className="name" title={`Show only ${c.name}`} onClick={() => { onScope({ place: c.code }); close(); }}>
        <span>{c.name} <span className="muted">{c.level === "city" ? "city" : c.level === "state" ? "state" : ""}</span></span><span className="n">{c.count || ""}</span>
      </button>
      {drillable(c.level) ? <button className="in" aria-label={`Open ${c.name}`} onClick={() => { setFilter(""); setBrowseP(c.code); }}><Chevron /></button> : <span className="in" />}
    </div>
  );

  return (
    <>
      <div className="crumbs" aria-label="Where">
        <span className="crumb-kind">Where</span>
        {onePlace !== null ? (
          <ol>{(cur.data?.breadcrumb ?? [{ code: "world", name: "Global", level: "world" }]).map((c) => (
            <li key={c.code}><button className="crumb" onClick={() => onScope({ place: c.code })}>{c.name}</button></li>))}</ol>
        ) : (
          <div className="chips sel">{places.map((p) => <span key={p} className="chip on">{nameOf(p, "places")}<button aria-label={`Remove ${nameOf(p, "places")}`} onClick={() => flipPlace(p)}>×</button></span>)}
            <button className="link" onClick={() => onScope({ place: "world" })}>Clear</button></div>
        )}
        <button className="narrow" aria-expanded={open === "where"} onClick={(e) => toggle("where", e.currentTarget)}>{places.length > 1 ? "Add or remove" : cur.data?.children.length ? "Narrow" : "Change"}<ChevronDown /></button>
      </div>
      <div className="crumbs" aria-label="What">
        <span className="crumb-kind">What</span>
        {oneTopic !== null ? (
          <ol>
            <li><button className="crumb" onClick={() => onScope({ topic: "" })}>All topics</button></li>
            {(curT.data?.breadcrumb ?? []).map((c) => <li key={c.code}><button className="crumb" onClick={() => onScope({ topic: c.code })}>{c.name}</button></li>)}
          </ol>
        ) : (
          <div className="chips sel">{topics.map((t) => <span key={t} className="chip on">{nameOf(t, "topics")}<button aria-label={`Remove ${nameOf(t, "topics")}`} onClick={() => flipTopic(t)}>×</button></span>)}
            <button className="link" onClick={() => onScope({ topic: "" })}>Clear</button></div>
        )}
        <button className="narrow" aria-expanded={open === "what"} onClick={(e) => toggle("what", e.currentTarget)}>{topics.length > 1 ? "Add or remove" : (curT.data?.children.length ?? 0) > 0 ? "Narrow" : "Change"}<ChevronDown /></button>
      </div>

      {open === "where" && pm.data && (
        <Float anchor={anchor} onClose={close} className="menu">
          {browseP !== "world" && <button className="back" onClick={() => setBrowseP(pm.data.breadcrumb.length > 1 ? pm.data.breadcrumb[pm.data.breadcrumb.length - 2].code : "world")}>‹ {pm.data.breadcrumb.length > 1 ? pm.data.breadcrumb[pm.data.breadcrumb.length - 2].name : "Global"}</button>}
          {browseP === "world" && pm.data.home.length > 0 && (<><h6>Shortcuts</h6>
            <button className="plain" onClick={() => { onScope({ place: "world" }); close(); }}><span>Global</span></button>
            {pm.data.home.map((h) => <PlaceRow key={h.code} c={{ ...h, count: 0 }} />)}</>)}
          {browseP !== "world" && <PlaceRow c={{ ...pm.data.place, count: 0 }} />}
          {pm.data.children.length > 0 && <h6>{LEVEL_HEAD[pm.data.place.level] ?? "Cities"}{pm.data.place.level === "group" ? ` of ${pm.data.place.name}` : pm.data.place.level === "world" ? "" : ` in ${pm.data.place.name}`}</h6>}
          {pm.data.children.length > 12 && <div style={{ padding: "0 8px 6px" }}><input className="input" style={{ height: 32 }} placeholder="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} autoFocus /></div>}
          {pm.data.children.filter((c) => !f || c.name.toLowerCase().includes(f)).map((c) => <PlaceRow key={c.code} c={c} />)}
          {(pm.data.groups?.length ?? 0) > 0 && !f && <h6 title="Political and economic groupings. Each takes in its member countries.">Groups</h6>}
          {pm.data.groups?.filter((c) => !f || c.name.toLowerCase().includes(f)).map((c) => <PlaceRow key={c.code} c={c} />)}
          <div className="foot">Tick boxes to combine places. Press <kbd>/</kbd> to jump anywhere.</div>
        </Float>
      )}
      {open === "what" && tm.data && (
        <Float anchor={anchor} onClose={close} className="menu">
          {browseT && <button className="back" onClick={() => setBrowseT(tm.data.breadcrumb.length > 1 ? tm.data.breadcrumb[tm.data.breadcrumb.length - 2].code : "")}>‹ {tm.data.breadcrumb.length > 1 ? tm.data.breadcrumb[tm.data.breadcrumb.length - 2].name : "All topics"}</button>}
          <h6>{tm.data.breadcrumb.length ? `Inside ${tm.data.breadcrumb[tm.data.breadcrumb.length - 1].name}` : "Categories"}</h6>
          {tm.data.children.length > 12 && <div style={{ padding: "0 8px 6px" }}><input className="input" style={{ height: 32 }} placeholder="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} autoFocus /></div>}
          {tm.data.children.filter((c) => !f || c.name.toLowerCase().includes(f)).map((c) => (
            <div className="mrow" key={c.code}>
              <button className={`box${topics.includes(c.code) ? " on" : ""}`} role="checkbox" aria-checked={topics.includes(c.code)} aria-label={`Include ${c.name}`} onClick={() => flipTopic(c.code)}>{topics.includes(c.code) && <Check />}</button>
              <button className="name" title={`Show only ${c.name}`} onClick={() => { onScope({ topic: c.code }); close(); }}><span>{c.name}</span><span className="n">{c.count || ""}</span></button>
              {c.has_children ? <button className="in" aria-label={`Open ${c.name}`} onClick={() => { setFilter(""); setBrowseT(c.code); }}><Chevron /></button> : <span className="in" />}
            </div>
          ))}
          {tm.data.hidden_count > 0 && <div className="foot">{tm.data.hidden_count} more here are hidden. <Link className="link" to="/settings#topics" onClick={close}>Choose topics</Link></div>}
          <div className="foot">Tick boxes to combine topics.</div>
        </Float>
      )}
    </>
  );
}
