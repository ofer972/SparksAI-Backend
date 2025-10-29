# FILE: recommendations_service.py
"""
Recommendations Service - Provides REST API endpoints for team recommendations
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
import logging
import re
from database_connection import get_db_connection
from database_general import get_top_ai_recommendations, get_recommendation_by_id
import config

logger = logging.getLogger(__name__)

recommendations_router = APIRouter()

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

def validate_limit(limit: int) -> int:
    """
    Validate limit parameter to prevent abuse.
    """
    if limit < 1:
        raise HTTPException(status_code=400, detail="Limit must be at least 1")
    
    if limit > 50:  # Reasonable upper limit
        raise HTTPException(status_code=400, detail="Limit cannot exceed 50")
    
    return limit

@recommendations_router.get("/recommendations/getTeamTop")
async def get_top_recommendations(
    team_name: str = Query(..., description="Team name to get recommendations for"),
    limit: int = Query(4, description="Number of recommendations to return (default: 4, max: 50)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get top recommendations for a specific team.
    
    Returns recommendations ordered by:
    1. Date (newest first)
    2. Priority (High > Medium > Low)
    3. ID (descending)
    
    Args:
        team_name: Name of the team
        limit: Number of recommendations to return (default: 4)
    
    Returns:
        JSON response with recommendations list and metadata
    """
    try:
        # Validate inputs
        validated_team_name = validate_team_name(team_name)
        validated_limit = validate_limit(limit)
        
        # Get recommendations from database function
        recommendations = get_top_ai_recommendations(validated_team_name, validated_limit, conn)
        
        return {
            "success": True,
            "data": {
                "recommendations": recommendations,
                "count": len(recommendations),
                "team_name": validated_team_name,
                "limit": validated_limit
            },
            "message": f"Retrieved {len(recommendations)} recommendations for team '{validated_team_name}'"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching recommendations for team {team_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch recommendations: {str(e)}"
        )

@recommendations_router.get("/recommendations/getPITop")
async def get_top_pi_recommendations(
    pi: str = Query(..., description="PI name to get recommendations for"),
    limit: int = Query(4, description="Number of recommendations to return (default: 4, max: 50)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get top recommendations for a specific PI.
    
    Returns recommendations ordered by:
    1. Date (newest first)
    2. Priority (High > Medium > Low)
    3. ID (descending)
    
    Args:
        pi: Name of the PI
        limit: Number of recommendations to return (default: 4)
    
    Returns:
        JSON response with recommendations list and metadata
    """
    try:
        # Validate inputs
        validated_pi_name = validate_team_name(pi)  # Reuse team_name validation for PI name
        validated_limit = validate_limit(limit)
        
        # Get recommendations from database function using PI name as team_name
        recommendations = get_top_ai_recommendations(validated_pi_name, validated_limit, conn)
        
        return {
            "success": True,
            "data": {
                "recommendations": recommendations,
                "count": len(recommendations),
                "pi": validated_pi_name,
                "limit": validated_limit
            },
            "message": f"Retrieved {len(recommendations)} recommendations for PI '{validated_pi_name}'"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching recommendations for PI {pi}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch recommendations: {str(e)}"
        )

@recommendations_router.get("/recommendations/{id}")
async def get_recommendation(id: int, conn: Connection = Depends(get_db_connection)):
    """
    Get a single recommendation by ID from recommendations table.
    Uses parameterized queries to prevent SQL injection.
    
    Args:
        id: The ID of the recommendation to retrieve
    
    Returns:
        JSON response with single recommendation or 404 if not found
    """
    try:
        # Use shared helper function from database_general
        recommendation = get_recommendation_by_id(id, conn)
        
        if not recommendation:
            raise HTTPException(
                status_code=404,
                detail=f"Recommendation with ID {id} not found"
            )
        
        return {
            "success": True,
            "data": {
                "recommendation": recommendation
            },
            "message": f"Retrieved recommendation with ID {id}"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (like the 404 error above)
        raise
    except Exception as e:
        logger.error(f"Error fetching recommendation {id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch recommendation: {str(e)}"
        )

