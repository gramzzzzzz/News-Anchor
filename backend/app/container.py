"""Composition root: the only place where concrete classes are chosen and wired together."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.config import Config
from app.dao.fetch.http import Fetcher
from app.dao.repo.postgres import Repository
from app.ai.router import AiRouter
from app.ai.service import AiService
from app.services.discovery import DiscoveryService
from app.services.embedder import Embedder
from app.services.events import EventBus
from app.services.extract import ExtractService
from app.services.meaning import MeaningCheck
from app.services.merge import StoryMerger
from app.services.search import SearchService
from app.services.stats import StatsService
from app.services.ingest import IngestService
from app.services.prefetch import PrefetchService
from app.services.social import SocialService
from app.services.places import Gazetteer
from app.services.resources import ResourceGovernor
from app.services.settings_service import SettingsService
from app.services.stories import StoryService
from app.services.storage import StorageGuard
from app.services.storylines import StorylineService
from app.services.headlines import HeadlineService
from app.services.translate import Translator
from app.services.topics import Taxonomy
from app.services.topics_service import TopicService
from app.services.trust import TrustService
from app.services.upgrade import ReliabilityUpgrade

log = logging.getLogger(__name__)


@dataclass
class Container:
    config: Config
    repo: Repository
    settings: SettingsService
    taxonomy: Taxonomy
    gazetteer: Gazetteer
    bus: EventBus
    fetcher: Fetcher
    governor: ResourceGovernor
    ingest: IngestService
    stories: StoryService
    topics: TopicService
    storage: StorageGuard
    trust: TrustService
    discovery: DiscoveryService
    embedder: Embedder
    merger: StoryMerger
    search: SearchService
    extract: ExtractService
    ai_router: AiRouter
    ai: AiService
    stats: StatsService
    translator: Translator
    social: SocialService | None = None
    prefetch: PrefetchService | None = None
    meaning: MeaningCheck | None = None
    storylines: StorylineService | None = None


def build(config: Config, repo: Repository | None = None, fetcher: Fetcher | None = None) -> Container:
    repo = repo or Repository(config.database_url)
    settings = SettingsService(repo)
    taxonomy = Taxonomy.load()
    gazetteer = Gazetteer()
    bus = EventBus()
    fetcher = fetcher or Fetcher(config.user_agent)
    fetcher.robots_on = lambda: bool(settings.get("fetch.robots"))
    fetcher.impersonate_profile = lambda: str(settings.get("fetch.impersonate_profile")) if settings.get("fetch.impersonate") else None
    fetcher.solver_config = lambda: ({"url": str(settings.get("fetch.flaresolverr_url")), "timeout": int(settings.get("fetch.flaresolverr_timeout_s")),
                                      "parallel": int(settings.get("fetch.flaresolverr_parallel")), "per_hour": int(settings.get("fetch.flaresolverr_per_hour"))}
                                     if settings.get("fetch.flaresolverr") else None)
    settings.listeners.append(lambda: None if settings.get("fetch.robots") else fetcher.flush_robots())   # switching it off discards what was kept
    governor = ResourceGovernor(settings, config.shared_dir)
    ingest = IngestService(repo, settings, taxonomy, gazetteer, fetcher, governor, bus)
    stories = StoryService(repo, settings, taxonomy, gazetteer)
    topics = TopicService(repo, taxonomy, stories)
    trust = TrustService(repo, settings, fetcher, bus)
    embedder = Embedder(settings, str(config.model_dir))
    merger = StoryMerger(repo, settings, embedder, bus, gazetteer)
    merger.gate = ingest._db_gate                      # a merge pass writes under the same gate as the feeds' own storing
    meaning = MeaningCheck(repo, settings)
    headlines = HeadlineService(repo, settings)
    translator = Translator(repo, settings, str(config.model_dir), config.user_agent)

    async def after_cycle():
        t = await translator.run()                     # English first, so new stories in other languages can be clustered by meaning
        await translator.close()                       # one worker at a time: the translation and meaning models together outgrew the memory limit
        m = await merger.run()
        async with ingest._db_gate:                    # both write stories' fields: one writer at a time, like the feeds' own storing
            try:
                m["headlines"] = await asyncio.to_thread(headlines.run)   # after merging: a story's reports are final for this refresh
            except Exception:                          # noqa: BLE001 - headlines improve the lists; a failure must not fail the refresh
                log.exception("Headline choice failed")
            try:
                m["meaning"] = await meaning.run()     # needs the new stories' vectors, so after merging
            except Exception:                          # noqa: BLE001 - the check improves tags; a failure must not fail the refresh
                log.exception("Meaning check failed")
        return {"translate": t, **m}

    ingest.after_cycle = after_cycle
    extract = ExtractService(repo, settings, fetcher)
    router = AiRouter(repo, settings)
    discovery = DiscoveryService(repo, settings, fetcher, bus, token_fallback=config.mediacloud_token, taxonomy=taxonomy, gazetteer=gazetteer)
    social = SocialService(repo, settings, config.user_agent)
    ingest.social = social
    discovery.upgrader = ReliabilityUpgrade(repo, settings, discovery, gazetteer)
    discovery.on_cards = ingest.ingest_cards          # stories on Google News pages from publishers already collected
    cont = Container(config, repo, settings, taxonomy, gazetteer, bus, fetcher, governor, ingest, stories, topics,
                     StorageGuard(repo, settings, config.data_dir), trust, discovery,
                     embedder, merger, SearchService(repo, settings, embedder, stories, taxonomy, gazetteer), extract, router,
                     AiService(repo, settings, router, extract, stories), StatsService(repo, settings, taxonomy, gazetteer, router, embedder, discovery), translator, social)
    cont.meaning = meaning
    cont.storylines = StorylineService(repo, settings, taxonomy, gazetteer, stories)
    cont.prefetch = PrefetchService(repo, settings, stories, cont.ai, extract, router)
    cont.stats.translator, cont.stats.prefetch, cont.stats.social = translator, cont.prefetch, social
    return cont
