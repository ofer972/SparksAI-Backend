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
from database_connection import get_db_connection
from database_team_metrics import (
    get_team_avg_sprint_metrics,
    get_team_count_in_progress,
    get_team_current_sprint_progress,
    get_sprints_with_total_issues_db,
    get_sprint_burndown_data_db,
    get_closed_sprints_data_db,
    get_issues_trend_data_db,
    get_average_sprint_velocity_per_team,
    resolve_team_names_from_filter,
    select_sprint_for_teams
)
from database_pi import get_pi_participating_teams_db
from pis_service import validate_pi
import config

logger = logging.getLogger(__name__)

team_metrics_router = APIRouter()


def validate_team_name(team_name: str) -> str:
    """
    Validate team name (basic validation only).
    """
    if not team_name or not isinstance(team_name, str):
        raise HTTPException(status_code=400, detail="Team name is required and must be a string")
    
    validated = team_name.strip()
    
    if not validated:
        raise HTTPException(status_code=400, detail="Team name cannot be empty")
    
    if len(validated) > 100:  # Reasonable length limit
        raise HTTPException(status_code=400, detail="Team name is too long (max 100 characters)")
    
    return validated

def validate_group_name(group_name: str) -> str:
    """
    Validate group name (basic validation only).
    """
    if not group_name or not isinstance(group_name, str):
        raise HTTPException(status_code=400, detail="Group name is required and must be a string")
    
    validated = group_name.strip()
    
    if not validated:
        raise HTTPException(status_code=400, detail="Group name cannot be empty")
    
    if len(validated) > 100:  # Reasonable length limit
        raise HTTPException(status_code=400, detail="Group name is too long (max 100 characters)")
    
    return validated


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
        # Compare actual completion to 100% expected (stricter thresholds)
        if percent_completed >= 90:  # Stricter: was 85% (100 - 15)
            return "green"
        elif percent_completed >= 80:  # Stricter: was 75%
            return "yellow"
        else:
            return "red"
    
    # Calculate expected completion based on timeline
    total_sprint_days = (end_date - start_date).days
    if total_sprint_days <= 0:
        return "green"
    
    days_elapsed = (today - start_date).days
    expected_completion = (days_elapsed / total_sprint_days) * 100
    
    # Determine status with slack (more relaxed thresholds)
    yellow_threshold = 25.0  # More relaxed: was 15.0
    if percent_completed >= expected_completion - slack_threshold:
        return "green"
    elif percent_completed >= expected_completion - yellow_threshold:
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
        "green" if < 25% of issues are in progress
        "yellow" if 25-50% of issues are in progress
        "red" if > 50% of issues are in progress
    """
    # Handle edge cases
    if total_issues == 0:
        return "green"
    
    in_progress_percent = (in_progress_issues / total_issues) * 100
    
    if in_progress_percent > 50:
        return "red"
    elif in_progress_percent >= 25:
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
    team_name: str = Query(..., description="Team name or group name (if isGroup=true)"),
    sprint_count: int = Query(5, description="Number of sprints to average (default: 5, max: 20)", ge=1, le=20),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get average sprint metrics for a specific team or group.
    
    Returns velocity, cycle time, and predictability metrics averaged over the last N sprints.
    When isGroup=true, calculates averages across all teams in the group.
    
    Args:
        team_name: Name of the team or group name (if isGroup=true)
        sprint_count: Number of recent sprints to average (default: 5)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with velocity, cycle_time, and predictability metrics
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Validate sprint_count
        validated_sprint_count = validate_sprint_count(sprint_count)
        
        # Validate and resolve team names (validate first, then resolve)
        validated_name = None
        if isGroup:
            validated_name = validate_group_name(team_name)
        else:
            validated_name = validate_team_name(team_name)
        
        # Resolve team names using shared helper function (same as other endpoints)
        team_names_list = resolve_team_names_from_filter(validated_name, isGroup, conn)
        
        # Get raw metrics data from database function (team-by-team rows)
        raw_data = get_team_avg_sprint_metrics(validated_sprint_count, team_names_list, conn)
        
        # Calculate averages from all rows
        # Velocity: average of completed issues count per sprint
        velocities = [row['issues_completed_count'] for row in raw_data if row.get('issues_completed_count') is not None]
        avg_velocity = int(round(sum(velocities) / len(velocities), 0)) if velocities else 0
        
        # Cycle time: total cycle time / total issues (weighted average)
        total_cycle_time = sum(row['total_cycle_time_sum_days'] for row in raw_data if row.get('total_cycle_time_sum_days') is not None)
        total_issues = sum(row['issues_completed_count'] for row in raw_data if row.get('issues_completed_count') is not None)
        avg_cycle_time = total_cycle_time / total_issues if total_issues > 0 else 0.0
        
        # Predictability: weighted by issue counts (total completed / total planned * 100)
        total_completed = sum(row['issues_completed_count'] for row in raw_data if row.get('issues_completed_count') is not None)
        total_planned = sum(row['issues_in_sprint_count'] for row in raw_data if row.get('issues_in_sprint_count') is not None)
        avg_predictability = (total_completed / total_planned * 100) if total_planned > 0 else 0.0
        
        # Calculate trend data grouped by sprint_id (aggregate across teams)
        trend_data = []
        try:
            from collections import defaultdict
            
            sprint_groups = defaultdict(lambda: {
                'issues_completed': 0,
                'total_cycle_time': 0.0,
                'issues_planned': 0,
                'sprint_complete_date': None
            })
            
            # Aggregate data by sprint_id (across all teams)
            rows_processed = 0
            rows_skipped = 0
            for row in raw_data:
                sprint_id = row.get('out_sprint_id')
                if sprint_id is None:
                    rows_skipped += 1
                    continue
                rows_processed += 1
                
                # Convert Decimal to float/int to avoid type mismatch errors
                issues_completed = row.get('issues_completed_count', 0) or 0
                cycle_time = row.get('total_cycle_time_sum_days', 0) or 0.0
                issues_planned = row.get('issues_in_sprint_count', 0) or 0
                
                sprint_groups[sprint_id]['issues_completed'] += int(issues_completed) if issues_completed else 0
                sprint_groups[sprint_id]['total_cycle_time'] += float(cycle_time) if cycle_time else 0.0
                sprint_groups[sprint_id]['issues_planned'] += int(issues_planned) if issues_planned else 0
                if row.get('sprint_complete_date') and not sprint_groups[sprint_id]['sprint_complete_date']:
                    sprint_groups[sprint_id]['sprint_complete_date'] = row.get('sprint_complete_date')
            
            # Calculate per-sprint metrics
            for sprint_id, data in sprint_groups.items():
                sprint_velocity = data['issues_completed']
                sprint_cycle_time = data['total_cycle_time'] / data['issues_completed'] if data['issues_completed'] > 0 else 0.0
                sprint_predictability = (data['issues_completed'] / data['issues_planned'] * 100) if data['issues_planned'] > 0 else 0.0
                
                # Format date if available
                sprint_date = None
                if data['sprint_complete_date']:
                    if hasattr(data['sprint_complete_date'], 'strftime'):
                        sprint_date = data['sprint_complete_date'].strftime('%Y-%m-%d')
                    else:
                        sprint_date = str(data['sprint_complete_date'])
                
                trend_data.append({
                    'sprint_id': sprint_id,
                    'sprint_complete_date': sprint_date,
                    'velocity': sprint_velocity,
                    'cycle_time': round(sprint_cycle_time, 2),
                    'predictability': round(sprint_predictability, 2)
                })
            
            # Sort by sprint_complete_date (oldest first), then by sprint_id
            # Fix: Handle type mismatches in sorting
            trend_data.sort(key=lambda x: (
                x['sprint_complete_date'] or '0000-00-00',
                int(x['sprint_id']) if x['sprint_id'] is not None else 0
            ))
            
            # Debug: Log results
            logger.info(f"DEBUG: Rows processed: {rows_processed}, skipped: {rows_skipped}, trend_data items: {len(trend_data)}")
        except Exception as e:
            logger.error(f"Error calculating trend data: {e}")
            logger.exception(e)  # Log full traceback
            trend_data = []  # Return empty array on error
        
        # Calculate status for each metric
        velocity_status = get_velocity_status(avg_velocity)
        cycle_time_status = get_cycle_time_status(avg_cycle_time)
        predictability_status = get_predictability_status(avg_predictability)
        
        # Build response data (same format as closed-sprints)
        response_data = {
            "velocity": avg_velocity,
            "cycle_time": avg_cycle_time,
            "predictability": avg_predictability,
            "velocity_status": velocity_status,
            "cycle_time_status": cycle_time_status,
            "predictability_status": predictability_status,
            "sprint_count": validated_sprint_count,
            "trend_data": trend_data
        }
        
        # Add metadata based on isGroup flag (same as closed-sprints endpoint)
        if isGroup:
            response_data["group_name"] = validated_name
            response_data["teams_in_group"] = team_names_list
            message = f"Retrieved average sprint metrics for group '{validated_name}'"
        else:
            response_data["team_name"] = validated_name
            message = f"Retrieved average sprint metrics for team '{validated_name}'"
        
        return {
            "success": True,
            "data": response_data,
            "message": message
        }
    
    except HTTPException:
        raise  # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching average sprint metrics (team_name={team_name}, isGroup={isGroup}): {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch average sprint metrics: {str(e)}"
        )


@team_metrics_router.get("/team-metrics/count-in-progress")
async def get_count_in_progress(
    team_name: str = Query(..., description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get count of issues currently in progress for a team or group with breakdown by issue type.
    
    Returns the number of issues with status_category = 'In Progress', grouped by issue type.
    Only includes issue types that have at least one issue in progress.
    When isGroup=true, aggregates counts across all teams in the group.
    
    Args:
        team_name: Name of the team or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with total count and breakdown by issue type
    """
    try:
        # Validate inputs (validate team_name or group_name based on isGroup)
        validated_name = None
        if isGroup:
            validated_name = validate_group_name(team_name)
        else:
            validated_name = validate_team_name(team_name)
        
        # Resolve team names using shared helper function (handles single team, group, or None)
        team_names_list = resolve_team_names_from_filter(validated_name, isGroup, conn)
        
        # Get count breakdown from database function
        count_data = get_team_count_in_progress(team_names_list, conn)
        
        # Build response data
        response_data = {
            "total_in_progress": count_data['total_in_progress'],
            "count_by_type": count_data['count_by_type']
        }
        
        # Add team/group information to response
        if isGroup:
            response_data["group_name"] = validated_name
            response_data["teams_in_group"] = team_names_list
            message = f"Retrieved count in progress for group '{validated_name}'"
        else:
            response_data["team_name"] = validated_name
            message = f"Retrieved count in progress for team '{validated_name}'"
        
        return {
            "success": True,
            "data": response_data,
            "message": message
        }
    
    except HTTPException:
        raise  # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching count in progress for team/group {team_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch count in progress: {str(e)}"
        )


