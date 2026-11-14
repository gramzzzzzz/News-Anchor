"""AI provider catalogue and one client that speaks the OpenAI-compatible chat API, which nearly every free service offers.

Free lineups rotate often, so a model is normally 'auto': the app asks the provider which models it offers and picks a general
chat model. The owner can pin one. Nothing here sends your keys anywhere except to that provider."""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    base_url: str
    key_url: str
    note: str
    models_url: str = ""            # when the model list is not at {base_url}/models
    json_mode: bool = True          # accepts response_format={"type": "json_object"}
    needs_key: bool = True
    free_only: bool = False         # keep only models whose id ends in ':free' (OpenRouter)
    editable_url: bool = False
    default_model: str = ""         # used for 'auto' when the provider's own list is not a good guide


CATALOGUE: list[Provider] = [
    Provider("gemini", "Google Gemini (AI Studio)", "https://generativelanguage.googleapis.com/v1beta/openai", "https://aistudio.google.com/apikey",
             "Free tier with per-model limits; no card."),
    Provider("groq", "Groq", "https://api.groq.com/openai/v1", "https://console.groq.com/keys", "Free, fast; daily request limits per model."),
    Provider("cerebras", "Cerebras", "https://api.cerebras.ai/v1", "https://cloud.cerebras.ai/", "Free tier, very fast; token limits per day."),
    Provider("openrouter", "OpenRouter (free models)", "https://openrouter.ai/api/v1", "https://openrouter.ai/keys",
             "Rotating ':free' models; about 50 requests a day until you add credit.", free_only=True, json_mode=False),
    Provider("mistral", "Mistral", "https://api.mistral.ai/v1", "https://console.mistral.ai/api-keys", "Free plan with monthly credit."),
    Provider("nvidia", "NVIDIA NIM", "https://integrate.api.nvidia.com/v1", "https://build.nvidia.com/", "Preview models on a free developer account.", json_mode=False),
    Provider("github", "GitHub Models", "https://models.github.ai/inference", "https://github.com/settings/tokens",
             "Free for prototyping with a GitHub token (models:read).", models_url="https://models.github.ai/catalog/models", json_mode=False),
    Provider("huggingface", "Hugging Face Inference", "https://router.huggingface.co/v1", "https://huggingface.co/settings/tokens", "Free monthly credit.", json_mode=False),
    Provider("sambanova", "SambaNova", "https://api.sambanova.ai/v1", "https://cloud.sambanova.ai/apis", "Free tier with rate limits.", json_mode=False),
    Provider("together", "Together AI", "https://api.together.xyz/v1", "https://api.together.ai/settings/api-keys", "Some free models; otherwise paid.", json_mode=False),
    Provider("cohere", "Cohere", "https://api.cohere.ai/compatibility/v1", "https://dashboard.cohere.com/api-keys", "Trial key; not for commercial use.", json_mode=False),
    Provider("llm7", "LLM7.io (no key needed)", "https://api.llm7.io/v1", "https://token.llm7.io/",
             "Works without an account within anonymous limits; a free token raises them. What you send is processed by this service.", needs_key=False, json_mode=False, default_model="default"),
    Provider("pollinations", "Pollinations (no key needed)", "https://text.pollinations.ai/openai", "https://enter.pollinations.ai/",
             "Anonymous use is limited to about one reply every 45 seconds. What you send is processed by this service.", needs_key=False, json_mode=False, default_model="openai-fast"),
    Provider("custom", "Any OpenAI-compatible server", "http://localhost:11434/v1", "", "Ollama, LM Studio, vLLM, Cloudflare Workers AI or any other. Set the address.",
             json_mode=False, needs_key=False, editable_url=True),
]
BY_ID = {p.id: p for p in CATALOGUE}

_SKIP = re.compile(r"embed|whisper|tts|guard|safety|moderation|rerank|ocr|audio|image|imagen|veo|speech|transcri|vision|-vl\b|vl-|\bvl\b|code|coder|math|prompt-guard|orpheus|lyria|robotics|live|dall|sora|diffusion", re.I)
_LIKE = [(re.compile(p, re.I), w) for p, w in (
    (r"gemini-\d(\.\d)?-flash(?!-lite)", 9), (r"gemini.*flash", 7), (r"llama-?3\.3-70b|llama-3\.1-70b|llama-4", 8), (r"gpt-oss-120b|gpt-oss-20b", 8), (r"gemma-?\d*-?\d*b-it|gemma", 5),
    (r"qwen3?-?\d*b|qwen", 6), (r"mistral-(small|medium|large)", 7), (r"deepseek", 6), (r"instruct|chat|-it\b", 3), (r"\b(70|120|235|405)b", 2), (r"nemotron", 4), (r"command", 3))]


