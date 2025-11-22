# FILE: team_ai_cards_service.py
"""
Team AI Cards Service - Provides REST API endpoints for team AI summary cards
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
    get_top_ai_cards,
    get_team_ai_card_by_id,
    create_ai_card,
    update_ai_card_by_id,
    delete_ai_card_by_id,
    get_top_ai_cards_with_recommendations_filtered,
)
from insight_types_service import get_insight_category_names
import config

logger = logging.getLogger(__name__)

team_ai_cards_router = APIRouter()

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

@team_ai_cards_router.get("/team-ai-cards/getTopCards")
async def get_team_ai_cards(
    team_name: str = Query(..., description="Team name to get AI cards for"),
    limit: int = Query(4, description="Number of AI cards to return (default: 4, max: 50)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get team AI summary cards for a specific team.
    
    Returns the most recent + highest priority card for each type (max 1 per type).
    Cards are ordered by:
    1. Priority (Critical > High > Medium)
    2. Date (newest first)
    
    Args:
        team_name: Name of the team
        limit: Number of AI cards to return (default: 4)
    
    Returns:
        JSON response with AI cards list and metadata
    """
    try:
        # Validate inputs
        validated_team_name = validate_team_name(team_name)
        validated_limit = validate_limit(limit)
        
        # Get AI cards from database function
        ai_cards = get_top_ai_cards(validated_team_name, validated_limit, conn)
        
        return {
            "success": True,
            "data": {
                "ai_cards": ai_cards,
                "count": len(ai_cards),
                "team_name": validated_team_name,
                "limit": validated_limit
            },
            "message": f"Retrieved {len(ai_cards)} AI cards for team '{validated_team_name}'"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching AI cards for team {team_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch AI cards: {str(e)}"
        )


@team_ai_cards_router.get("/team-ai-cards/getTopCardsWithRecommendations")
async def get_team_ai_cards_with_recommendations(
    team_name: str = Query(..., description="Team name to get AI cards for"),
    limit: int = Query(4, description="Number of AI cards to return (default: 4, max: 50)"),
    recommendations_limit: int = Query(5, description="Max recommendations per card (default: 5)"),
    category: Optional[List[str]] = Query(None, description="Filter by insight category/categories (e.g., 'Daily', 'Planning'). Can specify multiple: ?category=Daily&category=Planning"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get team AI summary cards for a specific team with their recommendations.
    
    Returns the most recent + highest priority card for each type (max 1 per type),
    with recommendations linked via source_ai_summary_id.
    Cards are ordered by:
    1. Priority (Critical > High > Medium)
    2. Date (newest first)
    
    Args:
        team_name: Name of the team
        limit: Number of AI cards to return (default: 4)
        recommendations_limit: Maximum recommendations per card (default: 5)
        category: Optional category filter(s) - only return cards with card_type matching insight types for any of these categories.
                 Can specify multiple: ?category=Daily&category=Planning
    
    Returns:
        JSON response with AI cards list (each with recommendations) and metadata
    """
    try:
        # Validate inputs
        validated_team_name = validate_team_name(team_name)
        validated_limit = validate_limit(limit)
        validated_recommendations_limit = validate_limit(recommendations_limit)
        
        # Validate categories if provided
        validated_categories = None
        if category:
            allowed_categories = get_insight_category_names()
            validated_categories = []
            seen = set()
            for cat in category:
                cat = cat.strip()
                if cat not in allowed_categories:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid insight category: '{cat}'. Allowed categories: {allowed_categories}"
                    )
                # Deduplicate categories
                if cat not in seen:
                    validated_categories.append(cat)
                    seen.add(cat)
            validated_categories = validated_categories if validated_categories else None
        
        # Get AI cards with recommendations using shared generic function
        ai_cards = get_top_ai_cards_with_recommendations_filtered(
            'team_name',
            validated_team_name,
            validated_limit,
            validated_recommendations_limit,
            validated_categories,
            conn
        )
        
        return {
            "success": True,
            "data": {
                "ai_cards": ai_cards,
                "count": len(ai_cards),
                "team_name": validated_team_name,
                "limit": validated_limit
            },
            "message": f"Retrieved {len(ai_cards)} AI cards with recommendations for team '{validated_team_name}'"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching AI cards with recommendations for team {team_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch AI cards with recommendations: {str(e)}"
        )


@team_ai_cards_router.get("/team-ai-cards")
async def get_team_ai_cards_collection(conn: Connection = Depends(get_db_connection)):
    """
    Get the latest 100 team AI summary cards from team_ai_summary_cards table.
    Uses parameterized queries to prevent SQL injection.
    
    Returns:
        JSON response with list of team AI cards and count
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        # Only return selected fields for the collection endpoint
        query = text(f"""
            SELECT 
                id,
                date,
                team_name,
                card_name,
                priority,
                description,
                source_job_id
            FROM {config.TEAM_AI_CARDS_TABLE}
            ORDER BY id DESC 
            LIMIT 100
        """)
        
        logger.info(f"Executing query to get latest 100 team AI cards from {config.TEAM_AI_CARDS_TABLE}")
        
        # Execute query with connection from dependency
        result = conn.execute(query)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        cards = []
        for row in rows:
            # Truncate description to first 200 characters with ellipsis when longer
            description_text = row[5]
            if isinstance(description_text, str) and len(description_text) > 200:
                description_text = description_text[:200] + "..."

            card_dict = {
                "id": row[0],
                "date": row[1],
                "team_name": row[2],
                "card_name": row[3],
                "priority": row[4],
                "description": description_text,
                "source_job_id": row[6]
            }
            cards.append(card_dict)
        
        return {
            "success": True,
            "data": {
                "cards": cards,
                "count": len(cards)
            },
            "message": f"Retrieved {len(cards)} team AI summary cards"
        }
    
    except Exception as e:
        logger.error(f"Error fetching team AI cards: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch team AI cards: {str(e)}"
        )

@team_ai_cards_router.get("/team-ai-cards/{id}")
async def get_team_ai_card(id: int, conn: Connection = Depends(get_db_connection)):
    """
    Get a single team AI summary card by ID from team_ai_summary_cards table.
    Uses parameterized queries to prevent SQL injection.
    
    Args:
        id: The ID of the team AI card to retrieve
    
    Returns:
        JSON response with single team AI card or 404 if not found
    """
    try:
        # Use shared helper function from database_general
        card = get_team_ai_card_by_id(id, conn)
        
        if not card:
            raise HTTPException(
                status_code=404,
                detail=f"Team AI card with ID {id} not found"
            )
        
        return {
            "success": True,
            "data": {
                "card": card
            },
            "message": f"Retrieved team AI card with ID {id}"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (like the 404 error above)
        raise
    except Exception as e:
        logger.error(f"Error fetching team AI card {id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch team AI card: {str(e)}"
        )


# -----------------------
# Create/Update/Delete
# -----------------------

class TeamAICardCreateRequest(BaseModel):
    team_name: str
    card_name: str
    card_type: str
    description: str
    date: Optional[str] = None
    priority: Optional[str] = None
    source: Optional[str] = None
    source_job_id: Optional[int] = None
    full_information: Optional[str] = None
    information_json: Optional[str] = None
    pi: Optional[str] = None


class TeamAICardUpdateRequest(BaseModel):
    team_name: Optional[str] = None
    pi: Optional[str] = None
    card_name: Optional[str] = None
    card_type: Optional[str] = None
    description: Optional[str] = None
    date: Optional[str] = None
    priority: Optional[str] = None
    source: Optional[str] = None
    source_job_id: Optional[int] = None
    full_information: Optional[str] = None
    information_json: Optional[str] = None


@team_ai_cards_router.post("/team-ai-cards")
async def create_team_ai_card(
    request: TeamAICardCreateRequest,
    conn: Connection = Depends(get_db_connection)
):
    try:
        validated_team_name = validate_team_name(request.team_name)
        payload = request.model_dump()
        payload["team_name"] = validated_team_name
        # If pi not provided, default to None (will be null in DB)
        created = create_ai_card(payload, conn)
        return {
            "success": True,
            "data": {"card": created},
            "message": f"Team AI card created with ID {created.get('id')}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating team AI card: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create team AI card: {str(e)}")


@team_ai_cards_router.patch("/team-ai-cards/{id}")
async def update_team_ai_card(
    id: int,
    request: TeamAICardUpdateRequest,
    conn: Connection = Depends(get_db_connection)
):
    try:
        updates = request.model_dump(exclude_unset=True)
        if "team_name" in updates and updates["team_name"] is not None:
            updates["team_name"] = validate_team_name(updates["team_name"])

        updated = update_ai_card_by_id(id, updates, conn)
        if not updated:
            raise HTTPException(status_code=404, detail=f"Team AI card with ID {id} not found")

        return {
            "success": True,
            "data": {"card": updated},
            "message": f"Team AI card {id} updated successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating team AI card {id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update team AI card: {str(e)}")


@team_ai_cards_router.delete("/team-ai-cards/{id}")
async def delete_team_ai_card(
    id: int,
    conn: Connection = Depends(get_db_connection)
):
    try:
        deleted = delete_ai_card_by_id(id, conn)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Team AI card with ID {id} not found")

        return {
            "success": True,
            "data": {"id": id},
            "message": f"Team AI card {id} deleted successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting team AI card {id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete team AI card: {str(e)}")

