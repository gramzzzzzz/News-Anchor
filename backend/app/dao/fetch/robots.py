"""robots.txt matching by RFC 9309."""
from __future__ import annotations

import re
from urllib.parse import urlsplit


def _rule_re(pattern: str) -> re.Pattern:
    end = pattern.endswith("$")
    body = re.escape(pattern[:-1] if end else pattern).replace(r"\*", ".*")
    return re.compile(body + ("$" if end else ""))


def robots_allows(robots_txt: str, url: str, agent: str = "NewsAnchor") -> bool:
    """Whether robots.txt lets ``agent`` read ``url``, by the rules of RFC 9309 (which Google follows for its own file): the group naming
    the agent, else the * group; the longest matching rule wins and Allow wins a tie. Python's urllib.robotparser applies the first
    matching line instead, and would read Google News' "Disallow: /" followed by "Allow: /topics/" as disallowing everything."""
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if rules:
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(v.lower())
        elif k in ("allow", "disallow") and agents and v:
            rules.append((k == "allow", v))
    if agents:
        groups.append((agents, rules))
    a = agent.lower()
    mine = [r for ags, r in groups if any(x != "*" and x in a for x in ags)]
    chosen = [x for r in mine for x in r] if mine else [x for ags, r in groups if "*" in ags for x in r]
    sp = urlsplit(url)
    path = (sp.path or "/") + (f"?{sp.query}" if sp.query else "")
    best: tuple[int, bool] | None = None
    for allow, pat in chosen:
        if _rule_re(pat).match(path) and (best is None or (len(pat), allow) > best):
            best = (len(pat), allow)
    return True if best is None else best[1]
