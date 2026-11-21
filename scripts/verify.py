#!/usr/bin/env python3
"""Verify catalogue formulas and compound identities against PubChem.

    python scripts/verify.py
    python scripts/verify.py --offline
    python scripts/verify.py --data data/compounds.csv --identities data/identities.csv

For compounds with a CID in identities.csv, PubChem is queried by CID rather than
by the potentially ambiguous compound name. The InChIKey checks structural identity;
the synonym list independently checks whether the catalogue name (or a recorded
alias) is associated with that CID.

The existing name-based PubChem cache used by scrape.py is preserved. CID results
are stored under separate ``cid:<CID>`` keys in the same cache.
"""
import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from generate import load, name_key, parent_formula, parse_formula
from project_paths import CATALOGUE, PUBCHEM_CACHE, REVIEW
from scrape import pubchem

PUBCHEM_CID_PROPERTIES = (
    "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{}/property/"
    "MolecularFormula,InChIKey/JSON"
)
PUBCHEM_CID_SYNONYMS = (
    "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{}/synonyms/JSON"
)
UA = {"User-Agent": "compounds-game-verifier/0.2 (personal project)"}

# Normalize Greek letters and punctuation consistently with the catalogue's
# existing name_key() normalization.
def norm(value):
    return re.sub(r"[^a-z0-9]", "", name_key(value or ""))


ADDUCTS = [
    Counter({"H": 1, "Cl": 1}),
    Counter({"H": 1, "Br": 1}),
    Counter({"H": 2, "O": 1}),
    Counter({"H": 2, "Cl": 2}),
    Counter({"H": 3, "Cl": 3}),
]


def counts(formula):
    return parse_formula(parent_formula(formula))


def same_compound(csv_formula, pc_formula):
    """Retain the project's existing salt/hydrate-tolerant formula comparison."""
    if not csv_formula or not pc_formula:
        return False
    a, b = counts(csv_formula), counts(pc_formula)
    if a == b:
        return True
    return any(b - ad == a and not (a - b) for ad in ADDUCTS)


def get_json(url):
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def fetch_cid_info(cid, cache):
    """Fetch formula, InChIKey and synonyms for one exact PubChem CID."""
    cache_key = f"cid:{cid}"
    if cache_key in cache:
        return cache[cache_key]

    info = {"cid": str(cid), "formula": "", "inchi_key": "", "synonyms": [],
            "lookup_error": ""}
    try:
        payload = get_json(PUBCHEM_CID_PROPERTIES.format(urllib.parse.quote(str(cid))))
        properties = payload["PropertyTable"]["Properties"][0]
        info["formula"] = properties.get("MolecularFormula", "")
        info["inchi_key"] = properties.get("InChIKey", "")
    except (urllib.error.HTTPError, urllib.error.URLError, KeyError, IndexError,
            TypeError, json.JSONDecodeError) as exc:
        info["lookup_error"] = f"properties:{exc.__class__.__name__}"
    time.sleep(0.25)

    try:
        payload = get_json(PUBCHEM_CID_SYNONYMS.format(urllib.parse.quote(str(cid))))
        entries = payload.get("InformationList", {}).get("Information", [])
        info["synonyms"] = [
            synonym
            for entry in entries
            for synonym in (entry.get("Synonym") or [])
            if isinstance(synonym, str)
        ]
    except (urllib.error.HTTPError, urllib.error.URLError, KeyError, IndexError,
            TypeError, json.JSONDecodeError) as exc:
        suffix = f"synonyms:{exc.__class__.__name__}"
        info["lookup_error"] = ";".join(filter(None, [info["lookup_error"], suffix]))
    time.sleep(0.25)

    cache[cache_key] = info
    return info


def load_identities(path):
    identities = {}
    with open(path, encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, delimiter="|")
        required = {"name", "inchi_key", "pubchem_cid", "aliases"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"{path} is missing required columns: {', '.join(sorted(missing))}"
            )
        for row in reader:
            key = norm(row.get("name", ""))
            if not key:
                continue
            if key in identities:
                raise ValueError(f"Duplicate identity name after normalization: {row['name']}")
            identities[key] = row
    return identities


