"""
PIs Service - REST API endpoints for PI-related operations.

This service provides endpoints for managing and retrieving PI information.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Union, Optional
from datetime import date, datetime
import logging
from database_connection import get_db_connection
from database_pi import fetch_pi_predictability_data, fetch_pi_burndown_data, fetch_scope_changes_data, fetch_pi_summary_data
import config

logger = logging.getLogger(__name__)

pis_router = APIRouter()


# Helper functions for PI progress calculations (similar to sprint progress)
def calculate_days_left_pi(end_date: date) -> Optional[int]:
    """
    Calculate days left in PI as integer (inclusive counting).
    
    Args:
        end_date: PI end date (date or datetime object)
    
    Returns:
        Integer representing days left (inclusive of today and end date)
        - If today is end date: returns 1
        - If end date is in future: returns (end_date - today).days + 1
        - If PI ended: returns 0
        - If end_date is None: returns None
    """
    if end_date is None:
        return None
    
    # Convert datetime to date if needed
    if isinstance(end_date, datetime):
        end_date = end_date.date()
    
    today = date.today()
    
    if end_date < today:
        return 0  # PI ended
    else:
        # Inclusive counting: (end_date - today).days + 1
        return (end_date - today).days + 1


def calculate_days_in_pi(start_date: date, end_date: date) -> Optional[int]:
    """
    Calculate total days in PI as integer (inclusive counting).
    
    Args:
        start_date: PI start date (date or datetime object)
        end_date: PI end date (date or datetime object)
    
    Returns:
        Integer representing total days in PI (inclusive of start and end dates)
        - Returns (end_date - start_date).days + 1
        - If either date is None: returns None
    """
    if start_date is None or end_date is None:
        return None
    
    # Convert datetime to date if needed
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if isinstance(end_date, datetime):
        end_date = end_date.date()
    
    # Inclusive counting: (end_date - start_date).days + 1
    return (end_date - start_date).days + 1


def get_pi_percent_completed_status(
    percent_completed: float,
    start_date: date,
    end_date: date,
    slack_threshold: float = 15.0
) -> str:
    """
    Determine PI completion status based on actual vs expected completion with slack.
    
    Compares actual completion percentage against expected completion based on
    timeline progress, with a slack threshold to account for normal variance.
    
    Args:
        percent_completed: Actual completion percentage (0-100)
        start_date: PI start date (date or datetime object)
        end_date: PI end date (date or datetime object)
        slack_threshold: Allowed slack percentage (default: 15.0)
    
    Returns:
        "green" if on track or ahead
        "yellow" if slightly behind (within 25% of expected)
        "red" if significantly behind
    """
    if start_date is None or end_date is None:
        return "green"
    
    # Convert datetime to date if needed
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if isinstance(end_date, datetime):
        end_date = end_date.date()
    
    today = date.today()
    
    # If PI hasn't started yet
    if today < start_date:
        return "green"
    
    # If PI has ended
    if today >= end_date:
        # Compare actual completion to 100% expected
        if percent_completed >= 100 - slack_threshold:
            return "green"
        elif percent_completed >= 75:
            return "yellow"
        else:
            return "red"
    
    # Calculate expected completion based on timeline
    total_pi_days = (end_date - start_date).days
    if total_pi_days <= 0:
        return "green"
    
    days_elapsed = (today - start_date).days
    expected_completion = (days_elapsed / total_pi_days) * 100
    
    # Determine status with slack
    if percent_completed >= expected_completion - slack_threshold:
        return "green"
    elif percent_completed >= expected_completion - 25.0:
        return "yellow"
    else:
        return "red"


def get_wip_count_status(in_progress_count: int, total_count: int) -> str:
    """
    Calculate WIP count status color based on percentage of epics in progress.
    
    Args:
        in_progress_count: Number of epics in progress
        total_count: Total number of epics
    
    Returns:
        str: "green", "yellow", or "red" based on the percentage
        - "green" if in-progress epics <= 30% of total
        - "yellow" if in-progress epics > 30% and <= 50% of total
        - "red" if in-progress epics > 50% of total
    """
    # Handle edge cases
    if total_count == 0:
        return "green"
    
    in_progress_percent = (in_progress_count / total_count) * 100
    
    if in_progress_percent <= 30:
        return "green"
    elif in_progress_percent <= 50:
        return "yellow"
    else:  # > 50%
        return "red"


def get_progress_delta_pct_status(progress_delta_pct: Optional[float]) -> str:
    """
    Calculate status color based on progress_delta_pct value.
    
    Args:
        progress_delta_pct: Progress delta percentage value
    
    Returns:
        str: "green", "yellow", or "red" based on the value
        - "green" if progress_delta_pct > -20
        - "yellow" if progress_delta_pct >= -40 and <= -20
        - "red" if progress_delta_pct < -40
    """
    if progress_delta_pct is None:
        return "green"  # Default to green if value is None
    
    if progress_delta_pct > -20:
        return "green"
    elif progress_delta_pct >= -40 and progress_delta_pct <= -20:
        return "yellow"
    else:  # progress_delta_pct < -40
        return "red"


def fetch_wip_data_from_db(
    pi: str,
    team: Optional[str] = None,
    project: Optional[str] = None,
    conn: Connection = None
) -> Dict[str, Any]:
    """
    Fetch WIP (Work In Progress) data from jira_issues table.
    Reusable helper function to calculate WIP metrics for epics.
    
    Args:
        pi: PI name (required)
        team: Team name filter (optional)
        project: Project key filter (optional)
        conn: Database connection
    
    Returns:
        Dictionary with WIP metrics:
        - total_epics: Total number of epics
        - in_progress_epics: Number of epics in progress
        - in_progress_percentage: Percentage of epics in progress
        - count_in_progress_status: Status color (green/yellow/red)
    """
    # Build WHERE clause conditions
    where_conditions = [
        "issue_type = 'Epic'",
        "quarter_pi = :pi"
    ]
    
    params = {
        "pi": pi
    }
    
    # Add optional filters
    if team:
        where_conditions.append("team_name = :team")
        params["team"] = team
    
    if project:
        where_conditions.append("project_key = :project")
        params["project"] = project
    
    # Build SQL query
    where_clause = " AND ".join(where_conditions)
    
    query = text(f"""
        SELECT 
            COUNT(*) as total_epics,
            COUNT(CASE WHEN status_category = 'In Progress' THEN 1 END) as in_progress_epics
        FROM public.jira_issues
        WHERE {where_clause}
    """)
    
    logger.info(f"Executing WIP query for PI: {pi}, team={team}, project={project}")
    
    result = conn.execute(query, params)
    row = result.fetchone()
    
    if not row:
        total_epics = 0
        in_progress_epics = 0
    else:
        total_epics = int(row[0]) if row[0] else 0
        in_progress_epics = int(row[1]) if row[1] else 0
    
    # Calculate percentage
    in_progress_percentage = (in_progress_epics / total_epics * 100) if total_epics > 0 else 0.0
    
    # Calculate status
    count_in_progress_status = get_wip_count_status(in_progress_epics, total_epics)
    
    return {
        "total_epics": total_epics,
        "in_progress_epics": in_progress_epics,
        "in_progress_percentage": round(in_progress_percentage, 2),
        "count_in_progress_status": count_in_progress_status
    }


def get_pi_in_progress_issues_status(
    in_progress_issues: int,
    total_issues: int
) -> str:
    """
    Determine in-progress issues status based on percentage of total issues.
    
    High WIP (work in progress) indicates potential bottlenecks.
    
    Args:
        in_progress_issues: Number of issues in progress
        total_issues: Total number of issues in PI
    
    Returns:
        "green" if < 40% of issues are in progress
        "yellow" if 40-60% of issues are in progress
        "red" if > 60% of issues are in progress
    """
    # Handle edge cases
    if total_issues == 0:
        return "green"
    
    in_progress_percent = (in_progress_issues / total_issues) * 100
    
    if in_progress_percent > 60:
        return "red"
    elif in_progress_percent >= 40:
        return "yellow"
    else:
        return "green"

@pis_router.get("/pis/getPis")
async def get_pis(conn: Connection = Depends(get_db_connection)):
    """
    Get all PIs from pis table.
    Uses parameterized queries to prevent SQL injection.
    
    Returns:
        JSON response with list of PIs and count
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT * 
            FROM {config.PIS_TABLE} 
            ORDER BY pi_name
        """)
        
        logger.info(f"Executing query to get all PIs from pis table")
        
        # Execute query with connection from dependency
        result = conn.execute(query)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        pis = []
        for row in rows:
            pis.append({
                "pi_name": row[0],
                "start_date": row[1],
                "end_date": row[2],
                "planning_grace_days": row[3],
                "prep_grace_days": row[4],
                "updated_at": row[5]
            })
        
        return {
            "success": True,
            "data": {
                "pis": pis,
                "count": len(pis)
            },
            "message": f"Retrieved {len(pis)} PIs"
        }
    
    except Exception as e:
        logger.error(f"Error fetching PIs: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch PIs: {str(e)}"
        )


@pis_router.get("/pis/predictability")
async def get_pi_predictability(
    pi_names: Union[str, List[str]] = Query(..., description="Single PI name or array of PI names (comma-separated)"),
    team_name: str = Query(None, description="Single team name filter (or 'ALL SUMMARY' for summary)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get PI predictability report data for specified PI(s).
    
    Supports multiple PI names (array or comma-separated) and single team name.
    Returns all columns from get_pi_predictability_by_team database function.
    
    Parameters:
        pi_names: Single PI name or array of PI names (comma-separated)
        team_name: Optional single team name for filtering (use 'ALL SUMMARY' for aggregated view)
    
    Returns:
        JSON response with PI predictability data (all columns)
    """
    try:
        # Handle pi_names parameter - can be comma-separated string or already a list
        if not pi_names:
            raise HTTPException(
                status_code=400,
                detail="pi_names parameter is required"
            )
        
        # Convert to list if it's a string
        if isinstance(pi_names, str):
            # Check if it's comma-separated
            if ',' in pi_names:
                pi_names = [name.strip() for name in pi_names.split(',')]
            else:
                pi_names = [pi_names]
        
        logger.info(f"Fetching PI predictability data for PIs: {pi_names}")
        logger.info(f"Team filter: {team_name if team_name else 'None'}")
        
        # Call database function (logic copied from old project)
        predictability_data = fetch_pi_predictability_data(
            pi_names=pi_names,
            team_name=team_name,
            conn=conn
        )
        
        return {
            "success": True,
            "data": {
                "predictability_data": predictability_data,
                "count": len(predictability_data),
                "pi_names": pi_names,
                "team_name": team_name
            },
            "message": f"Retrieved PI predictability data for {len(predictability_data)} records"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (like the 400 error above)
        raise
    except Exception as e:
        logger.error(f"Error fetching PI predictability data: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch PI predictability data: {str(e)}"
        )


@pis_router.get("/pis/burndown")
async def get_pi_burndown(
    pi: str = Query(..., description="PI name (mandatory)"),
    project: str = Query(None, description="Project key filter"),
    issue_type: str = Query(None, description="Issue type filter"),
    team: str = Query(None, description="Team name filter"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get PI burndown data for a specific PI.
    
    Parameters:
        pi: PI name (mandatory)
        project: Project key filter (optional)
        issue_type: Issue type filter (optional, defaults to 'Epic')
        team: Team name filter (optional)
    
    Returns:
        JSON response with PI burndown data
    """
    try:
        # Validate pi parameter (mandatory)
        if not pi:
            raise HTTPException(
                status_code=400,
                detail="pi parameter is required"
            )
        
        # Set default value of "Epic" for issue_type if empty/None
        if issue_type is None or issue_type == "":
            issue_type = "Epic"
        
        logger.info(f"Fetching PI burndown data for PI: {pi}")
        logger.info(f"Filters: project={project}, issue_type={issue_type}, team={team}")
        
        # Call database function (logic copied from old project)
        burndown_data = fetch_pi_burndown_data(
            pi_name=pi,
            project_keys=project,
            issue_type=issue_type,
            team_names=team,
            conn=conn
        )
        
        return {
            "success": True,
            "data": {
                "burndown_data": burndown_data,
                "count": len(burndown_data),
                "pi": pi,
                "project": project,
                "issue_type": issue_type,
                "team": team
            },
            "message": f"Retrieved PI burndown data for {len(burndown_data)} records"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (like the 400 error above)
        raise
    except Exception as e:
        logger.error(f"Error fetching PI burndown data: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch PI burndown data: {str(e)}"
        )


@pis_router.get("/pis/scope-changes")
async def get_scope_changes(
    quarter: Union[str, List[str]] = Query(..., description="Quarter/PI name(s) to get scope changes for (can be single or multiple)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get scope changes data for specified quarters/PIs.
    
    Parameters:
        quarter: Quarter/PI name(s) - can be a single value or multiple (e.g., quarter=2025-Q1&quarter=2025-Q2)
    
    Returns:
        JSON response with scope changes data
    """
    try:
        # Validate quarter parameter (mandatory)
        if not quarter:
            raise HTTPException(
                status_code=400,
                detail="quarter parameter is required"
            )
        
        # Normalize quarter to a list
        if isinstance(quarter, str):
            quarters = [quarter]
        else:
            quarters = quarter
        
        # Validate we have at least one quarter
        if not quarters or len(quarters) == 0:
            raise HTTPException(
                status_code=400,
                detail="At least one quarter must be provided"
            )
        
        logger.info(f"Fetching scope changes data for quarters: {quarters}")
        
        # Call database function (logic copied from old project)
        scope_data = fetch_scope_changes_data(
            quarters=quarters,
            conn=conn
        )
        
        return {
            "success": True,
            "data": {
                "scope_data": scope_data,
                "count": len(scope_data),
                "quarters": quarters
            },
            "message": f"Retrieved scope changes data for {len(scope_data)} records"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (like the 400 error above)
        raise
    except Exception as e:
        logger.error(f"Error fetching scope changes data: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch scope changes data: {str(e)}"
        )


@pis_router.get("/pis/get-pi-status-for-today")
async def get_pi_status_for_today(
    pi: str = Query(None, description="PI name filter"),
    project: str = Query(None, description="Project key filter"),
    issue_type: str = Query(None, description="Issue type filter"),
    team: str = Query(None, description="Team name filter"),
    plan_grace_period: int = Query(None, description="Planned grace period in days"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get PI status for today using the get_pi_summary_data database function.
    
    Returns all columns from the SELECT * query of get_pi_summary_data function.
    
    Parameters:
        pi: PI name filter (optional)
        project: Project key filter (optional)
        issue_type: Issue type filter (optional)
        team: Team name filter (optional)
        plan_grace_period: Planned grace period in days (optional)
    
    Returns:
        JSON response with PI summary data (all columns from database function)
    """
    try:
        # Set default value of 5 for plan_grace_period if empty/None
        if plan_grace_period is None:
            plan_grace_period = 5
        
        # Set default value of "Epic" for issue_type if empty/None
        if issue_type is None or issue_type == "":
            issue_type = "Epic"
        
        logger.info(f"Fetching PI status for today")
        logger.info(f"Parameters: pi={pi}, project={project}, issue_type={issue_type}, team={team}, plan_grace_period={plan_grace_period}")
        
        # Call database function
        summary_data = fetch_pi_summary_data(
            target_pi_name=pi,
            target_project_keys=project,
            target_issue_type=issue_type,
            target_team_names=team,
            planned_grace_period_days=plan_grace_period,
            conn=conn
        )
        
        # Fetch WIP data using the same logic as WIP endpoint
        wip_data = None
        if pi:  # Only fetch WIP if PI is provided
            wip_data = fetch_wip_data_from_db(
                pi=pi,
                team=team,
                project=project,
                conn=conn
            )
        
        # Add progress_delta_pct_status and WIP fields to each record
        for record in summary_data:
            progress_delta_pct = record.get('progress_delta_pct')
            record['progress_delta_pct_status'] = get_progress_delta_pct_status(progress_delta_pct)
            
            # Add WIP fields from database query (if available)
            if wip_data:
                record['in_progress_issues'] = wip_data['in_progress_epics']
                record['in_progress_percentage'] = wip_data['in_progress_percentage']
                record['count_in_progress_status'] = wip_data['count_in_progress_status']
            else:
                # Fallback: try to get from database function response if PI not provided
                in_progress_issues = record.get('in_progress_issues', 0) or 0
                total_issues = record.get('total_issues', 0) or 0
                record['in_progress_issues'] = in_progress_issues
                in_progress_percentage = ((in_progress_issues / total_issues) * 100) if total_issues > 0 else 0.0
                record['in_progress_percentage'] = round(in_progress_percentage, 2)
                record['count_in_progress_status'] = get_wip_count_status(in_progress_issues, total_issues)
        
        return {
            "success": True,
            "data": summary_data,
            "count": len(summary_data),
            "message": f"Retrieved PI status data for {len(summary_data)} records"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error fetching PI status for today: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch PI status for today: {str(e)}"
        )


@pis_router.get("/pis/WIP")
async def get_pi_wip(
    pi: str = Query(..., description="PI name (mandatory)"),
    team: str = Query(None, description="Team name filter (optional)"),
    project: str = Query(None, description="Project key filter (optional)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get WIP (Work In Progress) counts for epics in a specific PI.
    
    Returns counts of total epics and epics in progress, along with status color
    based on the percentage of epics in progress.
    
    Parameters:
        pi: PI name (mandatory)
        team: Team name filter (optional)
        project: Project key filter (optional)
    
    Returns:
        JSON response with:
        - count_in_progress: Number of epics with status_category = 'In Progress'
        - count_in_progress_status: "green", "yellow", or "red" based on percentage
        - total_epics: Total number of epics
        - in_progress_percentage: Percentage of epics in progress
    """
    try:
        # Validate pi parameter (mandatory)
        if not pi:
            raise HTTPException(
                status_code=400,
                detail="pi parameter is required"
            )
        
        # Use shared helper function to fetch WIP data
        wip_data = fetch_wip_data_from_db(
            pi=pi,
            team=team,
            project=project,
            conn=conn
        )
        
        return {
            "success": True,
            "data": {
                "count_in_progress": wip_data['in_progress_epics'],
                "count_in_progress_status": wip_data['count_in_progress_status'],
                "total_epics": wip_data['total_epics'],
                "in_progress_percentage": wip_data['in_progress_percentage'],
                "pi": pi,
                "team": team,
                "project": project
            },
            "message": f"Retrieved WIP counts: {wip_data['in_progress_epics']} epics in progress out of {wip_data['total_epics']} total epics ({wip_data['in_progress_percentage']:.2f}%)"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error fetching PI WIP counts: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch PI WIP counts: {str(e)}"
        )


@pis_router.get("/pis/get-pi-progress")
async def get_pi_progress(
    pi: str = Query(None, description="PI name filter"),
    project: str = Query(None, description="Project key filter"),
    issue_type: str = Query(None, description="Issue type filter"),
    team: str = Query(None, description="Team name filter"),
    plan_grace_period: int = Query(None, description="Planned grace period in days"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get PI progress with calculated metrics similar to current sprint progress.
    
    Uses the get_pi_summary_data database function and adds calculated fields:
    - days_left: Days remaining in PI (integer, inclusive counting)
    - days_in_pi: Total days in PI (integer, inclusive counting)
    - percent_completed_status: Status indicator (green/yellow/red) based on timeline vs completion
    - in_progress_issues_status: Status indicator (green/yellow/red) based on WIP percentage
    
    Parameters:
        pi: PI name filter (optional)
        project: Project key filter (optional)
        issue_type: Issue type filter (optional, defaults to 'Epic')
        team: Team name filter (optional)
        plan_grace_period: Planned grace period in days (optional, defaults to 5)
    
    Returns:
        JSON response with PI progress metrics including calculated status fields
    """
    try:
        # Set default value of 5 for plan_grace_period if empty/None
        if plan_grace_period is None:
            plan_grace_period = 5
        
        # Set default value of "Epic" for issue_type if empty/None
        if issue_type is None or issue_type == "":
            issue_type = "Epic"
        
        logger.info(f"Fetching PI progress")
        logger.info(f"Parameters: pi={pi}, project={project}, issue_type={issue_type}, team={team}, plan_grace_period={plan_grace_period}")
        
        # Call database function (same as get-pi-status-for-today)
        summary_data = fetch_pi_summary_data(
            target_pi_name=pi,
            target_project_keys=project,
            target_issue_type=issue_type,
            target_team_names=team,
            planned_grace_period_days=plan_grace_period,
            conn=conn
        )
        
        # Handle empty result
        if not summary_data or len(summary_data) == 0:
            return {
                "success": True,
                "data": {
                    "days_left": None,
                    "days_in_pi": None,
                    "total_issues": 0,
                    "completed_issues": 0,
                    "in_progress_issues": 0,
                    "todo_issues": 0,
                    "percent_completed": 0.0,
                    "percent_completed_status": "green",
                    "in_progress_issues_status": "green",
                    "pi": pi,
                    "team": team,
                    "project": project
                },
                "message": "No PI data found for the specified filters"
            }
        
        # Take first row (if multiple rows returned, we use the first one)
        # TODO: Consider aggregation logic if multiple rows should be combined
        pi_data = summary_data[0]
        
        # Extract fields with flexible field name handling (assumptions)
        # Try common field name variations (using 'in' check to avoid falsy 0/None issues)
        start_date = None
        for key in ['start_date', 'pi_start_date', 'pi_start']:
            if key in pi_data and pi_data[key] is not None:
                start_date = pi_data[key]
                break
        
        end_date = None
        for key in ['end_date', 'pi_end_date', 'pi_end']:
            if key in pi_data and pi_data[key] is not None:
                end_date = pi_data[key]
                break
        
        total_issues = 0
        for key in ['total_issues', 'total_count', 'count']:
            if key in pi_data and pi_data[key] is not None:
                total_issues = pi_data[key]
                break
        
        completed_issues = 0
        for key in ['completed_issues', 'done_count', 'done_issues']:
            if key in pi_data and pi_data[key] is not None:
                completed_issues = pi_data[key]
                break
        
        in_progress_issues = 0
        for key in ['in_progress_issues', 'in_progress_count', 'wip_count']:
            if key in pi_data and pi_data[key] is not None:
                in_progress_issues = pi_data[key]
                break
        
        todo_issues = 0
        for key in ['todo_issues', 'todo_count', 'open_count']:
            if key in pi_data and pi_data[key] is not None:
                todo_issues = pi_data[key]
                break
        
        percent_completed = 0.0
        for key in ['percent_completed', 'completion_percent', 'completion_percentage']:
            if key in pi_data and pi_data[key] is not None:
                percent_completed = pi_data[key]
                break
        
        # Ensure numeric types
        try:
            total_issues = int(total_issues) if total_issues is not None else 0
            completed_issues = int(completed_issues) if completed_issues is not None else 0
            in_progress_issues = int(in_progress_issues) if in_progress_issues is not None else 0
            todo_issues = int(todo_issues) if todo_issues is not None else 0
            percent_completed = float(percent_completed) if percent_completed is not None else 0.0
        except (ValueError, TypeError):
            logger.warning(f"Error converting numeric fields, using defaults")
            total_issues = 0
            completed_issues = 0
            in_progress_issues = 0
            todo_issues = 0
            percent_completed = 0.0
        
        # Calculate derived fields in service layer (business logic)
        days_left = calculate_days_left_pi(end_date)
        days_in_pi = calculate_days_in_pi(start_date, end_date)
        percent_completed_status = get_pi_percent_completed_status(
            percent_completed,
            start_date,
            end_date
        )
        in_progress_issues_status = get_pi_in_progress_issues_status(
            in_progress_issues,
            total_issues
        )
        
        return {
            "success": True,
            "data": {
                "days_left": days_left,
                "days_in_pi": days_in_pi,
                "total_issues": total_issues,
                "completed_issues": completed_issues,
                "in_progress_issues": in_progress_issues,
                "todo_issues": todo_issues,
                "percent_completed": percent_completed,
                "percent_completed_status": percent_completed_status,
                "in_progress_issues_status": in_progress_issues_status,
                "pi": pi,
                "team": team,
                "project": project,
                "issue_type": issue_type
            },
            "message": f"Retrieved PI progress data"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error fetching PI progress: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch PI progress: {str(e)}"
        )
