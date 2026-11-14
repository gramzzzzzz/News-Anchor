"""A last way to fetch a page: FlareSolverr (https://github.com/FlareSolverr/FlareSolverr), a small service that opens the page in a real
headless Chrome, waits for a JavaScript challenge (Cloudflare's "Just a moment...", DDoS-Guard and similar) to finish, and returns the page and
the cookies it earned. It is used only when the setting "Solve JavaScript challenges with FlareSolverr" is on, and only after the ordinary
request and curl-impersonate (if on) returned nothing usable. It does not solve CAPTCHAs that need a person, log in, or get past a paywall or
a block on the server's address.

A browser is slow (5 to 30 seconds) and heavy (a few hundred MB while a page is open), so this module is built to be used rarely:
  * at most ``parallel`` pages at once; a feed check that finds the solver busy is put at the end of its queue instead of waiting;
  * at most ``per_hour`` solves an hour, over all sites;
  * a site where a solve failed is left alone for 6 hours; a solver that cannot be reached, for 5 minutes;
  * the cookies a solve earned are kept (up to 30 minutes) and used for the next requests to that site, which then need no browser;
  * a page that loaded but is not recognisably a challenge is not sent here (``looks_challenged``)."""
from __future__ import annotations

import asyncio
import html as htmllib
import re
import time
from collections import deque
from dataclasses import dataclass, field

import httpx

MAX_BYTES = 6_000_000
HOST_PAUSE = 6 * 3600           # a site where a solve failed is not sent to the solver again for six hours
DOWN_PAUSE = 300                # the solver itself could not be reached
COOKIE_TTL = 1800               # the longest the cookies of one solve are used again
NO_REUSE_FOR = 24 * 3600        # a site that refused the earned cookies once (they are tied to the browser) is solved afresh each time
TRIGGER_STATUS = (202, 403, 429, 503)
_CHALLENGE = re.compile(
    r"just a moment|cf-browser-verification|challenge-platform|_cf_chl|cf-challenge|checking your browser|attention required|"
    r"enable javascript and cookies|ddos-guard|ddos protection by|please enable js|verify you are human|managed challenge|"
    r"sucuri website firewall|px-captcha|perimeterx|incapsula incident|are you a robot", re.I)
_PRE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.S | re.I)


def worth_trying(status: int | None, error: str | None) -> bool:
    """Whether a failed request may be a JavaScript challenge: the statuses such pages are served with. Not a timeout, a reset, a missing page."""
    return status in TRIGGER_STATUS


def looks_challenged(body: bytes | str | None) -> bool:
    """Whether a page that arrived (HTTP 200) is a challenge or interstitial rather than content: marker text in its first 40 KB, and short."""
    if not body:
        return False
    head = body[:40_000]
    text = head.decode("utf-8", "replace") if isinstance(head, bytes) else head
    return len(body) < 120_000 and bool(_CHALLENGE.search(text))


def unwrap(page: str) -> str:
    """Chrome shows an XML or plain-text response inside an HTML viewer (<pre> with the text escaped); the feed itself is what is wanted."""
    s = page.lstrip()
    if s[:6].lower() == "<html>" or s[:5].lower() == "<html":
        m = _PRE.search(s)
        if m:
            inner = htmllib.unescape(m.group(1)).lstrip()
            if inner[:5] in ("<?xml", "<rss ", "<feed") or inner[:4] == "<rss" or inner[:1] in "{[":
                return inner
    return page


@dataclass
class Solved:
    status: int | None = None
    body: bytes | None = None
    final_url: str | None = None
    error: str | None = None
    user_agent: str | None = None
    cookies: str | None = None          # a Cookie header for the next requests
    cookie_ttl: float = COOKIE_TTL
    busy: bool = False                  # not tried: every slot was in use and the caller does not wait
    headers: dict = field(default_factory=dict)


class Solver:
    def __init__(self):
        self._sem: asyncio.Semaphore | None = None
        self._size = 0
        self._stamps: deque[float] = deque()
        self._down_until = 0.0
        self.failed: dict[str, float] = {}
        self.stats = {"tried": 0, "worked": 0, "busy": 0, "capped": 0, "reused": 0}

    def _slots(self, n: int) -> asyncio.Semaphore:
        if self._sem is None or n != self._size:
            self._sem, self._size = asyncio.Semaphore(n), n
        return self._sem

    def paused(self, host: str) -> bool:
        return time.monotonic() - self.failed.get(host, -HOST_PAUSE) < HOST_PAUSE or time.monotonic() < self._down_until

    def _under_cap(self, per_hour: int) -> bool:
        now = time.monotonic()
        while self._stamps and now - self._stamps[0] > 3600:
            self._stamps.popleft()
        return len(self._stamps) < per_hour

    async def solve(self, client: httpx.AsyncClient, url: str, cfg: dict, host: str, *, wait: bool) -> Solved:
        """One page through FlareSolverr. cfg: url, timeout (seconds), parallel, per_hour. ``wait=False``: if every slot is in use, return at once
        with ``busy`` set (the caller tries again later)."""
        if self.paused(host):
            return Solved(error="Solver paused for this site")
        sem = self._slots(max(1, int(cfg["parallel"])))
        if not wait and sem.locked():
            self.stats["busy"] += 1
            return Solved(busy=True, error="Solver busy")
        async with sem:
            if not self._under_cap(int(cfg["per_hour"])):
                self.stats["capped"] += 1
                return Solved(error="Hourly solve limit reached")
            self._stamps.append(time.monotonic())
            self.stats["tried"] += 1
            timeout = float(cfg["timeout"])
            try:
                r = await client.post(cfg["url"], json={"cmd": "request.get", "url": url, "maxTimeout": int(timeout * 1000)}, timeout=timeout + 20)
                data = r.json()
            except (httpx.TransportError, ValueError) as e:
                if not isinstance(e, httpx.TimeoutException):
                    self._down_until = time.monotonic() + DOWN_PAUSE
                return Solved(error=f"FlareSolverr not reachable: {type(e).__name__}")
            sol = data.get("solution") or {}
            if data.get("status") != "ok" or not sol:
                self.failed[host] = time.monotonic()
                return Solved(error=f"FlareSolverr: {str(data.get('message') or 'no solution')[:100]}")
            page = unwrap(sol.get("response") or "")
            status = int(sol.get("status") or 0) or None
            if status != 200 or not page or looks_challenged(page) or len(page) > MAX_BYTES:
                self.failed[host] = time.monotonic()
                return Solved(status=status, error=f"Challenge not solved (HTTP {status})")
            self.failed.pop(host, None)
            self.stats["worked"] += 1
            cookies = sol.get("cookies") or []
            ttl = COOKIE_TTL
            clear = [float(c.get("expires") or 0) for c in cookies if c.get("name") == "cf_clearance" and float(c.get("expires") or 0) > 0]
            if clear:
                ttl = max(0.0, min(COOKIE_TTL, clear[0] - time.time() - 60))
            header = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name") and "value" in c) or None
            return Solved(200, page.encode("utf-8"), sol.get("url") or url, None, sol.get("userAgent"), header, ttl)
