"""
Groups and Teams Cache - Redis-based cache for groups and teams data.

This module provides simple cache functions following the same pattern as reports cache.
Cache functions are simple get/set - services orchestrate the flow.
"""

import json
from typing import Dict, List, Optional, Any, Tuple
from sqlalchemy import text
from sqlalchemy.engine import Connection
import logging
from cache_utils import get_redis_client
import config

logger = logging.getLogger(__name__)

# Cache keys
CACHE_KEY_GROUPS = "groups:all"
CACHE_KEY_TEAMS = "teams:all"


def get_cached_groups() -> Optional[Dict[str, Any]]:
    """
    Get all groups from Redis cache.
    
    Returns:
        Cached groups data as dict if found, None otherwise
    """
    try:
        client = get_redis_client()
        if not client:
            return None
        
        cached = client.get(CACHE_KEY_GROUPS)
        if cached:
            logger.info("✅ Cache HIT: groups:all")
            return json.loads(cached)
        else:
            logger.info("❌ Cache MISS: groups:all")
    except Exception as e:
        logger.warning(f"Cache retrieval error for groups:all: {e}")
    
    return None


def set_cached_groups(data: Dict[str, Any], ttl: Optional[int] = None) -> bool:
    """
    Store groups data in Redis cache.
    
    Args:
        data: The groups data to cache (will be JSON serialized)
        ttl: Time-to-live in seconds (default: CACHE_TTL_GROUPS_TEAMS)
    
    Returns:
        True if cache was successfully set, False otherwise
    """
    try:
        client = get_redis_client()
        if not client:
            return False
        
        ttl = ttl or config.CACHE_TTL_GROUPS_TEAMS
        client.setex(CACHE_KEY_GROUPS, ttl, json.dumps(data, default=str))
        return True
    except Exception as e:
        logger.warning(f"Cache set error for groups:all: {e}")
        return False


def get_cached_teams() -> Optional[Dict[str, Any]]:
    """
    Get all teams from Redis cache.
    
    Returns:
        Cached teams data as dict if found, None otherwise
    """
    try:
        client = get_redis_client()
        if not client:
            return None
        
        cached = client.get(CACHE_KEY_TEAMS)
        if cached:
            logger.info("✅ Cache HIT: teams:all")
            return json.loads(cached)
        else:
            logger.info("❌ Cache MISS: teams:all")
    except Exception as e:
        logger.warning(f"Cache retrieval error for teams:all: {e}")
    
    return None


def set_cached_teams(data: Dict[str, Any], ttl: Optional[int] = None) -> bool:
    """
    Store teams data in Redis cache.
    
    Args:
        data: The teams data to cache (will be JSON serialized)
        ttl: Time-to-live in seconds (default: CACHE_TTL_GROUPS_TEAMS)
    
    Returns:
        True if cache was successfully set, False otherwise
    """
    try:
        client = get_redis_client()
        if not client:
            return False
        
        ttl = ttl or config.CACHE_TTL_GROUPS_TEAMS
        client.setex(CACHE_KEY_TEAMS, ttl, json.dumps(data, default=str))
        return True
    except Exception as e:
        logger.warning(f"Cache set error for teams:all: {e}")
        return False


def populate_groups_teams_cache(conn: Connection) -> Tuple[bool, int, int]:
    """
    Populate both groups and teams cache from database.
    Helper function to avoid code duplication.
    
    Args:
        conn: Database connection
    
    Returns:
        Tuple of (success: bool, groups_count: int, teams_count: int)
    """
    try:
        groups = load_all_groups_from_db(conn)
        teams = load_all_teams_from_db(conn)
        groups_success = set_cached_groups({"groups": groups, "count": len(groups)})
        teams_success = set_cached_teams({"teams": teams, "count": len(teams)})
        return (groups_success and teams_success, len(groups), len(teams))
    except Exception as e:
        logger.warning(f"Error populating groups/teams cache: {e}")
        return (False, 0, 0)


def invalidate_groups_teams_cache() -> int:
    """
    Invalidate all groups and teams cache.
    
    Returns:
        Number of cache entries deleted
    """
    try:
        client = get_redis_client()
        if not client:
            return 0
        
        keys = []
        if client.exists(CACHE_KEY_GROUPS):
            keys.append(CACHE_KEY_GROUPS)
        if client.exists(CACHE_KEY_TEAMS):
            keys.append(CACHE_KEY_TEAMS)
        
        if keys:
            deleted = client.delete(*keys)
            logger.info(f"Invalidated {deleted} groups/teams cache entries")
            return deleted
        else:
            return 0
    except Exception as e:
        logger.warning(f"Cache invalidation error: {e}")
    
    return 0


# ==================== Helper Functions for Loading from DB ====================

