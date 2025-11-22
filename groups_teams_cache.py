"""
Groups and Teams Cache - In-memory cache for groups and teams data.

This module provides caching for groups and teams to avoid repeated database queries.
The cache is loaded on startup and refreshed after any mutations (create/update/delete).

Cache Structure:
- groups_by_id: Dict[int, Dict] - group_key -> group data
- groups_by_name: Dict[str, Dict] - group_name -> group data
- teams_by_id: Dict[int, Dict] - team_key -> team data
- teams_by_name: Dict[str, Dict] - team_name -> team data
- group_children: Dict[int, List[int]] - parent_key -> [child_keys]
- group_teams_direct: Dict[int, List[str]] - group_key -> [team_names] (direct only)
- group_teams_recursive: Dict[int, List[str]] - group_key -> [team_names] (includes descendants)
"""

import threading
from typing import Dict, List, Optional, Any
from sqlalchemy import text
from sqlalchemy.engine import Connection
import logging

logger = logging.getLogger(__name__)

# Cache data structures
_groups_by_id: Dict[int, Dict[str, Any]] = {}
_groups_by_name: Dict[str, Dict[str, Any]] = {}
_teams_by_id: Dict[int, Dict[str, Any]] = {}
_teams_by_name: Dict[str, Dict[str, Any]] = {}
_group_children: Dict[int, List[int]] = {}  # parent_key -> [child_keys]
_group_teams_direct: Dict[int, List[str]] = {}  # group_key -> [team_names] (direct)
_group_teams_recursive: Dict[int, List[str]] = {}  # group_key -> [team_names] (recursive)

# Thread safety
_cache_lock = threading.Lock()
_cache_loaded = False


def _build_recursive_teams(group_key: int) -> List[str]:
    """
    Build recursive team list for a group (includes teams from all descendant groups).
    Uses DFS traversal.
    """
    teams = set(_group_teams_direct.get(group_key, []))
    
    # Recursively add teams from child groups
    for child_key in _group_children.get(group_key, []):
        child_teams = _build_recursive_teams(child_key)
        teams.update(child_teams)
    
    return sorted(list(teams))


def load_groups_teams_cache(conn: Connection) -> None:
    """
    Load all groups and teams from database into cache.
    Builds hierarchical tree structure and team-group relationships.
    
    Args:
        conn: Database connection
    """
    global _groups_by_id, _groups_by_name, _teams_by_id, _teams_by_name
    global _group_children, _group_teams_direct, _group_teams_recursive, _cache_loaded
    
    with _cache_lock:
        try:
            logger.info("Loading groups and teams cache from database...")
            
            # Clear existing cache
            _groups_by_id.clear()
            _groups_by_name.clear()
            _teams_by_id.clear()
            _teams_by_name.clear()
            _group_children.clear()
            _group_teams_direct.clear()
            _group_teams_recursive.clear()
            
            # Load all groups
            groups_query = text("""
                SELECT group_key, group_name, parent_group_key
                FROM public.groups
                ORDER BY group_name
            """)
            groups_result = conn.execute(groups_query)
            
            for row in groups_result:
                group_key, group_name, parent_key = row[0], row[1], row[2]
                group_data = {
                    "group_key": group_key,
                    "group_name": group_name,
                    "parent_group_key": parent_key
                }
                _groups_by_id[group_key] = group_data
                _groups_by_name[group_name] = group_data
                
                # Build parent-child relationships
                if parent_key is not None:
                    if parent_key not in _group_children:
                        _group_children[parent_key] = []
                    _group_children[parent_key].append(group_key)
            
            # Load all teams
            teams_query = text("""
                SELECT team_key, team_name, number_of_team_members, ai_insight
                FROM public.teams
                ORDER BY team_name
            """)
            teams_result = conn.execute(teams_query)
            
            for row in teams_result:
                team_key, team_name, members, ai_insight = row[0], row[1], row[2], row[3]
                team_data = {
                    "team_key": team_key,
                    "team_name": team_name,
                    "number_of_team_members": members,
                    "ai_insight": ai_insight
                }
                _teams_by_id[team_key] = team_data
                _teams_by_name[team_name] = team_data
            
            # Load team-group relationships
            team_groups_query = text("""
                SELECT team_id, group_id
                FROM public.team_groups
            """)
            team_groups_result = conn.execute(team_groups_query)
            
            for row in team_groups_result:
                team_id, group_id = row[0], row[1]
                team = _teams_by_id.get(team_id)
                if team:
                    team_name = team["team_name"]
                    if group_id not in _group_teams_direct:
                        _group_teams_direct[group_id] = []
                    _group_teams_direct[group_id].append(team_name)
            
            # Build recursive team lists (includes teams from descendant groups)
            for group_key in _groups_by_id.keys():
                _group_teams_recursive[group_key] = _build_recursive_teams(group_key)
            
            _cache_loaded = True
            logger.info(f"✅ Groups/Teams cache loaded: {len(_groups_by_id)} groups, {len(_teams_by_id)} teams")
            
        except Exception as e:
            logger.error(f"❌ Failed to load groups/teams cache: {e}")
            _cache_loaded = False
            raise


