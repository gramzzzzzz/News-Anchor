from pathlib import Path

import pytest

from app.dao.trust import lin2023, wikipedia_rsp
from app.dao.trust.domains import candidates, registrable
from app.dao.trust.transparency import analyse
from app.services import trust_scoring as ts
from app.services.trust_scoring import Sig

TH = {"highly": 0.80, "reliable": 0.65, "mixed": 0.45}


def test_score_and_confidence_by_hand():
    # (1*0.5 + 3*0.705 + 3*0.85 + 1*0.5) / (1 + 3 + 3 + 1) = 5.665 / 8
    sigs = [Sig("lin2023", 0.705, 3), Sig("wikipedia_rsp", 0.85, 3), Sig("transparency", 0.5, 1)]
    score, conf = ts.combine(sigs)
    assert score == pytest.approx(5.665 / 8) and conf == pytest.approx(7 / 9)
    assert ts.confidence_label(conf) == "high"
    assert ts.assign_band(score, conf, sigs, TH) == "reliable"          # 0.708 is below the 0.80 line for Highly reliable


def test_bands():
    hi = [Sig("lin2023", 0.88, 3), Sig("wikipedia_rsp", 0.85, 3)]
    s, c = ts.combine(hi)
    assert s == pytest.approx(5.69 / 7) and c == pytest.approx(0.75) and ts.assign_band(s, c, hi, TH) == "highly_reliable"
    mid = [Sig("lin2023", 0.5, 3)]
    assert ts.assign_band(*ts.combine(mid), mid, TH) == "mixed"
    low = [Sig("lin2023", 0.1, 3), Sig("wikipedia_rsp", 0.15, 3)]
    assert ts.assign_band(*ts.combine(low), low, TH) == "low"


def test_no_expert_signal_means_unrated_whatever_the_weak_signals_say():
    weak = [Sig("transparency", 1.0, 1), Sig("domain_age", 0.9, 0.5), Sig("ownership", 0.75, 0.5), Sig("corroboration", 1.0, 1)]
    s, c = ts.combine(weak)
    assert s > 0.7 and ts.assign_band(s, c, weak, TH) == "unrated"
    assert ts.assign_band(None, None, [], TH) == "unrated"


def test_deprecated_blocks_and_override_wins():
    dep = [Sig("wikipedia_rsp", 0.0, 3), Sig("lin2023", 0.7, 3)]
    assert ts.assign_band(*ts.combine(dep), dep, TH) == "blocked"
    assert ts.assign_band(*ts.combine(dep), dep, TH, override="reliable") == "reliable"
    ok = [Sig("lin2023", 0.9, 3)]
    assert ts.assign_band(*ts.combine(ok), ok, TH, override="blocked") == "blocked"


def test_visibility_and_ranking_weight():
    assert ts.is_visible("reliable", "reliable", True) and not ts.is_visible("mixed", "reliable", True)
    assert ts.is_visible("mixed", "mixed", True) and ts.is_visible("highly_reliable", "highly_reliable", True)
    assert not ts.is_visible("reliable", "highly_reliable", True)
    assert ts.is_visible("unrated", "highly_reliable", True) and not ts.is_visible("unrated", "reliable", False)
    assert not ts.is_visible("blocked", "low", True)
    assert ts.rank_weight("reliable", 0.7, 0.6, 1.5) == pytest.approx(0.7 ** 1.5, abs=1e-4)
    assert ts.rank_weight("unrated", None, 0.6, 1.5) == pytest.approx(0.6 ** 1.5, abs=1e-4)
    assert ts.rank_weight("highly_reliable", 0.9, 0.6, 1.5) > ts.rank_weight("reliable", 0.7, 0.6, 1.5)


def test_rsp_values():
    assert [ts.rsp_value(x) for x in ("Generally reliable", "No consensus", "Generally unreliable", "Deprecated", "Blacklisted")] == [0.85, 0.5, 0.15, 0.0, 0.0]


def test_lin_parse_skips_paths_and_bad_rows():
    d = lin2023.parse("domain,pc1\nreuters.com,1\nthehindu.com,0.705\ntheguardian.com/observer,0.7\nbad.example,notanumber\n,0.5\n")
    assert d == {"reuters.com": 1.0, "thehindu.com": pytest.approx(0.705)}


def test_rsp_parse_real_excerpt():
    es = {e.name.split(" (")[0]: e for e in wikipedia_rsp.parse((Path(__file__).parent / "fixtures" / "rsp_excerpt.html").read_text(encoding="utf-8"))}
    assert es["The Hindu"].status == "generally reliable" and es["The Hindu"].domains == ["thehindu.com"] and es["The Hindu"].shortcut == "WP:THEHINDU"
    assert es["Daily Mail"].status == "deprecated" and "dailymail.co.uk" in es["Daily Mail"].domains
    assert "bbc.com" in es["BBC"].domains and es["BBC"].url.endswith("WP:RSPBBC")
    assert any(e.status == "no consensus" for e in es.values()) and "Hindu" in es["The Hindu"].summary


def test_domain_helpers():
    assert registrable("sportstar.thehindu.com") == "thehindu.com" and registrable("news.bbc.co.uk") == "bbc.co.uk"
    assert candidates("sportstar.thehindu.com") == ["sportstar.thehindu.com", "thehindu.com"]
    assert registrable("www.infobae.com.ar") == "infobae.com.ar" and registrable("nation.africa") == "nation.africa"
    assert registrable("www.nation.co.ke") == "nation.co.ke" and registrable("www.pib.gov.in") == "pib.gov.in"
    assert candidates("importai.substack.com") == ["importai.substack.com"]          # a shared host says nothing about one publication