def load_all_groups_from_db(conn: Connection) -> List[Dict[str, Any]]:
    """
    Load all groups from database.
    Helper function for services to use when cache misses.
    
    Returns:
        List of groups with id, name, parent_id, ai_insight
    """
    query = text("""
        SELECT group_key, group_name, parent_group_key, ai_insight
        FROM public.groups
        ORDER BY group_name
    """)
    result = conn.execute(query)
    groups = []
    for row in result:
        groups.append({
            "id": row[0],
            "name": row[1],
            "parent_id": row[2],
            "ai_insight": row[3]
        })
    return groups


def load_all_teams_from_db(conn: Connection) -> List[Dict[str, Any]]:
    """
    Load all teams from database with their group associations.
    Helper function for services to use when cache misses.
    
    Returns:
        List of teams with team_key, team_name, number_of_team_members, 
        ai_insight, group_keys, group_names
    """
    query = text("""
        SELECT t.team_key, t.team_name, t.number_of_team_members, t.ai_insight,
               array_agg(tg.group_id) as group_keys,
               array_agg(g.group_name) as group_names
        FROM public.teams t
        LEFT JOIN public.team_groups tg ON t.team_key = tg.team_id
        LEFT JOIN public.groups g ON tg.group_id = g.group_key
        GROUP BY t.team_key, t.team_name, t.number_of_team_members, t.ai_insight
        ORDER BY t.team_name
    """)
    result = conn.execute(query)
    teams = []
    for row in result:
        teams.append({
            "team_key": row[0],
            "team_name": row[1],
            "number_of_team_members": row[2],
            "ai_insight": row[3],
            "group_keys": [k for k in (row[4] or []) if k is not None],
            "group_names": [n for n in (row[5] or []) if n is not None]
        })
    return teams


def load_teams_in_group_from_db(group_id: int, conn: Connection, include_children: bool = False) -> List[Dict[str, Any]]:
    """
    Load teams in a specific group from database.
    Helper function for services to use when cache misses.
    
    Args:
        group_id: Group key (ID)
        conn: Database connection
        include_children: If True, includes teams from descendant groups (recursive)
    
    Returns:
        List of team dictionaries
    """
    if include_children:
        query = text("""
            WITH RECURSIVE group_tree AS (
                SELECT group_key FROM public.groups WHERE group_key = :group_id
                UNION ALL
                SELECT g.group_key FROM public.groups g
                INNER JOIN group_tree gt ON g.parent_group_key = gt.group_key
            )
            SELECT DISTINCT t.team_key, t.team_name, t.number_of_team_members
            FROM public.teams t
            INNER JOIN public.team_groups tg ON t.team_key = tg.team_id
            WHERE tg.group_id IN (SELECT group_key FROM group_tree)
            ORDER BY t.team_name
        """)
    else:
        query = text("""
            SELECT t.team_key, t.team_name, t.number_of_team_members
            FROM public.teams t
            INNER JOIN public.team_groups tg ON t.team_key = tg.team_id
            WHERE tg.group_id = :group_id
            ORDER BY t.team_name
        """)
    
    result = conn.execute(query, {"group_id": group_id})
    teams = []
    for row in result:
        teams.append({
            "team_key": row[0],
            "team_name": row[1],
            "number_of_team_members": row[2],
            "group_key": group_id
        })
    return teams


def load_team_names_from_db(conn: Connection) -> List[str]:
    """
    Load all team names from database.
    Helper function for services to use when cache misses.
    
    Returns:
        List of team names (sorted)
    """
    query = text("SELECT team_name FROM public.teams ORDER BY team_name")
    result = conn.execute(query)
    return [row[0] for row in result]


def load_teams_for_group_from_db(group_name: str, conn: Connection, include_children: bool = True) -> List[str]:
    """
    Load team names for a group from database.
    Helper function for services to use when cache misses.
    
    Args:
        group_name: Group name
        conn: Database connection
        include_children: If True, includes teams from descendant groups (recursive)
    
    Returns:
        List of team names
    """
    # First get group_key
    query = text("SELECT group_key FROM public.groups WHERE group_name = :group_name")
    result = conn.execute(query, {"group_name": group_name})
    row = result.fetchone()
    if not row:
        return []
    
    group_key = row[0]
    
    if include_children:
        query = text("""
            WITH RECURSIVE group_tree AS (
                SELECT group_key FROM public.groups WHERE group_key = :group_key
                UNION ALL
                SELECT g.group_key FROM public.groups g
                INNER JOIN group_tree gt ON g.parent_group_key = gt.group_key
            )
            SELECT DISTINCT t.team_name
            FROM public.teams t
            INNER JOIN public.team_groups tg ON t.team_key = tg.team_id
            WHERE tg.group_id IN (SELECT group_key FROM group_tree)
            ORDER BY t.team_name
        """)
    else:
        query = text("""
            SELECT t.team_name
            FROM public.teams t
            INNER JOIN public.team_groups tg ON t.team_key = tg.team_id
            WHERE tg.group_id = :group_key
            ORDER BY t.team_name
        """)
    
    result = conn.execute(query, {"group_key": group_key})
    return [row[0] for row in result]


