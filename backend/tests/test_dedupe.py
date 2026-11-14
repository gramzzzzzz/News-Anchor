"""A list never shows the same news twice (services/dedupe.py, stories.chain_id; playbook 20.26)."""
import datetime as dt

import numpy as np
import pytest

from app.services.dedupe import drop_duplicates

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def vec(*direction):
    v = np.zeros(384, dtype=np.float32)
    for k, x in enumerate(direction):
        v[k] = x
    return "[" + ",".join(str(float(x)) for x in v) + "]"


def story(i, title, e, first=0, last=2):
    return {"id": i, "title": title, "e": e, "first_seen": T0 + dt.timedelta(hours=first), "last_update": T0 + dt.timedelta(hours=last)}


def run(stories, gap=8):
    rows = [{"id": s["id"]} for s in stories]
    kept, folded = drop_duplicates(rows, {s["id"]: dict(s) for s in stories}, gap)
    return [r["id"] for r in kept], folded


def test_two_reports_of_one_event_are_one_row():
    a = story(1, "Longtime NBC Sports announcer Dan Hicks dies at 64", vec(1, 0.1))
    b = story(2, "Dan Hicks, the longtime NBC Sports announcer, dies at 64", vec(1, 0.15), first=0.5)
    other = story(3, "Heavy rain floods the Lisbon metro", vec(0, 0, 1), first=0.2)
    assert run([a, b, other]) == ([1, 3], {1: [2]})


def test_alike_in_meaning_but_different_events_stay_two_rows():
    # one subject, two events: the vectors are close, the headlines say different things
    a = story(1, "Gaza death toll surges to 74,640 with 175,947 injured", vec(1, 0.05))
    b = story(2, "Four killed in Gaza as sand embankment collapses on displacement camp", vec(1, 0.1), first=1)
    assert run([a, b])[0] == [1, 2]


def test_different_counts_are_different_events():
    a = story(1, "Fire at a Dhaka garment factory kills 12 dead", vec(1, 0.05))
    b = story(2, "Fire at a Dhaka garment factory kills 3 dead", vec(1, 0.07), first=1)
    assert run([a, b])[0] == [1, 2]


def test_the_same_news_days_apart_is_not_folded():
    a = story(1, "US envoys meet Putin in the Kremlin after Ukraine talks", vec(1, 0.05), first=0, last=5)
    b = story(2, "US envoys meet Putin in the Kremlin for Ukraine talks", vec(1, 0.06), first=100, last=102)
    assert run([a, b])[0] == [1, 2]


def test_a_row_without_a_meaning_vector_is_kept():
    a = story(1, "Dan Hicks dies at 64", None)
    b = story(2, "Dan Hicks dies at 64", None, first=0.1)
    assert run([a, b])[0] == [1, 2]


