"""
Database CRUD functions for goals table.
Adapted from pi_goals logic in database_general.py.
Supports PI, Sprint, and Release goals.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
import json
import logging
import config

logger = logging.getLogger(__name__)


def determine_goal_type(team_name: Optional[str], group_id: Optional[int], is_overall: bool = False) -> str:
    """
    Determine goal_type based on team_name and group_id.
    Same logic as pi_goals - just uses group_id instead of group_name.
    
    Args:
        team_name: Optional team name
        group_id: Optional group ID
        is_overall: If True, this is an overall goal (not team-specific)
        
    Returns:
        'team', 'group', or 'overall'
    """
    if is_overall:
        # For overall goals: if group_id provided, it's 'group', otherwise 'overall'
        if group_id:
            return 'group'
        else:
            return 'overall'
    else:
        # For team goals: always 'team' (even if group_id also exists)
        if team_name:
            return 'team'
        else:
            return 'overall'


def _get_next_goal_number(
    scope_type: str,
    pi_name: Optional[str],
    sprint_id: Optional[int],
    release_id: Optional[int],
    goal_type: str,
    team_name: Optional[str],
    group_id: Optional[int],
    ai: bool,
    conn: Connection
) -> int:
    """
    Get the next available goal_number for a given combination.
    Adapted from pi_goals - now supports scope_type and context fields.
    
    Args:
        scope_type: 'pi', 'sprint', or 'release'
        pi_name: PI name (if scope_type='pi')
        sprint_id: Sprint ID (if scope_type='sprint')
        release_id: Release ID (if scope_type='release')
        goal_type: Goal type ('overall', 'team', 'group')
        team_name: Optional team name
        group_id: Optional group ID
        ai: AI flag
        conn: Database connection
        
    Returns:
        Next available goal_number (1 if no goals exist, max + 1 otherwise)
    """
    where_conditions = [
        "scope_type = :scope_type",
        "goal_type = :goal_type",
        "COALESCE(team_name, '') = COALESCE(:team_name, '')",
        "((group_id IS NULL AND :group_id IS NULL) OR (group_id = :group_id))",
        "ai = :ai"
    ]
    
    params = {
        "scope_type": scope_type,
        "goal_type": goal_type,
        "team_name": team_name,
        "group_id": group_id,
        "ai": ai
    }
    
    # Add context field based on scope_type
    if scope_type == 'pi':
        where_conditions.append("COALESCE(pi_name, '') = COALESCE(:pi_name, '')")
        params["pi_name"] = pi_name
    elif scope_type == 'sprint':
        where_conditions.append("((sprint_id IS NULL AND :sprint_id IS NULL) OR (sprint_id = :sprint_id))")
        params["sprint_id"] = sprint_id
    elif scope_type == 'release':
        where_conditions.append("((release_id IS NULL AND :release_id IS NULL) OR (release_id = :release_id))")
        params["release_id"] = release_id
    
    where_clause = " AND ".join(where_conditions)
    
    query = text(f"""
        SELECT COALESCE(MAX(goal_number), 0) + 1
        FROM {config.GOALS_TABLE}
        WHERE {where_clause}
    """)
    
    result = conn.execute(query, params)
    next_number = result.scalar()
    return next_number if next_number else 1


def _prepare_goal_data_for_db(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Prepare goal data for database insertion/update.
    Determines goal_type and converts issue_keys to JSON string.
    Same logic as pi_goals - adapted for new structure.
    
    Args:
        data: Dictionary with goal data
        
    Returns:
        Dictionary ready for database insertion with goal_type and converted issue_keys
    """
    team_name = data.get("team_name")
    group_id = data.get("group_id")
    is_overall = data.get("is_overall", False)
    
    # Determine goal_type automatically
    goal_type = determine_goal_type(team_name, group_id, is_overall)
    
    # Log for debugging group goals
    if is_overall and group_id:
        logger.info(f"_prepare_goal_data_for_db: is_overall=True, group_id={group_id}, determined goal_type={goal_type}")
    
    # Prepare data
    goal_data = {
        "scope_type": data.get("scope_type", "pi"),
        "pi_name": data.get("pi_name"),
        "sprint_id": data.get("sprint_id"),
        "release_id": data.get("release_id"),
        "goal_type": goal_type,
        "team_name": team_name,
        "group_id": group_id,
        "goal_text": data.get("goal_text"),
        "issue_keys": json.dumps(data.get("issue_keys", [])) if data.get("issue_keys") is not None else None,
        "status": data.get("status", "Draft"),
        "priority_bv": data.get("priority_bv"),
        "ai": data.get("ai", False)
    }
    
    # goal_number is set separately (auto-assigned or from data)
    if "goal_number" in data:
        goal_data["goal_number"] = data.get("goal_number")
    
    return goal_data


