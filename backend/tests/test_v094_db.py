"""v0.9.4: the line under the headline, languages not collected, reprints, storylines (anchors), the Storylines tab, estimated trust."""
import datetime as dt

import pytest

from app.services.anchors import anchors, different_events
from app.services.textutil import clean_lead, clean_leads, title_key
from app.services import trust_scoring as ts
from app.services.trust_scoring import Sig

pytestmark = pytest.mark.db
UTC = dt.timezone.utc
NOW = lambda: dt.datetime.now(UTC)
HINDU = "https://www.thehindu.com/news/national/feeder/default.rss"
MINT = "https://www.livemint.com/rss/news"
BBC = "https://feeds.bbci.co.uk/news/world/rss.xml"


def feed_row(c, url):
    return next(f for f in c.repo.all_feeds() if f["url"] == url)


def it(title, url, lead="", mins=10):
    return {"title": title, "url": url, "lead": lead, "published": NOW() - dt.timedelta(minutes=mins)}


def test_lead_keeps_only_the_headlines_writing_system_and_drops_a_feeds_shared_notice():
    cedar = ("نُشر هذا المقال أولاً عبر Cedarnews.net . لمتابعة المزيد من الأخبار والتقارير الحصرية، زورونا على موقعنا. "
             "Severe thunderstorms are possible Sunday afternoon.")
    assert clean_lead("Severe Storms Threaten New Mexico, Texas and Oklahoma", cedar) == "Severe thunderstorms are possible Sunday afternoon."
    items = [{"title": f"Story {i} headline here", "lead": f"Item {i} is about a thing. This article first appeared on Example News."} for i in range(4)]
    clean_leads(items)
    assert [x["lead"] for x in items] == [f"Item {i} is about a thing." for i in range(4)]


def test_names_tell_two_grand_prix_apart():
    gp_f1 = "Hamilton wins pole for Ferrari at the Mexico City Grand Prix"
    gp_moto = "MotoGP Malaysian GP: Pedro Acosta beats Marc Marquez in tense sprint"
    a, b = anchors(gp_f1), anchors(gp_moto)
    assert "ferrari" in a and "@MY" in b and not (a & b)
    assert different_events(a, b, title_key(gp_f1), title_key(gp_moto), set())
    # same event, different names for the place: a shared rare word ties them
    x, y = "Helicopter returning from Badrinath Dham crashes near Joshimath", "Six killed in helicopter crash in Indian Himalayan state"
    assert not different_events(anchors(x), anchors(y), title_key(x), title_key(y), set())


def test_ingest_never_puts_two_series_in_one_story(container):
    import time
    c = container
    c.ingest._frequent = ({"win", "pole", "dominate", "qualifying"}, time.monotonic())   # as in a real 72-hour window: everyday race words
    c.ingest.process_items(feed_row(c, HINDU), [it("Marquez wins pole at Malaysian Grand Prix as Ducati dominate qualifying", "https://www.thehindu.com/m1")])
    c.ingest.process_items(feed_row(c, MINT), [it("Hamilton wins pole at Mexico City Grand Prix as Ferrari dominate qualifying", "https://www.livemint.com/f1")])
    assert c.repo.one("SELECT count(*) AS n FROM stories")["n"] == 2


def test_languages_not_collected_are_never_stored(container):
    c = container
    n, _s = c.ingest.process_items(feed_row(c, HINDU), [it("Der Bundestag beschließt neues Gesetz über die Rente und die Steuer für alle", "https://www.thehindu.com/de1",
                                                              lead="Die Abgeordneten haben am Freitag mit großer Mehrheit für das Gesetz gestimmt, das im nächsten Jahr gilt.")])
    assert n == 0 and c.repo.one("SELECT count(*) AS n FROM articles")["n"] == 0


