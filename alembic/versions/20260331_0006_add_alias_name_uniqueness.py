"""add alias name uniqueness

Revision ID: 20260331_0006
Revises: 20260331_0005
Create Date: 2026-03-31 17:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260331_0006"
down_revision = "20260331_0005"
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
    if not _has_table("aliases"):
        return

    op.execute(
        """
        DELETE FROM aliases AS target
        USING (
            SELECT alias_id
            FROM (
                SELECT
                    alias_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY alias_name
                        ORDER BY alias_id
                    ) AS row_num
                FROM aliases
                WHERE alias_name IS NOT NULL
            ) AS ranked
            WHERE ranked.row_num > 1
        ) AS duplicates
        WHERE target.alias_id = duplicates.alias_id
        """
    )

    if not _has_unique_constraint("aliases", "uq_aliases_alias_name"):
        op.create_unique_constraint(
            "uq_aliases_alias_name",
            "aliases",
            ["alias_name"],
        )


def downgrade() -> None:
    if _has_unique_constraint("aliases", "uq_aliases_alias_name"):
        op.drop_constraint("uq_aliases_alias_name", "aliases", type_="unique")
