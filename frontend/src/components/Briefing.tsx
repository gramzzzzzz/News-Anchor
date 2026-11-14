import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, type AiBriefing, type AiReady } from "../api";
import { ago } from "../format";
import { BriefingBody } from "./AiViews";
import { Link } from "react-router-dom";

interface Geo { x: number; y: number; w: number; h: number; min: boolean }
const KEY = "na.briefing.geo";
const load = (): Geo => {
  try { const g = JSON.parse(localStorage.getItem(KEY) ?? ""); if (g && typeof g.x === "number") return g; } catch { /* default */ }
  return { x: Math.max(16, window.innerWidth - 470), y: 96, w: 440, h: Math.min(620, window.innerHeight - 140), min: false };
};

/** A cited briefing of the current list in a floating panel: drag it by its title, minimise it to a bar, or close it. On a phone it docks to the bottom. */
export function Briefing({ place, topic, scopeName, onClose }: { place: string; topic: string; scopeName: string; onClose: () => void }) {
  const [g, setG] = useState<Geo>(load);
  const ref = useRef<HTMLDivElement>(null);
  const phone = window.innerWidth <= 760;
  const run = useMutation({ mutationFn: (refresh: boolean) => api<AiBriefing>("/ai/briefing", { method: "POST", body: { place, topic, refresh } }) });
  const ready = useQuery({ queryKey: ["aiReady", "scope", place, topic], queryFn: () => api<AiReady<AiBriefing>>(`/ai/ready?place=${encodeURIComponent(place)}&topic=${encodeURIComponent(topic)}`), staleTime: 60_000 });
  const data = run.data ?? (ready.data?.ready ? ready.data.result : undefined);
  const save = (n: Geo) => { setG(n); localStorage.setItem(KEY, JSON.stringify(n)); };
  useEffect(() => { run.reset(); }, [place, topic]);   // eslint-disable-line react-hooks/exhaustive-deps

  const drag = (e: React.PointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest("button, a") || phone) return;
    const el = e.currentTarget; el.setPointerCapture(e.pointerId);
    const sx = e.clientX - g.x, sy = e.clientY - g.y;
    const move = (ev: PointerEvent) => setG((o) => ({ ...o, x: Math.max(0, Math.min(window.innerWidth - 120, ev.clientX - sx)), y: Math.max(0, Math.min(window.innerHeight - 40, ev.clientY - sy)) }));
    const up = () => { el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up); setG((o) => { localStorage.setItem(KEY, JSON.stringify(o)); return o; }); };
    el.addEventListener("pointermove", move); el.addEventListener("pointerup", up);
  };
  const resize = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = e.currentTarget; el.setPointerCapture(e.pointerId);
    const sw = g.w - e.clientX, sh = g.h - e.clientY;
    const move = (ev: PointerEvent) => setG((o) => ({ ...o, w: Math.max(300, Math.min(900, sw + ev.clientX)), h: Math.max(200, Math.min(window.innerHeight - 40, sh + ev.clientY)) }));
    const up = () => { el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up); setG((o) => { localStorage.setItem(KEY, JSON.stringify(o)); return o; }); };
    el.addEventListener("pointermove", move); el.addEventListener("pointerup", up);
  };
  const style: React.CSSProperties = phone ? {} : { left: g.x, top: g.y, width: g.w, height: g.min ? "auto" : g.h };
  const err = run.isError ? (run.error as Error).message : "";
  return (
    <div ref={ref} className={`briefing${g.min ? " min" : ""}${phone ? " phone" : ""}`} style={style} role="dialog" aria-label={`Briefing: ${scopeName}`}>
      <div className="bhead" onPointerDown={drag}>
        <div><b>Briefing</b><span>{scopeName}</span></div>
        <button className="icon-btn" aria-label={g.min ? "Expand briefing" : "Minimise briefing"} onClick={() => save({ ...g, min: !g.min })}>{g.min ? "▢" : "–"}</button>
        <button className="icon-btn" aria-label="Close briefing" onClick={onClose}>×</button>
      </div>
      {!g.min && (
        <div className="bbody">
          {!data && (
            <div className="ai-actions">
              <button className="ghost" disabled={run.isPending} onClick={() => run.mutate(false)}>{run.isPending ? "Writing the briefing…" : "Write a briefing for this list"}</button>
              <span className="muted">Cited, neutral, from the top stories you see. Length is in <Link className="link" to="/settings#ai">Settings → AI</Link>.</span>
            </div>
          )}
          {err && <p className="note">{err} {/No AI provider|switched off/.test(err) && <Link className="link" to="/settings#ai">Open AI settings</Link>}</p>}
          {!run.data && data && <p className="prepared">Prepared {ago(data.meta?.created_at as string | undefined)} in the background.</p>}
          {data && <><BriefingBody d={data} /><div className="ai-actions"><button className="link" disabled={run.isPending} onClick={() => run.mutate(true)}>{run.isPending ? "Writing…" : "Write it again"}</button></div></>}
        </div>
      )}
      {!g.min && !phone && <div className="grip" onPointerDown={resize} aria-hidden="true" />}
    </div>
  );
}
