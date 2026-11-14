"""Advertisements and promotions in headlines.

A story is *ad-like* when its headline carries a marker of paid or promotional content (sponsored posts, coupon and promo codes, "buy now"
calls to action, affiliate disclosures, shopping-event deal roundups). The pattern is written so that Python's ``re`` and PostgreSQL's ``~*``
read it the same way (no ``\\b``, which PostgreSQL reads as a backspace): the clean-up job tests it in SQL, the lists in Python.

An ad-like story is not hidden for being ad-like alone. It is held back from Latest, and the clean-up removes it when no second independent
outlet wrote it (``indep <= 1``) after the grace period. Percentages alone (``40% off``) are not a marker: financial headlines use them."""
from __future__ import annotations

import re

_WORD = r"(^|[^a-z0-9])"
_END = r"($|[^a-z0-9])"
MARKERS = (
    r"sponsored (content|post|by)", r"sponsored ?[:|]", r"advertorial", r"paid (post|content|partnership|promotion)", r"partner content", r"promoted content",
    r"promo(tion(al)?)? codes?", r"coupon(s| codes?)?", r"discount codes?", r"voucher codes?", r"affiliate (link|disclosure|commission)s?",
    r"as an amazon associate", r"buy now", r"shop now", r"order now", r"click here", r"limited[- ]time (offer|deal)", r"free trial",
    r"(black friday|cyber monday|prime day|boxing day) (deals?|sales?|offers?|discounts?)", r"best (deals?|discounts?) (on|for|of|this)",
    r"deals? of the day", r"(casino|slots?) (bonus|bonuses|promo|free spins)", r"free spins", r"no deposit bonus",
    r"(\d+|up to \d+)% (off|discount) (on|for|with|code|your)",
)
PATTERN = _WORD + "(" + "|".join(MARKERS) + ")" + _END
_RE = re.compile(PATTERN, re.IGNORECASE)


def looks_like_ad(title: str, lead: str = "") -> bool:
    """True when the headline (or the first words of the lead) carries a marker of an advertisement."""
    return bool(_RE.search(title or "") or _RE.search((lead or "")[:160]))
