"""add interaction logging hybrid schema

Revision ID: 20260302_0001
Revises:
Create Date: 2026-03-02 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260302_0001"
down_revision = None
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


def upgrade() -> None:
    if _has_table("interaction_events"):
        if not _has_column("interaction_events", "page"):
            op.add_column("interaction_events", sa.Column("page", sa.Integer(), nullable=True))
        if not _has_column("interaction_events", "scroll_depth"):
            op.add_column("interaction_events", sa.Column("scroll_depth", sa.Float(), nullable=True))

    if not _has_table("recommendation_serves"):
        op.create_table(
            "recommendation_serves",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("request_id", sa.String(length=128), nullable=False),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("screen_session_id", sa.String(length=64), nullable=True),
            sa.Column("app_session_id", sa.String(length=255), nullable=True),
            sa.Column("source", sa.String(length=50), nullable=False),
            sa.Column("page", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("limit", sa.Integer(), nullable=False),
            sa.Column("served_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_mock", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("request_id", "page", name="uq_recommendation_serves_request_page"),
        )
        op.create_index("ix_recommendation_serves_id", "recommendation_serves", ["id"], unique=False)
        op.create_index("ix_recommendation_serves_request_id", "recommendation_serves", ["request_id"], unique=False)
        op.create_index("ix_recommendation_serves_user_id", "recommendation_serves", ["user_id"], unique=False)

    if not _has_table("recommendation_serve_items"):
        op.create_table(
            "recommendation_serve_items",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("serve_id", sa.Integer(), nullable=False),
            sa.Column("request_id", sa.String(length=128), nullable=False),
            sa.Column("page", sa.Integer(), nullable=False),
            sa.Column("news_id", sa.BigInteger(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["serve_id"], ["recommendation_serves.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "request_id",
                "page",
                "news_id",
                "position",
                name="uq_recommendation_serve_items_request_page_position",
            ),
        )
        op.create_index("ix_recommendation_serve_items_id", "recommendation_serve_items", ["id"], unique=False)
        op.create_index("ix_recommendation_serve_items_serve_id", "recommendation_serve_items", ["serve_id"], unique=False)
        op.create_index("ix_recommendation_serve_items_request_id", "recommendation_serve_items", ["request_id"], unique=False)
        op.create_index("ix_recommendation_serve_items_page", "recommendation_serve_items", ["page"], unique=False)
        op.create_index("ix_recommendation_serve_items_news_id", "recommendation_serve_items", ["news_id"], unique=False)

    if not _has_table("recommendation_feedback"):
        op.create_table(
            "recommendation_feedback",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("request_id", sa.String(length=128), nullable=False),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("app_session_id", sa.String(length=255), nullable=True),
            sa.Column("screen_session_id", sa.String(length=64), nullable=True),
            sa.Column("content_session_id", sa.String(length=64), nullable=True),
            sa.Column("source", sa.String(length=50), nullable=False, server_default="recommendations"),
            sa.Column("page", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("news_id", sa.BigInteger(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=True),
            sa.Column("impression_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("first_impression_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_impression_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("clicked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("clicked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("exited_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("dwell_ms", sa.Integer(), nullable=True),
            sa.Column("completed_read", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "request_id",
                "user_id",
                "page",
                "news_id",
                "position",
                name="uq_recommendation_feedback_request_user_item",
            ),
        )
        op.create_index("ix_recommendation_feedback_id", "recommendation_feedback", ["id"], unique=False)
        op.create_index("ix_recommendation_feedback_request_id", "recommendation_feedback", ["request_id"], unique=False)
        op.create_index("ix_recommendation_feedback_user_id", "recommendation_feedback", ["user_id"], unique=False)
        op.create_index("ix_recommendation_feedback_news_id", "recommendation_feedback", ["news_id"], unique=False)
        op.create_index("ix_recommendation_feedback_page", "recommendation_feedback", ["page"], unique=False)
        op.create_index("ix_recommendation_feedback_clicked", "recommendation_feedback", ["clicked"], unique=False)
        op.create_index(
            "ix_recommendation_feedback_completed_read",
            "recommendation_feedback",
            ["completed_read"],
            unique=False,
        )


def downgrade() -> None:
    if _has_table("recommendation_feedback"):
        op.drop_index("ix_recommendation_feedback_completed_read", table_name="recommendation_feedback")
        op.drop_index("ix_recommendation_feedback_clicked", table_name="recommendation_feedback")
        op.drop_index("ix_recommendation_feedback_page", table_name="recommendation_feedback")
        op.drop_index("ix_recommendation_feedback_news_id", table_name="recommendation_feedback")
        op.drop_index("ix_recommendation_feedback_user_id", table_name="recommendation_feedback")
        op.drop_index("ix_recommendation_feedback_request_id", table_name="recommendation_feedback")
        op.drop_index("ix_recommendation_feedback_id", table_name="recommendation_feedback")
        op.drop_table("recommendation_feedback")

    if _has_table("recommendation_serve_items"):
        op.drop_index("ix_recommendation_serve_items_news_id", table_name="recommendation_serve_items")
        op.drop_index("ix_recommendation_serve_items_page", table_name="recommendation_serve_items")
        op.drop_index("ix_recommendation_serve_items_request_id", table_name="recommendation_serve_items")
        op.drop_index("ix_recommendation_serve_items_serve_id", table_name="recommendation_serve_items")
        op.drop_index("ix_recommendation_serve_items_id", table_name="recommendation_serve_items")
        op.drop_table("recommendation_serve_items")

    if _has_table("recommendation_serves"):
        op.drop_index("ix_recommendation_serves_user_id", table_name="recommendation_serves")
        op.drop_index("ix_recommendation_serves_request_id", table_name="recommendation_serves")
        op.drop_index("ix_recommendation_serves_id", table_name="recommendation_serves")
        op.drop_table("recommendation_serves")

    if _has_column("interaction_events", "scroll_depth"):
        op.drop_column("interaction_events", "scroll_depth")
    if _has_column("interaction_events", "page"):
        op.drop_column("interaction_events", "page")
