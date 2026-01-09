"""
Database PI - Database access functions for PI-related operations.

This module contains database access functions for PI operations.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
import logging
from datetime import datetime, date, timedelta

logger = logging.getLogger(__name__)


def reduce_pi_burndown_data(burndown_data: List[Dict[str, Any]], days_without_change_threshold: int = 5) -> List[Dict[str, Any]]:
    """
    Reduce PI burndown data using Enhanced Option 5:
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
    
    # Fields to track for changes (try multiple naming variations)
    possible_change_fields = [
        'issues_completed', 'issues_completed_on_day', 'completed_issues',
        'issues_removed', 'issues_removed_on_day', 'removed_issues',
        'total_scope', 'total_issues', 'scope',
        'actual_remaining', 'remaining_issues', 'remaining'
    ]
    
    # Find date field (try common variations)
    possible_date_fields = ['date', 'snapshot_date', 'day', 'snapshot_day', 'burndown_date']
    
    # Get field names that exist in the data
    first_row = burndown_data[0]
    available_fields = [field for field in possible_change_fields if field in first_row]
    date_field = None
    for field in possible_date_fields:
        if field in first_row:
            date_field = field
            break
    
    # If no change fields found, return original data
    if not available_fields:
        logger.warning("No change fields found in PI burndown data, returning original data")
        return burndown_data
    
    reduced_data = []
    last_values = {}
    last_included_index = -1
    
    for i, row in enumerate(burndown_data):
        is_first = (i == 0)
        is_last = (i == len(burndown_data) - 1)
        
        # Calculate days since last included point (if we have date field)
        days_since_last_point = 0
        if date_field and last_included_index >= 0:
            try:
                current_date = row.get(date_field)
                last_date = burndown_data[last_included_index].get(date_field)
                
                # Handle different date formats
                if isinstance(current_date, str):
                    current_date = datetime.fromisoformat(current_date.replace('Z', '+00:00')).date() if 'T' in current_date else datetime.strptime(current_date, '%Y-%m-%d').date()
                elif isinstance(current_date, datetime):
                    current_date = current_date.date()
                
                if isinstance(last_date, str):
                    last_date = datetime.fromisoformat(last_date.replace('Z', '+00:00')).date() if 'T' in last_date else datetime.strptime(last_date, '%Y-%m-%d').date()
                elif isinstance(last_date, datetime):
                    last_date = last_date.date()
                
                if isinstance(current_date, date) and isinstance(last_date, date):
                    days_since_last_point = (current_date - last_date).days
            except (ValueError, AttributeError, TypeError) as e:
                # If date parsing fails, fall back to index-based counting
                days_since_last_point = i - last_included_index
        
        # Check if any tracked field has changed
        has_change = False
        if is_first:
            # Always include first day
            has_change = True
            # Initialize last_values
            for field in available_fields:
                last_values[field] = row.get(field)
        else:
            # Check for changes in any tracked field
            for field in available_fields:
                current_value = row.get(field)
                last_value = last_values.get(field)
                # Compare values (handle None, handle type differences)
                if current_value != last_value:
                    has_change = True
                    last_values[field] = current_value
        
        # Include if:
        # 1. First day
        # 2. Last day
        # 3. Has changes
        # 4. No change for threshold days (add marker point)
        if is_first or is_last or has_change or days_since_last_point >= days_without_change_threshold:
            reduced_data.append(row)
            last_included_index = i
            if has_change:
                # Update last_values for all fields when we include a point
                for field in available_fields:
                    last_values[field] = row.get(field)
        # else: skip this row
    
    logger.info(f"Reduced PI burndown data from {len(burndown_data)} to {len(reduced_data)} records ({len(burndown_data) - len(reduced_data)} removed, {100 * (len(burndown_data) - len(reduced_data)) / len(burndown_data):.1f}% reduction)")
    
    return reduced_data