@pytest.mark.db
class TestChains:
    def put(self, c, key, title, url, hours_ago):
        f = next(f for f in c.repo.all_feeds() if f["source_key"] == key)
        c.ingest.process_items(f, [{"title": title, "url": url, "lead": "", "published": dt.datetime.now(UTC) - dt.timedelta(hours=hours_ago)}])

    def test_an_event_cut_by_the_story_length_limit_is_one_row_in_a_long_period(self, container):
        c = container
        for d in range(4):
            c.repo.ensure_partition(dt.date.today() - dt.timedelta(days=d))
        c.settings.set_many({"search.embeddings": "hashed", "feed.min_outlets": 2})
        outlets = ["bbc", "dw", "france24", "abc-au", "cbc", "rnz", "nikkei", "scmp", "straitstimes", "yonhap", "hindu", "nyt"]
        # a report every hour for 60 hours: the 48-hour limit cuts the story into two
        for k, hours_ago in enumerate(range(60, 0, -1)):
            self.put(c, outlets[k % len(outlets)], f"Chip maker chief resigns after investigation {k}", f"https://t/{k}", hours_ago)
        rows = c.repo.q("SELECT id, thread_id, first_seen FROM stories ORDER BY first_seen")
        assert len(rows) == 2 and rows[0]["thread_id"] == rows[1]["thread_id"]
        c.repo.run("UPDATE stories SET embedding = (SELECT array_fill(0.1::real, ARRAY[384])::vector), embed_backend = 'hashed'")
        assert c.repo.link_chains(48, 8) == 1
        assert c.repo.q("SELECT chain_id FROM stories WHERE id = %s", (rows[1]["id"],))[0]["chain_id"] == rows[0]["id"]
        listed = c.stories.list("world", "", "top", 20, view="all")["stories"]
        assert len(listed) == 1 and listed[0]["parts"] == 2
        assert listed[0]["id"] in (rows[0]["id"], rows[1]["id"]) and listed[0]["first_seen"] == rows[0]["first_seen"]       # one of the parts, dated from the first
        assert listed[0]["outlets_period"] == len(set(outlets))                                                # counted over both parts

    def test_a_different_event_starting_when_the_limit_is_reached_is_not_chained(self, container):
        c = container
        for d in range(4):
            c.repo.ensure_partition(dt.date.today() - dt.timedelta(days=d))
        c.settings.set_many({"search.embeddings": "hashed", "feed.min_outlets": 2})
        outlets = ["bbc", "dw", "france24", "abc-au", "cbc", "rnz", "nikkei", "scmp", "straitstimes", "yonhap", "hindu", "nyt"]
        for k, hours_ago in enumerate(range(60, 0, -1)):
            self.put(c, outlets[k % len(outlets)], f"Chip maker chief resigns after investigation {k}", f"https://t/{k}", hours_ago)
        # the second story is made to say something else
        rows = c.repo.q("SELECT id FROM stories ORDER BY first_seen")
        c.repo.run("UPDATE stories SET title = 'Flood warning for the Lisbon metro lines tonight' WHERE id = %s", (rows[1]["id"],))
        c.repo.run("UPDATE stories SET embedding = (SELECT array_fill(0.1::real, ARRAY[384])::vector), embed_backend = 'hashed'")
        c.repo.run("UPDATE stories SET embedding = (SELECT (array_fill(0::real, ARRAY[383]) || ARRAY[1.0::real])::vector) WHERE id = %s", (rows[1]["id"],))
        assert c.repo.link_chains(48, 8) == 0
        assert len(c.stories.list("world", "", "top", 20, view="all")["stories"]) == 2


@pytest.mark.db
def test_reports_after_the_limit_gather_in_one_new_story_not_one_each(container):
    c = container
    for d in range(4):
        c.repo.ensure_partition(dt.date.today() - dt.timedelta(days=d))
    c.settings.set_many({"search.embeddings": "hashed", "feed.min_outlets": 2})
    outlets = ["bbc", "dw", "france24", "abc-au", "cbc", "rnz", "nikkei", "scmp", "straitstimes", "yonhap", "hindu", "nyt"]
    words = ["investigation", "board", "shares", "stock", "probe", "scandal", "successor", "markets"]
    for k, hours_ago in enumerate(range(60, 0, -1)):
        f = next(f for f in c.repo.all_feeds() if f["source_key"] == outlets[k % len(outlets)])
        c.ingest.process_items(f, [{"title": f"Chip maker chief resigns as {words[k % 8]} {words[(k * 3 + 1) % 8]} {k}", "url": f"https://t/{k}", "lead": "",
                                    "published": dt.datetime.now(UTC) - dt.timedelta(hours=hours_ago)}])
    assert c.repo.q("SELECT count(*) AS n FROM stories")[0]["n"] == 2          # the limit cuts it once, not once per report


@pytest.mark.db
def test_a_new_story_with_a_few_outlets_is_listed_though_hundreds_of_older_stories_have_more_coverage(container):
    c = container
    c.repo.ensure_partition(dt.date.today())
    c.settings.set_many({"search.embeddings": "hashed", "feed.min_outlets": 3})
    c.repo.run("""INSERT INTO stories (title, first_seen, last_update, outlets, articles, tokens, lead_source, lead_url, fair, indep, types)
                  SELECT 'Old big story ' || g, now() - interval '30 hours', now() - interval '10 minutes', 50, 50, '{old}', 'bbc', 'https://o/' || g, 40 + g, 50, '{news}'
                  FROM generate_series(1, 400) g""")
    for i, k in enumerate(["bbc", "dw", "france24", "cbc"]):
        f = next(f for f in c.repo.all_feeds() if f["source_key"] == k)
        c.ingest.process_items(f, [{"title": f"Tunnel collapse traps miners in remote province {i}", "url": f"https://n/{i}", "lead": "",
                                    "published": dt.datetime.now(UTC) - dt.timedelta(minutes=20)}])
    assert any(s["title"].startswith("Tunnel collapse") for s in c.stories.list("world", "", "top", 20, view="fresh")["stories"])
    assert any(s["title"].startswith("Tunnel collapse") for s in c.stories.list("world", "", "trending", 20)["stories"])


