"""
AI Insights Service - Unified REST API endpoints for AI insights (Team, Group, and PI).

This service provides unified endpoints for managing and retrieving AI summary cards
for teams, groups, and PIs. Uses FastAPI dependencies for clean connection management
and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
from enum import Enum
import logging
import re
from database_connection import get_db_connection
from database_general import (
    get_top_ai_cards,
    get_top_ai_cards_filtered,
    get_ai_card_by_id,
    create_ai_card,
    update_ai_card_by_id,
    delete_ai_card_by_id,
    get_top_ai_cards_with_recommendations_from_json,
)
from insight_types_service import get_insight_category_names
import config

logger = logging.getLogger(__name__)

ai_insights_router = APIRouter()


class InsightType(str, Enum):
    """Enum for insight types."""
    TEAM = "team"
    GROUP = "group"
    PI = "pi"


def validate_team_name(team_name: str) -> str:
    """Validate team name (basic validation only)."""
    if not team_name or not isinstance(team_name, str):
        raise HTTPException(status_code=400, detail="Team name is required and must be a string")
    
    validated = team_name.strip()
    
    if not validated:
        raise HTTPException(status_code=400, detail="Team name cannot be empty")
    
    if len(validated) > 100:
        raise HTTPException(status_code=400, detail="Team name is too long (max 100 characters)")
    
    return validated


def validate_group_name(group_name: str, conn: Connection) -> str:
    """Validate group name and check if it exists in the database."""
    if not group_name or not isinstance(group_name, str):
        raise HTTPException(status_code=400, detail="Group name is required and must be a string")
    
    validated = group_name.strip()
    
    if not validated:
        raise HTTPException(status_code=400, detail="Group name cannot be empty")
    
    if len(validated) > 100:
        raise HTTPException(status_code=400, detail="Group name is too long (max 100 characters)")
    
    # Check if group exists
    from groups_teams_cache import group_exists_by_name_in_db
    if not group_exists_by_name_in_db(validated, conn):
        raise HTTPException(status_code=404, detail=f"Group '{validated}' not found")
    
    return validated


def validate_pi_name(pi_name: str) -> str:
    """Validate and sanitize PI name to prevent SQL injection."""
    if not pi_name or not isinstance(pi_name, str):
        raise HTTPException(status_code=400, detail="PI name is required and must be a string")
    
    # Remove any potentially dangerous characters
    sanitized = re.sub(r'[^a-zA-Z0-9\s\-_]', '', pi_name.strip())
    
    if not sanitized:
        raise HTTPException(status_code=400, detail="PI name contains invalid characters")
    
    if len(sanitized) > 100:
        raise HTTPException(status_code=400, detail="PI name is too long (max 100 characters)")
    
    return sanitized


def validate_insight_identifier(insight_type: str, identifier: str, conn: Connection) -> str:
    """Validate identifier based on insight type."""
    if insight_type == InsightType.TEAM:
        return validate_team_name(identifier)
    elif insight_type == InsightType.GROUP:
        return validate_group_name(identifier, conn)
    elif insight_type == InsightType.PI:
        return validate_pi_name(identifier)
    else:
        raise HTTPException(status_code=400, detail=f"Invalid insight_type: {insight_type}. Must be 'team', 'group', or 'pi'")


def validate_limit(limit: int) -> int:
    """Validate limit parameter to prevent abuse."""
    if limit < 1:
        raise HTTPException(status_code=400, detail="Limit must be at least 1")
    
    if limit > 50:  # Reasonable upper limit for getTopCards endpoints
        raise HTTPException(status_code=400, detail="Limit cannot exceed 50")
    
    return limit


def validate_limit_large(limit: int) -> int:
    """Validate limit parameter for collection endpoints (allows larger values)."""
    if limit < 1:
        raise HTTPException(status_code=400, detail="Limit must be at least 1")
    
    if limit > 1000:
        raise HTTPException(status_code=400, detail="Limit cannot exceed 1000")
    
    return limit


@ai_insights_router.get("/ai-insights/getTopCards")
async def get_ai_insights(
    insight_type: str = Query(..., description="Type of insight: 'team', 'group', or 'pi'"),
    team_name: Optional[str] = Query(None, description="Team name (required if insight_type='team')"),
    group_name: Optional[str] = Query(None, description="Group name (required if insight_type='group')"),
    pi: Optional[str] = Query(None, description="PI name (required if insight_type='pi')"),
    limit: int = Query(4, description="Number of AI cards to return (default: 4, max: 50)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get AI summary cards for a specific team, group, or PI.
    
    Returns the most recent + highest priority card for each type (max 1 per type).
    Cards are ordered by:
    1. Priority (Critical > High > Medium)
    2. Date (newest first)
    
    Args:
        insight_type: Type of insight - 'team', 'group', or 'pi'
        team_name: Team name (required if insight_type='team')
        group_name: Group name (required if insight_type='group')
        pi: PI name (required if insight_type='pi')
        limit: Number of AI cards to return (default: 4)
    
    Returns:
        JSON response with AI cards list and metadata
    """
    try:
        # Validate insight_type
        if insight_type not in [InsightType.TEAM, InsightType.GROUP, InsightType.PI]:
            raise HTTPException(status_code=400, detail=f"Invalid insight_type: {insight_type}. Must be 'team', 'group', or 'pi'")
        
        # Determine identifier based on insight_type
        if insight_type == InsightType.TEAM:
            if not team_name:
                raise HTTPException(status_code=400, detail="team_name is required when insight_type='team'")
            identifier = validate_team_name(team_name)
            filter_column = 'team_name'
        elif insight_type == InsightType.GROUP:
            if not group_name:
                raise HTTPException(status_code=400, detail="group_name is required when insight_type='group'")
            identifier = validate_group_name(group_name, conn)
            filter_column = 'group_name'
        else:  # PI
            if not pi:
                raise HTTPException(status_code=400, detail="pi is required when insight_type='pi'")
            identifier = validate_pi_name(pi)
            filter_column = 'pi'
        
        validated_limit = validate_limit(limit)
        
        # Get AI cards from database function
        if insight_type == InsightType.TEAM:
            # Use get_top_ai_cards for team (no filter needed, uses team_name directly)
            ai_cards = get_top_ai_cards(identifier, validated_limit, conn)
        else:
            # Use get_top_ai_cards_filtered for group and PI
            ai_cards = get_top_ai_cards_filtered(filter_column, identifier, validated_limit, categories=None, conn=conn)
        
        # Build response identifier name
        identifier_key = 'team_name' if insight_type == InsightType.TEAM else ('group_name' if insight_type == InsightType.GROUP else 'pi')
        
        return {
            "success": True,
            "data": {
                "ai_cards": ai_cards,
                "count": len(ai_cards),
                insight_type: identifier,
                identifier_key: identifier,
                "limit": validated_limit
            },
            "message": f"Retrieved {len(ai_cards)} AI cards for {insight_type} '{identifier}'"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching AI cards for {insight_type}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch AI cards: {str(e)}"
        )


