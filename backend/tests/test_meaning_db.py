"""The meaning check: a sub-topic tag whose headline's meaning does not fit the sub-topic is rejected and left out everywhere."""
import numpy as np
import pytest

from app.services.meaning import judge, score

pytestmark = pytest.mark.db


def test_judge_rejects_parents_only_when_all_their_subtopics_are_rejected():
    fails = {"sport": True, "sport.football": True, "sport.cricket": False, "economy": True, "economy.markets": True}
    assert judge(["sport", "sport.football", "sport.cricket"], fails) == {"sport.football"}
    assert judge(["sport", "sport.football"], fails) == {"sport", "sport.football"}
    assert judge(["economy"], fails) == set()                                # a category with no sub-topic is never rejected
    assert judge(["sport", "sport.football"], {**fails, "sport": False}) == {"sport.football"}   # the category itself passes
    assert judge(["arts.film"], fails) == set()                               # not judged (no model): kept


def test_score_follows_the_signals():
    base = score(0.5, 0.2, 0.05, False, False)
    assert score(0.9, 0.2, 0.05, False, False) > base > score(0.1, 0.2, 0.05, False, False)
    assert score(0.5, 0.6, 0.05, False, False) > base > score(0.5, 0.0, 0.05, False, False)
    assert score(0.5, 0.2, 0.05, False, True) > base > score(0.5, 0.2, 0.05, True, False)
    assert score(0.5, 0.04, 0.001, False, False) > score(0.5, 0.04, 0.2, False, False)       # one neighbour of a rare topic says more


def test_url_sections():
    from app.services import urlsection as u
    from app.services.topics import Taxonomy
    t = Taxonomy.load()
    assert not [c for c in set(u.SECTIONS.values()) if c not in t.topics]
    assert u.sections("https://www.thehindu.com/sport/cricket/x/article1.ece") == ["sport", "sport.cricket"]
    assert u.sections("https://www.bbc.com/news/articles/c1") == [] and u.sections("https://x.example/2026/11/01/slug-of-story") == []
    assert u.evidence("sport.cricket", ["sport"]) == 1 and u.evidence("economy.markets", ["sport", "sport.cricket"]) == -1
    assert u.evidence("sport.football", ["sport.cricket"]) == 0


def _vec(v):
    return "[" + ",".join(f"{x:.5f}" for x in v) + "]"


def test_meaning_check_rejects_tags_whose_meaning_does_not_fit(container):
    c = container
    rng = np.random.default_rng(1)
    src = c.repo.one("SELECT key FROM sources LIMIT 1")["key"]
    a, b = np.zeros(384), np.zeros(384)
    a[0], b[1] = 1.0, 1.0

    def story(direction, tags):
        v = direction + rng.normal(0, 0.02, 384)          # stories about one subject lie close together
        sid = c.repo.one("""INSERT INTO stories (title, first_seen, last_update, articles, lead_source, lead_url, embedding, embed_backend)
                            VALUES ('t', now(), now(), 1, %s, 'https://x.example/', %s::vector, 'model') RETURNING id""", (src, _vec(v)))["id"]
        for t in tags:
            c.repo.run("INSERT INTO story_topics (story_id, code, n) VALUES (%s, %s, 1)", (sid, t))
        return sid

    for _ in range(500):
        story(a, ["sport", "sport.football"])
    for _ in range(570):
        story(b, ["economy", "economy.markets"])
    wrong = [story(b, ["sport", "sport.football"]) for _ in range(30)]           # a trading story tagged football ("derby", "transfer")
    mixed = story(b, ["economy", "economy.markets", "sport", "sport.football"])
    right = story(a, ["sport", "sport.football"])
    c.repo.kv_set("tag_version", "test")

    m = c.meaning
    assert "topics" in m.train("test")
    res = m.apply()
    assert res["stories"] == 1102
    rej = {(r["story_id"], r["code"]) for r in c.repo.q("SELECT story_id, code FROM story_topics WHERE rejected")}
    caught = sum((w, "sport.football") in rej for w in wrong)
    assert caught >= 25 and all(((w, "sport.football") in rej) == ((w, "sport") in rej) for w in wrong)
    assert (mixed, "sport.football") in rej and (mixed, "economy.markets") not in rej
    assert not any(sid == right for sid, _ in rej)
    assert len(rej) < 2 * 30 + 10                                                 # right tags are kept

    inc = m.apply(1.5)                                                            # the check after a refresh: recently updated stories only
    assert inc["stories"] == 1102 and inc["changed"] == 0
    tt, _pt = c.repo.story_tags([mixed, right])
    assert "sport.football" not in tt[mixed] and "economy.markets" in tt[mixed] and "sport.football" in tt[right]
    t, _p = c.repo.facets(place="world", topic="", window_h=24, min_outlets=1)
    assert t["sport.football"] == 501 + (30 - caught)

    c.settings.set_many({"feed.meaning_check": False})                            # turned off: every tag counts again
    assert m.apply()["cleared"] == len(rej)
    assert not c.repo.q("SELECT 1 FROM story_topics WHERE rejected")
