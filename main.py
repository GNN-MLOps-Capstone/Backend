"""
뉴스 추천 시뮬레이션 - 단일 파일 버전
실행: python simulation_standalone.py
"""

import asyncio
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path

import httpx
from google import genai

from app.config import get_settings

# =============================================================================
# 설정
# =============================================================================

BASE_URL = "http://localhost:8000"
MAX_PAGES = 5
OUTPUT_DIR = Path("simulation/results")

settings = get_settings()
gemini_client = genai.Client(api_key=settings.gemini_api)
GEMINI_MODEL = "gemini-2.0-flash"

# =============================================================================
# 페르소나 정의
# =============================================================================

PERSONAS = [
    {"id": "9000", "name": "AI 연구자 민수",        "age_group": "30대", "job": "개발자",   "description": "AI·반도체 분야 전문가. 최신 기술 트렌드에 민감.", "stocks": ["삼성전자", "SK하이닉스", "네이버"],               "keywords": ["AI", "반도체", "GPU", "데이터센터", "LLM"]},
    {"id": "9001", "name": "반도체 장비 투자자 지훈","age_group": "40대", "job": "직장인",   "description": "반도체 장비 섹터 집중 투자자.",                 "stocks": ["한미반도체", "원익IPS", "주성엔지니어링"],        "keywords": ["반도체", "장비", "EUV", "HBM", "파운드리"]},
    {"id": "9002", "name": "배터리 산업 투자자 태호","age_group": "30대", "job": "직장인",   "description": "2차전지 산업 전문 투자자.",                     "stocks": ["LG에너지솔루션", "삼성SDI", "에코프로비엠"],      "keywords": ["2차전지", "배터리", "전기차", "리튬"]},
    {"id": "9003", "name": "전기차 매니아 수진",     "age_group": "20대", "job": "대학생",   "description": "전기차·자율주행 기술에 열정적.",                "stocks": ["현대차", "기아"],                                 "keywords": ["전기차", "자율주행", "모빌리티", "테슬라"]},
    {"id": "9004", "name": "친환경 에너지 관심 유진","age_group": "20대", "job": "활동가",   "description": "재생에너지·탄소중립 관심자.",                   "stocks": ["한화솔루션", "씨에스윈드"],                       "keywords": ["풍력", "태양광", "재생에너지", "탄소중립", "ESG"]},
    {"id": "9005", "name": "거시경제 분석가 준호",   "age_group": "40대", "job": "전문직",   "description": "금리·환율·거시경제 분석 전문가.",              "stocks": ["KB금융", "신한지주"],                             "keywords": ["금리", "인플레이션", "환율", "경기침체"]},
    {"id": "9006", "name": "배당주 투자자 영수",     "age_group": "50대", "job": "은퇴자",   "description": "안정적 배당 수익 추구.",                        "stocks": ["KT", "SK텔레콤"],                                "keywords": ["배당", "배당주", "우량주", "금리"]},
    {"id": "9007", "name": "글로벌 경제 관심 지민",  "age_group": "30대", "job": "직장인",   "description": "해외 증시·글로벌 경제 관심자.",                "stocks": ["삼성전자"],                                      "keywords": ["나스닥", "뉴욕증시", "환율", "Fed"]},
    {"id": "9008", "name": "플랫폼 산업 분석가 지은","age_group": "30대", "job": "프리랜서", "description": "IT 플랫폼 광고·커머스 분석가.",                "stocks": ["네이버", "카카오"],                               "keywords": ["플랫폼", "IT", "광고", "커머스"]},
    {"id": "9009", "name": "AI 스타트업 덕후 서연",  "age_group": "20대", "job": "개발자",   "description": "AI 스타트업·IPO 관심자.",                      "stocks": ["네이버", "카카오"],                               "keywords": ["AI", "LLM", "스타트업", "챗봇", "IPO"]},
    {"id": "9010", "name": "게임 산업 매니아 도윤",  "age_group": "20대", "job": "직장인",   "description": "게임 신작·메타버스 관심자.",                    "stocks": ["엔씨소프트", "크래프톤", "넷마블"],               "keywords": ["게임", "신작", "MMORPG", "메타버스"]},
    {"id": "9011", "name": "K-팝 산업 관심 신유진",  "age_group": "20대", "job": "직장인",   "description": "엔터테인먼트·K팝 팬덤 관심자.",                "stocks": ["와이지엔터테인먼트", "에스엠"],                   "keywords": ["K팝", "엔터테인먼트", "공연", "팬덤"]},
    {"id": "9012", "name": "바이오 투자자 세진",     "age_group": "40대", "job": "전문직",   "description": "바이오·신약개발 전문 투자자.",                  "stocks": ["삼성바이오로직스", "셀트리온"],                   "keywords": ["바이오", "임상시험", "신약개발", "CMO"]},
    {"id": "9013", "name": "헬스케어 관심 지수",     "age_group": "30대", "job": "직장인",   "description": "디지털헬스·고령화 사회 관심자.",               "stocks": ["유한양행"],                                      "keywords": ["헬스케어", "제약", "디지털헬스", "고령화"]},
    {"id": "9014", "name": "건설 인프라 투자자 동현","age_group": "40대", "job": "자영업",   "description": "건설·해외수주 관심 투자자.",                    "stocks": ["현대건설", "삼성물산"],                           "keywords": ["건설", "부동산", "인프라", "해외수주"]},
    {"id": "9015", "name": "철강 산업 분석가 성민",  "age_group": "50대", "job": "제조업",   "description": "철강·원자재 공급망 분석가.",                    "stocks": ["POSCO홀딩스", "현대제철"],                        "keywords": ["철강", "원자재", "물류", "중국", "공급망"]},
    {"id": "9016", "name": "소비 트렌드 관찰자 나연","age_group": "30대", "job": "주부",     "description": "K뷰티·소비 트렌드 관찰자.",                     "stocks": ["아모레퍼시픽", "LG생활건강"],                     "keywords": ["화장품", "K뷰티", "소비", "트렌드", "중국"]},
    {"id": "9017", "name": "유통 산업 관심 민재",    "age_group": "40대", "job": "직장인",   "description": "유통·이커머스 산업 관심자.",                    "stocks": ["이마트", "롯데쇼핑"],                             "keywords": ["유통", "리테일", "이커머스", "쇼핑", "물류"]},
    {"id": "9018", "name": "종합 뉴스 탐색가 승우",  "age_group": "30대", "job": "직장인",   "description": "특정 섹터 없이 경제 전반 탐색.",               "stocks": [],                                                "keywords": ["경제", "실적", "증시", "시장"]},
    {"id": "9019", "name": "관심사 없는 유저 소영",  "age_group": "20대", "job": "직장인",   "description": "뉴스 앱을 막 설치한 완전 신규 유저.",           "stocks": [],                                                "keywords": []},
]

