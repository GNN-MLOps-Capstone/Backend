"""add user onboarding keywords

Revision ID: 20260513_0010
Revises: 20260511_0009
Create Date: 2026-05-13 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260513_0010"
down_revision = "20260511_0009"
branch_labels = None
depends_on = None


def _get_inspector():
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return table_name in _get_inspector().get_table_names()


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(
        index["name"] == index_name
        for index in _get_inspector().get_indexes(table_name)
    )


def upgrade() -> None:
    if not _has_table("user_onboarding_keywords"):
        op.create_table(
            "user_onboarding_keywords",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "keyword_id",
                sa.Integer(),
                sa.ForeignKey("keywords.keyword_id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=True,
            ),
            sa.Column("original_keyword", sa.String(50), nullable=False),
            sa.UniqueConstraint(
                "user_id",
                "keyword_id",
                name="uk_user_onboarding_keyword",
            ),
        )

    if not _has_index("user_onboarding_keywords", "idx_uok_user_id"):
        op.create_index(
            "idx_uok_user_id",
            "user_onboarding_keywords",
            ["user_id"],
        )


def downgrade() -> None:
    if _has_table("user_onboarding_keywords"):
        if _has_index("user_onboarding_keywords", "idx_uok_user_id"):
            op.drop_index("idx_uok_user_id", table_name="user_onboarding_keywords")
        op.drop_table("user_onboarding_keywords")
