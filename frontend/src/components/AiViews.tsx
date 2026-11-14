import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type AiArticle, type AiBriefing, type AiReady, type AiStory, type ArticleText } from "../api";
import { ago, BAND_LABEL, plural, TYPE_LABEL } from "../format";
import { CitedList, Cites, Sourcing } from "./Cite";

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

function NeedsKey({ msg }: { msg: string }) {
  const setup = /No AI provider|switched off/.test(msg);
  return <p className="note">{msg} {setup && <Link className="link" to="/settings#ai">Open AI settings</Link>}</p>;
}

/** A neutral digest of one story from every outlet that carries it. */
export function StoryBrief({ storyId }: { storyId: number }) {
  const [full, setFull] = useState(false);
  const run = useMutation({ mutationFn: (v: { refresh: boolean }) => api<AiStory>("/ai/story", { method: "POST", body: { story_id: storyId, full_text: full, refresh: v.refresh } }) });
  // a digest prepared in the background shows at once; no AI call is made to show it
  const ready = useQuery({ queryKey: ["aiReady", "story", storyId], queryFn: () => api<AiReady<AiStory>>(`/ai/ready?story_id=${storyId}`), staleTime: 60_000 });
  const pre = !run.data && ready.data?.ready ? ready.data : null;
  const d = run.data ?? pre?.result;
  return (
    <div className="ai">
      {pre && <p className="prepared">Prepared {ago(pre.result?.meta?.created_at as string | undefined)}{pre.current === false ? ", before the newest reports arrived. Write it again to include them." : "."}</p>}
      <div className="ai-actions">
        <button className="ghost" disabled={run.isPending} onClick={() => run.mutate({ refresh: false })}>{run.isPending ? "Reading the coverage…" : d ? "Show again" : "Brief me on this story"}</button>
        <label className="chk"><input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} /> Read the top articles in full (slower)</label>
        {d && <button className="link" disabled={run.isPending} onClick={() => run.mutate({ refresh: true })}>Write it again</button>}
      </div>
      {run.isError && <NeedsKey msg={errText(run.error)} />}
      {d && (
        <div className="aiout" aria-live="polite">
          <h5>{d.neutral_headline}</h5>
          <CitedList items={d.summary} sources={d.sources} />
          {d.established.length > 0 && <><h6>Reported consistently</h6><CitedList items={d.established} sources={d.sources} /></>}
          {d.disputed.length > 0 && <><h6>Where sources differ</h6>{d.disputed.map((x, i) => <div key={i} className="disp"><b>{x.point}</b><ul className="cited">{x.positions.map((p, j) => <li key={j}>{p.by}: {p.says}<Cites ids={p.cite} sources={d.sources} /></li>)}</ul></div>)}</>}
          {d.official_statements.length > 0 && <><h6>Official statements</h6><ul className="cited">{d.official_statements.map((x, i) => <li key={i}>{x.by}: {x.says}<Cites ids={x.cite} sources={d.sources} /></li>)}</ul></>}
          {d.social_signals.length > 0 && <><h6>Social posts (not established fact)</h6><ul className="cited">{d.social_signals.map((x, i) => <li key={i}>{x.by}: {x.says}<Cites ids={x.cite} sources={d.sources} /></li>)}</ul></>}
          {d.single_source.length > 0 && <><h6>Carried by one source only</h6><CitedList items={d.single_source} sources={d.sources} /></>}
          {d.framing.length > 0 && <><h6>How coverage differs</h6><CitedList items={d.framing} sources={d.sources} /></>}
          {d.context.length > 0 && <><h6>Context</h6><CitedList items={d.context} sources={d.sources} /></>}
          {d.unknowns.length > 0 && <><h6>Not yet known</h6><ul className="plain">{d.unknowns.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
          {d.what_to_watch.length > 0 && <><h6>What to watch</h6><CitedList items={d.what_to_watch} sources={d.sources} /></>}
          {d.best_reads.length > 0 && <p className="muted">Best reads: {d.best_reads.map((id) => <a key={id} className="link" href={d.sources[id]?.url} target="_blank" rel="noopener noreferrer">{d.sources[id]?.name}</a>).reduce<React.ReactNode[]>((a, x, i) => (i ? [...a, ", ", x] : [x]), [])}</p>}
          <p className="aimeta">Confidence in the picture: {d.confidence}. Each number marker opens the article it cites; hover for the outlet and its reliability. The digest reads the most recent reports first.</p>
          <Sourcing meta={d.meta} quality={d.quality} />
        </div>
      )}
    </div>
  );
}

/** One article: its text as the publisher serves it, and a cited reading of it. */
export function ArticleReader({ articleId, onClose }: { articleId: number; onClose: () => void }) {
  const text = useMutation({ mutationFn: (refresh: boolean) => api<ArticleText>(`/articles/${articleId}/text${refresh ? "?refresh=true" : ""}`) });
  const brief = useMutation({ mutationFn: (refresh: boolean) => api<AiArticle>("/ai/article", { method: "POST", body: { article_id: articleId, refresh } }) });
  const [hl, setHl] = useState<string | null>(null);
  const t = text.data;
  const jump = (id: string) => { setHl(id); document.getElementById(`art${articleId}-${id}`)?.scrollIntoView({ block: "center", behavior: "smooth" }); };
  const b = brief.data;
  const paras = b?.paragraphs ?? t?.paragraphs ?? [];
  return (
    <div className="reader">
      <div className="ai-actions">
        <button className="ghost" disabled={text.isPending} onClick={() => text.mutate(false)}>{text.isPending ? "Fetching…" : t ? "Fetched" : "Read the text here"}</button>
        <button className="ghost" disabled={brief.isPending} onClick={() => brief.mutate(false)}>{brief.isPending ? "Reading…" : b ? "Read again" : "AI brief of this article"}</button>
        <button className="link" onClick={onClose}>Close</button>
      </div>
      {text.isError && <p className="note">{errText(text.error)}</p>}
      {brief.isError && <NeedsKey msg={errText(brief.error)} />}
      {t && <p className="aimeta">{t.status === "full" ? "Full text" : t.status === "partial" ? "Only the opening is available" : "Only the publisher's summary is available"} ({t.method}, {plural(t.words, "word")}). {t.note} {t.archive_url && <a className="link" href={t.archive_url} target="_blank" rel="noopener noreferrer">Archive copy</a>} <a className="link" href={t.url} target="_blank" rel="noopener noreferrer">Open the original</a></p>}
      {b && (
        <div className="aiout" aria-live="polite">
          <h5>{b.neutral_headline || b.article?.title}</h5>
          <p className="aimeta">{b.kind.replace(/_/g, " ")}</p>
          <p>{b.summary}<Cites ids={b.summary_cite} onPara={jump} /></p>
          {b.key_points.length > 0 && <><h6>Key points</h6><CitedList items={b.key_points} onPara={jump} /></>}
          {b.claims.length > 0 && <><h6>Claims and who made them</h6><ul className="cited">{b.claims.map((c, i) => <li key={i}>{c.by}: {c.claim} <span className="aimeta">({c.status})</span><Cites ids={c.cite} onPara={jump} /></li>)}</ul></>}
          {b.numbers.length > 0 && <><h6>Numbers</h6><ul className="cited">{b.numbers.map((c, i) => <li key={i}><b>{c.value}</b> {c.meaning}<Cites ids={c.cite} onPara={jump} /></li>)}</ul></>}
          {b.people_and_groups.length > 0 && <><h6>People and groups</h6><ul className="cited">{b.people_and_groups.map((c, i) => <li key={i}><b>{c.name}</b>: {c.role_in_story}<Cites ids={c.cite} onPara={jump} /></li>)}</ul></>}
          {b.context.length > 0 && <><h6>Background in the article</h6><CitedList items={b.context} onPara={jump} /></>}
          {b.open_questions.length > 0 && <><h6>Not yet known</h6><ul className="plain">{b.open_questions.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
          {b.framing_notes && <p className="aimeta">Framing: {b.framing_notes}</p>}
          {b.coverage_note && <p className="aimeta">{b.coverage_note}</p>}
          <Sourcing meta={b.meta} quality={b.quality} />
        </div>
      )}
      {paras.length > 0 && (
        <div className="atext" tabIndex={0} aria-label="Article text">
          {paras.map((p, i) => <p key={i} id={`art${articleId}-p${i + 1}`} className={hl === `p${i + 1}` ? "hl" : ""}><span className="pn">{i + 1}</span>{p}</p>)}
        </div>
      )}
    </div>
  );
}

/** The cited briefing for a scope, rendered. */
export function BriefingBody({ d }: { d: AiBriefing }) {
  return (
    <div className="aiout">
      <h5>{d.title || d.scope.name}</h5>
      {d.big_picture.length > 0 && <><h6>The big picture</h6><CitedList items={d.big_picture} sources={d.sources} /></>}
      {d.stories.map((s) => {
        const meta = d.scope.stories[s.story_id];
        return (
          <section key={s.story_id} className="bstory">
            <h6>{meta?.breaking && <span className="brk-label">Breaking</span>}{s.headline}{meta && <Link className="link small" to={`/?story=${meta.id}&place=${d.scope.place}${d.scope.topic ? `&topic=${d.scope.topic}` : ""}`}> open</Link>}</h6>
            {meta && <p className={`vtag ${meta.verification.level}`}>{meta.verification.label}</p>}
            <CitedList items={s.what_happened} sources={d.sources} />
            {s.why_it_matters.length > 0 && <><p className="aimeta">Why it matters, as the sources put it</p><CitedList items={s.why_it_matters} sources={d.sources} /></>}
            {s.where_sources_differ.length > 0 && <><p className="aimeta">Where sources differ</p><CitedList items={s.where_sources_differ} sources={d.sources} /></>}
          </section>
        );
      })}
      {d.threads.length > 0 && <><h6>Connections</h6><CitedList items={d.threads} sources={d.sources} /></>}
      {d.watch_next.length > 0 && <><h6>What to watch</h6><CitedList items={d.watch_next} sources={d.sources} /></>}
      {d.not_known_yet.length > 0 && <><h6>Not known yet</h6><ul className="plain">{d.not_known_yet.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
      <details className="srclist"><summary>{plural(Object.keys(d.sources).length, "source")} used</summary>
        <ol>{Object.values(d.sources).map((s) => <li key={s.id}><b>{s.id.replace(/^S/, "")}</b> <a className="link" href={s.url} target="_blank" rel="noopener noreferrer">{s.name}</a> <span className="muted">{TYPE_LABEL[s.type] ?? s.type}, {BAND_LABEL[s.band] ?? s.band}: {s.title}</span></li>)}</ol>
      </details>
      <Sourcing meta={d.meta} quality={d.quality} />
    </div>
  );
}

export const whenText = (iso: string | undefined) => (iso ? ago(iso) : "");
