"""
Database PI - Database access functions for PI-related operations.

This module contains database access functions for PI operations.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


def fetch_pi_predictability_data(pi_names, team_name=None, conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Fetch PI predictability data from the database function.
    
    For multiple PIs: loop through each PI and call the database function for each one.
    This matches the logic from pi_predictability_table.py lines 189-195.
    
    Args:
        pi_names (str | List[str]): Single PI name or list of PI names
        team_name (str, optional): Single team name filter (can be "ALL SUMMARY")
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of dictionaries with PI predictability data (all columns)
    """
    try:
        # Normalize pi_names to a list
        if isinstance(pi_names, str):
            pi_names = [pi_names]
        
        logger.info(f"Executing PI predictability query for PIs: {pi_names}")
        logger.info(f"Team filter: {team_name if team_name else 'None'}")
        
        # Execute query for each PI and combine results
        # This matches the logic from pi_predictability_table.py lines 189-195
        all_data = []
        for pi_name in pi_names:
            # Use parameterized query (same approach as other database functions)
            sql_query_text = text("""
                SELECT * FROM public.get_pi_predictability_by_team(
                    :team_name,
                    :pi_name
                )
            """)
            
            logger.info(f"Executing SQL for PI: {pi_name}")
            
            # Execute query with parameters (SECURE: prevents SQL injection)
            result = conn.execute(sql_query_text, {
                'team_name': team_name,
                'pi_name': pi_name
            })
            
            # Convert rows to list of dictionaries
            for row in result:
                row_dict = dict(row._mapping)
                
                # Format array columns (copied from old project lines 603-606)
                for col in ['issues_in_scope_keys', 'completed_issues_keys']:
                    if col in row_dict:
                        if isinstance(row_dict[col], list):
                            row_dict[col] = ', '.join(row_dict[col])
                        # else keep as is (already string or None)
                
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
        
        return burndown_data
            
    except Exception as e:
        logger.error(f"Error fetching PI burndown data: {e}")
        raise e


def fetch_scope_changes_data(quarters: List[str], conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Fetch scope changes data for specified quarters.
    
    Uses the view: public.epic_pi_scope_changes_long_with_issues
    Columns: "Quarter Name" (as quarter), "Metric Name" (as metric_name), "Value" (as value)
    
    Args:
        quarters (List[str]): List of quarter/PI names to filter by (mandatory)
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of dictionaries with scope changes data (all columns from view)
    """
    try:
        if not quarters:
            return []
        
        logger.info(f"Executing scope changes query for quarters: {quarters}")
        
        # SECURITY: Use parameterized query with PostgreSQL array handling for IN clause
        sql_query_text = text("""
            SELECT * FROM public.epic_pi_scope_changes_long_with_issues
            WHERE "Quarter Name" = ANY(:quarters)
            ORDER BY "Quarter Name", "Metric Name"
        """)
        
        logger.info(f"Executing SQL for scope changes")
        
        # Execute query with parameters (SECURE: prevents SQL injection)
        # PostgreSQL handles the array properly with = ANY()
        result = conn.execute(sql_query_text, {
            'quarters': quarters
        })
        
        # Convert rows to list of dictionaries
        scope_data = []
        for row in result:
            row_dict = dict(row._mapping)
            
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