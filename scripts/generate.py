#!/usr/bin/env python3
"""Daily Connections-style puzzles for drugs / research chemicals.

    python scripts/generate.py --start 2026-11-19 --days 60

Reads the compound catalogue (or another pipe-delimited dataset), supports both the
new scaffold_class / pharmacological_class schema and legacy class / effect columns,
fetches missing SMILES from PubChem / CACTUS with failure reporting, renders 2D
skeletal structure SVGs via RDKit, and writes puzzles.json.
"""
import argparse
import csv
import datetime as dt
import json
import logging
import random
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from itertools import combinations
from functools import lru_cache
from pathlib import Path
import os
import tempfile

from project_paths import CACHE, CATALOGUE, PUZZLES, SMILES

try:
    from rdkit import Chem
    from rdkit.Chem import AllChem
    from rdkit.Chem.Draw import rdMolDraw2D
    HAS_RDKIT = True
except ImportError:
    HAS_RDKIT = False

ATOMIC = {"H": 1.008, "C": 12.011, "N": 14.007, "O": 15.999, "F": 18.998,
          "P": 30.974, "S": 32.06, "Cl": 35.45, "Br": 79.904, "I": 126.904,
          "Na": 22.990, "K": 39.098}

ORIGIN_LABEL = {"natural": "Naturally occurring", "semisynthetic": "Semisynthetic",
                "synthetic": "Fully synthetic"}
TARGET_LABEL = {
    "Dopamine releaser": "Dopamine releasers",
    "Norepinephrine releaser": "Norepinephrine releasers",
    "Serotonin releaser": "Serotonin releasers",
    "Dopamine reuptake inhibitor": "Dopamine reuptake inhibitors",
    "Norepinephrine reuptake inhibitor": "Norepinephrine reuptake inhibitors",
    "Serotonin reuptake inhibitor": "Serotonin reuptake inhibitors",
    "NMDA antagonist": "NMDA antagonists",
    "GABA-A PAM": "GABA-A PAMs",
    "GABA-A agonist": "GABA-A agonists",
    "GABA-B agonist": "GABA-B agonists",
    "CB1 agonist": "CB1 agonists",
    "CB2 agonist": "CB2 agonists",
    "5-HT2A agonist": "5-HT2A agonists",
    "5-HT2C agonist": "5-HT2C agonists",
    "5-HT1A agonist": "5-HT1A agonists",
    "5-HT2B agonist": "5-HT2B agonists",
    "μ-opioid agonist": "μ-opioid agonists",
    "μ-opioid antagonist": "μ-opioid antagonists",
    "κ-opioid agonist": "κ-opioid agonists",
    "κ-opioid antagonist": "κ-opioid antagonists",
    "δ-opioid agonist": "δ-opioid agonists",
    "δ-opioid antagonist": "δ-opioid antagonists",
    "D2 agonist": "D2 agonists",
    "D2 antagonist": "D2 antagonists",
    "Sigma-1 agonist": "Sigma-1 agonists",
    "nACh agonist": "nACh agonists",
    "nACh antagonist": "nACh antagonists",
    "Muscarinic agonist": "Muscarinic agonists",
    "Muscarinic antagonist": "Muscarinic antagonists",
    "H1 antagonist": "H1 antagonists",
    "Adenosine antagonist": "Adenosine antagonists",
    "MAO-A inhibitor": "MAO-A inhibitors",
    "MAO-B inhibitor": "MAO-B inhibitors",
    "α2-adrenergic agonist": "α2-adrenergic agonists",
    "α2-adrenergic antagonist": "α2-adrenergic antagonists",
    "α2δ ligand": "α2δ ligands",
    "AMPA PAM": "AMPA PAMs",
    "NMDA agonist": "NMDA agonists",
    "5-HT2A antagonist": "5-HT2A antagonists",
    "5-HT2C antagonist": "5-HT2C antagonists",
}

# Families treated as cross-cutting "properties" rather than taxonomic classes/targets.
PROPERTY_FAMILIES = {"origin", "formula", "structure", "name:trivial", "name:specific"}

# Absolute 0-100 priors for the kind of knowledge a clue requires.
# These are NOT the final scores or the final colours: the board score below
# modifies them, and puzzle selection requires one group in each score band.
FAMILY_DIFFICULTY_BASE = {
    "name:trivial": 12,
    "name:specific": 26,
    "pharmacological_class": 46,
    "scaffold_class": 54,
    "origin": 48,
    "target": 72,
    "formula": 92,
    "structure": 80,
}

# Clue-specific difficulty adjustments, in points on the 0-100 scale.
DIFFICULTY_OVERRIDES = {
    "label:dox series": -18,
    "label:barbiturates": -18,
    "target:κ-opioid agonist": 8,
}

# Each accepted puzzle must contain one group in every band. The generator
# actively samples predicates from these prior ranges, then rejects a board
# unless its *actual board-context scores* land in all four bands.
DIFFICULTY_TARGETS = (25, 50, 75, 100)
DIFFICULTY_BANDS = ((25, 37), (38, 62), (63, 87), (88, 100))
# Fast candidate-generation strata. Manual overrides are included when a
# predicate is put into a pool. These are only priors: the final board score
# must still land in each fixed output band, or the whole board is rejected.
# Structural and specialist clues can reach Purple through board-specific ambiguity.
DIFFICULTY_PRIOR_BANDS = ((0, 37), (38, 62), (63, 87), (80, 100))
BOARD_VARIANTS_PER_PREDICATE_SET = 8
MIN_DIFFICULTY_GAP = 10

# If a group's label describes an elemental property, compound-name clues can
# make that property much more visible without spending a structure inspection.
ELEMENT_NAME_HINTS = {
    "br": ("brom",),
    "cl": ("chlor",),
    "f": ("fluor",),
    "i": ("iod",),
    "s": ("sulfur", "sulphur", "thio"),
    "p": ("phosph",),
}

# Class-name/compound-name correspondences that are often visually recognizable.
CLASS_NAME_HINTS = {
    "barbiturate": ("barbital", "barbitur"),
    "benzodiazepine": ("zepam", "zolam", "azepam"),
    "lysergamide": ("lad", "lsd", "lyserg"),
}

FAMILY_PRIORITY = {
    "origin": 0,
    "pharmacological_class": 1,
    "scaffold_class": 2,
    "name:trivial": 3,
    "name:specific": 3,
    "target": 4,
    "formula": 5,
    "structure": 5,
}

PHARMACOLOGICAL_LABEL = {
    "psychedelic": "Psychedelics",
    "stimulant": "Stimulants",
    "entactogen": "Entactogens",
    "depressant": "Depressants",
    "opioid": "Opioids",
    "cannabinoid": "Cannabinoids",
    "dissociative": "Dissociatives",
    "deliriant": "Deliriants",
    "therapeutic": "Therapeutics",
}

