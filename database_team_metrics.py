"""
Database Team Metrics - Database access functions for team metrics.

This module contains database access functions for team metrics.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
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


def get_team_count_in_progress(team_names: Optional[List[str]], conn: Connection = None) -> Dict[str, Any]:
    """
    Get current work in progress (WIP) for team(s) with breakdown by issue type.
    WIP = number of issues currently in progress, grouped by issue type.
    
    Args:
        team_names (Optional[List[str]]): List of team names, or None for all teams
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        dict: Dictionary with total count and breakdown by issue type
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        if team_names:
            # Build parameterized IN clause
            placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names))])
            params = {f"team_name_{i}": name for i, name in enumerate(team_names)}
            
            sql_query = f"""
                SELECT 
                    issue_type,
                    COUNT(*) as type_count
                FROM public.jira_issues
                WHERE team_name IN ({placeholders})
                AND status_category = 'In Progress'
                GROUP BY issue_type
                ORDER BY type_count DESC;
            """
            
            logger.info(f"Executing query to get count in progress for teams: {team_names}")
            logger.info(f"Parameters: team_names={team_names}")
            
            result = conn.execute(text(sql_query), params)
        else:
            # No filter - return all teams
            sql_query = """
                SELECT 
                    issue_type,
                    COUNT(*) as type_count
                FROM public.jira_issues
                WHERE status_category = 'In Progress'
                GROUP BY issue_type
                ORDER BY type_count DESC;
            """
            
            logger.info("Executing query to get count in progress for all teams")
            
            result = conn.execute(text(sql_query))
        
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
        logger.error(f"Error fetching count in progress for teams {team_names}: {e}")
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


def select_sprint_for_teams(
    team_name: Optional[str],
    is_group: bool,
    sprint_name: Optional[str],
    conn: Connection
) -> Dict[str, Any]:
    """
    Shared helper function for sprint selection with isGroup support.
    Handles team resolution, sprint selection, and multiple sprint validation.
    
    Args:
        team_name: Team name or group name (if is_group=True)
        is_group: If true, team_name is treated as a group name
        sprint_name: Optional sprint name (if provided, will be used)
        conn: Database connection
    
    Returns:
        Dict with:
            - 'team_names_list': List[str] - resolved team names
            - 'selected_sprint_name': Optional[str] - selected sprint name
            - 'selected_sprint_id': Optional[int] - selected sprint ID
            - 'selected_sprint_start_date': Optional[str] - selected sprint start date
            - 'selected_sprint_end_date': Optional[str] - selected sprint end date
            - 'error_message': Optional[str] - error message if validation fails
            - 'sprint_info': List[Dict] - detailed sprint information for logging
    """
    from fastapi import HTTPException
    import logging
    logger = logging.getLogger(__name__)
    
    # Resolve team names
    team_names_list = resolve_team_names_from_filter(team_name, is_group, conn)
    
    selected_sprint_name = sprint_name
    selected_sprint_id = None
    selected_sprint_start_date = None
    selected_sprint_end_date = None
    error_message = None
    sprint_info = []
    
    if not selected_sprint_name:
        if is_group:
            # Get sprints for all teams in the group
            all_sprints = []
            for team in team_names_list:
                team_sprints = get_sprints_with_total_issues_db(team, "active", conn)
                for sprint in team_sprints:
                    all_sprints.append(sprint)
                    sprint_info.append({
                        'sprint_id': sprint['sprint_id'],
                        'sprint_name': sprint['name'],
                        'team_name': team,
                        'total_issues': sprint.get('total_issues', 0)
                    })
            
            if not all_sprints:
                error_message = "No active sprints found"
            else:
                # Get unique sprint IDs
                unique_sprint_ids = set()
                for sprint in all_sprints:
                    unique_sprint_ids.add(sprint['sprint_id'])
                
                # If more than one unique sprint found, return error
                if len(unique_sprint_ids) > 1:
                    sprint_ids = sorted(list(unique_sprint_ids))
                    sprint_names = sorted(list(set(s['sprint_name'] for s in sprint_info)))
                    logger.error(f"Sprint Burndown is not shown because the group does not have one sprint for the group. "
                               f"Found {len(unique_sprint_ids)} unique sprints. Sprint IDs: {sprint_ids}, Sprint Names: {sprint_names}")
                    logger.error(f"Detailed sprint information by team: {sprint_info}")
                    error_message = "Sprint Burndown is not shown because the group does not have one sprint for the group"
                else:
                    # Only one unique sprint - use it
                    selected_sprint = all_sprints[0]
                    selected_sprint_name = selected_sprint['name']
                    selected_sprint_id = selected_sprint['sprint_id']
                    selected_sprint_start_date = selected_sprint.get('start_date')
                    selected_sprint_end_date = selected_sprint.get('end_date')
                    logger.info(f"Auto-selected sprint '{selected_sprint_name}' (ID: {selected_sprint_id})")
        else:
            # Single team - select sprint with max total_issues
            sprints = get_sprints_with_total_issues_db(team_names_list[0], "active", conn)
            if sprints:
                selected_sprint = max(sprints, key=lambda x: x['total_issues'])
                selected_sprint_name = selected_sprint['name']
                selected_sprint_id = selected_sprint['sprint_id']
                selected_sprint_start_date = selected_sprint.get('start_date')
                selected_sprint_end_date = selected_sprint.get('end_date')
                logger.info(f"Auto-selected sprint '{selected_sprint_name}' (ID: {selected_sprint_id}) with {selected_sprint['total_issues']} total issues")
            else:
                error_message = "No active sprints found"
    else:
        # Sprint name provided - need to look it up to get dates
        logger.info(f"Using provided sprint name: '{selected_sprint_name}'")
        # Look up sprint by name to get dates (try first team in list)
        if team_names_list:
            sprints = get_sprints_with_total_issues_db(team_names_list[0], None, conn)  # Get all sprints (no status filter)
            matching_sprint = next((s for s in sprints if s['name'] == selected_sprint_name), None)
            if matching_sprint:
                selected_sprint_id = matching_sprint['sprint_id']
                selected_sprint_start_date = matching_sprint.get('start_date')
                selected_sprint_end_date = matching_sprint.get('end_date')
                logger.info(f"Found sprint '{selected_sprint_name}' (ID: {selected_sprint_id}) with dates")
    
    return {
        'team_names_list': team_names_list,
        'selected_sprint_name': selected_sprint_name,
        'selected_sprint_id': selected_sprint_id,
        'selected_sprint_start_date': selected_sprint_start_date,
        'selected_sprint_end_date': selected_sprint_end_date,
        'error_message': error_message,
        'sprint_info': sprint_info
    }


