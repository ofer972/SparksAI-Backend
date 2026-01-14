"""
Goals Service - REST API endpoints for goals (PI, Sprint, Release).

Adapted from pi_goals_service.py - same logic, extended for Sprint and Release.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
from datetime import date, datetime
import logging
import json
from database_connection import get_db_connection
from database_team_metrics import resolve_team_names_from_filter
from database_general import get_prompt_by_email_and_name
from database_goals import (
    create_goal,
    upsert_goal,
    get_goals_filtered,
    get_goal_by_id,
    update_goal_by_id,
    delete_goal_by_id
)
from agent_llm_service import call_llm_service_process_single
from pydantic import BaseModel, Field, ValidationError, model_validator
import config

logger = logging.getLogger(__name__)

goals_router = APIRouter()


def get_current_pi(conn: Connection) -> Optional[str]:
    """Get the first current PI (where today is between start_date and end_date)."""
    try:
        today = date.today()
        query = text(f"""
            SELECT pi_name
            FROM {config.PIS_TABLE}
            WHERE start_date IS NOT NULL 
              AND end_date IS NOT NULL
              AND start_date <= :today
              AND end_date >= :today
            ORDER BY start_date ASC
            LIMIT 1
        """)
        result = conn.execute(query, {"today": today})
        row = result.fetchone()
        if row:
            return row[0]
        return None
    except Exception as e:
        logger.error(f"Error fetching current PI: {e}")
        return None


def get_group_id_from_name(group_name: str, conn: Connection) -> Optional[int]:
    """Convert group_name to group_id."""
    query = text("SELECT group_key FROM groups WHERE group_name = :group_name LIMIT 1")
    result = conn.execute(query, {"group_name": group_name})
    row = result.fetchone()
    return row[0] if row else None


def get_group_name_from_id(group_id: Optional[int], conn: Connection) -> Optional[str]:
    """Convert group_id to group_name."""
    if group_id is None:
        return None
    query = text("SELECT group_name FROM groups WHERE group_key = :group_id LIMIT 1")
    result = conn.execute(query, {"group_id": group_id})
    row = result.fetchone()
    return row[0] if row else None


def enrich_goals_with_group_names(goals: List[Dict[str, Any]], conn: Connection) -> List[Dict[str, Any]]:
    """Add group_name to goals that have group_id."""
    enriched_goals = []
    for goal in goals:
        enriched_goal = goal.copy()
        group_id = goal.get("group_id")
        if group_id is not None:
            group_name = get_group_name_from_id(group_id, conn)
            enriched_goal["group_name"] = group_name
        else:
            enriched_goal["group_name"] = None
        enriched_goals.append(enriched_goal)
    return enriched_goals


def fetch_issues_for_scope(
    scope_type: str,
    pi_name: Optional[str] = None,
    sprint_id: Optional[int] = None,
    release_id: Optional[int] = None,
    team_names_list: Optional[List[str]] = None,
    conn: Connection = None
) -> List[Dict[str, Any]]:
    """
    Fetch issues for a scope (PI, Sprint, or Release).
    - For PI: Returns epics only (issue_type='Epic')
    - For Sprint: Returns ALL issues in sprint
    - For Release: Returns ALL issues in release
    
    Args:
        scope_type: 'pi', 'sprint', or 'release'
        pi_name: PI name (if scope_type='pi')
        sprint_id: Sprint ID (if scope_type='sprint')
        release_id: Release ID (if scope_type='release')
        team_names_list: Optional list of team names to filter
        conn: Database connection
        
    Returns:
        List of issue dictionaries with issue_key, summary, description, team_name
    """
    try:
        where_conditions = []
        params = {}
        
        if scope_type == 'pi':
            where_conditions.append("issue_type = 'Epic'")
            where_conditions.append("quarter_pi = :pi_name")
            params["pi_name"] = pi_name
        elif scope_type == 'sprint':
            where_conditions.append("(:sprint_id = ANY(sprint_ids) OR current_sprint_id = :sprint_id)")
            params["sprint_id"] = sprint_id
        elif scope_type == 'release':
            where_conditions.append(":release_id = ANY(fix_version_ids)")
            params["release_id"] = release_id
        
        # Add team filter if provided
        if team_names_list:
            placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names_list))])
            where_conditions.append(f"team_name IN ({placeholders})")
            for i, name in enumerate(team_names_list):
                params[f"team_name_{i}"] = name
        
        where_clause = " AND ".join(where_conditions)
        
        query = text(f"""
            SELECT 
                issue_key,
                summary,
                description,
                team_name,
                issue_type
            FROM {config.WORK_ITEMS_TABLE}
            WHERE {where_clause}
            ORDER BY team_name, issue_key
        """)
        
        logger.info(f"Fetching issues for scope_type={scope_type}, pi_name={pi_name}, sprint_id={sprint_id}, release_id={release_id}, teams={team_names_list}")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        issues = []
        for row in rows:
            issues.append({
                "issue_key": row[0],
                "summary": row[1] or "",
                "description": row[2] or "",
                "team_name": row[3] or "Unknown",
                "issue_type": row[4] or ""
            })
        
        return issues
    except Exception as e:
        logger.error(f"Error fetching issues for scope {scope_type}: {e}")
        raise


@goals_router.get("/goals/available-sprints")
async def get_available_sprints(
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get sprints available for goal selection.
    Returns:
    - Current sprints: start_date <= today <= end_date
    - Upcoming sprints: start_date - 14 days <= today < start_date
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Resolve team names if team_name is provided
        team_names_list = None
        if team_name:
            team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Build query to get sprints
        where_conditions = [
            # Current sprints: today between start and end
            # OR Upcoming sprints: today is up to 14 days before start
            "((s.start_date <= CURRENT_DATE AND s.end_date >= CURRENT_DATE) OR (s.start_date >= CURRENT_DATE AND s.start_date <= CURRENT_DATE + INTERVAL '14 days'))"
        ]
        
        params = {}
        
        # Add team filter if provided
        if team_names_list:
            placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names_list))])
            where_conditions.append(f"""
                s.sprint_id IN (
                    SELECT DISTINCT current_sprint_id 
                    FROM {config.WORK_ITEMS_TABLE}
                    WHERE team_name IN ({placeholders}) 
                    AND current_sprint_id IS NOT NULL
                    UNION
                    SELECT DISTINCT unnest(sprint_ids) as sprint_id
                    FROM {config.WORK_ITEMS_TABLE}
                    WHERE team_name IN ({placeholders})
                    AND sprint_ids IS NOT NULL
                )
            """)
            for i, name in enumerate(team_names_list):
                params[f"team_name_{i}"] = name
        
        where_clause = " AND ".join(where_conditions)
        
        query = text(f"""
            SELECT 
                s.sprint_id,
                s.name as sprint_name,
                s.start_date,
                s.end_date
            FROM jira_sprints s
            JOIN {config.WORK_ITEMS_TABLE} i ON (
                s.sprint_id = i.current_sprint_id 
                OR s.sprint_id = ANY(i.sprint_ids)
            )
            WHERE {where_clause}
            GROUP BY s.sprint_id, s.name, s.start_date, s.end_date
            ORDER BY s.start_date ASC
        """)
        
        logger.info(f"Fetching available sprints for goals: team_name={team_name}, isGroup={isGroup}, teams={team_names_list}")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        sprints = []
        for row in rows:
            sprints.append({
                "sprint_id": row[0],
                "sprint_name": row[1] or "",
                "start_date": row[2].isoformat() if row[2] else None,
                "end_date": row[3].isoformat() if row[3] else None
            })
        
        return {
            "success": True,
            "data": {
                "sprints": sprints,
                "count": len(sprints)
            },
            "message": f"Retrieved {len(sprints)} available sprints"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching available sprints: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch available sprints: {str(e)}"
        )


@goals_router.get("/goals/issues-for-scope")
async def get_issues_for_scope(
    scope_type: str = Query(..., description="'pi', 'sprint', or 'release'"),
    pi_name: Optional[str] = Query(None, description="Required if scope_type='pi'"),
    sprint_id: Optional[int] = Query(None, description="Required if scope_type='sprint'"),
    release_id: Optional[int] = Query(None, description="Required if scope_type='release'"),
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get issues for goal selection dialog.
    - For PI: Returns epics only (issue_type='Epic')
    - For Sprint: Returns ALL issues in sprint
    - For Release: Returns ALL issues in release
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Resolve team names if team_name is provided
        team_names_list = None
        if team_name:
            team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Fetch issues
        issues = fetch_issues_for_scope(
            scope_type=scope_type,
            pi_name=pi_name,
            sprint_id=sprint_id,
            release_id=release_id,
            team_names_list=team_names_list,
            conn=conn
        )
        
        return {
            "success": True,
            "data": {
                "issues": issues,
                "count": len(issues)
            },
            "message": f"Retrieved {len(issues)} issues for {scope_type}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching issues for scope: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch issues for scope: {str(e)}"
        )


def get_prompt_from_database(scope_type: str, conn: Connection) -> str:
    """Get the Goals Recommendation prompt from the database based on scope_type."""
    # Determine prompt name based on scope_type
    if scope_type == 'sprint':
        prompt_name = "Sprint Goals Recommendation-Content"
    elif scope_type == 'pi':
        prompt_name = "PI Goals Recommendation-Content"
    else:
        # Default to PI for backward compatibility
        prompt_name = "PI Goals Recommendation-Content"
    
    prompt_data = get_prompt_by_email_and_name(
        email_address="admin",
        prompt_name=prompt_name,
        conn=conn,
        active=True
    )
    
    if not prompt_data:
        raise HTTPException(
            status_code=404,
            detail=f"Prompt '{prompt_name}' not found for 'admin' or prompt is inactive"
        )
    
    prompt_text = prompt_data.get("prompt_description", "")
    if not prompt_text:
        raise HTTPException(
            status_code=500,
            detail=f"Prompt '{prompt_name}' has empty content"
        )
    
    return prompt_text


def build_llm_prompt(
    scope_type: str,
    context_name: str,
    issues: List[Dict[str, Any]],
    conn: Connection
) -> str:
    """
    Build the LLM prompt by reading from database and adding context (PI/Sprint) and issues.
    
    Args:
        scope_type: 'pi' or 'sprint'
        context_name: PI name (for scope_type='pi') or Sprint name (for scope_type='sprint')
        issues: List of issues/epics with issue_key, summary, description, team_name
        conn: Database connection
    """
    prompt_template = get_prompt_from_database(scope_type, conn)
    
    # Replace placeholders in prompt template
    if scope_type == 'pi':
        prompt = prompt_template.replace("{pi}", context_name).replace("{{pi}}", context_name)
        # Also try {sprint} replacement in case prompt uses it
        prompt = prompt.replace("{sprint}", context_name).replace("{{sprint}}", context_name)
    elif scope_type == 'sprint':
        prompt = prompt_template.replace("{sprint}", context_name).replace("{{sprint}}", context_name)
        # Also try {pi} replacement in case prompt uses it
        prompt = prompt.replace("{pi}", context_name).replace("{{pi}}", context_name)
    else:
        prompt = prompt_template
    
    # Build issues section
    issues_section = []
    for issue in issues:
        # Handle both epic_key (from fetch_epics_for_pi) and issue_key (from fetch_issues_for_scope)
        issue_key = issue.get("issue_key", "") or issue.get("epic_key", "")
        issue_key = issue_key.strip() if issue_key else ""
        summary = issue.get("summary", "") or issue.get("epic_summary", "")
        summary = summary.strip() if summary else ""
        description = issue.get("description", "") or issue.get("epic_description", "")
        description = description.strip() if description else ""
        team_name = issue.get("team_name", "Unknown").strip()
        
        issue_text = f"{issue_key}: {summary}"
        if description:
            issue_text += f" - {description}"
        issue_text += f" - Team: {team_name}"
        issues_section.append(issue_text)
    
    issues_text = "\n".join(issues_section)
    
    # Use appropriate header based on scope_type
    if scope_type == 'pi':
        header = "Here are the epics details - Summary, Description and team name who owns the implementation of the Epic.:"
    else:
        header = "Here are the issues details - Summary, Description and team name who owns the implementation of the Issue.:"
    
    full_prompt = f"{prompt}\n\n{header}\n{issues_text}"
    
    return full_prompt


def extract_json_from_response(response_text: str) -> Optional[Dict[str, Any]]:
    """Extract JSON from LLM response - simple direct parsing with basic markdown removal."""
    if not response_text or not isinstance(response_text, str):
        return None
    
    response_text = response_text.strip()
    
    if response_text.startswith("```json"):
        response_text = response_text[7:]
    elif response_text.startswith("```"):
        response_text = response_text[3:]
    
    if response_text.endswith("```"):
        response_text = response_text[:-3]
    
    response_text = response_text.strip()
    
    try:
        return json.loads(response_text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON: {e}")
        return None


# Pydantic models for LLM response validation
class GoalItem(BaseModel):
    """Represents a single goal with optional issue keys."""
    goal: str
    issue_keys: Optional[List[str]] = None
    epic_keys: Optional[List[str]] = None  # Backward compatibility
    
    @model_validator(mode='after')
    def normalize_issue_keys(self):
        """Normalize issue_keys from epic_keys for backward compatibility."""
        if self.issue_keys is None and self.epic_keys is not None:
            self.issue_keys = self.epic_keys
        return self


class TeamGoal(BaseModel):
    """Represents team-specific goals."""
    team_name: str
    goals: List[GoalItem] = Field(min_length=1)


class LLMResponse(BaseModel):
    """Structure of LLM response for goal generation."""
    overall_goals: List[GoalItem]
    team_goals: List[TeamGoal]


def validate_llm_response(data: Dict[str, Any]) -> bool:
    """Validate the structure of LLM response using Pydantic models."""
    try:
        validated = LLMResponse(**data)
        
        # Log warnings for missing or empty issue_keys (non-blocking)
        for i, goal in enumerate(validated.overall_goals):
            if goal.issue_keys is None:
                logger.warning(f"⚠️  overall_goals[{i}] missing 'issue_keys' field - goal will be created without connected issues")
            elif len(goal.issue_keys) == 0:
                logger.warning(f"⚠️  overall_goals[{i}].issue_keys is empty - goal will be created without connected issues")
        
        for team_idx, team_goal in enumerate(validated.team_goals):
            for goal_idx, goal in enumerate(team_goal.goals):
                if goal.issue_keys is None:
                    logger.warning(f"⚠️  team_goals[{team_idx}].goals[{goal_idx}] missing 'issue_keys' field - goal will be created without connected issues")
                elif len(goal.issue_keys) == 0:
                    logger.warning(f"⚠️  team_goals[{team_idx}].goals[{goal_idx}].issue_keys is empty - goal will be created without connected issues")
        
        return True
    except ValidationError as e:
        logger.error(f"Validation failed: {e}")
        return False
    except Exception as e:
        logger.error(f"Validation failed with unexpected error: {e}")
        return False


def enrich_issue_keys_with_issue_details(
    goals: List[Dict[str, Any]],
    conn: Connection,
    scope_type: str = 'pi'
) -> List[Dict[str, Any]]:
    """
    Enrich issue_keys in goals with status, summary, and progress_percent from jira_issues.
    Same logic as enrich_epic_keys_with_issue_details - renamed for clarity.
    
    Args:
        goals: List of goal dictionaries
        conn: Database connection
        scope_type: 'pi', 'sprint', or 'release' - determines how progress is calculated
    """
    all_issue_keys = set()
    for goal in goals:
        issue_keys = goal.get("issue_keys", [])
        if isinstance(issue_keys, list):
            all_issue_keys.update(issue_keys)
    
    if not all_issue_keys:
        for goal in goals:
            goal["goal_progress_by_epics"] = 0.0
            goal["goal_progress_by_children"] = 0.0
        return goals
    
    issue_keys_list = list(all_issue_keys)
    placeholders = ", ".join([f":issue_key_{i}" for i in range(len(issue_keys_list))])
    params = {f"issue_key_{i}": key for i, key in enumerate(issue_keys_list)}
    
    query = text(f"""
        SELECT 
            issue_key,
            status,
            status_category,
            summary,
            issue_type,
            number_of_children,
            number_of_completed_children
        FROM {config.WORK_ITEMS_TABLE}
        WHERE issue_key IN ({placeholders})
    """)
    
    result = conn.execute(query, params)
    rows = result.fetchall()
    
    issue_details_lookup = {}
    for row in rows:
        issue_key = row[0]
        status = row[1] or ""
        status_category = row[2] or ""
        summary = row[3] or ""
        issue_type = row[4] or ""
        number_of_children = row[5] if row[5] is not None else 0
        number_of_completed_children = row[6] if row[6] is not None else 0
        
        if number_of_children > 0:
            progress_percent = (number_of_completed_children / number_of_children) * 100
        else:
            progress_percent = 0.0
        
        issue_details_lookup[issue_key] = {
            "status": status,
            "status_category": status_category,
            "summary": summary,
            "issue_type": issue_type,
            "progress_percent": round(progress_percent, 2),
            "number_of_children": number_of_children,
            "number_of_completed_children": number_of_completed_children
        }
    
    enriched_goals = []
    for goal in goals:
        enriched_goal = goal.copy()
        issue_keys = goal.get("issue_keys", [])
        
        if isinstance(issue_keys, list):
            enriched_issue_keys = []
            for issue_key in issue_keys:
                if issue_key in issue_details_lookup:
                    issue_details = issue_details_lookup[issue_key]
                    enriched_issue_keys.append({
                        "issue_key": issue_key,
                        "status": issue_details["status"],
                        "status_category": issue_details["status_category"],
                        "summary": issue_details["summary"],
                        "issue_type": issue_details["issue_type"],
                        "progress_percent": issue_details["progress_percent"],
                        "number_of_children": issue_details["number_of_children"],
                        "number_of_completed_children": issue_details["number_of_completed_children"]
                    })
                else:
                    enriched_issue_keys.append({
                        "issue_key": issue_key,
                        "status": None,
                        "status_category": None,
                        "summary": None,
                        "issue_type": None,
                        "progress_percent": None,
                        "number_of_children": 0,
                        "number_of_completed_children": 0
                    })
            enriched_goal["issue_keys"] = enriched_issue_keys
            
            total_issues = len(enriched_issue_keys)
            done_issues = sum(1 for issue in enriched_issue_keys if issue.get("status_category") == "Done")
            if total_issues > 0:
                enriched_goal["goal_progress_by_epics"] = round((done_issues / total_issues) * 100, 2)
            else:
                enriched_goal["goal_progress_by_epics"] = 0.0
            
            # For sprint goals: calculate progress based on connected issues directly
            # For PI goals: calculate progress based on epic children
            if scope_type == 'sprint':
                # Sprint goals: count connected issues with status_category='Done'
                total_connected_issues = len(enriched_issue_keys)
                done_connected_issues = sum(1 for issue in enriched_issue_keys if issue.get("status_category") == "Done")
                if total_connected_issues > 0:
                    enriched_goal["goal_progress_by_children"] = round((done_connected_issues / total_connected_issues) * 100, 2)
                else:
                    enriched_goal["goal_progress_by_children"] = 0.0
            else:
                # PI goals: use epic children (existing logic)
                total_children = sum(issue.get("number_of_children", 0) for issue in enriched_issue_keys)
                completed_children = sum(issue.get("number_of_completed_children", 0) for issue in enriched_issue_keys)
                if total_children > 0:
                    enriched_goal["goal_progress_by_children"] = round((completed_children / total_children) * 100, 2)
                else:
                    enriched_goal["goal_progress_by_children"] = 0.0
        else:
            enriched_goal["issue_keys"] = issue_keys
            enriched_goal["goal_progress_by_epics"] = 0.0
            enriched_goal["goal_progress_by_children"] = 0.0
        
        enriched_goals.append(enriched_goal)
    
    return enriched_goals


def format_goals_response(
    goals: List[Dict[str, Any]], 
    scope_type: str,
    pi_name: Optional[str] = None,
    sprint_id: Optional[int] = None,
    release_id: Optional[int] = None,
    group_id: Optional[int] = None,
    team_name: Optional[str] = None,
    isGroup: bool = False
) -> Dict[str, Any]:
    """
    Format flat list of goals into response structure.
    Same logic as pi_goals - adapted for new structure.
    """
    overall_goals = []
    group_goals = []
    team_goals_dict = {}
    
    for goal in goals:
        goal_type = goal.get("goal_type")
        goal_team_name = goal.get("team_name")
        goal_group_id = goal.get("group_id")
        
        if goal_type == "overall":
            overall_goals.append(goal)
        elif goal_type == "group":
            group_goals.append(goal)
        elif goal_type == "team" and goal_team_name:
            if goal_team_name not in team_goals_dict:
                team_goals_dict[goal_team_name] = []
            team_goals_dict[goal_team_name].append(goal)
    
    team_goals_response = []
    for team_name_key, team_goals_list in team_goals_dict.items():
        team_goals_response.append({
            "team_name": team_name_key,
            "goals": team_goals_list
        })
    
    response_data = {"scope_type": scope_type}
    if pi_name:
        response_data["pi_name"] = pi_name
    if sprint_id:
        response_data["sprint_id"] = sprint_id
    if release_id:
        response_data["release_id"] = release_id
    
    if isGroup and group_id:
        response_data["group_goals"] = group_goals
        response_data["team_goals"] = team_goals_response
    elif team_name and not isGroup:
        response_data["team_goals"] = team_goals_response
    else:
        response_data["overall_goals"] = overall_goals
        response_data["team_goals"] = team_goals_response
    
    return response_data


# Pydantic models
class GoalCreateRequest(BaseModel):
    scope_type: str  # 'pi', 'sprint', 'release'
    pi_name: Optional[str] = None
    sprint_id: Optional[int] = None
    release_id: Optional[int] = None
    team_name: Optional[str] = None
    group_name: Optional[str] = None  # UI sends name, we convert to group_id
    goal_text: str
    issue_keys: List[str]  # Changed from epic_keys
    status: Optional[str] = "Draft"
    priority_bv: Optional[int] = None


class GoalUpdateRequest(BaseModel):
    scope_type: Optional[str] = None
    pi_name: Optional[str] = None
    sprint_id: Optional[int] = None
    release_id: Optional[int] = None
    team_name: Optional[str] = None
    group_name: Optional[str] = None  # UI sends name, we convert to group_id
    goal_text: Optional[str] = None
    issue_keys: Optional[List[str]] = None  # Changed from epic_keys
    status: Optional[str] = None
    priority_bv: Optional[int] = None


class GoalGenerateRequest(BaseModel):
    scope_type: str = "pi"  # 'pi' or 'sprint'
    pi_name: Optional[str] = None
    sprint_id: Optional[int] = None
    team_name: Optional[str] = None
    isGroup: bool = False
    quarter: Optional[str] = None


class MoveGoalsAIToUserRequest(BaseModel):
    goal_ids: List[int]


@goals_router.post("/goals/generate")
async def generate_ai_goals(
    request: GoalGenerateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Generate goals using LLM analysis.
    Currently supports PI and Sprint (Release TBD).
    Same logic as pi_goals - adapted for scope_type.
    """
    try:
        scope_type = request.scope_type
        pi_name = request.pi_name
        sprint_id = request.sprint_id
        team_name = request.team_name
        isGroup = request.isGroup
        
        logger.info(f"POST /goals/generate - scope_type={scope_type}, pi_name={pi_name}, sprint_id={sprint_id}, team_name={team_name}, isGroup={isGroup}")
        
        # Resolve context based on scope_type
        if scope_type == 'pi':
            resolved_pi = pi_name
            if not resolved_pi:
                resolved_pi = get_current_pi(conn)
                if not resolved_pi:
                    raise HTTPException(
                        status_code=404,
                        detail="No current PI found and no pi_name parameter provided"
                    )
            logger.info(f"Using PI: {resolved_pi}")
        elif scope_type == 'sprint':
            if not sprint_id:
                raise HTTPException(
                    status_code=400,
                    detail="sprint_id is required when scope_type='sprint'"
                )
            logger.info(f"Using Sprint ID: {sprint_id}")
        else:
            raise HTTPException(
                status_code=400,
                detail=f"scope_type '{scope_type}' not yet supported for generation"
            )
        
        # Resolve team names
        team_names_list = None
        if team_name:
            team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
            logger.info(f"Resolved team names: {team_names_list}")
        
        # Fetch epics/issues based on scope_type
        issues = []
        context_name = ""
        
        if scope_type == 'pi':
            context_name = resolved_pi
            issues = fetch_issues_for_scope(
                scope_type='pi',
                pi_name=resolved_pi,
                team_names_list=team_names_list,
                conn=conn
            )
            if not issues:
                return {
                    "success": True,
                    "data": {
                        "scope_type": scope_type,
                        "pi_name": resolved_pi,
                        "overall_goals": [],
                        "team_goals": []
                    },
                    "message": f"No epics found for PI {resolved_pi}"
                }
            logger.info(f"Found {len(issues)} epics for PI {resolved_pi}")
        elif scope_type == 'sprint':
            # Fetch sprint name for context
            sprint_query = text("SELECT name FROM jira_sprints WHERE sprint_id = :sprint_id")
            sprint_result = conn.execute(sprint_query, {"sprint_id": sprint_id})
            sprint_row = sprint_result.fetchone()
            if sprint_row:
                context_name = sprint_row[0] or f"Sprint {sprint_id}"
            else:
                context_name = f"Sprint {sprint_id}"
            
            # Fetch all issues for sprint
            issues = fetch_issues_for_scope(
                scope_type='sprint',
                sprint_id=sprint_id,
                team_names_list=team_names_list,
                conn=conn
            )
            if not issues:
                return {
                    "success": True,
                    "data": {
                        "scope_type": scope_type,
                        "sprint_id": sprint_id,
                        "overall_goals": [],
                        "team_goals": []
                    },
                    "message": f"No issues found for Sprint {sprint_id}"
                }
            logger.info(f"Found {len(issues)} issues for Sprint {sprint_id}")
        else:
            # Release/other generation not yet implemented
            raise HTTPException(
                status_code=501,
                detail=f"Generation for scope_type '{scope_type}' not yet implemented"
            )
        
        # Build LLM prompt
        prompt = build_llm_prompt(scope_type, context_name, issues, conn)
        
        # Call LLM service
        metadata = {
            "job_type": "goals",
            "scope_type": scope_type,
            "pi_name": resolved_pi if scope_type == 'pi' else None,
            "sprint_id": sprint_id if scope_type == 'sprint' else None,
            "team_name": team_name,
            "isGroup": isGroup
        }
        
        logger.info(f"Calling LLM service for goals (prompt length: {len(prompt)} chars)")
        llm_response = await call_llm_service_process_single(
            prompt=prompt,
            system_prompt=None,
            metadata=metadata
        )
        
        # Extract response text
        if not isinstance(llm_response, dict):
            logger.error(f"LLM service returned unexpected response type: {type(llm_response)}")
            raise HTTPException(
                status_code=502,
                detail="LLM service returned invalid response format"
            )
        
        response_text = None
        if "data" in llm_response and isinstance(llm_response.get("data"), dict):
            response_text = llm_response.get("data", {}).get("response")
        
        if not response_text:
            response_text = llm_response.get("response")
        
        if not response_text or not isinstance(response_text, str):
            logger.error(f"LLM service returned empty or invalid response. Response: {llm_response}")
            raise HTTPException(
                status_code=502,
                detail="LLM service returned empty or invalid response"
            )
        
        response_text = response_text.strip()
        if not response_text:
            logger.error(f"LLM service returned empty response after stripping")
            raise HTTPException(
                status_code=502,
                detail="LLM service returned empty response"
            )
        
        logger.info(f"LLM response received (length: {len(response_text)} chars)")
        
        # Parse JSON from response
        parsed_data = extract_json_from_response(response_text)
        
        if not parsed_data:
            logger.error(f"Failed to parse JSON from LLM response.")
            logger.error(f"Response preview (first 500 chars): {response_text[:500]}")
            raise HTTPException(
                status_code=500,
                detail="LLM response is not valid JSON. The response should be only JSON with no additional text."
            )
        
        # Validate response structure
        if not validate_llm_response(parsed_data):
            logger.error(f"Invalid LLM response structure: {parsed_data}")
            raise HTTPException(
                status_code=500,
                detail="LLM response does not match expected structure"
            )
        
        logger.info("Successfully parsed and validated LLM response")
        
        # Convert group_name to group_id if isGroup=true
        group_id_for_saving = None
        if isGroup:
            if not team_name:
                raise HTTPException(
                    status_code=400,
                    detail="team_name parameter is required when isGroup=true (team_name should be the group name)"
                )
            group_id_for_saving = get_group_id_from_name(team_name, conn)
            if not group_id_for_saving:
                raise HTTPException(
                    status_code=404,
                    detail=f"Group '{team_name}' not found"
                )
            logger.info(f"isGroup=true: Converted group_name '{team_name}' to group_id={group_id_for_saving}")
        
        saved_overall_goals = []
        saved_team_goals_by_team = {}
        
        # Save overall goals using UPSERT (ai=true)
        overall_goals_from_llm = parsed_data.get("overall_goals", [])
        logger.info(f"Upserting {len(overall_goals_from_llm)} overall goals (will become {'group' if group_id_for_saving else 'overall'} goals)")
        for goal_index, goal in enumerate(overall_goals_from_llm, start=1):
            try:
                # Get issue_keys (prefer issue_keys, fallback to epic_keys for backward compatibility)
                issue_keys = goal.get("issue_keys") or goal.get("epic_keys", [])
                if not issue_keys or len(issue_keys) == 0:
                    logger.warning(f"⚠️  overall_goals[{goal_index}] has empty issue_keys - goal will be created without connected issues")
                
                goal_data = {
                    "scope_type": scope_type,
                    "pi_name": resolved_pi if scope_type == 'pi' else None,
                    "sprint_id": sprint_id if scope_type == 'sprint' else None,
                    "release_id": None,
                    "team_name": None,
                    "group_id": group_id_for_saving,
                    "goal_text": goal.get("goal", ""),
                    "issue_keys": issue_keys,
                    "status": "Draft-AI",
                    "ai": True,
                    "is_overall": True,
                    "goal_number": goal_index
                }
                saved_goal = upsert_goal(goal_data, conn)
                if saved_goal:
                    saved_overall_goals.append(saved_goal)
                    logger.info(f"Successfully upserted overall goal with ID {saved_goal.get('id')} (goal_number={goal_index})")
            except Exception as e:
                logger.error(f"❌ [GENERATE ERROR] Error upserting overall goal (goal_number={goal_index}): {e}", exc_info=True)
        
        # Save team goals using UPSERT (ai=true)
        team_goals_from_llm = parsed_data.get("team_goals", [])
        logger.info(f"Upserting team goals from {len(team_goals_from_llm)} teams")
        for team_goal in team_goals_from_llm:
            team_name_from_llm = team_goal.get("team_name")
            if team_name_from_llm not in saved_team_goals_by_team:
                saved_team_goals_by_team[team_name_from_llm] = []
            
            goals_for_team = team_goal.get("goals", [])
            logger.info(f"Upserting {len(goals_for_team)} goals for team {team_name_from_llm}")
            for goal_index, goal in enumerate(goals_for_team, start=1):
                try:
                    # Get issue_keys (prefer issue_keys, fallback to epic_keys for backward compatibility)
                    issue_keys = goal.get("issue_keys") or goal.get("epic_keys", [])
                    if not issue_keys or len(issue_keys) == 0:
                        logger.warning(f"⚠️  team_goals[{team_name_from_llm}].goals[{goal_index}] has empty issue_keys - goal will be created without connected issues")
                    
                    goal_data = {
                        "scope_type": scope_type,
                        "pi_name": resolved_pi if scope_type == 'pi' else None,
                        "sprint_id": sprint_id if scope_type == 'sprint' else None,
                        "release_id": None,
                        "team_name": team_name_from_llm,
                        "group_id": None,
                        "goal_text": goal.get("goal", ""),
                        "issue_keys": issue_keys,
                        "status": "Draft-AI",
                        "ai": True,
                        "is_overall": False,
                        "goal_number": goal_index
                    }
                    saved_goal = upsert_goal(goal_data, conn)
                    if saved_goal:
                        saved_team_goals_by_team[team_name_from_llm].append(saved_goal)
                        logger.info(f"Successfully upserted team goal with ID {saved_goal.get('id')} for team {team_name_from_llm} (goal_number={goal_index})")
                except Exception as e:
                    logger.error(f"❌ [GENERATE ERROR] Error upserting team goal for {team_name_from_llm} (goal_number={goal_index}): {e}", exc_info=True)
        
        logger.info(f"Response summary: {len(saved_overall_goals)} overall goals, {len(saved_team_goals_by_team)} teams with goals")
        
        return {
            "success": True,
            "message": f"Generated and saved goals for {scope_type}"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating goals: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate goals: {str(e)}"
        )