MISSING_TAGS = {"", "none", "unknown", "unclassified", "unspecified", "n/a", "na", "other", "others", "mixed", "-"}
# Fields a "# Inferred fields|<name>|<f1>,<f2>" comment line may mark as inferred (kept out of puzzle rules).
INFERRABLE_FIELDS = {"target", "effect", "pharmacological_class"}
MAX_EXACT_FOUR_DECOYS = 2

STRUCTURE_PATTERNS = (
    ("nitrile", "Contain a nitrile group", "[#6]#[#7]"),
    ("ester", "Contain a carboxylic ester group", "[CX3;H1,$([CX3][#6])](=[OX1])[OX2][#6]"),
    ("amide", "Contain an amide group", "[CX3](=[OX1])[NX3]"),
    ("phenol", "Contain a phenolic hydroxyl group", "[c][OX2H]"),
    ("methoxy", "Contain an aromatic methoxy group", "[c][OX2][CH3]"),
    ("lactam", "Contain a cyclic amide", "[C;r](=[OX1])@[N;r]"),
    ("thiophene", "Contain a thiophene ring", "s1cccc1"),
    ("cyclopropane", "Contain a cyclopropane ring", "[C;r3]1[C;r3][C;r3]1"),
)

NEW_NAME_PREDS = {
    "name:4locant": ('Names starting with "4-"', lambda n: n.startswith("4-")),
    "name:5locant": ('Names starting with "5-"', lambda n: n.startswith("5-")),
    "name:ol": ('Names ending in "ol"', lambda n: n.endswith("ol")),
    "name:oxy": ('Names containing the letters "oxy"', lambda n: "oxy" in n),
}

TRIVIAL_NAME_PIDS = {"name:2c", "name:digit", "name:three", "name:meth",
                     "name:di", "name:one", "name:ine", "name:phen"}

NAME_SUFFIXES = ("tryptamine", "cathinone", "phenidate", "cyclidine",
                 "nitazene", "fentanyl", "barbital", "ketamine", "qualone",
                 "inaca", "nbome", "phetamine", "pam")
BENZO_MARKERS = ("zepam", "azepam", "azolam", "zolam")


def plural(s):
    return s if s.endswith("s") else s + "s"


def normalize_alpha(text):
    return re.sub(r"alpha", "α", text, flags=re.IGNORECASE)


def format_target_label(t):
    if t in TARGET_LABEL:
        return TARGET_LABEL[t]
    for suffix, pl_suffix in [
        (" agonist", " agonists"),
        (" antagonist", " antagonists"),
        (" releaser", " releasers"),
        (" reuptake inhibitor", " reuptake inhibitors"),
        (" inhibitor", " inhibitors"),
        (" ligand", " ligands"),
    ]:
        if t.endswith(suffix):
            return t[: -len(suffix)] + pl_suffix
    return plural(t)


def parse_formula(f):
    el = Counter()
    for e, n in re.findall(r"([A-Z][a-z]?)(\d*)", f):
        el[e] += int(n or 1)
    return el


def molar_mass(el):
    return sum(ATOMIC[e] * n for e, n in el.items())


def parent_formula(f):
    return re.split(r"[·•*.]", f, maxsplit=1)[0].strip()


def parse_targets(raw):
    if not raw:
        return []
    return [
        tag for item in raw.split(";")
        if (tag := item.strip()).casefold() not in MISSING_TAGS
        and not tag.casefold().startswith("inferred:")
    ]


# ----------------------------------------------------------- structure SVG
def render_skeletal_svg(smiles, width=240, height=160):
    """Render a clean, theme-adaptive 2D skeletal structure SVG with no text labels/formulas."""
    if not HAS_RDKIT or not smiles:
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    AllChem.Compute2DCoords(mol)
    drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
    opts = drawer.drawOptions()
    opts.clearBackground = False
    opts.bondLineWidth = 1.9
    opts.padding = 0.08
    opts.useBWAtomPalette()
    drawer.DrawMolecule(mol)
    drawer.FinishDrawing()
    svg = drawer.GetDrawingText()
    svg = svg.replace("#000000", "currentColor").replace("rgb(0,0,0)", "currentColor")
    svg = re.sub(r"<\?xml[^>]*\?>", "", svg)
    svg = re.sub(r"<!--.*?-->", "", svg, flags=re.DOTALL)
    svg = re.sub(r"\s+xmlns:rdkit='[^']*'", "", svg)
    svg = re.sub(r"\s+xmlns:xlink='[^']*'", "", svg)
    svg = re.sub(r"\s+version='1\.1'", "", svg)
    svg = re.sub(r"\s+baseProfile='full'", "", svg)
    svg = re.sub(r"\s+xml:space='preserve'", "", svg)
    svg = re.sub(r"\s+class='[^']*'", "", svg)
    svg = re.sub(r"<rect[^>]*fill:#FFFFFF[^>]*/>", "", svg)
    common_bond = ("style='fill:none;fill-rule:evenodd;stroke:currentColor;"
                   "stroke-width:1.9px;stroke-linecap:butt;stroke-linejoin:miter;stroke-opacity:1'")
    svg = svg.replace(common_bond, "")
    svg = re.sub(r"\s+/>", "/>", svg)
    svg = re.sub(r">\s+<", "><", svg)
    svg = re.sub(r"\s{2,}", " ", svg).strip()
    svg = svg.replace(
        ">",
        "><style>path:not([fill]){fill:none;stroke:currentColor;stroke-width:1.9px;"
        "stroke-linecap:butt;stroke-linejoin:miter}</style>",
        1,
    )
    return svg


def _http_get_text(url, source, timeout, retries, logger):
    """GET a URL with bounded retries; return (body, error_message)."""
    retryable_statuses = {408, 425, 429, 500, 502, 503, 504}
    last_error = "unknown request error"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "CompoundsGame/2.0 (SMILES lookup; contact: local script)",
                    "Accept": "application/json,text/plain,*/*",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace").strip()
                if not body:
                    return None, f"{source}: empty response"
                return body, None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            detail = re.sub(r"\s+", " ", detail)[:400]
            last_error = f"{source}: HTTP {exc.code}" + (f" - {detail}" if detail else "")
            if exc.code not in retryable_statuses or attempt >= retries:
                logger.warning("%s (attempt %d/%d)", last_error, attempt + 1, retries + 1)
                return None, last_error
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            last_error = f"{source}: {type(reason).__name__}: {reason}"
            if attempt >= retries:
                logger.warning("%s (attempt %d/%d)", last_error, attempt + 1, retries + 1)
                return None, last_error
        except Exception as exc:
            last_error = f"{source}: {type(exc).__name__}: {exc}"
            logger.exception("Unexpected %s error", source)
            return None, last_error

        wait = min(2 ** attempt, 8)
        logger.info("%s; retrying in %ss", last_error, wait)
        time.sleep(wait)

    return None, last_error


