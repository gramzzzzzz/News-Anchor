"""Ranking without a database: how activity scales an ongoing story (playbook 20.27)."""
from app.services import ranking
from app.services.ranking import Params


def row(i, bucket, score, recent):
    return {"id": i, "bucket": bucket, "score": score, "recent_n": recent, "indep_p": 5, "fair": score}


def ids(rows):
    return [r["id"] for r in rows]


ROWS = lambda: [row(1, "ongoing", 10.0, 0), row(2, "ongoing", 8.0, 12), row(3, "fresh", 5.0, 5)]


def test_activity_is_the_share_of_recent_outlets_over_recent_plus_the_scale():
    p = Params(active_soft=6.0)
    assert ranking.activity({"recent_n": 0}, p) == 0.0
    assert ranking.activity({"recent_n": 6}, p) == 0.5
    assert round(ranking.activity({"recent_n": 30}, p), 2) == 0.83
    assert ranking.activity({"recent_n": 0}, Params(active_soft=0.0)) == 1.0           # off


def test_a_quiet_ongoing_story_sinks_in_aggregated_and_in_the_ongoing_list():
    p = Params(active_soft=6.0)
    assert ids(ranking.order(ROWS(), "top", "ongoing", p, 2)) == [2, 1]                # the bigger quiet story falls below the active one
    assert ids(ranking.order(ROWS(), "top", "all", p, 2)) == [3, 2, 1]                 # and last of all in Aggregated, below the fresh story
    off = Params(active_soft=0.0)
    assert ids(ranking.order(ROWS(), "top", "ongoing", off, 2)) == [1, 2]
    assert ids(ranking.order(ROWS(), "top", "all", off, 2))[0] == 1                    # without scaling the biggest ongoing story shares the top


def test_fresh_stories_and_trending_are_not_scaled():
    p = Params(active_soft=6.0)
    fresh = [row(1, "fresh", 3.0, 0), row(2, "fresh", 2.0, 0)]
    assert ids(ranking.order(fresh, "top", "fresh", p, 2)) == [1, 2]
    assert all(r["act"] == 1.0 for r in fresh)
