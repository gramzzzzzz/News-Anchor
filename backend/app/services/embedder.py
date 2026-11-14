"""Turns headlines into 384-number meaning vectors.

Two backends give vectors of the same size but are not comparable with each other:
  model   all-MiniLM-L6-v2 through ONNX (fastembed), in a child process that is started when needed and stopped after a quiet spell,
          so its ~350 MB is returned to the server. Understands paraphrase.
  hashed  word and word-pair hashing: no download, no memory, deterministic. Catches shared names and numbers, not paraphrase.
The backend in use is remembered; if it changes, stored vectors are cleared and rebuilt."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import sys
import time

log = logging.getLogger(__name__)
DIM = 384
_STOP = set("a an the of to in on for and or at by with from as is are was were be been it its this that these those after over amid says say said new will into out up down vs".split())
_WORD = re.compile(r"[a-z0-9]+")


def hashed_vector(text: str) -> list[float]:
    words = [w for w in _WORD.findall(text.lower()) if w not in _STOP]
    feats: dict[int, float] = {}
    grams = [(w, 1.0) for w in words] + [(a + " " + b, 0.7) for a, b in zip(words, words[1:])]
    for g, wt in grams:
        h = int.from_bytes(hashlib.blake2b(g.encode(), digest_size=8).digest(), "big")
        idx, sign = h % DIM, 1.0 if (h >> 40) & 1 else -1.0
        feats[idx] = feats.get(idx, 0.0) + sign * wt
    norm = math.sqrt(sum(v * v for v in feats.values())) or 1.0
    out = [0.0] * DIM
    for i, v in feats.items():
        out[i] = v / norm
    return out


class Embedder:
    def __init__(self, settings, model_dir: str, idle_seconds: int = 120):
        self.settings, self.model_dir, self.idle = settings, model_dir, idle_seconds
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._failed_until = 0.0
        self._idle_task: asyncio.Task | None = None
        self.last_error = ""

    def mode(self) -> str:
        return self.settings.get("search.embeddings")

    async def backend(self) -> str:
        """What the next call would use: 'model', 'hashed' or 'off'."""
        m = self.mode()
        if m == "off":
            return "off"
        if m == "hashed" or (m == "auto" and time.monotonic() < self._failed_until):
            return "hashed"
        return "model"

    async def _start(self) -> bool:
        os.makedirs(self.model_dir, exist_ok=True)
        # The app user's home may not be writable; keep every download and cache under the data folder.
        env = {**os.environ, "NA_MODEL_DIR": self.model_dir, "HF_HOME": self.model_dir + "/hf", "XDG_CACHE_HOME": self.model_dir + "/cache",
               "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_DISABLE_PROGRESS_BARS": "1"}
        try:
            self._proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "app.services.embed_worker", stdin=asyncio.subprocess.PIPE,
                                                              stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env, limit=64 * 1024 * 1024)
            line = await asyncio.wait_for(self._proc.stdout.readline(), timeout=180)
            msg = json.loads(line or b"{}")
            if msg.get("ready"):
                return True
            self.last_error = msg.get("error", "the model did not start")
            log.warning("Embedding worker failed to start: %s", self.last_error)
        except (OSError, asyncio.TimeoutError, ValueError) as e:
            self.last_error = f"{type(e).__name__}: {e}"[:200]
        await self._stop()
        return False

    async def _stop(self) -> None:
        p, self._proc = self._proc, None
        if p and p.returncode is None:
            try:
                p.stdin.close()
                await asyncio.wait_for(p.wait(), timeout=10)
            except Exception:                                        # noqa: BLE001
                p.kill()

    async def _watch_idle(self) -> None:
        while self._proc is not None:
            await asyncio.sleep(15)
            if time.monotonic() - self._last > self.idle and not self._lock.locked():
                async with self._lock:
                    await self._stop()
                return

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], str]:
        """(vectors, backend used). Raises nothing: falls back to hashing when the model cannot run, unless the mode is 'model'."""
        if not texts:
            return [], await self.backend()
        be = await self.backend()
        if be == "off":
            return [], "off"
        if be == "model":
            async with self._lock:
                if self._proc is None or self._proc.returncode is not None:
                    ok = await self._start()
                    if not ok:
                        if self.mode() == "auto" and self._worked():
                            log.warning("Embedding model did not start (%s); trying again next refresh", self.last_error)
                            return [], "unavailable"                 # it has worked here before: wait, do not replace good vectors with hashed ones
                        if self.mode() == "auto":
                            self._failed_until = time.monotonic() + 3600
                            log.warning("Embedding model unavailable (%s); using hashed vectors for an hour", self.last_error)
                            be = "hashed"
                        else:
                            raise RuntimeError(f"Embedding model unavailable: {self.last_error}")
                if be == "model":
                    try:
                        out: list[list[float]] = []
                        for i in range(0, len(texts), 128):
                            self._proc.stdin.write((json.dumps({"texts": texts[i:i + 128]}) + "\n").encode())
                            await self._proc.stdin.drain()
                            msg = json.loads(await asyncio.wait_for(self._proc.stdout.readline(), timeout=300))
                            if "vectors" not in msg:
                                raise RuntimeError(msg.get("error", "no vectors"))
                            out += msg["vectors"]
                        self._last = time.monotonic()
                        self._mark_worked()
                        if self._idle_task is None or self._idle_task.done():
                            self._idle_task = asyncio.create_task(self._watch_idle())
                        return out, "model"
                    except (RuntimeError, OSError, asyncio.TimeoutError, ValueError) as e:
                        await self._stop()
                        log.warning("Embedding model failed (%s: %s)", type(e).__name__, str(e)[:160])
                        if self.mode() != "auto":
                            raise
                        if self._worked():
                            self.last_error = str(e)[:200]
                            return [], "unavailable"
                        self._failed_until = time.monotonic() + 3600
                        self.last_error = str(e)[:200]
                        be = "hashed"
        return [hashed_vector(t) for t in texts], "hashed"

    def _worked(self) -> bool:
        return os.path.exists(os.path.join(self.model_dir, ".model_ok"))

    def _mark_worked(self) -> None:
        if not self._worked():
            try:
                open(os.path.join(self.model_dir, ".model_ok"), "w").close()
            except OSError:
                pass

    async def close(self) -> None:
        async with self._lock:
            await self._stop()

    @property
    def loaded(self) -> bool:
        return self._proc is not None and self._proc.returncode is None
