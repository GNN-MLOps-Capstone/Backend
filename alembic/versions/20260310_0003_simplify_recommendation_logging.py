"""simplify recommendation logging tables

Revision ID: 20260310_0003
Revises: 20260302_0002
Create Date: 2026-03-10 15:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260310_0003"
down_revision = "20260302_0002"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return index_name in {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if _has_table("recommendation_serves") and not _has_column("recommendation_serves", "served_items"):
        op.add_column(
            "recommendation_serves",
            sa.Column(
                "served_items",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )

    if _has_table("recommendation_serves"):
        if not _has_index("recommendation_serves", "ix_recommendation_serves_screen_session_id"):
            op.create_index(
                "ix_recommendation_serves_screen_session_id",
                "recommendation_serves",
                ["screen_session_id"],
                unique=False,
            )
        if not _has_index("recommendation_serves", "ix_recommendation_serves_app_session_id"):
            op.create_index(
                "ix_recommendation_serves_app_session_id",
                "recommendation_serves",
                ["app_session_id"],
                unique=False,
            )

    if _has_table("recommendation_serve_items"):
        op.drop_index("ix_recommendation_serve_items_news_id", table_name="recommendation_serve_items")
        op.drop_index("ix_recommendation_serve_items_page", table_name="recommendation_serve_items")
        op.drop_index("ix_recommendation_serve_items_request_id", table_name="recommendation_serve_items")
        op.drop_index("ix_recommendation_serve_items_serve_id", table_name="recommendation_serve_items")
        op.drop_index("ix_recommendation_serve_items_id", table_name="recommendation_serve_items")
        op.drop_table("recommendation_serve_items")

    if _has_table("recommendation_feedback"):
        op.drop_index("ix_recommendation_feedback_completed_read", table_name="recommendation_feedback")
        op.drop_index("ix_recommendation_feedback_clicked", table_name="recommendation_feedback")
        op.drop_index("ix_recommendation_feedback_page", table_name="recommendation_feedback")
        op.drop_index("ix_recommendation_feedback_news_id", table_name="recommendation_feedback")
        op.drop_index("ix_recommendation_feedback_user_id", table_name="recommendation_feedback")
        op.drop_index("ix_recommendation_feedback_request_id", table_name="recommendation_feedback")
        op.drop_index("ix_recommendation_feedback_id", table_name="recommendation_feedback")
        op.drop_table("recommendation_feedback")

    if _has_table("screen_sessions"):
        op.drop_index("ix_screen_sessions_status", table_name="screen_sessions")
        op.drop_index("ix_screen_sessions_request_id", table_name="screen_sessions")
        op.drop_index("ix_screen_sessions_app_session_id", table_name="screen_sessions")
        op.drop_index("ix_screen_sessions_user_id", table_name="screen_sessions")
        op.drop_index("ix_screen_sessions_screen_session_id", table_name="screen_sessions")
        op.drop_table("screen_sessions")

    if _has_table("content_sessions"):
        op.drop_index("ix_content_sessions_status", table_name="content_sessions")
        op.drop_index("ix_content_sessions_news_id", table_name="content_sessions")
        op.drop_index("ix_content_sessions_screen_session_id", table_name="content_sessions")
        op.drop_index("ix_content_sessions_app_session_id", table_name="content_sessions")
        op.drop_index("ix_content_sessions_user_id", table_name="content_sessions")
        op.drop_index("ix_content_sessions_content_session_id", table_name="content_sessions")
        op.drop_table("content_sessions")


def downgrade() -> None:
    return None
