"""
Issues integration test: current PI + fixed team/group (reports_config).
GET /pis/current-and-next, /issues, /issues/list, /issues/issue-types-hierarchy,
/issues/epics-by-pi (team + group), /issues/validations/summary, /issues/validations/issues.
"""

from __future__ import annotations

from integration_tests.client import BackendClient
from integration_tests.reports_config import TEAM_NAME, GROUP_NAME

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
    """Run the issues test. Returns (success, message). ignore_replay: skip replay (live only)."""
    client = BackendClient(base_url)
    prefix = "/api/v1"

    # 1. GET /pis/current-and-next -> current_pi
    r = client.get(f"{prefix}/pis/current-and-next")
    if r.status_code != 200:
        _fail("GET /pis/current-and-next", f"{r.status_code}")
        return False, f"current-and-next: expected 200, got {r.status_code}"
    try:
        data = r.json()
        if not data.get("success") or "data" not in data:
            _fail("GET /pis/current-and-next", "bad shape")
            return False, "current-and-next: unexpected response shape"
        current_pis = (data.get("data") or {}).get("current_pis") or []
        current_pi = current_pis[0].get("pi_name") if current_pis and isinstance(current_pis[0], dict) else None
    except Exception as e:
        _fail("GET /pis/current-and-next", str(e))
        return False, f"current-and-next: {e}"
    _pass("GET /pis/current-and-next")

    # 2. GET /issues (limit=10)
    r = client.get(f"{prefix}/issues", params={"limit": 10})
    if r.status_code != 200:
        _fail("GET /issues", f"{r.status_code}")
        return False, f"issues: expected 200, got {r.status_code}"
    try:
        data = r.json()
        if not data.get("success") or "data" not in data:
            _fail("GET /issues", "bad shape")
            return False, "issues: unexpected response shape"
        cnt = _count_from_response(data, "issues")
        _pass("GET /issues", cnt)
    except Exception as e:
        _fail("GET /issues", str(e))
        return False, f"issues: {e}"

    # 3. GET /issues/list
    r = client.get(f"{prefix}/issues/list", params={"limit": 10})
    if r.status_code != 200:
        _fail("GET /issues/list", f"{r.status_code}")
        return False, f"issues/list: expected 200, got {r.status_code}"
    try:
        data = r.json()
        if not data.get("success"):
            _fail("GET /issues/list", "bad shape")
            return False, "issues/list: unexpected response shape"
        cnt = _count_from_response(data, "issues")
        _pass("GET /issues/list", cnt)
    except Exception as e:
        _fail("GET /issues/list", str(e))
        return False, f"issues/list: {e}"

    # 4. GET /issues/issue-types-hierarchy
    r = client.get(f"{prefix}/issues/issue-types-hierarchy")
    if r.status_code != 200:
        _fail("GET /issues/issue-types-hierarchy", f"{r.status_code}")
        return False, f"issue-types-hierarchy: expected 200, got {r.status_code}"
    try:
        data = r.json()
        if not data.get("success"):
            _fail("GET /issues/issue-types-hierarchy", "bad shape")
            return False, "issue-types-hierarchy: unexpected response shape"
        payload = data.get("data")
        cnt = len(payload) if isinstance(payload, list) else _count_from_response(data)
        _pass("GET /issues/issue-types-hierarchy", cnt)
    except Exception as e:
        _fail("GET /issues/issue-types-hierarchy", str(e))
        return False, f"issue-types-hierarchy: {e}"

    # 5. GET /issues/epics-by-pi (team and group) when we have current_pi
    if current_pi:
        r = client.get(
            f"{prefix}/issues/epics-by-pi",
            params={"pi": current_pi, "team_name": TEAM_NAME},
        )
        if r.status_code != 200:
            _fail("GET /issues/epics-by-pi (team)", f"{r.status_code}")
            return False, f"epics-by-pi (team): expected 200, got {r.status_code}"
        try:
            data = r.json()
            if not data.get("success"):
                _fail("GET /issues/epics-by-pi (team)", "bad shape")
                return False, "epics-by-pi (team): unexpected response shape"
            cnt = _count_from_response(data, "epics")
            _pass("GET /issues/epics-by-pi (team)", cnt)
        except Exception as e:
            _fail("GET /issues/epics-by-pi (team)", str(e))
            return False, f"epics-by-pi (team): {e}"

        r = client.get(
            f"{prefix}/issues/epics-by-pi",
            params={"pi": current_pi, "team_name": GROUP_NAME, "isGroup": True},
        )
        if r.status_code != 200:
            _fail("GET /issues/epics-by-pi (group)", f"{r.status_code}")
            return False, f"epics-by-pi (group): expected 200, got {r.status_code}"
        try:
            data = r.json()
            if not data.get("success"):
                _fail("GET /issues/epics-by-pi (group)", "bad shape")
                return False, "epics-by-pi (group): unexpected response shape"
            cnt = _count_from_response(data, "epics")
            _pass("GET /issues/epics-by-pi (group)", cnt)
        except Exception as e:
            _fail("GET /issues/epics-by-pi (group)", str(e))
            return False, f"epics-by-pi (group): {e}"

    # 6. GET /issues/validations/summary (returns list of metric cards, no success wrapper)
    r = client.get(f"{prefix}/issues/validations/summary")
    if r.status_code != 200:
        _fail("GET /issues/validations/summary", f"{r.status_code}")
        return False, f"validations/summary: expected 200, got {r.status_code}"
    try:
        data = r.json()
        if not isinstance(data, list):
            _fail("GET /issues/validations/summary", "bad shape")
            return False, "validations/summary: expected list"
        _pass("GET /issues/validations/summary", len(data))
    except Exception as e:
        _fail("GET /issues/validations/summary", str(e))
        return False, f"validations/summary: {e}"

    # 7. GET /issues/validations/issues (returns dict with validations list, no success wrapper)
    r = client.get(f"{prefix}/issues/validations/issues")
    if r.status_code != 200:
        _fail("GET /issues/validations/issues", f"{r.status_code}")
        return False, f"validations/issues: expected 200, got {r.status_code}"
    try:
        data = r.json()
        if not isinstance(data, dict) or "validations" not in data:
            _fail("GET /issues/validations/issues", "bad shape")
            return False, "validations/issues: expected dict with validations"
        cnt = len(data["validations"]) if isinstance(data["validations"], list) else None
        _pass("GET /issues/validations/issues", cnt)
    except Exception as e:
        _fail("GET /issues/validations/issues", str(e))
        return False, f"validations/issues: {e}"

    return True, "ok"
