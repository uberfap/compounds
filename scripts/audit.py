#!/usr/bin/env python3
"""Sanity-check compounds.csv: formula-derived molar mass vs. independently recalled masses.

    python scripts/audit.py              # exits 1 if anything is off by more than 0.15 g/mol

CAVEAT: known_masses.csv was recalled from memory, so it can share a mistake with the formula it checks
(Diphenidine and Methoxphenidine were both wrong in formula AND reference and still "passed"). This is a typo
catcher, not proof. verify.py (PubChem) is the independent check.

Also lists duplicate formulas under different names (usually fine: isomers) and
compounds with no reference mass yet, so you know what is still unchecked.
"""
import csv
import re
import sys
from collections import Counter, defaultdict

from generate import load, normalize_alpha
from project_paths import CATALOGUE, IDENTITIES, REVIEW

TOL = 0.15

loaded, load_warnings = load(CATALOGUE)
comps = {c["name"]: c for c in loaded}
for w in load_warnings:
    print(f"load warning: {w}")
known = {}
for line in open(REVIEW / "known_masses.csv", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#"):
        n, m = line.split("|")
        known[normalize_alpha(n)] = float(m)

bad = 0
for n, m in known.items():
    c = comps.get(n)
    if c is None:
        print(f"reference for a name not in the dataset: {n}")
        continue
    if c["mass"] is None:
        print(f"no usable formula: {n} ({c['formula']!r})")
        bad += 1
    elif abs(c["mass"] - m) > TOL:
        print(f"MISMATCH {n}: {c['formula']} -> {c['mass']:.1f}, reference {m}")
        bad += 1

# Names whose formula PubChem confirmed (run verify.py first; optional)
pubchem_ok = set()
try:
    with open(REVIEW / "verify_report.csv", encoding="utf-8", newline="") as f:
        pubchem_ok = {r["name"] for r in csv.DictReader(f, delimiter="|") if r["status"] == "match"}
except FileNotFoundError:
    print("(no verify_report.csv: run verify.py so PubChem-confirmed rows count as checked)")

# Formulas derived by hand from the structure (hand_checked.csv). Valid only while the formula is unchanged.
hand = {}
try:
    with open(REVIEW / "hand_checked.csv", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                parts = line.split("|", 2)
                if len(parts) >= 2:
                    hand[normalize_alpha(parts[0])] = parts[1]
except FileNotFoundError:
    pass
hand_ok = {n for n, fm in hand.items() if n in comps and comps[n]["formula"] == fm}
for n in hand:
    if n in comps and n not in hand_ok:
        print(f"formula changed since it was hand-checked: {n} ({hand[n]} -> {comps[n]['formula']})")

def read_pipe_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        lines = [l for l in f if l.strip() and not l.lstrip().startswith("#")]
    return list(csv.DictReader(lines, delimiter="|"))


def split_aliases(raw):
    return [a.strip() for a in (raw or "").split(";") if a.strip()]


def audit_identities():
    """identities.csv must line up with compounds.csv; returns the number of problems found."""
    problems = []
    try:
        rows = read_pipe_csv(IDENTITIES)
    except FileNotFoundError:
        print(f"(no {IDENTITIES.name}: skipping identity checks)")
        return 0
    expected = ["name", "inchi_key", "pubchem_cid", "aliases", "alias_source"]
    if rows and list(rows[0].keys()) != expected:
        problems.append(f"identities columns are {list(rows[0].keys())}, expected {expected}")

    names = [normalize_alpha((r["name"] or "").strip()) for r in rows]
    for n, count in Counter(n.casefold() for n in names).items():
        if count > 1:
            problems.append(f"duplicate identity row for {n!r}")
    missing = sorted(set(comps) - set(names))
    extra = sorted(set(names) - set(comps))
    if missing:
        problems.append(f"in compounds.csv but not identities.csv: {', '.join(missing)}")
    if extra:
        problems.append(f"in identities.csv but not compounds.csv: {', '.join(extra)}")

    keys, cids, alias_owner = Counter(), Counter(), defaultdict(set)
    name_lookup = {n.casefold(): n for n in comps}
    for name, r in zip(names, rows):
        key, cid = (r["inchi_key"] or "").strip(), (r["pubchem_cid"] or "").strip()
        if not re.fullmatch(r"[A-Z]{14}-[A-Z]{10}-[A-Z]", key):
            problems.append(f"{name}: malformed InChIKey {key!r}")
        keys[key] += 1
        if cid:
            if not re.fullmatch(r"[1-9][0-9]*", cid):
                problems.append(f"{name}: PubChem CID {cid!r} is not a positive integer")
            cids[cid] += 1
        aliases = split_aliases(r["aliases"])
        if aliases and not (r["alias_source"] or "").strip():
            problems.append(f"{name}: aliases have no alias_source")
        for a in aliases:
            alias_owner[a.casefold()].add(name)
            other = name_lookup.get(a.casefold())
            if other and other != name:
                problems.append(f"{name}: alias {a!r} is also the compound name {other!r}")
    problems += [f"InChIKey shared by several rows: {k}" for k, c in keys.items() if c > 1]
    problems += [f"PubChem CID shared by several rows: {k}" for k, c in cids.items() if c > 1]
    problems += [f"alias {a!r} used by {sorted(o)}" for a, o in alias_owner.items() if len(o) > 1]

    # compounds.csv also carries an aliases column; the two files must agree until one is dropped.
    ident_aliases = {n: {a.casefold() for a in split_aliases(r["aliases"])} for n, r in zip(names, rows)}
    for r in read_pipe_csv(CATALOGUE):
        name = normalize_alpha((r.get("name") or "").strip())
        theirs = {a.casefold() for a in split_aliases(r.get("aliases"))}
        if name in ident_aliases and theirs != ident_aliases[name]:
            problems.append(f"{name}: aliases differ between compounds.csv and identities.csv")

    for p in problems:
        print(f"IDENTITY PROBLEM: {p}")
    return len(problems)


bad += audit_identities()

unchecked = [n for n, c in comps.items()
             if n not in known and n not in pubchem_ok and n not in hand_ok and c["mass"] is not None]
no_formula = [n for n, c in comps.items() if c["mass"] is None]
only_pc = len([n for n in pubchem_ok if n in comps and n not in known])
print(f"\n{len(comps)} compounds | {len(known) - bad} by reference mass | {only_pc} more confirmed by PubChem | "
      f"{len(hand_ok - pubchem_ok - set(known))} hand-checked only | "
      f"{len(unchecked)} unchecked | {len(no_formula)} without a formula")
if unchecked:
    print("not confirmed by any source (check these):", ", ".join(unchecked))
sys.exit(1 if bad else 0)
