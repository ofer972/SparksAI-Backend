"""
Database Releases - Database access functions for release-related operations.

This module contains database access functions for release operations.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional, Tuple
import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)


def _calculate_ideal_remaining_and_progress_delta(
    total_count: int,
    completed_count: int,
    release_start_date: Optional[date],
    release_end_date: Optional[date]
) -> Tuple[float, float]:
    """
    Calculate ideal remaining and progress delta percentage.
    
    Args:
        total_count: Total number of issues
        completed_count: Number of completed issues
        release_start_date: Release start date
        release_end_date: Release end date
    
    Returns:
        Tuple of (ideal_remaining, progress_delta_pct)
    """
    remaining = total_count - completed_count
    total_scope = total_count
    
    # If no dates, can't calculate ideal
    if not release_start_date or not release_end_date:
        return float(remaining), 0.0
    
    current_date = datetime.now().date()
    
    # Calculate ideal remaining using linear interpolation
    if current_date > release_end_date:
        ideal_remaining = 0.0
    elif current_date < release_start_date:
        ideal_remaining = float(total_count)
    else:
        total_days = (release_end_date - release_start_date).days
        if total_days <= 0:
            ideal_remaining = float(total_count)
        else:
            days_elapsed = (current_date - release_start_date).days
            ideal_remaining = max(0.0, total_count - ((total_count / total_days) * days_elapsed))
    
    # Calculate progress delta percentage
    if total_scope > 0:
        progress_delta_pct = round(((ideal_remaining - remaining) / total_scope) * 100, 2)
    else:
        progress_delta_pct = 0.0
    
    return ideal_remaining, progress_delta_pct


def reduce_release_burndown_data(burndown_data: List[Dict[str, Any]], days_without_change_threshold: int = 5) -> List[Dict[str, Any]]:
    """
    Reduce release burndown data using Enhanced Option 5:
    - Keep first and last day
    - Keep days with changes (issues_completed, issues_removed, total_scope, actual_remaining)
    - If no change for N days (default 5), add a data point to mark the period
    
    Args:
        burndown_data: List of burndown data dictionaries
        days_without_change_threshold: Number of days without change before adding a marker point (default: 5)
    
    Returns:
        Reduced list of burndown data dictionaries
    """
    if not burndown_data or len(burndown_data) <= 1:
        return burndown_data
    
    # Fields to track for changes
    possible_change_fields = [
        'issues_completed', 'issues_completed_on_day', 'completed_issues',
        'issues_removed', 'issues_removed_on_day', 'removed_issues',
        'total_scope', 'total_issues', 'scope',
        'actual_remaining', 'remaining_issues', 'remaining'
    ]
    
    # Find date field
    possible_date_fields = ['date', 'snapshot_date', 'day', 'snapshot_day', 'burndown_date']
    
    first_row = burndown_data[0]
    available_fields = [field for field in possible_change_fields if field in first_row]
    date_field = None
    for field in possible_date_fields:
        if field in first_row:
            date_field = field
            break
    
    if not date_field:
        return burndown_data
    
    reduced = [burndown_data[0]]
    last_change_index = 0
    
    for i in range(1, len(burndown_data) - 1):
        current = burndown_data[i]
        prev = burndown_data[last_change_index]
        
        has_change = False
        for field in available_fields:
            if current.get(field) != prev.get(field):
                has_change = True
                break
        
        days_since_change = i - last_change_index
        
        if has_change:
            reduced.append(current)
            last_change_index = i
        elif days_since_change >= days_without_change_threshold:
            reduced.append(current)
            last_change_index = i
    
    if len(burndown_data) > 1:
        reduced.append(burndown_data[-1])
    
    return reduced


def fetch_releases(project_key: Optional[str] = None, conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Fetch releases from jira_releases table.
    
    Args:
        project_key: Optional project key filter
        conn: Database connection
    
    Returns:
        List of release dictionaries
    """
    try:
        if not conn:
            return []
        
        query = text("""
            SELECT 
                release_id,
                project_key,
                project_id,
                name,
                description,
                archived,
                released,
                start_date,
                release_date,
                overdue,
                self_url,
                synced_at
            FROM jira_releases
            WHERE (:project_key IS NULL OR project_key = :project_key)
            ORDER BY release_date DESC NULLS LAST, name DESC
        """)
        
        params = {"project_key": project_key}
        result = conn.execute(query, params)
        
        releases = []
        for row in result:
            row_dict = dict(row._mapping)
            for key, value in list(row_dict.items()):
                if value is not None and hasattr(value, 'strftime'):
                    row_dict[key] = value.strftime('%Y-%m-%d')
            releases.append(row_dict)
        
        logger.info(f"Retrieved {len(releases)} releases")
        return releases
        
    except Exception as e:
        logger.error(f"Error fetching releases: {e}")
        raise e


