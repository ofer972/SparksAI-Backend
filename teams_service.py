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
        
        logger.info(f"Executing query to get distinct team names from {config.WORK_ITEMS_TABLE}")
        
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

@teams_router.get("/teams/stats")
async def get_teams_stats(conn: Connection = Depends(get_db_connection)):
    """
    Get statistics about teams.
    Uses parameterized queries to prevent SQL injection.
    
    Returns:
        JSON response with team statistics
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT 
                COUNT(DISTINCT team_name) as total_teams,
                COUNT(*) as total_issues,
                AVG(team_issue_count) as avg_issues_per_team
            FROM (
                SELECT team_name, COUNT(*) as team_issue_count
                FROM {config.WORK_ITEMS_TABLE} 
                WHERE team_name IS NOT NULL AND team_name != ''
                GROUP BY team_name
            ) team_counts
        """)
        
        logger.info("Executing query to get team statistics")
        
        # Execute query with connection from dependency
        result = conn.execute(query)
        row = result.fetchone()
        
        stats = {
            "total_teams": row[0] if row[0] else 0,
            "total_issues": row[1] if row[1] else 0,
            "avg_issues_per_team": round(float(row[2]), 2) if row[2] else 0
        }
        
        return {
            "success": True,
            "data": stats,
            "message": "Retrieved team statistics"
        }
    
    except Exception as e:
        logger.error(f"Error fetching team statistics: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch team statistics: {str(e)}"
        )
