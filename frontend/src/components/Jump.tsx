import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type IndexResp } from "../api";

export interface Pick { place?: string; topic?: string }
interface Item { label: string; path: string; kind: string; pick: Pick }

const words = (s: string) => s.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
function matches(q: string[], text: string): boolean {
  const w = words(text);
  return q.every((t) => w.some((x) => x.startsWith(t)));
}

/** Jump to any place, topic or place + topic ("chennai politics"). Opens with "/". */
export function Jump({ onPick, onClose }: { onPick: (p: Pick) => void; onClose: () => void }) {
  const idx = useQuery({ queryKey: ["index"], queryFn: () => api<IndexResp>("/index"), staleTime: 300_000 });
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);

  const items = useMemo<Item[]>(() => {
    const d = idx.data;
    if (!d) return [];
    const qs = words(q);
    if (!qs.length) return [];
    const places = d.places.filter((p) => p.code !== "world" || "global world".startsWith(qs[0])).filter((p) => matches(qs, p.name));
    const topics = d.topics.filter((t) => matches(qs, t.name) || matches(qs, t.path));
    const out: Item[] = [];
    if (qs.length > 1) {                                   // "chennai politics": try every split into place + topic
      for (let i = 1; i < qs.length && out.length < 4; i++) {
        for (const [a, b] of [[qs.slice(0, i), qs.slice(i)], [qs.slice(i), qs.slice(0, i)]]) {
          const p = d.places.find((x) => matches(a, x.name)), t = d.topics.find((x) => matches(b, x.name));
          if (p && t && !out.some((o) => o.pick.place === p.code && o.pick.topic === t.code)) out.push({ label: `${t.name} in ${p.name}`, path: `${t.path} · ${p.path}`, kind: "Place and topic", pick: { place: p.code, topic: t.code } });
        }
      }
    }
    places.sort((a, b) => a.name.length - b.name.length).slice(0, 12).forEach((p) => out.push({ label: p.name, path: p.path, kind: p.level === "world" ? "Place" : p.level[0].toUpperCase() + p.level.slice(1), pick: { place: p.code } }));
    topics.sort((a, b) => a.path.length - b.path.length).slice(0, 14).forEach((t) => out.push({ label: t.name, path: t.enabled ? t.path : `${t.path} · not enabled`, kind: "Topic", pick: { topic: t.code } }));
    return out;
  }, [q, idx.data]);

  useEffect(() => setSel(0), [q]);
  const key = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(items.length - 1, s + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
    else if (e.key === "Enter" && items[sel]) { onPick(items[sel].pick); onClose(); }
    else if (e.key === "Escape") onClose();
  };
  return (
    <div className="scrim" onPointerDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="jbox" role="dialog" aria-label="Jump to a place or topic">
        <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={key} placeholder="Jump to a place or topic, e.g. “chennai politics” or “f1”" aria-label="Jump to" />
        <ul>
          {items.map((it, i) => (
            <li key={it.kind + it.path + it.label + i}>
              <button className={i === sel ? "on" : ""} onMouseEnter={() => setSel(i)} onClick={() => { onPick(it.pick); onClose(); }}>
                <span>{it.label} <span className="path">{it.path !== it.label ? it.path : ""}</span></span>
                <span className="path">{it.kind}</span>
              </button>
            </li>
          ))}
          {q && !items.length && idx.data && <li className="hint">Nothing matches “{q}”.</li>}
        </ul>
        <div className="hint">↑ ↓ to move, Enter to open, Esc to close. A topic you have not enabled still opens; enable it in Settings to follow its specialist sources.</div>
      </div>
    </div>
  );
}
