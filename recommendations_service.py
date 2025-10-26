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

@recommendations_router.get("/recommendations/getTop")
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
        
        # SECURE: Parameterized query prevents SQL injection
        query = text("""
            SELECT 
                id,
                team_name,
                date,
                action_text,
                rational,
                full_information,
                priority,
                status
            FROM public.recommendations
            WHERE team_name = :team_name
            ORDER BY 
                DATE(date) DESC,
                CASE priority 
                    WHEN 'High' THEN 1
                    WHEN 'Medium' THEN 2
                    WHEN 'Low' THEN 3
                    ELSE 4
                END,
                id DESC
            LIMIT :limit
        """)
        
        logger.info(f"Executing query to get top recommendations from {config.RECOMMENDATIONS_TABLE} for team: {validated_team_name}")
        logger.info(f"SQL Query: {query}")
        logger.info(f"Parameters: team_name={validated_team_name}, limit={validated_limit}")
        
        # Execute query with connection from dependency
        result = conn.execute(query, {
            'team_name': validated_team_name, 
            'limit': validated_limit
        })
        
        # Convert rows to list of dictionaries
        recommendations = []
        for row in result:
            recommendations.append({
                'id': row.id,
                'team_name': row.team_name,
                'date': row.date,
                'action_text': row.action_text,
                'rational': row.rational,
                'full_information': row.full_information,
                'priority': row.priority,
                'status': row.status
            })
        
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

