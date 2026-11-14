"""Finding publishers and stories on public services, used lightly and with spacing:

* Google News RSS (the default): every country edition's front page and sections, local pages for cities, and news search. Each item
  names its publisher and the publisher's home page (<source url=...>); the item's link is Google's redirect, which is opened (two
  requests) only for stories from publishers the app already collects and has not stored yet. Google's robots.txt disallows /rss and the
  redirect links for programs; the owner was told by Google that reading them for personal use, without heavy load, is fine (2 Nov 2026),
  and chose to use them. Turning "discovery.gnews_mode" to "web" goes back to only the pages robots.txt allows.
* Google News web pages: the editions' section pages (allowed by robots.txt). Each story card carries the publisher's own address.
* GDELT DOC API: searches worldwide news and answers with each article's domain, country and language. Allows one request every 5 seconds.
"""
from __future__ import annotations

import base64
import datetime as dt
import html as htmllib
import json
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlsplit

import httpx

from app.dao.fetch.robots import robots_allows  # noqa: F401 - used by callers as websearch.robots_allows
from app.services.textutil import clean_url

GN = "https://news.google.com"


class WebRateLimited(Exception):
    pass


# ---- GDELT DOC API -------------------------------------------------------------------------------
def gdelt_url(query: str, timespan: str = "3d", lang: str = "english") -> str:
    return (f"https://api.gdeltproject.org/api/v2/doc/doc?query={quote_plus(query + ' sourcelang:' + lang)}"
            f"&mode=artlist&maxrecords=250&format=json&timespan={timespan}&sort=datedesc")


def parse_gdelt(text: str) -> list[dict]:
    """[{domain, country, language, url, title}]. GDELT answers a rate limit with plain text, not JSON."""
    try:
        d = json.loads(text)
    except ValueError:
        if "limit requests" in text.lower() or "one every" in text.lower():
            raise WebRateLimited("GDELT allows one request every 5 seconds")
        return []
    return [{"domain": (a.get("domain") or "").lower().removeprefix("www."), "country": a.get("sourcecountry") or "", "language": (a.get("language") or "").lower(),
             "url": a.get("url") or "", "title": a.get("title") or ""} for a in d.get("articles", []) if a.get("domain")]


async def gdelt(client: httpx.AsyncClient, query: str, timespan: str = "3d") -> list[dict]:
    r = await client.get(gdelt_url(query, timespan), timeout=60)
    if r.status_code == 429:
        raise WebRateLimited("GDELT answered 429")
    return parse_gdelt(r.text) if r.status_code == 200 else []


# ---- Google News editions -----------------------------------------------------------------------
# Editions (language-country), each checked to answer (en-LB, en-BD, en-LK, en-NP, fr-CI and fr-CM do not exist).
EDITIONS = """en-US en-GB en-IN en-AU en-CA en-NZ en-IE en-SG en-ZA en-NG en-KE en-PH en-PK en-MY en-GH en-ZW en-UG en-TZ en-BW en-NA en-ET
en-IL hi-IN bn-IN ta-IN te-IN ml-IN mr-IN bn-BD ur-PK ar-EG ar-SA ar-AE ar-LB fr-FR fr-BE fr-CA fr-CH fr-SN fr-MA
de-DE de-AT de-CH es-ES es-MX es-AR es-CO es-CL es-PE es-VE es-CU es-US pt-BR pt-PT it-IT nl-NL nl-BE sv-SE no-NO pl-PL cs-CZ
sk-SK sl-SI hu-HU ro-RO bg-BG sr-RS uk-UA ru-RU lv-LV lt-LT el-GR tr-TR he-IL id-ID th-TH vi-VN zh-TW zh-HK ja-JP ko-KR""".split()
# An edition's section pages, by the names of its English menu. NATION is the country's own page ("India", "U.S.", "Kenya").
SECTIONS = ["TOP", "WORLD", "NATION", "BUSINESS", "TECHNOLOGY", "ENTERTAINMENT", "SPORTS", "SCIENCE", "HEALTH"]
SECTION_NAMES = {"Top stories": "TOP", "World": "WORLD", "Business": "BUSINESS", "Technology": "TECHNOLOGY", "Entertainment": "ENTERTAINMENT",
                 "Sports": "SPORTS", "Science": "SCIENCE", "Health": "HEALTH"}
