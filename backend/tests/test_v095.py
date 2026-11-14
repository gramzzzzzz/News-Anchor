"""v0.9.5: Lin et al. coverage, weights learned from agreement, geographic focus, headlines chosen from the reports."""
import datetime as dt
import random

from app.dao.trust import lin2023
from app.services import trust_scoring as ts
from app.services.headlines import clean_title, pick, quality
from app.services.places import Gazetteer

UTC = dt.timezone.utc


def test_lin_tells_real_ratings_from_imputed_ones():
    text = ("domain,pc1,afm,fc,mbfc,lewandowsky_mean,misinfome_bin\n"
            "economictimes.indiatimes.com,0.2156,0.455039352178574,0.0433019250631332,0.374044477939606,0,0\n"
            "whale.to,0.7694,0.6312345678,0.512345678,0.701234567,0.712345678,0\n"
            "nytimes.com,0.8588,0.8137,1,0.8333,0.875,1\n")
    got = lin2023.parse_full(text)
    assert got["economictimes.indiatimes.com"][1] == ["Lewandowsky et al. list", "MisinfoMe list"]
    assert got["whale.to"][1] == ["MisinfoMe list"]                      # its 0.77 is imputed against the one real rating
    assert len(got["nytimes.com"][1]) == 4
    assert lin2023.coverage_factor(0) < lin2023.coverage_factor(1) < lin2023.coverage_factor(2) == 1.0


def test_weights_follow_agreement_not_assumption():
    rnd = random.Random(1)
    rows = []
    for _ in range(600):
        q = rnd.random()
        rows.append({"good": (q + rnd.gauss(0, 0.25), 1.0), "fair": (q + rnd.gauss(0, 0.35), 1.0), "ok": (q + rnd.gauss(0, 0.5), 1.0),
                     "noise": (rnd.random(), 1.0), "flat": (0.75, 1.0)})
    cal = ts.calibrate(rows, {"good": 1, "ok": 1, "fair": 1, "noise": 1, "flat": 1})
    assert cal["good"]["weight"] > cal["fair"]["weight"] > cal["ok"]["weight"] > cal["noise"]["weight"]
    assert cal["noise"]["learned"] < 0.2 and cal["flat"]["learned"] == 0.0


def test_focus_drops_places_mentioned_in_passing():
    g = Gazetteer()
    t = "India's 10-year G-Sec yield could settle in the 6.75-7.00% range by March-end 2027: SBI Ecowrap"
    lead = "The rupee will also have an appreciating bias with Dollar Index weakening post US mid-term elections"
    assert "US" in g.detect(t, lead, "IN") and set(g.detect_focus(t, lead, "IN")) == {"IN"}
    assert {"US", "CN"} <= set(g.detect_focus("US, China leaders meet in Busan as trade truce holds", "", "US"))
    assert "IN" not in g.detect_focus("Tech shares slump as AI spending fears drive investors to United States bonds - Telegraph India", "", "IN")
    assert "IN" in g.detect("RBI holds the repo rate at 5.5% for a third meeting", "", "")         # an institution names its country


def test_headline_is_the_representative_current_one():
    now = dt.datetime.now(UTC)
    reps = [{"title": "Browns go for first sweep of Steelers since 1988", "published": now - dt.timedelta(hours=30), "weight": 0.5, "lead": "", "url": "a", "source_key": "a"},
            {"title": "Steelers beat Browns 27-6 to take AFC North lead", "published": now - dt.timedelta(hours=1), "weight": 0.5,
             "lead": "Jaylen Warren rushed for 143 yards as the Steelers beat the Browns 27-6 on Sunday.", "url": "b", "source_key": "b"},
            {"title": "Steelers rout Browns 27-6, take AFC North lead - NewsDay", "published": now - dt.timedelta(hours=2), "weight": 0.5, "lead": "", "url": "c", "source_key": "c"},
            {"title": "WATCH LIVE: STEELERS VS BROWNS", "published": now, "weight": 0.5, "lead": "", "url": "d", "source_key": "d"}]
    p = pick(reps, {}, 1.0)
    assert p["url"] in ("b", "c") and "27-6" in p["title"] and "NewsDay" not in p["title"]
    assert p["lead"].startswith("Jaylen Warren")
    assert quality("WATCH LIVE: STEELERS VS BROWNS") < 0.5 and quality("@user: a post") == 0
    assert clean_title("Quad unveils AI initiative for the region | The Hindu") == "Quad unveils AI initiative for the region"


def test_a_social_post_never_titles_a_story_an_outlet_reported():
    now = dt.datetime.now(UTC)
    reps = [{"title": "@ft.com: Global AI sell-off eases ft.trib.al/W", "published": now, "weight": 0.5, "lead": "", "url": "s", "source_key": "s"},
            {"title": "Global AI sell-off eases after Nasdaq's worst month in over a year", "published": now, "weight": 0.5, "lead": "", "url": "n", "source_key": "n"}]
    assert pick(reps, {}, 1.0)["url"] == "n"
