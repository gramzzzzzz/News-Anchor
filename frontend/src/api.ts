import { QueryClient, useQuery } from "@tanstack/react-query";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T = unknown>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const method = init?.method ?? "GET";
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["X-Requested-With"] = "news-anchor";
  if (init?.body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch("/api" + path, { method, headers, credentials: "same-origin", body: init?.body !== undefined ? JSON.stringify(init.body) : undefined });
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const t = await r.text();
      try { const j = JSON.parse(t); msg = typeof j.detail === "string" ? j.detail : Array.isArray(j.detail) ? j.detail.map((d: { msg: string }) => d.msg).join("; ") : t; } catch { msg = t || msg; }
    } catch { /* keep status text */ }
    if (r.status === 401) window.dispatchEvent(new Event("na-unauthorized"));
    throw new ApiError(r.status, msg);
  }
  return (await r.json()) as T;
}

export const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 20_000, retry: 1, refetchOnWindowFocus: false } } });

// ---------- types ----------
export interface Strip { k: string; n: string; t: string; b: string; e?: boolean }
export interface Tagged { code: string; name: string }
export interface Verification { level: "confirmed" | "reported" | "single" | "caution"; label: string; outlets: number; reliable: number }
export interface Story {
  id: number; title: string; lead: string; first_seen: string; last_update: string; outlets: number; indep: number; outlets_period?: number | null; parts?: number; articles: number;
  bucket?: "fresh" | "ongoing" | null; qualified_at?: string | null; group?: Story[];
  title_en: string | null; lang: string | null; bands: Record<string, number>;
  thread: { id: number; developments: number; since: string; minor?: number };
  breaking: boolean; saved: boolean; single_source: boolean; url: string; developing: boolean;
  lead_source: { key: string; name: string; type: string; band: string; basis?: string | null }; first_source?: { key: string; name: string } | null; strip: Strip[]; topic: Tagged | null; place: Tagged | null; verification: Verification;
  hit?: string | null; matched?: string;
}
export interface ArticleItem { id: number; url: string; title: string; title_en?: string | null; lang?: string | null; lead: string; published_at: string; source: { key: string; name: string; type: string; country: string; domain: string; provisional: boolean; band: string; score: number | null; basis?: string | null } }
export interface Development { id: number; title: string; title_en: string | null; lang: string | null; first_seen: string; last_update: string; indep: number; breaking: boolean; current: boolean }
export interface Reprint { url: string; published_at: string; source: { key: string; name: string; type: string; country: string; band: string; basis?: string | null } }
export interface SubEvent { label: string; headline: string; url: string | null; source: string; outlets: number; reports: number; first: string; last: string }
export interface StoryDetail extends Story { subevents?: SubEvent[]; timeline: Development[]; topics: (Tagged & { n: number })[]; places: (Tagged & { n: number })[]; articles_list: ArticleItem[]; other_coverage: ArticleItem[]; hidden_articles: number; reprints?: Reprint[] }
export interface StorylineInfo {
  id: number; score: number; developments: number; developments_in_range: number; began: string; latest: string; outlets_in_range: number; countries_in_range: number;
  latest_title: string | null; latest_id: number; activity: number[];
  timeline: { id: number; title: string; title_en: string | null; lang: string | null; first_seen: string; indep: number; in_range: boolean }[]; minor?: number;
}
export interface StorylineItem extends Story { storyline: StorylineInfo }
export interface StorylineList { scope: { place: string; topic: string; hours: number }; ranges: number[]; more: boolean; limit: number; storylines: StorylineItem[] }
export interface StoryList { scope: { place: string; topic: string; tab: string; view: string }; more: boolean; limit: number; stories: Story[] }
export interface PlaceMenu { place: { code: string; name: string; level: string }; breadcrumb: { code: string; name: string; level: string }[]; children: { code: string; name: string; level: string; count: number }[]; groups: { code: string; name: string; level: string; count: number }[]; home: { code: string; name: string; level: string }[] }
export interface TopicMenu { breadcrumb: Tagged[]; children: (Tagged & { count: number; has_children: boolean })[]; hidden_count: number }
export interface Setting { key: string; group: string; label: string; help: string; kind: string; multiline?: boolean; unit: string; min: number | null; max: number | null; choices: string[]; nullable: boolean; default: unknown; value: unknown; is_default: boolean }
export interface SettingsResp { settings: Setting[]; groups: string[]; profiles: string[] }
export interface TopicNode { code: string; name: string; parent: string | null; depth: number; enabled: boolean; default_enabled: boolean; visible: boolean; children: number; sources: string[]; specialist: boolean }
export interface Favourite { place: string; topic: string; label: string }
export interface Status {
  counts: { stories: number; sources: number; feeds: number; feeds_ok: number; feeds_failing: number; last_poll: string | null; last_story: string | null };
  running: boolean; paused: boolean; next_poll: string | null; last_job: { id: number; state: string; progress: Record<string, number>; finished_at: string | null } | null;
}
export interface StorageStatus {
  disk_total: number; disk_free: number; cap: number; cap_confirmed: boolean; proposed_cap_gb: number; min_free: number; days_stored: number; days_with_data: number;
  per_day_bytes: number; fixed_bytes: number; retention_days: number; paused: boolean; kept_days_note: string | null; oldest_day: string | null;
  usage: { database: number; wal: number; wal_known: boolean; images_allowance: number; files: number; total: number };
}
export interface Resources { server_total_mb: number | null; server_available_mb: number | null; app_mb: number; db_mb: number | null; app_peak_24h_mb: number; budget_mb: number; leave_available_mb: number }
export interface SourceRow { key: string; name: string; domain: string; country: string; type: string; scope: string; provisional: boolean; curated: boolean; trust_note: string; trust_basis?: string | null; status: string; status_note?: string; language?: string; origin?: string; stories_per_week?: number | null; score: number | null; confidence: number | null; band: string; visible: boolean; owner: string | null; override_band: string | null; trust_updated_at: string | null; feeds: number; feeds_active: number; last_ok_at: string | null }
export interface Signal { signal: string; label: string; help: string; expert: boolean; value: number; factor: number; weight: number; detail: string; evidence_url: string | null; evidence_date: string | null }
export interface SourceDetail { source: SourceRow; feeds: FeedRow[]; signals: Signal[]; history: { at: string; change: string; reason: string }[] }
export interface TrustMethod { learned?: boolean; signals: { key: string; label: string; help: string; expert: boolean; weight: number; learned?: { weight: number; reliability: number | null; n: number; prior: number } | null }[]; thresholds: { highly: number; reliable: number; mixed: number }; prior: string; rules: string[]; floor: string; show_unrated: boolean; running: boolean;
  estimate?: { at: string; estimated: number; eval?: { sources: number; spearman: number | null; band_agreement: number | null } } | null; last_run: { at: string; sources: number; bands: Record<string, number>; seconds: number; notes: string[] } | null }
