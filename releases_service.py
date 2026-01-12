"""
Releases Service - REST API endpoints for release-related operations.

This service provides endpoints for managing and retrieving release information.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import logging
from database_connection import get_db_connection
from database_releases import fetch_releases, fetch_release_burndown_data, fetch_release_metrics
from database_team_metrics import resolve_team_names_from_filter

logger = logging.getLogger(__name__)

releases_router = APIRouter()


@releases_router.get("/releases")
async def get_releases(
    project: Optional[str] = Query(None, description="Filter by project key"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get collection of releases.
    
    Args:
        project: Optional project key filter
    
    Returns:
        JSON response with releases list and metadata
    """
    try:
        releases = fetch_releases(project_key=project, conn=conn)
        
        return {
            "success": True,
            "data": {
                "releases": releases,
                "count": len(releases)
            },
            "message": f"Retrieved {len(releases)} releases"
        }
    
    except Exception as e:
        logger.error(f"Error fetching releases: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch releases: {str(e)}"
        )


@releases_router.get("/releases/predictability")
async def get_release_predictability(
    release: Optional[str] = Query(None, description="Filter by specific release name"),
    months: int = Query(6, description="Show releases where start_date is in the last N months", ge=1, le=12),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get release predictability analysis from the release_predictability_analysis table.
    
    Returns release predictability metrics including version name, project key, dates,
    epic completion percentages, and other issues completion percentages.
    
    Args:
        release: Optional - Filter by specific release name
        months: Optional (default=6) - Show releases where start_date is in the last N months
    
    Returns:
        JSON response with release predictability data list and metadata
    """
    try:
        start_date = datetime.now().date() - timedelta(days=months * 30)
        
        where_conditions = ["release_start_date >= :start_date"]
        params = {"start_date": start_date.strftime("%Y-%m-%d")}
        
        if release:
            where_conditions.append("version_name = :release_name")
            params["release_name"] = release
        
        query = text(f"""
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
            WHERE {' AND '.join(where_conditions)}
            ORDER BY release_start_date DESC
        """)
        
        logger.info(f"Executing query to get release predictability: release={release}, months={months}")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        predictability_data = []
        for row in rows:
            data_dict = dict(row._mapping)
            for key, value in data_dict.items():
                if value is not None and hasattr(value, 'strftime'):
                    data_dict[key] = value.strftime('%Y-%m-%d')
            predictability_data.append(data_dict)
        
        return {
            "success": True,
            "data": {
                "release_predictability": predictability_data,
                "count": len(predictability_data),
                "months": months,
                "release": release
            },
            "message": f"Retrieved {len(predictability_data)} release predictability records"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching release predictability: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch release predictability: {str(e)}"
        )


@releases_router.get("/releases/burndown")
async def get_release_burndown(
    release: str = Query(..., description="Release name (mandatory)"),
    issue_type: str = Query(None, description="Issue type filter"),
    team_name: str = Query(None, description="Team name filter (or group name if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get release burndown data for a specific release.
    
    Parameters:
        release: Release name (mandatory)
        issue_type: Issue type filter (optional, defaults to 'all')
        team_name: Team name filter (optional, or group name if isGroup=true)
        isGroup: If true, team_name parameter is treated as a group name (default: false)
    
    Returns:
        JSON response with release burndown data
    """
    try:
        if not release:
            raise HTTPException(
                status_code=400,
                detail="release parameter is required"
            )
        
        if issue_type is None or issue_type == "":
            issue_type = "all"
        
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        logger.info(f"Fetching release burndown data for release: {release}")
        logger.info(f"Filters: issue_type={issue_type}, team_name={team_name}, isGroup={isGroup}")
        if team_names_list:
            logger.info(f"Resolved team names: {team_names_list}")
        
        burndown_data = fetch_release_burndown_data(
            release_name=release,
            project_keys=None,
            issue_type=issue_type,
            team_names=team_names_list,
            conn=conn
        )
        
        response_data = {
            "burndown_data": burndown_data,
            "count": len(burndown_data),
            "release": release,
            "issue_type": issue_type,
            "isGroup": isGroup
        }
        
        if team_name:
            if isGroup:
                response_data["group_name"] = team_name
                response_data["teams_in_group"] = team_names_list
            else:
                response_data["team_name"] = team_name
        else:
            response_data["team_name"] = None
        
        return {
            "success": True,
            "data": response_data,
            "message": f"Retrieved release burndown data for {len(burndown_data)} records"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching release burndown data: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch release burndown data: {str(e)}"
        )


@releases_router.get("/releases/metrics")
async def get_release_metrics(
    release: Optional[str] = Query(None, description="Filter by specific release name"),
    release_id: Optional[int] = Query(None, description="Filter by specific release ID"),
    months: int = Query(6, description="Show releases where start_date is in the last N months", ge=1, le=12),
    issue_type: Optional[str] = Query(None, description="Filter by issue type"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get release metrics including epic and standard issue type counts and completion percentages.
    
    Returns release metrics for each release including:
    - Release ID, release name, start date, end date
    - Number of epics and standard issue types
    - Percentage of completed epics and standard issue types
    - Per-issue-type breakdown
    
    Standard issue types are defined as issue types with hierarchyLevel = 0.
    Completed is defined as status_category = 'Done'.
    
    Args:
        release: Optional - Filter by specific release name
        release_id: Optional - Filter by specific release ID
        months: Optional (default=6) - Show releases where start_date is in the last N months
        issue_type: Optional - Filter by issue type
    
    Returns:
        JSON response with release metrics data list and metadata
    """
    try:
        metrics = fetch_release_metrics(
            release_name=release,
            release_id=release_id,
            months=months,
            issue_type=issue_type,
            conn=conn
        )
        
        return {
            "success": True,
            "data": {
                "release_metrics": metrics,
                "count": len(metrics),
                "months": months,
                "release": release,
                "release_id": release_id,
                "issue_type": issue_type
            },
            "message": f"Retrieved {len(metrics)} release metrics records"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching release metrics: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch release metrics: {str(e)}"
        )

