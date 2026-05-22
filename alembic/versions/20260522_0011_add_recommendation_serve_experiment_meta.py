"""add recommendation serve experiment metadata

Revision ID: 20260522_0011
Revises: 20260513_0010
Create Date: 2026-05-22 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260522_0011"
down_revision = "20260513_0010"
branch_labels = None
depends_on = None


def _get_inspector():
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return table_name in _get_inspector().get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return column_name in {
        column["name"]
        for column in _get_inspector().get_columns(table_name)
    }


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(
        index["name"] == index_name
        for index in _get_inspector().get_indexes(table_name)
    )


def upgrade() -> None:
    if not _has_table("recommendation_serves"):
        return

    if not _has_column("recommendation_serves", "experiment_id"):
        op.add_column(
            "recommendation_serves",
            sa.Column("experiment_id", sa.String(length=128), nullable=True),
        )
    if not _has_column("recommendation_serves", "variant"):
        op.add_column(
            "recommendation_serves",
            sa.Column("variant", sa.String(length=32), nullable=True),
        )

    op.execute(
        sa.text(
            """
            UPDATE recommendation_serves
            SET experiment_id = COALESCE(experiment_id, 'control'),
                variant = COALESCE(variant, 'recommend')
            """
        )
    )

    if not _has_index("recommendation_serves", "ix_recommendation_serves_experiment_id"):
        op.create_index(
            "ix_recommendation_serves_experiment_id",
            "recommendation_serves",
            ["experiment_id"],
            unique=False,
        )
    if not _has_index("recommendation_serves", "ix_recommendation_serves_variant"):
        op.create_index(
            "ix_recommendation_serves_variant",
            "recommendation_serves",
            ["variant"],
            unique=False,
        )
    if not _has_index("recommendation_serves", "ix_recommendation_serves_experiment_variant_created_at"):
        op.create_index(
            "ix_recommendation_serves_experiment_variant_created_at",
            "recommendation_serves",
            ["experiment_id", "variant", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    if not _has_table("recommendation_serves"):
        return

    if _has_index("recommendation_serves", "ix_recommendation_serves_experiment_variant_created_at"):
        op.drop_index(
            "ix_recommendation_serves_experiment_variant_created_at",
            table_name="recommendation_serves",
        )
    if _has_index("recommendation_serves", "ix_recommendation_serves_variant"):
        op.drop_index("ix_recommendation_serves_variant", table_name="recommendation_serves")
    if _has_index("recommendation_serves", "ix_recommendation_serves_experiment_id"):
        op.drop_index("ix_recommendation_serves_experiment_id", table_name="recommendation_serves")
    if _has_column("recommendation_serves", "variant"):
        op.drop_column("recommendation_serves", "variant")
    if _has_column("recommendation_serves", "experiment_id"):
        op.drop_column("recommendation_serves", "experiment_id")
