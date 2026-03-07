"""
Issues Service - REST API endpoints for issue-related operations.

This service provides endpoints for managing and retrieving issue information.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, date
import logging
import re
import os
from database_connection import get_db_connection
import config
from global_settings_loader import settings

logger = logging.getLogger(__name__)

issues_router = APIRouter()

# Use unified constant from config for both duration and cycle time filtering
# This filters out very short durations that may not be meaningful

def enrich_epic_hierarchy_with_dates(issues: List[Dict[str, Any]], conn: Connection) -> Dict[str, Any]:
    """
    Enrich epic hierarchy issues with Start Date, End Date, and Progress % fields.
    
    The SQL function (get_epic_hierarchy_by_pi) returns raw data including number_of_children 
    and number_of_completed_children. This function calculates Progress % and adds date fields.
    
    Logic:
    - Stories (Level 0): 
        - Use Sprint field → sprint dates
        - Progress % = None (stories don't have progress)
    - Epics (Level 1): 
        - Start Date = PI start date
        - End Date = sprint end date (if Epic Target Completion is sprint) or PI end date
        - Progress % = (number_of_completed_children / number_of_children) * 100
    - Level 2: Progress and dates calculated from Epics (Level 1) children
    - Level 3: Progress and dates calculated from Level 2 children
    
    Args:
        issues: List of issue dictionaries from get_epic_hierarchy_by_pi SQL function
        conn: Database connection
    
    Returns:
        Dictionary with:
        - issues: List of enriched issue dictionaries with Start Date, End Date, and Progress % fields added
        - sprints: List of sprint dictionaries with "Sprint name", "start date", "end date" (max 20, sorted by start_date)
        - pis: List of PI dictionaries with "PI name", "start date", "end date" (sorted by start_date)
        - releases: List of release dictionaries with "Release name", "start date", "end date" (sorted by start_date)
    """
    # Step 1: Initialize Progress % field and calculate for Epics (Hierarchy Level 1)
    # SQL function only returns raw data (number_of_children, number_of_completed_children)
    # Python calculates Progress % from these fields
    for issue in issues:
        issue_type = issue.get("Type")
        hierarchy_level = issue.get("Hierarchy Level")
        
        # Only calculate for Epics (Level 1) - stories (Level 0) don't have children
        # Level 2 and 3 progress is calculated later from their children
        if issue_type == "Epic" and hierarchy_level == 1:
            number_of_children = issue.get("number_of_children", 0)
            number_of_completed_children = issue.get("number_of_completed_children", 0)
            
            if number_of_children > 0:
                progress_percent = (number_of_completed_children / number_of_children) * 100
                issue["Progress %"] = round(progress_percent, 1)
            else:
                issue["Progress %"] = 0.0
        else:
            # Initialize to None for stories and parent items (will be calculated later for Level 2/3)
            issue["Progress %"] = None
    
    # Step 2: Collect unique sprint names, PIs, and fix version IDs
    unique_sprint_names = set()
    unique_pis = set()
    unique_fix_version_ids = set()
    sprints_from_stories = set()  # DEBUG: Track sprints from "Sprint" field
    sprints_from_epic_target = set()  # DEBUG: Track sprints from "Epic Target Completion"
    
    # DEBUG: Track issue types and epic target completion values
    issue_types_found = set()
    epic_count = 0
    epics_with_target_completion = 0
    epics_without_target_completion = 0
    
    for issue in issues:
        issue_type = issue.get("Type")
        issue_key = issue.get("Key")
        issue_types_found.add(issue_type)
        
        # Collect sprint names from stories
        sprint_name = issue.get("Sprint")
        if sprint_name:
            unique_sprint_names.add(sprint_name)
            sprints_from_stories.add(sprint_name)
        
        # Collect sprint names from Epic Target Completion
        if issue_type == "Epic":
            epic_count += 1
            epic_target_completion = issue.get("Epic Target Completion")
            if epic_target_completion:
                unique_sprint_names.add(epic_target_completion)
                sprints_from_epic_target.add(epic_target_completion)
                epics_with_target_completion += 1
            else:
                epics_without_target_completion += 1
            
            # Also collect Original Epic Target Completion sprint names
            original_epic_target_completion = issue.get("Original Epic Target Completion")
            if original_epic_target_completion:
                unique_sprint_names.add(original_epic_target_completion)
        
        # Collect PIs from epics
        if issue_type == "Epic" and issue.get("Quarter PI of Epic"):
            unique_pis.add(issue["Quarter PI of Epic"])
        
        # Collect fix version IDs from all issues
        fix_version_ids = issue.get("Fix Version IDs")
        if fix_version_ids:  # Check if not None and not empty
            # fix_version_ids is an array (int4[]), so iterate through it
            for release_id in fix_version_ids:
                if release_id is not None:
                    unique_fix_version_ids.add(release_id)
    
    
    # Step 3: Query sprint dates
    sprint_dates_dict = {}
    if unique_sprint_names:
        sprint_names_list = list(unique_sprint_names)
        
        sprint_query = text("""
            SELECT name, start_date, end_date
            FROM jira_sprints
            WHERE name = ANY(:sprint_names)
        """)
        sprint_result = conn.execute(sprint_query, {"sprint_names": sprint_names_list})
        for row in sprint_result:
            sprint_dates_dict[row.name] = {
                "start_date": row.start_date,
                "end_date": row.end_date
            }
    
    # Step 4: Query PI dates
    pi_dates_dict = {}
    if unique_pis:
        pi_query = text("""
            SELECT pi_name, start_date, end_date
            FROM pis
            WHERE pi_name = ANY(:pi_names)
        """)
        pi_result = conn.execute(pi_query, {"pi_names": list(unique_pis)})
        for row in pi_result:
            pi_dates_dict[row.pi_name] = {
                "start_date": row.start_date,
                "end_date": row.end_date
            }
    
    # Step 4.5: Query release dates
    release_dates_dict = {}
    if unique_fix_version_ids:
        release_query = text("""
            SELECT release_id, name, start_date, release_date
            FROM jira_releases
            WHERE release_id = ANY(:release_ids)
        """)
        release_result = conn.execute(release_query, {"release_ids": list(unique_fix_version_ids)})
        for row in release_result:
            release_dates_dict[row.release_id] = {
                "name": row.name,
                "start_date": row.start_date,
                "end_date": row.release_date  # release_date is the end date
            }
    
    # Helper function to normalize dates for comparison (needed for resolved_at logic)
    def normalize_date_for_comparison(date_value):
        """Convert date to naive datetime for comparison, handling both date and datetime objects."""
        if isinstance(date_value, datetime):
            # Convert to naive datetime if it's timezone-aware
            if date_value.tzinfo is not None:
                # Remove timezone info by converting to UTC then removing tzinfo
                return date_value.astimezone().replace(tzinfo=None)
            return date_value
        elif isinstance(date_value, date):
            return datetime.combine(date_value, datetime.min.time())
        return date_value
    
    # Step 5: Enrich each issue with Start Date and End Date (existing logic)
    for issue in issues:
        issue_type = issue.get("Type")
        
        if issue_type in ["Story", "Task", "Bug"]:
            # Stories: Use Sprint field to get dates
            sprint_name = issue.get("Sprint")
            if sprint_name and sprint_name in sprint_dates_dict:
                sprint_data = sprint_dates_dict[sprint_name]
                issue["Start Date"] = sprint_data["start_date"]
                issue["End Date"] = sprint_data["end_date"]
            else:
                issue["Start Date"] = None
                issue["End Date"] = None
        
        elif issue_type == "Epic":
            # Epics: Start Date from PI, End Date from sprint or PI
            quarter_pi = issue.get("Quarter PI of Epic")
            if quarter_pi and quarter_pi in pi_dates_dict:
                pi_data = pi_dates_dict[quarter_pi]
                issue["Start Date"] = pi_data["start_date"]
                
                # End Date: Check if Epic Target Completion is a sprint name
                epic_target_completion = issue.get("Epic Target Completion")
                if epic_target_completion and epic_target_completion in sprint_dates_dict:
                    issue["End Date"] = sprint_dates_dict[epic_target_completion]["end_date"]
                else:
                    issue["End Date"] = pi_data["end_date"]
                
                # Original Epic End Date: Add if Original Epic Target Completion is different, matches sprint, and has value
                original_epic_target_completion = issue.get("Original Epic Target Completion")
                # First check: if different from Epic Target Completion (if same, skip)
                if original_epic_target_completion != epic_target_completion:
                    # Second check: if it matches a sprint name
                    if original_epic_target_completion and original_epic_target_completion in sprint_dates_dict:
                        # Third check: if it has a value (already checked above, but being explicit)
                        issue["Original Epic End Date"] = sprint_dates_dict[original_epic_target_completion]["end_date"]
            else:
                issue["Start Date"] = None
                issue["End Date"] = None
        else:
            # Ancestors (Level 2/3) - will be calculated below
            issue["Start Date"] = None
            issue["End Date"] = None
        
        # Apply resolved_at logic: If status is Done and resolved_at < end_date, use resolved_at
        status_category = issue.get("status_category")
        resolved_at = issue.get("Resolved At")
        end_date = issue.get("End Date")
        
        if (status_category == "Done" and 
            resolved_at is not None and 
            end_date is not None):
            # Normalize both dates for comparison
            resolved_normalized = normalize_date_for_comparison(resolved_at)
            end_date_normalized = normalize_date_for_comparison(end_date)
            
            # If resolved_at < end_date, use resolved_at as end_date
            if resolved_normalized < end_date_normalized:
                # Return resolved_at in the same type as original end_date
                if isinstance(end_date, date) and not isinstance(end_date, datetime):
                    issue["End Date"] = resolved_at.date() if isinstance(resolved_at, datetime) else resolved_at
                else:
                    # If end_date was datetime, return resolved_at as datetime (naive if original was naive)
                    if isinstance(end_date, datetime) and end_date.tzinfo is None:
                        # Original was naive datetime, return naive
                        if isinstance(resolved_at, datetime) and resolved_at.tzinfo is not None:
                            issue["End Date"] = resolved_at.astimezone().replace(tzinfo=None)
                        else:
                            issue["End Date"] = resolved_at
                    else:
                        issue["End Date"] = resolved_at
    
    # Helper function to normalize dates for comparison (handles both date and datetime, naive and aware)
    def normalize_date_for_comparison(date_value):
        """Convert date to naive datetime for comparison, handling both date and datetime objects."""
        if isinstance(date_value, datetime):
            # Convert to naive datetime if it's timezone-aware
            if date_value.tzinfo is not None:
                # Remove timezone info by converting to UTC then removing tzinfo
                return date_value.astimezone().replace(tzinfo=None)
            return date_value
        elif isinstance(date_value, date):
            return datetime.combine(date_value, datetime.min.time())
        return date_value
    
    # Helper function to get min/max from mixed date types
    def get_min_date(dates_list):
        """Get minimum date from list that may contain both date and datetime objects (naive/aware)."""
        if not dates_list:
            return None
        normalized = [normalize_date_for_comparison(d) for d in dates_list]
        min_val = min(normalized)
        # Return in the same type as the first date in the list
        first_date = dates_list[0]
        if isinstance(first_date, date) and not isinstance(first_date, datetime):
            return min_val.date() if isinstance(min_val, datetime) else min_val
        elif isinstance(first_date, datetime):
            # Return as naive datetime (matching normalized form)
            return min_val
        return min_val
    
    def get_max_date(dates_list):
        """Get maximum date from list that may contain both date and datetime objects (naive/aware)."""
        if not dates_list:
            return None
        normalized = [normalize_date_for_comparison(d) for d in dates_list]
        max_val = max(normalized)
        # Return in the same type as the first date in the list
        first_date = dates_list[0]
        if isinstance(first_date, date) and not isinstance(first_date, datetime):
            return max_val.date() if isinstance(max_val, datetime) else max_val
        elif isinstance(first_date, datetime):
            # Return as naive datetime (matching normalized form)
            return max_val
        return max_val
    
    # Step 6: Build parent-child relationships
    children_by_parent = {}
    for issue in issues:
        parent_key = issue.get("Parent Key")
        if parent_key:
            if parent_key not in children_by_parent:
                children_by_parent[parent_key] = []
            children_by_parent[parent_key].append(issue)
    
    # Step 7: Calculate Progress % and dates for Level 2 (from Epics - Level 1)
    for issue in issues:
        hierarchy_level = issue.get("Hierarchy Level")
        
        if hierarchy_level == 2:
            issue_key = issue.get("Key")
            children = children_by_parent.get(issue_key, [])
            
            # Filter for Epics (Level 1)
            epic_children = [c for c in children if c.get("Hierarchy Level") == 1]
            
            # Calculate Progress %
            if not epic_children:
                issue["Progress %"] = None  # Empty if no children
            else:
                total = len(epic_children)
                completed = len([c for c in epic_children if c.get("status_category") == "Done"])
                if total > 0:
                    issue["Progress %"] = (completed / total * 100.0)
                else:
                    issue["Progress %"] = None
            
            # Calculate Start Date (min of children's Start Dates)
            start_dates = [c.get("Start Date") for c in epic_children if c.get("Start Date") is not None]
            issue["Start Date"] = get_min_date(start_dates) if start_dates else None
            
            # Calculate End Date (max of children's End Dates)
            end_dates = [c.get("End Date") for c in epic_children if c.get("End Date") is not None]
            issue["End Date"] = get_max_date(end_dates) if end_dates else None
    
    # Step 8: Calculate Progress % and dates for Level 3 (from Level 2)
    for issue in issues:
        hierarchy_level = issue.get("Hierarchy Level")
        
        if hierarchy_level == 3:
            issue_key = issue.get("Key")
            children = children_by_parent.get(issue_key, [])
            
            # Filter for Level 2 items
            level2_children = [c for c in children if c.get("Hierarchy Level") == 2]
            
            # Calculate Progress %
            if not level2_children:
                issue["Progress %"] = None  # Empty if no children
            else:
                total = len(level2_children)
                completed = len([c for c in level2_children if c.get("status_category") == "Done"])
                if total > 0:
                    issue["Progress %"] = (completed / total * 100.0)
                else:
                    issue["Progress %"] = None
            
            # Calculate Start Date (min of children's Start Dates)
            start_dates = [c.get("Start Date") for c in level2_children if c.get("Start Date") is not None]
            issue["Start Date"] = get_min_date(start_dates) if start_dates else None
            
            # Calculate End Date (max of children's End Dates)
            end_dates = [c.get("End Date") for c in level2_children if c.get("End Date") is not None]
            issue["End Date"] = get_max_date(end_dates) if end_dates else None
    
    # Helper function to format dates as YYYY-MM-DD strings
    def format_date_only(date_value):
        """Convert datetime or date to YYYY-MM-DD string format."""
        if date_value is None:
            return None
        if isinstance(date_value, datetime):
            return date_value.date().isoformat()
        elif isinstance(date_value, date):
            return date_value.isoformat()
        return date_value
    
    # Step 8.5: Format all date fields on issues to YYYY-MM-DD strings (same format as End Date in sprints/pis/releases)
    for issue in issues:
        if "Start Date" in issue:
            issue["Start Date"] = format_date_only(issue["Start Date"])
        if "End Date" in issue:
            issue["End Date"] = format_date_only(issue["End Date"])
        if "Original Epic End Date" in issue:
            issue["Original Epic End Date"] = format_date_only(issue["Original Epic End Date"])
    
    # Step 9: Convert sprint_dates_dict to array format (max 20, sorted by start_date)
    sprints_list = []
    if sprint_dates_dict:
        # Create list with original dates for sorting
        sprints_with_dates = [
            {
                "Sprint name": sprint_name,
                "start date": format_date_only(sprint_data["start_date"]),
                "end date": format_date_only(sprint_data["end_date"]),
                "_sort_date": sprint_data["start_date"]  # Keep original for sorting
            }
            for sprint_name, sprint_data in sprint_dates_dict.items()
        ]
        # Sort by original start_date (earliest first), handling None values
        sprints_with_dates.sort(key=lambda x: normalize_date_for_comparison(x["_sort_date"]) if x["_sort_date"] is not None else datetime.max)
        
        # Remove sort helper field and limit to max sprint count for reports
        sprints_list = [
            {k: v for k, v in sprint.items() if k != "_sort_date"}
            for sprint in sprints_with_dates[:settings.MAX_SPRINT_COUNT_FOR_REPORTS]
        ]
    
    # Step 10: Convert pi_dates_dict to array format (sorted by start_date)
    pis_list = []
    if pi_dates_dict:
        # Create list with original dates for sorting
        pis_with_dates = [
            {
                "PI name": pi_name,
                "start date": format_date_only(pi_data["start_date"]),
                "end date": format_date_only(pi_data["end_date"]),
                "_sort_date": pi_data["start_date"]  # Keep original for sorting
            }
            for pi_name, pi_data in pi_dates_dict.items()
        ]
        # Sort by original start_date (earliest first), handling None values
        pis_with_dates.sort(key=lambda x: normalize_date_for_comparison(x["_sort_date"]) if x["_sort_date"] is not None else datetime.max)
        # Remove sort helper field
        pis_list = [
            {k: v for k, v in pi.items() if k != "_sort_date"}
            for pi in pis_with_dates
        ]
    
    # Step 10.5: Convert release_dates_dict to array format (sorted by start_date)
    releases_list = []
    if release_dates_dict:
        # Create list with original dates for sorting
        releases_with_dates = [
            {
                "Release name": release_data["name"],
                "start date": format_date_only(release_data["start_date"]),
                "end date": format_date_only(release_data["end_date"]),
                "_sort_date": release_data["start_date"]  # Keep original for sorting
            }
            for release_id, release_data in release_dates_dict.items()
        ]
        # Sort by original start_date (earliest first), handling None values
        releases_with_dates.sort(key=lambda x: normalize_date_for_comparison(x["_sort_date"]) if x["_sort_date"] is not None else datetime.max)
        # Remove sort helper field
        releases_list = [
            {k: v for k, v in release.items() if k != "_sort_date"}
            for release in releases_with_dates
        ]
    
    return {
        "issues": issues,
        "sprints": sprints_list,
        "pis": pis_list,
        "releases": releases_list
    }


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
    issue_key: Optional[str] = Query(None, description="Filter by issue key (exact match)"),
    issue_type: Optional[str] = Query(None, description="Filter by issue type"),
    status_category: Optional[str] = Query(None, description="Filter by status category"),
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    pi: Optional[str] = Query(None, description="Filter by PI (quarter_pi)"),
    sprint_id: Optional[int] = Query(None, description="Filter by sprint ID (matches any sprint_ids array element)"),
    limit: int = Query(settings.DEFAULT_QUERY_LIMIT, description=f"Number of issues to return (default: {settings.DEFAULT_QUERY_LIMIT}, max: 1000)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get a collection of issues with optional filtering.
    
    Returns issues with fields: issue_key, issue_type, summary, description, status_category, flagged, dependency, parent_key, team_name.
    
    Args:
        issue_type: Optional filter by issue type
        status_category: Optional filter by status category
        team_name: Team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
        pi: Optional filter by PI (quarter_pi)
        sprint_id: Optional filter by sprint ID (checks if sprint_id is in sprint_ids array)
        limit: Number of issues to return (default: 200, max: 1000)
    
    Returns:
        JSON response with issues list and metadata
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Validate limit
        validated_limit = validate_limit(limit)
        
        # Resolve team names if team_name is provided (handles group to teams translation)
        team_names_list = None
        if team_name:
            team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Build WHERE clause conditions based on provided filters
        where_conditions = []
        params = {"limit": validated_limit}
        
        if issue_key:
            where_conditions.append("issue_key = :issue_key")
            params["issue_key"] = issue_key

        if issue_type:
            where_conditions.append("issue_type = :issue_type")
            params["issue_type"] = issue_type
        
        if status_category:
            where_conditions.append("status_category = :status_category")
            params["status_category"] = status_category
        
        if team_names_list:
            # Build parameterized IN clause for multiple teams
            placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names_list))])
            where_conditions.append(f"team_name IN ({placeholders})")
            for i, name in enumerate(team_names_list):
                params[f"team_name_{i}"] = name
        
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
                parent_key,
                team_name
            FROM {config.WORK_ITEMS_TABLE}
            WHERE {where_clause}
            ORDER BY issue_id DESC
            LIMIT :limit
        """)
        
        logger.info(f"Executing query to get issues with filters: issue_type={issue_type}, status_category={status_category}, team_name={team_name}, isGroup={isGroup}, pi={pi}, sprint_id={sprint_id}, limit={validated_limit}")
        if team_names_list:
            logger.info(f"Resolved team names: {team_names_list}")
        
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
                "parent_key": row[7],
                "team_name": row[8] or ""
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
    team_name: Optional[str] = Query(None, description="Filter by team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    limit: int = Query(settings.DEFAULT_QUERY_LIMIT, description=f"Number of records to return (default: {settings.DEFAULT_QUERY_LIMIT}, max: 1000)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get epic hierarchy data from get_epic_hierarchy_by_pi function.
    
    Returns all columns from the function with optional filtering by PI and/or team name(s).
    Supports both single team and group filtering (when isGroup=true).
    Includes Initiatives/Portfolio Epics that have child Epics matching the filters.
    
    Args:
        pi: Optional filter by PI (filters on quarter_pi_of_epic column)
        team_name: Optional filter by team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name (expands to multiple teams)
        limit: Number of records to return (default: 500, max: 1000)
    
    Returns:
        JSON response with epic hierarchy data list and metadata
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Validate limit
        validated_limit = validate_limit(limit)
        
        # Resolve team names if team_name is provided (handles group to teams translation)
        team_names_list = None
        if team_name:
            team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Prepare parameters for function call
        params = {
            "pi": pi if pi else None,
            "limit": validated_limit
        }
        
        # Call SQL function
        # Use CAST for array parameter when teams are provided, otherwise pass NULL
        if team_names_list:
            params["teams"] = team_names_list
            query = text("""
                SELECT *
                FROM get_epic_hierarchy_by_pi(:pi, CAST(:teams AS text[]))
                LIMIT :limit
            """)
        else:
            query = text("""
                SELECT *
                FROM get_epic_hierarchy_by_pi(:pi, NULL::text[])
                LIMIT :limit
            """)
        
        logger.info(f"Executing query to get epic hierarchy: pi={pi}, team_name={team_name}, isGroup={isGroup}, limit={validated_limit}")
        if team_names_list:
            logger.info(f"Resolved team names: {team_names_list}")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        issues = []
        for row in rows:
            issues.append(dict(row._mapping))
        
        # Enrich with dates using shared function
        enrichment_result = enrich_epic_hierarchy_with_dates(issues, conn)
        issues = enrichment_result["issues"]
        sprints = enrichment_result["sprints"]
        pis = enrichment_result["pis"]
        releases = enrichment_result["releases"]
        
        return {
            "success": True,
            "data": {
                "issues": issues,
                "count": len(issues),
                "limit": validated_limit,
                "sprints": sprints,
                "pis": pis,
                "releases": releases
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
            f"isd.duration_days >= {settings.MIN_DURATION_AND_CYCLE_TIME_DAYS}",
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
            HAVING AVG(isd.duration_days) >= {settings.MIN_DURATION_AND_CYCLE_TIME_DAYS}
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


@issues_router.get("/issues/issues-grouped-by-priority")
async def get_issues_grouped_by_priority(
    issue_type: Optional[str] = Query(None, description="Filter by issue type"),
    team_name: Optional[str] = Query(None, description="Filter by team name or group name (if isGroup=true)"),
    status_category: Optional[List[str]] = Query(None, description="Filter by status categories (array)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    months: Optional[int] = Query(3, description="Number of months to look back (1-12)", ge=1, le=12),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get issues grouped by priority from the jira_issues table.
    
    Returns the count of issues per priority level, with optional filtering by issue_type, team_name, status_category, and time period.
    When isGroup=true, aggregates data across all teams in the group.
    Uses the same logic as the reports service endpoint.
    
    Args:
        issue_type: Optional filter by issue type
        team_name: Optional filter by team name or group name (if isGroup=true)
        status_category: Optional filter by status categories (array)
        isGroup: If true, team_name is treated as a group name
        months: Number of months to look back (default: 3)
    
    Returns:
        JSON response with issues grouped by priority (priority, status_category, and issue_count)
    """
    try:
        from database_reports import _fetch_issues_bugs_by_priority
        
        # Build filters dict to match what _fetch_issues_bugs_by_priority expects
        filters: Dict[str, Any] = {}
        if issue_type:
            filters["issue_type"] = issue_type
        if team_name:
            filters["team_name"] = team_name
        if status_category:
            filters["status_category"] = status_category
        if isGroup:
            filters["isGroup"] = isGroup
        if months:
            filters["months"] = months
        
        # Call the shared function
        result = _fetch_issues_bugs_by_priority(filters, conn)
        
        # Transform response to match direct endpoint format
        priority_summary = result.get("data", {}).get("priority_summary", [])
        meta = result.get("meta", {})
        
        # Convert to issues_by_priority format
        issues_by_priority = priority_summary
        
        # Build response data
        response_data = {
            "issues_by_priority": issues_by_priority,
            "count": len(issues_by_priority)
        }
        
        # Add metadata based on what was filtered
        if team_name:
            if isGroup:
                response_data["group_name"] = team_name
                teams_in_group = meta.get("teams_in_group")
                if teams_in_group:
                    response_data["teams_in_group"] = teams_in_group
                message = f"Retrieved {len(issues_by_priority)} priority groups for group '{team_name}'"
            else:
                response_data["team_name"] = team_name
                message = f"Retrieved {len(issues_by_priority)} priority groups for team '{team_name}'"
        else:
            message = f"Retrieved {len(issues_by_priority)} priority groups"
        
        logger.info(f"Retrieved issues grouped by priority: issue_type={issue_type}, team_name={team_name}, isGroup={isGroup}, status_category={status_category}, months={months}")
        
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
        
        # Calculate average number of dependencies per team
        # Count unique teams in the response
        unique_teams = set()
        total_dependencies = 0
        
        for record in records:
            assignee_team = record.get("assignee_team")
            volume_of_work_relied_upon = record.get("volume_of_work_relied_upon", 0)
            
            if assignee_team:
                unique_teams.add(assignee_team)
            
            # Sum the volume_of_work_relied_upon (dependencies) for all records
            if volume_of_work_relied_upon is not None:
                total_dependencies += volume_of_work_relied_upon
        
        # Calculate average: total dependencies / number of teams
        number_of_teams = len(unique_teams) if unique_teams else 0
        average_number_of_dependencies_per_team = (
            total_dependencies / number_of_teams 
            if number_of_teams > 0 
            else 0
        )
        
        # Build response
        response = {
            "success": True,
            "data": records,
            "count": len(records),
            "message": f"Retrieved {len(records)} epic inbound dependency load records",
            "average_number_of_dependencies_per_team": round(average_number_of_dependencies_per_team, 2)
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
        
        # Calculate average number of dependencies per team
        # Count unique teams in the response
        unique_teams = set()
        total_dependencies = 0
        
        for record in records:
            owned_team = record.get("owned_team")
            number_of_dependent_issues = record.get("number_of_dependent_issues", 0)
            
            if owned_team:
                unique_teams.add(owned_team)
            
            # Sum the number_of_dependent_issues (dependencies) for all records
            if number_of_dependent_issues is not None:
                total_dependencies += number_of_dependent_issues
        
        # Calculate average: total dependencies / number of teams
        number_of_teams = len(unique_teams) if unique_teams else 0
        average_number_of_dependencies_per_team = (
            total_dependencies / number_of_teams 
            if number_of_teams > 0 
            else 0
        )
        
        # Build response
        response = {
            "success": True,
            "data": records,
            "count": len(records),
            "message": f"Retrieved {len(records)} epic outbound dependency metrics records",
            "average_number_of_dependencies_per_team": round(average_number_of_dependencies_per_team, 2)
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


@issues_router.get("/issues/active-sprint-epic-dependencies")
async def get_active_epic_dependencies(
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get active epic dependencies for a team or group.
    
    Calls the database function get_active_epic_dependencies() which retrieves
    dependency metrics for all non-Done Epics that currently have one or more
    dependency stories in active sprints.
    
    Args:
        team_name: Optional team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with list of active epic dependencies and their details
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Resolve team names (handles group to teams translation)
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        logger.info(f"Fetching active epic dependencies")
        logger.info(f"Parameters: team_name={team_name}, isGroup={isGroup}")
        if team_names_list:
            logger.info(f"Resolved team names: {team_names_list}")
        
        # Build parameters for the function call
        params = {}
        
        # Build query - pass team_names as array or NULL
        if team_names_list:
            # Pass array of team names to function
            params['team_names_param'] = team_names_list
            sql_query_text = text("""
                SELECT * FROM public.get_active_epic_dependencies(
                    CAST(:team_names_param AS text[])
                )
            """)
            
            logger.info(f"Executing SQL for active epic dependencies with teams: {team_names_list}")
        else:
            # Pass NULL for all teams
            sql_query_text = text("""
                SELECT * FROM public.get_active_epic_dependencies(
                    NULL
                )
            """)
            
            logger.info("Executing SQL for active epic dependencies for all teams")
        
        # Execute query with parameters (SECURE: prevents SQL injection)
        result = conn.execute(sql_query_text, params)
        
        # Convert rows to list of dictionaries - return all columns as-is
        dependencies = []
        for row in result:
            row_dict = dict(row._mapping)
            dependencies.append(row_dict)
        
        # Build response metadata
        response_data = {
            "dependencies": dependencies,
            "count": len(dependencies),
            "isGroup": isGroup
        }
        
        # Add team/group information to response (following pattern from pis_service.py)
        # This ensures the original parameter value is always included in the response
        if team_name:
            if isGroup:
                # When isGroup=true, include the original group name AND the list of teams
                response_data["group_name"] = team_name  # Original group name passed
                response_data["teams_in_group"] = team_names_list  # List of teams in the group
            else:
                # When isGroup=false, include the original team name
                response_data["team_name"] = team_name  # Original team name passed
        else:
            # No filter was provided
            response_data["team_name"] = None
        
        return {
            "success": True,
            "data": response_data,
            "message": f"Retrieved {len(dependencies)} active epic dependencies"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (like validation errors from resolve_team_names_from_filter)
        raise
    except Exception as e:
        logger.error(f"Error fetching active epic dependencies: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch active epic dependencies: {str(e)}"
        )


@issues_router.get("/issues/dependency-heatmap")
async def get_dependency_heatmap(
    pi: str = Query(..., description="PI name filter (quarter_pi_of_epic) - REQUIRED"),
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get dependency heatmap data showing team-to-team dependencies.
    
    This endpoint returns data for a heatmap visualization where:
    - Rows represent "owning teams" (teams that own epics)
    - Columns represent "blocking teams" (teams that the owning teams depend on)
    - Cell values show total issues, completed issues, and completion percentage
    
    Args:
        pi: Required PI name filter
        team_name: Optional team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with heatmap data including:
        - heatmap_data: List of team-to-team dependency records
        - owning_teams: Sorted list of unique owning teams
        - blocking_teams: Sorted list of unique blocking teams
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        from database_pi import build_dependency_heatmap_response
        
        # Resolve team names (handles group to teams translation)
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        logger.info(f"Fetching dependency heatmap data")
        logger.info(f"Parameters: pi={pi}, team_name={team_name}, isGroup={isGroup}")
        if team_names_list:
            logger.info(f"Resolved team names: {team_names_list}")
        
        # Use shared helper function to build response data
        response_data = build_dependency_heatmap_response(
            pi=pi,
            team_names_list=team_names_list,
            team_name=team_name,
            is_group=isGroup,
            conn=conn
        )
        
        return {
            "success": True,
            "data": response_data,
            "message": f"Retrieved {len(heatmap_data)} dependency relationships"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching dependency heatmap: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch dependency heatmap: {str(e)}"
        )


@issues_router.get("/issues/dependency-heatmap-stories")
async def get_dependency_heatmap_stories(
    pi: str = Query(..., description="PI name filter (quarter_pi_of_epic) - REQUIRED"),
    owning_team: str = Query(..., description="Owning team name (team_name_of_epic) - REQUIRED"),
    blocking_team: Optional[str] = Query(None, description="Blocking team name (team_name) - OPTIONAL. If provided, filters by specific blocking team. If not provided, returns all blocking stories for the owning team."),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get dependent stories/issues for a dependency heatmap.
    
    Returns the list of stories/issues that are dependent, filtered by:
    - PI (quarter_pi_of_epic)
    - Owning team (team_name_of_epic) - the team that owns the epic
    - Blocking team (team_name) - OPTIONAL. If provided, filters by specific blocking team. If not provided, returns all blocking stories.
    
    Args:
        pi: Required PI name filter
        owning_team: Required owning team name (team that owns the epic)
        blocking_team: Optional blocking team name. If provided, returns stories for specific owning_team → blocking_team relationship. If not provided, returns all stories where owning_team is blocked by any team.
    
    Returns:
        JSON response with list of dependent stories/issues including:
        - issue_key: Story/issue key
        - summary: Story/issue summary
        - issue_type: Issue type (Story, Task, etc.)
        - team_name: Team name of the dependent issue
        - status_category: Status category
        - parent_key: Epic key
        - parent_name: Epic name/summary
    """
    try:
        # Build WHERE clause conditions
        where_conditions = []
        params = {}
        
        # Required parameters
        where_conditions.append("ji.quarter_pi_of_epic = :pi")
        params["pi"] = pi
        
        where_conditions.append("ji.team_name_of_epic = :owning_team")
        params["owning_team"] = owning_team
        
        # Optional blocking team filter
        if blocking_team:
            where_conditions.append("ji.team_name = :blocking_team")
            params["blocking_team"] = blocking_team
        
        # Base conditions for dependency issues
        where_conditions.append("ji.dependency = TRUE")
        where_conditions.append("ji.issue_type::text <> 'Epic'::text")
        where_conditions.append("ji.parent_key IS NOT NULL")
        where_conditions.append("ji.team_name_of_epic IS NOT NULL")
        where_conditions.append("ji.team_name IS NOT NULL")
        
        # Build SQL query
        where_clause = " AND ".join(where_conditions)
        query = text(f"""
            SELECT 
                ji.issue_key,
                ji.summary,
                ji.issue_type,
                ji.team_name,
                ji.status_category,
                ji.parent_key,
                epic.summary AS parent_name
            FROM 
                jira_issues ji
            LEFT JOIN
                jira_issues epic ON ji.parent_key = epic.issue_key
            WHERE 
                {where_clause}
            ORDER BY
                ji.parent_key,
                ji.issue_key
        """)
        
        logger.info(f"Executing query for dependency heatmap stories: pi={pi}, owning_team={owning_team}, blocking_team={blocking_team or 'ALL'}")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        stories = []
        for row in rows:
            row_dict = dict(row._mapping)
            
            # Format any date/datetime fields if they exist
            for key, value in row_dict.items():
                if value is not None:
                    if hasattr(value, 'strftime'):
                        if 'date' in key.lower() or 'time' in key.lower():
                            row_dict[key] = value.strftime('%Y-%m-%d %H:%M:%S')
                        else:
                            row_dict[key] = value.strftime('%Y-%m-%d')
                    elif hasattr(value, 'isoformat'):
                        row_dict[key] = value.isoformat()
            
            stories.append(row_dict)
        
        logger.info(f"Retrieved {len(stories)} dependent stories for heatmap cell")
        
        return {
            "success": True,
            "data": {
                "issues": stories
            },
            "message": f"Retrieved {len(stories)} dependent stories"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching dependency heatmap stories: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch dependency heatmap stories: {str(e)}"
        )


@issues_router.get("/issues/epics-by-pi-summary")
async def get_epics_by_pi_summary(
    pi: str = Query(..., description="PI name (quarter_pi) to filter epics"),
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get summary information about EPICs in a specific PI.
    Returns only essential fields: epic_key, epic_name, epic_status_category, and dependent_issues_total.
    Results are sorted by dependent_issues_total descending (epics with most dependencies first).
    
    Args:
        pi: PI name (quarter_pi) to filter epics (required)
        team_name: Optional team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with list of epics sorted by dependency count (descending)
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Resolve team names (handles group to teams translation)
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Query 1: Get all epics for the PI
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
                status_category as epic_status_category
            FROM {config.WORK_ITEMS_TABLE}
            WHERE {where_clause}
        """)
        
        logger.info(f"Executing query to get epics summary for PI: {pi}, team_name={team_name}, isGroup={isGroup}")
        
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
        
        # Extract epic keys for dependency query
        epic_keys = [row[0] for row in epic_rows]
        epic_data = {}
        
        # Initialize epic data structure
        for row in epic_rows:
            epic_key = row[0]
            epic_data[epic_key] = {
                "epic_key": epic_key,
                "epic_name": row[1],
                "epic_status_category": row[2],
                "dependent_issues_total": 0  # Will be filled by query 2
            }
        
        # Query 2: Get dependency counts per epic
        if epic_keys:
            placeholders2 = ", ".join([f":epic_key_{i}" for i in range(len(epic_keys))])
            params2 = {}
            for i, key in enumerate(epic_keys):
                params2[f"epic_key_{i}"] = key
            
            query2 = text(f"""
                SELECT 
                    parent_key as epic_key,
                    COUNT(*) as dependent_issues_total
                FROM {config.WORK_ITEMS_TABLE}
                WHERE parent_key IN ({placeholders2})
                  AND dependency = TRUE
                GROUP BY parent_key
            """)
            
            result2 = conn.execute(query2, params2)
            dependency_rows = result2.fetchall()
            
            # Update epic data with dependency counts
            for row in dependency_rows:
                epic_key = row[0]
                dependent_count = int(row[1]) if row[1] else 0
                if epic_key in epic_data:
                    epic_data[epic_key]["dependent_issues_total"] = dependent_count
        
        # Convert to list and sort by dependent_issues_total descending
        epics_list = list(epic_data.values())
        epics_list.sort(key=lambda x: x["dependent_issues_total"], reverse=True)
        
        return {
            "success": True,
            "data": {
                "epics": epics_list,
                "count": len(epics_list)
            },
            "message": f"Retrieved {len(epics_list)} epics for PI {pi}"
        }
    
    except Exception as e:
        logger.error(f"Error fetching epics by PI summary: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch epics by PI summary: {str(e)}"
        )


@issues_router.get("/issues/active-sprint-stories-by-epic")
async def get_active_sprint_stories_by_epic(
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get active sprint stories by epic for a team or group.
    
    Calls the database function get_active_sprint_stories_by_epic() which retrieves
    active sprint stories grouped by epic.
    
    Args:
        team_name: Optional team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with list of active sprint stories by epic and their details
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Resolve team names (handles group to teams translation)
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        logger.info(f"Fetching active sprint stories by epic")
        logger.info(f"Parameters: team_name={team_name}, isGroup={isGroup}")
        if team_names_list:
            logger.info(f"Resolved team names: {team_names_list}")
        
        # Build parameters for the function call
        params = {}
        
        # Build query - pass team_names as array or NULL
        if team_names_list:
            # Pass array of team names to function
            params['team_names_param'] = team_names_list
            sql_query_text = text("""
                SELECT * FROM public.get_active_sprint_stories_by_epic(
                    CAST(:team_names_param AS text[])
                )
            """)
            
            logger.info(f"Executing SQL for active sprint stories by epic with teams: {team_names_list}")
        else:
            # Pass NULL for all teams
            sql_query_text = text("""
                SELECT * FROM public.get_active_sprint_stories_by_epic(
                    NULL
                )
            """)
            
            logger.info("Executing SQL for active sprint stories by epic for all teams")
        
        # Execute query with parameters (SECURE: prevents SQL injection)
        result = conn.execute(sql_query_text, params)
        
        # Convert rows to list of dictionaries - return all columns as-is
        stories = []
        for row in result:
            row_dict = dict(row._mapping)
            stories.append(row_dict)
        
        # Build response - stories array goes directly in data
        # Metadata (count, team/group info) goes at top level
        response = {
            "success": True,
            "data": stories,  # Direct array of story objects, NOT wrapped in a key
            "count": len(stories),
            "isGroup": isGroup
        }
        
        # Add team/group information to response
        # This ensures the original parameter value is always included in the response
        if team_name:
            if isGroup:
                # When isGroup=true, include the original group name AND the list of teams
                response["group_name"] = team_name  # Original group name passed
                response["teams_in_group"] = team_names_list  # List of teams in the group
            else:
                # When isGroup=false, include the original team name
                response["team_name"] = team_name  # Original team name passed
        else:
            # No filter was provided
            response["team_name"] = None
        
        response["message"] = f"Retrieved {len(stories)} active sprint stories by epic"
        
        return response
    
    except HTTPException:
        # Re-raise HTTP exceptions (like validation errors from resolve_team_names_from_filter)
        raise
    except Exception as e:
        logger.error(f"Error fetching active sprint stories by epic: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch active sprint stories by epic: {str(e)}"
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
            FROM public.{config.ISSUE_TYPES_TABLE}
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


@issues_router.get("/issues/issue-types-hierarchy")
async def get_issue_types_hierarchy(
    conn: Connection = Depends(get_db_connection)
):
    """
    Get all issue types grouped by hierarchy level.
    
    Returns issue types organized by their hierarchy level, ordered from highest to lowest.
    Issue types with NULL hierarchy level are included at the end.
    
    Returns:
        JSON response with levels array, where each level contains:
        - hierarchyLevel: The hierarchy level number (or null)
        - issue_types: Array of issue type names at that level
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT 
                issue_type,
                "hierarchyLevel"
            FROM public.{config.ISSUE_TYPES_TABLE}
            ORDER BY "hierarchyLevel" DESC NULLS LAST, issue_type ASC
        """)
        
        logger.info("Executing query to get issue types grouped by hierarchy")
        
        result = conn.execute(query)
        rows = result.fetchall()
        
        # Group issue types by hierarchy level
        levels_dict = {}
        for row in rows:
            issue_type = row[0]
            hierarchy_level = row[1]
            
            # Use None as key for NULL hierarchy levels (for consistent handling)
            level_key = hierarchy_level if hierarchy_level is not None else None
            
            if level_key not in levels_dict:
                levels_dict[level_key] = {
                    "hierarchyLevel": hierarchy_level,
                    "issue_types": []
                }
            
            levels_dict[level_key]["issue_types"].append(issue_type)
        
        # Convert to list, maintaining order (highest to lowest, then NULL)
        # The dict keys are already in the correct order from the query
        levels = list(levels_dict.values())
        
        # Calculate total count
        total_count = sum(len(level["issue_types"]) for level in levels)
        
        return {
            "success": True,
            "data": {
                "levels": levels,
                "count": total_count
            },
            "message": f"Retrieved {total_count} issue types grouped by hierarchy"
        }
    
    except Exception as e:
        logger.error(f"Error fetching issue types hierarchy: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch issue types hierarchy: {str(e)}"
        )


def _normalize_multi_value_issue_type(values: Optional[List[str] | str]) -> Optional[List[str]]:
    """
    Normalize multi-value issue_type parameter.
    Handles comma-separated strings, lists, and single values.
    """
    if values is None:
        return None
    if isinstance(values, list):
        normalized: List[str] = []
        for value in values:
            if value is None:
                continue
            if isinstance(value, str):
                parts = [part.strip() for part in value.split(",") if part.strip()]
                normalized.extend(parts)
            else:
                normalized.append(str(value))
        return normalized if normalized else None
    if isinstance(values, str):
        parts = [part.strip() for part in values.split(",") if part.strip()]
        return parts if parts else None
    return [str(values)]


@issues_router.get("/issues/cycle-time-with-issues-keys")
async def get_cycle_time_with_issue_keys(
    request: Request,
    period_start: str = Query(..., description="Start date (YYYY-MM-DD) - filter by resolved_at >= period_start"),
    period_end: str = Query(..., description="End date (YYYY-MM-DD) - filter by resolved_at <= period_end"),
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    issue_type: Optional[str] = Query(None, description="Filter by issue type(s) - can be single value, comma-separated, or multiple params (e.g., 'Story,Bug' or ?issue_type=Story&issue_type=Bug)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get issues with cycle time for a specific period.
    
    Returns issue keys, summaries, cycle times, resolved dates, issue types, and team names
    for completed issues within the specified date range.
    
    Args:
        period_start: Start date (YYYY-MM-DD) - required
        period_end: End date (YYYY-MM-DD) - required
        team_name: Optional team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
        issue_type: Optional filter by issue type(s) - supports multi-value (comma-separated or multiple params)
    
    Returns:
        JSON response with list of issues (max 100) containing:
        - issue_key
        - summary
        - cycle_time (rounded to 2 decimal places)
        - resolved_at (date string)
        - issue_type
        - team_name
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Validate and parse dates
        try:
            start_date = datetime.strptime(period_start, "%Y-%m-%d").date()
            end_date = datetime.strptime(period_end, "%Y-%m-%d").date()
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date format. Expected YYYY-MM-DD format. Error: {str(e)}"
            )
        
        # Validate date range
        if start_date > end_date:
            raise HTTPException(
                status_code=400,
                detail="period_start must be less than or equal to period_end"
            )
        
        # Normalize multi-value issue_type parameter
        # Handle both single query param and multiple query params
        issue_type_values = None
        # Get all issue_type values from query params (handles multiple params like ?issue_type=Story&issue_type=Bug)
        issue_type_params = request.query_params.getlist("issue_type")
        if issue_type_params:
            issue_type_values = _normalize_multi_value_issue_type(issue_type_params)
        elif issue_type:
            # Fallback to single parameter if not provided as multiple params
            issue_type_values = _normalize_multi_value_issue_type(issue_type)
        
        # Resolve team names using shared helper function
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Build WHERE clause conditions
        where_conditions = [
            "status_category = 'Done'",
            f"cycle_time_days >= {settings.MIN_DURATION_AND_CYCLE_TIME_DAYS}",
            "resolved_at IS NOT NULL",
            "DATE(resolved_at) >= :period_start",
            "DATE(resolved_at) <= :period_end"
        ]
        
        params = {
            "period_start": start_date.strftime("%Y-%m-%d"),
            "period_end": end_date.strftime("%Y-%m-%d"),
            "limit": 100
        }
        
        # Add team filter if provided
        if team_names_list:
            team_placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names_list))])
            where_conditions.append(f"team_name IN ({team_placeholders})")
            for i, name in enumerate(team_names_list):
                params[f"team_name_{i}"] = name
        
        # Add issue_type filter if provided
        if issue_type_values:
            issue_type_placeholders = ", ".join([f":issue_type_{i}" for i in range(len(issue_type_values))])
            where_conditions.append(f"issue_type IN ({issue_type_placeholders})")
            for i, itype in enumerate(issue_type_values):
                params[f"issue_type_{i}"] = itype
        
        # Build SQL query
        where_clause = " AND ".join(where_conditions)
        
        query = text(f"""
            SELECT 
                issue_key,
                summary,
                ROUND(cycle_time_days, 2) AS cycle_time,
                DATE(resolved_at) AS resolved_at,
                issue_type,
                team_name
            FROM {config.WORK_ITEMS_TABLE}
            WHERE {where_clause}
            ORDER BY resolved_at DESC
            LIMIT :limit
        """)
        
        logger.info(f"Executing query to get cycle time with issue keys: period_start={period_start}, period_end={period_end}, team_name={team_name}, isGroup={isGroup}, issue_type={issue_type_values}")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        issues = []
        for row in rows:
            issue_dict = {
                "issue_key": row[0],
                "summary": row[1],
                "cycle_time": float(row[2]) if row[2] is not None else None,
                "resolved_at": row[3].strftime("%Y-%m-%d") if row[3] else None,
                "issue_type": row[4],
                "team_name": row[5]
            }
            issues.append(issue_dict)
        
        return {
            "success": True,
            "data": {
                "issues": issues,
                "count": len(issues),
                "limit": 100
            },
            "message": f"Retrieved {len(issues)} issues"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching cycle time with issue keys: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch cycle time with issue keys: {str(e)}"
        )


@issues_router.get("/issues/get-history-info")
async def get_history_info(
    date: str = Query(..., description="Date to query (YYYY-MM-DD format, date only, no time)"),
    sprint_id: int = Query(..., description="Sprint ID to filter by"),
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    issue_type: Optional[str] = Query(None, description="Filter by issue type (e.g., 'Story', 'Bug', 'Epic')"),
    metric_type: str = Query(..., description="Metric type: 'issues_completed', 'issues_removed', 'issues_added', 'total_scope', 'wip_in_progress', or 'actual_remaining'"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get history info for issues on a specific date in a sprint.
    
    Returns issues matching the specified metric type for the given date, sprint, team, and issue type.
    
    Args:
        date: Date to query (YYYY-MM-DD format, date only, no time)
        sprint_id: Sprint ID to filter by
        team_name: Optional team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
        issue_type: Optional filter by issue type
        metric_type: Metric type to return:
            - "issues_completed" - Issues completed on this day
            - "issues_removed" - Issues removed from sprint (were in sprint day before, not now)
            - "issues_added" - Issues added to sprint on this day
            - "total_scope" - Total scope of sprint on this day (all issues in sprint)
            - "wip_in_progress" - Work in progress items on this day
            - "actual_remaining" - Actual remaining items on this day (not done)
    
    Returns:
        JSON response with issues list and metadata
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter, get_sprint_history_issues_db
        
        # Validate date format
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date format. Expected YYYY-MM-DD format. Error: {str(e)}"
            )
        
        # Validate metric_type
        valid_metric_types = ["issues_completed", "issues_removed", "issues_added", "total_scope", "wip_in_progress", "actual_remaining"]
        if metric_type not in valid_metric_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid metric_type. Must be one of: {', '.join(valid_metric_types)}"
            )
        
        # Resolve team names using shared helper function
        # Returns None if no team_name provided (meaning all teams)
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        logger.info(f"Fetching history info: date={date}, sprint_id={sprint_id}, team_name={team_name}, isGroup={isGroup}, issue_type={issue_type}, metric_type={metric_type}")
        if team_names_list:
            logger.info(f"Resolved team names: {team_names_list}")
        
        # Call database helper function
        issues = get_sprint_history_issues_db(
            sprint_id=sprint_id,
            target_date=target_date,
            team_names=team_names_list,
            issue_type=issue_type,
            metric_type=metric_type,
            conn=conn
        )
        
        # Build response
        response_data = {
            "issues": issues,
            "count": len(issues),
            "date": date,
            "sprint_id": sprint_id,
            "metric_type": metric_type
        }
        
        # Add optional filters to response
        if team_name:
            if isGroup:
                response_data["group_name"] = team_name
                if team_names_list:
                    response_data["teams_in_group"] = team_names_list
            else:
                response_data["team_name"] = team_name
        
        if issue_type:
            response_data["issue_type"] = issue_type
        
        return {
            "success": True,
            "data": response_data,
            "message": f"Retrieved {len(issues)} issues"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching history info: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch history info: {str(e)}"
        )


@issues_router.get("/issues/get-pi-history-info")
async def get_pi_history_info(
    date: str = Query(..., description="Date to query (YYYY-MM-DD format, date only, no time)"),
    pi: str = Query(..., description="PI name (quarter_pi) to filter by"),
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    issue_type: Optional[str] = Query(None, description="Filter by issue type (e.g., 'Story', 'Bug', 'Epic')"),
    metric_type: str = Query(..., description="Metric type: 'issues_completed', 'issues_removed', 'issues_added', 'total_scope', 'wip_in_progress', or 'actual_remaining'"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get history info for issues on a specific date in a PI.
    
    Returns issues matching the specified metric type for the given date, PI, team, and issue type.
    
    Args:
        date: Date to query (YYYY-MM-DD format, date only, no time)
        pi: PI name (quarter_pi) to filter by
        team_name: Optional team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
        issue_type: Optional filter by issue type
        metric_type: Metric type to return:
            - "issues_completed" - Issues completed on this day
            - "issues_removed" - Issues removed from PI (were in PI day before, not now)
            - "issues_added" - Issues added to PI on this day (were not in PI day before)
            - "total_scope" - Total scope of PI on this day (all issues in PI)
            - "wip_in_progress" - Work in progress items on this day
            - "actual_remaining" - Actual remaining items on this day (not done)
    
    Returns:
        JSON response with issues list and metadata
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        from database_pi import get_pi_history_issues_db
        
        # Validate date format
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date format. Expected YYYY-MM-DD format. Error: {str(e)}"
            )
        
        # Validate metric_type
        valid_metric_types = ["issues_completed", "issues_removed", "issues_added", "total_scope", "wip_in_progress", "actual_remaining"]
        if metric_type not in valid_metric_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid metric_type. Must be one of: {', '.join(valid_metric_types)}"
            )
        
        # Resolve team names using shared helper function
        # Returns None if no team_name provided (meaning all teams)
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        logger.info(f"Fetching PI history info: date={date}, pi={pi}, team_name={team_name}, isGroup={isGroup}, issue_type={issue_type}, metric_type={metric_type}")
        if team_names_list:
            logger.info(f"Resolved team names: {team_names_list}")
        
        # Call database helper function
        issues = get_pi_history_issues_db(
            pi_name=pi,
            target_date=target_date,
            team_names=team_names_list,
            issue_type=issue_type,
            metric_type=metric_type,
            conn=conn
        )
        
        # Build response
        response_data = {
            "issues": issues,
            "count": len(issues),
            "date": date,
            "pi": pi,
            "metric_type": metric_type
        }
        
        # Add optional filters to response
        if team_name:
            if isGroup:
                response_data["group_name"] = team_name
                if team_names_list:
                    response_data["teams_in_group"] = team_names_list
            else:
                response_data["team_name"] = team_name
        
        if issue_type:
            response_data["issue_type"] = issue_type
        
        return {
            "success": True,
            "data": response_data,
            "message": f"Retrieved {len(issues)} issues"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching PI history info: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch PI history info: {str(e)}"
        )


@issues_router.get("/issues/get-release-history-info")
async def get_release_history_info(
    date: str = Query(..., description="Date to query (YYYY-MM-DD format, date only, no time)"),
    release: str = Query(..., description="Release name to filter by"),
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    issue_type: Optional[str] = Query(None, description="Filter by issue type (e.g., 'Story', 'Bug', 'Epic')"),
    metric_type: str = Query(..., description="Metric type: 'issues_completed', 'issues_removed', 'total_scope', 'wip_in_progress', or 'actual_remaining'"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get history info for issues on a specific date in a release.
    
    Returns issues matching the specified metric type for the given date, release, team, and issue type.
    
    Args:
        date: Date to query (YYYY-MM-DD format, date only, no time)
        release: Release name to filter by
        team_name: Optional team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
        issue_type: Optional filter by issue type
        metric_type: Metric type to return:
            - "issues_completed" - Issues completed on this day
            - "issues_removed" - Issues removed from release (were in release day before, not now)
            - "total_scope" - Total scope of release on this day (all issues in release)
            - "wip_in_progress" - Work in progress items on this day
            - "actual_remaining" - Actual remaining items on this day (not done)
    
    Returns:
        JSON response with issues list and metadata
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        from database_releases import get_release_history_issues_db
        
        # Validate date format
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date format. Expected YYYY-MM-DD format. Error: {str(e)}"
            )
        
        # Validate metric_type
        valid_metric_types = ["issues_completed", "issues_removed", "total_scope", "wip_in_progress", "actual_remaining"]
        if metric_type not in valid_metric_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid metric_type. Must be one of: {', '.join(valid_metric_types)}"
            )
        
        # Resolve team names using shared helper function
        # Returns None if no team_name provided (meaning all teams)
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        logger.info(f"Fetching release history info: date={date}, release={release}, team_name={team_name}, isGroup={isGroup}, issue_type={issue_type}, metric_type={metric_type}")
        if team_names_list:
            logger.info(f"Resolved team names: {team_names_list}")
        
        # Call database helper function
        issues = get_release_history_issues_db(
            release_name=release,
            target_date=target_date,
            team_names=team_names_list,
            issue_type=issue_type,
            metric_type=metric_type,
            conn=conn
        )
        
        # Build response
        response_data = {
            "issues": issues,
            "count": len(issues),
            "date": date,
            "release": release,
            "metric_type": metric_type
        }
        
        # Add optional filters to response
        if team_name:
            if isGroup:
                response_data["group_name"] = team_name
                if team_names_list:
                    response_data["teams_in_group"] = team_names_list
            else:
                response_data["team_name"] = team_name
        
        if issue_type:
            response_data["issue_type"] = issue_type
        
        return {
            "success": True,
            "data": response_data,
            "message": f"Retrieved {len(issues)} issues"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching release history info: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch release history info: {str(e)}"
        )


@issues_router.get("/issues/list")
async def get_issues_list(
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    pi: Optional[str] = Query(None, description="Filter by PI (quarter_pi)"),
    issue_type: Optional[str] = Query(None, description="Filter by issue type"),
    status: Optional[str] = Query(None, description="Filter by status"),
    dependency: Optional[bool] = Query(None, description="Filter by dependency flag"),
    flagged: Optional[bool] = Query(None, description="Filter by flagged flag"),
    sprint_id: Optional[int] = Query(None, description="Filter by sprint ID (matches any sprint_ids array element)"),
    limit: int = Query(settings.DEFAULT_QUERY_LIMIT, description=f"Number of issues to return (default: {settings.DEFAULT_QUERY_LIMIT}, max: 1000)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get a list of issues with all fields from jira_issues table, with optional filtering.
    
    Args:
        team_name: Team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
        pi: Optional filter by PI (quarter_pi)
        issue_type: Optional filter by issue type
        status: Optional filter by status
        dependency: Optional filter by dependency flag (true/false)
        flagged: Optional filter by flagged flag (true/false)
        sprint_id: Optional filter by sprint ID
        limit: Number of issues to return (default: 500, max: 1000)
    
    Returns:
        JSON response with issues list (all fields) and count
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Validate limit
        validated_limit = validate_limit(limit)
        
        # Resolve team names if team_name is provided
        team_names_list = None
        if team_name:
            team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Build WHERE clause conditions
        where_conditions = []
        params = {"limit": validated_limit}
        
        if issue_type:
            where_conditions.append("issue_type = :issue_type")
            params["issue_type"] = issue_type
        
        if status:
            where_conditions.append("status = :status")
            params["status"] = status
        
        if dependency is not None:
            where_conditions.append("dependency = :dependency")
            params["dependency"] = dependency
        
        if flagged is not None:
            where_conditions.append("flagged = :flagged")
            params["flagged"] = flagged
        
        if team_names_list:
            placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names_list))])
            where_conditions.append(f"team_name IN ({placeholders})")
            for i, name in enumerate(team_names_list):
                params[f"team_name_{i}"] = name
        
        if pi:
            where_conditions.append("quarter_pi = :quarter_pi")
            params["quarter_pi"] = pi
        
        if sprint_id is not None:
            where_conditions.append(":sprint_id = ANY(sprint_ids)")
            params["sprint_id"] = sprint_id
        
        # Build SQL query - select all columns
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        query = text(f"""
            SELECT *
            FROM {config.WORK_ITEMS_TABLE}
            WHERE {where_clause}
            ORDER BY issue_id DESC
            LIMIT :limit
        """)
        
        logger.info(f"Executing query to get issues list with filters: team_name={team_name}, isGroup={isGroup}, pi={pi}, issue_type={issue_type}, status={status}, dependency={dependency}, flagged={flagged}, sprint_id={sprint_id}, limit={validated_limit}")
        if team_names_list:
            logger.info(f"Resolved team names: {team_names_list}")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries (all columns)
        issues = []
        for row in rows:
            issue_dict = dict(row._mapping)
            issues.append(issue_dict)
        
        return {
            "success": True,
            "data": {
                "issues": issues,
                "count": len(issues)
            },
            "message": f"Retrieved {len(issues)} issues"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching issues list: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch issues: {str(e)}"
        )


async def get_cycle_time_with_issue_keys(
    request: Request,
    period_start: str = Query(..., description="Start date (YYYY-MM-DD) - filter by resolved_at >= period_start"),
    period_end: str = Query(..., description="End date (YYYY-MM-DD) - filter by resolved_at <= period_end"),
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    issue_type: Optional[str] = Query(None, description="Filter by issue type(s) - can be single value, comma-separated, or multiple params (e.g., 'Story,Bug' or ?issue_type=Story&issue_type=Bug)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get issues with cycle time for a specific period.
    
    Returns issue keys, summaries, cycle times, resolved dates, issue types, and team names
    for completed issues within the specified date range.
    
    Args:
        period_start: Start date (YYYY-MM-DD) - required
        period_end: End date (YYYY-MM-DD) - required
        team_name: Optional team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
        issue_type: Optional filter by issue type(s) - supports multi-value (comma-separated or multiple params)
    
    Returns:
        JSON response with list of issues (max 100) containing:
        - issue_key
        - summary
        - cycle_time (rounded to 2 decimal places)
        - resolved_at (date string)
        - issue_type
        - team_name
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Validate and parse dates
        try:
            start_date = datetime.strptime(period_start, "%Y-%m-%d").date()
            end_date = datetime.strptime(period_end, "%Y-%m-%d").date()
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date format. Expected YYYY-MM-DD format. Error: {str(e)}"
            )
        
        # Validate date range
        if start_date > end_date:
            raise HTTPException(
                status_code=400,
                detail="period_start must be less than or equal to period_end"
            )
        
        # Normalize multi-value issue_type parameter
        # Handle both single query param and multiple query params
        issue_type_values = None
        # Get all issue_type values from query params (handles multiple params like ?issue_type=Story&issue_type=Bug)
        issue_type_params = request.query_params.getlist("issue_type")
        if issue_type_params:
            issue_type_values = _normalize_multi_value_issue_type(issue_type_params)
        elif issue_type:
            # Fallback to single parameter if not provided as multiple params
            issue_type_values = _normalize_multi_value_issue_type(issue_type)
        
        # Resolve team names using shared helper function
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Build WHERE clause conditions
        where_conditions = [
            "status_category = 'Done'",
            f"cycle_time_days >= {settings.MIN_DURATION_AND_CYCLE_TIME_DAYS}",
            "resolved_at IS NOT NULL",
            "DATE(resolved_at) >= :period_start",
            "DATE(resolved_at) <= :period_end"
        ]
        
        params = {
            "period_start": start_date.strftime("%Y-%m-%d"),
            "period_end": end_date.strftime("%Y-%m-%d"),
            "limit": 100
        }
        
        # Add team filter if provided
        if team_names_list:
            team_placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names_list))])
            where_conditions.append(f"team_name IN ({team_placeholders})")
            for i, name in enumerate(team_names_list):
                params[f"team_name_{i}"] = name
        
        # Add issue_type filter if provided
        if issue_type_values:
            issue_type_placeholders = ", ".join([f":issue_type_{i}" for i in range(len(issue_type_values))])
            where_conditions.append(f"issue_type IN ({issue_type_placeholders})")
            for i, itype in enumerate(issue_type_values):
                params[f"issue_type_{i}"] = itype
        
        # Build SQL query
        where_clause = " AND ".join(where_conditions)
        
        query = text(f"""
            SELECT 
                issue_key,
                summary,
                ROUND(cycle_time_days, 2) AS cycle_time,
                DATE(resolved_at) AS resolved_at,
                issue_type,
                team_name
            FROM {config.WORK_ITEMS_TABLE}
            WHERE {where_clause}
            ORDER BY resolved_at DESC
            LIMIT :limit
        """)
        
        logger.info(f"Executing query to get cycle time with issue keys: period_start={period_start}, period_end={period_end}, team_name={team_name}, isGroup={isGroup}, issue_type={issue_type_values}")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        issues = []
        for row in rows:
            issue_dict = {
                "issue_key": row[0],
                "summary": row[1],
                "cycle_time": float(row[2]) if row[2] is not None else None,
                "resolved_at": row[3].strftime("%Y-%m-%d") if row[3] else None,
                "issue_type": row[4],
                "team_name": row[5]
            }
            issues.append(issue_dict)
        
        return {
            "success": True,
            "data": {
                "issues": issues,
                "count": len(issues),
                "limit": 100
            },
            "message": f"Retrieved {len(issues)} issues"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error fetching cycle time with issue keys: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch cycle time with issue keys: {str(e)}"
        )