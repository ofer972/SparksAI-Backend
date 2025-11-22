"""
Database Team Metrics - Database access functions for team metrics.

This module contains database access functions for team metrics.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
Copied exact logic, SQL statements, and functions from JiraDashboard-NEWUI project.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import Dict, Any, List, Optional
from datetime import datetime, date, timedelta
import logging
import config

logger = logging.getLogger(__name__)


def get_team_avg_sprint_metrics(sprint_count: int = 5, team_names: Optional[List[str]] = None, conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Get sprint metrics trend data for team(s) over the last N sprints.
    Uses get_sprint_metrics_trend_data_all_issues database function.
    Returns raw team-by-team data (averages calculated in endpoint).
    
    Args:
        sprint_count (int): Number of recent sprints to include (default: 5)
        team_names (Optional[List[str]]): List of team names, or None for all teams
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of dictionaries with team-by-team metrics (raw data, not averaged)
    """
    try:
        # Build parameters
        params = {"sprint_count": sprint_count}
        
        if team_names:
            # Pass array of team names
            sql_query = """
                SELECT * 
                FROM public.get_sprint_metrics_trend_data_all_issues(:sprint_count, CAST(:team_names AS text[]))
            """
            params["team_names"] = team_names
        else:
            # Pass NULL for all teams
            sql_query = """
                SELECT * 
                FROM public.get_sprint_metrics_trend_data_all_issues(:sprint_count, NULL)
            """
        
        logger.info(f"Executing query to get sprint metrics trend data")
        logger.info(f"Parameters: sprint_count={sprint_count}, team_names={team_names}")
        
        result = conn.execute(text(sql_query), params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        metrics_data = []
        for row in rows:
            metrics_data.append(dict(row._mapping))
        
        return metrics_data
            
    except Exception as e:
        logger.error(f"Error fetching sprint metrics trend data: {e}")
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


def get_team_current_sprint_progress(team_name: str, conn: Connection = None) -> Dict[str, Any]:
    """
    Get current sprint progress for a team with detailed breakdown.
    Returns sprint ID, sprint name, start date, end date, total issues, completed, in progress, to do counts, and completion percentage.
    
    Args:
        team_name (str): Team name
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        dict: Dictionary with 'sprint_id', 'sprint_name', 'start_date', 'end_date', 'total_issues', 'completed_issues', 
              'in_progress_issues', 'todo_issues', and 'percent_completed' values
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        sql_query = """
            SELECT 
                s.sprint_id,
                s.name as sprint_name,
                s.start_date,
                s.end_date,
                COUNT(*) as total_issues,
                COUNT(CASE WHEN i.status_category = 'Done' THEN 1 END) as completed_issues,
                COUNT(CASE WHEN i.status_category = 'In Progress' THEN 1 END) as in_progress_issues,
                COUNT(CASE WHEN i.status_category = 'To Do' THEN 1 END) as todo_issues,
                (COUNT(CASE WHEN i.status_category = 'Done' THEN 1 END)::numeric * 100) 
                    / NULLIF(COUNT(*), 0) as percent_completed
            FROM 
                public.jira_issues AS i
            INNER JOIN 
                public.jira_sprints AS s
                ON i.current_sprint_id = s.sprint_id
            WHERE 
                i.team_name = :team_name
                AND s.state = 'active'
            GROUP BY 
                s.sprint_id, s.name, s.start_date, s.end_date;
        """
        
        logger.info(f"Executing query to get current sprint progress for team: {team_name}")
        logger.info(f"Parameters: team_name={team_name}")
        
        result = conn.execute(text(sql_query), {"team_name": team_name})
        row = result.fetchone()
        
        if row:
            # Keep dates as date objects (not strings) for calculations in service layer
            sprint_id = int(row[0]) if row[0] else None
            sprint_name = str(row[1]) if row[1] else None
            start_date = row[2] if row[2] and hasattr(row[2], 'strftime') else None
            end_date = row[3] if row[3] and hasattr(row[3], 'strftime') else None
            
            return {
                'sprint_id': sprint_id,
                'sprint_name': sprint_name,
                'start_date': start_date,
                'end_date': end_date,
                'total_issues': int(row[4]) if row[4] else 0,
                'completed_issues': int(row[5]) if row[5] else 0,
                'in_progress_issues': int(row[6]) if row[6] else 0,
                'todo_issues': int(row[7]) if row[7] else 0,
                'percent_completed': float(row[8]) if row[8] is not None else 0.0
            }
        else:
            return {
                'sprint_id': None,
                'sprint_name': None,
                'start_date': None,
                'end_date': None,
                'total_issues': 0,
                'completed_issues': 0,
                'in_progress_issues': 0,
                'todo_issues': 0,
                'percent_completed': 0.0
            }
            
    except Exception as e:
        logger.error(f"Error fetching current sprint progress for team {team_name}: {e}")
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


def get_sprints_with_total_issues_db(team_name: str, sprint_status: str = None, conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Get sprints with their total issues count for a specific team.
    Used for sprint list endpoint and sprint selection logic.
    
    Args:
        team_name (str): Team name
        sprint_status (str): Sprint status filter (optional: "active", "closed", or None for all)
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of sprint dictionaries with sprint_id, name, total_issues, start_date, end_date, and sprint_goal
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        sql_query = """
            SELECT 
                s.sprint_id, 
                s.name,
                COUNT(i.issue_id) as total_issues,
                s.start_date,
                s.end_date,
                s.goal as sprint_goal
            FROM public.jira_sprints s
            LEFT JOIN public.jira_issues i ON (
                s.sprint_id = i.current_sprint_id AND i.team_name = :team_name
                OR s.sprint_id = ANY(i.sprint_ids) AND i.team_name = :team_name
            )
            WHERE s.sprint_id IN (
                SELECT DISTINCT current_sprint_id 
                FROM public.jira_issues 
                WHERE team_name = :team_name 
                AND current_sprint_id IS NOT NULL
                UNION
                SELECT DISTINCT unnest(sprint_ids) as sprint_id
                FROM public.jira_issues 
                WHERE team_name = :team_name 
                AND sprint_ids IS NOT NULL
            )
        """
        
        # Add sprint status filter if provided
        if sprint_status:
            sql_query += " AND s.state = :sprint_status"
        
        sql_query += """
            GROUP BY s.sprint_id, s.name, s.end_date, s.start_date, s.goal
            ORDER BY s.end_date DESC
            LIMIT 10;"""
        
        logger.info(f"Executing query to get sprints with total issues count for team: {team_name}")
        logger.info(f"Parameters: team_name={team_name}, sprint_status={sprint_status}")
        
        # Prepare parameters
        params = {"team_name": team_name}
        if sprint_status:
            params["sprint_status"] = sprint_status
        
        result = conn.execute(text(sql_query), params)
        
        sprints = []
        for row in result:
            sprints.append({
                'sprint_id': row[0],
                'name': row[1],
                'total_issues': int(row[2]) if row[2] else 0,
                'start_date': row[3].strftime('%Y-%m-%d') if row[3] else None,
                'end_date': row[4].strftime('%Y-%m-%d') if row[4] else None,
                'sprint_goal': row[5] if row[5] else None
            })
        
        return sprints
            
    except Exception as e:
        logger.error(f"Error fetching active sprints with total issues: {e}")
        raise e


def resolve_team_names_from_filter(
    team_name: Optional[str], 
    is_group: bool, 
    conn: Connection
) -> Optional[List[str]]:
    """
    Resolve team names from a filter (single team, group, or None for all teams).
    
    Args:
        team_name: Optional team name or group name (if is_group=True)
        is_group: If true, team_name is treated as a group name
        conn: Database connection
    
    Returns:
        List of team names, or None if no filter (meaning all teams)
    
    Raises:
        HTTPException: If validation fails or group not found
    """
    from fastapi import HTTPException
    from sqlalchemy import text
    import re
    
    if not team_name:
        return None  # None means all teams
    
    if is_group:
        # Validate group name
        if not isinstance(team_name, str):
            raise HTTPException(status_code=400, detail="Group name is required and must be a string")
        
        sanitized_group_name = re.sub(r'[^a-zA-Z0-9\s\-_]', '', team_name.strip())
        
        if not sanitized_group_name:
            raise HTTPException(status_code=400, detail="Group name contains invalid characters")
        
        # Get all teams under this group
        get_teams_query = text("""
            SELECT t.team_name
            FROM public.teams t
            JOIN public.team_groups tg ON t.team_key = tg.team_id
            JOIN public.groups g ON tg.group_id = g.group_key
            WHERE g.group_name = :group_name
            ORDER BY t.team_name
        """)
        
        teams_result = conn.execute(get_teams_query, {"group_name": sanitized_group_name})
        team_rows = teams_result.fetchall()
        
        if not team_rows:
            raise HTTPException(
                status_code=404,
                detail=f"Group '{sanitized_group_name}' not found or has no teams"
            )
        
        team_names_list = [row[0] for row in team_rows]
        return team_names_list
    else:
        # Single team - validate it
        if not isinstance(team_name, str):
            raise HTTPException(status_code=400, detail="Team name is required and must be a string")
        
        sanitized = re.sub(r'[^a-zA-Z0-9\s\-_]', '', team_name.strip())
        
        if not sanitized:
            raise HTTPException(status_code=400, detail="Team name contains invalid characters")
        
        return [sanitized]


def get_closed_sprints_data_db(team_names: Optional[List[str]], months: int = 3, conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Get closed sprints data for specific team(s) or all teams with detailed metrics.
    Uses the closed_sprint_summary view to get comprehensive sprint completion data.
    
    Args:
        team_names (Optional[List[str]]): List of team names to filter by, or None for all teams
        months (int): Number of months to look back (1, 2, 3, 4, 6, 9)
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of closed sprint dictionaries with detailed metrics (includes team_name)
    """
    try:
        # Calculate start date based on months parameter
        start_date = datetime.now().date() - timedelta(days=months * 30)
        
        # Build query with IN clause or no filter
        if team_names:
            # Build parameterized IN clause
            placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names))])
            params = {f"team_name_{i}": name for i, name in enumerate(team_names)}
            params["start_date"] = start_date.strftime("%Y-%m-%d")
            
            sql_query = f"""
                SELECT 
                    team_name,
                    sprint_name,
                    start_date,
                    end_date,
                    completed_percentage,
                    issues_at_start,
                    issues_added,
                    issues_done,
                    issues_remaining,
                    sprint_goal
                FROM closed_sprint_summary
                WHERE team_name IN ({placeholders})
                AND end_date >= :start_date
                ORDER BY team_name, end_date DESC
            """
            
            logger.info(f"Executing query to get closed sprints data for teams: {team_names}")
            logger.info(f"Parameters: team_names={team_names}, months={months}, start_date={start_date}")
            
            result = conn.execute(text(sql_query), params)
        else:
            # No team filter - return all teams
            sql_query = """
                SELECT 
                    team_name,
                    sprint_name,
                    start_date,
                    end_date,
                    completed_percentage,
                    issues_at_start,
                    issues_added,
                    issues_done,
                    issues_remaining,
                    sprint_goal
                FROM closed_sprint_summary
                WHERE end_date >= :start_date
                ORDER BY team_name, end_date DESC
            """
            
            logger.info(f"Executing query to get closed sprints data for all teams")
            logger.info(f"Parameters: months={months}, start_date={start_date}")
            
            result = conn.execute(text(sql_query), {
                "start_date": start_date.strftime("%Y-%m-%d")
            })
        
        closed_sprints = []
        for row in result:
            closed_sprints.append({
                'team_name': row[0],
                'sprint_name': row[1],
                'start_date': row[2],
                'end_date': row[3],
                'completed_percentage': float(row[4]) if row[4] else 0.0,
                'issues_at_start': int(row[5]) if row[5] else 0,
                'issues_added': int(row[6]) if row[6] else 0,
                'issues_done': int(row[7]) if row[7] else 0,
                'issues_remaining': int(row[8]) if row[8] else 0,
                'sprint_goal': row[9] if row[9] else ""
            })
        
        return closed_sprints
            
    except Exception as e:
        logger.error(f"Error fetching closed sprints data (team_names={team_names}): {e}")
        raise e