def resolve_team_names_from_filter(
    team_name: Optional[str], 
    is_group: bool, 
    conn: Connection
) -> Optional[List[str]]:
    """
    Resolve team names from a filter (single team, group, or None for all teams).
    Uses groups/teams cache for fast lookups.
    
    Args:
        team_name: Optional team name or group name (if is_group=True)
        is_group: If true, team_name is treated as a group name
        conn: Database connection (used for cache refresh if needed, but not for reads)
    
    Returns:
        List of team names, or None if no filter (meaning all teams)
    
    Raises:
        HTTPException: If validation fails or group not found
    """
    from fastapi import HTTPException
    from groups_teams_cache import (
        get_cached_groups, get_cached_teams,
        group_exists_by_name_in_db, team_exists_by_name_in_db
    )
    
    if not team_name:
        return None  # None means all teams
    
    if is_group:
        # Validate group name
        if not isinstance(team_name, str):
            raise HTTPException(status_code=400, detail="Group name is required and must be a string")
        
        validated_group_name = team_name.strip()
        
        if not validated_group_name:
            raise HTTPException(status_code=400, detail="Group name cannot be empty")
        
        if len(validated_group_name) > 100:
            raise HTTPException(status_code=400, detail="Group name is too long (max 100 characters)")
        
        # Check if group exists
        cached_groups = get_cached_groups()
        if cached_groups:
            groups = cached_groups.get("groups", [])
            group_exists = any(g.get("name") == validated_group_name for g in groups)
        else:
            group_exists = group_exists_by_name_in_db(validated_group_name, conn)
        
        if not group_exists:
            raise HTTPException(
                status_code=404,
                detail=f"Group '{validated_group_name}' not found or has no teams"
            )
        
        # Get teams - use cache with recursive support
        from groups_teams_cache import get_recursive_teams_for_group_from_cache
        team_names_list = get_recursive_teams_for_group_from_cache(validated_group_name, conn, include_children=True)
        
        if not team_names_list:
            raise HTTPException(
                status_code=404,
                detail=f"Group '{validated_group_name}' has no teams"
            )
        
        return team_names_list
    else:
        # Single team - validate it
        if not isinstance(team_name, str):
            raise HTTPException(status_code=400, detail="Team name is required and must be a string")
        
        validated = team_name.strip()
        
        if not validated:
            raise HTTPException(status_code=400, detail="Team name cannot be empty")
        
        if len(validated) > 100:
            raise HTTPException(status_code=400, detail="Team name is too long (max 100 characters)")
        
        # Verify team exists
        cached_teams = get_cached_teams()
        if cached_teams:
            teams = cached_teams.get("teams", [])
            team_exists = any(t.get("team_name") == validated for t in teams)
        else:
            team_exists = team_exists_by_name_in_db(validated, conn)
        
        if not team_exists:
            raise HTTPException(
                status_code=404,
                detail=f"Team '{validated}' not found"
            )
        
        return [validated]


