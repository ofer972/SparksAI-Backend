"""
Settings Service - REST API endpoints for global settings.

This service provides endpoints for retrieving global settings.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection
from typing import Dict, Any, Optional
import logging
from database_connection import get_db_connection
from database_general import get_all_settings_db, get_setting_db, set_setting_db, set_settings_batch_db

logger = logging.getLogger(__name__)

settings_router = APIRouter()


class SettingUpdateItem(BaseModel):
    """Individual setting update item with optional description"""
    value: str = Field(..., description="The setting value")
    description: Optional[str] = Field(None, description="Optional description to update")


class BatchSettingsUpdateRequest(BaseModel):
    """Request model for batch updating settings"""
    settings: Dict[str, SettingUpdateItem] = Field(..., description="Dictionary of setting_key: SettingUpdateItem pairs")
    updated_by: Optional[str] = Field(None, description="Email of user making the change")


@settings_router.get("/settings/getAll")
async def get_all_settings(conn: Connection = Depends(get_db_connection)):
    """
    Get all global settings from the database, grouped by category.

    Returns settings organized by category with proper ordering.
    Metrics & KPIs appears first, followed by other categories.

    Returns:
        JSON response with settings grouped by category, categories list, and count
    """
    try:
        # Get settings from database function (already grouped by category)
        settings_by_category = get_all_settings_db(conn)
        
        # Extract category names in order (dict keys maintain insertion order in Python 3.7+)
        categories = list(settings_by_category.keys())
        
        # Count total settings
        total_count = sum(len(settings) for settings in settings_by_category.values())
        
        return {
            "success": True,
            "data": {
                "settings_by_category": settings_by_category,
                "categories": categories,
                "count": total_count
            },
            "message": f"Retrieved {total_count} settings across {len(categories)} categories"
        }
    
    except Exception as e:
        logger.error(f"Error fetching all settings: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch settings: {str(e)}"
        )


# Alias endpoint for convenience: GET /settings
# Returns the same payload as /settings/getAll
@settings_router.get("/settings")
async def get_all_settings_alias(conn: Connection = Depends(get_db_connection)):
    return await get_all_settings(conn)


@settings_router.get("/settings/{setting_key}")
async def get_setting(
    setting_key: str,
    conn: Connection = Depends(get_db_connection)
):
    """
    Get a single global setting from the database by key.
    
    Args:
        setting_key: The setting key to retrieve (path parameter)
        
    Returns:
        JSON response with setting key and value, or 404 if not found
    """
    try:
        # Get setting from database function
        setting_value = get_setting_db(setting_key, conn)
        
        if setting_value is None:
            raise HTTPException(
                status_code=404,
                detail=f"Setting '{setting_key}' not found"
            )
        
        return {
            "success": True,
            "data": {
                "setting_key": setting_key,
                "setting_value": setting_value
            },
            "message": f"Retrieved setting '{setting_key}'"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching setting '{setting_key}': {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch setting: {str(e)}"
        )


@settings_router.put("/settings/batch")
async def update_settings_batch(
    request: BatchSettingsUpdateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Update multiple global settings in a batch.
    
    Uses UPSERT logic (INSERT ... ON CONFLICT UPDATE) for each setting.
    After successful update, calls LLM service /reset-settings to clear cache.
    
    Args:
        request: BatchSettingsUpdateRequest containing settings dict and optional updated_by
        
    Returns:
        JSON response with success status and results for each setting
    """
    try:
        if not request.settings:
            raise HTTPException(
                status_code=400,
                detail="Settings dictionary cannot be empty"
            )
        
        updated_by = request.updated_by or 'admin'
        
        # Log all settings received from client in compact format
        settings_list = [f"{k}={v.value}" for k, v in request.settings.items()]
        logger.info(f"Client settings: {', '.join(settings_list)}")
        
        # Convert SettingUpdateItem dict to format expected by database function
        settings_dict = {k: v.value for k, v in request.settings.items()}
        descriptions_dict = {k: v.description for k, v in request.settings.items() if v.description is not None}
        
        # Update settings in database
        results = set_settings_batch_db(
            settings=settings_dict,
            descriptions=descriptions_dict if descriptions_dict else None,
            updated_by=updated_by,
            conn=conn
        )
        
        # Check if any failed
        failed = [key for key, success in results.items() if not success]
        success_count = sum(1 for success in results.values() if success)
        
        return {
            "success": True,
            "data": {
                "results": results,
                "success_count": success_count,
                "failed_count": len(failed),
                "failed_keys": failed if failed else None
            },
            "message": f"Batch update completed: {success_count}/{len(request.settings)} settings updated successfully"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in batch settings update: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update settings: {str(e)}"
        )


@settings_router.put("/settings/{setting_key}")
async def update_setting(
    setting_key: str,
    value: str = Query(..., description="The value to set"),
    description: Optional[str] = Query(None, description="Optional description to update"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Update a single global setting in the database.
    
    Uses UPSERT logic (INSERT ... ON CONFLICT UPDATE).
    After successful update, calls LLM service /reset-settings to clear cache.
    
    Args:
        setting_key: The setting key to update (path parameter)
        value: The value to set (query parameter)
        description: Optional description to update (query parameter)
        
    Returns:
        JSON response with success status and message
    """
    try:
        # Log setting received from client
        logger.info(f"Client setting: {setting_key}={value}" + (f", description={description}" if description else ""))
        
        # Update setting in database
        success = set_setting_db(
            setting_key=setting_key,
            setting_value=value,
            description=description,
            updated_by='admin',
            conn=conn
        )
        
        if not success:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to update setting '{setting_key}'"
            )
        
        return {
            "success": True,
            "data": {
                "setting_key": setting_key,
                "setting_value": value
            },
            "message": f"Setting '{setting_key}' updated successfully"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating setting '{setting_key}': {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update setting: {str(e)}"
        )
