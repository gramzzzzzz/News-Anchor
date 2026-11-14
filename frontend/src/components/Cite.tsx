import type { AiSource, Cited } from "../api";
import { BAND_LABEL, TYPE_LABEL } from "../format";

/** Citation markers. Source ids (S1…) link to the publisher's own page; paragraph ids (p1…) jump to that paragraph in the reader. */
export function Cites({ ids, sources, onPara }: { ids: string[]; sources?: Record<string, AiSource>; onPara?: (id: string) => void }) {
  return (
    <>
      {ids.map((id) => {
        const s = sources?.[id];
        if (s) {
          return <a key={id} className={`cite ${s.band}`} href={s.url} target="_blank" rel="noopener noreferrer" title={`${s.name} · ${TYPE_LABEL[s.type] ?? s.type} · ${BAND_LABEL[s.band] ?? s.band}\n${s.title}`}>{id.replace(/^S/, "")}</a>;
        }
        return <button key={id} className="cite para" onClick={() => onPara?.(id)} title={`Paragraph ${id.replace(/^p/, "")} of the article`}>{id.replace(/^p/, "")}</button>;
      })}
    </>
  );
}

export function CitedLine({ item, sources, onPara }: { item: Cited; sources?: Record<string, AiSource>; onPara?: (id: string) => void }) {
  return (
    <li>
      {item.text}<Cites ids={item.cite} sources={sources} onPara={onPara} />
      {item.unverified && <span className="unver" title="A number in this statement was not found in the text it cites. Check the source.">number not found in source</span>}
    </li>
  );
}

export function CitedList({ items, sources, onPara }: { items: Cited[]; sources?: Record<string, AiSource>; onPara?: (id: string) => void }) {
  if (!items?.length) return null;
  return <ul className="cited">{items.map((x, i) => <CitedLine key={i} item={x} sources={sources} onPara={onPara} />)}</ul>;
}

export function Sourcing({ meta, quality }: { meta: { cached: boolean; provider: string; model: string; basis?: string; note?: string }; quality: { dropped_uncited: number; invalid_ids_removed: number; numbers_not_found: number } }) {
  const fixed = quality.dropped_uncited + quality.invalid_ids_removed + quality.numbers_not_found;
  return (
    <p className="aimeta">
      Written by {meta.provider} ({meta.model}){meta.cached ? ", saved earlier" : ""}. {meta.basis ? `Based on ${meta.basis}. ` : ""}
      {fixed === 0 ? "Every statement was checked against its cited text." : `Checked against the sources: ${quality.dropped_uncited} uncited statements removed, ${quality.invalid_ids_removed} invalid citations removed, ${quality.numbers_not_found} numbers marked as not found.`}
      {meta.note ? ` ${meta.note}` : ""}
    </p>
  );
}
