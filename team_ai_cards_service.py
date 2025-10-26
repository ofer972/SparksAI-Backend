# FILE: team_ai_cards_service.py
"""
Team AI Cards Service - Provides REST API endpoints for team AI summary cards
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

@team_ai_cards_router.get("/team-ai-cards/getCards")
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
        
        # SECURE: Parameterized query prevents SQL injection
        query = text("""
            WITH ranked_cards AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY card_type 
                           ORDER BY 
                               date DESC,
                               CASE priority 
                                   WHEN 'Critical' THEN 1 
                                   WHEN 'High' THEN 2 
                                   WHEN 'Medium' THEN 3 
                               END
                       ) as rn
                FROM public.team_ai_summary_cards
                WHERE team_name = :team_name
            )
            SELECT id, date, team_name, card_name, card_type, priority, source, description, full_information
            FROM ranked_cards
            WHERE rn = 1
            ORDER BY 
                CASE priority 
                    WHEN 'Critical' THEN 1 
                    WHEN 'High' THEN 2 
                    WHEN 'Medium' THEN 3 
                END,
                date DESC
            LIMIT :limit
        """)
        
        logger.info(f"Executing query to get team AI cards from {config.TEAM_AI_CARDS_TABLE} for team: {validated_team_name}")
        
        # Execute query with connection from dependency
        result = conn.execute(query, {
            'team_name': validated_team_name, 
            'limit': validated_limit
        })
        
        # Convert rows to list of dictionaries
        ai_cards = []
        for row in result:
            ai_cards.append({
                'id': row[0],
                'date': row[1],
                'team_name': row[2],
                'card_name': row[3],
                'card_type': row[4],
                'priority': row[5],
                'source': row[6],
                'description': row[7],
                'full_information': row[8]
            })
        
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

