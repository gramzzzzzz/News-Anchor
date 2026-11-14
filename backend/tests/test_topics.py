import pytest

from app.services.topics import Taxonomy


@pytest.fixture(scope="module")
def tax():
    return Taxonomy.load()


def codes(tax, title, lead="", hints=None, country=""):
    return set(tax.classify(title, lead, hints, country=country))


def test_taxonomy_shape(tax):
    """The 17 IPTC categories, one level of sub-topics, and no sub-topic that names a place (the place axis does that)."""
    assert len(tax.roots) == 17 and {"politics", "economy", "sport", "science", "weather", "religion", "education", "labour"} <= set(tax.roots)
    assert max(t.depth for t in tax.topics.values()) == 2
    assert 90 <= len(tax.topics) - len(tax.roots) <= 140
    placey = {"india", "indian", "tamil", "hindi", "us", "american", "chinese", "european", "africa", "asia", "east", "russia", "ukraine"}
    assert not [t.name for t in tax.topics.values() if t.depth == 2 and placey & set(t.name.lower().split()) and t.code != "sport.american-football"]
    enabled = [t for t in tax.topics.values() if t.default_enabled]
    assert 40 < len(enabled) < 110 and not any(t.default_favourite for t in tax.topics.values())


def test_motor_racing_with_and_without_feed_hint(tax):
    assert "sport.motor-racing" in codes(tax, "Verstappen takes pole for the Mexico City Grand Prix")
    assert "sport.motor-racing" in codes(tax, "Hirakawa to replace Ocon at Haas for Mexico City Grand Prix", hints=["sport.motor-racing"])


def test_ambiguous_names_need_context(tax):
    assert "sport.motor-racing" not in codes(tax, "Hamilton city council approves new budget")
    assert "sport.motor-racing" in codes(tax, "Hamilton and Leclerc clash at Ferrari")
    assert "sport.motor-racing" not in codes(tax, "India vs New Zealand 5th T20I Playing 11: Will IND recall Sooryavanshi for Samson in Hamilton?")
    assert "sport.cricket" in codes(tax, "India vs New Zealand 5th T20I Playing 11: Will IND recall Sooryavanshi for Samson in Hamilton?")


@pytest.mark.parametrize("title,want", [
    ("Gukesh beats Carlsen in Norway Chess", "sport.chess"),
    ("Crypto giant Binance hit by $6.3bn hack suspected to be largest in history", "science.cybersecurity"),
    ("Crypto giant Binance hit by $6.3bn hack", "economy.crypto"),
    ("OpenAI releases a new ChatGPT model", "science.ai"),
    ("India vs Australia: Kohli hits a century in the second Test", "sport.cricket"),
    ("Sensex falls 600 points as FPIs sell", "economy.markets"),
    ("Wall Street rallies as Nasdaq hits record", "economy.markets"),
    ("ISRO launches PSLV with five satellites from Sriharikota", "science.space"),
    ("Earthquake of magnitude 6.1 strikes off Japan, tsunami warning issued", "disaster.earthquakes"),
    ("Cyclone alert: heavy rain to lash Chennai", "disaster.storms-floods"),
    ("Heavy rain and thunderstorms forecast for the weekend", "weather"),
    ("Brent crude falls as OPEC+ agrees output increase", "economy.energy"),
    ("Farmers protest over MSP legal guarantee", "economy.agriculture"),
    ("Tech firm announces layoffs of 2,000 staff", "labour.jobs"),
    ("Tata Motors launches new electric SUV with 500 km range", "economy.autos"),
    ("Taylor Swift announces new world tour dates", "arts.music"),
    ("Jailer 2 box office collection day 3", "arts.film"),
    ("Pope Leo XIV casts his vote by mail", "religion"),
    ("NEET results declared, toppers list out", "education.higher-education"),
    ("Daily horoscope for Aries, Taurus and Gemini", "lifestyle.astrology"),
    ("Mohun Bagan beat East Bengal in the Kolkata derby", "sport.football"),
])
def test_categories_classify(tax, title, want):
    assert want in codes(tax, title), (title, codes(tax, title))


def test_football_means_american_football_in_the_us(tax):
    assert "sport.american-football" in codes(tax, "Friday night football: Central beats North", country="US")
    assert "sport.football" in codes(tax, "Friday night football: Central beats North", country="GB")


def test_ancestors_included_and_no_false_positive_on_plain_text(tax):
    c = tax.classify("Verstappen wins the Grand Prix", "Formula 1 champion")
    assert {"sport", "sport.motor-racing"} <= set(c)
    assert codes(tax, "A quiet day in the village") == set()


def test_feed_gating_follows_the_topic_picker(tax):
    enabled = {c for c, t in tax.topics.items() if t.default_enabled}
    gate = lambda *h: tax.feed_active(list(h), enabled)
    assert gate("sport.motor-racing") and gate("sport.cricket") and gate("arts.video-games")
    assert not gate("sport.gaelic")                        # not in the menus by default: its specialist feeds wait
    assert gate("science")                                 # category-level hints never gate
    assert tax.feed_active(["sport.gaelic"], enabled | {"sport.gaelic"})


def test_feed_hint_is_a_prior_not_a_label(tax):
    assert "science.ai" not in codes(tax, "The races that will decide control of the US Senate", hints=["science.ai"])
    assert "science.ai" in codes(tax, "Anthropic and Google expand a model partnership", "The deal covers new AI chips.", hints=["science.ai"])


def test_everyday_words_do_not_trigger_topics(tax):
    assert "economy.energy" not in codes(tax, "Power struggle inside the ruling party deepens")
    assert "sport.other-sports" not in codes(tax, "Man shot dead outside his home in Pune")
    assert "labour" not in codes(tax, "Jobs minister says talks with the opposition will continue")