def test_transparency_analysis():
    html = """<html><head><script type="application/ld+json">{"@type":"NewsMediaOrganization","correctionsPolicy":"https://x/c","ownershipFundingInfo":"https://x/o"}</script></head>
    <body><footer><a href="/about-us">About us</a> <a href="/contact">Contact us</a> <a href="/editorial-standards">Our editorial standards</a>
    <a href="/team">Our team</a></footer><a href="/sport">Sport</a></body></html>"""
    f = analyse(html)
    assert set(f) == {"about", "contact", "standards", "masthead", "corrections", "ownership"}
    assert f["corrections"] == "declared in structured data" and "editorial standards" in f["standards"]
    assert analyse("<html><body><a href='/news'>News</a></body></html>") == {}


async def test_rdap_backs_off_then_gives_up_on_429(monkeypatch):
    import asyncio
    import httpx
    from app.dao.trust import external

    slept: list[float] = []

    async def fake_sleep(n):
        slept.append(n)

    monkeypatch.setattr(external.asyncio, "sleep", fake_sleep)
    calls = {"n": 0}

    def limited(request):
        calls["n"] += 1
        return httpx.Response(429, headers={"retry-after": "3"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(limited)) as c:
        with pytest.raises(external.RateLimited):
            await external.rdap_registration(c, "example.com")
    assert calls["n"] == 3 and slept == [3.0, 3.0]

    ok = httpx.Response(200, json={"events": [{"eventAction": "registration", "eventDate": "1999-03-04T00:00:00Z"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: ok)) as c:
        import datetime as dt
        assert await external.rdap_registration(c, "example.com") == dt.date(1999, 3, 4)


def test_one_rating_set_never_calls_a_source_low_and_disagreement_is_noted():
    from app.services import trust_scoring as ts
    th = {"highly": 0.8, "reliable": 0.65, "mixed": 0.45}
    lin_low = ts.Sig("lin2023", 0.1, 3.0)
    sc, cf = ts.combine([lin_low])
    assert sc < th["mixed"] and ts.assign_band(sc, cf, [lin_low], th) == "mixed"            # one set: Mixed, never Low
    assert "one expert rating set" in ts.explain([lin_low], "mixed")
    rsp_low = ts.Sig("wikipedia_rsp", 0.15, 3.0)
    sc, cf = ts.combine([lin_low, rsp_low])
    assert ts.assign_band(sc, cf, [lin_low, rsp_low], th) == "low"                           # two agree: Low
    split = [ts.Sig("lin2023", 0.9, 3.0), ts.Sig("wikipedia_rsp", 0.15, 3.0)]
    assert "disagree" in ts.explain(split, "mixed")
    assert ts.assign_band(*ts.combine(split), split, th) == "mixed"
    dep = [ts.Sig("wikipedia_rsp", 0.0, 3.0)]
    assert ts.assign_band(*ts.combine(dep), dep, th) == "blocked"
    assert ts.assign_band(0.1, 0.9, [lin_low], th, override="reliable") == "reliable"


def test_hysteresis_keeps_a_band_unless_clearly_past_the_threshold():
    from app.services import trust_scoring as ts
    th = {"highly": 0.8, "reliable": 0.65, "mixed": 0.45}
    assert ts.hysteresis("mixed", 0.645, "reliable", th, 0.02) == "reliable"                 # 0.005 under the line: keep
    assert ts.hysteresis("mixed", 0.60, "reliable", th, 0.02) == "mixed"                     # clearly under: change
    assert ts.hysteresis("highly_reliable", 0.81, "mixed", th, 0.02) == "highly_reliable"    # jumping two bands: crossed lines are far apart
    assert ts.hysteresis("reliable", 0.66, "unrated", th, 0.02) == "reliable"
    assert ts.hysteresis("mixed", 0.644, "reliable", th, 0) == "mixed"


def test_story_verification_levels():
    from app.services.stories import verification
    s = lambda k, b: {"key": k, "band": b}
    assert verification([s("a", "reliable"), s("b", "highly_reliable")])["level"] == "confirmed"
    assert verification([s("a", "reliable"), s("b", "mixed")])["level"] == "reported"
    assert verification([s("a", "highly_reliable")])["level"] == "single"
    assert verification([s("a", "unrated")])["level"] == "caution"
    assert verification([s("a", "low"), s("b", "mixed")])["level"] == "caution"
    assert verification([s("a", "low"), s("a", "low")])["outlets"] == 1


def test_wire_copy_counts_once_in_the_verification_mark():
    from app.services.stories import verification
    wire = [{"key": f"station{i}", "band": "reliable", "grp": "same-wire-text"} for i in range(12)]
    v = verification(wire)
    assert v["level"] == "single" and v["outlets"] == 12 and v["independent"] == 1 and "12 outlets carry the same text" in v["label"]
    mixed = wire + [{"key": "bbc", "band": "highly_reliable", "grp": "own-text"}]
    v = verification(mixed)
    assert v["level"] == "confirmed" and v["reliable"] == 2 and "13 outlets carry it" in v["label"]
    unrated_copies = [{"key": f"u{i}", "band": "unrated", "grp": "g"} for i in range(4)]
    assert verification(unrated_copies)["level"] == "caution"
