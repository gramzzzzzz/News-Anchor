"""Topic evidence from the section part of an article's address.

Editors file every article under a section, and most sites put the section in the address: /sport/cricket/..., /business/markets/...,
/entertainment/movies/... This is a label made by a person at the publisher, independent of the headline's words, so it is a good
check on the keyword rules (Pagel et al. 2023, "URLs can facilitate machine learning classification of news stories across languages
and contexts", Computational Communication Research). Only the folders of the path are used, never the last part (the headline slug),
and only folder names that clearly name a section; "news", "india", "world", dates and numbers say nothing about the topic."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

# folder name -> topic code (a category, or a sub-topic when the folder is that specific)
SECTIONS: dict[str, str] = {}


def _add(code: str, *names: str) -> None:
    for n in names:
        SECTIONS[n] = code


_add("sport", "sport", "sports", "sportsnews", "sport-news", "khel", "games-sports")
_add("sport.cricket", "cricket", "ipl", "cricket-news")
_add("sport.football", "football", "soccer", "premier-league", "laliga", "la-liga", "serie-a", "bundesliga", "champions-league", "isl", "epl")
_add("sport.motor-racing", "f1", "formula-1", "formula1", "formula-one", "motorsport", "motorsports", "motogp", "nascar", "indycar")
_add("sport.tennis", "tennis")
_add("sport.chess", "chess")
_add("sport.basketball", "basketball", "nba", "wnba")
_add("sport.american-football", "nfl", "american-football", "college-football", "ncaaf")
_add("sport.baseball", "baseball", "mlb")
_add("sport.hockey", "hockey", "nhl")
_add("sport.golf", "golf")
_add("sport.athletics", "athletics", "track-and-field", "running")
_add("sport.olympics", "olympics")
_add("sport.badminton", "badminton")
_add("sport.combat", "boxing", "mma", "ufc", "wrestling")
_add("sport.rugby", "rugby", "rugby-union", "rugby-league")
_add("sport.cycling", "cycling")
_add("economy", "business", "economy", "economics", "money", "finance", "biz", "business-news", "business-economy", "industry", "corporate")
_add("economy.markets", "markets", "market", "stocks", "stock-market", "investing", "mutual-funds", "ipo")
_add("economy.companies", "companies", "earnings", "company")
_add("economy.personal-finance", "personal-finance", "your-money", "tax", "savings")
_add("economy.crypto", "crypto", "cryptocurrency", "bitcoin", "web3")
_add("economy.startups", "startups", "startup")
_add("economy.banking", "banking", "banks", "insurance", "fintech")
_add("economy.autos", "auto", "autos", "automobile", "automobiles", "cars", "motoring", "car-news", "bikes", "wheels")
_add("economy.energy", "energy", "oil-and-gas", "power")
_add("economy.agriculture", "agriculture", "agri", "farming", "agribusiness", "rural")
_add("economy.trade", "trade")
_add("economy.macro", "macroeconomy", "indicators")
_add("economy.transport", "aviation", "airlines", "railways", "shipping", "logistics")
_add("economy.consumer", "retail", "consumer")
_add("science", "science", "tech", "technology", "sci-tech", "scitech", "science-technology", "science-and-technology", "innovation", "techscience")
_add("science.ai", "ai", "artificial-intelligence")
_add("science.cybersecurity", "cybersecurity", "cyber-security", "security-news", "infosec")
_add("science.computing", "gadgets", "mobile", "mobiles", "smartphones", "computing", "software", "apps", "gaming-hardware")
_add("science.internet", "internet", "social-media")
_add("science.space", "space", "astronomy", "isro", "nasa")
_add("health", "health", "healthcare", "wellbeing", "health-news", "medical")
_add("health.mental-health", "mental-health")
_add("health.nutrition-fitness", "fitness", "nutrition", "diet")
_add("environment", "environment", "climate", "green", "sustainability", "climate-change", "earth")
_add("weather", "weather")
_add("crime", "crime", "crime-news", "law", "legal", "courts", "justice", "law-and-order")
_add("crime.courts", "courts", "supreme-court", "high-court", "judiciary")
_add("politics", "politics", "political", "elections", "election", "government", "polls")
_add("politics.elections", "elections", "election", "assembly-elections", "lok-sabha-elections", "us-elections", "midterms")
_add("education", "education", "schools", "campus", "universities", "exams", "results", "careers", "edu")
_add("education.higher-education", "universities", "higher-education", "university", "college")
_add("labour", "jobs", "employment", "workplace", "labour", "labor")
_add("religion", "religion", "faith", "spirituality", "devotional")
_add("arts", "entertainment", "arts", "culture", "arts-culture", "arts-and-culture", "showbiz", "bollywood", "hollywood", "kollywood", "tollywood", "regional-cinema")
_add("arts.film", "movies", "movie", "film", "films", "cinema", "movie-reviews", "box-office", "bollywood", "hollywood", "kollywood", "tollywood")
_add("arts.tv-streaming", "tv", "television", "web-series", "ott", "streaming", "tv-shows")
_add("arts.music", "music")
_add("arts.celebrity", "celebrity", "celebrities", "celebs", "gossip")
_add("arts.books", "books", "literature")
_add("arts.video-games", "gaming", "games", "video-games", "esports")
_add("arts.media", "media")
_add("arts.stage", "theatre", "theater", "dance")
_add("lifestyle", "lifestyle", "life-style", "life", "living", "leisure")
_add("lifestyle.travel", "travel", "tourism")
_add("lifestyle.food", "food", "food-drink", "recipes", "dining", "restaurants")
_add("lifestyle.fashion", "fashion", "style", "beauty")
_add("lifestyle.home", "home-garden", "real-estate", "property", "homes", "interiors")
_add("lifestyle.astrology", "astrology", "horoscope", "horoscopes", "zodiac")
_add("lifestyle.pets", "pets")
_add("lifestyle.wellness", "wellness")
_add("conflict", "defence", "defense", "military", "war")
_add("conflict.defence", "defence", "defense", "military")
_add("disaster", "disaster", "disasters")
_add("society", "society", "social")
_add("society.obituaries", "obituaries", "obituary", "obits")
_add("human-interest", "offbeat", "viral", "trending", "weird", "odd-news", "bizarre")

# folders that look like sections but say nothing about the subject (and some that mislead)
_SKIP = {"news", "article", "articles", "story", "stories", "india", "world", "nation", "national", "international", "us", "uk", "local", "city", "cities",
         "states", "state", "regional", "latest", "top", "top-stories", "breaking", "breaking-news", "live", "blogs", "blog", "opinion", "opinions",
         "editorial", "editorials", "columns", "comment", "analysis", "features", "feature", "amp", "en", "english", "web", "photos", "videos", "video",
         "gallery", "podcasts", "podcast", "explainers", "explained", "content", "post", "posts", "p", "a", "m", "html", "index"}


def sections(url: str) -> list[str]:
    """The topic codes the address's folders name, most specific last; [] when it names none."""
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        return []
    parts = [p for p in path.split("/") if p]
    out: list[str] = []
    for p in parts[:-1][:4]:                                   # the folders, not the slug at the end
        if p in _SKIP or re.fullmatch(r"[\d_-]+|[a-z]{1,2}\d*|.*\d{3,}.*", p):
            continue
        code = SECTIONS.get(p)
        if code and code not in out:
            out.append(code)
    return out


def evidence(code: str, codes: list[str]) -> int:
    """+1 when the folders support ``code`` (the same topic, its category, or one of its sub-topics), -1 when they name only another
    category, 0 when they say nothing."""
    if not codes:
        return 0
    cat = code.split(".")[0]
    if any(c == code or c.split(".")[0] == cat and ("." not in c or "." not in code) for c in codes):
        return 1
    if any(c.split(".")[0] == cat for c in codes):              # a sibling sub-topic: the category agrees, the sub-topic does not
        return 0
    return -1