export interface FeedRow { id: number; url: string; topics: string[]; places: string[]; state: string; last_status: number | null; last_error: string | null; last_ok_at: string | null; last_new_at: string | null; fail_count: number; items_total: number; poll_interval_s: number }
export interface IndexResp { places: { code: string; name: string; level: string; path: string }[]; topics: { code: string; name: string; path: string; enabled: boolean }[] }

// ---------- hooks ----------
export function useSettings() {
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsResp>("/settings"), staleTime: 60_000 });
  const get = <T = unknown,>(key: string, fallback?: T): T => (q.data?.settings.find((s) => s.key === key)?.value ?? fallback) as T;
  return { ...q, get };
}
export const useStatus = () => useQuery({ queryKey: ["status"], queryFn: () => api<Status>("/status"), refetchInterval: 15_000 });

// ---------- phase 2b to 4 ----------
export interface SourcesPage { sources: SourceRow[]; total: number; facets?: { status: Record<string, number>; band: Record<string, number>; type: Record<string, number>; language: Record<string, number>; country: Record<string, number> } }
export interface DiscoveryStatus { running: boolean; counts: Record<string, number>; feeds: Record<string, number>; pending_feed_search: number; last_directory: { at: string; added?: { mediacloud: number; wikidata: number }; notes?: string[] } | null; last_batch: { at: string; searched?: number; found?: number; none?: number } | null; mediacloud_token: boolean; last_error: string | null; web_running?: boolean; directory_busy?: boolean; batch_busy?: boolean; web?: { seen: number; promoted: number; waiting: number }; last_web: { at: string; queries?: number; domains_seen?: number; promoted?: number; notes?: string[]; error?: string } | null }
export interface ExploreFilters { q: string; days: number; place: string; topic: string; types: string[]; bands: string[]; countries: string[]; sources: string[] }
export interface ExploreResp {
  totals: { articles: number; stories: number; sources: number; countries: number };
  timeline: { t: string; n: number; reliable: number; weak: number; unrated: number }[];
  sources: { key: string; name: string; band: string; type: string; country: string; n: number }[];
  countries: { code: string; n: number; sources: number }[]; bands: { band: string; n: number }[]; types: { type: string; n: number }[];
  topics: { code: string; name: string; n: number }[]; places: { code: string; name: string; n: number }[]; terms: { term: string; n: number }[];
  stories_top: { id: number; articles: number; outlets: number; title: string; latest: string }[];
  articles: { id: number; title: string; url: string; published_at: string; story_id: number | null; source_key: string; source_name: string; band: string; type: string; country: string }[];
  articles_more: boolean; share_reliable: number | null; filters: { bucket: string; retention_note: string };
}
export interface CompareResp { bucket: string; series: { q: string; total: number; points: { t: string; n: number }[] }[] }
export interface SearchResp { query: string; mode: string; count: number; stories: Story[] }
export interface MediaCloudCtx { state?: string; error?: string; at?: string; count?: { relevant?: number; total?: number } | number; timeline?: { date: string; count: number; total_count?: number; ratio?: number }[]; sources?: { source?: string; name?: string; count?: number }[]; quota?: { hits: number; limit: number } }
export interface ArticleText { status: "full" | "partial" | "summary"; method: string; paragraphs: string[]; words: number; note: string; archive_url: string | null; url: string; title: string; article: { id: number; title: string; url: string; source_name: string; band: string; story_id: number | null } }
export interface AiProvider { id: string; label: string; note: string; key_url: string; needs_key: boolean; editable_url: boolean; has_key: boolean; enabled: boolean; model: string; base_url: string; daily_limit: number; calls_today: number; blocked_until: string | null; last_error: string; resolved_model: string | null }
export interface AiStatus { providers: AiProvider[]; ready: boolean; enabled: boolean; daily_cap: number; usage: { calls_today: number; today: { provider: string; calls: number; ok: number; tin: number; tout: number }[]; week: { task: string; calls: number }[]; recent: { at: string; provider: string; model: string; task: string; tokens_in: number; tokens_out: number; ms: number; ok: boolean; error: string }[] } }
export interface Cited { text: string; cite: string[]; unverified?: boolean }
export interface AiMeta { cached: boolean; provider: string; model: string; created_at: string; basis?: string; note?: string; text_method?: string; archive_url?: string | null; length?: string }
export interface AiSource { id: string; name: string; type: string; band: string; url: string; title: string; country: string; full_text?: boolean; story_id?: number }
export interface AiQuality { dropped_uncited: number; invalid_ids_removed: number; numbers_not_found: number }
export interface AiArticle { kind: string; neutral_headline: string; summary: string; summary_cite: string[]; key_points: Cited[]; context: Cited[]; people_and_groups: { name: string; role_in_story: string; cite: string[] }[]; numbers: { value: string; meaning: string; cite: string[] }[]; claims: { claim: string; by: string; status: string; cite: string[] }[]; open_questions: string[]; framing_notes: string; coverage_note: string; paragraphs: string[]; article?: { id: number; title: string; url: string; source: string }; quality: AiQuality; meta: AiMeta }
export interface AiStory { neutral_headline: string; summary: Cited[]; established: Cited[]; disputed: { point: string; positions: { by: string; says: string; cite: string[] }[] }[]; single_source: Cited[]; official_statements: { by: string; says: string; cite: string[] }[]; social_signals: { by: string; says: string; cite: string[] }[]; framing: Cited[]; context: Cited[]; unknowns: string[]; what_to_watch: Cited[]; confidence: string; best_reads: string[]; sources: Record<string, AiSource>; quality: AiQuality; meta: AiMeta }
export interface AiBriefing { title: string; big_picture: Cited[]; stories: { story_id: string; headline: string; what_happened: Cited[]; why_it_matters: Cited[]; where_sources_differ: Cited[] }[]; threads: Cited[]; watch_next: Cited[]; not_known_yet: string[]; sources: Record<string, AiSource>; scope: { place: string; topic: string; name: string; stories: Record<string, { id: number; title: string; breaking: boolean; verification: Verification }> }; quality: AiQuality; meta: AiMeta }

