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

        # Removed: left sprint, not Done when left, first_seen in sprint >= start_date
        removed_this_day = (
            in_sprint_before and not in_sprint_now and prev_status != "Done"
            and first_seen is not None and first_seen >= start_date
        )
        if removed_this_day:
            daily_removed[snap_d] += 1
        # Completed: transition to Done while in sprint on this day; exclude if also removed same day.
        # Use in_sprint_now (not in_sprint_before) so issues added and completed the same day are counted.
        if curr_status == "Done" and prev_status != "Done" and in_sprint_now and not removed_this_day:
            daily_completed[snap_d] += 1
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


def compute_total_scope_issues_for_date(
    sprint_id: int,
    start_date: date,
    target_date: date,
    history_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Return list of issues that are in scope on target_date using the same formula as the chart:
    initial_scope + added through target_date + re-added through target_date - removed through target_date.
    history_rows must cover start_date through target_date. Returns [{"issue_key", "team_name"}].
    """
    by_issue_date: Dict[tuple, Dict[str, Any]] = {}
    for row in history_rows:
        d = _normalize_date(row.get("snapshot_date"))
        if d is None or d < start_date or d > target_date:
            continue
        key = (row["issue_key"], d)
        if key not in by_issue_date:
            by_issue_date[key] = row

    first_seen_in_sprint: Dict[str, date] = {}
    for (issue_key, snap_d), row in by_issue_date.items():
        if snap_d is None:
            continue
        if sprint_id not in _sprint_ids_set(row.get("sprint_ids")):
            continue
        if issue_key not in first_seen_in_sprint or snap_d < first_seen_in_sprint[issue_key]:
            first_seen_in_sprint[issue_key] = snap_d

    start_plus_one = start_date + timedelta(days=1)
    initial_scope: set = set()
    for (issue_key, snap_d), row in by_issue_date.items():
        if snap_d not in (start_date, start_plus_one):
            continue
        if sprint_id not in _sprint_ids_set(row.get("sprint_ids")):
            continue
        initial_scope.add(issue_key)

    sorted_rows = sorted(
        by_issue_date.items(),
        key=lambda x: (x[0][0], x[0][1] or date(1, 1, 1)),
    )
    removed_through_date: Dict[date, set] = defaultdict(set)
    readded_through_date: Dict[date, set] = defaultdict(set)
    prev_by_issue: Dict[str, Dict] = {}
    for (issue_key, snap_d), row in sorted_rows:
        if snap_d is None:
            continue
        curr_sids = _sprint_ids_set(row.get("sprint_ids"))
        prev = prev_by_issue.get(issue_key)
        prev_sids = _sprint_ids_set(prev.get("sprint_ids")) if prev else set()
        in_sprint_before = sprint_id in prev_sids
        in_sprint_now = sprint_id in curr_sids
        first_seen = first_seen_in_sprint.get(issue_key)
        prev_status = (prev.get("status_category") or "").strip() if prev else ""

        if in_sprint_before and not in_sprint_now and prev_status != "Done" and first_seen is not None and first_seen >= start_date:
            removed_through_date[snap_d].add(issue_key)
        if in_sprint_now and not in_sprint_before and first_seen is not None and first_seen < snap_d and snap_d > start_plus_one:
            readded_through_date[snap_d].add(issue_key)

        prev_by_issue[issue_key] = dict(row)
        if "sprint_ids" in row:
            prev_by_issue[issue_key]["sprint_ids"] = row["sprint_ids"]
        prev_by_issue[issue_key]["status_category"] = (row.get("status_category") or "").strip()

    added_through_target: set = set()
    for issue_key, first_seen in first_seen_in_sprint.items():
        if start_plus_one < first_seen <= target_date:
            added_through_target.add(issue_key)

    removed_set: set = set()
    for d in removed_through_date:
        if d <= target_date:
            removed_set |= removed_through_date[d]
    readded_set: set = set()
    for d in readded_through_date:
        if d <= target_date:
            readded_set |= readded_through_date[d]

    in_scope = (initial_scope | added_through_target | readded_set) - removed_set

    # Team name: use latest row we have for each issue (iteration is already by (issue_key, date))
    issue_to_team: Dict[str, Optional[str]] = {}
    for (issue_key, d), row in sorted(by_issue_date.items(), key=lambda x: (x[0][0], x[0][1] or date(1, 1, 1))):
        if issue_key in in_scope and d:
            issue_to_team[issue_key] = (row.get("team_name") or "").strip() or None

    result = []
    for issue_key in sorted(in_scope):
        result.append({"issue_key": issue_key, "team_name": issue_to_team.get(issue_key)})
    return result


def get_sprint_issues_for_date_and_metric(
    sprint_id: int,
    target_date: date,
    history_rows: List[Dict[str, Any]],
    metric_type: str,
) -> List[Dict[str, Any]]:
    """
    Return list of issues for a given date and metric.
    Supports: issues_completed, issues_removed, total_scope, wip_in_progress, actual_remaining.
    Uses status_category for WIP (In Progress) and remaining (not Done).
    Returns list of {"issue_key": str, "team_name": str}. Caller adds summary from jira_issues.
    Same-day rule: if an issue was both completed and removed on target_date, it appears
    only in the removed list, not in the completed list.
    For total_scope use compute_total_scope_issues_for_date with full sprint history instead.
    """
    prev_date = target_date - timedelta(days=1)
    by_issue_date: Dict[tuple, Dict[str, Any]] = {}
    for row in history_rows:
        d = _normalize_date(row.get("snapshot_date"))
        if d is None or d not in (target_date, prev_date):
            continue
        key = (row["issue_key"], d)
        if key not in by_issue_date:
            by_issue_date[key] = row

    # Issues removed on target_date (so we can exclude them from completed when same day)
    removed_on_day: set = set()
    for (issue_key, d), row in by_issue_date.items():
        if d != prev_date:
            continue
        if sprint_id not in _sprint_ids_set(row.get("sprint_ids")):
            continue
        if (row.get("status_category") or "").strip() == "Done":
            continue
        curr_row = by_issue_date.get((issue_key, target_date))
        if curr_row is not None and sprint_id in _sprint_ids_set(curr_row.get("sprint_ids")):
            continue
        removed_on_day.add(issue_key)

    result: List[Dict[str, Any]] = []
    if metric_type == "issues_completed":
        for (issue_key, d), row in by_issue_date.items():
            if d != target_date:
                continue
            if issue_key in removed_on_day:
                continue
            if sprint_id not in _sprint_ids_set(row.get("sprint_ids")):
                continue
            if (row.get("status_category") or "").strip() != "Done":
                continue
            prev_row = by_issue_date.get((issue_key, prev_date))
            if prev_row is not None and (prev_row.get("status_category") or "").strip() == "Done":
                continue
            result.append({
                "issue_key": issue_key,
                "team_name": (row.get("team_name") or "").strip() or None,
            })
    elif metric_type == "issues_removed":
        for (issue_key, d), row in by_issue_date.items():
            if d != prev_date:
                continue
            if sprint_id not in _sprint_ids_set(row.get("sprint_ids")):
                continue
            if (row.get("status_category") or "").strip() == "Done":
                continue
            curr_row = by_issue_date.get((issue_key, target_date))
            if curr_row is not None and sprint_id in _sprint_ids_set(curr_row.get("sprint_ids")):
                continue
            result.append({
                "issue_key": issue_key,
                "team_name": (row.get("team_name") or "").strip() or None,
            })
    elif metric_type == "total_scope":
        seen: set = set()
        for (issue_key, d), row in by_issue_date.items():
            if d != target_date:
                continue
            if sprint_id not in _sprint_ids_set(row.get("sprint_ids")):
                continue
            if issue_key in seen:
                continue
            seen.add(issue_key)
            result.append({
                "issue_key": issue_key,
                "team_name": (row.get("team_name") or "").strip() or None,
            })
    elif metric_type == "wip_in_progress":
        for (issue_key, d), row in by_issue_date.items():
            if d != target_date:
                continue
            if sprint_id not in _sprint_ids_set(row.get("sprint_ids")):
                continue
            if (row.get("status_category") or "").strip() != "In Progress":
                continue
            result.append({
                "issue_key": issue_key,
                "team_name": (row.get("team_name") or "").strip() or None,
            })
    elif metric_type == "actual_remaining":
        for (issue_key, d), row in by_issue_date.items():
            if d != target_date:
                continue
            if sprint_id not in _sprint_ids_set(row.get("sprint_ids")):
                continue
            if (row.get("status_category") or "").strip() == "Done":
                continue
            result.append({
                "issue_key": issue_key,
                "team_name": (row.get("team_name") or "").strip() or None,
            })
    return result
