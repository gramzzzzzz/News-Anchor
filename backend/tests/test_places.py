import pytest

from app.services.places import Gazetteer


@pytest.fixture(scope="module")
def gaz():
    return Gazetteer()


def test_hierarchy_and_ancestors(gaz):
    assert [p.name for p in gaz.breadcrumb("IN-TN-chennai")] == ["Global", "Asia", "South Asia", "India", "Tamil Nadu", "Chennai"]
    anc = set(gaz.ancestors("IN-TN-chennai"))
    assert anc == {"IN-TN", "IN", "south-asia", "asia"}                          # one line up the tree, no overlapping regions
    assert gaz.ancestors("DE") == ["western-europe", "europe"] and "eu" in gaz.places["DE"].regions and "middle-east" in gaz.places["AE"].regions
    assert "world" not in gaz.with_ancestors(["IN"])
    assert set(gaz.scope_codes("gcc")) == {"gcc", "BH", "KW", "OM", "QA", "SA", "AE"} and gaz.scope_codes("IN") == ["IN"]
    assert all(p.level == "country" for p in gaz.children("eu")) and len(gaz.children("eu")) == 27
    # every country sits under exactly one region of one continent
    assert all(gaz.places[p.parent].level == "subregion" for p in gaz.places.values() if p.level == "country" and p.code != "AQ")


def test_children(gaz):
    names = [p.name for p in gaz.children("world")]
    assert names == ["Africa", "Americas", "Asia", "Europe", "Oceania"]
    assert any(p.code == "IN-TN" for p in gaz.children("IN")) and any(p.code == "IN-TN-chennai" for p in gaz.children("IN-TN"))
    assert any(p.code == "IN" for p in gaz.children("south-asia"))


def test_detect(gaz):
    d = gaz.detect("Chennai police arrest former minister", "Madras High Court heard the plea")
    assert d["IN-TN-chennai"] == 3.0
    assert gaz.detect("Floods in Papua New Guinea") == {"PG": 3.0}           # not Guinea
    assert gaz.detect("UK and US sign trade deal").keys() >= {"GB", "US"}
    assert gaz.detect("Tamil Nadu Governor and Karnataka CM meet").keys() >= {"IN-TN", "IN-KA"}
    assert gaz.detect("Georgia and Jordan said") == {}                      # everyday words are left out on purpose
    assert gaz.detect("Delhi smog worsens").keys() == {"IN-DL-delhi"}           # the city (its state follows as an ancestor)


def test_shared_names_are_only_tagged_when_it_is_clear_which_place(gaz):
    d = lambda t, cc="": set(gaz.detect(t, "", cc))
    assert d("TN transfers prison officials at Madurai, Salem and Cuddalore", "IN") >= {"IN-TN", "IN-TN-salem", "IN-TN-madurai"}
    assert d("Salem man charged with fleeing DUI", "US") == {"US"}                 # a US outlet: some Salem in the US, not Tamil Nadu
    assert d("Salem: Two held for theft of temple jewels", "IN") == {"IN-TN-salem"}
    assert d("Witch trials museum opens in Salem, Massachusetts", "GB") == {"US-MA", "US-MA-salem"}
    assert "NZ-G1-hamilton" in d("India vs New Zealand 5th T20I: Samson in Hamilton?", "IN")
    assert d("Lewis Hamilton wins pole", "GB") == set() and d("Becky Burke envisions huge jump", "US") == set()
    assert d("Ground cinnamon recalled ahead of holiday season", "US") == set()
    assert d("UK and US sign trade deal") == {"GB", "US"}
    assert d("Floods in Manchester", "GB") == {"GB-ENG-manchester"} and d("Manchester United beat Chelsea", "GB") == set()
    assert d("Lagos State assembly passes budget", "NG") == {"NG-05"} and "NG-05-lagos" in d("Lagos traffic eases as bridge reopens", "NG")
    assert d("Search continues for missing boy last seen in Tucson", "US") == {"US-AZ-tucson"}
    assert d("Houston County beat Northside 21-7", "US") == set()
    assert d("Protests in Paris over pension law", "US") == {"FR"} or d("Protests in Paris over pension law", "US") == {"FR-11-paris"}
