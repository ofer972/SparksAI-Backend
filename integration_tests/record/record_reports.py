#!/usr/bin/env python3
"""
Record report responses for all report IDs, with team and with group.
Saves JSON to integration_tests/recordings/reports/ (report_id_team.json, report_id_group.json).
Run with backend up: python integration_tests/record/record_reports.py [--base-url URL]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Repo root on path
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from integration_tests.client import BackendClient
from integration_tests.reports_config import TEAM_NAME, GROUP_NAME

PREFIX = "/api/v1"
RECORDINGS_DIR = os.path.join(_ROOT, "integration_tests", "recordings", "reports")
_R = "\033[31m"   # red
_B = "\033[1m"   # bold
_N = "\033[0m"    # reset


def _safe_filename(report_id: str) -> str:
    return report_id.replace("/", "_").replace("\\", "_")


def _get_result_count(rec: dict) -> int | None:
    """Extract record count from a recording's response if present (meta.count, result.count, or result length)."""
    body = rec.get("response", {}).get("body") or {}
    if not isinstance(body, dict):
        return None
    data = body.get("data") or {}
    meta = data.get("meta") or {}
    if isinstance(meta.get("count"), (int, float)):
        return int(meta["count"])
    result = data.get("result")
    if isinstance(result, dict) and "count" in result:
        c = result["count"]
        if isinstance(c, (int, float)):
            return int(c)
    if isinstance(result, list):
        return len(result)
    return None


