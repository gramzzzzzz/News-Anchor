"""X (Twitter) API v2. Reading is pay-per-use (about $0.005 a post read, billed by X to your developer account), so every read is
counted against a monthly budget you set. Two ways in, both with your own developer app (developer.x.com):
* Log in with your X account (OAuth 2.0 with PKCE): reads your home timeline, as you see it.
* An app bearer token: reads the posts of accounts you list."""
from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import urlencode

import httpx

from app.dao.social import SocialError, post

AUTHORIZE = "https://x.com/i/oauth2/authorize"
TOKEN = "https://api.x.com/2/oauth2/token"
API = "https://api.x.com/2/"
SCOPES = "tweet.read users.read offline.access"


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    return AUTHORIZE + "?" + urlencode({"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": SCOPES,
                                        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})


async def _token(client: httpx.AsyncClient, data: dict, client_id: str, secret: str) -> dict:
    auth = (client_id, secret) if secret else None                 # a confidential app authenticates; a public one sends its id
    r = await client.post(TOKEN, data={**data, "client_id": client_id}, auth=auth, timeout=30)
    d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or "access_token" not in d:
        raise SocialError(f"X refused the login ({r.status_code}{': ' + str(d.get('error_description') or d.get('error')) if d else ''})")
    return d


async def exchange(client: httpx.AsyncClient, client_id: str, secret: str, code: str, redirect_uri: str, verifier: str) -> dict:
    return await _token(client, {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "code_verifier": verifier}, client_id, secret)


async def refresh(client: httpx.AsyncClient, client_id: str, secret: str, refresh_token: str) -> dict:
    return await _token(client, {"grant_type": "refresh_token", "refresh_token": refresh_token}, client_id, secret)


async def _get(client: httpx.AsyncClient, token: str, path: str, params: dict | None = None) -> dict:
    r = await client.get(API + path, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.status_code == 401:
        raise SocialError("expired")
    if r.status_code == 402 or (r.status_code == 403 and "credits" in r.text.lower()):
        raise SocialError("X says the developer account has no credits left")
    if r.status_code == 429:
        raise SocialError("X rate limit reached; trying again later")
    if r.status_code != 200:
        raise SocialError(f"X answered {r.status_code}")
    return r.json()


async def me(client: httpx.AsyncClient, token: str) -> dict:
    return (await _get(client, token, "users/me"))["data"]


async def user_id(client: httpx.AsyncClient, token: str, username: str) -> str:
    return (await _get(client, token, f"users/by/username/{username.strip().lstrip('@')}"))["data"]["id"]


def items(d: dict) -> list[dict]:
    users = {u["id"]: u.get("username", "?") for u in (d.get("includes") or {}).get("users", [])}
    out = []
    for t in d.get("data") or []:
        name = users.get(t.get("author_id", ""), "i")
        urls = [u for u in ((t.get("entities") or {}).get("urls") or []) if "x.com" not in u.get("expanded_url", "") and "twitter.com" not in u.get("expanded_url", "")]
        it = post(name, t.get("text") or "", f"https://x.com/{name}/status/{t['id']}", t.get("created_at"),
                  urls[0].get("expanded_url", "") if urls else "", urls[0].get("title", "") if urls else "")
        if it:
            out.append(it)
    return out


FIELDS = {"tweet.fields": "created_at,author_id,entities", "expansions": "author_id", "user.fields": "username"}


async def home(client: httpx.AsyncClient, token: str, uid: str, max_results: int, since_id: str | None) -> tuple[list[dict], str | None, int]:
    """(posts, newest id, posts read). Only posts newer than ``since_id`` are read (and paid for)."""
    p = {**FIELDS, "max_results": max(1, min(100, max_results)), "exclude": "replies"}
    if since_id:
        p["since_id"] = since_id
    d = await _get(client, token, f"users/{uid}/timelines/reverse_chronological", p)
    return items(d), (d.get("meta") or {}).get("newest_id") or since_id, len(d.get("data") or [])


async def user_posts(client: httpx.AsyncClient, token: str, uid: str, max_results: int, since_id: str | None) -> tuple[list[dict], str | None, int]:
    p = {**FIELDS, "max_results": max(5, min(100, max_results)), "exclude": "replies,retweets"}
    if since_id:
        p["since_id"] = since_id
    d = await _get(client, token, f"users/{uid}/tweets", p)
    return items(d), (d.get("meta") or {}).get("newest_id") or since_id, len(d.get("data") or [])