_NOT_NATION = {"Local news", "Local", "For you", "Following", "Home", "Showcase", "News Showcase"}


async def robots(client: httpx.AsyncClient) -> str | None:
    r = await client.get(f"{GN}/robots.txt", timeout=30)
    return r.text if r.status_code == 200 else None


def _params(edition: str) -> str:
    hl, gl = edition.split("-")
    return f"hl={hl}-{gl}&gl={gl}&ceid={gl}:{hl}" if hl == "en" else f"hl={hl}&gl={gl}&ceid={gl}:{hl}"


def home_url(edition: str) -> str:
    return f"{GN}/home?{_params(edition)}"


def topic_url(edition: str, topic_id: str) -> str:
    return f"{GN}/topics/{topic_id}?{_params(edition)}"


def parse_sections(page_html: str) -> dict[str, str]:
    """{section: topic id} from an edition's menu. Only English menus are understood; other editions give {}."""
    order: list[tuple[str, str]] = []
    for tid, label in re.findall(r'<a [^>]*href="\./topics/([A-Za-z0-9_-]+)\?[^"]*"[^>]*>([^<]{1,40})<', page_html):
        label = htmllib.unescape(label).strip()
        if label and label not in [x for x, _ in order]:
            order.append((label, tid))
    out = {SECTION_NAMES[x]: t for x, t in order if x in SECTION_NAMES}
    labels = [x for x, _ in order]
    if "World" in labels:
        k = labels.index("World")
        if k > 0 and labels[k - 1] not in SECTION_NAMES and labels[k - 1] not in _NOT_NATION:
            out["NATION"] = order[k - 1][1]
    return out


def _publisher_url(jslog: str) -> str | None:
    b = jslog.replace("-", "+").replace("_", "/")
    try:
        d = json.loads(base64.b64decode(b + "=" * (-len(b) % 4)))
    except (ValueError, TypeError):
        return None
    for x in d if isinstance(d, list) else []:
        if isinstance(x, str) and x.startswith(("https://", "http://")) and "news.google." not in x:
            return x
    return None


def parse_cards(page_html: str) -> list[dict]:
    """[{url, name, title, published}] for every story card: the publisher's own article address (from the card's tracking data;
    Google's /read/ link is never kept), the publisher's name, the headline and the time Google shows (None if absent)."""
    out = []
    for part in page_html.split('<a class="WwrzSb"')[1:]:
        m = re.search(r'jslog="95014; 5:([A-Za-z0-9+/=_-]+)', part[:4000])
        url = _publisher_url(m.group(1)) if m else None
        src = re.search(r'class="vr1PYe"[^>]*>([^<]+)<', part)
        title = re.search(r'class="(?:gPFEn|JtKRv)[^"]*"[^>]*>([^<]+)<', part)
        if not (url and src and title):
            continue
        when = None
        tm = re.search(r'<time[^>]*datetime="([^"]+)"', part)
        if tm:
            try:
                when = dt.datetime.fromisoformat(tm.group(1).replace("Z", "+00:00"))
            except ValueError:
                when = None
        out.append({"url": htmllib.unescape(url), "name": htmllib.unescape(src.group(1)).strip(),
                    "title": htmllib.unescape(title.group(1)).strip(), "published": when})
    return out


async def page(client: httpx.AsyncClient, url: str) -> str | None:
    """The page's HTML; None when it does not exist (or Google sent us elsewhere)."""
    r = await client.get(url, timeout=30, follow_redirects=True)
    if r.status_code in (429, 503):
        raise WebRateLimited("Google News asked us to slow down")
    if r.status_code != 200 or urlsplit(str(r.url)).hostname != "news.google.com":
        return None
    return r.text



# ---- Google News RSS ----------------------------------------------------------------------------
RSS_SECTIONS = {"TOP": "", "WORLD": "WORLD", "NATION": "NATION", "BUSINESS": "BUSINESS", "TECHNOLOGY": "TECHNOLOGY", "ENTERTAINMENT": "ENTERTAINMENT",
                "SPORTS": "SPORTS", "SCIENCE": "SCIENCE", "HEALTH": "HEALTH"}


