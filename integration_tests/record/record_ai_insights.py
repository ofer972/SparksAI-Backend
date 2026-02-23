#!/usr/bin/env python3
"""
Record AI insights flow (same as test_ai_insights): GET /ai-insights, getTopCards (team + group),
getTopCardsWithRecommendations (team + group), GET /ai-insights/{id} for first 4.
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
GET_BY_ID_LIMIT = 4


def _resp_body(r) -> object:
    try:
        return r.json()
    except Exception:
        return r.text


def main() -> int:
    parser = argparse.ArgumentParser(description="Record AI insights responses")
    parser.add_argument("--base-url", default=os.environ.get("BACKEND_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--out", required=True, help="Output directory for recordings")
    args = parser.parse_args()
    client = BackendClient(args.base_url.rstrip("/"))
    out_dir = args.out

    # 1. GET /ai-insights
    params = {"limit": 20}
    r = client.get(f"{PREFIX}/ai-insights", params=params)
    save_recording(out_dir, "GET", f"{PREFIX}/ai-insights", params, None, r.status_code, _resp_body(r))
    cards = []
    if r.status_code == 200:
        data = r.json()
        payload = (data.get("data") or {}) if isinstance(data, dict) else {}
        cards = payload.get("cards") or [] if isinstance(payload, dict) else []

    # 2. getTopCards (team + group)
    r = client.get(f"{PREFIX}/ai-insights/getTopCards", params={"team_name": TEAM_NAME})
    save_recording(out_dir, "GET", f"{PREFIX}/ai-insights/getTopCards", {"team_name": TEAM_NAME}, None, r.status_code, _resp_body(r), slug_extra="team")
    r = client.get(f"{PREFIX}/ai-insights/getTopCards", params={"group_name": GROUP_NAME})
    save_recording(out_dir, "GET", f"{PREFIX}/ai-insights/getTopCards", {"group_name": GROUP_NAME}, None, r.status_code, _resp_body(r), slug_extra="group")

    # 3. getTopCardsWithRecommendations (team + group)
    r = client.get(f"{PREFIX}/ai-insights/getTopCardsWithRecommendations", params={"team_name": TEAM_NAME})
    save_recording(out_dir, "GET", f"{PREFIX}/ai-insights/getTopCardsWithRecommendations", {"team_name": TEAM_NAME}, None, r.status_code, _resp_body(r), slug_extra="team")
    r = client.get(f"{PREFIX}/ai-insights/getTopCardsWithRecommendations", params={"group_name": GROUP_NAME})
    save_recording(out_dir, "GET", f"{PREFIX}/ai-insights/getTopCardsWithRecommendations", {"group_name": GROUP_NAME}, None, r.status_code, _resp_body(r), slug_extra="group")

    # 4. GET /ai-insights/{id} for first N
    for i, card in enumerate(cards[:GET_BY_ID_LIMIT]):
        card_id = card.get("id")
        if card_id is None:
            continue
        r = client.get(f"{PREFIX}/ai-insights/{card_id}")
        save_recording(out_dir, "GET", f"{PREFIX}/ai-insights/{card_id}", None, None, r.status_code, _resp_body(r))

    print(f"AI insights: recorded to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
