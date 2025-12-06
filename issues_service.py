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

# Minimum duration threshold for issue status durations (in days)
# Used to filter out very short durations that may not be meaningful
MIN_DURATION_DAYS = 0.05

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
    team_name: Optional[str] = Query(None, description="Filter by team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get average duration by status name from issue_status_durations table.
    
    Returns average duration in days for each status name, filtered by time period and optional filters.
    Only includes issues with status_category = 'In Progress'.
    
    Args:
        months: Number of months to look back (default: 3, valid: 1, 2, 3, 4, 6, 9)
        issue_type: Optional filter by issue type
        team_name: Optional filter by team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with status duration data list and metadata
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Validate months parameter (same validation as closed sprints)
        if months not in [1, 2, 3, 4, 6, 9]:
            raise HTTPException(
                status_code=400, 
                detail="Months parameter must be one of: 1, 2, 3, 4, 6, 9"
            )
        
        # Resolve team names using shared helper function
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Calculate start date based on months parameter
        start_date = datetime.now().date() - timedelta(days=months * 30)
        
        # Build WHERE clause conditions
        where_conditions = [
            "isd.status_category = 'In Progress'",
            f"isd.duration_days >= {MIN_DURATION_DAYS}",
            "isd.time_exited >= :start_date"
        ]
        
        params = {
            "start_date": start_date.strftime("%Y-%m-%d")
        }
        
        # Add optional filters
        if issue_type:
            where_conditions.append("isd.issue_type = :issue_type")
            params["issue_type"] = issue_type
        
        if team_names_list:
            # Build parameterized IN clause (same pattern as closed sprints)
            placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names_list))])
            where_conditions.append(f"isd.team_name IN ({placeholders})")
            for i, name in enumerate(team_names_list):
                params[f"team_name_{i}"] = name
        
        # Build SQL query
        where_clause = " AND ".join(where_conditions)
        
        query = text(f"""
            SELECT 
                isd.status_name,
                AVG(isd.duration_days) as avg_duration_days
            FROM public.issue_status_durations isd
            WHERE {where_clause}
            GROUP BY isd.status_name
            HAVING AVG(isd.duration_days) >= {MIN_DURATION_DAYS}
            ORDER BY
                CASE
                    WHEN isd.status_name = 'In Progress' THEN 1
                    WHEN isd.status_name LIKE '%Review%' THEN 2
                    WHEN isd.status_name LIKE '%QA%' THEN 3
                    WHEN isd.status_name LIKE '%Approved%' THEN 4
                    ELSE 99
                END
        """)
        
        logger.info(f"Executing query to get issue status duration: months={months}, issue_type={issue_type}, team_name={team_name}")
        logger.info(f"Parameters: start_date={start_date}")
        
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