def create_goal(data: Dict[str, Any], conn: Connection = None) -> Dict[str, Any]:
    """
    Insert a new goal and return the created row.
    Same logic as create_pi_goal - adapted for new structure.
    """
    try:
        # Prepare goal data using helper function
        goal_data = _prepare_goal_data_for_db(data)
        
        # Auto-assign goal_number if not provided
        if "goal_number" not in goal_data:
            scope_type = goal_data.get("scope_type")
            goal_type = goal_data.get("goal_type")
            team_name = goal_data.get("team_name")
            group_id = goal_data.get("group_id")
            ai = goal_data.get("ai", False)
            pi_name = goal_data.get("pi_name")
            sprint_id = goal_data.get("sprint_id")
            release_id = goal_data.get("release_id")
            
            goal_data["goal_number"] = _get_next_goal_number(
                scope_type=scope_type,
                pi_name=pi_name,
                sprint_id=sprint_id,
                release_id=release_id,
                goal_type=goal_type,
                team_name=team_name,
                group_id=group_id,
                ai=ai,
                conn=conn
            )
        
        columns_sql = ", ".join(goal_data.keys())
        values_sql = ", ".join([f":{k}" for k in goal_data.keys()])
        
        query = text(f"""
            INSERT INTO {config.GOALS_TABLE} ({columns_sql})
            VALUES ({values_sql})
            RETURNING *
        """)
        
        result = conn.execute(query, goal_data)
        row = result.fetchone()
        conn.commit()
        
        # Convert issue_keys back to list for response
        result_dict = dict(row._mapping)
        issue_keys_value = result_dict.get("issue_keys")
        if issue_keys_value is not None:
            if isinstance(issue_keys_value, str):
                result_dict["issue_keys"] = json.loads(issue_keys_value)
            else:
                result_dict["issue_keys"] = issue_keys_value
        else:
            result_dict["issue_keys"] = []
        
        return result_dict
    except Exception as e:
        logger.error(f"Error creating goal: {e}")
        conn.rollback()
        raise e


