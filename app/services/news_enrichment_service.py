"""
==============================================================================
뉴스 Gemini enrichment 서비스
==============================================================================

뉴스 상세/요약 생성에서 사용하는 Gemini 프롬프트와 호출 로직을 관리합니다.

==============================================================================
"""

from __future__ import annotations

import asyncio
import json
import logging

from google import genai
from google.genai import types
from google.genai.errors import APIError

from app.config import get_settings
from app.utils.text import decode_html_entities


logger = logging.getLogger(__name__)
settings = get_settings()
_gemini_client: genai.Client | None = None

_GEMINI_MODEL_NAME = "gemini-2.0-flash-lite"
_GEMINI_TEMPERATURE = 0.3
_GEMINI_MAX_RETRIES = 4
_GEMINI_VALIDATION_RETRIES = 2
_VALID_SENTIMENTS = {"긍정", "중립", "부정"}
_GEMINI_SYSTEM_PROMPT = """
너는 금융 뉴스 데이터 분석 전문가야. 기사 내용을 분석해서 투자 정보를 추출해.

[핵심 작업 절차]
1. **Full Scan**: 기사의 첫 문장부터 마지막 문장까지 **한 글자도 빠뜨리지 말고 정독**해.
2. **Event Check**: 기사 안에는 서로 다른 여러 기업의 소식이 나열되어 있을 수 있다. (예: 특징주 모음, 섹터 결산 등)
3. **Selection**: 각 기업별로 **'구체적인 사건(신제품, 실적, 급등락, 공시 등)'이 서술된 경우**에만 추출해.

[상세 규칙]
1. related_stocks:
   기업이 아래 **3가지 카테고리 중 하나 이상**에 해당하면 무조건 추출해.

   **(A) 비즈니스/재무/영업 (Business & Sales)**
     - 실적, 계약, M&A, 공시.
     - **신규 서비스/제품 출시, 대규모 마케팅.**
     - **전시회 참가(CES, TGS, 지스타 등), 신작 공개/시연, 베타테스트(CBT/OBT) 진행.**
       (이유: 게임/IT 기업의 경우, 신작에 대한 '기대감'이나 '공개 행사' 자체가 중요한 투자 재료임. 기자가 '체험해봤다'는 형식의 기사라도 신작 공개가 핵심이면 추출할 것.)

   **(B) ESG/사회공헌/협력 (Cooperation & ESG)**
     - 업무협약(MOU), 제휴, 정부 지원사업 참여, 기부, 상생 활동.

   **(C) 리스크/사건사고 (Risk & Issue)**
     - 수사, 규제, 소송, 해킹, 화재, 횡령.
     - 기업 인프라 악용, 보안 사고, 서비스 장애 등 관리 책임 이슈.
     - 기업의 대응(해명, 사과 등)이 포함된 경우.

   **[제외 기준]**
   - 단순히 비교 대상으로 언급된 경쟁사.
   - 기사의 핵심 사건과 직접적인 관련이 없는 단순 배경 기업.

2. keywords:
   - 기사의 길이와 정보량에 따라 **핵심 명사(Noun)를 3개에서 6개 사이**로 유동적으로 추출해.
   - 내용이 짧거나 단순하면 3개만, 복잡하고 중요하면 최대 6개까지 추출.
   - (중요) 개수를 맞추기 위해 불필요한 단어를 억지로 포함하지 말 것.

3. summary: 기사를 한 줄로 요약.
4. sentiment: 주가에 미칠 영향 (긍정/부정/중립).

반드시 JSON 형식으로만 응답해. 잡담하지 마.
""".strip()


def _get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        runtime_settings = get_settings()
        _gemini_client = genai.Client(api_key=runtime_settings.gemini_api)
    return _gemini_client


def _normalize_whitespace(text: str | None) -> str:
    if not text:
        return ""
    cleaned = decode_html_entities(text) or ""
    lines = [" ".join(line.split()) for line in cleaned.replace("\r", "\n").split("\n")]
    filtered = [line for line in lines if line]
    return "\n".join(filtered).strip()


def _fallback_summary(text: str | None) -> str:
    normalized = _normalize_whitespace(text)
    if not normalized:
        return "요약 생성 실패"
    return normalized[:220]


def _extract_valid_analysis(payload: object) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return "", ""
    summary = payload.get("summary")
    sentiment = payload.get("sentiment")
    if not isinstance(summary, str):
        summary = ""
    if not isinstance(sentiment, str):
        sentiment = ""
    summary = summary.strip()
    sentiment = sentiment.strip()
    if sentiment not in _VALID_SENTIMENTS:
        sentiment = ""
    return summary, sentiment