def _valid_smiles(smiles):
    """Validate that a SMILES string can be parsed by RDKit."""
    if not smiles or not smiles.strip():
        return False, "empty SMILES"
    if HAS_RDKIT:
        try:
            if Chem.MolFromSmiles(smiles.strip()) is None:
                return False, "RDKit could not parse returned SMILES"
        except Exception as exc:
            return False, f"RDKit validation failed: {type(exc).__name__}: {exc}"
    return True, None


def _matches_parent_formula(smiles, expected_elements):
    """Check heavy-atom composition against the CSV parent formula.

    Salt/counterion fragments are allowed: at least one disconnected SMILES
    fragment must have the same non-hydrogen element counts as the CSV formula.
    This catches syntactically valid but unrelated SMILES (for example, the
    literal string 'PCP', which RDKit parses as a phosphorus-containing chain).
    """
    if not HAS_RDKIT or not expected_elements:
        return True, None

    expected = Counter({
        element: count for element, count in expected_elements.items()
        if element != "H" and count
    })
    if not expected:
        return True, None

    try:
        mol = Chem.MolFromSmiles(smiles.strip())
        if mol is None:
            return False, "RDKit could not parse SMILES"
        fragments = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=True)
        for fragment in fragments:
            actual = Counter(
                atom.GetSymbol() for atom in fragment.GetAtoms()
                if atom.GetAtomicNum() > 1
            )
            if actual == expected:
                return True, None

        largest = max(fragments, key=lambda frag: frag.GetNumHeavyAtoms())
        actual = Counter(
            atom.GetSymbol() for atom in largest.GetAtoms()
            if atom.GetAtomicNum() > 1
        )
        expected_text = ", ".join(f"{el}:{n}" for el, n in sorted(expected.items()))
        actual_text = ", ".join(f"{el}:{n}" for el, n in sorted(actual.items()))
        return False, (
            "SMILES element counts do not match the CSV formula "
            f"(expected [{expected_text}], largest fragment has [{actual_text}])"
        )
    except Exception as exc:
        return False, f"SMILES formula validation failed: {type(exc).__name__}: {exc}"


def fetch_smiles_online(name, timeout=15, retries=2, logger=None,
                        expected_elements=None):
    """Look up a name in PubChem, then CACTUS, validating formula at each source."""
    logger = logger or logging.getLogger(__name__)
    query_name = (name.replace("α", "alpha")
                      .replace("β", "beta")
                      .replace("Δ", "delta")
                      .strip())
    encoded = urllib.parse.quote(query_name, safe="")
    errors = []

    # PubChem's current property names are SMILES and ConnectivitySMILES.
    pubchem_url = (
        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded}/"
        "property/SMILES,ConnectivitySMILES/JSON"
    )
    body, error = _http_get_text(pubchem_url, "PubChem", timeout, retries, logger)
    if error:
        errors.append(error)
    else:
        try:
            data = json.loads(body)
            props = data["PropertyTable"]["Properties"][0]
            smi = props.get("SMILES") or props.get("ConnectivitySMILES")
            valid, validation_error = _valid_smiles(smi or "")
            if valid:
                formula_ok, formula_error = _matches_parent_formula(
                    smi, expected_elements
                )
                if formula_ok:
                    return smi.strip(), "PubChem", errors
                errors.append(f"PubChem: {formula_error}")
            else:
                errors.append(f"PubChem: {validation_error or 'no SMILES property in response'}")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            errors.append(f"PubChem: unexpected response ({type(exc).__name__}: {exc})")

    # CACTUS resolver uses an exact full-string name lookup; no fuzzy guesses are made.
    cactus_url = f"https://cactus.nci.nih.gov/chemical/structure/{encoded}/smiles"
    body, error = _http_get_text(cactus_url, "CACTUS", timeout, retries, logger)
    if error:
        errors.append(error)
    else:
        smi = body.splitlines()[0].strip()
        if smi.startswith("<") or smi.lower().startswith("error"):
            errors.append(f"CACTUS: resolver returned non-SMILES response: {smi[:200]}")
        else:
            valid, validation_error = _valid_smiles(smi)
            if valid:
                formula_ok, formula_error = _matches_parent_formula(
                    smi, expected_elements
                )
                if formula_ok:
                    return smi, "CACTUS", errors
                errors.append(f"CACTUS: {formula_error}")
            else:
                errors.append(f"CACTUS: {validation_error}")

    return None, None, errors


def _write_json_atomic(path, data, retries=10, retry_delay=0.25):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Create a unique temporary file in the same directory.
    # This avoids collisions between concurrent executions.
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent)
    )

    temp = Path(temp_name)

    try:
        # Write and flush the complete JSON before replacing the original.
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        # Windows may temporarily deny replacement if another process
        # is accessing the destination file. Retry a few times.
        for attempt in range(retries):
            try:
                os.replace(str(temp), str(path))
                return

            except PermissionError:
                if attempt == retries - 1:
                    raise

                time.sleep(retry_delay * (attempt + 1))

    finally:
        # Remove the temporary file if replacement did not succeed.
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _write_smiles_failures(path, failures):
    if not path:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "errors"])
        writer.writeheader()
        for item in failures:
            writer.writerow({"name": item["name"], "errors": " | ".join(item["errors"])})


