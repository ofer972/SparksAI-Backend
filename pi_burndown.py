"""
PI burndown calculation in Python.

Computes PI burndown series from raw PI details and jira_issue_history rows.
Replaces the logic previously in get_pi_burndown_data SQL function.
Aligns with sprint burndown adaptations: re-added, grace period for planned, same issue-list semantics.
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

# issue_lists_by_metric[metric][date] = list of {"issue_key", "team_name"}
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


def _effective_pi(row: Dict[str, Any]) -> Optional[str]:
    """COALESCE(quarter_pi_of_epic, quarter_pi) for PI membership."""
    q = row.get("quarter_pi_of_epic") or row.get("quarter_pi")
    if q is None:
        return None
    return (q.strip() or None) if isinstance(q, str) else None


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


def compute_pi_burndown_from_history(
    pi_name: str,
    start_date: date,
    end_date: date,
    grace_period_days: int,
    history_rows: List[Dict[str, Any]],
    team_names: Optional[List[str]],
    issue_type: str,
    project_keys: Optional[List[str]] = None,
    resolved_at_map: Optional[Dict[str, Optional[date]]] = None,
) -> Tuple[List[Dict[str, Any]], IssueListsByMetric]:
    """
    Compute PI burndown daily series and per-day issue lists from raw issue history.

    Based on get_pi_burndown_data SQL, with sprint-style adaptations:
    - Planned (initial scope): issues in PI on any day in [start_date, start_date + grace_period_days].
      Optionally exclude issues already Done before start_date (sprint-style).
    - Added: first_seen in PI on day > (start_date + grace_period_days).
    - Re-added: back in PI, first_seen in PI < current_date (same as sprint).
    - Remaining / WIP / Completed / Removed: same structure as sprint; total_scope includes re-added.
    - issues_added_on_day (display): first-time adds + re-adds for that day.
    - Completed: transition to Done while in PI; only count when resolved_at is in [start_date, end_date]
      when resolved_at_map is provided (same as sprint).

    history_rows must contain: issue_key, snapshot_date, quarter_pi, quarter_pi_of_epic,
    status_category, team_name, issuetype; and be filtered by date range and optionally team/issue_type/project.
    """
    today = date.today()
    end_date_cap = max(end_date, today)
    snapshot_dates: List[date] = []
    d = start_date
    while d <= end_date_cap:
        snapshot_dates.append(d)
        d += timedelta(days=1)

    # Build by (issue_key, snapshot_date) -> row; add effective_pi to each row
    by_issue_date: Dict[tuple, Dict] = {}
    for row in history_rows:
        key = (row["issue_key"], _normalize_date(row["snapshot_date"]))
        if key not in by_issue_date:
            r = dict(row)
            r["effective_pi"] = _effective_pi(row)
            by_issue_date[key] = r

    # Team filter: if team_names provided, only rows where team_name in team_names count for "in PI"
    def _in_scope(row: Dict[str, Any]) -> bool:
        if row.get("effective_pi") != pi_name:
            return False
        if team_names is not None and len(team_names) > 0:
            tn = (row.get("team_name") or "").strip()
            if tn not in team_names:
                return False
        if project_keys is not None and len(project_keys) > 0:
            ik = row.get("issue_key") or ""
            prefix = ik.split("-")[0] if "-" in ik else ik
            if prefix not in project_keys:
                return False
        return True

    # First-seen in PI per issue (for this PI name and filters)
    first_seen_in_pi: Dict[str, date] = {}
    for (issue_key, snap_d), row in by_issue_date.items():
        if snap_d is None:
            continue
        if not _in_scope(row):
            continue
        if issue_key not in first_seen_in_pi or snap_d < first_seen_in_pi[issue_key]:
            first_seen_in_pi[issue_key] = snap_d

    # Grace cutoff: planned = in PI on any day in [start_date, start_date + grace_period_days]
    grace_end = start_date + timedelta(days=grace_period_days)
    planned_set: set = set()
    for (issue_key, snap_d), row in by_issue_date.items():
        if snap_d is None:
            continue
        if start_date <= snap_d <= grace_end and _in_scope(row):
            planned_set.add(issue_key)

    # Exclude Done-before-start from planned (sprint-style)
    day_before_start = start_date - timedelta(days=1)
    completed_outside_set = set()
    for issue_key in planned_set:
        prev_row = by_issue_date.get((issue_key, day_before_start))
        if prev_row and (prev_row.get("status_category") or "").strip() == "Done":
            completed_outside_set.add(issue_key)
    initial_scope = planned_set - completed_outside_set
    planned_issues = len(initial_scope)

    # Added: first_seen in PI after grace_end (and sets/lists)
    daily_added: Dict[date, int] = defaultdict(int)
    added_by_date_set: Dict[date, set] = defaultdict(set)
    added_by_date_list: Dict[date, List[Dict[str, Any]]] = defaultdict(list)
    for issue_key, first_seen in first_seen_in_pi.items():
        if first_seen > grace_end:
            daily_added[first_seen] += 1
            added_by_date_set[first_seen].add(issue_key)
            row_add = by_issue_date.get((issue_key, first_seen), {})
            tn = row_add.get("team_name")
            team_name_add = (tn.strip() or None) if isinstance(tn, str) else None
            added_by_date_list[first_seen].append({"issue_key": issue_key, "team_name": team_name_add})

    # Per-day remaining and WIP (only days with data); then carry-forward
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
            if not _in_scope(row):
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

    # State changes: removed, completed, re-added
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
    for (issue_key, snap_d), row in sorted_rows:
        if snap_d is None:
            continue
        curr_in = _in_scope(row)
        curr_status = (row.get("status_category") or "").strip()
        prev = prev_by_issue.get(issue_key)
        prev_in = _in_scope(prev) if prev else False
        prev_status = (prev.get("status_category") or "").strip() if prev else ""
        first_seen = first_seen_in_pi.get(issue_key)
        tn = row.get("team_name")
        team_name_row = (tn.strip() or None) if isinstance(tn, str) else None

        # Removed: left PI, not Done. Count if added during PI (first_seen >= start_date) or removal after start_date
        removed_this_day = (
            prev_in and not curr_in and prev_status != "Done"
            and (
                (first_seen is not None and first_seen >= start_date)
                or snap_d > start_date
            )
        )
        if removed_this_day:
            daily_removed[snap_d] += 1
            daily_removed_set[snap_d].add(issue_key)
            daily_removed_list[snap_d].append({"issue_key": issue_key, "team_name": team_name_row})
        # Completed: transition to Done while in PI; exclude if same-day removed.
        # Only count when resolved_at is within PI date range (same as sprint) when resolved_at_map provided.
        resolved_ok = True
        if resolved_at_map is not None:
            ra = resolved_at_map.get(issue_key)
            resolved_ok = ra is not None and start_date <= ra <= end_date
        if curr_status == "Done" and prev_status != "Done" and curr_in and not removed_this_day and resolved_ok:
            daily_completed[snap_d] += 1
            daily_completed_list[snap_d].append({"issue_key": issue_key, "team_name": team_name_row})
        # Re-added: back in PI, was in PI before (first_seen < snap_d), day > start_date
        if curr_in and not prev_in and first_seen is not None and first_seen < snap_d:
            if snap_d > start_date:
                daily_readded[snap_d] += 1
                daily_readded_set[snap_d].add(issue_key)
                daily_readded_list[snap_d].append({"issue_key": issue_key, "team_name": team_name_row})

        prev_by_issue[issue_key] = dict(row)
        prev_by_issue[issue_key]["effective_pi"] = row.get("effective_pi")
        prev_by_issue[issue_key]["status_category"] = curr_status

    # Total scope: planned + cum(added) + cum(re-added) - cum(removed)
    cum_added = 0
    cum_removed = 0
    cum_readded = 0
    total_by_date: Dict[date, int] = {}
    in_scope_set: set = set(initial_scope)
    total_scope_list_by_date: Dict[date, List[Dict[str, Any]]] = {}
    for snap_d in snapshot_dates:
        cum_added += daily_added.get(snap_d, 0)
        cum_removed += daily_removed.get(snap_d, 0)
        cum_readded += daily_readded.get(snap_d, 0)
        in_scope_set |= added_by_date_set.get(snap_d, set())
        in_scope_set -= daily_removed_set.get(snap_d, set())
        in_scope_set |= daily_readded_set.get(snap_d, set())
        total_by_date[snap_d] = planned_issues + cum_added + cum_readded - cum_removed
        total_scope_list_by_date[snap_d] = [
            {"issue_key": k, "team_name": _team_name_for_issue_on_date(by_issue_date, k, snap_d)}
            for k in sorted(in_scope_set)
        ]
    peak_scope = max(total_by_date.values()) if total_by_date else 0
    pi_length_days = (end_date - start_date).days
    if pi_length_days <= 0:
        pi_length_days = 1

    # Build chart rows (same shape as get_pi_burndown_data)
    out: List[Dict[str, Any]] = []
    for snap_d in snapshot_dates:
        remaining = daily_remaining.get(snap_d, 0)
        wip = daily_wip.get(snap_d, 0)
        if snap_d > today:
            remaining = None
            wip = None
        ideal = round(
            max(0.0, peak_scope * (end_date - snap_d).days / pi_length_days),
            1,
        )
        out.append({
            "snapshot_date": snap_d,
            "pi_name": pi_name,
            "start_date": start_date,
            "end_date": end_date,
            "planned_issues": planned_issues,
            "issues_added_on_day": daily_added.get(snap_d, 0) + daily_readded.get(snap_d, 0),
            "issues_removed_on_day": daily_removed.get(snap_d, 0),
            "issues_completed_on_day": daily_completed.get(snap_d, 0),
            "remaining_issues": remaining,
            "ideal_remaining": int(ideal) if ideal == int(ideal) else ideal,
            "total_issues": total_by_date.get(snap_d, 0),
            "wip_issues_in_progress": wip,
        })
    issue_lists_by_metric: IssueListsByMetric = {
        "total_scope": {d: total_scope_list_by_date.get(d, []) for d in snapshot_dates},
        "issues_completed": {d: daily_completed_list.get(d, []) for d in snapshot_dates},
        "issues_removed": {d: daily_removed_list.get(d, []) for d in snapshot_dates},
        "issues_added": {d: added_by_date_list.get(d, []) + daily_readded_list.get(d, []) for d in snapshot_dates},
        "wip_in_progress": {d: wip_list_by_date.get(d, []) for d in snapshot_dates},
        "actual_remaining": {d: remaining_list_by_date.get(d, []) for d in snapshot_dates},
    }
    return (out, issue_lists_by_metric)


def compute_pi_scope_change_sets(
    pi_name: str,
    start_date: date,
    end_date: date,
    grace_period_days: int,
    history_rows: List[Dict[str, Any]],
    team_names: Optional[List[str]],
    issue_type: str,
    project_keys: Optional[List[str]] = None,
    resolved_at_map: Optional[Dict[str, Optional[date]]] = None,
) -> Dict[str, Any]:
    """
    Compute Planned, Added, Removed, Completed, Not completed sets for Epic scope change.
    Uses the same core as compute_pi_burndown_from_history so definitions match.
    Completed = in PI at end, status Done, and (when resolved_at_map provided) resolved_at in [start_date, end_date].
    Returns dict with keys: planned_issue_keys, added_issue_keys, removed_issue_keys,
    completed_issue_keys, not_completed_issue_keys (each a set of issue_key).
    """
    chart_rows, issue_lists = compute_pi_burndown_from_history(
        pi_name=pi_name,
        start_date=start_date,
        end_date=end_date,
        grace_period_days=grace_period_days,
        history_rows=history_rows,
        team_names=team_names,
        issue_type=issue_type,
        project_keys=project_keys,
        resolved_at_map=resolved_at_map,
    )
    # Planned = initial scope (from first row's total_scope at start_date, but we need the set)
    # We don't have initial_scope exposed; recompute minimal sets from chart logic.
    by_issue_date: Dict[tuple, Dict] = {}
    for row in history_rows:
        key = (row["issue_key"], _normalize_date(row["snapshot_date"]))
        if key not in by_issue_date:
            r = dict(row)
            r["effective_pi"] = _effective_pi(row)
            by_issue_date[key] = r

    def _in_scope(row: Dict[str, Any]) -> bool:
        if row.get("effective_pi") != pi_name:
            return False
        if team_names is not None and len(team_names) > 0:
            tn = (row.get("team_name") or "").strip()
            if tn not in team_names:
                return False
        if project_keys is not None and len(project_keys) > 0:
            ik = row.get("issue_key") or ""
            prefix = ik.split("-")[0] if "-" in ik else ik
            if prefix not in project_keys:
                return False
        return True

    grace_end = start_date + timedelta(days=grace_period_days)
    planned_set: set = set()
    for (issue_key, snap_d), row in by_issue_date.items():
        if snap_d is None:
            continue
        if start_date <= snap_d <= grace_end and _in_scope(row):
            planned_set.add(issue_key)
    day_before_start = start_date - timedelta(days=1)
    for issue_key in list(planned_set):
        prev_row = by_issue_date.get((issue_key, day_before_start))
        if prev_row and (prev_row.get("status_category") or "").strip() == "Done":
            planned_set.discard(issue_key)

    first_seen_in_pi: Dict[str, date] = {}
    for (issue_key, snap_d), row in by_issue_date.items():
        if snap_d is None or not _in_scope(row):
            continue
        if issue_key not in first_seen_in_pi or snap_d < first_seen_in_pi[issue_key]:
            first_seen_in_pi[issue_key] = snap_d
    added_set: set = set()
    for issue_key, first_seen in first_seen_in_pi.items():
        if first_seen > grace_end:
            added_set.add(issue_key)
    all_in_scope = planned_set | added_set

    # For "in PI at end" and "status at end": use latest available snapshot date (capped at end_date).
    # For a current/active PI, end_date is in the future so we have no rows for it; use latest data we have.
    snapshot_dates_in_data = [snap_d for (_, snap_d) in by_issue_date.keys() if snap_d is not None]
    reference_date = min(max(snapshot_dates_in_data), end_date) if snapshot_dates_in_data else end_date

    # Per issue: latest snapshot date on or before reference_date (so we don't require a snapshot on exact reference_date).
    latest_snap_by_issue: Dict[str, date] = {}
    for (issue_key, snap_d), row in by_issue_date.items():
        if snap_d is None or snap_d > reference_date:
            continue
        if issue_key not in latest_snap_by_issue or snap_d > latest_snap_by_issue[issue_key]:
            latest_snap_by_issue[issue_key] = snap_d

    # In PI at reference_date: have a row on or before reference_date showing in scope
    in_pi_at_end: set = set()
    status_at_end: Dict[str, str] = {}
    for issue_key in all_in_scope:
        snap_d = latest_snap_by_issue.get(issue_key)
        if snap_d is None:
            continue
        row = by_issue_date.get((issue_key, snap_d))
        if row is not None and _in_scope(row):
            in_pi_at_end.add(issue_key)
            status_at_end[issue_key] = (row.get("status_category") or "").strip()
    # Removed: was in scope (planned or added) by reference_date, but not in PI at reference_date.
    # Exclude issues whose first_seen > reference_date (added after reference_date - not "removed", just not yet in PI then).
    removed_set = {
        k for k in all_in_scope - in_pi_at_end
        if first_seen_in_pi.get(k) is not None and first_seen_in_pi[k] <= reference_date
    }

    # Completed = in PI at end, Done at end, and (when resolved_at_map) resolved_at in PI range
    done_at_end = {k for k in in_pi_at_end if status_at_end.get(k) == "Done"}
    if resolved_at_map is not None:
        done_at_end = {
            k for k in done_at_end
            if resolved_at_map.get(k) is not None and start_date <= resolved_at_map[k] <= end_date
        }
    completed_set = done_at_end - removed_set
    not_completed_set = all_in_scope - removed_set - completed_set

    return {
        "planned_issue_keys": planned_set,
        "added_issue_keys": added_set,
        "removed_issue_keys": removed_set,
        "completed_issue_keys": completed_set,
        "not_completed_issue_keys": not_completed_set,
        "all_in_scope": all_in_scope,
    }
