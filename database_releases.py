"""
Database Releases - Database access functions for release-related operations.

This module contains database access functions for release operations.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


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
        project_keys: Project keys filter
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
        logger.info(f"Filters: project_keys={project_keys}, issue_type={issue_type}, team_names={team_names}")
        
        params = {
            'release_id': release_id,
            'project_keys': project_keys,
            'issue_type': issue_type
        }
        
        if team_names:
            params['team_names'] = team_names
            sql_query_text = text("""
                SELECT * FROM public.get_release_burndown_data(
                    :release_id,
                    :project_keys,
                    :issue_type,
                    CAST(:team_names AS text[])
                )
            """)
            logger.info(f"Executing SQL for release burndown: release_id={release_id} with teams: {team_names}")
        else:
            sql_query_text = text("""
                SELECT * FROM public.get_release_burndown_data(
                    :release_id,
                    :project_keys,
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
                COUNT(DISTINCT CASE WHEN i.issue_type IN (SELECT issue_type FROM standard_issue_types){issue_type_condition} THEN i.issue_id END) AS number_of_standard_issue_types,
                COUNT(DISTINCT CASE WHEN i.issue_type IN (SELECT issue_type FROM standard_issue_types) AND i.status_category = 'Done'{issue_type_condition} THEN i.issue_id END) AS standard_issue_types_completed
            FROM filtered_releases r
            LEFT JOIN jira_issues i ON r.release_id = ANY(i.fix_version_ids)
            GROUP BY r.release_id, r.name, r.start_date, r.release_date
            ORDER BY r.start_date DESC
        """)
        
        # Breakdown query - groups by release_id and issue_type
        breakdown_query = text(f"""
            WITH filtered_releases AS (
                SELECT release_id, name, start_date, release_date
                FROM jira_releases
                WHERE {release_where_clause}
            )
            SELECT 
                r.release_id,
                i.issue_type,
                COUNT(DISTINCT i.issue_id) AS number_of_issues,
                COUNT(DISTINCT CASE WHEN i.status_category = 'Done' THEN i.issue_id END) AS number_of_issues_completed
            FROM filtered_releases r
            LEFT JOIN jira_issues i ON r.release_id = ANY(i.fix_version_ids)
            WHERE i.issue_id IS NOT NULL
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
        breakdown_dict = {}
        for row in breakdown_rows:
            row_dict = dict(row._mapping)
            release_id = row_dict.get('release_id')
            if release_id not in breakdown_dict:
                breakdown_dict[release_id] = []
            
            issue_type_name = row_dict.get('issue_type')
            number_of_issues = row_dict.get('number_of_issues') or 0
            number_of_issues_completed = row_dict.get('number_of_issues_completed') or 0
            
            # Calculate percentage
            percentage_completed_issues = 0.0
            if number_of_issues > 0:
                percentage_completed_issues = round((number_of_issues_completed / number_of_issues) * 100.0, 2)
            
            breakdown_dict[release_id].append({
                "issue_type": issue_type_name,
                "number_of_issues": number_of_issues,
                "number_of_issues_completed": number_of_issues_completed,
                "percentage_completed_issues": percentage_completed_issues
            })
        
        # Build metrics list
        metrics = []
        for row in summary_rows:
            row_dict = dict(row._mapping)
            
            # Format dates
            for key in ['release_start_date', 'release_end_date']:
                if row_dict.get(key) and hasattr(row_dict[key], 'strftime'):
                    row_dict[key] = row_dict[key].strftime('%Y-%m-%d')
            
            # Get counts
            number_of_epics = row_dict.get('number_of_epics') or 0
            epics_completed = row_dict.get('epics_completed') or 0
            number_of_standard_issue_types = row_dict.get('number_of_standard_issue_types') or 0
            standard_issue_types_completed = row_dict.get('standard_issue_types_completed') or 0
            
            # Calculate percentages (handle division by zero)
            percentage_completed_epics = 0.0
            if number_of_epics > 0:
                percentage_completed_epics = round((epics_completed / number_of_epics) * 100.0, 2)
            
            percentage_completed_standard_issue_types = 0.0
            if number_of_standard_issue_types > 0:
                percentage_completed_standard_issue_types = round((standard_issue_types_completed / number_of_standard_issue_types) * 100.0, 2)
            
            release_id = row_dict.get('release_id')
            issue_types_breakdown = breakdown_dict.get(release_id, [])
            
            metric_dict = {
                "release_id": release_id,
                "release_name": row_dict.get('release_name'),
                "release_start_date": row_dict.get('release_start_date'),
                "release_end_date": row_dict.get('release_end_date'),
                "number_of_epics": number_of_epics,
                "number_of_standard_issue_types": number_of_standard_issue_types,
                "percentage_completed_epics": percentage_completed_epics,
                "percentage_completed_standard_issue_types": percentage_completed_standard_issue_types,
                "issue_types_breakdown": issue_types_breakdown
            }
            
            metrics.append(metric_dict)
        
        logger.info(f"Retrieved {len(metrics)} release metrics records")
        return metrics
        
    except Exception as e:
        logger.error(f"Error fetching release metrics: {e}")
        raise e