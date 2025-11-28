"""
Teams Service - REST API endpoints for team-related operations.

This service provides endpoints for managing and retrieving team information.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
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

def validate_id(id_value: int, field_name: str = "ID") -> int:
    """
    Validate ID parameter to ensure it's a positive integer.
    """
    if not isinstance(id_value, int) or id_value < 1:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a positive integer")
    return id_value

def validate_limit(limit: int) -> int:
    """
    Validate limit parameter to prevent abuse.
    """
    if limit < 1:
        raise HTTPException(status_code=400, detail="Limit must be at least 1")
    
    if limit > 1000:  # Reasonable upper limit
        raise HTTPException(status_code=400, detail="Limit cannot exceed 1000")
    
    return limit


# ====================
# Existing Endpoints
# ====================

@teams_router.get("/teams/getNames")
async def get_team_names(conn: Connection = Depends(get_db_connection)):
    """
    Get all team names from teams table.
    Uses groups/teams cache for fast retrieval.
    
    Returns:
        JSON response with list of team names and count
    """
    try:
        from groups_teams_cache import get_cached_teams, set_cached_teams, load_team_names_from_db, load_all_teams_from_db
        
        # Try cache first
        cached = get_cached_teams()
        if cached:
            team_names = [t["team_name"] for t in cached.get("teams", [])]
        else:
            # Cache miss - load from DB
            team_names = load_team_names_from_db(conn)
            # Also build full teams cache for future use
            all_teams = load_all_teams_from_db(conn)
            set_cached_teams({"teams": all_teams, "count": len(all_teams)})
        
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


# ====================
# Team Endpoints
# ====================

@teams_router.get("/teams")
async def get_all_teams(
    group_key: Optional[int] = Query(None, description="Filter by group key"),
    search: Optional[str] = Query(None, description="Search team names"),
    ai_insight: Optional[bool] = Query(None, description="Filter by AI insight flag (true/false)"),
    limit: int = Query(200, description="Maximum number of teams to return (default: 200, max: 1000)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get all teams with optional filtering.
    
    Args:
        group_key: Optional filter by group
        search: Optional search term for team names
        ai_insight: Optional filter by AI insight flag (true/false)
        limit: Maximum number of results (default: 200, max: 1000)
    
    Returns:
        JSON response with list of teams
    """
    try:
        validated_limit = validate_limit(limit)
        
        # Validate group_key if provided
        validated_group_key = None
        if group_key is not None:
            validated_group_key = validate_id(group_key, "Group key")
        
        # Try cache first
        from groups_teams_cache import get_cached_teams, set_cached_teams, load_all_teams_from_db
        
        cached = get_cached_teams()
        if cached:
            all_teams = cached.get("teams", [])
        else:
            # Cache miss - load from DB
            all_teams = load_all_teams_from_db(conn)
            set_cached_teams({"teams": all_teams, "count": len(all_teams)})
        
        # Apply filters in service layer
        teams = []
        for team in all_teams:
            # Apply filters
            if validated_group_key is not None and validated_group_key not in team.get("group_keys", []):
                continue
            if search and search.lower() not in team["team_name"].lower():
                continue
            if ai_insight is not None and team.get("ai_insight") != ai_insight:
                continue
            teams.append(team)
        
        # Sort and limit
        teams.sort(key=lambda x: x["team_name"])
        teams = teams[:validated_limit]
        
        return {
            "success": True,
            "data": {
                "teams": teams,
                "count": len(teams)
            },
            "message": f"Retrieved {len(teams)} teams"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching teams: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch teams: {str(e)}"
        )


class TeamCreateRequest(BaseModel):
    team_name: str
    number_of_team_members: int = 0
    group_keys: Optional[list[int]] = None  # Changed from group_key to group_keys (list)


class TeamUpdateRequest(BaseModel):
    team_name: Optional[str] = None
    number_of_team_members: Optional[int] = None
    ai_insight: Optional[bool] = None
    group_keys: Optional[list[int]] = None  # Changed from group_key to group_keys (list)


