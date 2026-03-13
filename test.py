"""
뉴스 추천 시뮬레이션 - personas_tokens.json 기반
실행: python test.py
"""

import asyncio
import json
import random
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from google import genai

from app.config import get_settings

# =============================================================================
# 설정
# =============================================================================

BASE_URL    = "http://localhost:8000"
MAX_PAGES   = 10
OUTPUT_DIR  = Path("simulation/results")
TOKENS_FILE = OUTPUT_DIR / "personas_tokens.json"
DETAILS_FILE  = OUTPUT_DIR / "persona_details.json"
CONFIG_FILE   = OUTPUT_DIR / "simulation_config.json"

settings = get_settings()
gemini_client = genai.Client(api_key=settings.gemini_api)
GEMINI_MODEL  = "gemini-2.0-flash"

# =============================================================================
# 데이터 로드
# =============================================================================

def load_config() -> dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def load_personas() -> list[dict]:
    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        records = json.load(f)
    with open(DETAILS_FILE, "r", encoding="utf-8") as f:
        details = json.load(f)

    personas = []
    for r in records:
        if r.get("error") or not r.get("token"):
            print(f"  [건너뜀] {r['persona_name']} - 토큰 없음")
            continue
        pid    = r["persona_id"]
        detail = details.get(pid, {})
        personas.append({
            "id":          pid,
            "name":        r["persona_name"],
            "google_id":   r["google_id"],
            "email":       r["email"],
            "nickname":    r["nickname"],
            "user_id":     r.get("user_id"),
            "token":       r["token"],
            "age_group":   detail.get("age_group", "미상"),
            "job":         detail.get("job", "미상"),
            "description": detail.get("description", ""),
            "stocks":      detail.get("stocks", []),
            "keywords":    detail.get("keywords", []),
        })
    return personas

# =============================================================================
# 유틸
# =============================================================================

def generate_context(cfg: dict) -> dict:
    time_slot = random.choice(cfg["time_slots"])
    return {
        "attitude":              random.choice(cfg["attitudes"]),
        "time_slot":             time_slot["label"],
        "time_slot_description": time_slot["description"],
        "condition":             random.choice(cfg["conditions"]),
        "fatigue":               0,
        "random_seed":           random.randint(1000, 9999),
    }

def parse_json(text: str) -> dict:
    clean = re.sub(r"```json|```", "", text).strip()
    return json.loads(clean)

def persona_prompt(p: dict) -> str:
    stocks   = ", ".join(p.get("stocks", [])) or "없음"
    keywords = ", ".join(p.get("keywords", [])) or "없음"
    ctx      = p["context"]
    return f"""당신은 다음과 같은 사람입니다:
- 이름: {p['name']} / 나이대: {p['age_group']} / 직업: {p['job']}
- 특징: {p['description']}
- 관심 종목: {stocks}
- 관심 키워드: {keywords}

오늘의 상황:
- 시간대: {ctx['time_slot']} -> {ctx['time_slot_description']}
- 컨디션: {ctx['condition']}
- 태도: {ctx['attitude']} (태도는 읽는 깊이/종료 타이밍에만 영향. 클릭은 순수 관심도 기반)
- 현재 피로도: {ctx['fatigue']}단계 (0=없음, 높을수록 종료 가능성 증가)"""

# =============================================================================
# API 호출
# =============================================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

async def post_events(events: list[dict], headers: dict = None) -> None:
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{BASE_URL}/api/interactions/events",
            json={"events": events},
            headers=headers or {},
            timeout=10.0,
        )