def refresh_groups_teams_cache(conn: Connection) -> None:
    """
    Refresh the groups and teams cache by reloading from database.
    Same as load_groups_teams_cache but with explicit refresh naming.
    
    Args:
        conn: Database connection
    """
    load_groups_teams_cache(conn)


def is_groups_teams_cache_loaded() -> bool:
    """
    Check if the groups/teams cache has been loaded.
    
    Returns:
        True if cache is loaded, False otherwise
    """
    return _cache_loaded


# ==================== Read Functions ====================

def get_all_groups_from_cache() -> List[Dict[str, Any]]:
    """
    Get all groups from cache.
    
    Returns:
        List of group dictionaries with id, name, parent_id
    """
    if not _cache_loaded:
        raise RuntimeError("Groups/Teams cache not loaded. Call load_groups_teams_cache() first.")
    
    groups = []
    for group in _groups_by_id.values():
        groups.append({
            "id": group["group_key"],
            "name": group["group_name"],
            "parent_id": group["parent_group_key"]
        })
    return sorted(groups, key=lambda x: x["name"])


def get_teams_in_group_from_cache(group_id: int, include_children: bool = False) -> List[Dict[str, Any]]:
    """
    Get teams in a specific group from cache.
    
    Args:
        group_id: Group key (ID)
        include_children: If True, includes teams from descendant groups (recursive)
    
    Returns:
        List of team dictionaries
    """
    if not _cache_loaded:
        raise RuntimeError("Groups/Teams cache not loaded. Call load_groups_teams_cache() first.")
    
    if group_id not in _groups_by_id:
        return []
    
    # Get team names (direct or recursive)
    if include_children:
        team_names = _group_teams_recursive.get(group_id, [])
    else:
        team_names = _group_teams_direct.get(group_id, [])
    
    # Convert team names to full team dictionaries
    teams = []
    for team_name in team_names:
        team = _teams_by_name.get(team_name)
        if team:
            teams.append({
                "team_key": team["team_key"],
                "team_name": team["team_name"],
                "number_of_team_members": team["number_of_team_members"],
                "group_key": group_id
            })
    
    return sorted(teams, key=lambda x: x["team_name"])


