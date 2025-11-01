"""
Users Service - REST API endpoints for user-related operations.

This service provides endpoints for managing and retrieving user information.
"""

from fastapi import APIRouter
import logging

logger = logging.getLogger(__name__)

users_router = APIRouter()

@users_router.get("/users/get-current-user")
async def get_current_user():
    """
    Get the current user's information.
    
    Returns:
        JSON response with user_id, user_name, and user_type
    """
    return {
        "user_id": "ofer972@gmail.com",
        "user_name": "Ofer Cohen",
        "user_type": "Admin"
    }

