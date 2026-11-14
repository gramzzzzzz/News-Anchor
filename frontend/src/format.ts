export const TYPE_LABEL: Record<string, string> = {
  news: "News outlet", public_broadcaster: "Public broadcaster", state_funded_broadcaster: "State-funded broadcaster", official: "Official body",
  company: "Company publication", specialist: "Specialist outlet", factcheck: "Fact-checker", community: "Community link site", social: "Social post",
};
export const TYPE_NOTE: Record<string, string> = {
  news: "An independent news publisher.", public_broadcaster: "Funded by the public (licence fee or public budget) and legally required to be impartial.",
  state_funded_broadcaster: "Funded by a government. Check how it covers that government's own affairs.", official: "A government, regulator, agency or sports body speaking for itself. A primary source, not independent reporting.",
  company: "A company writing about its own products and plans.", specialist: "Covers one field in depth (a sport, technology, law, games).", factcheck: "Checks claims made by others.", community: "A site where people or editors pick stories from many outlets (Hacker News, Techmeme). Its stories are credited to the outlet when that outlet is collected; the rest show the community as source, with the outlet's own link.",
  social: "A post on a social platform (Bluesky, Mastodon, YouTube, Reddit or X). Not established fact; a story needs a non-social outlet to reach Top.",
};

export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}
export function inFuture(iso: string | null | undefined): string {
  if (!iso) return "";
  const m = Math.round((new Date(iso).getTime() - Date.now()) / 60000);
  return m <= 0 ? "now" : m < 60 ? `in ${m} min` : `in ${Math.round(m / 60)} h`;
}
export const GB = 1024 ** 3;
export function bytes(n: number | null | undefined): string {
  if (n == null) return "–";
  if (n >= GB) return `${(n / GB).toFixed(n >= 10 * GB ? 0 : 1)} GB`;
  if (n >= 1024 ** 2) return `${Math.round(n / 1024 ** 2)} MB`;
  return `${Math.round(n / 1024)} KB`;
}
export function mb(n: number | null | undefined): string {
  if (n == null) return "–";
  return n >= 1024 ? `${(n / 1024).toFixed(1)} GB` : `${Math.round(n)} MB`;
}
export const plural = (n: number, one: string, many = one + "s") => `${n} ${n === 1 ? one : many}`;

export const BAND_LABEL: Record<string, string> = { highly_reliable: "Highly reliable", reliable: "Reliable", mixed: "Mixed", low: "Low", unrated: "Unrated", blocked: "Blocked" };
/** A band's label, marked when no expert rating is behind it (estimated from measured evidence). */
export const bandText = (band: string, basis?: string | null) => (BAND_LABEL[band] ?? band) + (basis === "estimated" ? " (estimated)" : "");
export const BAND_NOTE: Record<string, string> = {
  highly_reliable: "Strong, consistent evidence of reliable reporting.", reliable: "Evidence points to reliable reporting.",
  mixed: "Evidence is mixed or weaker; read with care.", low: "Evidence points to unreliable reporting.",
  unrated: "No rating dataset covers this source, so News Anchor does not claim a rating.", blocked: "Hidden: deprecated by Wikipedia's editors, or blocked by you.",
};
export const confLabel = (c: number | null | undefined) => (c == null ? "" : c >= 0.75 ? "high" : c >= 0.5 ? "medium" : "low");

export const VERIFY_LABEL: Record<string, string> = { confirmed: "Confirmed", reported: "Reported", single: "One reliable outlet", caution: "Check before sharing" };
export const STATUS_LABEL: Record<string, string> = { candidate: "Candidate", active: "Active", degraded: "Degraded", dormant: "Dormant", retired: "Retired", no_feed: "No feed found", parked: "Parked", swapped: "Swapped out" };
export const STATUS_NOTE: Record<string, string> = {
  candidate: "Found by discovery. Its feed has not been checked yet, so nothing is collected from it.", active: "Its feed works and is collected.",
  degraded: "Its feeds are failing; they are retried with growing gaps.", dormant: "No feed has worked for a long time; retried rarely.",
  retired: "Gave up after months without a working feed. Kept with its history; you can re-check it.", no_feed: "No usable feed, news sitemap or home page with article links was found. Tried again after 30 days or more.",
  swapped: "Replaced by a more reliable source of the same country at the source limit; every place, subject and language it covered still has other sources. Look for its feed again to bring it back.",
  parked: "Not polled for now: its country already has the most sources allowed (Settings → Sources). Raising the limit brings it back.",
};
export const COUNTRY_NAMES = new Intl.DisplayNames(["en"], { type: "region" });
export const countryName = (code: string) => { try { return COUNTRY_NAMES.of(code) ?? code; } catch { return code; } };
const LANG_NAMES = new Intl.DisplayNames(["en"], { type: "language" });
export const langName = (code: string | null | undefined) => { if (!code) return ""; try { return LANG_NAMES.of(code) ?? code; } catch { return code; } };
/** The headline to show: the English translation when there is one, with the original kept for the tooltip. */
export const headline = (x: { title: string; title_en?: string | null; lang?: string | null }) =>
  x.title_en && x.lang && x.lang !== "en" ? { text: x.title_en, from: langName(x.lang), original: x.title } : { text: x.title, from: "", original: "" };
export const BAND_ORDER = ["highly_reliable", "reliable", "mixed", "low", "unrated", "blocked"];

const DAY = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short" });
const TIME = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });
/** "Thu 30 Oct, 02:18" in the reader's time zone. */
export const when = (iso: string) => { const d = new Date(iso); return `${DAY.format(d)}, ${TIME.format(d)}`; };
export const dayOf = (iso: string) => DAY.format(new Date(iso));
/** The line that says when a story's event began, and, if it belongs to a storyline of several developments, that. */
export function eventLine(s: { first_seen: string; thread?: { developments: number; since: string } }): string {
  const first = `First reported ${when(s.first_seen)}`;
  return s.thread && s.thread.developments > 1 ? `${first} · one of ${s.thread.developments} developments of a storyline since ${dayOf(s.thread.since)}` : first;
}