def resolve_smiles_map(comps, cache_path, fetch_missing=False, *, timeout=15,
                       retries=2, delay=0.25, log_path="smiles_fetch.log",
                       failures_path="smiles_failures.csv"):
    """Resolve SMILES with progress, persistent success cache, and actionable failures."""
    logger = logging.getLogger("smiles")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    if log_path:
        log_file = Path(log_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)

    cache = {}
    p = Path(cache_path)
    if p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cache = {str(k): str(v) for k, v in loaded.items() if v}
            else:
                logger.warning("Ignoring SMILES cache: expected a JSON object")
        except Exception as exc:
            logger.error("Could not read SMILES cache %s: %s: %s", p, type(exc).__name__, exc)

    failures = []
    stats = Counter()
    total = len(comps)
    for index, c in enumerate(comps, 1):
        name = c["name"]
        smi = c.get("smiles", "").strip()
        source = "CSV"

        if smi:
            valid, validation_error = _valid_smiles(smi)
            if valid:
                valid, validation_error = _matches_parent_formula(smi, c.get("el"))
            if not valid:
                logger.warning("%s: rejected CSV SMILES (%s)", name, validation_error)
                smi, source = "", ""

        if not smi and cache.get(name):
            candidate = cache[name]
            valid, validation_error = _valid_smiles(candidate)
            if valid:
                valid, validation_error = _matches_parent_formula(candidate, c.get("el"))
            if valid:
                smi, source = candidate, "cache"
            else:
                logger.warning(
                    "%s: rejected cached SMILES (%s); will refetch if enabled",
                    name, validation_error
                )
                cache.pop(name, None)

        if not smi and fetch_missing:
            fetched, fetched_source, errors = fetch_smiles_online(
                name, timeout=timeout, retries=retries, logger=logger,
                expected_elements=c.get("el")
            )
            if fetched:
                smi, source = fetched, fetched_source
                cache[name] = smi
                stats[f"success_{source.lower()}"] += 1
                # Persist every hit so an interrupted run does not lose completed lookups.
                _write_json_atomic(p, cache)
                if errors:
                    logger.info("%s: resolved by %s after fallback: %s", name, source, " | ".join(errors))
                logger.info("%s: SUCCESS via %s", name, source)

            if not smi:
                failures.append({"name": name, "errors": errors})
                stats["failed"] += 1
                logger.error("%s: FAILED: %s", name, " | ".join(errors) or "no details returned")
            if delay > 0:
                time.sleep(delay)
        elif smi:
            stats[f"available_{source.lower()}"] += 1
            if source == "CSV":
                cache[name] = smi

        c["smiles"] = smi or ""
        status = f"OK ({source})" if smi else ("FAILED" if fetch_missing else "missing; use --fetch-smiles")
        print(f"[SMILES {index:>4}/{total}] {name}: {status}", flush=True)

    # Preserve successful values only; failed lookups are deliberately not negative-cached.
    _write_json_atomic(p, cache)
    _write_smiles_failures(failures_path, failures)

    available = sum(1 for c in comps if c.get("smiles"))
    print("\nSMILES summary:")
    print(f"  available: {available}/{total}")
    if fetch_missing:
        print(f"  fetched from PubChem: {stats['success_pubchem']}")
        print(f"  fetched from CACTUS:  {stats['success_cactus']}")
        print(f"  failed lookups:      {stats['failed']}")
        if failures_path:
            print(f"  failure report:      {failures_path}")
    if log_path:
        print(f"  detailed log:        {log_path}")
    return stats


# --------------------------------------------------------------------- data
def load(path):
    with open(path, encoding="utf-8", newline="") as f:
        lines, inferred, warnings = [], {}, []
        for lineno, line in enumerate(f, 1):
            if line.startswith("# Inferred fields|"):
                parts = line.strip().split("|", 2)
                if len(parts) < 3 or not parts[1].strip():
                    warnings.append(f"line {lineno}: malformed inferred-fields annotation {line.strip()!r}")
                    continue
                compound = parts[1].strip()
                fields = {x.strip() for x in parts[2].split(",") if x.strip()}
                unknown = fields - INFERRABLE_FIELDS
                if unknown:
                    warnings.append(
                        f"line {lineno}: {compound}: unknown inferred field(s) {sorted(unknown)} "
                        f"(expected {sorted(INFERRABLE_FIELDS)}); they will not be applied"
                    )
                inferred.setdefault(compound, set()).update(fields & INFERRABLE_FIELDS)
            elif line.strip() and not line.lstrip().startswith("#"):
                lines.append(line)
    comps, seen, row_names = [], set(), set()
    for r in csv.DictReader(lines, delimiter="|"):
        raw_name = (r.get("name") or "").strip()
        row_names.add(raw_name)
        name = normalize_alpha(raw_name)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        formula = (r.get("formula") or "").strip()
        el = mass = None
        if formula:
            try:
                el = parse_formula(parent_formula(formula))
                mass = molar_mass(el)
                if not el:
                    raise KeyError("(empty formula after salt-strip)")
            except KeyError as exc:
                warnings.append(f"{name}: cannot parse formula {formula!r} ({exc})")
                el = mass = None
        inferred_fields = inferred.get(raw_name, set())
        targets = [] if "target" in inferred_fields else parse_targets(r.get("target") or "")
        effect = (r.get("effect") or "").strip().casefold()
        if "effect" in inferred_fields or effect.startswith("inferred:"):
            effect = ""
        pharmacological_class = (r.get("pharmacological_class") or effect).strip().casefold()
        if "pharmacological_class" in inferred_fields or pharmacological_class.startswith("inferred:"):
            pharmacological_class = ""
        comps.append({
            "name": name,
            "formula": formula,
            "smiles": (r.get("smiles") or "").strip(),
            "el": el,
            "mass": mass,
            # New schema first; legacy columns remain supported for older CSVs.
            "scaffold_class": (r.get("scaffold_class") or r.get("class") or "").strip().casefold(),
            "pharmacological_class": pharmacological_class,
            "effect": effect,
            "target": ";".join(targets),
            "targets": targets,
            "origin": (r.get("origin") or "").strip().lower(),
        })
    for compound in sorted(set(inferred) - row_names):
        warnings.append(
            f"inferred-fields annotation names {compound!r}, which is not a compound in the file; "
            "its inferred fields are NOT being hidden from anything"
        )
    return comps, warnings


def diversity_key(c):
    if c["scaffold_class"]:
        return "scaffold:" + c["scaffold_class"]
    n = c["name"].lower()
    if any(m in n for m in BENZO_MARKERS):
        return "family:benzodiazepine"
    for suf in NAME_SUFFIXES:
        if n.endswith(suf):
            return "suf:" + suf
    if c["pharmacological_class"]:
        return "pharm:" + c["pharmacological_class"]
    if c["targets"]:
        return "tgt:" + c["targets"][0].lower()
    return "name:" + n


# --------------------------------------------------------------- predicates
class Pred:
    def __init__(self, pid, family, label, fn):
        self.pid, self.family, self.label, self.fn = pid, family, label, fn
        self.matches = frozenset()


NAME_PREDS = [
    ("name:2c", "Names starting with “2C-”", lambda n: n.startswith("2c-")),
    ("name:digit", "Names starting with a number", lambda n: bool(n) and n[0].isdigit()),
    ("name:three", "Three-letter names", lambda n: len(n) == 3 and n.isalpha()),
    ("name:meth", "Names starting with “Meth”", lambda n: n.startswith("meth")),
    ("name:di", "Names starting with “Di”", lambda n: n.startswith("di") and len(n) > 3),
    ("name:phen", "Names containing “phen”", lambda n: "phen" in n),
    ("name:hyphen", "Names containing a hyphen", lambda n: "-" in n),
    ("name:greek", "Names with a Greek letter", lambda n: any(ch in n for ch in "αβγδεζηθικλμνξοπρστυφχψωδ")),
    ("name:zolam", "Names ending in -zolam", lambda n: n.endswith("zolam")),
    ("name:pam", "Names ending in -pam", lambda n: n.endswith("pam")),
    ("name:inaca", "Names ending in -INACA", lambda n: n.endswith("inaca")),
    ("name:nbome", "Names ending in -NBOMe", lambda n: n.endswith("nbome")),
    ("name:nitazene", "Names ending in -nitazene", lambda n: n.endswith("nitazene")),
    ("name:ine", "Names ending in -ine", lambda n: n.endswith("ine")),
    ("name:one", "Names ending in -one", lambda n: n.endswith("one")),
    ("name:fly", "Names containing “FLY”", lambda n: "fly" in n),
    ("name:mdmb", "Names containing “MDMB”", lambda n: "mdmb" in n),
    ("name:pcp", "Names containing “PCP”", lambda n: "pcp" in n),
]


