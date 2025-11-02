"""
Sprints Service - REST API endpoints for sprint-related operations.

This service provides endpoints for managing and retrieving sprint information.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any
import logging
import re
from database_connection import get_db_connection

logger = logging.getLogger(__name__)

sprints_router = APIRouter()

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

@sprints_router.get("/sprints")
async def get_sprints(conn: Connection = Depends(get_db_connection)):
    """
    Get a collection of sprints.
    
    Returns sprints with fields: sprint_id, name, state, start_date, end_date, goal.
    
    Returns:
        JSON response with sprints list and metadata
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text("""
            SELECT 
                sprint_id,
                name,
                state,
                start_date,
                end_date,
                goal
            FROM public.jira_sprints
            ORDER BY sprint_id DESC
        """)
        
        logger.info(f"Executing query to get sprints collection")
        
        result = conn.execute(query)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        sprints = []
        for row in rows:
            sprint_dict = {
                "sprint_id": row[0],
                "name": row[1],
                "state": row[2],
                "start_date": row[3].strftime('%Y-%m-%d') if row[3] else None,
                "end_date": row[4].strftime('%Y-%m-%d') if row[4] else None,
                "goal": row[5]
            }
            sprints.append(sprint_dict)
        
        return {
            "success": True,
            "data": {
                "sprints": sprints,
                "count": len(sprints)
            },
            "message": f"Retrieved {len(sprints)} sprints"
        }
    
    except Exception as e:
        logger.error(f"Error fetching sprints: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch sprints: {str(e)}"
        )