@issues_router.get("/issues/issue-status-duration-with-issue-keys")
async def get_issue_status_duration_with_issue_keys(
    status_name: str = Query(..., description="Status name to get issues for (required)"),
    months: int = Query(3, description="Number of months to look back (1, 2, 3, 4, 6, 9). Mutually exclusive with year_month.", ge=1, le=12),
    year_month: Optional[str] = Query(None, description="Year and month in YYYY-MM format (e.g., '2025-06'). If provided, returns data only for that specific month. Mutually exclusive with months parameter."),
    issue_type: Optional[str] = Query(None, description="Filter by issue type"),
    team_name: Optional[str] = Query(None, description="Filter by team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get issue keys, summaries, and durations for a specific status from issue_status_durations table.
    
    Returns individual issues with their issue_key, summary, and duration_days for the specified status,
    filtered by time period and optional filters.
    Only includes issues with status_category = 'In Progress'.
    
    Args:
        status_name: Status name to filter by (required)
        months: Number of months to look back (default: 3, valid: 1, 2, 3, 4, 6, 9). Mutually exclusive with year_month.
        year_month: Year and month in YYYY-MM format (e.g., '2025-06'). If provided, returns data only for that specific month. Mutually exclusive with months.
        issue_type: Optional filter by issue type
        team_name: Optional filter by team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with issue keys, summaries, and duration data list and metadata
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        # Validate status_name parameter
        if not status_name or not isinstance(status_name, str):
            raise HTTPException(
                status_code=400,
                detail="status_name parameter is required and must be a string"
            )
        
        # Sanitize status_name to prevent SQL injection
        status_name = status_name.strip()
        if not status_name:
            raise HTTPException(
                status_code=400,
                detail="status_name cannot be empty"
            )
        
        # Validate mutual exclusivity: months and year_month cannot both be explicitly provided
        # Since months has a default value of 3, we check if both are explicitly provided
        # If year_month is provided AND months is not the default (3), then both were explicitly provided
        if year_month and months != 3:
            raise HTTPException(
                status_code=400,
                detail="Parameters 'months' and 'year_month' are mutually exclusive. Please provide only one of them."
            )
        
        if year_month:
            # Validate year_month format: YYYY-MM
            if not re.match(r'^\d{4}-\d{2}$', year_month):
                raise HTTPException(
                    status_code=400,
                    detail="year_month must be in YYYY-MM format (e.g., '2025-06')"
                )
            
            # Parse and validate year and month
            try:
                year, month = year_month.split('-')
                year_int = int(year)
                month_int = int(month)
                
                if year_int < 2000 or year_int > 2100:
                    raise HTTPException(
                        status_code=400,
                        detail="year must be between 2000 and 2100"
                    )
                
                if month_int < 1 or month_int > 12:
                    raise HTTPException(
                        status_code=400,
                        detail="month must be between 01 and 12"
                    )
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="year_month must be in YYYY-MM format (e.g., '2025-06')"
                )
            
            # Check if months parameter was explicitly set to non-default value
            # Since months has default=3, we can't detect if user provided it
            # So we'll just ignore months when year_month is provided
            # But we should validate that months wasn't explicitly changed from default
            
            # Actually, we can't detect if months was explicitly provided
            # So we'll just use year_month and ignore months
            use_year_month = True
        else:
            # Validate months parameter (same validation as existing endpoint)
            if months not in [1, 2, 3, 4, 6, 9]:
                raise HTTPException(
                    status_code=400, 
                    detail="Months parameter must be one of: 1, 2, 3, 4, 6, 9"
                )
            use_year_month = False
        
        # Build WHERE clause conditions
        where_conditions = [
            "isd.status_name = :selected_status_name",
            "isd.status_category = 'In Progress'",
            f"isd.duration_days >= {MIN_DURATION_DAYS}"
        ]
        
        params = {
            "selected_status_name": status_name
        }
        
        # Add date filtering based on year_month or months
        if use_year_month:
            # Filter by specific year-month
            where_conditions.append("TO_CHAR(isd.time_exited, 'YYYY-MM') = :year_month")
            params["year_month"] = year_month
            logger.info(f"Filtering by specific month: {year_month}")
        else:
            # Calculate start date based on months parameter
            start_date = datetime.now().date() - timedelta(days=months * 30)
            where_conditions.append("isd.time_exited >= :start_date")
            params["start_date"] = start_date.strftime("%Y-%m-%d")
            logger.info(f"Filtering by months: {months}, start_date: {start_date}")
        
        # Resolve team names using shared helper function
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Add optional filters
        if issue_type:
            where_conditions.append("isd.issue_type = :issue_type")
            params["issue_type"] = issue_type
        
        if team_names_list:
            # Build parameterized IN clause (same pattern as closed sprints)
            placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names_list))])
            where_conditions.append(f"isd.team_name IN ({placeholders})")
            for i, name in enumerate(team_names_list):
                params[f"team_name_{i}"] = name
        
        # Build SQL query
        where_clause = " AND ".join(where_conditions)
        
        query = text(f"""
            SELECT 
                isd.issue_key,
                ji.summary AS issue_summary,
                isd.duration_days
            FROM 
                public.issue_status_durations isd
            INNER JOIN 
                public.jira_issues ji 
                ON isd.issue_key = ji.issue_key
            WHERE {where_clause}
            ORDER BY 
                isd.duration_days DESC
        """)
        
        if use_year_month:
            logger.info(f"Executing query to get issue status duration with issue keys: status_name={status_name}, year_month={year_month}, issue_type={issue_type}, team_name={team_name}")
        else:
            logger.info(f"Executing query to get issue status duration with issue keys: status_name={status_name}, months={months}, issue_type={issue_type}, team_name={team_name}")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        issues = []
        for row in rows:
            issues.append({
                "issue_key": row[0],
                "issue_summary": row[1] if row[1] else "",
                "duration_days": float(row[2]) if row[2] else 0.0
            })
        
        # Build response message based on whether year_month or months was used
        if use_year_month:
            message = f"Retrieved {len(issues)} issues for status '{status_name}' in {year_month}"
            response_data = {
                "issues": issues,
                "count": len(issues),
                "status_name": status_name,
                "year_month": year_month
            }
        else:
            message = f"Retrieved {len(issues)} issues for status '{status_name}' (last {months} months)"
            response_data = {
                "issues": issues,
                "count": len(issues),
                "status_name": status_name,
                "months": months
            }
        
        return {
            "success": True,
            "data": response_data,
            "message": message
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching issue status duration with issue keys: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch issue status duration with issue keys: {str(e)}"
        )


