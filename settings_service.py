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
import httpx
from database_connection import get_db_connection
from database_general import get_all_settings_db, set_setting_db, set_settings_batch_db
import config

logger = logging.getLogger(__name__)

settings_router = APIRouter()


class BatchSettingsUpdateRequest(BaseModel):
    """Request model for batch updating settings"""
    settings: Dict[str, str] = Field(..., description="Dictionary of setting_key: setting_value pairs")
    updated_by: Optional[str] = Field(None, description="Email of user making the change")


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


# Alias endpoint for convenience: GET /settings
# Returns the same payload as /settings/getAll
@settings_router.get("/settings")
async def get_all_settings_alias(conn: Connection = Depends(get_db_connection)):
    return await get_all_settings(conn)


async def call_llm_reset_settings() -> None:
    """
    Call LLM service /reset-settings endpoint to clear settings cache.
    This is called after settings are updated.
    """
    llm_service_url = f"{config.LLM_SERVICE_URL}/reset-settings"
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(llm_service_url)
            if response.status_code == 200:
                logger.info("LLM service settings cache cleared successfully")
            else:
                logger.warning(f"LLM service reset-settings returned status {response.status_code}")
    except httpx.HTTPError as e:
        logger.warning(f"Failed to call LLM service reset-settings: {e}")
        # Don't fail the request if LLM service is unavailable
    except Exception as e:
        logger.warning(f"Error calling LLM service reset-settings: {e}")
        # Don't fail the request if LLM service is unavailable


@settings_router.put("/settings/{setting_key}")
async def update_setting(
    setting_key: str,
    value: str = Query(..., description="The value to set"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Update a single global setting in the database.
    
    Uses UPSERT logic (INSERT ... ON CONFLICT UPDATE).
    After successful update, calls LLM service /reset-settings to clear cache.
    
    Args:
        setting_key: The setting key to update (path parameter)
        value: The value to set (query parameter)
        
    Returns:
        JSON response with success status and message
    """
    try:
        # Update setting in database
        success = set_setting_db(
            setting_key=setting_key,
            setting_value=value,
            updated_by='admin',
            conn=conn
        )
        
        if not success:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to update setting '{setting_key}'"
            )
        
        # Call LLM service to reset settings cache
        await call_llm_reset_settings()
        
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
        
        # Update settings in database
        results = set_settings_batch_db(
            settings=request.settings,
            updated_by=updated_by,
            conn=conn
        )
        
        # Check if any failed
        failed = [key for key, success in results.items() if not success]
        success_count = sum(1 for success in results.values() if success)
        
        # Call LLM service to reset settings cache (if any succeeded)
        if success_count > 0:
            await call_llm_reset_settings()
        
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
