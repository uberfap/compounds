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

import json

from generate import load, name_key, parent_formula, parse_formula
from project_paths import CATALOGUE, IDENTITIES, REVIEW, SMILES

TOL = 0.15

# Every collection below is keyed by name_key(), so "alpha-PVP", "α-PVP" and "Alpha-PVP" are one compound.
# Names printed to the reader always come from compounds.csv (c["name"]) or the file that mentioned them.
loaded, load_warnings = load(CATALOGUE)
comps = {name_key(c["name"]): c for c in loaded}
for w in load_warnings:
    print(f"load warning: {w}")
known, known_label = {}, {}
for line in open(REVIEW / "known_masses.csv", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#"):
        n, m = line.split("|")
        known[name_key(n)] = float(m)
        known_label[name_key(n)] = n

bad = 0
for k, m in known.items():
    c = comps.get(k)
    if c is None:
        print(f"reference for a name not in the dataset: {known_label[k]}")
        continue
    if c["mass"] is None:
        print(f"no usable formula: {c['name']} ({c['formula']!r})")
        bad += 1
    elif abs(c["mass"] - m) > TOL:
        print(f"MISMATCH {c['name']}: {c['formula']} -> {c['mass']:.1f}, reference {m}")
        bad += 1

# Names whose formula PubChem confirmed (run verify.py first; optional)
pubchem_ok = set()
try:
    with open(REVIEW / "verify_report.csv", encoding="utf-8", newline="") as f:
        # verify.py writes "MATCH" (structure, formula AND name confirmed); older reports used "match".
        pubchem_ok = {name_key(r["name"]) for r in csv.DictReader(f, delimiter="|")
                      if (r["status"] or "").strip().casefold() == "match"}
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
                    hand[name_key(parts[0])] = parts[1]
except FileNotFoundError:
    pass
hand_ok = {k for k, fm in hand.items() if k in comps and comps[k]["formula"] == fm}
for k in hand:
    if k in comps and k not in hand_ok:
        print(f"formula changed since it was hand-checked: {comps[k]['name']} ({hand[k]} -> {comps[k]['formula']})")

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

    raw_names = [(r["name"] or "").strip() for r in rows]
    nkeys = [name_key(n) for n in raw_names]
    for k, count in Counter(nkeys).items():
        if count > 1:
            problems.append(f"duplicate identity row for {k!r}")
    missing = sorted(comps[k]["name"] for k in set(comps) - set(nkeys))
    extra = sorted(raw for raw, k in zip(raw_names, nkeys) if k not in comps)
    if missing:
        problems.append(f"in compounds.csv but not identities.csv: {', '.join(missing)}")
    if extra:
        problems.append(f"in identities.csv but not compounds.csv: {', '.join(extra)}")

    keys, cids, alias_owner = Counter(), Counter(), defaultdict(set)
    names = [comps[k]["name"] if k in comps else raw for raw, k in zip(raw_names, nkeys)]  # display names
    for name, nk, r in zip(names, nkeys, rows):
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
            alias_owner[name_key(a)].add(name)
            other = comps.get(name_key(a))
            if other and name_key(other["name"]) != nk:
                problems.append(f"{name}: alias {a!r} is also the compound name {other['name']!r}")
    problems += [f"InChIKey shared by several rows: {k}" for k, c in keys.items() if c > 1]
    problems += [f"PubChem CID shared by several rows: {k}" for k, c in cids.items() if c > 1]
    problems += [f"alias {a!r} used by {sorted(o)}" for a, o in alias_owner.items() if len(o) > 1]

    # compounds.csv also carries an aliases column; the two files must agree until one is dropped.
    ident_aliases = {k: {name_key(a) for a in split_aliases(r["aliases"])} for k, r in zip(nkeys, rows)}
    for r in read_pipe_csv(CATALOGUE):
        raw = (r.get("name") or "").strip()
        theirs = {name_key(a) for a in split_aliases(r.get("aliases"))}
        if name_key(raw) in ident_aliases and theirs != ident_aliases[name_key(raw)]:
            problems.append(f"{raw}: aliases differ between compounds.csv and identities.csv")

    for p in problems:
        print(f"IDENTITY PROBLEM: {p}")
    return len(problems)


def audit_structures():
    """Compare each formula with the formula of its cached SMILES (largest fragment, counter-ions dropped).

    Returns (keys whose formula matches the structure, number of disagreements). This catches a typo in
    either the formula or the cached structure. It does NOT prove the structure belongs to that name:
    only PubChem (verify.py) or a hand check does that.
    """
    try:
        from rdkit import Chem, RDLogger
    except ImportError:
        print("(rdkit not installed: skipping the structure-vs-formula check)")
        return set(), 0
    RDLogger.DisableLog("rdApp.*")
    try:
        cache = json.loads(SMILES.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"(no {SMILES.name}: skipping the structure-vs-formula check)")
        return set(), 0
    cached = {name_key(n): (n, smi) for n, smi in cache.items()}
    ok, problems = set(), 0
    for k, c in comps.items():
        if not c["formula"] or c["mass"] is None:
            continue
        if k not in cached:
            print(f"STRUCTURE PROBLEM: {c['name']}: no cached SMILES")
            problems += 1
            continue
        mol = Chem.MolFromSmiles(cached[k][1])
        if mol is None:
            print(f"STRUCTURE PROBLEM: {c['name']}: cached SMILES does not parse: {cached[k][1]!r}")
            problems += 1
            continue
        parent = max(Chem.GetMolFrags(mol, asMols=True), key=lambda m: m.GetNumHeavyAtoms())
        got = Counter(a.GetSymbol() for a in Chem.AddHs(parent).GetAtoms())
        want = Counter(parse_formula(parent_formula(c["formula"])))
        if got == want:
            ok.add(k)
        else:
            fmt = lambda cnt: "".join(f"{e}{n if n > 1 else ''}" for e, n in sorted(cnt.items()))
            print(f"STRUCTURE PROBLEM: {c['name']}: formula {fmt(want)} but cached SMILES gives {fmt(got)}")
            problems += 1
    return ok, problems


identity_problems = audit_identities()
structure_ok, structure_problems = audit_structures()

unchecked = [c["name"] for k, c in comps.items()
             if k not in known and k not in pubchem_ok and k not in hand_ok and c["mass"] is not None]
unchecked_structure_ok = [c["name"] for k, c in comps.items()
                          if k in structure_ok and k not in known and k not in pubchem_ok and k not in hand_ok]
no_formula = [c["name"] for c in comps.values() if c["mass"] is None]
only_pc = len([k for k in pubchem_ok if k in comps and k not in known])
print(f"\n{len(comps)} compounds | {len(known) - bad} by reference mass | {only_pc} more confirmed by PubChem | "
      f"{len(hand_ok - pubchem_ok - set(known))} hand-checked only | "
      f"{len(unchecked)} unchecked | {len(no_formula)} without a formula")
if structure_ok or structure_problems:
    print(f"structure check: {len(structure_ok)} formulas match their cached SMILES, "
          f"{structure_problems} do not; {len(unchecked_structure_ok)} of the {len(unchecked)} 'unchecked' are in the "
          "matching group (consistent with a structure, but name -> structure not confirmed: run verify.py)")
if unchecked:
    print("no reference mass, PubChem or hand check yet:", ", ".join(unchecked))
sys.exit(1 if bad or identity_problems or structure_problems else 0)