def upsert_goal(data: Dict[str, Any], conn: Connection = None) -> Dict[str, Any]:
    """
    Insert or update a goal (UPSERT) and return the created/updated row.
    Used for AI-generated goals. Same logic as upsert_pi_goal.
    """
    try:
        # Set default status and ai for AI-generated goals if not provided
        if "status" not in data:
            data["status"] = "Draft-AI"
        if "ai" not in data:
            data["ai"] = True
        
        # goal_number must be provided for upsert
        if "goal_number" not in data:
            raise ValueError("goal_number is required for upsert_goal")
        
        # Prepare goal data using helper function
        goal_data = _prepare_goal_data_for_db(data)
        
        # Get values for the check query
        scope_type = goal_data.get("scope_type")
        goal_type = goal_data.get("goal_type")
        team_name = goal_data.get("team_name")
        group_id = goal_data.get("group_id")
        ai = goal_data.get("ai")
        goal_number = goal_data.get("goal_number")
        pi_name = goal_data.get("pi_name")
        sprint_id = goal_data.get("sprint_id")
        release_id = goal_data.get("release_id")
        
        logger.info(f"[UPSERT] Checking for existing goal: scope_type={scope_type}, goal_type={goal_type}, ai={ai}, goal_number={goal_number}, pi_name={pi_name}, team_name={team_name}, group_id={group_id}")
        
        # Build check query based on scope_type
        where_conditions = [
            "scope_type = :scope_type",
            "goal_type = :goal_type",
            "COALESCE(team_name, '') = COALESCE(:team_name, '')",
            "((group_id IS NULL AND :group_id IS NULL) OR (group_id = :group_id))",
            "ai = :ai",
            "goal_number = :goal_number"
        ]
        
        check_params = {
            "scope_type": scope_type,
            "goal_type": goal_type,
            "team_name": team_name,
            "group_id": group_id,
            "ai": ai,
            "goal_number": goal_number
        }
        
        if scope_type == 'pi':
            where_conditions.append("COALESCE(pi_name, '') = COALESCE(:pi_name, '')")
            check_params["pi_name"] = pi_name
        elif scope_type == 'sprint':
            where_conditions.append("((sprint_id IS NULL AND :sprint_id IS NULL) OR (sprint_id = :sprint_id))")
            check_params["sprint_id"] = sprint_id
        elif scope_type == 'release':
            where_conditions.append("((release_id IS NULL AND :release_id IS NULL) OR (release_id = :release_id))")
            check_params["release_id"] = release_id
        
        where_clause = " AND ".join(where_conditions)
        
        check_query = text(f"""
            SELECT id FROM {config.GOALS_TABLE}
            WHERE {where_clause}
            LIMIT 1
        """)
        
        logger.info(f"[UPSERT] Executing check query with params: {check_params}")
        existing = conn.execute(check_query, check_params).fetchone()
        
        if existing:
            # Update existing goal
            goal_id = existing[0]
            logger.info(f"[UPSERT] Found existing goal ID={goal_id}, updating...")
            update_fields = ["goal_text", "issue_keys", "status", "priority_bv", "goal_number", "updated_at"]
            set_clauses = ", ".join([f"{k} = :{k}" if k != "updated_at" else f"{k} = CURRENT_TIMESTAMP" for k in update_fields])
            
            update_params = {k: goal_data.get(k) for k in update_fields if k != "updated_at"}
            update_params["id"] = goal_id
            
            update_query = text(f"""
                UPDATE {config.GOALS_TABLE}
                SET {set_clauses}
                WHERE id = :id
                RETURNING *
            """)
            
            result = conn.execute(update_query, update_params)
            row = result.fetchone()
            logger.info(f"[UPSERT] Successfully updated goal ID={goal_id}")
        else:
            # Insert new goal
            logger.info(f"[UPSERT] No existing goal found, inserting new goal...")
            columns_sql = ", ".join(goal_data.keys())
            values_sql = ", ".join([f":{k}" for k in goal_data.keys()])
            
            insert_query = text(f"""
                INSERT INTO {config.GOALS_TABLE} ({columns_sql})
                VALUES ({values_sql})
                RETURNING *
            """)
            
            logger.info(f"[UPSERT] Insert query params: {goal_data}")
            result = conn.execute(insert_query, goal_data)
            row = result.fetchone()
            if row:
                result_dict = dict(row._mapping)
                logger.info(f"[UPSERT] Successfully inserted new goal ID={result_dict.get('id')}")
            else:
                logger.error("❌ [UPSERT] INSERT executed but no row returned!")
                raise Exception("INSERT executed but no row returned")
        
        conn.commit()
        
        # Convert issue_keys back to list for response
        result_dict = dict(row._mapping)
        issue_keys_value = result_dict.get("issue_keys")
        if issue_keys_value is not None:
            if isinstance(issue_keys_value, str):
                result_dict["issue_keys"] = json.loads(issue_keys_value)
            else:
                result_dict["issue_keys"] = issue_keys_value
        else:
            result_dict["issue_keys"] = []
        
        return result_dict
    except Exception as e:
        logger.error(f"❌ [UPSERT ERROR] Error upserting goal: {e}", exc_info=True)
        logger.error(f"❌ [UPSERT ERROR] Goal data that failed: {data}")
        if conn:
            conn.rollback()
        raise e


def get_goals_filtered(
    scope_type: Optional[str] = None,
    pi_name: Optional[str] = None,
    sprint_id: Optional[int] = None,
    release_id: Optional[int] = None,
    goal_type: Optional[str] = None,
    team_name: Optional[str] = None,
    team_names_list: Optional[List[str]] = None,
    group_id: Optional[int] = None,
    status: Optional[str] = None,
    ai: Optional[bool] = None,
    limit: int = 100,
    conn: Connection = None
) -> List[Dict[str, Any]]:
    """
    Get goals with optional filters.
    Same logic as get_pi_goals_filtered - adapted for new structure.
    """
    try:
        where_conditions = []
        params = {"limit": limit}
        
        if scope_type:
            where_conditions.append("scope_type = :scope_type")
            params["scope_type"] = scope_type
        
        if pi_name:
            where_conditions.append("pi_name = :pi_name")
            params["pi_name"] = pi_name
        
        if sprint_id is not None:
            where_conditions.append("sprint_id = :sprint_id")
            params["sprint_id"] = sprint_id
        
        if release_id is not None:
            where_conditions.append("release_id = :release_id")
            params["release_id"] = release_id
        
        if goal_type:
            where_conditions.append("goal_type = :goal_type")
            params["goal_type"] = goal_type
        
        if team_names_list:
            placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names_list))])
            where_conditions.append(f"team_name IN ({placeholders})")
            for i, name in enumerate(team_names_list):
                params[f"team_name_{i}"] = name
        elif team_name:
            where_conditions.append("team_name = :team_name")
            params["team_name"] = team_name
        
        if group_id is not None:
            where_conditions.append("group_id = :group_id")
            params["group_id"] = group_id
        
        if status:
            where_conditions.append("status = :status")
            params["status"] = status
        
        if ai is not None:
            where_conditions.append("ai = :ai")
            params["ai"] = ai
        
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        query = text(f"""
            SELECT *
            FROM {config.GOALS_TABLE}
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT :limit
        """)
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        goals = []
        for row in rows:
            goal_dict = dict(row._mapping)
            # Convert issue_keys JSON to list
            issue_keys_value = goal_dict.get("issue_keys")
            if issue_keys_value is not None:
                if isinstance(issue_keys_value, str):
                    goal_dict["issue_keys"] = json.loads(issue_keys_value)
                else:
                    goal_dict["issue_keys"] = issue_keys_value
            else:
                goal_dict["issue_keys"] = []
            goals.append(goal_dict)
        
        return goals
    except Exception as e:
        logger.error(f"Error fetching goals: {e}")
        raise e


