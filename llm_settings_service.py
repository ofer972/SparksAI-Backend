"""
LLM Settings Service - REST API endpoints for LLM-specific settings.

This service provides endpoints for managing LLM configuration settings
including provider selection, models, temperatures, and API keys.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection
from typing import Dict, Optional
import logging
from database_connection import get_db_connection
from database_general import get_all_llm_settings_db, set_llm_settings_batch_db

logger = logging.getLogger(__name__)

llm_settings_router = APIRouter()

# LLM settings keys
LLM_SETTINGS_KEYS = [
    "ai_provider",
    "ai_chatgpt_model",
    "ai_gemini_model",
    "ai_gemini_temperature",
    "ai_chatgpt_temperature",
    "gemini_api_key",
    "chatgpt_api_key"
]

# API key settings (to exclude from default GET)
LLM_API_KEY_KEYS = [
    "gemini_api_key",
    "chatgpt_api_key"
]


class LLMSettingsUpdateRequest(BaseModel):
    """Request model for updating LLM settings"""
    settings: Dict[str, str] = Field(..., description="Dictionary of LLM setting_key: setting_value pairs")
    updated_by: Optional[str] = Field(None, description="Email of user making the change")


@llm_settings_router.get("/llm-settings")
async def get_llm_settings(conn: Connection = Depends(get_db_connection)):
    """
    Get LLM settings from the database (excluding API keys).
    
    Returns LLM settings as key-value pairs, excluding API keys for security.
    
    Returns:
        JSON response with LLM settings dictionary (without API keys) and count
    """
    try:
        # Get all LLM settings from database
        all_llm_settings = get_all_llm_settings_db(conn)
        
        # Filter to exclude API keys
        llm_settings = {
            key: value 
            for key, value in all_llm_settings.items() 
            if key not in LLM_API_KEY_KEYS
        }
        
        return {
            "success": True,
            "data": {
                "settings": llm_settings,
                "count": len(llm_settings)
            },
            "message": f"Retrieved {len(llm_settings)} LLM settings (excluding API keys)"
        }
    
    except Exception as e:
        logger.error(f"Error fetching LLM settings: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch LLM settings: {str(e)}"
        )


@llm_settings_router.get("/llm-settings/all")
async def get_all_llm_settings(conn: Connection = Depends(get_db_connection)):
    """
    Get all LLM settings from the database (including API keys).
    
    Returns all LLM settings as key-value pairs, including full API keys.
    
    Returns:
        JSON response with all LLM settings dictionary (including API keys) and count
    """
    try:
        # Get all LLM settings from database (including API keys)
        llm_settings = get_all_llm_settings_db(conn)
        
        return {
            "success": True,
            "data": {
                "settings": llm_settings,
                "count": len(llm_settings)
            },
            "message": f"Retrieved {len(llm_settings)} LLM settings (including API keys)"
        }
    
    except Exception as e:
        logger.error(f"Error fetching all LLM settings: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch all LLM settings: {str(e)}"
        )


@llm_settings_router.put("/llm-settings")
async def update_llm_settings(
    request: LLMSettingsUpdateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Update LLM settings in the database.
    
    Uses UPSERT logic (INSERT ... ON CONFLICT UPDATE) for each setting.
    Only accepts LLM-related setting keys. API keys are stored as-is without hashing/masking.
    
    Args:
        request: LLMSettingsUpdateRequest containing settings dict and optional updated_by
        
    Returns:
        JSON response with success status and results for each setting
    """
    try:
        if not request.settings:
            raise HTTPException(
                status_code=400,
                detail="Settings dictionary cannot be empty"
            )
        
        # Validate that all keys are LLM settings keys
        invalid_keys = [key for key in request.settings.keys() if key not in LLM_SETTINGS_KEYS]
        if invalid_keys:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid setting keys: {', '.join(invalid_keys)}. Only LLM settings keys are allowed."
            )
        
        updated_by = request.updated_by or 'admin'
        
        # Log all settings received from client in compact format (mask API keys)
        settings_list = []
        for k, v in request.settings.items():
            if k in LLM_API_KEY_KEYS:
                settings_list.append(f"{k}=***")
            else:
                settings_list.append(f"{k}={v}")
        logger.info(f"Client LLM settings: {', '.join(settings_list)}")
        
        # Update settings in database
        results = set_llm_settings_batch_db(
            settings=request.settings,
            updated_by=updated_by,
            conn=conn
        )
        
        # Check if any failed
        failed = [key for key, success in results.items() if not success]
        success_count = sum(1 for success in results.values() if success)
        
        # Log updated settings in compact format (mask API keys)
        updated_settings_list = []
        for key, success in results.items():
            if success:
                if key in LLM_API_KEY_KEYS:
                    updated_settings_list.append(f"{key}=***")
                else:
                    updated_settings_list.append(f"{key}={request.settings[key]}")
        if updated_settings_list:
            logger.info(f"Updated LLM settings: {', '.join(updated_settings_list)}")
        
        return {
            "success": True,
            "data": {
                "results": results,
                "success_count": success_count,
                "failed_count": len(failed),
                "failed_keys": failed if failed else None
            },
            "message": f"LLM settings update completed: {success_count}/{len(request.settings)} settings updated successfully"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in LLM settings update: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update LLM settings: {str(e)}"
        )

