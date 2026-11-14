"""Places: Global > Continent > Region > Country > State > City, plus Groups, and a gazetteer that finds them in headlines.

The tree is the UN's geographic scheme (M49): five continents, their regions (the UN intermediate region where there is one, such as
Western Africa or South America, otherwise the sub-region, such as Southern Asia), then countries. Every country has exactly one
place in the tree, so no two menu entries overlap. Political and economic groupings that cut across it (EU, NATO, ASEAN, the
African Union, G20, the Middle East ...) are Groups: a group's scope is the group itself plus every member country.

Countries and UN regions come from data/countries.json (ISO 3166 with UN M49 regions, CC BY-SA 4.0,
github.com/lukes/ISO-3166-Countries-with-Regional-Codes). First-level divisions of every country and every city of
15,000 people or more come from GeoNames (CC BY 4.0, geonames.org; data/geonames_*.tsv, built by scripts/build_geonames.py).
India's states keep their own short codes (IN-TN) and its main cities their curated aliases.

Many place names are shared (Salem is in Tamil Nadu, Oregon and Massachusetts; Hamilton in New Zealand, Ontario and
Scotland; Hamilton is also a racing driver). A city is only tagged when the text or the publisher says which one is meant:
its state or country is named too, or it is by far the biggest place of that name, or the publisher is in a country with
exactly one city of that name. Otherwise only the country is tagged, or nothing."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import re

from app.services.textutil import _KEEP, _stem, grams, norm_tokens

_US = re.compile(r"\b(?:US|U\.S\.|U\.S\.A\.|USA)\b")   # case-sensitive: the pronoun "us" is not a place

DATA = Path(__file__).resolve().parent.parent / "data"
WORLD = "world"


@dataclass
class Place:
    code: str
    name: str
    level: str                 # world | region (continent) | subregion | group | country | state | city
    parent: str | None
    aliases: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)     # groups a country belongs to (not part of the tree)
    members: list[str] = field(default_factory=list)     # a group's member countries
    population: int = 0
    featured: bool = False                               # shown directly under its country in the menu (India's main cities)


CONTINENTS = {"Africa": "africa", "Americas": "americas", "Asia": "asia", "Europe": "europe", "Oceania": "oceania"}
CONTINENT_ALIASES = {"africa": ["africa", "african continent"], "americas": ["the americas"], "asia": ["asia"], "europe": ["europe"],
                     "oceania": ["oceania", "pacific islands"]}
# UN region names, in the form news uses
SUBREGION_NAMES = {"Southern Asia": "South Asia", "South-eastern Asia": "Southeast Asia", "Eastern Asia": "East Asia", "Western Asia": "West Asia",
                   "Northern America": "North America", "Northern Africa": "North Africa", "Western Africa": "West Africa",
                   "Eastern Africa": "East Africa", "Middle Africa": "Central Africa"}
SUBREGION_ALIASES = {"south-asia": ["south asia", "subcontinent"], "southeast-asia": ["southeast asia", "south east asia"], "east-asia": ["east asia"],
                     "central-asia": ["central asia"], "west-asia": ["west asia", "western asia"], "north-africa": ["north africa", "maghreb"],
                     "west-africa": ["west africa"], "east-africa": ["east africa", "horn of africa"], "central-africa": ["central africa"],
                     "southern-africa": ["southern africa"], "caribbean": ["caribbean"], "central-america": ["central america"],
                     "south-america": ["south america"], "north-america": ["north america"], "eastern-europe": ["eastern europe"],
                     "western-europe": ["western europe"], "northern-europe": ["northern europe", "nordic countries", "scandinavia", "baltic states"],
                     "southern-europe": ["southern europe", "balkans"], "australia-and-new-zealand": ["australasia"]}
EU = set("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split())
# Groups (members as of November 2026). code: (name, aliases, members). "@africa" = every sovereign country of the continent.
GROUPS = {
    "eu": ("European Union", ["european union", "eu", "brussels", "european commission", "eurozone"], sorted(EU)),
    "nato": ("NATO", ["nato", "north atlantic treaty organization"],
             "AL BE BG CA HR CZ DK EE FI FR DE GR HU IS IT LV LT LU ME NL MK NO PL PT RO SK SI ES SE TR GB US".split()),
    "g7": ("G7", ["g7", "group of seven"], "CA FR DE IT JP GB US".split()),
    "g20": ("G20", ["g20", "group of twenty"], "AR AU BR CA CN FR DE IN ID IT JP MX RU SA ZA KR TR GB US".split()),
    "brics": ("BRICS", ["brics"], "BR RU IN CN ZA EG ET IR AE ID".split()),
    "quad": ("Quad", ["quad grouping", "quad summit", "quad leaders"], "US IN JP AU".split()),
    "asean": ("ASEAN", ["asean"], "BN KH ID LA MY MM PH SG TH VN TL".split()),
    "saarc": ("SAARC", ["saarc"], "AF BD BT IN MV NP PK LK".split()),
    "african-union": ("African Union", ["african union"], ["@africa"]),
    "arab-league": ("Arab League", ["arab league", "arab states"], "DZ BH KM DJ EG IQ JO KW LB LY MR MA OM PS QA SA SO SD SY TN AE YE".split()),
    "gcc": ("Gulf Cooperation Council", ["gulf cooperation council", "gcc", "gulf states"], "BH KW OM QA SA AE".split()),
    "opec": ("OPEC", ["opec", "opec+"], "DZ CG GQ GA IR IQ KW LY NG SA AE VE".split()),
    "mercosur": ("Mercosur", ["mercosur"], "AR BR PY UY BO".split()),
    "middle-east": ("Middle East", ["middle east", "mideast", "persian gulf"], "BH CY EG IR IQ IL JO KW LB OM PS QA SA SY TR AE YE".split()),
    "latin-america": ("Latin America and the Caribbean", ["latin america"], ["@Latin America and the Caribbean"]),
    "sub-saharan-africa": ("Sub-Saharan Africa", ["sub saharan africa"], ["@Sub-Saharan Africa"]),
}
NOT_SOVEREIGN_AFRICA = {"RE", "YT", "SH", "IO", "TF"}
# (display name, aliases). Aliases that are everyday words (Georgia, Jordan, Chad) are left out on purpose.
COUNTRY_OVERRIDES = {
    "US": ("United States", ["united states", "united states of america", "usa", "u s", "u s a", "america", "washington dc"]),
    "GB": ("United Kingdom", ["united kingdom", "uk", "u k", "britain", "great britain", "england", "scotland", "wales", "northern ireland"]),
    "RU": ("Russia", ["russia", "russian federation", "moscow", "kremlin"]),
    "KR": ("South Korea", ["south korea", "republic of korea", "seoul"]),
    "KP": ("North Korea", ["north korea", "pyongyang"]),
    "IR": ("Iran", ["iran", "tehran"]), "SY": ("Syria", ["syria", "damascus"]), "VN": ("Vietnam", ["vietnam", "viet nam"]),
    "TW": ("Taiwan", ["taiwan", "taipei"]), "CZ": ("Czechia", ["czechia", "czech republic"]),
    "TR": ("Türkiye", ["turkey", "turkiye", "ankara"]), "AE": ("United Arab Emirates", ["uae", "united arab emirates", "dubai", "abu dhabi"]),
    "PS": ("Palestine", ["palestine", "palestinian territories", "gaza", "west bank"]), "IL": ("Israel", ["israel", "tel aviv", "jerusalem"]),
    "CN": ("China", ["china", "beijing", "prc"]), "HK": ("Hong Kong", ["hong kong"]), "MO": ("Macao", ["macao", "macau"]),
    "MM": ("Myanmar", ["myanmar", "burma"]), "LA": ("Laos", ["laos"]), "VE": ("Venezuela", ["venezuela"]), "BO": ("Bolivia", ["bolivia"]),
    "TZ": ("Tanzania", ["tanzania"]), "MD": ("Moldova", ["moldova"]), "CD": ("DR Congo", ["dr congo", "democratic republic of the congo", "drc"]),
    "CG": ("Republic of the Congo", ["republic of the congo", "congo brazzaville"]), "CI": ("Ivory Coast", ["ivory coast", "cote d ivoire"]),
    "SZ": ("Eswatini", ["eswatini", "swaziland"]), "MK": ("North Macedonia", ["north macedonia"]), "CV": ("Cape Verde", ["cape verde", "cabo verde"]),
    "TL": ("Timor-Leste", ["timor leste", "east timor"]), "BN": ("Brunei", ["brunei"]), "VA": ("Vatican City", ["vatican", "holy see"]),
    "FM": ("Micronesia", ["micronesia"]), "NL": ("Netherlands", ["netherlands", "the netherlands", "holland"]),
    "BQ": ("Caribbean Netherlands", ["bonaire", "sint eustatius", "saba"]), "SH": ("Saint Helena", ["saint helena", "ascension island", "tristan da cunha"]), "GE": ("Georgia", []), "JO": ("Jordan", []), "TD": ("Chad", []),
    "IN": ("India", ["india", "new delhi"]), "PK": ("Pakistan", ["pakistan", "islamabad"]), "BD": ("Bangladesh", ["bangladesh", "dhaka"]),
    "LK": ("Sri Lanka", ["sri lanka", "colombo"]), "NP": ("Nepal", ["nepal", "kathmandu"]), "AU": ("Australia", ["australia", "canberra"]),
    "JP": ("Japan", ["japan", "tokyo"]), "FR": ("France", ["france", "paris"]), "DE": ("Germany", ["germany", "berlin"]),
    "UA": ("Ukraine", ["ukraine", "kyiv", "kiev"]), "EG": ("Egypt", ["egypt", "cairo"]), "SA": ("Saudi Arabia", ["saudi arabia", "riyadh"]),
    "PG": ("Papua New Guinea", ["papua new guinea"]), "GQ": ("Equatorial Guinea", ["equatorial guinea"]), "GW": ("Guinea-Bissau", ["guinea bissau"]),
}
INDIA_STATES = [
    ("AN", "Andaman and Nicobar Islands"), ("AP", "Andhra Pradesh"), ("AR", "Arunachal Pradesh"), ("AS", "Assam"),
    ("BR", "Bihar"), ("CH", "Chandigarh"), ("CG", "Chhattisgarh"), ("DH", "Dadra and Nagar Haveli and Daman and Diu"),
    ("DL", "Delhi"), ("GA", "Goa"), ("GJ", "Gujarat"), ("HR", "Haryana"), ("HP", "Himachal Pradesh"),
    ("JK", "Jammu and Kashmir"), ("JH", "Jharkhand"), ("KA", "Karnataka"), ("KL", "Kerala"), ("LA", "Ladakh"),
    ("LD", "Lakshadweep"), ("MP", "Madhya Pradesh"), ("MH", "Maharashtra"), ("MN", "Manipur"), ("ML", "Meghalaya"),
    ("MZ", "Mizoram"), ("NL", "Nagaland"), ("OD", "Odisha"), ("PY", "Puducherry"), ("PB", "Punjab"),
    ("RJ", "Rajasthan"), ("SK", "Sikkim"), ("TN", "Tamil Nadu"), ("TS", "Telangana"), ("TR", "Tripura"),
    ("UP", "Uttar Pradesh"), ("UK", "Uttarakhand"), ("WB", "West Bengal"),
]
INDIA_STATE_ALIASES = {"DL": ["delhi", "new delhi"], "OD": ["odisha", "orissa"], "TN": ["tamil nadu", "tn"], "JK": ["jammu and kashmir", "kashmir"],
                       "UK": ["uttarakhand"], "PY": ["puducherry", "pondicherry"]}
INDIA_CITIES = [   # (state, slug, name, aliases)
    ("TN", "chennai", "Chennai", ["chennai", "madras"]), ("TN", "coimbatore", "Coimbatore", ["coimbatore", "kovai"]),
    ("TN", "madurai", "Madurai", []), ("TN", "tiruchirappalli", "Tiruchirappalli", ["tiruchirappalli", "trichy", "tiruchi"]),
    ("TN", "salem", "Salem", []), ("TN", "tiruppur", "Tiruppur", ["tiruppur", "tirupur"]), ("TN", "vellore", "Vellore", []),
    ("TN", "tirunelveli", "Tirunelveli", ["tirunelveli", "nellai"]), ("TN", "erode", "Erode", []), ("TN", "thoothukudi", "Thoothukudi", ["thoothukudi", "tuticorin"]),
    ("TN", "kanchipuram", "Kanchipuram", ["kanchipuram", "kancheepuram"]), ("TN", "hosur", "Hosur", []),
    ("MH", "mumbai", "Mumbai", ["mumbai", "bombay"]), ("MH", "pune", "Pune", []), ("MH", "nagpur", "Nagpur", []),
    ("KA", "bengaluru", "Bengaluru", ["bengaluru", "bangalore"]), ("KA", "mysuru", "Mysuru", ["mysuru", "mysore"]),
    ("TS", "hyderabad", "Hyderabad", []), ("WB", "kolkata", "Kolkata", ["kolkata", "calcutta"]), ("GJ", "ahmedabad", "Ahmedabad", []),
    ("GJ", "surat", "Surat", []), ("RJ", "jaipur", "Jaipur", []), ("UP", "lucknow", "Lucknow", []), ("BR", "patna", "Patna", []),
    ("MP", "bhopal", "Bhopal", []), ("MP", "indore", "Indore", []), ("KL", "kochi", "Kochi", ["kochi", "cochin"]),
    ("KL", "thiruvananthapuram", "Thiruvananthapuram", ["thiruvananthapuram", "trivandrum"]), ("AP", "visakhapatnam", "Visakhapatnam", ["visakhapatnam", "vizag"]),
    ("AS", "guwahati", "Guwahati", []), ("OD", "bhubaneswar", "Bhubaneswar", []),
]


# English names that differ from GeoNames' main name (the ones news uses).
# Institutions, markets and assemblies that name a country without naming it ("RBI raises rates", "Wall Street slides"). Words that are
# also everyday words, or shared by several countries (rupee, Treasury, the Fed), are left out.
COUNTRY_CUES = {
    "IN": ["rbi", "reserve bank of india", "sensex", "nifty 50", "nifty bank", "sebi", "lok sabha", "rajya sabha", "g sec", "g secs"],
    "US": ["wall street", "federal reserve", "white house", "pentagon", "nasdaq", "dow jones", "capitol hill", "us treasury", "us treasuries"],
    "GB": ["downing street", "bank of england", "ftse"],
    "JP": ["nikkei", "bank of japan"], "DE": ["bundestag", "bundesbank"], "CN": ["people's bank of china", "pboc", "hang seng"],
    "AU": ["asx", "reserve bank of australia"], "CA": ["bank of canada", "tsx"], "RU": ["duma"],
}
EXONYMS = {"IN-MH-mumbai": ["bombay"], "IN-WB-kolkata": ["calcutta"], "IN-KA-bengaluru": ["bangalore"], "IN-TN-chennai": ["madras"],
           "CN-22-beijing": ["peking"], "MM-06-yangon": ["rangoon"], "VN-SG-ho-chi-minh-city": ["saigon"], "UA-12-kyiv": ["kiev"]}
# City names that are everyday words or common names: tagged only when the state or country is named as well.
COMMON = set("""mobile reading bath nice split mission price normal hope liberty independence victoria florence orange marion
jackson clinton franklin madison lincoln washington hamilton austin aurora eugene henderson lawrence warren taylor augusta columbia
charlotte elizabeth jasper troy alexandria athens rome paris london berlin dublin moscow cairo delhi manchester birmingham
newcastle cambridge oxford bristol plymouth portsmouth richmond salem springfield greenville fairfield franklin georgetown
kingston victoria wellington perth darwin hobart brisbane sydney melbourne vancouver halifax regina windsor waterloo
union unity concord harmony faith grace joy may june summit ocean valley fort lake bay beach park mount port mountain river
sale gap hull deal march wells street mold bury batley barry morley ayr sandy marina laval nancy angers metz lens tours
zug bar pau albany lancaster durham lebanon jordan chad georgia guinea niger nice bend palm hollywood eden temple oakland aden
santa san gaza homs hama patna bari pune surat salem""".split()) - {"paris", "london", "berlin", "dublin", "moscow", "cairo", "delhi",
                                                                   "sydney", "melbourne", "brisbane", "perth", "vancouver", "rome", "athens",
                                                                   "manchester", "birmingham", "patna", "pune", "surat", "salem", "gaza"}
COMMON |= {w.strip() for w in (DATA / "place_common_words.txt").read_text(encoding="utf-8").splitlines() if w.strip() and not w.startswith("#")} \
    if (DATA / "place_common_words.txt").exists() else set()
COMMON |= {"north", "south", "east", "west", "central", "centre", "center", "northern", "southern", "eastern", "western", "northeast",
           "northwest", "southeast", "southwest", "midwest", "capital", "coast", "littoral", "highlands", "lakes", "islands", "plateau", "valley"}
PREFIX = {"lake", "port", "fort", "mount", "mt", "new", "old", "east", "west", "north", "south", "upper", "lower", "little", "big",
          "great", "san", "santa", "saint", "st", "greater", "downtown"}       # "Lake Geneva" is not Geneva
# Common given names: "Lewis Hamilton", "Michael Douglas", "Becky Burke" are people, not towns.
# surnames that are also the names of small towns: after a capitalised word they are a person ("Prashakha Mathur")
SURNAMES = set("""mathur kapoor saxena malhotra chopra mittal bansal goel sinha bhatia khanna batra arora""".split())
FIRST_NAMES = set("""james john robert michael william david richard joseph thomas charles christopher daniel matthew anthony mark donald steven paul
andrew joshua kenneth kevin brian george timothy ronald edward jason jeffrey ryan jacob gary nicholas eric jonathan stephen larry justin scott
brandon benjamin samuel gregory alexander frank patrick raymond jack dennis jerry tyler aaron jose adam nathan henry douglas zachary peter kyle
ethan walter noah jeremy christian keith roger terry gerald harold sean austin carl arthur lawrence dylan jesse jordan bryan billy joe bruce
gabriel logan albert willie alan juan wayne elijah randy roy vincent ralph eugene russell bobby mason philip louis lewis max lando oscar carlos
fernando sergio lance charles kimi george liam oliver harry jamie mary patricia jennifer linda elizabeth barbara susan jessica sarah karen lisa
nancy betty margaret sandra ashley kimberly emily donna michelle carol amanda dorothy melissa deborah stephanie rebecca sharon laura cynthia
kathleen amy angela shirley anna brenda pamela emma nicole helen samantha katherine christine debra rachel carolyn janet catherine maria heather
diane ruth julie olivia joyce virginia victoria kelly lauren christina joan evelyn judith megan andrea cheryl hannah jacqueline martha gloria
teresa ann sara madison frances kathryn janice jean abigail alice judy sophia grace denise amber doris marilyn danielle beverly isabella theresa
diana natalie brittany charlotte marie kayla alexis lori becky rick ricky tom tony bill bob ben jim tim mike chris matt nick dan steve dave
rahul amit raj ravi sunil anil vijay ajay sanjay rohit virat sachin narendra arvind mamata nitish akhilesh priyanka sonia smriti shah modi
kamala donald joe hillary bernie elon jeff mark tim sundar satya angela emmanuel olaf justin boris rishi keir""".split())
BLOCK = {"xinhua", "reuters", "bloomberg", "afp", "tass", "anadolu", "bernama", "yonhap", "kyodo", "antara", "pti", "ians", "ani"}   # agencies, not places
CUES = {"in", "at", "near", "from", "of", "outside", "across", "around", "to", "into"}
NOT_A_TOWN = {"county", "district", "parish", "township", "borough", "province", "university", "college", "school", "high", "street", "road",
              "avenue", "united", "fc", "athletic", "rovers", "wanderers", "albion", "hotspur", "airport"}   # "Manchester United" is a club


def _slug(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "x"


_SUFFIX = re.compile(r"\s+(?:[-|\u2013\u2014])\s+((?:[A-Z][\w.&'\u2019]*|of|the|and|de|la|&)(?:\s+(?:[A-Z][\w.&'\u2019]*|of|the|and|de|la|&)){0,5})\s*$")


def strip_publisher(title: str) -> str:
    """A headline without the publisher's name its feed appends ("... - Telegraph India", "... | The Hindu")."""
    m = _SUFFIX.search(title or "")
    return title[:m.start()] if m and len(title[:m.start()]) >= 20 else (title or "")


