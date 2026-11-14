"""Social posts as sources. What you list in Settings, Social (accounts, hashtags, subreddits, channels, your own timelines) becomes
sources of type 'social', one per account or timeline, each polled at the news refresh interval like any feed.

Posts are always marked social. They never reach Top or Trending on their own: a story needs a non-social outlet too.
Feeds that are plain RSS (Mastodon accounts and hashtags, YouTube channels, subreddits without a login) are read by the normal
feed reader; the rest (Bluesky, Mastodon home, Reddit with a login, X) are read here, by kind."""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import re
import secrets

import httpx

from app.dao.social import SocialError, bluesky, mastodon, reddit, wikidata_accounts, x, youtube

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
API_KINDS = ("bluesky", "mastodon", "reddit", "x", "youtube")
HOSTS = {"bluesky": "bsky.app", "mastodon": "mastodon.social", "reddit": "reddit.com", "x": "x.com", "youtube": "youtube.com"}


def _lines(v: str) -> list[str]:
    return [s.strip() for s in re.split(r"[\n,]+", v or "") if s.strip() and not s.strip().startswith("#")]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48] or hashlib.md5(s.encode()).hexdigest()[:10]


class SocialService:
    def __init__(self, repo, settings, user_agent: str, http_factory=None):
        self.repo, self.settings = repo, settings
        self.http = http_factory or (lambda: httpx.AsyncClient(headers={"User-Agent": user_agent}, follow_redirects=True))
        self._bsky: dict | None = None
        self._reddit: dict | None = None
        self._x_lock = asyncio.Lock()
        self._sig = ""

    def g(self, k: str):
        return self.settings.get(k)

    # ---- what to follow ----------------------------------------------------------------------------
    async def specs(self) -> list[dict]:
        """Every social feed the settings ask for: {key, name, platform, kind, url, country}."""
        out: list[dict] = []
        if not self.g("social.enabled"):
            return out

        def add(key, name, platform, kind, url, country="XX"):
            if url:
                out.append({"key": key[:64], "name": name[:120], "platform": platform, "kind": kind, "url": url, "country": country or "XX"})

        bs_login = bool(self.g("social.bluesky.handle") and self.g("social.bluesky.app_password"))
        if bs_login and self.g("social.bluesky.timeline"):
            add("social-bluesky-timeline", "Your Bluesky timeline", "bluesky", "bluesky", "bluesky:timeline")
        for h in _lines(self.g("social.bluesky.accounts")):
            add(f"social-bluesky-{_slug(h)}", f"@{h.lstrip('@')} on Bluesky", "bluesky", "bluesky", f"bluesky:author:{h.lstrip('@')}")
        if bs_login:
            for q in _lines(self.g("social.bluesky.searches")):
                add(f"social-bluesky-search-{_slug(q)}", f"Bluesky search: {q}", "bluesky", "bluesky", f"bluesky:search:{q}")
        inst = (self.g("social.mastodon.instance") or "").strip().removeprefix("https://").strip("/")
        if inst and self.g("social.mastodon.token"):
            add("social-mastodon-home", f"Your Mastodon home timeline ({inst})", "mastodon", "mastodon", "mastodon:home")
        for a in _lines(self.g("social.mastodon.accounts")):
            add(f"social-mastodon-{_slug(a)}", f"{a} on Mastodon", "mastodon", "rss", mastodon.account_rss(a))
        for t in _lines(self.g("social.mastodon.tags")):
            add(f"social-mastodon-tag-{_slug(t)}", f"#{t.rsplit('/', 1)[-1].lstrip('#')} on Mastodon", "mastodon", "rss", mastodon.tag_rss(t, inst or "mastodon.social"))
        ids = self.repo.kv_get("yt_ids") or {}
        todo = [c for c in _lines(self.g("social.youtube.channels")) if c not in ids]
        if todo:
            async with self.http() as h:
                for c in todo[:40]:
                    ids[c] = await youtube.resolve(h, c) or ""
            self.repo.kv_set("yt_ids", ids)
        for c in _lines(self.g("social.youtube.channels")) if self.g("social.youtube.api_key") else []:
            if ids.get(c):
                add(f"social-youtube-{ids[c].lower()}", f"{c} on YouTube", "youtube", "youtube", f"youtube:{ids[c]}")
        rd_login = all(self.g(k) for k in ("social.reddit.client_id", "social.reddit.client_secret", "social.reddit.username", "social.reddit.password"))
        if rd_login and self.g("social.reddit.home"):
            add("social-reddit-home", "Your Reddit front page", "reddit", "reddit", "reddit:home")
        for s in _lines(self.g("social.reddit.subreddits")):
            n = reddit.sub_name(s)
            add(f"social-reddit-{_slug(n)}", f"r/{n} on Reddit", "reddit", "reddit" if rd_login else "rss", f"reddit:r/{n}" if rd_login else reddit.rss_url(n))
        xt = self.repo.kv_get("x_tokens") or {}
        if xt.get("access") and self.g("social.x.home"):
            add("social-x-home", f"Your X home timeline (@{xt.get('username', '')})", "x", "x", "x:home")
        if xt.get("access") or self.g("social.x.bearer"):
            for u in _lines(self.g("social.x.accounts")):
                add(f"social-x-{_slug(u)}", f"@{u.lstrip('@')} on X", "x", "x", f"x:user:{u.lstrip('@')}")
        if self.g("social.official_accounts"):
            n = 0
            for dom, o in sorted((self.repo.kv_get("official_accounts") or {}).items()):
                if n >= int(self.g("social.official_max")):
                    break
                src = o.get("source") or {}
                if o.get("youtube") and self.g("social.youtube.api_key"):
                    add(f"social-yt-{src.get('key', _slug(dom))}", f"{src.get('name', dom)} on YouTube", "youtube", "youtube", f"youtube:{o['youtube']}", src.get("country"))
                    n += 1
                if o.get("bluesky"):
                    add(f"social-bsky-{src.get('key', _slug(dom))}", f"{src.get('name', dom)} on Bluesky", "bluesky", "bluesky", f"bluesky:author:{o['bluesky']}", src.get("country"))
                    n += 1
                if o.get("mastodon") and mastodon.account_rss(o["mastodon"]):
                    add(f"social-masto-{src.get('key', _slug(dom))}", f"{src.get('name', dom)} on Mastodon", "mastodon", "rss", mastodon.account_rss(o["mastodon"]), src.get("country"))
                    n += 1
        seen, uniq = set(), []
        for s in out:
            if s["url"] not in seen:
                seen.add(s["url"])
                uniq.append(s)
        return uniq

    def _signature(self) -> str:
        keys = sorted(k for k in self.settings.defs if k.startswith("social."))
        vals = [str(self.g(k)) for k in keys] + [str(bool((self.repo.kv_get("x_tokens") or {}).get("access"))), str(len(self.repo.kv_get("official_accounts") or {}))]
        return hashlib.md5("|".join(vals).encode()).hexdigest()

    async def sync(self, force: bool = False) -> dict:
        """Make the registry match the settings: add or revive the feeds asked for, retire the rest. Cheap when nothing changed."""
        sig = await asyncio.to_thread(self._signature)
        if sig == self._sig and not force:
            return {"unchanged": True}
        specs = await self.specs()
        interval = int(self.g("refresh.news_minutes")) * 60
        for s in specs:
            await asyncio.to_thread(self.repo.social_upsert, {**s, "domain": HOSTS[s["platform"]]}, interval)
        retired = await asyncio.to_thread(self.repo.social_retire_except, [s["url"] for s in specs])
        self._sig = sig
        return {"feeds": len(specs), "retired": retired}

    # ---- official accounts from Wikidata ------------------------------------------------------------
    async def refresh_official(self) -> dict:
        """Look up the official accounts of the best-rated and curated news sources (monthly)."""
        rows = await asyncio.to_thread(self.repo.q, """SELECT key, name, domain, country FROM sources WHERE type <> 'social' AND blocked = false
                                                       AND status IN ('active', 'degraded') AND (curated OR band IN ('reliable', 'highly_reliable'))
                                                       ORDER BY curated DESC, score DESC NULLS LAST LIMIT 400""")
        found: dict[str, dict] = {}
        async with self.http() as h:
            for i in range(0, len(rows), 40):
                batch = rows[i:i + 40]
                try:
                    res = await wikidata_accounts.accounts(h, [r["domain"].removeprefix("www.") for r in batch])
                except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as e:
                    log.info("Wikidata accounts lookup failed: %s", e)
                    continue
                by = {r["domain"].removeprefix("www."): r for r in batch}
                for dom, o in res.items():
                    if dom in by:
                        found[dom] = {**o, "source": {k: by[dom][k] for k in ("key", "name", "country")}}
                await asyncio.sleep(1)
        await asyncio.to_thread(self.repo.kv_set, "official_accounts", found)
        await asyncio.to_thread(self.repo.kv_set, "official_accounts_at", dt.datetime.now(UTC).isoformat())
        return {"sources_checked": len(rows), "with_accounts": len(found), **(await self.sync(force=True))}

    def official_due(self) -> bool:
        if not (self.g("social.enabled") and self.g("social.official_accounts")):
            return False
        at = self.repo.kv_get("official_accounts_at")
        return not at or dt.datetime.now(UTC) - dt.datetime.fromisoformat(at) > dt.timedelta(days=30)

    # ---- reading ----------------------------------------------------------------------------------
    async def fetch(self, f: dict) -> tuple[list[dict], bool, str | None]:
        """(posts, ok, error) for one API feed."""
        url = f["url"]
        try:
            async with self.http() as h:
                if url.startswith("bluesky:"):
                    return await self._bluesky(h, url), True, None
                if url == "mastodon:home":
                    return await mastodon.home(h, self.g("social.mastodon.instance"), self.g("social.mastodon.token")), True, None
                if url.startswith("reddit:"):
                    return await self._reddit_read(h, url), True, None
                if url.startswith("youtube:"):
                    return await youtube.uploads(h, self.g("social.youtube.api_key"), url.split(":", 1)[1]), True, None
                if url.startswith("x:"):
                    posts, note = await self._x_read(h, url)
                    return posts, True, note
        except SocialError as e:
            return [], False, str(e)[:200]
        except httpx.HTTPError as e:
            return [], False, f"{type(e).__name__}"
        return [], False, "Unknown social feed"

    async def _bluesky(self, h: httpx.AsyncClient, url: str) -> list[dict]:
        if url.startswith("bluesky:author:"):
            return await bluesky.author_feed(h, url.split(":", 2)[2])
        for attempt in range(2):
            if not self._bsky or attempt:
                self._bsky = await bluesky.session(h, self.g("social.bluesky.handle"), self.g("social.bluesky.app_password"))
            try:
                if url == "bluesky:timeline":
                    return await bluesky.timeline(h, self._bsky["accessJwt"])
                return await bluesky.search(h, self._bsky["accessJwt"], url.split(":", 2)[2])
            except SocialError as e:
                if str(e) != "expired" or attempt:
                    raise
        return []

    async def _reddit_read(self, h: httpx.AsyncClient, url: str) -> list[dict]:
        user = self.g("social.reddit.username")
        for attempt in range(2):
            if not self._reddit or self._reddit["until"] < dt.datetime.now(UTC) or attempt:
                t = await reddit.token(h, self.g("social.reddit.client_id"), self.g("social.reddit.client_secret"), user, self.g("social.reddit.password"))
                self._reddit = {"access": t["access_token"], "until": dt.datetime.now(UTC) + dt.timedelta(seconds=int(t.get("expires_in", 3600)) - 60)}
            try:
                return await reddit.listing(h, self._reddit["access"], "/best" if url == "reddit:home" else "/" + url.split(":", 1)[1] + "/hot", user)
            except SocialError as e:
                if str(e) != "expired" or attempt:
                    raise
        return []

    # ---- X: login, budget, reading -------------------------------------------------------------------
    def x_budget(self) -> dict:
        month = dt.date.today().strftime("%Y-%m")
        used = self.repo.kv_get("x_reads") or {}
        n = used.get("n", 0) if used.get("month") == month else 0
        cap = int(self.g("social.x.monthly_reads"))
        return {"month": month, "used": n, "budget": cap, "left": max(0, cap - n), "cost_usd": round(n * 0.005, 2)}

    def _x_count(self, n: int) -> None:
        b = self.x_budget()
        self.repo.kv_set("x_reads", {"month": b["month"], "n": b["used"] + n})

    def x_login(self, redirect_uri: str) -> str:
        cid = (self.g("social.x.client_id") or "").strip()
        if not cid:
            raise SocialError("Add your X app's client ID first (Settings, Social)")
        verifier, challenge = x.pkce()
        state = secrets.token_urlsafe(24)
        self.repo.kv_set("x_pkce", {"state": state, "verifier": verifier, "redirect": redirect_uri, "at": dt.datetime.now(UTC).isoformat()})
        return x.authorize_url(cid, redirect_uri, state, challenge)

    async def x_callback(self, code: str, state: str) -> dict:
        p = self.repo.kv_get("x_pkce") or {}
        if not p or not secrets.compare_digest(p.get("state", ""), state or "") or dt.datetime.now(UTC) - dt.datetime.fromisoformat(p["at"]) > dt.timedelta(minutes=15):
            raise SocialError("This login link has expired or was not started here. Start again from Settings, Social.")
        self.repo.kv_set("x_pkce", None)
        async with self.http() as h:
            t = await x.exchange(h, self.g("social.x.client_id").strip(), self.g("social.x.client_secret") or "", code, p["redirect"], p["verifier"])
            who = await x.me(h, t["access_token"])
        self.repo.kv_set("x_tokens", {"access": t["access_token"], "refresh": t.get("refresh_token"), "uid": who["id"], "username": who.get("username", ""),
                                      "until": (dt.datetime.now(UTC) + dt.timedelta(seconds=int(t.get("expires_in", 7200)) - 60)).isoformat()})
        self.repo.audit("social.x", f"connected @{who.get('username', '')}")
        await self.sync(force=True)
        return {"username": who.get("username", "")}

    def x_logout(self) -> None:
        self.repo.kv_set("x_tokens", None)
        self.repo.audit("social.x", "disconnected")

    async def _x_token(self, h: httpx.AsyncClient) -> str:
        async with self._x_lock:
            t = self.repo.kv_get("x_tokens") or {}
            if t.get("access") and dt.datetime.fromisoformat(t["until"]) > dt.datetime.now(UTC):
                return t["access"]
            if t.get("refresh"):
                n = await x.refresh(h, self.g("social.x.client_id").strip(), self.g("social.x.client_secret") or "", t["refresh"])
                t.update(access=n["access_token"], refresh=n.get("refresh_token", t["refresh"]),
                         until=(dt.datetime.now(UTC) + dt.timedelta(seconds=int(n.get("expires_in", 7200)) - 60)).isoformat())
                self.repo.kv_set("x_tokens", t)
                return t["access"]
            if self.g("social.x.bearer"):
                return self.g("social.x.bearer")
            raise SocialError("Not logged in to X")

    async def _x_read(self, h: httpx.AsyncClient, url: str) -> tuple[list[dict], str | None]:
        b = self.x_budget()
        if b["left"] <= 0:
            return [], f"Monthly X budget of {b['budget']} posts used up; reading resumes next month or when you raise it."
        since_key = f"x_since:{url}"
        since = self.repo.kv_get(since_key)
        per = min(b["left"], int(self.g("social.x.per_poll")))
        if url == "x:home":
            tok = await self._x_token(h)
            posts, newest, n = await x.home(h, tok, (self.repo.kv_get("x_tokens") or {})["uid"], per, since)
        else:
            name = url.split(":", 2)[2]
            tok = await self._x_token(h) if (self.repo.kv_get("x_tokens") or {}).get("access") else self.g("social.x.bearer")
            ids = self.repo.kv_get("x_user_ids") or {}
            if name not in ids:
                ids[name] = await x.user_id(h, tok, name)
                self.repo.kv_set("x_user_ids", ids)
            posts, newest, n = await x.user_posts(h, tok, ids[name], max(5, per), since)
        self._x_count(n)
        if newest:
            self.repo.kv_set(since_key, newest)
        return posts, None

    # ---- status -----------------------------------------------------------------------------------
    def status(self) -> dict:
        feeds = self.repo.social_feeds()
        by: dict[str, dict] = {}
        for f in feeds:
            p = f["url"].split(":", 1)[0] if f["kind"] in API_KINDS else ("reddit" if "reddit.com" in f["url"] else "mastodon")
            d = by.setdefault(p, {"feeds": 0, "working": 0, "posts": 0, "errors": []})
            d["feeds"] += 1
            d["working"] += 1 if f["last_ok_at"] and f["state"] == "active" else 0
            d["posts"] += f["items_total"] or 0
            if f["last_error"] and len(d["errors"]) < 3:
                d["errors"].append({"name": f["source_name"], "error": f["last_error"]})
        xt = self.repo.kv_get("x_tokens") or {}
        return {"enabled": bool(self.g("social.enabled")), "platforms": by, "x": {"connected": bool(xt.get("access")), "username": xt.get("username", ""), **self.x_budget(),
                "client_id": bool(self.g("social.x.client_id")), "bearer": bool(self.g("social.x.bearer"))},
                "youtube_key": bool(self.g("social.youtube.api_key")),
                "bluesky_login": bool(self.g("social.bluesky.handle") and self.g("social.bluesky.app_password")),
                "reddit_login": all(self.g(k) for k in ("social.reddit.client_id", "social.reddit.client_secret", "social.reddit.username", "social.reddit.password")),
                "reddit_rss_ends": "2026-11-13", "official_accounts": len(self.repo.kv_get("official_accounts") or {}),
                "official_at": self.repo.kv_get("official_accounts_at")}
