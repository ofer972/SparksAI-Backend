"""
Issues Service - REST API endpoints for issue-related operations.

This service provides endpoints for managing and retrieving issue information.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
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
                parent_key
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


@issues_router.get("/issues/epics-hierarchy")
async def get_epics_hierarchy(
    pi: Optional[str] = Query(None, description="Filter by PI (quarter_pi_of_epic)"),
    team_name: Optional[str] = Query(None, description="Filter by team name (team_name_of_epic)"),
    limit: int = Query(500, description="Number of records to return (default: 500, max: 1000)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get epic hierarchy data from epic_hierarchy_with_progress view.
    
    Returns all columns from the view with optional filtering by PI and/or team name.
    
    Args:
        pi: Optional filter by PI (filters on quarter_pi_of_epic column)
        team_name: Optional filter by team name (filters on team_name_of_epic column)
        limit: Number of records to return (default: 500, max: 1000)
    
    Returns:
        JSON response with epic hierarchy data list and metadata
    """
    try:
        # Validate limit
        validated_limit = validate_limit(limit)
        
        # Build WHERE clause conditions based on provided filters
        where_conditions = []
        params = {"limit": validated_limit}
        
        if pi:
            where_conditions.append('"Quarter PI of Epic" = :pi')
            params["pi"] = pi
        
        if team_name:
            where_conditions.append('"Team Name of Epic" = :team_name')
            params["team_name"] = team_name
        
        # Build SQL query
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        query = text(f"""
            SELECT *
            FROM epic_hierarchy_with_progress
            WHERE {where_clause}
            LIMIT :limit
        """)
        
        logger.info(f"Executing query to get epic hierarchy: pi={pi}, team_name={team_name}, limit={validated_limit}")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        issues = []
        for row in rows:
            issues.append(dict(row._mapping))
        
        return {
            "success": True,
            "data": {
                "issues": issues,
                "count": len(issues),
                "limit": validated_limit
            },
            "message": f"Retrieved {len(issues)} epic hierarchy records"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching epic hierarchy: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch epic hierarchy: {str(e)}"
        )


