"""
Team Metrics Service - REST API endpoints for team metrics.

This service provides endpoints for retrieving team metrics.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import Dict, Any, Optional
from datetime import date, datetime
import logging
import re
from database_connection import get_db_connection
from database_team_metrics import (
    get_team_avg_sprint_metrics,
    get_team_count_in_progress,
    get_team_current_sprint_progress,
    get_sprints_with_total_issues_db,
    get_sprint_burndown_data_db,
    get_closed_sprints_data_db,
    get_issues_trend_data_db
)
import config

logger = logging.getLogger(__name__)

team_metrics_router = APIRouter()


def validate_team_name(team_name: str) -> str:
    """
    Validate and sanitize team name to prevent SQL injection.
    Only allows alphanumeric characters, spaces, hyphens, and underscores.
    """
    if not team_name or not isinstance(team_name, str):
        raise HTTPException(status_code=400, detail="Team name is required and must be a string")
    
    # Remove any potentially dangerous characters
    sanitized = re.sub(r'[^a-zA-Z0-9\s\-_]', '', team_name.strip())
    
    if not sanitized:
        raise HTTPException(status_code=400, detail="Team name contains invalid characters")
    
    if len(sanitized) > 100:  # Reasonable length limit
        raise HTTPException(status_code=400, detail="Team name is too long (max 100 characters)")
    
    return sanitized

def validate_group_name(group_name: str) -> str:
    """
    Validate and sanitize group name to prevent SQL injection.
    Only allows alphanumeric characters, spaces, hyphens, and underscores.
    """
    if not group_name or not isinstance(group_name, str):
        raise HTTPException(status_code=400, detail="Group name is required and must be a string")
    
    # Remove any potentially dangerous characters
    sanitized = re.sub(r'[^a-zA-Z0-9\s\-_]', '', group_name.strip())
    
    if not sanitized:
        raise HTTPException(status_code=400, detail="Group name contains invalid characters")
    
    if len(sanitized) > 100:  # Reasonable length limit
        raise HTTPException(status_code=400, detail="Group name is too long (max 100 characters)")
    
    return sanitized


def validate_sprint_count(sprint_count: int) -> int:
    """
    Validate and sanitize sprint count.
    """
    if not isinstance(sprint_count, int) or sprint_count <= 0:
        raise HTTPException(status_code=400, detail="Sprint count must be a positive integer")
    if sprint_count > 20:  # Reasonable max limit
        raise HTTPException(status_code=400, detail="Sprint count cannot exceed 20")
    return sprint_count


def get_cycle_time_status(cycle_time: float) -> str:
    """
    Determine cycle time status based on value.
    
    Args:
        cycle_time: Cycle time in days
    
    Returns:
        "green" if cycle_time < 10
        "yellow" if 10 <= cycle_time <= 15
        "red" if cycle_time > 15
    """
    if cycle_time < 10:
        return "green"
    elif cycle_time <= 15:
        return "yellow"
    else:
        return "red"


def get_predictability_status(predictability: float) -> str:
    """
    Determine predictability status based on value.
    
    Args:
        predictability: Predictability percentage (0-100)
    
    Returns:
        "green" if predictability >= 75
        "yellow" if 60 <= predictability < 75
        "red" if predictability < 60
    """
    if predictability >= 75:
        return "green"
    elif predictability >= 60:
        return "yellow"
    else:
        return "red"


def get_velocity_status(velocity: int) -> str:
    """
    Determine velocity status based on value.
    Currently always returns "green" until a proper determination method is found.
    
    Args:
        velocity: Velocity (issue count)
    
    Returns:
        "green" (always for now)
    """
    return "green"


def get_percent_completed_status(
    percent_completed: float,
    start_date: date,
    end_date: date,
    slack_threshold: float = 15.0
) -> str:
    """
    Determine completion status based on sprint timeline vs actual completion.
    
    Compares actual completion percentage against expected completion based on
    how much of the sprint has elapsed.
    
    Args:
        percent_completed: Actual completion percentage (0-100)
        start_date: Sprint start date (date or datetime object)
        end_date: Sprint end date (date or datetime object)
        slack_threshold: Percentage slack allowed (default: 15%)
    
    Returns:
        "green" if ahead of schedule (actual >= expected - slack)
        "yellow" if slightly behind (expected - 25% <= actual < expected - slack)
        "red" if significantly behind (actual < expected - 25%)
        "green" if unable to calculate (edge cases)
    """
    # Handle edge cases
    if start_date is None or end_date is None:
        return "green"
    
    # Convert datetime to date if needed
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if isinstance(end_date, datetime):
        end_date = end_date.date()
    
    today = date.today()
    
    # If sprint hasn't started yet
    if today < start_date:
        return "green"
    
    # If sprint has ended
    if today >= end_date:
        # Compare actual completion to 100% expected
        if percent_completed >= 100 - slack_threshold:
            return "green"
        elif percent_completed >= 75:
            return "yellow"
        else:
            return "red"
    
    # Calculate expected completion based on timeline
    total_sprint_days = (end_date - start_date).days
    if total_sprint_days <= 0:
        return "green"
    
    days_elapsed = (today - start_date).days
    expected_completion = (days_elapsed / total_sprint_days) * 100
    
    # Determine status with slack
    if percent_completed >= expected_completion - slack_threshold:
        return "green"
    elif percent_completed >= expected_completion - 25.0:
        return "yellow"
    else:
        return "red"


def get_in_progress_issues_status(
    in_progress_issues: int,
    total_issues: int
) -> str:
    """
    Determine in-progress issues status based on percentage of total issues.
    
    High WIP (work in progress) indicates potential bottlenecks.
    
    Args:
        in_progress_issues: Number of issues in progress
        total_issues: Total number of issues in sprint
    
    Returns:
        "green" if < 40% of issues are in progress
        "yellow" if 40-60% of issues are in progress
        "red" if > 60% of issues are in progress
    """
    # Handle edge cases
    if total_issues == 0:
        return "green"
    
    in_progress_percent = (in_progress_issues / total_issues) * 100
    
    if in_progress_percent > 60:
        return "red"
    elif in_progress_percent >= 40:
        return "yellow"
    else:
        return "green"


def calculate_days_left(end_date: date) -> Optional[int]:
    """
    Calculate days left in sprint as integer (inclusive counting).
    
    Args:
        end_date: Sprint end date (date or datetime object)
    
    Returns:
        Integer representing days left (inclusive of today and end date)
        - If today is end date: returns 1
        - If end date is in future: returns (end_date - today).days + 1
        - If sprint ended: returns 0
        - If end_date is None: returns None
    """
    if end_date is None:
        return None
    
    # Convert datetime to date if needed
    if isinstance(end_date, datetime):
        end_date = end_date.date()
    
    today = date.today()
    
    if end_date < today:
        return 0  # Sprint ended
    else:
        # Inclusive counting: (end_date - today).days + 1
        # If today is end_date, result is 1
        # If today is Nov 3 and end is Nov 4, result is 2
        return (end_date - today).days + 1


def calculate_days_in_sprint(start_date: date, end_date: date) -> Optional[int]:
    """
    Calculate total days in sprint as integer (inclusive counting).
    
    Args:
        start_date: Sprint start date (date or datetime object)
        end_date: Sprint end date (date or datetime object)
    
    Returns:
        Integer representing total days in sprint (inclusive of start and end dates)
        - Returns (end_date - start_date).days + 1
        - If either date is None: returns None
    """
    if start_date is None or end_date is None:
        return None
    
    # Convert datetime to date if needed
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if isinstance(end_date, datetime):
        end_date = end_date.date()
    
    # Inclusive counting: (end_date - start_date).days + 1
    return (end_date - start_date).days + 1


@team_metrics_router.get("/team-metrics/get-avg-sprint-metrics")
async def get_avg_sprint_metrics(
    team_name: str = Query(..., description="Team name to get metrics for"),
    sprint_count: int = Query(5, description="Number of sprints to average (default: 5, max: 20)", ge=1, le=20),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get average sprint metrics for a specific team.
    
    Returns velocity, cycle time, and predictability metrics averaged over the last N sprints.
    
    Args:
        team_name: Name of the team
        sprint_count: Number of recent sprints to average (default: 5)
    
    Returns:
        JSON response with velocity, cycle_time, and predictability metrics
    """
    try:
        # Validate inputs
        validated_team_name = validate_team_name(team_name)
        validated_sprint_count = validate_sprint_count(sprint_count)
        
        # Get metrics from database function
        metrics = get_team_avg_sprint_metrics(validated_team_name, validated_sprint_count, conn)
        
        # Calculate status for each metric
        velocity_status = get_velocity_status(metrics['velocity'])
        cycle_time_status = get_cycle_time_status(metrics['cycle_time'])
        predictability_status = get_predictability_status(metrics['predictability'])
        
        return {
            "success": True,
            "data": {
                "velocity": metrics['velocity'],
                "cycle_time": metrics['cycle_time'],
                "predictability": metrics['predictability'],
                "velocity_status": velocity_status,
                "cycle_time_status": cycle_time_status,
                "predictability_status": predictability_status,
                "team_name": validated_team_name,
                "sprint_count": validated_sprint_count
            },
            "message": f"Retrieved average sprint metrics for team '{validated_team_name}'"
        }
    
    except HTTPException:
        raise  # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching average sprint metrics for team {validated_team_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch average sprint metrics: {str(e)}"
        )


