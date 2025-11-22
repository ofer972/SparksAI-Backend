"""
Groups Service - REST API endpoints for team group-related operations.

This service provides endpoints for managing and retrieving team group information.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import Optional
from pydantic import BaseModel
import logging
from database_connection import get_db_connection

logger = logging.getLogger(__name__)

groups_router = APIRouter()

def validate_id(id_value: int, field_name: str = "ID") -> int:
    """
    Validate ID parameter to ensure it's a positive integer.
    """
    if not isinstance(id_value, int) or id_value < 1:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a positive integer")
    return id_value


@groups_router.get("/groups")
async def get_all_groups(conn: Connection = Depends(get_db_connection)):
    """
    Get all groups (flat list with hierarchy info).
    Returns empty array with 200 status if no groups exist.
    Uses groups/teams cache for fast retrieval.
    
    Returns:
        JSON response with list of groups (id, name, parent_id)
    """
    try:
        from groups_teams_cache import get_all_groups_from_cache
        
        groups = get_all_groups_from_cache()
        
        return {
            "success": True,
            "data": {
                "groups": groups,
                "count": len(groups)
            },
            "message": f"Retrieved {len(groups)} groups"
        }
    
    except RuntimeError as e:
        logger.error(f"Cache not loaded: {e}")
        raise HTTPException(
            status_code=500,
            detail="Groups/Teams cache not loaded. Please restart the application."
        )
    except Exception as e:
        logger.error(f"Error fetching groups: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch groups: {str(e)}"
        )


@groups_router.get("/groups/{groupId}/teams")
async def get_teams_in_group(
    groupId: int,
    conn: Connection = Depends(get_db_connection)
):
    """
    Get all teams in a specific group.
    Uses groups/teams cache for fast retrieval.
    
    Args:
        groupId: The ID of the group
    
    Returns:
        JSON response with list of teams in the group
    """
    try:
        from groups_teams_cache import get_teams_in_group_from_cache, group_exists_in_cache
        
        validated_group_id = validate_id(groupId, "Group ID")
        
        # Check if group exists
        if not group_exists_in_cache(validated_group_id):
            raise HTTPException(
                status_code=404,
                detail=f"Group with ID {validated_group_id} not found"
            )
        
        # Get teams from cache (direct only, not recursive)
        teams = get_teams_in_group_from_cache(validated_group_id, include_children=False)
        
        return {
            "success": True,
            "data": {
                "teams": teams,
                "count": len(teams),
                "group_key": validated_group_id
            },
            "message": f"Retrieved {len(teams)} teams for group {validated_group_id}"
        }
    
    except HTTPException:
        raise
    except RuntimeError as e:
        logger.error(f"Cache not loaded: {e}")
        raise HTTPException(
            status_code=500,
            detail="Groups/Teams cache not loaded. Please restart the application."
        )
    except Exception as e:
        logger.error(f"Error fetching teams for group {groupId}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch teams for group: {str(e)}"
        )


# Pydantic models for request bodies
class GroupCreateRequest(BaseModel):
    group_name: str
    parent_group_key: Optional[int] = None


class GroupUpdateRequest(BaseModel):
    group_name: Optional[str] = None
    parent_group_key: Optional[int] = None


@groups_router.post("/groups")
async def create_group(
    request: GroupCreateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Create a new group (root or sub-group).
    
    Args:
        request: GroupCreateRequest with group_name and optional parent_group_key
    
    Returns:
        JSON response with created group
    """
    try:
        # Validate parent_group_key if provided
        parent_key = None
        if request.parent_group_key is not None:
            from groups_teams_cache import group_exists_in_cache
            
            parent_key = validate_id(request.parent_group_key, "Parent group key")
            # Verify parent group exists in cache
            if not group_exists_in_cache(parent_key):
                raise HTTPException(
                    status_code=404,
                    detail=f"Parent group with ID {parent_key} not found"
                )
        
        query = text("""
            INSERT INTO public.groups (group_name, parent_group_key)
            VALUES (:group_name, :parent_group_key)
            RETURNING group_key, group_name, parent_group_key
        """)
        
        logger.info(f"Creating group: {request.group_name}, parent: {parent_key}")
        
        result = conn.execute(query, {
            "group_name": request.group_name,
            "parent_group_key": parent_key
        })
        row = result.fetchone()
        conn.commit()
        
        # Refresh cache after mutation
        from groups_teams_cache import refresh_groups_teams_cache
        refresh_groups_teams_cache(conn)
        
        # Return database field names to match pattern in other services
        group = {
            "group_key": row[0],
            "group_name": row[1],
            "parent_group_key": row[2]
        }
        
        return {
            "success": True,
            "data": {
                "group": group
            },
            "message": f"Created group with ID {group['group_key']}"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating group: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create group: {str(e)}"
        )


