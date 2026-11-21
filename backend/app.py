"""HTTP API for the published Compounds puzzle batch."""

import hashlib
import json
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import database

BATCH = Path(__file__).resolve().parents[1] / "data" / "puzzles" / "puzzles.json"
MAX_MISTAKES = 4
MAX_INSPECTIONS = 4


class StartAttempt(BaseModel):
    version: str


class Guess(BaseModel):
    tiles: list[int] = Field(min_length=4, max_length=4)


@dataclass
class Puzzle:
    number: int
    date: str
    version: str
    tiles: dict[int, str]
    groups: list[dict]
    structures: dict[str, str]

    @classmethod
    def from_record(cls, record):
        tiles = dict(enumerate(record["board"], start=1))
        ids = {name: tile for tile, name in tiles.items()}
        groups = [
            {
                "tier": group["tier"],
                "label": group["label"],
                "tiles": [ids[name] for name in group["members"]],
            }
            for group in sorted(record["groups"], key=lambda group: group["tier"])
        ]
        structures = record.get("svgs", {})
        content = json.dumps([tiles, groups, structures], sort_keys=True).encode()
        version = hashlib.sha256(content).hexdigest()
        return cls(record["number"], record["date"], version, tiles, groups, structures)

    def public_board(self):
        return {
            "number": self.number,
            "date": self.date,
            "version": self.version,
            "tiles": [
                {"id": tile, "name": name, "has_structure": name in self.structures}
                for tile, name in self.tiles.items()
            ],
        }


@dataclass
class Attempt:
    puzzle: Puzzle
    id: UUID = field(default_factory=uuid4)
    mistakes: int = 0
    solved: list[int] = field(default_factory=list)
    inspected: set[int] = field(default_factory=set)
    guesses: dict[tuple[int, ...], list[int]] = field(default_factory=dict)

    @classmethod
    def from_state(cls, puzzle, attempt_id, state):
        return cls(
            puzzle=puzzle,
            id=attempt_id,
            mistakes=state["mistakes"],
            solved=list(state["solved"]),
            inspected=set(state["inspected"]),
            guesses={tuple(tiles): tiers for tiles, tiers in state["guesses"]},
        )

    def stored_state(self):
        return {
            "mistakes": self.mistakes,
            "solved": self.solved,
            "inspected": sorted(self.inspected),
            "guesses": [[list(tiles), tiers] for tiles, tiers in self.guesses.items()],
        }

    @property
    def won(self):
        return len(self.solved) == 4

    @property
    def over(self):
        return self.won or self.mistakes >= MAX_MISTAKES

    def public_state(self):
        groups = self.puzzle.groups
        revealed = list(self.solved)
        if self.over:
            revealed.extend(tier for tier in range(4) if tier not in revealed)
        return {
            "id": str(self.id),
            "puzzle_version": self.puzzle.version,
            "mistakes": self.mistakes,
            "mistakes_left": MAX_MISTAKES - self.mistakes,
            "inspections_left": MAX_INSPECTIONS - len(self.inspected),
            "inspected": sorted(self.inspected),
            "solved": [groups[tier] for tier in self.solved],
            "revealed": [groups[tier] for tier in revealed],
            "over": self.over,
            "won": self.won,
            # Preserve the original share pattern without exposing tier mappings
            # for incorrect guesses during play.
            "share_rows": list(self.guesses.values()) if self.over else [],
        }


@asynccontextmanager
async def lifespan(app: FastAPI):
    with database.connect() as connection:
        first, _ = database.puzzle_range(connection)
    if first is None:
        raise RuntimeError("Publish a puzzle batch before starting the API")
    yield


app = FastAPI(title="Compounds", lifespan=lifespan)


def get_puzzle(connection, number: int) -> Puzzle:
    record = database.published_board(connection, number)
    if record is None:
        raise HTTPException(404, "Puzzle not found")
    return Puzzle.from_record(record)


