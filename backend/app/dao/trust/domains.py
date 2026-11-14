"""Domain helpers shared by the trust signals."""
from __future__ import annotations

TWO_PART = {"co.uk", "org.uk", "ac.uk", "com.au", "net.au", "org.au", "co.in", "org.in", "net.in", "co.jp", "or.jp", "co.nz", "co.za",
            "com.hk", "com.sg", "co.kr", "or.kr", "com.br", "com.cn", "com.tr", "com.pk", "com.bd", "com.np", "com.ph", "com.my", "co.id"}
# Several unrelated publishers live under these hosts, so facts about the host say nothing about one of them.
SHARED_HOSTS = {"substack.com", "medium.com", "blogspot.com", "wordpress.com", "github.io", "tumblr.com", "wixsite.com"}


# Generic second-level labels used under country codes (com.ar, co.ke, gov.in ...): a heuristic in place of the full public-suffix list.
GENERIC_SLD = {"co", "com", "org", "net", "gov", "edu", "ac", "or", "ne", "go", "gob", "nic"}


def _two_part_suffix(parts: list[str]) -> bool:
    last2 = ".".join(parts[-2:])
    return last2 in TWO_PART or (len(parts[-1]) == 2 and parts[-2] in GENERIC_SLD)


def registrable(domain: str) -> str:
    parts = domain.lower().strip(".").split(".")
    if len(parts) <= 2:
        return ".".join(parts)
    if _two_part_suffix(parts):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def candidates(domain: str) -> list[str]:
    """Most specific first: the full host, then each parent down to the registrable domain."""
    d = domain.lower().strip(".")
    if registrable(d) in SHARED_HOSTS:
        return [d]
    out, parts = [], d.split(".")
    reg = registrable(d).count(".") + 1
    for i in range(0, len(parts) - reg + 1):
        out.append(".".join(parts[i:]))
    return out
