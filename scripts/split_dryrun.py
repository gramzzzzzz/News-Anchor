"""Dry run of the storyline split on the biggest storylines: sizes after splitting and sample headlines. Changes nothing."""
import os, sys
from app.dao.repo.postgres import Repository
from app.services.places import Gazetteer
from app.services.anchors import rarity_bar
from app.services.threads import repair_thread, SPLIT_MIN
repo = Repository(os.environ["NA_DATABASE_URL"])
repo.open()
gaz = Gazetteer()
frequent, n = repo.frequent_tokens(72, rarity_bar)
ids = repo.threads_to_check(int(sys.argv[1]) if len(sys.argv) > 1 else 40)
coh = repo.thread_coherence(ids)
for t in ids[:int(sys.argv[2]) if len(sys.argv) > 2 else 12]:
    r = repair_thread(repo, gaz, t, frequent, apply=False)
    print(t, "devs", r["before"], "coherence", round(coh.get(t, (0, 0))[1], 2), "->", r["groups"], "groups; biggest", r["sizes"][:8])
    if "-v" in sys.argv:
        from app.services.threads import split
