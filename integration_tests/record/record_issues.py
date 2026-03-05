#!/usr/bin/env python3
"""
Record issues flow (same as test_issues): current-and-next, GET /issues, /issues/list,
issue-types-hierarchy, epics-by-pi (team + group), validations/summary, validations/issues.
"""

from __future__ import annotations

import argparse
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from integration_tests.client import BackendClient
from integration_tests.record.record_common import save_recording
from integration_tests.reports_config import TEAM_NAME, GROUP_NAME

PREFIX = "/api/v1"


def _resp_body(r) -> object:
    try:
        return r.json()
    except Exception:
        return r.text


def main() -> int:
    parser = argparse.ArgumentParser(description="Record issues responses")
    parser.add_argument("--base-url", default=os.environ.get("BACKEND_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--out", required=True, help="Output directory for recordings")
    args = parser.parse_args()
    client = BackendClient(args.base_url.rstrip("/"))
    out_dir = args.out

    # 1. GET /pis/current-and-next
    r = client.get(f"{PREFIX}/pis/current-and-next")
    save_recording(out_dir, "GET", f"{PREFIX}/pis/current-and-next", None, None, r.status_code, _resp_body(r))
    current_pi = None
    if r.status_code == 200:
        data = r.json()
        current_pis = (data.get("data") or {}).get("current_pis") or []
        if current_pis and isinstance(current_pis[0], dict):
            current_pi = current_pis[0].get("pi_name")

    # 2. GET /issues (limit=10)
    params = {"limit": 10}
    r = client.get(f"{PREFIX}/issues", params=params)
    save_recording(out_dir, "GET", f"{PREFIX}/issues", params, None, r.status_code, _resp_body(r))

    # 3. GET /issues/list
    params = {"limit": 10}
    r = client.get(f"{PREFIX}/issues/list", params=params)
    save_recording(out_dir, "GET", f"{PREFIX}/issues/list", params, None, r.status_code, _resp_body(r))

    # 4. GET /issues/issue-types-hierarchy
    r = client.get(f"{PREFIX}/issues/issue-types-hierarchy")
    save_recording(out_dir, "GET", f"{PREFIX}/issues/issue-types-hierarchy", None, None, r.status_code, _resp_body(r))

    # 5. GET /issues/epics-by-pi (team and group)
    if current_pi:
        for label, team_param in [("team", TEAM_NAME), ("group", GROUP_NAME)]:
            q = {"pi": current_pi, "team_name": team_param}
            if label == "group":
                q["isGroup"] = True
            r = client.get(f"{PREFIX}/issues/epics-by-pi", params=q)
            save_recording(out_dir, "GET", f"{PREFIX}/issues/epics-by-pi", q, None, r.status_code, _resp_body(r), slug_extra=label)

    # 6. GET /issues/validations/summary
    r = client.get(f"{PREFIX}/issues/validations/summary")
    save_recording(out_dir, "GET", f"{PREFIX}/issues/validations/summary", None, None, r.status_code, _resp_body(r))

    # 7. GET /issues/validations/issues
    r = client.get(f"{PREFIX}/issues/validations/issues")
    save_recording(out_dir, "GET", f"{PREFIX}/issues/validations/issues", None, None, r.status_code, _resp_body(r))

    print(f"Issues: recorded to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