@pytest.mark.db
class TestViews:
    OUTLETS = ["bbc", "dw", "france24", "abc-au", "cbc", "rnz", "nikkei", "scmp", "straitstimes", "yonhap", "hindu", "nyt"]

    def put(self, c, key, title, url, minutes_ago):
        f = next(f for f in c.repo.all_feeds() if f["source_key"] == key)
        c.ingest.process_items(f, [{"title": title, "url": url, "lead": "", "published": dt.datetime.now(UTC) - dt.timedelta(minutes=minutes_ago)}])

    def setup(self, c):
        for d in range(4):
            c.repo.ensure_partition(dt.date.today() - dt.timedelta(days=d))
        c.settings.set_many({"search.embeddings": "hashed", "feed.min_outlets": 2, "feed.trending_min_outlets": 3})

    def titles(self, c, tab, view):
        return [s["title"] for s in c.stories.list("world", "", tab, 20, view=view)["stories"]]

    def test_fresh_and_ongoing_split_by_when_the_event_was_first_reported(self, container):
        c = container
        self.setup(c)
        for i, k in enumerate(self.OUTLETS[:6]):                               # begun 30 hours ago, still reported an hour ago
            self.put(c, k, f"Harbour crane collapse injures dockworkers {i}", f"https://old/{i}", (1800, 1500, 1200, 900, 600, 300)[i] - i)
        for i, k in enumerate(self.OUTLETS[:4]):                               # begun two hours ago
            self.put(c, k, f"Minister unveils new rail subsidy plan {i}", f"https://new/{i}", 120 - i)
        assert [t[:15] for t in self.titles(c, "top", "fresh")] == ["Minister unveil"]
        assert [t[:15] for t in self.titles(c, "top", "ongoing")] == ["Harbour crane c"]
        assert sorted(t[:15] for t in self.titles(c, "top", "all")) == ["Harbour crane c", "Minister unveil"]
        assert {s["bucket"] for s in c.stories.list("world", "", "top", 20, view="all")["stories"]} == {"fresh", "ongoing"}

    def test_a_giant_ongoing_story_does_not_hide_the_fresh_news_in_the_aggregated_list(self, container):
        c = container
        self.setup(c)
        for n in range(4):                                                     # four big ongoing stories: 12 outlets each, begun yesterday
            for i, k in enumerate(self.OUTLETS):
                self.put(c, k, f"Ongoing saga number{n} deepens as talks stall {i}", f"https://g{n}/{i}", (1800, 1500, 1200, 900, 600, 300, 200, 150, 120, 100, 90, 80)[i] - i)
        for n in range(2):                                                     # two fresh stories: 3 outlets each
            for i, k in enumerate(self.OUTLETS[:3]):
                self.put(c, k, f"Fresh event alpha{n} unfolds in the capital {i}", f"https://f{n}/{i}", 20 - i)
        top4 = self.titles(c, "top", "all")[:4]
        assert any(t.startswith("Fresh event") for t in top4) and any(t.startswith("Ongoing saga") for t in top4)       # both kinds near the top
        assert self.titles(c, "top", "all")[0].startswith(("Fresh event", "Ongoing saga"))

    def test_decay_ranks_recent_outlets_above_old_ones(self, container):
        c = container
        self.setup(c)
        for i, k in enumerate(self.OUTLETS[:6]):
            self.put(c, k, f"Harbour crane collapse injures dockworkers {i}", f"https://old/{i}", 20 * 60 - i)          # six outlets, 20 hours ago
        for i, k in enumerate(self.OUTLETS[:6]):
            self.put(c, k, f"Minister unveils new rail subsidy plan {i}", f"https://new/{i}", 30 - i)                  # six outlets, half an hour ago
        top = self.titles(c, "top", "fresh")
        assert top[0].startswith("Minister unveils") and top[1].startswith("Harbour crane")                            # both listed, the fresher first

    def test_trending_ranks_the_surge_not_the_size(self, container):
        c = container
        self.setup(c)
        for i in range(12):                                                    # a big story that has been steady all day
            self.put(c, self.OUTLETS[i % 12], f"Election count continues in the capital {i}", f"https://s/{i}", 600 + i * 40)
        for i in range(3):
            self.put(c, self.OUTLETS[i], f"Election count continues in the capital again {i}", f"https://t/{i}", 40 + i)
        for i in range(4):
            self.put(c, self.OUTLETS[i], f"Bridge closes after crack found in span {i}", f"https://u/{i}", 30 - i)
        trend = self.titles(c, "trending", "all")
        assert trend and trend[0].startswith("Bridge closes")

    def test_views_are_checked(self, container):
        from app.services.stories import ScopeError
        with pytest.raises(ScopeError):
            container.stories.list("world", "", "top", 5, view="week")


