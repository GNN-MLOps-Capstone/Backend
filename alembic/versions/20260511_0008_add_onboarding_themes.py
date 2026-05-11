"""add onboarding themes and market_cap

Revision ID: 20260511_0008
Revises: 20260331_0007
Create Date: 2026-05-11 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260511_0008"
down_revision = "20260331_0007"
branch_labels = None
depends_on = None


def _get_inspector():
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return table_name in _get_inspector().get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    cols = [c["name"] for c in _get_inspector().get_columns(table_name)]
    return column_name in cols


def upgrade() -> None:
    # 1. stocks.market_cap
    if _has_table("stocks") and not _has_column("stocks", "market_cap"):
        op.add_column("stocks", sa.Column("market_cap", sa.BigInteger(), nullable=True))
        op.create_index("ix_stocks_market_cap", "stocks", ["market_cap"])

    # 2. onboarding_themes
    if not _has_table("onboarding_themes"):
        op.create_table(
            "onboarding_themes",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("name", sa.String(100), nullable=False, unique=True),
            sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        )

    # 3. onboarding_theme_categories
    if not _has_table("onboarding_theme_categories"):
        op.create_table(
            "onboarding_theme_categories",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column(
                "theme_id",
                sa.Integer(),
                sa.ForeignKey("onboarding_themes.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("category_name", sa.String(200), nullable=False),
        )

    # 4. seed data
    conn = op.get_bind()

    existing = conn.execute(
        sa.text("SELECT COUNT(*) FROM onboarding_themes")
    ).scalar()
    if existing and existing > 0:
        return

    themes_seed = [
        ("AI/IT서비스", 0, [
            "소프트웨어", "IT서비스", "양방향미디어와서비스", "인터넷과카탈로그소매",
            "게임엔터테인먼트", "방송과엔터테인먼트",
        ]),
        ("반도체/전자", 1, [
            "반도체와반도체장비", "전자장비와기기", "전자제품", "디스플레이장비및부품",
            "디스플레이패널", "통신장비", "핸드셋", "컴퓨터와주변기기",
        ]),
        ("바이오/헬스케어", 2, [
            "제약", "생물공학", "생명과학도구및서비스", "건강관리장비와용품",
            "건강관리업체및서비스", "건강관리기술",
        ]),
        ("전기차/자동차", 3, [
            "자동차", "자동차부품", "전기장비", "전기제품",
        ]),
        ("에너지/화학", 4, [
            "화학", "에너지장비및서비스", "석유와가스", "가스유틸리티",
            "전기유틸리티", "복합유틸리티",
        ]),
        ("금융", 5, [
            "은행", "증권", "생명보험", "손해보험", "기타금융", "카드", "창업투자",
        ]),
        ("소비재/뷰티", 6, [
            "화장품", "식품", "음료", "섬유,의류,신발,호화품",
            "가정용기기와용품", "호텔,레스토랑,레저",
        ]),
        ("산업재/방산", 7, [
            "기계", "우주항공과국방", "건설", "건축자재", "조선",
            "철강", "비철금속", "항공사", "해운사", "항공화물운송과물류",
        ]),
    ]

    for theme_name, display_order, categories in themes_seed:
        result = conn.execute(
            sa.text(
                "INSERT INTO onboarding_themes (name, display_order) "
                "VALUES (:name, :order) RETURNING id"
            ),
            {"name": theme_name, "order": display_order},
        )
        theme_id = result.scalar()
        for cat in categories:
            conn.execute(
                sa.text(
                    "INSERT INTO onboarding_theme_categories (theme_id, category_name) "
                    "VALUES (:tid, :cat)"
                ),
                {"tid": theme_id, "cat": cat},
            )


def downgrade() -> None:
    if _has_table("onboarding_theme_categories"):
        op.drop_table("onboarding_theme_categories")
    if _has_table("onboarding_themes"):
        op.drop_table("onboarding_themes")
    if _has_table("stocks") and _has_column("stocks", "market_cap"):
        op.drop_index("ix_stocks_market_cap", table_name="stocks")
        op.drop_column("stocks", "market_cap")
