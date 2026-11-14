"""Place names that are also everyday words, measured from real headlines rather than guessed.

A state or city name counts as an everyday word when, in collected headlines and leads, it is written in lower case at least 15% of
the time (mission, holiday, male, north, erode). Those places are then tagged only with context (their state or country named, or the
publisher in that country after a word such as "in"). Run against a text file of headlines, one per line:
    docker exec news-anchor-db psql -U na -d na -At -c "select title || ' ' || left(lead, 300) from articles" > /tmp/corpus.txt
    cd backend && .venv/bin/python ../scripts/build_place_common_words.py /tmp/corpus.txt
"""
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.places import Gazetteer, _tokens  # noqa: E402

g = Gazetteer()
names = set(g._multi)
low, cap = collections.Counter(), collections.Counter()
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    pairs = _tokens(line)
    for k, (t, raw) in enumerate(pairs):
        if t in names:
            if raw.islower():
                low[t] += 1
            elif k > 0 and raw[:1].isupper():
                cap[t] += 1
common = sorted(t for t in names if (low[t] + cap[t]) >= 3 and low[t] / (low[t] + cap[t]) >= 0.15)
out = Path(__file__).resolve().parents[1] / "backend" / "app" / "data" / "place_common_words.txt"
out.write_text("# Place names written in lower case in at least 15% of their uses in real headlines (scripts/build_place_common_words.py).\n" + "\n".join(common) + "\n")
print(len(common), "words")