@goals_router.get("/goals")
async def get_goals(
    scope_type: str = Query(..., description="'pi', 'sprint', or 'release'"),
    pi_name: Optional[str] = Query(None, description="Required if scope_type='pi'"),
    sprint_id: Optional[int] = Query(None, description="Required if scope_type='sprint'"),
    release_id: Optional[int] = Query(None, description="Required if scope_type='release'"),
    ai: Optional[bool] = Query(None, description="Filter by AI-generated goals"),
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get goals with optional filters.
    Same logic as get_pi_goals - adapted for scope_type.
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Resolve team names if team_name is provided
        team_names_list = None
        group_id_for_response = None
        
        if team_name:
            if isGroup:
                # team_name is actually a group name - convert to group_id
                group_id_for_response = get_group_id_from_name(team_name, conn)
                if not group_id_for_response:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Group '{team_name}' not found"
                    )
                team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
            else:
                team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Fetch goals from database
        filtered_goals = []
        
        if isGroup and group_id_for_response:
            # For group filter: fetch group goals and team goals for teams in group
            group_goals = get_goals_filtered(
                scope_type=scope_type,
                pi_name=pi_name,
                sprint_id=sprint_id,
                release_id=release_id,
                goal_type="group",
                group_id=group_id_for_response,
                ai=ai,
                limit=100,
                conn=conn
            )
            filtered_goals.extend(group_goals)
            
            if team_names_list:
                team_goals = get_goals_filtered(
                    scope_type=scope_type,
                    pi_name=pi_name,
                    sprint_id=sprint_id,
                    release_id=release_id,
                    goal_type="team",
                    team_names_list=team_names_list,
                    ai=ai,
                    limit=1000,
                    conn=conn
                )
                filtered_goals.extend(team_goals)
        elif team_name and not isGroup:
            # For team filter: fetch only goals for this team
            filtered_goals = get_goals_filtered(
                scope_type=scope_type,
                pi_name=pi_name,
                sprint_id=sprint_id,
                release_id=release_id,
                goal_type="team",
                team_name=team_name,
                ai=ai,
                limit=100,
                conn=conn
            )
        else:
            # No filter: fetch overall and team goals
            overall_goals = get_goals_filtered(
                scope_type=scope_type,
                pi_name=pi_name,
                sprint_id=sprint_id,
                release_id=release_id,
                goal_type="overall",
                ai=ai,
                limit=100,
                conn=conn
            )
            team_goals = get_goals_filtered(
                scope_type=scope_type,
                pi_name=pi_name,
                sprint_id=sprint_id,
                release_id=release_id,
                goal_type="team",
                ai=ai,
                limit=1000,
                conn=conn
            )
            filtered_goals = overall_goals + team_goals
        
        # Enrich issue_keys with issue details
        enriched_goals = enrich_issue_keys_with_issue_details(filtered_goals, conn, scope_type=scope_type)
        
        # Enrich with group_name for UI compatibility
        enriched_goals = enrich_goals_with_group_names(enriched_goals, conn)
        
        # Format response
        response_data = format_goals_response(
            goals=enriched_goals,
            scope_type=scope_type,
            pi_name=pi_name,
            sprint_id=sprint_id,
            release_id=release_id,
            group_id=group_id_for_response,
            team_name=team_name if not isGroup else None,
            isGroup=isGroup
        )
        
        return {
            "success": True,
            "data": response_data,
            "message": f"Retrieved goals for {scope_type}"
        }
    except HTTPException as e:
        logger.error(f"Error fetching goals - HTTPException: status_code={e.status_code}, detail={e.detail}")
        raise
    except Exception as e:
        logger.error(f"Error fetching goals: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch goals: {str(e)}"
        )