def fetch_pi_predictability_data(pi_names, team_names: Optional[List[str]] = None, conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Fetch PI predictability data from the database function.
    
    For multiple PIs: loop through each PI and call the database function for each one.
    For multiple teams: call the function once per team and combine results.
    The SQL function get_pi_predictability_by_team accepts a single TEXT parameter (not array),
    so we call it once per team when team_names is provided.
    
    Args:
        pi_names (str | List[str]): Single PI name or list of PI names
        team_names (Optional[List[str]], optional): List of team names filter, or None for all teams
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of dictionaries with PI predictability data (all columns)
    """
    try:
        # Normalize pi_names to a list
        if isinstance(pi_names, str):
            pi_names = [pi_names]
        
        logger.info(f"Executing PI predictability query for PIs: {pi_names}")
        logger.info(f"Team filter: {team_names if team_names else 'None (all teams)'}")
        
        # IMPORTANT: Preserve the exact order returned by the database function
        # Try calling with NULL for both parameters to get the function's natural order
        # Then filter by pi_names and team_names in Python to preserve that order
        all_data = []
        
        # Try calling with NULL for both parameters to preserve database function's natural order
        # This returns all data in the order the function defines, then we filter in Python
        sql_query_text = text("""
            SELECT * FROM public.get_pi_predictability_by_team(
                NULL,
                NULL
            )
        """)
        
        logger.info(f"Executing SQL with NULL for both parameters to preserve database function order")
        logger.info(f"Will filter by PIs: {pi_names}, Teams: {team_names if team_names else 'all'}")
        
        result = conn.execute(sql_query_text)
        
        # Convert rows to list of dictionaries, preserving exact order from database function
        for row in result:
            row_dict = dict(row._mapping)
            
            # Filter by pi_names if specific PIs requested
            row_pi_name = row_dict.get('pi_name')
            if row_pi_name not in pi_names:
                continue  # Skip this row if PI not in filter
            
            # Filter by team_names if provided
            if team_names:
                row_team_name = row_dict.get('team_name')
                if row_team_name not in team_names:
                    continue  # Skip this row if team not in filter
            
            # Format array columns
            for col in ['issues_in_scope_keys', 'completed_issues_keys']:
                if col in row_dict:
                    if isinstance(row_dict[col], list):
                        row_dict[col] = ', '.join(row_dict[col])
            
            all_data.append(row_dict)
        
        logger.info(f"Retrieved {len(all_data)} PI predictability records")
        
        return all_data
            
    except Exception as e:
        logger.error(f"Error fetching PI predictability data: {e}")
        raise e


def fetch_pi_burndown_data(pi_name: str, project_keys: str = None, issue_type: str = None, team_names: Optional[List[str]] = None, conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Fetch PI burndown data from the database function.
    
    Args:
        pi_name (str): PI name to fetch data for (mandatory)
        project_keys (str, optional): Project keys filter
        issue_type (str, optional): Issue type filter (defaults to 'Epic' if not provided)
        team_names (Optional[List[str]], optional): List of team names filter, or None for all teams
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of dictionaries with PI burndown data (all columns)
    """
    try:
        if not pi_name:
            return []
        
        # Default issue_type to 'Epic' if not provided (note: can still pass 'all' explicitly)
        if not issue_type or issue_type == "":
            issue_type = 'Epic'
        
        logger.info(f"Executing PI burndown query for PI: {pi_name}")
        logger.info(f"Filters: project_keys={project_keys}, issue_type={issue_type}, team_names={team_names}")
        
        # Build parameters for the function call
        params = {
            'pi_name': pi_name,
            'project_keys': project_keys,
            'issue_type': issue_type
        }
        
        # Build query - pass team_names as array or NULL (following pattern from get_closed_sprints_data_db)
        if team_names:
            # Pass array of team names to function
            params['team_names'] = team_names
            sql_query_text = text("""
                SELECT * FROM public.get_pi_burndown_data(
                    :pi_name,
                    :project_keys,
                    :issue_type,
                    CAST(:team_names AS text[])
                )
            """)
            
            logger.info(f"Executing SQL for PI burndown: {pi_name} with teams: {team_names}")
        else:
            # Pass NULL for all teams
            sql_query_text = text("""
                SELECT * FROM public.get_pi_burndown_data(
                    :pi_name,
                    :project_keys,
                    :issue_type,
                    NULL
                )
            """)
            
            logger.info(f"Executing SQL for PI burndown: {pi_name} for all teams")
        
        # Execute query with parameters (SECURE: prevents SQL injection)
        result = conn.execute(sql_query_text, params)
        
        # Convert rows to list of dictionaries
        burndown_data = []
        for row in result:
            row_dict = dict(row._mapping)
            
            # Format array/list columns if present
            # Following the same pattern as pi_predictability_data
            for col in row_dict.keys():
                if isinstance(row_dict[col], list):
                    row_dict[col] = ', '.join(row_dict[col])
            
            burndown_data.append(row_dict)
        
        logger.info(f"Retrieved {len(burndown_data)} PI burndown records")
        
        # Apply Enhanced Option 5 data reduction
        burndown_data = reduce_pi_burndown_data(burndown_data, days_without_change_threshold=5)
        
        return burndown_data
            
    except Exception as e:
        logger.error(f"Error fetching PI burndown data: {e}")
        raise e


def fetch_scope_changes_data(pi_names: List[str], team_names: Optional[List[str]] = None, conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Fetch scope changes data for specified PIs.
    
    Uses the function: public.get_epic_pi_scope_changes
    Columns: "Quarter Name", "Stack Group", "Metric Name", "Value", "Issue Keys"
    Results are ordered by PI end date (chronological order).
    
    Args:
        pi_names (List[str]): List of PI names to filter by (mandatory)
        team_names (Optional[List[str]]): List of team names filter, or None for all teams
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of dictionaries with scope changes data (all columns from function)
    """
    try:
        if not pi_names:
            return []
        
        logger.info(f"Executing scope changes query for PIs: {pi_names}, Teams: {team_names if team_names else 'all'}")
        
        # Build parameters for the function call
        params = {}
        
        # Build query - pass team_names as array or NULL (following pattern from fetch_pi_burndown_data)
        if team_names:
            # Pass array of team names to function
            params['team_names'] = team_names
            sql_query_text = text("""
                SELECT * FROM public.get_epic_pi_scope_changes(
                    CAST(:team_names AS text[])
                )
            """)
            
            logger.info(f"Executing SQL for scope changes: PIs={pi_names} with teams: {team_names}")
        else:
            # Pass NULL for all teams
            sql_query_text = text("""
                SELECT * FROM public.get_epic_pi_scope_changes(
                    NULL
                )
            """)
            
            logger.info(f"Executing SQL for scope changes: PIs={pi_names} for all teams")
        
        # Execute query with parameters (SECURE: prevents SQL injection)
        result = conn.execute(sql_query_text, params)
        
        # Convert rows to list of dictionaries
        scope_data = []
        for row in result:
            row_dict = dict(row._mapping)
            
            # Filter by pi_names if specific PIs requested (function returns all PIs)
            row_pi_name = row_dict.get('Quarter Name')
            if row_pi_name not in pi_names:
                continue
            
            # Format array/list columns if present
            for col in row_dict.keys():
                if isinstance(row_dict[col], list):
                    row_dict[col] = ', '.join(row_dict[col])
            
            scope_data.append(row_dict)
        
        logger.info(f"Retrieved {len(scope_data)} scope changes records")
        
        return scope_data
            
    except Exception as e:
        logger.error(f"Error fetching scope changes data: {e}")
        raise e


def fetch_pi_summary_data(
    target_pi_name: str = None,
    target_project_keys: str = None,
    target_issue_type: str = None,
    target_team_names: Optional[List[str]] = None,
    planned_grace_period_days: int = None,
    conn: Connection = None
) -> List[Dict[str, Any]]:
    """
    Fetch PI summary data from the database function get_pi_summary_data.
    
    Args:
        target_pi_name (str, optional): PI name filter
        target_project_keys (str, optional): Project keys filter
        target_issue_type (str, optional): Issue type filter
        target_team_names (Optional[List[str]], optional): List of team names filter, or None for all teams
        planned_grace_period_days (int, optional): Planned grace period in days
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of dictionaries with PI summary data (all columns from SELECT *)
    """
    try:
        logger.info(f"Executing PI summary query")
        logger.info(f"Filters: pi={target_pi_name}, project={target_project_keys}, issue_type={target_issue_type}, team={target_team_names}, grace_period={planned_grace_period_days}")
        
        # Build parameters for the function call
        params = {
            'target_pi_name_param': target_pi_name,
            'target_issue_type_param': target_issue_type,
            'target_project_keys_param': target_project_keys,
            'planned_grace_period_days_param': planned_grace_period_days
        }
        
        # Build query - pass team_names as array or NULL (following pattern from fetch_pi_burndown_data)
        if target_team_names:
            # Pass array of team names to function
            params['target_team_names_param'] = target_team_names
            sql_query_text = text("""
                SELECT * FROM public.get_pi_summary_data(
                    :target_pi_name_param,
                    :target_issue_type_param,
                    :target_project_keys_param,
                    CAST(:target_team_names_param AS text[]),
                    :planned_grace_period_days_param
                )
            """)
            
            logger.info(f"Executing SQL for PI summary: {target_pi_name} with teams: {target_team_names}")
        else:
            # Pass NULL for all teams
            sql_query_text = text("""
                SELECT * FROM public.get_pi_summary_data(
                    :target_pi_name_param,
                    :target_issue_type_param,
                    :target_project_keys_param,
                    NULL,
                    :planned_grace_period_days_param
                )
            """)
            
            logger.info(f"Executing SQL for PI summary: {target_pi_name} for all teams")
        
        # Execute query with parameters (SECURE: prevents SQL injection)
        result = conn.execute(sql_query_text, params)
        
        # Convert rows to list of dictionaries - return all columns as-is
        summary_data = []
        for row in result:
            row_dict = dict(row._mapping)
            
            # Format array/list columns if present (following same pattern as other functions)
            for col in row_dict.keys():
                if isinstance(row_dict[col], list):
                    row_dict[col] = ', '.join(row_dict[col])
            
            summary_data.append(row_dict)
        
        logger.info(f"Retrieved {len(summary_data)} PI summary records")
        
        return summary_data
            
    except Exception as e:
        logger.error(f"Error fetching PI summary data: {e}")
        raise e


def fetch_pi_summary_data_by_team(
    target_pi_name: str = None,
    target_project_keys: str = None,
    target_issue_type: str = None,
    target_team_names: Optional[List[str]] = None,
    planned_grace_period_days: int = None,
    conn: Connection = None
) -> List[Dict[str, Any]]:
    """
    Fetch PI summary data grouped by team from the database function get_pi_summary_data_by_team.
    
    Returns multiple rows, one per team_name, with all columns from the SQL function.
    
    Args:
        target_pi_name (str, optional): PI name filter
        target_project_keys (str, optional): Project keys filter
        target_issue_type (str, optional): Issue type filter
        target_team_names (Optional[List[str]], optional): List of team names filter, or None for all teams
        planned_grace_period_days (int, optional): Planned grace period in days
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of dictionaries with PI summary data by team (all columns from SELECT *, including team_name)
    """
    try:
        logger.info(f"Executing PI summary by team query")
        logger.info(f"Filters: pi={target_pi_name}, project={target_project_keys}, issue_type={target_issue_type}, team={target_team_names}, grace_period={planned_grace_period_days}")
        
        # Build parameters for the function call
        params = {
            'target_pi_name_param': target_pi_name,
            'target_issue_type_param': target_issue_type,
            'target_project_keys_param': target_project_keys,
            'planned_grace_period_days_param': planned_grace_period_days
        }
        
        # Build query - pass team_names as array or NULL (following pattern from fetch_pi_summary_data)
        if target_team_names:
            # Pass array of team names to function
            params['target_team_names_param'] = target_team_names
            sql_query_text = text("""
                SELECT * FROM public.get_pi_summary_data_by_team(
                    :target_pi_name_param,
                    :target_issue_type_param,
                    :target_project_keys_param,
                    CAST(:target_team_names_param AS text[]),
                    :planned_grace_period_days_param
                )
            """)
            
            logger.info(f"Executing SQL for PI summary by team: {target_pi_name} with teams: {target_team_names}")
        else:
            # Pass NULL for all teams
            sql_query_text = text("""
                SELECT * FROM public.get_pi_summary_data_by_team(
                    :target_pi_name_param,
                    :target_issue_type_param,
                    :target_project_keys_param,
                    NULL,
                    :planned_grace_period_days_param
                )
            """)
            
            logger.info(f"Executing SQL for PI summary by team: {target_pi_name} for all teams")
        
        # Execute query with parameters (SECURE: prevents SQL injection)
        result = conn.execute(sql_query_text, params)
        
        # Convert rows to list of dictionaries - return all columns as-is
        summary_data = []
        for row in result:
            row_dict = dict(row._mapping)
            
            # Format array/list columns if present (following same pattern as other functions)
            for col in row_dict.keys():
                if isinstance(row_dict[col], list):
                    row_dict[col] = ', '.join(row_dict[col])
            
            # Filter out teams with all zeros in epic metrics
            planned_epics = row_dict.get('planned_epics', 0) or 0
            added_epics = row_dict.get('added_epics', 0) or 0
            removed_epics = row_dict.get('removed_epics', 0) or 0
            closed_epics = row_dict.get('closed_epics', 0) or 0
            remaining_epics = row_dict.get('remaining_epics', 0) or 0
            ideal_remaining = row_dict.get('ideal_remaining', 0) or 0
            
            # Skip teams with all zeros
            if (planned_epics == 0 and added_epics == 0 and removed_epics == 0 and 
                closed_epics == 0 and remaining_epics == 0 and ideal_remaining == 0):
                continue
            
            summary_data.append(row_dict)
        
        logger.info(f"Retrieved {len(summary_data)} PI summary by team records (after filtering zeros)")
        
        return summary_data
            
    except Exception as e:
        logger.error(f"Error fetching PI summary data by team: {e}")
        raise e


def get_pi_participating_teams_db(pi: str, conn: Connection = None) -> List[str]:
    """
    Get list of teams that have any issues in the jira_issues table for a specific PI.
    
    Returns distinct team names that have issues where quarter_pi_of_epic matches the provided PI.
    This is a reusable database function to avoid code duplication.
    
    Args:
        pi (str): Program Increment value (filters on quarter_pi_of_epic column)
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        List[str]: List of team names that participate in the PI
    
    Raises:
        Exception: If database query fails
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text("""
            SELECT DISTINCT team_name 
            FROM public.jira_issues 
            WHERE quarter_pi_of_epic = :pi
              AND team_name IS NOT NULL 
              AND team_name != ''
            ORDER BY team_name
        """)
        
        logger.info(f"Executing query to get PI participating teams for PI: {pi}")
        
        result = conn.execute(query, {"pi": pi})
        rows = result.fetchall()
        
        # Extract team names from rows
        team_names = [row[0] for row in rows if row[0]]
        
        logger.info(f"Retrieved {len(team_names)} participating teams for PI '{pi}'")
        
        return team_names
            
    except Exception as e:
        logger.error(f"Error fetching PI participating teams for PI {pi}: {e}")
        raise e


def fetch_epic_inbound_dependency_data(
    pi: Optional[str] = None,
    team_names: Optional[List[str]] = None,
    conn: Connection = None
) -> List[Dict[str, Any]]:
    """
    Fetch epic inbound dependency load data from epic_inbound_dependency_load_by_quarter view.

    Args:
        pi: Optional PI name filter (filters on quarter_pi_of_epic column)
        team_names: Optional list of team names to filter by (filters on assignee_team column)
        conn: Database connection from FastAPI dependency

    Returns:
        List of dictionaries with all columns from view
    """
    try:
        # Build WHERE clause conditions
        where_conditions = []
        params = {}

        if pi:
            where_conditions.append("quarter_pi_of_epic = :pi")
            params["pi"] = pi

        if team_names:
            # Build parameterized IN clause
            placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names))])
            where_conditions.append(f"assignee_team IN ({placeholders})")
            for i, name in enumerate(team_names):
                params[f"team_name_{i}"] = name

        # Build SQL query
        if where_conditions:
            where_clause = " AND ".join(where_conditions)
            query = text(f"""
                SELECT *
                FROM public.epic_inbound_dependency_load_by_quarter
                WHERE {where_clause}
            """)
        else:
            query = text("""
                SELECT *
                FROM public.epic_inbound_dependency_load_by_quarter
            """)

        logger.info(f"Executing query to get epic inbound dependency load: pi={pi}, team_names={team_names}")

        result = conn.execute(query, params)
        rows = result.fetchall()

        # Convert rows to list of dictionaries
        records = []
        for row in rows:
            row_dict = dict(row._mapping)

            # Format date/datetime fields if they exist
            for key, value in row_dict.items():
                if value is not None:
                    if hasattr(value, 'strftime'):
                        if 'date' in key.lower() or 'time' in key.lower():
                            row_dict[key] = value.strftime('%Y-%m-%d %H:%M:%S')
                        else:
                            row_dict[key] = value.strftime('%Y-%m-%d')
                    elif hasattr(value, 'isoformat'):
                        row_dict[key] = value.isoformat()

            records.append(row_dict)

        logger.info(f"Retrieved {len(records)} epic inbound dependency load records")
        return records

    except Exception as e:
        logger.error(f"Error fetching epic inbound dependency data: {e}")
        raise e


