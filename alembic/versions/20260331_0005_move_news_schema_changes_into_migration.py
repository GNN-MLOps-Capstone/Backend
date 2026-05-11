"""move news schema changes into explicit migration

Revision ID: 20260331_0005
Revises: 20260310_0004
Create Date: 2026-03-31 15:40:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260331_0005"
down_revision = "20260310_0004"
branch_labels = None
depends_on = None


def _get_inspector():
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    inspector = _get_inspector()
    return table_name in inspector.get_table_names()


def _get_column(table_name: str, column_name: str) -> dict | None:
    inspector = _get_inspector()
    if table_name not in inspector.get_table_names():
        return None
    for column in inspector.get_columns(table_name):
        if column["name"] == column_name:
            return column
    return None


def _has_column(table_name: str, column_name: str) -> bool:
    return _get_column(table_name, column_name) is not None


def _get_pk_name(table_name: str) -> str | None:
    return _get_pk_constraint(table_name).get("name")


def _get_pk_constraint(table_name: str) -> dict:
    inspector = _get_inspector()
    if table_name not in inspector.get_table_names():
        return {}
    return inspector.get_pk_constraint(table_name)


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = _get_inspector()
    if table_name not in inspector.get_table_names():
        return False
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def _has_unique_constraint(table_name: str, constraint_name: str) -> bool:
    inspector = _get_inspector()
    if table_name not in inspector.get_table_names():
        return False
    return constraint_name in {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(table_name)
    }


def _find_foreign_key_name(
    table_name: str,
    constrained_column: str,
    referred_table: str | None = None,
) -> str | None:
    inspector = _get_inspector()
    if table_name not in inspector.get_table_names():
        return None
    for foreign_key in inspector.get_foreign_keys(table_name):
        if foreign_key.get("constrained_columns") != [constrained_column]:
            continue
        if referred_table and foreign_key.get("referred_table") != referred_table:
            continue
        return foreign_key.get("name")
    return None


def _varchar_length(table_name: str, column_name: str) -> int | None:
    column = _get_column(table_name, column_name)
    if not column:
        return None
    column_type = column["type"]
    return getattr(column_type, "length", None)


def _has_sequence(sequence_name: str) -> bool:
    inspector = _get_inspector()
    try:
        return sequence_name in inspector.get_sequence_names()
    except NotImplementedError:
        bind = op.get_bind()
        exists = bind.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.sequences
                    WHERE sequence_name = :sequence_name
                )
                """
            ),
            {"sequence_name": sequence_name},
        ).scalar()
        return bool(exists)


