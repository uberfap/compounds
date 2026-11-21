"""Store immutable puzzle versions and anonymous attempts."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE puzzle_versions (
            number integer NOT NULL,
            day date NOT NULL,
            version text NOT NULL,
            record jsonb NOT NULL,
            PRIMARY KEY (number, day, version)
        )
    """)
    op.execute("""
        CREATE TABLE published_puzzles (
            number integer PRIMARY KEY,
            day date NOT NULL UNIQUE,
            version text NOT NULL,
            FOREIGN KEY (number, day, version)
                REFERENCES puzzle_versions (number, day, version)
        )
    """)
    op.execute("""
        CREATE TABLE attempts (
            id uuid PRIMARY KEY,
            number integer NOT NULL,
            day date NOT NULL,
            version text NOT NULL,
            state jsonb NOT NULL,
            FOREIGN KEY (number, day, version)
                REFERENCES puzzle_versions (number, day, version)
        )
    """)


def downgrade():
    op.drop_table("attempts")
    op.drop_table("published_puzzles")
    op.drop_table("puzzle_versions")
