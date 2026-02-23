"""
Sprints integration test: GET /sprints, active-sprint-summary-by-team,
active-sprint-summary/{id}, sprint-issues-with-epic-for-llm, sprints/{id}.
"""

from __future__ import annotations

from integration_tests.client import BackendClient
from integration_tests.reports_config import TEAM_NAME

_G = "\033[32m"
_R = "\033[31m"
_N = "\033[0m"


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
    """Run the sprints test. Returns (success, message). ignore_replay: skip replay (live only)."""
    client = BackendClient(base_url)
    prefix = "/api/v1"

    # 1. GET /sprints
    r = client.get(f"{prefix}/sprints")
    if r.status_code != 200:
        _fail("GET /sprints", f"{r.status_code}")
        return False, f"sprints list: expected 200, got {r.status_code} - {r.text}"
    try:
        data = r.json()
        if not data.get("success") or "data" not in data:
            _fail("GET /sprints", "bad shape")
            return False, "sprints list: unexpected response shape"
        payload = data["data"]
        sprints = payload.get("sprints")
        if not isinstance(sprints, list):
            _fail("GET /sprints", "missing sprints list")
            return False, "sprints list: missing data.sprints"
        cnt = _count_from_response(data, "sprints")
        _pass("GET /sprints", cnt)
    except Exception as e:
        _fail("GET /sprints", str(e))
        return False, f"sprints list: {e}"

    # Use an active sprint from the list for active-sprint-summary/{id} (view only has active sprints)
    sprint_id = None
    if sprints:
        active = next((s for s in sprints if str(s.get("state") or "").lower() == "active"), None)
        sprint_id = active.get("sprint_id") if active else sprints[0].get("sprint_id")

    # 2. GET /sprints/active-sprint-summary-by-team
    r = client.get(f"{prefix}/sprints/active-sprint-summary-by-team")
    if r.status_code != 200:
        _fail("GET /sprints/active-sprint-summary-by-team", f"{r.status_code}")
        return False, f"active-sprint-summary-by-team: expected 200, got {r.status_code}"
    try:
        data = r.json()
        if not data.get("success"):
            _fail("GET /sprints/active-sprint-summary-by-team", "bad shape")
            return False, "active-sprint-summary-by-team: unexpected response shape"
        cnt = _count_from_response(data)
        _pass("GET /sprints/active-sprint-summary-by-team", cnt)
    except Exception as e:
        _fail("GET /sprints/active-sprint-summary-by-team", str(e))
        return False, f"active-sprint-summary-by-team: {e}"

    # 3–5 only if we have a sprint_id
    if sprint_id is not None:
        # 3. GET /sprints/active-sprint-summary/{sprint_id}
        r = client.get(f"{prefix}/sprints/active-sprint-summary/{sprint_id}")
        if r.status_code != 200:
            _fail("GET /sprints/active-sprint-summary/{id}", f"{r.status_code}")
            return False, f"active-sprint-summary/{{id}}: expected 200, got {r.status_code}"
        try:
            data = r.json()
            if not data.get("success") or "data" not in data:
                _fail("GET /sprints/active-sprint-summary/{id}", "bad shape")
                return False, "active-sprint-summary/{id}: unexpected response shape"
            _pass("GET /sprints/active-sprint-summary/{id}")
        except Exception as e:
            _fail("GET /sprints/active-sprint-summary/{id}", str(e))
            return False, f"active-sprint-summary/{{id}}: {e}"

        # 4. GET /sprints/sprint-issues-with-epic-for-llm (team_name required)
        r = client.get(
            f"{prefix}/sprints/sprint-issues-with-epic-for-llm",
            params={"sprint_id": sprint_id, "team_name": TEAM_NAME},
        )
        if r.status_code != 200:
            _fail("GET /sprints/sprint-issues-with-epic-for-llm", f"{r.status_code}")
            return False, f"sprint-issues-with-epic-for-llm: expected 200, got {r.status_code}"
        try:
            data = r.json()
            if not data.get("success"):
                _fail("GET /sprints/sprint-issues-with-epic-for-llm", "bad shape")
                return False, "sprint-issues-with-epic-for-llm: unexpected response shape"
            cnt = _count_from_response(data, "sprint_issues")
            _pass("GET /sprints/sprint-issues-with-epic-for-llm", cnt)
        except Exception as e:
            _fail("GET /sprints/sprint-issues-with-epic-for-llm", str(e))
            return False, f"sprint-issues-with-epic-for-llm: {e}"

        # 5. GET /sprints/{sprint_id}
        r = client.get(f"{prefix}/sprints/{sprint_id}")
        if r.status_code != 200:
            _fail("GET /sprints/{id}", f"{r.status_code}")
            return False, f"sprints/{{id}}: expected 200, got {r.status_code}"
        try:
            data = r.json()
            if not data.get("success") or "data" not in data:
                _fail("GET /sprints/{id}", "bad shape")
                return False, "sprints/{id}: unexpected response shape"
            single = data["data"].get("sprint") or data["data"]
            if single.get("sprint_id") != sprint_id:
                _fail("GET /sprints/{id}", "wrong sprint_id")
                return False, "sprints/{id}: sprint_id mismatch"
            _pass("GET /sprints/{id}")
        except Exception as e:
            _fail("GET /sprints/{id}", str(e))
            return False, f"sprints/{{id}}: {e}"

    return True, "ok"