def get_closed_sprints_data_db(team_names: Optional[List[str]], months: int = 3, issue_type: Optional[str] = None, sort_by: str = "default", conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Get closed sprints data for specific team(s) or all teams with detailed metrics.
    Uses the get_closed_sprint_summary_fn database function to get comprehensive sprint completion data.
    
    Args:
        team_names (Optional[List[str]]): List of team names to filter by, or None for all teams
        months (int): Number of months to look back (1, 2, 3, 4, 6, 9)
        issue_type (Optional[str]): Issue type filter (optional, e.g., "Story", "Bug", "Task")
        sort_by (str): Sort order - "default" (team_name, end_date DESC) or "advanced" (start_date ASC, team_name ASC)
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of closed sprint dictionaries with detailed metrics (includes team_name)
    """
    try:
        # Build parameters for the function call
        params = {"months_back": months}
        
        # Add issue_type parameter (pass NULL if not provided)
        if issue_type:
            params["p_issue_type"] = issue_type
        else:
            params["p_issue_type"] = None
        
        # Determine sort order
        if sort_by == "advanced":
            order_by_clause = "ORDER BY start_date ASC, team_name ASC"
        else:
            order_by_clause = "ORDER BY team_name, end_date DESC"
        
        # Build query - pass team_names as array or NULL
        if team_names:
            # Pass array of team names to function
            params["p_team_names"] = team_names
            query = text(f"""
                SELECT *
                FROM public.get_closed_sprint_summary_fn(:months_back, CAST(:p_team_names AS text[]), :p_issue_type)
                {order_by_clause}
            """)
            
            logger.info(f"Executing query to get closed sprints data for teams: {team_names}")
            logger.info(f"Parameters: team_names={team_names}, months={months}, issue_type={issue_type}, sort_by={sort_by}")
            
            result = conn.execute(query, params)
        else:
            # Pass NULL for all teams
            query = text(f"""
                SELECT *
                FROM public.get_closed_sprint_summary_fn(:months_back, NULL, :p_issue_type)
                {order_by_clause}
            """)
            
            logger.info(f"Executing query to get closed sprints data for all teams")
            logger.info(f"Parameters: months={months}, issue_type={issue_type}, sort_by={sort_by}")
            
            result = conn.execute(query, params)
        
        # Convert rows to list of dictionaries - return all fields from database function
        closed_sprints = []
        for row in result:
            row_dict = dict(row._mapping)
            
            # Format complete_date if it exists
            complete_date = row_dict.get('complete_date')
            if complete_date and hasattr(complete_date, 'strftime'):
                complete_date = complete_date.strftime('%Y-%m-%d')
            
            # Remove end_date, keep only complete_date
            if 'end_date' in row_dict:
                del row_dict['end_date']
            
            # Set complete_date (will be None if not present in DB)
            row_dict['complete_date'] = complete_date
            
            # Format other date fields if they exist (excluding complete_date which is already formatted)
            for key, value in row_dict.items():
                if value is not None and hasattr(value, 'strftime') and key != 'complete_date':
                    row_dict[key] = value.strftime('%Y-%m-%d')
            
            closed_sprints.append(row_dict)
        
        return closed_sprints
            
    except Exception as e:
        logger.error(f"Error fetching closed sprints data (team_names={team_names}): {e}")
        raise e


def get_sprint_burndown_data_db(team_names: List[str], sprint_name: str, issue_type: str = "all", conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Get sprint burndown data for a specific team and sprint.
    Uses the get_sprint_burndown_data_for_team database function.
    
    Args:
        team_names (List[str]): List of team names
        sprint_name (str): Sprint name
        issue_type (str): Issue type filter (default: "all")
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of burndown data dictionaries
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        sql_query = """
            SELECT * FROM get_sprint_burndown_data_for_team(:sprint_name, :issue_type, CAST(:team_names AS text[]));
        """
        
        logger.info(f"Executing query to get sprint burndown data for teams: {team_names}, sprint: {sprint_name}")
        logger.info(f"Parameters: sprint_name={sprint_name}, issue_type={issue_type}, team_names={team_names}")
        
        result = conn.execute(text(sql_query), {
            "sprint_name": sprint_name,
            "issue_type": issue_type,
            "team_names": team_names
        })
        
        burndown_data = []
        for row in result:
            # Use row._mapping to access columns by name (safer than positional indexing)
            row_dict = dict(row._mapping)
            
            # Helper function to preserve None values
            def safe_int(value):
                if value is None:
                    return None
                return int(value) if value else 0
            
            burndown_data.append({
                'snapshot_date': row_dict.get('snapshot_date'),
                'start_date': row_dict.get('start_date'),
                'end_date': row_dict.get('end_date'),
                'remaining_issues': safe_int(row_dict.get('remaining_issues')),
                'ideal_remaining': safe_int(row_dict.get('ideal_remaining')),
                'total_issues': safe_int(row_dict.get('total_issues')),
                'issues_added_on_day': safe_int(row_dict.get('issues_added_on_day')),
                'issues_removed_on_day': safe_int(row_dict.get('issues_removed_on_day')),
                'issues_completed_on_day': safe_int(row_dict.get('issues_completed_on_day'))
            })
        
        return burndown_data
            
    except Exception as e:
        logger.error(f"Error fetching sprint burndown data for teams {team_names}, sprint {sprint_name}: {e}")
        raise e


def get_issues_trend_data_db(team_names: Optional[List[str]], months: int = 6, issue_type: str = "all", conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Get issues created and resolved over time for one or more teams.
    Uses the issues_created_and_resolved_over_time view.
    Returns all columns from the view (pass-through).
    
    Args:
        team_names (Optional[List[str]]): List of team names to filter by. If None or empty, returns data for all teams.
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
            WHERE report_month >= :start_date
        """
        
        # Prepare parameters
        params = {
            "start_date": start_date.strftime("%Y-%m-%d")
        }
        
        # Add team filter if team_names provided
        if team_names and len(team_names) > 0:
            # Build parameterized IN clause (same pattern as closed sprints)
            placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names))])
            sql_query += f" AND team_name IN ({placeholders})"
            for i, name in enumerate(team_names):
                params[f"team_name_{i}"] = name
        
        # Add issue type filter if not "all"
        if issue_type and issue_type != "all":
            sql_query += " AND issue_type = :issue_type"
            params["issue_type"] = issue_type
        
        sql_query += " ORDER BY report_month DESC;"
        
        logger.info(f"Executing query to get issues trend data for teams: {team_names}")
        logger.info(f"Parameters: months={months}, start_date={start_date}, issue_type={issue_type}")
        
        result = conn.execute(text(sql_query), params)
        
        # Convert rows to dictionaries - pass through all columns
        trend_data = []
        for row in result:
            # Convert row to dictionary using row._mapping
            row_dict = dict(row._mapping)
            trend_data.append(row_dict)
        
        return trend_data
            
    except Exception as e:
        logger.error(f"Error fetching issues trend data for teams {team_names}: {e}")
        raise e


