"""Replay of the lists: what Top (and Trending) would have shown at each snapshot of the last days, and how well they did. Run in a separate container
(the stories are read as they are now, so a story that merged later appears whole; whether a story is "big" is known in hindsight, so read differences):

  docker run --rm --network news-anchor_default --memory 1200m -e PYTHONPATH=/app -e NA_DATABASE_URL=... -e NA_DATA_DIR=/tmp -e NA_STATIC_DIR=/app/static \\
      -v $PWD/backend/app:/app/app:ro -v $PWD/scripts:/app/scripts:ro news-anchor:latest python /app/scripts/replay_lists.py --days 2 --step 3

Lists    before  Top over the last 24 hours, ranked by coverage with no decay (the design before Fresh/Ongoing)
         fresh, ongoing, aggregated  the three views now (decay on), and trending (surge, aggregated)
Measured, for the big stories (finally ``--big`` outlets or more, not routine) that began in the replayed days:
  found     share in the first 10 rows of the list within 1 / 3 / 6 / 24 hours of their first report;  never  share never in them within 24 hours
  lag       median hours from first report to the first time in the first 10 rows (of those that got there)
  stale     share of the first 10 rows that began more than 24 hours before the snapshot (Fresh: none by definition; Aggregated: shows the mix)
  churn     share of the first 10 rows that were not in the first 10 at the snapshot before
``--watch ID,ID`` prints the place of those stories in the aggregated list at every snapshot (e.g. the Nvidia resignation story and its parts)."""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import statistics

from app.config import load_config
from app.container import build
from app.services import ranking
from app.services.dedupe import without_repeats

UTC = dt.timezone.utc


def stay(tops: list[list[int]], step: float) -> float:
    """Mean hours a story stays in the first 10 (consecutive snapshots in the list x the step), over all its stays."""
    # runs of one story = lengths of consecutive presence; count each when it ends
    lens: list[int] = []
    live: dict[int, int] = {}
    for top in tops + [[]]:
        for u in list(live):
            if u not in top:
                lens.append(live.pop(u))
        for u in top:
            live[u] = live.get(u, 0) + 1
    return statistics.mean(lens) * step if lens else float("nan")


