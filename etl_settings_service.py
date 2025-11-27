"""
ETL Settings Service - REST API endpoints for ETL settings management.
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.engine import Connection
from typing import Optional
import logging
from database_connection import get_db_connection
from database_general import get_etl_setting_from_db, get_all_etl_settings_from_db

logger = logging.getLogger(__name__)

etl_settings_router = APIRouter()


@etl_settings_router.get("/etl/settings")
async def get_all_settings(
    conn: Connection = Depends(get_db_connection)
):
    """
    Get all ETL settings from the database.
    
    Returns:
        JSON response with all settings as key-value pairs
    """
    try:
        settings = get_all_etl_settings_from_db(conn)
        return {
            "success": True,
            "data": {
                "settings": settings,
                "count": len(settings)
            },
            "message": f"Retrieved {len(settings)} ETL settings"
        }
    except Exception as e:
        logger.error(f"Error fetching all ETL settings: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch ETL settings: {str(e)}"
        )


@etl_settings_router.get("/etl/settings/{setting_key}")
async def get_setting(
    setting_key: str,
    conn: Connection = Depends(get_db_connection)
):
    """
    Get a single setting value.
    
    Args:
        setting_key: Setting key to retrieve
        conn: Database connection
        
    Returns:
        JSON response with setting value
    """
    try:
        value = get_etl_setting_from_db(conn, setting_key)
        return {
            "success": True,
            "data": {
                "setting_key": setting_key,
                "setting_value": value
            },
            "message": f"Retrieved setting '{setting_key}'"
        }
    except Exception as e:
        logger.error(f"Error fetching setting '{setting_key}': {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch setting: {str(e)}"
        )

