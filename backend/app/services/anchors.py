"""What a headline is about, as a small set of names: the people, teams, companies, series and countries it names.

Two reports are about the same event only if they name the same things. An embedding, or shared ordinary words, cannot tell
"Hamilton wins pole for Ferrari at the Mexico City Grand Prix" from "Marquez wins pole at the Malaysian Grand Prix": both are a
"driver wins pole at a Grand Prix" sentence. Their names are disjoint ({hamilton, ferrari, @MX} against {marquez, @MY}), and that is
the evidence that they are two events. This is the entity overlap used by news-stream clustering systems (Miranda et al. 2018,
"Multilingual clustering of streaming news"; Saravanakumar et al. 2021, "Event-driven news stream clustering using entity-aware
contextual embeddings"; Liu et al. 2017, "Story Forest"), done with headline capitalisation instead of a tagger.

A name ("anchor") is a word written with a capital where ordinary words are not (measured from real headlines:
data/common_words.txt, words written in lower case in at least a quarter of their uses), a short code with a digit (F1, G7, A320),
or an acronym (NBA, FBI). Places count as their country (@MX), so "Mexico City" and "Mexico" agree; demonyms do too ("Malaysian").
Name-like words that many unrelated events share ("Grand Prix", "Cup", "Senate", "University") are not anchors."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from app.services.textutil import _stem

DATA = Path(__file__).resolve().parent.parent / "data"

GENERIC = frozenset("""
grand prix gp cup trophy league championship championships series open games olympic olympics paralympic award awards festival summit
conference forum expo show tour final finals semifinal semifinals quarterfinal playoff playoffs season week weekend day night month year
club fc afc sc cf ac united city county state states republic kingdom federation union association council committee commission board
agency authority institute university college school academy hospital airport station park street road avenue bridge river lake mount
mountain island islands bay valley beach coast north south east west northern southern eastern western central northeast northwest
southeast southwest upper lower greater new old high royal national international global world federal regional provincial municipal
government ministry minister department secretary president vice prime chief governor mayor senator senate congress parliament
assembly house court supreme justice police army navy military forces force party democrats democrat republicans republican gop
labour labor conservative conservatives liberal liberals green greens inc corp corporation ltd llc co company group holdings bank
news times post daily journal herald tribune gazette telegraph mail express star sun live update updates breaking watch video videos
photo photos gallery report reports exclusive analysis opinion editorial review preview recap highlights podcast interview explainer
mr mrs ms dr sir st saint lord lady king queen prince princess pope
january february march april may june july august september october november december jan feb mar apr jun jul aug sep sept oct nov dec
monday tuesday wednesday thursday friday saturday sunday today tonight tomorrow yesterday
christmas halloween thanksgiving easter diwali eid ramadan
european african asian arab latin western eastern
ai ceo cfo coo cto pm mp mla mlas mps eu un uk us usa
the a an and or of in on at to for with by from as is are was were be it its this that he she they we you i
""".split())

# Demonyms read as their country. Only common ones; anything missing simply stays an ordinary name.
DEMONYMS = {
    "american": "US", "british": "GB", "english": "GB", "scottish": "GB", "welsh": "GB", "irish": "IE", "french": "FR", "german": "DE",
    "italian": "IT", "spanish": "ES", "portuguese": "PT", "dutch": "NL", "belgian": "BE", "swiss": "CH", "austrian": "AT", "swedish": "SE",
    "norwegian": "NO", "danish": "DK", "finnish": "FI", "icelandic": "IS", "polish": "PL", "czech": "CZ", "slovak": "SK", "hungarian": "HU",
    "romanian": "RO", "bulgarian": "BG", "greek": "GR", "turkish": "TR", "cypriot": "CY", "serbian": "RS", "croatian": "HR", "bosnian": "BA",
    "slovenian": "SI", "albanian": "AL", "ukrainian": "UA", "russian": "RU", "belarusian": "BY", "moldovan": "MD", "georgian": "GE",
    "armenian": "AM", "azerbaijani": "AZ", "kazakh": "KZ", "uzbek": "UZ", "estonian": "EE", "latvian": "LV", "lithuanian": "LT",
    "israeli": "IL", "palestinian": "PS", "lebanese": "LB", "syrian": "SY", "jordanian": "JO", "iraqi": "IQ", "iranian": "IR",
    "saudi": "SA", "emirati": "AE", "qatari": "QA", "kuwaiti": "KW", "bahraini": "BH", "omani": "OM", "yemeni": "YE", "egyptian": "EG",
    "libyan": "LY", "tunisian": "TN", "algerian": "DZ", "moroccan": "MA", "sudanese": "SD", "ethiopian": "ET", "eritrean": "ER",
    "somali": "SO", "kenyan": "KE", "ugandan": "UG", "tanzanian": "TZ", "rwandan": "RW", "congolese": "CD", "nigerian": "NG",
    "ghanaian": "GH", "senegalese": "SN", "malian": "ML", "cameroonian": "CM", "angolan": "AO", "zambian": "ZM", "zimbabwean": "ZW",
    "mozambican": "MZ", "malawian": "MW", "namibian": "NA", "botswanan": "BW", "south african": "ZA", "afghan": "AF", "pakistani": "PK",
    "indian": "IN", "bangladeshi": "BD", "nepali": "NP", "nepalese": "NP", "sri lankan": "LK", "lankan": "LK", "maldivian": "MV",
    "chinese": "CN", "taiwanese": "TW", "japanese": "JP", "korean": "KR", "north korean": "KP", "mongolian": "MN", "vietnamese": "VN",
    "thai": "TH", "cambodian": "KH", "laotian": "LA", "burmese": "MM", "myanmar": "MM", "malaysian": "MY", "singaporean": "SG",
    "indonesian": "ID", "filipino": "PH", "philippine": "PH", "australian": "AU", "aussie": "AU", "kiwi": "NZ", "zealander": "NZ",
    "fijian": "FJ", "canadian": "CA", "mexican": "MX", "cuban": "CU", "haitian": "HT", "dominican": "DO", "jamaican": "JM",
    "guatemalan": "GT", "honduran": "HN", "salvadoran": "SV", "nicaraguan": "NI", "costa rican": "CR", "panamanian": "PA",
    "colombian": "CO", "venezuelan": "VE", "ecuadorian": "EC", "peruvian": "PE", "bolivian": "BO", "chilean": "CL", "argentine": "AR",
    "argentinian": "AR", "uruguayan": "UY", "paraguayan": "PY", "brazilian": "BR", "puerto rican": "PR",
}

_WORD = re.compile(r"[A-Za-z0-9\u00C0-\u024F][A-Za-z0-9\u00C0-\u024F'\u2019.&\-]*")
_YEARISH = re.compile(r"^\d{1,4}(?:st|nd|rd|th|s)?$")
_COMMON: set[str] | None = None


def _common() -> set[str]:
    global _COMMON
    if _COMMON is None:
        p = DATA / "common_words.txt"
        _COMMON = {w.strip() for w in p.read_text(encoding="utf-8").splitlines() if w.strip() and not w.startswith("#")} if p.exists() else set()
    return _COMMON


def _plain(w: str) -> str:
    w = unicodedata.normalize("NFKD", w.replace("\u2019", "'")).encode("ascii", "ignore").decode()
    w = w.strip(".-'&")
    if w.lower().endswith("'s"):
        w = w[:-2]
    return w


def _name(lw: str) -> str:
    """One spelling for a name's forms: "Houthis" and "Houthi" agree (the same simple plural rule as headline words)."""
    return _stem(lw)


