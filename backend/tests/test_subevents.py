import datetime as dt

from app.services.subevents import find_subevents

T0 = dt.datetime(2026, 11, 12, 6, 0, tzinfo=dt.timezone.utc)


def rep(i, source, title):
    return {"published": T0 + dt.timedelta(minutes=i * 3), "source": source, "title": title, "url": f"https://x/{i}"}


def test_a_part_of_a_story_that_many_outlets_report_is_listed():
    rs = [rep(i, f"o{i % 60}", f"Nvidia chief Jensen Huang resigns after affair probe {i % 7}") for i in range(120)]
    rs += [rep(130 + i, f"p{i}", "Trump says Apple's Tim Cook should replace Jensen Huang at Nvidia") for i in range(25)]
    rs += [rep(220 + i, f"q{i}", "Nvidia names Colette Kress interim chief executive") for i in range(12)]
    ev = find_subevents(rs)
    labels = [e["label"] for e in ev]
    assert any("cook" in l for l in labels) and any("kress" in l or "colette" in l for l in labels)
    tim = next(e for e in ev if "cook" in e["label"])
    assert tim["outlets"] == 25 and tim["reports"] == 25 and "Tim Cook" in tim["headline"]
    assert not any("nvidia" in l or "huang" in l for l in labels)            # the story's own theme is not a part of it


def test_a_story_that_says_one_thing_has_no_parts():
    rs = [rep(i, f"o{i}", f"Flood warning issued for the river valley {i % 5}") for i in range(40)]
    assert find_subevents(rs) == []


def test_one_outlets_repetition_is_not_a_part():
    rs = [rep(i, f"o{i}", "Fire at the harbour warehouse destroys stock") for i in range(30)]
    rs += [rep(40 + i, "single", "Warehouse owner Smith blames faulty wiring") for i in range(10)]
    assert find_subevents(rs) == []
