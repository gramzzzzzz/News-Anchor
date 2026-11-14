"""An extra way to fetch a page: curl-impersonate (https://github.com/lwthiker/curl-impersonate), a build of curl that sends the TLS
handshake, HTTP/2 settings and headers of a real browser (Chrome 116 by default). Some sites refuse a plain program by that fingerprint
alone, whatever its User-Agent says. It is used only when the setting "Fetch with curl-impersonate" is on, and only after the ordinary
request returned nothing usable. It does not solve JavaScript challenges, log in, or get past a block based on the server's address.

The binaries are installed by the Dockerfile in NA_CURL_IMPERSONATE_DIR (default /opt/curl-impersonate) as curl_<profile> scripts."""
from __future__ import annotations

import asyncio
import os
import re
import tempfile
from pathlib import Path

MAX_BYTES = 6_000_000
PROFILES = ("chrome116", "chrome110", "chrome107", "chrome104", "chrome101", "chrome100", "chrome99", "edge101", "safari15_5", "ff117")
_SLOTS = asyncio.Semaphore(4)               # a few processes at a time: each is short-lived but the app has a memory budget
_NO_RETRY = re.compile(r"Name or service not known|No address associated|nodename nor servname|Disallowed by robots|too large", re.I)


def directory() -> Path:
    return Path(os.environ.get("NA_CURL_IMPERSONATE_DIR", "/opt/curl-impersonate"))


def available(profile: str) -> bool:
    return profile in PROFILES and (directory() / f"curl_{profile}").exists()


def worth_trying(status: int | None, error: str | None) -> bool:
    """Whether a failed ordinary request might succeed with a browser's fingerprint: refusals and challenges (401, 403, 406, 429, 444, 456,
    468, 202), server-side errors, resets, TLS failures and timeouts. Not a missing page (404, 410), a name that does not resolve, or a robots.txt refusal."""
    if status == 200 or status == 304:
        return False
    if status is None:
        return not (error and _NO_RETRY.search(error))
    return status in (202, 401, 403, 406, 429, 444, 451, 456, 468) or status >= 500


async def fetch(url: str, profile: str, timeout: float, *, accept: str | None = None, etag: str | None = None,
                last_modified: str | None = None) -> tuple[int | None, bytes | None, dict, str | None, str | None]:
    """(status, body, headers, final_url, error). status None with an error when curl itself failed."""
    exe = directory() / f"curl_{profile}"
    if not exe.exists():
        return None, None, {}, None, f"curl-impersonate profile {profile} is not installed"
    args = [str(exe), "-sS", "-L", "--max-redirs", "5", "--max-time", str(int(max(5, timeout))), "--max-filesize", str(MAX_BYTES), "--compressed"]
    if accept:
        args += ["-H", f"Accept: {accept}"]
    if etag:
        args += ["-H", f"If-None-Match: {etag}"]
    if last_modified:
        args += ["-H", f"If-Modified-Since: {last_modified}"]
    async with _SLOTS:
        with tempfile.TemporaryDirectory(prefix="imp-") as tmp:
            body_f, head_f = Path(tmp, "body"), Path(tmp, "head")
            args += ["-o", str(body_f), "-D", str(head_f), "-w", "%{http_code}\n%{url_effective}", url]
            proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout + 10)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return None, None, {}, None, "Timed out"
            if proc.returncode != 0:
                msg = (err or b"").decode("utf-8", "replace").strip().splitlines()
                return None, None, {}, None, f"curl-impersonate exit {proc.returncode}: {(msg[-1] if msg else '')[:100]}"
            lines = out.decode("utf-8", "replace").split("\n", 1)
            try:
                status = int(lines[0])
            except ValueError:
                return None, None, {}, None, "curl-impersonate gave no status"
            final = lines[1].strip() if len(lines) > 1 else url
            headers: dict[str, str] = {}
            blocks = head_f.read_bytes().decode("utf-8", "replace").split("\r\n\r\n") if head_f.exists() else []
            for ln in (next((b for b in reversed(blocks) if b.strip()), "")).split("\r\n")[1:]:      # the last response of any redirects
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            body = body_f.read_bytes() if body_f.exists() else b""
    return status, body, headers, final, None