TIME_SLOTS = [
    {"label": "출근 전 아침",  "description": "시간이 없어 빠르게 훑어봄. 핵심만 확인."},
    {"label": "점심시간",      "description": "여유 있게 읽을 수 있음. 관심 있으면 꼼꼼히 읽음."},
    {"label": "퇴근 후 저녁",  "description": "피곤하지만 관심 있는 뉴스는 끝까지 읽음."},
    {"label": "주말 오전",     "description": "여유롭게 여러 뉴스를 천천히 탐색."},
]

CONDITIONS = [
    "컨디션 좋음. 집중력 높음.",
    "약간 피곤함. 흥미로운 뉴스만 읽을 것 같음.",
    "매우 바쁨. 훑어보고 빠르게 종료할 가능성 높음.",
    "여유로움. 다양한 뉴스를 탐색하고 싶음.",
]

ATTITUDES = ["적극적", "소극적", "무관심"]

# =============================================================================
# 유틸
# =============================================================================

def generate_context() -> dict:
    time_slot = random.choice(TIME_SLOTS)
    return {
        "attitude": random.choice(ATTITUDES),
        "time_slot": time_slot["label"],
        "time_slot_description": time_slot["description"],
        "condition": random.choice(CONDITIONS),
        "fatigue": 0,
        "random_seed": random.randint(1000, 9999),
    }


def parse_json(text: str) -> dict:
    clean = re.sub(r"```json|```", "", text).strip()
    return json.loads(clean)


def persona_prompt(p: dict) -> str:
    stocks = ", ".join(p.get("stocks", [])) or "없음"
    keywords = ", ".join(p.get("keywords", [])) or "없음"
    ctx = p["context"]
    return f"""당신은 다음과 같은 사람입니다:
- 이름: {p['name']} / 나이대: {p['age_group']} / 직업: {p['job']}
- 특징: {p['description']}
- 관심 종목: {stocks}
- 관심 키워드: {keywords}

오늘의 상황:
- 시간대: {ctx['time_slot']} → {ctx['time_slot_description']}
- 컨디션: {ctx['condition']}
- 태도: {ctx['attitude']} (태도는 읽는 깊이/종료 타이밍에만 영향. 클릭은 순수 관심도 기반)
- 현재 피로도: {ctx['fatigue']}단계 (0=없음, 높을수록 종료 가능성 증가)"""


