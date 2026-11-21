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


def create_attempt(connection, attempt_id, number, day, version, state):
    connection.execute("""
        INSERT INTO attempts (id, number, day, version, state)
        VALUES (%s, %s, %s, %s, %s)
    """, (attempt_id, number, day, version, Jsonb(state)))


def read_attempt(connection, attempt_id, *, lock=False):
    query = """
        SELECT attempts.state, versions.record
        FROM attempts
        JOIN puzzle_versions AS versions USING (number, day, version)
        WHERE attempts.id = %s
    """
    if lock:
        query += " FOR UPDATE OF attempts"
    return connection.execute(query, (attempt_id,)).fetchone()


def write_attempt(connection, attempt_id, state):
    connection.execute(
        "UPDATE attempts SET state = %s WHERE id = %s", (Jsonb(state), attempt_id)
    )
