# News Anchor

A personal, neutral news service for the home server. Top stories at any place (Global, region, country, state, city) and any topic
(the 17 IPTC categories, 118 sub-topics; continents, UN regions, countries, states and cities, plus groups such as the EU and ASEAN), every story linked to its publishers, every source labelled by type. Full reference:
**docs/playbook.html** (also kept as a Google Doc; sections 20.11 to 20.28 describe the lists, the Latest tab and the clean-up). All build phases are done: collection, trust crawler, discovery,
search and story merging, AI readings, social sources.

## Run

```bash
cp .env.example .env          # set NA_DB_PASSWORD; adjust the memory backstops if you like
docker-compose up -d --build  # then open http://<server>:8001 ; the first visit asks you to choose a password
```

(`docker compose` works the same if you have the compose plugin instead of the standalone binary.)

Two containers: `app` (web, API and background jobs in one process) and `db` (PostgreSQL with pgvector, tuned small).
Data lives in `./data` (`postgres`, `app`, `shared`). Everything else is set on the Settings page: disk cap and retention (Storage),
RAM budget (Memory), topics, discovery, search, AI services and prompts, refresh interval, the lists (Fresh window, decay, Trending, Latest), breaking marks, the clean-up of stories only one outlet reported, theme, trust weights and the lowest trust band shown. Every setting has a Reset.

Nothing is hidden: every story from every source is shown, marked with its publisher's reliability band and a verification mark (confirmed, reported, one reliable outlet, check before sharing). A reliability filter is available in Settings but off by default.