# =============================================================================
# API 호출
# =============================================================================

async def fetch_recommendations(user_id: str, cursor: str = None) -> dict:
    params = {"user_id": user_id, "limit": 20, "log_served": True}
    if cursor:
        params["cursor"] = cursor
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"{BASE_URL}/api/news/recommendations",
            params=params,
            timeout=15.0,
        )
        res.raise_for_status()
        data = res.json()
        return {
            "request_id": data.get("request_id"),
            "source": data.get("source"),
            "cursor": data.get("cursor"),
            "items": data.get("items", []),
        }


async def fetch_news_detail(news_id: int) -> dict:
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{BASE_URL}/api/news/{news_id}", timeout=15.0)
        res.raise_for_status()
        return res.json()


# =============================================================================
# LLM 판단
# =============================================================================

async def judge_news_list(p: dict, items: list, already_read: list) -> dict:
    # 이미 읽은 뉴스 제외한 목록만 LLM에 제공
    unread_items = [item for item in items if item["news_id"] not in already_read]

    # 읽지 않은 뉴스가 없으면 즉시 next/exit 판단
    if not unread_items:
        return {"action": "next", "reason": "읽지 않은 뉴스가 없음"}

    news_text = ""
    valid_ids = []
    for i, item in enumerate(unread_items, 1):
        news_text += f"{i}. [ID:{item['news_id']}] {item['title']}\n   요약: {str(item.get('summary',''))[:100]}...\n\n"
        valid_ids.append(item["news_id"])

    prompt = f"""{persona_prompt(p)}

---

아래는 현재 추천된 뉴스 목록입니다 (이미 읽은 뉴스는 제외됨):

{news_text}
위 목록을 보고 어떤 행동을 취하겠습니까? 관심도에 따라 솔직하게 판단하세요.

★ 중요 규칙:
- news_id는 반드시 위 목록에 있는 ID 중 하나를 그대로 사용하세요
- 사용 가능한 ID 목록: {valid_ids}
- 목록에 없는 ID를 임의로 만들지 마세요

반드시 아래 JSON 형식 중 하나로만 응답하세요:
읽고 싶은 뉴스가 있는 경우: {{"action": "select", "news_id": <위 목록의 ID 중 하나>, "reason": "<이유 한 줄>"}}
더 보고 싶지만 원하는 뉴스가 없는 경우: {{"action": "next", "reason": "<이유 한 줄>"}}
탐색을 마치고 싶은 경우: {{"action": "exit", "reason": "<이유 한 줄>"}}"""

    res = await gemini_client.aio.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    result = parse_json(res.text)

    # 응답 ID 검증: 목록에 없는 ID면 next로 처리
    if result.get("action") == "select" and result.get("news_id") not in valid_ids:
        return {"action": "next", "reason": f"유효하지 않은 news_id 반환 (id={result.get('news_id')})"}

    return result


async def judge_article_entry(p: dict, article: dict) -> dict:
    """뉴스 클릭 직후: 계속 읽을지 바로 나갈지 판단"""
    fatigue = p["context"]["fatigue"]
    read_count = fatigue  # 피로도 = 읽은 뉴스 수

    prompt = f"""{persona_prompt(p)}

---

방금 아래 뉴스를 클릭했습니다:
제목: {article.get('title')}
요약 첫 줄: {str(article.get('summary', ''))[:80]}...

제목과 첫 인상만 보고 계속 읽을지 바로 나갈지 판단하세요.

★ 현실적인 행동 기준:
- 지금까지 이미 {read_count}개 읽었습니다
- 관심사와 딱 맞아야 계속 읽습니다. 조금이라도 아니다 싶으면 바로 나가세요.
- 피로도가 높을수록 기준이 엄격해집니다

반드시 아래 JSON 형식 중 하나로만 응답하세요:
계속 읽는 경우: {{"action": "read", "reason": "<이유 한 줄>"}}
바로 나가는 경우: {{"action": "bounce", "reason": "<이유 한 줄>"}}"""

    res = await gemini_client.aio.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return parse_json(res.text)