def _record_one(client: BackendClient, report_id: str, query_params: dict, suffix: str) -> dict:
    path = f"{PREFIX}/reports/{report_id}"
    r = client.get(path, params=query_params)
    try:
        body = r.json()
    except Exception:
        body = r.text
    return {
        "request": {
            "method": "GET",
            "path": path,
            "query_params": query_params,
        },
        "response": {
            "status": r.status_code,
            "body": body,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Record report responses (team + group) for all report IDs")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BACKEND_BASE_URL", "http://localhost:8000"),
        help="Backend base URL",
    )
    parser.add_argument(
        "--out",
        default=RECORDINGS_DIR,
        help="Output directory for recording JSON files",
    )
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    out_dir = args.out

    os.makedirs(out_dir, exist_ok=True)
    client = BackendClient(base_url)

    # Get all report definitions (bypass cache for fresh list)
    r = client.get(f"{PREFIX}/reports", params={"bypass_cache": "true"})
    if r.status_code != 200:
        print(f"Failed to list reports: {r.status_code} - {r.text[:200]}")
        return 1
    data = r.json()
    reports = data.get("data") or data
    if not isinstance(reports, list):
        print("Unexpected list response shape")
        return 1
    # Keep full definitions so we can read meta_schema.required_filters per report
    definitions = [item for item in reports if isinstance(item, dict) and item.get("report_id")]
    # Exclude DORA and PR/pull-request reports from integration tests
    definitions = [d for d in definitions if not (d["report_id"].startswith("dora-") or d["report_id"].startswith("pr-workflow-"))]
    excluded_count = len(reports) - len(definitions)
    if excluded_count:
        print(f"Excluded {excluded_count} report(s): DORA and PR-workflow")
    print(f"Found {len(definitions)} report(s). Team={TEAM_NAME!r}, Group={GROUP_NAME!r}")

    # Values for required_filters: key = filter name from report meta_schema.required_filters, value = param value.
    # Add entries here when new report required filters need to be filled (no script logic change needed).
    required_filters_fill = {}
    current_pi = None
    r_pis = client.get(f"{PREFIX}/pis/current-and-next")
    if r_pis.status_code == 200:
        try:
            pis_data = r_pis.json().get("data") or {}
            current_pis = pis_data.get("current_pis") or []
            if current_pis and isinstance(current_pis[0], dict):
                current_pi = current_pis[0].get("pi_name")
                if current_pi:
                    required_filters_fill["pi"] = current_pi
                    print(f"Using current PI for required-filters: {current_pi!r}")
        except (IndexError, KeyError, TypeError):
            pass
    if not required_filters_fill:
        print("No current PI (or request failed); reports requiring 'pi' may return 400")

    # Optional filters we can fill from current context (PI). Any report with these in meta_schema.optional_filters gets them.
    optional_filters_fill = {}
    if current_pi:
        optional_filters_fill["pi"] = current_pi
        optional_filters_fill["pi_name"] = current_pi
        optional_filters_fill["pi_names"] = current_pi

    # First sprint per scope for goal-progress (sprint scope)
    first_sprint_team = None
    first_sprint_group = None
    r_sprints_team = client.get(f"{PREFIX}/goals/available-sprints", params={"team_name": TEAM_NAME})
    if r_sprints_team.status_code == 200:
        try:
            data = r_sprints_team.json().get("data") or {}
            sprints = data.get("sprints") or []
            if sprints and isinstance(sprints[0], dict):
                first_sprint_team = sprints[0].get("sprint_name") or sprints[0].get("name")
        except (IndexError, KeyError, TypeError):
            pass
    r_sprints_group = client.get(f"{PREFIX}/goals/available-sprints", params={"team_name": GROUP_NAME, "isGroup": "true"})
    if r_sprints_group.status_code == 200:
        try:
            data = r_sprints_group.json().get("data") or {}
            sprints = data.get("sprints") or []
            if sprints and isinstance(sprints[0], dict):
                first_sprint_group = sprints[0].get("sprint_name") or sprints[0].get("name")
        except (IndexError, KeyError, TypeError):
            pass
    if first_sprint_team or first_sprint_group:
        print(f"Using first available sprint for goal-progress: team={first_sprint_team!r}, group={first_sprint_group!r}")

    print(f"Writing to {out_dir}")
    print()

    for definition in definitions:
        report_id = definition["report_id"]
        safe_id = _safe_filename(report_id)
        required = (definition.get("meta_schema") or {}).get("required_filters") or []
        # Base params for team and group
        params_team = {"team_name": TEAM_NAME, "bypass_cache": "true"}
        params_group = {"team_name": GROUP_NAME, "isGroup": "true", "bypass_cache": "true"}
        # Add any required filter we have a value for (generic: add more fillers in required_filters_fill if needed)
        for key in required:
            if key in required_filters_fill:
                params_team[key] = required_filters_fill[key]
                params_group[key] = required_filters_fill[key]
        # Add optional filters we have in context (e.g. current PI for pi/pi_name/pi_names)
        optional = (definition.get("meta_schema") or {}).get("optional_filters") or []
        for key in optional:
            if key in optional_filters_fill:
                params_team[key] = optional_filters_fill[key]
                params_group[key] = optional_filters_fill[key]

        # goal-progress is recorded separately (PI + Sprint scope, 4 files)
        if report_id == "goal-progress":
            continue

        # Record with team
        rec_team = _record_one(client, report_id, params_team, "team")
        path_team = os.path.join(out_dir, f"{safe_id}_team.json")
        with open(path_team, "w", encoding="utf-8") as f:
            json.dump(rec_team, f, indent=2, ensure_ascii=False)
        st_team = rec_team["response"]["status"]
        line_team = f"  {report_id} (team) -> {os.path.basename(path_team)} status={st_team}"
        cnt = _get_result_count(rec_team)
        if cnt is not None:
            line_team += f" count={_B}{cnt}{_N}"
        print(f"{_R}{line_team}{_N}" if st_team != 200 else line_team)

        # Record with group
        rec_group = _record_one(client, report_id, params_group, "group")
        path_group = os.path.join(out_dir, f"{safe_id}_group.json")
        with open(path_group, "w", encoding="utf-8") as f:
            json.dump(rec_group, f, indent=2, ensure_ascii=False)
        st_group = rec_group["response"]["status"]
        line_group = f"  {report_id} (group) -> {os.path.basename(path_group)} status={st_group}"
        cnt = _get_result_count(rec_group)
        if cnt is not None:
            line_group += f" count={_B}{cnt}{_N}"
        print(f"{_R}{line_group}{_N}" if st_group != 200 else line_group)

    # goal-progress: record twice (PI scope + Sprint scope), 4 files total
    goal_progress_definition = next((d for d in definitions if d.get("report_id") == "goal-progress"), None)
    if goal_progress_definition:
        report_id = "goal-progress"
        safe_id = "goal-progress"
        # PI scope: scope_type=pi, pi_name=current_pi (team + group)
        for scope_suffix, params in [
            ("pi_team", {"team_name": TEAM_NAME, "bypass_cache": "true", "scope_type": "pi", "pi_name": current_pi or ""}),
            ("pi_group", {"team_name": GROUP_NAME, "isGroup": "true", "bypass_cache": "true", "scope_type": "pi", "pi_name": current_pi or ""}),
        ]:
            rec = _record_one(client, report_id, params, scope_suffix)
            path = os.path.join(out_dir, f"{safe_id}_{scope_suffix}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rec, f, indent=2, ensure_ascii=False)
            st = rec["response"]["status"]
            line = f"  {report_id} ({scope_suffix}) -> {os.path.basename(path)} status={st}"
            cnt = _get_result_count(rec)
            if cnt is not None:
                line += f" count={_B}{cnt}{_N}"
            print(f"{_R}{line}{_N}" if st != 200 else line)
        # Sprint scope: scope_type=sprint, sprint_name=first sprint (skip if no sprint available)
        for scope_suffix, params in [
            ("sprint_team", {"team_name": TEAM_NAME, "bypass_cache": "true", "scope_type": "sprint", "sprint_name": first_sprint_team} if first_sprint_team else None),
            ("sprint_group", {"team_name": GROUP_NAME, "isGroup": "true", "bypass_cache": "true", "scope_type": "sprint", "sprint_name": first_sprint_group} if first_sprint_group else None),
        ]:
            if params is None:
                continue
            rec = _record_one(client, report_id, params, scope_suffix)
            path = os.path.join(out_dir, f"{safe_id}_{scope_suffix}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rec, f, indent=2, ensure_ascii=False)
            st = rec["response"]["status"]
            line = f"  {report_id} ({scope_suffix}) -> {os.path.basename(path)} status={st}"
            cnt = _get_result_count(rec)
            if cnt is not None:
                line += f" count={_B}{cnt}{_N}"
            print(f"{_R}{line}{_N}" if st != 200 else line)

    print()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