@sprints_router.get("/sprints/active-sprint-summary-by-team")
async def get_active_sprint_summary_by_team(
    team_name: str = Query(..., description="Team name to get active sprint summary for"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get active sprint summary by team from the active_sprint_summary_by_team view.
    
    Returns all columns from the view for the specified team.
    
    Args:
        team_name: The name of the team to get active sprint summary for
    
    Returns:
        JSON response with active sprint summary (all columns from view)
    """
    try:
        # Validate team name
        validated_team_name = validate_team_name(team_name)
        
        # SECURE: Parameterized query prevents SQL injection
        query = text("""
            SELECT *
            FROM public.active_sprint_summary_by_team
            WHERE team_name = :team_name
        """)
        
        logger.info(f"Executing query to get active sprint summary for team: {validated_team_name}")
        
        result = conn.execute(query, {"team_name": validated_team_name})
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries - return all columns from view
        summaries = []
        for row in rows:
            summary_dict = dict(row._mapping)
            
            # Format date/datetime fields if they exist
            for key, value in summary_dict.items():
                if value is not None:
                    if hasattr(value, 'strftime'):
                        # Date field
                        summary_dict[key] = value.strftime('%Y-%m-%d')
                    elif hasattr(value, 'isoformat'):
                        # Datetime field
                        summary_dict[key] = value.isoformat()
            
            summaries.append(summary_dict)
        
        return {
            "success": True,
            "data": {
                "summaries": summaries,
                "count": len(summaries),
                "team_name": validated_team_name
            },
            "message": f"Retrieved active sprint summary for team '{validated_team_name}'"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching active sprint summary for team {team_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch active sprint summary: {str(e)}"
        )

@sprints_router.get("/sprints/active-sprint-summary/{sprint_id}")
async def get_active_sprint_summary(
    sprint_id: int,
    conn: Connection = Depends(get_db_connection)
):
    """
    Get active sprint summary by sprint ID from the active_sprint_summary view.
    
    Returns all columns from the view for the specified sprint_id.
    
    Args:
        sprint_id: The ID of the sprint to get summary for (int4)
    
    Returns:
        JSON response with active sprint summary (all columns from view) or 404 if not found
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text("""
            SELECT *
            FROM public.active_sprint_summary
            WHERE sprint_id = :sprint_id
        """)
        
        logger.info(f"Executing query to get active sprint summary for sprint_id: {sprint_id}")
        
        result = conn.execute(query, {"sprint_id": sprint_id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Active sprint summary with sprint_id {sprint_id} not found"
            )
        
        # Convert row to dictionary - return all columns from view
        summary_dict = dict(row._mapping)
        
        # Format date/datetime fields if they exist
        for key, value in summary_dict.items():
            if value is not None:
                if hasattr(value, 'strftime'):
                    # Date or timestamp field
                    if 'date' in key.lower() or 'time' in key.lower():
                        summary_dict[key] = value.strftime('%Y-%m-%d %H:%M:%S') if hasattr(value, 'strftime') else str(value)
                elif hasattr(value, 'isoformat'):
                    # Datetime field
                    summary_dict[key] = value.isoformat()
        
        return {
            "success": True,
            "data": {
                "summary": summary_dict
            },
            "message": f"Retrieved active sprint summary for sprint_id {sprint_id}"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (like the 404 error above)
        raise
    except Exception as e:
        logger.error(f"Error fetching active sprint summary for sprint_id {sprint_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch active sprint summary: {str(e)}"
        )

@sprints_router.get("/sprints/sprint-issues-with-epic-for-llm")
async def get_sprint_issues_with_epic_for_llm(
    sprint_id: int = Query(..., description="Sprint ID to get issues with epic for"),
    team_name: str = Query(..., description="Team name to get issues for"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get sprint issues with epic data from the sprint_issues_with_epic_for_llm view.
    
    Returns all columns from the view using SELECT * for the specified sprint_id and team_name.
    
    Args:
        sprint_id: Sprint ID to get issues for (int)
        team_name: The name of the team to get issues for (str)
    
    Returns:
        JSON response with all fields from the sprint_issues_with_epic_for_llm view
    """
    try:
        # Validate team name
        validated_team_name = validate_team_name(team_name)
        
        # SECURE: Parameterized query prevents SQL injection
        # Using SELECT * to return all fields from the view
        query = text("""
            SELECT *
            FROM public.sprint_issues_with_epic_for_llm
            WHERE sprint_id = :sprint_id AND team_name = :team_name
        """)
        
        logger.info(f"Executing query to get sprint issues with epic for LLM for sprint_id: {sprint_id}, team_name: {validated_team_name}")
        
        result = conn.execute(query, {"sprint_id": sprint_id, "team_name": validated_team_name})
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries - return all columns from view
        sprint_issues = []
        for row in rows:
            issue_dict = dict(row._mapping)
            
            # Format date/datetime fields if they exist
            for key, value in issue_dict.items():
                if value is not None:
                    if hasattr(value, 'strftime'):
                        # Date field
                        issue_dict[key] = value.strftime('%Y-%m-%d')
                    elif hasattr(value, 'isoformat'):
                        # Datetime field
                        issue_dict[key] = value.isoformat()
            
            sprint_issues.append(issue_dict)
        
        return {
            "success": True,
            "data": {
                "sprint_issues": sprint_issues,
                "count": len(sprint_issues),
                "sprint_id": sprint_id,
                "team_name": validated_team_name
            },
            "message": f"Retrieved {len(sprint_issues)} sprint issues with epic data for sprint_id {sprint_id} and team '{validated_team_name}'"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching sprint issues with epic for LLM (sprint_id: {sprint_id}, team_name: {team_name}): {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch sprint issues with epic for LLM: {str(e)}"
        )

@sprints_router.get("/sprints/{sprint_id}")
async def get_sprint(
    sprint_id: int,
    conn: Connection = Depends(get_db_connection)
):
    """
    Get a single sprint by ID.
    
    Returns all columns from the jira_sprints table for the specified sprint_id.
    
    Args:
        sprint_id: The ID of the sprint to retrieve (int4)
    
    Returns:
        JSON response with single sprint (all columns) or 404 if not found
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text("""
            SELECT *
            FROM public.jira_sprints
            WHERE sprint_id = :sprint_id
        """)
        
        logger.info(f"Executing query to get sprint with ID: {sprint_id}")
        
        result = conn.execute(query, {"sprint_id": sprint_id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Sprint with ID {sprint_id} not found"
            )
        
        # Convert row to dictionary - return all columns
        sprint_dict = dict(row._mapping)
        
        # Format date fields if they exist
        if 'start_date' in sprint_dict and sprint_dict['start_date']:
            sprint_dict['start_date'] = sprint_dict['start_date'].strftime('%Y-%m-%d') if hasattr(sprint_dict['start_date'], 'strftime') else sprint_dict['start_date']
        if 'end_date' in sprint_dict and sprint_dict['end_date']:
            sprint_dict['end_date'] = sprint_dict['end_date'].strftime('%Y-%m-%d') if hasattr(sprint_dict['end_date'], 'strftime') else sprint_dict['end_date']
        if 'created_at' in sprint_dict and sprint_dict['created_at']:
            sprint_dict['created_at'] = sprint_dict['created_at'].isoformat() if hasattr(sprint_dict['created_at'], 'isoformat') else sprint_dict['created_at']
        if 'updated_at' in sprint_dict and sprint_dict['updated_at']:
            sprint_dict['updated_at'] = sprint_dict['updated_at'].isoformat() if hasattr(sprint_dict['updated_at'], 'isoformat') else sprint_dict['updated_at']
        
        return {
            "success": True,
            "data": {
                "sprint": sprint_dict
            },
            "message": f"Retrieved sprint with ID {sprint_id}"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (like the 404 error above)
        raise
    except Exception as e:
        logger.error(f"Error fetching sprint {sprint_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch sprint: {str(e)}"
        )