def pick_model(ids: list[str], free_only: bool = False) -> str | None:
    pool = [i for i in ids if (i.endswith(":free") if free_only else True) and not _SKIP.search(i)]
    if not pool:
        return None
    def score(i: str) -> tuple[int, int]:
        return (sum(w for rx, w in _LIKE if rx.search(i)), -len(i))
    return max(pool, key=score)


class AiError(Exception):
    def __init__(self, kind: str, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.kind, self.retry_after = kind, retry_after       # kind: rate | quota | auth | model | server | bad_output | network


@dataclass
class Reply:
    text: str
    tokens_in: int
    tokens_out: int
    ms: int
    model: str


def classify(status: int, body: str, headers: httpx.Headers) -> AiError:
    ra = None
    try:
        ra = float(headers.get("retry-after")) if headers.get("retry-after") else None
    except ValueError:
        ra = None
    low = body.lower()
    if status in (401, 403):
        return AiError("auth", f"The key was rejected ({status})")
    if status == 402 and "retry after" in low:
        m = re.search(r"retry after (\d+)", low)
        return AiError("rate", "Free limit reached for now", float(m.group(1)) if m else 60.0)
    if status == 404 or ("model" in low and ("not found" in low or "does not exist" in low or "unknown" in low or "not supported" in low) and status in (400, 422)):
        return AiError("model", "The model is not available on this provider")
    if status == 429 or "rate limit" in low or "quota" in low and status < 500:
        daily = any(w in low for w in ("per day", "daily", "per_day", "quota exceeded", "free-models-per-day", "exhausted"))
        # A service that says when to come back (Retry-After) is rate limiting, whatever its wording; "daily" with no time given means wait for tomorrow.
        quota = daily and ra is None
        return AiError("quota" if quota else "rate", f"Rate limited ({status})" + (" for today" if quota else f", retry in {int(ra)} s" if ra else ""), ra)
    if status >= 500:
        return AiError("server", f"Provider error {status}")
    return AiError("bad_output", f"Provider refused the request ({status}): {body[:160]}")


class Client:
    def __init__(self, http: httpx.AsyncClient | None = None, timeout: float = 90):
        self._http = http
        self.timeout = timeout
        self._models: dict[str, tuple[float, list[str]]] = {}

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        return self._http

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    @staticmethod
    def _headers(p: Provider, key: str) -> dict:
        h = {"Content-Type": "application/json"}
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    async def list_models(self, p: Provider, base_url: str, key: str, fresh: bool = False) -> list[str]:
        ck = f"{p.id}|{base_url}"
        hit = self._models.get(ck)
        if hit and not fresh and time.monotonic() - hit[0] < 6 * 3600:
            return hit[1]
        http = await self._client()
        url = p.models_url or base_url.rstrip("/") + "/models"
        try:
            r = await http.get(url, headers=self._headers(p, key), timeout=30)
        except httpx.HTTPError as e:
            raise AiError("network", f"{type(e).__name__}") from e
        if r.status_code != 200:
            raise classify(r.status_code, r.text, r.headers)
        d = r.json()
        rows = d.get("data", d) if isinstance(d, dict) else d
        ids = []
        for m in rows:
            i = m.get("id") or m.get("name") or ""
            ids.append(i.removeprefix("models/"))
        ids = [i for i in ids if i]
        self._models[ck] = (time.monotonic(), ids)
        return ids

    async def resolve_model(self, p: Provider, base_url: str, key: str, wanted: str) -> str:
        if wanted and wanted != "auto":
            return wanted
        if p.default_model:
            return p.default_model
        ids = await self.list_models(p, base_url, key)
        m = pick_model(ids, p.free_only)
        if not m:
            raise AiError("model", "The provider lists no suitable chat model")
        return m

    async def chat(self, p: Provider, base_url: str, key: str, model: str, messages: list[dict], *, max_tokens: int, temperature: float, json_mode: bool) -> Reply:
        http = await self._client()
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if json_mode and p.json_mode:
            body["response_format"] = {"type": "json_object"}
        t0 = time.monotonic()
        try:
            r = await http.post(base_url.rstrip("/") + "/chat/completions", headers=self._headers(p, key), json=body)
        except httpx.TimeoutException as e:
            raise AiError("server", "Timed out") from e
        except httpx.HTTPError as e:
            raise AiError("network", f"{type(e).__name__}") from e
        if r.status_code == 400 and "response_format" in body and "response_format" in r.text.lower():
            body.pop("response_format")                                      # this model has no JSON mode; the prompt still demands JSON
            r = await http.post(base_url.rstrip("/") + "/chat/completions", headers=self._headers(p, key), json=body)
        if r.status_code != 200:
            raise classify(r.status_code, r.text, r.headers)
        try:
            d = r.json()
            text = d["choices"][0]["message"]["content"] or ""
            u = d.get("usage") or {}
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise AiError("bad_output", "The reply was not in the expected form") from e
        return Reply(text, int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0), int((time.monotonic() - t0) * 1000), model)