@ai_insights_router.get("/ai-insights/getTopCardsWithRecommendations")
async def get_ai_insights_with_recommendations(
        insight_type: Optional[str] = Query(None, description="Type of insight: 'team', 'group', or 'pi'. Optional - mode is determined from parameters. Only used for backward compatibility."),
    team_name: Optional[str] = Query(None, description="Team name"),
    group_name: Optional[str] = Query(None, description="Group name"),
    pi: Optional[str] = Query(None, description="PI name (quarter)"),
    limit: int = Query(4, description="Number of AI cards to return (default: 4, max: 50)"),
    recommendations_limit: int = Query(5, description="Max recommendations per card (default: 5)"),
    category: Optional[List[str]] = Query(None, description="Filter by insight category/categories (e.g., 'PI Events', 'Sprint Status'). Can specify multiple: ?category=PI Events&category=Sprint Status"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get AI summary cards with their recommendations.
    
    Supports multiple filter combinations:
    - PI only: pi=<PI_NAME>
    - PI + Team: pi=<PI_NAME>&team_name=<TEAM_NAME>
    - PI + Group: pi=<PI_NAME>&group_name=<GROUP_NAME>
    - Team only: team_name=<TEAM_NAME>
    - Group only: group_name=<GROUP_NAME>
    
    Note: insight_type parameter is optional and only used for backward compatibility.
    The mode is automatically determined from the provided parameters.
    
    Returns the most recent + highest priority card for each type (max 1 per type),
    with recommendations parsed from the card's information_json field.
    Cards are ordered by:
    1. Priority (Critical > High > Medium)
    2. Date (newest first)
    
    Args:
        insight_type: Type of insight - 'team', 'group', or 'pi' (optional if pi is provided)
        team_name: Team name
        group_name: Group name
        pi: PI name (quarter)
        limit: Number of AI cards to return (default: 4)
        recommendations_limit: Maximum recommendations per card (default: 5)
        category: Optional category filter(s) - PI Events, PI Status, Sprint Status, Sprint Events
    
    Returns:
        JSON response with AI cards list (each with recommendations) and metadata
    """
    try:
        # Determine mode from parameters (insight_type is optional and only used for backward compatibility)
        # Priority: If pi is provided, use multi-filter mode; otherwise infer from team_name/group_name
        
        validated_pi = None
        validated_team_name = None
        validated_group_name = None
        
        # Validate and set PI if provided
        if pi:
            validated_pi = validate_pi_name(pi)
        
        # Validate and set team_name if provided
        if team_name:
            validated_team_name = validate_team_name(team_name)
        
        # Validate and set group_name if provided
        if group_name:
            validated_group_name = validate_group_name(group_name, conn)
        
        # Validate that we don't have both team and group
        if validated_team_name and validated_group_name:
            raise HTTPException(status_code=400, detail="Cannot specify both team_name and group_name")
        
        # Ensure at least one filter is provided
        if not validated_pi and not validated_team_name and not validated_group_name:
            raise HTTPException(
                status_code=400, 
                detail="At least one filter must be provided: pi, team_name, or group_name"
            )
        
        # Handle legacy insight_type parameter for backward compatibility
        # If insight_type is provided, it should match the parameters (but we don't require it)
        if insight_type:
            if insight_type not in [InsightType.TEAM, InsightType.GROUP, InsightType.PI]:
                raise HTTPException(status_code=400, detail=f"Invalid insight_type: {insight_type}. Must be 'team', 'group', or 'pi'")
            
            # Validate that insight_type matches the provided parameters
            if insight_type == InsightType.TEAM and not validated_team_name:
                raise HTTPException(status_code=400, detail="insight_type='team' requires team_name parameter")
            if insight_type == InsightType.GROUP and not validated_group_name:
                raise HTTPException(status_code=400, detail="insight_type='group' requires group_name parameter")
            if insight_type == InsightType.PI and not validated_pi:
                raise HTTPException(status_code=400, detail="insight_type='pi' requires pi parameter")
        
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
        
        # Get AI cards with recommendations from JSON
        # Limit recommendations to max 3 as per requirements
        recommendations_limit = min(validated_recommendations_limit, 3)
        
        # Always use multi-filter mode (supports all combinations)
        ai_cards = get_top_ai_cards_with_recommendations_from_json(
            pi=validated_pi,
            team_name=validated_team_name,
            group_name=validated_group_name,
            limit=validated_limit,
            recommendations_limit=recommendations_limit,
            categories=validated_categories,
            conn=conn
        )
        
        # Build response with all provided filters
        response_data = {
            "ai_cards": ai_cards,
            "count": len(ai_cards),
            "limit": validated_limit
        }
        if validated_pi:
            response_data["pi"] = validated_pi
        if validated_team_name:
            response_data["team_name"] = validated_team_name
        if validated_group_name:
            response_data["group_name"] = validated_group_name
        
        # Build message
        message_parts = []
        if validated_pi:
            message_parts.append(f"PI '{validated_pi}'")
        if validated_team_name:
            message_parts.append(f"team '{validated_team_name}'")
        if validated_group_name:
            message_parts.append(f"group '{validated_group_name}'")
        message = f"Retrieved {len(ai_cards)} AI cards with recommendations for {', '.join(message_parts)}"
        
        # For backward compatibility, also include insight_type in response if it was provided
        if insight_type:
            identifier = validated_pi or validated_team_name or validated_group_name
            identifier_key = 'team_name' if insight_type == InsightType.TEAM else ('group_name' if insight_type == InsightType.GROUP else 'pi')
            response_data[insight_type] = identifier
            response_data[identifier_key] = identifier
        
        return {
            "success": True,
            "data": response_data,
            "message": message
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching AI cards with recommendations: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch AI cards with recommendations: {str(e)}"
        )


@ai_insights_router.get("/ai-insights")
async def get_ai_insights_collection(
    insight_type: Optional[str] = Query(None, description="Filter by insight type: 'team', 'group', or 'pi'"),
    date: Optional[str] = Query(None, description="Filter by date in YYYY-MM-DD format (e.g., '2025-12-05')"),
    card_name: Optional[str] = Query(None, description="Filter by exact card name"),
    team_name: Optional[str] = Query(None, description="Filter by team name"),
    group_name: Optional[str] = Query(None, description="Filter by group name"),
    pi: Optional[str] = Query(None, description="Filter by PI name"),
    limit: int = Query(100, description="Maximum number of cards to return (default: 100)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get AI summary cards collection with optional filtering.
    Returns the latest cards by default, or filtered results if parameters are provided.
    Uses parameterized queries to prevent SQL injection.
    
    Args:
        insight_type: Optional filter by type ('team', 'group', or 'pi')
        date: Optional date filter in YYYY-MM-DD format (e.g., '2025-12-05')
        card_name: Optional card name filter (exact match)
        team_name: Optional team name filter
        group_name: Optional group name filter
        pi: Optional PI name filter
        limit: Maximum number of cards to return (default: 100)
    
    Returns:
        JSON response with list of AI cards and count
    """
    try:
        # Validate date format if provided
        if date:
            date_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}$')
            if not date_pattern.match(date):
                raise HTTPException(
                    status_code=400,
                    detail="Date must be in YYYY-MM-DD format (e.g., '2025-12-05')"
                )
            try:
                from datetime import datetime
                datetime.strptime(date, '%Y-%m-%d')
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid date: '{date}'. Date must be in YYYY-MM-DD format and be a valid date."
                )
        
        # Validate insight_type if provided
        if insight_type and insight_type not in [InsightType.TEAM, InsightType.GROUP, InsightType.PI]:
            raise HTTPException(status_code=400, detail=f"Invalid insight_type: {insight_type}. Must be 'team', 'group', or 'pi'")
        
        validated_limit = validate_limit_large(limit)
        
        # Build WHERE clause dynamically
        where_conditions = []
        params = {}
        
        # Filter by insight_type
        if insight_type == InsightType.TEAM:
            where_conditions.append("(team_name IS NOT NULL AND (group_name IS NULL OR group_name = '') AND (pi IS NULL OR pi = ''))")
        elif insight_type == InsightType.GROUP:
            where_conditions.append("(group_name IS NOT NULL AND group_name != '')")
        elif insight_type == InsightType.PI:
            where_conditions.append("(pi IS NOT NULL AND pi != '')")
        else:
            # No insight_type filter - return all cards (but exclude cards with all NULL)
            where_conditions.append("(team_name IS NOT NULL OR group_name IS NOT NULL OR pi IS NOT NULL)")
        
        if date:
            where_conditions.append("date = :date")
            params["date"] = date
        
        if card_name:
            where_conditions.append("card_name = :card_name")
            params["card_name"] = card_name
        
        if team_name:
            validated_team = validate_team_name(team_name)
            where_conditions.append("team_name = :team_name")
            params["team_name"] = validated_team
        
        if group_name:
            validated_group = validate_group_name(group_name, conn)
            where_conditions.append("group_name = :group_name")
            params["group_name"] = validated_group
        
        if pi:
            validated_pi = validate_pi_name(pi)
            where_conditions.append("pi = :pi")
            params["pi"] = validated_pi
        
        # Build WHERE clause
        where_clause = ""
        if where_conditions:
            where_clause = "WHERE " + " AND ".join(where_conditions)
        
        # SECURE: Parameterized query prevents SQL injection
        # Return all fields including updated_at, created_at, and insight_type
        query = text(f"""
            SELECT 
                id,
                date,
                team_name,
                group_name,
                card_name,
                priority,
                description,
                source_job_id,
                pi,
                insight_type,
                updated_at,
                created_at
            FROM {config.AI_INSIGHTS_TABLE}
            {where_clause}
            ORDER BY id DESC 
            LIMIT :limit
        """)
        params["limit"] = validated_limit
        
        filter_info = []
        if insight_type:
            filter_info.append(f"insight_type={insight_type}")
        if date:
            filter_info.append(f"date={date}")
        if card_name:
            filter_info.append(f"card_name={card_name}")
        if team_name:
            filter_info.append(f"team_name={team_name}")
        if group_name:
            filter_info.append(f"group_name={group_name}")
        if pi:
            filter_info.append(f"pi={pi}")
        filter_str = f" with filters: {', '.join(filter_info)}" if filter_info else ""
        
        logger.info(f"Executing query to get AI insights collection from {config.AI_INSIGHTS_TABLE}{filter_str}")
        
        # Execute query
        result = conn.execute(query, params)
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
                "source_job_id": row[7],
                "pi": row[8] if len(row) > 8 else None,
                "insight_type": row[9] if len(row) > 9 else None,
                "updated_at": row[10] if len(row) > 10 else None,
                "created_at": row[11] if len(row) > 11 else None
            }
            cards.append(card_dict)
        
        filter_message = ""
        if filter_info:
            filter_message = f" (filtered by: {', '.join(filter_info)})"
        
        return {
            "success": True,
            "data": {
                "cards": cards,
                "count": len(cards)
            },
            "message": f"Retrieved {len(cards)} AI insight cards{filter_message}"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching AI insights collection: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch AI insights: {str(e)}"
        )