async def fetch_recommendations(
    cursor: str = None,
    app_session_id: str = None,
    screen_session_id: str = None,
    request_id: str = None,
    page: int = 1,
    headers: dict = None,
) -> dict:
    params = {
        "limit":             20,
        "log_served":        False,
        "app_session_id":    app_session_id,
        "screen_session_id": screen_session_id,
        "request_id":        request_id,
        "page":              page,
    }
    if cursor:
        params["cursor"] = cursor
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"{BASE_URL}/api/news/recommendations",
            params=params,
            headers=headers or {},
            timeout=15.0,
        )
        res.raise_for_status()
        data = res.json()
        return {
            "request_id": data.get("request_id", request_id),
            "source":     data.get("source"),
            "cursor":     data.get("next_cursor"),
            "page":       data.get("page", page),
            "items":      data.get("items", []),
        }

async def fetch_news_detail(news_id: int, headers: dict = None) -> dict:
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"{BASE_URL}/api/news/{news_id}",
            headers=headers or {},
            timeout=15.0,
        )
        res.raise_for_status()
        return res.json()

# =============================================================================
# LLM 판단
# =============================================================================

async def judge_news_list(p: dict, items: list, already_read: list, fetch_count: int = 1) -> dict:
    unread_items = [item for item in items if item["news_id"] not in already_read]
    if not unread_items:
        return {"action": "next", "reason": "읽지 않은 뉴스가 없음"}

    news_text = ""
    valid_ids = []
    for i, item in enumerate(unread_items, 1):
        news_text += f"{i}. [ID:{item['news_id']}] {item['title']}\n   요약: {str(item.get('summary',''))[:100]}...\n\n"
        valid_ids.append(item["news_id"])

    prompt = f"""{persona_prompt(p)}

---

지금 {fetch_count}페이지째 보고 있습니다.
아래는 현재 추천된 뉴스 목록입니다 (이미 읽은 뉴스는 제외됨):

{news_text}
제목만 빠르게 훑어보고 행동을 결정하세요.

★ 클릭 기준 (엄격하게):
- 제목 보자마자 바로 흥미가 생기는 뉴스만 클릭합니다
- 키워드와 직접 관련 없는 뉴스를 억지로 연결짓지 마세요
- 관심 분야가 아니더라도 제목이 강하게 끌리면 클릭할 수 있습니다 (사람은 다양한 뉴스를 봅니다)
- 20개 중 0~2개만 클릭하는 게 자연스럽습니다. 마음에 드는 게 없으면 next나 exit 하세요
- bounce한 주제와 비슷한 뉴스는 다시 클릭하지 마세요

★ 페이지 기준 (중요):
- 1~2페이지: next 가능
- 3~4페이지: 마음에 드는 게 없으면 exit을 고려하세요
- 5페이지 이상: 특별히 흥미로운 뉴스가 없으면 exit하세요
- 7페이지 이상: 거의 무조건 exit입니다

★ 중요 규칙:
- news_id는 반드시 위 목록에 있는 ID 중 하나를 그대로 사용하세요
- 사용 가능한 ID 목록: {valid_ids}
- 목록에 없는 ID를 임의로 만들지 마세요

반드시 아래 JSON 형식 중 하나로만 응답하세요:
읽고 싶은 뉴스가 있는 경우: {{"action": "select", "news_id": <위 목록의 ID 중 하나>, "reason": "<이유 한 줄>"}}
더 보고 싶지만 원하는 뉴스가 없는 경우: {{"action": "next", "reason": "<이유 한 줄>"}}
탐색을 마치고 싶은 경우: {{"action": "exit", "reason": "<이유 한 줄>"}}"""

    res    = await gemini_client.aio.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    result = parse_json(res.text)
    if result.get("action") == "select" and result.get("news_id") not in valid_ids:
        return {"action": "next", "reason": f"유효하지 않은 news_id 반환 (id={result.get('news_id')})"}
    return result

