"""Reads a publisher's home page for transparency indicators: pages it links to, and schema.org fields it declares."""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser

INDICATORS: dict[str, tuple[str, re.Pattern]] = {
    "about": ("About page", re.compile(r"\babout( us)?\b|who we are|our story|our mission", re.I)),
    "contact": ("Contact page", re.compile(r"\bcontact\b|reach us|get in touch|feedback", re.I)),
    "standards": ("Ethics or editorial standards", re.compile(r"\bethic|editorial (policy|standards|guidelines|code)|code of (conduct|ethics|principles)|publishing principles|journalism trust|standards and practices", re.I)),
    "corrections": ("Corrections policy", re.compile(r"\bcorrection|clarification|errata", re.I)),
    "masthead": ("Masthead or team", re.compile(r"masthead|our (team|people|staff)|editorial team|leadership|who's who", re.I)),
    "ownership": ("Ownership or funding", re.compile(r"ownership|who owns|funding|our owners|about the (company|group|trust)|parent company", re.I)),
}
LD_KEYS = {"publishingPrinciples": "standards", "ethicsPolicy": "standards", "correctionsPolicy": "corrections", "masthead": "masthead",
           "ownershipFundingInfo": "ownership", "verificationFactCheckingPolicy": "standards", "actionableFeedbackPolicy": "contact"}


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.ld: list[str] = []
        self._a: list | None = None
        self._ld = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self._a = [a["href"], a.get("aria-label") or a.get("title") or ""]
        elif tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._ld = True

    def handle_data(self, data):
        if self._a is not None:
            self._a[1] += " " + data
        elif self._ld:
            self.ld.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._a is not None:
            self.links.append((self._a[0], " ".join(self._a[1].split())))
            self._a = None
        elif tag == "script":
            self._ld = False


def _walk(o, found: set[str]) -> None:
    if isinstance(o, dict):
        for k, v in o.items():
            if k in LD_KEYS and v:
                found.add(LD_KEYS[k])
            _walk(v, found)
    elif isinstance(o, list):
        for v in o:
            _walk(v, found)


def analyse(page_html: str) -> dict[str, str]:
    """Return {indicator: how it was found} for the indicators present."""
    p = _Page()
    try:
        p.feed(page_html)
    except Exception:
        pass
    found: dict[str, str] = {}
    for href, text in p.links:
        for key, (_label, rx) in INDICATORS.items():
            if key not in found and (rx.search(text) or rx.search(href.rsplit("/", 2)[-2] if href.count("/") > 2 else href)):
                found[key] = f"link “{text[:40] or href[:40]}”"
    declared: set[str] = set()
    for blob in p.ld:
        try:
            _walk(json.loads(blob), declared)
        except ValueError:
            continue
    for k in declared:
        found.setdefault(k, "declared in structured data")
    return found
