"""
Sprints Service - REST API endpoints for sprint-related operations.

This service provides endpoints for managing and retrieving sprint information.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
import logging
import re
from datetime import datetime, timedelta, date
from database_connection import get_db_connection

logger = logging.getLogger(__name__)

sprints_router = APIRouter()


def get_sprint_progress_status_with_slack(
    overall_progress_pct: Optional[float],
    start_date: Any,
    end_date: Any,
    slack_threshold: float = 20.0
) -> Optional[str]:
    """
    Determine sprint progress status based on timeline vs actual completion with 20% slack.
    
    Compares actual progress percentage (overall_progress_pct) against expected completion 
    based on how much of the sprint has elapsed.
    
    Args:
        overall_progress_pct: Actual progress percentage (0-100) from view
        start_date: Sprint start date (date, datetime, or timestamptz)
        end_date: Sprint end date (date, datetime, or timestamptz)
        slack_threshold: Percentage slack allowed (default: 20%)
    
    Returns:
        "green" if ahead of schedule (actual >= expected - slack)
        "yellow" if slightly behind (expected - 40% <= actual < expected - slack)
        "red" if significantly behind (actual < expected - 40%)
        None if unable to calculate (edge cases: missing data, invalid dates, sprint not started)
    """
    # Handle edge cases - return None instead of "green"
    if start_date is None or end_date is None:
        return None
    
    if overall_progress_pct is None:
        return None
    
    # Convert datetime/timestamptz to date if needed
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    elif isinstance(start_date, str):
        # Try to parse if it's a string
        try:
            start_date = datetime.fromisoformat(start_date.replace('Z', '+00:00')).date()
        except:
            return None
    
    if isinstance(end_date, datetime):
        end_date = end_date.date()
    elif isinstance(end_date, str):
        # Try to parse if it's a string
        try:
            end_date = datetime.fromisoformat(end_date.replace('Z', '+00:00')).date()
        except:
            return None
    
    today = date.today()
    
    # If sprint hasn't started yet - return None
    if today < start_date:
        return None
    
    # If sprint has ended
    if today >= end_date:
        # Compare actual completion to 100% expected
        if overall_progress_pct >= 100 - slack_threshold:
            return "green"
        elif overall_progress_pct >= 75:
            return "yellow"
        else:
            return "red"
    
    # Calculate expected completion based on timeline
    total_sprint_days = (end_date - start_date).days
    if total_sprint_days <= 0:
        return None
    
    days_elapsed = (today - start_date).days
    expected_completion = (days_elapsed / total_sprint_days) * 100
    
    # Determine status with slack (20%)
    if overall_progress_pct >= expected_completion - slack_threshold:
        return "green"
    elif overall_progress_pct >= expected_completion - 40.0:
        return "yellow"
    else:
        return "red"


def get_active_sprint_summary_data_db(
    issue_type: Optional[str] = None,
    team_names: Optional[List[str]] = None,
    conn: Connection = None
) -> List[Dict[str, Any]]:
    """
    Get active sprint summary data from the database function get_active_sprint_summary_with_issue_keys.
    
    This is a reusable helper function that can be called by endpoints and report functions.
    Returns raw data from the database function without any calculations or formatting.
    
    Args:
        issue_type (Optional[str]): Issue type filter (e.g., "Story", "Bug", "Task"). If None, passes NULL to function.
        team_names (Optional[List[str]]): List of team names to filter by. If None, passes NULL to function (returns all teams).
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of dictionaries with raw data from database function (all columns as-is)
    """
    try:
        # Build parameters for the function call
        params = {}
        
        # Handle issue_type parameter (pass NULL if not provided)
        if issue_type:
            params['p_issue_type'] = issue_type
        else:
            params['p_issue_type'] = None
        
        # Build query - pass team_names as array or NULL
        # Note: Function parameter is p_teams (varchar), but we pass as text[] array
        if team_names:
            # Pass array of team names to function
            params['p_teams'] = team_names
            sql_query_text = text("""
                SELECT * FROM public.get_active_sprint_summary_with_issue_keys(
                    :p_issue_type,
                    CAST(:p_teams AS text[])
                )
            """)
            
            logger.info(f"Executing SQL for active sprint summary: issue_type={issue_type}, teams={team_names}")
        else:
            # Pass NULL for all teams
            sql_query_text = text("""
                SELECT * FROM public.get_active_sprint_summary_with_issue_keys(
                    :p_issue_type,
                    NULL
                )
            """)
            
            logger.info(f"Executing SQL for active sprint summary: issue_type={issue_type}, all teams")
        
        # Execute query with parameters (SECURE: prevents SQL injection)
        result = conn.execute(sql_query_text, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries - return raw data (no processing)
        raw_data = []
        for row in rows:
            row_dict = dict(row._mapping)
            raw_data.append(row_dict)
        
        logger.info(f"Retrieved {len(raw_data)} active sprint summary records from database")
        
        return raw_data
            
    except Exception as e:
        logger.error(f"Error fetching active sprint summary data (issue_type={issue_type}, team_names={team_names}): {e}")
        raise e


def process_active_sprint_summary_data(
    raw_data: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Process active sprint summary raw data and add calculated fields.
    
    This is a reusable helper function that:
    1. Calculates overall_progress_pct from issues_done, issues_at_start, issues_added
    2. Calculates overall_progress_pct_color based on progress vs timeline
    3. Formats date/datetime fields
    
    This function can be called by endpoints and report functions to ensure consistent calculations.
    
    Args:
        raw_data (List[Dict[str, Any]]): Raw data from get_active_sprint_summary_data_db()
    
    Returns:
        list: List of dictionaries with processed data including calculated fields
    """
    processed_data = []
    
    for row in raw_data:
        summary_dict = dict(row)  # Create a copy to avoid modifying original
        
        # Store original date values for calculation before formatting
        start_date_raw = summary_dict.get('start_date')
        end_date_raw = summary_dict.get('end_date')
        
        # Extract values needed for overall_progress_pct calculation
        # Using exact field names from function definition
        total_issues_done = summary_dict.get('total_issues_done')
        issues_at_start = summary_dict.get('issues_at_start')
        issues_added = summary_dict.get('issues_added')
        
        # Calculate overall_progress_pct in Python
        # Formula: (total_issues_done / (issues_at_start + issues_added)) * 100
        overall_progress_pct = None
        if total_issues_done is not None and issues_at_start is not None and issues_added is not None:
            denominator = issues_at_start + issues_added
            if denominator > 0:
                overall_progress_pct = (total_issues_done / denominator) * 100
            else:
                overall_progress_pct = 0.0
        
        # Add calculated field to summary_dict
        summary_dict['overall_progress_pct'] = overall_progress_pct
        
        # Calculate overall_progress_pct_color (using existing function)
        if start_date_raw is not None and end_date_raw is not None and overall_progress_pct is not None:
            summary_dict['overall_progress_pct_color'] = get_sprint_progress_status_with_slack(
                overall_progress_pct=float(overall_progress_pct),
                start_date=start_date_raw,
                end_date=end_date_raw,
                slack_threshold=20.0
            )
        else:
            summary_dict['overall_progress_pct_color'] = None
        
        # Calculate issues_added_color
        # Based on percentage of issues_added compared to issues_at_start
        issues_added_color = None
        if issues_added is not None and issues_at_start is not None and issues_at_start > 0:
            issues_added_pct = (issues_added / issues_at_start) * 100
            if issues_added_pct > 50:
                issues_added_color = "red"
            elif issues_added_pct > 30:
                issues_added_color = "yellow"
            else:
                issues_added_color = "default"
        
        summary_dict['issues_added_color'] = issues_added_color
        
        # Format date/datetime fields if they exist
        for key, value in summary_dict.items():
            if value is not None:
                if hasattr(value, 'strftime'):
                    # Date field
                    summary_dict[key] = value.strftime('%Y-%m-%d')
                elif hasattr(value, 'isoformat'):
                    # Datetime field
                    summary_dict[key] = value.isoformat()
        
        processed_data.append(summary_dict)
    
    return processed_data


