"""Official social accounts of news outlets, as recorded in Wikidata (properties P2397 YouTube channel, P4033 Mastodon address,
P12361 Bluesky handle, P2002 X username, P3984 subreddit). Looked up by the outlet's website."""
from __future__ import annotations

import json

import httpx

from app.dao import wikimedia

SPARQL = "https://query.wikidata.org/sparql"
PROPS = {"youtube": "P2397", "mastodon": "P4033", "bluesky": "P12361", "x": "P2002", "reddit": "P3984"}


def query(domains: list[str]) -> str:
    vals = " ".join(f"<{s}://{w}{d}{t}>" for d in domains for s in ("http", "https") for w in ("", "www.") for t in ("", "/")
                    if all(ch.isalnum() or ch in ".-" for ch in d))
    opt = " ".join(f"OPTIONAL {{ ?item wdt:{p} ?{k} }}" for k, p in PROPS.items())
    agg = " ".join(f'(GROUP_CONCAT(DISTINCT ?{k}; separator="|") AS ?{k}s)' for k in PROPS)
    return f"SELECT ?item ?site {agg} WHERE {{ VALUES ?site {{ {vals} }} ?item wdt:P856 ?site . {opt} }} GROUP BY ?item ?site"


def _host(site: str) -> str:
    return site.split("//", 1)[-1].split("/", 1)[0].lower().removeprefix("www.")


def _pick(values: list[str], domain: str) -> str:
    """The account that best matches the outlet: the one named after its domain, else the shortest (main accounts are usually shortest)."""
    vals = [v for v in values if v]
    if not vals:
        return ""
    for v in vals:
        if v.lower() == domain or v.lower().split("@")[0] == domain.split(".")[0]:
            return v
    return min(vals, key=len)


async def accounts(client: httpx.AsyncClient, domains: list[str]) -> dict[str, dict]:
    """{domain: {qid, youtube, mastodon, bluesky, x, reddit}} for the domains Wikidata knows."""
    if not domains:
        return {}
    r = await client.post(SPARQL, data={"query": query(domains)}, headers={"Accept": "application/sparql-results+json", "User-Agent": wikimedia.user_agent()}, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"Wikidata answered {r.status_code}")
    out: dict[str, dict] = {}
    for b in json.loads(r.text)["results"]["bindings"]:
        dom = _host(b["site"]["value"])
        o = out.setdefault(dom, {"qid": b["item"]["value"].rsplit("/", 1)[-1], **{k: "" for k in PROPS}})
        for k in PROPS:
            v = _pick(b.get(f"{k}s", {}).get("value", "").split("|"), dom)
            if v and not o[k]:
                o[k] = v
    return out