@teams_router.post("/teams")
async def create_team(
    request: TeamCreateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Create a new team.
    
    Args:
        request: TeamCreateRequest with team_name, number_of_team_members, and optional group_key
    
    Returns:
        JSON response with created team
    """
    try:
        # Validate group_keys if provided
        from groups_teams_cache import get_cached_groups, group_exists_in_db
        
        group_keys = []
        if request.group_keys:
            cached_groups = get_cached_groups()
            for gk in request.group_keys:
                validated_gk = validate_id(gk, "Group key")
                # Verify group exists
                if cached_groups:
                    groups = cached_groups.get("groups", [])
                    group_exists = any(g.get("id") == validated_gk for g in groups)
                else:
                    group_exists = group_exists_in_db(validated_gk, conn)
                
                if not group_exists:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Group with ID {validated_gk} not found"
                    )
                group_keys.append(validated_gk)
        
        # Validate number_of_team_members
        if request.number_of_team_members < 0:
            raise HTTPException(
                status_code=400,
                detail="number_of_team_members must be 0 or greater"
            )
        
        # Create team without group_key column
        query = text("""
            INSERT INTO public.teams (team_name, number_of_team_members)
            VALUES (:team_name, :number_of_team_members)
            RETURNING team_key, team_name, number_of_team_members
        """)
        
        logger.info(f"Creating team: {request.team_name}, members: {request.number_of_team_members}, groups: {group_keys}")
        
        result = conn.execute(query, {
            "team_name": request.team_name,
            "number_of_team_members": request.number_of_team_members
        })
        row = result.fetchone()
        team_key = row[0]
        
        # Insert team-group associations
        if group_keys:
            for group_id in group_keys:
                insert_assoc_query = text("""
                    INSERT INTO public.team_groups (team_id, group_id)
                    VALUES (:team_id, :group_id)
                    ON CONFLICT DO NOTHING
                """)
                conn.execute(insert_assoc_query, {"team_id": team_key, "group_id": group_id})
        
        conn.commit()
        
        # Refresh cache after mutation
        from groups_teams_cache import invalidate_groups_teams_cache
        invalidate_groups_teams_cache()
        
        team = {
            "team_key": row[0],
            "team_name": row[1],
            "number_of_team_members": row[2],
            "group_keys": group_keys
        }
        
        return {
            "success": True,
            "data": {
                "team": team
            },
            "message": f"Created team with ID {team['team_key']}"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating team: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create team: {str(e)}"
        )


@teams_router.patch("/teams/{teamId}")
async def update_team(
    teamId: int,
    request: TeamUpdateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Update team details.
    
    Args:
        teamId: The ID of the team to update
        request: TeamUpdateRequest with optional fields to update
    
    Returns:
        JSON response with updated team
    """
    try:
        from groups_teams_cache import (
            get_cached_groups, get_cached_teams,
            team_exists_in_db, group_exists_in_db
        )
        
        validated_team_id = validate_id(teamId, "Team ID")
        
        # Check if team exists
        cached_teams = get_cached_teams()
        if cached_teams:
            teams = cached_teams.get("teams", [])
            team_exists = any(t.get("team_key") == validated_team_id for t in teams)
        else:
            team_exists = team_exists_in_db(validated_team_id, conn)
        
        if not team_exists:
            raise HTTPException(
                status_code=404,
                detail=f"Team with ID {validated_team_id} not found"
            )
        
        # Validate group_keys if provided
        group_keys = None
        if request.group_keys is not None:
            group_keys = []
            cached_groups = get_cached_groups()
            for gk in request.group_keys:
                validated_gk = validate_id(gk, "Group key")
                # Verify group exists
                if cached_groups:
                    groups = cached_groups.get("groups", [])
                    group_exists = any(g.get("id") == validated_gk for g in groups)
                else:
                    group_exists = group_exists_in_db(validated_gk, conn)
                
                if not group_exists:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Group with ID {validated_gk} not found"
                    )
                group_keys.append(validated_gk)
        
        # Validate number_of_team_members if provided
        if request.number_of_team_members is not None and request.number_of_team_members < 0:
            raise HTTPException(
                status_code=400,
                detail="number_of_team_members must be 0 or greater"
            )
        
        # Build dynamic UPDATE query (no group_key column anymore)
        updates = []
        params = {"team_key": validated_team_id}
        
        if request.team_name is not None:
            updates.append("team_name = :team_name")
            params["team_name"] = request.team_name
        
        if request.number_of_team_members is not None:
            updates.append("number_of_team_members = :number_of_team_members")
            params["number_of_team_members"] = request.number_of_team_members
        
        if request.ai_insight is not None:
            updates.append("ai_insight = :ai_insight")
            params["ai_insight"] = request.ai_insight
        
        if not updates and group_keys is None:
            raise HTTPException(
                status_code=400,
                detail="At least one field must be provided for update"
            )
        
        # Update team basic info if there are updates
        if updates:
            set_clause = ", ".join(updates)
            query = text(f"""
                UPDATE public.teams
                SET {set_clause}
                WHERE team_key = :team_key
                RETURNING team_key, team_name, number_of_team_members, ai_insight
            """)
            
            logger.info(f"Updating team {validated_team_id}")
            
            result = conn.execute(query, params)
            row = result.fetchone()
            
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail=f"Team with ID {validated_team_id} not found"
                )
        else:
            # Just fetch current team data
            query = text("""
                SELECT team_key, team_name, number_of_team_members, ai_insight
                FROM public.teams
                WHERE team_key = :team_key
            """)
            result = conn.execute(query, {"team_key": validated_team_id})
            row = result.fetchone()
        
        # Update group associations if provided
        if group_keys is not None:
            # Delete all existing associations
            delete_assoc_query = text("""
                DELETE FROM public.team_groups WHERE team_id = :team_id
            """)
            conn.execute(delete_assoc_query, {"team_id": validated_team_id})
            
            # Insert new associations
            for group_id in group_keys:
                insert_assoc_query = text("""
                    INSERT INTO public.team_groups (team_id, group_id)
                    VALUES (:team_id, :group_id)
                    ON CONFLICT DO NOTHING
                """)
                conn.execute(insert_assoc_query, {"team_id": validated_team_id, "group_id": group_id})
        
        conn.commit()
        
        # Refresh cache after mutation
        from groups_teams_cache import invalidate_groups_teams_cache
        invalidate_groups_teams_cache()
        
        # Fetch current group associations from database (after refresh, cache is up to date)
        fetch_groups_query = text("""
            SELECT array_agg(group_id) as group_keys
            FROM public.team_groups
            WHERE team_id = :team_id
        """)
        groups_result = conn.execute(fetch_groups_query, {"team_id": validated_team_id})
        groups_row = groups_result.fetchone()
        current_group_keys = groups_row[0] if groups_row and groups_row[0] else []
        
        team = {
            "team_key": row[0],
            "team_name": row[1],
            "number_of_team_members": row[2],
            "ai_insight": row[3],
            "group_keys": current_group_keys
        }
        
        return {
            "success": True,
            "data": {
                "team": team
            },
            "message": f"Team {validated_team_id} updated successfully"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating team {teamId}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update team: {str(e)}"
        )