def name_is_confirmed(compound_name, identity_aliases, synonyms, sourced_aliases):
    """Return (confirmed, method, source_urls)."""
    accepted = {norm(compound_name)}
    accepted.update(norm(alias) for alias in identity_aliases if alias.strip())
    accepted.discard("")
    observed = {norm(synonym) for synonym in synonyms}
    observed.discard("")
    if accepted & observed:
        return True, "PubChem synonym", []
    # The alias table is keyed by the exact catalogue name. Each row records
    # an external source explicitly equating that name with the canonical
    # chemical name, so the source-backed row itself is the evidence.
    usable_sources = [
        item for item in sourced_aliases
        if not re.search(r"\b(ambiguous|ambiguity|avoid)\b", item[2], re.I)
    ]
    if usable_sources:
        sources = [
            f"{source_url} ({note})" if note else source_url
            for alias, source_url, note in usable_sources
        ]
        return True, "independent alias source", sources
    return False, "", []


def load_alias_database(path):
    """Load independently documented name-to-compound aliases."""
    aliases = {}
    if not path.exists():
        return aliases
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, delimiter="|")
        required = {"name", "alias", "source_url", "source_note"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} is missing required columns: {', '.join(sorted(missing))}")
        for row in reader:
            key = norm(row.get("name", ""))
            alias = (row.get("alias") or "").strip()
            url = (row.get("source_url") or "").strip()
            if key and alias and url:
                aliases.setdefault(key, []).append((alias, url, (row.get("source_note") or "").strip()))
    return aliases


