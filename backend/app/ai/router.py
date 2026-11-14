"""Chooses a provider for each AI call and keeps going when one runs out: the first enabled provider that is not rate limited
and has not used its daily allowance answers; on a limit or error the next one is tried. Every call is logged for the usage page."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
from typing import Callable

from app.ai.providers import BY_ID, CATALOGUE, AiError, Client, Reply

log = logging.getLogger(__name__)
UTC = dt.timezone.utc


class AiUnavailable(Exception):
    def __init__(self, message: str, reasons: list[dict]):
        super().__init__(message)
        self.reasons = reasons


def midnight_utc() -> dt.datetime:
    n = dt.datetime.now(UTC)
    return (n + dt.timedelta(days=1)).replace(hour=0, minute=0, second=1, microsecond=0)


def extract_json(text: str) -> dict:
    """The model's reply as an object: tolerates code fences and a sentence before or after the JSON."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I)
    try:
        v = json.loads(t)
    except ValueError:
        a, b = t.find("{"), t.rfind("}")
        if a < 0 or b <= a:
            raise ValueError("no JSON object in the reply")
        blob = t[a:b + 1]
        try:
            v = json.loads(blob)
        except ValueError:
            v = json.loads(re.sub(r",\s*([}\]])", r"\1", blob))          # trailing commas are the commonest slip
    if not isinstance(v, dict):
        raise ValueError("the reply is not a JSON object")
    return v


