from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.kis.errors import KISError


@dataclass
class RecommendationCandidate:
    news_id: int
    score: float | None = None
    reason: str | None = None


class RecommendationClient:
    """Client wrapper for external recommendation service."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def get_news_candidates(self, user_id: str, limit: int) -> list[RecommendationCandidate]:
        """
        Request recommended news list from recommendation server.

        Supported response shapes:
        - {"items": [{"news_id": 1, "score": 0.9, "reason": "..."}]}
        - {"news_ids": [1, 2, 3]}
        - [{"news_id": 1, ...}, ...]
        - [1, 2, 3]
        """
        if not self._settings.recommender_base_url:
            raise KISError("Recommendation base URL not configured", status_code=500)

        url = f"{self._settings.recommender_base_url.rstrip('/')}{self._settings.recommender_news_path}"

        headers: dict[str, str] = {"content-type": "application/json"}
        if self._settings.recommender_api_key:
            headers["authorization"] = f"Bearer {self._settings.recommender_api_key}"

        payload = {
            "user_id": user_id,
            "limit": limit,
        }

        try:
            async with httpx.AsyncClient(timeout=self._settings.recommender_timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.RequestError as exc:
            raise KISError(f"Recommendation request failed: {exc}", status_code=502) from exc

        if response.status_code >= 400:
            raise KISError(
                f"Recommendation request HTTP {response.status_code}",
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise KISError("Recommendation response is not JSON", status_code=502) from exc

        return self._normalize(data)

    def _normalize(self, data: Any) -> list[RecommendationCandidate]:
        if isinstance(data, dict):
            if isinstance(data.get("items"), list):
                rows = data["items"]
            elif isinstance(data.get("news_ids"), list):
                rows = data["news_ids"]
            else:
                rows = []
        elif isinstance(data, list):
            rows = data
        else:
            rows = []

        normalized: list[RecommendationCandidate] = []

        for row in rows:
            if isinstance(row, int):
                normalized.append(RecommendationCandidate(news_id=row))
                continue

            if not isinstance(row, dict):
                continue

            raw_id = row.get("news_id")
            if raw_id is None:
                raw_id = row.get("id")

            try:
                news_id = int(raw_id)
            except (TypeError, ValueError):
                continue

            score = row.get("score")
            reason = row.get("reason")

            try:
                score_value = float(score) if score is not None else None
            except (TypeError, ValueError):
                score_value = None

            reason_value = str(reason) if reason is not None else None

            normalized.append(
                RecommendationCandidate(
                    news_id=news_id,
                    score=score_value,
                    reason=reason_value,
                )
            )

        return normalized