def get_sprint_burndown_data_db(team_name: str, sprint_name: str, issue_type: str = "all", conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Get sprint burndown data for a specific team and sprint.
    Uses the get_sprint_burndown_data_for_team database function.
    Copied exact logic from JiraDashboard-NEWUI project.
    
    Args:
        team_name (str): Team name
        sprint_name (str): Sprint name
        issue_type (str): Issue type filter (default: "all")
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of burndown data dictionaries
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        sql_query = """
            SELECT * FROM get_sprint_burndown_data_for_team(:sprint_name, :issue_type, :team_name);
        """
        
        logger.info(f"Executing query to get sprint burndown data for team: {team_name}, sprint: {sprint_name}")
        logger.info(f"Parameters: sprint_name={sprint_name}, issue_type={issue_type}, team_name={team_name}")
        
        result = conn.execute(text(sql_query), {
            "sprint_name": sprint_name,
            "issue_type": issue_type,
            "team_name": team_name
        })
        
        burndown_data = []
        for row in result:
            burndown_data.append({
                'snapshot_date': row[0],
                'start_date': row[1],
                'end_date': row[2],
                'remaining_issues': int(row[3]) if row[3] else 0,
                'ideal_remaining': int(row[4]) if row[4] else 0,
                'total_issues': int(row[5]) if row[5] else 0,
                'issues_added_on_day': int(row[6]) if row[6] else 0,
                'issues_removed_on_day': int(row[7]) if row[7] else 0,
                'issues_completed_on_day': int(row[8]) if row[8] else 0
            })
        
        return burndown_data
            
    except Exception as e:
        logger.error(f"Error fetching sprint burndown data for team {team_name}, sprint {sprint_name}: {e}")
        raise e


def get_sprint_burndown_data_db(team_name: str, sprint_name: str, issue_type: str = "all", conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Get sprint burndown data for a specific team and sprint.
    Uses the get_sprint_burndown_data_for_team database function.
    Copied exact logic from JiraDashboard-NEWUI project.
    
    Args:
        team_name (str): Team name
        sprint_name (str): Sprint name
        issue_type (str): Issue type filter (default: "all")
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of dictionaries with burndown data
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        sql_query = """
            SELECT * FROM public.get_sprint_burndown_data_for_team(:sprint_name, :issue_type, :team_name);
        """
        
        logger.info(f"Executing query to get sprint burndown data for team: {team_name}, sprint: {sprint_name}")
        logger.info(f"Parameters: sprint_name={sprint_name}, issue_type={issue_type}, team_name={team_name}")
        
        result = conn.execute(text(sql_query), {
            'sprint_name': sprint_name,
            'issue_type': issue_type,
            'team_name': team_name
        })
        
        burndown_data = []
        for row in result:
            burndown_data.append({
                'snapshot_date': row.snapshot_date,
                'start_date': row.sprint_start_date,
                'end_date': row.sprint_end_date,
                'remaining_issues': row.remaining_issues,
                'ideal_remaining': row.ideal_remaining,
                'total_issues': row.total_issues,
                'issues_added_on_day': row.issues_added_on_day,
                'issues_removed_on_day': row.issues_removed_on_day,
                'issues_completed_on_day': row.issues_completed_on_day
            })
        
        return burndown_data
            
    except Exception as e:
        logger.error(f"Error fetching sprint burndown data for team {team_name}, sprint {sprint_name}: {e}")
        raise e


def get_issues_trend_data_db(team_name: str, months: int = 6, issue_type: str = "all", conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Get issues created and resolved over time for a specific team.
    Uses the issues_created_and_resolved_over_time view.
    Returns all columns from the view (pass-through).
    
    Args:
        team_name (str): Team name to filter by
        months (int): Number of months to look back (1, 2, 3, 4, 6, 9, 12) - default: 6
        issue_type (str): Issue type filter (default: "all")
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of trend data dictionaries directly from the view
    """
    try:
        # Calculate start date based on months parameter
        start_date = datetime.now().date() - timedelta(days=months * 30)
        
        # SECURE: Parameterized query prevents SQL injection
        # We use SELECT * to pass through all columns from the view
        sql_query = """
            SELECT *
            FROM issues_created_and_resolved_over_time
            WHERE team_name = :team_name 
            AND report_month >= :start_date
        """
        
        # Add issue type filter if not "all"
        if issue_type and issue_type != "all":
            sql_query += " AND issue_type = :issue_type"
        
        sql_query += " ORDER BY report_month DESC;"
        
        logger.info(f"Executing query to get issues trend data for team: {team_name}")
        logger.info(f"Parameters: team_name={team_name}, months={months}, start_date={start_date}, issue_type={issue_type}")
        
        # Prepare parameters
        params = {
            "team_name": team_name,
            "start_date": start_date.strftime("%Y-%m-%d")
        }
        
        if issue_type and issue_type != "all":
            params["issue_type"] = issue_type
        
        result = conn.execute(text(sql_query), params)
        
        # Convert rows to dictionaries - pass through all columns
        trend_data = []
        for row in result:
            # Convert row to dictionary using row._mapping
            row_dict = dict(row._mapping)
            trend_data.append(row_dict)
        
        return trend_data
            
    except Exception as e:
        logger.error(f"Error fetching issues trend data for team {team_name}: {e}")
        raise e