class AiRouter:
    def __init__(self, repo, settings, client: Client | None = None):
        self.repo, self.settings = repo, settings
        self.client = client or Client()
        self._models: dict[str, str] = {}

    # ---- configuration (keys live in this server's database and are never sent to the browser) ----------
    def config(self) -> dict:
        return self.repo.kv_get("ai_providers", {}) or {}

    def order(self) -> list[str]:
        saved = self.repo.kv_get("ai_order", None)
        ids = [p.id for p in CATALOGUE]
        first = [i for i in (saved or ids) if i in BY_ID]
        return first + [i for i in ids if i not in first]          # each provider once (it used to list every one twice with no saved order)

    def set_provider(self, pid: str, changes: dict) -> None:
        if pid not in BY_ID:
            raise ValueError("Unknown provider")
        cfg = self.config()
        cur = cfg.get(pid, {})
        for k in ("enabled", "key", "model", "base_url", "daily_limit"):
            if k in changes and changes[k] is not None:
                v = changes[k]
                if k == "key":
                    v = str(v).strip()
                if k == "daily_limit":
                    v = max(0, int(v))
                if k == "base_url":
                    v = str(v).strip().rstrip("/")
                    if v and not v.startswith(("http://", "https://")):
                        raise ValueError("The address must start with http:// or https://")
                cur[k] = v
        cfg[pid] = cur
        self.repo.kv_set("ai_providers", cfg)
        self._models.pop(pid, None)
        self.repo.ai_block(pid, None, "")

    def reset_provider(self, pid: str) -> None:
        cfg = self.config()
        cfg.pop(pid, None)
        self.repo.kv_set("ai_providers", cfg)
        self._models.pop(pid, None)
        self.repo.ai_block(pid, None, "")

    def set_order(self, ids: list[str]) -> None:
        self.repo.kv_set("ai_order", [i for i in ids if i in BY_ID])

    def usable(self, pid: str) -> bool:
        p, c = BY_ID[pid], self.config().get(pid, {})
        return bool(c.get("enabled", bool(c.get("key")) or (not p.needs_key and bool(c.get("base_url"))))) and (bool(c.get("key")) or not p.needs_key)

    def public_state(self) -> list[dict]:
        cfg, states = self.config(), self.repo.ai_states()
        used = {r["provider"]: r for r in self.repo.ai_usage_summary()["today"]}
        out = []
        for pid in self.order():
            p, c = BY_ID[pid], cfg.get(pid, {})
            st = states.get(pid) or {}
            blocked = st.get("blocked_until")
            out.append({"id": pid, "label": p.label, "note": p.note, "key_url": p.key_url, "needs_key": p.needs_key, "editable_url": p.editable_url,
                        "has_key": bool(c.get("key")), "enabled": self.usable(pid), "model": c.get("model") or "auto", "base_url": c.get("base_url") or p.base_url,
                        "daily_limit": c.get("daily_limit", 0), "calls_today": used.get(pid, {}).get("calls", 0),
                        "blocked_until": blocked if blocked and blocked > dt.datetime.now(UTC) else None, "last_error": st.get("last_error", ""),
                        "resolved_model": self._models.get(pid)})
        return out

    def any_usable(self) -> bool:
        return any(self.usable(p) for p in self.order())

    # ---- the call ------------------------------------------------------------------------------
    async def complete(self, task: str, messages: list[dict], check: Callable[[str], dict], *, prefer: str | None = None) -> tuple[dict, Reply, str]:
        """Try the providers in order. If every one is only briefly rate limited (30 s or less), wait that long once and try again."""
        try:
            return await self._complete(task, messages, check, prefer)
        except AiUnavailable as e:
            waits = [r["wait"] for r in e.reasons if r.get("wait")]
            if waits and len(waits) == len(e.reasons) and min(waits) <= 30:
                await asyncio.sleep(min(waits) + 1)
                for r in e.reasons:
                    await asyncio.to_thread(self.repo.ai_block, r["provider"], None, "")        # their short wait is over
                return await self._complete(task, messages, check, prefer)
            raise

    async def _complete(self, task: str, messages: list[dict], check: Callable[[str], dict], prefer: str | None) -> tuple[dict, Reply, str]:
        cap = int(self.settings.get("ai.daily_call_cap"))
        calls_today = await asyncio.to_thread(lambda: self.repo.ai_usage_summary()["calls_today"])
        if calls_today >= cap:
            raise AiUnavailable(f"The daily AI call cap ({cap}) is reached. Raise it in Settings → AI, or try tomorrow.", [])
        cfg, states = self.config(), await asyncio.to_thread(self.repo.ai_states)
        usage = {r["provider"]: r["calls"] for r in await asyncio.to_thread(lambda: self.repo.ai_usage_summary()["today"])}
        reasons: list[dict] = []
        order = self.order()
        if prefer in order:
            order = [prefer] + [o for o in order if o != prefer]
        for pid in order:
            if not self.usable(pid):
                continue
            p, c = BY_ID[pid], cfg.get(pid, {})
            st = states.get(pid) or {}
            if st.get("blocked_until") and st["blocked_until"] > dt.datetime.now(UTC):
                left = (st["blocked_until"] - dt.datetime.now(UTC)).total_seconds()
                reasons.append({"provider": pid, "reason": f"waiting until {st['blocked_until'].astimezone(UTC):%H:%M} UTC: {st.get('last_error', '')}", "wait": left if left <= 300 else None})
                continue
            limit = int(c.get("daily_limit") or 0)
            if limit and usage.get(pid, 0) >= limit:
                reasons.append({"provider": pid, "reason": f"your daily limit of {limit} calls for it is reached"})
                continue
            base = c.get("base_url") or p.base_url
            key = c.get("key", "")
            model = ""
            try:
                model = self._models.get(pid) or await self.client.resolve_model(p, base, key, c.get("model") or "auto")
                self._models[pid] = model
                msgs = messages
                obj = None
                for attempt in range(2):                      # a reply that is not usable gets one chance to be corrected, on the same service
                    reply = await self.client.chat(p, base, key, model, msgs, max_tokens=int(self.settings.get("ai.max_tokens")),
                                                   temperature=float(self.settings.get("ai.temperature")), json_mode=True)
                    try:
                        obj = check(reply.text)
                        break
                    except (ValueError, KeyError, TypeError) as e:
                        await asyncio.to_thread(self.repo.ai_usage_add, pid, model, task, reply.tokens_in, reply.tokens_out, reply.ms, False, f"unusable reply: {e}")
                        last = f"unusable reply ({e})"
                        msgs = messages + [{"role": "assistant", "content": reply.text[:6000]},
                                           {"role": "user", "content": f"That reply could not be used: {e}. Reply again with only the corrected JSON object, in exactly the shape asked for."}]
                if obj is None:
                    reasons.append({"provider": pid, "reason": last})
                    continue
                await asyncio.to_thread(self.repo.ai_usage_add, pid, model, task, reply.tokens_in, reply.tokens_out, reply.ms, True, "")
                return obj, reply, pid
            except AiError as e:
                await asyncio.to_thread(self.repo.ai_usage_add, pid, model or "?", task, 0, 0, 0, False, f"{e.kind}: {e}")
                wait = {"rate": min(max(e.retry_after or 60, 10), 3600), "server": 120, "network": 120, "auth": 6 * 3600, "model": 3600, "bad_output": 300}.get(e.kind, 120)
                until = midnight_utc() if e.kind == "quota" else dt.datetime.now(UTC) + dt.timedelta(seconds=wait)
                if e.kind == "model":
                    self._models.pop(pid, None)
                await asyncio.to_thread(self.repo.ai_block, pid, until, str(e))
                reasons.append({"provider": pid, "reason": str(e), "wait": e.retry_after if e.kind == "rate" else None})
        if not self.any_usable():
            raise AiUnavailable("No AI provider is set up. Add a free key in Settings → AI.", [])
        raise AiUnavailable("Every AI provider failed or is waiting: " + "; ".join(f"{r['provider']}: {r['reason']}" for r in reasons), reasons)

    async def test(self, pid: str) -> dict:
        p, c = BY_ID[pid], self.config().get(pid, {})
        base, key = c.get("base_url") or p.base_url, c.get("key", "")
        try:
            models = await self.client.list_models(p, base, key, fresh=True)
            model = await self.client.resolve_model(p, base, key, c.get("model") or "auto")
            r = await self.client.chat(p, base, key, model, [{"role": "user", "content": 'Reply with the JSON {"ok": true}'}], max_tokens=40, temperature=0, json_mode=True)
            await asyncio.to_thread(self.repo.ai_block, pid, None, "")
            self._models[pid] = model
            return {"ok": True, "model": model, "models": models[:200], "ms": r.ms, "reply": r.text[:120]}
        except AiError as e:
            return {"ok": False, "error": str(e), "kind": e.kind}
