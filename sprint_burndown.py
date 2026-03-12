"""
Sprint burndown calculation in Python.

Computes burndown series from raw sprint details and jira_issue_history rows.
Replaces the logic previously in get_sprint_burndown_data_for_team SQL function.
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

# Option A: issue_lists_by_metric[metric][date] = list of {"issue_key", "team_name"}
IssueListsByMetric = Dict[str, Dict[date, List[Dict[str, Any]]]]


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


def _team_name_for_issue_on_date(
    by_issue_date: Dict[tuple, Dict[str, Any]], issue_key: str, d: date
) -> Optional[str]:
    """Get team_name for issue_key on or before date d from by_issue_date."""
    row = by_issue_date.get((issue_key, d))
    if row is not None:
        val = row.get("team_name")
        return (val.strip() or None) if isinstance(val, str) else None
    best_row = None
    best_d = None
    for (ik, sd), r in by_issue_date.items():
        if ik != issue_key or sd is None or sd > d:
            continue
        if best_d is None or sd > best_d:
            best_d = sd
            best_row = r
    if best_row is None:
        return None
    val = best_row.get("team_name")
    return (val.strip() or None) if isinstance(val, str) else None


def compute_sprint_burndown_from_history(
    sprint_id: int,
    sprint_name: str,
    start_date: date,
    end_date: date,
    state: str,
    history_rows: List[Dict[str, Any]],
    team_names: List[str],
    issue_type: str,
    complete_date: Optional[date] = None,
    resolved_at_map: Optional[Dict[str, Optional[date]]] = None,
) -> Tuple[List[Dict[str, Any]], IssueListsByMetric]:
    """
    Compute sprint burndown daily series and per-day issue lists from raw issue history.

    Matches semantics of get_sprint_burndown_data_for_team:
    - Initial scope (planned_issues): distinct issues in sprint on start_date only (same day).
    - Added: first_seen in sprint on day > start_date.
    - Remaining: in sprint and not Done; carry-forward on days with no data.
    - WIP: in sprint and In Progress; carry-forward.
    - Completed: transition to Done while in sprint.
    - Removed: left sprint, not Done, first_seen in sprint >= start_date.
    - Re-added: back in sprint, first_seen in sprint < current_date.
    - Total scope: initial + cum(added) + cum(re-added) - cum(removed).
    - Ideal line from peak scope and sprint length.

    Returns:
      - chart_rows: list of dicts with keys snapshot_date, start_date, end_date,
        remaining_issues, ideal_remaining, total_issues, issues_added_on_day,
        issues_removed_on_day, issues_completed_on_day, wip_issues_in_progress.
      - issue_lists_by_metric: for each metric ("total_scope", "issues_completed",
        "issues_removed", "issues_added", "wip_in_progress", "actual_remaining"), a dict mapping
        date -> list of {"issue_key", "team_name"} for that day (Option A: list uses this).
    """
    today = date.today()
    # Active: no complete_date → cap at max(end_date, today). Closed: use complete_date when present
    if state == "active":
        end_date_cap = max(end_date, today)
    else:
        end_date_cap = complete_date if complete_date is not None else end_date
    sprint_end_date = complete_date if complete_date is not None else end_date
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

    # Initial scope: in sprint on start_date only (same day), excluding issues Done before start_date
    day_before_start = start_date - timedelta(days=1)
    in_sprint_on_start = set()
    for (issue_key, snap_d), row in by_issue_date.items():
        if snap_d != start_date:
            continue
        if sprint_id not in _sprint_ids_set(row.get("sprint_ids")):
            continue
        in_sprint_on_start.add(issue_key)
    completed_outside_sprint_set = set()
    for issue_key in in_sprint_on_start:
        prev_row = by_issue_date.get((issue_key, day_before_start))
        if prev_row and (prev_row.get("status_category") or "").strip() == "Done":
            completed_outside_sprint_set.add(issue_key)
    initial_scope = in_sprint_on_start - completed_outside_sprint_set
    planned_issues = len(initial_scope)
    issues_completed_outside_sprint = len(completed_outside_sprint_set)

    # Resurfaced (edge case only): Done before start -> re-appears in middle (TODO/In Progress) -> later closed.
    # Only issues in completed_outside_sprint_set; first day >= start_date where in sprint and status != Done.
    first_resurfaced_day_sprint: Dict[str, date] = {}
    for issue_key in completed_outside_sprint_set:
        for (ik, snap_d), row in by_issue_date.items():
            if ik != issue_key or snap_d is None or snap_d < start_date:
                continue
            if sprint_id not in _sprint_ids_set(row.get("sprint_ids")):
                continue
            if (row.get("status_category") or "").strip() == "Done":
                continue
            if issue_key not in first_resurfaced_day_sprint or snap_d < first_resurfaced_day_sprint[issue_key]:
                first_resurfaced_day_sprint[issue_key] = snap_d
    daily_resurfaced: Dict[date, int] = defaultdict(int)
    daily_resurfaced_set: Dict[date, set] = defaultdict(set)
    daily_resurfaced_list: Dict[date, List[Dict[str, Any]]] = defaultdict(list)
    for issue_key, resurfaced_d in first_resurfaced_day_sprint.items():
        daily_resurfaced[resurfaced_d] += 1
        daily_resurfaced_set[resurfaced_d].add(issue_key)
        row_r = by_issue_date.get((issue_key, resurfaced_d), {})
        tn = row_r.get("team_name")
        team_name_r = (tn.strip() or None) if isinstance(tn, str) else None
        daily_resurfaced_list[resurfaced_d].append({"issue_key": issue_key, "team_name": team_name_r})

    # Daily added: first_seen == d and d > start_date (and sets/lists for Option A)
    daily_added: Dict[date, int] = defaultdict(int)
    added_by_date_set: Dict[date, set] = defaultdict(set)
    added_by_date_list: Dict[date, List[Dict[str, Any]]] = defaultdict(list)
    for issue_key, first_seen in first_seen_in_sprint.items():
        if first_seen > start_date:
            daily_added[first_seen] += 1
            added_by_date_set[first_seen].add(issue_key)
            row_add = by_issue_date.get((issue_key, first_seen), {})
            tn = row_add.get("team_name")
            team_name_add = (tn.strip() or None) if isinstance(tn, str) else None
            added_by_date_list[first_seen].append({"issue_key": issue_key, "team_name": team_name_add})

    # Per-day remaining and WIP from history (only days that have data); keep lists for Option A
    daily_remaining: Dict[date, int] = {}
    daily_wip: Dict[date, int] = {}
    remaining_list_by_date: Dict[date, List[Dict[str, Any]]] = defaultdict(list)
    wip_list_by_date: Dict[date, List[Dict[str, Any]]] = defaultdict(list)
    for snap_d in snapshot_dates:
        remaining_set = set()
        wip_set = set()
        for (issue_key, d), row in by_issue_date.items():
            if d != snap_d:
                continue
            if sprint_id not in _sprint_ids_set(row.get("sprint_ids")):
                continue
            tn = row.get("team_name")
            team_name_row = (tn.strip() or None) if isinstance(tn, str) else None
            if (row.get("status_category") or "").strip() != "Done":
                remaining_set.add(issue_key)
                remaining_list_by_date[snap_d].append({"issue_key": issue_key, "team_name": team_name_row})
            if (row.get("status_category") or "").strip() == "In Progress":
                wip_set.add(issue_key)
                wip_list_by_date[snap_d].append({"issue_key": issue_key, "team_name": team_name_row})
        if remaining_set or wip_set:
            daily_remaining[snap_d] = len(remaining_set)
            daily_wip[snap_d] = len(wip_set)
    # Carry-forward remaining and WIP for days with no data (counts and lists)
    last_remaining = 0
    last_wip = 0
    last_remaining_list: List[Dict[str, Any]] = []
    last_wip_list: List[Dict[str, Any]] = []
    for snap_d in snapshot_dates:
        if snap_d in daily_remaining:
            last_remaining = daily_remaining[snap_d]
            last_wip = daily_wip.get(snap_d, 0)
            last_remaining_list = list(remaining_list_by_date.get(snap_d, []))
            last_wip_list = list(wip_list_by_date.get(snap_d, []))
        else:
            daily_remaining[snap_d] = last_remaining
            daily_wip[snap_d] = last_wip
            remaining_list_by_date[snap_d] = list(last_remaining_list)
            wip_list_by_date[snap_d] = list(last_wip_list)

    # State changes: sort by issue_key, snapshot_date; also build per-day lists for Option A
    sorted_rows = sorted(
        by_issue_date.items(),
        key=lambda x: (x[0][0], x[0][1] or date(1, 1, 1)),
    )
    daily_completed: Dict[date, int] = defaultdict(int)
    daily_removed: Dict[date, int] = defaultdict(int)
    daily_readded: Dict[date, int] = defaultdict(int)
    daily_completed_list: Dict[date, List[Dict[str, Any]]] = defaultdict(list)
    daily_removed_list: Dict[date, List[Dict[str, Any]]] = defaultdict(list)
    daily_readded_list: Dict[date, List[Dict[str, Any]]] = defaultdict(list)
    daily_removed_set: Dict[date, set] = defaultdict(set)
    daily_readded_set: Dict[date, set] = defaultdict(set)
    prev_by_issue: Dict[str, Dict] = {}
    # Issues that were ever removed from this sprint; do not count them as completed even if
    # a later row still has this sprint in sprint_ids (e.g. Jira keeps both source and target).
    issues_ever_removed_from_sprint: set = set()
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
        tn = row.get("team_name")
        team_name_row = (tn.strip() or None) if isinstance(tn, str) else None

        # Removed: left sprint. Count if (a) added during sprint (first_seen >= start_date)
        # or (b) removed after start_date. Normally exclude when Done when left; but if Done and
        # removed on the same day (resolved_at == snap_d), count as removed so total scope is reduced.
        in_scope_for_removal = (first_seen is not None and first_seen >= start_date) or snap_d > start_date
        done_same_day = False
        if prev_status == "Done" and resolved_at_map is not None:
            ra = resolved_at_map.get(issue_key)
            done_same_day = ra is not None and _normalize_date(ra) == snap_d
        removed_this_day = (
            in_sprint_before
            and not in_sprint_now
            and in_scope_for_removal
            and (prev_status != "Done" or done_same_day)
        )
        if removed_this_day:
            daily_removed[snap_d] += 1
            daily_removed_set[snap_d].add(issue_key)
            daily_removed_list[snap_d].append({"issue_key": issue_key, "team_name": team_name_row})
            issues_ever_removed_from_sprint.add(issue_key)
        # Completed: transition to Done while in sprint on this day; exclude if also removed same day.
        # Do not count if the issue was ever removed from this sprint (e.g. moved to another sprint
        # then done; Jira may still keep this sprint in sprint_ids, but we must not count it here).
        # Only count if resolved_at is within sprint range (reopened issues have resolved_at cleared).
        resolved_ok = True
        if resolved_at_map is not None:
            ra = resolved_at_map.get(issue_key)
            resolved_ok = ra is not None and start_date <= ra <= sprint_end_date
        if (
            curr_status == "Done"
            and prev_status != "Done"
            and in_sprint_now
            and not removed_this_day
            and issue_key not in issues_ever_removed_from_sprint
            and resolved_ok
        ):
            daily_completed[snap_d] += 1
            daily_completed_list[snap_d].append({"issue_key": issue_key, "team_name": team_name_row})
        # Re-added: back in sprint, was in sprint before (first_seen < current_date), day > start_date.
        # If the issue was previously removed, allow it to count as completed again when it goes Done.
        if in_sprint_now and not in_sprint_before and first_seen is not None and first_seen < snap_d:
            if snap_d > start_date:
                daily_readded[snap_d] += 1
                daily_readded_set[snap_d].add(issue_key)
                daily_readded_list[snap_d].append({"issue_key": issue_key, "team_name": team_name_row})
                issues_ever_removed_from_sprint.discard(issue_key)

        prev_by_issue[issue_key] = dict(row)
        if "sprint_ids" in row:
            prev_by_issue[issue_key]["sprint_ids"] = row["sprint_ids"]
        prev_by_issue[issue_key]["status_category"] = curr_status

    # Total scope per day: initial + cum(added) + cum(re-added) + cum(resurfaced) - cum(removed)
    cum_added = 0
    cum_removed = 0
    cum_readded = 0
    cum_resurfaced = 0
    total_by_date: Dict[date, int] = {}
    in_scope_set: set = set(initial_scope)
    total_scope_list_by_date: Dict[date, List[Dict[str, Any]]] = {}
    for snap_d in snapshot_dates:
        cum_added += daily_added.get(snap_d, 0)
        cum_removed += daily_removed.get(snap_d, 0)
        cum_readded += daily_readded.get(snap_d, 0)
        cum_resurfaced += daily_resurfaced.get(snap_d, 0)
        in_scope_set |= added_by_date_set.get(snap_d, set())
        in_scope_set -= daily_removed_set.get(snap_d, set())
        in_scope_set |= daily_readded_set.get(snap_d, set())
        in_scope_set |= daily_resurfaced_set.get(snap_d, set())
        total_by_date[snap_d] = planned_issues + cum_added + cum_readded + cum_resurfaced - cum_removed
        total_scope_list_by_date[snap_d] = [
            {"issue_key": k, "team_name": _team_name_for_issue_on_date(by_issue_date, k, snap_d)}
            for k in sorted(in_scope_set)
        ]
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
        row = {
            "snapshot_date": snap_d,
            "start_date": start_date,
            "end_date": end_date,
            "remaining_issues": remaining,
            "ideal_remaining": int(ideal) if ideal == int(ideal) else ideal,
            "total_issues": total_by_date.get(snap_d, 0),
            "issues_added_on_day": daily_added.get(snap_d, 0) + daily_readded.get(snap_d, 0) + daily_resurfaced.get(snap_d, 0),
            "issues_removed_on_day": daily_removed.get(snap_d, 0),
            "issues_completed_on_day": daily_completed.get(snap_d, 0),
            "wip_issues_in_progress": wip,
            "issues_completed_outside_sprint": issues_completed_outside_sprint,
            "issues_completed_outside_sprint_keys": sorted(completed_outside_sprint_set),
        }
        if not out:
            row["issues_ever_removed_keys"] = sorted(issues_ever_removed_from_sprint)
        out.append(row)

    # Option A: per-day issue lists for list endpoint (single source of truth)
    issue_lists_by_metric: Dict[str, Dict[date, List[Dict[str, Any]]]] = {
        "total_scope": {d: total_scope_list_by_date.get(d, []) for d in snapshot_dates},
        "issues_completed": {d: daily_completed_list.get(d, []) for d in snapshot_dates},
        "issues_removed": {d: daily_removed_list.get(d, []) for d in snapshot_dates},
        "issues_added": {d: added_by_date_list.get(d, []) + daily_readded_list.get(d, []) + daily_resurfaced_list.get(d, []) for d in snapshot_dates},
        "wip_in_progress": {d: wip_list_by_date.get(d, []) for d in snapshot_dates},
        "actual_remaining": {d: remaining_list_by_date.get(d, []) for d in snapshot_dates},
    }
    return (out, issue_lists_by_metric)


def compute_closed_sprint_summary_from_history(
    sprint_id: int,
    start_date: date,
    end_date: date,
    complete_date: Optional[date],
    history_rows: List[Dict[str, Any]],
    team_names: List[str],
    issue_type: str,
    sprint_name: str = "",
    resolved_at_map: Optional[Dict[str, Optional[date]]] = None,
) -> Dict[str, Any]:
    """
    Compute a single closed-sprint summary from raw issue history.
    Uses the same rules as the sprint burndown (at start, added, removed, completed,
    re-added, same-day edge cases). Calls compute_sprint_burndown_from_history and
    aggregates to one summary row.

    Returns a dict with: issues_at_start, issues_added, issues_removed, issues_done,
    issues_not_completed, completed_percentage, issues_at_start_keys, issues_added_keys,
    issues_removed_keys, completed_issue_keys, issues_not_completed_keys,
    issues_completed_outside_sprint. Key arrays are sorted lists.
    """
    if not history_rows:
        return _empty_closed_sprint_summary()

    state = "closed"
    try:
        chart_rows, issue_lists_by_metric = compute_sprint_burndown_from_history(
            sprint_id=sprint_id,
            sprint_name=sprint_name,
            start_date=start_date,
            end_date=end_date,
            state=state,
            history_rows=history_rows,
            team_names=team_names,
            issue_type=issue_type,
            complete_date=complete_date,
            resolved_at_map=resolved_at_map,
        )
    except Exception:
        return _empty_closed_sprint_summary()

    if not chart_rows:
        return _empty_closed_sprint_summary()

    snapshot_dates = sorted(issue_lists_by_metric.get("total_scope", {}).keys())
    last_date = snapshot_dates[-1] if snapshot_dates else start_date

    # At start = total_scope on start_date (initial scope only)
    total_scope_start = issue_lists_by_metric.get("total_scope", {}).get(start_date, [])
    issues_at_start_keys = sorted({x["issue_key"] for x in total_scope_start})
    issues_at_start = len(issues_at_start_keys)

    # Added = distinct union of issues_added over all days (first-time + re-added)
    added_all: set = set()
    for d in snapshot_dates:
        for x in issue_lists_by_metric.get("issues_added", {}).get(d, []):
            added_all.add(x["issue_key"])
    issues_added_keys = sorted(added_all)
    issues_added = len(issues_added_keys)

    # Removed = distinct union of issues_removed over all days
    removed_all: set = set()
    for d in snapshot_dates:
        for x in issue_lists_by_metric.get("issues_removed", {}).get(d, []):
            removed_all.add(x["issue_key"])
    issues_removed_keys = sorted(removed_all)
    issues_removed = len(issues_removed_keys)

    # Completed = distinct union of issues_completed (transition to Done while in sprint),
    # minus issues that were ever removed (e.g. Done then rework then removed, or moved then done).
    completed_all: set = set()
    for d in snapshot_dates:
        for x in issue_lists_by_metric.get("issues_completed", {}).get(d, []):
            completed_all.add(x["issue_key"])
    ever_removed = set(chart_rows[0].get("issues_ever_removed_keys") or [])
    completed_issue_keys = sorted(completed_all - ever_removed)
    issues_done = len(completed_issue_keys)

    # Not completed = actual_remaining on last date (in sprint at end, not Done)
    actual_remaining_last = issue_lists_by_metric.get("actual_remaining", {}).get(last_date, [])
    issues_not_completed_keys = sorted({x["issue_key"] for x in actual_remaining_last})
    issues_not_completed = len(issues_not_completed_keys)

    issues_completed_outside_sprint = int(chart_rows[0].get("issues_completed_outside_sprint", 0) or 0)
    issues_completed_outside_sprint_keys = chart_rows[0].get("issues_completed_outside_sprint_keys") or []

    total_planned = issues_at_start + issues_added
    completed_percentage = (
        round((issues_done / total_planned * 100.0), 2) if total_planned else 0.0
    )

    return {
        "issues_at_start": issues_at_start,
        "issues_added": issues_added,
        "issues_removed": issues_removed,
        "issues_done": issues_done,
        "issues_not_completed": issues_not_completed,
        "completed_percentage": completed_percentage,
        "issues_at_start_keys": issues_at_start_keys,
        "issues_added_keys": issues_added_keys,
        "issues_removed_keys": issues_removed_keys,
        "completed_issue_keys": completed_issue_keys,
        "issues_not_completed_keys": issues_not_completed_keys,
        "issues_completed_outside_sprint": issues_completed_outside_sprint,
        "issues_completed_outside_sprint_keys": issues_completed_outside_sprint_keys,
    }


def _empty_closed_sprint_summary() -> Dict[str, Any]:
    return {
        "issues_at_start": 0,
        "issues_added": 0,
        "issues_removed": 0,
        "issues_done": 0,
        "issues_not_completed": 0,
        "completed_percentage": 0.0,
        "issues_at_start_keys": [],
        "issues_added_keys": [],
        "issues_removed_keys": [],
        "completed_issue_keys": [],
        "issues_not_completed_keys": [],
        "issues_completed_outside_sprint": 0,
        "issues_completed_outside_sprint_keys": [],
    }


def compute_total_scope_issues_for_date(
    sprint_id: int,
    start_date: date,
    target_date: date,
    history_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Return list of issues that are in scope on target_date using the same formula as the chart:
    initial_scope + added through target_date + re-added through target_date - removed through target_date.
    history_rows must cover start_date - 1 through target_date (same as chart) so first_seen and prev match.
    Returns [{"issue_key", "team_name"}].
    """
    history_start = start_date - timedelta(days=1)
    by_issue_date: Dict[tuple, Dict[str, Any]] = {}
    for row in history_rows:
        d = _normalize_date(row.get("snapshot_date"))
        if d is None or d < history_start or d > target_date:
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

    day_before_start = start_date - timedelta(days=1)
    in_sprint_on_start: set = set()
    for (issue_key, snap_d), row in by_issue_date.items():
        if snap_d != start_date:
            continue
        if sprint_id not in _sprint_ids_set(row.get("sprint_ids")):
            continue
        in_sprint_on_start.add(issue_key)
    completed_outside_set = set()
    for issue_key in in_sprint_on_start:
        prev_row = by_issue_date.get((issue_key, day_before_start))
        if prev_row and (prev_row.get("status_category") or "").strip() == "Done":
            completed_outside_set.add(issue_key)
    initial_scope = in_sprint_on_start - completed_outside_set

    # Resurfaced (same as chart): Done before start -> first day >= start_date in sprint and status != Done
    resurfaced_by_date: Dict[date, set] = defaultdict(set)
    for issue_key in completed_outside_set:
        for (ik, snap_d), row in by_issue_date.items():
            if ik != issue_key or snap_d is None or snap_d < start_date or snap_d > target_date:
                continue
            if sprint_id not in _sprint_ids_set(row.get("sprint_ids")):
                continue
            if (row.get("status_category") or "").strip() == "Done":
                continue
            resurfaced_by_date[snap_d].add(issue_key)
            break

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

        # Match chart: count as removed if added during sprint or if removal is after start_date
        if in_sprint_before and not in_sprint_now and prev_status != "Done" and (
            (first_seen is not None and first_seen >= start_date) or snap_d > start_date
        ):
            removed_through_date[snap_d].add(issue_key)
        if in_sprint_now and not in_sprint_before and first_seen is not None and first_seen < snap_d and snap_d > start_date:
            readded_through_date[snap_d].add(issue_key)

        prev_by_issue[issue_key] = dict(row)
        if "sprint_ids" in row:
            prev_by_issue[issue_key]["sprint_ids"] = row["sprint_ids"]
        prev_by_issue[issue_key]["status_category"] = (row.get("status_category") or "").strip()

    # Which issues were added on each day (first_seen == day, day > start_date)
    added_by_date: Dict[date, set] = defaultdict(set)
    for issue_key, first_seen in first_seen_in_sprint.items():
        if start_date < first_seen <= target_date:
            added_by_date[first_seen].add(issue_key)

    # Build in_scope by applying add/remove/re-add/resurfaced in date order (matches chart formula).
    in_scope: set = set(initial_scope)
    d = start_date
    while d <= target_date:
        in_scope |= added_by_date.get(d, set())
        in_scope -= removed_through_date.get(d, set())
        in_scope |= readded_through_date.get(d, set())
        in_scope |= resurfaced_by_date.get(d, set())
        d += timedelta(days=1)

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
