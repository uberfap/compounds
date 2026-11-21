"""Store Discord accounts, one-use sign-in flows, and sessions."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE discord_accounts (
            player_id uuid PRIMARY KEY,
            discord_id text NOT NULL UNIQUE,
            display_name text NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE discord_flows (
            token_hash text PRIMARY KEY,
            verifier text NOT NULL,
            guest_id uuid,
            expires_at timestamptz NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE discord_sessions (
            token_hash text PRIMARY KEY,
            player_id uuid NOT NULL REFERENCES discord_accounts ON DELETE CASCADE,
            expires_at timestamptz NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE linked_guests (
            guest_id uuid PRIMARY KEY,
            player_id uuid NOT NULL REFERENCES discord_accounts ON DELETE CASCADE
        )
    """)


def downgrade():
    op.drop_table("linked_guests")
    op.drop_table("discord_sessions")
    op.drop_table("discord_flows")
    op.drop_table("discord_accounts")
