"""
Settings Service - REST API endpoints for global settings.

This service provides endpoints for retrieving global settings.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.engine import Connection
from typing import Dict, Any
import logging
from database_connection import get_db_connection
from database_general import get_all_settings_db
import config

logger = logging.getLogger(__name__)

settings_router = APIRouter()


@settings_router.get("/settings/getAll")
async def get_all_settings(conn: Connection = Depends(get_db_connection)):
    """
    Get all global settings from the database.

    Returns all settings as key-value pairs, including full API keys.

    Returns:
        JSON response with settings dictionary and count
    """
    try:
        # Get settings from database function
        settings = get_all_settings_db(conn)
        
        return {
            "success": True,
            "data": {
                "settings": settings,
                "count": len(settings)
            },
            "message": f"Retrieved {len(settings)} settings"
        }
    
    except Exception as e:
        logger.error(f"Error fetching all settings: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch settings: {str(e)}"
        )
