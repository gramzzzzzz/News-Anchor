"""Headlines in other languages, translated into English on this server, so that every story can be read, clustered and searched.

Models: the free OPUS-MT/Argos models (CC BY / MIT), one per language into English, 50 to 290 MB each, downloaded only for languages
that actually turn up in the news collected (at least ``translate.min_daily`` articles a day) and evicted, least recently used first,
to stay inside ``translate.disk_budget_mb``. Translation runs in a child process that exits when idle. Nothing leaves the server.
Languages with no model (Tamil, Telugu, Malayalam, Marathi ... at the time of writing) stay in the original and are marked so."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path

import httpx

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
INDEX = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"
NAMES = {"ar": "Arabic", "az": "Azerbaijani", "bg": "Bulgarian", "bn": "Bengali", "ca": "Catalan", "cs": "Czech", "da": "Danish", "de": "German",
         "el": "Greek", "eo": "Esperanto", "es": "Spanish", "et": "Estonian", "eu": "Basque", "fa": "Persian", "fi": "Finnish", "fr": "French",
         "ga": "Irish", "gl": "Galician", "he": "Hebrew", "hi": "Hindi", "hu": "Hungarian", "id": "Indonesian", "it": "Italian", "ja": "Japanese",
         "ko": "Korean", "ky": "Kyrgyz", "lt": "Lithuanian", "lv": "Latvian", "ms": "Malay", "nb": "Norwegian", "nl": "Dutch", "pl": "Polish",
         "pt": "Portuguese", "ro": "Romanian", "ru": "Russian", "sk": "Slovak", "sl": "Slovenian", "sq": "Albanian", "sv": "Swedish", "sw": "Swahili",
         "th": "Thai", "tl": "Tagalog", "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu", "vi": "Vietnamese", "zh": "Chinese", "ta": "Tamil",
         "te": "Telugu", "ml": "Malayalam", "kn": "Kannada", "mr": "Marathi", "gu": "Gujarati", "pa": "Punjabi", "or": "Odia", "si": "Sinhala",
         "ne": "Nepali", "my": "Burmese", "km": "Khmer", "am": "Amharic", "ka": "Georgian", "hy": "Armenian", "no": "Norwegian", "lo": "Lao"}
ALIAS = {"no": "nb"}                    # the index's code for a language our detector names differently


def lang_name(code: str | None) -> str:
    return NAMES.get(code or "", code or "")


class Translator:
    def __init__(self, repo, settings, model_dir: str, user_agent: str, http_factory=None, idle_seconds: int = 120):
        self.repo, self.settings = repo, settings
        self.root = Path(model_dir) / "argos"
        self.ua = user_agent
        self.http_factory = http_factory or (lambda: httpx.AsyncClient(headers={"User-Agent": user_agent}, follow_redirects=True, timeout=1800))
        self.idle = idle_seconds
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._dl_lock = asyncio.Lock()
        self._last = 0.0
        self.running = False
        self.last_error = ""

    # ---- models ----------------------------------------------------------------------------------
    async def index(self) -> dict[str, str]:
        """{language: download url} for models that translate into English (cached for a week)."""
        cached = await asyncio.to_thread(self.repo.kv_get, "argos_index")
        if cached and time.time() - cached.get("t", 0) < 7 * 86400:
            return cached["links"]
        async with self.http_factory() as http:
            r = await http.get(INDEX, timeout=60)
            r.raise_for_status()
            links = {p["from_code"]: p["links"][0] for p in r.json() if p.get("to_code") == "en" and p.get("links") and p["links"][0].startswith("https://")}
        await asyncio.to_thread(self.repo.kv_set, "argos_index", {"t": time.time(), "links": links})
        return links

    def model_dir(self, lang: str) -> Path | None:
        d = self.root / lang
        if not d.exists():
            return None
        hits = [p for p in d.iterdir() if (p / "model" / "model.bin").exists()]
        return hits[0] if hits else None

    def installed(self) -> dict[str, int]:
        out = {}
        if self.root.exists():
            for d in self.root.iterdir():
                if d.is_dir():
                    out[d.name] = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        return out

    def _evict(self, need: int) -> None:
        budget = int(self.settings.get("translate.disk_budget_mb")) * 1024 * 1024
        used = self.installed()
        order = sorted(used, key=lambda k: (self.root / k / ".used").stat().st_mtime if (self.root / k / ".used").exists() else 0)
        total = sum(used.values())
        while order and total + need > budget:
            k = order.pop(0)
            total -= used[k]
            shutil.rmtree(self.root / k, ignore_errors=True)
            log.info("Removed the %s translation model to stay inside the disk budget", k)

    async def ensure(self, lang: str) -> Path | None:
        if (d := self.model_dir(lang)):
            return d
        links = await self.index()
        url = links.get(ALIAS.get(lang, lang))
        if not url:
            return None
        async with self._dl_lock:
            if (d := self.model_dir(lang)):
                return d
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.root / f".{lang}.zip"
            async with self.http_factory() as http:
                head = await http.head(url)
                size = int(head.headers.get("content-length") or 0)
                if size > int(self.settings.get("translate.disk_budget_mb")) * 1024 * 1024:
                    raise RuntimeError(f"The {lang_name(lang)} model ({size // 2**20} MB) is larger than the translation disk budget")
                await asyncio.to_thread(self._evict, size)
                async with http.stream("GET", url) as r:
                    r.raise_for_status()
                    with open(tmp, "wb") as fh:
                        async for chunk in r.aiter_bytes(1 << 20):
                            fh.write(chunk)
            target = self.root / lang
            await asyncio.to_thread(self._unzip, tmp, target)
            log.info("Installed the %s translation model", lang_name(lang))
            return self.model_dir(lang)

    @staticmethod
    def _unzip(zpath: Path, target: Path) -> None:
        try:
            with zipfile.ZipFile(zpath) as z:
                for m in z.namelist():                                 # no absolute paths or .. in a downloaded archive
                    if m.startswith("/") or ".." in Path(m).parts:
                        raise RuntimeError("unsafe path in model archive")
                z.extractall(target)
        finally:
            zpath.unlink(missing_ok=True)
        for st in target.rglob("stanza"):                              # sentence splitter for long texts; headlines do not need it
            shutil.rmtree(st, ignore_errors=True)

    # ---- the worker ------------------------------------------------------------------------------
    async def _start(self) -> bool:
        env = {**os.environ, "NA_TRANSLATE_THREADS": "2"}
        self._proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "app.services.translate_worker", stdin=asyncio.subprocess.PIPE,
                                                          stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env, limit=16 * 1024 * 1024)
        line = await asyncio.wait_for(self._proc.stdout.readline(), timeout=60)
        msg = json.loads(line or b"{}")
        if msg.get("ready"):
            return True
        self.last_error = msg.get("error", "the translator did not start")
        await self._stop()
        return False

    async def _stop(self) -> None:
        p, self._proc = self._proc, None
        if p and p.returncode is None:
            try:
                p.stdin.close()
                await asyncio.wait_for(p.wait(), timeout=10)
            except Exception:                                          # noqa: BLE001
                p.kill()

    async def translate(self, lang: str, texts: list[str]) -> list[str]:
        d = await self.ensure(lang)
        if not d:
            raise LookupError(f"No free model translates {lang_name(lang)} into English")
        (d.parent / ".used").touch()
        async with self._lock:
            if self._proc is None or self._proc.returncode is not None:
                if not await self._start():
                    raise RuntimeError(self.last_error)
            out: list[str] = []
            for i in range(0, len(texts), 32):
                self._proc.stdin.write((json.dumps({"dir": str(d), "texts": texts[i:i + 32]}) + "\n").encode())
                await self._proc.stdin.drain()
                msg = json.loads(await asyncio.wait_for(self._proc.stdout.readline(), timeout=600))
                if "out" not in msg:
                    raise RuntimeError(msg.get("error", "no translation"))
                out += msg["out"]
            self._last = time.monotonic()
            asyncio.get_running_loop().call_later(self.idle + 1, lambda: asyncio.ensure_future(self._idle_stop()))
            return out

    async def _idle_stop(self) -> None:
        if time.monotonic() - self._last >= self.idle and not self._lock.locked():
            async with self._lock:
                await self._stop()

    async def close(self) -> None:
        await self._stop()

    # ---- the pass after each refresh ---------------------------------------------------------------
    async def run(self) -> dict:
        if not self.settings.get("translate.enabled") or self.running:
            return {"skipped": True}
        self.running = True
        try:
            links = await self.index()
        except (httpx.HTTPError, ValueError) as e:
            self.running = False
            self.last_error = f"model index: {type(e).__name__}"
            return {"error": self.last_error}
        try:
            budget = int(self.settings.get("translate.per_cycle"))
            volume = await asyncio.to_thread(self.repo.untranslated_by_language, 2)
            min_daily = int(self.settings.get("translate.min_daily"))
            done, langs, skipped = 0, [], []
            for lang, n in volume:
                if done >= budget:
                    break
                if ALIAS.get(lang, lang) not in links:
                    skipped.append(lang)
                    continue
                if n < min_daily and not self.model_dir(lang):
                    continue
                rows = await asyncio.to_thread(self.repo.untranslated_articles, lang, min(400, budget - done), 2)
                if not rows:
                    continue
                try:
                    titles = await self.translate(lang, [r["title"] for r in rows])
                    leads = await self.translate(lang, [r["lead"][:300] if r["lead"] else "" for r in rows])
                except (LookupError, RuntimeError, httpx.HTTPError, OSError, asyncio.TimeoutError) as e:
                    self.last_error = f"{lang}: {e}"[:200]
                    log.warning("Translation of %s failed: %s", lang, e)
                    continue
                await asyncio.to_thread(self.repo.translations_set, [(t, l if r["lead"] else "", r["id"], r["seen_day"]) for r, t, l in zip(rows, titles, leads)])
                done += len(rows)
                langs.append(lang)
            res = {"translated": done, "languages": langs, "no_model": skipped[:20], "at": dt.datetime.now(UTC).isoformat()}
            await asyncio.to_thread(self.repo.kv_set, "translate_last", res)
            return res
        finally:
            self.running = False

    def status(self) -> dict:
        inst = self.installed()
        return {"enabled": bool(self.settings.get("translate.enabled")), "installed": {k: v // (1024 * 1024) for k, v in inst.items()},
                "disk_mb": sum(inst.values()) // (1024 * 1024), "budget_mb": self.settings.get("translate.disk_budget_mb"),
                "last": self.repo.kv_get("translate_last"), "worker": self._proc is not None and self._proc.returncode is None, "last_error": self.last_error}