def _normalize_keywords(raw_keywords: object) -> list[str]:
    if not isinstance(raw_keywords, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_keyword in raw_keywords:
        keyword = str(raw_keyword).strip()
        if not keyword:
            continue
        lowered = keyword.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(keyword)
        if len(normalized) >= 6:
            break
    return normalized


def _normalize_related_stocks(raw_stocks: object) -> list[str]:
    if not isinstance(raw_stocks, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_stock in raw_stocks:
        if isinstance(raw_stock, dict) and raw_stock:
            raw_stock = next(iter(raw_stock.values()))
        name = str(raw_stock).strip()
        if not name:
            continue
        lowered = name.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(name)
    return normalized


async def _call_article_gemini(text: str) -> dict | None:
    for attempt in range(_GEMINI_MAX_RETRIES):
        try:
            response = await _get_gemini_client().aio.models.generate_content(
                model=_GEMINI_MODEL_NAME,
                contents=text,
                config=types.GenerateContentConfig(
                    temperature=_GEMINI_TEMPERATURE,
                    system_instruction=_GEMINI_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                ),
            )
            response_text = (response.text or "").strip()
            if not response_text:
                raise ValueError("empty Gemini response")
            payload = json.loads(response_text)
            if isinstance(payload, list):
                payload = payload[0] if payload else {}
            if isinstance(payload, dict):
                return payload
            raise ValueError("invalid Gemini response payload")
        except (json.JSONDecodeError, ValueError, APIError) as exc:
            logger.warning("article Gemini call failed (%s/%s): %s", attempt + 1, _GEMINI_MAX_RETRIES, exc)
            if attempt < _GEMINI_MAX_RETRIES - 1:
                await asyncio.sleep(min(2**attempt, 8))
    return None


async def analyze_article(text: str) -> dict:
    normalized_text = _normalize_whitespace(text)
    if not normalized_text or not settings.gemini_api.strip():
        return {
            "summary": _fallback_summary(normalized_text),
            "sentiment": "중립",
            "keywords": [],
            "related_stocks": [],
        }

    total_attempts = max(1, _GEMINI_VALIDATION_RETRIES + 1)
    last_payload: dict | None = None
    had_response = False

    for attempt in range(total_attempts):
        payload = await _call_article_gemini(normalized_text)
        if not payload:
            continue
        had_response = True
        last_payload = payload
        summary, sentiment = _extract_valid_analysis(payload)
        if summary and sentiment:
            return {
                "summary": summary,
                "sentiment": sentiment,
                "keywords": _normalize_keywords(payload.get("keywords")),
                "related_stocks": _normalize_related_stocks(payload.get("related_stocks")),
            }
        if attempt < total_attempts - 1:
            logger.warning("invalid Gemini analysis format. retrying (%s/%s)", attempt + 1, total_attempts - 1)
            await asyncio.sleep(1.0)

    fallback_summary = _fallback_summary(normalized_text)
    if had_response and last_payload:
        return {
            "summary": fallback_summary,
            "sentiment": "중립",
            "keywords": _normalize_keywords(last_payload.get("keywords")),
            "related_stocks": _normalize_related_stocks(last_payload.get("related_stocks")),
        }
    return {
        "summary": fallback_summary,
        "sentiment": "중립",
        "keywords": [],
        "related_stocks": [],
    }


async def generate_stock_summary(stock_name: str, num_article: int, text_combined: str) -> str | None:
    normalized_text = text_combined.strip()
    if not normalized_text or not settings.gemini_api.strip():
        logger.debug("skip stock summary generation for %s: missing input or Gemini API key", stock_name)
        return None

    summary_length = "2줄" if num_article <= 5 else "3줄"
    system_prompt = f"""
    당신은 모바일 증권 앱의 AI 뉴스 요약 봇입니다.
    사용자가 스마트폰으로 한눈에 볼 수 있도록, 아래 제공된 {num_article}개의 기사 요약문을 **모두 하나로 통합하여** '{stock_name}'의 전체 핵심 이슈를 **단 {summary_length}**로 압축 요약하세요.

    [작성 규칙]
    1. ⚠️ 절대 기사 요약문별로 개별 요약하지 말 것. 전체 기사 요약문을 아우르는 최종 {summary_length}만 출력할 것.
    2. 서술형 줄글(~했습니다)은 금지하고, 뉴스 헤드라인처럼 핵심 단어(명사형) 위주로 끝맺음할 것.
    3. 각 줄은 '- ' 기호로 시작할 것.
    4. 한 줄의 길이는 40자를 넘지 않을 것.
    5. 제목이나 인사말 없이 결과물만 바로 출력할 것.
    """
    try:
        response = await _get_gemini_client().aio.models.generate_content(
            model=_GEMINI_MODEL_NAME,
            contents=normalized_text,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.2,
            ),
        )
        text = (response.text or "").strip()
        return text or None
    except Exception:
        logger.exception("stock summary generation failed: %s", stock_name)
        return None