def build(comps, cap):
    preds = []

    def add(pid, fam, label, fn):
        preds.append(Pred(pid, fam, label, fn))

    for o in sorted({c["origin"] for c in comps} - MISSING_TAGS):
        add(f"origin:{o}", "origin", ORIGIN_LABEL.get(o, o.title()),
            lambda c, o=o: c["origin"] == o)
    for category in sorted({c["pharmacological_class"] for c in comps} - MISSING_TAGS):
        label = PHARMACOLOGICAL_LABEL.get(category, plural(category.title()))
        add(f"pharmacological_class:{category}", "pharmacological_class", label,
            lambda c, category=category: c["pharmacological_class"] == category)
    for scaffold in sorted({c["scaffold_class"] for c in comps} - MISSING_TAGS):
        label = plural(scaffold.title())
        add(f"scaffold_class:{scaffold}", "scaffold_class", label,
            lambda c, scaffold=scaffold: c["scaffold_class"] == scaffold)
    for t in sorted({t for c in comps for t in c["targets"]} - MISSING_TAGS):
        add(f"target:{t}", "target", format_target_label(t),
            lambda c, t=t: t in c["targets"])

    for sym, word in [("Br", "bromine"), ("Cl", "chlorine"), ("F", "fluorine"),
                      ("I", "iodine"), ("S", "sulfur"), ("P", "phosphorus")]:
        add(f"el:{sym}", "formula", f"Contain {word}",
            lambda c, s=sym: c["el"] is not None and c["el"][s] > 0)
    add("el:noN", "formula", "Contain no nitrogen",
        lambda c: c["el"] is not None and c["el"]["N"] == 0)
    add("el:noO", "formula", "Contain no oxygen",
        lambda c: c["el"] is not None and c["el"]["O"] == 0)
    add("el:N3plus", "formula", "Three or more nitrogens",
        lambda c: c["el"] is not None and c["el"]["N"] >= 3)
    add("el:O3plus", "formula", "Three or more oxygens",
        lambda c: c["el"] is not None and c["el"]["O"] >= 3)
    add("el:C20plus", "formula", "20 or more carbons",
        lambda c: c["el"] is not None and c["el"]["C"] >= 20)
    add("mass:lt200", "formula", "Molar mass under 200 g/mol",
        lambda c: c["mass"] is not None and c["mass"] < 200)
    add("mass:200_250", "formula", "Molar mass 200–250 g/mol",
        lambda c: c["mass"] is not None and 200 <= c["mass"] <= 250)
    add("mass:250_300", "formula", "Molar mass over 250 and up to 300 g/mol",
        lambda c: c["mass"] is not None and 250 < c["mass"] <= 300)
    add("mass:300_350", "formula", "Molar mass over 300 and up to 350 g/mol",
        lambda c: c["mass"] is not None and 300 < c["mass"] <= 350)
    add("mass:gt350", "formula", "Molar mass over 350 g/mol",
        lambda c: c["mass"] is not None and c["mass"] > 350)

    if HAS_RDKIT:
        molecules = {
            smiles: Chem.MolFromSmiles(smiles)
            for smiles in {c["smiles"] for c in comps if c["smiles"]}
        }
        for key, label, smarts in STRUCTURE_PATTERNS:
            pattern = Chem.MolFromSmarts(smarts)
            matches = {smiles for smiles, mol in molecules.items()
                       if mol is not None and mol.HasSubstructMatch(pattern)}
            add(f"structure:{key}", "structure", label,
                lambda c, matches=matches: c["smiles"] in matches)

    for pid, label, fn in NAME_PREDS:
        fam = "name:trivial" if pid in TRIVIAL_NAME_PIDS else "name:specific"
        add(pid, fam, label, lambda c, fn=fn: fn(normalize_alpha(c["name"]).lower()))
    for pid, (label, fn) in NEW_NAME_PREDS.items():
        add(pid, "name:trivial", label, lambda c, fn=fn: fn(re.sub(
            r"^(?:\((?:r|s|±|\+|−|-)\)-)+", "",
            unicodedata.normalize("NFKC", c["name"]).casefold())))

    for p in preds:
        p.matches = frozenset(i for i, c in enumerate(comps) if p.fn(c))
    preds = [p for p in preds if 4 <= len(p.matches) <= cap * len(comps)]
    # Keep the predicate library un-tiered. Difficulty is assessed later for
    # each concrete 16-tile board, because a group's ambiguity depends on its
    # particular distractors and the other groups selected with it.
    preds = _dedupe(preds)
    return preds


def _dedupe(preds, threshold=0.9):
    ordered = sorted(preds, key=lambda p: (FAMILY_PRIORITY.get(p.family, 9),
                                           len(p.matches), p.pid))
    kept = []
    for p in ordered:
        for q in kept:
            inter = len(p.matches & q.matches)
            if inter and inter / (len(p.matches) + len(q.matches) - inter) >= threshold:
                break
        else:
            kept.append(p)
    return kept


def _compact_name(value):
    """Normalize names for simple, human-visible substring comparisons."""
    value = normalize_alpha(str(value)).casefold()
    return re.sub(r"[^a-z0-9]+", "", value)


