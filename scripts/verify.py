#!/usr/bin/env python3
"""Cross-check every formula in compounds.csv against PubChem.

    python scripts/verify.py             # asks PubChem for names not yet in cache/pubchem.json (needs internet)
    python scripts/verify.py --offline   # cache only, no network

Shares cache/pubchem.json with scrape.py, so names scrape.py already looked up are free.
Writes data/review/verify_report.csv and prints everything that needs a human look.

Why this exists: formulas typed from memory can be wrong by a CH2 or a missing ring, and a wrong
formula silently breaks "Contain nitrogen" / "Molar mass over 380" groups. audit.py only checks
valence parity and recalled masses, which can share the same mistake as the formula. PubChem is
the independent source.

Result classes
  match            same parent formula (HCl / HBr / H2O salts and hydrates are stripped)
  DIFF (title ok)  formulas differ AND PubChem's title looks like the same compound -> probably a real error, fix it
  DIFF (title?)    formulas differ but PubChem's title looks like something else. Usually a wrong name match
                   on a short acronym (LSA -> saccharin). Read the title and decide.
  no data          PubChem has nothing under that name. Nothing is checked for these rows.
"""
import argparse
import csv
import json
import re
import sys
import time
from collections import Counter

from generate import load, name_key, parent_formula, parse_formula
from project_paths import CATALOGUE, PUBCHEM_CACHE, REVIEW
from scrape import pubchem

# "α-PVP" and PubChem's "alpha-PVP" must compare equal, so spell Greek letters out before stripping.
norm = lambda s: re.sub(r"[^a-z0-9]", "", name_key(s))
ADDUCTS = [Counter({"H": 1, "Cl": 1}), Counter({"H": 1, "Br": 1}), Counter({"H": 2, "O": 1}),
           Counter({"H": 2, "Cl": 2}), Counter({"H": 3, "Cl": 3})]


def counts(formula):
    return parse_formula(parent_formula(formula))


def same_compound(csv_formula, pc_formula):
    a, b = counts(csv_formula), counts(pc_formula)
    if a == b:
        return True
    return any(b - ad == a and not (a - b) for ad in ADDUCTS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=CATALOGUE)
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()

    comps, _ = load(a.data)
    cache = json.loads(PUBCHEM_CACHE.read_text()) if PUBCHEM_CACHE.exists() else {}
    index = {norm(k): k for k in cache}          # "m-CPP", "mCPP" and "MCPP" are the same entry
    rows, todo = [], 0
    for i, c in enumerate(comps, 1):
        if not c["formula"]:
            continue
        info = cache.get(c["name"]) or cache.get(index.get(norm(c["name"]), ""))
        if info is None and not a.offline:
            try:
                info = pubchem(c["name"], cache)
            except Exception as exc:  # network hiccup: skip this one, keep going
                print(f"  {c['name']}: lookup failed ({exc.__class__.__name__})", file=sys.stderr)
                info = None
            todo += 1
            if todo % 20 == 0:
                PUBCHEM_CACHE.parent.mkdir(exist_ok=True)
                PUBCHEM_CACHE.write_text(json.dumps(cache))
                print(f"  {i}/{len(comps)} looked up", end="\r", file=sys.stderr)
        if not info or not info.get("formula"):
            rows.append((c["name"], c["formula"], "", "", "no data"))
            continue
        if same_compound(c["formula"], info["formula"]):
            status = "match"
        else:
            nn, nt = norm(c["name"]), norm(info.get("title", ""))
            agree = nt and (nn == nt or (min(len(nn), len(nt)) >= 7 and (nn in nt or nt in nn)))
            status = "DIFF (title ok)" if agree else "DIFF (title?)"
        rows.append((c["name"], c["formula"], info["formula"], info.get("title", ""), status))
    if cache and not a.offline:
        PUBCHEM_CACHE.parent.mkdir(exist_ok=True)
        PUBCHEM_CACHE.write_text(json.dumps(cache))

    with open(REVIEW / "verify_report.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="|")
        w.writerow(["name", "csv_formula", "pubchem_formula", "pubchem_title", "status"])
        w.writerows(rows)

    tally = Counter(r[4] for r in rows)
    print(f"\n{len(rows)} formulas checked: " + ", ".join(f"{v} {k}" for k, v in tally.most_common()))
    for status in ("DIFF (title ok)", "DIFF (title?)"):
        sel = [r for r in rows if r[4] == status]
        if sel:
            print(f"\n== {status} ==")
            for n, cf, pf, t, _ in sel:
                print(f"  {n}: csv {cf}  vs  pubchem {pf}  [{t}]")
    print("\nFull list: data/review/verify_report.csv. Rows marked 'no data' were NOT verified.")


if __name__ == "__main__":
    main()