@groups_router.patch("/groups/{groupId}")
async def update_group(
    groupId: int,
    request: GroupUpdateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Update group (name or parent for moving).
    
    Args:
        groupId: The ID of the group to update
        request: GroupUpdateRequest with optional fields to update
    
    Returns:
        JSON response with updated group
    """
    try:
        from groups_teams_cache import group_exists_in_cache
        
        validated_group_id = validate_id(groupId, "Group ID")
        
        # Check if group exists in cache
        if not group_exists_in_cache(validated_group_id):
            raise HTTPException(
                status_code=404,
                detail=f"Group with ID {validated_group_id} not found"
            )
        
        # Validate parent_group_key if provided
        parent_key = request.parent_group_key
        if parent_key is not None:
            parent_key = validate_id(parent_key, "Parent group key")
            # Prevent self-reference
            if parent_key == validated_group_id:
                raise HTTPException(
                    status_code=400,
                    detail="Group cannot be its own parent"
                )
            # Verify parent group exists in cache
            if not group_exists_in_cache(parent_key):
                raise HTTPException(
                    status_code=404,
                    detail=f"Parent group with ID {parent_key} not found"
                )
        
        # Build dynamic UPDATE query
        updates = []
        params = {"group_key": validated_group_id}
        
        if request.group_name is not None:
            updates.append("group_name = :group_name")
            params["group_name"] = request.group_name
        
        if request.parent_group_key is not None:
            updates.append("parent_group_key = :parent_group_key")
            params["parent_group_key"] = parent_key
        
        if not updates:
            raise HTTPException(
                status_code=400,
                detail="At least one field must be provided for update"
            )
        
        set_clause = ", ".join(updates)
        query = text(f"""
            UPDATE public.groups
            SET {set_clause}
            WHERE group_key = :group_key
            RETURNING group_key, group_name, parent_group_key
        """)
        
        logger.info(f"Updating group {validated_group_id}")
        
        result = conn.execute(query, params)
        row = result.fetchone()
        conn.commit()
        
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Group with ID {validated_group_id} not found"
            )
        
        # Refresh cache after mutation
        from groups_teams_cache import refresh_groups_teams_cache
        refresh_groups_teams_cache(conn)
        
        # Return database field names to match pattern in other services
        group = {
            "group_key": row[0],
            "group_name": row[1],
            "parent_group_key": row[2]
        }
        
        return {
            "success": True,
            "data": {
                "group": group
            },
            "message": f"Group {validated_group_id} updated successfully"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating group {groupId}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update group: {str(e)}"
        )


@groups_router.delete("/groups/{groupId}")
async def delete_group(
    groupId: int,
    conn: Connection = Depends(get_db_connection)
):
    """
    Delete a group. Moves all teams in this group to parent=null.
    
    Args:
        groupId: The ID of the group to delete
    
    Returns:
        JSON response with deletion confirmation
    """
    try:
        from groups_teams_cache import group_exists_in_cache
        
        validated_group_id = validate_id(groupId, "Group ID")
        
        # Check if group exists in cache
        if not group_exists_in_cache(validated_group_id):
            raise HTTPException(
                status_code=404,
                detail=f"Group with ID {validated_group_id} not found"
            )
        
        # First, remove all team-group associations for this group
        # (CASCADE will handle this, but we do it explicitly for clarity)
        delete_associations_query = text("""
            DELETE FROM public.team_groups
            WHERE group_id = :group_key
        """)
        conn.execute(delete_associations_query, {"group_key": validated_group_id})
        
        # Then delete the group
        delete_query = text("""
            DELETE FROM public.groups
            WHERE group_key = :group_key
        """)
        result = conn.execute(delete_query, {"group_key": validated_group_id})
        conn.commit()
        
        if result.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail=f"Group with ID {validated_group_id} not found"
            )
        
        # Refresh cache after mutation
        from groups_teams_cache import refresh_groups_teams_cache
        refresh_groups_teams_cache(conn)
        
        logger.info(f"Deleted group {validated_group_id} and moved teams to null")
        
        return {
            "success": True,
            "data": {
                "id": validated_group_id
            },
            "message": f"Group {validated_group_id} deleted successfully"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting group {groupId}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete group: {str(e)}"
        )