def test_fusion_gives_each_list_a_share_of_the_top():
    from app.services import ranking
    fresh = [{"id": f"f{i}", "rank": 5.0 - i} for i in range(3)]
    ongoing = [{"id": f"o{i}", "rank": 500.0 - i} for i in range(3)]
    out = [r["id"] for r in ranking.fuse(fresh, ongoing)]
    assert out == ["o0", "f0", "o1", "f1", "o2", "f2"]            # equal places alternate, the bigger scale does not matter


def test_a_surge_needs_more_outlets_than_the_usual_number():
    from app.services import ranking
    steady = {"arrivals_now": 100, "arrivals_base": 100}
    new = {"arrivals_now": 4, "arrivals_base": 0}
    assert ranking.surge(steady) == 0 and ranking.surge(new) == 4


@pytest.mark.db
class TestChaining:
    def _stories(self, c, rows):
        import math
        def vec(x, y, z):
            v = [0.0] * 384
            v[0], v[1], v[2] = x, y, z
            n = math.sqrt(x * x + y * y + z * z)
            return "[" + ",".join(str(a / n) for a in v) + "]"
        c.repo.ensure_partition(dt.date.today())
        now = dt.datetime.now(UTC)
        for title, toks, outlets, emb, hrs in rows:
            c.repo.run("""INSERT INTO stories (title, lead, first_seen, last_update, outlets, articles, tokens, lead_source, lead_url, countries, types, indep, embedding, embed_n, embed_backend, lang)
                          VALUES (%s, '', %s, %s, %s, %s, %s, 'bbc', 'https://x', '{US}', '{news}', %s, %s::vector, 1, 'hashed', 'en')""",
                       (title, now - dt.timedelta(hours=hrs), now - dt.timedelta(minutes=5), outlets, outlets, toks, outlets, vec(*emb)))
        return c.merger._cluster("hashed", set(), True)()

    BIG = ("Jensen Huang steps down as Nvidia CEO", "{jensen,huang,step,nvidia,ceo}", 300, (1, 0, 0), 20)
    TIM = ("Trump says Apple's Tim Cook should replace Jensen Huang as Nvidia CEO", "{trump,apple,tim,cook,replac,jensen,huang,nvidia,ceo}", 30, (.71, .704, 0), 5)
    BRIDGE = ("Trump floats Tim Cook as next Nvidia CEO after Huang exit", "{trump,float,tim,cook,next,nvidia,ceo,huang,exit}", 5, (.82, .381, .427), 4)

    def test_a_distinct_event_is_not_pulled_into_a_big_story_through_a_small_one(self, container):
        c = container
        self._stories(c, [self.BIG, self.TIM, self.BRIDGE])
        titles = sorted(r["title"][:12] for r in c.repo.q("SELECT title FROM stories"))
        assert titles == ["Jensen Huang", "Trump says A"] or titles == ["Jensen Huang", "Trump floats"]       # the big story alone, the Tim Cook story (with its small neighbour) apart
        assert len(titles) == 2

    def test_a_close_story_still_joins_a_big_one(self, container):
        c = container
        close = ("Jensen Huang resigns as Nvidia chief executive", "{jensen,huang,resign,nvidia,chief,executive}", 12, (.95, .1, .0), 3)
        self._stories(c, [self.BIG, close])
        assert c.repo.one("SELECT count(*) AS n FROM stories")["n"] == 1


def test_developments_of_one_storyline_in_a_list_are_grouped_but_nothing_is_dropped():
    from app.services.dedupe import group_by_storyline
    def row(i, th, indep=5):
        return {"id": i, "thread_id": th, "indep_p": indep}
    def meta(i, x, y):
        return {"id": i, "e": "[" + ",".join(str(v) for v in ([x, y] + [0.0] * 382)) + "]"}
    rows = [row(1, 10), row(2, 20), row(3, 10), row(4, 10), row(5, 10, indep=1), row(6, 20)]
    m = {1: meta(1, 1, 0), 2: meta(2, 0, 1), 3: meta(3, .75, .66), 4: meta(4, -.5, .866), 6: meta(6, 0, 1), 5: meta(5, 1, 0)}
    groups = group_by_storyline(rows, m, eligible=lambda r: r["indep_p"] >= 2)
    assert [[r["id"] for r in g] for g in groups] == [[1, 3], [2, 6], [4], [5]]       # 3 is near 1; 4 is of the storyline but another matter; 5 is too small to group
    assert sorted(r["id"] for g in groups for r in g) == [1, 2, 3, 4, 5, 6]


