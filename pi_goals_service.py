"""
PI Goals Service - REST API endpoint for generating PI goals using LLM.

This endpoint analyzes epics for a PI and uses LLM to generate
1-4 PI goals per team, plus overall PI goals.
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
from database_general import (
    get_prompt_by_email_and_name,
    create_pi_goal,
    upsert_pi_goal,
    get_pi_goals_filtered,
    get_pi_goal_by_id,
    update_pi_goal_by_id,
    delete_pi_goal_by_id
)
from agent_llm_service import call_llm_service_process_single
from pydantic import BaseModel
import config

logger = logging.getLogger(__name__)

pi_goals_router = APIRouter()


def get_current_pi(conn: Connection) -> Optional[str]:
    """
    Get the first current PI (where today is between start_date and end_date).
    
    Args:
        conn: Database connection
        
    Returns:
        PI name (first current PI) or None if no current PI exists
    """
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


def fetch_epics_for_pi(
    pi: str,
    team_names_list: Optional[List[str]],
    conn: Connection
) -> List[Dict[str, Any]]:
    """
    Fetch epics for a PI with summary, description, and team_name.
    
    Args:
        pi: PI name
        team_names_list: Optional list of team names to filter
        conn: Database connection
        
    Returns:
        List of epic dictionaries with epic_key, summary, description, team_name
    """
    try:
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
        
        query = text(f"""
            SELECT 
                issue_key as epic_key,
                summary as epic_summary,
                description as epic_description,
                team_name
            FROM {config.WORK_ITEMS_TABLE}
            WHERE {where_clause}
            ORDER BY team_name, issue_key
        """)
        
        logger.info(f"Fetching epics for PI: {pi}, teams: {team_names_list}")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        epics = []
        for row in rows:
            epics.append({
                "epic_key": row[0],
                "summary": row[1] or "",
                "description": row[2] or "",
                "team_name": row[3] or "Unknown"
            })
        
        return epics
    except Exception as e:
        logger.error(f"Error fetching epics for PI {pi}: {e}")
        raise


def get_prompt_from_database(conn: Connection) -> str:
    """
    Get the PI Goals Recommendation prompt from the database.
    
    Args:
        conn: Database connection
        
    Returns:
        Prompt text from database
        
    Raises:
        HTTPException if prompt not found or inactive
    """
    prompt_data = get_prompt_by_email_and_name(
        email_address="admin",
        prompt_name="PI Goals Recommendation-Content",
        conn=conn,
        active=True  # Only get active prompts
    )
    
    if not prompt_data:
        raise HTTPException(
            status_code=404,
            detail="Prompt 'PI Goals Recommendation-Content' not found for 'admin' or prompt is inactive"
        )
    
    prompt_text = prompt_data.get("prompt_description", "")
    if not prompt_text:
        raise HTTPException(
            status_code=500,
            detail="Prompt 'PI Goals Recommendation-Content' has empty content"
        )
    
    return prompt_text


def build_llm_prompt(pi: str, epics: List[Dict[str, Any]], conn: Connection) -> str:
    """
    Build the LLM prompt by reading from database and adding PI name and epics.
    
    Args:
        pi: PI name
        epics: List of epic dictionaries with epic_key, summary, description, team_name
        conn: Database connection
        
    Returns:
        Formatted prompt string
    """
    # Get prompt from database
    prompt_template = get_prompt_from_database(conn)
    
    # Replace PI name placeholder in the prompt (handle both {pi} and {{pi}})
    prompt = prompt_template.replace("{pi}", pi).replace("{{pi}}", pi)
    
    # Build epics section - list all epics one by one
    epics_section = []
    for epic in epics:
        epic_key = epic.get("epic_key", "").strip()
        summary = epic.get("summary", "").strip()
        description = epic.get("description", "").strip()
        team_name = epic.get("team_name", "Unknown").strip()
        
        # Format: Epic KEY: Summary - Description - Team: TeamName
        epic_text = f"Epic {epic_key}: {summary}"
        if description:
            epic_text += f" - {description}"
        epic_text += f" - Team: {team_name}"
        epics_section.append(epic_text)
    
    epics_text = "\n".join(epics_section)
    
    # Concatenate header and epics after the prompt
    header = "Here are the epics details - Summary, Description and team name who owns the implementation of the Epic.:"
    full_prompt = f"{prompt}\n\n{header}\n{epics_text}"
    
    return full_prompt


def extract_json_from_response(response_text: str) -> Optional[Dict[str, Any]]:
    """
    Extract JSON from LLM response - simple direct parsing with basic markdown removal.
    
    Args:
        response_text: Raw response text from LLM
        
    Returns:
        Parsed JSON dictionary or None if parsing fails
    """
    if not response_text or not isinstance(response_text, str):
        return None
    
    # Clean up the response text
    response_text = response_text.strip()
    
    # Simple markdown code block removal (if present)
    if response_text.startswith("```json"):
        response_text = response_text[7:]  # Remove ```json
    elif response_text.startswith("```"):
        response_text = response_text[3:]  # Remove ```
    
    if response_text.endswith("```"):
        response_text = response_text[:-3]  # Remove closing ```
    
    response_text = response_text.strip()
    
    # Simple direct JSON parsing
    try:
        return json.loads(response_text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON: {e}")
        return None


def validate_llm_response(data: Dict[str, Any]) -> bool:
    """
    Validate the structure of LLM response - simple validation.
    
    Args:
        data: Parsed JSON data
        
    Returns:
        True if valid, False otherwise
    """
    if not isinstance(data, dict):
        logger.error("Validation failed: data is not a dict")
        return False
    
    # Check overall_goals
    if "overall_goals" not in data:
        logger.error("Validation failed: overall_goals missing")
        return False
    overall_goals = data["overall_goals"]
    if not isinstance(overall_goals, list):
        logger.error("Validation failed: overall_goals is not a list")
        return False
    
    # Validate each overall goal
    for i, goal in enumerate(overall_goals):
        if not isinstance(goal, dict):
            logger.error(f"Validation failed: overall_goals[{i}] is not a dict")
            return False
        if "goal" not in goal:
            logger.error(f"Validation failed: overall_goals[{i}] missing 'goal' field")
            return False
        if "epic_keys" not in goal:
            logger.error(f"Validation failed: overall_goals[{i}] missing 'epic_keys' field")
            return False
        if not isinstance(goal["epic_keys"], list):
            logger.error(f"Validation failed: overall_goals[{i}].epic_keys is not a list")
            return False
    
    # Check team_goals
    if "team_goals" not in data:
        logger.error("Validation failed: team_goals missing")
        return False
    team_goals = data["team_goals"]
    if not isinstance(team_goals, list):
        logger.error("Validation failed: team_goals is not a list")
        return False
    
    # Validate each team goal
    for team_idx, team_goal in enumerate(team_goals):
        if not isinstance(team_goal, dict):
            logger.error(f"Validation failed: team_goals[{team_idx}] is not a dict")
            return False
        if "team_name" not in team_goal:
            logger.error(f"Validation failed: team_goals[{team_idx}] missing 'team_name' field")
            return False
        if "goals" not in team_goal:
            logger.error(f"Validation failed: team_goals[{team_idx}] missing 'goals' field")
            return False
        goals = team_goal["goals"]
        if not isinstance(goals, list):
            logger.error(f"Validation failed: team_goals[{team_idx}].goals is not a list")
            return False
        if len(goals) < 1 or len(goals) > 4:
            logger.error(f"Validation failed: team_goals[{team_idx}].goals count is {len(goals)}, must be 1-4")
            return False
        for goal_idx, goal in enumerate(goals):
            if not isinstance(goal, dict):
                logger.error(f"Validation failed: team_goals[{team_idx}].goals[{goal_idx}] is not a dict")
                return False
            if "goal" not in goal:
                logger.error(f"Validation failed: team_goals[{team_idx}].goals[{goal_idx}] missing 'goal' field")
                return False
            if "epic_keys" not in goal:
                logger.error(f"Validation failed: team_goals[{team_idx}].goals[{goal_idx}] missing 'epic_keys' field")
                return False
            if not isinstance(goal["epic_keys"], list):
                logger.error(f"Validation failed: team_goals[{team_idx}].goals[{goal_idx}].epic_keys is not a list")
                return False
    
    return True


def enrich_epic_keys_with_issue_details(
    goals: List[Dict[str, Any]],
    conn: Connection
) -> List[Dict[str, Any]]:
    """
    Enrich epic_keys in goals with status, summary, and progress_percent from jira_issues.
    
    Args:
        goals: List of goal dictionaries with epic_keys arrays
        conn: Database connection
        
    Returns:
        List of goals with enriched epic_keys (array of objects instead of strings)
    """
    # Collect all unique epic_keys from all goals
    all_epic_keys = set()
    for goal in goals:
        epic_keys = goal.get("epic_keys", [])
        if isinstance(epic_keys, list):
            all_epic_keys.update(epic_keys)
    
    if not all_epic_keys:
        # No epic_keys to enrich, add progress fields with 0.0
        for goal in goals:
            goal["goal_progress_by_epics"] = 0.0
            goal["goal_progress_by_children"] = 0.0
        return goals
    
    # Single batch query to fetch all epic details
    epic_keys_list = list(all_epic_keys)
    placeholders = ", ".join([f":epic_key_{i}" for i in range(len(epic_keys_list))])
    params = {f"epic_key_{i}": key for i, key in enumerate(epic_keys_list)}
    
    query = text(f"""
        SELECT 
            issue_key,
            status,
            status_category,
            summary,
            number_of_children,
            number_of_completed_children
        FROM {config.WORK_ITEMS_TABLE}
        WHERE issue_key IN ({placeholders})
    """)
    
    result = conn.execute(query, params)
    rows = result.fetchall()
    
    # Build lookup dictionary: {issue_key: {status, status_category, summary, progress_percent, number_of_children, number_of_completed_children}}
    epic_details_lookup = {}
    for row in rows:
        issue_key = row[0]
        status = row[1] or ""
        status_category = row[2] or ""
        summary = row[3] or ""
        number_of_children = row[4] if row[4] is not None else 0
        number_of_completed_children = row[5] if row[5] is not None else 0
        
        # Calculate progress_percent
        if number_of_children > 0:
            progress_percent = (number_of_completed_children / number_of_children) * 100
        else:
            progress_percent = 0.0
        
        epic_details_lookup[issue_key] = {
            "status": status,
            "status_category": status_category,
            "summary": summary,
            "progress_percent": round(progress_percent, 2),
            "number_of_children": number_of_children,
            "number_of_completed_children": number_of_completed_children
        }
    
    # Enrich goals with epic details
    enriched_goals = []
    for goal in goals:
        enriched_goal = goal.copy()
        epic_keys = goal.get("epic_keys", [])
        
        if isinstance(epic_keys, list):
            # Transform array of strings to array of objects
            enriched_epic_keys = []
            for epic_key in epic_keys:
                if epic_key in epic_details_lookup:
                    epic_details = epic_details_lookup[epic_key]
                    enriched_epic_keys.append({
                        "issue_key": epic_key,
                        "status": epic_details["status"],
                        "status_category": epic_details["status_category"],
                        "summary": epic_details["summary"],
                        "progress_percent": epic_details["progress_percent"],
                        "number_of_children": epic_details["number_of_children"],
                        "number_of_completed_children": epic_details["number_of_completed_children"]
                    })
                else:
                    # Epic not found in jira_issues, include with null values
                    enriched_epic_keys.append({
                        "issue_key": epic_key,
                        "status": None,
                        "status_category": None,
                        "summary": None,
                        "progress_percent": None,
                        "number_of_children": 0,
                        "number_of_completed_children": 0
                    })
            enriched_goal["epic_keys"] = enriched_epic_keys
            
            # Calculate goal progress
            # goal_progress_by_epics: percentage of epics with status_category='Done'
            total_epics = len(enriched_epic_keys)
            done_epics = sum(1 for epic in enriched_epic_keys if epic.get("status_category") == "Done")
            if total_epics > 0:
                enriched_goal["goal_progress_by_epics"] = round((done_epics / total_epics) * 100, 2)
            else:
                enriched_goal["goal_progress_by_epics"] = 0.0
            
            # goal_progress_by_children: percentage based on sum of completed children / sum of total children
            total_children = sum(epic.get("number_of_children", 0) for epic in enriched_epic_keys)
            completed_children = sum(epic.get("number_of_completed_children", 0) for epic in enriched_epic_keys)
            if total_children > 0:
                enriched_goal["goal_progress_by_children"] = round((completed_children / total_children) * 100, 2)
            else:
                enriched_goal["goal_progress_by_children"] = 0.0
        else:
            # Keep as-is if not a list
            enriched_goal["epic_keys"] = epic_keys
            # No epics, so progress is 0
            enriched_goal["goal_progress_by_epics"] = 0.0
            enriched_goal["goal_progress_by_children"] = 0.0
        
        enriched_goals.append(enriched_goal)
    
    return enriched_goals


def format_goals_response(
    goals: List[Dict[str, Any]], 
    pi: str,
    group_name: Optional[str] = None,
    team_name: Optional[str] = None,
    isGroup: bool = False
) -> Dict[str, Any]:
    """
    Format flat list of goals into response structure.
    
    Args:
        goals: Flat list of goal dictionaries from database
        pi: PI name
        group_name: Group name if filtering by group
        team_name: Team name if filtering by team
        isGroup: If true, team_name is treated as group name
        
    Returns:
        Dictionary with formatted response structure:
        - If isGroup=true: {"group_goals": [...], "team_goals": [...]}
        - If team_name only: {"team_goals": [...]} (no overall_goals)
        - If no filter: {"overall_goals": [...], "team_goals": [...]}
    """
    # Separate goals by type
    overall_goals = []
    group_goals = []
    team_goals_dict = {}  # Group by team_name
    
    for goal in goals:
        goal_type = goal.get("goal_type")
        goal_team_name = goal.get("team_name")
        goal_group_name = goal.get("group_name")
        
        if goal_type == "overall":
            overall_goals.append(goal)
        elif goal_type == "group":
            group_goals.append(goal)
        elif goal_type == "team" and goal_team_name:
            if goal_team_name not in team_goals_dict:
                team_goals_dict[goal_team_name] = []
            team_goals_dict[goal_team_name].append(goal)
    
    # Build team_goals array (grouped by team_name)
    team_goals_response = []
    for team_name_key, team_goals_list in team_goals_dict.items():
        team_goals_response.append({
            "team_name": team_name_key,
            "goals": team_goals_list
        })
    
    # Build response based on filters
    response_data = {"pi": pi}
    
    if isGroup and group_name:
        # Case A: Group filter - return group_goals and team_goals
        response_data["group_goals"] = group_goals
        response_data["team_goals"] = team_goals_response
    elif team_name and not isGroup:
        # Case B: Team filter only - return only team_goals (omit overall_goals)
        response_data["team_goals"] = team_goals_response
    else:
        # Case C: No filter - return overall_goals and team_goals
        response_data["overall_goals"] = overall_goals
        response_data["team_goals"] = team_goals_response
    
    return response_data


# Pydantic models for request/response
class PIGoalCreateRequest(BaseModel):
    pi: str
    team_name: Optional[str] = None
    group_name: Optional[str] = None
    goal_text: str
    epic_keys: List[str]
    status: Optional[str] = "Draft"
    priority_bv: Optional[int] = None


class PIGoalUpdateRequest(BaseModel):
    pi: Optional[str] = None
    team_name: Optional[str] = None
    group_name: Optional[str] = None
    goal_text: Optional[str] = None
    epic_keys: Optional[List[str]] = None
    status: Optional[str] = None
    priority_bv: Optional[int] = None


class PIGoalGenerateRequest(BaseModel):
    pi: Optional[str] = None
    team_name: Optional[str] = None
    isGroup: bool = False
    quarter: Optional[str] = None


class MoveGoalsAIToUserRequest(BaseModel):
    goal_ids: List[int]


@pi_goals_router.post("/pi-goals/generate")
async def generate_ai_pi_goals(
    request: PIGoalGenerateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Generate PI goals using LLM analysis of epics and save to database.
    
    This endpoint:
    1. Gets current PI if not provided
    2. Fetches all epics for the PI (optionally filtered by team)
    3. Sends formatted prompt to LLM
    4. Parses LLM response and saves all goals to database with status "Draft-AI"
    5. Returns the generated goals
    
    Args:
        request: PIGoalGenerateRequest with pi, team_name, isGroup, and quarter
        conn: Database connection
        
    Returns:
        JSON response with overall_goals and team_goals (saved to database)
    """
    try:
        # Extract parameters from request
        pi = request.pi
        team_name = request.team_name
        isGroup = request.isGroup
        quarter = request.quarter
        
        # Log all POST parameters received
        logger.info(f"POST /pi-goals/generate - Received parameters: pi={pi}, team_name={team_name}, isGroup={isGroup}, quarter={quarter}")
        logger.info(f"POST /pi-goals/generate - isGroup type: {type(isGroup)}, isGroup value: {isGroup}")
        
        # Step 1: Resolve PI (get current if not provided)
        resolved_pi = pi
        if not resolved_pi:
            resolved_pi = get_current_pi(conn)
            if not resolved_pi:
                raise HTTPException(
                    status_code=404,
                    detail="No current PI found and no PI parameter provided"
                )
        
        logger.info(f"Using PI: {resolved_pi}")
        
        # Step 2: Resolve team names (handles group to teams translation)
        team_names_list = None
        if team_name:
            team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
            logger.info(f"Resolved team names: {team_names_list}")
        
        # Step 3: Fetch epics for the PI
        epics = fetch_epics_for_pi(resolved_pi, team_names_list, conn)
        
        if not epics:
            return {
                "success": True,
                "data": {
                    "pi": resolved_pi,
                    "overall_goals": [],
                    "team_goals": []
                },
                "message": f"No epics found for PI {resolved_pi}"
            }
        
        logger.info(f"Found {len(epics)} epics for PI {resolved_pi}")
        
        # Step 4: Build LLM prompt (reads from database and adds PI name and epics)
        prompt = build_llm_prompt(resolved_pi, epics, conn)
        
        # Step 5: Call LLM service
        metadata = {
            "job_type": "pi_goals",
            "pi": resolved_pi,
            "team_name": team_name,
            "isGroup": isGroup
        }
        
        logger.info(f"Calling LLM service for PI goals (prompt length: {len(prompt)} chars)")
        llm_response = await call_llm_service_process_single(
            prompt=prompt,
            system_prompt=None,
            metadata=metadata
        )
        
        # Step 6: Extract response text
        if not isinstance(llm_response, dict):
            logger.error(f"LLM service returned unexpected response type: {type(llm_response)}")
            raise HTTPException(
                status_code=502,
                detail="LLM service returned invalid response format"
            )
        
        # Try to get response from data.response first, then fallback to response
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
        
        # Step 7: Parse JSON from response - simple direct parsing
        parsed_data = extract_json_from_response(response_text)
        
        if not parsed_data:
            logger.error(f"Failed to parse JSON from LLM response.")
            logger.error(f"Response preview (first 500 chars): {response_text[:500]}")
            raise HTTPException(
                status_code=500,
                detail="LLM response is not valid JSON. The response should be only JSON with no additional text."
            )
        
        # Step 8: Validate response structure
        if not validate_llm_response(parsed_data):
            logger.error(f"Invalid LLM response structure: {parsed_data}")
            raise HTTPException(
                status_code=500,
                detail="LLM response does not match expected structure"
            )
        
        # Step 9: Save goals to database and build response
        logger.info("Successfully parsed and validated LLM response")
        
        # Determine group_name from parameters (if isGroup=true, team_name parameter is actually group_name)
        group_name_for_saving = None
        if isGroup:
            if not team_name:
                raise HTTPException(
                    status_code=400,
                    detail="team_name parameter is required when isGroup=true (team_name should be the group name)"
                )
            group_name_for_saving = team_name
            logger.info(f"isGroup=true: Setting group_name_for_saving={group_name_for_saving}")
        else:
            logger.info(f"isGroup=false: group_name_for_saving will be None")
        
        saved_overall_goals = []
        saved_team_goals_by_team = {}  # Group by team_name for response format
        
        # Save overall goals using UPSERT (ai=true)
        overall_goals_from_llm = parsed_data.get("overall_goals", [])
        logger.info(f"Upserting {len(overall_goals_from_llm)} overall goals (will become {'group' if group_name_for_saving else 'overall'} goals)")
        for goal_index, goal in enumerate(overall_goals_from_llm, start=1):
            try:
                goal_data = {
                    "pi_name": resolved_pi,
                    "team_name": None,
                    "group_name": group_name_for_saving,  # Set if isGroup=true
                    "goal_text": goal.get("goal", ""),
                    "epic_keys": goal.get("epic_keys", []),
                    "status": "Draft-AI",
                    "ai": True,  # AI-generated goal
                    "is_overall": True,  # Flag for goal_type determination
                    "goal_number": goal_index  # Assign goal_number based on order (1, 2, 3, ...)
                }
                logger.info(f"Goal {goal_index} data before upsert: group_name={goal_data.get('group_name')}, is_overall={goal_data.get('is_overall')}")
                saved_goal = upsert_pi_goal(goal_data, conn)
                if saved_goal:
                    saved_overall_goals.append(saved_goal)
                    logger.info(f"Successfully upserted overall goal with ID {saved_goal.get('id')} (goal_number={goal_index})")
                else:
                    logger.warning("upsert_pi_goal returned None for overall goal")
            except Exception as e:
                logger.error(f"Error upserting overall goal: {e}", exc_info=True)
        
        # Save team goals using UPSERT (ai=true)
        # When isGroup=true, team goals should NOT have group_name (mutually exclusive)
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
                    goal_data = {
                        "pi_name": resolved_pi,
                        "team_name": team_name_from_llm,
                        "group_name": None,  # Team goals never have group_name (mutually exclusive)
                        "goal_text": goal.get("goal", ""),
                        "epic_keys": goal.get("epic_keys", []),
                        "status": "Draft-AI",
                        "ai": True,  # AI-generated goal
                        "is_overall": False,  # Flag for goal_type determination
                        "goal_number": goal_index  # Assign goal_number based on order (1, 2, 3, 4)
                    }
                    saved_goal = upsert_pi_goal(goal_data, conn)
                    if saved_goal:
                        saved_team_goals_by_team[team_name_from_llm].append(saved_goal)
                        logger.info(f"Successfully upserted team goal with ID {saved_goal.get('id')} for team {team_name_from_llm} (goal_number={goal_index})")
                    else:
                        logger.warning(f"upsert_pi_goal returned None for team goal of {team_name_from_llm}")
                except Exception as e:
                    logger.error(f"Error upserting team goal for {team_name_from_llm}: {e}", exc_info=True)
        
        # Collect all saved goals into a flat list for formatting
        all_saved_goals = saved_overall_goals.copy()
        for team_name_key, goals_list in saved_team_goals_by_team.items():
            all_saved_goals.extend(goals_list)
        
        logger.info(f"Response summary: {len(saved_overall_goals)} overall goals, {len(saved_team_goals_by_team)} teams with goals")
        
        # Print per team goals (as requested)
        for team_name, goals in saved_team_goals_by_team.items():
            logger.info(f"Team {team_name}: {len(goals)} goal(s)")
            for i, goal in enumerate(goals, 1):
                goal_title = goal.get("goal_text", "N/A")
                epic_count = len(goal.get("epic_keys", []))
                logger.info(f"  Goal {i}: {goal_title} ({epic_count} epics)")
        
        # Return generic success response (no goal details)
        return {
            "success": True,
            "message": f"Generated and saved PI goals for {resolved_pi}"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating PI goals: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate PI goals: {str(e)}"
        )