@teams_router.delete("/teams/{teamId}")
async def delete_team(
    teamId: int,
    conn: Connection = Depends(get_db_connection)
):
    """
    Delete a team.
    
    Args:
        teamId: The ID of the team to delete
    
    Returns:
        JSON response with deletion confirmation
    """
    try:
        validated_team_id = validate_id(teamId, "Team ID")
        
        query = text("""
            DELETE FROM public.teams
            WHERE team_key = :team_key
        """)
        
        logger.info(f"Deleting team {validated_team_id}")
        
        result = conn.execute(query, {"team_key": validated_team_id})
        conn.commit()
        
        if result.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail=f"Team with ID {validated_team_id} not found"
            )
        
        # Refresh cache after mutation
        from groups_teams_cache import invalidate_groups_teams_cache
        invalidate_groups_teams_cache()
        
        return {
            "success": True,
            "data": {
                "id": validated_team_id
            },
            "message": f"Team {validated_team_id} deleted successfully"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting team {teamId}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete team: {str(e)}"
        )


class BatchAssignRequest(BaseModel):
    group_id: int
    team_ids: List[int]


@teams_router.put("/teams/batch-assign")
async def batch_assign_teams_to_group(
    request: BatchAssignRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Assign multiple teams to a single group.
    
    Args:
        request: BatchAssignRequest with group_id and team_ids array
    
    Returns:
        JSON response with assignment confirmation
    """
    try:
        validated_group_id = validate_id(request.group_id, "Group ID")
        
        # Validate all team IDs
        validated_team_ids = [validate_id(tid, "Team ID") for tid in request.team_ids]
        
        if not validated_team_ids:
            raise HTTPException(
                status_code=400,
                detail="team_ids array cannot be empty"
            )
        
        # Verify group exists
        from groups_teams_cache import get_cached_groups, get_cached_teams, group_exists_in_db, team_exists_in_db
        
        cached_groups = get_cached_groups()
        if cached_groups:
            groups = cached_groups.get("groups", [])
            group_exists = any(g.get("id") == validated_group_id for g in groups)
        else:
            group_exists = group_exists_in_db(validated_group_id, conn)
        
        if not group_exists:
            raise HTTPException(
                status_code=404,
                detail=f"Group with ID {validated_group_id} not found"
            )
        
        # Insert team-group associations (many-to-many)
        logger.info(f"Batch assigning {len(validated_team_ids)} teams to group {validated_group_id}")
        
        updated_count = 0
        cached_teams = get_cached_teams()
        for team_id in validated_team_ids:
            # Verify team exists
            if cached_teams:
                teams = cached_teams.get("teams", [])
                team_exists = any(t.get("team_key") == team_id for t in teams)
            else:
                team_exists = team_exists_in_db(team_id, conn)
            
            if not team_exists:
                logger.warning(f"Team {team_id} not found, skipping")
                continue
            
            # Insert association
            insert_query = text("""
                INSERT INTO public.team_groups (team_id, group_id)
                VALUES (:team_id, :group_id)
                ON CONFLICT DO NOTHING
            """)
            conn.execute(insert_query, {"team_id": team_id, "group_id": validated_group_id})
            updated_count += 1
        
        conn.commit()
        
        # Refresh cache after mutation
        from groups_teams_cache import invalidate_groups_teams_cache
        invalidate_groups_teams_cache()
        
        return {
            "success": True,
            "data": {
                "updated_teams": updated_count,
                "group_id": validated_group_id,
                "team_ids": validated_team_ids
            },
            "message": f"Assigned {updated_count} teams to group {validated_group_id}"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error batch assigning teams: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to batch assign teams: {str(e)}"
        )


@teams_router.delete("/teams/{teamId}/group")
async def remove_team_from_group(
    teamId: int,
    conn: Connection = Depends(get_db_connection)
):
    """
    Remove team from all groups.
    
    Args:
        teamId: The ID of the team to remove from all groups
    
    Returns:
        JSON response with confirmation
    """
    try:
        validated_team_id = validate_id(teamId, "Team ID")
        
        # Verify team exists and get team data
        from groups_teams_cache import get_cached_teams, get_team_by_id_from_db
        
        cached_teams = get_cached_teams()
        if cached_teams:
            teams = cached_teams.get("teams", [])
            team_data = next((t for t in teams if t.get("team_key") == validated_team_id), None)
        else:
            team_data = get_team_by_id_from_db(validated_team_id, conn)
        
        if not team_data:
            raise HTTPException(
                status_code=404,
                detail=f"Team with ID {validated_team_id} not found"
            )
        
        row = (team_data["team_key"], team_data["team_name"], team_data["number_of_team_members"])
        
        # Delete all team-group associations
        delete_query = text("""
            DELETE FROM public.team_groups
            WHERE team_id = :team_id
        """)
        
        logger.info(f"Removing team {validated_team_id} from all groups")
        
        delete_result = conn.execute(delete_query, {"team_id": validated_team_id})
        conn.commit()
        
        # Refresh cache after mutation
        from groups_teams_cache import invalidate_groups_teams_cache
        invalidate_groups_teams_cache()
        
        removed_count = delete_result.rowcount
        
        team = {
            "team_key": row[0],
            "team_name": row[1],
            "number_of_team_members": row[2],
            "group_keys": []
        }
        
        return {
            "success": True,
            "data": {
                "team": team,
                "removed_associations": removed_count
            },
            "message": f"Team {validated_team_id} removed from {removed_count} group(s)"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing team from group {teamId}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to remove team from group: {str(e)}"
        )


@teams_router.post("/teams/populate-teams-from-jira-issues")
async def populate_teams_from_jira_issues(
    conn: Connection = Depends(get_db_connection)
):
    """
    Populate teams table from distinct team names in jira_issues table.
    Uses UPSERT to insert new teams without touching existing group connections.
    
    Returns:
        JSON response with count of teams processed
    """
    try:
        # First, get all distinct team names from jira_issues
        select_query = text(f"""
            SELECT DISTINCT team_name 
            FROM {config.WORK_ITEMS_TABLE} 
            WHERE team_name IS NOT NULL 
            AND team_name != ''
            ORDER BY team_name
        """)
        
        logger.info("Fetching distinct team names from jira_issues table")
        
        result = conn.execute(select_query)
        rows = result.fetchall()
        team_names = [row[0] for row in rows]
        
        if not team_names:
            return {
                "success": True,
                "data": {
                    "teams_processed": 0,
                    "teams_inserted": 0,
                    "teams_existed": 0
                },
                "message": "No team names found in jira_issues table"
            }
        
        # UPSERT each team name
        # Use ON CONFLICT DO NOTHING to preserve existing team data
        insert_query = text("""
            INSERT INTO public.teams (team_name, number_of_team_members)
            VALUES (:team_name, 0)
            ON CONFLICT (team_name) DO NOTHING
        """)
        
        inserted_count = 0
        existed_count = 0
        
        for team_name in team_names:
            result = conn.execute(insert_query, {"team_name": team_name})
            if result.rowcount > 0:
                inserted_count += 1
            else:
                existed_count += 1
        
        conn.commit()
        
        # Refresh cache after bulk mutation - invalidate and rebuild
        from groups_teams_cache import invalidate_groups_teams_cache, populate_groups_teams_cache
        
        invalidate_groups_teams_cache()
        cache_success, _, _ = populate_groups_teams_cache(conn)
        
        if cache_success:
            logger.info(f"Populated teams: {inserted_count} inserted, {existed_count} already existed. Cache refreshed.")
        else:
            logger.warning(f"Populated teams: {inserted_count} inserted, {existed_count} already existed. Cache refresh failed (Redis unavailable).")
        
        return {
            "success": True,
            "data": {
                "teams_processed": len(team_names),
                "teams_inserted": inserted_count,
                "teams_existed": existed_count
            },
            "message": f"Processed {len(team_names)} teams: {inserted_count} inserted, {existed_count} already existed"
        }
    
    except Exception as e:
        logger.error(f"Error populating teams from jira_issues: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to populate teams from jira_issues: {str(e)}"
        )

