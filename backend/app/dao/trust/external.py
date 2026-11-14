"""RDAP (domain registration) and Wikidata (ownership) lookups."""
from __future__ import annotations

import asyncio
import datetime as dt
import json

import httpx

from app.dao import wikimedia

SPARQL = "https://query.wikidata.org/sparql"


class RateLimited(Exception):
    """The service asked us to slow down and did not recover after a short wait."""


async def rdap_registration(client: httpx.AsyncClient, domain: str) -> dt.date | None:
    """Registration date, or None when unknown. Raises RateLimited when the service keeps answering 429."""
    for attempt in range(3):
        try:
            r = await client.get(f"https://rdap.org/domain/{domain}", timeout=20, follow_redirects=True)
            if r.status_code == 429:
                if attempt == 2:
                    raise RateLimited(domain)
                try:
                    wait = float(r.headers.get("retry-after", "5"))
                except ValueError:
                    wait = 5.0
                await asyncio.sleep(min(max(wait, 1.0), 20.0))
                continue
            if r.status_code != 200:
                return None
            for e in r.json().get("events", []):
                if e.get("eventAction") == "registration":
                    return dt.date.fromisoformat(e["eventDate"][:10])
            return None
        except (httpx.HTTPError, ValueError, KeyError):
            return None
    return None


def _sparql(domains: list[str]) -> str:
    sites = " ".join(f"<{p}{w}{d}/>" for d in domains for p in ("https://", "http://") for w in ("www.", ""))
    return ("SELECT ?site ?item ?itemLabel (GROUP_CONCAT(DISTINCT ?ownerLabel; separator='; ') AS ?owners) WHERE { "
            f"VALUES ?site {{ {sites} }} ?item wdt:P856 ?site . "
            "OPTIONAL { ?item wdt:P127|wdt:P749 ?owner . ?owner rdfs:label ?ownerLabel . FILTER(LANG(?ownerLabel) = 'en') } "
            "SERVICE wikibase:label { bd:serviceParam wikibase:language 'en'. } } GROUP BY ?site ?item ?itemLabel")


async def wikidata_owners(client: httpx.AsyncClient, domains: list[str]) -> dict[str, dict]:
    """{domain: {qid, label, owners}} for the domains Wikidata knows as an official website."""
    out: dict[str, dict] = {}
    for i in range(0, len(domains), 25):
        chunk = domains[i:i + 25]
        try:
            r = await client.post(SPARQL, data={"query": _sparql(chunk)}, headers={"Accept": "application/sparql-results+json", "User-Agent": wikimedia.user_agent()}, timeout=60)
            if r.status_code != 200:
                continue
            for b in json.loads(r.text)["results"]["bindings"]:
                host = b["site"]["value"].split("//", 1)[1].strip("/").removeprefix("www.")
                out.setdefault(host, {"qid": b["item"]["value"].rsplit("/", 1)[-1], "label": b.get("itemLabel", {}).get("value", ""),
                                      "owners": b.get("owners", {}).get("value", "")})
        except (httpx.HTTPError, ValueError, KeyError):
            continue
    return out