def _shared_name_signal(member_names, distractor_names):
    """Return 0..1 for how visibly related the four member names are.

    Common 3-8 character fragments are weighted by how few distractor names
    contain them. A short common prefix (e.g. DO...) still counts, but less
    strongly than a distinctive suffix such as -barbital.
    """
    members = [_compact_name(n) for n in member_names]
    distractors = [_compact_name(n) for n in distractor_names]
    members = [n for n in members if n]
    if len(members) < 3:
        return 0.0

    best = 0.0
    gram_hits = defaultdict(set)
    for mi, name in enumerate(members):
        for width in range(3, min(8, len(name)) + 1):
            for start in range(len(name) - width + 1):
                gram_hits[name[start:start + width]].add(mi)
    for gram, hits in gram_hits.items():
        coverage = len(hits) / 4.0
        if coverage < 0.75:
            continue
        distractor_hits = sum(1 for name in distractors if gram in name)
        length_strength = min(1.0, len(gram) / 7.0)
        specificity = 1.0 / (1.0 + 0.38 * distractor_hits)
        best = max(best, coverage * length_strength * specificity)

    # A two-letter common prefix can be highly recognizable in a curated
    # family such as DOx, but should not dominate longer distinctive patterns.
    prefix = members[0]
    for name in members[1:]:
        limit = min(len(prefix), len(name))
        pos = 0
        while pos < limit and prefix[pos] == name[pos]:
            pos += 1
        prefix = prefix[:pos]
    if len(prefix) >= 2:
        distractor_hits = sum(1 for name in distractors if name.startswith(prefix))
        prefix_signal = min(0.52, 0.20 * len(prefix)) / (1.0 + 0.28 * distractor_hits)
        best = max(best, prefix_signal)

    # Likewise consider a shared suffix; it is especially useful for families
    # whose names conventionally end in the same chemical-class stem.
    suffix = members[0]
    for name in members[1:]:
        limit = min(len(suffix), len(name))
        pos = 0
        while pos < limit and suffix[-1 - pos] == name[-1 - pos]:
            pos += 1
        suffix = suffix[len(suffix) - pos:] if pos else ""
    if len(suffix) >= 3:
        distractor_hits = sum(1 for name in distractors if name.endswith(suffix))
        suffix_signal = min(1.0, len(suffix) / 7.0) / (1.0 + 0.38 * distractor_hits)
        best = max(best, suffix_signal)

    return max(0.0, min(1.0, best))


def _label_name_signal(pred, member_names, distractor_names):
    """Recognize when a group's label has an obvious cue in its member names."""
    label = _compact_name(pred.label)
    hints = []

    for key, values in CLASS_NAME_HINTS.items():
        if key in label:
            hints.extend(values)

    if pred.pid.startswith("el:"):
        element = pred.pid.split(":", 1)[1]
        hints.extend(ELEMENT_NAME_HINTS.get(element, ()))

    if not hints:
        return 0.0

    member_hits = sum(
        1 for name in member_names
        if any(hint in _compact_name(name) for hint in hints)
    )
    distractor_hits = sum(
        1 for name in distractor_names
        if any(hint in _compact_name(name) for hint in hints)
    )
    coverage = member_hits / 4.0
    if coverage < 0.5:
        return 0.0
    return min(1.0, coverage / (1.0 + 0.25 * distractor_hits))


def _difficulty_override(pred):
    """Read a small manual adjustment by predicate ID or human-readable label."""
    label_key = "label:" + _compact_name(pred.label)
    if pred.pid in DIFFICULTY_OVERRIDES:
        return DIFFICULTY_OVERRIDES[pred.pid]
    for key, value in DIFFICULTY_OVERRIDES.items():
        if key.startswith("label:") and _compact_name(key[6:]) == label_key[6:]:
            return value
    return 0.0


def difficulty_tier(score):
    """Return the fixed 0..3 tier for an absolute 0..100 difficulty score."""
    for tier, (low, high) in enumerate(DIFFICULTY_BANDS):
        if low <= score <= high:
            return tier
    raise ValueError(f"difficulty score out of range: {score}")


def board_group_difficulty_details(pred, member_ids, board_ids, chosen, lib, comps):
    """Return an auditable 0..100 difficulty score and its contributing factors.

    Important: only ambiguity visible on this 16-tile board counts. The old
    implementation counted other predicates matching two/three members across
    the *whole database*, even if they did not match any tile on this board;
    that could inflate difficulty for irrelevant reasons.
    """
    member_ids = set(member_ids)
    board_ids = set(board_ids)
    distractor_ids = board_ids - member_ids
    member_names = [comps[i]["name"] for i in sorted(member_ids)]
    distractor_names = [comps[i]["name"] for i in sorted(distractor_ids)]

    # Start with the type of knowledge the rule requires.
    base = FAMILY_DIFFICULTY_BASE.get(pred.family, 58)
    name_signal = max(
        _shared_name_signal(member_names, distractor_names),
        _label_name_signal(pred, member_names, distractor_names),
    )

    # A clue that is directly visible in names should become materially easier.
    # This handles DOx/barbiturate stems and elemental labels like "chlorine".
    visible_name_bonus = 25.0 * name_signal

    # Count only false-positive tiles actually present on this board.
    false_positives = len((pred.matches & board_ids) - member_ids)

    # Count only real 3-of-4 competing patterns which also include a distractor
    # from this board. Other predicates elsewhere in the database are irrelevant.
    near_miss_predicates = 0
    for other in lib:
        if other.pid == pred.pid:
            continue
        on_board = other.matches & board_ids
        if len(on_board & member_ids) == 3 and bool(on_board - member_ids):
            near_miss_predicates += 1

    # Keep ambiguity bounded so unrelated overlap statistics cannot dominate
    # the underlying clue difficulty.
    board_ambiguity_bonus = min(
        22.0,
        5.0 * false_positives + 3.0 * min(near_miss_predicates, 4),
    )

    manual_adjustment = _difficulty_override(pred)
    raw_score = base - visible_name_bonus + board_ambiguity_bonus + manual_adjustment
    score = int(round(max(25.0, min(100.0, raw_score))))
    return {
        "score": score,
        "raw_score": round(raw_score, 2),
        "knowledge_prior": base,
        "visible_name_reduction": round(visible_name_bonus, 1),
        "board_false_positive_tiles": false_positives,
        "board_near_miss_patterns": near_miss_predicates,
        "board_ambiguity_addition": round(board_ambiguity_bonus, 1),
        "manual_adjustment": manual_adjustment,
    }


def board_group_difficulty(pred, member_ids, board_ids, chosen, lib, comps):
    """Return only the absolute difficulty score (0..100)."""
    return board_group_difficulty_details(
        pred, member_ids, board_ids, chosen, lib, comps
    )["score"]


def difficulty_balance_cost(scores):
    """Cost against desired 25/50/75/100 levels; lower means better balanced."""
    ordered = sorted(scores)
    if len(ordered) != 4:
        return float("inf")
    target_cost = sum((score - target) ** 2 for score, target in zip(ordered, DIFFICULTY_TARGETS))
    gaps = [ordered[i + 1] - ordered[i] for i in range(3)]
    gap_cost = sum(max(0, MIN_DIFFICULTY_GAP - gap) ** 2 for gap in gaps)
    return target_cost + 4.0 * gap_cost


