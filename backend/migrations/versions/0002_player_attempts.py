"""Identify browser attempts and record their completion."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE attempts
        ADD COLUMN player_id uuid,
        ADD COLUMN finished_at timestamptz,
        ADD CONSTRAINT one_attempt_per_player UNIQUE (player_id, number, day, version)
    """)
    op.execute("CREATE INDEX attempts_by_puzzle ON attempts (number, day, version)")


def downgrade():
    op.drop_index("attempts_by_puzzle")
    op.execute("""
        ALTER TABLE attempts DROP CONSTRAINT one_attempt_per_player,
        DROP COLUMN finished_at, DROP COLUMN player_id
    """)