# Focus weights (detect_focus), tuned on 117 hand-labelled multi-country reports (playbook 20.17)
FW = {"t1": 3, "t2": 1, "dl": 1.5, "l1": 1, "l2": 0.5, "pub": 0.5, "ctx": 0.5, "alpha": 0.25, "beta": 0.5}
FOCUS_ALPHA = None
_CLAUSE = re.compile(r"\s*(?:;|\|| [\u2013\u2014-] |: (?=[A-Z0-9]))\s*")
_HAS_PLACEISH = re.compile(r"[A-Z][a-z]")
_DATELINE = re.compile(r"^\s*([A-Z][A-Za-z .'\-]{1,40}?)\s*(?:\[[^\]]{1,30}\])?\s*(?:,\s*[A-Z][a-z]{2,8}\.?\s*\d{1,2})?\s*(?:\([A-Za-z .]{2,30}\))?\s*(?::|[\u2013\u2014-]{1,2})\s")
_CONTEXT = re.compile(r"\b(?:vs|versus|compared|than|against|cues?|tracking|tracks?|mirror(?:s|ing)?|following|amid|despite|ahead|peers?|"
                      r"overseas|abroad|unlike|like|while|counterparts?|rivals?|global|benchmark)\b", re.I)


def _context_weights(text: str, base: float):
    """Token weights for one clause or sentence: ``base``, halved for tokens after a word of comparison or context."""
    toks = _tokens(text)
    cut = len(toks)
    for i, (_t, raw) in enumerate(toks):
        if _CONTEXT.fullmatch(raw):
            cut = i
            break
    return lambda i: base if i < cut else base * FW["ctx"]