@contextmanager
def attempt_transaction(attempt_id: UUID, *, write=False):
    with database.connect() as connection:
        row = database.read_attempt(connection, attempt_id, lock=write)
        if row is None:
            raise HTTPException(404, "Attempt not found")
        state, record = row
        attempt = Attempt.from_state(Puzzle.from_record(record), attempt_id, state)
        yield attempt
        if write:
            updated = attempt.stored_state()
            if updated != state:
                database.write_attempt(connection, attempt_id, updated)


@app.get("/api/puzzles/daily")
def daily_board(day: date):
    with database.connect() as connection:
        record = database.daily_board(connection, day)
    if record is None:
        raise HTTPException(404, "No puzzle published for this date")
    return Puzzle.from_record(record).public_board()


@app.get("/api/puzzles/range")
def puzzle_range():
    with database.connect() as connection:
        first, last = database.puzzle_range(connection)
    return {"first": first, "last": last}


@app.get("/api/puzzles/{number}")
def board(number: int):
    with database.connect() as connection:
        return get_puzzle(connection, number).public_board()


@app.post("/api/puzzles/{number}/attempts", status_code=201)
def start_attempt(number: int, body: StartAttempt):
    with database.connect() as connection:
        puzzle = get_puzzle(connection, number)
        if body.version != puzzle.version:
            raise HTTPException(409, "Puzzle version changed")
        attempt = Attempt(puzzle)
        database.create_attempt(
            connection, attempt.id, puzzle.number, puzzle.date,
            puzzle.version, attempt.stored_state(),
        )
        return attempt.public_state()


@app.get("/api/attempts/{attempt_id}")
def attempt_state(attempt_id: UUID):
    with attempt_transaction(attempt_id) as attempt:
        return attempt.public_state()


@app.post("/api/attempts/{attempt_id}/guesses")
def submit_guess(attempt_id: UUID, body: Guess):
    key = tuple(sorted(set(body.tiles)))
    if len(key) != 4:
        raise HTTPException(422, "Choose four distinct tiles from this puzzle")

    with attempt_transaction(attempt_id, write=True) as attempt:
        if any(tile not in attempt.puzzle.tiles for tile in key):
            raise HTTPException(422, "Choose four distinct tiles from this puzzle")
        if key in attempt.guesses:
            return {"result": "duplicate", "state": attempt.public_state()}
        if attempt.over:
            raise HTTPException(409, "Attempt is over")

        selected = set(key)
        matches = [
            len(selected.intersection(group["tiles"])) for group in attempt.puzzle.groups
        ]
        if any(matches[tier] for tier in attempt.solved):
            raise HTTPException(422, "Choose only unsolved tiles")

        attempt.guesses[key] = [
            tier for tier, count in enumerate(matches) for _ in range(count)
        ]
        best = max(matches)
        if best == 4:
            attempt.solved.append(matches.index(4))
            result = "correct"
        else:
            attempt.mistakes += 1
            result = "one_away" if best == 3 else "incorrect"
        return {"result": result, "state": attempt.public_state()}


@app.post("/api/attempts/{attempt_id}/structures/{tile}")
def inspect_structure(attempt_id: UUID, tile: int):
    with attempt_transaction(attempt_id, write=True) as attempt:
        name = attempt.puzzle.tiles.get(tile)
        svg = attempt.puzzle.structures.get(name)
        if not svg:
            raise HTTPException(404, "Structure not found")
        if attempt.over:
            raise HTTPException(409, "Attempt is over")
        if any(tile in attempt.puzzle.groups[tier]["tiles"] for tier in attempt.solved):
            raise HTTPException(422, "Choose an unsolved tile")
        if tile not in attempt.inspected and len(attempt.inspected) >= MAX_INSPECTIONS:
            raise HTTPException(409, "No structure checks left")
        attempt.inspected.add(tile)
        return {"tile": tile, "svg": svg, "state": attempt.public_state()}


frontend_build = BATCH.parents[2] / "frontend" / "dist"
if (frontend_build / "index.html").is_file():
    app.mount("/", StaticFiles(directory=frontend_build, html=True), name="frontend")