@pytest.mark.db
class TestStorylineView:
    OUTLETS = ["bbc", "dw", "france24", "abc-au", "cbc", "rnz", "nikkei", "scmp", "straitstimes", "yonhap", "hindu", "nyt"]

    def put(self, c, key, title, url, minutes_ago):
        f = next(f for f in c.repo.all_feeds() if f["source_key"] == key)
        c.ingest.process_items(f, [{"title": title, "url": url, "lead": "", "published": dt.datetime.now(UTC) - dt.timedelta(minutes=minutes_ago)}])

    def setup(self, c):
        for d in range(3):
            c.repo.ensure_partition(dt.date.today() - dt.timedelta(days=d))
        c.settings.set_many({"search.embeddings": "hashed", "feed.min_outlets": 2})

    def vec(self, x, y):
        return "[" + ",".join(str(v) for v in ([x, y] + [0.0] * 382)) + "]"

    def test_two_developments_of_one_storyline_are_one_row_that_opens_and_none_is_lost(self, container):
        c = container
        self.setup(c)
        for i, k in enumerate(self.OUTLETS[:6]):
            self.put(c, k, f"Chip chief Huang resigns after affair probe {i}", f"https://a/{i}", 50 - i)
        for i, k in enumerate(self.OUTLETS[:4]):
            self.put(c, k, f"Trump says Tim Cook should replace Huang at Orion Chips {i}", f"https://b/{i}", 30 - i)
        for i, k in enumerate(self.OUTLETS[:3]):
            self.put(c, k, f"Markets open lower on bank worries {i}", f"https://m/{i}", 20 - i)
        ids = {r["title"][:8]: r["id"] for r in c.repo.q("SELECT id, title FROM stories")}
        c.repo.run("UPDATE stories SET thread_id = %s WHERE title LIKE 'Chip chi%%' OR title LIKE 'Trump sa%%'", (ids["Chip chi"],))
        c.repo.run("UPDATE stories SET embedding = %s::vector, embed_backend = 'hashed' WHERE title LIKE 'Chip chi%%'", (self.vec(1, 0),))
        c.repo.run("UPDATE stories SET embedding = %s::vector, embed_backend = 'hashed' WHERE title LIKE 'Trump sa%%'", (self.vec(.75, .66),))
        c.repo.run("UPDATE stories SET embedding = %s::vector, embed_backend = 'hashed' WHERE title LIKE 'Markets%%'", (self.vec(0, 1),))
        rows = c.stories.list("world", "", "top", 10, view="fresh")["stories"]
        assert len(rows) == 2                                              # the storyline's two developments are one row, the other story its own
        grouped = next(r for r in rows if r.get("group"))
        assert [g["title"][:6] for g in grouped["group"]] in (["Trump "], ["Chip c"]) and len(grouped["group"]) == 1       # the second development is kept, under the first
        assert {grouped["title"][:6], grouped["group"][0]["title"][:6]} == {"Chip c", "Trump "}

    def test_a_part_of_a_story_is_listed_inside_it_and_small_reports_do_not_crowd_the_timeline(self, container):
        c = container
        self.setup(c)
        for i in range(20):
            self.put(c, self.OUTLETS[i % 12], f"Chip chief Huang resigns after affair probe {i}", f"https://a/{i}", 600 - i)
        sid = c.repo.one("SELECT id FROM stories")["id"]
        for i in range(6):                                                 # a part of the story: six outlets, other words; joined to the story as the merge pass does
            self.put(c, self.OUTLETS[i], f"Trump says Tim Cook should replace Huang {i}", f"https://b/{i}", 300 - i)
        c.repo.run("UPDATE articles SET story_id = %s WHERE title LIKE 'Trump says%%'", (sid,))
        c.repo.run("DELETE FROM stories WHERE id <> %s", (sid,))
        c.repo.run("UPDATE stories SET thread_id = %s WHERE id = %s", (sid, sid))
        for k in range(15):                                                # minor reports of the same storyline
            c.repo.run("""INSERT INTO stories (title, lead, first_seen, last_update, outlets, articles, tokens, lead_source, lead_url, countries, types, indep, thread_id)
                          VALUES (%s, '', now() - interval '1 hour', now(), 1, 1, '{x}', 'bbc', 'https://z', '{US}', '{news}', 1, %s)""", (f"Small report {k}", sid))
        d = c.stories.detail(sid)
        assert any("cook" in e["label"] for e in d["subevents"])
        assert d["thread"]["minor"] == 15 and [t["id"] for t in d["timeline"]] == [sid]
