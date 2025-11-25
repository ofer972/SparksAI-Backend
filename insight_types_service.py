"""
Insight Types Service - REST API endpoints for insight type-related operations.

This service provides endpoints for managing and retrieving insight type information.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
import logging
import re
from database_connection import get_db_connection
from database_general import (
    get_insight_type_by_id,
    get_insight_types,
    create_insight_type,
    update_insight_type_by_id,
    delete_insight_type_by_id,
)
import config

logger = logging.getLogger(__name__)

insight_types_router = APIRouter()

# Fixed list of insight categories - available for import by other services
# Each category has a name and a class (Team or PI)
INSIGHT_CATEGORIES = [
    {"name": "Daily", "class": "Team"},
    {"name": "Planning", "class": "Team"},
    {"name": "Retrospective", "class": "Team"},
    {"name": "Sprint Review", "class": "Team"},
    {"name": "Backlog Refinement", "class": "Team"},
    {"name": "PI Sync", "class": "Team"},
    {"name": "PI Retrospective-Train", "class": "PI"},
    {"name": "PI Sync-Train", "class": "PI"},
    {"name": "PI Planning-Train", "class": "PI"},
    {"name": "PI Preparation-Train", "class": "PI"},
    {"name": "PI Dependencies", "class": "PI"},
    {"name": "PI Planning Gaps", "class": "PI"},
]


def get_insight_category_names() -> List[str]:
    """Extract just the category names from INSIGHT_CATEGORIES for validation"""
    return [cat["name"] for cat in INSIGHT_CATEGORIES]


def validate_insight_type(insight_type: str) -> str:
    """
    Validate and sanitize insight type to prevent SQL injection.
    Only allows alphanumeric characters, spaces, hyphens, and underscores.
    """
    if not insight_type or not isinstance(insight_type, str):
        raise HTTPException(status_code=400, detail="Insight type is required and must be a string")
    
    # Remove any potentially dangerous characters
    sanitized = re.sub(r'[^a-zA-Z0-9\s\-_]', '', insight_type.strip())
    
    if not sanitized:
        raise HTTPException(status_code=400, detail="Insight type contains invalid characters")
    
    if len(sanitized) > 255:  # Match database column limit
        raise HTTPException(status_code=400, detail="Insight type is too long (max 255 characters)")
    
    return sanitized


def validate_insight_categories(insight_categories: List[str]) -> List[str]:
    """
    Validate and sanitize insight categories list.
    Each category must be in the allowed INSIGHT_CATEGORIES list (by name).
    """
    if not insight_categories or not isinstance(insight_categories, list):
        raise HTTPException(status_code=400, detail="Insight categories must be a list")
    
    if len(insight_categories) == 0:
        raise HTTPException(status_code=400, detail="At least one insight category is required")
    
    allowed_names = get_insight_category_names()
    validated = []
    for category in insight_categories:
        if not isinstance(category, str):
            raise HTTPException(status_code=400, detail="Each insight category must be a string")
        
        category = category.strip()
        
        # Validate category name is in allowed list
        if category not in allowed_names:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid insight category: '{category}'. Allowed categories: {allowed_names}"
            )
        
        validated.append(category)
    
    return validated


@insight_types_router.get("/insight-types/categories")
async def get_insight_categories():
    """
    Get the list of available insight categories.
    Returns the fixed constant array of categories.
    
    Returns:
        JSON response with list of categories
    """
    return {
        "success": True,
        "data": {
            "categories": INSIGHT_CATEGORIES,
            "count": len(INSIGHT_CATEGORIES)
        },
        "message": f"Retrieved {len(INSIGHT_CATEGORIES)} insight categories"
    }


@insight_types_router.get("/insight-types")
async def get_insight_types_endpoint(
    insight_type: Optional[str] = Query(None, description="Filter by insight type (exact match)"),
    insight_category: Optional[str] = Query(None, description="Filter by insight category (exact match)"),
    active: Optional[bool] = Query(None, description="Filter by active status"),
    search: Optional[str] = Query(None, description="Search in insight types"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of insight types to return"),
    offset: int = Query(0, ge=0, description="Number of insight types to skip"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get collection of insight types with optional filtering.
    
    Args:
        insight_type: Filter by specific insight type (exact match)
        insight_category: Filter by insight category (exact match)
        active: Filter by active status
        search: Search term for insight types
        limit: Maximum number of results (1-1000)
        offset: Number of results to skip
    
    Returns:
        JSON response with list of insight types and count
    """
    try:
        # Validate filter parameters if provided
        validated_insight_type = None
        if insight_type:
            validated_insight_type = validate_insight_type(insight_type)
        
        validated_category = None
        if insight_category:
            # Validate category name is in allowed list
            allowed_names = get_insight_category_names()
            if insight_category not in allowed_names:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid insight category: '{insight_category}'. Allowed categories: {allowed_names}"
                )
            validated_category = insight_category.strip()
        
        # Get insight types from database function
        # JSONB arrays are already parsed to Python lists by get_insight_types function
        insight_types_list = get_insight_types(
            insight_type=validated_insight_type,
            insight_category=validated_category,
            active=active,
            search=search,
            limit=limit,
            offset=offset,
            conn=conn
        )
        
        return {
            "success": True,
            "data": {
                "insight_types": insight_types_list,
                "count": len(insight_types_list)
            },
            "message": f"Retrieved {len(insight_types_list)} insight types"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (like validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching insight types: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch insight types: {str(e)}"
        )


@insight_types_router.get("/insight-types/{id}")
async def get_insight_type(
    id: int,
    conn: Connection = Depends(get_db_connection)
):
    """
    Get a single insight type by ID.
    
    Args:
        id: The ID of the insight type to retrieve
    
    Returns:
        JSON response with single insight type or 404 if not found
    """
    try:
        # Use shared helper function from database_general
        insight_type = get_insight_type_by_id(id, conn)
        
        # JSONB is already parsed by get_insight_type_by_id function
        
        if not insight_type:
            raise HTTPException(
                status_code=404,
                detail=f"Insight type with ID {id} not found"
            )
        
        return {
            "success": True,
            "data": {
                "insight_type": insight_type
            },
            "message": f"Retrieved insight type with ID {id}"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (like the 404 error above)
        raise
    except Exception as e:
        logger.error(f"Error fetching insight type {id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch insight type: {str(e)}"
        )


# Pydantic models for request body
class CronConfig(BaseModel):
    day_of_week: Optional[str] = None
    hour: Optional[int] = None
    minute: Optional[int] = None


class InsightTypeCreateRequest(BaseModel):
    insight_type: str
    insight_description: Optional[str] = None
    insight_categories: List[str]  # Now a list instead of single string
    active: bool = True
    cron_config: Optional[CronConfig] = None


class InsightTypeUpdateRequest(BaseModel):
    insight_type: Optional[str] = None
    insight_description: Optional[str] = None
    insight_categories: Optional[List[str]] = None  # Now a list instead of single string
    active: Optional[bool] = None
    cron_config: Optional[CronConfig] = None


@insight_types_router.post("/insight-types")
async def create_insight_type_endpoint(
    request: InsightTypeCreateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Create a new insight type.
    
    Args:
        request: InsightTypeCreateRequest containing all required fields
    
    Returns:
        JSON response with created insight type
    """
    try:
        # Validate inputs
        validated_insight_type = validate_insight_type(request.insight_type)
        validated_categories = validate_insight_categories(request.insight_categories)
        
        if request.insight_description is not None and not isinstance(request.insight_description, str):
            raise HTTPException(status_code=400, detail="Insight description must be a string")
        
        # Prepare data for database function
        data = {
            "insight_type": validated_insight_type,
            "insight_categories": validated_categories,
            "active": request.active
        }
        
        if request.insight_description is not None:
            data["insight_description"] = request.insight_description
        
        if request.cron_config is not None:
            data["cron_config"] = request.cron_config.model_dump(exclude_unset=True)
        
        # Create insight type using database function
        # JSONB is already parsed by create_insight_type function
        created = create_insight_type(data, conn)
        
        return {
            "success": True,
            "data": {
                "insight_type": created
            },
            "message": f"Created insight type with ID {created.get('id')}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating insight type: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create insight type: {str(e)}"
        )


@insight_types_router.put("/insight-types/{id}")
async def update_insight_type_full(
    id: int,
    request: InsightTypeCreateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Update an existing insight type (full replacement - all fields required).
    
    Args:
        id: The ID of the insight type to update
        request: InsightTypeCreateRequest containing all required fields
    
    Returns:
        JSON response with updated insight type
    """
    try:
        # Validate inputs
        validated_insight_type = validate_insight_type(request.insight_type)
        validated_categories = validate_insight_categories(request.insight_categories)
        
        if request.insight_description is not None and not isinstance(request.insight_description, str):
            raise HTTPException(status_code=400, detail="Insight description must be a string")
        
        # Prepare data for database function (all fields required for PUT)
        updates = {
            "insight_type": validated_insight_type,
            "insight_categories": validated_categories,
            "active": request.active
        }
        
        if request.insight_description is not None:
            updates["insight_description"] = request.insight_description
        
        if request.cron_config is not None:
            updates["cron_config"] = request.cron_config.model_dump(exclude_unset=True)
        
        # Update insight type using database function
        # JSONB is already parsed by update_insight_type_by_id function
        updated = update_insight_type_by_id(id, updates, conn)
        
        if not updated:
            raise HTTPException(
                status_code=404,
                detail=f"Insight type with ID {id} not found"
            )
        
        return {
            "success": True,
            "data": {
                "insight_type": updated
            },
            "message": f"Updated insight type with ID {id}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating insight type {id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update insight type: {str(e)}"
        )


@insight_types_router.patch("/insight-types/{id}")
async def update_insight_type_partial(
    id: int,
    request: InsightTypeUpdateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Partially update an existing insight type (only provided fields).
    
    Args:
        id: The ID of the insight type to update
        request: InsightTypeUpdateRequest containing fields to update (all optional)
    
    Returns:
        JSON response with updated insight type
    """
    try:
        # Get only the fields that were provided (exclude None values)
        updates = request.model_dump(exclude_unset=True)
        
        if not updates:
            raise HTTPException(
                status_code=400,
                detail="At least one field must be provided for update"
            )
        
        # Validate fields if provided
        if "insight_type" in updates and updates["insight_type"] is not None:
            updates["insight_type"] = validate_insight_type(updates["insight_type"])
        
        if "insight_categories" in updates and updates["insight_categories"] is not None:
            updates["insight_categories"] = validate_insight_categories(updates["insight_categories"])
        
        if "insight_description" in updates and updates["insight_description"] is not None:
            if not isinstance(updates["insight_description"], str):
                raise HTTPException(status_code=400, detail="Insight description must be a string")
        
        if "cron_config" in updates and updates["cron_config"] is not None:
            # Convert Pydantic model to dict if it's a CronConfig instance
            if isinstance(updates["cron_config"], CronConfig):
                updates["cron_config"] = updates["cron_config"].model_dump(exclude_unset=True)
        
        # Update insight type using database function
        # JSONB is already parsed by update_insight_type_by_id function
        updated = update_insight_type_by_id(id, updates, conn)
        
        if not updated:
            raise HTTPException(
                status_code=404,
                detail=f"Insight type with ID {id} not found"
            )
        
        return {
            "success": True,
            "data": {
                "insight_type": updated
            },
            "message": f"Updated insight type with ID {id}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating insight type {id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update insight type: {str(e)}"
        )


@insight_types_router.delete("/insight-types/{id}")
async def delete_insight_type_endpoint(
    id: int,
    conn: Connection = Depends(get_db_connection)
):
    """
    Delete an insight type permanently.
    
    Args:
        id: The ID of the insight type to delete
    
    Returns:
        JSON response with success message
    """
    try:
        # Delete insight type using database function
        deleted = delete_insight_type_by_id(id, conn)
        
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"Insight type with ID {id} not found"
            )
        
        return {
            "success": True,
            "data": {
                "id": id
            },
            "message": f"Deleted insight type with ID {id}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting insight type {id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete insight type: {str(e)}"
        )