async def judge_after_read(p: dict, article: dict) -> dict:
    """뉴스 본문 읽은 후: 리스트 복귀 or 종료 판단"""
    fatigue = p["context"]["fatigue"]
    read_count = fatigue

    prompt = f"""{persona_prompt(p)}

---

방금 아래 뉴스를 끝까지 읽었습니다:
제목: {article.get('title')}

현재까지 {read_count}개 읽었습니다.

★ 현실적인 행동 기준:
- 3개 이상 읽었다면 슬슬 마무리할 때입니다
- 5개 이상 읽었다면 특별한 이유가 없는 한 종료하세요
- 시간대와 컨디션도 고려하세요

반드시 아래 JSON 형식 중 하나로만 응답하세요:
뉴스 리스트로 돌아가는 경우: {{"action": "back", "reason": "<이유 한 줄>"}}
앱을 종료하는 경우: {{"action": "exit", "reason": "<이유 한 줄>"}}"""

    res = await gemini_client.aio.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return parse_json(res.text)


async def evaluate_session(p: dict, log: dict) -> dict:
    read_titles = "\n".join(f"- {r['title']}" for r in log["read_articles"]) or "없음"

    prompt = f"""{persona_prompt(p)}

---

방금 뉴스 앱 세션을 마쳤습니다.
- 총 추천 받은 뉴스: {log['total_served']}개
- 실제 읽은 뉴스: {len(log['read_articles'])}개
- 페이지 넘김: {log['next_count']}회
- 종료 이유: {log['exit_reason']}

읽은 뉴스:
{read_titles}

이번 세션을 솔직하게 평가해주세요.
반드시 아래 JSON 형식으로만 응답하세요:
{{"satisfaction": <1-5>, "freshness": <1-5>, "relevance": <1-5>, "diversity": <1-5>, "comment": "<한 줄 총평>"}}"""

    res = await gemini_client.aio.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return parse_json(res.text)


# =============================================================================
# 1명 세션
# =============================================================================

async def run_session(p: dict) -> dict:
    name = p["name"]
    log = {
        "persona_id": p["id"],
        "persona_name": name,
        "context": p["context"],
        "served_news": [],
        "read_articles": [],
        "actions": [],
        "next_count": 0,
        "total_served": 0,
        "exit_reason": "",
        "evaluation": {},
    }

    cursor = None
    fetch_count = 0
    already_read = []
    action = None
    session_start = time.time()
    print(f"[{name}] 세션 시작")

    while fetch_count <= MAX_PAGES:
        # 추천 API 호출 (첫 호출은 cursor=None, 이후는 이전 응답의 cursor 사용)
        rec = await fetch_recommendations(p["id"], cursor)
        items = rec["items"]
        cursor = rec.get("cursor")  # 다음 페이지용 cursor 저장
        fetch_count += 1
        log["total_served"] += len(items)
        log["served_news"].append({
            "fetch": fetch_count,
            "cursor_used": cursor,
            "request_id": rec["request_id"],
            "source": rec["source"],
            "news_ids": [n["news_id"] for n in items],
        })
        print(f"  [{name}] fetch={fetch_count} | {len(items)}개 수신")

        # 리스트 탐색 루프
        while True:
            result = await judge_news_list(p, items, already_read)
            action = result.get("action")
            reason = result.get("reason", "")
            log["actions"].append({"type": action, "reason": reason, "fetch": fetch_count})
            print(f"  [{name}] → {action} | {reason}")

            if action == "select":
                news_id = result.get("news_id")
                selected = next((n for n in items if n["news_id"] == news_id), None)
                if not selected:
                    log["exit_reason"] = "뉴스 ID 매칭 실패"
                    break

                # 뉴스 상세 API 호출
                try:
                    article = await fetch_news_detail(news_id)
                except Exception:
                    article = selected

                # 클릭 직후: 계속 읽을지 바로 나갈지 판단
                entry = await judge_article_entry(p, article)
                entry_action = entry.get("action")

                t_start = time.time()

                if entry_action == "bounce":
                    # 흥미 없어서 바로 이탈 → 5~15초 대기
                    bounce_time = random.uniform(5, 15)
                    await asyncio.sleep(bounce_time)
                    dwell = round(time.time() - t_start, 2)
                    print(f"  [{name}] 이탈(bounce) id={news_id} dwell={dwell}s")
                    log["read_articles"].append({
                        "news_id": news_id,
                        "title": selected["title"],
                        "url": article.get("url"),
                        "dwell_time_sec": dwell,
                        "read_type": "bounce",
                        "after_action": "back",
                        "after_reason": entry.get("reason", ""),
                    })
                    log["actions"].append({
                        "type": "bounce",
                        "news_id": news_id,
                        "reason": entry.get("reason", ""),
                        "fetch": fetch_count,
                    })
                    continue  # 리스트로 복귀

                # 흥미 있어서 본문 읽기 → 30초~2분 대기
                read_time = random.uniform(30, 120)
                await asyncio.sleep(read_time)
                dwell = round(time.time() - t_start, 2)

                already_read.append(news_id)
                p["context"]["fatigue"] = min(p["context"]["fatigue"] + 1, 5)

                after = await judge_after_read(p, article)
                after_action = after.get("action")

                log["read_articles"].append({
                    "news_id": news_id,
                    "title": selected["title"],
                    "url": article.get("url"),
                    "dwell_time_sec": dwell,
                    "read_type": "full",
                    "after_action": after_action,
                    "after_reason": after.get("reason", ""),
                })
                log["actions"].append({
                    "type": f"after_read_{after_action}",
                    "news_id": news_id,
                    "reason": after.get("reason", ""),
                    "fetch": fetch_count,
                })
                print(f"  [{name}] 읽기 완료 id={news_id} dwell={dwell}s | 다음={after_action}")

                if after_action == "exit":
                    log["exit_reason"] = after.get("reason", "읽은 후 종료")
                    break
                continue  # back → 리스트로 복귀

            elif action == "next":
                log["next_count"] += 1
                # cursor 없으면 더 이상 뉴스 없음
                if not cursor:
                    log["exit_reason"] = "더 이상 추천 뉴스 없음"
                    action = "exit"
                # 현재 페이지에서 읽을 뉴스를 모두 읽었으면 자동 종료 판단
                unread_left = [n for n in items if n["news_id"] not in already_read]
                if not unread_left and not cursor:
                    log["exit_reason"] = "현재 페이지 뉴스 모두 읽음, 추가 추천 없음"
                    action = "exit"
                break

            elif action == "exit":
                log["exit_reason"] = reason
                break
            else:
                log["exit_reason"] = "알 수 없는 액션"
                break

        if action in ("exit", None) or log["exit_reason"]:
            break

    # 세션 종료 평가
    log["evaluation"] = await evaluate_session(p, log)
    log["total_session_time_sec"] = round(time.time() - session_start, 2)
    print(f"  [{name}] 완료 | 읽음={len(log['read_articles'])}개 | {log['evaluation']}")
    return log


