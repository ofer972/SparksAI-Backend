#!/usr/bin/env python3
"""
Record sprints flow (same as test_sprints): GET /sprints, active-sprint-summary-by-team,
active-sprint-summary/{id}, sprint-issues-with-epic-for-llm, sprints/{id}.
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
from integration_tests.reports_config import TEAM_NAME

PREFIX = "/api/v1"


def _resp_body(r) -> object:
    try:
        return r.json()
    except Exception:
        return r.text


def main() -> int:
    parser = argparse.ArgumentParser(description="Record sprints responses")
    parser.add_argument("--base-url", default=os.environ.get("BACKEND_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--out", required=True, help="Output directory for recordings")
    args = parser.parse_args()
    client = BackendClient(args.base_url.rstrip("/"))
    out_dir = args.out

    # 1. GET /sprints
    r = client.get(f"{PREFIX}/sprints")
    save_recording(out_dir, "GET", f"{PREFIX}/sprints", None, None, r.status_code, _resp_body(r))

    data = r.json() if r.status_code == 200 else {}
    payload = (data.get("data") or {}) if isinstance(data, dict) else {}
    sprints = payload.get("sprints") if isinstance(payload, dict) else []
    sprint_id = None
    if sprints:
        active = next((s for s in sprints if str(s.get("state") or "").lower() == "active"), None)
        sprint_id = active.get("sprint_id") if active else sprints[0].get("sprint_id")

    # 2. GET /sprints/active-sprint-summary-by-team
    r = client.get(f"{PREFIX}/sprints/active-sprint-summary-by-team")
    save_recording(out_dir, "GET", f"{PREFIX}/sprints/active-sprint-summary-by-team", None, None, r.status_code, _resp_body(r))

    if sprint_id is not None:
        # 3. GET /sprints/active-sprint-summary/{sprint_id}
        r = client.get(f"{PREFIX}/sprints/active-sprint-summary/{sprint_id}")
        save_recording(out_dir, "GET", f"{PREFIX}/sprints/active-sprint-summary/{sprint_id}", None, None, r.status_code, _resp_body(r))

        # 4. GET /sprints/sprint-issues-with-epic-for-llm
        params = {"sprint_id": sprint_id, "team_name": TEAM_NAME}
        r = client.get(f"{PREFIX}/sprints/sprint-issues-with-epic-for-llm", params=params)
        save_recording(out_dir, "GET", f"{PREFIX}/sprints/sprint-issues-with-epic-for-llm", params, None, r.status_code, _resp_body(r))

        # 5. GET /sprints/{sprint_id}
        r = client.get(f"{PREFIX}/sprints/{sprint_id}")
        save_recording(out_dir, "GET", f"{PREFIX}/sprints/{sprint_id}", None, None, r.status_code, _resp_body(r))

    print(f"Sprints: recorded to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
