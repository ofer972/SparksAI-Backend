"""
PI AI Cards Service - REST API endpoints for PI AI summary card-related operations.

This service provides endpoints for managing and retrieving PI AI summary card information.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
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
    get_pi_ai_card_by_id,
    create_ai_card,
    update_ai_card_by_id,
    delete_ai_card_by_id,
)
import config

logger = logging.getLogger(__name__)

pi_ai_cards_router = APIRouter()

def validate_pi_name(pi_name: str) -> str:
    """
    Validate and sanitize PI name to prevent SQL injection.
    Only allows alphanumeric characters, spaces, hyphens, and underscores.
    """
    if not pi_name or not isinstance(pi_name, str):
        raise HTTPException(status_code=400, detail="PI name is required and must be a string")
    
    # Remove any potentially dangerous characters
    sanitized = re.sub(r'[^a-zA-Z0-9\s\-_]', '', pi_name.strip())
    
    if not sanitized:
        raise HTTPException(status_code=400, detail="PI name contains invalid characters")
    
    if len(sanitized) > 100:  # Reasonable length limit
        raise HTTPException(status_code=400, detail="PI name is too long (max 100 characters)")
    
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

@pi_ai_cards_router.get("/pi-ai-cards/getTopCards")
async def get_pi_ai_cards(
    pi: str = Query(..., description="PI name to get PI AI cards for"),
    limit: int = Query(4, description="Number of PI AI cards to return (default: 4, max: 50)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get PI AI summary cards for a specific PI.
    
    Returns the most recent + highest priority card for each type (max 1 per type).
    Cards are ordered by:
    1. Priority (Critical > High > Medium)
    2. Date (newest first)
    
    Args:
        pi: Name of the PI
        limit: Number of PI AI cards to return (default: 4)
    
    Returns:
        JSON response with PI AI cards list and metadata
    """
    try:
        # Validate inputs
        validated_pi_name = validate_pi_name(pi)
        validated_limit = validate_limit(limit)
        
        # Get PI AI cards using direct SQL query (filter by pi field)
        query = text(f"""
            WITH ranked_cards AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY card_type 
                        ORDER BY 
                            CASE priority 
                                WHEN 'Critical' THEN 1 
                                WHEN 'High' THEN 2 
                                WHEN 'Medium' THEN 3 
                                ELSE 4 
                            END,
                            date DESC
                    ) as rn
                FROM {config.PI_AI_CARDS_TABLE}
                WHERE pi = :pi_name
            )
            SELECT *
            FROM ranked_cards
            WHERE rn = 1
            ORDER BY 
                CASE priority 
                    WHEN 'Critical' THEN 1 
                    WHEN 'High' THEN 2 
                    WHEN 'Medium' THEN 3 
                    ELSE 4 
                END,
                date DESC
            LIMIT :limit
        """)
        
        logger.info(f"Executing query to get PI AI cards for PI '{validated_pi_name}'")
        
        result = conn.execute(query, {"pi_name": validated_pi_name, "limit": validated_limit})
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        ai_cards = []
        for row in rows:
            card_dict = dict(row._mapping)
            ai_cards.append(card_dict)
        
        return {
            "success": True,
            "data": {
                "ai_cards": ai_cards,
                "count": len(ai_cards),
                "pi": validated_pi_name,
                "limit": validated_limit
            },
            "message": f"Retrieved {len(ai_cards)} PI AI cards for PI '{validated_pi_name}'"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching PI AI cards for PI {pi}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch PI AI cards: {str(e)}"
        )

@pi_ai_cards_router.get("/pi-ai-cards")
async def get_pi_ai_cards_collection(conn: Connection = Depends(get_db_connection)):
    """
    Get the latest 100 PI AI summary cards from ai_summary table.
    Uses parameterized queries to prevent SQL injection.
    
    Returns:
        JSON response with list of PI AI cards and count
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
                card_type,
                priority,
                description,
                source,
                pi
            FROM {config.PI_AI_CARDS_TABLE}
            ORDER BY id DESC 
            LIMIT 100
        """)
        
        logger.info(f"Executing query to get latest 100 PI AI cards from {config.PI_AI_CARDS_TABLE}")
        
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
                "card_name": row[3],
                "card_type": row[4],
                "priority": row[5],
                "description": description_text,
                "source": row[7],
                "pi": row[8]
            }
            cards.append(card_dict)
        
        return {
            "success": True,
            "data": {
                "cards": cards,
                "count": len(cards)
            },
            "message": f"Retrieved {len(cards)} PI AI summary cards"
        }
    
    except Exception as e:
        logger.error(f"Error fetching PI AI cards: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch PI AI cards: {str(e)}"
        )

