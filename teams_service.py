"""
Teams Service - REST API endpoints for team-related operations.

This service provides endpoints for managing and retrieving team information.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any
import logging
import re
from database_connection import get_db_connection
import config

logger = logging.getLogger(__name__)

teams_router = APIRouter()

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

@teams_router.get("/teams/getNames")
async def get_team_names(conn: Connection = Depends(get_db_connection)):
    """
    Get all distinct team names from jira_issues table.
    Uses parameterized queries to prevent SQL injection.
    
    Returns:
        JSON response with list of team names and count
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT DISTINCT team_name 
            FROM {config.WORK_ITEMS_TABLE} 
            WHERE team_name IS NOT NULL 
            AND team_name != '' 
            ORDER BY team_name
        """)
        
        logger.info(f"Executing query to get distinct team names from work items table")
        
        # Execute query with connection from dependency
        result = conn.execute(query)
        rows = result.fetchall()
        
        # Extract team names from result
        team_names = [row[0] for row in rows]
        
        return {
            "success": True,
            "data": {
                "teams": team_names,
                "count": len(team_names)
            },
            "message": f"Retrieved {len(team_names)} team names"
        }
    
    except Exception as e:
        logger.error(f"Error fetching team names: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch team names: {str(e)}"
        )