def get_average_sprint_velocity_per_team(num_sprints: int = 5, team_names: Optional[List[str]] = None, conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Get average sprint velocity per team using the get_average_sprint_velocity_per_team database function.
    
    Args:
        num_sprints (int): Number of recent sprints to average (default: 5)
        team_names (Optional[List[str]]): List of team names, or None for all teams
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of dictionaries with team_name and avg_velocity
    """
    try:
        # Build parameters
        params = {"p_num_sprints": num_sprints}
        
        if team_names:
            # Pass array of team names
            sql_query = text("""
                SELECT * 
                FROM public.get_average_sprint_velocity_per_team(:p_num_sprints, CAST(:p_team_list AS text[]))
            """)
            params["p_team_list"] = team_names
        else:
            # Pass NULL for all teams
            sql_query = text("""
                SELECT * 
                FROM public.get_average_sprint_velocity_per_team(:p_num_sprints, NULL)
            """)
        
        logger.info(f"Executing query to get average sprint velocity per team")
        logger.info(f"Parameters: num_sprints={num_sprints}, team_names={team_names}")
        
        result = conn.execute(sql_query, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        velocity_data = []
        for row in rows:
            velocity_data.append({
                'team_name': row[0],
                'avg_velocity': float(row[1]) if row[1] is not None else 0.0
            })
        
        return velocity_data
            
    except Exception as e:
        logger.error(f"Error fetching average sprint velocity per team: {e}")
        raise e