@ai_insights_router.get("/ai-insights/getAllFields")
async def get_ai_insights_all_fields(
    insight_type: Optional[str] = Query(None, description="Filter by insight type: 'team', 'group', or 'pi'"),
    date: Optional[str] = Query(None, description="Filter by date in YYYY-MM-DD format (e.g., '2025-12-05')"),
    card_name: Optional[str] = Query(None, description="Filter by exact card name"),
    team_name: Optional[str] = Query(None, description="Filter by team name"),
    group_name: Optional[str] = Query(None, description="Filter by group name"),
    pi: Optional[str] = Query(None, description="Filter by PI name"),
    limit: int = Query(100, description="Maximum number of cards to return (default: 100)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get AI summary cards with ALL fields.
    Returns all columns from the table using SELECT *.
    Uses parameterized queries to prevent SQL injection.
    
    Args:
        insight_type: Optional filter by type ('team', 'group', or 'pi')
        date: Optional date filter in YYYY-MM-DD format (e.g., '2025-12-05')
        card_name: Optional card name filter (exact match)
        team_name: Optional team name filter
        group_name: Optional group name filter
        pi: Optional PI name filter
        limit: Maximum number of cards to return (default: 100)
    
    Returns:
        JSON response with list of AI cards (all fields) and count
    """
    try:
        # Validate date format if provided
        if date:
            date_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}$')
            if not date_pattern.match(date):
                raise HTTPException(
                    status_code=400,
                    detail="Date must be in YYYY-MM-DD format (e.g., '2025-12-05')"
                )
            try:
                from datetime import datetime
                datetime.strptime(date, '%Y-%m-%d')
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid date: '{date}'. Date must be in YYYY-MM-DD format and be a valid date."
                )
        
        # Validate insight_type if provided
        if insight_type and insight_type not in [InsightType.TEAM, InsightType.GROUP, InsightType.PI]:
            raise HTTPException(status_code=400, detail=f"Invalid insight_type: {insight_type}. Must be 'team', 'group', or 'pi'")
        
        validated_limit = validate_limit_large(limit)
        
        # Build WHERE clause dynamically
        where_conditions = []
        params = {}
        
        # Filter by insight_type
        if insight_type == InsightType.TEAM:
            where_conditions.append("(team_name IS NOT NULL AND (group_name IS NULL OR group_name = '') AND (pi IS NULL OR pi = ''))")
        elif insight_type == InsightType.GROUP:
            where_conditions.append("(group_name IS NOT NULL AND group_name != '')")
        elif insight_type == InsightType.PI:
            where_conditions.append("(pi IS NOT NULL AND pi != '')")
        else:
            # No insight_type filter - return all cards
            where_conditions.append("(team_name IS NOT NULL OR group_name IS NOT NULL OR pi IS NOT NULL)")
        
        if date:
            where_conditions.append("date = :date")
            params["date"] = date
        
        if card_name:
            where_conditions.append("card_name = :card_name")
            params["card_name"] = card_name
        
        if team_name:
            validated_team = validate_team_name(team_name)
            where_conditions.append("team_name = :team_name")
            params["team_name"] = validated_team
        
        if group_name:
            validated_group = validate_group_name(group_name, conn)
            where_conditions.append("group_name = :group_name")
            params["group_name"] = validated_group
        
        if pi:
            validated_pi = validate_pi_name(pi)
            where_conditions.append("pi = :pi")
            params["pi"] = validated_pi
        
        # Build WHERE clause
        where_clause = ""
        if where_conditions:
            where_clause = "WHERE " + " AND ".join(where_conditions)
        
        # SECURE: Parameterized query prevents SQL injection
        # Return ALL fields using SELECT *
        query = text(f"""
            SELECT *
            FROM {config.AI_INSIGHTS_TABLE}
            {where_clause}
            ORDER BY id DESC 
            LIMIT :limit
        """)
        params["limit"] = validated_limit
        
        filter_info = []
        if insight_type:
            filter_info.append(f"insight_type={insight_type}")
        if date:
            filter_info.append(f"date={date}")
        if card_name:
            filter_info.append(f"card_name={card_name}")
        if team_name:
            filter_info.append(f"team_name={team_name}")
        if group_name:
            filter_info.append(f"group_name={group_name}")
        if pi:
            filter_info.append(f"pi={pi}")
        filter_str = f" with filters: {', '.join(filter_info)}" if filter_info else ""
        
        logger.info(f"Executing query to get all fields from AI insights from {config.AI_INSIGHTS_TABLE}{filter_str}")
        
        # Execute query
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries with all fields
        cards = []
        for row in rows:
            card_dict = dict(row._mapping)
            cards.append(card_dict)
        
        filter_message = ""
        if filter_info:
            filter_message = f" (filtered by: {', '.join(filter_info)})"
        
        return {
            "success": True,
            "data": {
                "cards": cards,
                "count": len(cards)
            },
            "message": f"Retrieved {len(cards)} AI insight cards with all fields{filter_message}"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching AI insights with all fields: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch AI insights: {str(e)}"
        )


@ai_insights_router.get("/ai-insights/{id}")
async def get_ai_insight(id: int, conn: Connection = Depends(get_db_connection)):
    """
    Get a single AI summary card by ID.
    Uses parameterized queries to prevent SQL injection.
    
    Args:
        id: The ID of the AI card to retrieve
    
    Returns:
        JSON response with single AI card or 404 if not found
    """
    try:
        card = get_ai_card_by_id(id, conn)
        
        if not card:
            raise HTTPException(
                status_code=404,
                detail=f"AI insight card with ID {id} not found"
            )
        
        return {
            "success": True,
            "data": {
                "card": card
            },
            "message": f"Retrieved AI insight card with ID {id}"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching AI insight card {id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch AI insight card: {str(e)}"
        )


# -----------------------
# Create/Update/Delete
# -----------------------

class AIInsightCreateRequest(BaseModel):
    insight_type: str  # Insight type name (e.g., "Daily Progress", "PI Sync") - matches insight_types.insight_type
    team_name: Optional[str] = None
    group_name: Optional[str] = None
    pi: Optional[str] = None
    card_name: str
    description: str
    date: Optional[str] = None
    priority: Optional[str] = None
    source: Optional[str] = None
    source_job_id: Optional[int] = None
    full_information: Optional[str] = None
    information_json: Optional[str] = None


class AIInsightUpdateRequest(BaseModel):
    team_name: Optional[str] = None
    group_name: Optional[str] = None
    pi: Optional[str] = None
    card_name: Optional[str] = None
    insight_type: Optional[str] = None
    description: Optional[str] = None
    date: Optional[str] = None
    priority: Optional[str] = None
    source: Optional[str] = None
    source_job_id: Optional[int] = None
    full_information: Optional[str] = None
    information_json: Optional[str] = None


@ai_insights_router.post("/ai-insights")
async def create_ai_insight(
    request: AIInsightCreateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """Create a new AI insight card."""
    try:
        # Validate that team_name and group_name are not both set
        if request.team_name and request.group_name:
            raise HTTPException(status_code=400, detail="team_name and group_name cannot both be set. Only one identifier can be used.")
        
        # Validate that at least one identifier is provided
        if not request.team_name and not request.group_name and not request.pi:
            raise HTTPException(status_code=400, detail="At least one identifier (team_name, group_name, or pi) must be provided")
        
        # Create payload from request
        payload = request.model_dump()
        
        # Validate and normalize identifier fields
        if request.team_name:
            payload["team_name"] = validate_team_name(request.team_name)
        if request.group_name:
            payload["group_name"] = validate_group_name(request.group_name, conn)
        if request.pi:
            payload["pi"] = validate_pi_name(request.pi)
        
        # Normalize empty strings to None
        if payload.get("team_name") == "":
            payload["team_name"] = None
        if payload.get("group_name") == "":
            payload["group_name"] = None
        if payload.get("pi") == "":
            payload["pi"] = None
        
        created = create_ai_card(payload, conn)
        return {
            "success": True,
            "data": {"card": created},
            "message": f"AI insight card created with ID {created.get('id')}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating AI insight card: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create AI insight card: {str(e)}")


@ai_insights_router.patch("/ai-insights/{id}")
async def update_ai_insight(
    id: int,
    request: AIInsightUpdateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """Update an existing AI insight card."""
    try:
        updates = request.model_dump(exclude_unset=True)
        
        # Validate team_name if provided
        if "team_name" in updates and updates["team_name"] is not None:
            updates["team_name"] = validate_team_name(updates["team_name"])
        
        # Validate group_name if provided
        if "group_name" in updates and updates["group_name"] is not None:
            updates["group_name"] = validate_group_name(updates["group_name"], conn)
        
        # Validate pi if provided
        if "pi" in updates and updates["pi"] is not None:
            updates["pi"] = validate_pi_name(updates["pi"])
        
        # Normalize empty strings to None
        if "team_name" in updates and updates["team_name"] == "":
            updates["team_name"] = None
        if "group_name" in updates and updates["group_name"] == "":
            updates["group_name"] = None
        if "pi" in updates and updates["pi"] == "":
            updates["pi"] = None
        
        # Validate that team_name and group_name are not both set (same as CREATE endpoint)
        if updates.get("team_name") and updates.get("group_name"):
            raise HTTPException(
                status_code=400,
                detail="team_name and group_name cannot both be set. Only one identifier can be used."
            )
        
        updated = update_ai_card_by_id(id, updates, conn)
        if not updated:
            raise HTTPException(status_code=404, detail=f"AI insight card with ID {id} not found")
        
        return {
            "success": True,
            "data": {"card": updated},
            "message": f"AI insight card {id} updated successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating AI insight card {id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update AI insight card: {str(e)}")


@ai_insights_router.delete("/ai-insights/{id}")
async def delete_ai_insight(
    id: int,
    conn: Connection = Depends(get_db_connection)
):
    """Delete an AI insight card."""
    try:
        deleted = delete_ai_card_by_id(id, conn)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"AI insight card with ID {id} not found")
        
        return {
            "success": True,
            "data": {"id": id},
            "message": f"AI insight card {id} deleted successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting AI insight card {id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete AI insight card: {str(e)}")