def rss_url(edition: str, section: str = "TOP", geo: str = "", query: str = "") -> str:
    q = _params(edition)
    if query:
        return f"{GN}/rss/search?q={quote_plus(query)}&{q}"
    if geo:
        return f"{GN}/rss/headlines/section/geo/{quote_plus(geo)}?{q}"
    sec = RSS_SECTIONS.get(section, section)
    return f"{GN}/rss/headlines/section/topic/{sec}?{q}" if sec else f"{GN}/rss?{q}"


def search_url(query: str, gl: str = "US", hl: str = "en") -> str:
    gl = (gl or "US").upper()
    return rss_url(f"{hl}-{gl}", query=query)


def gid_of(link: str) -> str | None:
    """Google's id of a story from its redirect link (news.google.com/rss/articles/<id>?...)."""
    m = re.search(r"/(?:rss/)?articles/([A-Za-z0-9_-]+)", link or "")
    return m.group(1) if m else None


def parse_rss(xml: bytes | str) -> list[dict]:
    """[{name, site_url, title, link, gid, published}] one per item that names its publisher. The title loses Google's " - Publisher" tail."""
    root = ET.fromstring(xml)
    out = []
    for it in root.iter("item"):
        src = it.find("source")
        if src is None or not src.get("url"):
            continue
        name = (src.text or "").strip()
        title = (it.findtext("title") or "").strip()
        if name and title.endswith(f" - {name}"):
            title = title[: -len(name) - 3].strip()
        when = None
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            when = None
        link = (it.findtext("link") or "").strip()
        out.append({"name": name, "site_url": src.get("url", "").strip(), "url": src.get("url", "").strip(), "title": title, "link": link,
                    "gid": gid_of(link), "published": when})
    return out


async def rss(client: httpx.AsyncClient, url: str) -> list[dict] | None:
    """Items of one Google News RSS page; None when the page does not exist."""
    r = await client.get(url, timeout=30, follow_redirects=True)
    if r.status_code in (429, 503):
        raise WebRateLimited("Google News asked us to slow down")
    if r.status_code != 200:
        return None
    try:
        return parse_rss(r.content)
    except ET.ParseError:
        return None


async def google_news(client: httpx.AsyncClient, query: str, gl: str = "US") -> list[dict]:
    return await rss(client, search_url(query, gl)) or []


# ---- Google's redirect links --------------------------------------------------------------------
BATCH = f"{GN}/_/DotsSplashUi/data/batchexecute"


def _decode_request(gid: str, ts: str, sig: str) -> str:
    inner = (f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
             f'"{gid}",{ts},"{sig}"]')
    return json.dumps([[["Fbv4je", inner, None, "generic"]]])


def parse_decoded(body: str) -> str | None:
    """The publisher's address from Google's answer to a decode request."""
    try:
        chunk = body.split("\n\n", 1)[1]
        data = json.loads(chunk)
        for row in data:
            if isinstance(row, list) and len(row) > 2 and row[1] == "Fbv4je" and row[2]:
                url = json.loads(row[2])[1]
                return url if isinstance(url, str) and url.startswith(("http://", "https://")) else None
    except (IndexError, ValueError, TypeError):
        return None
    return None


async def decode(client: httpx.AsyncClient, gid: str) -> str | None:
    """The publisher's own address behind a Google News link: the link's page gives a signature and time, which Google's page script
    sends to its own endpoint to get the address. Two requests (about 120 KB). None when Google gives no answer."""
    r = await client.get(f"{GN}/rss/articles/{gid}", timeout=30, follow_redirects=True)
    if r.status_code in (429, 503):
        raise WebRateLimited("Google News asked us to slow down")
    if r.status_code != 200:
        return None
    sg = re.search(r'data-n-a-sg="([^"]+)"', r.text)
    ts = re.search(r'data-n-a-ts="([^"]+)"', r.text)
    if not (sg and ts):
        return None
    r2 = await client.post(BATCH, data={"f.req": _decode_request(gid, ts.group(1), sg.group(1))},
                           headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}, timeout=30)
    if r2.status_code in (429, 503):
        raise WebRateLimited("Google News asked us to slow down")
    if r2.status_code != 200:
        return None
    url = parse_decoded(r2.text)
    return clean_url(url) if url else None
