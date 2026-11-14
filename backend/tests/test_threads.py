"""Storyline coherence: the join guard, the split, and the repair of a storyline that has drifted apart."""
import numpy as np
import pytest

from app.services import threads as T


def unit(*v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


def test_guard_joins_close_storylines_and_refuses_distant_ones():
    info = {1: (list(unit(1, 0, 0)), 5), 2: (list(unit(0.9, 0.3, 0)), 4), 3: (list(unit(0, 1, 0)), 4)}
    g = T.ThreadGuard(lambda ids: {i: info[i] for i in ids if i in info})
    v = unit(1, 0, 0)
    assert g.ok(1, 2, v, v)                       # centroids alike
    assert not g.ok(1, 3, v, unit(0, 1, 0))       # unrelated storylines never join on one loose link
    g.join(1, 2, v, v)
    assert g.ok(1, 2, v, v)                       # already one storyline


def test_a_long_storyline_keeps_growing_while_cohesive_and_stops_when_a_join_would_blur_it():
    def mean(length, deg):                        # the stored mean of a storyline's unit vectors: its length is the storyline's cohesion
        r = np.radians(deg)
        return list(length * np.array([np.cos(r), np.sin(r), 0], dtype=np.float32))
    v = unit(1, 0, 0)
    # 300 developments about one thing (cohesion 0.85) take 300 more of the same: there is no length limit
    info = {1: (mean(0.85, 0), 300), 2: (mean(0.85, 5), 300)}
    assert T.ThreadGuard(lambda ids: {i: info[i] for i in ids if i in info}).ok(1, 2, v, v)
    # two storylines whose centres are alike enough to pass the centroid test (cos 0.77) but that are each loosely held together
    # (cohesion 0.68) would give a joined storyline of cohesion 0.64: refused
    info = {1: (mean(0.68, 0), 100), 2: (mean(0.68, 40), 100)}
    assert np.cos(np.radians(40)) >= T.THREAD_COS
    assert not T.ThreadGuard(lambda ids: {i: info[i] for i in ids if i in info}).ok(1, 2, v, v)
    # the same two, held together tightly enough (0.8 each), join
    info = {1: (mean(0.80, 0), 100), 2: (mean(0.80, 40), 100)}
    assert T.ThreadGuard(lambda ids: {i: info[i] for i in ids if i in info}).ok(1, 2, v, v)


def test_guard_running_centroid_stops_a_chain_of_acceptable_joins():
    a, c = unit(1, 0, 0), unit(0, 1, 0)           # unrelated to each other
    b = unit(1, 1, 0)                             # halfway between them
    assert T.ThreadGuard(lambda ids: {}).ok(10, 20, a, b)    # a and b are 0.71 alike: acceptable by itself
    g = T.ThreadGuard(lambda ids: {})
    g.join(10, 20, a, a)
    g.join(10, 40, a, a)                          # a storyline made of a, a, a ...
    assert not g.ok(10, 30, a, c)                 # ... does not take c, however the link looked


def test_split_separates_unrelated_developments_and_keeps_a_coherent_run_together():
    names = [{"iran"}, {"iran"}, {"iran"}, {"tinubu"}, {"tinubu"}, {"sensex"}]
    toks = [{"iran", "hormuz", "tanker"}, {"iran", "hormuz", "strike"}, {"iran", "hormuz", "talks"}, {"tinubu", "campaign"}, {"tinubu", "campaign", "council"}, {"sensex", "points"}]
    E = np.vstack([unit(1, 0.05, 0), unit(1, 0.1, 0), unit(1, 0, 0.1), unit(0, 1, 0.05), unit(0.05, 1, 0), unit(0, 0, 1)])
    groups = sorted(sorted(g) for g in T.split(names, toks, E, set()))
    assert groups == [[0, 1, 2], [3, 4], [5]]


def test_split_does_not_chain_two_ends_through_a_middle():
    # 0~1 and 1~2 are close pairs, but 0 and 2 are not alike: average linkage refuses to put all three together
    names = [{"x"}, {"x"}, {"x"}]
    toks = [{"x", "a"}, {"x", "b"}, {"x", "c"}]
    E = np.vstack([unit(1, 0, 0), unit(1, 0.7, 0), unit(0.3, 1, 0)])
    assert sorted(len(g) for g in T.split(names, toks, E, set())) != [3]


@pytest.mark.db
def test_repair_thread_splits_a_mixed_storyline_and_keeps_the_coherent_part_together(container):
    c = container
    rng = np.random.default_rng(1)

    def vec(axis):
        v = rng.normal(0, 0.03, 384).astype(np.float32)
        v[axis] += 1.0
        v /= np.linalg.norm(v)
        return "[" + ",".join(f"{x:.5f}" for x in v) + "]"

    def add(title, axis, thread, hours):
        return c.repo.one("""INSERT INTO stories (title, lead, first_seen, last_update, outlets, articles, tokens, lead_source, lead_url, countries, types,
                                                    embedding, embed_n, embed_backend, thread_id)
                             VALUES (%s, '', now() - make_interval(hours => %s), now(), 1, 1, %s, 's', 'u', ARRAY['US'], ARRAY['news'], %s::vector, 1, 'model', %s)
                             RETURNING id""", (title, hours, title.lower().split(), vec(axis), thread))["id"]
    first = add("Iran tanker Hormuz strike", 0, None, 50)
    c.repo.run("UPDATE stories SET thread_id = %s WHERE id = %s", (first, first))
    ids = [first]
    for t in ["Iran Hormuz talks resume", "Iran Hormuz tanker attack", "Iran Hormuz ceasefire"]:
        ids.append(add(t, 0, first, 40))
    for t in ["Tinubu campaign council named", "Tinubu campaign rally Kano", "Tinubu campaign Lagos debate"]:
        ids.append(add(t, 1, first, 30))
    ids.append(add("Sensex ends lower on auto stocks", 2, first, 20))
    n, coh = c.repo.thread_coherence([first])[first]
    assert n == 8 and coh < T.COHESION_MIN
    r = T.repair_thread(c.repo, c.gazetteer, first, set())
    assert r["before"] == 8 and r["groups"] == 3 and r["sizes"] == [4, 3, 1]
    of = {row["id"]: row["thread_id"] for row in c.repo.q("SELECT id, thread_id FROM stories WHERE id = ANY(%s)", (ids,))}
    assert len({of[i] for i in ids[:4]}) == 1 and len({of[i] for i in ids[4:7]}) == 1 and of[ids[0]] != of[ids[4]] != of[ids[7]]