// ---------- status page ----------
type Dt = string | null;
export interface Stats {
  now: string; app_started: string;
  news: { articles: number; articles_24h: number; articles_7d: number; stories: number; stories_24h: number; stories_multi: number; breaking_now: number; last_article_seen: Dt; last_article_published: Dt; last_story_first_seen: Dt; oldest_day: Dt; sources_reporting_24h: number; per_day: { day: string; n: number }[] };
  refresh: { interval_minutes: number; last_poll: Dt; next_poll: Dt; due_now: number; polled_last_hour: number; paused: boolean; last_job: { state: string; finished_at: Dt } | null };
  sources: { total: number; curated: number; by_status: Record<string, number>; by_band: Record<string, number>; by_type: Record<string, number>; by_origin: Record<string, number>; by_language: Record<string, number>; active_by_country: Record<string, number>; discovered_per_day: { day: string; n: number }[]; top_by_volume: { key: string; name: string; band: string; type: string; country: string; n: number }[] };
  feeds: { total: number; collected: number; not_collected: number; late: number; never_checked: number; polled_last_30m: number; ok: number; failing: number; due_now: number; polled_last_hour: number; by_state: Record<string, number>; by_kind: Record<string, number> };
  discovery: { last_directory: { at: string; added?: { mediacloud: number; wikidata: number } } | null; last_web: { at: string; queries?: number; promoted?: number; domains_seen?: number } | null; last_batch: { at: string; searched?: number; found?: number } | null; lifecycle_last: Dt; pending_feed_search: number; web: { seen: number; promoted: number; waiting: number }; running: { directory: boolean; batch: boolean; web: boolean }; mediacloud_token: boolean; ceiling: number; languages: string[];
    all_languages: boolean; directory_resume: number; busy: { stream: boolean; editions: boolean; wikidata: boolean };
    last_stream: ({ at: string; files?: number; publishers?: number; languages?: number; promoted?: number; error?: string }) | null;
    last_editions: ({ at: string; pages?: number; cards?: number; articles_new?: number; publishers?: number; promoted?: number; sweep_position?: string; notes?: string[]; error?: string }) | null;
    last_wikidata: ({ at: string; countries?: number; added?: number; countries_left?: number; error?: string }) | null;
    last_rebalance: ({ at: string; parked?: number; unparked?: number; upgrade?: Upgrade }) | null;
    coverage: { countries: number; with_sources: number; without: number; under_target: number; at_cap: number; target: number; cap: number; unknown_country: number; top: [string, number][] } };
  translate: { enabled: boolean; installed: Record<string, number>; disk_mb: number; budget_mb: number; last: { at?: string; translated?: number } | null; last_error: string | null } | null;
  prefetch: { enabled: boolean; running: boolean; last: { at: string; scopes: number; texts: number; briefings: number; digests: number; readings: number; reused: number; stopped: string } | null; ai_ready: boolean; ai_room_today: number } | null;
  social: SocialStatus | null;
  trust: { last_run: { at: string; sources: number; seconds: number; bands: Record<string, number> } | null; waiting_to_be_rated: number };
  categories: { top: { code: string; name: string; stories: number; subtopics_active: number; subtopics: number }[]; busiest_subtopics: { code: string; name: string; stories: number }[]; enabled_but_quiet: string[]; enabled_but_quiet_count: number; topics_total: number };
  geography: { regions: { code: string; name: string; stories: number }[]; top_countries: { code: string; name: string; stories: number; sources: number }[]; countries_total: number; countries_with_active_source: number; countries_with_stories_7d: number; countries_without_source: string[]; countries_without_source_count: number };
  ai: { calls_today: number; ready: boolean; providers_ready: number };
  search: { embedded: number; stories: number; backend: string | null; model_loaded: boolean };
  cleanup: { enabled: boolean; wait_hours: number; waiting: number; stories: number; articles: number; ads: number; last_at: Dt; last: { stories: number; articles: number; ads: number } | null; since: Dt; per_day: Record<string, { stories: number; articles: number; ads: number }> };
  storage: { database_bytes: number; retention_days: number; oldest_day: Dt };
  jobs: { id: number; kind: string; state: string; created_at: string; finished_at: Dt; result: string | null }[];
}

export interface AiReady<T> { ready: boolean; current?: boolean | null; result?: T }
export interface SocialStatus {
  enabled: boolean; platforms: Record<string, { feeds: number; working: number; posts: number; errors: { name: string; error: string }[] }>;
  x: { connected: boolean; username: string; month: string; used: number; budget: number; left: number; cost_usd: number; client_id: boolean; bearer: boolean };
  youtube_key: boolean; bluesky_login: boolean; reddit_login: boolean; reddit_rss_ends: string; official_accounts: number; official_at: string | null;
}

export type Breadth = { sources: number; countries: number; cells: number; cells_thin: number; country_evenness_bits: number; topic_evenness_bits: number;
  reliable_share_sources: number; reliable_share_articles: number; low_or_mixed_sources: number; unrated_sources: number };
export type Upgrade = { off?: boolean; restored?: number; error?: string; seed?: { added: number; note?: string }; swap?: { swapped: number; over_limit: number; newcomers_waiting: number; protected_for_breadth: number; before: Breadth; after: Breadth } };
