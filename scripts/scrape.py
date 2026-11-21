#!/usr/bin/env python3
"""Bootstrap more compounds from PsychonautWiki (GraphQL) + PubChem (formulas).

    python scripts/scrape.py            # writes data/review/scraped.csv and caches API responses

scraped.csv has the same columns as compounds.csv. It skips anything already in
compounds.csv, so your hand-tagged rows always win. What you still do by hand:

  * `target` and `origin` are left blank (the wiki doesn't expose them cleanly).
    Blank is fine: the generator just won't build target/origin groups for that row,
    but class / effect / formula / NAME groups still work.
  * CHECK the `pubchem_title` column. Name lookups on short RC names can match the
    wrong compound (acronyms especially). Blank the formula if the title looks off.

When a row looks right, move it into compounds.csv and re-run gen.py.

Data: PsychonautWiki text is CC BY-SA 4.0, keep the footer attribution.
NOTE: written from the public API docs and not yet run against the live endpoints.
Adjust QUERY if the schema differs. Be polite with the rate limits.
"""
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from project_paths import CACHE, CATALOGUE, PUBCHEM_CACHE, REVIEW

PW_API = "https://api.psychonautwiki.org"
PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{}/property/MolecularFormula,Title/JSON"
PAGE = 50
UA = {"User-Agent": "compounds-game-scraper/0.1 (personal project)", "Content-Type": "application/json"}

QUERY = """
query ($limit: Int, $offset: Int) {
  substances(limit: $limit, offset: $offset) {
    name
    class { chemical psychoactive }
  }
}
"""

EFFECT_MAP = {"psychedelic": "psychedelic", "hallucinogen": "psychedelic", "stimulant": "stimulant",
              "depressant": "depressant", "dissociative": "dissociative", "deliriant": "deliriant",
              "entactogen": "entactogen", "opioid": "opioid", "cannabinoid": "cannabinoid"}


def post_json(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_substances():
    CACHE.mkdir(exist_ok=True)
    out, offset = [], 0
    while True:
        f = CACHE / f"pw_{offset}.json"
        if f.exists():
            page = json.loads(f.read_text())
        else:
            page = post_json(PW_API, {"query": QUERY, "variables": {"limit": PAGE, "offset": offset}})
            if "errors" in page:
                sys.exit(f"GraphQL error: {page['errors']}")
            f.write_text(json.dumps(page))
            time.sleep(1.0)
        items = (page.get("data") or {}).get("substances") or []
        out += items
        print(f"  fetched {len(out)} substances", end="\r")
        if len(items) < PAGE:
            break
        offset += PAGE
    print()
    return out


def pubchem(name, cache):
    if name in cache:
        return cache[name]
    url = PUBCHEM.format(urllib.parse.quote(name))
    try:
        props = get_json(url)["PropertyTable"]["Properties"][0]
        res = {"formula": props.get("MolecularFormula", ""), "title": props.get("Title", "")}
    except (urllib.error.HTTPError, KeyError, IndexError):
        res = {"formula": "", "title": ""}
    cache[name] = res
    time.sleep(0.25)  # PubChem asks for <= 5 requests/second
    return res


def main():
    have = set()
    if CATALOGUE.exists():
        with CATALOGUE.open(encoding="utf-8") as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    have.add(line.split("|")[0].strip().lower())

    subs = fetch_substances()
    pc_file = PUBCHEM_CACHE
    pc = json.loads(pc_file.read_text()) if pc_file.exists() else {}

    rows = []
    for i, s in enumerate(subs, 1):
        name = (s.get("name") or "").strip()
        if not name or name.lower() in have:
            continue
        cls = s.get("class") or {}
        chem = (cls.get("chemical") or [""])[0] if cls.get("chemical") else ""
        psy = [p.lower() for p in (cls.get("psychoactive") or [])]
        effect = next((EFFECT_MAP[p] for p in psy if p in EFFECT_MAP), "")
        info = pubchem(name, pc)
        rows.append([name, info["formula"], chem, effect, "", "", info["title"]])
        if i % 20 == 0:
            pc_file.write_text(json.dumps(pc))
            print(f"  {i}/{len(subs)} looked up", end="\r")
    pc_file.write_text(json.dumps(pc))

    with open(REVIEW / "scraped.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="|")
        w.writerow(["name", "formula", "class", "effect", "target", "origin", "pubchem_title"])
        w.writerows(rows)
    with_formula = sum(1 for r in rows if r[1])
    print(f"\nwrote data/review/scraped.csv: {len(rows)} new rows, {with_formula} with a PubChem formula")


if __name__ == "__main__":
    main()
