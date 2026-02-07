"""
Validation Summary Service - KPI Metrics Endpoint
Returns count-only summaries for validation issues in metric card format.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
from database_connection import get_db_connection
from database_team_metrics import resolve_team_names_from_filter
from config import get_jira_url
from database_validation_queries import (
    query_old_bugs,
    query_stuck_in_progress,
    query_dragged_sprints,
    query_epic_health_issues
)
from global_settings_loader import settings
import config

router = APIRouter()


@router.get("/issues/validations/summary")
async def get_validation_summary(
    days_back: int = Query(settings.DEFAULT_VALIDATION_DAYS_BACK, ge=1, le=365, description="Number of days to look back"),
    old_bugs_threshold_days: int = Query(settings.OLD_BUGS_THRESHOLD_DAYS, ge=1, description="Days threshold for old bugs"),
    stuck_stories_threshold_days: int = Query(settings.STUCK_STORIES_THRESHOLD_DAYS, ge=1, description="Days threshold for stuck stories"),
    stuck_epics_threshold_days: int = Query(settings.STUCK_EPICS_THRESHOLD_DAYS, ge=1, description="Days threshold for stuck epics"),
    dragged_sprints_threshold: int = Query(settings.DRAGGED_SPRINTS_THRESHOLD, ge=1, description="Sprints threshold for dragged issues"),
    epic_max_children_threshold: int = Query(settings.EPIC_MAX_CHILDREN_THRESHOLD, ge=1, description="Max children threshold for epics"),
    team_name: Optional[str] = Query(None, description="Team or group name"),
    isGroup: bool = Query(False, description="If true, team_name is treated as group"),
    pi: Optional[str] = Query(None, description="PI name or comma-separated list (for epics)"),
    conn: Connection = Depends(get_db_connection)
) -> List[Dict[str, Any]]:
    """
    Get validation summary metrics in KPI card format.
    
    Returns 5 metric cards showing counts only (no issue details):
    1. old_bugs: Unresolved bugs open too long
    2. stuck_stories: Stories stuck in progress (hierarchy_level = 0)
    3. stuck_epics: Epics stuck in progress
    4. dragged_sprints: Issues in too many sprints
    5. epic_health: Epics with child issues problems
    
    Supports filtering by team/group and PI (PI applies only to epic validations).
    Response follows GitHub service KPI structure matching /team-metrics endpoints.
    Each card includes an action that links to the detailed validation report.
    """
    # Resolve team names from filter
    team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn) if team_name else None
    
    # Parse PI list
    pi_names_list = None
    if pi:
        pi_names_list = [p.strip() for p in pi.split(',') if p.strip()]
    
    # Get counts using the same query functions with count_only=True
    old_bugs_count = query_old_bugs(
        conn, days_back, old_bugs_threshold_days, 
        count_only=True, team_names=team_names_list
    )
    
    stuck_stories_count = query_stuck_in_progress(
        conn, days_back, stuck_stories_threshold_days, stuck_epics_threshold_days,
        count_only=True, hierarchy_level=0, team_names=team_names_list, pi_names=None
    )
    
    stuck_epics_count = query_stuck_in_progress(
        conn, days_back, stuck_stories_threshold_days, stuck_epics_threshold_days,
        count_only=True, hierarchy_level=1, team_names=team_names_list, pi_names=pi_names_list
    )
    
    dragged_count = query_dragged_sprints(
        conn, days_back, dragged_sprints_threshold, 
        count_only=True, team_names=team_names_list
    )
    
    epic_health_count = query_epic_health_issues(
        conn, days_back, epic_max_children_threshold, 
        count_only=True, team_names=team_names_list, pi_names=pi_names_list
    )
    
    # Build common filter params for action links
    common_filter_params = {}
    if team_name:
        common_filter_params["team_name"] = team_name
        common_filter_params["isGroup"] = isGroup
    if pi_names_list:
        common_filter_params["pi"] = ','.join(pi_names_list)
    
    # Build metric cards in GitHub service KPI format
    metrics = [
        {
            "metric_id": "old_bugs",
            "label": "Aged Bugs",
            "value": str(old_bugs_count),
            "tier_status": "",
            "description": f"Unresolved bugs open for {old_bugs_threshold_days}+ days",
            "tooltip": f"Bugs in non-Done status category that haven't been resolved for {old_bugs_threshold_days}+ days",
            "alternative_text": f"Bugs unresolved {old_bugs_threshold_days}+ days",
            "trend": None,
            "action": {
                "type": "report",
                "report_ids": ["validation-issues"],
                "params": {
                    "validation_type": "old_bugs",
                    "days_back": days_back,
                    "old_bugs_threshold_days": old_bugs_threshold_days,
                    **common_filter_params
                }
            }
        },
        {
            "metric_id": "stuck_stories",
            "label": "Stuck Issues (not epics)",
            "value": str(stuck_stories_count),
            "tier_status": "",
            "description": f"Issues that are not epics that are in Progress status category for {stuck_stories_threshold_days}+ days",
            "tooltip": f"Issues that are not epics that are stuck in In Progress status category for {stuck_stories_threshold_days}+ days",
            "alternative_text": f"In progress {stuck_stories_threshold_days}+ days",
            "trend": None,
            "action": {
                "type": "report",
                "report_ids": ["validation-issues"],
                "params": {
                    "validation_type": "stuck_in_progress",
                    "days_back": days_back,
                    "stuck_stories_threshold_days": stuck_stories_threshold_days,
                    "stuck_epics_threshold_days": stuck_epics_threshold_days,
                    "hierarchy_level": 0,
                    **common_filter_params
                }
            }
        },
        {
            "metric_id": "stuck_epics",
            "label": "Stuck Epics",
            "value": str(stuck_epics_count),
            "tier_status": "",
            "description": f"Epics in progress for {stuck_epics_threshold_days}+ days",
            "tooltip": f"Epics stuck in 'In Progress' status for {stuck_epics_threshold_days}+ days",
            "alternative_text": f"In progress {stuck_epics_threshold_days}+ days",
            "trend": None,
            "action": {
                "type": "report",
                "report_ids": ["validation-issues"],
                "params": {
                    "validation_type": "stuck_in_progress",
                    "days_back": days_back,
                    "stuck_stories_threshold_days": stuck_stories_threshold_days,
                    "stuck_epics_threshold_days": stuck_epics_threshold_days,
                    "hierarchy_level": 1,
                    **common_filter_params
                }
            }
        },
        {
            "metric_id": "dragged_sprints",
            "label": "Dragged Issues",
            "value": str(dragged_count),
            "tier_status": "",
            "description": f"Issues in {dragged_sprints_threshold}+ sprints",
            "tooltip": f"Issues that have been moved/dragged through {dragged_sprints_threshold}+ sprints (excluding issues with status category 'Done')",
            "alternative_text": f"Moved {dragged_sprints_threshold}+ Sprints",
            "trend": None,
            "action": {
                "type": "report",
                "report_ids": ["validation-issues"],
                "params": {
                    "validation_type": "dragged_sprints",
                    "days_back": days_back,
                    "dragged_sprints_threshold": dragged_sprints_threshold,
                    **common_filter_params
                }
            }
        },
        {
            "metric_id": "epic_health",
            "label": "Epic Health",
            "value": str(epic_health_count),
            "tier_status": "",
            "description": "Epics with child problems",
            "tooltip": f"Epics marked Done with open children OR epics with {epic_max_children_threshold}+ children",
            "alternative_text": "Epics that are too big or have status conflict",
            "trend": None,
            "action": {
                "type": "report",
                "report_ids": ["validation-issues"],
                "params": {
                    "validation_type": "epic_health",
                    "days_back": days_back,
                    "epic_max_children_threshold": epic_max_children_threshold,
                    **common_filter_params
                }
            }
        }
    ]
    
    return metrics

