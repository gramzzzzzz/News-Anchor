"""Settings registry. Every setting has a default, a range and a group, and can be reset on its own,
by group, or all together. Only settings that something reads exist here (no dead controls)."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from app.ai import prompts as P

LANGUAGES = ("en", "hi", "ta", "te", "ml", "kn", "bn", "mr", "gu", "pa", "ur", "ne", "si", "es", "fr", "de", "pt", "it", "nl", "ru", "uk", "pl", "cs", "ro", "hu", "el",
             "sv", "no", "da", "fi", "tr", "ar", "fa", "he", "zh", "ja", "ko", "id", "ms", "th", "vi", "sw", "af", "xx")
SOURCE_TYPES = ["news", "public_broadcaster", "state_funded_broadcaster", "official", "company", "specialist", "factcheck", "community", "social"]


@dataclass(frozen=True)
class Def:
    key: str
    group: str
    label: str
    help: str
    kind: str                         # int | float | bool | enum | multi | place | text
    default: Any
    min: float | None = None
    max: float | None = None
    choices: tuple = ()
    unit: str = ""
    nullable: bool = False
    multiline: bool = False


def _defs() -> list[Def]:
    D = Def
    return [
        D("home.country", "Home", "Home country", "Country code used as the default scope.", "place", "IN"),
        D("home.state", "Home", "Home state", "State or province, shown as a shortcut in the place menu.", "place", "IN-TN"),
        D("home.city", "Home", "Home city", "City, shown as a shortcut in the place menu.", "place", "IN-TN-chennai"),
        D("home.landing", "Home", "Open on", "The place shown when the app opens. Global unless you choose one.", "place", "world"),

        D("feed.top_n", "Feed", "Stories per list", "How many stories a Top list shows before 'Show more'.", "int", 10, 1, 100),
        D("feed.window_hours", "Feed", "Window for menus and the brief", "The place and topic menus count, and the brief reads, the stories of this many hours. Top and Trending have their own windows (Fresh, Ongoing and Aggregated cover the last 24 hours; Trending reads the last 2).", "int", 24, 1, 168, unit="hours"),
        D("feed.development_hours", "Feed", "A story takes reports for up to", "A story that is still being reported keeps taking reports of its event for up to this many hours after its first; later ones are a new development in the same thread. A longer limit lets a subject covered for days become one very large story with a headline from its last day. A pause (below) ends a story sooner.", "int", 48, 6, 336, unit="hours"),
        D("feed.story_gap_hours", "Feed", "...unless its reports pause for", "A story that has had no report for this long stops taking more: a report after the pause is a new development (so a daily headline like 'markets fall' is a new story each day, while a story reported hour by hour stays one).", "int", 8, 1, 48, unit="hours"),
        D("feed.meaning_check", "Feed", "Check topic tags against the headline's meaning", "A story keeps a sub-topic tag only if other signals agree with the keywords: the 25 most similar stories of the last 3 days (do some share the tag?), a model of the topic's meaning trained daily, and the section in the article's address (/sport/cricket/). On 312 hand-checked tags, with the keyword rules, about 68% of wrong tags go and 15% of right ones; wrong tags among those kept fall from about 26% to 12%. Stories without an English headline are not checked.", "bool", True),
        D("feed.meaning_min", "Feed", "...reject a tag when its score is below", "Higher rejects more tags, right ones too. Measured: 0.5 removes 55% of wrong tags and 11% of right ones; 0.65 removes 68% and 15%; 0.75 removes 76% and 22%. Topics with fewer than 300 stories in 3 days get a cut 0.15 lower, so rare subjects are not lost.", "float", 0.65, 0.3, 0.95),
        D("feed.languages_shown", "Feed", "Languages shown in lists", "Only stories in these languages appear in Top and Trending. 'all' shows every language, untranslated.", "multi", ["en"], choices=("all",) + tuple(LANGUAGES)),
        D("feed.trending_hours", "Feed", "Trending window", "Trending ranks stories by how far the outlets that joined within this many hours are above the story's usual number for such a window (read from the day before).", "int", 2, 1, 24, unit="hours"),
        D("feed.trending_min_outlets", "Feed", "Trending: outlets needed in the window", "A story needs this many independent outlets inside the Trending window to be listed.", "int", 3, 2, 20),
        D("feed.trending_baseline_hours", "Feed", "Trending: the usual rate is read from", "Trending ranks how far a story's coverage in the window is above its own usual rate, read from this many hours before the window (a story that began inside the window has no usual rate, so everything counts as surge).", "int", 24, 6, 72, unit="hours"),
        D("feed.fresh_hours", "Feed", "Fresh and Ongoing: the day", "Top and Trending have three views. Fresh: stories whose event was first reported within this many hours (a new story, or a new development of a storyline that had been quiet). Ongoing: stories that began earlier and were still reported within these hours. Aggregated: both, fused by rank so that neither hides the other. The coverage that counts in Top is the reports of these hours.", "int", 24, 6, 72, unit="hours"),
        D("feed.decay_hours", "Feed", "Decay: time scale (T)", "In Top, an outlet that joined a story counts for (1 + age / T) to the power -g, so recent outlets count for more. With T = 6 hours and g = 2 an outlet that joined 6 hours ago counts for a quarter, one from 12 hours ago for a ninth. A larger T forgets more slowly.", "float", 6.0, 1, 48, unit="hours"),
        D("feed.decay_gravity", "Feed", "Decay: how fast it fades (g)", "The exponent g of the rule above. 0 turns decay off (every report of the day counts the same); Hacker News uses 1.8. A replay of two days of lists found that a higher value lists new big stories sooner and changes the lists more.", "float", 2.0, 0, 4),
        D("feed.active_hours", "Feed", "Ongoing: how lately outlets must have joined", "A story that began earlier counts in Top by how many outlets joined it in this many hours (next setting). Fresh stories are not affected.", "int", 6, 1, 24, unit="hours"),
        D("feed.active_scale", "Feed", "Ongoing: outlets for half weight", "The score of a story that began earlier is multiplied by n / (n + this number), n being the outlets that joined it in the hours above, and its share of the Aggregated list shrinks the same way. A story nobody has joined lately sinks smoothly; one still gaining outlets keeps its place. With 12: 0 recent outlets count for nothing, 12 for a half, 60 for 83%. A replay of three days of lists (50 big stories): 12 found as many big stories within a day as 6 (68%; 62% with no scaling), a few more within 3 and 6 hours, and held places for over a day 3% of the time (12% with no scaling, 5% with 6); it changes the lists a little more (churn 33%, 31% with 6) and gives Ongoing a third of Aggregated instead of 38%. 0 turns it off.", "float", 12.0, 0, 100),
        D("feed.min_outlets", "Feed", "Outlets needed for Top", "A story needs this many independent outlets to enter Top.", "int", 2, 1, 10),
        D("feed.single_source", "Feed", "Fill thin lists with single-source stories", "When fewer stories qualify than the list size, show single-source ones, marked.", "bool", True),
        D("feed.show_lead", "Feed", "Show lead text under headlines", "The summary of the report whose headline is shown.", "bool", True),
        D("feed.best_headline", "Feed", "Choose each story's headline from its reports", "On (default): the headline most representative of what the story's outlets are reporting now (the words most of its reports use, recent reports counting more, plain headlines preferred over questions and live blogs), always a real headline credited to its outlet. Storylines get one the same way from all their reports. Off: the first report's headline.", "bool", True),
        D("feed.density", "Feed", "List density", "Compact hides lead text and tightens rows.", "enum", "comfortable", choices=("comfortable", "compact")),

        D("storylines.hours", "Storylines", "Storylines tab: period", "The Storylines tab ranks storylines by the coverage their developments drew in this period (you can change it on the tab too).", "enum", "24", choices=("6", "12", "24", "48", "72", "168", "336", "720")),
        D("storylines.min_developments", "Storylines", "Only storylines with at least this many developments", "1 lists every storyline, including events reported on a single day; 2 or more lists only those that went on over several days.", "int", 1, 1, 10),
        D("storylines.cohesion", "Storylines", "Storyline cohesion", "How alike the developments of one storyline must stay: the mean similarity of its developments to the storyline's centre, from 0 to 1. A development or storyline joins another only if the joined storyline would stay at or above this, however long it gets, and a storyline found below it is split by meaning and names. Higher gives tighter, shorter storylines; lower lets storylines wander. Live storylines average about 0.76; the default 0.65 stops mixed ones.", "float", 0.65, 0.4, 0.9),

        D("latest.hours", "Latest", "Latest: how far back", "The Latest tab lists stories that earned their place within this many hours, newest first. A story earns its place when enough different outlets have reported it (the settings below), not when its first report arrives, so one outlet's post, an advertisement or a spam site cannot appear.", "int", 24, 1, 72, unit="hours"),
        D("latest.min_outlets", "Latest", "Latest: outlets needed", "The story must have been reported by this many different outlets. Outlets printing the same wire text count once, and a report counts only if it came at a different time from the ones before it (next setting). The story appears at the moment its last needed outlet reports.", "int", 3, 2, 20),
        D("latest.spread_minutes", "Latest", "Latest: reports this far apart count separately", "Reports published within the same stretch of this many minutes count as one, whatever the number of outlets: posts placed at the same moment are how spam networks and scheduled syndication look. Real breaking news that many outlets report within seconds still gets in, through the next setting.", "int", 3, 1, 60, unit="minutes"),
        D("latest.sure_outlets", "Latest", "Latest: enough outlets to list at once", "A story reported by this many different outlets (wire text counted once, whatever the timing) is listed without the timing test. Different outlets writing their own text of one event, all at once, is how a big breaking story looks.", "int", 10, 3, 100),
        D("breaking.enabled", "Breaking news", "Mark breaking news", "A story picked up by many outlets within minutes is marked Breaking. The mark does not change the ranking: size and freshness do (five outlets within half an hour is a low bar, and small stories pinned above the biggest news were worse than the rare late breaking story).", "bool", True),
        D("breaking.outlets", "Breaking news", "Outlets needed", "Distinct outlets within the window below.", "int", 5, 2, 50),
        D("breaking.minutes", "Breaking news", "Within", "Time window after the story first appears.", "int", 30, 5, 240, unit="minutes"),
        D("breaking.mark_hours", "Breaking news", "Mark lasts", "How long the Breaking mark stays.", "int", 3, 1, 24, unit="hours"),

        D("sources.floor", "Sources", "Hide sources rated below", "Everything is shown by default, each story marked with how reliable its sources are, so you decide. Choose a band to hide anything below it from lists and counts (an optional view filter, not a collection rule).", "enum", "all", choices=("all", "highly_reliable", "reliable", "mixed", "low")),
        D("sources.show_unrated", "Sources", "Keep unrated sources when hiding", "Only matters when a band is chosen above: sources without enough evidence for a band (official bodies, specialist sites, new additions) stay visible if on.", "bool", True),
        D("sources.unrated_weight", "Sources", "Weight of unrated sources in ranking", "Stands in for a score of this value when ranking stories (1 = as good as the best).", "float", 0.6, 0.1, 1, ),
        D("sources.gamma", "Sources", "How strongly reliability counts in ranking", "A source counts as score to this power. Higher favours reliable sources more.", "float", 1.5, 0.5, 4),
        D("sources.show_other_coverage", "Sources", "Show 'Other coverage' in story view", "Only matters when a band is chosen above: list articles from hidden sources in a separate group, clearly marked.", "bool", False),
        D("sources.all_languages", "Sources", "Collect news in every language", "On: sources in any language are found and collected (shown in lists only if their language is chosen in Lists). Off: only the languages below.", "bool", False),
        D("sources.languages", "Sources", "Languages to collect when not all", "Only sources in these languages are collected. Discovery still lists other languages so you can turn them on later (xx = not recognised).", "multi", ["en"], choices=LANGUAGES),
        D("sources.sitemap_fallback", "Sources", "Without a feed, use the site's Google News sitemap", "The file publishers make for Google News (announced in the site's robots.txt, whatever the robots.txt setting, or at a usual address): the last two days of articles with headline and time.", "bool", True),
        D("sources.homepage_fallback", "Sources", "With neither, read the home page", "The home page's article links: each new link on a later visit becomes an article (headline from the link, time when first seen). Checked at most every 30 minutes; the site's robots.txt rules are obeyed when \"Obey robots.txt rules\" is on.", "bool", True),
        D("sources.homepage_min_links", "Sources", "...if it shows at least this many article links", "Fewer means a site that is not really a news site, or one built with scripts.", "int", 8, 3, 50),
        D("discovery.enabled", "Sources", "Find new sources automatically", "Grows the registry from Media Cloud and Wikidata, finds each new site's feed, and retires sources that stop working.", "bool", True),
        D("discovery.refresh_days", "Sources", "Look for new sources every", "How often the directories are read again.", "int", 7, 1, 60, unit="days"),
        D("discovery.use_mediacloud", "Sources", "Use Media Cloud's directory", "Needs a free API token (below).", "bool", True),
        D("discovery.use_wikidata", "Sources", "Use Wikidata", "Outlets that list a website and a web feed.", "bool", True),
        D("discovery.use_web", "Sources", "Search the web for more publishers", "Searches news for the subjects and places the collection covers thinnest, and adds publishers that keep turning up. Stories in the results from publishers you already collect are stored too.", "bool", True),
        D("discovery.web_engine", "Sources", "...using", "Google News search (default) or GDELT's news search (often rate limited).", "enum", "google_news", choices=("google_news", "gdelt")),
        D("discovery.use_gdelt", "Sources", "Read GDELT's world news stream", "Every 15 minutes GDELT lists the articles its crawlers found in world news, in English and 65 other languages, with where the events happen. Publishers that keep appearing become candidates. Small files (about 50 KB each).", "bool", True),
        D("discovery.gdelt_minutes", "Sources", "Read the GDELT stream every", "Each read takes the quarter hours not read yet.", "int", 60, 15, 1440, unit="minutes"),
        D("discovery.gdelt_files", "Sources", "Quarter hours per GDELT read", "At most this many 15-minute files (English and translated each) per read.", "int", 4, 1, 96),
        D("discovery.gdelt_min_files", "Sources", "Add a GDELT publisher after it appears in this many quarter hours", "Separate 15-minute files, so a one-off mention is not enough.", "int", 3, 1, 50),
        D("discovery.use_editions", "Sources", "Read Google News' country editions", "Top stories and the 8 sections (World, the country, Business, Technology, Entertainment, Sports, Science, Health) of every edition in the languages you collect (22 English editions, 198 pages), plus local pages for cities. Publishers on several pages become candidates.", "bool", True),
        D("discovery.gnews_mode", "Sources", "...read as", "RSS (default): small feeds, local pages and links for every story; Google's robots.txt disallows them for programs, used here on your decision (personal use, light load). Web pages: only the pages Google's robots.txt allows (while \"Obey robots.txt rules\" is on), no local pages, about 600 KB a page.", "enum", "rss", choices=("rss", "web")),
        D("discovery.gnews_local", "Sources", "...including local pages for cities", "A third of each run reads Google News' local page of a city (100,000 people or more), those in the countries and places with the fewest sources and stories first; each city again after 14 days.", "bool", True),
        D("discovery.edition_articles", "Sources", "...and collect their stories", "Each story on those pages and in web searches from a publisher already in your list is stored as that publisher's article, with the publisher's own link (never Google's). Catches stories missing from the publisher's feed. Headline only.", "bool", True),
        D("discovery.gnews_decode_max", "Sources", "...opening at most this many Google links per run", "In RSS, each story's link is Google's; finding the publisher's link costs two requests (about 120 KB), one a second. Only stories you do not have yet are opened.", "int", 150, 0, 1000),
        D("discovery.edition_pages", "Sources", "Edition pages per run", "Pages read per run, 3 seconds apart (a third of them local pages when those are on). RSS pages are about 40 KB.", "int", 45, 5, 300),
        D("discovery.edition_minutes", "Sources", "Read edition pages every", "Time between runs.", "int", 60, 15, 1440, unit="minutes"),
        D("discovery.wikidata_countries", "Sources", "Countries asked of Wikidata per hour", "Each country's best-known outlets (most Wikipedia articles first), the countries with the fewest sources first; each country again after 30 days.", "int", 10, 1, 60),
        D("discovery.country_target", "Sources", "Sources wanted per country", "Countries below this are served first at every step: new publishers, feed checks and Wikidata questions.", "int", 30, 1, 1000),
        D("discovery.country_cap", "Sources", "Most sources per country", "A country with this many working or waiting sources takes no new ones, so discovery's effort goes to the rest of the world. Sources already collected are kept unless parking is on below.", "int", 200, 10, 10000),
        D("discovery.rebalance", "Sources", "Park sources beyond the per-country limit", "Off (default): the limit only stops new sources from being added for a full country, and nothing you collect is dropped. On: every 6 hours the weakest discovered sources beyond the limit (unrated, fewest articles) stop being polled, kept with their history; turning this off or raising the limit brings them back.", "bool", False),
        D("discovery.web_queries", "Sources", "Searches per web run", "Each run asks this many questions, a few seconds apart. Gentle on the services; more finds sources faster.", "int", 30, 4, 200),
        D("discovery.web_hours", "Sources", "Search the web every", "Time between web runs.", "int", 6, 1, 168, unit="hours"),
        D("discovery.web_min_queries", "Sources", "Add a publisher after it shows up in this many searches", "A single appearance is often chance; several different searches is a real news publisher.", "int", 2, 1, 10),
        D("discovery.web_min_hits", "Sources", "...and at least this many results", "Total results naming the publisher across searches.", "int", 3, 1, 50),
        D("discovery.include_local", "Sources", "Include state and local collections", "Media Cloud also groups regional outlets by state and city. Turn off for national outlets only.", "bool", True),
        D("discovery.per_country_cap", "Sources", "At most this many sources per collection", "Busiest first. Keeps the registry, and the work each refresh does, bounded.", "int", 80, 10, 1000),
        D("discovery.local_cap", "Sources", "At most this many per state or city collection", "Regional collections are many; a few busy outlets from each is enough.", "int", 25, 5, 500),
        D("discovery.max_total", "Sources", "Most discovered sources to keep", "A ceiling on how many sources discovery may add in all (social accounts not counted), so the registry and the work of each refresh stay bounded on a shared server. Each source adds about 40 articles a day (measured), about 6 MB over 90 days, so 10,000 sources need about 60 GB at the default retention.", "int", 10000, 100, 50000),
        D("discovery.upgrade", "Sources", "Raise reliability, keeping breadth", "Adds publishers that expert ratings call reliable (Lin et al. 2023 at 0.65 or more; Wikipedia's list, generally reliable) and, at the limit, swaps out a weaker source of the same country for each, only if every place, subject and language that source covers keeps enough other sources. Off: everything swapped out comes back.", "bool", True),
        D("discovery.upgrade_min_score", "Sources", "...newcomers rated at least", "Lin et al. 2023 combined score (0 to 1).", "float", 0.65, 0.5, 0.95),
        D("discovery.upgrade_batch", "Sources", "...newcomers added per run", "Every 6 hours.", "int", 40, 0, 500),
        D("discovery.upgrade_pool", "Sources", "...room above the limit for newcomers waiting", "Rated newcomers may exceed the limit by this many while their feed is checked and rated.", "int", 300, 0, 3000),
        D("discovery.upgrade_unrated", "Sources", "...also swap out unrated sources", "Off (default): only sources rated low or mixed are swapped out. Unrated sources are most of the local and small outlets, and much of the breadth.", "bool", False),
        D("discovery.diversity_floor", "Sources", "...keep at least this many sources per place, subject and language", "A source is swapped out only if each (country, topic), (country, sub-topic), state or city, (country, language) and (country, source type) it covered keeps at least this many other sources.", "int", 3, 1, 20),
        D("discovery.min_stories_per_week", "Sources", "Ignore sources publishing less than", "Media Cloud's stories per week for the source.", "float", 5.0, 0, 1000, unit="a week"),
        D("discovery.feed_search_batch", "Sources", "Feed searches per batch", "Each batch (about once a minute while sources are waiting) looks for the feed of this many new sources. Lower is gentler on their sites.", "int", 60, 5, 300),
        D("mediacloud.token", "Sources", "Media Cloud API token", "Free at search.mediacloud.org (Account). Stored on this server only and never shown again.", "secret", "", nullable=True),
        D("mediacloud.weekly_budget", "Sources", "Media Cloud searches allowed per week", "The Explore page can add Media Cloud's global counts; each costs hits against your 10,000 a week. Source discovery costs none.", "int", 1500, 0, 10000),
        D("sources.exclude_types", "Sources", "Hide source types", "Source types left out of lists and counts.", "multi", [], choices=tuple(SOURCE_TYPES)),

        D("trust.refresh_days", "Trust", "Re-check trust every", "The trust crawler re-reads rating datasets, Wikipedia, ownership records and each site's transparency pages.", "int", 7, 1, 60, unit="days"),
        D("trust.calibrate", "Trust", "Learn the weights from the evidence", "On (default): each kind of evidence counts as much as it agrees with the others across all sources (a one-factor model, as in truth discovery and in Lin et al.'s own aggregate), shrunk toward the weights below when it was measured on few sources. Relearned daily; the Sources page shows the learned weights. Off: the weights below are used as set, and the measured evidence (link graph, pickups, citations, sister outlets) counts only for sources without an expert rating.", "bool", True),
        D("trust.w.lin2023", "Trust", "Weight: expert rating aggregate", "Lin et al. 2023, combining six expert rating sets for about 11,500 news domains. Each site's score counts in proportion to the public rating sets that really rated it (most values in the file are imputed). Used as set when learning is off; otherwise the starting point for learning.", "float", 3.0, 0, 10),
        D("trust.w.wikipedia_rsp", "Trust", "Weight: Wikipedia perennial sources", "Community consensus on a source's reliability, with discussions behind it.", "float", 3.0, 0, 10),
        D("trust.w.factcheck_labels", "Trust", "Weight: fact-checkers' labels", "Media Bias/Fact Check, NewsGuard and Wikipedia labels collected by Burdisso et al. 2024 (5,332 domains). An expert signal, used only where the expert rating aggregate has no score.", "float", 2.0, 0, 10),
        D("trust.estimate", "Trust", "Estimate a band for sources without an expert rating", "A model trained daily on the sources experts rated predicts, from measured evidence (the published hyperlink-graph estimate, rated outlets that cite the site, sister outlets, pickup by rated outlets, transparency pages, domain age), how likely experts would be to call a source Reliable. Estimated bands are marked, never Highly reliable or Low, and never count towards Confirmed. Off: such sources stay Unrated.", "bool", True),
        D("trust.estimate_precision", "Trust", "...estimated Reliable only where the model is right this often", "On the expert-rated sources (cross-validated), at least this share of those above the cut must really be rated Reliable or better. Higher gives fewer, surer estimates.", "float", 0.85, 0.6, 0.99),
        D("trust.estimate_precision_mixed", "Trust", "...estimated Mixed only where the model is right this often", "The same for Mixed: at least this share of the rated sources below the cut must be rated Mixed or Low.", "float", 0.8, 0.5, 0.99),
        D("trust.w.transparency", "Trust", "Weight: transparency pages", "Whether the site publishes about, contact, ethics, corrections, masthead and ownership pages.", "float", 1.0, 0, 10),
        D("trust.w.domain_age", "Trust", "Weight: domain age", "Years since the domain was registered (RDAP).", "float", 0.5, 0, 10),
        D("trust.w.ownership", "Trust", "Weight: ownership on record", "An owner is recorded in Wikidata.", "float", 0.5, 0, 10),
        D("trust.corroboration_age_hours", "Trust", "Judge a story as picked up after", "Only stories first published at least this long ago count towards 'picked up by other outlets'.", "int", 24, 6, 168, unit="hours"),
        D("trust.hysteresis", "Trust", "Margin before a band changes", "A source keeps its band unless its score is this far past a threshold. Stops weak signals from flipping bands.", "float", 0.02, 0, 0.1),
        D("trust.band_highly", "Trust", "Highly reliable from", "Score at or above this (and medium confidence) is Highly reliable.", "float", 0.80, 0.5, 1),
        D("trust.band_reliable", "Trust", "Reliable from", "Score at or above this is Reliable.", "float", 0.65, 0.3, 1),
        D("trust.band_mixed", "Trust", "Mixed from", "Score at or above this is Mixed; below it is Low.", "float", 0.45, 0.1, 1),

        D("search.embeddings", "Search", "Meaning vectors for stories", "Used to merge reworded reports of one story and to search by meaning. 'auto' uses the small MiniLM model if it can be loaded and a built-in hashing method otherwise.", "enum", "auto", choices=("auto", "model", "hashed", "off")),
        D("search.merge_threshold", "Search", "Merge stories when similarity is at least", "Higher merges fewer stories. Headlines about the same event usually score above 0.8.", "float", 0.82, 0.5, 0.99),
        D("translate.enabled", "Search", "Translate headlines into English on this server", "Off by default: the free local models (OPUS/Argos) are weak and can change the meaning of a sensitive headline. On: headlines are translated on this server for clustering and shown marked 'Translated'.", "bool", False),
        D("translate.disk_budget_mb", "Search", "Disk for translation models", "Least recently used models are removed to stay inside this.", "int", 3000, 300, 50000, unit="MB"),
        D("translate.min_daily", "Search", "Fetch a language's model once it has", "Articles in two days. Below this, a language stays untranslated (a model is a large download).", "int", 10, 1, 10000, unit="articles"),
        D("translate.per_cycle", "Search", "Most headlines translated per refresh", "Bounds the work (and CPU) of each refresh.", "int", 1500, 50, 20000),
        D("extract.enabled", "Search", "Fetch article text on demand", "When you open an article in the reading pane, fetch its text from the publisher (their robots.txt rules obeyed when \"Obey robots.txt rules\" is on). Nothing is fetched in bulk.", "bool", True),
        D("extract.archive_fallback", "Search", "Use a public archive copy when the page is blocked", "Only the Internet Archive's own public copy, never a login or paywall workaround.", "bool", True),
        D("extract.prefetch", "Search", "Fetch the text of top stories ahead", "After each refresh, the lead article of each top story in the scopes you use is fetched, so the reading pane opens at once. Only the publisher's page is read.", "bool", True),

        D("ai.enabled", "AI", "Use AI readings and briefings", "Off removes the AI buttons. Without any provider key nothing is called.", "bool", True),
        D("ai.cache_hours", "AI", "Reuse a finished reading for", "Saves your free-tier quota.", "int", 24, 1, 720, unit="hours"),
        D("ai.daily_call_cap", "AI", "Most AI calls a day", "A safety net so a loop or an open tab cannot use up free quotas.", "int", 300, 1, 5000),
        D("ai.temperature", "AI", "Creativity", "Low keeps the wording close to the sources.", "float", 0.2, 0, 1),
        D("ai.max_tokens", "AI", "Longest answer", "Tokens per call.", "int", 2500, 300, 8000),
        D("ai.briefing_length", "AI", "Scope briefing length", "Short, standard or deep.", "enum", "standard", choices=("short", "standard", "deep")),
        D("ai.briefing_stories", "AI", "Stories in a scope briefing", "How many of the top stories a briefing covers.", "int", 8, 3, 20),
        D("ai.auto_briefing", "AI", "Prepare briefings in the background", "Refreshes the briefing for your favourites after each news refresh, using your free quota. Off by default.", "bool", False),
        D("ai.prefetch", "AI", "Prepare readings ahead", "After news arrives, the briefing and the top stories' digests of Global, your favourites and the scopes you opened recently are made in the background, so they are ready when you open them.", "bool", True),
        D("ai.prefetch_stories", "AI", "Stories prepared per scope", "Top stories whose digest is made ahead.", "int", 10, 0, 50),
        D("ai.prefetch_scopes", "AI", "Scopes prepared", "Global first, then favourites, then the places and topics you opened in the last three days.", "int", 6, 1, 30),
        D("ai.prefetch_share", "AI", "Share of the daily AI calls for preparing ahead", "Preparing stops at this share of the daily cap, so what you open yourself always has room.", "int", 60, 0, 100, unit="%"),
        D("ai.prefetch_minutes", "AI", "Prepare at most every", "Readings for unchanged material are reused, not made again.", "int", 30, 5, 1440, unit="minutes"),
        D("ai.prompt_shared", "AI", "Prompt: rules for every task", "Prepended to every request. The reply format is added by the app and cannot be edited.", "text", P.SHARED, multiline=True),
        D("ai.prompt_article", "AI", "Prompt: article reading", "Task text for a single article.", "text", P.ARTICLE, multiline=True),
        D("ai.prompt_story", "AI", "Prompt: story digest", "Task text for one story reported by several sources.", "text", P.STORY, multiline=True),
        D("ai.prompt_scope", "AI", "Prompt: scope briefing", "Task text for a briefing of a place and topic.", "text", P.SCOPE, multiline=True),

        D("social.enabled", "Social", "Collect social posts", "Posts from the accounts, timelines and searches below, read at the news refresh interval. Always marked Social; a story needs a non-social outlet to reach Top or Trending.", "bool", True),
        D("social.official_accounts", "Social", "Follow news outlets' official accounts", "The YouTube, Bluesky and Mastodon accounts that Wikidata records for your curated and reliably rated sources (free to read). Looked up monthly.", "bool", True),
        D("social.official_max", "Social", "At most this many official accounts", "Each is one more feed to check.", "int", 150, 0, 2000),
        D("social.bluesky.handle", "Social", "Bluesky: your handle", "For your timeline and post search, e.g. name.bsky.social. Public accounts below need no login.", "text", ""),
        D("social.bluesky.app_password", "Social", "Bluesky: app password", "Create one on bsky.app (Settings, Privacy and security, App passwords). Never your main password. Stored on this server only.", "secret", "", nullable=True),
        D("social.bluesky.timeline", "Social", "Bluesky: read your timeline", "The posts of the accounts you follow, when logged in.", "bool", True),
        D("social.bluesky.accounts", "Social", "Bluesky: accounts to follow", "One handle per line, e.g. reuters.com. Public; no login needed.", "text", "", multiline=True),
        D("social.bluesky.searches", "Social", "Bluesky: searches", "One search per line (newest posts first). Needs the login above.", "text", "", multiline=True),
        D("social.mastodon.instance", "Social", "Mastodon: your instance", "For your home timeline, e.g. mastodon.social.", "text", ""),
        D("social.mastodon.token", "Social", "Mastodon: access token", "From your instance: Preferences, Development, New application, scope read:statuses. Stored on this server only.", "secret", "", nullable=True),
        D("social.mastodon.accounts", "Social", "Mastodon: accounts to follow", "One per line, e.g. @lemonde@mastodon.social. Read from their public feeds.", "text", "", multiline=True),
        D("social.mastodon.tags", "Social", "Mastodon: hashtags", "One per line, as instance/tag (e.g. mastodon.social/news) or just a tag (your instance is used).", "text", "", multiline=True),
        D("social.youtube.api_key", "Social", "YouTube: Data API key", "Free from console.cloud.google.com (enable YouTube Data API v3, create an API key). YouTube asks automated programs not to read its channel feeds, so channels are read through the API: one unit of the free 10,000 a day per check. Without a key, YouTube is not read. Stored on this server only.", "secret", "", nullable=True),
        D("social.youtube.channels", "Social", "YouTube: channels", "One per line: a channel address, @handle or channel ID. Needs the API key above.", "text", "", multiline=True),
        D("social.reddit.client_id", "Social", "Reddit: app client ID", "Your own 'script' app at reddit.com/prefs/apps. Reddit stopped approving new API apps on 31 Oct 2026, so this works only with an app you already have.", "text", ""),
        D("social.reddit.client_secret", "Social", "Reddit: app secret", "Stored on this server only.", "secret", "", nullable=True),
        D("social.reddit.username", "Social", "Reddit: your username", "The account that owns the app above.", "text", ""),
        D("social.reddit.password", "Social", "Reddit: your password", "Used only to get an access token from Reddit. Stored on this server only.", "secret", "", nullable=True),
        D("social.reddit.home", "Social", "Reddit: read your front page", "The subreddits you are subscribed to, when logged in.", "bool", True),
        D("social.reddit.subreddits", "Social", "Reddit: subreddits", "One per line, e.g. worldnews. With a login they are read through the API; without one, from Reddit's RSS feeds (Reddit is ending these on 13 Nov 2026).", "text", "", multiline=True),
        D("social.x.client_id", "Social", "X: your app's client ID", "From your app at developer.x.com (User authentication, OAuth 2.0, type Web App). Set its callback to this server's /api/social/x/callback address, then use Log in with X below.", "text", ""),
        D("social.x.client_secret", "Social", "X: your app's client secret", "Only for a confidential app (Web App type). Stored on this server only.", "secret", "", nullable=True),
        D("social.x.bearer", "Social", "X: app bearer token (instead of logging in)", "Reads the accounts listed below, without your timeline. Stored on this server only.", "secret", "", nullable=True),
        D("social.x.home", "Social", "X: read your home timeline", "The posts of the accounts you follow, when logged in.", "bool", True),
        D("social.x.accounts", "Social", "X: accounts to read", "One username per line. Each check reads only posts newer than the last.", "text", "", multiline=True),
        D("social.x.monthly_reads", "Social", "X: posts to read per month", "X bills about $0.005 per post read to your developer account. Reading stops for the month at this many (3,000 is about $15).", "int", 3000, 0, 1000000, unit="posts"),
        D("social.x.per_poll", "Social", "X: at most this many posts per check", "Per timeline or account, per check.", "int", 30, 5, 100, unit="posts"),

        D("refresh.news_minutes", "Refresh", "News refresh interval", "Base time between checks of each feed. A feed with something new is checked sooner (down to a sixth of this, never under 5 minutes); one with nothing new is checked 1.5 times later each time, up to 2 hours. Once a day every feed's interval is set again from its last week of activity, so no feed stays slowed down.", "int", 30, 5, 240, unit="minutes"),
        D("refresh.merge_gap_s", "Refresh", "Merge reports at least every", "Reports of one event are merged into one story by a pass that runs next to the feed checks. It starts when new reports are waiting and this long has passed since the last pass. A shorter gap shows merged stories sooner and uses more of the database.", "int", 120, 30, 900, unit="seconds"),
        D("refresh.concurrency", "Refresh", "Parallel fetches", "Feeds fetched at the same time. Lower uses less memory and network; the app also lowers it by itself under memory pressure.", "int", 8, 1, 32),
        D("fetch.robots", "Refresh", "Obey robots.txt rules", "On (default): before reading a page or feed, the site's robots.txt is fetched (kept in memory for 24 hours) and its rules are obeyed, matched under the name NewsAnchor; Google News web pages are checked against Google's. Off (for debugging): no rules are matched or obeyed and nothing is kept (what was kept is discarded at once). robots.txt is still fetched, fresh and unstored, only when looking for a site's news sitemap, to read its \"Sitemap:\" lines. Every request carries the one browser user agent either way.", "bool", True),
        D("fetch.impersonate", "Refresh", "Also fetch with curl-impersonate", "Off (default): only ordinary requests. On: when an ordinary request returns nothing usable (a refusal such as HTTP 403 or 429, a challenge, a server error, a reset, a TLS failure or a timeout), it is repeated once with curl-impersonate (github.com/lwthiker/curl-impersonate), which sends the TLS handshake, HTTP/2 settings and headers of a real browser; in the reading pane it is also tried when a page loaded but held no article. It applies to feeds, sitemaps, home pages, discovery and the reader. It does not solve JavaScript challenges, log in, or get around a block on the server's address, and a host where it failed is not tried again for an hour. robots.txt (if obeyed) is checked first and applies to this step too. The request then carries the impersonated browser's own User-Agent (Chrome 116 on Windows for the default profile), which must match its fingerprint, not the one set in NA_USER_AGENT.", "bool", False),
        D("fetch.impersonate_profile", "Refresh", "...as this browser", "Which browser curl-impersonate imitates. Chrome 116 (default) is the newest Chrome in the release; the Firefox, Edge and Safari profiles are there for sites that treat Chrome differently. Used only while \"Also fetch with curl-impersonate\" is on.", "enum", "chrome116", choices=("chrome116", "chrome110", "chrome107", "chrome104", "chrome101", "chrome100", "chrome99", "edge101", "safari15_5", "ff117")),
        D("fetch.flaresolverr", "Refresh", "Also solve JavaScript challenges with FlareSolverr", "Off (default): no browser is used. On: the last step, after the ordinary request and curl-impersonate (if on) returned nothing usable. A feed, home page, sitemap or article that is refused with HTTP 202, 403, 429 or 503, or that loaded as a challenge page (\"Just a moment...\", a check of your browser), is opened in a real headless Chrome by FlareSolverr (github.com/FlareSolverr/FlareSolverr), which waits for the challenge to finish and returns the page. It applies to feeds and the reading pane only, not to discovery or trust checks. It does not solve CAPTCHAs that need a person, log in, or get past a block on the server's address. A browser is slow (5 to 30 seconds) and uses a few hundred MB, so it is limited: the number of pages at once and per hour are set below; a feed check that finds it busy goes to the end of its queue; a site where it failed is not tried again for 6 hours; the cookies it earns are used for that site's next requests for up to 30 minutes, which then need no browser. robots.txt (if obeyed) is checked first. Needs the FlareSolverr container: docker compose --profile flaresolverr up -d flaresolverr.", "bool", False),
        D("fetch.flaresolverr_url", "Refresh", "...FlareSolverr address", "Where FlareSolverr listens. The bundled container is http://flaresolverr:8191/v1; point it elsewhere to use one running on another machine. Used only while \"Also solve JavaScript challenges\" is on.", "text", "http://flaresolverr:8191/v1"),
        D("fetch.flaresolverr_parallel", "Refresh", "...pages open at once", "Each page open in the browser holds a few hundred MB. 1 (default) is gentlest; feeds that find all slots busy are checked again later, so a low number slows nothing else.", "int", 1, 1, 4),
        D("fetch.flaresolverr_per_hour", "Refresh", "...at most this many solves an hour", "Over all sites. When it is reached, challenged pages are left as they are until the hour has passed. Cookies kept from an earlier solve do not count.", "int", 60, 1, 1000, unit="solves"),
        D("fetch.flaresolverr_timeout_s", "Refresh", "...give up on a challenge after", "How long the browser may spend on one page.", "int", 60, 15, 180, unit="seconds"),
        D("refresh.host_delay_s", "Refresh", "Pause between requests to one site", "Politeness delay per website.", "float", 2.0, 0.5, 30, unit="seconds"),
        D("refresh.max_item_age_hours", "Refresh", "Ignore items older than", "Older items in a feed are skipped.", "int", 72, 6, 336, unit="hours"),
        D("refresh.feed_timeout_s", "Refresh", "Feed timeout", "Give up on a feed after this long.", "int", 25, 5, 120, unit="seconds"),

        D("storage.retention_days", "Storage", "Keep articles for", "Articles older than this are deleted a whole day at a time.", "int", 90, 3, 365, unit="days"),
        D("storage.purge_uncorroborated", "Storage", "Delete stories that only one outlet reported", "On (default): a story that only one outlet reported, that is not part of a storyline in which another story has two outlets or more, and that you did not save, is deleted with its articles once the wait below has passed; so is a story with an advertisement's headline (sponsored posts, promo codes) that no second independent outlet wrote. One outlet alone is most often an advertisement, spam or a post nobody else carried. Stories that others picked up are not touched. Runs with the storage check, every 10 minutes.", "bool", True),
        D("storage.purge_wait_hours", "Storage", "...after waiting for a second outlet for", "A story is given this long to be picked up by a second outlet before it is deleted. The wait is never shorter than the age limit for feed items (Refresh) plus 12 hours: until then a feed may still show the item, and deleting it would only store it again.", "int", 84, 24, 720, unit="hours"),
        D("storage.store_reprints", "Storage", "Store reprints as articles", "Off (default): a report that repeats another outlet's text word for word (same headline and opening, or the same headline in the same story) is not stored again; only which outlet carried it, when, and its link are kept (about 150 bytes instead of about 2 KB), so the outlet counts and the list of outlets stay complete. On: every copy is stored.", "bool", False),
        D("storage.purge_disabled_languages", "Storage", "Delete stored articles in languages you do not collect", "On (default): articles in a language that is not in Sources → Languages to collect are deleted at the next storage check (every 10 minutes), and new ones are never stored. Stories you saved keep theirs.", "bool", True),
        D("storage.disk_cap_gb", "Storage", "Disk cap", "The most disk space News Anchor may use, everything included. Empty = the proposed value until you confirm it.", "float", None, 1, 10000, unit="GB", nullable=True),
        D("storage.min_free_gb", "Storage", "Keep this much disk free", "If the disk's free space falls below this, News Anchor frees its own space first.", "float", 20.0, 0, 10000, unit="GB"),
        D("storage.image_reserve_gb", "Storage", "Allowance for container images", "Docker keeps images outside the data folder; this much is counted against the cap.", "float", 2.0, 0, 100, unit="GB"),

        D("resources.ram_budget_mb", "Resources", "RAM budget", "News Anchor lowers its own activity to stay under this (app process only).", "int", 1024, 256, 16384, unit="MB"),
        D("resources.leave_available_mb", "Resources", "Leave this much server memory available", "Optional work waits while the server has less memory available than this.", "int", 1536, 0, 16384, unit="MB"),

        D("appearance.theme", "Appearance", "Theme", "Dark is the default.", "enum", "dark", choices=("dark", "light", "system")),
        D("appearance.pane", "Appearance", "Reading pane on large screens", "Whether the reading pane starts open or minimised.", "enum", "open", choices=("open", "minimised")),
        D("appearance.font_px", "Appearance", "Reading text size", "Size of text in the reading pane.", "int", 17, 14, 22, unit="px"),
    ]


GROUP_ORDER = ["Home", "Feed", "Breaking news", "Sources", "Trust", "Social", "Search", "AI", "Refresh", "Storage", "Resources", "Appearance"]
PROFILES = {
    "light": {"refresh.concurrency": 8, "resources.ram_budget_mb": 1024},
    "balanced": {"refresh.concurrency": 16, "resources.ram_budget_mb": 2048},
    "fast": {"refresh.concurrency": 32, "resources.ram_budget_mb": 3072},
}


class SettingError(ValueError):
    pass


class SettingsService:
    def __init__(self, repo):
        self.repo = repo
        self.defs = {d.key: d for d in _defs()}
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None
        self.listeners: list = []                    # called (no arguments) after every change or reset

    # ---- reading ---------------------------------------------------------------------------------
    def _values(self) -> dict[str, Any]:
        with self._lock:
            if self._cache is None:
                self._cache = self.repo.settings_all()
            return self._cache

    def get(self, key: str) -> Any:
        d = self.defs[key]
        return self._values().get(key, d.default)

    def snapshot(self) -> list[dict]:
        vals = self._values()
        out = []
        for d in self.defs.values():
            v = vals.get(d.key, d.default)
            if d.kind == "secret":
                v = "set" if v else ""                       # a secret is never sent back to the browser
            out.append({"key": d.key, "group": d.group, "label": d.label, "help": d.help, "kind": d.kind, "unit": d.unit,
                        "min": d.min, "max": d.max, "choices": list(d.choices), "nullable": d.nullable, "multiline": d.multiline,
                        "default": d.default, "value": v, "is_default": d.key not in vals or vals[d.key] == d.default})
        return out

    # ---- writing ---------------------------------------------------------------------------------
    def coerce(self, d: Def, v: Any) -> Any:
        if v is None:
            if d.nullable:
                return None
            raise SettingError(f"{d.label} needs a value")
        try:
            if d.kind == "int":
                v = int(v)
            elif d.kind == "float":
                v = float(v)
            elif d.kind == "bool":
                if isinstance(v, str):
                    v = v.lower() in ("1", "true", "yes", "on")
                v = bool(v)
            elif d.kind == "enum":
                v = str(v)
                if v not in d.choices:
                    raise SettingError(f"{d.label} must be one of {', '.join(d.choices)}")
            elif d.kind == "multi":
                v = [str(x) for x in v]
                bad = [x for x in v if x not in d.choices]
                if bad:
                    raise SettingError(f"{d.label}: unknown {', '.join(bad)}")
            elif d.kind in ("place", "text", "secret"):
                v = str(v).strip() if d.kind == "secret" else str(v)
        except (TypeError, ValueError) as e:
            if isinstance(e, SettingError):
                raise
            raise SettingError(f"{d.label} must be a {d.kind}") from e
        if d.kind in ("int", "float"):
            if d.min is not None and v < d.min or d.max is not None and v > d.max:
                raise SettingError(f"{d.label} must be between {d.min:g} and {d.max:g}")
        return v

    def set_many(self, changes: dict[str, Any]) -> None:
        clean = {}
        for k, v in changes.items():
            if k not in self.defs:
                raise SettingError(f"Unknown setting {k}")
            clean[k] = self.coerce(self.defs[k], v)
        for k, v in clean.items():
            if v == self.defs[k].default:
                self.repo.setting_delete(k)
            else:
                self.repo.setting_set(k, v)
        self._invalidate()
        self.repo.audit("settings.update", ", ".join(clean))

    def reset(self, key: str | None = None, group: str | None = None) -> int:
        keys = [k for k, d in self.defs.items() if (key is None and group is None) or k == key or d.group == group]
        if key and key not in self.defs:
            raise SettingError(f"Unknown setting {key}")
        for k in keys:
            self.repo.setting_delete(k)
        self._invalidate()
        self.repo.audit("settings.reset", key or group or "all")
        return len(keys)

    def apply_profile(self, name: str) -> None:
        if name not in PROFILES:
            raise SettingError("Unknown profile")
        self.set_many(PROFILES[name])

    def _invalidate(self) -> None:
        with self._lock:
            self._cache = None
        for fn in self.listeners:
            try:
                fn()
            except Exception:                        # noqa: BLE001 - a listener must not break saving a setting
                pass
