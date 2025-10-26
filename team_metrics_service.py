"""
Team Metrics Service - REST API endpoints for team metrics.

This service provides endpoints for retrieving team metrics.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.engine import Connection
from typing import Dict, Any
import logging
import re
from database_connection import get_db_connection
from database_team_metrics import (
    get_team_avg_sprint_metrics,
    get_team_count_in_progress,
    get_team_current_sprint_completion
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


def validate_sprint_count(sprint_count: int) -> int:
    """
    Validate and sanitize sprint count.
    """
    if not isinstance(sprint_count, int) or sprint_count <= 0:
        raise HTTPException(status_code=400, detail="Sprint count must be a positive integer")
    if sprint_count > 20:  # Reasonable max limit
        raise HTTPException(status_code=400, detail="Sprint count cannot exceed 20")
    return sprint_count


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
        
        return {
            "success": True,
            "data": {
                "velocity": metrics['velocity'],
                "cycle_time": metrics['cycle_time'],
                "predictability": metrics['predictability'],
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
    Get count of issues currently in progress for a specific team.
    
    Returns the number of issues with status_category = 'In Progress'.
    
    Args:
        team_name: Name of the team
    
    Returns:
        JSON response with count of issues in progress
    """
    try:
        # Validate inputs
        validated_team_name = validate_team_name(team_name)
        
        # Get count from database function
        count = get_team_count_in_progress(validated_team_name, conn)
        
        return {
            "success": True,
            "data": {
                "count": count,
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


@team_metrics_router.get("/team-metrics/current-sprint-completion")
async def get_current_sprint_completion(
    team_name: str = Query(..., description="Team name to get completion rate for"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get current sprint completion rate for a specific team.
    
    Returns the percentage of completed issues in the current active sprint.
    
    Args:
        team_name: Name of the team
    
    Returns:
        JSON response with completion percentage
    """
    try:
        # Validate inputs
        validated_team_name = validate_team_name(team_name)
        
        # Get completion rate from database function
        completion_rate = get_team_current_sprint_completion(validated_team_name, conn)
        
        return {
            "success": True,
            "data": {
                "completion_rate": completion_rate,
                "team_name": validated_team_name
            },
            "message": f"Retrieved current sprint completion rate for team '{validated_team_name}'"
        }
    
    except HTTPException:
        raise  # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching current sprint completion for team {validated_team_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch current sprint completion: {str(e)}"
        )
