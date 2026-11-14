"""Topic taxonomy (data/topics.txt) and the alias-based classifier.

The 17 IPTC Media Topics categories with one level of place-free sub-topics. Classification is a dictionary lookup over
word n-grams, so it needs almost no memory or CPU."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.services.textutil import grams, norm_tokens

DATA = Path(__file__).resolve().parent.parent / "data"


@dataclass
class Topic:
    code: str
    slug: str
    name: str
    parent: str | None
    depth: int                      # 1 = category
    aliases: list[str] = field(default_factory=list)      # normalised, unambiguous
    ambiguous: list[str] = field(default_factory=list)    # normalised, count only with context
    exact: list[str] = field(default_factory=list)        # matched as written, case and plural included: "WHO", "Chiefs", "Patriots"
    default_enabled: bool = False
    default_favourite: bool = False
    children: list[str] = field(default_factory=list)


class Taxonomy:
    STOP_PHRASES: list[str] = []

    def __init__(self, topics: dict[str, Topic]):
        self.topics = topics
        self.roots = [c for c, t in topics.items() if t.parent is None]
        self._index: dict[str, list[tuple[str, bool]]] = {}
        import re as _re
        self._exact = [(_re.compile(r"(?<![\w-])" + _re.escape(a) + r"(?![\w-])"), t.code) for t in topics.values() for a in t.exact]
        for p in self.STOP_PHRASES:
            self._index.setdefault(p, [])                      # a phrase that only covers its words: "hacked to death" is not a hack
        for t in topics.values():
            for a in t.aliases:
                self._index.setdefault(a, []).append((t.code, False))
            for a in t.ambiguous:
                self._index.setdefault(a, []).append((t.code, True))

    @classmethod
    def load(cls, path: Path | None = None) -> "Taxonomy":
        topics: dict[str, Topic] = {}
        stack: list[Topic] = []
        cls.STOP_PHRASES = []
        for raw in (path or DATA / "topics.txt").read_text(encoding="utf-8").splitlines():
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            if raw.startswith("!ignore"):
                cls.STOP_PHRASES += [" ".join(norm_tokens(x)) for x in raw.split("|", 1)[1].split(",") if x.strip()]
                continue
            indent = (len(raw) - len(raw.lstrip(" "))) // 2
            parts = [p.strip() for p in raw.split("|")]
            slug, name = parts[0], parts[1]
            alias_str = parts[2] if len(parts) > 2 else ""
            flags = parts[3] if len(parts) > 3 else ""
            del stack[indent:]
            parent = stack[-1] if stack else None
            code = f"{parent.code}.{slug}" if parent else slug
            t = Topic(code=code, slug=slug, name=name, parent=parent.code if parent else None, depth=indent + 1,
                      default_enabled="*" in flags, default_favourite="!" in flags)
            for a in (x.strip() for x in alias_str.split(",")):
                if not a:
                    continue
                if a.startswith("="):
                    t.exact.append(a[1:].strip())
                    continue
                amb = a.startswith("~")
                toks = " ".join(norm_tokens(a.lstrip("~")))
                if toks:
                    (t.ambiguous if amb else t.aliases).append(toks)
            topics[code] = t
            if parent:
                parent.children.append(code)
            stack.append(t)
        return cls(topics)

    # ---- hierarchy -------------------------------------------------------------------------------
    def ancestors(self, code: str) -> list[str]:
        out = []
        t = self.topics.get(code)
        while t and t.parent:
            out.append(t.parent)
            t = self.topics.get(t.parent)
        return out

    def with_ancestors(self, codes) -> set[str]:
        s = set()
        for c in codes:
            if c in self.topics:
                s.add(c)
                s.update(self.ancestors(c))
        return s

    def descendants(self, code: str) -> list[str]:
        out, todo = [], list(self.topics[code].children) if code in self.topics else []
        while todo:
            c = todo.pop()
            out.append(c)
            todo.extend(self.topics[c].children)
        return out

    def related(self, a: str, b: str) -> bool:
        return a == b or b in self.ancestors(a) or a in self.ancestors(b)

    def is_specialist(self, hints: list[str]) -> bool:
        """A feed is specialist (gated by the topic picker) when every hint is a sub-topic."""
        hs = [h for h in hints if h in self.topics]
        return bool(hs) and all(self.topics[h].depth >= 2 for h in hs)

    def feed_active(self, hints: list[str], enabled: set[str]) -> bool:
        if not self.is_specialist(hints):
            return True
        for h in hints:
            if h not in self.topics:
                continue
            for e in enabled:
                if self.related(h, e):
                    return True
        return False

    def _branch(self, code: str) -> str:
        """The depth-2 topic a code belongs to (sports.cricket for sports.cricket.ipl), or the code itself at depth 1."""
        parts = code.split(".")
        return ".".join(parts[:2])

    def _has_context(self, code: str, context: set[str]) -> bool:
        """Context for an ambiguous word of topic ``code``: a related topic (itself, an ancestor or descendant), where a bare category
        word only supports a topic one level below it; or, outside exclusive categories, a sibling sub-topic ("heavy rain" supports
        "flood" in Disasters). A team nickname deep in Sports needs its own sport mentioned."""
        d = self.topics[code].depth
        root = code.split(".")[0]
        for c in context:
            if c not in self.topics:
                continue
            if self.related(code, c) and (self.topics[c].depth >= 2 or d <= 2):
                return True
            if root not in self.EXCLUSIVE and c.split(".")[0] == root and self.topics[c].depth >= 2 and d == 2:
                return True
        return False

    EXCLUSIVE = {"sport"}           # categories whose sub-branches rarely share a story (one sport per match report)

    def _resolve_siblings(self, score: dict[str, float]) -> dict[str, float]:
        """Within one category, a sub-topic whose evidence is much weaker than a sibling's is dropped:
        a cricket headline with one stray motorsport word stays cricket. Each branch's strength is its best score."""
        best: dict[str, float] = {}
        for c, v in score.items():
            if self.topics[c].depth >= 2:
                b = self._branch(c)
                best[b] = max(best.get(b, 0.0), v)
        by_root: dict[str, list[str]] = {}
        for b in best:
            by_root.setdefault(b.split(".")[0], []).append(b)
        drop: set[str] = set()
        for root, branches in by_root.items():
            if root not in self.EXCLUSIVE or len(branches) < 2:
                continue
            top = max(best[b] for b in branches)
            for b in branches:
                if best[b] < 0.6 * top:
                    drop.add(b)
        if not drop:
            return score
        return {c: v for c, v in score.items() if self.topics[c].depth < 2 or self._branch(c) not in drop}

    # ---- classification --------------------------------------------------------------------------
    # Words whose meaning depends on where the publisher is: "football" in a US or Canadian outlet is American football.
    LOCALE = {("football", "US"): "sport.american-football", ("football", "CA"): "sport.american-football"}

    def classify(self, title: str, lead: str = "", hints: list[str] | None = None,
                 threshold: float = 2.0, keep: int = 6, country: str = "") -> dict[str, float]:
        """Return {code: score} including ancestors. Title hit = 3, lead hit = 1, feed hint = 1.5 (a prior, never enough alone).
        ``country`` is the publisher's country, used only for words such as "football" whose meaning depends on it."""
        score: dict[str, float] = {}
        amb_hits: list[tuple[str, float]] = []
        seen: set[tuple[str, str]] = set()
        for text, w in ((title, 3.0), (lead, 1.0)):
            toks = norm_tokens(text)
            hits = [(i, n, g) for i, n, g in grams(toks) if g in self._index or (country and (g, country) in self.LOCALE)]
            covered: list[tuple[int, int]] = []
            for i, n, g in sorted(hits, key=lambda h: (-h[1], h[0])):
                # a phrase inside a longer matched phrase does not count on its own: "food bank" is not banking
                if any(a <= i and i + n <= b and (b - a) > n for a, b in covered):
                    continue
                covered.append((i, i + n))
                entries = self._index.get(g, ())
                local = self.LOCALE.get((g, country)) if country else None
                if local and local in self.topics:
                    entries = [(local, False)]
                for code, amb in entries:
                    if (g, code) in seen:
                        continue
                    seen.add((g, code))
                    if amb:
                        amb_hits.append((code, w))
                    else:
                        score[code] = score.get(code, 0.0) + w
        for text, w in ((title, 3.0), (lead, 1.0)):
            for rx, code in self._exact:
                if text and rx.search(text) and ("=", code, w) not in seen:
                    seen.add(("=", code, w))
                    score[code] = score.get(code, 0.0) + w
        text_context = {c for c, s in score.items() if s > 0}
        hints = [h for h in (hints or []) if h in self.topics]
        for h in hints:
            score[h] = score.get(h, 0.0) + 1.5
        # An ambiguous word ("Hamilton", "race", "match") counts only with context: a word of its own branch in the text, or a feed
        # hint below the category level. A cricket story from a general sports feed must not turn "Hamilton" (the city) into Formula 1.
        hint_context = {h for h in hints if self.topics[h].depth >= 2}
        for code, w in amb_hits:
            if self._has_context(code, text_context | hint_context):
                score[code] = score.get(code, 0.0) + w
        score = self._resolve_siblings(score)
        chosen = sorted(((c, s) for c, s in score.items() if s >= threshold), key=lambda x: -x[1])
        # keep the most specific topics: drop a code when a chosen descendant already carries it
        codes = [c for c, _ in chosen]
        leafy = [c for c in codes if not any(d in codes for d in self.descendants(c))] or codes
        top = sorted(leafy, key=lambda c: -score[c])[:keep]
        out: dict[str, float] = {}
        for c in top:
            out[c] = max(out.get(c, 0.0), score[c])
            for a in self.ancestors(c):
                out[a] = max(out.get(a, 0.0), score[c])
        return out
