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
import sys
from collections import defaultdict

from generate import load
from project_paths import CATALOGUE, REVIEW

TOL = 0.15

loaded, _ = load(CATALOGUE)
comps = {c["name"]: c for c in loaded}
known = {}
for line in open(REVIEW / "known_masses.csv", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#"):
        n, m = line.split("|")
        known[n] = float(m)

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
                    hand[parts[0]] = parts[1]
except FileNotFoundError:
    pass
hand_ok = {n for n, fm in hand.items() if n in comps and comps[n]["formula"] == fm}
for n in hand:
    if n in comps and n not in hand_ok:
        print(f"formula changed since it was hand-checked: {n} ({hand[n]} -> {comps[n]['formula']})")

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
