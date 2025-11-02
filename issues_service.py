"""
Issues Service - REST API endpoints for issue-related operations.

This service provides endpoints for managing and retrieving issue information.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
import logging
import re
from database_connection import get_db_connection
import config

logger = logging.getLogger(__name__)

issues_router = APIRouter()

def validate_limit(limit: int) -> int:
    """
    Validate limit parameter to prevent abuse.
    """
    if limit < 1:
        raise HTTPException(status_code=400, detail="Limit must be at least 1")
    
    if limit > 1000:  # Reasonable upper limit
        raise HTTPException(status_code=400, detail="Limit cannot exceed 1000")
    
    return limit

@issues_router.get("/issues")
async def get_issues(
    issue_type: Optional[str] = Query(None, description="Filter by issue type"),
    status_category: Optional[str] = Query(None, description="Filter by status category"),
    team_name: Optional[str] = Query(None, description="Filter by team name"),
    pi: Optional[str] = Query(None, description="Filter by PI (quarter_pi)"),
    sprint_id: Optional[int] = Query(None, description="Filter by sprint ID (matches any sprint_ids array element)"),
    limit: int = Query(200, description="Number of issues to return (default: 200, max: 1000)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get a collection of issues with optional filtering.
    
    Returns issues with fields: issue_key, issue_type, summary, description, status_category, flagged, dependency, parent_key.
    
    Args:
        issue_type: Optional filter by issue type
        status_category: Optional filter by status category
        team_name: Optional filter by team name
        pi: Optional filter by PI (quarter_pi)
        sprint_id: Optional filter by sprint ID (checks if sprint_id is in sprint_ids array)
        limit: Number of issues to return (default: 200, max: 1000)
    
    Returns:
        JSON response with issues list and metadata
    """
    try:
        # Validate limit
        validated_limit = validate_limit(limit)
        
        # Build WHERE clause conditions based on provided filters
        where_conditions = []
        params = {"limit": validated_limit}
        
        if issue_type:
            where_conditions.append("issue_type = :issue_type")
            params["issue_type"] = issue_type
        
        if status_category:
            where_conditions.append("status_category = :status_category")
            params["status_category"] = status_category
        
        if team_name:
            where_conditions.append("team_name = :team_name")
            params["team_name"] = team_name
        
        if pi:
            where_conditions.append("quarter_pi = :quarter_pi")
            params["quarter_pi"] = pi
        
        if sprint_id is not None:
            where_conditions.append(":sprint_id = ANY(sprint_ids)")
            params["sprint_id"] = sprint_id
        
        # Build SQL query
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        query = text(f"""
            SELECT 
                issue_key,
                issue_type,
                summary,
                description,
                status_category,
                flagged,
                dependency,
                "Epic Link / Parent Key" as parent_key
            FROM {config.WORK_ITEMS_TABLE}
            WHERE {where_clause}
            ORDER BY issue_id DESC
            LIMIT :limit
        """)
        
        logger.info(f"Executing query to get issues with filters: issue_type={issue_type}, status_category={status_category}, team_name={team_name}, pi={pi}, sprint_id={sprint_id}, limit={validated_limit}")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        issues = []
        for row in rows:
            issue_dict = {
                "issue_key": row[0],
                "issue_type": row[1],
                "summary": row[2],
                "description": row[3],
                "status_category": row[4],
                "flagged": row[5],
                "dependency": row[6],
                "parent_key": row[7]
            }
            issues.append(issue_dict)
        
        return {
            "success": True,
            "data": {
                "issues": issues,
                "count": len(issues),
                "limit": validated_limit
            },
            "message": f"Retrieved {len(issues)} issues"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching issues: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch issues: {str(e)}"
        )

@issues_router.get("/issues/{issue_id}")
async def get_issue(
    issue_id: str,
    conn: Connection = Depends(get_db_connection)
):
    """
    Get a single issue by ID.
    
    Returns all columns from the jira_issues table for the specified issue_id.
    
    Args:
        issue_id: The ID of the issue to retrieve (varchar(50))
    
    Returns:
        JSON response with single issue (all columns) or 404 if not found
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT *
            FROM {config.WORK_ITEMS_TABLE}
            WHERE issue_id = :issue_id
        """)
        
        logger.info(f"Executing query to get issue with ID: {issue_id}")
        
        result = conn.execute(query, {"issue_id": issue_id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Issue with ID {issue_id} not found"
            )
        
        # Convert row to dictionary - return all columns
        issue_dict = dict(row._mapping)
        
        return {
            "success": True,
            "data": {
                "issue": issue_dict
            },
            "message": f"Retrieved issue with ID {issue_id}"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (like the 404 error above)
        raise
    except Exception as e:
        logger.error(f"Error fetching issue {issue_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch issue: {str(e)}"
        )

