#!/usr/bin/env python3
"""Safely merge manually verified SMILES into an existing generator cache."""
import argparse
import json
import shutil
from pathlib import Path

from project_paths import REVIEW, SMILES

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=SMILES)
    ap.add_argument("--additions", default=REVIEW / "smiles_cache_additions.json")
    args = ap.parse_args()

    cache_path = Path(args.cache)
    additions_path = Path(args.additions)

    if not additions_path.exists():
        raise SystemExit(f"Additions file not found: {additions_path}")

    additions = json.loads(additions_path.read_text(encoding="utf-8"))
    if not isinstance(additions, dict):
        raise SystemExit("Additions JSON must be an object mapping names to SMILES.")

    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SystemExit(f"Could not read {cache_path}: {exc}")
        if not isinstance(cache, dict):
            raise SystemExit(f"{cache_path} must contain a JSON object.")
    else:
        cache = {}

    inserted = 0
    preserved = 0
    conflicts = []

    for name, smiles in additions.items():
        if not isinstance(name, str) or not isinstance(smiles, str) or not smiles.strip():
            continue
        current = cache.get(name)
        if current:
            if current == smiles:
                preserved += 1
            else:
                conflicts.append(name)
                preserved += 1
            continue
        cache[name] = smiles
        inserted += 1

    if cache_path.exists():
        backup = cache_path.with_suffix(cache_path.suffix + ".bak")
        shutil.copy2(cache_path, backup)
        print(f"Backup: {backup}")

    cache_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"Inserted: {inserted}; preserved existing: {preserved}; conflicts: {len(conflicts)}")
    if conflicts:
        print("Existing non-empty entries were NOT overwritten:")
        for name in conflicts:
            print(f"  - {name}")

if __name__ == "__main__":
    main()
