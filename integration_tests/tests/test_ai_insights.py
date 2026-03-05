"""
AI insights integration test (read-only; no POST).
GET /ai-insights, getTopCards, getTopCardsWithRecommendations,
then GET /ai-insights/{id} for the first 4 insights in the list.
"""

from __future__ import annotations

from integration_tests.client import BackendClient
from integration_tests.reports_config import TEAM_NAME, GROUP_NAME

_G = "\033[32m"
_R = "\033[31m"
_N = "\033[0m"

# How many insight ids to fetch by id (first N in list)
GET_BY_ID_LIMIT = 4


def _count_from_response(data: dict, list_key: str | None = None) -> int | None:
    """Extract count from response data (data.count or len(data[list_key]))."""
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(payload, dict):
        return None
    if "count" in payload and isinstance(payload["count"], (int, float)):
        return int(payload["count"])
    if list_key and list_key in payload and isinstance(payload[list_key], list):
        return len(payload[list_key])
    return None


def _pass(endpoint: str, count: int | None = None) -> None:
    if count is not None:
        print(f"  {_G}PASS{_N} {endpoint} count={count}")
    else:
        print(f"  {_G}PASS{_N} {endpoint}")


def _fail(endpoint: str, msg: str) -> None:
    print(f"  {_R}FAIL{_N} {endpoint}: {msg}")


def run(base_url: str, ignore_replay: bool = False) -> tuple[bool, str]:
    """Run the AI insights test. Returns (success, message). ignore_replay: skip replay (live only)."""
    client = BackendClient(base_url)
    prefix = "/api/v1"

    # 1. GET /ai-insights (limit high enough to have at least 4 if available)
    r = client.get(f"{prefix}/ai-insights", params={"limit": 20})
    if r.status_code != 200:
        _fail("GET /ai-insights", f"{r.status_code}")
        return False, f"ai-insights list: expected 200, got {r.status_code}"
    try:
        data = r.json()
        if not data.get("success") or "data" not in data:
            _fail("GET /ai-insights", "bad shape")
            return False, "ai-insights list: unexpected response shape"
        payload = data["data"]
        cards = payload.get("cards")
        if not isinstance(cards, list):
            _fail("GET /ai-insights", "missing cards list")
            return False, "ai-insights list: missing data.cards"
        cnt = _count_from_response(data, "cards")
        _pass("GET /ai-insights", cnt)
    except Exception as e:
        _fail("GET /ai-insights", str(e))
        return False, f"ai-insights list: {e}"

    # 2. GET /ai-insights/getTopCards (team + group, 2 calls – same as reports)
    r = client.get(f"{prefix}/ai-insights/getTopCards", params={"team_name": TEAM_NAME})
    if r.status_code != 200:
        _fail("GET /ai-insights/getTopCards (team)", f"{r.status_code}")
        return False, f"getTopCards (team): expected 200, got {r.status_code}"
    try:
        data = r.json()
        if not data.get("success"):
            _fail("GET /ai-insights/getTopCards (team)", "bad shape")
            return False, "getTopCards (team): unexpected response shape"
        cnt = _count_from_response(data, "ai_cards")
        _pass("GET /ai-insights/getTopCards (team)", cnt)
    except Exception as e:
        _fail("GET /ai-insights/getTopCards (team)", str(e))
        return False, f"getTopCards (team): {e}"

    r = client.get(f"{prefix}/ai-insights/getTopCards", params={"group_name": GROUP_NAME})
    if r.status_code != 200:
        _fail("GET /ai-insights/getTopCards (group)", f"{r.status_code}")
        return False, f"getTopCards (group): expected 200, got {r.status_code}"
    try:
        data = r.json()
        if not data.get("success"):
            _fail("GET /ai-insights/getTopCards (group)", "bad shape")
            return False, "getTopCards (group): unexpected response shape"
        cnt = _count_from_response(data, "ai_cards")
        _pass("GET /ai-insights/getTopCards (group)", cnt)
    except Exception as e:
        _fail("GET /ai-insights/getTopCards (group)", str(e))
        return False, f"getTopCards (group): {e}"

    # 3. GET /ai-insights/getTopCardsWithRecommendations (team + group, 2 calls)
    r = client.get(f"{prefix}/ai-insights/getTopCardsWithRecommendations", params={"team_name": TEAM_NAME})
    if r.status_code != 200:
        _fail("GET /ai-insights/getTopCardsWithRecommendations (team)", f"{r.status_code}")
        return False, f"getTopCardsWithRecommendations (team): expected 200, got {r.status_code}"
    try:
        data = r.json()
        if not data.get("success"):
            _fail("GET /ai-insights/getTopCardsWithRecommendations (team)", "bad shape")
            return False, "getTopCardsWithRecommendations (team): unexpected response shape"
        cnt = _count_from_response(data, "ai_cards")
        _pass("GET /ai-insights/getTopCardsWithRecommendations (team)", cnt)
    except Exception as e:
        _fail("GET /ai-insights/getTopCardsWithRecommendations (team)", str(e))
        return False, f"getTopCardsWithRecommendations (team): {e}"

    r = client.get(f"{prefix}/ai-insights/getTopCardsWithRecommendations", params={"group_name": GROUP_NAME})
    if r.status_code != 200:
        _fail("GET /ai-insights/getTopCardsWithRecommendations (group)", f"{r.status_code}")
        return False, f"getTopCardsWithRecommendations (group): expected 200, got {r.status_code}"
    try:
        data = r.json()
        if not data.get("success"):
            _fail("GET /ai-insights/getTopCardsWithRecommendations (group)", "bad shape")
            return False, "getTopCardsWithRecommendations (group): unexpected response shape"
        cnt = _count_from_response(data, "ai_cards")
        _pass("GET /ai-insights/getTopCardsWithRecommendations (group)", cnt)
    except Exception as e:
        _fail("GET /ai-insights/getTopCardsWithRecommendations (group)", str(e))
        return False, f"getTopCardsWithRecommendations (group): {e}"

    # 4. GET /ai-insights/{id} for the first 4 (or fewer if list has fewer)
    first_n = cards[:GET_BY_ID_LIMIT]
    for i, card in enumerate(first_n):
        card_id = card.get("id")
        if card_id is None:
            _fail(f"GET /ai-insights/{{id}} (card[{i}])", "missing id")
            return False, f"card at index {i} has no id"
        r = client.get(f"{prefix}/ai-insights/{card_id}")
        if r.status_code != 200:
            _fail(f"GET /ai-insights/{{id}} id={card_id}", f"{r.status_code}")
            return False, f"ai-insights/{{id}}: expected 200, got {r.status_code} for id={card_id}"
        try:
            data = r.json()
            if not data.get("success") or "data" not in data:
                _fail(f"GET /ai-insights/{{id}} id={card_id}", "bad shape")
                return False, f"ai-insights/{{id}}: unexpected response shape for id={card_id}"
            single = (data["data"].get("card") or data["data"])
            if single.get("id") != card_id:
                _fail(f"GET /ai-insights/{{id}} id={card_id}", "id mismatch")
                return False, f"ai-insights/{{id}}: response id mismatch for id={card_id}"
            _pass(f"GET /ai-insights/{{id}} id={card_id}")
        except Exception as e:
            _fail(f"GET /ai-insights/{{id}} id={card_id}", str(e))
            return False, f"ai-insights/{{id}}: {e}"

    return True, "ok"
