"""Initial schema: calls, stage runs, transcripts, versioned QA results, outbox.

Revision ID: 0001
Revises:
Create Date: 2026-10-26
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _ts(name: str, *, default: bool = False, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable,
                     server_default=sa.func.now() if default else None)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "calls",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("external_id", sa.String(128)),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("object_key", sa.Text, nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("duration_s", sa.Float),
        sa.Column("sample_rate", sa.Integer),
        sa.Column("channels", sa.Integer),
        sa.Column("audio_features", pg.JSONB),
        sa.Column("error", sa.Text),
        sa.Column("attributes", pg.JSONB, nullable=False, server_default="{}"),
        _ts("created_at", default=True, nullable=False),
        _ts("updated_at", default=True, nullable=False),
        _ts("completed_at"),
    )
    op.create_index("ix_calls_status", "calls", ["status"])
    op.create_index("ix_calls_external_id", "calls", ["external_id"])
    op.create_index("ix_calls_created_at", "calls", [sa.text("created_at DESC")])

    op.create_table(
        "stage_runs",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("call_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("version", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text),
        _ts("started_at"),
        _ts("finished_at"),
        sa.UniqueConstraint("call_id", "stage", "version", name="uq_stage_runs_call_stage_version"),
    )

    op.create_table(
        "transcripts",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("call_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("language", sa.String(8), nullable=False, server_default="fa"),
        sa.Column("segments", pg.JSONB, nullable=False),
        sa.Column("full_text", sa.Text, nullable=False),
        sa.Column("search_text", sa.Text, nullable=False),
        sa.Column("processing_s", sa.Float, nullable=False),
        sa.Column("real_time_factor", sa.Float),
        _ts("created_at", default=True, nullable=False),
        sa.UniqueConstraint("call_id", "model", name="uq_transcripts_call_model"),
    )
    op.execute(
        "CREATE INDEX ix_transcripts_search_trgm ON transcripts "
        "USING gin (search_text gin_trgm_ops)"
    )

    op.create_table(
        "qa_results",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("call_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("analyzer_version", sa.String(32), nullable=False),
        sa.Column("transcript_model", sa.String(128), nullable=False),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("compliance", sa.String(16), nullable=False),
        sa.Column("flags", pg.ARRAY(sa.String(64)), nullable=False),
        sa.Column("details", pg.JSONB, nullable=False),
        _ts("created_at", default=True, nullable=False),
        sa.UniqueConstraint("call_id", "analyzer_version", name="uq_qa_results_call_version"),
    )
    op.execute("CREATE INDEX ix_qa_results_flags ON qa_results USING gin (flags)")

    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("message_id", pg.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("exchange", sa.String(128), nullable=False),
        sa.Column("routing_key", sa.String(128), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("headers", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text),
        _ts("created_at", default=True, nullable=False),
        _ts("next_attempt_at"),
        _ts("published_at"),
    )
    # The relay only ever scans unpublished rows; keep that index tiny.
    op.execute("CREATE INDEX ix_outbox_pending ON outbox (id) WHERE published_at IS NULL")
    op.execute("CREATE INDEX ix_outbox_published_at ON outbox (published_at) "
               "WHERE published_at IS NOT NULL")

    # Wake the relay immediately on commit instead of waiting for its next poll.
    # Statement-level + constant payload: Postgres collapses duplicate notifications per txn.
    op.execute(
        """
        CREATE FUNCTION outbox_notify() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_notify('outbox', '');
            RETURN NULL;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER outbox_notify AFTER INSERT ON outbox "
        "FOR EACH STATEMENT EXECUTE FUNCTION outbox_notify()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS outbox_notify ON outbox")
    op.execute("DROP FUNCTION IF EXISTS outbox_notify()")
    for table in ("outbox", "qa_results", "transcripts", "stage_runs", "calls"):
        op.drop_table(table)
