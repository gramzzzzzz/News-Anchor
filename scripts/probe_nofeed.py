"""Why are sources stuck in no_feed? Reads each one's home page once (same fetcher, user agent and robots.txt setting as the app),
classifies what came back, and writes /tmp/nofeed_probe.json. Read-only: nothing in the database changes.
Run inside the app container:  docker cp scripts/probe_nofeed.py news-anchor:/tmp/ && docker exec -d -w /app -e PYTHONPATH=/app news-anchor python /tmp/probe_nofeed.py"""
import asyncio
import json
import os
import re
import sys

import psycopg

from app.dao.discovery import feedfind
from app.dao.feeds import homepage
from app.dao.fetch.http import Fetcher

OUT = os.environ.get("PROBE_OUT", "/tmp/nofeed_probe.json")
KEYS = json.load(open(os.environ["PROBE_KEYS"])) if os.environ.get("PROBE_KEYS") else None      # re-probe just these sources
SLOTS = int(os.environ.get("PROBE_SLOTS", "80"))
TIMEOUT = float(os.environ.get("PROBE_TIMEOUT", "15"))
MIN_LINKS = 5            # sources.homepage_min_links default


def classify(r, home: str) -> dict:
    if r.error == "Disallowed by robots.txt":
        return {"cls": "robots_disallowed"}
    if r.error and r.status is None:
        e = r.error
        if "Timed out" in e:
            return {"cls": "timeout"}
        if re.search(r"gaierror|Name or service|nodename|resolve|ConnectError", e, re.I) and not re.search(r"ssl|certificate", e, re.I):
            return {"cls": "dns_or_connect_error", "detail": e[:80]}
        if re.search(r"ssl|certificate", e, re.I):
            return {"cls": "tls_error", "detail": e[:80]}
        if "too large" in e.lower():
            return {"cls": "too_large"}
        return {"cls": "other_error", "detail": e[:80]}
    if r.status and r.status != 200:
        return {"cls": f"http_{r.status}" if r.status in (401, 403, 404, 410, 429, 451) else ("http_5xx" if r.status >= 500 else f"http_{r.status}")}
    page = (r.body or b"").decode("utf-8", "replace")
    if len(page) < 500:
        return {"cls": "empty_page"}
    final = r.final_url or home
    adv = feedfind.advertised(page, final)
    links = homepage.article_links(page, final)
    out = {"advertised": len(adv), "links": len(links), "final": final}
    if adv:
        out["cls"] = "feed_advertised_but_not_valid"
    elif len(links) >= MIN_LINKS:
        out["cls"] = "homepage_has_links"             # should have been usable as a home-page source
    elif len(links) > 0:
        out["cls"] = "few_links"
    elif re.search(r"<script", page, re.I) and len(re.sub(r"<[^>]+>", " ", page)) < 3000:
        out["cls"] = "js_only_page"
    else:
        out["cls"] = "no_article_links"
    return out


async def main():
    with psycopg.connect(os.environ["NA_DATABASE_URL"]) as c:
        rows = c.execute("SELECT key, domain, homepage, country, language, origin FROM sources WHERE status = 'no_feed' ORDER BY key").fetchall()
    f = Fetcher(os.environ["NA_USER_AGENT"], timeout=TIMEOUT, host_delay=0)
    await f.start()
    sem = asyncio.Semaphore(SLOTS)          # few at a time: the app container has a 1.5 GB limit and a refresh may be running
    res = json.load(open(OUT)) if os.path.exists(OUT) else {}     # resumable
    rows = [r for r in rows if r[0] not in res and (KEYS is None or r[0] in KEYS)]
    done = 0

    async def one(row):
        nonlocal done
        key, domain, home, country, lang, origin = row
        home = home or f"https://{domain}/"
        async with sem:
            try:
                r = await asyncio.wait_for(f.get(home, accept="text/html,*/*;q=0.8"), 45)       # a site that drips bytes must not hold a slot
                d = classify(r, home)
            except asyncio.TimeoutError:
                d = {"cls": "timeout", "detail": "overall 45 s"}
            except Exception as e:                    # noqa: BLE001
                d = {"cls": "probe_error", "detail": str(e)[:80]}
        d.update(domain=domain, country=country, language=lang, origin=origin)
        res[key] = d
        done += 1
        if done % 250 == 0:
            json.dump(res, open(OUT, "w"))
            print(done, file=sys.stderr, flush=True)

    await asyncio.gather(*(one(r) for r in rows))
    json.dump(res, open(OUT, "w"))
    print("done", len(res), flush=True)
    await f.close()


asyncio.run(main())