@issues_router.get("/issues/issue-status-duration-per-month")
async def get_issue_status_duration_per_month(
    months: int = Query(3, description="Number of months to look back (1, 2, 3, 4, 6, 9, 12)", ge=1, le=12),
    team_name: Optional[str] = Query(None, description="Filter by team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get average duration per month by status name from issue_status_durations table.
    
    Returns data formatted for chart rendering with labels (months) and datasets (one per status).
    Missing data is filled with 0. Statuses are ordered by priority (In Progress, Review, QA, Approved, etc.).
    
    Args:
        months: Number of months to look back (default: 3, valid: 1, 2, 3, 4, 6, 9, 12)
        team_name: Optional filter by team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with labels array (months) and datasets array (one per status with data per month)
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        # Validate months parameter
        if months not in [1, 2, 3, 4, 6, 9, 12]:
            raise HTTPException(
                status_code=400, 
                detail="Months parameter must be one of: 1, 2, 3, 4, 6, 9, 12"
            )
        
        # Calculate start date and end date based on months parameter
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=months * 30)
        
        # Generate all month labels in the range
        month_labels = []
        current = start_date.replace(day=1)  # Start from first day of start month
        end_month = end_date.replace(day=1)
        
        while current <= end_month:
            month_labels.append(current.strftime('%Y-%m'))
            # Move to next month
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)
        
        # Build WHERE clause conditions
        where_conditions = [
            "isd.time_exited >= :start_date",
            "isd.time_exited < :end_date",
            "isd.status_category = 'In Progress'",
            f"isd.duration_days >= {MIN_DURATION_DAYS}"  # Filter 1: Only average issues >= MIN_DURATION_DAYS
        ]
        
        params = {
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d")
        }
        
        # Resolve team names using shared helper function
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Add optional team filter
        if team_names_list:
            # Build parameterized IN clause (same pattern as closed sprints)
            placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names_list))])
            where_conditions.append(f"isd.team_name IN ({placeholders})")
            for i, name in enumerate(team_names_list):
                params[f"team_name_{i}"] = name
        
        # Build SQL query
        where_clause = " AND ".join(where_conditions)
        
        query = text(f"""
            SELECT
                isd.status_name,
                TO_CHAR(isd.time_exited, 'YYYY-MM') AS month_exited,
                AVG(isd.duration_days) AS avg_duration_days
            FROM
                public.issue_status_durations isd
            WHERE {where_clause}
            GROUP BY
                isd.status_name,
                month_exited
            HAVING
                AVG(isd.duration_days) >= {MIN_DURATION_DAYS}  -- Filter 2: Only show results if the AVG is >= MIN_DURATION_DAYS
            ORDER BY
                CASE
                    WHEN isd.status_name = 'In Progress' THEN 1
                    WHEN isd.status_name LIKE '%Review%' THEN 2
                    WHEN isd.status_name LIKE '%QA%' THEN 3
                    WHEN isd.status_name LIKE '%Approved%' THEN 4
                    ELSE 99
                END,
                month_exited
        """)
        
        logger.info(f"Executing query to get issue status duration per month: months={months}, team_name={team_name}")
        logger.info(f"Parameters: start_date={start_date}, end_date={end_date}")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        # Process results: group by status_name and create month-to-value mapping
        status_data = {}
        for row in rows:
            status_name = row[0]
            month_exited = row[1]
            avg_duration = float(row[2]) if row[2] else 0.0
            
            if status_name not in status_data:
                status_data[status_name] = {}
            status_data[status_name][month_exited] = avg_duration
        
        # Define status priority for ordering datasets
        def get_status_priority(status_name):
            if status_name == 'In Progress':
                return 1
            elif 'Review' in status_name:
                return 2
            elif 'QA' in status_name:
                return 3
            elif 'Approved' in status_name:
                return 4
            else:
                return 99
        
        # Build datasets array: one dataset per status, ordered by priority
        datasets = []
        sorted_statuses = sorted(status_data.keys(), key=get_status_priority)
        
        for status_name in sorted_statuses:
            # Create data array: one value per month in labels
            data_values = []
            for month_label in month_labels:
                # Use value if exists, otherwise 0
                value = status_data[status_name].get(month_label, 0.0)
                data_values.append(value)
            
            datasets.append({
                "label": status_name,
                "data": data_values
            })
        
        return {
            "success": True,
            "data": {
                "labels": month_labels,
                "datasets": datasets,
                "months": months,
                "team_name": team_name
            },
            "message": f"Retrieved status duration data per month for last {months} months"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching issue status duration per month: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch issue status duration per month: {str(e)}"
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
    team_name: Optional[str] = Query(None, description="Filter by team name or group name (if isGroup=true)"),
    status_category: Optional[str] = Query(None, description="Filter by status category"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get issues grouped by priority from the jira_issues table.
    
    Returns the count of issues per priority level, with optional filtering by issue_type, team_name, and/or status_category.
    When isGroup=true, aggregates data across all teams in the group.
    
    Args:
        issue_type: Optional filter by issue type
        team_name: Optional filter by team name or group name (if isGroup=true)
        status_category: Optional filter by status category
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with issues grouped by priority (priority, status_category, and issue_count)
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Build WHERE clause conditions based on provided filters
        where_conditions = []
        params = {}
        
        if issue_type:
            where_conditions.append("issue_type = :issue_type")
            params["issue_type"] = issue_type
        
        # Resolve team names using shared helper function (same pattern as other endpoints)
        team_names_list = None
        if team_name:
            team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
            if team_names_list:
                # Build parameterized IN clause (same pattern as closed sprints)
                placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names_list))])
                where_conditions.append(f"team_name IN ({placeholders})")
                for i, name in enumerate(team_names_list):
                    params[f"team_name_{i}"] = name
        
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
        
        logger.info(f"Executing query to get issues grouped by priority: issue_type={issue_type}, team_name={team_name}, isGroup={isGroup}, status_category={status_category}")
        
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
        
        # Build response data
        response_data = {
            "issues_by_priority": issues_by_priority,
            "count": len(issues_by_priority)
        }
        
        # Add metadata based on what was filtered
        if team_name:
            if isGroup:
                response_data["group_name"] = team_name
                response_data["teams_in_group"] = team_names_list
                message = f"Retrieved {len(issues_by_priority)} priority groups for group '{team_name}' ({len(team_names_list)} teams)"
            else:
                response_data["team_name"] = team_name
                message = f"Retrieved {len(issues_by_priority)} priority groups for team '{team_name}'"
        else:
            message = f"Retrieved {len(issues_by_priority)} priority groups"
        
        return {
            "success": True,
            "data": response_data,
            "message": message
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


@issues_router.get("/issues/epic-inbound-dependency-load-by-quarter")
async def get_epic_inbound_dependency_load_by_quarter(
    pi: Optional[str] = Query(None, description="Filter by PI (quarter_pi_of_epic)"),
    team_name: Optional[str] = Query(None, description="Filter by team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get epic inbound dependency load data from epic_inbound_dependency_load_by_quarter view.
    
    Returns all columns from the view with optional filtering by PI and/or team name.
    
    Args:
        pi: Optional filter by PI (filters on quarter_pi_of_epic column)
        team_name: Optional filter by team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with epic inbound dependency load data (all columns from view)
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        from database_pi import fetch_epic_inbound_dependency_data
        
        # Resolve team names FIRST (before building WHERE clause)
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Call shared function to fetch data
        records = fetch_epic_inbound_dependency_data(pi, team_names_list, conn)
        
        # Build response
        response = {
            "success": True,
            "data": records,
            "count": len(records),
            "message": f"Retrieved {len(records)} epic inbound dependency load records"
        }
        
        # Add metadata based on what was filtered
        if team_name:
            if isGroup:
                response["group_name"] = team_name
                response["teams_in_group"] = team_names_list
            else:
                response["team_name"] = team_name
        
        return response
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching epic inbound dependency load by quarter: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch epic inbound dependency load by quarter: {str(e)}"
        )


@issues_router.get("/issues/epic-outbound-dependency-metrics-by-quarter")
async def get_epic_outbound_dependency_metrics_by_quarter(
    pi: Optional[str] = Query(None, description="Filter by PI (quarter_pi_of_epic)"),
    team_name: Optional[str] = Query(None, description="Filter by team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get epic outbound dependency metrics data from epic_outbound_dependency_metrics_by_quarter view.
    
    Returns all columns from the view with optional filtering by PI and/or team name.
    
    Args:
        pi: Optional filter by PI (filters on quarter_pi_of_epic column)
        team_name: Optional filter by team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with epic outbound dependency metrics data (all columns from view)
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        from database_pi import fetch_epic_outbound_dependency_data
        
        # Resolve team names FIRST (before building WHERE clause)
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Call shared function to fetch data
        records = fetch_epic_outbound_dependency_data(pi, team_names_list, conn)
        
        # Build response
        response = {
            "success": True,
            "data": records,
            "count": len(records),
            "message": f"Retrieved {len(records)} epic outbound dependency metrics records"
        }
        
        # Add metadata based on what was filtered
        if team_name:
            if isGroup:
                response["group_name"] = team_name
                response["teams_in_group"] = team_names_list
            else:
                response["team_name"] = team_name
        
        return response
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching epic outbound dependency metrics by quarter: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch epic outbound dependency metrics by quarter: {str(e)}"
        )


@issues_router.get("/issues/epics-by-pi")
async def get_epics_by_pi(
    pi: str = Query(..., description="PI name (quarter_pi) to filter epics"),
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get comprehensive information about EPICs in a specific PI.
    
    Returns epic information including current state, historical baseline data,
    story tracking, team involvement, and dependency metrics.
    
    Args:
        pi: PI name (quarter_pi) to filter epics (required)
        team_name: Optional team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with list of epics and their detailed metrics
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Resolve team names (handles group to teams translation)
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Step 1: Get all epics for the PI
        where_conditions = [
            "issue_type = 'Epic'",
            "quarter_pi = :pi"
        ]
        params = {"pi": pi}
        
        # Add team filter if provided
        if team_names_list:
            placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names_list))])
            where_conditions.append(f"team_name IN ({placeholders})")
            for i, name in enumerate(team_names_list):
                params[f"team_name_{i}"] = name
        
        where_clause = " AND ".join(where_conditions)
        
        query1 = text(f"""
            SELECT 
                issue_key as epic_key,
                summary as epic_name,
                team_name as owning_team,
                quarter_pi,
                status_category,
                issue_id
            FROM {config.WORK_ITEMS_TABLE}
            WHERE {where_clause}
            ORDER BY issue_key
        """)
        
        logger.info(f"Executing query to get epics for PI: {pi}, team_name={team_name}, isGroup={isGroup}")
        
        result1 = conn.execute(query1, params)
        epic_rows = result1.fetchall()
        
        if not epic_rows:
            return {
                "success": True,
                "data": {
                    "epics": [],
                    "count": 0
                },
                "message": f"No epics found for PI {pi}"
            }
        
        # Extract epic keys for subsequent queries
        epic_keys = [row[0] for row in epic_rows]
        epic_data = {}
        
        # Initialize epic data structure
        for row in epic_rows:
            epic_key = row[0]
            epic_data[epic_key] = {
                "epic_name": row[1],
                "epic_key": epic_key,
                "owning_team": row[2],
                "planned_for_quarter": "Yes" if row[3] == pi else "No",
                "epic_status": row[4],  # Use status_category directly
                "epic_status_category": row[4],  # Add status_category as separate field
                "in_progress_date": None,
                "count_of_child_issues_when_epic_moved_to_inprogress": 0,
                "current_count_of_child_issues": 0,
                "child_issues_completed": 0,
                "child_issues_remaining": 0,
                "number_of_relying_teams": 0,
                "dependent_issues_total": 0,
                "dependent_issues_done": 0,
                "team_progress_breakdown": []
            }
        
        # Step 2: Get in-progress dates for all epics (batch)
        if epic_keys:
            placeholders = ", ".join([f":epic_key_{i}" for i in range(len(epic_keys))])
            params2 = {}
            for i, key in enumerate(epic_keys):
                params2[f"epic_key_{i}"] = key
            
            query2 = text(f"""
                SELECT 
                    h1.issue_key,
                    h1.snapshot_date as in_progress_date
                FROM (
                    SELECT 
                        issue_key,
                        MIN(snapshot_date) as min_date
                    FROM jira_issue_history
                    WHERE issue_key IN ({placeholders})
                      AND status_category = 'In Progress'
                    GROUP BY issue_key
                ) first_in_progress
                INNER JOIN jira_issue_history h1 
                    ON h1.issue_key = first_in_progress.issue_key
                    AND h1.snapshot_date = first_in_progress.min_date
                    AND h1.status_category = 'In Progress'
                ORDER BY h1.snapshot_date
            """)
            
            result2 = conn.execute(query2, params2)
            in_progress_rows = result2.fetchall()
            
            # Store in-progress dates
            for row in in_progress_rows:
                epic_key = row[0]
                if epic_key in epic_data:
                    epic_data[epic_key]["in_progress_date"] = row[1].strftime("%Y-%m-%d") if row[1] else None
            
            # Step 3: Get baseline story count (batch query for epics with in_progress_date)
            epics_with_dates = [k for k, v in epic_data.items() if v["in_progress_date"]]
            
            if epics_with_dates:
                placeholders3 = ", ".join([f":epic_key_{i}" for i in range(len(epics_with_dates))])
                params3 = {}
                for i, key in enumerate(epics_with_dates):
                    params3[f"epic_key_{i}"] = key
                
                    query3 = text(f"""
                        SELECT 
                            h.parent_key as epic_key,
                            COUNT(DISTINCT h.issue_key) as story_count
                        FROM jira_issue_history h
                        INNER JOIN (
                            SELECT 
                                h1.issue_key,
                                h1.snapshot_date as in_progress_date
                            FROM (
                                SELECT 
                                    issue_key,
                                    MIN(snapshot_date) as min_date
                                FROM jira_issue_history
                                WHERE issue_key IN ({placeholders3})
                                  AND status_category = 'In Progress'
                                GROUP BY issue_key
                            ) first_in_progress
                            INNER JOIN jira_issue_history h1 
                                ON h1.issue_key = first_in_progress.issue_key
                                AND h1.snapshot_date = first_in_progress.min_date
                                AND h1.status_category = 'In Progress'
                        ) epic_dates ON h.parent_key = epic_dates.issue_key
                            AND h.snapshot_date = epic_dates.in_progress_date
                        GROUP BY h.parent_key
                    """)
                
                result3 = conn.execute(query3, params3)
                baseline_rows = result3.fetchall()
                
                for row in baseline_rows:
                    epic_key = row[0]
                    if epic_key in epic_data:
                        epic_data[epic_key]["count_of_child_issues_when_epic_moved_to_inprogress"] = int(row[1]) if row[1] else 0
            
            # Step 4: Get current story metrics (batch for all epics)
            placeholders4 = ", ".join([f":epic_key_{i}" for i in range(len(epic_keys))])
            params4 = {}
            for i, key in enumerate(epic_keys):
                params4[f"epic_key_{i}"] = key
            
            # Team breakdown (all issues, not just stories)
            query4a = text(f"""
                SELECT 
                    parent_key as epic_key,
                    team_name,
                    COUNT(*) as total,
                    COUNT(CASE WHEN status_category = 'Done' THEN 1 END) as done
                FROM {config.WORK_ITEMS_TABLE}
                WHERE parent_key IN ({placeholders4})
                GROUP BY parent_key, team_name
                ORDER BY parent_key, team_name
            """)
            
            result4a = conn.execute(query4a, params4)
            team_breakdown_rows = result4a.fetchall()
            
            # Total issue counts (all issues, not just stories)
            query4b = text(f"""
                SELECT 
                    parent_key as epic_key,
                    COUNT(*) as current_count_of_child_issues,
                    COUNT(CASE WHEN status_category = 'Done' THEN 1 END) as child_issues_completed
                FROM {config.WORK_ITEMS_TABLE}
                WHERE parent_key IN ({placeholders4})
                GROUP BY parent_key
            """)
            
            result4b = conn.execute(query4b, params4)
            story_count_rows = result4b.fetchall()
            
            # Process team breakdown
            team_breakdown_by_epic = {}
            
            for row in team_breakdown_rows:
                epic_key = row[0]
                team_name = row[1]
                total = int(row[2]) if row[2] else 0
                done = int(row[3]) if row[3] else 0
                
                if epic_key not in team_breakdown_by_epic:
                    team_breakdown_by_epic[epic_key] = []
                
                team_breakdown_by_epic[epic_key].append({
                    "team_name": team_name,
                    "count_of_child_issues_done": done,
                    "total_count_of_child_issues": total
                })
            
            # Process story counts
            for row in story_count_rows:
                epic_key = row[0]
                if epic_key in epic_data:
                    epic_data[epic_key]["current_count_of_child_issues"] = int(row[1]) if row[1] else 0
                    epic_data[epic_key]["child_issues_completed"] = int(row[2]) if row[2] else 0
                    epic_data[epic_key]["child_issues_remaining"] = epic_data[epic_key]["current_count_of_child_issues"] - epic_data[epic_key]["child_issues_completed"]
            
            # Set team_progress_breakdown for all epics (even if no stories)
            for epic_key in epic_data.keys():
                epic_data[epic_key]["team_progress_breakdown"] = team_breakdown_by_epic.get(epic_key, [])
            
            # Set baseline count for epics that never entered "In Progress"
            for epic_key, data in epic_data.items():
                if data["count_of_child_issues_when_epic_moved_to_inprogress"] == 0 and data["in_progress_date"] is None:
                    # Epic never entered "In Progress" - use current as baseline
                    data["count_of_child_issues_when_epic_moved_to_inprogress"] = data["current_count_of_child_issues"]
            
            # Step 5: Get dependency metrics (batch for all epics)
            # Number of relying teams and dependent issues (using dependency = true)
            query5 = text(f"""
                SELECT 
                    parent_key as epic_key,
                    COUNT(DISTINCT team_name) as number_of_relying_teams,
                    COUNT(*) as total_dependent_issues,
                    COUNT(CASE WHEN status_category = 'Done' THEN 1 END) as done_dependent_issues
                FROM {config.WORK_ITEMS_TABLE}
                WHERE parent_key IN ({placeholders4})
                  AND dependency = true
                GROUP BY parent_key
            """)
            
            result5 = conn.execute(query5, params4)
            dependency_rows = result5.fetchall()
            
            for row in dependency_rows:
                epic_key = row[0]
                if epic_key in epic_data:
                    epic_data[epic_key]["number_of_relying_teams"] = int(row[1]) if row[1] else 0
                    epic_data[epic_key]["dependent_issues_total"] = int(row[2]) if row[2] else 0
                    epic_data[epic_key]["dependent_issues_done"] = int(row[3]) if row[3] else 0
        
        # Convert to list
        epics_list = list(epic_data.values())
        
        return {
            "success": True,
            "data": {
                "epics": epics_list,
                "count": len(epics_list)
            },
            "message": f"Retrieved {len(epics_list)} epics for PI {pi}"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching epics by PI: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch epics by PI: {str(e)}"
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


@issues_router.get("/issues/issue-types")
async def get_issue_types(
    conn: Connection = Depends(get_db_connection)
):
    """
    Get all issue types from the issue_types table.
    
    Returns all issue types with their metadata including description, iconUrl, name, subtask, and hierarchyLevel.
    
    Returns:
        JSON response with list of issue types and count
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT 
                issue_type,
                description,
                "iconUrl",
                name,
                subtask,
                "hierarchyLevel"
            FROM {config.ISSUE_TYPES_TABLE}
            ORDER BY issue_type
        """)
        
        logger.info("Executing query to get issue types")
        
        result = conn.execute(query)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        issue_types = []
        for row in rows:
            issue_type_dict = {
                "issue_type": row[0],
                "description": row[1],
                "iconUrl": row[2],
                "name": row[3],
                "subtask": row[4],
                "hierarchyLevel": row[5]
            }
            issue_types.append(issue_type_dict)
        
        return {
            "success": True,
            "data": {
                "issue_types": issue_types,
                "count": len(issue_types)
            },
            "message": f"Retrieved {len(issue_types)} issue types"
        }
    
    except Exception as e:
        logger.error(f"Error fetching issue types: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch issue types: {str(e)}"
        )