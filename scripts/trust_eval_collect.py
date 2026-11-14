"""Collect a labelled sample for evaluating the trust score. Run: cd backend && .venv/bin/python ../scripts/trust_eval_collect.py

Labels come from two independent expert sources: Wikipedia's perennial-sources list (status per domain) and the Lin et al. 2023
aggregate. For every sampled domain we also collect the weak signals the app uses (transparency links, domain age, Wikidata owner)
so that analyse can ask: how well does each signal predict the experts' verdict? Output: /tmp/na-eval/sample.json (resumable)."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import httpx  # noqa: E402

from app.dao.fetch.http import Fetcher  # noqa: E402
from app.dao.trust import lin2023, wikipedia_rsp  # noqa: E402
from app.dao.trust.external import RateLimited, rdap_registration, wikidata_owners  # noqa: E402
from app.dao.trust.transparency import INDICATORS, analyse  # noqa: E402
from app.dao.trust.domains import registrable  # noqa: E402

OUT = Path("/tmp/na-eval")
OUT.mkdir(exist_ok=True)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
random.seed(7)


async def main() -> None:
    f = Fetcher(UA, timeout=20, host_delay=1.0)
    await f.start()
    lin_p, rsp_p = OUT / "lin.json", OUT / "rsp.json"
    if lin_p.exists() and rsp_p.exists():
        lin, rsp = json.loads(lin_p.read_text()), json.loads(rsp_p.read_text())
    else:
        lin = lin2023.parse((await f.get(lin2023.URL, robots=False)).body.decode())
        page = json.loads((await f.get(wikipedia_rsp.API, robots=False)).body)["parse"]["text"]
        rsp = {d: {"status": e.status, "name": e.name} for e in wikipedia_rsp.parse(page) for d in e.domains}
        lin_p.write_text(json.dumps(lin)); rsp_p.write_text(json.dumps(rsp))
    print(f"Lin {len(lin)} domains, RSP {len(rsp)} domains, both {len(set(lin) & set(rsp))}")

    # sample: RSP domains balanced by status, plus Lin extremes that RSP does not cover
    by = {}
    for d, e in rsp.items():
        by.setdefault(e["status"], []).append(d)
    pick: dict[str, dict] = {}
    for status, n in (("generally reliable", 90), ("no consensus", 40), ("generally unreliable", 70), ("deprecated", 60), ("blacklisted", 5)):
        for d in random.sample(sorted(by.get(status, [])), min(n, len(by.get(status, [])))):
            pick[d] = {"rsp": status, "lin": lin.get(d)}
    rest = [d for d in lin if d not in rsp]
    hi = [d for d in rest if lin[d] >= 0.85]
    lo = [d for d in rest if lin[d] <= 0.25]
    for d in random.sample(hi, 80) + random.sample(lo, 80):
        pick[d] = {"rsp": None, "lin": lin[d]}
    print("sample", len(pick))

    path = OUT / "sample.json"
    done = json.loads(path.read_text()) if path.exists() else {}
    todo = [d for d in pick if d not in done]
    sem = asyncio.Semaphore(8)

    async def look(d: str) -> None:
        async with sem:
            rec = dict(pick[d])
            r = await f.get(f"https://{d}/", accept="text/html,*/*;q=0.8")
            rec["fetched"] = bool(r.body and not r.error)
            rec["fetch_error"] = r.error if not rec["fetched"] else None
            if rec["fetched"]:
                found = analyse(r.body.decode("utf-8", "replace"))
                rec["transparency"] = len(found) / len(INDICATORS)
                rec["indicators"] = sorted(found)
            done[d] = rec

    for i in range(0, len(todo), 40):
        await asyncio.gather(*(look(d) for d in todo[i:i + 40]))
        path.write_text(json.dumps(done))
        print("transparency", min(i + 40, len(todo)), "/", len(todo), flush=True)

    # domain age
    async with httpx.AsyncClient(headers={"User-Agent": UA}) as http:
        today = dt.date.today()
        n = 0
        for d, rec in done.items():
            if "age" in rec:
                continue
            try:
                reg = await rdap_registration(http, registrable(d))
            except RateLimited:
                print("RDAP rate limited, stopping; rerun later"); break
            rec["age"] = (today - reg).days / 365.25 if reg else None
            n += 1
            if n % 25 == 0:
                path.write_text(json.dumps(done)); print("age", n, flush=True)
            await asyncio.sleep(0.5)
        path.write_text(json.dumps(done))
        wd = await wikidata_owners(http, sorted({registrable(d) for d in done}))
        for d, rec in done.items():
            w = wd.get(registrable(d)) or wd.get(d)
            rec["owner"] = bool(w and w["owners"]); rec["wikidata"] = bool(w)
        path.write_text(json.dumps(done))
    await f.close()
    print("done", len(done), "fetched ok", sum(1 for r in done.values() if r.get("fetched")))


asyncio.run(main())
