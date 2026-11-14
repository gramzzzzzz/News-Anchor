"""Wikipedia's 'Reliable sources/Perennial sources' list: sources editors have discussed repeatedly, with the consensus reached.
Read through the MediaWiki API (CC BY-SA 4.0). Each row names the source, gives its status and links the domains it covers."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

API = ("https://en.wikipedia.org/w/api.php?action=parse&page=Wikipedia:Reliable_sources/Perennial_sources"
       "&prop=text&format=json&formatversion=2&redirects=1")
PAGE = "https://en.wikipedia.org/wiki/Wikipedia:Reliable_sources/Perennial_sources"
STATUSES = ("generally reliable", "no consensus", "generally unreliable", "deprecated", "blacklisted")

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_ALT = re.compile(r'alt="([^"]+)"')
_DOM = re.compile(r"Special:LinkSearch/\*?\.?([a-z0-9.-]+\.[a-z]{2,})", re.I)
_SC = re.compile(r'class="wp-rsp-sc"[^>]*>\s*<a [^>]*>(WP:[A-Za-z0-9_]+)</a>')
_TAG = re.compile(r"<[^>]+>")


@dataclass
class Entry:
    name: str
    status: str
    domains: list[str] = field(default_factory=list)
    shortcut: str | None = None
    summary: str = ""

    @property
    def url(self) -> str:
        return f"https://en.wikipedia.org/wiki/{self.shortcut}" if self.shortcut else PAGE


def _text(s: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", s)).replace("\xa0", " ").split())


def parse(page_html: str) -> list[Entry]:
    out: list[Entry] = []
    for row in _ROW.findall(page_html):
        cells = _CELL.findall(row)
        if len(cells) < 5:
            continue
        status = next((a.lower() for a in _ALT.findall(cells[1]) if a.lower() in STATUSES), None)
        if not status:
            continue
        domains = sorted({d.lower() for d in _DOM.findall(row)})
        if not domains:
            continue
        sc = _SC.search(cells[0])
        summary = max((_text(c) for c in cells[2:]), key=len)
        out.append(Entry(name=_text(cells[0]).split(" WP:")[0].strip(), status=status, domains=domains, shortcut=sc.group(1) if sc else None,
                         summary=summary[:600]))
    return out
