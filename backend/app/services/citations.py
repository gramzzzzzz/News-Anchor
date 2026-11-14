"""Who cites whom: the links an outlet puts in its articles, and outlets it names as its source ("..., Tasnim reported").

A link or a credit from a newsroom is a judgement that the other site is worth sending readers to. TrustRank (Gyöngyi, Garcia-Molina
and Pedersen 2004) spreads trust from a seed of expert-reviewed sites along such links; Fairbanks et al. 2018 ("Credibility assessment
in the news: do we need to read?") found the link structure predicts reliability better than the articles' text; Burdisso et al. 2024
("birds of a feather flock together") learned reliability from the news hyperlink graph with a Spearman correlation of 0.80 against
journalists' ratings; Carragher et al. 2024 (ICWSM) classify and discover unreliable sites from link neighbourhoods. Here the seed is
every source with an expert rating, and only one step is taken: an unrated site gets the average rating of the rated outlets that cite
it, each outlet counted once. That is easy to check on the source card and cannot be gamed by a ring of unrated sites linking to each
other."""
from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlsplit

from app.dao.trust.domains import registrable

# platforms, link shorteners, ad and tracking hosts, archives and reference sites: a link to them says nothing about a publisher
PLATFORMS = frozenset("""
twitter.com x.com t.co facebook.com fb.com instagram.com threads.net tiktok.com youtube.com youtu.be linkedin.com pinterest.com reddit.com
whatsapp.com telegram.org t.me bsky.app mastodon.social snapchat.com tumblr.com flickr.com vimeo.com soundcloud.com spotify.com
apple.com apps.apple.com google.com goo.gl g.co googleusercontent.com googleapis.com gstatic.com doubleclick.net googletagmanager.com
google-analytics.com amazon.com amzn.to amazon-adsystem.com bit.ly ow.ly buff.ly tinyurl.com dlvr.it ift.tt lnkd.in feedburner.com
feedproxy.google.com wp.com wordpress.com wordpress.org gravatar.com w.org disqus.com addtoany.com sharethis.com outbrain.com taboola.com
archive.org web.archive.org archive.ph archive.today wikipedia.org wikimedia.org wikidata.org gofundme.com patreon.com paypal.com
substack.com medium.com blogspot.com github.com gitlab.com dropbox.com scribd.com issuu.com cloudfront.net akamaihd.net
""".split())
_P = re.compile(r"<p\b[^>]*>(.*?)</p>", re.S | re.I)
_HREF = re.compile(r"""href=["'](https?://[^"'\s>]+)["']""", re.I)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def cited_domains(urls, own_domain: str = "") -> Counter:
    """Registrable domains cited by these links, each counted once per article, without the outlet's own and platforms."""
    own = registrable(own_domain.lower().removeprefix("www.")) if own_domain else ""
    out: Counter = Counter()
    for d in {registrable(h) for h in (_host(u) for u in urls) if h}:
        if d and d != own and d not in PLATFORMS and "." in d:
            out[d] += 1
    return out


def page_links(page: str) -> list[str]:
    """Links inside the paragraphs of an article page (editorial citations, not menus, footers or share buttons)."""
    out = []
    for m in _P.finditer(page[:2_000_000]):
        out += _HREF.findall(m.group(1))
        if len(out) > 200:
            break
    return out