@issues_router.get("/issues/issue-status-duration")
async def get_issue_status_duration(
    months: int = Query(3, description="Number of months to look back (1, 2, 3, 4, 6, 9)", ge=1, le=12),
    issue_type: Optional[str] = Query(None, description="Filter by issue type"),
    team_name: Optional[str] = Query(None, description="Filter by team name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get average duration by status name from issue_status_durations table.
    
    Returns average duration in days for each status name, filtered by time period and optional filters.
    Only includes issues with status_category = 'In Progress'.
    
    Args:
        months: Number of months to look back (default: 3, valid: 1, 2, 3, 4, 6, 9)
        issue_type: Optional filter by issue type
        team_name: Optional filter by team name
    
    Returns:
        JSON response with status duration data list and metadata
    """
    try:
        # Validate months parameter (same validation as closed sprints)
        if months not in [1, 2, 3, 4, 6, 9]:
            raise HTTPException(
                status_code=400, 
                detail="Months parameter must be one of: 1, 2, 3, 4, 6, 9"
            )
        
        # Calculate start date based on months parameter
        start_date = datetime.now().date() - timedelta(days=months * 30)
        
        # min_duration: 0.003472222222222222 days (approximately 5 minutes)
        min_duration = 0.003472222222222222
        
        # Build WHERE clause conditions
        where_conditions = [
            "isd.status_category = 'In Progress'",
            "isd.duration_days >= :min_duration",
            "isd.time_exited >= :start_date"
        ]
        
        params = {
            "min_duration": min_duration,
            "start_date": start_date.strftime("%Y-%m-%d")
        }
        
        # Add optional filters
        if issue_type:
            where_conditions.append("isd.issue_type = :issue_type")
            params["issue_type"] = issue_type
        
        if team_name:
            where_conditions.append("isd.team_name = :team_name")
            params["team_name"] = team_name
        
        # Build SQL query
        where_clause = " AND ".join(where_conditions)
        
        query = text(f"""
            SELECT 
                isd.status_name,
                AVG(isd.duration_days) as avg_duration_days
            FROM public.issue_status_durations isd
            WHERE {where_clause}
            GROUP BY isd.status_name
            ORDER BY isd.status_name
        """)
        
        logger.info(f"Executing query to get issue status duration: months={months}, issue_type={issue_type}, team_name={team_name}")
        logger.info(f"Parameters: start_date={start_date}, min_duration={min_duration}")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        status_durations = []
        for row in rows:
            status_durations.append({
                "status_name": row[0],
                "avg_duration_days": float(row[1]) if row[1] else 0.0
            })
        
        return {
            "success": True,
            "data": {
                "status_durations": status_durations,
                "count": len(status_durations),
                "months": months
            },
            "message": f"Retrieved {len(status_durations)} status duration records (last {months} months)"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching issue status duration: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch issue status duration: {str(e)}"
        )


@issues_router.get("/issues/release-predictability")
async def get_release_predictability(
    months: int = Query(3, description="Number of months to look back", ge=1, le=12),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get release predictability analysis from the release_predictability_analysis table.
    
    Returns release predictability metrics including version name, project key, dates,
    epic completion percentages, and other issues completion percentages.
    
    Args:
        months: Number of months to look back (default: 3)
    
    Returns:
        JSON response with release predictability data list and metadata
    """
    try:
        # Calculate start date based on months parameter
        start_date = datetime.now().date() - timedelta(days=months * 30)
        
        # SECURE: Parameterized query prevents SQL injection
        query = text("""
            SELECT 
                version_name, 
                project_key, 
                release_start_date, 
                release_date, 
                total_epics_in_scope, 
                epics_completed, 
                epic_percent_completed, 
                total_other_issues_in_scope, 
                other_issues_completed, 
                other_issues_percent_completed 
            FROM public.release_predictability_analysis 
            WHERE release_start_date >= :start_date
            ORDER BY release_start_date DESC
        """)
        
        logger.info(f"Executing query to get release predictability: months={months}")
        
        result = conn.execute(query, {"start_date": start_date.strftime("%Y-%m-%d")})
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        predictability_data = []
        for row in rows:
            data_dict = dict(row._mapping)
            
            # Format date fields if they exist
            for key, value in data_dict.items():
                if value is not None and hasattr(value, 'strftime'):
                    data_dict[key] = value.strftime('%Y-%m-%d')
            
            predictability_data.append(data_dict)
        
        return {
            "success": True,
            "data": {
                "release_predictability": predictability_data,
                "count": len(predictability_data),
                "months": months
            },
            "message": f"Retrieved {len(predictability_data)} release predictability records (last {months} months)"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching release predictability: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch release predictability: {str(e)}"
        )


@issues_router.get("/issues/issues-grouped-by-priority")
async def get_issues_grouped_by_priority(
    issue_type: Optional[str] = Query(None, description="Filter by issue type"),
    team_name: Optional[str] = Query(None, description="Filter by team name"),
    status_category: Optional[str] = Query(None, description="Filter by status category"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get issues grouped by priority from the jira_issues table.
    
    Returns the count of issues per priority level, with optional filtering by issue_type, team_name, and/or status_category.
    
    Args:
        issue_type: Optional filter by issue type
        team_name: Optional filter by team name
        status_category: Optional filter by status category
    
    Returns:
        JSON response with issues grouped by priority (priority, status_category, and issue_count)
    """
    try:
        # Build WHERE clause conditions based on provided filters
        where_conditions = []
        params = {}
        
        if issue_type:
            where_conditions.append("issue_type = :issue_type")
            params["issue_type"] = issue_type
        
        if team_name:
            where_conditions.append("team_name = :team_name")
            params["team_name"] = team_name
        
        if status_category:
            where_conditions.append("status_category = :status_category")
            params["status_category"] = status_category
        else:
            # Default: exclude "Done" to get only open issues
            where_conditions.append("status_category != 'Done'")
        
        # Build SQL query
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT 
                priority,
                status_category,
                COUNT(*) as issue_count
            FROM {config.WORK_ITEMS_TABLE}
            WHERE {where_clause}
            GROUP BY priority, status_category
            ORDER BY priority, status_category
        """)
        
        logger.info(f"Executing query to get issues grouped by priority: issue_type={issue_type}, team_name={team_name}, status_category={status_category}")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        issues_by_priority = []
        for row in rows:
            issues_by_priority.append({
                "priority": row[0] if row[0] is not None else "Unspecified",
                "status_category": row[1] if row[1] is not None else "Unspecified",
                "issue_count": int(row[2])
            })
        
        return {
            "success": True,
            "data": {
                "issues_by_priority": issues_by_priority,
                "count": len(issues_by_priority)
            },
            "message": f"Retrieved {len(issues_by_priority)} priority groups"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching issues grouped by priority: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch issues grouped by priority: {str(e)}"
        )


@issues_router.get("/issues/issues-grouped-by-team")
async def get_issues_grouped_by_team(
    issue_type: Optional[str] = Query(None, description="Filter by issue type"),
    status_category: Optional[str] = Query(None, description="Filter by status category"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get issues grouped by team with priority breakdown from the jira_issues table.
    
    Returns issues grouped by team_name, with each team containing a breakdown of priorities and counts.
    Optional filtering by issue_type and/or status_category.
    
    Args:
        issue_type: Optional filter by issue type
        status_category: Optional filter by status category (default: excludes "Done" to get only open issues)
    
    Returns:
        JSON response with issues grouped by team, each team containing priorities array with counts
    """
    try:
        # Build WHERE clause conditions based on provided filters
        where_conditions = []
        params = {}
        
        if issue_type:
            where_conditions.append("issue_type = :issue_type")
            params["issue_type"] = issue_type
        
        if status_category:
            where_conditions.append("status_category = :status_category")
            params["status_category"] = status_category
        else:
            # Default: exclude "Done" to get only open issues
            where_conditions.append("status_category != 'Done'")
        
        # Build SQL query
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT 
                team_name,
                priority,
                COUNT(*) as issue_count
            FROM {config.WORK_ITEMS_TABLE}
            WHERE {where_clause}
            GROUP BY team_name, priority
            ORDER BY team_name, priority
        """)
        
        logger.info(f"Executing query to get issues grouped by team: issue_type={issue_type}, status_category={status_category}")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        # Group by team_name into nested structure
        teams_dict = {}
        for row in rows:
            team = row[0] if row[0] is not None else "Unspecified"
            priority = row[1] if row[1] is not None else "Unspecified"
            count = int(row[2])
            
            if team not in teams_dict:
                teams_dict[team] = {"priorities": [], "total_issues": 0}
            
            teams_dict[team]["priorities"].append({
                "priority": priority,
                "issue_count": count
            })
            teams_dict[team]["total_issues"] += count
        
        # Convert to list format
        issues_by_team = []
        for team_name, data in teams_dict.items():
            issues_by_team.append({
                "team_name": team_name,
                "priorities": data["priorities"],
                "total_issues": data["total_issues"]
            })
        
        return {
            "success": True,
            "data": {
                "issues_by_team": issues_by_team,
                "count": len(issues_by_team)
            },
            "message": f"Retrieved {len(issues_by_team)} teams with priority breakdown"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching issues grouped by team: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch issues grouped by team: {str(e)}"
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