def fetch_epic_outbound_dependency_data(
    pi: Optional[str] = None,
    team_names: Optional[List[str]] = None,
    conn: Connection = None
) -> List[Dict[str, Any]]:
    """
    Fetch epic outbound dependency metrics data from epic_outbound_dependency_metrics_by_quarter view.

    Args:
        pi: Optional PI name filter (filters on quarter_pi_of_epic column)
        team_names: Optional list of team names to filter by (filters on owned_team column)
        conn: Database connection from FastAPI dependency

    Returns:
        List of dictionaries with all columns from view
    """
    try:
        # Build WHERE clause conditions
        where_conditions = []
        params = {}

        if pi:
            where_conditions.append("quarter_pi_of_epic = :pi")
            params["pi"] = pi

        if team_names:
            # Build parameterized IN clause
            placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names))])
            where_conditions.append(f"owned_team IN ({placeholders})")
            for i, name in enumerate(team_names):
                params[f"team_name_{i}"] = name

        # Build SQL query
        if where_conditions:
            where_clause = " AND ".join(where_conditions)
            query = text(f"""
                SELECT *
                FROM public.epic_outbound_dependency_metrics_by_quarter
                WHERE {where_clause}
            """)
        else:
            query = text("""
                SELECT *
                FROM public.epic_outbound_dependency_metrics_by_quarter
            """)

        logger.info(f"Executing query to get epic outbound dependency metrics: pi={pi}, team_names={team_names}")

        result = conn.execute(query, params)
        rows = result.fetchall()

        # Convert rows to list of dictionaries
        records = []
        for row in rows:
            row_dict = dict(row._mapping)

            # Format date/datetime fields if they exist
            for key, value in row_dict.items():
                if value is not None:
                    if hasattr(value, 'strftime'):
                        if 'date' in key.lower() or 'time' in key.lower():
                            row_dict[key] = value.strftime('%Y-%m-%d %H:%M:%S')
                        else:
                            row_dict[key] = value.strftime('%Y-%m-%d')
                    elif hasattr(value, 'isoformat'):
                        row_dict[key] = value.isoformat()

            records.append(row_dict)

        logger.info(f"Retrieved {len(records)} epic outbound dependency metrics records")
        return records

    except Exception as e:
        logger.error(f"Error fetching epic outbound dependency data: {e}")
        raise e


