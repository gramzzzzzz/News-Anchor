"""GDELT 2.0 event files: every 15 minutes, the articles GDELT's crawlers found in world news, in English and (translated) 65 other
languages. Small static files (about 50 KB each), so they can be read freely. We take only who published, in what language, and where
the events they report took place; never the articles themselves. https://www.gdeltproject.org (open data)."""
from __future__ import annotations

import csv
import datetime as dt
import io
import zipfile

import httpx

BASE = "http://data.gdeltproject.org/gdeltv2/"
# GDELT's translation stream names languages by ISO 639-3 / 639-2 codes
ISO3 = {"spa": "es", "fra": "fr", "deu": "de", "ger": "de", "ita": "it", "por": "pt", "rus": "ru", "ara": "ar", "zho": "zh", "chi": "zh", "jpn": "ja",
        "kor": "ko", "tur": "tr", "pol": "pl", "nld": "nl", "dut": "nl", "ell": "el", "gre": "el", "ron": "ro", "rum": "ro", "slk": "sk", "slo": "sk",
        "srp": "sr", "hrv": "hr", "bos": "bs", "slv": "sl", "ces": "cs", "cze": "cs", "hun": "hu", "bul": "bg", "ukr": "uk", "bel": "be", "lit": "lt",
        "lav": "lv", "est": "et", "fin": "fi", "swe": "sv", "nor": "no", "nob": "nb", "dan": "da", "isl": "is", "heb": "he", "fas": "fa", "per": "fa",
        "urd": "ur", "hin": "hi", "ben": "bn", "tam": "ta", "tel": "te", "mal": "ml", "kan": "kn", "mar": "mr", "guj": "gu", "pan": "pa", "ori": "or",
        "sin": "si", "nep": "ne", "tha": "th", "vie": "vi", "ind": "id", "msa": "ms", "may": "ms", "tgl": "tl", "fil": "tl", "swa": "sw", "amh": "am",
        "som": "so", "hau": "ha", "yor": "yo", "ibo": "ig", "kat": "ka", "geo": "ka", "hye": "hy", "arm": "hy", "aze": "az", "kaz": "kk", "uzb": "uz",
        "kir": "ky", "tgk": "tg", "mon": "mn", "khm": "km", "lao": "lo", "mya": "my", "bur": "my", "sqi": "sq", "alb": "sq", "mkd": "mk", "mac": "mk",
        "cat": "ca", "eus": "eu", "baq": "eu", "glg": "gl", "afr": "af", "pus": "ps", "kur": "ku", "tat": "tt", "eng": "en"}


def stamps(now: dt.datetime, files: int) -> list[str]:
    """Timestamps of the last ``files`` 15-minute files, newest first (GDELT publishes a few minutes after each quarter hour)."""
    t = now.astimezone(dt.timezone.utc) - dt.timedelta(minutes=20)
    t = t.replace(minute=(t.minute // 15) * 15, second=0, microsecond=0)
    return [(t - dt.timedelta(minutes=15 * i)).strftime("%Y%m%d%H%M%S") for i in range(files)]


def _unzip_tsv(body: bytes) -> list[list[str]]:
    z = zipfile.ZipFile(io.BytesIO(body))
    data = z.read(z.namelist()[0]).decode("utf-8", "replace")
    return list(csv.reader(io.StringIO(data), delimiter="\t", quoting=csv.QUOTE_NONE))


def parse(mentions: list[list[str]], events: list[list[str]], fips: dict[str, str]) -> dict[str, dict]:
    """{domain: {articles, language, focus: {country: n}, example}} from one quarter hour's mention and event rows."""
    where = {}
    for r in events:
        if len(r) > 53 and r[53]:
            where[r[0]] = fips.get(r[53], "")
    out: dict[str, dict] = {}
    for r in mentions:
        if len(r) < 6 or r[3] != "1":                                  # mention type 1 = web news
            continue
        dom = r[4].lower().removeprefix("www.").strip()
        if not dom or "." not in dom:
            continue
        lang = "en"
        if len(r) > 14 and r[14].startswith("srclc:"):
            lang = ISO3.get(r[14][6:9], r[14][6:9])
        d = out.setdefault(dom, {"articles": set(), "language": lang, "focus": {}, "example": r[5]})
        d["articles"].add(r[5])
        c = where.get(r[0])
        if c:
            d["focus"][c] = d["focus"].get(c, 0) + 1
    for d in out.values():
        d["articles"] = len(d["articles"])
    return out


async def quarter(client: httpx.AsyncClient, stamp: str, translation: bool) -> tuple[list[list[str]], list[list[str]]]:
    pre = f"{stamp}.translation." if translation else f"{stamp}."
    rm = await client.get(f"{BASE}{pre}mentions.CSV.zip", timeout=60)
    re_ = await client.get(f"{BASE}{pre}export.CSV.zip", timeout=60)
    if rm.status_code != 200 or re_.status_code != 200:
        return [], []
    return _unzip_tsv(rm.content), _unzip_tsv(re_.content)
