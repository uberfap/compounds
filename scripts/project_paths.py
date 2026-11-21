"""Repository paths shared by the generation and maintenance commands."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "data" / "catalogue" / "compounds.csv"
SMILES = CATALOGUE.with_name("smiles_cache.json")
PUZZLES = ROOT / "data" / "puzzles" / "puzzles.json"
REVIEW = ROOT / "data" / "review"
CACHE = ROOT / "cache"
PUBCHEM_CACHE = CACHE / "pubchem.json"
