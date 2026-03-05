"""
Sprint burndown calculation in Python.

Computes burndown series from raw sprint details and jira_issue_history rows.
Replaces the logic previously in get_sprint_burndown_data_for_team SQL function.
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional
from collections import defaultdict


def _normalize_date(d: Any) -> Optional[date]:
    if d is None:
        return None
    if isinstance(d, date):
        return d
    if hasattr(d, "date"):
        return d.date()
    if isinstance(d, str):
        return date.fromisoformat(d[:10])
    return None


def _sprint_ids_set(sprint_ids: Any) -> set:
    """Return set of sprint IDs from DB value (list or None)."""
    if sprint_ids is None:
        return set()
    if isinstance(sprint_ids, (list, tuple)):
        return set(int(x) for x in sprint_ids if x is not None)
    return set()


def compute_sprint_burndown_from_history(
    sprint_id: int,
    sprint_name: str,
    start_date: date,
    end_date: date,
    state: str,
    history_rows: List[Dict[str, Any]],
    team_names: List[str],
    issue_type: str,
) -> List[Dict[str, Any]]:
    """
    Compute sprint burndown daily series from raw issue history.

    Matches semantics of get_sprint_burndown_data_for_team:
    - Initial scope (planned_issues): distinct issues in sprint on start_date or start_date+1.
    - Added: first_seen in sprint on day > start_date+1.
    - Remaining: in sprint and not Done; carry-forward on days with no data.
    - WIP: in sprint and In Progress; carry-forward.
    - Completed: transition to Done while in sprint.
    - Removed: left sprint, not Done, first_seen in sprint >= start_date.
    - Re-added: back in sprint, first_seen in sprint < current_date.
    - Total scope: initial + cum(added) + cum(re-added) - cum(removed).
    - Ideal line from peak scope and sprint length.

    Returns list of dicts with keys: snapshot_date, start_date, end_date,
    remaining_issues, ideal_remaining, total_issues, issues_added_on_day,
    issues_removed_on_day, issues_completed_on_day, wip_issues_in_progress.
    """
    today = date.today()
    end_date_cap = max(end_date, today) if state == "active" else end_date
    snapshot_dates = []
    d = start_date
    while d <= end_date_cap:
        snapshot_dates.append(d)
        d += timedelta(days=1)

    # Filter history to team/issue_type (history_rows are already filtered by caller)
    # Build by (issue_key, snapshot_date) -> row; if multiple per day, keep one
    by_issue_date: Dict[tuple, Dict] = {}
    for row in history_rows:
        key = (row["issue_key"], _normalize_date(row["snapshot_date"]))
        if key not in by_issue_date:
            by_issue_date[key] = row

    # First-seen in sprint per (issue_key, issuetype) for this sprint
    first_seen_in_sprint: Dict[str, date] = {}
    for (issue_key, snap_d), row in by_issue_date.items():
        if snap_d is None:
            continue
        sids = _sprint_ids_set(row.get("sprint_ids"))
        if sprint_id not in sids:
            continue
        if issue_key not in first_seen_in_sprint or snap_d < first_seen_in_sprint[issue_key]:
            first_seen_in_sprint[issue_key] = snap_d

    # Initial scope: distinct issues in sprint on start_date or start_date+1
    initial_scope = set()
    for (issue_key, snap_d), row in by_issue_date.items():
        if snap_d not in (start_date, start_date + timedelta(days=1)):
            continue
        if sprint_id not in _sprint_ids_set(row.get("sprint_ids")):
            continue
        initial_scope.add(issue_key)
    planned_issues = len(initial_scope)

    # Daily added: first_seen == d and d > start_date+1
    daily_added: Dict[date, int] = defaultdict(int)
    for issue_key, first_seen in first_seen_in_sprint.items():
        if first_seen > start_date + timedelta(days=1):
            daily_added[first_seen] += 1

    # Per-day remaining and WIP from history (only days that have data)
    daily_remaining: Dict[date, int] = {}
    daily_wip: Dict[date, int] = {}
    for snap_d in snapshot_dates:
        remaining_set = set()
        wip_set = set()
        for (issue_key, d), row in by_issue_date.items():
            if d != snap_d:
                continue
            if sprint_id not in _sprint_ids_set(row.get("sprint_ids")):
                continue
            if (row.get("status_category") or "").strip() != "Done":
                remaining_set.add(issue_key)
            if (row.get("status_category") or "").strip() == "In Progress":
                wip_set.add(issue_key)
        if remaining_set or wip_set:
            daily_remaining[snap_d] = len(remaining_set)
            daily_wip[snap_d] = len(wip_set)
    # Carry-forward remaining and WIP for days with no data
    last_remaining = 0
    last_wip = 0
    for snap_d in snapshot_dates:
        if snap_d in daily_remaining:
            last_remaining = daily_remaining[snap_d]
            last_wip = daily_wip.get(snap_d, 0)
        else:
            daily_remaining[snap_d] = last_remaining
            daily_wip[snap_d] = last_wip

    # State changes: sort by issue_key, snapshot_date
    sorted_rows = sorted(
        by_issue_date.items(),
        key=lambda x: (x[0][0], x[0][1] or date(1, 1, 1)),
    )
    daily_completed: Dict[date, int] = defaultdict(int)
    daily_removed: Dict[date, int] = defaultdict(int)
    daily_readded: Dict[date, int] = defaultdict(int)
    prev_by_issue: Dict[str, Dict] = {}
    for (issue_key, snap_d), row in sorted_rows:
        if snap_d is None:
            continue
        curr_sids = _sprint_ids_set(row.get("sprint_ids"))
        curr_status = (row.get("status_category") or "").strip()
        prev = prev_by_issue.get(issue_key)
        prev_sids = _sprint_ids_set(prev.get("sprint_ids")) if prev else set()
        prev_status = (prev.get("status_category") or "").strip() if prev else ""
        in_sprint_before = sprint_id in prev_sids
        in_sprint_now = sprint_id in curr_sids
        first_seen = first_seen_in_sprint.get(issue_key)

        # Completed: transition to Done while in sprint
        if curr_status == "Done" and prev_status != "Done" and in_sprint_before:
            daily_completed[snap_d] += 1
        # Removed: left sprint, not Done when left, first_seen in sprint >= start_date
        if in_sprint_before and not in_sprint_now and prev_status != "Done":
            if first_seen is not None and first_seen >= start_date:
                daily_removed[snap_d] += 1
        # Re-added: back in sprint, was in sprint before (first_seen < current_date), day > start+1
        if in_sprint_now and not in_sprint_before and first_seen is not None and first_seen < snap_d:
            if snap_d > start_date + timedelta(days=1):
                daily_readded[snap_d] += 1

        prev_by_issue[issue_key] = dict(row)
        if "sprint_ids" in row:
            prev_by_issue[issue_key]["sprint_ids"] = row["sprint_ids"]
        prev_by_issue[issue_key]["status_category"] = curr_status

    # Total scope per day: initial + cum(added) + cum(re-added) - cum(removed)
    cum_added = 0
    cum_removed = 0
    cum_readded = 0
    total_by_date: Dict[date, int] = {}
    for snap_d in snapshot_dates:
        cum_added += daily_added.get(snap_d, 0)
        cum_removed += daily_removed.get(snap_d, 0)
        cum_readded += daily_readded.get(snap_d, 0)
        total_by_date[snap_d] = planned_issues + cum_added + cum_readded - cum_removed
    peak_scope = max(total_by_date.values()) if total_by_date else 0
    sprint_length_days = (end_date - start_date).days
    if sprint_length_days <= 0:
        sprint_length_days = 1

    # Build output rows (same shape as current get_sprint_burndown_data_db)
    out = []
    for snap_d in snapshot_dates:
        remaining = daily_remaining.get(snap_d, 0)
        wip = daily_wip.get(snap_d, 0)
        if snap_d > today:
            remaining = None
            wip = None
        ideal = round(
            max(0.0, peak_scope * (end_date - snap_d).days / sprint_length_days),
            1,
        )
        out.append({
            "snapshot_date": snap_d,
            "start_date": start_date,
            "end_date": end_date,
            "remaining_issues": remaining,
            "ideal_remaining": int(ideal) if ideal == int(ideal) else ideal,
            "total_issues": total_by_date.get(snap_d, 0),
            "issues_added_on_day": daily_added.get(snap_d, 0),
            "issues_removed_on_day": daily_removed.get(snap_d, 0),
            "issues_completed_on_day": daily_completed.get(snap_d, 0),
            "wip_issues_in_progress": wip,
        })
    return out