def validate_team_name(team_name: str) -> str:
    """
    Validate team name (basic validation only).
    """
    if not team_name or not isinstance(team_name, str):
        raise HTTPException(status_code=400, detail="Team name is required and must be a string")
    
    validated = team_name.strip()
    
    if not validated:
        raise HTTPException(status_code=400, detail="Team name cannot be empty")
    
    if len(validated) > 100:  # Reasonable length limit
        raise HTTPException(status_code=400, detail="Team name is too long (max 100 characters)")
    
    return validated

def validate_group_name(group_name: str) -> str:
    """
    Validate group name (basic validation only).
    """
    if not group_name or not isinstance(group_name, str):
        raise HTTPException(status_code=400, detail="Group name is required and must be a string")
    
    validated = group_name.strip()
    
    if not validated:
        raise HTTPException(status_code=400, detail="Group name cannot be empty")
    
    if len(validated) > 100:  # Reasonable length limit
        raise HTTPException(status_code=400, detail="Group name is too long (max 100 characters)")
    
    return validated

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
    issue_type: Optional[str] = Query(None, description="Issue type filter (optional, e.g., 'Story', 'Bug', 'Task'). If not provided, returns all issue types."),
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true). If not provided, returns all summaries."),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get active sprint summary by team from the get_active_sprint_summary_with_issue_keys database function.
    
    Returns all columns from the function for the specified team(s), group, and/or issue type.
    If team_name is not provided, returns summaries for all teams.
    If issue_type is not provided, returns summaries for all issue types.
    
    The function does not return overall_progress_pct, so it is calculated in Python using:
    overall_progress_pct = (issues_done / (issues_at_start + issues_added)) * 100
    
    This endpoint uses reusable helper functions:
    - get_active_sprint_summary_data_db(): Fetches raw data from database
    - process_active_sprint_summary_data(): Adds calculated fields and formats data
    
    Args:
        issue_type: Optional issue type filter (e.g., "Story", "Bug", "Task"). If not provided, returns all issue types.
        team_name: Optional team name or group name (if isGroup=true). If not provided, returns all summaries.
        isGroup: If true, team_name is treated as a group name and returns summaries for all teams in that group
    
    Returns:
        JSON response with active sprint summary (all columns from function + calculated overall_progress_pct)
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Resolve team names using shared helper function
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Build filter description for logging and response
        filter_description = None
        validated_name = None
        
        if team_name is not None:
            if isGroup:
                validated_name = team_name
                if team_names_list:
                    filter_description = f"group '{team_name}' ({len(team_names_list)} teams)"
                    logger.info(f"Found {len(team_names_list)} teams in group '{team_name}': {team_names_list}")
            else:
                validated_name = team_name
                if team_names_list:
                    filter_description = f"team '{team_name}'"
        
        # Get raw data from database using reusable function
        raw_summaries = get_active_sprint_summary_data_db(
            issue_type=issue_type,
            team_names=team_names_list if team_names_list else None,
            conn=conn
        )
        
        # Process data and add calculated fields using reusable function
        summaries = process_active_sprint_summary_data(raw_summaries)
        
        # Build response message
        if filter_description:
            if issue_type:
                message = f"Retrieved active sprint summary for {filter_description} (issue_type: {issue_type})"
            else:
                message = f"Retrieved active sprint summary for {filter_description}"
        else:
            if issue_type:
                message = f"Retrieved active sprint summary for all teams (issue_type: {issue_type})"
            else:
                message = f"Retrieved active sprint summary for all teams"
        
        response_data = {
            "summaries": summaries,
            "count": len(summaries)
        }
        
        # Add metadata based on what was filtered
        if validated_name:
            if isGroup:
                response_data["group_name"] = validated_name
                if team_names_list:
                    response_data["teams_in_group"] = team_names_list
            else:
                response_data["team_name"] = validated_name
        
        # Optionally add issue_type to metadata if provided
        if issue_type:
            response_data["issue_type"] = issue_type
        
        return {
            "success": True,
            "data": response_data,
            "message": message
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching active sprint summary (team_name={team_name}, isGroup={isGroup}, issue_type={issue_type}): {e}")
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
        # sprint_ids is an array column, so we check if :sprint_id exists in that array
        query = text("""
            SELECT *
            FROM public.sprint_issues_with_epic_for_llm
            WHERE :sprint_id = ANY(sprint_ids) AND team_name = :team_name
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

@sprints_router.get("/sprints/sprint-predictability")
async def get_sprint_predictability(
    months: int = Query(3, description="Number of months to look back (1, 2, 3, 4, 6, 9)", ge=1, le=12),
    team_name: Optional[str] = Query(None, description="Optional team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get sprint predictability metrics from the get_sprint_predictability_metrics_with_issues database function.
    
    Returns all columns from the function for sprints within the specified time period.
    Optionally filters by team name(s) if provided. Supports filtering by single team or all teams in a group.
    
    Args:
        months: Number of months to look back (default: 3, valid: 1, 2, 3, 4, 6, 9)
        team_name: Optional team name or group name (if isGroup=true). If not provided, returns all teams.
        isGroup: If true, team_name is treated as a group name and returns metrics for all teams in that group
    
    Returns:
        JSON response with sprint predictability metrics (all columns)
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Validate months parameter
        if months not in [1, 2, 3, 4, 6, 9]:
            raise HTTPException(
                status_code=400, 
                detail="Months parameter must be one of: 1, 2, 3, 4, 6, 9"
            )
        
        # Resolve team names using shared helper function
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Build query and parameters
        if team_names_list:
            # Pass array of team names to function
            params = {"months": months, "team_names": team_names_list}
            query = text("""
                SELECT * FROM public.get_sprint_predictability_metrics_with_issues(:months, CAST(:team_names AS text[]))
            """)
            if isGroup:
                logger.info(f"Executing query to get sprint predictability metrics: months={months}, group='{team_name}' ({len(team_names_list)} teams)")
            else:
                logger.info(f"Executing query to get sprint predictability metrics: months={months}, team_name={team_names_list[0]}")
        else:
            # Pass NULL for all teams
            params = {"months": months}
            query = text("""
                SELECT * FROM public.get_sprint_predictability_metrics_with_issues(:months, NULL)
            """)
            logger.info(f"Executing query to get sprint predictability metrics: months={months}, team_name=NULL (all teams)")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries - return all fields from database function
        predictability_data = []
        for row in rows:
            data_dict = dict(row._mapping)
            
            # Format all date fields if they exist
            for key, value in data_dict.items():
                if value is not None and hasattr(value, 'strftime'):
                    data_dict[key] = value.strftime('%Y-%m-%d')
            
            predictability_data.append(data_dict)
        
        # Build response message
        message = f"Retrieved {len(predictability_data)} sprint predictability records (last {months} months)"
        if team_name:
            if isGroup:
                message += f" for all teams in group '{team_name}' ({len(team_names_list)} teams)"
            else:
                message += f" for team '{team_name}'"
        
        response_data = {
            "sprint_predictability": predictability_data,
            "count": len(predictability_data),
            "months": months
        }
        
        # Add metadata based on what was filtered
        if team_name:
            if isGroup:
                response_data["group_name"] = team_name
                response_data["teams_in_group"] = team_names_list
            else:
                response_data["team_name"] = team_name
        
        return {
            "success": True,
            "data": response_data,
            "message": message
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching sprint predictability metrics: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch sprint predictability metrics: {str(e)}"
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

