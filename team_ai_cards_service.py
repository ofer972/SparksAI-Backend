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
    get_top_ai_cards_filtered,
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


def validate_group_name(group_name: str, conn: Connection) -> str:
    """
    Validate group name and check if it exists in the database.
    """
    if not group_name or not isinstance(group_name, str):
        raise HTTPException(status_code=400, detail="Group name is required and must be a string")
    
    validated = group_name.strip()
    
    if not validated:
        raise HTTPException(status_code=400, detail="Group name cannot be empty")
    
    if len(validated) > 100:  # Reasonable length limit
        raise HTTPException(status_code=400, detail="Group name is too long (max 100 characters)")
    
    # Check if group exists
    from groups_teams_cache import group_exists_by_name_in_db
    if not group_exists_by_name_in_db(validated, conn):
        raise HTTPException(status_code=404, detail=f"Group '{validated}' not found")
    
    return validated


def validate_team_name_or_group_name(name: str, is_group: bool, conn: Connection) -> str:
    """
    Validate team name or group name based on is_group flag.
    
    Args:
        name: Team name or group name
        is_group: If True, validate as group name; if False, validate as team name
        conn: Database connection
    
    Returns:
        Validated name
    """
    if is_group:
        return validate_group_name(name, conn)
    else:
        return validate_team_name(name)


def validate_team_or_group_name(team_name: Optional[str], group_name: Optional[str], conn: Connection) -> Dict[str, Optional[str]]:
    """
    Validate that exactly one of team_name or group_name is provided and validate it.
    
    Args:
        team_name: Optional team name
        group_name: Optional group name
        conn: Database connection
    
    Returns:
        Dict with validated 'team_name' and 'group_name' (one will be None)
    """
    # Check that exactly one is provided
    has_team = team_name is not None and isinstance(team_name, str) and team_name.strip() != ""
    has_group = group_name is not None and isinstance(group_name, str) and group_name.strip() != ""
    
    if not has_team and not has_group:
        raise HTTPException(status_code=400, detail="Either team_name or group_name must be provided")
    
    if has_team and has_group:
        raise HTTPException(status_code=400, detail="Cannot provide both team_name and group_name. Provide exactly one.")
    
    result: Dict[str, Optional[str]] = {"team_name": None, "group_name": None}
    
    if has_team:
        result["team_name"] = validate_team_name(team_name)
    else:
        result["group_name"] = validate_group_name(group_name, conn)
    
    return result

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
    team_name: str = Query(..., description="Team name or group name (if isGroup=true) to get AI cards for"),
    limit: int = Query(4, description="Number of AI cards to return (default: 4, max: 50)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get team AI summary cards for a specific team or group.
    
    Returns the most recent + highest priority card for each type (max 1 per type).
    Cards are ordered by:
    1. Priority (Critical > High > Medium)
    2. Date (newest first)
    
    Args:
        team_name: Name of the team or group (if isGroup=true)
        limit: Number of AI cards to return (default: 4)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with AI cards list and metadata
    """
    try:
        # Validate inputs
        validated_team_name = validate_team_name_or_group_name(team_name, isGroup, conn)
        validated_limit = validate_limit(limit)
        
        # Get AI cards from database function
        # Use 'group_name' filter column if isGroup=True, otherwise 'team_name'
        if isGroup:
            ai_cards = get_top_ai_cards_filtered('group_name', validated_team_name, validated_limit, categories=None, conn=conn)
        else:
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
    team_name: str = Query(..., description="Team name or group name (if isGroup=true) to get AI cards for"),
    limit: int = Query(4, description="Number of AI cards to return (default: 4, max: 50)"),
    recommendations_limit: int = Query(5, description="Max recommendations per card (default: 5)"),
    category: Optional[List[str]] = Query(None, description="Filter by insight category/categories (e.g., 'Daily', 'Planning'). Can specify multiple: ?category=Daily&category=Planning"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get team AI summary cards for a specific team or group with their recommendations.
    
    Returns the most recent + highest priority card for each type (max 1 per type),
    with recommendations linked via source_ai_summary_id.
    Cards are ordered by:
    1. Priority (Critical > High > Medium)
    2. Date (newest first)
    
    Args:
        team_name: Name of the team or group (if isGroup=true)
        limit: Number of AI cards to return (default: 4)
        recommendations_limit: Maximum recommendations per card (default: 5)
        category: Optional category filter(s) - only return cards with card_type matching insight types for any of these categories.
                 Can specify multiple: ?category=Daily&category=Planning
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with AI cards list (each with recommendations) and metadata
    """
    try:
        # Log isGroup parameter if received
        if isGroup:
            logger.info(f"isGroup parameter received: isGroup={isGroup}, team_name={team_name}")
        
        # Validate inputs
        validated_team_name = validate_team_name_or_group_name(team_name, isGroup, conn)
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
        # Use 'group_name' filter column if isGroup=True, otherwise 'team_name'
        filter_column = 'group_name' if isGroup else 'team_name'
        ai_cards = get_top_ai_cards_with_recommendations_filtered(
            filter_column,
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
                group_name,
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
            description_text = row[6]
            if isinstance(description_text, str) and len(description_text) > 200:
                description_text = description_text[:200] + "..."

            card_dict = {
                "id": row[0],
                "date": row[1],
                "team_name": row[2],
                "group_name": row[3],
                "card_name": row[4],
                "priority": row[5],
                "description": description_text,
                "source_job_id": row[7]
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
    team_name: Optional[str] = None
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
    group_name: Optional[str] = None


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
    group_name: Optional[str] = None


@team_ai_cards_router.post("/team-ai-cards")
async def create_team_ai_card(
    request: TeamAICardCreateRequest,
    conn: Connection = Depends(get_db_connection)
):
    try:
        # Validate that exactly one of team_name or group_name is provided
        validated = validate_team_or_group_name(request.team_name, request.group_name, conn)
        payload = request.model_dump()
        payload["team_name"] = validated["team_name"] if validated["team_name"] else ""
        payload["group_name"] = validated["group_name"]
        # If pi not provided, default to empty string (pi is NOT NULL in DB)
        if payload.get("pi") is None:
            payload["pi"] = ""
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
        
        # Validate team_name if provided
        if "team_name" in updates and updates["team_name"] is not None:
            updates["team_name"] = validate_team_name(updates["team_name"])
        
        # Validate group_name if provided
        if "group_name" in updates and updates["group_name"] is not None:
            updates["group_name"] = validate_group_name(updates["group_name"], conn)
        
        # Ensure at least one of team_name or group_name is provided if updating name-related fields
        # (This check is optional - we allow updating other fields without name)
        if "team_name" in updates and "group_name" in updates:
            if updates["team_name"] is not None and updates["group_name"] is not None:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot set both team_name and group_name. Provide exactly one."
                )

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