@team_metrics_router.get("/team-metrics/count-in-progress")
async def get_count_in_progress(
    team_name: str = Query(..., description="Team name to get count for"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get count of issues currently in progress for a specific team with breakdown by issue type.
    
    Returns the number of issues with status_category = 'In Progress', grouped by issue type.
    Only includes issue types that have at least one issue in progress.
    
    Args:
        team_name: Name of the team
    
    Returns:
        JSON response with total count and breakdown by issue type
    """
    try:
        # Validate inputs
        validated_team_name = validate_team_name(team_name)
        
        # Get count breakdown from database function
        count_data = get_team_count_in_progress(validated_team_name, conn)
        
        return {
            "success": True,
            "data": {
                "total_in_progress": count_data['total_in_progress'],
                "count_by_type": count_data['count_by_type'],
                "team_name": validated_team_name
            },
            "message": f"Retrieved count in progress for team '{validated_team_name}'"
        }
    
    except HTTPException:
        raise  # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching count in progress for team {validated_team_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch count in progress: {str(e)}"
        )


@team_metrics_router.get("/team-metrics/current-sprint-progress")
async def get_current_sprint_progress(
    team_name: str = Query(..., description="Team name to get sprint progress for"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get current sprint progress for a specific team with detailed breakdown.
    
    Returns sprint ID, sprint name, days left, total issues, completed, in progress, to do counts, completion percentage,
    and status indicators for the current active sprint.
    
    Args:
        team_name: Name of the team
    
    Returns:
        JSON response with sprint progress metrics including:
        - sprint_id: Sprint ID
        - sprint_name: Sprint name
        - days_left: Days remaining in sprint as integer (inclusive counting, 1 = last day)
        - days_in_sprint: Total days in sprint as integer (inclusive counting)
        - total_issues: Total number of issues in active sprint
        - completed_issues: Number of completed issues (status_category = 'Done')
        - in_progress_issues: Number of issues in progress
        - todo_issues: Number of issues in to do status
        - percent_completed: Percentage of completed issues (0-100)
        - percent_completed_status: Status indicator (green/yellow/red) based on timeline vs completion
        - in_progress_issues_status: Status indicator (green/yellow/red) based on WIP percentage
    """
    try:
        # Validate inputs
        validated_team_name = validate_team_name(team_name)
        
        # Get sprint progress data from database function
        progress_data = get_team_current_sprint_progress(validated_team_name, conn)
        
        # Calculate derived fields in service layer (business logic)
        days_left = calculate_days_left(progress_data['end_date'])
        days_in_sprint = calculate_days_in_sprint(
            progress_data['start_date'],
            progress_data['end_date']
        )
        percent_completed_status = get_percent_completed_status(
            progress_data['percent_completed'],
            progress_data['start_date'],
            progress_data['end_date']
        )
        in_progress_issues_status = get_in_progress_issues_status(
            progress_data['in_progress_issues'],
            progress_data['total_issues']
        )
        
        return {
            "success": True,
            "data": {
                "sprint_id": progress_data['sprint_id'],
                "sprint_name": progress_data['sprint_name'],
                "days_left": days_left,
                "days_in_sprint": days_in_sprint,
                "total_issues": progress_data['total_issues'],
                "completed_issues": progress_data['completed_issues'],
                "in_progress_issues": progress_data['in_progress_issues'],
                "todo_issues": progress_data['todo_issues'],
                "percent_completed": progress_data['percent_completed'],
                "percent_completed_status": percent_completed_status,
                "in_progress_issues_status": in_progress_issues_status,
                "team_name": validated_team_name
            },
            "message": f"Retrieved current sprint progress for team '{validated_team_name}'"
        }
    
    except HTTPException:
        raise  # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching current sprint progress for team {validated_team_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch current sprint progress: {str(e)}"
        )


@team_metrics_router.get("/team-metrics/sprint-burndown")
async def get_sprint_burndown_data(
    team_name: str = Query(..., description="Team name to get burndown data for"),
    issue_type: str = Query("all", description="Issue type filter (default: 'all')"),
    sprint_name: str = Query(None, description="Sprint name (optional, will auto-select ACTIVE Sprint if not provided)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get sprint burndown data for a specific team.

    If no sprint_name is provided, automatically selects the ACTIVE sprint with the maximum total issues.

    Args:
        team_name: Name of the team
        issue_type: Issue type filter (default: "all")
        sprint_name: Sprint name (optional, auto-selected if not provided)

    Returns:
        JSON response with burndown data and metadata
    """
    try:
        # Validate inputs
        validated_team_name = validate_team_name(team_name)
        
        # Validate issue_type
        if not isinstance(issue_type, str):
            raise HTTPException(status_code=400, detail="Issue type must be a string")
        if issue_type.strip() == "":
            issue_type = "all"
        
        # Sprint selection logic (API service decides)
        selected_sprint_name = sprint_name
        selected_sprint_id = None
        
        if not selected_sprint_name:
            # Get active sprints and select the one with max total issues
            sprints = get_sprints_with_total_issues_db(validated_team_name, "active", conn)
            if sprints:
                # Select sprint with maximum total_issues
                selected_sprint = max(sprints, key=lambda x: x['total_issues'])
                selected_sprint_name = selected_sprint['name']
                selected_sprint_id = selected_sprint['sprint_id']
                logger.info(f"Auto-selected sprint '{selected_sprint_name}' (ID: {selected_sprint_id}) with {selected_sprint['total_issues']} total issues")
            else:
                return {
                    "success": False,
                    "data": {
                        "burndown_data": [],
                        "team_name": validated_team_name,
                        "sprint_id": None,
                        "sprint_name": None,
                        "issue_type": issue_type,
                        "total_issues_in_sprint": 0
                    },
                    "message": "No active sprints found"
                }
        else:
            # If sprint_name was provided, we don't need to search for sprint_id
            # We'll get it from the burndown data or set it to None
            logger.info(f"Using provided sprint name: '{selected_sprint_name}'")
        
        # Get burndown data for selected sprint
        burndown_data = get_sprint_burndown_data_db(validated_team_name, selected_sprint_name, issue_type, conn)
        
        # Calculate total issues in sprint and extract start/end dates from burndown data
        total_issues_in_sprint = 0
        start_date = None
        end_date = None
        
        if burndown_data:
            total_issues_in_sprint = burndown_data[0].get('total_issues', 0)
            start_date = burndown_data[0].get('start_date')
            end_date = burndown_data[0].get('end_date')
        
        return {
            "success": True,
            "data": {
                "sprint_id": selected_sprint_id,
                "sprint_name": selected_sprint_name,
                "start_date": start_date,
                "end_date": end_date,
                "burndown_data": burndown_data,
                "team_name": validated_team_name,
                "issue_type": issue_type,
                "total_issues_in_sprint": total_issues_in_sprint
            },
            "message": f"Retrieved sprint burndown data for team '{validated_team_name}' and sprint '{selected_sprint_name}'"
        }
    
    except HTTPException:
        raise # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching sprint burndown data for team {validated_team_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch sprint burndown data: {str(e)}"
        )


@team_metrics_router.get("/team-metrics/get-sprints")
async def get_sprints(
    team_name: str = Query(..., description="Team name to get sprints for"),
    sprint_status: str = Query(None, description="Sprint status filter (optional: 'active', 'closed', or leave empty for all)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get list of sprints for a specific team with total issues count.

    Args:
        team_name: Name of the team
        sprint_status: Sprint status filter (optional: "active", "closed", or None for all)

    Returns:
        JSON response with sprints list and metadata
    """
    try:
        # Validate inputs
        validated_team_name = validate_team_name(team_name)
        
        # Validate sprint_status if provided
        if sprint_status and sprint_status not in ["active", "closed"]:
            raise HTTPException(status_code=400, detail="Sprint status must be 'active' or 'closed'")
        
        # Get sprints from database function
        sprints = get_sprints_with_total_issues_db(validated_team_name, sprint_status, conn)
        
        return {
            "success": True,
            "data": {
                "team_name": validated_team_name,
                "sprint_status": sprint_status,
                "sprints": sprints,
                "count": len(sprints)
            },
            "message": f"Retrieved {len(sprints)} sprints for team '{validated_team_name}'"
        }
    
    except HTTPException:
        raise # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching sprints for team {validated_team_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch sprints: {str(e)}"
        )


@team_metrics_router.get("/team-metrics/closed-sprints")
async def get_closed_sprints(
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true). If not provided, returns all closed sprints."),
    months: int = Query(3, description="Number of months to look back (1, 2, 3, 4, 6, 9)", ge=1, le=12),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get closed sprints data for a specific team(s) or group with detailed completion metrics.
    
    This endpoint retrieves comprehensive sprint completion data including:
    - Sprint name, start/end dates, and sprint goals
    - Completion percentages and issue counts
    - Issues planned, added, done, and remaining
    
    Results are grouped by team name.
    
    Parameters:
    - team_name: Optional team name or group name (if isGroup=true). If not provided, returns all closed sprints.
    - months: Number of months to look back (optional, default: 3)
      Valid values: 1, 2, 3, 4, 6, 9
      Examples:
        - months=1: Last 1 month
        - months=3: Last 3 months (default)
        - months=6: Last 6 months
        - months=9: Last 9 months
    - isGroup: If true, team_name is treated as a group name and returns closed sprints for all teams in that group
    
    Returns:
        JSON response with closed sprints grouped by team and metadata
    """
    try:
        team_names_list = []
        filter_description = None
        validated_name = None
        
        # Build team list based on parameters
        if team_name is not None:
            if isGroup:
                # Validate as group name
                validated_group_name = validate_group_name(team_name)
                validated_name = validated_group_name
                
                # Get all teams under this group
                get_teams_query = text("""
                    SELECT t.team_name
                    FROM public.teams t
                    JOIN public.team_groups g ON t.group_key = g.group_key
                    WHERE g.group_name = :group_name
                    ORDER BY t.team_name
                """)
                
                logger.info(f"Fetching teams for group: {validated_group_name}")
                teams_result = conn.execute(get_teams_query, {"group_name": validated_group_name})
                team_rows = teams_result.fetchall()
                
                if not team_rows:
                    # Group doesn't exist or has no teams
                    raise HTTPException(
                        status_code=404,
                        detail=f"Group '{validated_group_name}' not found or has no teams"
                    )
                
                team_names_list = [row[0] for row in team_rows]
                filter_description = f"group '{validated_group_name}' ({len(team_names_list)} teams)"
                logger.info(f"Found {len(team_names_list)} teams in group '{validated_group_name}': {team_names_list}")
            else:
                # Validate as team name
                validated_team_name = validate_team_name(team_name)
                validated_name = validated_team_name
                team_names_list = [validated_team_name]
                filter_description = f"team '{validated_team_name}'"
        
        # Validate months parameter
        if months not in [1, 2, 3, 4, 6, 9]:
            raise HTTPException(
                status_code=400, 
                detail="Months parameter must be one of: 1, 2, 3, 4, 6, 9"
            )
        
        # Get closed sprints from database function (supports multiple teams)
        closed_sprints_all = get_closed_sprints_data_db(team_names_list if team_names_list else None, months, conn)
        
        # Group closed sprints by team_name
        sprints_by_team = {}
        for sprint in closed_sprints_all:
            sprint_team = sprint.get('team_name')
            if sprint_team:
                if sprint_team not in sprints_by_team:
                    sprints_by_team[sprint_team] = []
                sprints_by_team[sprint_team].append(sprint)
        
        # Build response message
        if filter_description:
            message = f"Retrieved closed sprints for {filter_description} (last {months} months)"
        else:
            message = f"Retrieved closed sprints for all teams (last {months} months)"
        
        total_sprints = len(closed_sprints_all)
        
        response_data = {
            "months": months,
            "closed_sprints_by_team": sprints_by_team,
            "total_sprints": total_sprints,
            "teams_count": len(sprints_by_team)
        }
        
        # Add metadata based on what was filtered
        if validated_name:
            if isGroup:
                response_data["group_name"] = validated_name
                response_data["teams_in_group"] = team_names_list
            else:
                response_data["team_name"] = validated_name
        
        return {
            "success": True,
            "data": response_data,
            "message": message
        }
    
    except HTTPException:
        raise # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching closed sprints (team_name={team_name}, isGroup={isGroup}): {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch closed sprints: {str(e)}"
        )


@team_metrics_router.get("/team-metrics/issues-trend")
async def get_issues_trend(
    team_name: str = Query(..., description="Team name to get trend data for"),
    months: int = Query(6, description="Number of months to look back (1, 2, 3, 4, 6, 9, 12)", ge=1, le=12),
    issue_type: str = Query("all", description="Issue type filter (default: 'all')"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get issues created and resolved over time for a specific team.
    
    This endpoint retrieves trend data showing issues created, resolved, and cumulative open issues over time.
    Returns all columns from the issues_created_and_resolved_over_time view.
    
    Parameters:
    - team_name: Name of the team (required)
    - months: Number of months to look back (optional, default: 6)
      Valid values: 1, 2, 3, 4, 6, 9, 12
      Note: Only values 1, 2, 3, 4, 6, 9 are accepted (will validate in code)
    - issue_type: Issue type filter (optional, default: 'all')
      Examples: 'Bug', 'Story', 'Task', 'all'
    
    Returns:
        JSON response with trend data list and metadata
    """
    try:
        # Validate inputs
        validated_team_name = validate_team_name(team_name)
        
        # Validate months parameter (same validation as closed sprints)
        if months not in [1, 2, 3, 4, 6, 9]:
            raise HTTPException(
                status_code=400, 
                detail="Months parameter must be one of: 1, 2, 3, 4, 6, 9"
            )
        
        # Validate issue_type
        if not isinstance(issue_type, str):
            raise HTTPException(status_code=400, detail="Issue type must be a string")
        if issue_type.strip() == "":
            issue_type = "all"
        
        # Get issues trend data from database function
        trend_data = get_issues_trend_data_db(validated_team_name, months, issue_type, conn)
        
        return {
            "success": True,
            "data": {
                "team_name": validated_team_name,
                "months": months,
                "issue_type": issue_type,
                "trend_data": trend_data,
                "count": len(trend_data)
            },
            "message": f"Retrieved issues trend data for team '{validated_team_name}' (last {months} months)"
        }
    
    except HTTPException:
        raise # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching issues trend data for team {validated_team_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch issues trend data: {str(e)}"
        )