# ------------------------------------------------------------------- solver
def count_partitions(ids, lib, limit=2):
    """Count exact four-tile partitions, stopping at `limit`.

    Use 16-bit board masks and memoized recursion. The earlier frozenset
    recursion recomputed identical remainder states many times, becoming the
    main bottleneck during long multi-day generation runs.
    """
    ordered_ids = tuple(sorted(ids))
    if len(ordered_ids) % 4:
        return 0
    position = {compound_id: bit for bit, compound_id in enumerate(ordered_ids)}
    blocks_by_first = [set() for _ in ordered_ids]

    for pred in lib:
        matched = sorted(pred.matches.intersection(ordered_ids))
        if len(matched) < 4:
            continue
        for group in combinations(matched, 4):
            mask = 0
            positions = []
            for compound_id in group:
                bit = position[compound_id]
                positions.append(bit)
                mask |= 1 << bit
            for bit in positions:
                blocks_by_first[bit].add(mask)

    # Every block has four bits; deterministic ordering helps repeatability.
    blocks_by_first = [tuple(sorted(blocks)) for blocks in blocks_by_first]
    full_mask = (1 << len(ordered_ids)) - 1

    @lru_cache(maxsize=None)
    def solve(remaining):
        if remaining == 0:
            return 1
        first_bit_mask = remaining & -remaining
        first_bit = first_bit_mask.bit_length() - 1
        count = 0
        for block in blocks_by_first[first_bit]:
            if remaining & block == block:
                count += solve(remaining ^ block)
                if count >= limit:
                    return limit
        return count

    return solve(full_mask)


def count_near_misses(used, chosen, lib, min_size=3, max_size=6, *, group_mass=False):
    chosen_pids = {p.pid for p in chosen}
    chosen_sets = [p.matches & used for p in chosen]
    n = 0
    saw_mass = False
    for p in lib:
        if p.pid in chosen_pids:
            continue
        inter = p.matches & used
        if not (min_size <= len(inter) <= max_size):
            continue
        if any(inter <= cs for cs in chosen_sets):
            continue
        if group_mass and p.pid.startswith("mass:"):
            if saw_mass:
                continue
            saw_mass = True
        n += 1
    return n


def count_exact_four_decoys(used, picks, lib):
    intended = {frozenset(members) for members in picks.values()}
    return len({match for p in lib if len(match := p.matches & used) == 4} - intended)


def board_diagnostics(used, chosen, picks, lib, near_max_size):
    return {
        "near_miss_rules": count_near_misses(used, chosen, lib, 3, near_max_size),
        "near_miss_cap_count": count_near_misses(used, chosen, lib, 3, near_max_size, group_mass=True),
        "exact_four_decoys": count_exact_four_decoys(used, picks, lib),
        "selected_rule_overlap": [
            {"rules": [left.pid, right.pid], "shared": len(left.matches & right.matches),
             "support": [len(left.matches), len(right.matches)]}
            for left, right in combinations(chosen, 2)
        ],
    }


# ---------------------------------------------------------------- generator
def weighted_sample(rng, pool, k, weight):
    assert 0 <= k <= len(pool), "sample larger than pool"
    pool, out = list(pool), []
    for _ in range(k):
        pick = rng.choices(pool, weights=[weight(x) for x in pool])[0]
        out.append(pick)
        pool.remove(pick)
    return out