def _tokens(text: str) -> list[tuple[str, str]]:
    """(normalised token, original token) pairs, aligned the way norm_tokens splits."""
    import unicodedata
    a = unicodedata.normalize("NFKD", (text or "").replace("\u2019", "'")).encode("ascii", "ignore").decode()
    out = []
    for raw in re.sub(r"[^A-Za-z0-9&+' ]+", " ", a).split():
        t = _stem(raw.lower())
        if t:
            out.append((t, raw))
    return out


class Gazetteer:
    def __init__(self):
        self.places: dict[str, Place] = {WORLD: Place(WORLD, "Global", "world", None)}
        self._index: dict[str, str] = {}
        data = json.loads((DATA / "countries.json").read_text(encoding="utf-8"))
        for name, code in CONTINENTS.items():
            self.places[code] = Place(code, name, "region", WORLD, [norm(a) for a in CONTINENT_ALIASES.get(code, [])])
        by_sub: dict[str, list[str]] = {}
        for c in data:
            a2 = c["alpha-2"]
            if a2 == "AQ":
                continue
            display, aliases = COUNTRY_OVERRIDES.get(a2, (None, None))
            raw = c["name"]
            if display is None:
                display = raw.split("(")[0].strip()
                aliases = [display] if "," not in raw else []
                if "," in display:                                 # ISO's "Name, Kind of the" form reads badly in menus
                    display = display.split(",")[0].strip()
            region, sub, inter = c.get("region") or "", c.get("sub-region") or "", c.get("intermediate-region") or ""
            if a2 == "TW":                                     # not listed by the UN; geographically East Asia
                region, sub = "Asia", "Eastern Asia"
            finest = inter or sub
            parent = WORLD
            if region in CONTINENTS and finest:
                name = SUBREGION_NAMES.get(finest, finest)
                parent = _slug(name)
                if parent not in self.places:
                    self.places[parent] = Place(parent, name, "subregion", CONTINENTS[region], [norm(a) for a in SUBREGION_ALIASES.get(parent, [name])])
            for k in (region, sub):
                by_sub.setdefault(k, []).append(a2)
            self.places[a2] = Place(a2, display, "country", parent, [norm(a) for a in aliases])
        for code, (name, aliases, members) in GROUPS.items():
            mem: list[str] = []
            for m in members:
                if m == "@africa":
                    mem += [x for x in by_sub.get("Africa", []) if x not in NOT_SOVEREIGN_AFRICA] + ["EH"]
                elif m.startswith("@"):
                    mem += by_sub.get(m[1:], [])
                else:
                    mem.append(m)
            mem = sorted({m for m in mem if m in self.places})
            self.places[code] = Place(code, name, "group", WORLD, [norm(a) for a in aliases], members=mem)
            for m in mem:
                self.places[m].regions.append(code)
        for st, name in INDIA_STATES:
            code = f"IN-{st}"
            self.places[code] = Place(code, name, "state", "IN", [norm(a) for a in INDIA_STATE_ALIASES.get(st, [name])])
        for st, slug, name, aliases in INDIA_CITIES:
            code = f"IN-{st}-{slug}"
            self.places[code] = Place(code, name, "city", f"IN-{st}", [norm(a) for a in (aliases or [name])], featured=True)
        self._load_geonames()
        # country and region names are unique; states and cities can share a name, so their index keeps every candidate
        self._multi: dict[str, list[str]] = {}
        for p in self.places.values():
            for a in p.aliases:
                if not a:
                    continue
                if p.level in ("state", "city"):
                    self._multi.setdefault(a, []).append(p.code)
                else:
                    self._index.setdefault(a, p.code)
        for a, codes in self._multi.items():
            codes.sort(key=lambda c: -self.places[c].population)

    def _load_geonames(self) -> None:
        f_states, f_cities = DATA / "geonames_admin1.tsv", DATA / "geonames_cities.tsv"
        if not f_states.exists():
            return
        in_names = {norm(name): st for st, name in INDIA_STATES}
        in_names.update({norm("Andaman and Nicobar"): "AN", norm("Orissa"): "OD"})
        a1map: dict[tuple[str, str], str] = {}
        for line in f_states.read_text(encoding="utf-8").splitlines():
            cc, a1, name = line.split("\t")
            if cc not in self.places:
                continue
            if cc == "IN":
                st = in_names.get(norm(name))
                if st:
                    a1map[(cc, a1)] = f"IN-{st}"
                continue
            code = f"{cc}-{a1}"
            a1map[(cc, a1)] = code
            self.places[code] = Place(code, name, "state", cc, [norm(name)])
        curated = {(p.parent, _slug(p.name)): p for p in self.places.values() if p.level == "city"}
        for line in f_cities.read_text(encoding="utf-8").splitlines():
            gid, name, ascii_name, cc, a1, pop, _extra = line.split("\t")
            if cc not in self.places:
                continue
            parent = a1map.get((cc, a1), cc)
            slug = _slug(name)
            hit = curated.get((parent, slug))
            if hit:
                hit.population = int(pop)
                continue
            code = f"{parent}-{slug}"
            if code in self.places:
                code = f"{code}-{gid}"
            aliases = {norm(name), norm(ascii_name)}
            self.places[code] = Place(code, name, "city", parent, [a for a in aliases if a], population=int(pop))
        for code, extra in EXONYMS.items():
            if code in self.places:
                self.places[code].aliases += [norm(x) for x in extra]
        for code, extra in COUNTRY_CUES.items():
            if code in self.places:
                self.places[code].aliases += [norm(x) for x in extra]

    def get(self, code: str) -> Place | None:
        return self.places.get(code)

    def ancestors(self, code: str) -> list[str]:
        """Parents in the tree up to (not including) world. Groups are not ancestors: a group's scope takes in its members instead."""
        out: list[str] = []
        p = self.places.get(code)
        while p and p.parent and p.parent != WORLD:
            out.append(p.parent)
            p = self.places.get(p.parent)
        return out

    def scope_codes(self, code: str) -> list[str]:
        """The place codes a scope matches: itself, and for a group also each member country."""
        p = self.places.get(code)
        return [code] + list(p.members) if p and p.level == "group" else [code]

    def groups(self) -> list[Place]:
        return [p for p in self.places.values() if p.level == "group"]

    def with_ancestors(self, codes) -> set[str]:
        s: set[str] = set()
        for c in codes:
            if c in self.places and c != WORLD:
                s.add(c)
                s.update(self.ancestors(c))
        return s

    def children(self, parent: str) -> list[Place]:
        if parent == WORLD:
            return sorted((p for p in self.places.values() if p.level == "region"), key=lambda p: p.name)
        if self.places.get(parent) and self.places[parent].level == "group":
            return sorted((self.places[m] for m in self.places[parent].members), key=lambda p: p.name)
        kids = self._kids.get(parent) if hasattr(self, "_kids") else None
        if kids is None:
            if not hasattr(self, "_kids"):
                self._kids: dict[str, list[Place]] = {}
                for p in self.places.values():
                    if p.parent:
                        self._kids.setdefault(p.parent, []).append(p)
            kids = list(self._kids.get(parent, []))
        states = [p for p in kids if p.level != "city"]
        cities = sorted((p for p in kids if p.level == "city"), key=lambda p: -p.population)[:80]   # a menu, not an atlas: the jump box finds the rest
        if self.places.get(parent) and self.places[parent].level == "country":
            cities = [p for p in cities if p.featured or not states]
            cities += [p for p in self.places.values() if p.featured and p.code.startswith(parent + "-") and p.parent != parent and p not in cities]
        return sorted({p.code: p for p in states + cities}.values(), key=lambda p: (p.level == "city", -p.population if p.level == "city" else 0, p.name))

    def breadcrumb(self, code: str) -> list[Place]:
        out, p = [], self.places.get(code)
        while p:
            out.append(p)
            p = self.places.get(p.parent) if p.parent else None
        return list(reversed(out))

    def detect(self, title: str, lead: str = "", source_country: str = "") -> dict[str, float]:
        """Places mentioned in a headline (weight 3) and lead (weight 1). Longest name wins at a position. Countries and regions are
        taken as they come; a state or city is taken only when it is clear which one is meant (see the module note)."""
        found: dict[str, float] = {}
        pending: list[tuple[list[str], float, bool, str, bool]] = []    # (candidates, weight, has cue, name or #person, followed by "state")
        for text, w in ((title, 3.0), (lead, 1.0)):
            if _US.search(text or ""):
                found["US"] = max(found.get("US", 0.0), w)
            pairs = _tokens(text)
            toks = [t for t, _ in pairs]
            used = [False] * len(toks)
            hits = []
            for i, n, g in grams(toks, 4):
                if g in self._index or g in self._multi:
                    hits.append((n, i, g))
            for n, i, g in sorted(hits, key=lambda h: (-h[0], h[1])):
                if any(used[i:i + n]):
                    continue
                for k in range(i, i + n):
                    used[k] = True
                code = self._index.get(g)
                if code:
                    found[code] = max(found.get(code, 0.0), w)
                    continue
                if g in BLOCK or (n == 1 and len(g) <= 3 and not any(c.startswith("IN-") for c in self._multi[g])):
                    continue
                prev_tok, prev_raw = (pairs[i - 1] if i > 0 else ("", ""))
                next_tok, next_raw = (pairs[i + n] if i + n < len(pairs) else ("", ""))
                if next_tok in NOT_A_TOWN or (prev_tok in PREFIX and not (i >= 2 and pairs[i - 2][0] in CUES and False)):
                    continue                                                   # "Houston County", "Ohio University": a county or a school
                state_word = next_tok in ("state", "province", "region")
                last_raw = pairs[i + n - 1][1]
                pos = (text or "").find(last_raw)
                after = (text or "")[pos + len(last_raw):pos + len(last_raw) + 2].strip() if pos >= 0 else ""
                at_start = i == 0 and (after[:1] in (":", ",", "-", "\u2013") or (next_raw[:1].islower()))
                cue = prev_tok in CUES or at_start
                person = i > 0 and prev_raw[:1].isupper() and (prev_tok in FIRST_NAMES or (n == 1 and g in SURNAMES))
                if pairs[i][1][:1].islower() and any(r[:1].isupper() for _t, r in pairs):
                    continue                                                   # a place name is written with a capital: "holiday season" is not Holiday, Florida
                pending.append((self._multi[g], w, cue and not person, "#person" if person else g, state_word))
        context_countries = {c for c in found if self.places[c].level == "country"}
        # states first: a named state gives its cities context. A name shared by a big city and a state ("Lagos", "Manchester")
        # is read as the city unless "state" follows it or the state's country is named.
        states_found: set[str] = set()
        for cands, w, cue, name, state_word in pending:
            sts = [c for c in cands if self.places[c].level == "state"]
            big_city = any(self.places[c].level == "city" and self.places[c].population >= 100_000 for c in cands)
            if not sts or (big_city and not state_word and not any(self._country_of(c) in context_countries for c in sts)):
                continue
            pick = self._choose(sts, context_countries, set(), source_country, cue or state_word, name)
            if pick:
                found[pick] = max(found.get(pick, 0.0), w)
                states_found.add(pick)
                context_countries.add(self.places[pick].parent)
        named_states = states_found | {c for c in found if self.places[c].level == "state"}
        for cands, w, cue, name, _sw in pending:
            cities = [c for c in cands if self.places[c].level == "city"]
            if not cities or any(c in found for c in cands if self.places[c].level == "state"):
                continue
            if named_states and not cue:
                # the text names a state: a small town of the same name in another state is not meant ("Rajasthan ... Mathur")
                cities = [c for c in cities if self.places[c].parent in named_states or self.places[c].population >= 1_000_000
                          or self._country_of(c) not in {self._country_of(st) for st in named_states}]
                if not cities:
                    continue
            pick = self._choose(cities, context_countries, states_found, source_country, cue, name)
            if pick:
                found[pick] = max(found.get(pick, 0.0), w)
            elif name != "#person" and source_country and (name not in COMMON or cue) and not context_countries - {source_country}:
                if any(self._country_of(c) == source_country for c in cities):
                    found[source_country] = max(found.get(source_country, 0.0), min(w, 1.0))     # we know the country, not which city
        return found

    # ---- geographic focus ---------------------------------------------------------------------------
    def detect_focus(self, title: str, lead: str = "", source_country: str = "") -> dict[str, float]:
        """The places a report is about, not every place it mentions (geographic focus; NLGF, Ariyarathne et al. 2026, and
        Web-a-Where, Amitay et al. 2004). Each country (and its states and cities together) gets a focus score from where it is
        named: the headline's first clause that names a place counts 3, a later clause 1.5; a dateline ("MUMBAI:", "New Delhi
        [India], Nov 1 (ANI):") 2; the lead's first sentence 1, later ones 0.5. A mention after a word of comparison or context
        ("vs", "than", "cues from", "amid", "ahead of", "despite", ...) counts half. Places whose country has at least ``alpha`` of
        the total are kept; the rest were mentioned in passing ("India's bond yields ... the US 10-year Treasury yield is 4.93%")."""
        found = self.detect(strip_publisher(title), lead, source_country)
        if not found:
            return found
        key = {c: self._focus_key(c) for c in found}
        if len(set(key.values())) <= 1:
            return found
        score: dict[str, float] = {}
        for text, weights in self._focus_segments(title, lead):
            toks = _tokens(text)
            if not toks:
                continue
            norm_toks = [t for t, _r in toks]
            for c in found:
                pos = self._positions(c, norm_toks)
                if not pos:
                    continue
                w = max(weights(i) for i in pos)
                score[key[c]] = score.get(key[c], 0.0) + w
        for c in found:                                          # inferred from the publisher (no name in the text): a lead-level mention
            score.setdefault(key[c], FW["l2"])
        if source_country and source_country in score:
            score[source_country] += FW["pub"]                   # the publisher's own country (NLGF: the publisher's location)
        total = sum(score.values()) or 1.0
        top = max(score.values())
        keep = {k for k, v in score.items() if v / total > FW["alpha"] or v > FW["beta"] * top or v == top}
        return {c: w for c, w in found.items() if key[c] in keep}

    def _focus_key(self, code: str) -> str:
        p = self.places.get(code)
        if not p or p.level in ("world", "region", "subregion", "group"):
            return code
        return self._country_of(code)

    def _positions(self, code: str, toks: list[str]) -> list[int]:
        p = self.places.get(code)
        if not p:
            return []
        names = {tuple(a.split()) for a in p.aliases + [norm(p.name)] if a}
        if code == "US":
            names |= {("us",), ("u", "s"), ("usa",)}
        out = []
        for i in range(len(toks)):
            for nm in names:
                if tuple(toks[i:i + len(nm)]) == nm:
                    out.append(i)
                    break
        return out

    @staticmethod
    def _focus_segments(title: str, lead: str):
        """(text, weight of token i) for the headline and the lead."""
        out = []
        clauses = [c for c in _CLAUSE.split(strip_publisher(title or "")) if c and c.strip()]
        first = True
        for cl in clauses:
            base = FW["t1"] if first else FW["t2"]
            out.append((cl, _context_weights(cl, base)))
            if any(ch.isupper() for ch in cl):
                first = first and not _HAS_PLACEISH.search(cl)
        lead = lead or ""
        m = _DATELINE.match(lead)
        if m:
            out.append((m.group(1), lambda i: FW["dl"]))
            lead = lead[m.end():]
        for k, sent in enumerate(re.split(r"(?<=[.!?])\s+", lead)[:4]):
            out.append((sent, _context_weights(sent, FW["l1"] if k == 0 else FW["l2"])))
        return out

    def _country_of(self, code: str) -> str:
        p = self.places[code]
        while p.level not in ("country", "world") and p.parent:
            p = self.places[p.parent]
        return p.code

    def _choose(self, cands: list[str], countries: set[str], states: set[str], source_country: str, cue: bool, name: str) -> str | None:
        """One place among same-named candidates, or None when it is not clear which is meant."""
        if name == "#person":
            return None                                              # "Becky Burke", "Lewis Hamilton"
        if states:
            inside = [c for c in cands if self.places[c].parent in states]
            if len(inside) >= 1:
                return inside[0]
        if countries and (name not in COMMON or cue):
            inside = [c for c in cands if self._country_of(c) in countries]
            if len(inside) == 1 or (inside and self.places[inside[0]].population >= 5 * max(1, self.places[inside[1]].population)):
                return inside[0]
            if inside:
                return None
        if name == "#person":
            return None
        top = self.places[cands[0]]
        common = name in COMMON and top.population < 1_000_000
        second = self.places[cands[1]].population if len(cands) > 1 else 0
        if not common and top.population >= 1_000_000 and top.population >= 8 * max(1, second):
            return cands[0]                                          # Paris, Lagos, Chennai: by far the biggest of that name
        if top.level == "state" and len(cands) == 1 and not common:
            return cands[0]                                          # state names are rarely shared
        if source_country and (not common or cue):
            in_src = [c for c in cands if self._country_of(c) == source_country]
            if len(in_src) == 1 and (cue or self.places[in_src[0]].population >= 100_000 or self.places[in_src[0]].level == "state"):
                return in_src[0]
        return None


def norm(s: str) -> str:
    return " ".join(norm_tokens(s))
