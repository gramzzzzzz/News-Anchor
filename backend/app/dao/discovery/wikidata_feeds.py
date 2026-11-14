"""Wikidata: news outlets. Two questions are asked:

* outlets(): every outlet that records an official website and a web-feed address (property P1019), worldwide.
* country_outlets(): the best-known outlets of one country (newspapers, news sites, agencies, broadcasters) that record a website,
  most Wikipedia articles first. Most outlets record no feed, so the feed finder looks for one on the site.
Wikidata is edited by volunteers in every language, so it is not one company's or one country's list."""
from __future__ import annotations

import asyncio
import json

import httpx

from app.dao import wikimedia

SPARQL = "https://query.wikidata.org/sparql"
CLASSES = "Q11032 Q1153191 Q1110794 Q17232649 Q1002697 Q2001305 Q41298 Q5398426 Q15265344 Q1616075 Q1371037 Q220898 Q1860"
# newspaper, online newspaper, daily, news website, periodical, news media, magazine, TV series?, broadcaster, TV station, ...
COUNTRY_CLASSES = CLASSES + " Q192283 Q14350 Q1254874 Q11033"         # + news agency, radio station, TV network, mass media
QUERY = f"""SELECT ?item ?itemLabel ?site ?feed ?cc ?lang WHERE {{
  VALUES ?cls {{ {' '.join('wd:' + c for c in CLASSES.split())} }}
  ?item wdt:P31 ?cls ; wdt:P1019 ?feed ; wdt:P856 ?site .
  OPTIONAL {{ ?item wdt:P17 ?c . ?c wdt:P297 ?cc }}
  OPTIONAL {{ ?item wdt:P407 ?l . ?l wdt:P218 ?lang }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" }}
}} LIMIT 20000"""


def country_query(qid: str, limit: int = 80) -> str:
    """Step one: the best-known items of the country in the outlet classes, most Wikipedia articles first, nothing else asked. The country
    is given by its item (Q142), and the classes are a UNION after it: the same question with a VALUES list of classes first, or with
    the website, language, label and not-closed conditions added, runs for over a minute and hits Wikidata's time limit."""
    qid = "Q" + "".join(ch for ch in qid if ch.isdigit())
    classes = " UNION ".join(f"{{ ?item wdt:P31 wd:{c} }}" for c in COUNTRY_CLASSES.split())
    return f"""SELECT ?item ?links WHERE {{
  ?item wdt:P17 wd:{qid} ; wikibase:sitelinks ?links .
  {classes}
}} ORDER BY DESC(?links) LIMIT {int(limit)}"""


def details_query(qids: list[str]) -> str:
    """Step two: for those few items, the website, language and name, and whether the outlet has closed (it then has an end date)."""
    vals = " ".join("wd:Q" + "".join(ch for ch in q if ch.isdigit()) for q in qids)
    return f"""SELECT ?item ?itemLabel ?site ?lang ?end WHERE {{
  VALUES ?item {{ {vals} }}
  ?item wdt:P856 ?site .
  OPTIONAL {{ ?item wdt:P576 ?end }}
  OPTIONAL {{ ?item wdt:P407 ?l . ?l wdt:P218 ?lang }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul" }}
}}"""


_qids: dict[str, str] = {}


async def country_qid(client: httpx.AsyncClient, cc: str) -> str | None:
    """The Wikidata item of a country from its ISO 3166-1 alpha-2 code (kept for the life of the process), or None."""
    cc = "".join(ch for ch in cc.upper() if ch.isalpha())[:2]
    if cc not in _qids:
        rows = await _sparql(client, f'SELECT ?c WHERE {{ ?c wdt:P297 "{cc}" }} LIMIT 1', 30)
        if rows:
            _qids[cc] = rows[0]["c"]["value"].rsplit("/", 1)[-1]
    return _qids.get(cc)


async def _sparql(client: httpx.AsyncClient, query: str, timeout: float) -> list[dict]:
    for pause in (5, 20, None):                              # 429, 502 and 503 are Wikidata's front servers busy or restarting: asked again shortly
        r = await client.post(SPARQL, data={"query": query}, headers={"Accept": "application/sparql-results+json", "User-Agent": wikimedia.user_agent()}, timeout=timeout)
        if r.status_code not in (429, 502, 503) or pause is None:
            break
        await asyncio.sleep(pause)
    if r.status_code != 200:
        raise RuntimeError(f"Wikidata answered {r.status_code}")
    return json.loads(r.text)["results"]["bindings"]


async def outlets(client: httpx.AsyncClient) -> list[dict]:
    """[{qid, name, site, feeds, country, language}] one per outlet."""
    by: dict[str, dict] = {}
    for b in await _sparql(client, QUERY, 120):
        qid = b["item"]["value"].rsplit("/", 1)[-1]
        o = by.setdefault(qid, {"qid": qid, "name": b.get("itemLabel", {}).get("value", qid), "site": b["site"]["value"], "feeds": [],
                                "country": b.get("cc", {}).get("value"), "language": b.get("lang", {}).get("value")})
        f = b["feed"]["value"]
        if f.startswith("http") and f not in o["feeds"]:
            o["feeds"].append(f)
    return list(by.values())


async def country_outlets(client: httpx.AsyncClient, cc: str, limit: int = 80) -> list[dict]:
    """[{qid, name, site, language, links}] for one country, most Wikipedia articles (sitelinks) first; one site per outlet.
    Two small questions instead of one big one (see country_query); a few more items than ``limit`` are read in step one because
    some have no website or have closed."""
    qid = await country_qid(client, cc)
    if not qid:
        return []
    top = await _sparql(client, country_query(qid, limit * 2 + 20), 100)
    links = {b["item"]["value"].rsplit("/", 1)[-1]: int(b.get("links", {}).get("value", 0) or 0) for b in top}
    if not links:
        return []
    by: dict[str, dict] = {}
    closed: set[str] = set()
    for b in await _sparql(client, details_query(list(links)), 100):
        item = b["item"]["value"].rsplit("/", 1)[-1]
        if b.get("end"):
            closed.add(item)
        site = b["site"]["value"]
        if item in by or not site.startswith("http"):
            continue
        by[item] = {"qid": item, "name": b.get("itemLabel", {}).get("value", item), "site": site, "language": b.get("lang", {}).get("value"),
                    "links": links[item], "country": cc.upper()}
    return sorted((o for q, o in by.items() if q not in closed), key=lambda o: -o["links"])[:limit]