Sources: the registry starts from a hand-checked list and grows by itself from several independent views of the news: GDELT's 15-minute world news files (English and 65 translated languages), Google News' country editions, local city pages and search (RSS; stories from publishers already collected are stored with the publisher's own link), community link sites (Hacker News, Techmeme and others), Wikidata (country by country), Media Cloud's directory (free API token in `.env` as `NA_MEDIACLOUD_TOKEN` or in Settings → Sources) and news searches for what is covered thinnest. Sites without a feed are read from their Google News sitemap or, failing that, their home page. Topic tags come from keyword rules checked against the 25 most similar recent stories, a small model per topic and the section in the article's address. Rated newcomers replace weaker sources only where no place, subject or language loses coverage. Countries with the fewest sources are served first, and a country at its limit takes no more. Each candidate's feed is found and validated before anything is collected. Lists show English only and only English-language sources are collected by default; both are settings. Articles in languages you do not collect are never stored, and word-for-word reprints keep only outlet, time and link. Local translation exists but is off by default (the free models are too weak for sensitive news).

Lists: Latest lists the stories that earned their place in the last 24 hours, newest first: a story is listed when 3 different outlets reported it at different times (wire copies count once, reports within the same 3 minutes count once, and 10 different outlets are listed at once); one outlet, a spam network and advertisements never appear. Top and Trending each have three views: Aggregated (the default), Fresh (events first reported in the last day, including a new development of a storyline that had been quiet) and Ongoing (stories that began earlier and are still reported). Aggregated fuses the two Top lists by rank, so giants cannot bury fresh news and fresh news cannot hide giants; in Trending it sorts the union by surge. Top ranks fair coverage of the last 24 hours, where an outlet counts less the longer ago it joined (decay, set in Settings → Feed). Trending ranks the surge: outlets that joined in the last 2 hours against the story's own usual rate. An ongoing story is scaled by the outlets that joined it in the last 6 hours (n / (n + 12)), so a story nobody reports any more sinks smoothly in Top and in the Aggregated mix. Breaking is a mark only and does not change the order. A story that repeats a better one above it is dropped, and developments of one storyline that are all in the list share one row that opens. The Storylines tab ranks whole storylines by the coverage they drew in a period you choose (6 hours to 30 days). Reports that name different people, teams or places are never put in one story or storyline (playbook 20.16). Ranking is country-balanced, so breadth across countries outweighs volume from one country. Every story says when its event is (first reported, ongoing since, or scheduled for a date), and the story view has the storyline's timeline and the parts of the story that many outlets reported on their own. Method: playbook sections 20.11 and 20.27.

Clean-up: a story that only one outlet reported (and that is in no storyline with a bigger story, and is not saved) is deleted with its articles after a wait (84 hours by default, never shorter than the feed item age limit plus 12 hours, so that a feed still showing the item does not store it again), and so is a story with an advertisement's headline that no second independent outlet wrote. Status shows what was deleted and what is waiting; Settings → Storage turns it off.

Collection: every feed is checked on its own schedule (base interval in Settings → Refresh, 30 minutes by default; a feed with news is checked sooner, a quiet one up to every 2 hours), and a scheduler tick every 30 seconds starts a cycle whenever any collected feed is due. Only feeds of sources in an enabled language are collected (English by default); Status → "Feeds last checked" shows how many are collected, checked in the last 30 minutes, more than 15 minutes late and never checked. The server must stay awake: while the machine sleeps nothing is collected, and a catch-up burst follows when it wakes. Lists are cached for 20 seconds, so a new story shows within a refresh of the page.

Social: Settings → Social follows Bluesky and Mastodon accounts (free), your Bluesky or Mastodon timeline, YouTube channels (free Data API key), Reddit (your own script app; RSS until Reddit ends it on 13 Nov 2026) and X (Log in with X using your own developer app, callback `http://<server>:8001/api/social/x/callback`; pay-per-use, with a monthly post budget). Posts are typed Social and never reach Top, Trending or Latest without a news outlet carrying the story. Explore (left rail) is a Media-Cloud-style statistics page over everything collected; Status shows what has been collected, from whom, how many feeds are collected, checked and late, what the clean-up deleted, and when each job last ran. Change the password under Settings → Password.

AI: add any free key under Settings → AI (Gemini, Groq, Cerebras, OpenRouter, Mistral, NVIDIA, GitHub, Hugging Face, SambaNova, Together, Cohere, or any OpenAI-compatible address; LLM7 and Pollinations work without a key but are tightly limited). Every statement in a reading cites its source and is checked against it. Briefings and story digests for Global, your favourites and recently opened scopes are prepared in the background so they open at once (within a share of the daily call cap).

Trust: a crawl (every 7 days by default) scores every source from published evidence (Lin et al. 2023 domain ratings, Wikipedia's perennial-sources list,
corroboration across our own stories, transparency pages, domain age, ownership from Wikidata) and shows the evidence on the Sources page.
Sources with no expert rating get measured evidence (fact-checkers' labels, the Burdisso et al. 2024 hyperlink-graph estimate, citations by rated outlets, sister outlets, pickup by rated outlets) and a model trained on the rated sources; a band is estimated, and marked so, only where that model is precise enough, otherwise the source stays "unrated". You can override any source there. Method and weights: Sources page, or playbook section 5.

Secrets: nothing is stored in the repository. `.env` (from `.env.example`) holds the database password and optional tokens and is ignored by git; tests use a random password for each run (`NA_TEST_PASSWORD`, set in `backend/tests/conftest.py`); `scripts/e2e_ui.py` reads the password of the running app from `NA_E2E_PASSWORD`.

The image build runs the unit tests. Database tests need a live PostgreSQL: `scripts/test-db.sh` starts a throwaway one and runs
everything (254 tests). `scripts/e2e_ui.py` drives the running stack in a real browser (see its header). UI: `cd frontend && npm run dev` (proxies `/api` to port 8001). `npm run build` type-checks strictly.
The frontend installs from the public npm registry (`frontend/.npmrc`).

## Layout

```
backend/app/
  data/        topics.txt (taxonomy + aliases), sources.txt (starter registry), countries.json (ISO 3166 / UN M49, CC BY-SA 4.0)
  dao/         fetch/http.py (one browser user agent, robots.txt on/off, per-host spacing, conditional GET), feeds/rss.py, trust/ (rating datasets, RDAP, Wikidata),
               discovery/ (GDELT files, Google News, Wikidata, Media Cloud, feed finder), social/ (Bluesky, Mastodon, YouTube, Reddit, X),
               repo/ (all SQL)
  services/    topics, places, ingest, stories, topics_service, settings_service, storage (disk cap), resources (RAM budget),
               trust + trust_scoring (source reliability), discovery, merge (story clustering), ranking (views, decay, surge), dedupe (repeats, grouping), subevents, spam (advertisement headlines), translate, social, prefetch
  jobs/        scheduler.py (storage check, trust, discovery, feed cycles, merge passes)
  ai/          providers, router, prompts and the checks of a reading against its sources
  api/         main.py (routes), auth.py (scrypt + signed cookie + CSRF header, as in Portfolio Tracker)
  migrations/  numbered SQL files
frontend/src/  React 19 + TypeScript + Vite; pages News, Settings, Sources, Status, Explore, Login
prototypes/    the layout prototype used to choose the split view
docs/          playbook.html
```

## Requests and robots.txt

Every request carries the browser user agent in `NA_USER_AGENT` (default: Chrome 122 on Windows). Settings → Refresh → *Obey robots.txt rules*
(default on): on, a site's robots.txt is fetched, kept in memory for 24 hours and obeyed (matched as `NewsAnchor`); off, nothing is matched or
kept (for debugging), and robots.txt is read afresh only to find a site's announced `Sitemap:` lines. Details: playbook section 20.18.

Settings → Refresh → *Also fetch with curl-impersonate* (default off): when an ordinary request returns nothing usable, it is repeated once with
[curl-impersonate](https://github.com/lwthiker/curl-impersonate) (a browser's TLS fingerprint; installed in the image under `/opt/curl-impersonate`). Playbook section 20.20.

## Adding sources and topics

* `backend/app/data/sources.txt`: add an `@source` line and one or more `feed` lines (format explained at the top of the file; each source states its type: news, public_broadcaster, state_funded_broadcaster, official, company, specialist or factcheck).
  A feed whose topic hint is a sub-topic is polled only while a related topic is enabled in Settings → Topics.
* `backend/app/data/topics.txt`: indentation is the hierarchy; aliases after the second `|`; `~alias` means "count only with context";
  `*` = enabled by default.
  Both files are checked by tests (`tests/test_sources_file.py`).

Third-party data: country list from github.com/lukes/ISO-3166-Countries-with-Regional-Codes (CC BY-SA 4.0); states and cities from GeoNames (CC BY 4.0); translation models from OPUS-MT / Argos (CC BY 4.0).