def upgrade() -> None:
    if _has_table("aliases") and _has_column("aliases", "stock_id"):
        if _varchar_length("aliases", "stock_id") == 6:
            op.alter_column(
                "aliases",
                "stock_id",
                existing_type=sa.String(length=6),
                type_=sa.String(length=20),
                existing_nullable=False,
            )

    if _has_table("news_stock_mapping"):
        if _has_column("news_stock_mapping", "news_id"):
            op.alter_column(
                "news_stock_mapping",
                "news_id",
                existing_type=sa.Integer(),
                type_=sa.BigInteger(),
                existing_nullable=False,
            )
        if _has_column("news_stock_mapping", "stock_id") and _varchar_length("news_stock_mapping", "stock_id") == 6:
            op.alter_column(
                "news_stock_mapping",
                "stock_id",
                existing_type=sa.String(length=6),
                type_=sa.String(length=20),
                existing_nullable=False,
            )

        if not _has_column("news_stock_mapping", "extractor_version"):
            op.add_column(
                "news_stock_mapping",
                sa.Column("extractor_version", sa.String(length=50), nullable=True),
            )
        if not _has_column("news_stock_mapping", "weight"):
            op.add_column(
                "news_stock_mapping",
                sa.Column("weight", sa.Float(), nullable=True, server_default=sa.text("1.0")),
            )

        old_stock_fk = _find_foreign_key_name(
            "news_stock_mapping",
            "stock_id",
            "stock_summary_cache",
        )
        if old_stock_fk:
            op.drop_constraint(old_stock_fk, "news_stock_mapping", type_="foreignkey")
        if not _find_foreign_key_name("news_stock_mapping", "stock_id", "stocks"):
            op.create_foreign_key(
                "fk_news_stock_mapping_stock_id_stocks",
                "news_stock_mapping",
                "stocks",
                ["stock_id"],
                ["stock_id"],
                ondelete="CASCADE",
            )
        if not _find_foreign_key_name("news_stock_mapping", "news_id", "naver_news"):
            op.create_foreign_key(
                "fk_news_stock_mapping_news_id_naver_news",
                "news_stock_mapping",
                "naver_news",
                ["news_id"],
                ["news_id"],
                ondelete="CASCADE",
            )
        if not _has_index("news_stock_mapping", "ix_news_stock_mapping_news_id"):
            op.create_index(
                "ix_news_stock_mapping_news_id",
                "news_stock_mapping",
                ["news_id"],
            )

    if _has_table("filtered_news"):
        col = _get_column("filtered_news", "news_id")
        if col is not None and not isinstance(col["type"], sa.BigInteger):
            op.alter_column(
                "filtered_news",
                "news_id",
                existing_type=sa.Integer(),
                type_=sa.BigInteger(),
                existing_nullable=False,
            )

        if not _has_column("filtered_news", "filtered_news_id"):
            op.add_column(
                "filtered_news",
                sa.Column("filtered_news_id", sa.BigInteger(), nullable=True),
            )
            op.execute(
                """
                CREATE SEQUENCE IF NOT EXISTS filtered_news_filtered_news_id_seq
                """
            )
            op.execute(
                """
                ALTER TABLE filtered_news
                ALTER COLUMN filtered_news_id
                SET DEFAULT nextval('filtered_news_filtered_news_id_seq')
                """
            )
            op.execute(
                """
                UPDATE filtered_news
                SET filtered_news_id = nextval('filtered_news_filtered_news_id_seq')
                WHERE filtered_news_id IS NULL
                """
            )
            op.execute(
                """
                SELECT setval(
                    'filtered_news_filtered_news_id_seq',
                    COALESCE(MAX(filtered_news_id), 1),
                    COALESCE(MAX(filtered_news_id), 0) > 0
                )
                FROM filtered_news
                """
            )
            op.alter_column(
                "filtered_news",
                "filtered_news_id",
                existing_type=sa.BigInteger(),
                nullable=False,
            )

        pk_constraint = _get_pk_constraint("filtered_news")
        current_pk = pk_constraint.get("name")
        current_pk_columns = list(pk_constraint.get("constrained_columns") or [])
        if not (
            current_pk == "filtered_news_pkey"
            and current_pk_columns == ["filtered_news_id"]
        ):
            # Legacy databases may still use the canonical PK name while pointing at news_id,
            # so check the constrained columns before deciding whether a drop/recreate is needed.
            if current_pk:
                op.drop_constraint(current_pk, "filtered_news", type_="primary")
            op.create_primary_key("filtered_news_pkey", "filtered_news", ["filtered_news_id"])

        if not _has_unique_constraint("filtered_news", "uq_filtered_news_news_id"):
            op.create_unique_constraint(
                "uq_filtered_news_news_id",
                "filtered_news",
                ["news_id"],
            )
        if not _has_index("filtered_news", "ix_filtered_news_news_id"):
            op.create_index("ix_filtered_news_news_id", "filtered_news", ["news_id"])
        if not _find_foreign_key_name("filtered_news", "news_id", "naver_news"):
            op.create_foreign_key(
                "fk_filtered_news_news_id_naver_news",
                "filtered_news",
                "naver_news",
                ["news_id"],
                ["news_id"],
                ondelete="CASCADE",
            )
        if not _has_column("filtered_news", "embedding_model_version"):
            op.add_column(
                "filtered_news",
                sa.Column("embedding_model_version", sa.String(length=50), nullable=True),
            )
        if not _has_column("filtered_news", "updated_at"):
            op.add_column(
                "filtered_news",
                sa.Column(
                    "updated_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                    server_default=sa.text("now()"),
                ),
            )


