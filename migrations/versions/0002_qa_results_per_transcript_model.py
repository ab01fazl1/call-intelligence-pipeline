"""QA results are unique per (call, analyzer version, transcript model).

Scoring a call's new transcript (e.g. Whisper replacing the mock) must add a row, not
overwrite the score computed from the previous transcript.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-26
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_qa_results_call_version", "qa_results", type_="unique")
    op.create_unique_constraint("uq_qa_results_call_version_model", "qa_results",
                                ["call_id", "analyzer_version", "transcript_model"])


def downgrade() -> None:
    # Keep the newest row per (call, version) so the old constraint can be restored.
    op.execute("""
        DELETE FROM qa_results a USING qa_results b
        WHERE a.call_id = b.call_id AND a.analyzer_version = b.analyzer_version
          AND a.created_at < b.created_at
    """)
    op.drop_constraint("uq_qa_results_call_version_model", "qa_results", type_="unique")
    op.create_unique_constraint("uq_qa_results_call_version", "qa_results",
                                ["call_id", "analyzer_version"])
