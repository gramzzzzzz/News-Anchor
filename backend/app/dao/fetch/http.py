"""Polite async fetcher: one browser user agent for every request, robots.txt (obeyed as "NewsAnchor" when the switch is on; off, read only for its Sitemap lines), per-host spacing, conditional GET, size cap."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.dao.fetch import impersonate, solver
from app.dao.fetch.robots import robots_allows

log = logging.getLogger(__name__)
MAX_BYTES = 6_000_000
IMPERSONATE_PAUSE = 3600           # a host where curl-impersonate also failed is not tried with it again for an hour
ROBOTS_TTL = 24 * 3600
ROBOTS_TOKEN = "NewsAnchor"       # the name robots.txt groups are matched against; the User-Agent header itself is a browser's


@dataclass
class FetchResult:
    status: int | None
    body: bytes | None = None
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None
    final_url: str | None = None
    impersonated: bool = False          # fetched with curl-impersonate
    solved: bool = False                # fetched through FlareSolverr (a browser solved a JavaScript challenge), or with the cookies it earned
    transient: bool = False             # a timeout or reset on a first try: not retried with curl-impersonate, the caller tries again later

    @property
    def not_modified(self) -> bool:
        return self.status == 304


class Fetcher:
    def __init__(self, user_agent: str, timeout: float = 25, host_delay: float = 2.0, robots_token: str = ROBOTS_TOKEN):
        self.ua = user_agent
        self.token = robots_token
        self.timeout = timeout
        self.host_delay = host_delay
        self._client: httpx.AsyncClient | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._next: dict[str, float] = {}
        self._robots: dict[str, tuple[float, str | None]] = {}
        self.robots_on = lambda: True            # the container points this at the "fetch.robots" setting
        self.impersonate_profile = lambda: None  # ... and this at "fetch.impersonate": the curl-impersonate profile to use, or None for off
        self._imp_failed: dict[str, float] = {}
        self.imp_stats = {"tried": 0, "worked": 0}
        self.solver_config = lambda: None        # ... and this at the "fetch.flaresolverr*" settings: {url, timeout, parallel, per_hour}, or None for off
        self.solver = solver.Solver()
        self._clearance: dict[str, tuple[float, str, str | None]] = {}   # host -> (valid until, Cookie header, User-Agent) from the last solve
        self._no_reuse: dict[str, float] = {}

    def flush_robots(self) -> None:
        self._robots.clear()

    def configure(self, timeout: float, host_delay: float) -> None:
        self.timeout, self.host_delay = timeout, host_delay

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": self.ua, "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
                         "Accept-Language": "en"},
                follow_redirects=True, limits=httpx.Limits(max_connections=40, max_keepalive_connections=8, keepalive_expiry=20))

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _space(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._next.get(host, 0) - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next[host] = time.monotonic() + self.host_delay

    async def _read_robots(self, p) -> str | None:
        try:
            await self._space(p.netloc)
            r = await self._client.get(f"{p.scheme}://{p.netloc}/robots.txt", timeout=10)
            if r.status_code == 200 and len(r.content) < 500_000:
                return r.text
        except Exception:
            pass
        return None

    async def _robots_txt(self, url: str) -> str | None:
        """The site's robots.txt, kept in memory for a day. Switched off: nothing is kept and None is returned."""
        if not self.robots_on():
            self.flush_robots()
            return None
        p = urlsplit(url)
        host = f"{p.scheme}://{p.netloc}"
        now = time.monotonic()
        hit = self._robots.get(host)
        if not hit or now - hit[0] > ROBOTS_TTL:
            hit = (now, await self._read_robots(p))
            self._robots[host] = hit
        return hit[1]

    async def allowed(self, url: str) -> bool:
        """robots.txt by RFC 9309 (the group naming us, else *; the longest matching rule wins, Allow on a tie). Python's
        urllib.robotparser applies the first matching line instead and misreads common files: it refused news.ycombinator.com/rss
        because of "Disallow: /r?" (2 Nov 2026)."""
        if not self.robots_on():
            return True
        text = await self._robots_txt(url)
        return True if text is None else robots_allows(text, url, self.token)

    async def sitemaps(self, url: str) -> list[str]:
        """The sitemaps the site's robots.txt lists ("Sitemap: ..."). With the robots.txt switch on, from the cached file. Off, the file
        is still read for this one purpose, fresh each time and not kept; none of its rules are applied to anything."""
        text = await self._robots_txt(url) if self.robots_on() else await self._read_robots(urlsplit(url))
        return [ln.split(":", 1)[1].strip() for ln in (text or "").splitlines() if ln.lower().startswith("sitemap:") and "http" in ln]

    async def get(self, url: str, etag: str | None = None, last_modified: str | None = None, *, robots: bool = True, accept: str | None = None,
                  user_agent: str | None = None, impersonated: bool = False, defer_transient: bool = False,
                  solve: bool = False, solved: bool = False) -> FetchResult:
        """One GET. With the curl-impersonate setting on, a request that returned nothing usable (a refusal, a challenge, a reset, a timeout)
        is repeated once with curl-impersonate; ``impersonated=True`` goes there directly (the reader, when a page loaded but held no article).
        The original result is returned if the second try does not give a page either.
        ``solve=True`` (feeds and the reader only, never discovery or trust probes) adds a last step when the FlareSolverr setting is on: a
        refusal that may be a JavaScript challenge (HTTP 202, 403, 429, 503) is repeated through FlareSolverr; ``solved=True`` goes there
        directly (the caller got a 200 that is a challenge page). Cookies from an earlier solve are tried first, so a site needs the browser
        only now and then.
        ``defer_transient``: a timeout or reset is not retried at once; the result is marked ``transient`` and the caller asks again later
        (the feed poller puts such a feed at the end of its queue, so one slow response costs the others nothing). A solver whose slots are
        all in use is treated the same way."""
        assert self._client, "call start() first"
        if robots and self.robots_on() and not await self.allowed(url):
            return FetchResult(status=None, error="Disallowed by robots.txt")
        cfg = self.solver_config() if (solve or solved) else None
        host = urlsplit(url).netloc
        if cfg and not solved:
            hit = await self._reuse(url, host, accept)
            if hit is not None:
                return hit
        if cfg and solved:
            return await self._solved(url, host, cfg, wait=not defer_transient)
        profile = self.impersonate_profile()
        if profile and impersonated:
            alt = await self._impersonated(url, profile, etag, last_modified, accept)
            if alt is not None:
                return alt
        res = await self._plain(url, etag, last_modified, accept, user_agent)
        if defer_transient and res.status is None and impersonate.worth_trying(res.status, res.error):
            res.transient = True
            return res
        if profile and not impersonated and impersonate.worth_trying(res.status, res.error):
            alt = await self._impersonated(url, profile, etag, last_modified, accept)
            if alt is not None:
                return alt
        if cfg and solver.worth_trying(res.status, res.error):
            alt = await self._solved(url, host, cfg, wait=not defer_transient)
            if alt.solved:
                return alt
            if alt.transient:
                res.transient = True
        return res

    async def _reuse(self, url: str, host: str, accept: str | None) -> FetchResult | None:
        """The page with the cookies and User-Agent of an earlier solve for this site, or None (nothing kept, or the site refused them: then
        they are dropped and the site is solved afresh each time, since its clearance is tied to the browser)."""
        hit = self._clearance.get(host)
        if not hit:
            return None
        until, cookie, ua = hit
        if time.monotonic() > until:
            self._clearance.pop(host, None)
            return None
        res = await self._plain(url, None, None, accept, ua, cookie)
        if res.status == 200 and res.body and not solver.looks_challenged(res.body):
            self.solver.stats["reused"] += 1
            res.solved = True
            return res
        self._clearance.pop(host, None)
        self._no_reuse[host] = time.monotonic()
        return None

    async def _solved(self, url: str, host: str, cfg: dict, *, wait: bool) -> FetchResult:
        """The page through FlareSolverr. ``solved`` is set when it worked, ``transient`` when it was not tried because the solver was busy."""
        out = await self.solver.solve(self._client, url, cfg, host, wait=wait)
        if out.status == 200 and out.body:
            log.info("FlareSolverr fetched %s", url)
            if out.cookies and time.monotonic() - self._no_reuse.get(host, -solver.NO_REUSE_FOR) >= solver.NO_REUSE_FOR and out.cookie_ttl > 0:
                self._clearance[host] = (time.monotonic() + out.cookie_ttl, out.cookies, out.user_agent)
                if len(self._clearance) > 500:
                    self._clearance.pop(next(iter(self._clearance)))
            return FetchResult(200, out.body, final_url=out.final_url, solved=True)
        return FetchResult(out.status, error=out.error, transient=out.busy)

    async def _impersonated(self, url: str, profile: str, etag, last_modified, accept) -> FetchResult | None:
        """A page fetched with curl-impersonate, or None when it also gives none (or is unavailable, or failed on this host recently)."""
        host = urlsplit(url).netloc
        if time.monotonic() - self._imp_failed.get(host, -IMPERSONATE_PAUSE) < IMPERSONATE_PAUSE or not impersonate.available(profile):
            return None
        await self._space(host)
        self.imp_stats["tried"] += 1
        status, body, headers, final, err = await impersonate.fetch(url, profile, self.timeout, accept=accept, etag=etag, last_modified=last_modified)
        if err is None and status == 304:
            self.imp_stats["worked"] += 1
            return FetchResult(304, etag=etag, last_modified=last_modified, final_url=final, impersonated=True)
        if err is None and status == 200 and body:
            self.imp_stats["worked"] += 1
            log.info("curl-impersonate (%s) fetched %s", profile, url)
            return FetchResult(200, body, headers.get("etag"), headers.get("last-modified"), final_url=final, impersonated=True)
        self._imp_failed[host] = time.monotonic()
        return None

    async def _plain(self, url: str, etag: str | None, last_modified: str | None, accept: str | None, user_agent: str | None,
                     cookie: str | None = None) -> FetchResult:
        try:
            await self._space(urlsplit(url).netloc)
            h = {"Accept": accept} if accept else {}
            if user_agent:
                h["User-Agent"] = user_agent
                h["Accept-Language"] = "en-US,en;q=0.9"
            if cookie:
                h["Cookie"] = cookie
            if etag:
                h["If-None-Match"] = etag
            if last_modified:
                h["If-Modified-Since"] = last_modified
            async with self._client.stream("GET", url, headers=h, timeout=self.timeout) as r:
                if r.status_code == 304:
                    return FetchResult(304, etag=etag, last_modified=last_modified, final_url=str(r.url))
                if r.status_code != 200:
                    return FetchResult(r.status_code, error=f"HTTP {r.status_code}", final_url=str(r.url))
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > MAX_BYTES:
                        return FetchResult(200, error="Response too large", final_url=str(r.url))
                return FetchResult(200, bytes(buf), r.headers.get("etag"), r.headers.get("last-modified"), final_url=str(r.url))
        except httpx.TimeoutException:
            return FetchResult(None, error="Timed out")
        except httpx.HTTPError as e:
            return FetchResult(None, error=f"{type(e).__name__}: {str(e)[:120]}")
