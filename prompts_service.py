"""
Prompts Service - REST API endpoints for prompt-related operations.

This service provides endpoints for managing and retrieving prompt information.
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
import config

logger = logging.getLogger(__name__)

prompts_router = APIRouter()

# Pydantic model for request body - used by both POST and PUT
class PromptRequest(BaseModel):
    email_address: str
    prompt_name: str
    prompt_description: str
    prompt_type: str
    prompt_active: bool = True

def validate_prompt_name(prompt_name: str) -> str:
    """
    Validate and sanitize prompt name to prevent SQL injection.
    Only allows alphanumeric characters, spaces, hyphens, and underscores.
    """
    if not prompt_name or not isinstance(prompt_name, str):
        raise HTTPException(status_code=400, detail="Prompt name is required and must be a string")
    
    # Remove any potentially dangerous characters
    sanitized = re.sub(r'[^a-zA-Z0-9\s\-_]', '', prompt_name.strip())
    
    if not sanitized:
        raise HTTPException(status_code=400, detail="Prompt name contains invalid characters")
    
    if len(sanitized) > 255:  # Match database column limit
        raise HTTPException(status_code=400, detail="Prompt name is too long (max 255 characters)")
    
    return sanitized

def validate_email_address(email_address: str) -> str:
    """
    Validate and sanitize email address field to prevent SQL injection.
    Accepts any text string (not validating email format).
    """
    if not email_address or not isinstance(email_address, str):
        raise HTTPException(status_code=400, detail="Email address is required and must be a string")
    
    if len(email_address) > 255:  # Match database column limit
        raise HTTPException(status_code=400, detail="Email address is too long (max 255 characters)")
    
    return email_address.strip()

@prompts_router.get("/prompts")
async def get_prompts(
    email_address: Optional[str] = Query(None, description="Filter by email address"),
    prompt_type: Optional[str] = Query(None, description="Filter by prompt type"),
    active: Optional[bool] = Query(None, description="Filter by active status"),
    search: Optional[str] = Query(None, description="Search in prompt names"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of prompts to return"),
    offset: int = Query(0, ge=0, description="Number of prompts to skip"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get collection of prompts with optional filtering.
    Returns truncated prompt_description (200 chars + "...") for collection view.
    
    Args:
        email_address: Filter by specific email address
        prompt_type: Filter by prompt type
        active: Filter by active status
        search: Search term for prompt names
        limit: Maximum number of results (1-1000)
        offset: Number of results to skip
    
    Returns:
        JSON response with list of prompts and count
    """
    try:
        # Build WHERE clause dynamically based on filters
        where_conditions = []
        params = {}
        
        if email_address:
            validated_email = validate_email_address(email_address)
            where_conditions.append("email_address = :email_address")
            params["email_address"] = validated_email
        
        if prompt_type:
            where_conditions.append("prompt_type = :prompt_type")
            params["prompt_type"] = prompt_type
        
        if active is not None:
            where_conditions.append("prompt_active = :active")
            params["active"] = active
        
        if search:
            where_conditions.append("prompt_name ILIKE :search")
            params["search"] = f"%{search}%"
        
        where_clause = ""
        if where_conditions:
            where_clause = "WHERE " + " AND ".join(where_conditions)
        
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT 
                email_address,
                prompt_name,
                CASE 
                    WHEN LENGTH(prompt_description) > 200 
                    THEN LEFT(prompt_description, 200) || '...'
                    ELSE prompt_description
                END as prompt_description,
                prompt_type,
                prompt_active,
                created_at,
                updated_at
            FROM {config.PROMPTS_TABLE}
            {where_clause}
            ORDER BY updated_at DESC
            LIMIT :limit OFFSET :offset
        """)
        
        params["limit"] = limit
        params["offset"] = offset
        
        logger.info(f"Executing query to get prompts from {config.PROMPTS_TABLE}")
        
        # Execute query with connection from dependency
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        prompts = []
        for row in rows:
            prompt_dict = {
                "email_address": row[0],
                "prompt_name": row[1],
                "prompt_description": row[2],  # Already truncated in SQL
                "prompt_type": row[3],
                "prompt_active": row[4],
                "created_at": row[5],
                "updated_at": row[6]
            }
            prompts.append(prompt_dict)
        
        return {
            "success": True,
            "data": {
                "prompts": prompts,
                "count": len(prompts)
            },
            "message": f"Retrieved {len(prompts)} prompts"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (like validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching prompts: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch prompts: {str(e)}"
        )

@prompts_router.get("/prompts/{email_address}/{prompt_name}")
async def get_prompt(
    email_address: str, 
    prompt_name: str, 
    conn: Connection = Depends(get_db_connection)
):
    """
    Get a single prompt by email_address and prompt_name.
    Returns full prompt_description (not truncated).
    
    Args:
        email_address: The email address of the prompt owner
        prompt_name: The name of the prompt
    
    Returns:
        JSON response with single prompt or 404 if not found
    """
    try:
        # Validate inputs
        validated_email = validate_email_address(email_address)
        validated_name = validate_prompt_name(prompt_name)
        
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT 
                email_address,
                prompt_name,
                prompt_description,
                prompt_type,
                prompt_active,
                created_at,
                updated_at
            FROM {config.PROMPTS_TABLE} 
            WHERE email_address = :email_address AND prompt_name = :prompt_name
        """)
        
        logger.info(f"Executing query to get prompt {validated_name} for {validated_email}")
        
        # Execute query with connection from dependency
        result = conn.execute(query, {
            "email_address": validated_email,
            "prompt_name": validated_name
        })
        row = result.fetchone()
        
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Prompt '{prompt_name}' not found for email '{email_address}'"
            )
        
        # Convert row to dictionary
        prompt = {
            "email_address": row[0],
            "prompt_name": row[1],
            "prompt_description": row[2],  # Full description
            "prompt_type": row[3],
            "prompt_active": row[4],
            "created_at": row[5],
            "updated_at": row[6]
        }
        
        return {
            "success": True,
            "data": {
                "prompt": prompt
            },
            "message": f"Retrieved prompt '{prompt_name}' for '{email_address}'"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (like the 404 error above)
        raise
    except Exception as e:
        logger.error(f"Error fetching prompt {prompt_name} for {email_address}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch prompt: {str(e)}"
        )

@prompts_router.post("/prompts")
async def create_prompt(
    request: PromptRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Create a new prompt.
    
    Args:
        request: PromptRequest containing all required fields (email_address, prompt_name, prompt_description, prompt_type, prompt_active)
    
    Returns:
        JSON response with created prompt
    """
    try:
        # Validate inputs
        validated_email = validate_email_address(request.email_address)
        validated_name = validate_prompt_name(request.prompt_name)
        
        if not request.prompt_description or not isinstance(request.prompt_description, str):
            raise HTTPException(status_code=400, detail="Prompt description is required and must be a string")
        
        if not request.prompt_type or not isinstance(request.prompt_type, str):
            raise HTTPException(status_code=400, detail="Prompt type is required and must be a string")
        
        if len(request.prompt_type) > 100:
            raise HTTPException(status_code=400, detail="Prompt type is too long (max 100 characters)")
        
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            INSERT INTO {config.PROMPTS_TABLE} 
            (email_address, prompt_name, prompt_description, prompt_type, prompt_active, created_at, updated_at)
            VALUES (:email_address, :prompt_name, :prompt_description, :prompt_type, :prompt_active, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING email_address, prompt_name, prompt_description, prompt_type, prompt_active, created_at, updated_at
        """)
        
        logger.info(f"Creating prompt '{validated_name}' for '{validated_email}'")
        
        result = conn.execute(query, {
            "email_address": validated_email,
            "prompt_name": validated_name,
            "prompt_description": request.prompt_description,
            "prompt_type": request.prompt_type,
            "prompt_active": request.prompt_active
        })
        
        row = result.fetchone()
        conn.commit()
        
        # Convert row to dictionary
        prompt = {
            "email_address": row[0],
            "prompt_name": row[1],
            "prompt_description": row[2],
            "prompt_type": row[3],
            "prompt_active": row[4],
            "created_at": row[5],
            "updated_at": row[6]
        }
        
        return {
            "success": True,
            "data": {
                "prompt": prompt
            },
            "message": f"Created prompt '{request.prompt_name}' for '{request.email_address}'"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        # Check if it's a unique constraint violation
        if "unique constraint" in str(e).lower() or "duplicate key" in str(e).lower():
            raise HTTPException(
                status_code=409,
                detail=f"Prompt '{request.prompt_name}' already exists for email '{request.email_address}'"
            )
        logger.error(f"Error creating prompt: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create prompt: {str(e)}"
        )

@prompts_router.put("/prompts/{email_address}/{prompt_name}")
async def update_prompt(
    email_address: str,
    prompt_name: str,
    request: PromptRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Update an existing prompt (full replacement - all fields required).
    
    Args:
        email_address: Email address of the prompt owner (from path)
        prompt_name: Name of the prompt (from path)
        request: PromptRequest containing all required fields (email_address, prompt_name, prompt_description, prompt_type, prompt_active)
                 Note: email_address and prompt_name in body should match path parameters
    
    Returns:
        JSON response with updated prompt
    """
    try:
        # Validate path parameters
        validated_email = validate_email_address(email_address)
        validated_name = validate_prompt_name(prompt_name)
        
        # Validate that body email_address and prompt_name match path parameters
        validated_body_email = validate_email_address(request.email_address)
        validated_body_name = validate_prompt_name(request.prompt_name)
        
        if validated_email != validated_body_email:
            raise HTTPException(
                status_code=400,
                detail=f"email_address in path ({email_address}) does not match email_address in body ({request.email_address})"
            )
        
        if validated_name != validated_body_name:
            raise HTTPException(
                status_code=400,
                detail=f"prompt_name in path ({prompt_name}) does not match prompt_name in body ({request.prompt_name})"
            )
        
        # Validate other fields
        if not request.prompt_description or not isinstance(request.prompt_description, str):
            raise HTTPException(status_code=400, detail="Prompt description is required and must be a string")
        
        if not request.prompt_type or not isinstance(request.prompt_type, str):
            raise HTTPException(status_code=400, detail="Prompt type is required and must be a string")
        
        if len(request.prompt_type) > 100:
            raise HTTPException(status_code=400, detail="Prompt type is too long (max 100 characters)")
        
        # SECURE: Parameterized query prevents SQL injection
        # Full replacement - update all fields
        query = text(f"""
            UPDATE {config.PROMPTS_TABLE} 
            SET prompt_description = :prompt_description,
                prompt_type = :prompt_type,
                prompt_active = :prompt_active,
                updated_at = CURRENT_TIMESTAMP
            WHERE email_address = :email_address AND prompt_name = :prompt_name
            RETURNING email_address, prompt_name, prompt_description, prompt_type, prompt_active, created_at, updated_at
        """)
        
        logger.info(f"Updating prompt '{validated_name}' for '{validated_email}'")
        
        result = conn.execute(query, {
            "email_address": validated_email,
            "prompt_name": validated_name,
            "prompt_description": request.prompt_description,
            "prompt_type": request.prompt_type,
            "prompt_active": request.prompt_active
        })
        
        row = result.fetchone()
        
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Prompt '{prompt_name}' not found for email '{email_address}'"
            )
        
        conn.commit()
        
        # Convert row to dictionary
        prompt = {
            "email_address": row[0],
            "prompt_name": row[1],
            "prompt_description": row[2],
            "prompt_type": row[3],
            "prompt_active": row[4],
            "created_at": row[5],
            "updated_at": row[6]
        }
        
        return {
            "success": True,
            "data": {
                "prompt": prompt
            },
            "message": f"Updated prompt '{prompt_name}' for '{email_address}'"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating prompt {prompt_name} for {email_address}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update prompt: {str(e)}"
        )

@prompts_router.delete("/prompts/{email_address}/{prompt_name}")
async def delete_prompt(
    email_address: str,
    prompt_name: str,
    conn: Connection = Depends(get_db_connection)
):
    """
    Delete a prompt permanently.
    
    Args:
        email_address: Email address of the prompt owner
        prompt_name: Name of the prompt
    
    Returns:
        JSON response with success message
    """
    try:
        # Validate inputs
        validated_email = validate_email_address(email_address)
        validated_name = validate_prompt_name(prompt_name)
        
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            DELETE FROM {config.PROMPTS_TABLE} 
            WHERE email_address = :email_address AND prompt_name = :prompt_name
            RETURNING email_address, prompt_name
        """)
        
        logger.info(f"Deleting prompt '{validated_name}' for '{validated_email}'")
        
        result = conn.execute(query, {
            "email_address": validated_email,
            "prompt_name": validated_name
        })
        row = result.fetchone()
        
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Prompt '{prompt_name}' not found for email '{email_address}'"
            )
        
        conn.commit()
        
        return {
            "success": True,
            "data": {
                "deleted_prompt": {
                    "email_address": row[0],
                    "prompt_name": row[1]
                }
            },
            "message": f"Deleted prompt '{prompt_name}' for '{email_address}'"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting prompt {prompt_name} for {email_address}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete prompt: {str(e)}"
        )