def test_a_word_for_word_copy_is_a_reprint_not_an_article(container):
    c = container
    t, lead = "Port authority signs dredging deal with consortium worth crores", "The port authority on Monday signed a dredging contract with a consortium."
    c.ingest.process_items(feed_row(c, HINDU), [it(t, "https://www.thehindu.com/p1", lead)])
    c.ingest.process_items(feed_row(c, MINT), [it(t, "https://www.livemint.com/p1", lead)])          # wire copy
    assert c.repo.one("SELECT count(*) AS n FROM articles")["n"] == 1
    assert c.repo.one("SELECT count(*) AS n FROM reprints")["n"] == 1
    st = c.repo.one("SELECT id, outlets, indep FROM stories")
    assert (st["outlets"], st["indep"]) == (2, 1)                        # two outlets carry it, one independent text
    d = c.stories.detail(st["id"])
    assert [r["source"]["key"] for r in d["reprints"]] == ["mint"]
    # polling the copy again adds nothing
    assert c.ingest.process_items(feed_row(c, MINT), [it(t, "https://www.livemint.com/p1", lead)]) == (0, 0)


def test_storylines_tab_ranks_storylines_over_the_period(container):
    c = container
    old = NOW() - dt.timedelta(hours=40)
    c.repo.ensure_partition(old.date())
    t1 = "Kansas Senate passes school funding overhaul after marathon session"
    for k, url in (("hindu", HINDU), ("mint", MINT)):                    # each outlet in its own words (identical headlines would be one text)
        c.ingest.process_items(feed_row(c, url), [{"title": f"{t1}, {k} reports", "url": f"https://x.example/{k}1", "lead": "", "published": old}])
    t2 = "Kansas Senate school funding overhaul signed by governor Kelly"
    for k, url in (("hindu", HINDU), ("mint", MINT), ("bbc", BBC)):
        c.ingest.process_items(feed_row(c, url), [{"title": f"{t2} ({k})", "url": f"https://x.example/{k}2", "lead": "", "published": NOW() - dt.timedelta(minutes=20)}])
    devs = c.repo.q("SELECT id, thread_id FROM stories ORDER BY first_seen")
    assert len(devs) == 2 and devs[0]["thread_id"] == devs[1]["thread_id"]    # one storyline, two developments
    out = c.storylines.list("world", "", 72)
    assert len(out["storylines"]) == 1
    L = out["storylines"][0]["storyline"]
    assert L["developments"] == 2 and L["developments_in_range"] == 2 and len(L["activity"]) == 24 and sum(L["activity"]) >= 3
    assert len(c.storylines.list("world", "", 6)["storylines"]) == 1          # the newer development is in the last 6 hours too


def test_an_estimated_band_is_never_highly_reliable_and_never_confirms():
    th = {"highly": 0.8, "reliable": 0.65, "mixed": 0.45, "estimate_hi": 0.8, "estimate_lo": 0.3}
    sure = [Sig("webgraph", 0.95, 0.0), Sig("estimate_model", 0.97, 0.0), Sig("estimate_score", 0.9, 0.0)]
    assert ts.assign_band(0.9, 0.94, sure, th) == "reliable" and ts.basis(sure, "reliable") == "estimated"
    unsure = [Sig("estimate_model", 0.6, 0.0)]
    assert ts.assign_band(0.6, 0.2, unsure, th) == "unrated"                                      # between the cuts: no band
    assert ts.assign_band(0.4, 0.6, [Sig("estimate_model", 0.2, 0.0)], th) == "mixed"
    from app.services.stories import verification
    v = verification([{"key": "a", "band": "reliable", "basis": "estimated", "grp": "1"}, {"key": "b", "band": "reliable", "basis": "estimated", "grp": "2"}])
    assert v["level"] == "reported"


def test_the_estimate_model_learns_from_rated_sources_and_abstains_when_unsure():
    import random
    from app.services import trust_model as tm
    rnd = random.Random(4)
    X, y = [], []
    for _ in range(600):
        good = rnd.random() < 0.6
        w = min(1, max(0, rnd.gauss(0.8 if good else 0.45, 0.12)))
        X.append(tm.features({"webgraph": (w, 1.0)}))
        y.append(1 if good else 0)
    oof = tm.out_of_fold(X, y)
    m = tm.cuts(oof, y, 0.85, 0.7)
    assert m["auc"] > 0.85 and m["hi"] is not None and m["hi_precision"] >= 0.85 and m["lo"] is not None and m["lo"] < m["hi"]
