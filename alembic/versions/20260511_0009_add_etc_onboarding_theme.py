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

    # upgrade()가 삽입한 행만 삭제: display_order=8이고 categories가
    # 이 마이그레이션에서 삽입한 목록과 정확히 일치하는 경우에만 제거한다.
    row = conn.execute(
        sa.text(
            "SELECT id FROM onboarding_themes "
            "WHERE name = '기타' AND display_order = 8"
        )
    ).fetchone()
    if row is None:
        return

    theme_id = row[0]

    actual_cats = set(
        r[0]
        for r in conn.execute(
            sa.text(
                "SELECT category_name FROM onboarding_theme_categories "
                "WHERE theme_id = :tid"
            ),
            {"tid": theme_id},
        ).fetchall()
    )
    if actual_cats != set(_ETC_CATEGORIES):
        # 카테고리가 외부에서 변경된 경우 데이터 손실 방지를 위해 no-op
        return

    conn.execute(
        sa.text("DELETE FROM onboarding_themes WHERE id = :tid"),
        {"tid": theme_id},
    )
