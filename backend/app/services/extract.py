"""Article text for the reading pane, fetched on demand (never in bulk).

What it does, in order, and stops at the first that gives a full article:
  1. the page as any reader's browser would get it (publisher's robots.txt obeyed), cleaned with trafilatura;
  2. text the publisher itself put in the page for search engines and apps: schema.org articleBody, embedded page data, the AMP version;
  3. the Internet Archive's own public copy (setting), labelled as an archive copy.
It does not impersonate a search engine, log in, or defeat a paywall. When only a summary is available it says so, and the AI
reading is told the same, so it never pretends to have read the rest."""
from __future__ import annotations

import asyncio
import hashlib
import html as htmllib
import json
import logging
import re
from urllib.parse import quote

from app.dao.fetch import solver as flare

log = logging.getLogger(__name__)
FULL_WORDS = 180
PAYWALL = re.compile(r"subscribe (now )?to (continue|read)|already a subscriber|sign in to (read|continue)|log in to read|this content is for (subscribers|members)|unlock (this|the full) (article|story)|become a member to read", re.I)
_LD = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
_NEXT = re.compile(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.S | re.I)
_AMP = re.compile(r'<link[^>]+rel=["\']amphtml["\'][^>]+href=["\']([^"\']+)["\']', re.I)
# page state some publishing systems embed as script variables (Arc XP's Fusion, Redux/Apollo stores)
_STATE = re.compile(r"(?:Fusion\.globalContent|Fusion\.contentCache|window\.__PRELOADED_STATE__|window\.__INITIAL_STATE__|window\.__APOLLO_STATE__)\s*=\s*")
_BLOCK_TYPES = ("text", "paragraph", "p", "body-text", "core/paragraph")
_BLOCK_KEYS = ("content", "text", "html", "value")
_LINK = re.compile(r"Links to .*?(https?://\S+)\s*$")
_EN_STOP = frozenset("the of and to in a is that for on with as was at by it from he said his be are have has an they not but this who were".split())


def words(text: str) -> int:
    return len(text.split())


def to_paragraphs(text: str) -> list[str]:
    parts = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n|\n", text)]
    return [p for p in parts if len(p) > 25]


def _walk_for_body(o, found: list[str], depth: int = 0) -> None:
    """Article bodies in embedded data: a long text field, or a list of paragraph blocks ({"type": "text", "content": ...}, as Arc XP's
    content_elements and many headless systems store them)."""
    if depth > 14:
        return
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("articleBody", "body", "content", "text", "bodyHtml", "articleText", "storyText") and isinstance(v, str) and len(v) > 400:
                found.append(v)
            else:
                _walk_for_body(v, found, depth + 1)
    elif isinstance(o, list):
        blocks = [b for b in o[:400] if isinstance(b, dict) and str(b.get("type", "")).lower() in _BLOCK_TYPES]
        if len(blocks) >= 3:
            parts = [next((b[k] for k in _BLOCK_KEYS if isinstance(b.get(k), str)), "") for b in blocks]
            joined = "\n\n".join(p for p in parts if p)
            if len(joined) > 400:
                found.append(joined)
        for v in o[:400]:
            _walk_for_body(v, found, depth + 1)


def _loads(text: str):
    try:
        return json.loads(text, strict=False)
    except ValueError:
        return None


def strip_tags(s: str) -> str:
    s = re.sub(r"</(p|div|li|h\d|br)>|<br\s*/?>", "\n\n", s, flags=re.I)
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", s))


def structured_text(page: str) -> str:
    """Longest article body the page declares in structured data or embedded state."""
    found: list[str] = []
    for m in _LD.finditer(page):
        o = _loads(m.group(1).strip())
        if o is not None:
            _walk_for_body(o, found)
    m = _NEXT.search(page)
    if m:
        o = _loads(m.group(1))
        if o is not None:
            _walk_for_body(o, found)
    dec = json.JSONDecoder(strict=False)
    for m in _STATE.finditer(page):
        try:
            o, _end = dec.raw_decode(page, m.end())
        except ValueError:
            continue
        _walk_for_body(o, found)
    best = max((strip_tags(f) for f in found), key=words, default="")
    return best.strip()


