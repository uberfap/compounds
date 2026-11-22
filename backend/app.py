"""HTTP API for the published Compounds puzzle batch."""

import csv
import hashlib
import json
import os
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import auth, database

BATCH = Path(__file__).resolve().parents[1] / "data" / "puzzles" / "puzzles.json"
MAX_MISTAKES = 4
MAX_INSPECTIONS = 4
CATALOGUE = BATCH.parents[1] / "catalogue" / "compounds.csv"


@lru_cache(maxsize=1)
def catalogue_metadata():
    names = {}
    count = 0
    with CATALOGUE.open(encoding="utf-8", newline="") as source:
        rows = csv.DictReader((line for line in source if not line.startswith("#") and line.strip()), delimiter="|")
        for row in rows:
            count += 1
            aliases = [name.strip() for name in (row.get("aliases") or "").split(";") if name.strip()]
            if aliases:
                group = (row["name"], *aliases)
                for name in group:
                    names[name] = tuple(other for other in group if other != name)
    return count, names


def catalogue_synonyms():
    return catalogue_metadata()[1]


def player_label(attempt_id: UUID):
    return "Player " + hashlib.sha256(attempt_id.bytes).hexdigest()[:10]


class StartAttempt(BaseModel):
    version: str
    player_id: UUID | None = None


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
                {
                    "id": tile, "name": name, "has_structure": name in self.structures,
                    "has_synonyms": name in catalogue_synonyms(),
                }
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
    synonym_inspected: set[int] = field(default_factory=set)
    guesses: dict[tuple[int, ...], list[int]] = field(default_factory=dict)

    @classmethod
    def from_state(cls, puzzle, attempt_id, state):
        return cls(
            puzzle=puzzle,
            id=attempt_id,
            mistakes=state["mistakes"],
            solved=list(state["solved"]),
            inspected=set(state["inspected"]),
            synonym_inspected=set(state.get("synonym_inspected", [])),
            guesses={tuple(tiles): tiers for tiles, tiers in state["guesses"]},
        )

    def stored_state(self):
        return {
            "mistakes": self.mistakes,
            "solved": self.solved,
            "inspected": sorted(self.inspected),
            "synonym_inspected": sorted(self.synonym_inspected),
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
            "player": player_label(self.id),
            "puzzle_version": self.puzzle.version,
            "mistakes": self.mistakes,
            "mistakes_left": MAX_MISTAKES - self.mistakes,
            "inspections_left": MAX_INSPECTIONS - len(self.inspected),
            "inspected": sorted(self.inspected),
            "synonym_inspected": sorted(self.synonym_inspected),
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
app.include_router(auth.router)


def debug_allowed(request: Request):
    return (
        os.environ.get("COMPOUNDS_LOCAL_DEBUG") == "1"
        and request.url.hostname in {"localhost", "127.0.0.1", "::1"}
        and request.client is not None
        and request.client.host in {"127.0.0.1", "::1"}
    )


def release_day():
    return datetime.now(timezone.utc).date()


def get_puzzle(connection, number: int, request: Request) -> Puzzle:
    record = database.published_board(connection, number)
    if record is None:
        raise HTTPException(404, "Puzzle not found")
    puzzle = Puzzle.from_record(record)
    if puzzle.date > release_day().isoformat() and not debug_allowed(request):
        raise HTTPException(404, "Puzzle not found")
    return puzzle


@contextmanager
def attempt_transaction(attempt_id: UUID, request: Request, *, write=False):
    with database.connect() as connection:
        row = database.read_attempt(connection, attempt_id, lock=write)
        if row is None:
            raise HTTPException(404, "Attempt not found")
        state, record, owner = row
        account = auth.session_account(connection, request)
        if account:
            if owner != account[0]:
                raise HTTPException(404, "Attempt not found")
        elif owner and (auth.account_player(connection, owner) or auth.linked_guest(connection, owner)):
            raise HTTPException(404, "Attempt not found")
        puzzle = Puzzle.from_record(record)
        if puzzle.date > release_day().isoformat() and not debug_allowed(request):
            raise HTTPException(404, "Attempt not found")
        attempt = Attempt.from_state(puzzle, attempt_id, state)
        yield attempt
        if write:
            updated = attempt.stored_state()
            if updated != state:
                database.write_attempt(connection, attempt_id, updated, over=attempt.over)


@app.get("/api/puzzles/daily")
def daily_board(day: date, request: Request):
    if not debug_allowed(request):
        day = min(day, release_day())
    with database.connect() as connection:
        record = database.daily_board(connection, day)
    if record is None:
        raise HTTPException(404, "No puzzle published for this date")
    puzzle = Puzzle.from_record(record)
    if puzzle.date > release_day().isoformat() and not debug_allowed(request):
        raise HTTPException(404, "No puzzle published for this date")
    return puzzle.public_board()


@app.get("/api/catalogue")
def catalogue_summary():
    return {"substance_count": catalogue_metadata()[0]}


@app.get("/api/health")
def health():
    with database.connect() as connection:
        first, _ = database.puzzle_range(connection)
    if first is None:
        raise HTTPException(503, "No puzzles published")
    return {"status": "ok"}


@app.get("/api/puzzles/range")
def puzzle_range(request: Request):
    if not debug_allowed(request):
        raise HTTPException(404, "Puzzle navigation is unavailable")
    with database.connect() as connection:
        first, last = database.puzzle_range(connection)
    return {"first": first, "last": last}


@app.post("/api/puzzles/archive")
def archive(request: Request, player_id: UUID | None = Body(default=None, embed=True)):
    with database.connect() as connection:
        player = auth.effective_player(connection, request, player_id)
        rows = database.player_puzzles(connection, release_day(), player, MAX_MISTAKES)
    return [
        {"number": number, "date": day, "version": version, "status": status}
        for number, day, version, status, _, _ in rows
    ]


def summarize_player(rows, today):
    counts = dict.fromkeys(("solved", "missed", "in_progress", "unplayed"), 0)
    distribution = [0] * MAX_MISTAKES
    best_streak = streak = 0
    previous_solve = None
    one_day = timedelta(days=1)
    for _, day, _, status, mistakes, finished_day in reversed(rows):
        counts[status] += 1
        if status != "solved":
            continue
        distribution[mistakes] += 1
        if finished_day != day:
            continue
        streak = streak + 1 if previous_solve == day - one_day else 1
        best_streak = max(best_streak, streak)
        previous_solve = day

    finished = counts["solved"] + counts["missed"]
    today_missed = bool(rows and rows[0][1] == today and rows[0][3] == "missed")
    current_streak = streak if not today_missed and previous_solve in {today, today - one_day} else 0
    return {
        "played": finished + counts["in_progress"],
        "finished": finished,
        "solved": counts["solved"],
        "missed": counts["missed"],
        "in_progress": counts["in_progress"],
        "current_streak": current_streak,
        "best_streak": best_streak,
        "mistake_distribution": distribution,
        "recent": [
            {"number": number, "date": day, "status": status}
            for number, day, _, status, _, _ in rows[:30]
        ],
    }


@app.post("/api/player/stats")
def player_stats(request: Request, response: Response, player_id: UUID | None = Body(default=None, embed=True)):
    today = release_day()
    with database.connect() as connection:
        player = auth.effective_player(connection, request, player_id)
        rows = database.player_puzzles(connection, today + timedelta(days=1), player, MAX_MISTAKES)
    response.headers["Cache-Control"] = "no-store"
    return summarize_player(rows, today)


@app.get("/api/puzzles/{number}")
def board(number: int, request: Request):
    with database.connect() as connection:
        return get_puzzle(connection, number, request).public_board()


@app.get("/api/puzzles/{number}/stats")
def puzzle_stats(number: int, request: Request):
    with database.connect() as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        puzzle = get_puzzle(connection, number, request)
        totals, leaders = database.puzzle_metrics(connection, puzzle)
    played, finished, won, mistakes, inspections, *solved_by_tier = totals
    return {
        "played": played,
        "finished": finished,
        "won": won,
        "mean_mistakes": mistakes,
        "mean_inspections": inspections,
        "solved_by_tier": solved_by_tier,
        "leaderboard": [
            {
                "player": player_label(attempt_id),
                "mistakes": misses,
                "inspections": hints,
            }
            for attempt_id, misses, hints in leaders
        ],
    }


@app.post("/api/puzzles/{number}/attempts", status_code=201)
def start_attempt(number: int, body: StartAttempt, request: Request):
    with database.connect() as connection:
        puzzle = get_puzzle(connection, number, request)
        if body.version != puzzle.version:
            raise HTTPException(409, "Puzzle version changed")
        attempt = Attempt(puzzle)
        player = auth.effective_player(connection, request, body.player_id, write=True)
        attempt_id, state = database.resume_or_create(connection, attempt, player)
        return Attempt.from_state(puzzle, attempt_id, state).public_state()


@app.get("/api/attempts/{attempt_id}")
def attempt_state(attempt_id: UUID, request: Request):
    with attempt_transaction(attempt_id, request) as attempt:
        return attempt.public_state()


@app.post("/api/attempts/{attempt_id}/guesses")
def submit_guess(attempt_id: UUID, body: Guess, request: Request):
    key = tuple(sorted(set(body.tiles)))
    if len(key) != 4:
        raise HTTPException(422, "Choose four distinct tiles from this puzzle")

    with attempt_transaction(attempt_id, request, write=True) as attempt:
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
def inspect_structure(attempt_id: UUID, tile: int, request: Request):
    with attempt_transaction(attempt_id, request, write=True) as attempt:
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
        # Older batches applied the bond rule to filled atom glyphs as well.
        svg = svg.replace("<style>path{fill:none;stroke:currentColor;",
                          "<style>path:not([fill]){fill:none;stroke:currentColor;", 1)
        return {"tile": tile, "svg": svg, "state": attempt.public_state()}


@app.post("/api/attempts/{attempt_id}/synonyms/{tile}")
def inspect_synonyms(attempt_id: UUID, tile: int, request: Request):
    with attempt_transaction(attempt_id, request, write=True) as attempt:
        name = attempt.puzzle.tiles.get(tile)
        synonyms = catalogue_synonyms().get(name)
        if not synonyms:
            raise HTTPException(404, "Synonyms not found")
        if attempt.over:
            raise HTTPException(409, "Attempt is over")
        if any(tile in attempt.puzzle.groups[tier]["tiles"] for tier in attempt.solved):
            raise HTTPException(422, "Choose an unsolved tile")
        attempt.synonym_inspected.add(tile)
        return {"tile": tile, "synonyms": synonyms, "state": attempt.public_state()}


frontend_build = BATCH.parents[2] / "frontend" / "dist"
if (frontend_build / "index.html").is_file():
    app.mount("/", StaticFiles(directory=frontend_build, html=True), name="frontend")