def anchors(title: str, gazetteer=None) -> set[str]:
    """The names a headline mentions (lower case; countries as @XX)."""
    if not title:
        return set()
    common = _common()
    words = [w for w in (_plain(x) for x in _WORD.findall(title)) if w]
    out: set[str] = set()
    lowered = [w.lower() for w in words]
    i = 0
    while i < len(words):
        w, lw = words[i], lowered[i]
        two = f"{lw} {lowered[i + 1]}" if i + 1 < len(words) else ""
        if two in DEMONYMS:
            out.add("@" + DEMONYMS[two]); i += 2; continue
        if lw in DEMONYMS and (w[0].isupper()):
            out.add("@" + DEMONYMS[lw]); i += 1; continue
        if lw.endswith("s") and lw[:-1] in DEMONYMS and w[0].isupper():
            out.add("@" + DEMONYMS[lw[:-1]]); i += 1; continue     # "Ukrainians", "Americans"
        if lw in GENERIC or _YEARISH.match(lw):
            i += 1; continue
        has_digit, has_alpha = any(c.isdigit() for c in w), any(c.isalpha() for c in w)
        if has_digit and has_alpha:
            out.add(lw)                                         # F1, G7, A320, COVID-19 (codes are not stemmed)
        elif not has_alpha:
            pass
        elif w.isupper() and 2 <= len(w) <= 6:
            out.add(_name(lw))                                  # NBA, FBI, NATO
        elif len(w) >= 3 and any(c.isupper() for c in w) and lw not in common:
            out.add(_name(lw))                                  # Ferrari, Hamilton, MotoGP, iPhone; not "Wins" in a Title Case headline
        i += 1
    if gazetteer is not None:
        try:
            for code in gazetteer.detect(title, ""):
                c = gazetteer._country_of(code) if code in gazetteer.places else code
                if c and c != "world" and gazetteer.places.get(c) and gazetteer.places[c].level == "country":
                    out.add("@" + c)
        except Exception:                                       # noqa: BLE001 - a place lookup never blocks clustering
            pass
    return out


