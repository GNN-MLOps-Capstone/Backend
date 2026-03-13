"""add interaction event foreign key and enum constraints

Revision ID: 20260310_0004
Revises: 20260310_0003
Create Date: 2026-03-10 16:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260310_0004"
down_revision = "20260310_0003"
branch_labels = None
depends_on = None


_ENUM_NAME = "interaction_event_type_enum"
_ENUM_VALUES = (
    "screen_view",
    "screen_heartbeat",
    "screen_leave",
    "content_open",
    "content_heartbeat",
    "content_leave",
    "recommendation_request",
    "recommendation_response",
    "recommendation_impression",
    "scroll_depth",
)


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_foreign_key(table_name: str, fk_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return fk_name in {fk["name"] for fk in inspector.get_foreign_keys(table_name)}


def _event_type_is_enum() -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = inspector.get_columns("interaction_events")
    for column in columns:
        if column["name"] == "event_type":
            return isinstance(column["type"], sa.Enum)
    return False


def _user_id_is_integer() -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = inspector.get_columns("interaction_events")
    for column in columns:
        if column["name"] == "user_id":
            return isinstance(column["type"], sa.Integer)
    return False


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_type
                WHERE typname = 'interaction_event_type_enum'
            ) THEN
                CREATE TYPE interaction_event_type_enum AS ENUM (
                    'screen_view',
                    'screen_heartbeat',
                    'screen_leave',
                    'content_open',
                    'content_heartbeat',
                    'content_leave',
                    'recommendation_request',
                    'recommendation_response',
                    'recommendation_impression',
                    'scroll_depth'
                );
            END IF;
        END
        $$;
        """
    )

    bind = op.get_bind()

    if _has_table("interaction_events"):
        invalid_event_type = bind.execute(
            sa.text(
                """
                SELECT event_type
                FROM interaction_events
                WHERE event_type IS NOT NULL
                  AND event_type::text NOT IN :allowed
                LIMIT 1
                """
            ).bindparams(sa.bindparam("allowed", expanding=True, value=list(_ENUM_VALUES)))
        ).scalar_one_or_none()
        if invalid_event_type is not None:
            raise RuntimeError(
                "Cannot migrate interaction_events.event_type to enum. "
                f"Invalid value found: {invalid_event_type}"
            )

        invalid_user_id = bind.execute(
            sa.text(
                """
                SELECT ie.user_id
                FROM interaction_events ie
                WHERE ie.user_id IS NULL
                   OR ie.user_id::text !~ '^[0-9]+$'
                LIMIT 1
                """
            )
        ).scalar_one_or_none()
        if invalid_user_id is not None:
            raise RuntimeError(
                "Cannot migrate interaction_events.user_id to INTEGER. "
                f"Non-numeric user_id found: {invalid_user_id}"
            )

        orphan_user = bind.execute(
            sa.text(
                """
                SELECT ie.user_id
                FROM interaction_events ie
                LEFT JOIN users u
                  ON u.id = ie.user_id::integer
                WHERE u.id IS NULL
                LIMIT 1
                """
            )
        ).scalar_one_or_none()
        if orphan_user is not None:
            raise RuntimeError(
                "Cannot add foreign key interaction_events.user_id -> users.id. "
                f"Orphan user_id found: {orphan_user}"
            )

        if not _event_type_is_enum():
            op.execute(
                """
                ALTER TABLE interaction_events
                ALTER COLUMN event_type TYPE interaction_event_type_enum
                USING event_type::interaction_event_type_enum
                """
            )

        if not _user_id_is_integer():
            op.execute(
                """
                ALTER TABLE interaction_events
                ALTER COLUMN user_id TYPE INTEGER
                USING user_id::integer
                """
            )

        if not _has_foreign_key("interaction_events", "fk_interaction_events_user_id"):
            op.create_foreign_key(
                "fk_interaction_events_user_id",
                "interaction_events",
                "users",
                ["user_id"],
                ["id"],
                ondelete="CASCADE",
            )

    if _has_table("recommendation_serves"):
        invalid_recommendation_user_id = bind.execute(
            sa.text(
                """
                SELECT user_id
                FROM recommendation_serves
                WHERE user_id IS NULL
                   OR user_id::text !~ '^[0-9]+$'
                LIMIT 1
                """
            )
        ).scalar_one_or_none()
        if invalid_recommendation_user_id is not None:
            raise RuntimeError(
                "Cannot migrate recommendation_serves.user_id to INTEGER. "
                f"Non-numeric user_id found: {invalid_recommendation_user_id}"
            )

        orphan_recommendation_user = bind.execute(
            sa.text(
                """
                SELECT rs.user_id
                FROM recommendation_serves rs
                LEFT JOIN users u
                  ON u.id = rs.user_id::integer
                WHERE u.id IS NULL
                LIMIT 1
                """
            )
        ).scalar_one_or_none()
        if orphan_recommendation_user is not None:
            raise RuntimeError(
                "Cannot add foreign key recommendation_serves.user_id -> users.id. "
                f"Orphan user_id found: {orphan_recommendation_user}"
            )

        recommendation_user_column = sa.inspect(bind).get_columns("recommendation_serves")
        recommendation_user_is_integer = any(
            column["name"] == "user_id" and isinstance(column["type"], sa.Integer)
            for column in recommendation_user_column
        )
        if not recommendation_user_is_integer:
            op.execute(
                """
                ALTER TABLE recommendation_serves
                ALTER COLUMN user_id TYPE INTEGER
                USING user_id::integer
                """
            )

        if not _has_foreign_key("recommendation_serves", "fk_recommendation_serves_user_id"):
            op.create_foreign_key(
                "fk_recommendation_serves_user_id",
                "recommendation_serves",
                "users",
                ["user_id"],
                ["id"],
                ondelete="CASCADE",
            )


def downgrade() -> None:
    if _has_table("recommendation_serves") and _has_foreign_key("recommendation_serves", "fk_recommendation_serves_user_id"):
        op.drop_constraint("fk_recommendation_serves_user_id", "recommendation_serves", type_="foreignkey")

    if _has_table("recommendation_serves") and _has_column("recommendation_serves", "user_id"):
        recommendation_user_column = sa.inspect(op.get_bind()).get_columns("recommendation_serves")
        recommendation_user_is_integer = any(
            column["name"] == "user_id" and isinstance(column["type"], sa.Integer)
            for column in recommendation_user_column
        )
        if recommendation_user_is_integer:
            op.execute(
                """
                ALTER TABLE recommendation_serves
                ALTER COLUMN user_id TYPE VARCHAR(255)
                USING user_id::text
                """
            )

    if _has_table("interaction_events") and _has_foreign_key("interaction_events", "fk_interaction_events_user_id"):
        op.drop_constraint("fk_interaction_events_user_id", "interaction_events", type_="foreignkey")

    if _has_table("interaction_events") and _user_id_is_integer():
        op.execute(
            """
            ALTER TABLE interaction_events
            ALTER COLUMN user_id TYPE VARCHAR(255)
            USING user_id::text
            """
        )

    if _has_table("interaction_events") and _event_type_is_enum():
        op.execute(
            """
            ALTER TABLE interaction_events
            ALTER COLUMN event_type TYPE VARCHAR(50)
            USING event_type::text
            """
        )
