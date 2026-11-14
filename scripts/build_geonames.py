"""Build backend/app/data/geonames_*.tsv from GeoNames (CC BY 4.0, https://www.geonames.org/).

    curl -O https://download.geonames.org/export/dump/cities15000.zip && unzip cities15000.zip
    curl -O https://download.geonames.org/export/dump/admin1CodesASCII.txt
    curl -O https://download.geonames.org/export/dump/countryInfo.txt
    python scripts/build_geonames.py /path/to/those/files

Outputs (small enough to keep in the repository):
    geonames_admin1.tsv     country, admin1 code, name                      every first-level division in the world
    geonames_cities.tsv     id, name, ascii name, country, admin1, population, extra English names (major cities only)
    geonames_countries.tsv  iso2, fips, population, languages               FIPS codes are what GDELT uses
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/geo")
OUT = Path(__file__).resolve().parents[1] / "backend" / "app" / "data"
ASCII = re.compile(r"^[A-Za-z][A-Za-z .'-]{3,24}$")

admin1 = []
for line in (SRC / "admin1CodesASCII.txt").read_text(encoding="utf-8").splitlines():
    code, name, ascii_name, _gid = line.split("\t")
    cc, a1 = code.split(".", 1)
    admin1.append((cc, a1, name))
(OUT / "geonames_admin1.tsv").write_text("".join(f"{cc}\t{a1}\t{n}\n" for cc, a1, n in admin1), encoding="utf-8")

rows = []
for line in (SRC / "cities15000.txt").read_text(encoding="utf-8").splitlines():
    f = line.split("\t")
    gid, name, ascii_name, alts, cc, a1, pop, fcode = f[0], f[1], f[2], f[3], f[8], f[10], int(f[14] or 0), f[7]
    if fcode in ("PPLX", "PPLH", "PPLQ", "PPLW"):        # sections of a city, historical, abandoned, destroyed
        continue
    extra = []
    if False:                                          # GeoNames alternates are mostly transliterations; exonyms are curated in places.py
        for a in alts.split(","):
            a = a.strip()
            if ASCII.match(a) and a.lower() not in (name.lower(), ascii_name.lower()) and not a.isupper() and len(extra) < 4:
                extra.append(a)
    rows.append((gid, name, ascii_name, cc, a1, pop, "|".join(extra)))
rows.sort(key=lambda r: -r[5])
(OUT / "geonames_cities.tsv").write_text("".join("\t".join(map(str, r)) + "\n" for r in rows), encoding="utf-8")

ci = []
for line in (SRC / "countryInfo.txt").read_text(encoding="utf-8").splitlines():
    if line.startswith("#") or not line.strip():
        continue
    f = line.split("\t")
    ci.append((f[0], f[3], f[7] or "0", f[15]))
(OUT / "geonames_countries.tsv").write_text("".join("\t".join(r) + "\n" for r in ci), encoding="utf-8")
print(len(admin1), "divisions,", len(rows), "cities,", len(ci), "countries")