def group_exists_in_db(group_key: int, conn: Connection) -> bool:
    """Check if group exists in database."""
    query = text("SELECT 1 FROM public.groups WHERE group_key = :group_key LIMIT 1")
    result = conn.execute(query, {"group_key": group_key})
    return result.fetchone() is not None


def group_exists_by_name_in_db(group_name: str, conn: Connection) -> bool:
    """Check if group exists in database by name."""
    query = text("SELECT 1 FROM public.groups WHERE group_name = :group_name LIMIT 1")
    result = conn.execute(query, {"group_name": group_name})
    return result.fetchone() is not None


def team_exists_in_db(team_key: int, conn: Connection) -> bool:
    """Check if team exists in database."""
    query = text("SELECT 1 FROM public.teams WHERE team_key = :team_key LIMIT 1")
    result = conn.execute(query, {"team_key": team_key})
    return result.fetchone() is not None


def team_exists_by_name_in_db(team_name: str, conn: Connection) -> bool:
    """Check if team exists in database by name."""
    query = text("SELECT 1 FROM public.teams WHERE team_name = :team_name LIMIT 1")
    result = conn.execute(query, {"team_name": team_name})
    return result.fetchone() is not None


def get_team_by_id_from_db(team_key: int, conn: Connection) -> Optional[Dict[str, Any]]:
    """Get team data by team_key from database."""
    query = text("""
        SELECT team_key, team_name, number_of_team_members, ai_insight
        FROM public.teams
        WHERE team_key = :team_key
    """)
    result = conn.execute(query, {"team_key": team_key})
    row = result.fetchone()
    if row:
        return {
            "team_key": row[0],
            "team_name": row[1],
            "number_of_team_members": row[2],
            "ai_insight": row[3]
        }
    return None


def _build_recursive_teams_from_cache(
    group_id: int,
    cached_groups: Dict[str, Any],
    cached_teams: Dict[str, Any]
) -> List[str]:
    """
    Build recursive team list for a group from cache data.
    Includes teams from all descendant groups.
    
    Args:
        group_id: Target group ID
        cached_groups: Cached groups data
        cached_teams: Cached teams data
    
    Returns:
        List of team names
    """
    groups = cached_groups.get("groups", [])
    teams = cached_teams.get("teams", [])
    
    # Step 1: Build set of all descendant group IDs (including self)
    descendant_ids = {group_id}
    visited = set()  # Prevent circular references
    
    def find_children(parent_id: int):
        """Recursively find all child groups"""
        if parent_id in visited:
            return  # Prevent infinite loops
        visited.add(parent_id)
        
        for group in groups:
            if group.get("parent_id") == parent_id:
                child_id = group.get("id")
                if child_id:
                    descendant_ids.add(child_id)
                    find_children(child_id)
    
    find_children(group_id)
    
    # Step 2: Get all teams that belong to any descendant group
    team_names = set()
    for team in teams:
        team_group_keys = team.get("group_keys", [])
        if any(gid in descendant_ids for gid in team_group_keys):
            team_names.add(team.get("team_name"))
    
    return sorted(list(team_names))


def get_recursive_teams_for_group_from_cache(
    group_name: str,
    conn: Connection,
    include_children: bool = True
) -> List[str]:
    """
    Get team names for a group from cache (with recursive support).
    Falls back to DB if cache unavailable.
    
    Args:
        group_name: Group name
        conn: Database connection (for fallback)
        include_children: If True, includes teams from descendant groups (recursive)
    
    Returns:
        List of team names
    """
    # Try cache first
    cached_groups = get_cached_groups()
    cached_teams = get_cached_teams()
    
    if cached_groups and cached_teams:
        # Find group by name
        groups = cached_groups.get("groups", [])
        group_data = next((g for g in groups if g.get("name") == group_name), None)
        
        if group_data:
            group_id = group_data.get("id")
            
            if include_children:
                # Build recursive teams from cache
                return _build_recursive_teams_from_cache(group_id, cached_groups, cached_teams)
            else:
                # Direct teams only - filter from cache
                teams = cached_teams.get("teams", [])
                team_names = [
                    t.get("team_name") for t in teams
                    if group_id in t.get("group_keys", [])
                ]
                return sorted(team_names)
    
    # Fallback to DB
    return load_teams_for_group_from_db(group_name, conn, include_children)