def conflict(a: set[str], b: set[str], min_each: int = 2) -> bool:
    """Two headlines that each name at least ``min_each`` things and share none."""
    return len(a) >= min_each and len(b) >= min_each and not (a & b)


def shared(a: set[str], b: set[str]) -> int:
    return len(a & b)


def rare_shared(tokens_a, tokens_b, names: set[str], frequent: set[str]) -> list[str]:
    """Headline words (title_key tokens) both share that are neither names nor generic and that few stories in the window use
    ("helicopter", "schoolboy", "allowance"). Two reports that share one are probably about the same thing even when their names
    differ ("Joshimath" and "Indian Himalayan state"). ``frequent``: words used by more stories than the rarity bar."""
    return [t for t in set(tokens_a) & set(tokens_b) if t not in GENERIC and t not in names and t not in frequent]


def different_events(names_a: set[str], names_b: set[str], tokens_a, tokens_b, frequent: set[str]) -> bool:
    """The veto: each headline names two or more things, none in common, and they share too few rare words (one when either names
    only two things, two otherwise: "tense" alone does not make "Antonelli tense in Mexico City" the MotoGP sprint "Acosta beats
    Marquez in tense sprint"). On 360 sampled pairs from the live database (storyline links and story memberships) it separated
    18 pairs, 17 rightly."""
    if not conflict(names_a, names_b):
        return False
    need = 1 if min(len(names_a), len(names_b)) <= 2 else 2
    return len(rare_shared(tokens_a, tokens_b, names_a | names_b, frequent)) < need


def related(names_a: set[str], names_b: set[str], tokens_a, tokens_b, frequent: set[str]) -> bool:
    """Positive evidence that two developments days apart belong to one storyline: a shared name, a shared rare word, or the same
    headline words."""
    if names_a & names_b or rare_shared(tokens_a, tokens_b, names_a | names_b, frequent):
        return True
    ta, tb = set(tokens_a), set(tokens_b)
    return len(ta) >= 3 and ta == tb


def rarity_bar(n_stories: int) -> int:
    """A word is rare when at most this many stories of the window use it (0.04% of them, at least 20)."""
    return max(20, int(n_stories * 0.0004))