@goals_router.post("/goals")
async def create_goal_endpoint(
    request: GoalCreateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Manually create a goal.
    Same logic as create_pi_goal - adapted for new structure.
    """
    try:
        # Convert group_name to group_id if provided
        group_id = None
        if request.group_name:
            group_id = get_group_id_from_name(request.group_name, conn)
            if not group_id:
                raise HTTPException(
                    status_code=404,
                    detail=f"Group '{request.group_name}' not found"
                )
        
        is_overall = not request.team_name
        
        goal_data = {
            "scope_type": request.scope_type,
            "pi_name": request.pi_name,
            "sprint_id": request.sprint_id,
            "release_id": request.release_id,
            "team_name": request.team_name,
            "group_id": group_id,
            "goal_text": request.goal_text,
            "issue_keys": request.issue_keys,
            "status": request.status or "Draft",
            "priority_bv": request.priority_bv,
            "ai": False,
            "is_overall": is_overall
        }
        
        created_goal = create_goal(goal_data, conn)
        
        # Enrich with group_name for UI compatibility
        enriched_goals = enrich_goals_with_group_names([created_goal], conn)
        enriched_goal = enriched_goals[0] if enriched_goals else created_goal
        
        return {
            "success": True,
            "data": {
                "goal": enriched_goal
            },
            "message": f"Goal created with ID {created_goal.get('id')}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating goal: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create goal: {str(e)}"
        )


@goals_router.patch("/goals/ai-to-user")
async def move_goals_ai_to_user_endpoint(
    request: MoveGoalsAIToUserRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Move multiple goals from AI-generated to user-modified by setting ai = False and status = 'Draft'.
    Same logic as pi_goals.
    """
    try:
        goal_ids = request.goal_ids
        
        if not goal_ids:
            raise HTTPException(
                status_code=400,
                detail="goal_ids list cannot be empty"
            )
        
        placeholders = ", ".join([f":goal_id_{i}" for i in range(len(goal_ids))])
        params = {f"goal_id_{i}": goal_id for i, goal_id in enumerate(goal_ids)}
        
        query = text(f"""
            UPDATE {config.GOALS_TABLE}
            SET ai = false, status = 'Draft', updated_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
        """)
        
        result = conn.execute(query, params)
        rows_updated = result.rowcount
        conn.commit()
        
        logger.info(f"PATCH /goals/ai-to-user - Updated {rows_updated} goals from AI to user (requested {len(goal_ids)} goal_ids)")
        
        return {
            "success": True,
            "data": {
                "goal_ids_requested": len(goal_ids),
                "goals_updated": rows_updated
            },
            "message": f"Updated {rows_updated} goal(s) from AI to user"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error moving goals from AI to user: {e}")
        conn.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to move goals from AI to user: {str(e)}"
        )


@goals_router.patch("/goals/{goal_id}")
async def update_goal_endpoint(
    goal_id: int,
    request: GoalUpdateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Update an existing goal.
    Same logic as update_pi_goal - adapted for new structure.
    """
    try:
        updates = request.model_dump(exclude_unset=True)
        
        # Convert group_name to group_id if provided
        if "group_name" in updates:
            group_name = updates.pop("group_name")
            if group_name:
                group_id = get_group_id_from_name(group_name, conn)
                if not group_id:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Group '{group_name}' not found"
                    )
                updates["group_id"] = group_id
        
        if not updates:
            raise HTTPException(
                status_code=400,
                detail="No fields provided for update"
            )
        
        updated_goal = update_goal_by_id(goal_id, updates, conn)
        
        if not updated_goal:
            raise HTTPException(
                status_code=404,
                detail=f"Goal with ID {goal_id} not found"
            )
        
        # Enrich with group_name for UI compatibility
        enriched_goals = enrich_goals_with_group_names([updated_goal], conn)
        enriched_goal = enriched_goals[0] if enriched_goals else updated_goal
        
        return {
            "success": True,
            "data": {
                "goal": enriched_goal
            },
            "message": f"Goal {goal_id} updated successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating goal {goal_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update goal: {str(e)}"
        )


@goals_router.delete("/goals/{goal_id}")
async def delete_goal_endpoint(
    goal_id: int,
    conn: Connection = Depends(get_db_connection)
):
    """
    Delete a goal by ID.
    Same logic as delete_pi_goal.
    """
    try:
        deleted = delete_goal_by_id(goal_id, conn)
        
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"Goal with ID {goal_id} not found"
            )
        
        return {
            "success": True,
            "message": f"Goal {goal_id} deleted successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting goal {goal_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete goal: {str(e)}"
        )