# =============================================================================
# 메인: 20명 병렬 실행
# =============================================================================

async def main():

    # 각 페르소나에 랜덤 컨텍스트 부여
    personas_with_ctx = [{**p, "context": generate_context()} for p in PERSONAS]

    print(f"시뮬레이션 시작: {len(personas_with_ctx)}명 병렬 실행")
    start = datetime.now()

    results = await asyncio.gather(
        *[run_session(p) for p in personas_with_ctx],
        return_exceptions=True,
    )

    elapsed = (datetime.now() - start).total_seconds()

    # 에러 처리
    final = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            print(f"[ERROR] {personas_with_ctx[i]['name']}: {r}")
            final.append({"persona_id": personas_with_ctx[i]["id"], "persona_name": personas_with_ctx[i]["name"], "error": str(r)})
        else:
            final.append(r)

    # 집계
    ok = [r for r in final if "error" not in r]
    total_clicks  = sum(len(r.get("read_articles", [])) for r in ok)
    total_served  = sum(r.get("total_served", 0) for r in ok)
    ctr           = round(total_clicks / total_served, 4) if total_served else 0

    evals = [r["evaluation"] for r in ok if r.get("evaluation")]
    avg = lambda key: round(sum(e.get(key, 0) for e in evals) / len(evals), 2) if evals else 0

    output = {
        "simulation_id": start.strftime("%Y%m%d_%H%M%S"),
        "run_at": start.isoformat(),
        "elapsed_sec": round(elapsed, 2),
        "total_personas": len(personas_with_ctx),
        "success_count": len(ok),
        "summary": {
            "total_served": total_served,
            "total_clicks": total_clicks,
            "ctr": ctr,
            "avg_satisfaction": avg("satisfaction"),
            "avg_freshness":    avg("freshness"),
            "avg_relevance":    avg("relevance"),
            "avg_diversity":    avg("diversity"),
        },
        "results": final,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / f"simulation_{start.strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"완료 | 소요: {elapsed:.1f}초")
    print(f"CTR: {ctr} ({total_clicks}/{total_served})")
    print(f"만족도: {avg('satisfaction')} | 관련성: {avg('relevance')} | 신선도: {avg('freshness')} | 다양성: {avg('diversity')}")
    print(f"결과: {out_file}")
    print(f"{'='*50}")


if __name__ == "__main__":
    asyncio.run(main())