def fetch_release_burndown_data(release_id: Optional[int] = None, release_name: Optional[str] = None, project_keys: str = None, issue_type: str = None, team_names: Optional[List[str]] = None, conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Fetch release burndown data from the database function.
    
    Args:
        release_id: Release ID to fetch data for (preferred)
        release_name: Release name to fetch data for (will lookup ID if release_id not provided)
        project_keys: Project keys filter (deprecated, kept for backward compatibility but not used)
        issue_type: Issue type filter (defaults to 'all' if not provided)
        team_names: List of team names filter, or None for all teams
        conn: Database connection
    
    Returns:
        List of dictionaries with release burndown data
    """
    try:
        # If release_id not provided, lookup by name
        if release_id is None:
            if not release_name:
                return []
            
            # Lookup release_id from name
            lookup_query = text("""
                SELECT release_id FROM jira_releases WHERE name = :release_name LIMIT 1
            """)
            result = conn.execute(lookup_query, {"release_name": release_name})
            row = result.fetchone()
            if not row:
                logger.warning(f"Release not found: {release_name}")
                return []
            release_id = row[0]
        
        if not issue_type or issue_type == "":
            issue_type = 'all'
        
        logger.info(f"Executing release burndown query for release_id: {release_id}")
        logger.info(f"Filters: issue_type={issue_type}, team_names={team_names}")
        
        params = {
            'release_id': release_id,
            'issue_type': issue_type
        }
        
        if team_names:
            params['team_names'] = team_names
            sql_query_text = text("""
                SELECT * FROM public.get_release_burndown_data(
                    :release_id,
                    :issue_type,
                    CAST(:team_names AS text[])
                )
            """)
            logger.info(f"Executing SQL for release burndown: release_id={release_id} with teams: {team_names}")
        else:
            sql_query_text = text("""
                SELECT * FROM public.get_release_burndown_data(
                    :release_id,
                    :issue_type,
                    NULL
                )
            """)
            logger.info(f"Executing SQL for release burndown: {release_name} for all teams")
        
        result = conn.execute(sql_query_text, params)
        
        burndown_data = []
        for row in result:
            row_dict = dict(row._mapping)
            for col in row_dict.keys():
                if isinstance(row_dict[col], list):
                    row_dict[col] = ', '.join(row_dict[col])
            burndown_data.append(row_dict)
        
        logger.info(f"Retrieved {len(burndown_data)} release burndown records")
        
        burndown_data = reduce_release_burndown_data(burndown_data, days_without_change_threshold=5)
        
        return burndown_data
            
    except Exception as e:
        logger.error(f"Error fetching release burndown data: {e}")
        raise e


def fetch_release_metrics(
    release_name: Optional[str] = None,
    release_id: Optional[int] = None,
    months: int = 6,
    issue_type: Optional[str] = None,
    conn: Connection = None
) -> List[Dict[str, Any]]:
    """
    Fetch release metrics including epic and standard issue type counts and completion percentages.
    
    Args:
        release_name: Optional release name filter
        release_id: Optional release ID filter
        months: Number of months to look back from current date (default: 6)
        issue_type: Optional issue type filter
        conn: Database connection
    
    Returns:
        List of dictionaries with release metrics
    """
    try:
        if not conn:
            return []
        
        from datetime import datetime, timedelta
        from config import ISSUE_TYPES_TABLE
        
        # Calculate start date based on months
        start_date = datetime.now().date() - timedelta(days=months * 30)
        
        # Build WHERE conditions for releases
        release_where_conditions = ["start_date IS NOT NULL"]
        params = {}
        
        if months:
            release_where_conditions.append("start_date >= :start_date")
            params["start_date"] = start_date.strftime("%Y-%m-%d")
        
        if release_id is not None:
            release_where_conditions.append("release_id = :release_id")
            params["release_id"] = release_id
        elif release_name:
            release_where_conditions.append("name = :release_name")
            params["release_name"] = release_name
        
        release_where_clause = " AND ".join(release_where_conditions)
        
        # Always add issue_type to params (even if None) for breakdown query
        params["issue_type"] = issue_type
        
        # Build issue type filter condition for CASE statements
        issue_type_condition = ""
        if issue_type:
            issue_type_condition = " AND i.issue_type = :issue_type"
        
        # Summary query - simplified GROUP BY to just release_id
        summary_query = text(f"""
            WITH filtered_releases AS (
                SELECT release_id, name, start_date, release_date
                FROM jira_releases
                WHERE {release_where_clause}
            ),
            standard_issue_types AS (
                SELECT issue_type
                FROM {ISSUE_TYPES_TABLE}
                WHERE "hierarchyLevel" = 0
            )
            SELECT 
                r.release_id,
                r.name AS release_name,
                r.start_date AS release_start_date,
                r.release_date AS release_end_date,
                COUNT(DISTINCT CASE WHEN i.issue_type = 'Epic'{issue_type_condition} THEN i.issue_id END) AS number_of_epics,
                COUNT(DISTINCT CASE WHEN i.issue_type = 'Epic' AND i.status_category = 'Done'{issue_type_condition} THEN i.issue_id END) AS epics_completed,
                COUNT(DISTINCT CASE WHEN i.issue_type = 'Epic' AND i.status_category = 'In Progress'{issue_type_condition} THEN i.issue_id END) AS wip_epics,
                COUNT(DISTINCT CASE WHEN i.issue_type IN (SELECT issue_type FROM standard_issue_types){issue_type_condition} THEN i.issue_id END) AS number_of_standard_issue_types,
                COUNT(DISTINCT CASE WHEN i.issue_type IN (SELECT issue_type FROM standard_issue_types) AND i.status_category = 'Done'{issue_type_condition} THEN i.issue_id END) AS standard_issue_types_completed,
                COUNT(DISTINCT CASE WHEN i.issue_type IN (SELECT issue_type FROM standard_issue_types) AND i.status_category = 'In Progress'{issue_type_condition} THEN i.issue_id END) AS wip_standard_issue_types
            FROM filtered_releases r
            LEFT JOIN jira_issues i ON r.release_id = ANY(i.fix_version_ids)
            GROUP BY r.release_id, r.name, r.start_date, r.release_date
            ORDER BY r.start_date DESC
        """)
        
        # Breakdown query - groups by release_id and issue_type (only standard issue types, not Epic)
        breakdown_query = text(f"""
            WITH filtered_releases AS (
                SELECT release_id, name, start_date, release_date
                FROM jira_releases
                WHERE {release_where_clause}
            ),
            standard_issue_types AS (
                SELECT issue_type
                FROM {ISSUE_TYPES_TABLE}
                WHERE "hierarchyLevel" = 0
            )
            SELECT 
                r.release_id,
                i.issue_type,
                COUNT(DISTINCT i.issue_id) AS number_of_issues,
                COUNT(DISTINCT CASE WHEN i.status_category = 'Done' THEN i.issue_id END) AS number_of_issues_completed,
                COUNT(DISTINCT CASE WHEN i.status_category = 'In Progress' THEN i.issue_id END) AS wip_issues
            FROM filtered_releases r
            LEFT JOIN jira_issues i ON r.release_id = ANY(i.fix_version_ids)
            WHERE i.issue_id IS NOT NULL
              AND i.issue_type IN (SELECT issue_type FROM standard_issue_types)
              AND (:issue_type IS NULL OR i.issue_type = :issue_type)
            GROUP BY r.release_id, i.issue_type
            ORDER BY r.release_id, i.issue_type
        """)
        
        logger.info(f"Executing release metrics queries: release_name={release_name}, release_id={release_id}, months={months}, issue_type={issue_type}")
        
        # Execute summary query
        summary_result = conn.execute(summary_query, params)
        summary_rows = summary_result.fetchall()
        
        # Execute breakdown query
        breakdown_result = conn.execute(breakdown_query, params)
        breakdown_rows = breakdown_result.fetchall()
        
        # Build breakdown dictionary: {release_id: [issue_type_breakdowns]}
        # Also store release dates for breakdown calculations
        breakdown_dict = {}
        release_dates_dict = {}  # {release_id: (start_date, end_date)}
        for row in breakdown_rows:
            row_dict = dict(row._mapping)
            release_id = row_dict.get('release_id')
            if release_id not in breakdown_dict:
                breakdown_dict[release_id] = []
            
            issue_type_name = row_dict.get('issue_type')
            number_of_issues = row_dict.get('number_of_issues') or 0
            number_of_issues_completed = row_dict.get('number_of_issues_completed') or 0
            wip_issues = row_dict.get('wip_issues') or 0
            
            # Calculate percentage
            percentage_completed_issues = 0.0
            if number_of_issues > 0:
                percentage_completed_issues = round((number_of_issues_completed / number_of_issues) * 100.0, 2)
            
            # Calculate WIP percentage
            wip_percentage = 0.0
            if number_of_issues > 0:
                wip_percentage = round((wip_issues / number_of_issues) * 100.0, 2)
            
            breakdown_dict[release_id].append({
                "issue_type": issue_type_name,
                "number_of_issues": number_of_issues,
                "number_of_issues_completed": number_of_issues_completed,
                "percentage_completed_issues": percentage_completed_issues,
                "wip_issues": wip_issues,
                "wip_percentage": wip_percentage
            })
        
        # Build metrics list
        metrics = []
        for row in summary_rows:
            row_dict = dict(row._mapping)
            
            # Get and format dates
            release_start_date = row_dict.get('release_start_date')
            release_end_date = row_dict.get('release_end_date')
            
            # Convert date objects to strings if needed
            if release_start_date and hasattr(release_start_date, 'strftime'):
                release_start_date_str = release_start_date.strftime('%Y-%m-%d')
                release_start_date_obj = release_start_date
            else:
                release_start_date_str = release_start_date
                release_start_date_obj = None
                if release_start_date_str:
                    try:
                        release_start_date_obj = datetime.strptime(release_start_date_str, '%Y-%m-%d').date()
                    except:
                        pass
            
            if release_end_date and hasattr(release_end_date, 'strftime'):
                release_end_date_str = release_end_date.strftime('%Y-%m-%d')
                release_end_date_obj = release_end_date
            else:
                release_end_date_str = release_end_date
                release_end_date_obj = None
                if release_end_date_str:
                    try:
                        release_end_date_obj = datetime.strptime(release_end_date_str, '%Y-%m-%d').date()
                    except:
                        pass
            
            # Get counts
            number_of_epics = row_dict.get('number_of_epics') or 0
            epics_completed = row_dict.get('epics_completed') or 0
            wip_epics = row_dict.get('wip_epics') or 0
            number_of_standard_issue_types = row_dict.get('number_of_standard_issue_types') or 0
            standard_issue_types_completed = row_dict.get('standard_issue_types_completed') or 0
            wip_standard_issue_types = row_dict.get('wip_standard_issue_types') or 0
            
            # Calculate total counts
            total_issues = number_of_epics + number_of_standard_issue_types
            total_issues_completed = epics_completed + standard_issue_types_completed
            total_wip_issues = wip_epics + wip_standard_issue_types
            
            # Calculate percentages (handle division by zero)
            percentage_completed_epics = 0.0
            if number_of_epics > 0:
                percentage_completed_epics = round((epics_completed / number_of_epics) * 100.0, 2)
            
            percentage_completed_standard_issue_types = 0.0
            if number_of_standard_issue_types > 0:
                percentage_completed_standard_issue_types = round((standard_issue_types_completed / number_of_standard_issue_types) * 100.0, 2)
            
            percentage_completed_total = 0.0
            if total_issues > 0:
                percentage_completed_total = round((total_issues_completed / total_issues) * 100.0, 2)
            
            # Calculate WIP percentages
            wip_percentage_epics = 0.0
            if number_of_epics > 0:
                wip_percentage_epics = round((wip_epics / number_of_epics) * 100.0, 2)
            
            wip_percentage_standard = 0.0
            if number_of_standard_issue_types > 0:
                wip_percentage_standard = round((wip_standard_issue_types / number_of_standard_issue_types) * 100.0, 2)
            
            wip_percentage_total = 0.0
            if total_issues > 0:
                wip_percentage_total = round((total_wip_issues / total_issues) * 100.0, 2)
            
            # Calculate remaining, ideal_remaining, and progress_delta for epics
            remaining_epics = number_of_epics - epics_completed
            ideal_remaining_epics, progress_delta_pct_epics = _calculate_ideal_remaining_and_progress_delta(
                number_of_epics, epics_completed, release_start_date_obj, release_end_date_obj
            )
            
            # Calculate remaining, ideal_remaining, and progress_delta for standard issue types
            remaining_standard_issue_types = number_of_standard_issue_types - standard_issue_types_completed
            ideal_remaining_standard, progress_delta_pct_standard = _calculate_ideal_remaining_and_progress_delta(
                number_of_standard_issue_types, standard_issue_types_completed, release_start_date_obj, release_end_date_obj
            )
            
            # Calculate remaining, ideal_remaining, and progress_delta for total
            remaining_total = total_issues - total_issues_completed
            ideal_remaining_total, progress_delta_pct_total = _calculate_ideal_remaining_and_progress_delta(
                total_issues, total_issues_completed, release_start_date_obj, release_end_date_obj
            )
            
            # Get breakdown and enhance with remaining, ideal_remaining, progress_delta
            release_id = row_dict.get('release_id')
            issue_types_breakdown_raw = breakdown_dict.get(release_id, [])
            
            # Enhance breakdown with remaining, ideal_remaining, progress_delta
            issue_types_breakdown = []
            for breakdown_item in issue_types_breakdown_raw:
                issue_type_name = breakdown_item.get('issue_type')
                # Skip Epic in breakdown (only standard issue types)
                if issue_type_name == 'Epic':
                    continue
                
                number_of_issues = breakdown_item.get('number_of_issues', 0)
                number_of_issues_completed = breakdown_item.get('number_of_issues_completed', 0)
                wip_issues = breakdown_item.get('wip_issues', 0)
                wip_percentage = breakdown_item.get('wip_percentage', 0.0)
                
                # Calculate remaining, ideal_remaining, progress_delta for this issue type
                remaining_issues = number_of_issues - number_of_issues_completed
                ideal_remaining_issues, progress_delta_pct = _calculate_ideal_remaining_and_progress_delta(
                    number_of_issues, number_of_issues_completed, release_start_date_obj, release_end_date_obj
                )
                
                issue_types_breakdown.append({
                    "issue_type": issue_type_name,
                    "number_of_issues": number_of_issues,
                    "number_of_issues_completed": number_of_issues_completed,
                    "percentage_completed_issues": breakdown_item.get('percentage_completed_issues', 0.0),
                    "remaining_issues": remaining_issues,
                    "ideal_remaining_issues": round(ideal_remaining_issues, 2),
                    "progress_delta_pct": progress_delta_pct,
                    "wip_issues": wip_issues,
                    "wip_percentage": wip_percentage
                })
            
            # Build nested response structure
            metric_dict = {
                "release_id": release_id,
                "release_name": row_dict.get('release_name'),
                "release_start_date": release_start_date_str,
                "release_end_date": release_end_date_str,
                "epics": {
                    "number_of_epics": number_of_epics,
                    "epics_completed": epics_completed,
                    "percentage_completed_epics": percentage_completed_epics,
                    "remaining_epics": remaining_epics,
                    "ideal_remaining_epics": round(ideal_remaining_epics, 2),
                    "progress_delta_pct": progress_delta_pct_epics,
                    "wip_epics": wip_epics,
                    "wip_percentage": wip_percentage_epics
                },
                "standard_issue_types": {
                    "number_of_standard_issue_types": number_of_standard_issue_types,
                    "standard_issue_types_completed": standard_issue_types_completed,
                    "percentage_completed_standard_issue_types": percentage_completed_standard_issue_types,
                    "remaining_standard_issue_types": remaining_standard_issue_types,
                    "ideal_remaining_standard_issue_types": round(ideal_remaining_standard, 2),
                    "progress_delta_pct": progress_delta_pct_standard,
                    "wip_standard_issue_types": wip_standard_issue_types,
                    "wip_percentage": wip_percentage_standard
                },
                "total": {
                    "number_of_issues": total_issues,
                    "issues_completed": total_issues_completed,
                    "percentage_completed_issues": percentage_completed_total,
                    "remaining_issues": remaining_total,
                    "ideal_remaining_issues": round(ideal_remaining_total, 2),
                    "progress_delta_pct": progress_delta_pct_total,
                    "total_wip_issues": total_wip_issues,
                    "wip_percentage": wip_percentage_total
                },
                "issue_types_breakdown": issue_types_breakdown
            }
            
            metrics.append(metric_dict)
        
        logger.info(f"Retrieved {len(metrics)} release metrics records")
        return metrics
        
    except Exception as e:
        logger.error(f"Error fetching release metrics: {e}")
        raise e


def get_release_history_issues_db(
    release_name: str,
    target_date: date,
    team_names: Optional[List[str]],
    issue_type: Optional[str] = None,
    metric_type: str = "total_scope",
    conn: Connection = None
) -> List[Dict[str, Any]]:
    """
    Get release issue details for a specific date.
    
    Args:
        release_name (str): Release name to filter by
        target_date (date): The date to query (date only, no time)
        team_names (Optional[List[str]]): List of team names, or None for all teams
        issue_type (Optional[str]): Issue type filter (e.g., "Story", "Bug", "Epic"). If None, passes "all" to function.
        metric_type (str): Metric type to filter by. Valid values:
            - "issues_completed" - Issues completed on this day
            - "issues_removed" - Issues removed from release
            - "total_scope" - Total scope of release on this day (all issues in release)
            - "wip_in_progress" - Work in progress items
            - "actual_remaining" - Actual remaining items (not done)
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of issue dictionaries with issue_key, summary, team_name, metric_category
    """
    try:
        if not conn:
            return []
        
        # First, lookup release_id from release_name
        lookup_query = text("""
            SELECT release_id FROM jira_releases WHERE name = :release_name LIMIT 1
        """)
        lookup_result = conn.execute(lookup_query, {"release_name": release_name})
        lookup_row = lookup_result.fetchone()
        if not lookup_row:
            logger.warning(f"Release not found: {release_name}")
            return []
        release_id = lookup_row[0]
        
        # Handle issue_type parameter (pass "all" if not provided)
        target_issuetype = issue_type if issue_type else "all"
        
        # Build base parameters
        params = {
            "release_id": release_id,
            "target_date": target_date.strftime("%Y-%m-%d"),
            "target_issuetype": target_issuetype
        }
        
        # Build team filter if team_names provided
        team_filter = ""
        if team_names:
            team_placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names))])
            team_filter = f"AND jh.team_name IN ({team_placeholders})"
            for i, name in enumerate(team_names):
                params[f"team_name_{i}"] = name
        
        # Map metric_type to query conditions
        if metric_type == "total_scope":
            # Total scope: All issues in release on that date
            query = text(f"""
                SELECT DISTINCT
                    jh.issue_key,
                    ji.summary,
                    jh.team_name,
                    'TOTAL_SCOPE' as metric_category
                FROM public.jira_issue_history jh
                LEFT JOIN public.jira_issues ji ON jh.issue_key = ji.issue_key
                WHERE jh.snapshot_date::date = CAST(:target_date AS date)
                AND :release_id = ANY(jh.fix_version_ids)
                AND (:target_issuetype = 'all' OR jh.issuetype = :target_issuetype)
                {team_filter}
            """)
            
        elif metric_type == "issues_completed":
            # Issues completed on this day: status changed to Done on this date
            query = text(f"""
                SELECT DISTINCT
                    jh.issue_key,
                    ji.summary,
                    jh.team_name,
                    'COMPLETED' as metric_category
                FROM public.jira_issue_history jh
                LEFT JOIN public.jira_issues ji ON jh.issue_key = ji.issue_key
                WHERE jh.snapshot_date::date = CAST(:target_date AS date)
                AND :release_id = ANY(jh.fix_version_ids)
                AND jh.status_category = 'Done'
                AND (:target_issuetype = 'all' OR jh.issuetype = :target_issuetype)
                {team_filter}
            """)
            
        elif metric_type == "issues_removed":
            # Issues removed: Were in release on previous day, not in release on this day
            query = text(f"""
                SELECT DISTINCT
                    jh_prev.issue_key,
                    ji.summary,
                    jh_prev.team_name,
                    'REMOVED' as metric_category
                FROM public.jira_issue_history jh_prev
                LEFT JOIN public.jira_issues ji ON jh_prev.issue_key = ji.issue_key
                LEFT JOIN public.jira_issue_history jh_curr ON 
                    jh_prev.issue_key = jh_curr.issue_key 
                    AND jh_curr.snapshot_date::date = CAST(:target_date AS date)
                WHERE jh_prev.snapshot_date::date = CAST(:target_date AS date) - INTERVAL '1 day'
                AND :release_id = ANY(jh_prev.fix_version_ids)
                AND (jh_curr.issue_key IS NULL OR :release_id != ALL(COALESCE(jh_curr.fix_version_ids, ARRAY[]::integer[])))
                AND (:target_issuetype = 'all' OR jh_prev.issuetype = :target_issuetype)
                {team_filter}
            """)
            
        elif metric_type == "wip_in_progress":
            # WIP: In progress status on this date
            query = text(f"""
                SELECT DISTINCT
                    jh.issue_key,
                    ji.summary,
                    jh.team_name,
                    'WIP' as metric_category
                FROM public.jira_issue_history jh
                LEFT JOIN public.jira_issues ji ON jh.issue_key = ji.issue_key
                WHERE jh.snapshot_date::date = CAST(:target_date AS date)
                AND :release_id = ANY(jh.fix_version_ids)
                AND jh.status_category = 'In Progress'
                AND (:target_issuetype = 'all' OR jh.issuetype = :target_issuetype)
                {team_filter}
            """)
            
        elif metric_type == "actual_remaining":
            # Actual remaining: Not done on this date
            query = text(f"""
                SELECT DISTINCT
                    jh.issue_key,
                    ji.summary,
                    jh.team_name,
                    'REMAINING' as metric_category
                FROM public.jira_issue_history jh
                LEFT JOIN public.jira_issues ji ON jh.issue_key = ji.issue_key
                WHERE jh.snapshot_date::date = CAST(:target_date AS date)
                AND :release_id = ANY(jh.fix_version_ids)
                AND jh.status_category != 'Done'
                AND (:target_issuetype = 'all' OR jh.issuetype = :target_issuetype)
                {team_filter}
            """)
        else:
            raise ValueError(f"Invalid metric_type: {metric_type}")
        
        logger.info(f"Executing query to get release history issues: release={release_name}, date={target_date}, issue_type={target_issuetype}, teams={team_names}, metric_type={metric_type}")
        
        # Execute query
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        issues = []
        for row in rows:
            row_dict = dict(row._mapping)
            issues.append({
                "issue_key": row_dict.get("issue_key"),
                "summary": row_dict.get("summary"),
                "team_name": row_dict.get("team_name"),
                "metric_category": row_dict.get("metric_category")
            })
        
        logger.info(f"Retrieved {len(issues)} issues for metric_type={metric_type}")
        return issues
            
    except Exception as e:
        logger.error(f"Error fetching release history issues (release={release_name}, date={target_date}, metric_type={metric_type}): {e}")
        raise e