def readable(text: str) -> bool:
    """Prose rather than scrambled or encoded text (some publishers ship a subscriber's article scrambled in the page; that is not
    read). For English, at least one word in eight is a common short word."""
    toks = re.findall(r"[^\W\d_]+", text.lower())
    if len(toks) < 40:
        return True
    if sum(1 for t in toks if t.isascii()) < len(toks) * 0.8:
        return True                                          # another script: no English check
    return sum(1 for t in toks if t in _EN_STOP) >= len(toks) / 8


def recall_main(page: str, url: str) -> str:
    """trafilatura's wider pass, used only when the careful pass found little; kept only if it reads as prose."""
    import trafilatura
    t = trafilatura.extract(page, url=url, include_comments=False, include_tables=False, favor_recall=True) or ""
    return t if readable(t) else ""


def clean_main(page: str, url: str) -> tuple[str, str]:
    import trafilatura
    data = trafilatura.extract(page, url=url, output_format="json", include_comments=False, include_tables=False, favor_precision=True, with_metadata=True)
    if not data:
        return "", ""
    d = json.loads(data)
    return (d.get("text") or ""), (d.get("title") or "")


class ExtractService:
    def __init__(self, repo, settings, fetcher):
        self.repo, self.settings, self.fetcher = repo, settings, fetcher

    async def read(self, article: dict, force: bool = False) -> dict:
        """article: a row from repo.article_get. Returns {status, method, paragraphs, words, note, archive_url, url}."""
        url_hash = article["url_hash"]
        if not force:
            cached = await asyncio.to_thread(self.repo.text_get, url_hash)
            if cached:
                return self._shape(cached, article)
        fallback_lead = article["lead"]
        if not self.settings.get("extract.enabled"):
            return self._summary_only(article, "Fetching article text is switched off in Settings.")
        self.fetcher.configure(self.settings.get("refresh.feed_timeout_s"), self.settings.get("refresh.host_delay_s"))
        await self.fetcher.start()
        best = {"text": "", "method": "", "note": "", "archive": None, "title": article["title"]}

        def consider(text: str, method: str, title: str = "", archive: str | None = None, note: str = "") -> bool:
            if words(text) > words(best["text"]) and readable(text):
                best.update(text=text, method=method, archive=archive, note=note, title=title or best["title"])
            return words(best["text"]) >= FULL_WORDS

        url = article["url"]
        m = _LINK.search(article.get("lead") or "")
        if m and article.get("source_type") == "social":
            url = m.group(1)                                     # a post that shares an article: read the article it links to
        state = {"paywalled": False, "cited": False}

        async def from_page(ua: str | None, impersonated: bool = False, solved: bool = False):
            """One read of the page with this user agent: the careful extraction, the page's own data, the wider pass, the AMP page."""
            kw = {"impersonated": True} if impersonated else {}
            r = await self.fetcher.get(url, accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", user_agent=ua,
                                       **({"solved": True} if solved else {"solve": True}), **kw)
            page = r.body.decode("utf-8", "replace") if r.body and not r.error else ""
            paywalled = bool(PAYWALL.search(page[:200_000])) or '"isAccessibleForFree":false' in page.replace(" ", "").lower() or '"isaccessibleforfree":"false"' in page.replace(" ", "").lower()
            state["paywalled"] = state["paywalled"] or paywalled
            done = False
            if page and article.get("source_key") and not state["cited"]:
                state["cited"] = True
                try:                                             # whom the article cites, for the trust estimate of sites without a rating
                    from app.services.citations import cited_domains, page_links
                    c = cited_domains(page_links(page), article.get("source_domain") or "")
                    if c:
                        await asyncio.to_thread(self.repo.source_links_add, article["source_key"], c, "page")
                except Exception:                                # noqa: BLE001
                    log.debug("citations not recorded", exc_info=True)
            if page:
                txt, title = await asyncio.to_thread(clean_main, page, url)
                done = consider(txt, "page", title)
                if not done:
                    done = consider(await asyncio.to_thread(structured_text, page), "page data")
                if not done and not paywalled and "data-content-encrypted" not in page[:400_000]:
                    done = consider(await asyncio.to_thread(recall_main, page, url), "page")
                if not done:
                    m = _AMP.search(page)
                    if m:
                        ra = await self.fetcher.get(htmllib.unescape(m.group(1)), accept="text/html,*/*;q=0.5", user_agent=ua, **kw)
                        if ra.body and not ra.error and ra.status == 200:
                            t2, ti2 = await asyncio.to_thread(clean_main, ra.body.decode("utf-8", "replace"), m.group(1))
                            done = consider(t2, "amp version", ti2)
            return r, done

        r, done = await from_page(None)                          # every request carries the same browser user agent (the fetcher's)
        if not done and words(best["text"]) < 60 and r.status == 200 and not getattr(r, "impersonated", False) \
                and getattr(self.fetcher, "impersonate_profile", lambda: None)():
            r, done = await from_page(None, impersonated=True)   # the page loaded but held no article (a challenge or an empty shell): once more as a browser
        if not done and words(best["text"]) < 60 and r.status == 200 and not getattr(r, "solved", False) \
                and getattr(self.fetcher, "solver_config", lambda: None)() and flare.looks_challenged(r.body):
            r, done = await from_page(None, solved=True)         # still a challenge page: last, a real browser (FlareSolverr)
        paywalled = state["paywalled"]
        if not done and self.settings.get("extract.archive_fallback"):
            arch = await self._archive(url)
            if arch:
                text, snap = arch
                done = consider(text, "archive copy", archive=snap, note="This is the Internet Archive's public copy, not the live page.")
        text = best["text"]
        if words(text) < 60:                                     # nothing useful: summary only
            bad = r.error or (r.status and r.status != 200)
            why = "The publisher's page could not be read" + (f" ({r.error or 'HTTP ' + str(r.status)})" if bad else "") + "."
            if paywalled:
                why = "The article appears to be behind a paywall or sign-in. Only the publisher's summary is shown."
            return self._summary_only(article, why, store=True)
        note = best["note"] or ("Partly behind a paywall: only the opening is available." if (paywalled and words(text) < FULL_WORDS) else "")
        rec = {"url_hash": url_hash, "url": article["url"], "method": best["method"], "title": best["title"], "text": text, "words": words(text), "note": note, "archive_url": best["archive"]}
        await asyncio.to_thread(self.repo.text_put, rec)
        return self._shape(rec, article)

    async def _archive(self, url: str) -> tuple[str, str] | None:
        r = await self.fetcher.get(f"https://archive.org/wayback/available?url={quote(url, safe='')}", robots=False, accept="application/json")
        if not r.body or r.error:
            return None
        try:
            snap = json.loads(r.body).get("archived_snapshots", {}).get("closest")
        except ValueError:
            return None
        if not snap or not snap.get("available") or not snap.get("url"):
            return None
        ra = await self.fetcher.get(snap["url"].replace("http://", "https://", 1), robots=False, accept="text/html,*/*;q=0.5")
        if not ra.body or ra.error or ra.status != 200:
            return None
        text, _ = await asyncio.to_thread(clean_main, ra.body.decode("utf-8", "replace"), url)
        return (text, snap["url"]) if text else None

    def _summary_only(self, article: dict, why: str, store: bool = False) -> dict:
        if store:
            try:
                self.repo.text_put({"url_hash": article["url_hash"], "url": article["url"], "method": "summary only", "title": article["title"], "text": "", "words": 0, "note": why, "archive_url": None})
            except Exception:                                    # noqa: BLE001 - caching a failure is optional
                pass
        return {"status": "summary", "method": "summary only", "paragraphs": [article["lead"]] if article["lead"] else [], "words": words(article["lead"]),
                "note": why, "archive_url": None, "url": article["url"], "title": article["title"]}

    @staticmethod
    def _shape(rec: dict, article: dict) -> dict:
        paras = to_paragraphs(rec["text"])
        if not paras:
            return {"status": "summary", "method": "summary only", "paragraphs": [article["lead"]] if article["lead"] else [], "words": words(article["lead"]),
                    "note": rec.get("note") or "No text could be read.", "archive_url": None, "url": article["url"], "title": article["title"]}
        status = "full" if rec["words"] >= FULL_WORDS else "partial"
        return {"status": status, "method": rec["method"], "paragraphs": paras, "words": rec["words"], "note": rec.get("note") or "",
                "archive_url": rec.get("archive_url"), "url": article["url"], "title": rec.get("title") or article["title"]}