def save_cache(cache):
    PUBCHEM_CACHE.parent.mkdir(parents=True, exist_ok=True)
    PUBCHEM_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=CATALOGUE)
    parser.add_argument(
        "--identities",
        default=None,
        help="identity mapping (default: identities.csv beside the catalogue)",
    )
    parser.add_argument("--aliases", default=None,
                        help="independently sourced aliases (default: aliases.csv beside the catalogue)")
    parser.add_argument("--offline", action="store_true",
                        help="use cached PubChem responses only; do not make network requests")
    args = parser.parse_args()

    identity_path = Path(args.identities) if args.identities else Path(args.data).parent / "identities.csv"
    alias_path = Path(args.aliases) if args.aliases else Path(args.data).parent / "aliases.csv"
    if not identity_path.exists():
        parser.error(f"identity mapping not found: {identity_path}")

    compounds, _ = load(args.data)
    identities = load_identities(identity_path)
    alias_database = load_alias_database(alias_path)
    cache = json.loads(PUBCHEM_CACHE.read_text(encoding="utf-8")) if PUBCHEM_CACHE.exists() else {}

    rows = []
    requests_since_save = 0
    for index, compound in enumerate(compounds, 1):
        if not compound["formula"]:
            continue

        name = compound["name"]
        identity = identities.get(norm(name))
        csv_formula = compound["formula"]

        if identity is None:
            rows.append({
                "name": name, "csv_formula": csv_formula, "pubchem_cid": "",
                "expected_inchikey": "", "pubchem_inchikey": "",
                "pubchem_formula": "", "pubchem_title": "",
                "name_confirmed": "", "name_confirmation": "",
                "alias_sources": "", "status": "NO IDENTITY RECORD", "details": "",
            })
            continue

        cid = (identity.get("pubchem_cid") or "").strip()
        expected_key = (identity.get("inchi_key") or "").strip().upper()
        aliases = [part.strip() for part in (identity.get("aliases") or "").split(";") if part.strip()]

        if cid:
            cache_key = f"cid:{cid}"
            info = cache.get(cache_key)
            if info is None and not args.offline:
                try:
                    info = fetch_cid_info(cid, cache)
                except Exception as exc:
                    print(f"  {name} (CID {cid}): lookup failed ({exc.__class__.__name__})",
                          file=sys.stderr)
                    info = None
                requests_since_save += 1
                if requests_since_save % 10 == 0:
                    save_cache(cache)
                    print(f"  processed {index}/{len(compounds)} compounds",
                          end="\r", file=sys.stderr)

            if not info:
                rows.append({
                    "name": name, "csv_formula": csv_formula, "pubchem_cid": cid,
                    "expected_inchikey": expected_key, "pubchem_inchikey": "",
                    "pubchem_formula": "", "pubchem_title": "",
                    "name_confirmed": "", "name_confirmation": "",
                    "alias_sources": "", "status": "NO PUBCHEM DATA",
                    "details": "CID response unavailable (or not cached in --offline mode)",
                })
                continue

            pc_formula = info.get("formula", "")
            pc_key = (info.get("inchi_key") or "").strip().upper()
            synonyms = info.get("synonyms") or []
            external_aliases = alias_database.get(norm(name), [])
            confirmed, confirmation_method, confirmation_sources = name_is_confirmed(
                name, aliases, synonyms, external_aliases
            )
            formula_match = same_compound(csv_formula, pc_formula)
            key_match = bool(expected_key and pc_key and expected_key == pc_key)

            if not pc_formula or not pc_key:
                status = "INCOMPLETE PUBCHEM DATA"
            elif not key_match:
                status = "IDENTITY MISMATCH"
            elif not formula_match:
                status = "FORMULA DIFF"
            elif not confirmed and any(
                re.search(r"\b(ambiguous|ambiguity|avoid)\b", note, re.I)
                for alias, url, note in external_aliases
            ):
                status = "AMBIGUOUS ALIAS"
            elif not confirmed:
                status = "NAME NOT CONFIRMED"
            else:
                status = "MATCH"

            details = info.get("lookup_error", "")
            rows.append({
                "name": name, "csv_formula": csv_formula, "pubchem_cid": cid,
                "expected_inchikey": expected_key, "pubchem_inchikey": pc_key,
                "pubchem_formula": pc_formula, "pubchem_title": "",
                "name_confirmed": "yes" if confirmed else "no",
                "name_confirmation": confirmation_method,
                "alias_sources": ";".join(confirmation_sources),
                "status": status, "details": details,
            })
        else:
            # Only the eight records without a CID use the legacy name lookup.
            # This can check a formula, but cannot establish structural identity.
            info = cache.get(name)
            if info is None and not args.offline:
                try:
                    info = pubchem(name, cache)
                except Exception as exc:
                    print(f"  {name}: name lookup failed ({exc.__class__.__name__})",
                          file=sys.stderr)
                    info = None
                requests_since_save += 1
            pc_formula = (info or {}).get("formula", "")
            formula_match = same_compound(csv_formula, pc_formula) if pc_formula else False
            status = ("NAME LOOKUP ONLY" if formula_match else
                      "NAME LOOKUP FORMULA DIFF" if pc_formula else "NO PUBCHEM DATA")
            rows.append({
                "name": name, "csv_formula": csv_formula, "pubchem_cid": "",
                "expected_inchikey": expected_key, "pubchem_inchikey": "",
                "pubchem_formula": pc_formula,
                "pubchem_title": (info or {}).get("title", ""),
                "name_confirmed": "not checked (no CID)",
                "name_confirmation": "not checked (no CID)",
                "alias_sources": "",
                "status": status,
                "details": "Name-based lookup cannot independently confirm structure",
            })

    if cache and not args.offline:
        save_cache(cache)

    REVIEW.mkdir(parents=True, exist_ok=True)
    report_path = REVIEW / "verify_report.csv"
    fields = [
        "name", "csv_formula", "pubchem_formula", "pubchem_cid",
        "expected_inchikey", "pubchem_inchikey", "pubchem_title",
        "name_confirmed", "name_confirmation", "alias_sources", "status", "details",
    ]
    with report_path.open("w", encoding="utf-8", newline="") as report:
        writer = csv.DictWriter(report, fieldnames=fields, delimiter="|")
        writer.writeheader()
        writer.writerows(rows)

    tally = Counter(row["status"] for row in rows)
    print(f"\n{len(rows)} formulas checked: " +
          ", ".join(f"{count} {status}" for status, count in tally.most_common()))
    for status in (
        "IDENTITY MISMATCH", "FORMULA DIFF", "NAME NOT CONFIRMED", "AMBIGUOUS ALIAS",
        "INCOMPLETE PUBCHEM DATA", "NO PUBCHEM DATA",
        "NAME LOOKUP FORMULA DIFF", "NO IDENTITY RECORD",
    ):
        selected = [row for row in rows if row["status"] == status]
        if selected:
            print(f"\n== {status} ==")
            for row in selected:
                print(
                    f"  {row['name']}: CSV {row['csv_formula']} vs PubChem "
                    f"{row['pubchem_formula'] or '(no formula)'} "
                    f"[CID {row['pubchem_cid'] or 'none'}]"
                )
    print(f"\nFull report: {report_path}")
    print("MATCH requires matching InChIKeys, compatible formulas, and a confirmed name/alias.")
    print(f"Independent alias database: {alias_path} ({sum(map(len, alias_database.values()))} sourced aliases)")
    print("Rows without a CID are formula-checked by name only, not structurally verified.")


if __name__ == "__main__":
    main()
