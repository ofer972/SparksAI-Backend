# FILE: recommendations_service.py
"""
Recommendations Service - Provides REST API endpoints for team recommendations
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
import logging
import re
from database_connection import get_db_connection
from database_general import (
    get_top_ai_recommendations,
    get_recommendation_by_id,
    create_recommendation,
    update_recommendation_by_id,
    delete_recommendation_by_id,
)
import config

logger = logging.getLogger(__name__)

recommendations_router = APIRouter()

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
    source_ai_summary_id: Optional[int] = Query(None, description="Optional: Filter by source AI summary ID"),
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
        source_ai_summary_id: Optional filter by source AI summary ID
    
    Returns:
        JSON response with recommendations list and metadata
    """
    try:
        # Validate inputs
        validated_team_name = validate_team_name(team_name)
        validated_limit = validate_limit(limit)
        
        # Get recommendations from database function
        recommendations = get_top_ai_recommendations(validated_team_name, validated_limit, source_ai_summary_id, conn)
        
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
    source_ai_summary_id: Optional[int] = Query(None, description="Optional: Filter by source AI summary ID"),
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
        source_ai_summary_id: Optional filter by source AI summary ID
    
    Returns:
        JSON response with recommendations list and metadata
    """
    try:
        # Validate inputs
        validated_pi_name = validate_team_name(pi)  # Reuse team_name validation for PI name
        validated_limit = validate_limit(limit)
        
        # Get recommendations from database function using PI name as team_name
        recommendations = get_top_ai_recommendations(validated_pi_name, validated_limit, source_ai_summary_id, conn)
        
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

@recommendations_router.get("/recommendations/collection")
async def get_recommendations_collection(
    date: Optional[str] = Query(None, description="Filter by date in YYYY-MM-DD format (e.g., '2025-12-05')"),
    team_name: Optional[str] = Query(None, description="Filter by team name"),
    limit: int = Query(100, description="Maximum number of recommendations to return (default: 100)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get all recommendations from recommendations table with ALL fields.
    Returns all columns from the table using SELECT *.
    Uses parameterized queries to prevent SQL injection.
    
    Recommendations are ordered by updated_at DESC (most recently updated first).
    
    Args:
        date: Optional date filter in YYYY-MM-DD format (e.g., '2025-12-05')
        team_name: Optional team name filter
        limit: Maximum number of recommendations to return (default: 100)
    
    Returns:
        JSON response with list of recommendations (all fields) and count
    """
    try:
        # Validate date format if provided
        if date:
            # Validate date format YYYY-MM-DD
            date_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}$')
            if not date_pattern.match(date):
                raise HTTPException(
                    status_code=400,
                    detail="Date must be in YYYY-MM-DD format (e.g., '2025-12-05')"
                )
            # Validate it's a valid date
            try:
                from datetime import datetime
                datetime.strptime(date, '%Y-%m-%d')
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid date: '{date}'. Date must be in YYYY-MM-DD format and be a valid date."
                )
        
        # Validate limit
        if limit < 1:
            raise HTTPException(status_code=400, detail="Limit must be at least 1")
        if limit > 1000:
            raise HTTPException(status_code=400, detail="Limit cannot exceed 1000")
        
        # Build WHERE clause dynamically based on provided parameters
        where_conditions = []
        params = {}
        
        if date:
            where_conditions.append("date = :date")
            params["date"] = date
        
        if team_name:
            validated_team_name = validate_team_name(team_name)
            where_conditions.append("team_name = :team_name")
            params["team_name"] = validated_team_name
        
        # Build WHERE clause
        where_clause = ""
        if where_conditions:
            where_clause = "WHERE " + " AND ".join(where_conditions)
        
        # SECURE: Parameterized query prevents SQL injection
        # Return ALL fields using SELECT *
        query = text(f"""
            SELECT *
            FROM {config.RECOMMENDATIONS_TABLE}
            {where_clause}
            ORDER BY updated_at DESC 
            LIMIT :limit
        """)
        params["limit"] = limit
        
        filter_info = []
        if date:
            filter_info.append(f"date={date}")
        if team_name:
            filter_info.append(f"team_name={team_name}")
        filter_str = f" with filters: {', '.join(filter_info)}" if filter_info else ""
        
        logger.info(f"Executing query to get all fields from recommendations from {config.RECOMMENDATIONS_TABLE}{filter_str}")
        
        # Execute query with connection from dependency
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries with all fields
        # Using _mapping to convert SQLAlchemy row to dictionary
        recommendations = []
        for row in rows:
            recommendation_dict = dict(row._mapping)
            recommendations.append(recommendation_dict)
        
        filter_message = ""
        if date or team_name:
            filter_parts = []
            if date:
                filter_parts.append(f"date={date}")
            if team_name:
                filter_parts.append(f"team_name='{team_name}'")
            filter_message = f" (filtered by: {', '.join(filter_parts)})"
        
        return {
            "success": True,
            "data": {
                "recommendations": recommendations,
                "count": len(recommendations)
            },
            "message": f"Retrieved {len(recommendations)} recommendations with all fields{filter_message}"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching recommendations with all fields: {e}")
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


# -----------------------
# Create/Update/Delete
# -----------------------

class RecommendationCreateRequest(BaseModel):
    team_name: str
    action_text: str
    date: Optional[str] = None
    rational: Optional[str] = None
    full_information: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    information_json: Optional[str] = None
    source_job_id: Optional[int] = None
    source_ai_summary_id: Optional[int] = None


class RecommendationUpdateRequest(BaseModel):
    team_name: Optional[str] = None
    action_text: Optional[str] = None
    date: Optional[str] = None
    rational: Optional[str] = None
    full_information: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    information_json: Optional[str] = None
    source_job_id: Optional[int] = None
    source_ai_summary_id: Optional[int] = None


@recommendations_router.post("/recommendations")
async def create_recommendation_endpoint(
    request: RecommendationCreateRequest,
    conn: Connection = Depends(get_db_connection)
):
    try:
        validated_team_name = validate_team_name(request.team_name)
        payload = request.model_dump()
        payload["team_name"] = validated_team_name

        created = create_recommendation(payload, conn)
        return {
            "success": True,
            "data": {"recommendation": created},
            "message": f"Recommendation created with ID {created.get('id')}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating recommendation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create recommendation: {str(e)}")


@recommendations_router.patch("/recommendations/{id}")
async def update_recommendation_endpoint(
    id: int,
    request: RecommendationUpdateRequest,
    conn: Connection = Depends(get_db_connection)
):
    try:
        updates = request.model_dump(exclude_unset=True)
        if "team_name" in updates and updates["team_name"] is not None:
            updates["team_name"] = validate_team_name(updates["team_name"])

        updated = update_recommendation_by_id(id, updates, conn)
        if not updated:
            raise HTTPException(status_code=404, detail=f"Recommendation with ID {id} not found")

        return {
            "success": True,
            "data": {"recommendation": updated},
            "message": f"Recommendation {id} updated successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating recommendation {id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update recommendation: {str(e)}")


@recommendations_router.delete("/recommendations/{id}")
async def delete_recommendation_endpoint(
    id: int,
    conn: Connection = Depends(get_db_connection)
):
    try:
        deleted = delete_recommendation_by_id(id, conn)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Recommendation with ID {id} not found")

        return {
            "success": True,
            "data": {"id": id},
            "message": f"Recommendation {id} deleted successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting recommendation {id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete recommendation: {str(e)}")

