"""ensure interaction core tables and columns exist

Revision ID: 20260302_0002
Revises: 20260302_0001
Create Date: 2026-03-02 16:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260302_0002"
down_revision = "20260302_0001"
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


def _create_interaction_events() -> None:
    if not _has_table("interaction_events"):
        op.create_table(
            "interaction_events",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("event_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("device_id", sa.String(length=255), nullable=True),
            sa.Column("app_session_id", sa.String(length=255), nullable=True),
            sa.Column("event_type", sa.String(length=50), nullable=False),
            sa.Column("screen_session_id", sa.String(length=64), nullable=True),
            sa.Column("content_session_id", sa.String(length=64), nullable=True),
            sa.Column("news_id", sa.BigInteger(), nullable=True),
            sa.Column("request_id", sa.String(length=128), nullable=True),
            sa.Column("position", sa.Integer(), nullable=True),
            sa.Column("page", sa.Integer(), nullable=True),
            sa.Column("scroll_depth", sa.Float(), nullable=True),
            sa.Column("event_ts_client", sa.DateTime(timezone=True), nullable=True),
            sa.Column("event_ts_server", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("event_id"),
        )

    if not _has_column("interaction_events", "page"):
        op.add_column("interaction_events", sa.Column("page", sa.Integer(), nullable=True))
    if not _has_column("interaction_events", "scroll_depth"):
        op.add_column("interaction_events", sa.Column("scroll_depth", sa.Float(), nullable=True))

    if not _has_index("interaction_events", "ix_interaction_events_id"):
        op.create_index("ix_interaction_events_id", "interaction_events", ["id"], unique=False)
    if not _has_index("interaction_events", "ix_interaction_events_event_id"):
        op.create_index("ix_interaction_events_event_id", "interaction_events", ["event_id"], unique=True)
    if not _has_index("interaction_events", "ix_interaction_events_user_id"):
        op.create_index("ix_interaction_events_user_id", "interaction_events", ["user_id"], unique=False)
    if not _has_index("interaction_events", "ix_interaction_events_app_session_id"):
        op.create_index("ix_interaction_events_app_session_id", "interaction_events", ["app_session_id"], unique=False)
    if not _has_index("interaction_events", "ix_interaction_events_event_type"):
        op.create_index("ix_interaction_events_event_type", "interaction_events", ["event_type"], unique=False)
    if not _has_index("interaction_events", "ix_interaction_events_screen_session_id"):
        op.create_index("ix_interaction_events_screen_session_id", "interaction_events", ["screen_session_id"], unique=False)
    if not _has_index("interaction_events", "ix_interaction_events_content_session_id"):
        op.create_index("ix_interaction_events_content_session_id", "interaction_events", ["content_session_id"], unique=False)
    if not _has_index("interaction_events", "ix_interaction_events_news_id"):
        op.create_index("ix_interaction_events_news_id", "interaction_events", ["news_id"], unique=False)
    if not _has_index("interaction_events", "ix_interaction_events_request_id"):
        op.create_index("ix_interaction_events_request_id", "interaction_events", ["request_id"], unique=False)


def _create_screen_sessions() -> None:
    if _has_table("screen_sessions"):
        return

    op.create_table(
        "screen_sessions",
        sa.Column("screen_session_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("app_session_id", sa.String(length=255), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dwell_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("screen_session_id"),
    )
    op.create_index("ix_screen_sessions_screen_session_id", "screen_sessions", ["screen_session_id"], unique=False)
    op.create_index("ix_screen_sessions_user_id", "screen_sessions", ["user_id"], unique=False)
    op.create_index("ix_screen_sessions_app_session_id", "screen_sessions", ["app_session_id"], unique=False)
    op.create_index("ix_screen_sessions_request_id", "screen_sessions", ["request_id"], unique=False)
    op.create_index("ix_screen_sessions_status", "screen_sessions", ["status"], unique=False)


def _create_content_sessions() -> None:
    if _has_table("content_sessions"):
        return

    op.create_table(
        "content_sessions",
        sa.Column("content_session_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("app_session_id", sa.String(length=255), nullable=True),
        sa.Column("screen_session_id", sa.String(length=64), nullable=True),
        sa.Column("news_id", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dwell_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("content_session_id"),
    )
    op.create_index("ix_content_sessions_content_session_id", "content_sessions", ["content_session_id"], unique=False)
    op.create_index("ix_content_sessions_user_id", "content_sessions", ["user_id"], unique=False)
    op.create_index("ix_content_sessions_app_session_id", "content_sessions", ["app_session_id"], unique=False)
    op.create_index("ix_content_sessions_screen_session_id", "content_sessions", ["screen_session_id"], unique=False)
    op.create_index("ix_content_sessions_news_id", "content_sessions", ["news_id"], unique=False)
    op.create_index("ix_content_sessions_status", "content_sessions", ["status"], unique=False)


def upgrade() -> None:
    _create_interaction_events()
    _create_screen_sessions()
    _create_content_sessions()


def downgrade() -> None:
    # 안전한 롤백을 위해 본 리비전에서 생성한 신규 테이블만 제거
    # 주의: interaction_events는 기존 환경에 이미 존재할 수 있어 데이터 손실 방지를 위해 drop 대상에서 제외
    if _has_table("content_sessions"):
        op.drop_index("ix_content_sessions_status", table_name="content_sessions")
        op.drop_index("ix_content_sessions_news_id", table_name="content_sessions")
        op.drop_index("ix_content_sessions_screen_session_id", table_name="content_sessions")
        op.drop_index("ix_content_sessions_app_session_id", table_name="content_sessions")
        op.drop_index("ix_content_sessions_user_id", table_name="content_sessions")
        op.drop_index("ix_content_sessions_content_session_id", table_name="content_sessions")
        op.drop_table("content_sessions")

    if _has_table("screen_sessions"):
        op.drop_index("ix_screen_sessions_status", table_name="screen_sessions")
        op.drop_index("ix_screen_sessions_request_id", table_name="screen_sessions")
        op.drop_index("ix_screen_sessions_app_session_id", table_name="screen_sessions")
        op.drop_index("ix_screen_sessions_user_id", table_name="screen_sessions")
        op.drop_index("ix_screen_sessions_screen_session_id", table_name="screen_sessions")
        op.drop_table("screen_sessions")
