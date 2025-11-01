"""
PIs Service - REST API endpoints for PI-related operations.

This service provides endpoints for managing and retrieving PI information.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Union
import logging
from database_connection import get_db_connection
from database_pi import fetch_pi_predictability_data, fetch_pi_burndown_data, fetch_scope_changes_data, fetch_pi_summary_data
import config

logger = logging.getLogger(__name__)

pis_router = APIRouter()

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
        issue_type: Issue type filter (optional, defaults to 'all')
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
