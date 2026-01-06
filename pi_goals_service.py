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
from database_general import get_prompt_by_email_and_name
from agent_llm_service import call_llm_service_process_single
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


@pi_goals_router.get("/pi-goals")
async def get_pi_goals(
    pi: Optional[str] = Query(None, description="PI name (optional, uses current PI if not provided)"),
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    quarter: Optional[str] = Query(None, description="Quarter parameter (reserved for future use)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Endpoint to generate PI goals using LLM analysis of epics.
    
    This endpoint:
    1. Gets current PI if not provided
    2. Fetches all epics for the PI (optionally filtered by team)
    3. Sends formatted prompt to LLM
    4. Parses and returns LLM response with PI goals
    
    Args:
        pi: Optional PI name (uses current PI if not provided)
        team_name: Optional team name or group name filter
        isGroup: If true, team_name is treated as a group name
        quarter: Reserved for future use
        conn: Database connection
        
    Returns:
        JSON response with overall_goals and team_goals
    """
    try:
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
        
        # Step 9: Build and return response
        logger.info("Successfully parsed and validated LLM response")
        
        # Print per team goals (as requested)
        if "team_goals" in parsed_data:
            for team_goal in parsed_data["team_goals"]:
                team_name = team_goal.get("team_name", "Unknown")
                goals = team_goal.get("goals", [])
                logger.info(f"Team {team_name}: {len(goals)} goal(s)")
                for i, goal in enumerate(goals, 1):
                    goal_title = goal.get("goal", "N/A")
                    epic_count = len(goal.get("epic_keys", []))
                    logger.info(f"  Goal {i}: {goal_title} ({epic_count} epics)")
        
        return {
            "success": True,
            "data": {
                "pi": resolved_pi,
                "overall_goals": parsed_data.get("overall_goals", []),
                "team_goals": parsed_data.get("team_goals", [])
            },
            "message": f"Generated PI goals for {resolved_pi}"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating PI goals: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate PI goals: {str(e)}"
        )