@pi_ai_cards_router.get("/pi-ai-cards/{id}")
async def get_pi_ai_card(id: int, conn: Connection = Depends(get_db_connection)):
    """
    Get a single PI AI summary card by ID from ai_summary table.
    Uses parameterized queries to prevent SQL injection.
    
    Args:
        id: The ID of the PI AI card to retrieve
    
    Returns:
        JSON response with single PI AI card or 404 if not found
    """
    try:
        card = get_pi_ai_card_by_id(id, conn)
        if not card:
            raise HTTPException(
                status_code=404,
                detail=f"PI AI card with ID {id} not found"
            )

        return {
            "success": True,
            "data": {
                "card": card
            },
            "message": f"Retrieved PI AI card with ID {id}"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (like the 404 error above)
        raise
    except Exception as e:
        logger.error(f"Error fetching PI AI card {id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch PI AI card: {str(e)}"
        )


# -----------------------
# Create/Update/Delete
# -----------------------

class PIAICardCreateRequest(BaseModel):
    pi: str
    card_name: str
    card_type: str
    description: str
    team_name: Optional[str] = None
    date: Optional[str] = None
    priority: Optional[str] = None
    source: Optional[str] = None
    source_job_id: Optional[int] = None
    full_information: Optional[str] = None
    information_json: Optional[str] = None


class PIAICardUpdateRequest(BaseModel):
    pi: Optional[str] = None
    team_name: Optional[str] = None
    card_name: Optional[str] = None
    card_type: Optional[str] = None
    description: Optional[str] = None
    date: Optional[str] = None
    priority: Optional[str] = None
    source: Optional[str] = None
    source_job_id: Optional[int] = None
    full_information: Optional[str] = None
    information_json: Optional[str] = None


@pi_ai_cards_router.post("/pi-ai-cards")
async def create_pi_ai_card(
    request: PIAICardCreateRequest,
    conn: Connection = Depends(get_db_connection)
):
    try:
        validated_pi = validate_pi_name(request.pi)
        payload = request.model_dump()
        payload["pi"] = validated_pi
        # Optional team_name can be sanitized using team rules if provided
        if payload.get("team_name") is not None:
            # Reuse same rules as team validation
            payload["team_name"] = re.sub(r'[^a-zA-Z0-9\s\-_]', '', payload["team_name"].strip())

        created = create_ai_card(payload, conn)
        return {
            "success": True,
            "data": {"card": created},
            "message": f"PI AI card created with ID {created.get('id')}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating PI AI card: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create PI AI card: {str(e)}")


@pi_ai_cards_router.patch("/pi-ai-cards/{id}")
async def update_pi_ai_card(
    id: int,
    request: PIAICardUpdateRequest,
    conn: Connection = Depends(get_db_connection)
):
    try:
        updates = request.model_dump(exclude_unset=True)
        if "pi" in updates and updates["pi"] is not None:
            updates["pi"] = validate_pi_name(updates["pi"])
        if "team_name" in updates and updates["team_name"] is not None:
            updates["team_name"] = re.sub(r'[^a-zA-Z0-9\s\-_]', '', updates["team_name"].strip())

        updated = update_ai_card_by_id(id, updates, conn)
        if not updated:
            raise HTTPException(status_code=404, detail=f"PI AI card with ID {id} not found")

        return {
            "success": True,
            "data": {"card": updated},
            "message": f"PI AI card {id} updated successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating PI AI card {id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update PI AI card: {str(e)}")


@pi_ai_cards_router.delete("/pi-ai-cards/{id}")
async def delete_pi_ai_card(
    id: int,
    conn: Connection = Depends(get_db_connection)
):
    try:
        deleted = delete_ai_card_by_id(id, conn)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"PI AI card with ID {id} not found")

        return {
            "success": True,
            "data": {"id": id},
            "message": f"PI AI card {id} deleted successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting PI AI card {id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete PI AI card: {str(e)}")
