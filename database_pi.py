"""
Database PI - Database access functions for PI-related operations.

This module contains database access functions for PI operations.
Copied EXACT logic from JiraDashboard-NEWUI project - database.py fetch_pi_predictability_data function.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


def fetch_pi_predictability_data(pi_names, team_name=None, conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Fetch PI predictability data from the database function.
    Copied EXACT logic from JiraDashboard-NEWUI database.py lines 578-611
    
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


def fetch_pi_burndown_data(pi_name: str, project_keys: str = None, issue_type: str = None, team_names: str = None, conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Fetch PI burndown data from the database function.
    Copied EXACT logic from JiraDashboard-NEWUI database.py lines 538-576
    
    Args:
        pi_name (str): PI name to fetch data for (mandatory)
        project_keys (str, optional): Project keys filter
        issue_type (str, optional): Issue type filter (defaults to 'all' if not provided)
        team_names (str, optional): Team names filter
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of dictionaries with PI burndown data (all columns)
    """
    try:
        if not pi_name:
            return []
        
        # Default issue_type to 'all' if not provided
        issue_type = 'all' if not issue_type or issue_type == 'all' else issue_type
        
        logger.info(f"Executing PI burndown query for PI: {pi_name}")
        logger.info(f"Filters: project_keys={project_keys}, issue_type={issue_type}, team_names={team_names}")
        
        # SECURITY: Use parameterized query to prevent SQL injection
        sql_query_text = text("""
            SELECT * FROM public.get_pi_burndown_data(
                :pi_name,
                :project_keys,
                :issue_type,
                :team_names
            )
        """)
        
        logger.info(f"Executing SQL for PI burndown: {pi_name}")
        
        # Execute query with parameters (SECURE: prevents SQL injection)
        result = conn.execute(sql_query_text, {
            'pi_name': pi_name,
            'project_keys': project_keys,
            'issue_type': issue_type,
            'team_names': team_names
        })
        
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
    Copied EXACT logic from JiraDashboard-NEWUI _db_data_fetchers.py lines 792-832
    
    Uses the view: public.epic_pi_scope_changes_long
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
            SELECT * FROM public.epic_pi_scope_changes_long
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
    target_team_names: str = None,
    planned_grace_period_days: int = None,
    conn: Connection = None
) -> List[Dict[str, Any]]:
    """
    Fetch PI summary data from the database function get_pi_summary_data.
    
    Args:
        target_pi_name (str, optional): PI name filter
        target_project_keys (str, optional): Project keys filter
        target_issue_type (str, optional): Issue type filter
        target_team_names (str, optional): Team names filter
        planned_grace_period_days (int, optional): Planned grace period in days
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of dictionaries with PI summary data (all columns from SELECT *)
    """
    try:
        logger.info(f"Executing PI summary query")
        logger.info(f"Filters: pi={target_pi_name}, project={target_project_keys}, issue_type={target_issue_type}, team={target_team_names}, grace_period={planned_grace_period_days}")
        
        # SECURITY: Use parameterized query to prevent SQL injection
        sql_query_text = text("""
            SELECT * FROM public.get_pi_summary_data(
                :target_pi_name_param,
                :target_issue_type_param,
                :target_project_keys_param,
                :target_team_names_param,
                :planned_grace_period_days_param
            )
        """)
        
        logger.info(f"Executing SQL for PI summary data")
        
        # Execute query with parameters (SECURE: prevents SQL injection)
        result = conn.execute(sql_query_text, {
            'target_pi_name_param': target_pi_name,
            'target_issue_type_param': target_issue_type,
            'target_project_keys_param': target_project_keys,
            'target_team_names_param': target_team_names,
            'planned_grace_period_days_param': planned_grace_period_days
        })
        
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