@team_metrics_router.get("/team-metrics/current-sprint-progress")
async def get_current_sprint_progress(
    team_name: str = Query(..., description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get current sprint progress for a team or group with detailed breakdown.
    
    Returns sprint ID, sprint name, days left, total issues, completed, in progress, to do counts, completion percentage,
    and status indicators for the current active sprint.
    When isGroup=true and teams have different active sprints, aggregates counts and excludes sprint-specific fields.
    
    Args:
        team_name: Name of the team or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with sprint progress metrics including:
        - sprint_id: Sprint ID (only if single sprint)
        - sprint_name: Sprint name (only if single sprint)
        - days_left: Days remaining in sprint (only if single sprint)
        - days_in_sprint: Total days in sprint (only if single sprint)
        - total_issues: Total number of issues in active sprint(s)
        - completed_issues: Number of completed issues (status_category = 'Done')
        - in_progress_issues: Number of issues in progress
        - todo_issues: Number of issues in to do status
        - percent_completed: Percentage of completed issues (0-100)
        - percent_completed_status: Status indicator (green/yellow/red) based on timeline vs completion
        - in_progress_issues_status: Status indicator (green/yellow/red) based on WIP percentage
    """
    try:
        # Validate inputs (validate team_name or group_name based on isGroup)
        validated_name = None
        if isGroup:
            validated_name = validate_group_name(team_name)
        else:
            validated_name = validate_team_name(team_name)
        
        # Resolve team names using shared helper function (handles single team, group, or None)
        team_names_list = resolve_team_names_from_filter(validated_name, isGroup, conn)
        
        # Get sprint progress data from database function
        progress_data = get_team_current_sprint_progress(team_names_list, conn)
        
        # Calculate status indicators based on aggregated data
        percent_completed_status = get_percent_completed_status(
            progress_data['percent_completed'],
            progress_data['start_date'],
            progress_data['end_date']
        )
        in_progress_issues_status = get_in_progress_issues_status(
            progress_data['in_progress_issues'],
            progress_data['total_issues']
        )
        
        # Build response data
        response_data = {
            "total_issues": progress_data['total_issues'],
            "completed_issues": progress_data['completed_issues'],
            "in_progress_issues": progress_data['in_progress_issues'],
            "todo_issues": progress_data['todo_issues'],
            "percent_completed": progress_data['percent_completed'],
            "percent_completed_status": percent_completed_status,
            "in_progress_issues_status": in_progress_issues_status
        }
        
        # Always include sprint_id and sprint_name (null if multiple sprints)
        # Always calculate days_left and days_in_sprint if dates are available
        response_data["sprint_id"] = progress_data['sprint_id']
        response_data["sprint_name"] = progress_data['sprint_name']
        
        # Calculate days_left and days_in_sprint if we have dates (even for multiple sprints, use earliest dates)
        days_left = calculate_days_left(progress_data['end_date'])
        days_in_sprint = calculate_days_in_sprint(
            progress_data['start_date'],
            progress_data['end_date']
        )
        response_data["days_left"] = days_left
        response_data["days_in_sprint"] = days_in_sprint
        
        # Add team/group information to response
        if isGroup:
            response_data["group_name"] = validated_name
            response_data["teams_in_group"] = team_names_list
            message = f"Retrieved current sprint progress for group '{validated_name}'"
        else:
            response_data["team_name"] = validated_name
            message = f"Retrieved current sprint progress for team '{validated_name}'"
        
        return {
            "success": True,
            "data": response_data,
            "message": message
        }
    
    except HTTPException:
        raise  # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching current sprint progress for team/group {team_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch current sprint progress: {str(e)}"
        )


@team_metrics_router.get("/team-metrics/sprint-burndown")
async def get_sprint_burndown_data(
    team_name: str = Query(..., description="Team name or group name (if isGroup=true) to get burndown data for"),
    issue_type: str = Query("all", description="Issue type filter (default: 'all')"),
    sprint_name: str = Query(None, description="Sprint name (optional, will auto-select ACTIVE Sprint if not provided)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get sprint burndown data for a specific team or group.

    If no sprint_name is provided, automatically selects the ACTIVE sprint with the maximum total issues.
    For groups, all teams must have the same active sprint.

    Args:
        team_name: Name of the team or group (if isGroup=true)
        issue_type: Issue type filter (default: "all")
        sprint_name: Sprint name (optional, auto-selected if not provided)
        isGroup: If true, team_name is treated as a group name

    Returns:
        JSON response with burndown data and metadata
    """
    try:
        # Validate issue_type
        if not isinstance(issue_type, str):
            raise HTTPException(status_code=400, detail="Issue type must be a string")
        if issue_type.strip() == "":
            issue_type = "all"
        
        # Use shared helper for sprint selection
        sprint_selection = select_sprint_for_teams(team_name, isGroup, sprint_name, conn)
        team_names_list = sprint_selection['team_names_list']
        selected_sprint_name = sprint_selection['selected_sprint_name']
        selected_sprint_id = sprint_selection['selected_sprint_id']
        selected_sprint_start_date = sprint_selection.get('selected_sprint_start_date')
        selected_sprint_end_date = sprint_selection.get('selected_sprint_end_date')
        error_message = sprint_selection['error_message']
        
        # If error occurred, return error response
        if error_message:
            return {
                "success": False,
                "data": {},
                "message": error_message
            }
        
        # Get burndown data for selected sprint
        burndown_data = get_sprint_burndown_data_db(team_names_list, selected_sprint_name, issue_type, conn)
        
        # Calculate total issues in sprint and get start/end dates
        # Use dates from sprint selection first, fall back to burndown_data if needed
        total_issues_in_sprint = 0
        start_date = selected_sprint_start_date
        end_date = selected_sprint_end_date
        
        if burndown_data:
            total_issues_in_sprint = burndown_data[0].get('total_issues', 0)
            # Only use burndown_data dates if sprint selection didn't provide them
            if not start_date:
                start_date = burndown_data[0].get('start_date')
            if not end_date:
                end_date = burndown_data[0].get('end_date')
        
        # Build response data
        response_data = {
            "sprint_id": selected_sprint_id,
            "sprint_name": selected_sprint_name,
            "start_date": start_date,
            "end_date": end_date,
            "burndown_data": burndown_data,
            "issue_type": issue_type,
            "total_issues_in_sprint": total_issues_in_sprint,
            "isGroup": isGroup
        }
        
        # Add team/group information to response
        if isGroup:
            response_data["group_name"] = team_name
            response_data["teams_in_group"] = team_names_list
            message = f"Retrieved sprint burndown data for group '{team_name}' ({len(team_names_list)} teams) and sprint '{selected_sprint_name}'"
        else:
            response_data["team_name"] = team_name
            message = f"Retrieved sprint burndown data for team '{team_name}' and sprint '{selected_sprint_name}'"
        
        return {
            "success": True,
            "data": response_data,
            "message": message
        }
    
    except HTTPException:
        raise # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching sprint burndown data for team/group {team_name}: {e}")
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


def _fetch_closed_sprints_flat(
    team_name: Optional[str],
    isGroup: bool,
    months: int,
    issue_type: Optional[str],
    conn: Connection,
    sort_by: str = "default"
) -> Dict[str, Any]:
    """
    Shared helper function to fetch closed sprints in flat structure (not grouped by team).
    
    Args:
        team_name: Optional team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
        months: Number of months to look back
        issue_type: Optional issue type filter
        conn: Database connection
        sort_by: Sort order - "default" or "advanced"
    
    Returns:
        Dictionary with sprints list, metadata, and message
    """
    # Validate months parameter
    if months not in [1, 2, 3, 4, 6, 9]:
        raise HTTPException(
            status_code=400, 
            detail="Months parameter must be one of: 1, 2, 3, 4, 6, 9"
        )
    
    # Resolve team names using shared helper function
    team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
    
    # Build filter description for logging and response
    filter_description = None
    validated_name = None
    
    if team_name is not None:
        if isGroup:
            validated_name = validate_group_name(team_name)
            if team_names_list:
                filter_description = f"group '{validated_name}' ({len(team_names_list)} teams)"
                logger.info(f"Found {len(team_names_list)} teams in group '{validated_name}': {team_names_list}")
        else:
            validated_name = validate_team_name(team_name)
            filter_description = f"team '{validated_name}'"
    
    # Get closed sprints from database function with specified sort order
    closed_sprints_all = get_closed_sprints_data_db(
        team_names_list if team_names_list else None, 
        months, 
        issue_type=issue_type, 
        sort_by=sort_by,
        conn=conn
    )
    
    # Calculate metrics
    total_sprints = len(closed_sprints_all)
    total_issues_done = sum(sprint.get('issues_done', 0) or 0 for sprint in closed_sprints_all)
    average_velocity = round(total_issues_done / total_sprints, 2) if total_sprints > 0 else 0.0
    
    # Get unique teams count
    unique_teams = set()
    for sprint in closed_sprints_all:
        sprint_team = sprint.get('team_name')
        if sprint_team:
            unique_teams.add(sprint_team)
    teams_count = len(unique_teams)
    
    # Build metadata
    meta = {
        "months": months,
        "total_sprints": total_sprints,
        "teams_count": teams_count,
        "average_velocity": average_velocity
    }
    
    # Add team/group information to metadata
    if validated_name:
        if isGroup:
            meta["group_name"] = validated_name
            meta["teams_in_group"] = team_names_list
        else:
            meta["team_name"] = validated_name
    
    # Build response message
    if filter_description:
        message = f"Retrieved sprint velocity data for {filter_description} (last {months} months)"
    else:
        message = f"Retrieved sprint velocity data for all teams (last {months} months)"
    
    return {
        "sprints": closed_sprints_all,
        "meta": meta,
        "message": message
    }


@team_metrics_router.get("/team-metrics/closed-sprints")
async def get_closed_sprints(
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true). If not provided, returns all closed sprints."),
    months: int = Query(3, description="Number of months to look back (1, 2, 3, 4, 6, 9)", ge=1, le=12),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    issue_type: Optional[str] = Query(None, description="Issue type filter (optional, e.g., 'Story', 'Bug', 'Task')"),
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
    - issue_type: Optional issue type filter (e.g., 'Story', 'Bug', 'Task')
    
    Returns:
        JSON response with closed sprints grouped by team and metadata
    """
    try:
        # Validate months parameter first
        if months not in [1, 2, 3, 4, 6, 9]:
            raise HTTPException(
                status_code=400, 
                detail="Months parameter must be one of: 1, 2, 3, 4, 6, 9"
            )
        
        # Resolve team names using shared helper function (handles single team, group, or None)
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Build filter description for logging and response
        filter_description = None
        validated_name = None
        
        if team_name is not None:
            if isGroup:
                validated_name = validate_group_name(team_name)
                if team_names_list:
                    filter_description = f"group '{validated_name}' ({len(team_names_list)} teams)"
                    logger.info(f"Found {len(team_names_list)} teams in group '{validated_name}': {team_names_list}")
            else:
                validated_name = validate_team_name(team_name)
                filter_description = f"team '{validated_name}'"
        
        # Get closed sprints from database function (supports multiple teams)
        closed_sprints_all = get_closed_sprints_data_db(team_names_list if team_names_list else None, months, issue_type=issue_type, conn=conn)
        
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
        
        # Calculate average velocity: sum of issues_done across all sprints / number of sprints
        total_issues_done = sum(sprint.get('issues_done', 0) or 0 for sprint in closed_sprints_all)
        average_velocity = round(total_issues_done / total_sprints, 2) if total_sprints > 0 else 0.0
        
        response_data = {
            "months": months,
            "closed_sprints_by_team": sprints_by_team,
            "total_sprints": total_sprints,
            "teams_count": len(sprints_by_team),
            "average_velocity": average_velocity
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


@team_metrics_router.get("/team-metrics/sprint-velocity-advanced")
async def get_sprint_velocity_advanced(
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true). If not provided, returns all closed sprints."),
    months: int = Query(3, description="Number of months to look back (1, 2, 3, 4, 6, 9)", ge=1, le=12),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    issue_type: Optional[str] = Query(None, description="Issue type filter (optional, e.g., 'Story', 'Bug', 'Task')"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get closed sprints data sorted by start date (ascending) and team name (ascending) in a flat structure.
    
    This endpoint retrieves comprehensive sprint completion data including:
    - Sprint name, start/end dates, and sprint goals
    - Completion percentages and issue counts
    - Issues planned, added, done, and remaining
    
    Results are returned as a flat array sorted by sprint start date (oldest first) and team name (alphabetical).
    
    Parameters:
    - team_name: Optional team name or group name (if isGroup=true). If not provided, returns all closed sprints.
    - months: Number of months to look back (optional, default: 3)
      Valid values: 1, 2, 3, 4, 6, 9
    - isGroup: If true, team_name is treated as a group name and returns closed sprints for all teams in that group
    - issue_type: Optional issue type filter (e.g., 'Story', 'Bug', 'Task')
    
    Returns:
        JSON response with closed sprints as flat array sorted by start_date ASC, team_name ASC
    """
    try:
        result = _fetch_closed_sprints_flat(team_name, isGroup, months, issue_type, conn, sort_by="advanced")
        
        return {
            "success": True,
            "data": result["sprints"],
            "meta": result["meta"],
            "message": result["message"]
        }
    
    except HTTPException:
        raise # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching sprint velocity advanced (team_name={team_name}, isGroup={isGroup}): {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch sprint velocity advanced: {str(e)}"
        )


@team_metrics_router.get("/team-metrics/issues-trend")
async def get_issues_trend(
    team_name: str = Query(..., description="Team name or group name (if isGroup=true)"),
    months: int = Query(6, description="Number of months to look back (1, 2, 3, 4, 6, 9, 12)", ge=1, le=12),
    issue_type: str = Query("all", description="Issue type filter (default: 'all')"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get issues created and resolved over time for a specific team(s) or group.
    
    This endpoint retrieves trend data showing issues created, resolved, and cumulative open issues over time.
    Returns all columns from the issues_created_and_resolved_over_time view.
    When isGroup=true, aggregates data across all teams in the group.
    
    Parameters:
    - team_name: Name of the team or group name (if isGroup=true) (required)
    - months: Number of months to look back (optional, default: 6)
      Valid values: 1, 2, 3, 4, 6, 9, 12
      Note: Only values 1, 2, 3, 4, 6, 9 are accepted (will validate in code)
    - issue_type: Issue type filter (optional, default: 'all')
      Examples: 'Bug', 'Story', 'Task', 'all'
    - isGroup: If true, team_name is treated as a group name and returns trend data for all teams in that group
    
    Returns:
        JSON response with trend data list and metadata
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
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
        
        # Validate and resolve team names using shared helper function (same pattern as closed sprints)
        validated_name = None
        if isGroup:
            validated_name = validate_group_name(team_name)
        else:
            validated_name = validate_team_name(team_name)
        
        # Resolve team names using shared helper function
        team_names_list = resolve_team_names_from_filter(validated_name, isGroup, conn)
        
        # Get issues trend data from database function (now accepts list of team names)
        trend_data = get_issues_trend_data_db(team_names_list, months, issue_type, conn)
        
        # Build response data
        response_data = {
            "months": months,
            "issue_type": issue_type,
            "trend_data": trend_data,
            "count": len(trend_data)
        }
        
        # Add metadata based on what was filtered
        if isGroup:
            response_data["group_name"] = validated_name
            response_data["teams_in_group"] = team_names_list
            message = f"Retrieved issues trend data for group '{validated_name}' ({len(team_names_list)} teams) (last {months} months)"
        else:
            response_data["team_name"] = validated_name
            message = f"Retrieved issues trend data for team '{validated_name}' (last {months} months)"
        
        return {
            "success": True,
            "data": response_data,
            "message": message
        }
    
    except HTTPException:
        raise # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching issues trend data (team_name={team_name}, isGroup={isGroup}): {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch issues trend data: {str(e)}"
        )


@team_metrics_router.get("/team-metrics/get-average-sprint-velocity-per-team")
async def get_average_sprint_velocity_per_team_endpoint(
    num_sprints: int = Query(5, description="Number of sprints to average (default: 5, max: 20)", ge=1, le=20),
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    pi: Optional[str] = Query(None, description="Program Increment name - if provided, uses teams that participate in this PI"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get average sprint velocity per team using the get_average_sprint_velocity_per_team database function.
    
    Returns average velocity (completed issues per sprint) for each team over the last N sprints.
    
    Parameters:
    - num_sprints: Number of recent sprints to average (default: 5, max: 20)
    - team_name: Optional team name or group name (if isGroup=true)
    - isGroup: If true, team_name is treated as a group name
    - pi: Optional Program Increment name. If provided, uses all teams that participate in this PI.
          If both pi and team_name are provided, gets PI teams and then filters by team_name/isGroup.
    
    Returns:
        JSON response with velocity data per team
    """
    try:
        # Validate num_sprints
        validated_num_sprints = validate_sprint_count(num_sprints)
        
        # Resolve team names based on parameters
        team_names_list = None
        
        if pi:
            # Validate PI parameter
            validated_pi = validate_pi(pi)
            
            # Get teams that participate in the PI (reuse function to avoid duplication)
            pi_teams = get_pi_participating_teams_db(validated_pi, conn)
            
            if not pi_teams:
                return {
                    "success": True,
                    "data": {
                        "velocity_data": [],
                        "num_sprints": validated_num_sprints,
                        "count": 0,
                        "pi": validated_pi
                    },
                    "message": f"No teams found participating in PI '{validated_pi}'"
                }
            
            # If team_name is also provided, filter PI teams by team_name/isGroup
            if team_name:
                validated_name = None
                if isGroup:
                    validated_name = validate_group_name(team_name)
                    # Resolve group to team names
                    group_teams = resolve_team_names_from_filter(validated_name, True, conn)
                    # Intersection: teams that are both in PI and in the group
                    team_names_list = [t for t in pi_teams if t in group_teams]
                else:
                    validated_name = validate_team_name(team_name)
                    # Check if the team is in the PI teams
                    if validated_name in pi_teams:
                        team_names_list = [validated_name]
                    else:
                        team_names_list = []
            else:
                # Use all PI teams
                team_names_list = pi_teams
        elif team_name:
            # No PI provided, use team_name/isGroup resolution
            validated_name = None
            if isGroup:
                validated_name = validate_group_name(team_name)
            else:
                validated_name = validate_team_name(team_name)
            
            # Resolve team names using shared helper function
            team_names_list = resolve_team_names_from_filter(validated_name, isGroup, conn)
        else:
            # No filters - use all teams (pass None to database function)
            team_names_list = None
        
        # Get velocity data from database function
        velocity_data = get_average_sprint_velocity_per_team(validated_num_sprints, team_names_list, conn)
        
        # Build response data
        response_data = {
            "velocity_data": velocity_data,
            "num_sprints": validated_num_sprints,
            "count": len(velocity_data)
        }
        
        # Add metadata based on what was filtered
        if pi:
            response_data["pi"] = pi
        if team_name:
            if isGroup:
                response_data["group_name"] = team_name
                if team_names_list:
                    response_data["teams_in_group"] = team_names_list
            else:
                response_data["team_name"] = team_name
        
        # Build message
        if pi and team_name:
            message = f"Retrieved average sprint velocity for {len(velocity_data)} teams (PI: '{pi}', filter: '{team_name}')"
        elif pi:
            message = f"Retrieved average sprint velocity for {len(velocity_data)} teams participating in PI '{pi}'"
        elif team_name:
            if isGroup:
                message = f"Retrieved average sprint velocity for {len(velocity_data)} teams in group '{team_name}'"
            else:
                message = f"Retrieved average sprint velocity for team '{team_name}'"
        else:
            message = f"Retrieved average sprint velocity for {len(velocity_data)} teams"
        
        return {
            "success": True,
            "data": response_data,
            "message": message
        }
    
    except HTTPException:
        raise  # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching average sprint velocity per team (num_sprints={num_sprints}, team_name={team_name}, isGroup={isGroup}, pi={pi}): {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch average sprint velocity per team: {str(e)}"
        )