def downgrade() -> None:
    if _has_table("filtered_news"):
        news_fk = _find_foreign_key_name("filtered_news", "news_id", "naver_news")
        if news_fk:
            op.drop_constraint(news_fk, "filtered_news", type_="foreignkey")
        if _has_index("filtered_news", "ix_filtered_news_news_id"):
            op.drop_index("ix_filtered_news_news_id", table_name="filtered_news")
        if _has_unique_constraint("filtered_news", "uq_filtered_news_news_id"):
            op.drop_constraint("uq_filtered_news_news_id", "filtered_news", type_="unique")
        current_pk = _get_pk_name("filtered_news")
        if current_pk:
            op.drop_constraint(current_pk, "filtered_news", type_="primary")
        op.create_primary_key("filtered_news_pkey", "filtered_news", ["news_id"])
        if _has_column("filtered_news", "updated_at"):
            op.drop_column("filtered_news", "updated_at")
        if _has_column("filtered_news", "embedding_model_version"):
            op.drop_column("filtered_news", "embedding_model_version")
        if _has_column("filtered_news", "filtered_news_id"):
            op.drop_column("filtered_news", "filtered_news_id")
        if _has_sequence("filtered_news_filtered_news_id_seq"):
            op.execute("DROP SEQUENCE IF EXISTS filtered_news_filtered_news_id_seq")
        if _has_column("filtered_news", "news_id"):
            op.alter_column(
                "filtered_news",
                "news_id",
                existing_type=sa.BigInteger(),
                type_=sa.Integer(),
                existing_nullable=False,
            )


    if _has_table("news_stock_mapping"):
        news_fk = _find_foreign_key_name("news_stock_mapping", "news_id", "naver_news")
        if news_fk:
            op.drop_constraint(news_fk, "news_stock_mapping", type_="foreignkey")
        stock_fk = _find_foreign_key_name("news_stock_mapping", "stock_id", "stocks")
        if stock_fk:
            op.drop_constraint(stock_fk, "news_stock_mapping", type_="foreignkey")
        if not _find_foreign_key_name("news_stock_mapping", "stock_id", "stock_summary_cache"):
            op.create_foreign_key(
                "fk_news_stock_mapping_stock_id_stock_summary_cache",
                "news_stock_mapping",
                "stock_summary_cache",
                ["stock_id"],
                ["stock_id"],
            )
        if _has_index("news_stock_mapping", "ix_news_stock_mapping_news_id"):
            op.drop_index("ix_news_stock_mapping_news_id", table_name="news_stock_mapping")
        if _has_column("news_stock_mapping", "weight"):
            op.drop_column("news_stock_mapping", "weight")
        if _has_column("news_stock_mapping", "extractor_version"):
            op.drop_column("news_stock_mapping", "extractor_version")
        if _has_column("news_stock_mapping", "stock_id") and _varchar_length("news_stock_mapping", "stock_id") == 20:
            op.alter_column(
                "news_stock_mapping",
                "stock_id",
                existing_type=sa.String(length=20),
                type_=sa.String(length=6),
                existing_nullable=False,
            )
        if _has_column("news_stock_mapping", "news_id"):
            op.alter_column(
                "news_stock_mapping",
                "news_id",
                existing_type=sa.BigInteger(),
                type_=sa.Integer(),
                existing_nullable=False,
            )

    if _has_table("aliases") and _has_column("aliases", "stock_id"):
        if _varchar_length("aliases", "stock_id") == 20:
            op.alter_column(
                "aliases",
                "stock_id",
                existing_type=sa.String(length=20),
                type_=sa.String(length=6),
                existing_nullable=False,
            )
