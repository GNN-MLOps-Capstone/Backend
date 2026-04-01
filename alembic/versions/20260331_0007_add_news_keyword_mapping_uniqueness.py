"""add news keyword mapping uniqueness

Revision ID: 20260331_0007
Revises: 20260331_0006
Create Date: 2026-03-31 18:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260331_0007"
down_revision = "20260331_0006"
branch_labels = None
depends_on = None


def _get_inspector():
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    inspector = _get_inspector()
    return table_name in inspector.get_table_names()


def _has_unique_constraint(table_name: str, constraint_name: str) -> bool:
    inspector = _get_inspector()
    if table_name not in inspector.get_table_names():
        return False
    return constraint_name in {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(table_name)
    }


def upgrade() -> None:
    if not _has_table("news_keyword_mapping"):
        return

    op.execute(
        """
        DELETE FROM news_keyword_mapping AS target
        USING (
            SELECT mapping_id
            FROM (
                SELECT
                    mapping_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY news_id, keyword_id
                        ORDER BY created_at DESC NULLS LAST, mapping_id DESC
                    ) AS row_num
                FROM news_keyword_mapping
            ) AS ranked
            WHERE ranked.row_num > 1
        ) AS duplicates
        WHERE target.mapping_id = duplicates.mapping_id
        """
    )

    if not _has_unique_constraint(
        "news_keyword_mapping",
        "uq_news_keyword_mapping_news_keyword",
    ):
        op.create_unique_constraint(
            "uq_news_keyword_mapping_news_keyword",
            "news_keyword_mapping",
            ["news_id", "keyword_id"],
        )


def downgrade() -> None:
    if _has_unique_constraint(
        "news_keyword_mapping",
        "uq_news_keyword_mapping_news_keyword",
    ):
        op.drop_constraint(
            "uq_news_keyword_mapping_news_keyword",
            "news_keyword_mapping",
            type_="unique",
        )
