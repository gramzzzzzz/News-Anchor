"""Text normalisation shared by the topic classifier, the place finder and the story clusterer.

Both the dictionary of aliases and the text being classified go through ``norm_tokens``, so matching is
exact on whole words (never on substrings) and case, accents, possessives and simple plurals do not matter."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_KEEP = re.compile(r"[^a-z0-9&+' ]+")
_TAG = re.compile(r"<[^>]+>")
STOP = frozenset("""a an the and or but for nor of in on at to from by with as is are was were be been being it its this that these
those after before over under into out up down off about against between during than then so such not no new says say said
will would could should may might can has have had do does did how what why who when where which while also more most just
amid per via vs us we our their his her he she they you your i my me them him one two three four five six seven eight nine ten
first last next now today yesterday tomorrow week year years day days monday tuesday wednesday thursday friday saturday sunday
report reports latest news live updates update watch video photos photo explained explainer""".split())


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    return " ".join(_TAG.sub(" ", s).replace("&nbsp;", " ").replace("&amp;", "&").split())


def _stem(t: str) -> str:
    if t.endswith("'s"):
        t = t[:-2]
    elif t.endswith("'"):
        t = t[:-1]
    if len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is")):
        t = t[:-1]
    return t


def norm_tokens(s: str | None) -> list[str]:
    if not s:
        return []
    s = unicodedata.normalize("NFKD", s.replace("\u2019", "'")).encode("ascii", "ignore").decode().lower()
    return [t for t in (_stem(x) for x in _KEEP.sub(" ", s).split()) if t]


def grams(tokens: list[str], max_n: int = 5):
    """Yield (start, n, 'tok tok') for every n-gram, longest first at each position."""
    L = len(tokens)
    for i in range(L):
        for n in range(min(max_n, L - i), 0, -1):
            yield i, n, " ".join(tokens[i:i + n])


def title_key(title: str, limit: int = 14) -> list[str]:
    """Significant tokens of a headline, used to cluster articles about the same event."""
    out, seen = [], set()
    for t in norm_tokens(title):
        if len(t) < 3 or t in STOP or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out[:limit]


_TRACK_PREFIX = ("utm_", "mc_", "at_", "ns_", "pk_", "gaa_")
_TRACK_EXACT = frozenset("""fbclid gclid ref traffic_source cmpid ocid ito taid xtor sref ftag rss igshid mkt_tok
share_id campaign_id""".split())


def _tracking(k: str) -> bool:
    k = k.lower()
    return k.startswith(_TRACK_PREFIX) or k in _TRACK_EXACT


def clean_url(url: str, *, key: bool = False) -> str:
    """Drop tracking parameters and fragments. With key=True also fold host case/www/trailing slash for hashing."""
    p = urlsplit(url.strip())
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _tracking(k)]
    path = p.path or "/"
    host = p.netloc
    if key:
        host = host.lower().removeprefix("www.")
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
    return urlunsplit((p.scheme.lower() or "https", host, path, urlencode(q), ""))


def canonical_url(url: str) -> str:
    return clean_url(url, key=True)


# ---- language ------------------------------------------------------------------------------------
_SCRIPTS = [((0x0900, 0x097F), "hi"), ((0x0980, 0x09FF), "bn"), ((0x0A00, 0x0A7F), "pa"), ((0x0A80, 0x0AFF), "gu"), ((0x0B00, 0x0B7F), "or"),
            ((0x0B80, 0x0BFF), "ta"), ((0x0C00, 0x0C7F), "te"), ((0x0C80, 0x0CFF), "kn"), ((0x0D00, 0x0D7F), "ml"), ((0x0D80, 0x0DFF), "si"),
            ((0x0E00, 0x0E7F), "th"), ((0x0E80, 0x0EFF), "lo"), ((0x1000, 0x109F), "my"), ((0x1780, 0x17FF), "km"), ((0x0590, 0x05FF), "he"),
            ((0x0600, 0x06FF), "ar"), ((0x0750, 0x077F), "ar"), ((0x0400, 0x04FF), "ru"), ((0x0370, 0x03FF), "el"), ((0x0530, 0x058F), "hy"),
            ((0x10A0, 0x10FF), "ka"), ((0x1200, 0x137F), "am"), ((0x3040, 0x30FF), "ja"), ((0xAC00, 0xD7AF), "ko"), ((0x1100, 0x11FF), "ko"),
            ((0x4E00, 0x9FFF), "zh"), ((0x0780, 0x07BF), "dv"), ((0x0D80, 0x0DFF), "si"), ((0x1800, 0x18AF), "mn"), ((0x0F00, 0x0FFF), "bo"),
            ((0x2D30, 0x2D7F), "ber"), ((0x13A0, 0x13FF), "chr")]
_STOP = {
    "en": "the of and to in for on with is are was at by from as after over says said will new its their this that has have be been not but who what how why amid".split(),
    "es": "el la los las de del que en y por para con una un se su al es más tras sobre como pero sus este esta entre desde hasta según".split(),
    "pt": "o os as de do da dos das que em e para com uma um no na ao é mais após sobre pelo pela não seu sua entre até também".split(),
    "fr": "le la les des de du et en pour avec une un est sur dans au aux par qui après plus pas son ses cette entre selon".split(),
    "it": "il lo la gli le di del della dello degli delle che in e per con una un è sul sulla sull nella nel dopo più dei tra fra anche non al alla".split(),
    "de": "der die das und in den von mit für ist auf dem nach zu ein eine sich bei aus nicht im vom zum zur über wird wie".split(),
    "nl": "de het een en van in op voor met is dat niet aan bij uit na wordt zijn naar door ook".split(),
    "id": "yang dan ke dari untuk dengan ini itu pada akan dalam tidak oleh bagi warga jadi bisa saat usai kata bakal hingga terkait soal minta".split(),
    "tr": "ve bir bu için ile çok ne olarak sonra yeni gibi daha değil kadar ilk oldu".split(),
    "pl": "w i na z do się nie że jest o po od za dla przez oraz już jak nowy".split(),
    "ro": "și în de la cu pe din pentru un o care este după mai sunt fost".split(),
    "cs": "a v na se je že s z do o pro po jako ve které byl".split(),
    "sv": "och i att det som på är av för med till den har inte efter om".split(),
    "no": "og i det som på er av for med til den har ikke etter en blir".split(),
    "da": "og i at det som på er af for med til den har ikke efter en bliver".split(),
    "fi": "ja on ei että se oli kun hän mutta myös ovat".split(),
    "hu": "a az és hogy nem is egy meg ez van már után lesz".split(),
    "sw": "na ya wa kwa ni la katika za kwamba hiyo huo wake".split(),
    "tl": "ang ng mga sa na at si ay para kay nang".split(),
    "vi": "của và các có cho là được trong với những một người không".split(),
    "ca": "el la els les de del i que en per amb una un és al als".split(),
    # Serbian, Croatian and Bosnian in Latin letters share these; told from Czech and Polish by ć, đ and the words themselves
    "hr": "je u i na za od se da su koji koja koje nakon godine kao iz sa što ali biti bio bila ili prema tijekom tokom danas".split(),
}
# letters that mark a language strongly (one is enough to tip a short headline)
_MARKS = {"hr": "ćđĆĐ", "tr": "ıışşğİ", "pl": "łżźśąęń", "cs": "řěůťď", "ro": "șțăȘȚ", "hu": "őű", "de": "ßäÄ", "es": "ñ¿¡", "pt": "ãõ", "fr": "œêèÀ",
          "vi": "ơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ", "is": "þð", "da": "æø", "sv": "å"}
_STOPSETS = {k: set(v) for k, v in _STOP.items()}
_WORDS = re.compile(r"[^\W\d_]+", re.U)


def detect_lang(text: str, hint: str = "") -> str:
    """Language of a headline (and lead) without a model: the writing system, then common short words for Latin-script languages.
    ``hint`` (the publisher's usual language) settles scripts shared by several languages (Devanagari, Arabic, Cyrillic) and
    texts too short to tell."""
    text = unicodedata.normalize("NFKC", text or "")              # styled letters (𝗕𝗢𝗟𝗗, ｗｉｄｅ) are plain Latin
    counts: dict[str, int] = {}
    latin = 0
    for ch in text:
        o = ord(ch)
        if ch.isalpha():
            if o < 0x0250:
                latin += 1
                continue
            for (lo, hi), code in _SCRIPTS:
                if lo <= o <= hi:
                    counts[code] = counts.get(code, 0) + 1
                    break
            else:
                # Latin Extended Additional (Vietnamese), IPA, phonetic small capitals (ꜰʟᴏᴏʀ) and Latin Extended-C/D are Latin
                if not (0x1E00 <= o <= 0x1EFF or 0x0250 <= o <= 0x02AF or 0x1D00 <= o <= 0x1DBF or 0x2C60 <= o <= 0x2C7F or 0xA720 <= o <= 0xA7FF):
                    counts["xx"] = counts.get("xx", 0) + 1      # a writing system without a code here: not English, whatever it is
                else:
                    latin += 1
    if counts and sum(counts.values()) >= latin:
        code = max(counts, key=counts.get)
        if code in ("ja", "zh") and counts.get("ja"):
            return "ja"
        if code == "hi" and hint in ("mr", "ne", "sa"):
            return hint
        if code == "ar":
            if any(c in text for c in "ےٹڈڑں") or hint == "ur":
                return "ur"
            if hint in ("fa", "ps") or (any(c in text for c in "پچژگ") and "ة" not in text):
                return hint if hint in ("fa", "ps") else "fa"
        if code == "ru":
            if any(c in text for c in "іїєґІЇЄҐ"):
                return "uk"
            if hint in ("bg", "sr", "mk", "kk", "ky", "mn", "be", "uk"):
                return hint
        return code
    words = [w.lower() for w in _WORDS.findall(text)]
    if not words:
        return hint or "en"
    scores = {k: sum(1 for w in words if w in s) for k, s in _STOPSETS.items()}
    for k, marks in _MARKS.items():
        if any(c in text for c in marks):
            scores[k] = scores.get(k, 0) + 2
    best = max(scores, key=scores.get)
    if scores[best] == 0 or (len(words) < 6 and scores[best] < 2):
        return hint or "en"
    ties = [k for k, v in scores.items() if v == scores[best]]
    if hint in ties:
        return hint
    return best


def fingerprint(title: str, lead: str) -> str | None:
    """Identity of an article's text: outlets that print the same wire copy (same headline and same opening) share it.
    Letters of any script count; case, spaces and punctuation do not. Both parts are used, so a feed's boilerplate lead
    ("Read more on our website ...") never ties its different stories together. None for headlines too short to be distinctive."""
    import hashlib
    t = re.sub(r"[\W_]+", "", (title or "").lower())
    if len(t) < 12:
        return None
    return hashlib.md5((t[:120] + "|" + re.sub(r"[\W_]+", "", (lead or "").lower())[:120]).encode()).hexdigest()


def norm_title(title: str) -> str:
    """A headline reduced to its letters and digits (any script), for spotting word-for-word copies. Empty when too short to tell."""
    t = re.sub(r"[\W_]+", "", (title or "").lower())
    return t if len(t) >= 30 else ""


# ---- the line under the headline -----------------------------------------------------------------
# Writing systems, grouped so a text is compared with the right family: kanji and kana are one, the Arabic, Persian and Urdu alphabet
# is one, and so on.
_GROUP = {"ja": "cjk", "zh": "cjk", "ar": "arabic", "ru": "cyrillic", "el": "greek", "he": "hebrew", "hi": "devanagari"}


def script_of(text: str) -> str:
    """The writing system most of a text's letters are in: 'latin', 'cjk', 'arabic', 'cyrillic' ...; '' when it has no letters."""
    counts: dict[str, int] = {}
    for ch in text or "":
        if not ch.isalpha():
            continue
        o = ord(ch)
        if o < 0x0250 or 0x1E00 <= o <= 0x1EFF:
            k = "latin"
        else:
            k = "other"
            for (lo, hi), code in _SCRIPTS:
                if lo <= o <= hi:
                    k = _GROUP.get(code, code)
                    break
        counts[k] = counts.get(k, 0) + 1
    return max(counts, key=counts.get) if counts else ""


_SENT = re.compile(r"(?<=[.!?\u061F\u3002\u06D4])\s+|\s*\n+\s*|\s+[|\u2022]\s+")


def _sentences(text: str) -> list[str]:
    return [s for s in (x.strip() for x in _SENT.split(text or "")) if s]


def clean_lead(title: str, lead: str) -> str:
    """Keep only the sentences of a lead written in the headline's writing system. Feeds that republish an English article often put
    their own notice in front of it in another script (Cedar News: an Arabic "first published on Cedarnews.net, visit our website"),
    and that text was being shown under English headlines and read as part of the story."""
    if not lead:
        return lead or ""
    want = script_of(title)
    if not want:
        return lead
    parts = _sentences(lead)
    kept = [s for s in parts if script_of(s) in ("", want)]
    if len(kept) == len(parts):
        return lead
    return " ".join(kept).strip()


def clean_leads(items: list[dict]) -> None:
    """In place, for one feed's items: drop text in another script from each lead, then sentences that open or close most of the
    feed's leads (a publisher's own notice: "This article first appeared on ...", "Follow us on ..."), whatever the language."""
    for it in items:
        it["lead"] = clean_lead(it.get("title", ""), it.get("lead") or "")
    leads = [it["lead"] for it in items if it.get("lead")]
    if len(leads) < 3:
        return
    firsts, lasts = Counter(), Counter()
    for lead in leads:
        s = _sentences(lead)
        if len(s) >= 1:
            firsts[s[0]] += 1
            lasts[s[-1]] += 1
    bar = max(3, len(leads) // 2)
    heads = [s for s, n in firsts.items() if n >= bar and len(s) >= 20]
    tails = [s for s, n in lasts.items() if n >= bar and len(s) >= 20]
    if not heads and not tails:
        return
    for it in items:
        lead = it.get("lead") or ""
        for h in heads:
            if lead.startswith(h):
                lead = lead[len(h):].strip()
        for t in tails:
            if lead.endswith(t):
                lead = lead[:-len(t)].strip()
        it["lead"] = lead


_MEDIA = re.compile(r"\.(?:jpe?g|png|gif|webp|avif|svg|bmp|tiff?|mp4|m4v|mov|webm|mp3|m4a|wav|ogg)(?:$|[?#])", re.I)


def is_media_url(url: str) -> bool:
    """A link to a picture, a recording or a document, not to an article page (some feeds list their images as items)."""
    try:
        return bool(_MEDIA.search(urlsplit(url).path + ("?" if urlsplit(url).query else "")))
    except ValueError:
        return False