def get_all_teams_from_cache(
    group_key: Optional[int] = None,
    search: Optional[str] = None,
    ai_insight: Optional[bool] = None,
    limit: int = 200
) -> List[Dict[str, Any]]:
    """
    Get all teams from cache with optional filtering.
    
    Args:
        group_key: Optional filter by group
        search: Optional search term for team names
        ai_insight: Optional filter by AI insight flag
        limit: Maximum number of results
    
    Returns:
        List of team dictionaries with group_keys and group_names arrays
    """
    if not _cache_loaded:
        raise RuntimeError("Groups/Teams cache not loaded. Call load_groups_teams_cache() first.")
    
    teams = []
    for team in _teams_by_id.values():
        # Get all groups this team belongs to
        team_group_keys = []
        team_group_names = []
        for group_id, team_names in _group_teams_direct.items():
            if team["team_name"] in team_names:
                team_group_keys.append(group_id)
                group = _groups_by_id.get(group_id)
                if group:
                    team_group_names.append(group["group_name"])
        
        team_dict = {
            "team_key": team["team_key"],
            "team_name": team["team_name"],
            "number_of_team_members": team["number_of_team_members"],
            "group_keys": team_group_keys,
            "group_names": team_group_names,
            "ai_insight": team["ai_insight"]
        }
        
        # Apply filters
        if group_key is not None and group_key not in team_group_keys:
            continue
        if search and search.lower() not in team["team_name"].lower():
            continue
        if ai_insight is not None and team["ai_insight"] != ai_insight:
            continue
        
        teams.append(team_dict)
    
    # Sort and limit
    teams.sort(key=lambda x: x["team_name"])
    return teams[:limit]


def get_team_names_from_cache() -> List[str]:
    """
    Get all team names from cache.
    
    Returns:
        List of team names (sorted)
    """
    if not _cache_loaded:
        raise RuntimeError("Groups/Teams cache not loaded. Call load_groups_teams_cache() first.")
    
    return sorted(_teams_by_name.keys())


def get_teams_for_group_from_cache(group_name: str, include_children: bool = True) -> List[str]:
    """
    Get team names for a group (used by resolve_team_names_from_filter).
    
    Args:
        group_name: Group name
        include_children: If True, includes teams from descendant groups (recursive)
    
    Returns:
        List of team names
    """
    if not _cache_loaded:
        raise RuntimeError("Groups/Teams cache not loaded. Call load_groups_teams_cache() first.")
    
    group = _groups_by_name.get(group_name)
    if not group:
        return []
    
    group_key = group["group_key"]
    if include_children:
        return _group_teams_recursive.get(group_key, [])
    else:
        return _group_teams_direct.get(group_key, [])


# ==================== Validation Functions ====================

def group_exists_in_cache(group_key: int) -> bool:
    """
    Check if a group exists in cache by group_key.
    
    Args:
        group_key: Group key (ID)
    
    Returns:
        True if group exists, False otherwise
    """
    if not _cache_loaded:
        raise RuntimeError("Groups/Teams cache not loaded. Call load_groups_teams_cache() first.")
    
    return group_key in _groups_by_id


def group_exists_by_name_in_cache(group_name: str) -> bool:
    """
    Check if a group exists in cache by group_name.
    
    Args:
        group_name: Group name
    
    Returns:
        True if group exists, False otherwise
    """
    if not _cache_loaded:
        raise RuntimeError("Groups/Teams cache not loaded. Call load_groups_teams_cache() first.")
    
    return group_name in _groups_by_name


def team_exists_in_cache(team_key: int) -> bool:
    """
    Check if a team exists in cache by team_key.
    
    Args:
        team_key: Team key (ID)
    
    Returns:
        True if team exists, False otherwise
    """
    if not _cache_loaded:
        raise RuntimeError("Groups/Teams cache not loaded. Call load_groups_teams_cache() first.")
    
    return team_key in _teams_by_id


def team_exists_by_name_in_cache(team_name: str) -> bool:
    """
    Check if a team exists in cache by team_name.
    
    Args:
        team_name: Team name
    
    Returns:
        True if team exists, False otherwise
    """
    if not _cache_loaded:
        raise RuntimeError("Groups/Teams cache not loaded. Call load_groups_teams_cache() first.")
    
    return team_name in _teams_by_name


def get_team_by_id_from_cache(team_key: int) -> Optional[Dict[str, Any]]:
    """
    Get team data by team_key from cache.
    
    Args:
        team_key: Team key (ID)
    
    Returns:
        Team dictionary or None if not found
    """
    if not _cache_loaded:
        raise RuntimeError("Groups/Teams cache not loaded. Call load_groups_teams_cache() first.")
    
    return _teams_by_id.get(team_key)

