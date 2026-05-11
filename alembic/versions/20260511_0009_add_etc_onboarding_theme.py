"""add 기타 onboarding theme

Revision ID: 20260511_0009
Revises: 20260511_0008
Create Date: 2026-05-11 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260511_0009"
down_revision = "20260511_0008"
branch_labels = None
depends_on = None

_ETC_CATEGORIES = [
    "가구", "가정용품", "건축제품", "광고", "교육서비스",
    "다각화된소비자서비스", "다각화된통신서비스", "담배", "도로와철도운송",
    "레저용장비와제품", "무선통신서비스", "무역회사와판매업체", "문구류",
    "백화점과일반상점", "복합기업", "부동산", "사무용전자제품",
    "상업서비스와공급품", "식품과기본식료품소매", "운송인프라", "전문소매",
    "종이와목재", "출판", "판매업체", "포장재",
]


def upgrade() -> None:
    conn = op.get_bind()

    existing = conn.execute(
        sa.text("SELECT id FROM onboarding_themes WHERE name = '기타'")
    ).scalar()
    if existing:
        return

    result = conn.execute(
        sa.text(
            "INSERT INTO onboarding_themes (name, display_order) "
            "VALUES ('기타', 8) RETURNING id"
        )
    )
    theme_id = result.scalar()

    for cat in _ETC_CATEGORIES:
        conn.execute(
            sa.text(
                "INSERT INTO onboarding_theme_categories (theme_id, category_name) "
                "VALUES (:tid, :cat)"
            ),
            {"tid": theme_id, "cat": cat},
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM onboarding_themes WHERE name = '기타'"
        )
    )
