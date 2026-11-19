"""phase 3 foundation"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "icd_catalog_releases",
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("checksum", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("version"),
    )
    op.create_table(
        "encounters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("doctor_id", sa.Text(), nullable=False),
        sa.Column("patient_ref", sa.Text(), nullable=True),
        sa.Column("catalog_version", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="created", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("mode IN ('silent', 'assisted')", name="ck_encounter_mode"),
        sa.CheckConstraint("revision >= 1", name="ck_encounter_revision"),
        sa.ForeignKeyConstraint(
            ["catalog_version"],
            ["icd_catalog_releases.version"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_encounters_doctor_created",
        "encounters",
        ["doctor_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "icd_codes",
        sa.Column("catalog_version", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("title_vi", sa.Text(), nullable=False),
        sa.Column("title_en", sa.Text(), nullable=True),
        sa.Column("chapter", sa.Text(), nullable=True),
        sa.Column("block", sa.Text(), nullable=True),
        sa.Column("includes", sa.Text(), nullable=True),
        sa.Column("synonyms", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("excludes_codes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("sex_restriction", sa.Text(), nullable=True),
        sa.Column("age_min", sa.Integer(), nullable=True),
        sa.Column("age_max", sa.Integer(), nullable=True),
        sa.Column("rule_source", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("selectable", sa.Boolean(), nullable=False),
        sa.Column("in_focus_set", sa.Boolean(), nullable=False),
        sa.Column("search_title", sa.Text(), nullable=False),
        sa.Column("search_document", sa.Text(), nullable=False),
        sa.CheckConstraint("sex_restriction IN ('female', 'male')", name="ck_icd_sex_restriction"),
        sa.CheckConstraint("active OR NOT selectable", name="ck_icd_selectable_active"),
        sa.CheckConstraint("age_max IS NULL OR age_max >= 0", name="ck_icd_age_max"),
        sa.CheckConstraint("age_min <= age_max", name="ck_icd_age_range"),
        sa.CheckConstraint("age_min IS NULL OR age_min >= 0", name="ck_icd_age_min"),
        sa.ForeignKeyConstraint(
            ["catalog_version"],
            ["icd_catalog_releases.version"],
        ),
        sa.PrimaryKeyConstraint("catalog_version", "code"),
    )
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("encounter_id", sa.Uuid(), nullable=True),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("deployment", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["encounter_id"],
            ["encounters.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "icd_code_embeddings",
        sa.Column("catalog_version", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("deployment", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.ForeignKeyConstraint(
            ["catalog_version", "code"],
            ["icd_codes.catalog_version", "icd_codes.code"],
        ),
        sa.PrimaryKeyConstraint("catalog_version", "code", "deployment"),
    )


def downgrade() -> None:
    op.drop_table("icd_code_embeddings")
    op.drop_table("ai_runs")
    op.drop_table("icd_codes")
    op.drop_index("ix_encounters_doctor_created", table_name="encounters")
    op.drop_table("encounters")
    op.drop_table("icd_catalog_releases")
