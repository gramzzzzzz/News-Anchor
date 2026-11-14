import datetime as dt
import os
import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Config  # noqa: E402

DB_URL = os.environ.get("NA_TEST_DATABASE_URL")
os.environ.setdefault("NA_TEST_PASSWORD", secrets.token_urlsafe(16))      # a new password for every test run; no password is written in the repository
UTC = dt.timezone.utc


def pytest_collection_modifyitems(config, items):
    if DB_URL:
        return
    skip = pytest.mark.skip(reason="set NA_TEST_DATABASE_URL to run database tests")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


def rss(items: list[dict]) -> bytes:
    """Build an RSS document. Each item: title, link, desc, pub (datetime)."""
    parts = []
    for i in items:
        parts.append(f"<item><title>{i['title']}</title><link>{i['link']}</link><description>{i.get('desc', '')}</description>"
                     f"<pubDate>{i['pub'].strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate></item>")
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{"".join(parts)}</channel></rss>'.encode()


class FakeFetcher:
    """Serves canned feeds by URL; everything else is a 404."""
    ua = "NewsAnchor-test/0"
    token = "NewsAnchor"
    robots_on = staticmethod(lambda: True)
    impersonate_profile = staticmethod(lambda: None)
    solver_config = staticmethod(lambda: None)
    _robots: dict = {}

    def flush_robots(self):
        self._robots.clear()

    def __init__(self, feeds: dict[str, bytes], sitemaps: dict[str, list[str]] | None = None):
        self.feeds = feeds
        self.calls: list[str] = []
        self.listed = sitemaps or {}

    async def sitemaps(self, url):
        from urllib.parse import urlsplit
        return self.listed.get(urlsplit(url).hostname or "", [])

    def configure(self, timeout, host_delay): ...
    async def start(self): ...
    async def close(self): ...

    async def get(self, url, etag=None, last_modified=None, *, robots=True, accept=None, user_agent=None, impersonated=False, defer_transient=False, solve=False, solved=False):
        from app.dao.fetch.http import FetchResult
        self.calls.append(url)
        if url in self.feeds:
            return FetchResult(200, self.feeds[url], etag="abc", final_url=url)
        return FetchResult(404, error="HTTP 404", final_url=url)


@pytest.fixture
def fake_repo():
    class R:
        def __init__(self):
            self.s, self.log = {}, []
        def settings_all(self): return dict(self.s)
        def setting_set(self, k, v): self.s[k] = v
        def setting_delete(self, k): self.s.pop(k, None)
        def audit(self, kind, detail=""): self.log.append((kind, detail))
    return R()


@pytest.fixture
def container(tmp_path):
    from app.container import build
    from app.dao.repo.postgres import Repository
    import psycopg
    with psycopg.connect(DB_URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")
    cfg = Config(data_dir=tmp_path, database_url=DB_URL, secret_key=b"k" * 32, session_hours=1, static_dir=None, cookie_secure=False,
                 scheduler=False, user_agent="NewsAnchor-test/0", shared_dir=tmp_path / "shared", mediacloud_token="", model_dir=tmp_path / "models")
    cont = build(cfg, Repository(DB_URL), fetcher=FakeFetcher({}))
    cont.repo.open()
    cont.stories.cache_s = 0
    cont.ingest.seed()
    cont.storage.ensure_partitions()
    yield cont
    cont.repo.close()


@pytest.fixture
def client(container):
    from fastapi.testclient import TestClient
    from app.api import auth
    from app.api.main import create_app
    auth._attempts.clear()                      # the login rate limit is per process; every test starts fresh
    app = create_app(container.config, container)
    with TestClient(app) as c:
        yield c
