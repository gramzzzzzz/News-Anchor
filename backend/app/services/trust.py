"""The trust crawler: gathers evidence about every source, scores it, and applies the owner's reliability floor.

Evidence (all stored with its origin and date, shown on the source card):
  lin2023, wikipedia_rsp   published rating datasets          (expert signals; a band needs one of them)
  corroboration            measured from our own stories
  transparency             the publisher's home page
  domain_age               RDAP registration date
  ownership                Wikidata owner / parent organisation
and, for sources without an expert rating (estimate(), daily and after each crawl; playbook 5.9):
  factcheck_labels         fact-checkers' labels collected by Burdisso et al. 2024 (expert; only where Lin et al. has no score)
  webgraph                 Burdisso et al. 2024's hyperlink-graph estimate, calibrated here against the expert-rated sources
  endorsement              rated outlets that link to the site or name it as their source (services/citations.py)
  family                   sister outlets (same owner) with an expert rating
  corroboration            stories it reported first that a Reliable or better outlet also reported in its own words
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import math
import re

import httpx

from app.dao import wikimedia
from app.dao.trust import burdisso, lin2023, wikipedia_rsp
from app.dao.trust.domains import SHARED_HOSTS, candidates, registrable
from app.dao.trust.external import RateLimited, rdap_registration, wikidata_owners
from app.dao.trust.transparency import INDICATORS, analyse
from app.services import trust_scoring as ts
from app.services.trust_scoring import Sig

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
RDAP_CACHE_DAYS = 30


class TrustService:
    def __init__(self, repo, settings, fetcher, bus, http_factory=None):
        self.repo, self.settings, self.fetcher, self.bus = repo, settings, fetcher, bus
        self.http_factory = http_factory or (lambda: httpx.AsyncClient(headers={"User-Agent": self.fetcher.ua}, follow_redirects=True))
        self._lock = asyncio.Lock()
        self.running = False
        self.retry_pause = 5.0
        self.rdap_pause = 0.4          # politeness towards the RDAP service

    # ---- configuration from settings -------------------------------------------------------------
    def thresholds(self) -> dict[str, float]:
        g = self.settings.get
        cuts = getattr(self, "_model_cuts", None)
        if cuts is None:
            m = self.repo.kv_get("trust_model") or {}
            cuts = self._model_cuts = (m.get("hi"), m.get("lo"))
        on = bool(g("trust.estimate"))
        return {"highly": g("trust.band_highly"), "reliable": g("trust.band_reliable"), "mixed": g("trust.band_mixed"),
                "estimate_hi": cuts[0] if on else None, "estimate_lo": cuts[1] if on else None}

    def lin_sig(self, pc1: float, raters: str, domain: str, today: str) -> Sig:
        """Lin et al.'s aggregate, trusted in proportion to the public rating sets that really rated the site (the rest is imputed)."""
        rs = [x for x in (raters or "").split(",") if x]
        f = lin2023.coverage_factor(len(rs))
        who = ("rated by " + " and ".join(rs)) if rs else "no public rating set rated this site: the value is imputed (or rests on NewsGuard alone)"
        return Sig("lin2023", pc1, self.base_weight("lin2023") * f, f"Aggregate quality {pc1:.2f} out of 1 (listed as {domain}); {who}.", lin2023.PAGE, today, f)

    def base_weight(self, signal: str) -> float:
        """With Trust › Learn the weights on (default): the weight learned from agreement (trust_scoring.calibrate). Otherwise the
        weight set in Settings, and none for the measured evidence (which then only feeds the estimate for unrated sources)."""
        if signal in ("estimate_model", "estimate_score"):
            return 0.0
        if self.settings.get("trust.calibrate"):
            cal = self._calibration()
            if signal in cal:
                return float(cal[signal]["weight"])
        if signal in ts.ESTIMATE:
            return 0.0
        try:
            return float(self.settings.get(f"trust.w.{signal}"))
        except Exception:                                    # noqa: BLE001 - a signal with no setting
            return 0.5

    def _calibration(self) -> dict:
        c = getattr(self, "_cal", None)
        if c is None:
            c = self._cal = (self.repo.kv_get("trust_calibration") or {}).get("signals", {})
        return c

    PRIORS = {"lin2023": 3.0, "wikipedia_rsp": 3.0, "factcheck_labels": 2.0, "webgraph": 1.5, "endorsement": 1.5, "family": 1.0,
              "corroboration": 1.0, "transparency": 1.0, "domain_age": 0.5, "ownership": 0.5}

    def learn_weights(self) -> dict:
        """Learn every signal's weight from how well it agrees with the others over all sources, store it, and return it."""
        rows: dict[str, dict[str, tuple[float, float]]] = {}
        for r in self.repo.signals_all():
            if r["signal"] in self.PRIORS:
                rows.setdefault(r["source_key"], {})[r["signal"]] = (float(r["value"]), float(r["factor"] if r["signal"] not in ts.ESTIMATE else 1.0))
        cal = ts.calibrate(list(rows.values()), self.PRIORS)
        self._cal = cal
        self.repo.kv_set("trust_calibration", {"at": dt.datetime.now(UTC).isoformat(), "signals": cal})
        return cal

    def method(self) -> dict:
        cal = self._calibration() if self.settings.get("trust.calibrate") else {}
        return {"signals": [{"key": k, "label": v["label"], "help": v["help"], "expert": v["expert"], "weight": self.base_weight(k),
                             "learned": cal.get(k)} for k, v in ts.SIGNALS.items() if k not in ("estimate_model", "estimate_score")],
                "learned": bool(cal),
                "thresholds": self.thresholds(), "prior": "A neutral prior of 0.5 with weight 1 keeps thin evidence from giving extreme scores.",
                "rules": ["A band normally needs an expert signal (expert rating aggregate, Wikipedia, or fact-checkers' labels).",
                          "Without one, a source gets an estimated band when there is enough measured evidence (who cites it, the hyperlink-graph estimate, sister outlets, pickup by rated outlets): never Highly reliable, Low only when that evidence is itself poor, always marked estimated, and never counted as a reliable outlet for Confirmed. Otherwise it is Unrated.",
                          "For a source with an expert rating, the measured evidence is shown but not counted.",
                          "One expert rating set alone never calls a source Low; that needs two to agree. Where they disagree the source card says so.",
                          "A band changes only when the score is clearly past a threshold (a small margin, setting), so weak signals cannot flip it back and forth.",
                          "Wikipedia 'deprecated' or 'blacklisted' blocks a source.", "Your override beats everything."],
                "floor": self.settings.get("sources.floor"), "show_unrated": self.settings.get("sources.show_unrated"),
                "estimate": self.repo.kv_get("trust_estimate")}

    # ---- evaluation (no network) -----------------------------------------------------------------
    def evaluate(self, sigs: list[Sig], override: str | None, prev_band: str | None = None) -> dict:
        # without an expert rating, only measured evidence makes the score: domain age and transparency pages prove little alone
        experts = any(s.signal in ts.EXPERT for s in sigs)
        th = self.thresholds()
        if experts or override:
            use = ts.on_expert_scale(sigs, self._calibration()) if self.settings.get("trust.calibrate") else sigs
            score, conf = ts.combine(use) if use else (None, None)
        else:
            m = {s.signal: s.value for s in sigs if s.signal in ("estimate_model", "estimate_score")}
            score = m.get("estimate_score")
            conf = abs(m["estimate_model"] - 0.5) * 2 if "estimate_model" in m else None
        band = ts.assign_band(score, conf, sigs, th, override=override)
        if not override:
            band = ts.hysteresis(band, score, prev_band, th, float(self.settings.get("trust.hysteresis")))
        g = self.settings.get
        visible = ts.is_visible(band, g("sources.floor"), g("sources.show_unrated"))
        weight = ts.rank_weight(band, score if band not in ("unrated", "blocked") else None, g("sources.unrated_weight"), g("sources.gamma"))
        return {"score": score, "confidence": conf, "band": band, "visible": visible, "weight": weight, "note": ts.explain(sigs, band),
                "basis": ts.basis(sigs, band, override)}

    def rescore(self) -> dict:
        """Recompute every source from stored evidence with the current weights, thresholds and floor. Used when the owner
        changes a trust or source setting or an override: no network."""
        by_src: dict[str, list[Sig]] = {}
        for r in self.repo.signals_all():
            by_src.setdefault(r["source_key"], []).append(
                Sig(r["signal"], r["value"], self.base_weight(r["signal"]) * r["factor"], r["detail"], r["evidence_url"], str(r["evidence_date"]) if r["evidence_date"] else None, r["factor"]))
        changed = 0
        for s in self.repo.sources_for_trust():
            sigs = by_src.get(s["key"], [])
            ev = self.evaluate(sigs, s["override_band"])   # no hysteresis: the owner just changed a rule and expects it applied exactly
            if self.repo.trust_apply(s["key"], None, ev, owner=None, qid=None, touch=False, reason="settings or override changed"):
                changed += 1
        n = self.repo.recompute_coverage(days=7)
        self.bus.publish({"type": "trust", "changed": changed})
        return {"sources_changed": changed, "stories_updated": n}

    # ---- the crawl -------------------------------------------------------------------------------
    async def refresh(self, job_id: int | None = None, partial: bool = False) -> dict:
        if self._lock.locked():
            return {"skipped": "already running"}
        async with self._lock:
            self.running = True
            await asyncio.to_thread(self.repo.kv_set, "trust_last_attempt", dt.datetime.now(UTC).isoformat())
            try:
                return await self._refresh(job_id, partial)
            except Exception as e:                              # record it; the scheduler retries in an hour, not in a loop
                log.exception("Trust crawl failed")
                if job_id:
                    await asyncio.to_thread(self.repo.job_update, job_id, state="failed", result={"error": f"{type(e).__name__}: {str(e)[:200]}"})
                return {"error": str(e)}
            finally:
                self.running = False

    async def _download(self, url: str, accept: str, user_agent: str | None = None):
        """A reference dataset, tried up to three times: a single timeout must not leave a first crawl without its most important evidence."""
        r = None
        for attempt in range(3):
            r = await self.fetcher.get(url, robots=False, accept=accept, user_agent=user_agent)
            if r.body and not r.error:
                return r
            await asyncio.sleep(self.retry_pause * (attempt + 1))
        return r

    async def _step(self, job_id: int | None, step: str, done: int = 0, total: int = 0) -> None:
        p = {"step": step, "done": done, "total": total}
        self.bus.publish({"type": "trust-progress", **p})
        if job_id:
            await asyncio.to_thread(self.repo.job_update, job_id, state="running", progress=p)

    async def _refresh(self, job_id: int | None, partial: bool = False) -> dict:
        t0 = dt.datetime.now(UTC)
        today = t0.date().isoformat()
        await self.fetcher.start()
        sources = await asyncio.to_thread(self.repo.sources_for_trust)
        if partial:
            sources = [x for x in sources if x["trust_updated_at"] is None]
        sigs: dict[str, list[Sig]] = {s["key"]: [] for s in sources}
        owners: dict[str, tuple[str, str]] = {}
        notes: list[str] = []

        # 1. published rating datasets
        await self._step(job_id, "Rating datasets")
        lin, rsp = {}, []
        if partial:                                             # a few new sources: reuse the stored datasets
            lin = {d: (x["value"], x["label"]) for d, x in (await asyncio.to_thread(self.repo.ref_scores_get, "lin2023")).items()}
        else:
            r = await self._download(lin2023.URL, "text/csv,*/*")
            if r.body and not r.error:
                full = lin2023.parse_full(r.body.decode("utf-8", "replace"))
                lin = {d: (v, ",".join(raters)) for d, (v, raters) in full.items()}
                await asyncio.to_thread(self.repo.ref_scores_replace, "lin2023", [(d, v, lab, "", lin2023.PAGE) for d, (v, lab) in lin.items()])
            else:
                notes.append(f"Lin et al. dataset unavailable ({r.error}); the stored copy is used")
                lin = {d: (x["value"], x["label"]) for d, x in (await asyncio.to_thread(self.repo.ref_scores_get, "lin2023")).items()}
            r = await self._download(wikipedia_rsp.API, "application/json", wikimedia.user_agent())
            if r.body and not r.error:
                page = json.loads(r.body)["parse"]["text"]
                rsp = wikipedia_rsp.parse(page)
                rows = [(d, ts.rsp_value(e.status), e.status, e.summary, e.url) for e in rsp for d in e.domains]
                await asyncio.to_thread(self.repo.ref_scores_replace, "rsp", rows)
            else:
                notes.append(f"Wikipedia list unavailable ({r.error}); the stored copy is used")
        rsp_idx = {d: x for d, x in (await asyncio.to_thread(self.repo.ref_scores_get, "rsp")).items()}
        for s in sources:
            for d in candidates(s["domain"]):
                if d in lin:
                    sigs[s["key"]].append(self.lin_sig(lin[d][0], lin[d][1], d, today))
                    break
            for d in candidates(s["domain"]):
                if d in rsp_idx:
                    x = rsp_idx[d]
                    sigs[s["key"]].append(Sig("wikipedia_rsp", x["value"], self.base_weight("wikipedia_rsp"), f"Wikipedia editors: {x['label']}. {x['detail'][:260]}", x["url"], today))
                    break

        # 2. domain age (cached for 30 days)
        regs = sorted({registrable(s["domain"]) for s in sources if registrable(s["domain"]) not in SHARED_HOSTS})
        cached = await asyncio.to_thread(self.repo.ref_scores_fresh, "rdap", regs, RDAP_CACHE_DAYS)
        todo = [d for d in regs if d not in cached]
        await self._step(job_id, "Domain registration dates", 0, len(todo))
        async with self.http_factory() as http:
            for i, d in enumerate(todo, 1):
                try:
                    reg = await rdap_registration(http, d)
                except RateLimited:
                    log.warning("RDAP keeps answering 429; skipping the rest of the registration dates until the next run")
                    break
                if reg:
                    years = (t0.date() - reg).days / 365.25
                    await asyncio.to_thread(self.repo.ref_score_upsert, "rdap", d, years, reg.isoformat(), "", f"https://rdap.org/domain/{d}")
                    cached[d] = {"value": years, "label": reg.isoformat(), "url": f"https://rdap.org/domain/{d}"}
                if i % 10 == 0:
                    await self._step(job_id, "Domain registration dates", i, len(todo))
                await asyncio.sleep(self.rdap_pause)
            # 3. ownership
            await self._step(job_id, "Ownership records")
            hosts = sorted({h for s in sources for h in candidates(s["domain"])})
            wd = await wikidata_owners(http, hosts)
        for s in sources:
            reg = registrable(s["domain"])
            if reg in cached:
                y = cached[reg]["value"]
                sigs[s["key"]].append(Sig("domain_age", min(0.9, 0.2 + 0.07 * y), self.base_weight("domain_age"),
                                          f"Registered {cached[reg]['label']} ({y:.0f} years ago)" if y >= 1 else f"Registered {cached[reg]['label']} (under a year ago)", cached[reg]["url"], today))
            for h in candidates(s["domain"]):
                if h in wd:
                    w = wd[h]
                    if w["owners"]:
                        sigs[s["key"]].append(Sig("ownership", 0.75, self.base_weight("ownership"), f"Wikidata ({w['label']}) lists: {w['owners'][:200]}",
                                                  f"https://www.wikidata.org/wiki/{w['qid']}", today))
                    owners[s["key"]] = (w["owners"][:200], w["qid"])
                    break

        # 4. transparency pages
        await self._step(job_id, "Transparency pages", 0, len(sources))
        sem = asyncio.Semaphore(4)
        done = 0

        async def look(s: dict) -> None:
            nonlocal done
            async with sem:
                r = await self.fetcher.get(f"https://{s['domain']}/", accept="text/html,*/*;q=0.8")
                if r.body and not r.error:
                    found = await asyncio.to_thread(analyse, r.body.decode("utf-8", "replace"))
                    miss = [lab for k, (lab, _rx) in INDICATORS.items() if k not in found]
                    sigs[s["key"]].append(Sig("transparency", 0.5 + 0.5 * len(found) / len(INDICATORS), self.base_weight("transparency"),
                                              "Found: " + (", ".join(INDICATORS[k][0] for k in found) or "none") + ". Not found on the home page: " + (", ".join(miss) or "none") + ". Counts in the site's favour only.",
                                              f"https://{s['domain']}/", today))
                done += 1
                if done % 10 == 0:
                    await self._step(job_id, "Transparency pages", done, len(sources))

        await asyncio.gather(*(look(s) for s in sources))

        # 5. measured evidence and the published estimates are kept as stored here, and recomputed by estimate() just after
        for r in await asyncio.to_thread(self.repo.signals_all):
            if r["source_key"] in sigs and r["signal"] in self.ESTIMATED:
                sigs[r["source_key"]].append(Sig(r["signal"], r["value"], self.base_weight(r["signal"]) * r["factor"], r["detail"], r["evidence_url"],
                                                 str(r["evidence_date"]) if r["evidence_date"] else None, r["factor"]))

        # 6. score and store
        await self._step(job_id, "Scoring")
        changed = 0
        for s in sources:
            ev = self.evaluate(sigs[s["key"]], s["override_band"], s["band"])
            if await asyncio.to_thread(self.repo.trust_apply, s["key"], sigs[s["key"]], ev, owner=owners.get(s["key"], (None, None))[0],
                                       qid=owners.get(s["key"], (None, None))[1], touch=True, reason="trust crawl"):
                changed += 1
        updated = await asyncio.to_thread(self.repo.recompute_coverage, 7)
        bands: dict[str, int] = {}
        for r in await asyncio.to_thread(self.repo.sources_for_trust):
            bands[r["band"]] = bands.get(r["band"], 0) + 1
        result = {"sources": len(sources), "bands": bands, "changed": changed, "stories_updated": updated, "notes": notes,
                  "seconds": round((dt.datetime.now(UTC) - t0).total_seconds(), 1)}
        if not partial:
            await asyncio.to_thread(self.repo.kv_set, "trust_last_run", {"at": t0.isoformat(), **result})
            try:
                result["estimate"] = await self.estimate()
            except Exception:                                    # noqa: BLE001 - the crawl's own evidence is already stored
                log.exception("Trust estimate failed")
        await asyncio.to_thread(self.repo.kv_set, "trust_dirty", False)
        if job_id:
            await asyncio.to_thread(self.repo.job_update, job_id, state="done", progress={"step": "Done"}, result=result)
        self.bus.publish({"type": "trust", **result})
        log.info("Trust crawl: %s", result)
        return result


    # ---- estimates for sources without an expert rating ---------------------------------------------
    ESTIMATED = ("factcheck_labels", "webgraph", "endorsement", "family", "corroboration", "estimate_model", "estimate_score")

    async def run_estimate(self, job_id: int | None = None) -> dict:
        if self._lock.locked():
            return {"skipped": "trust work already running"}
        async with self._lock:
            self.running = True
            await asyncio.to_thread(self.repo.kv_set, "trust_estimate_attempt", dt.datetime.now(UTC).isoformat())
            try:
                return await self.estimate(job_id)
            except Exception as e:
                log.exception("Trust estimate failed")
                if job_id:
                    await asyncio.to_thread(self.repo.job_update, job_id, state="failed", result={"error": f"{type(e).__name__}: {str(e)[:200]}"})
                return {"error": str(e)}
            finally:
                self.running = False

    async def estimate(self, job_id: int | None = None) -> dict:
        """Recompute the measured evidence and the published estimates for every source, then score again. No crawling of
        publishers: two small downloads (refreshed monthly) and queries of the stored stories and citations."""
        t0 = dt.datetime.now(UTC)
        today = t0.date().isoformat()
        await self.fetcher.start()
        notes = []
        if not await asyncio.to_thread(self.repo.ref_scores_labelled, "lin2023"):
            # the per-rater file (which public rating sets really rated each site) arrived after the last crawl: read it now
            r = await self._download(lin2023.URL, "text/csv,*/*")
            if r.body and not r.error:
                full = lin2023.parse_full(r.body.decode("utf-8", "replace"))
                await asyncio.to_thread(self.repo.ref_scores_replace, "lin2023", [(d, v, ",".join(rs), "", lin2023.PAGE) for d, (v, rs) in full.items()])
        for ds, url, parse in (("webgraph", burdisso.SCORES_URL, burdisso.parse_scores), ("factcheck", burdisso.LABELS_URL, burdisso.parse_labels)):
            have = await asyncio.to_thread(self.repo.ref_scores_fresh_count, ds, 30)
            if have:
                continue
            r = await self.fetcher.get(url, robots=False, accept="application/json,text/csv,*/*")     # once: re-read monthly, the stored copy serves meanwhile
            if r.body and not r.error:
                try:
                    data = parse(r.body)
                except ValueError as e:
                    notes.append(f"{ds} dataset unreadable ({e})")
                    continue
                if ds == "webgraph":
                    rows = [(d, v, "", "", burdisso.PAGE) for d, v in data.items()]
                else:
                    rows = [(d, float(lab), str(ng if ng is not None else ""), src, burdisso.PAGE) for d, (lab, ng, src) in data.items()]
                await asyncio.to_thread(self.repo.ref_scores_replace, ds, rows)
            else:
                notes.append(f"{ds} dataset unavailable ({r.error}); the stored copy is used")
        res = await asyncio.to_thread(self._estimate, today, notes)
        res["seconds"] = round((dt.datetime.now(UTC) - t0).total_seconds(), 1)
        await asyncio.to_thread(self.repo.kv_set, "trust_estimate", {"at": t0.isoformat(), **res})
        if job_id:
            await asyncio.to_thread(self.repo.job_update, job_id, state="done", result=res)
        self.bus.publish({"type": "trust", "estimated": res.get("estimated")})
        log.info("Trust estimate: %s", {k: v for k, v in res.items() if k != "eval"})
        return res

    def _estimate(self, today: str, notes: list[str]) -> dict:
        g = self.settings.get
        sources = self.repo.sources_for_trust()
        stored: dict[str, list[Sig]] = {}
        for r in self.repo.signals_all():
            if r["signal"] in self.ESTIMATED:
                continue
            stored.setdefault(r["source_key"], []).append(Sig(r["signal"], r["value"], self.base_weight(r["signal"]) * r["factor"], r["detail"],
                                                              r["evidence_url"], str(r["evidence_date"]) if r["evidence_date"] else None, r["factor"]))
        # Lin et al. again from the stored dataset, with its coverage (cheap; the crawl does the same weekly)
        lin = self.repo.ref_scores_get("lin2023")
        for s in sources:
            d = next((d for d in candidates(s["domain"] or "") if d in lin), None)
            lst = stored.setdefault(s["key"], [])
            lst[:] = [x for x in lst if x.signal != "lin2023"]
            if d:
                lst.append(self.lin_sig(lin[d]["value"], lin[d]["label"], d, today))
        web = {d: x["value"] for d, x in self.repo.ref_scores_get("webgraph").items()}
        labels = self.repo.ref_scores_get("factcheck")
        new: dict[str, list[Sig]] = {s["key"]: [] for s in sources}
        by_key = {s["key"]: s for s in sources}

        def match(domain: str, table) -> str | None:
            return next((d for d in candidates(domain or "") if d in table), None)

        # 1. fact-checkers' labels (Burdisso et al.'s collection), unless the same verdict is already counted: Wikipedia's when the
        # perennial-sources signal exists, MBFC's when Lin et al. includes a real MBFC rating of the site
        for s in sources:
            have = {x.signal: x for x in stored.get(s["key"], [])}
            d = match(s["domain"], labels)
            if d:
                x = labels[d]
                src_name = (x["detail"] or "").lower()
                if (src_name == "wikipedia" and "wikipedia_rsp" in have) or \
                        (src_name == "mbfc" and "lin2023" in have and "Media Bias/Fact Check" in (have["lin2023"].detail or "")):
                    continue
                ng = float(x["label"]) if x["label"] else None
                v = ng / 100 if ng is not None else {1: 0.8, 0: 0.5, -1: 0.15, -2: 0.05}.get(int(x["value"]), 0.5)
                lab = {1: "reliable", 0: "mixed", -1: "unreliable", -2: "deprecated"}.get(int(x["value"]), "?")
                new[s["key"]].append(Sig("factcheck_labels", v, self.base_weight("factcheck_labels"),
                                         f"Labelled {lab} by {x['detail'] or 'a fact-checker'}" + (f" (NewsGuard {ng:.0f}/100)" if ng is not None else "") + f" (listed as {d}).",
                                         burdisso.PAGE, today))

        # the seed: sources with an expert rating, and their scores
        def expert_score(key: str) -> float | None:
            ex = [x for x in stored.get(key, []) + new.get(key, []) if x.signal in ts.EXPERT]
            if not ex:
                return None
            sc, _c = ts.combine(stored.get(key, []) + new.get(key, []))
            return sc
        seed = {k: sc for k in by_key if (sc := expert_score(k)) is not None}
        for k, s in by_key.items():                             # your override counts as a rating too
            if s["override_band"] in ("highly_reliable", "reliable", "mixed", "low"):
                seed[k] = {"highly_reliable": 0.9, "reliable": 0.75, "mixed": 0.5, "low": 0.25}[s["override_band"]]

        # 2. the hyperlink-graph estimate, as published (-1 to 1, shown 0 to 1); the model learns what it is worth
        for s in sources:
            d = match(s["domain"], web)
            if d:
                v = (web[d] + 1) / 2
                f = 1.0 if (s.get("language") or "en") == "en" else 0.5      # the graph was built from English-language news only
                new[s["key"]].append(Sig("webgraph", v, self.base_weight("webgraph") * f, f"Hyperlink-graph estimate {web[d]:+.2f} on its -1 to 1 scale (listed as {d})."
                                         + ("" if f == 1.0 else " Half weight: the graph was built from English-language news, so it sees non-English sites less."),
                                         burdisso.PAGE, today, f))

        # 3. cited by rated outlets: links in their articles and credits ("..., X reported") in their headlines and summaries
        self._scan_mentions(sources, seed)
        dom_to_keys: dict[str, list[str]] = {}
        for s in sources:
            if s["domain"]:
                dom_to_keys.setdefault(registrable(s["domain"].lower().removeprefix("www.")), []).append(s["key"])
        cites: dict[str, dict[str, int]] = {}
        kinds: dict[tuple[str, str], set] = {}
        for r in self.repo.source_links_to(90):
            for k in dom_to_keys.get(r["to_domain"], []):
                if r["from_key"] == k:
                    continue
                cites.setdefault(k, {})[r["from_key"]] = cites.get(k, {}).get(r["from_key"], 0) + int(r["n"])
                kinds[(k, r["from_key"])] = set(r["kinds"])
        owner_of = {k: (s.get("owner") or "").strip().lower() for k, s in by_key.items()}
        reg_of = {k: registrable((s["domain"] or "").lower().removeprefix("www.")) for k, s in by_key.items()}
        for k, by in cites.items():
            raters = {c: n for c, n in by.items() if c in seed and reg_of.get(c) != reg_of.get(k)
                      and not (owner_of.get(c) and owner_of.get(c) == owner_of.get(k))}
            if len(raters) < 2:
                continue
            w = {c: 1 + math.log(n) for c, n in raters.items()}
            v = sum(w[c] * seed[c] for c in raters) / sum(w.values())
            f = min(1.0, len(raters) / 5)
            top = sorted(raters, key=lambda c: -raters[c])[:5]
            how = sorted({x for c in raters for x in kinds.get((k, c), set())})
            new[k].append(Sig("endorsement", v, self.base_weight("endorsement") * f,
                              f"Cited by {len(raters)} outlets with an expert rating (average {v:.2f}; " + ", ".join(by_key[c]["key"] for c in top)
                              + ("" if len(raters) <= 5 else ", ...") + "). How: " + ", ".join({"link": "links in their feeds", "page": "links in their articles", "mention": "named as a source"}.get(x, x) for x in how) + ".",
                              None, today, f))

        # 4. sister outlets with the same owner
        fam: dict[str, list[str]] = {}
        for k, o in owner_of.items():
            if o:
                fam.setdefault(o, []).append(k)
        for o, ks in fam.items():
            rated = [k for k in ks if k in seed]
            for k in ks:
                sis = [x for x in rated if x != k]
                if len(sis) >= 2:
                    v = sum(seed[x] for x in sis) / len(sis)
                    f = min(1.0, len(sis) / 4)
                    new[k].append(Sig("family", v, self.base_weight("family") * f,
                                      f"{len(sis)} other outlets owned by {by_key[k]['owner'][:80]} have an expert rating (average {v:.2f}).", None, today, f))

        # 5. pickup by rated outlets
        strong = [k for k, sc in seed.items() if sc >= float(g("trust.band_reliable"))]
        stats = self.repo.corroboration_rated(int(g("trust.corroboration_age_hours")), strong)
        for key, (n, ok) in stats.items():
            if key in new and n >= 10:
                rate = ok / n
                f = min(1.0, n / 50)
                new[key].append(Sig("corroboration", 0.5 + 0.5 * min(1.0, rate / 0.25), self.base_weight("corroboration") * f,
                                    f"{ok} of the {n} stories it reported first (at least {g('trust.corroboration_age_hours')} h ago) were also reported, in their own words, by an outlet rated Reliable or better ({rate:.0%}).",
                                    None, today, f))

        # 5b. learn every signal's weight from agreement, then put the learned weights on the evidence
        if g("trust.calibrate"):
            rows = [{x.signal: (x.value, x.factor if x.signal not in ts.ESTIMATE else 1.0) for x in stored.get(k, []) + new.get(k, []) if x.signal in self.PRIORS}
                    for k in by_key]
            self._cal = ts.calibrate([r for r in rows if r], self.PRIORS)
            self.repo.kv_set("trust_calibration", {"at": dt.datetime.now(UTC).isoformat(), "signals": self._cal})
            for lst in list(stored.values()) + list(new.values()):
                for x in lst:
                    if x.signal in self.PRIORS:
                        x.weight = self.base_weight(x.signal) * x.factor
            seed = {k: sc for k in by_key if (sc := expert_score(k)) is not None}
            for k, s in by_key.items():
                if s["override_band"] in ("highly_reliable", "reliable", "mixed", "low"):
                    seed[k] = {"highly_reliable": 0.9, "reliable": 0.75, "mixed": 0.5, "low": 0.25}[s["override_band"]]

        # 6. the model: trained on the sources experts rated, applied to the rest (services/trust_model.py)
        from app.services import trust_model as tm
        evid = {k: {x.signal: (x.value, x.factor) for x in stored.get(k, []) + new.get(k, []) if x.signal in tm.FEATURES} for k in by_key}
        # labels from the expert ratings alone: the stored bands now include the measured evidence, and learning them back from that
        # same evidence would be circular (it gave a cross-validated AUC of 0.95 that meant nothing)
        def expert_only(k: str) -> float | None:
            """The experts' verdict alone: the weighted mean of the expert signals, without the neutral prior (which, with a single
            thinly covered rating, would pull most sites under the Reliable line and teach the model that unrated means Mixed)."""
            ex = [x for x in stored.get(k, []) + new.get(k, []) if x.signal in ts.EXPERT and x.weight > 0]
            tw = sum(x.weight for x in ex)
            return sum(x.weight * x.value for x in ex) / tw if tw > 0 else None
        rel_cut = float(g("trust.band_reliable"))
        experts = [k for k in seed if by_key[k]["override_band"] is None and expert_only(k) is not None]
        X = [tm.features(evid[k]) for k in experts]
        y = [1 if expert_only(k) >= rel_cut else 0 for k in experts]
        model = {"rated": len(experts)}
        w = ws = None
        if len(experts) >= 100 and 0 < sum(y) < len(y):
            oof = tm.out_of_fold(X, y)
            model = tm.cuts(oof, y, float(g("trust.estimate_precision")), float(g("trust.estimate_precision_mixed")))
            if model["lo"] is not None and model["hi"] is not None and model["lo"] >= model["hi"]:
                model["lo"] = None
            w = tm.fit_logistic(X, y)
            ws = tm.fit_ridge(X, [seed[k] for k in experts])
            model["weights"] = {n: round(float(v), 3) for n, v in zip(tm.NAMES, w)}
        else:
            notes.append("Too few expert-rated sources to train the estimate")
        cut_hi, cut_lo = model.get("hi"), model.get("lo")
        self.repo.kv_set("trust_model", {"hi": cut_hi, "lo": cut_lo})
        self._model_cuts = (cut_hi, cut_lo)

        # 7. score and store. The measured evidence is shown on every card with weight 0 (the model combines it); where an expert
        # rating exists it decides. Without one, the model's probability and expert-scale score are stored as two more rows.
        changed = estimated = 0
        for s in sources:
            k = s["key"]
            mine = [Sig(x.signal, x.value, self.base_weight(x.signal) * x.factor, x.detail, x.url, x.date, x.factor) for x in new[k]]
            if w is not None and k not in seed and any(x in evid[k] for x in tm.NETWORK):
                x = tm.features(evid[k])
                pr = tm.prob(w, x)
                sc = max(0.05, min(0.95, sum(a * b for a, b in zip(ws, x))))
                verdict = ("Reliable" if cut_hi is not None and pr >= cut_hi else "Mixed" if cut_lo is not None and pr <= cut_lo else "no band (not sure enough)")
                mine.append(Sig("estimate_model", round(pr, 4), 0.0,
                                f"Chance an expert would rate it Reliable or better: {pr:.0%}, from the evidence above (a model trained on {len(experts)} expert-rated sources). "
                                f"Estimated band: {verdict}. Score on the expert scale: {sc:.2f}.", None, today, 0.0))
                mine.append(Sig("estimate_score", round(sc, 4), 0.0, "The model's score on the expert scale, used for ranking.", None, today, 0.0))
            sigs = stored.get(k, []) + mine
            e = self.evaluate(sigs, s["override_band"], s["band"])
            estimated += e["basis"] == "estimated"
            if self.repo.trust_apply(k, sigs, e, owner=None, qid=None, touch=False, reason="trust estimate"):
                changed += 1
        self.repo.recompute_coverage(7)
        self.repo.source_links_prune(120)
        return {"sources": len(sources), "seed": len(seed), "estimated": estimated, "changed": changed,
                "weights": {k: v["weight"] for k, v in self._calibration().items()}, "with": {k: sum(1 for v in new.values() if any(x.signal == k for x in v)) for k in self.ESTIMATED}, "model": model, "notes": notes}

    _ATTR_BEFORE = re.compile(r"(?:according to|told|via|citing|cited by|quoted by|reported by|said to|in an interview with|speaking to|source:)\s+(?:the\s+)?$", re.I)
    _ATTR_AFTER = re.compile(r"^[\s'’\"”),]*(?:reported|reports|said|says|quoted|cited|has learned|learned|revealed|first reported|reporting)\b", re.I)

    def _scan_mentions(self, sources: list[dict], seed: dict[str, float]) -> int:
        """Rated outlets naming another outlet as their source ("according to Tasnim", "..., Kyiv Independent reported"), over 14 days.
        Only distinctive names (two or more words, or one rare word) and only with an attribution phrase next to them."""
        from app.services.anchors import GENERIC, _common
        common = _common()
        names: dict[str, list[tuple[tuple[str, ...], str]]] = {}
        for s in sources:
            if s["key"] in seed or s.get("type") == "social" or not s.get("name"):
                continue
            toks = re.findall(r"[\w'’.&-]+", s["name"])
            if toks and toks[0].lower() == "the":
                toks = toks[1:]
            if not toks or len(" ".join(toks)) < 5:
                continue
            low = [t.lower() for t in toks]
            if len(toks) == 1 and (low[0] in common or low[0] in GENERIC or not toks[0][:1].isupper()):
                continue
            if len(toks) >= 2 and all(t in common or t in GENERIC for t in low):
                continue
            names.setdefault(toks[0], []).append((tuple(toks), s["domain"]))
        found: dict[tuple[str, str], int] = {}
        doms = {s["key"]: (s["domain"] or "").lower().removeprefix("www.") for s in sources}
        for r in self.repo.rated_article_texts(list(seed), 14):
            text = f"{r['title']}. {r['lead'] or ''}"
            words = list(re.finditer(r"[\w'’.&-]+", text))
            for i, m in enumerate(words):
                for toks, dom in names.get(m.group(0), ()):
                    if tuple(w.group(0) for w in words[i:i + len(toks)]) != toks:
                        continue
                    start, end = m.start(), words[i + len(toks) - 1].end()
                    if self._ATTR_BEFORE.search(text[max(0, start - 40):start]) or self._ATTR_AFTER.search(text[end:end + 30]):
                        d = registrable((dom or "").lower().removeprefix("www."))
                        if d and d != registrable(doms.get(r["source_key"], "")):
                            found[(r["source_key"], d)] = found.get((r["source_key"], d), 0) + 1
        self.repo.source_links_replace("mention", [(a, d, n) for (a, d), n in found.items()])
        return len(found)