def get_goal_by_id(goal_id: int, conn: Connection = None) -> Optional[Dict[str, Any]]:
    """Get a single goal by ID."""
    try:
        query = text(f"""
            SELECT *
            FROM {config.GOALS_TABLE}
            WHERE id = :id
        """)
        
        result = conn.execute(query, {"id": goal_id})
        row = result.fetchone()
        
        if not row:
            return None
        
        goal_dict = dict(row._mapping)
        # Convert issue_keys JSON to list
        issue_keys_value = goal_dict.get("issue_keys")
        if issue_keys_value is not None:
            if isinstance(issue_keys_value, str):
                goal_dict["issue_keys"] = json.loads(issue_keys_value)
            else:
                goal_dict["issue_keys"] = issue_keys_value
        else:
            goal_dict["issue_keys"] = []
        
        return goal_dict
    except Exception as e:
        logger.error(f"Error fetching goal {goal_id}: {e}")
        raise e


def delete_goal_by_id(goal_id: int, conn: Connection = None) -> bool:
    """Delete a goal by ID."""
    try:
        query = text(f"""
            DELETE FROM {config.GOALS_TABLE}
            WHERE id = :id
            RETURNING id
        """)
        
        result = conn.execute(query, {"id": goal_id})
        row = result.fetchone()
        conn.commit()
        
        return row is not None
    except Exception as e:
        logger.error(f"Error deleting goal {goal_id}: {e}")
        conn.rollback()
        raise e


def update_goal_by_id(goal_id: int, updates: Dict[str, Any], conn: Connection = None) -> Optional[Dict[str, Any]]:
    """
    Update an existing goal by id and return the updated row, or None if not found.
    Same logic as update_pi_goal_by_id - adapted for new structure.
    The ai column is always set to False when a goal is updated by a user.
    """
    try:
        # Handle team_name/group_id updates - recalculate goal_type
        if "team_name" in updates or "group_id" in updates:
            current_goal = get_goal_by_id(goal_id, conn)
            if not current_goal:
                return None
            
            team_name = updates.get("team_name", current_goal.get("team_name"))
            group_id = updates.get("group_id", current_goal.get("group_id"))
            
            is_overall = not team_name
            goal_type = determine_goal_type(team_name, group_id, is_overall)
            updates["goal_type"] = goal_type
        
        # Handle issue_keys - convert to JSON if it's a list, allow NULL
        if "issue_keys" in updates:
            if updates["issue_keys"] is None:
                updates["issue_keys"] = None
            elif isinstance(updates["issue_keys"], list):
                updates["issue_keys"] = json.dumps(updates["issue_keys"])
        
        allowed_columns = {
            "scope_type", "pi_name", "sprint_id", "release_id", "goal_type", 
            "team_name", "group_id", "goal_text", "issue_keys", "status", "priority_bv"
        }
        filtered = {k: v for k, v in updates.items() if k in allowed_columns}
        
        if not filtered:
            raise ValueError("No valid fields provided for goal update")
        
        # Always set ai = False for user updates
        filtered["ai"] = False
        
        set_clauses = ", ".join([f"{k} = :{k}" for k in filtered.keys()])
        params = dict(filtered)
        params["id"] = goal_id
        
        query = text(f"""
            UPDATE {config.GOALS_TABLE}
            SET {set_clauses}, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
            RETURNING *
        """)
        
        result = conn.execute(query, params)
        row = result.fetchone()
        conn.commit()
        
        if not row:
            return None
        
        goal_dict = dict(row._mapping)
        # Convert issue_keys JSON to list
        issue_keys_value = goal_dict.get("issue_keys")
        if issue_keys_value is not None:
            if isinstance(issue_keys_value, str):
                goal_dict["issue_keys"] = json.loads(issue_keys_value)
            else:
                goal_dict["issue_keys"] = issue_keys_value
        else:
            goal_dict["issue_keys"] = []
        
        return goal_dict
    except Exception as e:
        logger.error(f"Error updating goal {goal_id}: {e}")
        conn.rollback()
        raise e