def get_pi_history_issues_db(
    pi_name: str,
    target_date: date,
    team_names: Optional[List[str]],
    issue_type: Optional[str] = None,
    metric_type: str = "total_scope",
    conn: Connection = None
) -> List[Dict[str, Any]]:
    """
    Get PI issue details for a specific date using get_pi_issue_details_for_date function.
    
    Args:
        pi_name (str): PI name (quarter_pi) to filter by
        target_date (date): The date to query (date only, no time)
        team_names (Optional[List[str]]): List of team names, or None for all teams
        issue_type (Optional[str]): Issue type filter (e.g., "Story", "Bug", "Epic"). If None, passes "all" to function.
        metric_type (str): Metric type to filter by. Valid values:
            - "issues_completed" - Issues completed on this day
            - "issues_removed" - Issues removed from PI
            - "total_scope" - Total scope of PI on this day (all issues in PI)
            - "wip_in_progress" - Work in progress items
            - "actual_remaining" - Actual remaining items (not done)
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of issue dictionaries with issue_key, summary, team_name, metric_category
    """
    try:
        # Map metric_type to function's metric_category values
        metric_category_map = {
            "issues_completed": "COMPLETED",
            "issues_removed": "REMOVED",
            "wip_in_progress": "WIP",
            "actual_remaining": "REMAINING"
        }
        
        # Handle issue_type parameter (pass "all" if not provided)
        target_issuetype = issue_type if issue_type else "all"
        
        # Build parameters for the function call
        params = {
            "target_pi_name": pi_name,
            "target_issuetype": target_issuetype,
            "target_date": target_date.strftime("%Y-%m-%d")
        }
        
        # Build query - pass team_names as array or NULL
        # The SQL function handles NULL team_names (returns all teams)
        if team_names:
            params["team_names"] = team_names
            sql_query_text = text("""
                SELECT * FROM public.get_pi_issue_details_for_date(
                    :target_pi_name,
                    :target_issuetype,
                    CAST(:team_names AS text[]),
                    CAST(:target_date AS date)
                )
            """)
            logger.info(f"Executing query to get PI history issues: pi={pi_name}, date={target_date}, issue_type={target_issuetype}, teams={team_names}, metric_type={metric_type}")
        else:
            # Pass NULL for all teams - function handles it
            sql_query_text = text("""
                SELECT * FROM public.get_pi_issue_details_for_date(
                    :target_pi_name,
                    :target_issuetype,
                    NULL,
                    CAST(:target_date AS date)
                )
            """)
            logger.info(f"Executing query to get PI history issues: pi={pi_name}, date={target_date}, issue_type={target_issuetype}, teams=all, metric_type={metric_type}")
        
        # Execute query with parameters (SECURE: prevents SQL injection)
        result = conn.execute(sql_query_text, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        all_issues = []
        for row in rows:
            row_dict = dict(row._mapping)
            all_issues.append(row_dict)
        
        # Filter by metric_type if not "total_scope"
        if metric_type == "total_scope":
            # For total_scope, we need ALL issues in PI on that date
            # The function returns issues with specific categories, so we need a separate query
            # Query jira_issue_history directly for all issues in PI on that date
            # Return same structure as function: issue_key, summary, team_name, metric_category
            params_scope = {
                "target_pi_name": pi_name,
                "target_date": target_date.strftime("%Y-%m-%d"),
                "target_issuetype": target_issuetype
            }
            
            # Build team filter if team_names provided
            if team_names:
                team_placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names))])
                for i, name in enumerate(team_names):
                    params_scope[f"team_name_{i}"] = name
                
                scope_query = text(f"""
                    SELECT DISTINCT
                        jh.issue_key,
                        ji.summary,
                        jh.team_name,
                        'TOTAL_SCOPE' as metric_category
                    FROM public.jira_issue_history jh
                    LEFT JOIN public.jira_issues ji ON jh.issue_key = ji.issue_key
                    WHERE jh.snapshot_date::date = CAST(:target_date AS date)
                    AND jh.quarter_pi = :target_pi_name
                    AND (:target_issuetype = 'all' OR jh.issuetype = :target_issuetype)
                    AND jh.team_name IN ({team_placeholders})
                """)
            else:
                # No team filter - get all teams
                scope_query = text("""
                    SELECT DISTINCT
                        jh.issue_key,
                        ji.summary,
                        jh.team_name,
                        'TOTAL_SCOPE' as metric_category
                    FROM public.jira_issue_history jh
                    LEFT JOIN public.jira_issues ji ON jh.issue_key = ji.issue_key
                    WHERE jh.snapshot_date::date = CAST(:target_date AS date)
                    AND jh.quarter_pi = :target_pi_name
                    AND (:target_issuetype = 'all' OR jh.issuetype = :target_issuetype)
                """)
            
            scope_result = conn.execute(scope_query, params_scope)
            scope_rows = scope_result.fetchall()
            
            issues = []
            for row in scope_rows:
                row_dict = dict(row._mapping)
                issues.append({
                    "issue_key": row_dict.get("issue_key"),
                    "summary": row_dict.get("summary"),
                    "team_name": row_dict.get("team_name"),
                    "metric_category": row_dict.get("metric_category")
                })
            
            logger.info(f"Retrieved {len(issues)} issues for total_scope metric")
            return issues
        else:
            # Filter by metric_category
            target_category = metric_category_map.get(metric_type)
            if not target_category:
                raise ValueError(f"Invalid metric_type: {metric_type}")
            
            # Filter issues by metric_category - return only what function returns
            filtered_issues = [
                {
                    "issue_key": issue.get("issue_key"),
                    "summary": issue.get("summary"),
                    "team_name": issue.get("team_name"),
                    "metric_category": issue.get("metric_category")
                }
                for issue in all_issues 
                if issue.get("metric_category") == target_category
            ]
            
            logger.info(f"Retrieved {len(filtered_issues)} issues for metric_type={metric_type}")
            return filtered_issues
            
    except Exception as e:
        logger.error(f"Error fetching PI history issues (pi={pi_name}, date={target_date}, metric_type={metric_type}): {e}")
        raise e