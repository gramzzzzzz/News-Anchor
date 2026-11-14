"""Topic picker data: the whole taxonomy with the owner's choices and the sources that cover each topic."""
from __future__ import annotations


class TopicService:
    def __init__(self, repo, taxonomy, stories):
        self.repo, self.tax, self.stories = repo, taxonomy, stories

    def tree(self) -> list[dict]:
        prefs = self.repo.topic_prefs()
        feeds = self.repo.all_feeds()
        by_hint: dict[str, dict[str, str]] = {}
        for f in feeds:
            for h in f["topics"]:
                by_hint.setdefault(h, {})[f["source_key"]] = f["source_name"]
        enabled = {c for c, on in prefs.items() if on}
        visible = set(self.tax.roots) | enabled | {a for c in enabled for a in self.tax.ancestors(c)}
        out = []
        for c, t in self.tax.topics.items():
            out.append({"code": c, "name": t.name, "parent": t.parent, "depth": t.depth, "enabled": True if t.depth == 1 else prefs.get(c, False),
                        "default_enabled": True if t.depth == 1 else t.default_enabled, "visible": c in visible, "children": len(t.children),
                        "sources": sorted(by_hint.get(c, {}).values()), "specialist": t.depth >= 2})
        return out

    def menu(self, parent: str, place: str) -> dict:
        """Children of ``parent`` the owner sees in the 'What' menu, with story counts in ``place``."""
        place, _ = self.stories.check_scope(place, parent)
        tf, _ = self.repo.facets(place=self.stories.place_codes(place), topic="", window_h=self.stories.settings.get("feed.window_hours"),
                                 min_outlets=self.stories.settings.get("feed.min_outlets"))
        tree = {t["code"]: t for t in self.tree()}
        kids = [c for c in (self.tax.roots if not parent else self.tax.topics[parent].children) if tree[c]["visible"]]
        crumbs = [{"code": a, "name": self.tax.topics[a].name} for a in reversed(self.tax.ancestors(parent))] + ([{"code": parent, "name": self.tax.topics[parent].name}] if parent else [])
        hidden = sum(1 for c in (self.tax.roots if not parent else self.tax.topics[parent].children) if not tree[c]["visible"])
        return {"breadcrumb": crumbs, "children": [{"code": c, "name": tree[c]["name"], "count": tf.get(c, 0), "has_children": tree[c]["children"] > 0} for c in kids],
                "hidden_count": hidden}

    def set_enabled(self, code: str, enabled: bool) -> None:
        t = self.tax.topics.get(code)
        if not t or t.depth == 1:
            raise ValueError("Unknown or always-on topic")
        self.repo.topic_pref_set(code, enabled)
        self.repo.audit("topics.set", f"{code}={enabled}")

    def set_many(self, codes: list[str], enabled: bool) -> int:
        ok = [c for c in codes if c in self.tax.topics and self.tax.topics[c].depth >= 2]
        for c in ok:
            self.repo.topic_pref_set(c, enabled)
        self.repo.audit("topics.bulk", f"{len(ok)} topics set to {enabled}")
        return len(ok)

    def reset(self) -> None:
        self.repo.topic_prefs_reset({c: t.default_enabled for c, t in self.tax.topics.items() if t.depth >= 2})
        self.repo.audit("topics.reset")
