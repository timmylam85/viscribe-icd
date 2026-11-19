"""file transcription"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stt_rate_limits",
        sa.Column("deployment", sa.Text(), nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("deployment"),
    )
    op.create_table(
        "audio_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("encounter_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(
            ["encounter_id"],
            ["encounters.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("encounter_id", "revision", name="uq_audio_revision"),
    )
    op.create_table(
        "transcription_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("audio_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("chunk_seconds", sa.Integer(), nullable=False),
        sa.Column("parts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("parts_done", sa.Integer(), nullable=False),
        sa.Column("parts_total", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'superseded')",
            name="ck_transcription_job_status",
        ),
        sa.ForeignKeyConstraint(
            ["audio_id"],
            ["audio_files.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("audio_id"),
    )
    op.create_index(
        "ix_transcription_jobs_due",
        "transcription_jobs",
        ["status", "available_at", "id"],
        unique=False,
    )
    op.create_table(
        "transcripts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("audio_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("segments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["audio_id"],
            ["audio_files.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("audio_id"),
    )


def downgrade() -> None:
    op.drop_table("transcripts")
    op.drop_index("ix_transcription_jobs_due", table_name="transcription_jobs")
    op.drop_table("transcription_jobs")
    op.drop_table("audio_files")
    op.drop_table("stt_rate_limits")
