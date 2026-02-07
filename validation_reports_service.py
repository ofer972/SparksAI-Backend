"""
Validation Reports Service - API endpoint for issue validation checks.
"""

from fastapi import APIRouter, Query, Depends
from typing import Optional, Dict, Any, List
from datetime import datetime
from sqlalchemy.engine import Connection

from database_connection import get_db_connection
from database_team_metrics import resolve_team_names_from_filter
from config import get_jira_url
from validation_logic import (
    validate_old_bugs,
    validate_dragged_sprints,
    validate_epic_health,
    validate_stuck_in_progress
)
from global_settings_loader import settings
import config

router = APIRouter()

VALIDATION_FUNCTIONS = {
    'old_bugs': validate_old_bugs,
    'dragged_sprints': validate_dragged_sprints,
    'epic_health': validate_epic_health,
    'stuck_in_progress': validate_stuck_in_progress
}


@router.get("/issues/validations/issues")
async def get_validation_issues(
    days_back: int = Query(settings.DEFAULT_VALIDATION_DAYS_BACK, ge=1, le=365, description="Days to look back for updated issues"),
    validation_type: Optional[str] = Query(
        None, 
        description="Specific validation type (old_bugs, dragged_sprints, epic_health, stuck_in_progress). Omit to run all."
    ),
    old_bugs_threshold_days: int = Query(settings.OLD_BUGS_THRESHOLD_DAYS, ge=1, description="Days threshold for old bugs"),
    stuck_stories_threshold_days: int = Query(settings.STUCK_STORIES_THRESHOLD_DAYS, ge=1, description="Days threshold for stuck stories (hierarchy_level=0)"),
    stuck_epics_threshold_days: int = Query(settings.STUCK_EPICS_THRESHOLD_DAYS, ge=1, description="Days threshold for stuck epics"),
    dragged_sprints_threshold: int = Query(settings.DRAGGED_SPRINTS_THRESHOLD, ge=1, description="Number of sprints threshold"),
    epic_max_children_threshold: int = Query(settings.EPIC_MAX_CHILDREN_THRESHOLD, ge=1, description="Max children threshold for epic health"),
    team_name: Optional[str] = Query(None, description="Team or group name"),
    isGroup: bool = Query(False, description="If true, team_name is treated as group"),
    pi: Optional[str] = Query(None, description="PI name or comma-separated list (for epics)"),
    hierarchy_level: Optional[int] = Query(None, ge=0, description="Filter by hierarchy level (0 for stories/bugs/tasks, 1 for epics). Only applies to stuck_in_progress validation."),
    conn: Connection = Depends(get_db_connection)
) -> Dict[str, Any]:
    """
    Get validation issues report.
    Returns issues that fail various validation checks.
    Supports filtering by team/group and PI (PI applies only to epic validations).
    """
    # Resolve team names from filter
    team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn) if team_name else None
    
    # Parse PI list
    pi_names_list = None
    if pi:
        pi_names_list = [p.strip() for p in pi.split(',') if p.strip()]
    
    if validation_type:
        validations_to_run = {validation_type: VALIDATION_FUNCTIONS[validation_type]}
    else:
        validations_to_run = VALIDATION_FUNCTIONS
    
    validation_results = []
    for val_type, val_func in validations_to_run.items():
        result = val_func(
            conn=conn,
            days_back=days_back,
            old_bugs_threshold=old_bugs_threshold_days,
            stories_threshold=stuck_stories_threshold_days,
            epics_threshold=stuck_epics_threshold_days,
            dragged_threshold=dragged_sprints_threshold,
            epic_max_children=epic_max_children_threshold,
            team_names=team_names_list,
            pi_names=pi_names_list,
            hierarchy_level=hierarchy_level
        )
        validation_results.append(result)
    
    response = {
        "days_back": days_back,
        "run_timestamp": datetime.utcnow().isoformat() + "Z",
        "validations": validation_results,
        "meta": {}
    }
    
    # Add filter metadata
    if team_name:
        if isGroup:
            response["group_name"] = team_name
            response["teams_in_group"] = team_names_list
        else:
            response["team_name"] = team_name
    
    if pi_names_list:
        response["pi_names"] = pi_names_list
    
    # Add JIRA URL to metadata (same pattern as reports_service.py)
    jira_settings = get_jira_url(conn=conn)
    if jira_settings.get("url"):
        response["meta"]["jira_url"] = jira_settings["url"]
    
    return response