async def judge_article_entry(p: dict, article: dict) -> dict:
    prompt = f"""{persona_prompt(p)}

---

방금 아래 뉴스를 클릭했습니다:
제목: {article.get('title')}
요약 첫 줄: {str(article.get('summary', ''))[:80]}...

첫 인상만 보고 계속 읽을지 바로 나갈지 판단하세요.

★ 현실적인 기준:
- 지금까지 이미 {p['context']['fatigue']}개 읽었습니다
- 기대했던 내용이 아니거나 생각보다 흥미롭지 않으면 바로 나가세요
- 읽은 개수가 많을수록 기준이 더 엄격해집니다
- 조금이라도 망설여지면 bounce입니다

반드시 아래 JSON 형식 중 하나로만 응답하세요:
계속 읽는 경우: {{"action": "read", "read_seconds": <이 사람이 이 기사를 읽는데 걸릴 초 (30~300 사이)>, "reason": "<이유 한 줄>"}}
바로 나가는 경우: {{"action": "bounce", "read_seconds": <이 사람이 훑어보다 나가는 시간 (5~20 사이)>, "reason": "<이유 한 줄>"}}"""

    res = await gemini_client.aio.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return parse_json(res.text)

async def judge_after_read(p: dict, article: dict) -> dict:
    prompt = f"""{persona_prompt(p)}

---

방금 아래 뉴스를 끝까지 읽었습니다:
제목: {article.get('title')}

현재까지 {p['context']['fatigue']}개 읽었습니다.

★ 현실적인 기준:
- 추천이 마음에 들었다면 리스트로 돌아가고, 아니면 그냥 나가세요
- 읽은 뉴스가 기대에 못 미쳤다면 exit 확률이 높아집니다
- 1~2개 읽었어도 더 볼 의욕이 없으면 exit해도 됩니다
- 4개 이상 읽었다면 exit을 강하게 고려하세요
- 억지로 더 읽을 필요 없습니다

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
    name    = p["name"]
    headers = {"Authorization": f"Bearer {p['token']}"}

    app_session_id    = f"sim-app-{p['id']}-{uuid.uuid4().hex[:8]}"
    screen_session_id = f"sim-screen-{p['id']}-{uuid.uuid4().hex[:8]}"

    log = {
        "persona_id":        p["id"],
        "persona_name":      name,
        "google_id":         p["google_id"],
        "email":             p["email"],
        "user_id":           p.get("user_id"),
        "token":             p["token"],
        "context":           p["context"],
        "app_session_id":    app_session_id,
        "screen_session_id": screen_session_id,
        "served_news":       [],
        "read_articles":     [],
        "actions":           [],
        "next_count":        0,
        "total_served":      0,
        "exit_reason":       "",
        "evaluation":        {},
    }

    cursor        = None
    fetch_count   = 0
    already_read  = []
    current_page  = 1
    action        = None
    session_start = time.time()
    print(f"[{name}] 세션 시작")

    await post_events([{
        "event_id":          f"evt-{uuid.uuid4().hex[:12]}",
        "user_id":           p.get("user_id"),
        "event_type":        "screen_view",
        "app_session_id":    app_session_id,
        "screen_session_id": screen_session_id,
        "event_ts_client":   now_iso(),
    }], headers=headers)

    while fetch_count < MAX_PAGES:
        request_id   = f"sim-req-{p['id']}-{uuid.uuid4().hex[:8]}"
        current_page = fetch_count + 1

        await post_events([{
            "event_id":          f"evt-{uuid.uuid4().hex[:12]}",
            "user_id":           p.get("user_id"),
            "event_type":        "recommendation_request",
            "app_session_id":    app_session_id,
            "screen_session_id": screen_session_id,
            "request_id":        request_id,
            "event_ts_client":   now_iso(),
            "page":              current_page,
        }], headers=headers)

        rec = await fetch_recommendations(
            cursor=cursor,
            app_session_id=app_session_id,
            screen_session_id=screen_session_id,
            request_id=request_id,
            page=current_page,
            headers=headers,
        )
        items        = rec["items"]
        cursor       = rec.get("cursor")
        fetch_count += 1
        log["total_served"] += len(items)

        await post_events([{
            "event_id":          f"evt-{uuid.uuid4().hex[:12]}",
            "user_id":           p.get("user_id"),
            "event_type":        "recommendation_response",
            "app_session_id":    app_session_id,
            "screen_session_id": screen_session_id,
            "request_id":        request_id,
            "event_ts_client":   now_iso(),
            "page":              current_page,
        }], headers=headers)

        log["served_news"].append({
            "fetch":      fetch_count,
            "request_id": request_id,
            "source":     rec["source"],
            "news_ids":   [n["news_id"] for n in items],
        })
        print(f"  [{name}] fetch={fetch_count} | {len(items)}개 수신")

        position_map = {item["news_id"]: i+1 for i, item in enumerate(items)}

        while True:
            result = await judge_news_list(p, items, already_read, fetch_count)
            action = result.get("action")
            reason = result.get("reason", "")
            log["actions"].append({"type": action, "reason": reason, "fetch": fetch_count})
            print(f"  [{name}] -> {action} | {reason}")

            if action == "select":
                news_id  = result.get("news_id")
                selected = next((n for n in items if n["news_id"] == news_id), None)
                if not selected:
                    log["exit_reason"] = "뉴스 ID 매칭 실패"
                    break

                try:
                    article = await fetch_news_detail(news_id, headers=headers)
                except Exception:
                    article = selected

                entry        = await judge_article_entry(p, article)
                entry_action = entry.get("action")

                content_session_id = f"sim-content-{p['id']}-{uuid.uuid4().hex[:8]}"
                t_start = time.time()

                await post_events([{
                    "event_id":           f"evt-{uuid.uuid4().hex[:12]}",
                    "user_id":            p.get("user_id"),
                    "event_type":         "content_open",
                    "app_session_id":     app_session_id,
                    "screen_session_id":  screen_session_id,
                    "content_session_id": content_session_id,
                    "request_id":         request_id,
                    "event_ts_client":    now_iso(),
                    "news_id":            news_id,
                    "position":           position_map.get(news_id, 0),
                    "page":               current_page,
                }], headers=headers)

                if entry_action == "bounce":
                    dwell_seconds = float(entry.get("read_seconds", random.uniform(5, 20)))
                    await asyncio.sleep(dwell_seconds)
                    dwell = round(time.time() - t_start, 2)
                    already_read.append(news_id)  # bounce도 다시 안 보이게
                    await post_events([{
                        "event_id":           f"evt-{uuid.uuid4().hex[:12]}",
                        "user_id":            p.get("user_id"),
                        "event_type":         "content_leave",
                        "app_session_id":     app_session_id,
                        "content_session_id": content_session_id,
                        "event_ts_client":    now_iso(),
                    }], headers=headers)
                    print(f"  [{name}] 이탈(bounce) id={news_id} dwell={dwell}s")
                    log["read_articles"].append({
                        "news_id": news_id, "title": selected["title"],
                        "url": article.get("url"), "dwell_time_sec": dwell,
                        "read_type": "bounce", "after_action": "back",
                        "after_reason": entry.get("reason", ""),
                    })
                    log["actions"].append({"type": "bounce", "news_id": news_id, "reason": entry.get("reason", ""), "fetch": fetch_count})
                    continue

                dwell_seconds = float(entry.get("read_seconds", random.uniform(30, 120)))
                await asyncio.sleep(dwell_seconds)
                dwell = round(time.time() - t_start, 2)
                already_read.append(news_id)
                p["context"]["fatigue"] = min(p["context"]["fatigue"] + 1, 5)

                after        = await judge_after_read(p, article)
                after_action = after.get("action")

                await post_events([{
                    "event_id":           f"evt-{uuid.uuid4().hex[:12]}",
                    "user_id":            p.get("user_id"),
                    "event_type":         "content_leave",
                    "app_session_id":     app_session_id,
                    "content_session_id": content_session_id,
                    "event_ts_client":    now_iso(),
                }], headers=headers)

                log["read_articles"].append({
                    "news_id": news_id, "title": selected["title"],
                    "url": article.get("url"), "dwell_time_sec": dwell,
                    "read_type": "full", "after_action": after_action,
                    "after_reason": after.get("reason", ""),
                })
                log["actions"].append({"type": f"after_read_{after_action}", "news_id": news_id, "reason": after.get("reason", ""), "fetch": fetch_count})
                print(f"  [{name}] 읽기 완료 id={news_id} dwell={dwell}s | 다음={after_action}")

                if after_action == "exit":
                    log["exit_reason"] = after.get("reason", "읽은 후 종료")
                    break
                continue

            elif action == "next":
                log["next_count"] += 1
                await post_events([{
                    "event_id":          f"evt-{uuid.uuid4().hex[:12]}",
                    "user_id":           p.get("user_id"),
                    "event_type":        "scroll_depth",
                    "app_session_id":    app_session_id,
                    "screen_session_id": screen_session_id,
                    "request_id":        request_id,
                    "event_ts_client":   now_iso(),
                    "page":              current_page,
                    "scroll_depth":      95.0,
                }], headers=headers)
                if not cursor:
                    log["exit_reason"] = "더 이상 추천 뉴스 없음"
                    action = "exit"
                if not [n for n in items if n["news_id"] not in already_read] and not cursor:
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

    await post_events([{
        "event_id":          f"evt-{uuid.uuid4().hex[:12]}",
        "user_id":           p.get("user_id"),
        "event_type":        "screen_leave",
        "app_session_id":    app_session_id,
        "screen_session_id": screen_session_id,
        "event_ts_client":   now_iso(),
    }], headers=headers)

    log["evaluation"]             = await evaluate_session(p, log)
    log["total_session_time_sec"] = round(time.time() - session_start, 2)
    print(f"  [{name}] 완료 | 읽음={len(log['read_articles'])}개 | {log['evaluation']}")
    return log

# =============================================================================
# 메인
# =============================================================================

async def main():
    cfg      = load_config()
    personas = load_personas()
    if not personas:
        print("토큰 파일에 유효한 페르소나가 없습니다. 먼저 get_tokens.py를 실행하세요.")
        return

    personas_with_ctx = [{**p, "context": generate_context(cfg)} for p in personas[:1]]

    print(f"\n시뮬레이션 시작: {len(personas_with_ctx)}명 병렬 실행")
    start = datetime.now()

    results = await asyncio.gather(
        *[run_session(p) for p in personas_with_ctx],
        return_exceptions=True,
    )

    elapsed = (datetime.now() - start).total_seconds()

    final = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            print(f"[ERROR] {personas_with_ctx[i]['name']}: {r}")
            final.append({
                "persona_id":   personas_with_ctx[i]["id"],
                "persona_name": personas_with_ctx[i]["name"],
                "token":        personas_with_ctx[i]["token"],
                "error":        str(r),
            })
        else:
            final.append(r)

    ok           = [r for r in final if "error" not in r]
    total_clicks = sum(len(r.get("read_articles", [])) for r in ok)
    total_served = sum(r.get("total_served", 0) for r in ok)
    ctr          = round(total_clicks / total_served, 4) if total_served else 0

    evals = [r["evaluation"] for r in ok if r.get("evaluation")]
    avg   = lambda key: round(sum(e.get(key, 0) for e in evals) / len(evals), 2) if evals else 0

    output = {
        "simulation_id":  start.strftime("%Y%m%d_%H%M%S"),
        "run_at":         start.isoformat(),
        "elapsed_sec":    round(elapsed, 2),
        "total_personas": len(personas_with_ctx),
        "success_count":  len(ok),
        "summary": {
            "total_served":     total_served,
            "total_clicks":     total_clicks,
            "ctr":              ctr,
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