@pi_goals_router.get("/pi-goals")
async def get_pi_goals(
    pi: str = Query(..., description="PI (mandatory)"),
    ai: Optional[bool] = Query(None, description="Filter by AI-generated goals (None = all goals, True = AI only, False = user only)"),
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get PI goals with optional filters.
    
    Args:
        pi: PI name (mandatory)
        ai: Filter by AI-generated goals (default: False)
        team_name: Team name or group name filter
        isGroup: If true, team_name is treated as a group name
        conn: Database connection
        
    Returns:
        JSON response with formatted goals structure:
        - If isGroup=true: {"group_goals": [...], "team_goals": [...]}
        - If team_name only: {"team_goals": [...]}
        - If no filter: {"overall_goals": [...], "team_goals": [...]}
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Resolve team names if team_name is provided
        team_names_list = None
        group_name_for_response = None
        
        if team_name:
            if isGroup:
                # team_name is actually a group name
                group_name_for_response = team_name
                team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
            else:
                # Single team
                team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Fetch goals from database with proper SQL filtering
        filtered_goals = []
        
        if isGroup and group_name_for_response:
            # For group filter: fetch group goals and team goals for teams in group
            # Fetch group goals
            group_goals = get_pi_goals_filtered(
                pi=pi,
                goal_type="group",
                group_name=group_name_for_response,
                ai=ai,
                limit=100,
                conn=conn
            )
            filtered_goals.extend(group_goals)
            
            # Fetch team goals for teams in the group
            if team_names_list:
                team_goals = get_pi_goals_filtered(
                    pi=pi,
                    goal_type="team",
                    team_names_list=team_names_list,
                    ai=ai,
                    limit=1000,
                    conn=conn
                )
                filtered_goals.extend(team_goals)
        elif team_name and not isGroup:
            # For team filter: fetch only goals for this team
            filtered_goals = get_pi_goals_filtered(
                pi=pi,
                goal_type="team",
                team_name=team_name,
                ai=ai,
                limit=100,
                conn=conn
            )
        else:
            # No filter: fetch overall and team goals (but not group goals)
            overall_goals = get_pi_goals_filtered(
                pi=pi,
                goal_type="overall",
                ai=ai,
                limit=100,
                conn=conn
            )
            team_goals = get_pi_goals_filtered(
                pi=pi,
                goal_type="team",
                ai=ai,
                limit=1000,
                conn=conn
            )
            filtered_goals = overall_goals + team_goals
        
        # Enrich epic_keys with issue details (status, summary, progress_percent)
        enriched_goals = enrich_epic_keys_with_issue_details(filtered_goals, conn)
        
        # Format response using helper function
        response_data = format_goals_response(
            goals=enriched_goals,
            pi=pi,
            group_name=group_name_for_response,
            team_name=team_name if not isGroup else None,
            isGroup=isGroup
        )
        
        return {
            "success": True,
            "data": response_data,
            "message": f"Retrieved PI goals for {pi}"
        }
    except HTTPException as e:
        logger.error(f"Error fetching PI goals - HTTPException: status_code={e.status_code}, detail={e.detail}")
        raise
    except Exception as e:
        logger.error(f"Error fetching PI goals: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch PI goals: {str(e)}"
        )


@pi_goals_router.post("/pi-goals")
async def create_pi_goal_endpoint(
    request: PIGoalCreateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Manually create a PI goal.
    
    goal_type is automatically determined:
    - If team_name provided → goal_type = 'team'
    - If group_name provided → goal_type = 'group'
    - If neither → goal_type = 'overall'
    
    Args:
        request: PIGoalCreateRequest with goal data
        conn: Database connection
        
    Returns:
        JSON response with created goal
    """
    try:
        # Determine if this is an overall goal
        is_overall = not request.team_name
        
        goal_data = {
            "pi_name": request.pi,  # Map 'pi' from request to 'pi_name' for database
            "team_name": request.team_name,
            "group_name": request.group_name,
            "goal_text": request.goal_text,
            "epic_keys": request.epic_keys,
            "status": request.status or "Draft",
            "priority_bv": request.priority_bv,
            "ai": False,  # User-created goals always have ai=false
            "is_overall": is_overall
        }
        
        created_goal = create_pi_goal(goal_data, conn)
        
        return {
            "success": True,
            "data": {
                "goal": created_goal
            },
            "message": f"PI goal created with ID {created_goal.get('id')}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating PI goal: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create PI goal: {str(e)}"
        )


@pi_goals_router.patch("/pi-goals/ai-to-user")
async def move_goals_ai_to_user_endpoint(
    request: MoveGoalsAIToUserRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Move multiple PI goals from AI-generated to user-modified by setting ai = False and status = 'Draft'.
    
    Args:
        request: MoveGoalsAIToUserRequest with list of goal_ids
        conn: Database connection
        
    Returns:
        JSON response with count of updated goals
    """
    try:
        goal_ids = request.goal_ids
        
        if not goal_ids:
            raise HTTPException(
                status_code=400,
                detail="goal_ids list cannot be empty"
            )
        
        # Build placeholders for IN clause
        placeholders = ", ".join([f":goal_id_{i}" for i in range(len(goal_ids))])
        params = {f"goal_id_{i}": goal_id for i, goal_id in enumerate(goal_ids)}
        
        # Single SQL UPDATE to set ai = False and status = 'Draft' for all provided goal_ids
        query = text(f"""
            UPDATE {config.PI_GOALS_TABLE}
            SET ai = false, status = 'Draft', updated_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
        """)
        
        result = conn.execute(query, params)
        rows_updated = result.rowcount
        conn.commit()
        
        logger.info(f"PATCH /pi-goals/ai-to-user - Updated {rows_updated} goals from AI to user (ai=false, status=Draft) (requested {len(goal_ids)} goal_ids)")
        
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


@pi_goals_router.patch("/pi-goals/{goal_id}")
async def update_pi_goal_endpoint(
    goal_id: int,
    request: PIGoalUpdateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Update an existing PI goal.
    
    If team_name or group_name is updated, goal_type is recalculated automatically.
    The ai column is always set to False when a goal is updated (even if it was True before),
    as user modifications mark the goal as user-modified rather than AI-generated.
    
    Args:
        goal_id: ID of the goal to update
        request: PIGoalUpdateRequest with fields to update
        conn: Database connection
        
    Returns:
        JSON response with updated goal
    """
    try:
        updates = request.model_dump(exclude_unset=True)
        
        # Log all parameters
        logger.info(f"PATCH /pi-goals/{goal_id} - Request parameters: goal_id={goal_id}, updates={updates}")
        logger.info(f"PATCH /pi-goals/{goal_id} - Full request model: {request.model_dump()}")
        
        # Map 'pi' from request to 'pi_name' for database
        if "pi" in updates:
            updates["pi_name"] = updates.pop("pi")
        
        logger.info(f"PATCH /pi-goals/{goal_id} - Processed updates (after mapping): {updates}")
        
        if not updates:
            raise HTTPException(
                status_code=400,
                detail="No fields provided for update"
            )
        
        updated_goal = update_pi_goal_by_id(goal_id, updates, conn)
        
        if not updated_goal:
            raise HTTPException(
                status_code=404,
                detail=f"PI goal with ID {goal_id} not found"
            )
        
        return {
            "success": True,
            "data": {
                "goal": updated_goal
            },
            "message": f"PI goal {goal_id} updated successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating PI goal {goal_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update PI goal: {str(e)}"
        )


@pi_goals_router.delete("/pi-goals/{goal_id}")
async def delete_pi_goal_endpoint(
    goal_id: int,
    conn: Connection = Depends(get_db_connection)
):
    """
    Delete a PI goal by ID.
    
    Args:
        goal_id: ID of the goal to delete
        conn: Database connection
        
    Returns:
        JSON response confirming deletion
    """
    try:
        deleted = delete_pi_goal_by_id(goal_id, conn)
        
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"PI goal with ID {goal_id} not found"
            )
        
        return {
            "success": True,
            "message": f"PI goal {goal_id} deleted successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting PI goal {goal_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete PI goal: {str(e)}"
        )


