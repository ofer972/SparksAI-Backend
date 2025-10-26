"""
Database Team Metrics - Database access functions for team metrics.

This module contains database access functions for team metrics.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
Copied exact logic, SQL statements, and functions from JiraDashboard-NEWUI project.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import Dict, Any
import logging
import config

logger = logging.getLogger(__name__)


def get_team_avg_sprint_metrics(team_name: str, sprint_count: int = 5, conn: Connection = None) -> Dict[str, float]:
    """
    Get average sprint metrics for a team over the last N closed sprints.
    Uses get_sprint_metrics_by_team database function.
    Copied exact logic from JiraDashboard-NEWUI project.
    
    Args:
        team_name (str): Team name
        sprint_count (int): Number of recent sprints to average (default: 5)
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        dict: Dictionary with 'velocity', 'cycle_time', and 'predictability' values
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        sql_query = """
            SELECT 
                average_velocity_issue_count,
                average_cycle_time_days,
                overall_predictability_percent
            FROM get_sprint_metrics_by_team(:sprint_count, :team_name);
        """
        
        logger.info(f"Executing query to get average sprint metrics for team: {team_name}")
        logger.info(f"SQL Query: {sql_query}")
        logger.info(f"Parameters: sprint_count={sprint_count}, team_name={team_name}")
        
        result = conn.execute(text(sql_query), {
            "sprint_count": sprint_count, 
            "team_name": team_name
        })
        row = result.fetchone()
        
        if row and row[0] is not None and row[1] is not None and row[2] is not None:
            return {
                'velocity': int(round(float(row[0]), 0)) if row[0] else 0,
                'cycle_time': float(row[1]) if row[1] else 0.0,
                'predictability': float(row[2]) if row[2] else 0.0
            }
        else:
            return {'velocity': 0, 'cycle_time': 0.0, 'predictability': 0.0}
            
    except Exception as e:
        logger.error(f"Error fetching average sprint metrics for team {team_name}: {e}")
        raise e


def get_team_count_in_progress(team_name: str, conn: Connection = None) -> Dict[str, Any]:
    """
    Get current work in progress (WIP) for a team with breakdown by issue type.
    WIP = number of issues currently in progress, grouped by issue type.
    Copied exact logic from JiraDashboard-NEWUI project.
    
    Args:
        team_name (str): Team name
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        dict: Dictionary with total count and breakdown by issue type
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        sql_query = """
            SELECT 
                issue_type,
                COUNT(*) as type_count
            FROM public.jira_issues
            WHERE team_name = :team_name 
            AND status_category = 'In Progress'
            GROUP BY issue_type
            ORDER BY type_count DESC;
        """
        
        logger.info(f"Executing query to get count in progress for team: {team_name}")
        logger.info(f"SQL Query: {sql_query}")
        logger.info(f"Parameters: team_name={team_name}")
        
        result = conn.execute(text(sql_query), {"team_name": team_name})
        rows = result.fetchall()
        
        total_count = 0
        count_by_type = {}
        
        for row in rows:
            issue_type = row[0]
            type_count = int(row[1])
            count_by_type[issue_type] = type_count
            total_count += type_count
        
        return {
            'total_in_progress': total_count,
            'count_by_type': count_by_type
        }
            
    except Exception as e:
        logger.error(f"Error fetching count in progress for team {team_name}: {e}")
        raise e


def get_team_current_sprint_completion(team_name: str, conn: Connection = None) -> float:
    """
    Get completion rate for a team (percentage of completed issues in current active sprint).
    Uses get_team_active_sprint_metrics helper function.
    Copied exact logic from JiraDashboard-NEWUI project.
    
    Args:
        team_name (str): Team name
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        float: Completion rate as percentage (0-100)
    """
    try:
        # Get active sprint metrics using helper function
        metrics = get_team_active_sprint_metrics(team_name, conn)
        total = metrics.get('total_issues', 0)
        completed = metrics.get('completed_issues', 0)
        
        if total > 0:
            return round((completed / total) * 100)
        else:
            return 0.0
            
    except Exception as e:
        logger.error(f"Error fetching current sprint completion for team {team_name}: {e}")
        raise e


def get_team_active_sprint_metrics(team_name: str, conn: Connection = None) -> Dict[str, Any]:
    """
    Get active sprint metrics for a team.
    Helper function used by get_team_current_sprint_completion.
    Copied exact logic from JiraDashboard-NEWUI project.
    
    Args:
        team_name (str): Team name
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        dict: Dictionary with sprint metrics
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        sql_query = """
            SELECT 
                COUNT(*) as total_issues,
                COUNT(CASE WHEN status_category = 'Done' THEN 1 END) as completed_issues,
                COUNT(CASE WHEN status_category != 'Done' THEN 1 END) as in_progress_issues
            FROM public.jira_issues
            WHERE team_name = :team_name 
            AND current_sprint_id IN (
                SELECT sprint_id FROM public.jira_sprints 
                WHERE state = 'active'
            );
        """
        
        logger.info(f"Executing query to get active sprint metrics for team: {team_name}")
        logger.info(f"SQL Query: {sql_query}")
        logger.info(f"Parameters: team_name={team_name}")
        
        result = conn.execute(text(sql_query), {"team_name": team_name})
        row = result.fetchone()
        
        if row:
            return {
                'total_issues': int(row[0]) if row[0] else 0,
                'completed_issues': int(row[1]) if row[1] else 0,
                'in_progress_issues': int(row[2]) if row[2] else 0
            }
        else:
            return {'total_issues': 0, 'completed_issues': 0, 'in_progress_issues': 0}
            
    except Exception as e:
        logger.error(f"Error fetching active sprint metrics for team {team_name}: {e}")
        raise e