def make_puzzle(rng, comps, lib, comp_use, pred_use, seen, div_keys,
                min_herrings, max_family, max_near_misses, near_max_size,
                seen_predsets, svg_cache, min_props=2, tries=4000):
    if len(lib) < 4:
        raise SystemExit("Need at least four usable groups to generate a puzzle.")

    wc = lambda i: 1 / (1 + comp_use[i]) ** 2
    wp = lambda p: 1 / (1 + pred_use[p.pid]) ** 2.5

    # Stratify predicate selection by prior difficulty. This is what makes the
    # balancing affect puzzle composition, rather than merely recolouring four
    # arbitrary groups after they have already been selected.
    predicate_pools = []
    for low, high in DIFFICULTY_PRIOR_BANDS:
        pool = [p for p in lib
                if low <= (FAMILY_DIFFICULTY_BASE.get(p.family, 58)
                           + _difficulty_override(p)) <= high]
        if not pool:
            raise SystemExit(
                f"No predicates available for candidate-prior band {low}-{high}. "
                "Adjust FAMILY_DIFFICULTY_BASE / DIFFICULTY_PRIOR_BANDS or add predicates."
            )
        predicate_pools.append(pool)

    for _ in range(tries):
        chosen = [rng.choices(pool, weights=[wp(p) for p in pool])[0]
                  for pool in predicate_pools]
        predkey = frozenset(p.pid for p in chosen)
        if len(predkey) != 4 or predkey in seen_predsets:
            continue

        prop_count = sum(1 for p in chosen if p.family in PROPERTY_FAMILIES)
        if prop_count < min_props:
            continue

        best_candidate = None
        best_cost = float("inf")

        # For the same four rules, try multiple different 4-of-N member
        # selections. Their names and cross-matches can substantially change
        # the real solve experience, so tile selection is part of balancing.
        for _board_try in range(BOARD_VARIANTS_PER_PREDICATE_SET):
            used, picks, ok = set(), {}, True
            for p in sorted(chosen, key=lambda p: len(p.matches)):
                avail = [i for i in p.matches if i not in used]
                if len(avail) < 4:
                    ok = False
                    break

                others = [q for q in chosen if q is not p]
                decoys = [i for i in avail if any(i in q.matches for q in others)]
                pick = weighted_sample(rng, decoys, 1, wc) if decoys and rng.random() < 0.7 else []
                rest = [i for i in avail if i not in pick]
                pick += weighted_sample(rng, rest, 4 - len(pick), wc)
                used.update(pick)
                picks[p.pid] = pick

            if not ok or len(used) != 16:
                continue

            key = frozenset(used)
            if key in seen:
                continue
            if max(Counter(div_keys[i] for i in used).values()) > max_family:
                continue

            chosen_herrings = sum(
                1 for i in used if sum(i in q.matches for q in chosen) >= 2
            )
            if chosen_herrings < min_herrings:
                continue

            # Score first. Most candidate boards fail the balance requirement;
            # avoid the much more expensive partition solver for those boards.
            difficulty_details = {
                p.pid: board_group_difficulty_details(
                    p, picks[p.pid], used, chosen, lib, comps
                )
                for p in chosen
            }
            scores = {pid: details["score"]
                      for pid, details in difficulty_details.items()}
            tiers = {p.pid: difficulty_tier(scores[p.pid]) for p in chosen}

            # A playable balanced board MUST have one group in every fixed band.
            # This rejects combinations like 25/70/80/100 instead of simply
            # assigning those four scores to four different colours.
            if set(tiers.values()) != {0, 1, 2, 3}:
                continue

            ordered_scores = sorted(scores.values())
            gaps = [ordered_scores[i + 1] - ordered_scores[i] for i in range(3)]
            if min(gaps) < MIN_DIFFICULTY_GAP:
                continue

            cost = difficulty_balance_cost(ordered_scores)
            if cost >= best_cost:
                continue

            # Run gameplay-integrity checks only after a board has passed
            # the difficulty-balance test.
            if count_near_misses(used, chosen, lib, 3, near_max_size, group_mass=True) > max_near_misses:
                continue
            if count_partitions(used, lib) != 1:
                continue
            if count_exact_four_decoys(used, picks, lib) > MAX_EXACT_FOUR_DECOYS:
                continue

            best_cost = cost
            best_candidate = (used.copy(), {k: list(v) for k, v in picks.items()},
                              dict(scores), dict(difficulty_details), dict(tiers),
                              chosen_herrings)

            # A near-perfect anchor match needs no further random variants.
            if best_cost <= 80:
                break

        if best_candidate is None:
            continue

        used, picks, scores, difficulty_details, tiers, chosen_herrings = best_candidate
        key = frozenset(used)
        seen.add(key)
        seen_predsets.add(predkey)
        for p in chosen:
            pred_use[p.pid] += 1
        for i in used:
            comp_use[i] += 1

        board = [comps[i]["name"] for i in used]
        rng.shuffle(board)

        # Render ONLY visual skeletal structure SVGs (never expose written formulas).
        svgs = {}
        for i in used:
            cname = comps[i]["name"]
            if cname not in svg_cache:
                svg_cache[cname] = render_skeletal_svg(comps[i]["smiles"])
            if svg_cache[cname]:
                svgs[cname] = svg_cache[cname]

        groups = []
        for p in sorted(chosen, key=lambda p: tiers[p.pid]):
            groups.append({
                "rule_id": p.pid,
                "tier": tiers[p.pid],
                "label": p.label,
                "members": [comps[i]["name"] for i in picks[p.pid]],
                "difficulty_score": scores[p.pid],
                "difficulty_factors": difficulty_details[p.pid],
            })

        return {"groups": groups, "board": board, "svgs": svgs,
                "herrings": chosen_herrings,
                "diagnostics": board_diagnostics(used, chosen, picks, lib, near_max_size)}

    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=CATALOGUE)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--start", default=dt.date.today().isoformat())
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--cap", type=float, default=0.28,
                    help="max share of the dataset one group may cover")
    ap.add_argument("--min-herrings", type=int, default=2)
    ap.add_argument("--min-props", type=int, default=2,
                    help="minimum property-style groups (origin/formula/name) per board")
    ap.add_argument("--max-family", type=int, default=5,
                    help="max compounds sharing a diversity key on one board")
    ap.add_argument("--max-near-misses", type=int, default=4,
                    help="reject puzzles with more near-miss predicates than this")
    ap.add_argument("--near-max-size", type=int, default=6,
                    help="predicates matching more than this many board compounds "
                         "are treated as 'too broad to tempt' and ignored")
    ap.add_argument("--smiles-cache", default=SMILES,
                    help="JSON cache mapping compound names to SMILES for SVG rendering")
    ap.add_argument("--fetch-smiles", action="store_true",
                    help="fetch missing SMILES from PubChem/CACTUS into --smiles-cache")
    ap.add_argument("--smiles-log", default=CACHE / "smiles_fetch.log",
                    help="detailed timestamped SMILES lookup log")
    ap.add_argument("--smiles-failures", default=CACHE / "smiles_failures.csv",
                    help="CSV report of names whose SMILES lookup failed")
    ap.add_argument("--smiles-timeout", type=float, default=15,
                    help="per-request timeout in seconds (default: 15)")
    ap.add_argument("--smiles-retries", type=int, default=2,
                    help="retries for transient network/server errors (default: 2)")
    ap.add_argument("--smiles-delay", type=float, default=0.25,
                    help="delay between compound lookups in seconds (default: 0.25)")
    ap.add_argument("--out", default=PUZZLES)
    a = ap.parse_args()

    comps, warnings = load(a.data)
    for w in warnings:
        print("warning:", w)
    resolve_smiles_map(
        comps, a.smiles_cache, fetch_missing=a.fetch_smiles,
        timeout=a.smiles_timeout, retries=a.smiles_retries, delay=a.smiles_delay,
        log_path=a.smiles_log, failures_path=a.smiles_failures,
    )
    smi_count = sum(1 for c in comps if c["smiles"])
    if not HAS_RDKIT:
        print("note: rdkit not installed; skeletal SVGs will be omitted unless rendered elsewhere")
    else:
        print(f"  SMILES available for {smi_count}/{len(comps)} compounds")

    lib = build(comps, a.cap)
    trivial = sum(1 for p in lib if p.family == "name:trivial")
    specific = sum(1 for p in lib if p.family == "name:specific")
    print(f"{len(comps)} compounds, {len(lib)} usable group predicates")
    print(f"  name predicates: {trivial} trivial, {specific} specific")
    print("  difficulty balance enforced: one group in each 25/50/75/100 band")

    rng = random.Random(a.seed)
    comp_use, pred_use, seen = Counter(), Counter(), set()
    seen_predsets = set()
    svg_cache = {}
    div_keys = [diversity_key(c) for c in comps]
    start = dt.date.fromisoformat(a.start)
    puzzles = []
    for d in range(a.days):
        p = None
        for relax in range(4):
            p = make_puzzle(rng, comps, lib, comp_use, pred_use, seen, div_keys,
                            max(0, a.min_herrings - relax),
                            a.max_family + relax,
                            a.max_near_misses + 2 * relax,
                            a.near_max_size,
                            seen_predsets,
                            svg_cache,
                            min_props=max(0, a.min_props - (1 if relax >= 2 else 0)))
            if p is not None:
                break
        if p is None:
            print(f"  could not build a puzzle for day {d + 1}; add compounds or "
                  f"lower --min-herrings / raise --max-family / --max-near-misses")
            break
        p["date"] = (start + dt.timedelta(days=d)).isoformat()
        p["number"] = d + 1
        p["diagnostics"]["relaxation_level"] = relax
        puzzles.append(p)
        score_text = ", ".join(str(g["difficulty_score"]) for g in p["groups"])
        print(f"[PUZZLE {d + 1:>3}/{a.days}] {p['date']}: difficulty {score_text}", flush=True)

    output = Path(a.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(puzzles, ensure_ascii=False, indent=1), encoding="utf-8")
    avg = sum(p["herrings"] for p in puzzles) / max(1, len(puzzles))
    print(f"wrote {len(puzzles)} puzzles to {a.out} (avg {avg:.1f} red herrings)")


if __name__ == "__main__":
    main()
