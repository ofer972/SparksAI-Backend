"""
Database helpers and data dispatchers for report metadata.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection
from fastapi import HTTPException

import config
from database_pi import (
    fetch_pi_burndown_data,
    fetch_pi_predictability_data,
    fetch_scope_changes_data,
    fetch_pi_summary_data,
    fetch_pi_summary_data_by_team,
    fetch_epic_inbound_dependency_data,
    fetch_epic_outbound_dependency_data,
)
from database_team_metrics import (
    get_sprint_burndown_data_db,
    get_sprints_with_total_issues_db,
    get_team_current_sprint_progress,
    get_closed_sprints_data_db,
    get_issues_trend_data_db,
    select_sprint_for_teams,
)

logger = logging.getLogger(__name__)

ReportDefinition = Dict[str, Any]
ReportDataResult = Dict[str, Any]
ReportDataFetcher = Callable[[Dict[str, Any], Connection], ReportDataResult]

MIN_DURATION_DAYS = 0.05
VALID_DURATION_MONTHS = {1, 2, 3, 4, 6, 9}
DEFAULT_HIERARCHY_LIMIT = 500


def _ensure_json_field(row_dict: Dict[str, Any], field: str) -> None:
    """
    Convert JSONB/text fields coming from PostgreSQL into Python dicts.
    """
    value = row_dict.get(field)
    if value is None:
        row_dict[field] = {}
        return

    if isinstance(value, (dict, list)):
        return

    if isinstance(value, str):
        try:
            row_dict[field] = json.loads(value)
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSON field '%s' for report '%s'", field, row_dict.get("report_id"))
            row_dict[field] = {}


def _date_to_iso(value: Optional[date | datetime]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def get_all_report_definitions(conn: Connection) -> List[ReportDefinition]:
    """
    Fetch all report definitions.
    """
    query = text(
        f"""
        SELECT
            report_id,
            report_name,
            chart_type,
            data_source,
            description,
            default_filters,
            meta_schema,
            created_at,
            updated_at
        FROM {config.REPORT_DEFINITIONS_TABLE}
        ORDER BY report_name
        """
    )

    results = conn.execute(query)
    definitions: List[ReportDefinition] = []
    for row in results:
        row_dict = dict(row._mapping)
        _ensure_json_field(row_dict, "default_filters")
        _ensure_json_field(row_dict, "meta_schema")
        definitions.append(row_dict)

    return definitions


def get_report_definition_by_id(report_id: str, conn: Connection) -> Optional[ReportDefinition]:
    """
    Fetch a single report definition by ID.
    """
    query = text(
        f"""
        SELECT
            report_id,
            report_name,
            chart_type,
            data_source,
            description,
            default_filters,
            meta_schema,
            created_at,
            updated_at
        FROM {config.REPORT_DEFINITIONS_TABLE}
        WHERE report_id = :report_id
        """
    )

    result = conn.execute(query, {"report_id": report_id})
    row = result.fetchone()
    if not row:
        return None

    row_dict = dict(row._mapping)
    _ensure_json_field(row_dict, "default_filters")
    _ensure_json_field(row_dict, "meta_schema")
    return row_dict


def get_supported_data_sources() -> List[str]:
    """
    Return a list of supported data source keys.
    """
    return list(_REPORT_DATA_FETCHERS.keys())


def resolve_report_data(data_source: str, filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    """
    Resolve report data via the dispatcher registry.

    Raises:
        KeyError: If the data source is not supported.
        ValueError: If required filters are missing.
    """
    fetcher = _REPORT_DATA_FETCHERS.get(data_source)
    if fetcher is None:
        raise KeyError(f"Unsupported report data source '{data_source}'")

    logger.info("Resolving report data for data_source='%s' with filters=%s", data_source, filters)
    return fetcher(filters, conn)


def _require_filter(filters: Dict[str, Any], key: str) -> Any:
    value = filters.get(key)
    if value is None or (isinstance(value, str) and value.strip() == ""):
        raise ValueError(f"Missing required filter '{key}'")
    return value


def _fetch_team_sprint_burndown(filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    team_name = filters.get("team_name")
    issue_type = (filters.get("issue_type") or "all").strip() or "all"
    sprint_name = filters.get("sprint_name")
    is_group = filters.get("isGroup", False)

    # Fetch available teams from cache
    from groups_teams_cache import get_cached_teams, set_cached_teams, load_team_names_from_db, load_all_teams_from_db
    
    cached = get_cached_teams()
    if cached:
        available_teams = [t["team_name"] for t in cached.get("teams", [])]
    else:
        # Cache miss - load from DB
        available_teams = load_team_names_from_db(conn)
        # Also build full teams cache for future use
        all_teams = load_all_teams_from_db(conn)
        set_cached_teams({"teams": all_teams, "count": len(all_teams)})

    # Only fetch sprint data if team is selected
    if not team_name:
        return {
            "data": [],
            "meta": {
                "team_name": None,
                "issue_type": issue_type,
                "sprint_name": None,
                "sprint_id": None,
                "auto_selected": False,
                "total_issues_in_sprint": 0,
                "start_date": None,
                "end_date": None,
                "available_teams": available_teams,
            },
        }

    # Use shared helper for sprint selection
    sprint_selection = select_sprint_for_teams(team_name, is_group, sprint_name, conn)
    team_names_list = sprint_selection['team_names_list']
    selected_sprint_name = sprint_selection['selected_sprint_name']
    selected_sprint_id = sprint_selection['selected_sprint_id']
    selected_sprint_start_date = sprint_selection.get('selected_sprint_start_date')
    selected_sprint_end_date = sprint_selection.get('selected_sprint_end_date')
    error_message = sprint_selection['error_message']
    auto_selected = sprint_name is None and selected_sprint_name is not None

    # If error occurred, return error in report format
    if error_message:
        return {
            "data": [],
            "meta": {
                "team_name": team_name,
                "issue_type": issue_type,
                "sprint_name": None,
                "sprint_id": None,
                "auto_selected": False,
                "total_issues_in_sprint": 0,
                "start_date": None,
                "end_date": None,
                "available_teams": available_teams,
                "error_message": error_message,
            },
        }

    if not selected_sprint_name:
        return {
            "data": [],
            "meta": {
                "team_name": team_name,
                "issue_type": issue_type,
                "sprint_name": None,
                "sprint_id": None,
                "auto_selected": auto_selected,
                "total_issues_in_sprint": 0,
                "start_date": None,
                "end_date": None,
                "available_teams": available_teams,
            },
        }

    burndown_data = get_sprint_burndown_data_db(team_names_list, selected_sprint_name, issue_type, conn)

    total_issues = 0
    # Use dates from sprint selection first, fall back to burndown_data if needed
    start_date: Optional[str] = _date_to_iso(selected_sprint_start_date) if selected_sprint_start_date else None
    end_date: Optional[str] = _date_to_iso(selected_sprint_end_date) if selected_sprint_end_date else None

    if burndown_data:
        first_entry = burndown_data[0]
        total_issues = first_entry.get("total_issues", 0) or 0
        # Only use burndown_data dates if sprint selection didn't provide them
        if not start_date:
            start_date = _date_to_iso(first_entry.get("start_date"))
        if not end_date:
            end_date = _date_to_iso(first_entry.get("end_date"))

    return {
        "data": burndown_data,
        "meta": {
            "team_name": team_name,
            "issue_type": issue_type,
            "sprint_name": sprint_name,
            "sprint_id": selected_sprint_id,
            "auto_selected": auto_selected,
            "total_issues_in_sprint": total_issues,
            "start_date": start_date,
            "end_date": end_date,
            "available_teams": available_teams,
        },
    }


def _fetch_team_current_sprint_progress(filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    team_name = _require_filter(filters, "team_name")
    progress = get_team_current_sprint_progress(team_name, conn)

    def _calculate_days_left(end_date_value: Optional[date | datetime]) -> Optional[int]:
        if end_date_value is None:
            return None
        if isinstance(end_date_value, datetime):
            end = end_date_value.date()
        else:
            end = end_date_value

        today = date.today()
        if end < today:
            return 0
        return (end - today).days + 1

    def _calculate_days_in_sprint(start_date_value: Optional[date | datetime], end_date_value: Optional[date | datetime]) -> Optional[int]:
        if start_date_value is None or end_date_value is None:
            return None
        if isinstance(start_date_value, datetime):
            start = start_date_value.date()
        else:
            start = start_date_value
        if isinstance(end_date_value, datetime):
            end = end_date_value.date()
        else:
            end = end_date_value
        return (end - start).days + 1

    def _percent_completed_status(percent_completed: float, start_date_value: Optional[date | datetime], end_date_value: Optional[date | datetime]) -> str:
        if start_date_value is None or end_date_value is None:
            return "green"
        if isinstance(start_date_value, datetime):
            start = start_date_value.date()
        else:
            start = start_date_value
        if isinstance(end_date_value, datetime):
            end = end_date_value.date()
        else:
            end = end_date_value

        today = date.today()
        slack_threshold = 15.0
        if today < start:
            return "green"
        if today >= end:
            if percent_completed >= 100 - slack_threshold:
                return "green"
            if percent_completed >= 75:
                return "yellow"
            return "red"

        total_days = (end - start).days
        if total_days <= 0:
            return "green"

        days_elapsed = (today - start).days
        expected_completion = (days_elapsed / total_days) * 100

        if percent_completed >= expected_completion - slack_threshold:
            return "green"
        if percent_completed >= expected_completion - 25.0:
            return "yellow"
        return "red"

    def _in_progress_issues_status(in_progress: int, total: int) -> str:
        if total == 0:
            return "green"
        percentage = (in_progress / total) * 100
        if percentage > 60:
            return "red"
        if percentage >= 40:
            return "yellow"
        return "green"

    days_left = _calculate_days_left(progress.get("end_date"))
    days_in_sprint = _calculate_days_in_sprint(progress.get("start_date"), progress.get("end_date"))
    percent_completed = float(progress.get("percent_completed") or 0.0)
    total_issues = int(progress.get("total_issues") or 0)
    in_progress_issues = int(progress.get("in_progress_issues") or 0)

    percent_status = _percent_completed_status(percent_completed, progress.get("start_date"), progress.get("end_date"))
    wip_status = _in_progress_issues_status(in_progress_issues, total_issues)

    serialized_progress = {
        **progress,
        "start_date": _date_to_iso(progress.get("start_date")),
        "end_date": _date_to_iso(progress.get("end_date")),
        "days_left": days_left,
        "days_in_sprint": days_in_sprint,
        "percent_completed_status": percent_status,
        "in_progress_issues_status": wip_status,
    }

    return {
        "data": serialized_progress,
        "meta": {
            "team_name": team_name,
        },
    }


def _fetch_pi_burndown(filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    from database_team_metrics import resolve_team_names_from_filter
    
    pi_name = (filters.get("pi") or "").strip() or None
    issue_type = (filters.get("issue_type") or "Epic").strip() or "Epic"
    project = filters.get("project")
    # Standardize on team_name: check team_name first, then fall back to team for backward compatibility
    team = (filters.get("team_name") or filters.get("team") or "").strip() or None
    is_group = filters.get("isGroup", False)

    # Fetch available PIs (always)
    pis_query = text(
        f"""
        SELECT DISTINCT pi_name
        FROM {config.PIS_TABLE}
        WHERE pi_name IS NOT NULL
        ORDER BY pi_name DESC
        """
    )
    pis_rows = conn.execute(pis_query).fetchall()
    available_pis = [row[0] for row in pis_rows if row[0]]

    # Resolve team names using shared helper function (handles single team, group, or None)
    team_names_list = resolve_team_names_from_filter(team, is_group, conn)

    # Only fetch burndown data if a PI is selected
    burndown_data = []
    if pi_name:
        burndown_data = fetch_pi_burndown_data(
            pi_name=pi_name,
            project_keys=project,
            issue_type=issue_type,
            team_names=team_names_list,
            conn=conn,
        )

    # Build meta with appropriate fields
    meta = {
        "pi": pi_name,
        "issue_type": issue_type,
        "project": project,
        "isGroup": is_group,
        "available_pis": available_pis,
    }
    
    # Add team/group information to meta (standardize on team_name)
    if team:
        if is_group:
            meta["group_name"] = team
            meta["teams_in_group"] = team_names_list
        else:
            meta["team_name"] = team
            meta["team"] = team  # Keep for backward compatibility
    else:
        meta["team_name"] = None
        meta["team"] = None

    return {
        "data": burndown_data,
        "meta": meta,
    }


def _parse_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        # Support comma-separated values
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(value).strip()]


def _fetch_closed_sprints_flat_report(filters: Dict[str, Any], conn: Connection, sort_by: str = "default") -> ReportDataResult:
    """
    Shared helper function to fetch closed sprints in flat structure for reports.
    
    Args:
        filters: Dictionary with team_name, isGroup, months, issue_type
        conn: Database connection
        sort_by: Sort order - "default" or "advanced"
    
    Returns:
        ReportDataResult with flat array of sprints and metadata
    """
    from database_team_metrics import resolve_team_names_from_filter
    
    team_name = filters.get("team_name")
    is_group = filters.get("isGroup", False)
    months_value = filters.get("months")
    months = _parse_int(months_value, default=3)
    if months <= 0:
        months = 3

    # Validate months parameter
    if months not in [1, 2, 3, 4, 6, 9]:
        months = 3

    # Get issue_type filter (optional)
    issue_type = filters.get("issue_type")

    # Fetch available teams from cache
    from groups_teams_cache import get_cached_teams, set_cached_teams, load_team_names_from_db, load_all_teams_from_db
    
    cached = get_cached_teams()
    if cached:
        available_teams = [t["team_name"] for t in cached.get("teams", [])]
    else:
        # Cache miss - load from DB
        available_teams = load_team_names_from_db(conn)
        # Also build full teams cache for future use
        all_teams = load_all_teams_from_db(conn)
        set_cached_teams({"teams": all_teams, "count": len(all_teams)})

    # Fetch available issue types (always)
    issue_types_query = text(
        f"""
        SELECT issue_type
        FROM {config.ISSUE_TYPES_TABLE}
        ORDER BY issue_type
        """
    )
    issue_types_rows = conn.execute(issue_types_query).fetchall()
    available_issue_types = [row[0] for row in issue_types_rows if row[0]]

    # Resolve team names using shared helper function
    team_names_list = resolve_team_names_from_filter(team_name, is_group, conn)

    # Fetch closed sprints with specified sort order
    closed_sprints = get_closed_sprints_data_db(team_names_list, months, issue_type, sort_by=sort_by, conn=conn)

    # Calculate average velocity: sum of issues_done across all sprints / number of sprints
    sprint_count = len(closed_sprints)
    total_issues_done = sum(sprint.get('issues_done', 0) or 0 for sprint in closed_sprints)
    average_velocity = round(total_issues_done / sprint_count, 2) if sprint_count > 0 else 0.0

    return {
        "data": closed_sprints,
        "meta": {
            "team_name": team_name,
            "isGroup": is_group,
            "months": months,
            "issue_type": issue_type,
            "count": len(closed_sprints),
            "average_velocity": average_velocity,
            "available_teams": available_teams,
            "available_issue_types": available_issue_types,
        },
    }


def _fetch_team_closed_sprints(filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    """Fetch team closed sprints report (uses default sorting)."""
    return _fetch_closed_sprints_flat_report(filters, conn, sort_by="default")


def _fetch_team_sprint_velocity_advanced(filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    """Fetch team sprint velocity advanced report (sorted by start_date ASC, team_name ASC)."""
    return _fetch_closed_sprints_flat_report(filters, conn, sort_by="advanced")


def _fetch_team_issues_trend(filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    team_name = _require_filter(filters, "team_name")
    is_group = filters.get("isGroup", False)
    issue_type = (filters.get("issue_type") or "Bug").strip() or "Bug"
    months_value = filters.get("months")
    months = _parse_int(months_value, default=6)
    if months <= 0:
        months = 6

    # Resolve team names using shared helper function (same pattern as other endpoints)
    from database_team_metrics import resolve_team_names_from_filter
    team_names_list = resolve_team_names_from_filter(team_name, is_group, conn)

    # Get trend data for all teams
    trend_data = get_issues_trend_data_db(team_names_list, months, issue_type, conn)

    # Build meta with appropriate fields
    meta = {
        "issue_type": issue_type,
        "months": months,
        "count": len(trend_data),
    }
    
    if is_group:
        meta["group_name"] = team_name
        meta["teams_in_group"] = team_names_list
    else:
        meta["team_name"] = team_name

    return {
        "data": trend_data,
        "meta": meta,
    }


def _fetch_pi_predictability(filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    from database_team_metrics import resolve_team_names_from_filter
    
    pi_values = filters.get("pi_names") or filters.get("pi")
    pi_list = _parse_list(pi_values)

    # Standardize on team_name: check team_name first, then fall back to team for backward compatibility
    team = (filters.get("team_name") or filters.get("team") or "").strip() or None
    is_group = filters.get("isGroup", False)
    
    # Fetch available teams from cache
    from groups_teams_cache import get_cached_teams, set_cached_teams, load_team_names_from_db, load_all_teams_from_db
    
    cached = get_cached_teams()
    if cached:
        available_teams = [t["team_name"] for t in cached.get("teams", [])]
    else:
        # Cache miss - load from DB
        available_teams = load_team_names_from_db(conn)
        # Also build full teams cache for future use
        all_teams = load_all_teams_from_db(conn)
        set_cached_teams({"teams": all_teams, "count": len(all_teams)})

    # Fetch available PIs (always)
    pis_query = text(
        f"""
        SELECT DISTINCT pi_name
        FROM {config.PIS_TABLE}
        WHERE pi_name IS NOT NULL
        ORDER BY pi_name DESC
        """
    )
    pis_rows = conn.execute(pis_query).fetchall()
    available_pis = [row[0] for row in pis_rows if row[0]]

    # Resolve team names using shared helper function (handles single team, group, or None)
    team_names_list = resolve_team_names_from_filter(team, is_group, conn)

    # Only fetch predictability data if PIs are selected
    if pi_list:
        predictability_data = fetch_pi_predictability_data(pi_list, team_names=team_names_list, conn=conn)
    else:
        predictability_data = []

    # Build meta with appropriate fields
    meta = {
        "pi_names": pi_list,
        "isGroup": is_group,
        "count": len(predictability_data),
        "available_teams": available_teams,
        "available_pis": available_pis,
    }
    
    # Add team/group information to meta (standardize on team_name)
    if team:
        if is_group:
            meta["group_name"] = team
            meta["teams_in_group"] = team_names_list
        else:
            meta["team_name"] = team
            meta["team"] = team  # Keep for backward compatibility
    else:
        meta["team_name"] = None
        meta["team"] = None

    return {
        "data": predictability_data,
        "meta": meta,
    }


def _fetch_epic_scope_changes(filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    quarters_value = filters.get("quarters") or filters.get("quarter")
    quarters = _parse_list(quarters_value)

    # Fetch available PIs (always)
    pis_query = text(
        f"""
        SELECT DISTINCT pi_name
        FROM {config.PIS_TABLE}
        WHERE pi_name IS NOT NULL
        ORDER BY pi_name DESC
        """
    )
    pis_rows = conn.execute(pis_query).fetchall()
    available_pis = [row[0] for row in pis_rows if row[0]]

    # Only fetch scope data if quarters are selected
    if quarters:
        scope_data = fetch_scope_changes_data(quarters, conn=conn)
    else:
        scope_data = []

    return {
        "data": scope_data,
        "meta": {
            "quarters": quarters,
            "count": len(scope_data),
            "available_pis": available_pis,
        },
    }


def _build_issue_where_clause(
    issue_type: Optional[str],
    team_name: Optional[str],
    status_category: Optional[str],
    include_done: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    conditions: List[str] = []
    params: Dict[str, Any] = {}

    if issue_type:
        conditions.append("issue_type = :issue_type")
        params["issue_type"] = issue_type

    if team_name:
        conditions.append("team_name = :team_name")
        params["team_name"] = team_name

    if status_category:
        conditions.append("status_category = :status_category")
        params["status_category"] = status_category
    elif not include_done:
        conditions.append("status_category != 'Done'")

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    return where_clause, params


def _fetch_issues_bugs_by_priority(filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    issue_type = (filters.get("issue_type") or "Bug").strip() or "Bug"
    team_name = (filters.get("team_name") or "").strip() or None
    is_group = filters.get("isGroup", False)
    status_category = (filters.get("status_category") or "").strip() or None
    include_done = bool(filters.get("include_done"))

    # Resolve team names using shared helper function (same pattern as other endpoints)
    from database_team_metrics import resolve_team_names_from_filter
    team_names_list = None
    if team_name:
        team_names_list = resolve_team_names_from_filter(team_name, is_group, conn)
    
    # Build WHERE clause with support for multiple teams
    conditions: List[str] = []
    params: Dict[str, Any] = {}

    if issue_type:
        conditions.append("issue_type = :issue_type")
        params["issue_type"] = issue_type

    if team_names_list:
        # Build parameterized IN clause (same pattern as closed sprints)
        placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names_list))])
        conditions.append(f"team_name IN ({placeholders})")
        for i, name in enumerate(team_names_list):
            params[f"team_name_{i}"] = name

    if status_category:
        conditions.append("status_category = :status_category")
        params["status_category"] = status_category
    elif not include_done:
        conditions.append("status_category != 'Done'")

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    priority_query = text(
        f"""
        SELECT 
            priority,
            status_category,
            COUNT(*) AS issue_count
        FROM {config.WORK_ITEMS_TABLE}
        WHERE {where_clause}
        GROUP BY priority, status_category
        ORDER BY priority, status_category
        """
    )

    priority_rows = conn.execute(priority_query, params).fetchall()
    priority_summary: List[Dict[str, Any]] = []
    for row in priority_rows:
        priority_summary.append(
            {
                "priority": row[0] if row[0] is not None else "Unspecified",
                "status_category": row[1] if row[1] is not None else "Unspecified",
                "issue_count": int(row[2]) if row[2] is not None else 0,
            }
        )

    team_query = text(
        f"""
        SELECT
            team_name,
            priority,
            COUNT(*) AS issue_count
        FROM {config.WORK_ITEMS_TABLE}
        WHERE {where_clause}
        GROUP BY team_name, priority
        ORDER BY team_name, priority
        """
    )

    team_rows = conn.execute(team_query, params).fetchall()
    teams_dict: Dict[str, Dict[str, Any]] = {}
    for row in team_rows:
        team = row[0] if row[0] is not None else "Unspecified"
        priority_value = row[1] if row[1] is not None else "Unspecified"
        count = int(row[2]) if row[2] is not None else 0

        if team not in teams_dict:
            teams_dict[team] = {"priorities": [], "total_issues": 0}

        teams_dict[team]["priorities"].append({"priority": priority_value, "issue_count": count})
        teams_dict[team]["total_issues"] += count

    team_breakdown = [
        {
            "team_name": team,
            "priorities": data["priorities"],
            "total_issues": data["total_issues"],
        }
        for team, data in teams_dict.items()
    ]

    # Fetch all available team names from cache (for dropdown - no issue_type filter needed)
    from groups_teams_cache import get_cached_teams, set_cached_teams, load_team_names_from_db, load_all_teams_from_db
    
    cached = get_cached_teams()
    if cached:
        available_teams = [t["team_name"] for t in cached.get("teams", [])]
    else:
        # Cache miss - load from DB
        available_teams = load_team_names_from_db(conn)
        # Also build full teams cache for future use
        all_teams = load_all_teams_from_db(conn)
        set_cached_teams({"teams": all_teams, "count": len(all_teams)})

    # Build meta with appropriate fields
    meta = {
        "issue_type": issue_type,
        "status_category": status_category,
        "include_done": include_done,
        "priority_count": len(priority_summary),
        "team_count": len(team_breakdown),
        "available_teams": available_teams,
        "isGroup": is_group,
    }
    
    if team_name:
        if is_group:
            meta["group_name"] = team_name
            meta["teams_in_group"] = team_names_list
        else:
            meta["team_name"] = team_name

    return {
        "data": {
            "priority_summary": priority_summary,
            "team_breakdown": team_breakdown,
        },
        "meta": meta,
    }


def _fetch_issues_bugs_by_team(filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    """
    Fetch bugs grouped by team with priority breakdown
    """
    issue_type = (filters.get("issue_type") or "Bug").strip() or "Bug"
    status_category = (filters.get("status_category") or "").strip() or None
    include_done = bool(filters.get("include_done"))

    # Don't filter by team for this report - we want all teams
    where_clause, params = _build_issue_where_clause(issue_type, None, status_category, include_done)

    team_query = text(
        f"""
        SELECT
            team_name,
            priority,
            COUNT(*) AS issue_count
        FROM {config.WORK_ITEMS_TABLE}
        WHERE {where_clause}
        GROUP BY team_name, priority
        ORDER BY team_name, priority
        """
    )

    team_rows = conn.execute(team_query, params).fetchall()
    teams_dict: Dict[str, Dict[str, Any]] = {}
    for row in team_rows:
        team = row[0] if row[0] is not None else "Unspecified"
        priority_value = row[1] if row[1] is not None else "Unspecified"
        count = int(row[2]) if row[2] is not None else 0

        if team not in teams_dict:
            teams_dict[team] = {"priorities": [], "total_issues": 0}

        teams_dict[team]["priorities"].append({"priority": priority_value, "issue_count": count})
        teams_dict[team]["total_issues"] += count

    team_breakdown = [
        {
            "team_name": team,
            "priorities": data["priorities"],
            "total_issues": data["total_issues"],
        }
        for team, data in sorted(teams_dict.items())
    ]

    return {
        "data": {
            "team_breakdown": team_breakdown,
        },
        "meta": {
            "issue_type": issue_type,
            "status_category": status_category,
            "include_done": include_done,
            "team_count": len(team_breakdown),
        },
    }


def _validate_months(months_value: Any, default: int = 3) -> int:
    months = _parse_int(months_value, default=default)
    if months not in VALID_DURATION_MONTHS:
        return default
    return months


def _fetch_issue_status_duration_summary(
    months: int,
    issue_type: Optional[str],
    team_names: Optional[List[str]],
    conn: Connection,
) -> List[Dict[str, Any]]:
    start_date = datetime.now().date() - timedelta(days=months * 30)

    where_conditions = [
        "isd.status_category = 'In Progress'",
        f"isd.duration_days >= {MIN_DURATION_DAYS}",
        "isd.time_exited >= :start_date",
    ]
    params: Dict[str, Any] = {"start_date": start_date.strftime("%Y-%m-%d")}

    if issue_type:
        where_conditions.append("isd.issue_type = :issue_type")
        params["issue_type"] = issue_type

    if team_names:
        # Build parameterized IN clause (same pattern as closed sprints)
        placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names))])
        where_conditions.append(f"isd.team_name IN ({placeholders})")
        for i, name in enumerate(team_names):
            params[f"team_name_{i}"] = name

    where_clause = " AND ".join(where_conditions)

    query = text(
        f"""
        SELECT 
            isd.status_name,
            AVG(isd.duration_days) AS avg_duration_days
        FROM public.issue_status_durations isd
        WHERE {where_clause}
        GROUP BY isd.status_name
        HAVING AVG(isd.duration_days) >= {MIN_DURATION_DAYS}
        ORDER BY
            CASE
                WHEN isd.status_name = 'In Progress' THEN 1
                WHEN isd.status_name LIKE '%Review%' THEN 2
                WHEN isd.status_name LIKE '%QA%' THEN 3
                WHEN isd.status_name LIKE '%Approved%' THEN 4
                ELSE 99
            END
        """
    )

    rows = conn.execute(query, params).fetchall()
    summary: List[Dict[str, Any]] = []
    for row in rows:
        summary.append(
            {
                "status_name": row[0],
                "avg_duration_days": float(row[1]) if row[1] is not None else 0.0,
            }
        )
    return summary


def _generate_month_labels(months: int) -> List[str]:
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=months * 30)

    labels: List[str] = []
    current = start_date.replace(day=1)
    end_month = end_date.replace(day=1)

    while current <= end_month:
        labels.append(current.strftime('%Y-%m'))
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)

    return labels


def _fetch_issue_status_duration_monthly(
    months: int,
    team_names: Optional[List[str]],
    conn: Connection,
) -> Dict[str, Any]:
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=months * 30)
    month_labels = _generate_month_labels(months)

    where_conditions = [
        "isd.time_exited >= :start_date",
        "isd.time_exited < :end_date",
        "isd.status_category = 'In Progress'",
        f"isd.duration_days >= {MIN_DURATION_DAYS}",
    ]
    params: Dict[str, Any] = {
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
    }

    if team_names:
        # Build parameterized IN clause (same pattern as closed sprints)
        placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names))])
        where_conditions.append(f"isd.team_name IN ({placeholders})")
        for i, name in enumerate(team_names):
            params[f"team_name_{i}"] = name

    where_clause = " AND ".join(where_conditions)

    query = text(
        f"""
        SELECT
            isd.status_name,
            TO_CHAR(isd.time_exited, 'YYYY-MM') AS month_exited,
            AVG(isd.duration_days) AS avg_duration_days
        FROM public.issue_status_durations isd
        WHERE {where_clause}
        GROUP BY isd.status_name, month_exited
        HAVING AVG(isd.duration_days) >= {MIN_DURATION_DAYS}
        ORDER BY
            CASE
                WHEN isd.status_name = 'In Progress' THEN 1
                WHEN isd.status_name LIKE '%Review%' THEN 2
                WHEN isd.status_name LIKE '%QA%' THEN 3
                WHEN isd.status_name LIKE '%Approved%' THEN 4
                ELSE 99
            END,
            month_exited
        """
    )

    rows = conn.execute(query, params).fetchall()
    status_data: Dict[str, Dict[str, float]] = {}
    for row in rows:
        status_name = row[0]
        month_exited = row[1]
        avg_duration = float(row[2]) if row[2] is not None else 0.0

        status_data.setdefault(status_name, {})
        status_data[status_name][month_exited] = avg_duration

    def status_priority(name: str) -> int:
        if name == 'In Progress':
            return 1
        if 'Review' in name:
            return 2
        if 'QA' in name:
            return 3
        if 'Approved' in name:
            return 4
        return 99

    datasets: List[Dict[str, Any]] = []
    for status_name in sorted(status_data.keys(), key=status_priority):
        data_values = [status_data[status_name].get(month, 0.0) for month in month_labels]
        datasets.append(
            {
                "label": status_name,
                "data": data_values,
            }
        )

    return {
        "labels": month_labels,
        "datasets": datasets,
        "months": months,
        "team_name": team_names[0] if team_names and len(team_names) == 1 else None,
    }


def _fetch_issue_status_duration_detail(
    status_name: str,
    months: int,
    year_month: Optional[str],
    issue_type: Optional[str],
    team_names: Optional[List[str]],
    conn: Connection,
) -> Dict[str, Any]:
    status = status_name.strip()
    if not status:
        raise ValueError("detail_status cannot be empty")

    use_year_month = False
    if year_month:
        if not re.match(r"^\d{4}-\d{2}$", year_month):
            raise ValueError("detail_year_month must be in YYYY-MM format")
        use_year_month = True

    if not use_year_month:
        months = _validate_months(months, default=3)
        start_date = datetime.now().date() - timedelta(days=months * 30)

    where_conditions = [
        "isd.status_category = 'In Progress'",
        "isd.status_name = :status_name",
        f"isd.duration_days >= {MIN_DURATION_DAYS}",
    ]
    params: Dict[str, Any] = {"status_name": status}

    if use_year_month:
        where_conditions.append("TO_CHAR(isd.time_exited, 'YYYY-MM') = :year_month")
        params["year_month"] = year_month
    else:
        where_conditions.append("isd.time_exited >= :start_date")
        params["start_date"] = (datetime.now().date() - timedelta(days=months * 30)).strftime("%Y-%m-%d")

    if issue_type:
        where_conditions.append("isd.issue_type = :issue_type")
        params["issue_type"] = issue_type

    if team_names:
        # Build parameterized IN clause (same pattern as closed sprints)
        placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names))])
        where_conditions.append(f"isd.team_name IN ({placeholders})")
        for i, name in enumerate(team_names):
            params[f"team_name_{i}"] = name

    where_clause = " AND ".join(where_conditions)

    query = text(
        f"""
        SELECT 
            isd.issue_key,
            ji.summary,
            isd.duration_days,
            isd.time_entered,
            isd.time_exited,
            isd.team_name,
            isd.issue_type
        FROM public.issue_status_durations isd
        INNER JOIN public.jira_issues ji ON isd.issue_key = ji.issue_key
        WHERE {where_clause}
        ORDER BY isd.time_exited DESC
        """
    )

    rows = conn.execute(query, params).fetchall()
    issues: List[Dict[str, Any]] = []
    for row in rows:
        issues.append(
            {
                "issue_key": row[0],
                "summary": row[1],
                "duration_days": float(row[2]) if row[2] is not None else 0.0,
                "time_entered": _date_to_iso(row[3]),
                "time_exited": _date_to_iso(row[4]),
                "team_name": row[5],
                "issue_type": row[6],
            }
        )

    return {
        "issues": issues,
        "count": len(issues),
        "status_name": status,
        "months": months if not use_year_month else None,
        "year_month": year_month if use_year_month else None,
    }


def _fetch_issues_flow_status_duration(filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    from database_team_metrics import resolve_team_names_from_filter
    
    months = _validate_months(filters.get("months"), default=3)
    issue_type = (filters.get("issue_type") or "").strip() or None
    team_name = (filters.get("team_name") or "").strip() or None
    is_group = filters.get("isGroup", False)
    view_mode = (filters.get("view_mode") or "total").strip() or "total"

    # Resolve team names using shared helper function
    team_names_list = resolve_team_names_from_filter(team_name, is_group, conn)

    # Fetch available teams from cache
    from groups_teams_cache import get_cached_teams, set_cached_teams, load_team_names_from_db, load_all_teams_from_db
    
    cached = get_cached_teams()
    if cached:
        available_teams = [t["team_name"] for t in cached.get("teams", [])]
    else:
        # Cache miss - load from DB
        available_teams = load_team_names_from_db(conn)
        # Also build full teams cache for future use
        all_teams = load_all_teams_from_db(conn)
        set_cached_teams({"teams": all_teams, "count": len(all_teams)})

    # Fetch available issue types (always)
    issue_types_query = text(
        f"""
        SELECT issue_type
        FROM {config.ISSUE_TYPES_TABLE}
        ORDER BY issue_type
        """
    )
    issue_types_rows = conn.execute(issue_types_query).fetchall()
    available_issue_types = [row[0] for row in issue_types_rows if row[0]]

    summary_data = _fetch_issue_status_duration_summary(months, issue_type, team_names_list, conn)
    monthly_data = _fetch_issue_status_duration_monthly(months, team_names_list, conn)

    detail_data = None
    detail_status = filters.get("detail_status")
    if detail_status:
        detail_year_month = filters.get("detail_year_month") or filters.get("year_month")
        detail_months = filters.get("detail_months") or months
        detail_data = _fetch_issue_status_duration_detail(
            str(detail_status),
            _parse_int(detail_months, default=months),
            detail_year_month,
            issue_type,
            team_names_list,
            conn,
        )

    return {
        "data": {
            "summary": summary_data,
            "monthly": monthly_data,
            "detail": detail_data,
            "view_mode": view_mode,
        },
        "meta": {
            "issue_type": issue_type,
            "team_name": team_name,
            "months": months,
            "available_teams": available_teams,
            "available_issue_types": available_issue_types,
        },
    }


def _fetch_epics_hierarchy(filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    pi_names = _parse_list(filters.get("pi") or filters.get("pi_names") or filters.get("pi_name"))
    team_name = (filters.get("team_name") or "").strip() or None
    limit_value = filters.get("limit")
    limit_int = _parse_int(limit_value, default=DEFAULT_HIERARCHY_LIMIT)
    if limit_int <= 0 or limit_int > 1000:
        limit_int = DEFAULT_HIERARCHY_LIMIT

    # Fetch available teams (always)
    teams_query = text(
        f"""
        SELECT DISTINCT "Team Name of Epic"
        FROM epic_hierarchy_with_progress
        WHERE "Team Name of Epic" IS NOT NULL
        ORDER BY "Team Name of Epic"
        """
    )
    teams_rows = conn.execute(teams_query).fetchall()
    available_teams = [row[0] for row in teams_rows if row[0]]

    # Fetch available PIs (always)
    pis_query = text(
        f"""
        SELECT DISTINCT "Quarter PI of Epic"
        FROM epic_hierarchy_with_progress
        WHERE "Quarter PI of Epic" IS NOT NULL
        ORDER BY "Quarter PI of Epic" DESC
        """
    )
    pis_rows = conn.execute(pis_query).fetchall()
    available_pis = [row[0] for row in pis_rows if row[0]]

    where_conditions = []
    params: Dict[str, Any] = {"limit": limit_int}

    if pi_names:
        placeholders = ", ".join([f":pi_{i}" for i in range(len(pi_names))])
        where_conditions.append(f'"Quarter PI of Epic" IN ({placeholders})')
        for i, pi in enumerate(pi_names):
            params[f"pi_{i}"] = pi

    if team_name:
        where_conditions.append('"Team Name of Epic" = :team_name')
        params["team_name"] = team_name

    where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"

    query = text(
        f"""
        SELECT *
        FROM epic_hierarchy_with_progress
        WHERE {where_clause}
        LIMIT :limit
        """
    )

    rows = conn.execute(query, params).fetchall()
    issues = [dict(row._mapping) for row in rows]

    return {
        "data": {
            "issues": issues,
            "count": len(issues),
            "limit": limit_int,
        },
        "meta": {
            "pi_names": pi_names,
            "team_name": team_name,
            "available_teams": available_teams,
            "available_pis": available_pis,
        },
    }


def _fetch_issue_hierarchy(filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    from database_team_metrics import resolve_team_names_from_filter
    
    pi_names = _parse_list(filters.get("pi") or filters.get("pi_names") or filters.get("pi_name"))
    team_name = (filters.get("team_name") or "").strip() or None
    is_group = filters.get("isGroup", False)
    hierarchy_level = filters.get("hierarchy_level")
    limit_value = filters.get("limit")
    limit_int = _parse_int(limit_value, default=DEFAULT_HIERARCHY_LIMIT)
    if limit_int <= 0 or limit_int > 1000:
        limit_int = DEFAULT_HIERARCHY_LIMIT
    
    # Validate hierarchy_level if provided
    if hierarchy_level is not None:
        try:
            hierarchy_level = int(hierarchy_level)
            if hierarchy_level < 0:
                raise ValueError("Hierarchy level must be non-negative")
        except (ValueError, TypeError):
            raise ValueError("Hierarchy level must be a non-negative integer")
    
    # Resolve team names if team_name provided (handles isGroup)
    team_names_list = resolve_team_names_from_filter(team_name, is_group, conn) if team_name else None
    
    # Fetch available teams (always)
    teams_query = text(
        f"""
        SELECT DISTINCT "Team Name of Epic"
        FROM get_issue_hierarchy_advanced
        WHERE "Team Name of Epic" IS NOT NULL
        ORDER BY "Team Name of Epic"
        """
    )
    teams_rows = conn.execute(teams_query).fetchall()
    available_teams = [row[0] for row in teams_rows if row[0]]
    
    # Fetch available PIs (always)
    pis_query = text(
        f"""
        SELECT DISTINCT "Quarter PI of Epic"
        FROM get_issue_hierarchy_advanced
        WHERE "Quarter PI of Epic" IS NOT NULL
        ORDER BY "Quarter PI of Epic" DESC
        """
    )
    pis_rows = conn.execute(pis_query).fetchall()
    available_pis = [row[0] for row in pis_rows if row[0]]
    
    where_conditions = []
    params: Dict[str, Any] = {"limit": limit_int}
    
    # Hierarchy level filter (less than or equal to)
    if hierarchy_level is not None:
        where_conditions.append('"Hierarchy Level" <= :hierarchy_level')
        params["hierarchy_level"] = hierarchy_level
    
    # Team name filter (single team or group) - using "Team Name of Epic" column
    if team_names_list:
        if len(team_names_list) == 1:
            # Single team
            where_conditions.append('"Team Name of Epic" = :team_name')
            params["team_name"] = team_names_list[0]
        else:
            # Multiple teams (from group)
            placeholders = ", ".join([f":team_{i}" for i in range(len(team_names_list))])
            where_conditions.append(f'"Team Name of Epic" IN ({placeholders})')
            for i, team in enumerate(team_names_list):
                params[f"team_{i}"] = team
    
    # PI filter (multiple PIs) - using "Quarter PI of Epic" column
    if pi_names:
        placeholders = ", ".join([f":pi_{i}" for i in range(len(pi_names))])
        where_conditions.append(f'"Quarter PI of Epic" IN ({placeholders})')
        for i, pi in enumerate(pi_names):
            params[f"pi_{i}"] = pi
    
    where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
    
    query = text(
        f"""
        SELECT *
        FROM get_issue_hierarchy_advanced
        WHERE {where_clause}
        LIMIT :limit
        """
    )
    
    rows = conn.execute(query, params).fetchall()
    issues = [dict(row._mapping) for row in rows]
    
    # Build meta with appropriate fields (matching pattern from other reports)
    meta = {
        "hierarchy_level": hierarchy_level,
        "pi_names": pi_names,
        "isGroup": is_group,
        "available_teams": available_teams,
        "available_pis": available_pis,
    }
    
    # Add team/group information to meta (standardize on team_name - matching pattern from _fetch_pi_burndown and _fetch_pi_predictability)
    if team_name:
        if is_group:
            meta["group_name"] = team_name
            meta["teams_in_group"] = team_names_list
        else:
            meta["team_name"] = team_name
            meta["team"] = team_name  # Keep for backward compatibility
    else:
        meta["team_name"] = None
        meta["team"] = None
    
    return {
        "data": {
            "issues": issues,
            "count": len(issues),
            "limit": limit_int,
        },
        "meta": meta,
    }


def _fetch_epic_dependencies(filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    from database_team_metrics import resolve_team_names_from_filter

    pi_names = _parse_list(filters.get("pi") or filters.get("pi_names") or filters.get("pi_name"))

    # Extract team filter parameters - only use team_name (removed legacy team parameter)
    team_name = (filters.get("team_name") or "").strip() or None
    is_group = filters.get("isGroup", False)

    # Resolve team names using shared helper function
    team_names_list = resolve_team_names_from_filter(team_name, is_group, conn)

    # Fetch available PIs from both dependency tables (always)
    pis_query = text(
        f"""
        SELECT DISTINCT quarter_pi_of_epic
        FROM (
            SELECT quarter_pi_of_epic FROM public.epic_inbound_dependency_load_by_quarter
            UNION
            SELECT quarter_pi_of_epic FROM public.epic_outbound_dependency_metrics_by_quarter
        ) AS all_pis
        WHERE quarter_pi_of_epic IS NOT NULL
        ORDER BY quarter_pi_of_epic DESC
        """
    )
    pis_rows = conn.execute(pis_query).fetchall()
    available_pis = [row[0] for row in pis_rows if row[0]]

    # Fetch data using shared functions
    # If multiple PIs, fetch for each and combine
    inbound_data = []
    outbound_data = []

    if pi_names:
        # Fetch for each PI and combine results
        for pi_name in pi_names:
            inbound_records = fetch_epic_inbound_dependency_data(pi_name, team_names_list, conn)
            outbound_records = fetch_epic_outbound_dependency_data(pi_name, team_names_list, conn)
            inbound_data.extend(inbound_records)
            outbound_data.extend(outbound_records)
    else:
        # No PI filter - fetch all data
        inbound_data = fetch_epic_inbound_dependency_data(None, team_names_list, conn)
        outbound_data = fetch_epic_outbound_dependency_data(None, team_names_list, conn)

    # Build meta with team/group information
    meta = {
        "pi_names": pi_names,
        "inbound_count": len(inbound_data),
        "outbound_count": len(outbound_data),
        "available_pis": available_pis,
    }

    # Add team/group information to meta
    if team_name:
        if is_group:
            meta["group_name"] = team_name
            meta["teams_in_group"] = team_names_list
        else:
            meta["team_name"] = team_name

    return {
        "data": {
            "inbound": inbound_data,
            "outbound": outbound_data,
        },
        "meta": meta,
    }


def _fetch_release_predictability(filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    months = _parse_int(filters.get("months"), default=3)
    if months <= 0:
        months = 3

    start_date = datetime.now().date() - timedelta(days=months * 30)

    query = text(
        """
        SELECT 
            version_name, 
            project_key, 
            release_start_date, 
            release_date, 
            total_epics_in_scope, 
            epics_completed, 
            epic_percent_completed, 
            total_other_issues_in_scope, 
            other_issues_completed, 
            other_issues_percent_completed 
        FROM public.release_predictability_analysis 
        WHERE release_start_date >= :start_date
        ORDER BY release_start_date DESC
        """
    )

    rows = conn.execute(query, {"start_date": start_date.strftime("%Y-%m-%d")}).fetchall()
    data: List[Dict[str, Any]] = []
    for row in rows:
        row_dict = dict(row._mapping)
        for key, value in list(row_dict.items()):
            if value is not None and hasattr(value, "strftime"):
                row_dict[key] = value.strftime("%Y-%m-%d")
        data.append(row_dict)

    return {
        "data": data,
        "meta": {
            "months": months,
            "count": len(data),
        },
    }


def _fetch_sprint_predictability(filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    from database_team_metrics import resolve_team_names_from_filter
    
    months = _parse_int(filters.get("months"), default=3)
    if months not in (1, 2, 3, 4, 6, 9):
        months = 3

    team_name = filters.get("team_name") or filters.get("team")
    is_group = filters.get("isGroup", False)

    # Resolve team names using shared helper function
    team_names_list = resolve_team_names_from_filter(team_name, is_group, conn)

    # Build query and parameters
    if team_names_list:
        # Pass array of team names to function
        params = {"months": months, "team_names": team_names_list}
        query = text(
            """
            SELECT *
            FROM public.get_sprint_predictability_metrics_with_issues(:months, CAST(:team_names AS text[]))
            """
        )
    else:
        # Pass NULL for all teams
        params = {"months": months}
        query = text(
            """
            SELECT *
            FROM public.get_sprint_predictability_metrics_with_issues(:months, NULL)
            """
        )

    rows = conn.execute(query, params).fetchall()
    data: List[Dict[str, Any]] = []
    for row in rows:
        row_dict = dict(row._mapping)
        
        # Format all date fields if they exist
        for key, value in row_dict.items():
            if value is not None and hasattr(value, 'strftime'):
                row_dict[key] = value.strftime('%Y-%m-%d')
        
        # Process issue key arrays (split comma-separated strings)
        for key in (
            "completed_issue_keys",
            "total_committed_issue_keys",
            "issues_not_completed_keys",
        ):
            value = row_dict.get(key)
            if isinstance(value, list):
                row_dict[key] = value
            elif isinstance(value, str):
                row_dict[key] = [item.strip() for item in value.split(",") if item.strip()]
        data.append(row_dict)

    meta: Dict[str, Any] = {
        "months": months,
        "count": len(data),
        "isGroup": is_group,
    }
    if team_name:
        if is_group:
            meta["group_name"] = team_name
            meta["teams_in_group"] = team_names_list
        else:
            meta["team_name"] = team_name

    return {
        "data": data,
        "meta": meta,
    }


def _fetch_pi_metrics_summary(filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    from database_team_metrics import resolve_team_names_from_filter
    
    pi_name = (filters.get("pi") or filters.get("pi_name") or "").strip() or None
    project = (filters.get("project") or "").strip() or None
    issue_type = (filters.get("issue_type") or "Epic").strip() or "Epic"
    team = (filters.get("team_name") or filters.get("team") or "").strip() or None
    is_group = filters.get("isGroup", False)
    plan_grace_period = _parse_int(filters.get("plan_grace_period"), default=5)

    # Fetch available teams from cache
    from groups_teams_cache import get_cached_teams, set_cached_teams, load_team_names_from_db, load_all_teams_from_db
    
    cached = get_cached_teams()
    if cached:
        available_teams = [t["team_name"] for t in cached.get("teams", [])]
    else:
        # Cache miss - load from DB
        available_teams = load_team_names_from_db(conn)
        # Also build full teams cache for future use
        all_teams = load_all_teams_from_db(conn)
        set_cached_teams({"teams": all_teams, "count": len(all_teams)})

    # Fetch available issue types (always)
    issue_types_query = text(
        f"""
        SELECT issue_type
        FROM {config.ISSUE_TYPES_TABLE}
        ORDER BY issue_type
        """
    )
    issue_types_rows = conn.execute(issue_types_query).fetchall()
    available_issue_types = [row[0] for row in issue_types_rows if row[0]]

    # Fetch available PIs (always)
    pis_query = text(
        f"""
        SELECT DISTINCT pi_name
        FROM {config.PIS_TABLE}
        WHERE pi_name IS NOT NULL
        ORDER BY pi_name DESC
        """
    )
    pis_rows = conn.execute(pis_query).fetchall()
    available_pis = [row[0] for row in pis_rows if row[0]]

    # Resolve team names using shared helper function (handles single team, group, or None)
    team_names_list = resolve_team_names_from_filter(team, is_group, conn)

    summary_data = fetch_pi_summary_data(
        target_pi_name=pi_name,
        target_project_keys=project,
        target_issue_type=issue_type,
        target_team_names=team_names_list,
        planned_grace_period_days=plan_grace_period,
        conn=conn,
    )

    wip_where = [
        "issue_type = 'Epic'",
    ]
    params: Dict[str, Any] = {}

    if pi_name:
        wip_where.append("quarter_pi = :pi")
        params["pi"] = pi_name

    if team_names_list:
        # Handle multiple teams using ANY() for array matching
        wip_where.append("team_name = ANY(:team_names)")
        params["team_names"] = team_names_list
    elif team:
        # Fallback for single team (shouldn't happen if resolve_team_names_from_filter works correctly)
        wip_where.append("team_name = :team_name")
        params["team_name"] = team

    if project:
        wip_where.append("project_key = :project")
        params["project"] = project

    where_clause = " AND ".join(wip_where) if wip_where else "1=1"

    wip_query = text(
        f"""
        SELECT 
            COUNT(*) AS total_epics,
            COUNT(CASE WHEN status_category = 'In Progress' THEN 1 END) AS in_progress_epics
        FROM public.jira_issues
        WHERE {where_clause}
        """
    )

    row = conn.execute(wip_query, params).fetchone()
    total_epics = int(row[0]) if row and row[0] is not None else 0
    in_progress_epics = int(row[1]) if row and row[1] is not None else 0
    in_progress_percentage = (in_progress_epics / total_epics * 100) if total_epics > 0 else 0.0

    def _wip_status(count_in_progress: int, total: int) -> str:
        if total <= 0:
            return "gray"
        percentage = (count_in_progress / total) * 100
        if percentage < 30:
            return "green"
        if percentage <= 50:
            return "yellow"
        return "red"

    wip_data = {
        "count_in_progress": in_progress_epics,
        "count_in_progress_status": _wip_status(in_progress_epics, total_epics),
        "total_epics": total_epics,
        "in_progress_percentage": round(in_progress_percentage, 2),
        "pi": pi_name,
        "team_name": team,
        "project": project,
    }

    # Build meta with appropriate fields
    meta = {
        "pi": pi_name,
        "project": project,
        "issue_type": issue_type,
        "isGroup": is_group,
        "available_teams": available_teams,
        "available_issue_types": available_issue_types,
        "available_pis": available_pis,
    }
    
    # Add team/group information to meta
    if team:
        if is_group:
            meta["group_name"] = team
            meta["teams_in_group"] = team_names_list
        else:
            meta["team_name"] = team
    else:
        meta["team_name"] = None

    return {
        "data": {
            "status_today": summary_data,
            "wip": wip_data,
        },
        "meta": meta,
    }


def _fetch_pi_metrics_summary_by_team(filters: Dict[str, Any], conn: Connection) -> ReportDataResult:
    from database_team_metrics import resolve_team_names_from_filter
    
    pi_name = (filters.get("pi") or filters.get("pi_name") or "").strip() or None
    project = (filters.get("project") or "").strip() or None
    issue_type = (filters.get("issue_type") or "Epic").strip() or "Epic"
    team = (filters.get("team_name") or filters.get("team") or "").strip() or None
    is_group = filters.get("isGroup", False)
    plan_grace_period = _parse_int(filters.get("plan_grace_period"), default=5)

    # Fetch available teams from cache
    from groups_teams_cache import get_cached_teams, set_cached_teams, load_team_names_from_db, load_all_teams_from_db
    
    cached = get_cached_teams()
    if cached:
        available_teams = [t["team_name"] for t in cached.get("teams", [])]
    else:
        # Cache miss - load from DB
        available_teams = load_team_names_from_db(conn)
        # Also build full teams cache for future use
        all_teams = load_all_teams_from_db(conn)
        set_cached_teams({"teams": all_teams, "count": len(all_teams)})

    # Fetch available issue types (always)
    issue_types_query = text(
        f"""
        SELECT issue_type
        FROM {config.ISSUE_TYPES_TABLE}
        ORDER BY issue_type
        """
    )
    issue_types_rows = conn.execute(issue_types_query).fetchall()
    available_issue_types = [row[0] for row in issue_types_rows if row[0]]

    # Fetch available PIs (always)
    pis_query = text(
        f"""
        SELECT DISTINCT pi_name
        FROM {config.PIS_TABLE}
        WHERE pi_name IS NOT NULL
        ORDER BY pi_name DESC
        """
    )
    pis_rows = conn.execute(pis_query).fetchall()
    available_pis = [row[0] for row in pis_rows if row[0]]

    # Resolve team names using shared helper function (handles single team, group, or None)
    team_names_list = resolve_team_names_from_filter(team, is_group, conn)

    summary_data = fetch_pi_summary_data_by_team(
        target_pi_name=pi_name,
        target_project_keys=project,
        target_issue_type=issue_type,
        target_team_names=team_names_list,
        planned_grace_period_days=plan_grace_period,
        conn=conn,
    )

    # Fetch per-team WIP data
    wip_data_by_team = []
    if pi_name:
        wip_where = [
            "issue_type = 'Epic'",
            "quarter_pi = :pi"
        ]
        wip_params: Dict[str, Any] = {"pi": pi_name}

        if team_names_list:
            wip_where.append("team_name = ANY(:team_names)")
            wip_params["team_names"] = team_names_list
        
        if project:
            wip_where.append("project_key = :project")
            wip_params["project"] = project

        where_clause = " AND ".join(wip_where)

        wip_query = text(
            f"""
            SELECT 
                team_name,
                COUNT(*) AS total_epics,
                COUNT(CASE WHEN status_category = 'In Progress' THEN 1 END) AS in_progress_epics
            FROM public.jira_issues
            WHERE {where_clause}
            GROUP BY team_name
            ORDER BY team_name
            """
        )

        wip_result = conn.execute(wip_query, wip_params)
        
        def _wip_status(count_in_progress: int, total: int) -> str:
            if total <= 0:
                return "gray"
            percentage = (count_in_progress / total) * 100
            if percentage < 30:
                return "green"
            if percentage <= 50:
                return "yellow"
            return "red"
        
        for row in wip_result:
            team_name = row[0]
            total_epics = int(row[1]) if row[1] else 0
            in_progress_epics = int(row[2]) if row[2] else 0
            in_progress_percentage = (in_progress_epics / total_epics * 100) if total_epics > 0 else 0.0
            
            wip_data_by_team.append({
                "team_name": team_name,
                "count_in_progress": in_progress_epics,
                "count_in_progress_status": _wip_status(in_progress_epics, total_epics),
                "total_epics": total_epics,
                "in_progress_percentage": round(in_progress_percentage, 2),
            })

    # Build meta with appropriate fields
    meta = {
        "pi": pi_name,
        "project": project,
        "issue_type": issue_type,
        "isGroup": is_group,
        "available_teams": available_teams,
        "available_issue_types": available_issue_types,
        "available_pis": available_pis,
    }
    
    # Add team/group information to meta
    if team:
        if is_group:
            meta["group_name"] = team
            meta["teams_in_group"] = team_names_list
        else:
            meta["team_name"] = team
    else:
        meta["team_name"] = None

    return {
        "data": {
            "status_today_by_team": summary_data,
            "wip_by_team": wip_data_by_team,
        },
        "meta": meta,
    }


_REPORT_DATA_FETCHERS: Dict[str, ReportDataFetcher] = {
    "team_sprint_burndown": _fetch_team_sprint_burndown,
    "team_current_sprint_progress": _fetch_team_current_sprint_progress,
    "pi_burndown": _fetch_pi_burndown,
    "team_closed_sprints": _fetch_team_closed_sprints,
    "team_sprint_velocity_advanced": _fetch_team_sprint_velocity_advanced,
    "team_issues_trend": _fetch_team_issues_trend,
    "pi_predictability": _fetch_pi_predictability,
    "epic_scope_changes": _fetch_epic_scope_changes,
    "issues_bugs_by_priority": _fetch_issues_bugs_by_priority,
    "issues_bugs_by_team": _fetch_issues_bugs_by_team,
    "issues_flow_status_duration": _fetch_issues_flow_status_duration,
    "issues_epics_hierarchy": _fetch_epics_hierarchy,
    "issues_hierarchy": _fetch_issue_hierarchy,
    "issues_epic_dependencies": _fetch_epic_dependencies,
    "issues_release_predictability": _fetch_release_predictability,
    "sprint_predictability": _fetch_sprint_predictability,
    "pi_metrics_summary": _fetch_pi_metrics_summary,
    "pi_metrics_summary_by_team": _fetch_pi_metrics_summary_by_team,
}

