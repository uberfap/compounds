"""Publish every puzzle in a generated batch in one database transaction."""

import argparse
import json
from pathlib import Path

from backend import database
from backend.app import BATCH, Puzzle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", nargs="?", type=Path, default=BATCH)
    args = parser.parse_args()

    records = json.loads(args.batch.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        parser.error("Expected a nonempty puzzle array")

    puzzles = [Puzzle.from_record(record) for record in records]
    rows = [(puzzle.number, puzzle.date, puzzle.version, record)
            for puzzle, record in zip(puzzles, records, strict=True)]
    with database.connect() as connection:
        database.publish(connection, rows)
    print(f"Published {len(rows)} puzzles.")


if __name__ == "__main__":
    main()