def long_slots(tops: list[list[int]], step: float, hours: float = 24) -> float:
    """Share of the places in the first 10 held by a story that has been in the first 10 without a break for more than ``hours``."""
    live: dict[int, int] = {}
    held = total = 0
    for top in tops:
        live = {u: live.get(u, 0) + 1 for u in top}
        held += sum(1 for u in top if live[u] * step > hours)
        total += len(top)
    return held / max(1, total)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=2)
    ap.add_argument("--step", type=float, default=3, help="hours between snapshots")
    ap.add_argument("--big", type=int, default=50)
    ap.add_argument("--end-ago", type=float, default=25, help="hours before now of the last snapshot (hindsight for the 'found' numbers)")
    ap.add_argument("--watch", default="")
    ap.add_argument("--decay-h", type=float, default=None, help="decay time scale T (default: the setting)")
    ap.add_argument("--gravity", type=float, default=None, help="decay exponent g (default: the setting)")
    ap.add_argument("--variants", default="base:6:0:2", help="name:story_decay_h:story_gravity:gravity,... Story-age decay counts an outlet by how long after the "
                    "story's first report it joined: (1 + gap / T_s) ** -g_s, on top of (or, with gravity 0, instead of) the decay by time since joining")
    a = ap.parse_args()
    watch = {int(x) for x in a.watch.split(",") if x}
    c = build(load_config())
    repo = c.repo
    repo.open()
    s = c.settings
    p = ranking.Params.from_settings(s)
    if a.decay_h is not None:
        p.decay_h = a.decay_h
    if a.gravity is not None:
        p.gravity = a.gravity
    print(f"decay T = {p.decay_h} h, g = {p.gravity}")
    end = dt.datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=a.end_ago)
    snaps = [end - dt.timedelta(hours=a.step * i) for i in range(int(a.days * 24 / a.step))][::-1]
    truth = {r["u"]: r["first_seen"] for r in repo.q(
        """SELECT COALESCE(chain_id, id) AS u, min(first_seen) AS first_seen FROM stories WHERE first_seen BETWEEN %s AND %s AND NOT routine
           AND NOT (types <@ ARRAY['social']) AND indep >= %s GROUP BY 1""", (snaps[0], snaps[-1], a.big))}
    unit_of: dict[int, int] = {}
    print(f"{len(snaps)} snapshots from {snaps[0]:%d %b %H:%M} to {snaps[-1]:%d %b %H:%M} UTC, {len(truth)} big stories", flush=True)
    variants = [(v.split(":")[0], float(v.split(":")[1]), float(v.split(":")[2]), float(v.split(":")[3]), int(v.split(":")[4]) if len(v.split(":")) > 4 else 0, float(v.split(":")[5]) if len(v.split(":")) > 5 else 0.0, float(v.split(":")[6]) if len(v.split(":")) > 6 else 0.0) for v in a.variants.split(",")]
    names = ["before", "trending"] + [f"{n}/{w}" for n, *_ in variants for w in ("fresh", "ongoing", "aggr")]
    floors = {n: fl for n, _a, _b, _c, fl, _s, _t in variants}
    softs = {n: (sc, fs) for n, _a, _b, _c, _f, sc, fs in variants}
    res = {n: {"tops": [], "first": {}, "stale": []} for n in names}

    def unit(i: int) -> int:
        if i not in unit_of:
            r = repo.one("SELECT COALESCE(chain_id, id) AS u FROM stories WHERE id = %s", (i,))
            unit_of[i] = r["u"] if r else i
        return unit_of[i]

    for t in snaps:
        common = dict(scopes=[("world", "")], min_outlets=s.get("feed.min_outlets"), allow_single=False, exclude_types=[], langs=["en"], limit=120, at=t,
                      fresh_h=p.fresh_h, decay_h=p.decay_h, trending_h=p.trending_h, base_h=p.base_h)
        before = repo.list_stories(tab="top", gravity=0.0, **common)
        trend = repo.list_stories(tab="trending", gravity=p.gravity, **common)
        tops = {n: repo.list_stories(tab="top", **{**common, "gravity": g, "story_decay_h": st, "story_gravity": sg}) for n, st, sg, g, _fl, _sc, _fs in variants}
        meta = {m["id"]: m for m in repo.story_vectors([r["id"] for r in before + trend + [x for v in tops.values() for x in v]])}
        lists = {
            "before": sorted(without_repeats([dict(r) for r in before], meta, 8, key=lambda r: float(r["score"])), key=lambda r: float(r["score"]), reverse=True),
            "trending": ranking.order(without_repeats([dict(r) for r in trend], meta, 8, key=ranking.surge), "trending", "all", p, 3),
        }
        for n, top in tops.items():
            rows = without_repeats([dict(r) for r in top], meta, 8, key=lambda r: float(r["score"]))
            rows = [r for r in rows if r["bucket"] == "fresh" or int(r.get("recent_n") or 0) >= floors[n]]       # an ongoing story needs outlets that joined lately
            for v, w in (("fresh", "fresh"), ("ongoing", "ongoing"), ("all", "aggr")):
                lists[f"{n}/{w}"] = ranking.order([dict(r) for r in rows], "top", v, dataclasses.replace(p, active_soft=softs[n][0], fresh_soft=softs[n][1]), 3)
        for n in names:
            first10 = lists[n][:10]
            res[n]["tops"].append([unit(r["id"]) for r in first10])
            res[n]["stale"].append(sum(1 for r in first10 if r["bucket"] == "ongoing") / max(1, len(first10)))
            res[n].setdefault("quiet", []).append(sum(1 for r in first10 if int(r.get("recent_n") or 0) == 0) / max(1, len(first10)))
            for r in first10:
                u = unit(r["id"])
                if u in truth and u not in res[n]["first"] and t >= truth[u]:
                    res[n]["first"][u] = (t - truth[u]).total_seconds() / 3600
        if watch:
            for vn, *_ in variants:
                pl = {w: {u: i + 1 for i, u in enumerate(unit(r["id"]) for r in lists[f"{vn}/{w}"])} for w in ("aggr", "fresh", "ongoing")}
                cell = lambda w: "/".join(str(pl[x].get(unit(w), "-")) for x in ("aggr", "fresh", "ongoing"))     # noqa: E731
                print(f"  {t:%d %b %H:%M} {vn:5s} aggr/fresh/ongoing place: " + "  ".join(f"{w}={cell(w)}" for w in sorted(watch)), flush=True)
        else:
            print(f"  {t:%d %b %H:%M} done", flush=True)

    print("\n" + "=" * 100)
    print(f"{'list':12s} {'<=1h':>6s} {'<=3h':>6s} {'<=6h':>6s} {'<=24h':>6s} {'never':>6s} {'lag h':>6s} {'ongoing share':>14s} {'churn':>6s} {'stay h':>7s} {'slots>24h':>10s} {'quiet 6h':>9s}")
    n_truth = max(1, len(truth))
    for n in names:
        r = res[n]
        lag = [v for v in r["first"].values() if v <= 24]
        fnd = lambda h: sum(1 for v in lag if v <= h) / n_truth       # noqa: E731
        churn = [len(set(b) - set(x)) / max(1, len(b)) for x, b in zip(r["tops"], r["tops"][1:])]
        print(f"{n:12s} {fnd(1):6.2f} {fnd(3):6.2f} {fnd(6):6.2f} {fnd(24):6.2f} {1 - fnd(24):6.2f} {statistics.median(lag) if lag else float('nan'):6.1f} "
              f"{statistics.mean(r['stale']):14.2f} {statistics.mean(churn) if churn else float('nan'):6.2f} {stay(r['tops'], a.step):7.1f} {long_slots(r['tops'], a.step):10.2f} {statistics.mean(r['quiet']):9.2f}")


if __name__ == "__main__":
    main()
