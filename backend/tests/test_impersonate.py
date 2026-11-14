"""curl-impersonate as an extra step: only when the setting is on, only after an ordinary request returned nothing usable."""
import asyncio
import os
import stat

import httpx

from app.dao.fetch import impersonate
from app.dao.fetch.http import Fetcher

FAKE = r"""#!/bin/bash
while [ $# -gt 0 ]; do
  case "$1" in -o) out=$2; shift 2;; -D) hd=$2; shift 2;; -w) shift 2;; -H|--max-time|--max-filesize|--max-redirs) shift 2;; -*) shift;; *) url=$1; shift;; esac
done
echo "$url" >> "$FAKE_LOG"
[ -n "$FAKE_FAIL" ] && { echo "curl: (7) Failed to connect" >&2; exit 7; }
printf 'HTTP/2 200\r\netag: "e1"\r\n\r\n' > "$hd"
printf '<html>the real page</html>' > "$out"
printf '200\n%s' "$url"
"""


def setup(tmp_path, monkeypatch, plain_status=403, fail=False):
    d = tmp_path / "imp"
    d.mkdir()
    exe = d / "curl_chrome116"
    exe.write_text(FAKE)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.log"
    monkeypatch.setenv("NA_CURL_IMPERSONATE_DIR", str(d))
    monkeypatch.setenv("FAKE_LOG", str(log))
    if fail:
        monkeypatch.setenv("FAKE_FAIL", "1")
    f = Fetcher("Mozilla/5.0 test", host_delay=0)
    f._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(plain_status, text="blocked")))
    return f, log


def calls(log):
    return log.read_text().split() if log.exists() else []


def test_off_by_default_an_ordinary_refusal_is_returned_as_it_is(tmp_path, monkeypatch):
    f, log = setup(tmp_path, monkeypatch)
    r = asyncio.run(f.get("https://a.example/", robots=False))
    assert r.status == 403 and not r.impersonated and calls(log) == []


def test_on_a_refusal_is_repeated_with_curl_impersonate_and_its_page_returned(tmp_path, monkeypatch):
    f, log = setup(tmp_path, monkeypatch)
    f.impersonate_profile = lambda: "chrome116"
    r = asyncio.run(f.get("https://a.example/news", robots=False))
    assert r.status == 200 and r.body == b"<html>the real page</html>" and r.impersonated and r.etag == '"e1"'
    assert calls(log) == ["https://a.example/news"] and f.imp_stats == {"tried": 1, "worked": 1}


def test_a_missing_page_or_unknown_host_is_not_retried(tmp_path, monkeypatch):
    f, log = setup(tmp_path, monkeypatch, plain_status=404)
    f.impersonate_profile = lambda: "chrome116"
    assert asyncio.run(f.get("https://a.example/gone", robots=False)).status == 404 and calls(log) == []
    assert not impersonate.worth_trying(None, "ConnectError: [Errno -2] Name or service not known")
    assert impersonate.worth_trying(None, "Timed out") and impersonate.worth_trying(403, "HTTP 403") and impersonate.worth_trying(503, "HTTP 503")


def test_when_it_fails_too_the_original_result_comes_back_and_the_host_rests(tmp_path, monkeypatch):
    f, log = setup(tmp_path, monkeypatch, fail=True)
    f.impersonate_profile = lambda: "chrome116"
    r = asyncio.run(f.get("https://a.example/x", robots=False))
    assert r.status == 403 and not r.impersonated and len(calls(log)) == 1
    asyncio.run(f.get("https://a.example/y", robots=False))
    assert len(calls(log)) == 1                                          # not tried again for an hour on this host
    asyncio.run(f.get("https://b.example/y", robots=False))
    assert len(calls(log)) == 2


def test_direct_use_for_a_page_that_loaded_but_held_no_article(tmp_path, monkeypatch):
    f, log = setup(tmp_path, monkeypatch, plain_status=200)
    f.impersonate_profile = lambda: "chrome116"
    r = asyncio.run(f.get("https://a.example/", robots=False, impersonated=True))
    assert r.impersonated and r.status == 200 and len(calls(log)) == 1


def test_missing_binary_is_skipped_quietly(tmp_path, monkeypatch):
    f, log = setup(tmp_path, monkeypatch)
    monkeypatch.setenv("NA_CURL_IMPERSONATE_DIR", str(tmp_path / "nowhere"))
    f.impersonate_profile = lambda: "chrome116"
    assert os.path.exists(str(tmp_path)) and asyncio.run(f.get("https://a.example/", robots=False)).status == 403
