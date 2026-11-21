"""PostgreSQL access for publishing and gameplay."""

import os

import psycopg
from psycopg.types.json import Jsonb


def database_url():
    url = os.environ.get("COMPOUNDS_DATABASE_URL")
    if not url:
        raise RuntimeError("Set COMPOUNDS_DATABASE_URL to a PostgreSQL connection URL")
    return url


def connect():
    return psycopg.connect(database_url())


def publish(connection, records):
    connection.execute("LOCK TABLE published_puzzles IN EXCLUSIVE MODE")
    with connection.cursor() as cursor:
        cursor.executemany("""
            INSERT INTO puzzle_versions (number, day, version, record)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, [(number, day, version, Jsonb(record)) for number, day, version, record in records])
        cursor.execute("DELETE FROM published_puzzles")
        cursor.executemany("""
            INSERT INTO published_puzzles (number, day, version)
            VALUES (%s, %s, %s)
        """, [(number, day, version) for number, day, version, _ in records])


def published_board(connection, number):
    row = connection.execute("""
        SELECT versions.record
        FROM published_puzzles AS published
        JOIN puzzle_versions AS versions USING (number, day, version)
        WHERE published.number = %s
    """, (number,)).fetchone()
    return row[0] if row else None


def daily_board(connection, day):
    row = connection.execute("""
        SELECT versions.record
        FROM published_puzzles AS published
        JOIN puzzle_versions AS versions USING (number, day, version)
        WHERE published.day = (
            SELECT GREATEST(min(day), LEAST(max(day), %s::date))
            FROM published_puzzles
        )
    """, (day,)).fetchone()
    return row[0] if row else None


def puzzle_range(connection):
    return connection.execute(
        "SELECT min(number), max(number) FROM published_puzzles"
    ).fetchone()


def player_puzzles(connection, before_day, player_id, max_mistakes):
    return connection.execute("""
        SELECT published.number, published.day, published.version,
               CASE
                   WHEN jsonb_array_length(attempts.state->'solved') = 4 THEN 'solved'
                   WHEN (attempts.state->>'mistakes')::integer >= %s THEN 'missed'
                   WHEN jsonb_array_length(attempts.state->'guesses') > 0
                        OR jsonb_array_length(attempts.state->'inspected') > 0 THEN 'in_progress'
                   ELSE 'unplayed'
               END AS status,
               (attempts.state->>'mistakes')::integer,
               (attempts.finished_at AT TIME ZONE 'UTC')::date
        FROM published_puzzles AS published
        LEFT JOIN attempts
               ON (attempts.number, attempts.day, attempts.version) =
                  (published.number, published.day, published.version)
              AND attempts.player_id = %s
        WHERE published.day < %s
        ORDER BY published.day DESC
    """, (max_mistakes, player_id, before_day)).fetchall()


def resume_or_create(connection, attempt, player_id):
    puzzle = attempt.puzzle
    identity = (puzzle.number, puzzle.date, puzzle.version)
    row = connection.execute(
        """
        INSERT INTO attempts (id, number, day, version, state, player_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (player_id, number, day, version) DO NOTHING
        RETURNING id, state
        """, (attempt.id, *identity, Jsonb(attempt.stored_state()), player_id)
    ).fetchone()
    if row is not None:
        return row
    return connection.execute("""
        SELECT id, state FROM attempts
        WHERE player_id = %s AND number = %s AND day = %s AND version = %s
    """, (player_id, *identity)).fetchone()


def puzzle_metrics(connection, puzzle):
    args = (puzzle.number, puzzle.date, puzzle.version)
    totals = connection.execute("""
        WITH games AS (
            SELECT state, jsonb_array_length(state->'solved') = 4 AS won,
                   (state->>'mistakes')::integer AS mistakes
            FROM attempts
            WHERE number = %s AND day = %s AND version = %s
              AND jsonb_array_length(state->'guesses') > 0
        )
        SELECT count(*), count(*) FILTER (WHERE won OR mistakes >= 4),
               count(*) FILTER (WHERE won), avg(mistakes) FILTER (WHERE won OR mistakes >= 4),
               avg(jsonb_array_length(state->'inspected')) FILTER (WHERE won OR mistakes >= 4),
               count(*) FILTER (WHERE (won OR mistakes >= 4) AND state->'solved' @> '[0]'),
               count(*) FILTER (WHERE (won OR mistakes >= 4) AND state->'solved' @> '[1]'),
               count(*) FILTER (WHERE (won OR mistakes >= 4) AND state->'solved' @> '[2]'),
               count(*) FILTER (WHERE (won OR mistakes >= 4) AND state->'solved' @> '[3]')
        FROM games
    """, args).fetchone()
    leaders = connection.execute("""
        SELECT id, (state->>'mistakes')::integer AS mistakes,
               jsonb_array_length(state->'inspected') AS inspections
        FROM attempts
        WHERE number = %s AND day = %s AND version = %s
          AND finished_at IS NOT NULL AND player_id IS NOT NULL
          AND jsonb_array_length(state->'solved') = 4
        ORDER BY mistakes, inspections, finished_at, id
        LIMIT 10
    """, args).fetchall()
    return totals, leaders


def read_attempt(connection, attempt_id, *, lock=False):
    query = """
        SELECT attempts.state, versions.record, attempts.player_id
        FROM attempts
        JOIN puzzle_versions AS versions USING (number, day, version)
        WHERE attempts.id = %s
    """
    if lock:
        query += " FOR UPDATE OF attempts"
    return connection.execute(query, (attempt_id,)).fetchone()


def write_attempt(connection, attempt_id, state, *, over):
    connection.execute(
        """UPDATE attempts SET state = %s,
           finished_at = CASE WHEN %s THEN COALESCE(finished_at, now()) ELSE finished_at END
           WHERE id = %s""", (Jsonb(state), over, attempt_id)
    )
