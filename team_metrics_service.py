"""
Team Metrics Service - REST API endpoints for team metrics.

This service provides endpoints for retrieving team metrics.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import Dict, Any, Optional, List
from datetime import date, datetime
import logging
from database_connection import get_db_connection
from database_team_metrics import (
    get_team_avg_sprint_metrics,
    get_team_count_in_progress,
    get_team_current_sprint_progress,
    get_sprints_with_total_issues_db,
    get_sprint_burndown_data_db,
    get_closed_sprints_data_db,
    get_issues_trend_data_db,
    get_average_sprint_velocity_per_team,
    resolve_team_names_from_filter,
    select_sprint_for_teams,
    get_cycle_time_for_period,
    get_cycle_time_for_period_by_issue_type,
    get_open_bugs_with_trend
)
from database_pi import get_pi_participating_teams_db
from pis_service import validate_pi
import config

logger = logging.getLogger(__name__)

team_metrics_router = APIRouter()

# ============================================================================
# CYCLE TIME TIER THRESHOLDS (configurable in one place)
# ============================================================================

# Story/Sprint Issue Cycle Time Tiers (in days)
STORY_CYCLE_TIME_ELITE = 10    # <= 10 days = Elite
STORY_CYCLE_TIME_HIGH = 20     # 10-20 days = High
STORY_CYCLE_TIME_MEDIUM = 30   # 20-30 days = Medium
                               # > 30 days = Low

# Epic Cycle Time Tiers (in days)
EPIC_CYCLE_TIME_ELITE = 40     # <= 40 days = Elite/High
EPIC_CYCLE_TIME_MEDIUM = 75    # 40-75 days = Medium
                               # > 75 days = Low

# Cycle Time Measurement Periods (in days)
CYCLE_TIME_PERIOD_DAYS = 30              # Stories: 30-day period
EPIC_CYCLE_TIME_PERIOD_DAYS = 90         # Epics: 90-day period (3 months)

# ============================================================================
# WIP TIER THRESHOLDS (configurable in one place)
# ============================================================================

# Sprint WIP Tiers (percentage of total sprint issues)
SPRINT_WIP_HIGH_THRESHOLD = 30      # <= 30% = High (green)
SPRINT_WIP_MEDIUM_THRESHOLD = 50    # 30-50% = Medium (yellow)
                                    # > 50% = Low (red)

# Sprint Completion & Predictability Tiers (percentage completed/predictability)
SPRINT_COMPLETION_HIGH_THRESHOLD = 80    # >= 80% = High (green)
SPRINT_COMPLETION_MEDIUM_THRESHOLD = 60  # 60-79.9% = Medium (yellow)
                                         # < 60% = Low (red)

# Epic WIP Tiers (percentage of total epics in PI)
EPIC_WIP_HIGH_THRESHOLD = 30        # <= 30% = High (green)
EPIC_WIP_MEDIUM_THRESHOLD = 60      # 30-60% = Medium (yellow)
                                    # > 60% = Low (red)

# PI Completion Tiers (percentage completed) - More relaxed than Sprint
PI_COMPLETION_HIGH_THRESHOLD = 75    # >= 75% = High (green)
PI_COMPLETION_MEDIUM_THRESHOLD = 55  # 55-74.9% = Medium (yellow)
                                     # < 55% = Low (red)

# ============================================================================
# OPEN BUGS KPI CONFIGURATION
# ============================================================================
# The Open Bugs KPI uses intelligent tier-based logic to prevent false alarms
# from small number fluctuations while still alerting when there's a real problem.
#
# HOW IT WORKS:
# 1. Tier Calculation: Thresholds scale with team count (6/15 bugs per team)
#    - Single team: High ≤6, Medium 7-15, Low >15
#    - 5-team group: High ≤30, Medium 31-75, Low >75
#
# 2. Tier-Based Trend Display:
#    - HIGH TIER (green): Shows neutral/flat trend (no red arrows)
#      → At low bug counts, small changes (+1, +2) are normal noise
#      → Users don't need alarms when health is excellent
#    
#    - MEDIUM/LOW TIER (yellow/red): Shows full trend with arrows
#      → Already have too many bugs - direction matters!
#      → Users need to know if improving (green ↓) or worsening (red ↑)
#
# This prevents scenarios like "2 bugs → 4 bugs = RED ALARM!" while still
# showing meaningful trends when bug counts are actually problematic.
# ============================================================================

# Bug Issue Types (configurable - different orgs may use different names)
BUG_ISSUE_TYPES = ["Bug", "Defect"]      # Add more as needed: "Incident", "Issue", etc.

# Open Bugs Measurement Period
OPEN_BUGS_TREND_PERIOD_DAYS = 30         # Period to calculate bug creation/resolution trend

# Open Bugs Tier Thresholds (PER TEAM - multiplied by team count for groups)
OPEN_BUGS_HIGH_PER_TEAM = 6              # <= 6 bugs per team = High (green)
OPEN_BUGS_MEDIUM_PER_TEAM = 15           # 7-15 bugs per team = Medium (yellow)
                                         # > 15 bugs per team = Low (red)


def validate_team_name(team_name: str) -> str:
    """
    Validate team name (basic validation only).
    """
    if not team_name or not isinstance(team_name, str):
        raise HTTPException(status_code=400, detail="Team name is required and must be a string")
    
    validated = team_name.strip()
    
    if not validated:
        raise HTTPException(status_code=400, detail="Team name cannot be empty")
    
    if len(validated) > 100:  # Reasonable length limit
        raise HTTPException(status_code=400, detail="Team name is too long (max 100 characters)")
    
    return validated

def validate_group_name(group_name: str) -> str:
    """
    Validate group name (basic validation only).
    """
    if not group_name or not isinstance(group_name, str):
        raise HTTPException(status_code=400, detail="Group name is required and must be a string")
    
    validated = group_name.strip()
    
    if not validated:
        raise HTTPException(status_code=400, detail="Group name cannot be empty")
    
    if len(validated) > 100:  # Reasonable length limit
        raise HTTPException(status_code=400, detail="Group name is too long (max 100 characters)")
    
    return validated


def validate_sprint_count(sprint_count: int) -> int:
    """
    Validate and sanitize sprint count.
    """
    if not isinstance(sprint_count, int) or sprint_count <= 0:
        raise HTTPException(status_code=400, detail="Sprint count must be a positive integer")
    if sprint_count > 20:  # Reasonable max limit
        raise HTTPException(status_code=400, detail="Sprint count cannot exceed 20")
    return sprint_count


def get_cycle_time_status(cycle_time: float) -> str:
    """
    Determine cycle time status based on value.
    
    Args:
        cycle_time: Cycle time in days
    
    Returns:
        "green" if cycle_time < 10
        "yellow" if 10 <= cycle_time <= 15
        "red" if cycle_time > 15
    """
    if cycle_time < 10:
        return "green"
    elif cycle_time <= 15:
        return "yellow"
    else:
        return "red"


def get_cycle_time_tier(cycle_time: float) -> str:
    """
    Determine tier for story/sprint cycle time.
    Uses 3-tier system (high/medium/low) for Sprint metrics.
    
    Tiers (based on STORY_CYCLE_TIME constants):
    - high: <= 10 days (best performance)
    - medium: 10-30 days
    - low: > 30 days
    
    Args:
        cycle_time: Cycle time in days
    
    Returns:
        Tier string: 'high', 'medium', or 'low'
    """
    if cycle_time <= 0:
        return "low"  # No data or invalid = low tier
    
    if cycle_time <= STORY_CYCLE_TIME_ELITE:
        return "high"  # Best tier for Sprint metrics is "high" (green)
    elif cycle_time <= STORY_CYCLE_TIME_MEDIUM:
        return "medium"  # Acceptable performance
    else:
        return "low"  # Needs improvement


def get_epic_cycle_time_tier(cycle_time: float) -> str:
    """
    Determine tier for epic cycle time.
    Uses 3-tier system (high/medium/low) for PI metrics.
    
    Tiers (based on EPIC_CYCLE_TIME constants):
    - high: <= 40 days (best performance)
    - medium: 40-75 days
    - low: > 75 days
    
    Args:
        cycle_time: Average epic cycle time in days
        
    Returns:
        Tier string: 'high', 'medium', or 'low'
    """
    if cycle_time <= 0:
        return "low"  # No data or invalid = low tier
    
    if cycle_time <= EPIC_CYCLE_TIME_ELITE:
        return "high"  # Best tier for PI metrics is "high" (green)
    elif cycle_time <= EPIC_CYCLE_TIME_MEDIUM:
        return "medium"
    else:
        return "low"


def get_sprint_wip_tier(wip_percentage: float) -> str:
    """
    Determine tier for sprint WIP percentage.
    Uses 3-tier system (high/medium/low) for Sprint metrics.
    
    Tiers:
    - high: <= 30% (healthy WIP)
    - medium: 30-50% (moderate WIP)
    - low: > 50% (too much WIP - bottleneck risk)
    
    Args:
        wip_percentage: WIP as percentage of total sprint issues
    
    Returns:
        Tier string: 'high', 'medium', or 'low'
    """
    if wip_percentage < 0:
        return "low"  # Invalid data
    
    if wip_percentage <= SPRINT_WIP_HIGH_THRESHOLD:
        return "high"  # Healthy WIP
    elif wip_percentage <= SPRINT_WIP_MEDIUM_THRESHOLD:
        return "medium"  # Moderate WIP
    else:
        return "low"  # Too much WIP


def get_epic_wip_tier(wip_percentage: float) -> str:
    """
    Determine tier for epic WIP percentage.
    Uses 3-tier system (high/medium/low) for PI metrics.
    
    Tiers:
    - high: <= 30% (healthy epic WIP)
    - medium: 30-60% (moderate epic WIP)
    - low: > 60% (too much epic WIP - focus issues)
    
    Args:
        wip_percentage: WIP as percentage of total epics in PI
    
    Returns:
        Tier string: 'high', 'medium', or 'low'
    """
    if wip_percentage < 0:
        return "low"  # Invalid data
    
    if wip_percentage <= EPIC_WIP_HIGH_THRESHOLD:
        return "high"  # Healthy epic WIP
    elif wip_percentage <= EPIC_WIP_MEDIUM_THRESHOLD:
        return "medium"  # Moderate epic WIP
    else:
        return "low"  # Too much epic WIP


def get_pi_completion_tier(
    percent_completed: float,
    start_date: date = None,
    end_date: date = None
) -> Optional[str]:
    """
    Determine tier for PI completion percentage.
    Returns None (no tier) for first 15% of PI, then uses timeline-based logic.
    
    Early PI (first 15% of PI duration):
    - Returns None (no tier badge shown)
    
    Rest of PI:
    - Uses timeline-based logic comparing actual vs expected completion
    - high: ahead of schedule (actual >= expected - 15% slack)
    - medium: slightly behind (expected - 25% <= actual < expected - 15%)
    - low: significantly behind (actual < expected - 25%)
    
    After PI ends OR if dates unavailable:
    - high: >= 75%
    - medium: 55-74.9%
    - low: < 55%
    
    Args:
        percent_completed: Actual completion percentage (0-100)
        start_date: PI start date (optional, for timeline-based calc)
        end_date: PI end date (optional, for timeline-based calc)
    
    Returns:
        Tier string: 'high', 'medium', 'low', or None (no tier for early PI)
    """
    # If dates not provided, use simple thresholds
    if start_date is None or end_date is None:
        if percent_completed >= PI_COMPLETION_HIGH_THRESHOLD:
            return "high"
        elif percent_completed >= PI_COMPLETION_MEDIUM_THRESHOLD:
            return "medium"
        else:
            return "low"
    
    # Convert datetime to date if needed
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if isinstance(end_date, datetime):
        end_date = end_date.date()
    
    today = date.today()
    
    # If PI hasn't started yet - use simple thresholds
    if today < start_date:
        if percent_completed >= PI_COMPLETION_HIGH_THRESHOLD:
            return "high"
        elif percent_completed >= PI_COMPLETION_MEDIUM_THRESHOLD:
            return "medium"
        else:
            return "low"
    
    # If PI has ended - use simple thresholds
    if today >= end_date:
        if percent_completed >= PI_COMPLETION_HIGH_THRESHOLD:
            return "high"
        elif percent_completed >= PI_COMPLETION_MEDIUM_THRESHOLD:
            return "medium"
        else:
            return "low"
    
    # During active PI - calculate progress percentage
    total_pi_days = (end_date - start_date).days
    if total_pi_days <= 0:
        # Invalid PI duration, use simple thresholds
        if percent_completed >= PI_COMPLETION_HIGH_THRESHOLD:
            return "high"
        elif percent_completed >= PI_COMPLETION_MEDIUM_THRESHOLD:
            return "medium"
        else:
            return "low"
    
    days_elapsed = (today - start_date).days
    progress_pct = (days_elapsed / total_pi_days) * 100
    
    # EARLY PI: First 15% - no tier shown
    if progress_pct <= 15:
        return None
    
    # REST OF PI: Apply timeline-based logic
    expected_completion = progress_pct
    slack_threshold = 15.0  # 15% slack
    yellow_threshold = 25.0  # More relaxed threshold for medium tier
    
    if percent_completed >= expected_completion - slack_threshold:
        return "high"  # On track or ahead
    elif percent_completed >= expected_completion - yellow_threshold:
        return "medium"  # Slightly behind
    else:
        return "low"  # Significantly behind


def get_sprint_completion_tier(
    percent_completed: float,
    start_date: date = None,
    end_date: date = None,
    slack_threshold: float = 15.0
) -> str:
    """
    Determine tier for sprint completion percentage.
    Uses timeline-based logic during active sprint, simple thresholds after sprint ends.
    
    During active sprint:
    - Compares actual completion to expected completion based on days elapsed
    - high: ahead of schedule (actual >= expected - slack)
    - medium: slightly behind (expected - 25% <= actual < expected - slack)
    - low: significantly behind (actual < expected - 25%)
    
    After sprint ends OR if dates unavailable:
    - high: >= 80%
    - medium: 60-79.9%
    - low: < 60%
    
    Args:
        percent_completed: Actual completion percentage (0-100)
        start_date: Sprint start date (optional, for timeline-based calc)
        end_date: Sprint end date (optional, for timeline-based calc)
        slack_threshold: Percentage slack allowed during active sprint (default: 15%)
    
    Returns:
        Tier string: 'high', 'medium', or 'low'
    """
    # If dates not provided, use simple thresholds
    if start_date is None or end_date is None:
        if percent_completed >= SPRINT_COMPLETION_HIGH_THRESHOLD:
            return "high"
        elif percent_completed >= SPRINT_COMPLETION_MEDIUM_THRESHOLD:
            return "medium"
        else:
            return "low"
    
    # Convert datetime to date if needed
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if isinstance(end_date, datetime):
        end_date = end_date.date()
    
    today = date.today()
    
    # If sprint hasn't started yet - use simple thresholds
    if today < start_date:
        if percent_completed >= SPRINT_COMPLETION_HIGH_THRESHOLD:
            return "high"
        elif percent_completed >= SPRINT_COMPLETION_MEDIUM_THRESHOLD:
            return "medium"
        else:
            return "low"
    
    # If sprint has ended - use simple thresholds
    if today >= end_date:
        if percent_completed >= SPRINT_COMPLETION_HIGH_THRESHOLD:
            return "high"
        elif percent_completed >= SPRINT_COMPLETION_MEDIUM_THRESHOLD:
            return "medium"
        else:
            return "low"
    
    # During active sprint - use timeline-based logic
    total_sprint_days = (end_date - start_date).days
    if total_sprint_days <= 0:
        # Invalid sprint duration, use simple thresholds
        if percent_completed >= SPRINT_COMPLETION_HIGH_THRESHOLD:
            return "high"
        elif percent_completed >= SPRINT_COMPLETION_MEDIUM_THRESHOLD:
            return "medium"
        else:
            return "low"
    
    days_elapsed = (today - start_date).days
    expected_completion = (days_elapsed / total_sprint_days) * 100
    
    # Determine tier based on comparison to expected
    yellow_threshold = 25.0  # More relaxed threshold for medium tier
    if percent_completed >= expected_completion - slack_threshold:
        return "high"  # On track or ahead
    elif percent_completed >= expected_completion - yellow_threshold:
        return "medium"  # Slightly behind
    else:
        return "low"  # Significantly behind


def get_sprint_predictability_tier(predictability: float) -> str:
    """
    Determine tier for sprint predictability percentage.
    Uses simple thresholds (same as completion after sprint ends).
    
    Tiers:
    - high: >= 80%
    - medium: 60-79.9%
    - low: < 60%
    
    Args:
        predictability: Predictability percentage (0-100)
    
    Returns:
        Tier string: 'high', 'medium', or 'low'
    """
    if predictability >= SPRINT_COMPLETION_HIGH_THRESHOLD:
        return "high"
    elif predictability >= SPRINT_COMPLETION_MEDIUM_THRESHOLD:
        return "medium"
    else:
        return "low"


def get_open_bugs_tier(open_bugs_count: int, team_count: int) -> str:
    """
    Determine tier for open bugs based on team count.
    Thresholds are multiplied by team count to adapt to group size.
    
    Tiers (based on OPEN_BUGS constants PER TEAM):
    - high: <= 6 bugs per team (best - green)
    - medium: 7-15 bugs per team (warning - yellow)
    - low: > 15 bugs per team (bad - red)
    
    For groups, thresholds scale with team count:
    - Example: 5 teams → high: <=30, medium: 31-75, low: >75
    
    Args:
        open_bugs_count: Current number of open bugs
        team_count: Number of teams (1 for single team, N for groups)
        
    Returns:
        Tier string: 'high', 'medium', or 'low'
    """
    if team_count <= 0 or open_bugs_count < 0:
        return "low"  # Invalid data = low tier
    
    # Adjust thresholds based on team count
    high_threshold = OPEN_BUGS_HIGH_PER_TEAM * team_count      # e.g., 6 * 5 = 30
    medium_threshold = OPEN_BUGS_MEDIUM_PER_TEAM * team_count  # e.g., 15 * 5 = 75
    
    if open_bugs_count <= high_threshold:
        return "high"   # Green (good)
    elif open_bugs_count <= medium_threshold:
        return "medium" # Yellow (warning)
    else:
        return "low"    # Red (bad)


def calculate_trend_for_cycle_time(current_value: float, previous_value: float) -> Optional[Dict]:
    """
    Calculate trend for cycle time (lower is better).
    
    Args:
        current_value: Current period cycle time
        previous_value: Previous period cycle time
    
    Returns:
        Trend dict or None if cannot calculate
    """
    if previous_value == 0 or current_value == 0:
        return None
    
    TREND_THRESHOLD = 0.1  # 0.1% minimum change
    
    percentage_float = ((current_value - previous_value) / previous_value) * 100
    percentage = int(round(abs(percentage_float)))
    
    # Determine direction
    if percentage_float > TREND_THRESHOLD:
        direction = "up"
    elif percentage_float < -TREND_THRESHOLD:
        direction = "down"
    else:
        direction = "flat"
        percentage = 0
    
    # For cycle time: lower is better (down = improved, up = not improved)
    improved = direction == "down"
    
    return {
        "direction": direction,
        "percentage": percentage,
        "label": "vs previous 30 days",
        "improved": improved
    }


def calculate_trend_for_open_bugs(bugs_created: int, bugs_resolved: int) -> Optional[Dict]:
    """
    Calculate trend for open bugs (fewer bugs = better).
    
    Formula: net_change = bugs_created - bugs_resolved
    - Positive (backlog growing): Red Up Arrow (worse)
    - Negative (backlog shrinking): Green Down Arrow (better)
    
    Args:
        bugs_created: Number of bugs created in period
        bugs_resolved: Number of bugs resolved in period
    
    Returns:
        Trend dict with direction, count, and improved flag
    """
    net_change = bugs_created - bugs_resolved
    
    if net_change == 0:
        return {
            "direction": "flat",
            "percentage": 0,
            "label": "vs previous 30 days",
            "improved": True  # No change is neutral/acceptable
        }
    
    # Use absolute value for display
    count = abs(net_change)
    
    if net_change > 0:
        # Backlog growing (more created than resolved) - BAD
        direction = "up"
        improved = False  # Getting worse (red up arrow)
    else:
        # Backlog shrinking (more resolved than created) - GOOD
        direction = "down"
        improved = True  # Getting better (green down arrow)
    
    return {
        "direction": direction,
        "percentage": count,  # Show actual count difference
        "label": "vs previous 30 days",
        "improved": improved
    }


def get_predictability_status(predictability: float) -> str:
    """
    Determine predictability status based on value.
    
    Args:
        predictability: Predictability percentage (0-100)
    
    Returns:
        "green" if predictability >= 75
        "yellow" if 60 <= predictability < 75
        "red" if predictability < 60
    """
    if predictability >= 75:
        return "green"
    elif predictability >= 60:
        return "yellow"
    else:
        return "red"


def get_velocity_status(velocity: int) -> str:
    """
    Determine velocity status based on value.
    Currently always returns "green" until a proper determination method is found.
    
    Args:
        velocity: Velocity (issue count)
    
    Returns:
        "green" (always for now)
    """
    return "green"


def get_percent_completed_status(
    percent_completed: float,
    start_date: date,
    end_date: date,
    slack_threshold: float = 15.0
) -> str:
    """
    Determine completion status based on sprint timeline vs actual completion.
    
    Compares actual completion percentage against expected completion based on
    how much of the sprint has elapsed.
    
    Args:
        percent_completed: Actual completion percentage (0-100)
        start_date: Sprint start date (date or datetime object)
        end_date: Sprint end date (date or datetime object)
        slack_threshold: Percentage slack allowed (default: 15%)
    
    Returns:
        "green" if ahead of schedule (actual >= expected - slack)
        "yellow" if slightly behind (expected - 25% <= actual < expected - slack)
        "red" if significantly behind (actual < expected - 25%)
        "green" if unable to calculate (edge cases)
    """
    # Handle edge cases
    if start_date is None or end_date is None:
        return "green"
    
    # Convert datetime to date if needed
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if isinstance(end_date, datetime):
        end_date = end_date.date()
    
    today = date.today()
    
    # If sprint hasn't started yet
    if today < start_date:
        return "green"
    
    # If sprint has ended
    if today >= end_date:
        # Compare actual completion to 100% expected (stricter thresholds)
        if percent_completed >= 90:  # Stricter: was 85% (100 - 15)
            return "green"
        elif percent_completed >= 80:  # Stricter: was 75%
            return "yellow"
        else:
            return "red"
    
    # Calculate expected completion based on timeline
    total_sprint_days = (end_date - start_date).days
    if total_sprint_days <= 0:
        return "green"
    
    days_elapsed = (today - start_date).days
    expected_completion = (days_elapsed / total_sprint_days) * 100
    
    # Determine status with slack (more relaxed thresholds)
    yellow_threshold = 25.0  # More relaxed: was 15.0
    if percent_completed >= expected_completion - slack_threshold:
        return "green"
    elif percent_completed >= expected_completion - yellow_threshold:
        return "yellow"
    else:
        return "red"


def get_in_progress_issues_status(
    in_progress_issues: int,
    total_issues: int
) -> str:
    """
    Determine in-progress issues status based on percentage of total issues.
    
    High WIP (work in progress) indicates potential bottlenecks.
    
    Args:
        in_progress_issues: Number of issues in progress
        total_issues: Total number of issues in sprint
    
    Returns:
        "green" if < 25% of issues are in progress
        "yellow" if 25-50% of issues are in progress
        "red" if > 50% of issues are in progress
    """
    # Handle edge cases
    if total_issues == 0:
        return "green"
    
    in_progress_percent = (in_progress_issues / total_issues) * 100
    
    if in_progress_percent > 50:
        return "red"
    elif in_progress_percent >= 25:
        return "yellow"
    else:
        return "green"


def calculate_days_left(end_date: date) -> Optional[int]:
    """
    Calculate days left in sprint as integer (inclusive counting).
    
    Args:
        end_date: Sprint end date (date or datetime object)
    
    Returns:
        Integer representing days left (inclusive of today and end date)
        - If today is end date: returns 1
        - If end date is in future: returns (end_date - today).days + 1
        - If sprint ended: returns 0
        - If end_date is None: returns None
    """
    if end_date is None:
        return None
    
    # Convert datetime to date if needed
    if isinstance(end_date, datetime):
        end_date = end_date.date()
    
    today = date.today()
    
    if end_date < today:
        return 0  # Sprint ended
    else:
        # Inclusive counting: (end_date - today).days + 1
        # If today is end_date, result is 1
        # If today is Nov 3 and end is Nov 4, result is 2
        return (end_date - today).days + 1


def calculate_days_in_sprint(start_date: date, end_date: date) -> Optional[int]:
    """
    Calculate total days in sprint as integer (inclusive counting).
    
    Args:
        start_date: Sprint start date (date or datetime object)
        end_date: Sprint end date (date or datetime object)
    
    Returns:
        Integer representing total days in sprint (inclusive of start and end dates)
        - Returns (end_date - start_date).days + 1
        - If either date is None: returns None
    """
    if start_date is None or end_date is None:
        return None
    
    # Convert datetime to date if needed
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if isinstance(end_date, datetime):
        end_date = end_date.date()
    
    # Inclusive counting: (end_date - start_date).days + 1
    return (end_date - start_date).days + 1


@team_metrics_router.get("/team-metrics/get-avg-sprint-metrics")
async def get_avg_sprint_metrics(
    team_name: str = Query(..., description="Team name or group name (if isGroup=true)"),
    sprint_count: int = Query(5, description="Number of sprints to average (default: 5, max: 20)", ge=1, le=20),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get average sprint metrics for a specific team or group.
    
    Returns velocity, cycle time, and predictability metrics averaged over the last N sprints.
    When isGroup=true, calculates averages across all teams in the group.
    
    Args:
        team_name: Name of the team or group name (if isGroup=true)
        sprint_count: Number of recent sprints to average (default: 5)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with velocity, cycle_time, and predictability metrics
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Validate sprint_count
        validated_sprint_count = validate_sprint_count(sprint_count)
        
        # Validate and resolve team names (validate first, then resolve)
        validated_name = None
        if isGroup:
            validated_name = validate_group_name(team_name)
        else:
            validated_name = validate_team_name(team_name)
        
        # Resolve team names using shared helper function (same as other endpoints)
        team_names_list = resolve_team_names_from_filter(validated_name, isGroup, conn)
        
        # Get raw metrics data from database function (team-by-team rows)
        raw_data = get_team_avg_sprint_metrics(validated_sprint_count, team_names_list, conn)
        
        # Calculate averages from all rows
        # Velocity: sum all issues across all teams per sprint, then average across sprints
        # Group by sprint_id to handle duplicates and exclude sprints with 0 total planned issues
        from collections import defaultdict
        sprint_totals = defaultdict(int)
        for row in raw_data:
            sprint_id = row.get('out_sprint_id')
            issues_completed = row.get('issues_completed_count', 0) or 0
            issues_planned = row.get('issues_in_sprint_count', 0) or 0
            # Only include sprints with at least 1 planned issue (issues_in_sprint_count > 0)
            if sprint_id is not None and issues_planned > 0:
                sprint_totals[sprint_id] += issues_completed
        
        # Calculate average: sum all issues across all sprints, divide by number of unique sprints
        total_issues = sum(sprint_totals.values())
        num_sprints = len(sprint_totals)
        avg_velocity = int(round(total_issues / num_sprints, 0)) if num_sprints > 0 else 0
        
        # Cycle time: total cycle time / total issues (weighted average)
        total_cycle_time = sum(row['total_cycle_time_sum_days'] for row in raw_data if row.get('total_cycle_time_sum_days') is not None)
        total_issues = sum(row['issues_completed_count'] for row in raw_data if row.get('issues_completed_count') is not None)
        avg_cycle_time = total_cycle_time / total_issues if total_issues > 0 else 0.0
        
        # Predictability: weighted by issue counts (total completed / total planned * 100)
        total_completed = sum(row['issues_completed_count'] for row in raw_data if row.get('issues_completed_count') is not None)
        total_planned = sum(row['issues_in_sprint_count'] for row in raw_data if row.get('issues_in_sprint_count') is not None)
        avg_predictability = (total_completed / total_planned * 100) if total_planned > 0 else 0.0
        
        # Calculate trend data grouped by sprint_id (aggregate across teams)
        trend_data = []
        try:
            from collections import defaultdict
            
            sprint_groups = defaultdict(lambda: {
                'issues_completed': 0,
                'total_cycle_time': 0.0,
                'issues_planned': 0,
                'sprint_complete_date': None
            })
            
            # Aggregate data by sprint_id (across all teams)
            rows_processed = 0
            rows_skipped = 0
            for row in raw_data:
                sprint_id = row.get('out_sprint_id')
                if sprint_id is None:
                    rows_skipped += 1
                    continue
                rows_processed += 1
                
                # Convert Decimal to float/int to avoid type mismatch errors
                issues_completed = row.get('issues_completed_count', 0) or 0
                cycle_time = row.get('total_cycle_time_sum_days', 0) or 0.0
                issues_planned = row.get('issues_in_sprint_count', 0) or 0
                
                sprint_groups[sprint_id]['issues_completed'] += int(issues_completed) if issues_completed else 0
                sprint_groups[sprint_id]['total_cycle_time'] += float(cycle_time) if cycle_time else 0.0
                sprint_groups[sprint_id]['issues_planned'] += int(issues_planned) if issues_planned else 0
                if row.get('sprint_complete_date') and not sprint_groups[sprint_id]['sprint_complete_date']:
                    sprint_groups[sprint_id]['sprint_complete_date'] = row.get('sprint_complete_date')
            
            # Calculate per-sprint metrics
            for sprint_id, data in sprint_groups.items():
                sprint_velocity = data['issues_completed']
                sprint_cycle_time = data['total_cycle_time'] / data['issues_completed'] if data['issues_completed'] > 0 else 0.0
                sprint_predictability = (data['issues_completed'] / data['issues_planned'] * 100) if data['issues_planned'] > 0 else 0.0
                
                # Format date if available
                sprint_date = None
                if data['sprint_complete_date']:
                    if hasattr(data['sprint_complete_date'], 'strftime'):
                        sprint_date = data['sprint_complete_date'].strftime('%Y-%m-%d')
                    else:
                        sprint_date = str(data['sprint_complete_date'])
                
                trend_data.append({
                    'sprint_id': sprint_id,
                    'sprint_complete_date': sprint_date,
                    'velocity': sprint_velocity,
                    'cycle_time': round(sprint_cycle_time, 2),
                    'predictability': round(sprint_predictability, 2)
                })
            
            # Sort by sprint_complete_date (oldest first), then by sprint_id
            # Fix: Handle type mismatches in sorting
            trend_data.sort(key=lambda x: (
                x['sprint_complete_date'] or '0000-00-00',
                int(x['sprint_id']) if x['sprint_id'] is not None else 0
            ))
            
            # Debug: Log results
            logger.info(f"DEBUG: Rows processed: {rows_processed}, skipped: {rows_skipped}, trend_data items: {len(trend_data)}")
        except Exception as e:
            logger.error(f"Error calculating trend data: {e}")
            logger.exception(e)  # Log full traceback
            trend_data = []  # Return empty array on error
        
        # Build response in GitHub service KPI structure (array of MetricResponse objects)
        # Note: trend_data calculation is kept but not included in response (used for line charts elsewhere)
        # Note: trend is set to None for now (will be added later)
        
        metrics = [
            {
                "metric_id": "velocity",
                "label": "Avg Sprint Velocity",
                "value": str(avg_velocity),
                "tier_status": "",
                "description": f"Average velocity in the last {validated_sprint_count} closed sprints",
                "tooltip": f"Average velocity in the last {validated_sprint_count} closed sprints",
                "trend": None,  # No trend calculation for now
                "action": {
                    "type": "report",
                    "report_ids": ["team-closed-sprints"],
                    "params": {
                        "team_name": validated_name,
                        "isGroup": isGroup
                    }
                }
            },
            {
                "metric_id": "cycle_time",
                "label": "Avg Story/Task Cycle Time",
                "value": f"{avg_cycle_time:.1f}d",
                "tier_status": "",
                "description": f"Average story cycle time in the last {validated_sprint_count} sprints",
                "tooltip": f"Average story cycle time in the last {validated_sprint_count} sprints",
                "trend": None,  # No trend calculation for now
                "action": {
                    "type": "report",
                    "report_ids": ["cycle-time-over-time"],
                    "params": {
                        "team_name": validated_name,
                        "isGroup": isGroup
                    }
                }
            },
            {
                "metric_id": "predictability",
                "label": "Avg Sprint Predictability",
                "value": f"{avg_predictability:.0f}%",
                "tier_status": "",
                "description": f"Average sprint predictability over last {validated_sprint_count} sprints",
                "tooltip": f"Average sprint predictability over last {validated_sprint_count} sprints",
                "trend": None,  # No trend calculation for now
                "action": {
                    "type": "report",
                    "report_ids": ["sprint-predictability"],
                    "params": {
                        "team_name": validated_name,
                        "isGroup": isGroup
                    }
                }
            }
        ]
        
        return metrics
    
    except HTTPException:
        raise  # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching average sprint metrics (team_name={team_name}, isGroup={isGroup}): {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch average sprint metrics: {str(e)}"
        )


@team_metrics_router.get("/team-metrics/count-in-progress")
async def get_count_in_progress(
    team_name: str = Query(..., description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get count of issues currently in progress for a team or group with breakdown by issue type.
    
    Returns the number of issues with status_category = 'In Progress', grouped by issue type.
    Only includes issue types that have at least one issue in progress.
    When isGroup=true, aggregates counts across all teams in the group.
    
    Args:
        team_name: Name of the team or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with total count and breakdown by issue type
    """
    try:
        # Validate inputs (validate team_name or group_name based on isGroup)
        validated_name = None
        if isGroup:
            validated_name = validate_group_name(team_name)
        else:
            validated_name = validate_team_name(team_name)
        
        # Resolve team names using shared helper function (handles single team, group, or None)
        team_names_list = resolve_team_names_from_filter(validated_name, isGroup, conn)
        
        # Get count breakdown from database function
        count_data = get_team_count_in_progress(team_names_list, conn)
        
        # Build response data
        response_data = {
            "total_in_progress": count_data['total_in_progress'],
            "count_by_type": count_data['count_by_type']
        }
        
        # Add team/group information to response
        if isGroup:
            response_data["group_name"] = validated_name
            response_data["teams_in_group"] = team_names_list
            message = f"Retrieved count in progress for group '{validated_name}'"
        else:
            response_data["team_name"] = validated_name
            message = f"Retrieved count in progress for team '{validated_name}'"
        
        return {
            "success": True,
            "data": response_data,
            "message": message
        }
    
    except HTTPException:
        raise  # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching count in progress for team/group {team_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch count in progress: {str(e)}"
        )


@team_metrics_router.get("/team-metrics/current-sprint-progress")
async def get_current_sprint_progress(
    team_name: str = Query(..., description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get current sprint progress for a team or group with detailed breakdown.
    
    Returns sprint ID, sprint name, days left, total issues, completed, in progress, to do counts, completion percentage,
    and status indicators for the current active sprint.
    When isGroup=true and teams have different active sprints, aggregates counts and excludes sprint-specific fields.
    
    Args:
        team_name: Name of the team or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with sprint progress metrics including:
        - sprint_id: Sprint ID (only if single sprint)
        - sprint_name: Sprint name (only if single sprint)
        - days_left: Days remaining in sprint (only if single sprint)
        - days_in_sprint: Total days in sprint (only if single sprint)
        - total_issues: Total number of issues in active sprint(s)
        - completed_issues: Number of completed issues (status_category = 'Done')
        - in_progress_issues: Number of issues in progress
        - todo_issues: Number of issues in to do status
        - percent_completed: Percentage of completed issues (0-100)
        - percent_completed_status: Status indicator (green/yellow/red) based on timeline vs completion
        - in_progress_issues_status: Status indicator (green/yellow/red) based on WIP percentage
    """
    try:
        # Validate inputs (validate team_name or group_name based on isGroup)
        validated_name = None
        if isGroup:
            validated_name = validate_group_name(team_name)
        else:
            validated_name = validate_team_name(team_name)
        
        # Resolve team names using shared helper function (handles single team, group, or None)
        team_names_list = resolve_team_names_from_filter(validated_name, isGroup, conn)
        
        # Get sprint progress data from database function
        progress_data = get_team_current_sprint_progress(team_names_list, conn)
        
        # Calculate status indicators based on aggregated data
        percent_completed_status = get_percent_completed_status(
            progress_data['percent_completed'],
            progress_data['start_date'],
            progress_data['end_date']
        )
        in_progress_issues_status = get_in_progress_issues_status(
            progress_data['in_progress_issues'],
            progress_data['total_issues']
        )
        
        # Build response data
        response_data = {
            "total_issues": progress_data['total_issues'],
            "completed_issues": progress_data['completed_issues'],
            "in_progress_issues": progress_data['in_progress_issues'],
            "todo_issues": progress_data['todo_issues'],
            "percent_completed": progress_data['percent_completed'],
            "percent_completed_status": percent_completed_status,
            "in_progress_issues_status": in_progress_issues_status
        }
        
        # Always include sprint_id and sprint_name (null if multiple sprints)
        # Always calculate days_left and days_in_sprint if dates are available
        response_data["sprint_id"] = progress_data['sprint_id']
        response_data["sprint_name"] = progress_data['sprint_name']
        
        # Calculate days_left and days_in_sprint if we have dates (even for multiple sprints, use earliest dates)
        days_left = calculate_days_left(progress_data['end_date'])
        days_in_sprint = calculate_days_in_sprint(
            progress_data['start_date'],
            progress_data['end_date']
        )
        response_data["days_left"] = days_left
        response_data["days_in_sprint"] = days_in_sprint
        
        # Add team/group information to response
        if isGroup:
            response_data["group_name"] = validated_name
            response_data["teams_in_group"] = team_names_list
            message = f"Retrieved current sprint progress for group '{validated_name}'"
        else:
            response_data["team_name"] = validated_name
            message = f"Retrieved current sprint progress for team '{validated_name}'"
        
        return {
            "success": True,
            "data": response_data,
            "message": message
        }
    
    except HTTPException:
        raise  # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching current sprint progress for team/group {team_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch current sprint progress: {str(e)}"
        )


@team_metrics_router.get("/team-metrics/sprint-burndown")
async def get_sprint_burndown_data(
    team_name: str = Query(..., description="Team name or group name (if isGroup=true) to get burndown data for"),
    issue_type: str = Query("all", description="Issue type filter (default: 'all')"),
    sprint_name: str = Query(None, description="Sprint name (optional, will auto-select ACTIVE Sprint if not provided)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get sprint burndown data for a specific team or group.

    If no sprint_name is provided, automatically selects the ACTIVE sprint with the maximum total issues.
    For groups, all teams must have the same active sprint.

    Args:
        team_name: Name of the team or group (if isGroup=true)
        issue_type: Issue type filter (default: "all")
        sprint_name: Sprint name (optional, auto-selected if not provided)
        isGroup: If true, team_name is treated as a group name

    Returns:
        JSON response with burndown data and metadata
    """
    try:
        # Validate issue_type
        if not isinstance(issue_type, str):
            raise HTTPException(status_code=400, detail="Issue type must be a string")
        if issue_type.strip() == "":
            issue_type = "all"
        
        # Use shared helper for sprint selection
        sprint_selection = select_sprint_for_teams(team_name, isGroup, sprint_name, conn)
        team_names_list = sprint_selection['team_names_list']
        selected_sprint_name = sprint_selection['selected_sprint_name']
        selected_sprint_id = sprint_selection['selected_sprint_id']
        selected_sprint_start_date = sprint_selection.get('selected_sprint_start_date')
        selected_sprint_end_date = sprint_selection.get('selected_sprint_end_date')
        error_message = sprint_selection['error_message']
        
        # If error occurred, return error response
        if error_message:
            return {
                "success": False,
                "data": {},
                "message": error_message
            }
        
        # Get burndown data for selected sprint
        burndown_data = get_sprint_burndown_data_db(team_names_list, selected_sprint_name, issue_type, conn)
        
        # Calculate total issues in sprint and get start/end dates
        # Use dates from sprint selection first, fall back to burndown_data if needed
        total_issues_in_sprint = 0
        start_date = selected_sprint_start_date
        end_date = selected_sprint_end_date
        
        if burndown_data:
            total_issues_in_sprint = burndown_data[0].get('total_issues', 0)
            # Only use burndown_data dates if sprint selection didn't provide them
            if not start_date:
                start_date = burndown_data[0].get('start_date')
            if not end_date:
                end_date = burndown_data[0].get('end_date')
        
        # Build response data
        response_data = {
            "sprint_id": selected_sprint_id,
            "sprint_name": selected_sprint_name,
            "start_date": start_date,
            "end_date": end_date,
            "burndown_data": burndown_data,
            "issue_type": issue_type,
            "total_issues_in_sprint": total_issues_in_sprint,
            "isGroup": isGroup
        }
        
        # Add team/group information to response
        if isGroup:
            response_data["group_name"] = team_name
            response_data["teams_in_group"] = team_names_list
            message = f"Retrieved sprint burndown data for group '{team_name}' ({len(team_names_list)} teams) and sprint '{selected_sprint_name}'"
        else:
            response_data["team_name"] = team_name
            message = f"Retrieved sprint burndown data for team '{team_name}' and sprint '{selected_sprint_name}'"
        
        return {
            "success": True,
            "data": response_data,
            "message": message
        }
    
    except HTTPException:
        raise # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching sprint burndown data for team/group {team_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch sprint burndown data: {str(e)}"
        )


@team_metrics_router.get("/team-metrics/get-sprints")
async def get_sprints(
    team_name: str = Query(..., description="Team name to get sprints for"),
    sprint_status: str = Query(None, description="Sprint status filter (optional: 'active', 'closed', or leave empty for all)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get list of sprints for a specific team with total issues count.

    Args:
        team_name: Name of the team
        sprint_status: Sprint status filter (optional: "active", "closed", or None for all)

    Returns:
        JSON response with sprints list and metadata
    """
    try:
        # Validate inputs
        validated_team_name = validate_team_name(team_name)
        
        # Validate sprint_status if provided
        if sprint_status and sprint_status not in ["active", "closed"]:
            raise HTTPException(status_code=400, detail="Sprint status must be 'active' or 'closed'")
        
        # Get sprints from database function
        sprints = get_sprints_with_total_issues_db(validated_team_name, sprint_status, conn)
        
        return {
            "success": True,
            "data": {
                "team_name": validated_team_name,
                "sprint_status": sprint_status,
                "sprints": sprints,
                "count": len(sprints)
            },
            "message": f"Retrieved {len(sprints)} sprints for team '{validated_team_name}'"
        }
    
    except HTTPException:
        raise # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching sprints for team {validated_team_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch sprints: {str(e)}"
        )


def _fetch_closed_sprints_flat(
    team_name: Optional[str],
    isGroup: bool,
    months: int,
    issue_type: Optional[str],
    conn: Connection,
    sort_by: str = "default"
) -> Dict[str, Any]:
    """
    Shared helper function to fetch closed sprints in flat structure (not grouped by team).
    
    Args:
        team_name: Optional team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
        months: Number of months to look back
        issue_type: Optional issue type filter
        conn: Database connection
        sort_by: Sort order - "default" or "advanced"
    
    Returns:
        Dictionary with sprints list, metadata, and message
    """
    # Validate months parameter
    if months not in [1, 2, 3, 4, 6, 9]:
        raise HTTPException(
            status_code=400, 
            detail="Months parameter must be one of: 1, 2, 3, 4, 6, 9"
        )
    
    # Resolve team names using shared helper function
    team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
    
    # Build filter description for logging and response
    filter_description = None
    validated_name = None
    
    if team_name is not None:
        if isGroup:
            validated_name = validate_group_name(team_name)
            if team_names_list:
                filter_description = f"group '{validated_name}' ({len(team_names_list)} teams)"
                logger.info(f"Found {len(team_names_list)} teams in group '{validated_name}': {team_names_list}")
        else:
            validated_name = validate_team_name(team_name)
            filter_description = f"team '{validated_name}'"
    
    # Get closed sprints from database function with specified sort order
    closed_sprints_all = get_closed_sprints_data_db(
        team_names_list if team_names_list else None, 
        months, 
        issue_type=issue_type, 
        sort_by=sort_by,
        conn=conn
    )
    
    # Calculate metrics
    # Group by sprint_id to count unique sprints and exclude sprints with 0 total planned issues
    # For groups: sum all issues across all teams per sprint, then average across sprints
    from collections import defaultdict
    sprint_totals = defaultdict(int)
    for sprint in closed_sprints_all:
        sprint_id = sprint.get('sprint_id')
        issues_at_start = sprint.get('issues_at_start', 0) or 0
        issues_added = sprint.get('issues_added', 0) or 0
        issues_done = sprint.get('issues_done', 0) or 0
        total_planned = issues_at_start + issues_added
        # Only include sprints with at least 1 planned issue (issues_at_start + issues_added > 0)
        if sprint_id is not None and total_planned > 0:
            sprint_totals[sprint_id] += issues_done
    
    # Calculate average: sum all issues across all sprints, divide by number of unique sprints
    total_issues_done = sum(sprint_totals.values())
    total_sprints = len(sprint_totals)  # Count unique sprints only
    average_velocity = round(total_issues_done / total_sprints, 2) if total_sprints > 0 else 0.0
    
    # Get unique teams count
    unique_teams = set()
    for sprint in closed_sprints_all:
        sprint_team = sprint.get('team_name')
        if sprint_team:
            unique_teams.add(sprint_team)
    teams_count = len(unique_teams)
    
    # Build metadata
    # total_sprints should reflect unique sprints with completed issues (for consistency with average_velocity calculation)
    meta = {
        "months": months,
        "total_sprints": total_sprints,  # Unique sprints with completed issues
        "teams_count": teams_count,
        "average_velocity": average_velocity
    }
    
    # Add team/group information to metadata
    if validated_name:
        if isGroup:
            meta["group_name"] = validated_name
            meta["teams_in_group"] = team_names_list
        else:
            meta["team_name"] = validated_name
    
    # Build response message
    if filter_description:
        message = f"Retrieved sprint velocity data for {filter_description} (last {months} months)"
    else:
        message = f"Retrieved sprint velocity data for all teams (last {months} months)"
    
    return {
        "sprints": closed_sprints_all,
        "meta": meta,
        "message": message
    }


@team_metrics_router.get("/team-metrics/closed-sprints")
async def get_closed_sprints(
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true). If not provided, returns all closed sprints."),
    months: int = Query(3, description="Number of months to look back (1, 2, 3, 4, 6, 9)", ge=1, le=12),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    issue_type: Optional[str] = Query(None, description="Issue type filter (optional, e.g., 'Story', 'Bug', 'Task')"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get closed sprints data for a specific team(s) or group with detailed completion metrics.
    
    This endpoint retrieves comprehensive sprint completion data including:
    - Sprint name, start/end dates, and sprint goals
    - Completion percentages and issue counts
    - Issues planned, added, done, and remaining
    
    Results are grouped by team name.
    
    Parameters:
    - team_name: Optional team name or group name (if isGroup=true). If not provided, returns all closed sprints.
    - months: Number of months to look back (optional, default: 3)
      Valid values: 1, 2, 3, 4, 6, 9
      Examples:
        - months=1: Last 1 month
        - months=3: Last 3 months (default)
        - months=6: Last 6 months
        - months=9: Last 9 months
    - isGroup: If true, team_name is treated as a group name and returns closed sprints for all teams in that group
    - issue_type: Optional issue type filter (e.g., 'Story', 'Bug', 'Task')
    
    Returns:
        JSON response with closed sprints grouped by team and metadata
    """
    try:
        # Validate months parameter first
        if months not in [1, 2, 3, 4, 6, 9]:
            raise HTTPException(
                status_code=400, 
                detail="Months parameter must be one of: 1, 2, 3, 4, 6, 9"
            )
        
        # Resolve team names using shared helper function (handles single team, group, or None)
        team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
        
        # Build filter description for logging and response
        filter_description = None
        validated_name = None
        
        if team_name is not None:
            if isGroup:
                validated_name = validate_group_name(team_name)
                if team_names_list:
                    filter_description = f"group '{validated_name}' ({len(team_names_list)} teams)"
                    logger.info(f"Found {len(team_names_list)} teams in group '{validated_name}': {team_names_list}")
            else:
                validated_name = validate_team_name(team_name)
                filter_description = f"team '{validated_name}'"
        
        # Get closed sprints from database function (supports multiple teams)
        closed_sprints_all = get_closed_sprints_data_db(team_names_list if team_names_list else None, months, issue_type=issue_type, conn=conn)
        
        # Group closed sprints by team_name
        sprints_by_team = {}
        for sprint in closed_sprints_all:
            sprint_team = sprint.get('team_name')
            if sprint_team:
                if sprint_team not in sprints_by_team:
                    sprints_by_team[sprint_team] = []
                sprints_by_team[sprint_team].append(sprint)
        
        # Build response message
        if filter_description:
            message = f"Retrieved closed sprints for {filter_description} (last {months} months)"
        else:
            message = f"Retrieved closed sprints for all teams (last {months} months)"
        
        total_sprints = len(closed_sprints_all)
        
        # Calculate average velocity: sum of issues_done across all sprints / number of sprints
        total_issues_done = sum(sprint.get('issues_done', 0) or 0 for sprint in closed_sprints_all)
        average_velocity = round(total_issues_done / total_sprints, 2) if total_sprints > 0 else 0.0
        
        response_data = {
            "months": months,
            "closed_sprints_by_team": sprints_by_team,
            "total_sprints": total_sprints,
            "teams_count": len(sprints_by_team),
            "average_velocity": average_velocity
        }
        
        # Add metadata based on what was filtered
        if validated_name:
            if isGroup:
                response_data["group_name"] = validated_name
                response_data["teams_in_group"] = team_names_list
            else:
                response_data["team_name"] = validated_name
        
        return {
            "success": True,
            "data": response_data,
            "message": message
        }
    
    except HTTPException:
        raise # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching closed sprints (team_name={team_name}, isGroup={isGroup}): {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch closed sprints: {str(e)}"
        )


@team_metrics_router.get("/team-metrics/sprint-velocity-advanced")
async def get_sprint_velocity_advanced(
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true). If not provided, returns all closed sprints."),
    months: int = Query(3, description="Number of months to look back (1, 2, 3, 4, 6, 9)", ge=1, le=12),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    issue_type: Optional[str] = Query(None, description="Issue type filter (optional, e.g., 'Story', 'Bug', 'Task')"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get closed sprints data sorted by start date (ascending) and team name (ascending) in a flat structure.
    
    This endpoint retrieves comprehensive sprint completion data including:
    - Sprint name, start/end dates, and sprint goals
    - Completion percentages and issue counts
    - Issues planned, added, done, and remaining
    
    Results are returned as a flat array sorted by sprint start date (oldest first) and team name (alphabetical).
    
    Parameters:
    - team_name: Optional team name or group name (if isGroup=true). If not provided, returns all closed sprints.
    - months: Number of months to look back (optional, default: 3)
      Valid values: 1, 2, 3, 4, 6, 9
    - isGroup: If true, team_name is treated as a group name and returns closed sprints for all teams in that group
    - issue_type: Optional issue type filter (e.g., 'Story', 'Bug', 'Task')
    
    Returns:
        JSON response with closed sprints as flat array sorted by start_date ASC, team_name ASC
    """
    try:
        result = _fetch_closed_sprints_flat(team_name, isGroup, months, issue_type, conn, sort_by="advanced")
        
        return {
            "success": True,
            "data": result["sprints"],
            "meta": result["meta"],
            "message": result["message"]
        }
    
    except HTTPException:
        raise # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching sprint velocity advanced (team_name={team_name}, isGroup={isGroup}): {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch sprint velocity advanced: {str(e)}"
        )


@team_metrics_router.get("/team-metrics/issues-trend")
async def get_issues_trend(
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    months: int = Query(6, description="Number of months to look back (any integer)"),
    issue_type: Optional[str] = Query(None, description="Issue type filter (e.g., 'Bug', 'Story'). If not provided, returns all types"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get issues created and resolved over time using the get_issues_created_and_resolved_trend SQL function.
    
    This endpoint retrieves trend data showing issues created, resolved, and cumulative open issues over time.
    Data is aggregated per month (not per team). When isGroup=true, aggregates data across all teams in the group.
    
    Parameters:
    - team_name: Optional team name or group name (if isGroup=true)
    - months: Number of months to look back (any integer, default: 6)
    - issue_type: Optional issue type filter (e.g., 'Bug', 'Story'). If not provided, returns data for all types
    - isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with trend data grouped by issue_type and metadata
    """
    try:
        from database_team_metrics import resolve_team_names_from_filter
        
        # Validate months is an integer (FastAPI already does this, but ensure it's positive)
        if not isinstance(months, int):
            raise HTTPException(status_code=400, detail="Months parameter must be an integer")
        
        # Normalize issue_type - convert "all" to None, empty string to None
        normalized_issue_type = None
        if issue_type:
            issue_type = issue_type.strip()
            if issue_type and issue_type.lower() != "all":
                normalized_issue_type = issue_type
        
        # Resolve team names using shared helper function
        team_names_list = None
        validated_name = None
        if team_name:
            team_name = team_name.strip()
            if team_name:
                if isGroup:
                    validated_name = validate_group_name(team_name)
                else:
                    validated_name = validate_team_name(team_name)
                team_names_list = resolve_team_names_from_filter(validated_name, isGroup, conn)
        
        # Get issues trend data from database function
        # Returns dict grouped by issue_type: {"Bug": [...], "Story": [...]}
        trend_data = get_issues_trend_data_db(team_names_list, months, normalized_issue_type, conn)
        
        # Calculate total count across all issue types
        total_count = sum(len(data_list) for data_list in trend_data.values())
        
        # Build response data
        response_data = {
            "data": trend_data,
            "meta": {
                "months": months,
                "count": total_count
            }
        }
        
        # Add metadata based on what was filtered
        if validated_name:
            if isGroup:
                response_data["meta"]["group_name"] = validated_name
                response_data["meta"]["teams_in_group"] = team_names_list
            else:
                response_data["meta"]["team_name"] = validated_name
        
        if normalized_issue_type:
            response_data["meta"]["issue_type"] = normalized_issue_type
        
        # Build message
        if validated_name:
            if isGroup:
                message = f"Retrieved issues trend data for group '{validated_name}' ({len(team_names_list)} teams) (last {months} months)"
            else:
                message = f"Retrieved issues trend data for team '{validated_name}' (last {months} months)"
        else:
            message = f"Retrieved issues trend data for all teams (last {months} months)"
        
        return {
            "success": True,
            "data": response_data["data"],
            "meta": response_data["meta"],
            "message": message
        }
    
    except HTTPException:
        raise # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching issues trend data (team_name={team_name}, isGroup={isGroup}): {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch issues trend data: {str(e)}"
        )


@team_metrics_router.get("/team-metrics/get-average-sprint-velocity-per-team")
async def get_average_sprint_velocity_per_team_endpoint(
    num_sprints: int = Query(5, description="Number of sprints to average (default: 5, max: 20)", ge=1, le=20),
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    pi: Optional[str] = Query(None, description="Program Increment name - if provided, uses teams that participate in this PI"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get average sprint velocity per team using the get_average_sprint_velocity_per_team database function.
    
    Returns average velocity (completed issues per sprint) for each team over the last N sprints.
    
    Parameters:
    - num_sprints: Number of recent sprints to average (default: 5, max: 20)
    - team_name: Optional team name or group name (if isGroup=true)
    - isGroup: If true, team_name is treated as a group name
    - pi: Optional Program Increment name. If provided, uses all teams that participate in this PI.
          If both pi and team_name are provided, gets PI teams and then filters by team_name/isGroup.
    
    Returns:
        JSON response with velocity data per team
    """
    try:
        # Validate num_sprints
        validated_num_sprints = validate_sprint_count(num_sprints)
        
        # Resolve team names based on parameters
        team_names_list = None
        
        if pi:
            # Validate PI parameter
            validated_pi = validate_pi(pi)
            
            # Get teams that participate in the PI (reuse function to avoid duplication)
            pi_teams = get_pi_participating_teams_db(validated_pi, conn)
            
            if not pi_teams:
                return {
                    "success": True,
                    "data": {
                        "velocity_data": [],
                        "num_sprints": validated_num_sprints,
                        "count": 0,
                        "pi": validated_pi
                    },
                    "message": f"No teams found participating in PI '{validated_pi}'"
                }
            
            # If team_name is also provided, filter PI teams by team_name/isGroup
            if team_name:
                validated_name = None
                if isGroup:
                    validated_name = validate_group_name(team_name)
                    # Resolve group to team names
                    group_teams = resolve_team_names_from_filter(validated_name, True, conn)
                    # Intersection: teams that are both in PI and in the group
                    team_names_list = [t for t in pi_teams if t in group_teams]
                else:
                    validated_name = validate_team_name(team_name)
                    # Check if the team is in the PI teams
                    if validated_name in pi_teams:
                        team_names_list = [validated_name]
                    else:
                        team_names_list = []
            else:
                # Use all PI teams
                team_names_list = pi_teams
        elif team_name:
            # No PI provided, use team_name/isGroup resolution
            validated_name = None
            if isGroup:
                validated_name = validate_group_name(team_name)
            else:
                validated_name = validate_team_name(team_name)
            
            # Resolve team names using shared helper function
            team_names_list = resolve_team_names_from_filter(validated_name, isGroup, conn)
        else:
            # No filters - use all teams (pass None to database function)
            team_names_list = None
        
        # Get velocity data from database function
        velocity_data = get_average_sprint_velocity_per_team(validated_num_sprints, team_names_list, conn)
        
        # Build response data
        response_data = {
            "velocity_data": velocity_data,
            "num_sprints": validated_num_sprints,
            "count": len(velocity_data)
        }
        
        # Add metadata based on what was filtered
        if pi:
            response_data["pi"] = pi
        if team_name:
            if isGroup:
                response_data["group_name"] = team_name
                if team_names_list:
                    response_data["teams_in_group"] = team_names_list
            else:
                response_data["team_name"] = team_name
        
        # Build message
        if pi and team_name:
            message = f"Retrieved average sprint velocity for {len(velocity_data)} teams (PI: '{pi}', filter: '{team_name}')"
        elif pi:
            message = f"Retrieved average sprint velocity for {len(velocity_data)} teams participating in PI '{pi}'"
        elif team_name:
            if isGroup:
                message = f"Retrieved average sprint velocity for {len(velocity_data)} teams in group '{team_name}'"
            else:
                message = f"Retrieved average sprint velocity for team '{team_name}'"
        else:
            message = f"Retrieved average sprint velocity for {len(velocity_data)} teams"
        
        return {
            "success": True,
            "data": response_data,
            "message": message
        }
    
    except HTTPException:
        raise  # Re-raise FastAPI HTTPExceptions
    except Exception as e:
        logger.error(f"Error fetching average sprint velocity per team (num_sprints={num_sprints}, team_name={team_name}, isGroup={isGroup}, pi={pi}): {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch average sprint velocity per team: {str(e)}"
        )


# Helper functions for Sprint KPIs endpoint
def get_velocity_metric(
    team_names_list: List[str],
    validated_sprint_count: int,
    validated_name: str,
    isGroup: bool,
    conn: Connection
) -> Dict:
    """Get velocity metric using sprint-based calculation."""
    raw_data = get_team_avg_sprint_metrics(validated_sprint_count, team_names_list, conn)
    
    # Group by sprint_id to handle duplicates and ensure we count unique sprints
    # Exclude sprints with 0 total planned issues (issues_in_sprint_count = 0)
    # For groups: sum all issues across all teams per sprint, then average across sprints
    from collections import defaultdict
    sprint_totals = defaultdict(int)
    for row in raw_data:
        sprint_id = row.get('out_sprint_id')
        issues_completed = row.get('issues_completed_count', 0) or 0
        issues_planned = row.get('issues_in_sprint_count', 0) or 0
        # Only include sprints with at least 1 planned issue (issues_in_sprint_count > 0)
        if sprint_id is not None and issues_planned > 0:
            sprint_totals[sprint_id] += issues_completed
    
    # Calculate average: sum all issues across all sprints, divide by number of unique sprints
    total_issues = sum(sprint_totals.values())
    num_sprints = len(sprint_totals)
    avg_velocity = int(round(total_issues / num_sprints, 0)) if num_sprints > 0 else 0
    
    # Build chart data from raw_data (last 5 sprints)
    chart_points = []
    for row in raw_data:
        sprint_id = row.get('out_sprint_id')
        velocity = row.get('issues_completed_count', 0) or 0
        sprint_date = row.get('sprint_complete_date')
        
        # Format date if available
        date_str = None
        if sprint_date:
            if hasattr(sprint_date, 'strftime'):
                date_str = sprint_date.strftime('%Y-%m-%d')
            else:
                date_str = str(sprint_date)
        
        chart_points.append({
            'sprint_id': str(sprint_id) if sprint_id else 'Unknown',
            'value': int(velocity),
            'date': date_str
        })
    
    # Sort by date (oldest first) and limit to last 5
    chart_points.sort(key=lambda x: x['date'] or '0000-00-00')
    chart_points = chart_points[-5:]
    
    return {
        "metric_id": "sprint_velocity",
        "label": "Avg Sprint Velocity",
        "value": str(avg_velocity),
        "tier_status": "",
        "metric_type": "sprint",
        "description": f"{validated_name}: Avg velocity {avg_velocity} (last {validated_sprint_count} closed sprints)",
        "tooltip": f"Average velocity in the last {validated_sprint_count} closed sprints",
        "trend": None,
        "chart_data": {
            "type": "line",
            "points": chart_points
        } if chart_points else None,
        "action": {
            "type": "report",
            "report_ids": ["team-closed-sprints"],
            "params": {
                "team_name": validated_name,
                "isGroup": isGroup
            }
        }
    }


def get_cycle_time_metric(
    team_names_list: List[str],
    validated_name: str,
    isGroup: bool,
    conn: Connection
) -> Dict:
    """Get cycle time metric using 30-day period calculation."""
    from datetime import timedelta
    from database_team_metrics import get_cycle_time_for_period
    
    today = date.today()
    current_end_date = today
    current_start_date = today - timedelta(days=CYCLE_TIME_PERIOD_DAYS)
    previous_end_date = current_start_date - timedelta(days=1)
    previous_start_date = previous_end_date - timedelta(days=CYCLE_TIME_PERIOD_DAYS)
    
    avg_cycle_time = get_cycle_time_for_period(team_names_list, current_start_date, current_end_date, conn)
    if avg_cycle_time is None:
        avg_cycle_time = 0.0
    
    previous_cycle_time = get_cycle_time_for_period(team_names_list, previous_start_date, previous_end_date, conn)
    
    cycle_time_trend = None
    if previous_cycle_time is not None and previous_cycle_time > 0 and avg_cycle_time > 0:
        cycle_time_trend = calculate_trend_for_cycle_time(avg_cycle_time, previous_cycle_time)
    
    cycle_time_tier = get_cycle_time_tier(avg_cycle_time)
    
    # Build tooltip similar to DORA metrics format
    tooltip = f"Average cycle time in the last {CYCLE_TIME_PERIOD_DAYS} days: {avg_cycle_time:.1f}d"
    if cycle_time_trend and cycle_time_trend.get('direction') and cycle_time_trend['direction'] != 'flat':
        if cycle_time_trend.get('improved'):
            tooltip += f"\nThis represents a {cycle_time_trend['percentage']}% improvement compared to the previous {CYCLE_TIME_PERIOD_DAYS} days."
        else:
            tooltip += f"\nThis represents a {cycle_time_trend['percentage']}% regression compared to the previous {CYCLE_TIME_PERIOD_DAYS} days."
    
    return {
        "metric_id": "cycle_time",
        "label": "Avg Cycle Time (sprint issues)",
        "value": f"{avg_cycle_time:.1f}d",
        "tier_status": cycle_time_tier,
        "metric_type": "sprint",
        "description": f"{validated_name}: Avg cycle time {avg_cycle_time:.1f} days (last {CYCLE_TIME_PERIOD_DAYS} days)",
        "tooltip": tooltip,
        "trend": cycle_time_trend,
        "action": {
            "type": "report",
            "report_ids": ["cycle-time-over-time"],
            "params": {
                "team_name": validated_name,
                "isGroup": isGroup
            }
        }
    }


def get_epic_cycle_time_metric(
    team_names_list: List[str],
    validated_name: str,
    isGroup: bool,
    conn: Connection
) -> Dict:
    """Get epic cycle time metric using 90-day (3 months) period calculation."""
    from datetime import timedelta
    
    today = date.today()
    current_end_date = today
    current_start_date = today - timedelta(days=EPIC_CYCLE_TIME_PERIOD_DAYS)
    previous_end_date = current_start_date - timedelta(days=1)
    previous_start_date = previous_end_date - timedelta(days=EPIC_CYCLE_TIME_PERIOD_DAYS)
    
    # Get epic cycle time for current period
    avg_cycle_time = get_cycle_time_for_period_by_issue_type(
        team_names_list, 
        current_start_date, 
        current_end_date, 
        'Epic',
        conn
    )
    if avg_cycle_time is None:
        avg_cycle_time = 0.0
    
    # Get epic cycle time for previous period (for trend)
    previous_cycle_time = get_cycle_time_for_period_by_issue_type(
        team_names_list, 
        previous_start_date, 
        previous_end_date,
        'Epic',
        conn
    )
    
    # Calculate trend
    cycle_time_trend = None
    if previous_cycle_time is not None and previous_cycle_time > 0 and avg_cycle_time > 0:
        cycle_time_trend = calculate_trend_for_cycle_time(avg_cycle_time, previous_cycle_time)
    
    # Get tier
    cycle_time_tier = get_epic_cycle_time_tier(avg_cycle_time)
    
    # Build tooltip - mention "3 months" as requested
    tooltip = f"Average epic cycle time in the last 3 months: {avg_cycle_time:.1f}d"
    if cycle_time_trend and cycle_time_trend.get('direction') and cycle_time_trend['direction'] != 'flat':
        if cycle_time_trend.get('improved'):
            tooltip += f"\nThis represents a {cycle_time_trend['percentage']}% improvement compared to the previous 3 months."
        else:
            tooltip += f"\nThis represents a {cycle_time_trend['percentage']}% regression compared to the previous 3 months."
    
    return {
        "metric_id": "epic_cycle_time",
        "label": "Avg Epic Cycle Time",
        "value": f"{avg_cycle_time:.1f}d",
        "tier_status": cycle_time_tier,
        "metric_type": "pi",
        "description": f"{validated_name}: Avg epic cycle time {avg_cycle_time:.1f} days (last 3 months)",
        "tooltip": tooltip,
        "trend": cycle_time_trend,
        "action": {
            "type": "report",
            "report_ids": ["cycle-time-over-time"],
            "params": {
                "team_name": validated_name,
                "isGroup": isGroup,
                "issue_type": "Epic"
            }
        }
    }


def get_predictability_metric(
    team_names_list: List[str],
    validated_sprint_count: int,
    validated_name: str,
    isGroup: bool,
    conn: Connection
) -> Dict:
    """
    Get predictability metric using sprint-based calculation with tier and trend.
    Measures what percentage of planned sprint work was actually completed.
    
    Trend compares last 3 closed sprints vs previous 3 closed sprints (sprints 4-6).
    Display shows average of last 3 closed sprints.
    """
    from collections import defaultdict
    from datetime import date as date_type
    
    logger.info(f"get_predictability_metric called: teams={team_names_list}, sprint_count={validated_sprint_count}")
    
    # Fetch 6 sprints for trend calculation (3 current + 3 previous)
    raw_data = get_team_avg_sprint_metrics(6, team_names_list, conn)
    logger.info(f"get_predictability_metric: fetched {len(raw_data)} raw sprint records")
    
    # Sort by date (oldest first)
    try:
        sorted_data = sorted(raw_data, key=lambda x: (
            x.get('sprint_complete_date') or date_type.min,
            x.get('out_sprint_id', 0)
        ))
        logger.info(f"get_predictability_metric: successfully sorted {len(sorted_data)} records")
    except Exception as e:
        logger.error(f"get_predictability_metric: Error sorting data: {e}")
        raise
    
    # Group by sprint_id and aggregate across teams (for groups)
    sprint_groups = defaultdict(lambda: {'completed': 0, 'planned': 0, 'date': None})
    for row in sorted_data:
        sprint_id = row.get('out_sprint_id')
        completed = row.get('issues_completed_count', 0) or 0
        planned = row.get('issues_in_sprint_count', 0) or 0
        date = row.get('sprint_complete_date')
        
        sprint_groups[sprint_id]['completed'] += completed
        sprint_groups[sprint_id]['planned'] += planned
        if not sprint_groups[sprint_id]['date']:
            sprint_groups[sprint_id]['date'] = date
    
    # Calculate per-sprint predictability and sort by date
    # Exclude sprints with 0 total planned issues (issues_at_start + issues_added = 0)
    sprint_predictabilities = []
    for sprint_id, data in sprint_groups.items():
        # Only include sprints with at least 1 planned issue
        if data['planned'] > 0:
            predictability = (data['completed'] / data['planned'] * 100) if data['planned'] > 0 else 0.0
            sprint_predictabilities.append({
                'sprint_id': sprint_id,
                'predictability': predictability,
                'completed': data['completed'],
                'planned': data['planned'],
                'date': data['date']
            })
    
    sprint_predictabilities.sort(key=lambda x: x['date'])
    
    # Calculate trend if we have enough data (4+ sprints)
    trend = None
    if len(sprint_predictabilities) >= 4:
        # Determine comparison periods based on available sprints
        if len(sprint_predictabilities) >= 6:
            # 6+ sprints: Compare last 3 vs previous 3
            current_sprints = sprint_predictabilities[-3:]
            previous_sprints = sprint_predictabilities[-6:-3]
            label = "vs previous 3 sprints"
        else:
            # 4-5 sprints: Compare last 2 vs previous 2
            current_sprints = sprint_predictabilities[-2:]
            previous_sprints = sprint_predictabilities[-4:-2]
            label = "vs previous 2 sprints"
        
        # Calculate average predictability for current period
        current_total_completed = sum(s['completed'] for s in current_sprints)
        current_total_planned = sum(s['planned'] for s in current_sprints)
        current_avg = (current_total_completed / current_total_planned * 100) if current_total_planned > 0 else 0.0
        
        # Calculate average predictability for previous period
        previous_total_completed = sum(s['completed'] for s in previous_sprints)
        previous_total_planned = sum(s['planned'] for s in previous_sprints)
        previous_avg = (previous_total_completed / previous_total_planned * 100) if previous_total_planned > 0 else 0.0
        
        # Calculate trend (higher predictability is better)
        if previous_avg > 0 and current_avg > 0:
            TREND_THRESHOLD = 0.1  # 0.1% minimum change
            percentage_float = ((current_avg - previous_avg) / previous_avg) * 100
            percentage = int(round(abs(percentage_float)))
            
            # Determine direction
            if percentage_float > TREND_THRESHOLD:
                direction = "up"
            elif percentage_float < -TREND_THRESHOLD:
                direction = "down"
            else:
                direction = "flat"
                percentage = 0
            
            # For predictability: higher is better (up = improved, down = not improved)
            improved = direction == "up"
            
            trend = {
                "direction": direction,
                "percentage": percentage,
                "label": label,
                "improved": improved
            }
    
    # Use sprints for display - match trend calculation when available
    if len(sprint_predictabilities) >= 6:
        # 6+ sprints: Use last 3 sprints (matches trend)
        display_sprints = sprint_predictabilities[-3:]
    elif len(sprint_predictabilities) >= 4:
        # 4-5 sprints: Use last 2 sprints (matches trend)
        display_sprints = sprint_predictabilities[-2:]
    else:
        # 3 or fewer: Use all available
        display_sprints = sprint_predictabilities
    
    total_completed = sum(s['completed'] for s in display_sprints)
    total_planned = sum(s['planned'] for s in display_sprints)
    avg_predictability = (total_completed / total_planned * 100) if total_planned > 0 else 0.0
    sprint_count_used = len(display_sprints)
    
    # Calculate tier
    predictability_tier = get_sprint_predictability_tier(avg_predictability)
    
    # Build tooltip matching cycle_time format
    tooltip = f"Average sprint predictability in the last {sprint_count_used} closed sprints: {avg_predictability:.1f}%"
    if trend and trend.get('direction') and trend['direction'] != 'flat':
        # Extract number of previous sprints from trend label
        previous_count = 3  # Default fallback
        if trend.get('label'):
            import re
            match = re.search(r'previous (\d+)', trend['label'])
            if match:
                previous_count = int(match.group(1))
        
        if trend.get('improved'):
            tooltip += f"\nThis represents a {trend['percentage']}% improvement compared to the previous {previous_count} sprints."
        else:
            tooltip += f"\nThis represents a {trend['percentage']}% regression compared to the previous {previous_count} sprints."
    
    return {
        "metric_id": "sprint_predictability",
        "label": "Avg Sprint Predictability",
        "value": f"{round(avg_predictability)}%",
        "tier_status": predictability_tier,
        "metric_type": "sprint",
        "description": f"{validated_name}: Avg predictability {avg_predictability:.0f}% (last {sprint_count_used} closed sprints)",
        "tooltip": tooltip,
        "trend": trend,
        "action": {
            "type": "report",
            "report_ids": ["sprint-predictability"],
            "params": {
                "team_name": validated_name,
                "isGroup": isGroup
            }
        }
    }


def get_wip_metric(
    team_names_list: List[str],
    validated_name: str,
    isGroup: bool,
    conn: Connection
) -> Dict:
    """
    Get Work in Progress metric from current active sprint with tier.
    No trend calculation (may be added in future phase).
    """
    # Get current sprint progress data
    progress_data = get_team_current_sprint_progress(team_names_list, conn)
    wip_count = progress_data.get('in_progress_issues', 0) or 0
    total_issues = progress_data.get('total_issues', 0) or 0
    sprint_id = progress_data.get('sprint_id')
    sprint_name = progress_data.get('sprint_name') or 'current sprint'
    
    # Check if there's no active sprint
    has_active_sprint = sprint_id is not None and total_issues > 0
    
    if not has_active_sprint:
        # No active sprint - return without tier_status (no badge will show)
        return {
            "metric_id": "sprint_wip",
            "label": "Sprint WIP",
            "value": "--",
            "tier_status": "",
            "metric_type": "sprint",
            "description": f"{validated_name}: No active sprint",
            "tooltip": f"Sprint Work In Progress\n\nNo active sprint found for {validated_name}. Sprint WIP metrics are only available when there is an active sprint.",
            "trend": None,
            "alternative_text": "No Sprint",
            "action": {
                "type": "report",
                "report_ids": ["team-sprint-burndown", "wip-over-time"],
                "params": {
                    "team_name": validated_name,
                    "isGroup": isGroup
                }
            }
        }
    
    # Calculate WIP percentage
    wip_percentage = (wip_count / total_issues * 100) if total_issues > 0 else 0.0
    
    # Calculate tier
    wip_tier = get_sprint_wip_tier(wip_percentage)
    
    # Build enhanced tooltip with tier explanation
    tooltip = f"Sprint Work In Progress\n\nCurrent sprint has {wip_count} issues in progress out of {total_issues} total issues ({wip_percentage:.1f}%)"
    
    # Add tier context to tooltip
    if wip_percentage <= SPRINT_WIP_HIGH_THRESHOLD:
        tooltip += f"\n\nHealthy WIP level (≤ {SPRINT_WIP_HIGH_THRESHOLD}% of sprint). Good flow with manageable work in progress."
    elif wip_percentage <= SPRINT_WIP_MEDIUM_THRESHOLD:
        tooltip += f"\n\nModerate WIP level ({SPRINT_WIP_HIGH_THRESHOLD}-{SPRINT_WIP_MEDIUM_THRESHOLD}% of sprint). Consider focusing on completing work before starting new items."
    else:
        tooltip += f"\n\nHigh WIP level (> {SPRINT_WIP_MEDIUM_THRESHOLD}% of sprint). Too much work in progress may indicate bottlenecks or multitasking issues."
    
    return {
        "metric_id": "sprint_wip",
        "label": "Sprint WIP",
        "value": str(wip_count),
        "tier_status": wip_tier,
        "metric_type": "sprint",
        "description": f"{validated_name}: {wip_count} issues in progress ({wip_percentage:.1f}% of {sprint_name})",
        "tooltip": tooltip,
        "trend": None,  # No trend for now
        "alternative_text": f"WIP%: {round(wip_percentage)}",
        "action": {
            "type": "report",
            "report_ids": ["team-sprint-burndown", "wip-over-time"],
            "params": {
                "team_name": validated_name,
                "isGroup": isGroup
            }
        }
    }


def get_completion_metric(
    team_names_list: List[str],
    validated_name: str,
    isGroup: bool,
    conn: Connection
) -> Dict:
    """
    Get Sprint Completion metric from current active sprint with tier.
    Uses timeline-based tier calculation during active sprint, simple thresholds after sprint ends.
    """
    progress_data = get_team_current_sprint_progress(team_names_list, conn)
    percent_completed = progress_data.get('percent_completed', 0) or 0.0
    start_date = progress_data.get('start_date')
    end_date = progress_data.get('end_date')
    completed_issues = progress_data.get('completed_issues', 0) or 0
    total_issues = progress_data.get('total_issues', 0) or 0
    sprint_id = progress_data.get('sprint_id')
    remaining_issues = total_issues - completed_issues
    sprint_name = progress_data.get('sprint_name') or 'current sprint'
    
    # Check if there's no active sprint
    has_active_sprint = sprint_id is not None and total_issues > 0
    
    if not has_active_sprint:
        # No active sprint - return without tier_status (no badge will show)
        return {
            "metric_id": "sprint_completion",
            "label": "Sprint Completion",
            "value": "--",
            "tier_status": "",
            "metric_type": "sprint",
            "description": f"{validated_name}: No active sprint",
            "tooltip": f"Sprint Completion\n\nNo active sprint found for {validated_name}. Sprint completion metrics are only available when there is an active sprint.",
            "trend": None,
            "alternative_text": "No Sprint",
            "action": {
                "type": "report",
                "report_ids": ["team-sprint-burndown"],
                "params": {
                    "team_name": validated_name,
                    "isGroup": isGroup
                }
            }
        }
    
    # Calculate tier using timeline-based logic
    completion_tier = get_sprint_completion_tier(
        percent_completed,
        start_date=start_date,
        end_date=end_date
    )
    
    # Build enhanced tooltip with tier explanation
    tooltip = f"Sprint Completion\n\nCurrent sprint has {completed_issues} completed issues out of {total_issues} total issues ({percent_completed:.1f}%)"
    
    # Add tier context to tooltip based on whether sprint is active
    if start_date and end_date:
        today = date.today()
        if isinstance(end_date, datetime):
            end_date_check = end_date.date()
        else:
            end_date_check = end_date
            
        if today >= end_date_check:
            # Sprint has ended - explain simple thresholds
            tooltip += f"\n\nSprint Completed: "
            if completion_tier == "high":
                tooltip += f"Excellent completion rate (≥ {SPRINT_COMPLETION_HIGH_THRESHOLD}%)."
            elif completion_tier == "medium":
                tooltip += f"Good completion rate ({SPRINT_COMPLETION_MEDIUM_THRESHOLD}-{SPRINT_COMPLETION_HIGH_THRESHOLD-1}%)."
            else:
                tooltip += f"Low completion rate (< {SPRINT_COMPLETION_MEDIUM_THRESHOLD}%). Consider sprint planning improvements."
        else:
            # Sprint is active - explain timeline-based logic
            if completion_tier == "high":
                tooltip += "\n\nOn track or ahead of schedule based on sprint timeline."
            elif completion_tier == "medium":
                tooltip += "\n\nSlightly behind schedule. Consider focusing on completing in-progress work."
            else:
                tooltip += "\n\nSignificantly behind schedule. May need to adjust scope or address blockers."
    else:
        # No dates available - explain simple thresholds
        if completion_tier == "high":
            tooltip += f"\n\nExcellent completion rate (≥ {SPRINT_COMPLETION_HIGH_THRESHOLD}%)."
        elif completion_tier == "medium":
            tooltip += f"\n\nGood completion rate ({SPRINT_COMPLETION_MEDIUM_THRESHOLD}-{SPRINT_COMPLETION_HIGH_THRESHOLD-1}%)."
        else:
            tooltip += f"\n\nLow completion rate (< {SPRINT_COMPLETION_MEDIUM_THRESHOLD}%)."
    
    return {
        "metric_id": "sprint_completion",
        "label": "Sprint Completion",
        "value": f"{round(percent_completed)}%",
        "tier_status": completion_tier,
        "metric_type": "sprint",
        "description": f"{validated_name}: {percent_completed:.1f}% completed in {sprint_name}",
        "tooltip": tooltip,
        "trend": None,
        "alternative_text": f"Remaining: {remaining_issues}",
        "action": {
            "type": "report",
            "report_ids": ["team-sprint-burndown"],
            "params": {
                "team_name": validated_name,
                "isGroup": isGroup
            }
        }
    }


def get_days_left_metric(
    team_names_list: List[str],
    validated_name: str,
    isGroup: bool,
    conn: Connection
) -> Dict:
    """Get Days Left metric from current active sprint."""
    progress_data = get_team_current_sprint_progress(team_names_list, conn)
    
    # Check if there's no active sprint
    sprint_id = progress_data.get('sprint_id')
    total_issues = progress_data.get('total_issues', 0) or 0
    has_active_sprint = sprint_id is not None and total_issues > 0
    
    if not has_active_sprint:
        # No active sprint - return without tier_status (no badge will show)
        return {
            "metric_id": "sprint_days_left",
            "label": "Days Left in Sprint",
            "value": "--",
            "tier_status": "",
            "metric_type": "sprint",
            "description": f"{validated_name}: No active sprint",
            "tooltip": "Days Left in Sprint\n\nNo active sprint found. Days remaining metrics are only available when there is an active sprint.",
            "trend": None,
            "alternative_text": "No Sprint",
            "chart_data": None,
            "action": {
                "type": "report",
                "report_ids": ["team-sprint-burndown"],
                "params": {
                    "team_name": validated_name,
                    "isGroup": isGroup
                }
            }
        }
    
    # Calculate days_left and days_in_sprint from dates
    start_date = progress_data.get('start_date')
    end_date = progress_data.get('end_date')
    sprint_name = progress_data.get('sprint_name') or 'current sprint'
    
    days_left = calculate_days_left(end_date) or 0
    days_in_sprint = calculate_days_in_sprint(start_date, end_date) or 0
    
    # DEBUG: Log data for troubleshooting progress bar
    logger.info(f"DEBUG days_left_metric: team={validated_name}, isGroup={isGroup}, start_date={start_date}, end_date={end_date}, days_left={days_left}, days_in_sprint={days_in_sprint}")
    
    chart_data = {
        "type": "progress",
        "current": days_left,
        "total": days_in_sprint,
        "percentage": round((days_in_sprint - days_left) / days_in_sprint * 100, 1) if days_in_sprint > 0 else 0
    } if days_in_sprint > 0 else None
    
    logger.info(f"DEBUG chart_data: {chart_data}")
    
    return {
        "metric_id": "sprint_days_left",
        "label": "Days Left in Sprint",
        "value": str(days_left),
        "tier_status": "",
        "metric_type": "sprint",
        "description": f"{validated_name}: {days_left} days remaining in {sprint_name}",
        "tooltip": "Number of days remaining in the current active sprint",
        "trend": None,
        "chart_data": chart_data,
        "action": {
            "type": "report",
            "report_ids": ["team-sprint-burndown"],
            "params": {
                "team_name": validated_name,
                "isGroup": isGroup,
                "days_in_sprint": days_in_sprint
            }
        }
    }


def get_open_bugs_metric(
    team_names_list: List[str],
    validated_name: str,
    isGroup: bool,
    conn: Connection
) -> Dict:
    """
    Get open bugs metric with tier-based trend logic.
    
    Returns current count of open bugs with adaptive tier thresholds and intelligent
    trend display based on overall health status.
    
    Tier Logic (adapts to team count):
    - High (green): ≤6 bugs per team (e.g., ≤6 for 1 team, ≤30 for 5 teams)
    - Medium (yellow): 7-15 bugs per team
    - Low (red): >15 bugs per team
    
    Trend Logic (prevents false alarms on small numbers):
    - When tier is "high" (healthy): Shows FLAT/NEUTRAL trend
      Rationale: At low bug counts (≤6 per team), small fluctuations (+1, +2 bugs)
      are normal noise. Users don't need red arrows when health is excellent.
      
    - When tier is "medium" or "low": Shows FULL TREND with arrows
      Rationale: Already have too many bugs - trend direction becomes critical.
      Users need to know if situation is improving (green ↓) or worsening (red ↑).
    
    Trend Calculation (for medium/low tiers):
    - Formula: net_change = bugs_created - bugs_resolved (over last 30 days)
    - Positive (+5): Red up arrow (backlog growing - bad)
    - Negative (-5): Green down arrow (backlog shrinking - good)
    - Zero: Flat (stable)
    
    Args:
        team_names_list: List of team names to aggregate bugs for
        validated_name: Team or group name for display/filtering
        isGroup: Whether this is a group (affects display only)
        conn: Database connection
        
    Returns:
        MetricResponse dict with metric_id, label, value, tier_status, trend, tooltip, action
    """
    from datetime import timedelta
    
    today = date.today()
    end_date = today
    start_date = today - timedelta(days=OPEN_BUGS_TREND_PERIOD_DAYS)
    
    # Get data from database - single efficient query
    bug_data = get_open_bugs_with_trend(
        team_names_list,
        BUG_ISSUE_TYPES,  # Use constant array ["Bug", "Defect"]
        start_date,
        end_date,
        conn
    )
    
    current_open_bugs = bug_data.get('current_open_bugs', 0)
    bugs_created = bug_data.get('bugs_created', 0)
    bugs_resolved = bug_data.get('bugs_resolved', 0)
    
    # Calculate team count for adaptive thresholds
    team_count = len(team_names_list) if team_names_list else 1
    
    # Calculate tier based on team count (adapts to group size)
    tier_status = get_open_bugs_tier(current_open_bugs, team_count)
    
    # Calculate net change for trend and tooltip
    net_change = bugs_created - bugs_resolved
    
    # IMPORTANT: Tier-based trend logic to prevent false alarms on small numbers
    # ============================================================================
    # When tier is "high" (green - healthy status with few bugs), we show a neutral trend
    # to avoid alarming users about minor fluctuations (e.g., 2→4 bugs showing red arrow).
    # 
    # Rationale:
    # - "High" tier = ≤6 bugs per team (e.g., ≤6 for single team, ≤30 for 5-team group)
    # - At this low bug count, small changes (+1, +2 bugs) are normal noise
    # - Users don't need to see red arrows when overall health is excellent
    # 
    # When tier is "medium" or "low" (already have too many bugs), the trend becomes
    # critical information - users need to know if the situation is improving or worsening.
    # ============================================================================
    
    if tier_status == "high":
        # Health is excellent - show neutral trend (no alarm for small changes)
        bug_trend = {
            "direction": "flat",
            "percentage": abs(net_change),
            "label": "vs previous 30 days",
            "improved": True  # Neutral/good status
        }
        
        # Enhanced tooltip for high tier - emphasize good health
        tooltip = f"Current open bugs: {current_open_bugs} ✓ (excellent)"
        if net_change != 0:
            # Show the change but frame it as minor
            change_sign = '+' if net_change > 0 else ''
            tooltip += f"\nMinor change: {change_sign}{net_change} bugs in last {OPEN_BUGS_TREND_PERIOD_DAYS} days"
            tooltip += f"\nCreated: {bugs_created}, Resolved: {bugs_resolved}"
        else:
            tooltip += f"\nNo change in last {OPEN_BUGS_TREND_PERIOD_DAYS} days"
    else:
        # Health is medium/low - show full trend with directional arrows
        # At this bug count, trend direction is critical information
        bug_trend = calculate_trend_for_open_bugs(bugs_created, bugs_resolved)
        
        # Standard tooltip emphasizing trend direction
        tooltip = f"Current open bugs: {current_open_bugs}"
        if bug_trend:
            if net_change > 0:
                tooltip += f"\n+{net_change} bugs in the last {OPEN_BUGS_TREND_PERIOD_DAYS} days (backlog growing)"
            elif net_change < 0:
                tooltip += f"\n{net_change} bugs in the last {OPEN_BUGS_TREND_PERIOD_DAYS} days (backlog shrinking)"
            else:
                tooltip += f"\nNo net change in the last {OPEN_BUGS_TREND_PERIOD_DAYS} days"
            tooltip += f"\nCreated: {bugs_created}, Resolved: {bugs_resolved}"
    
    return {
        "metric_id": "open_bugs",
        "label": "Number of Open Bugs",
        "value": str(current_open_bugs),
        "tier_status": tier_status,  # Tier adapts to team count
        "metric_type": "sprint",
        "description": f"{validated_name}: {current_open_bugs} open bugs",
        "tooltip": tooltip,
        "trend": bug_trend,
        "action": {
            "type": "report",
            "report_ids": [
                "team-issues-trend",        # Bug trend over time (default: issue_type=Bug)
                "issues-bugs-by-priority",  # Bugs by priority (default: issue_type=Bug)
                "issues-bugs-by-team"       # Bugs by team with priority (default: issue_type=Bug)
            ],
            "params": {
                "team_name": validated_name,
                "isGroup": isGroup,
                "issue_type": BUG_ISSUE_TYPES[0]                  # "Bug" - matches all report defaults
            }
        }
    }


def get_pi_completion_metric(
    pi_name: str,
    team_names_list: List[str],
    validated_name: str,
    isGroup: bool,
    conn: Connection
) -> Dict:
    """
    Get PI Completion metric from current PI using direct SQL query.
    Matches sprint completion pattern: queries live jira_issues table.
    Uses timeline-based tier calculation during active PI, returns None tier for first 15%.
    """
    try:
        # Build parameterized query
        placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names_list))])
        params = {
            'pi_name': pi_name,
            **{f"team_name_{i}": name for i, name in enumerate(team_names_list)}
        }
        
        sql_query = f"""
            SELECT 
                pi.pi_name,
                pi.start_date,
                pi.end_date,
                COUNT(*) as total_epics,
                COUNT(CASE WHEN i.status_category = 'Done' THEN 1 END) as completed_epics,
                COUNT(CASE WHEN i.status_category = 'In Progress' THEN 1 END) as in_progress_epics,
                COUNT(CASE WHEN i.status_category = 'To Do' THEN 1 END) as todo_epics
            FROM 
                public.jira_issues AS i
            INNER JOIN 
                public.pis AS pi
                ON i.quarter_pi = pi.pi_name
            WHERE 
                i.quarter_pi = :pi_name
                AND i.issue_type = 'Epic'
                AND i.team_name IN ({placeholders})
            GROUP BY 
                pi.pi_name, pi.start_date, pi.end_date;
        """
        
        logger.info(f"Fetching PI completion for PI={pi_name}, teams={team_names_list}")
        result = conn.execute(text(sql_query), params)
        row = result.fetchone()
        
        if not row:
            # No PI data found
            return {
                "metric_id": "pi_completion",
                "label": "PI Completion",
                "value": "0%",
                "tier_status": None,
                "metric_type": "pi",
                "description": "No epics found",
                "tooltip": f"PI Completion\n\nNo epics found for PI '{pi_name}'",
                "trend": None,
                "action": {
                    "type": "report",
                    "report_ids": ["pi-burndown"],
                    "params": {
                        "pi": pi_name,
                        "team_name": validated_name,
                        "isGroup": isGroup
                    }
                }
            }
        
        # Extract data from row
        start_date = row[1] if row[1] else None
        end_date = row[2] if row[2] else None
        total_epics = int(row[3]) if row[3] else 0
        completed_epics = int(row[4]) if row[4] else 0
        in_progress_epics = int(row[5]) if row[5] else 0
        todo_epics = int(row[6]) if row[6] else 0
        remaining_epics = total_epics - completed_epics
        
        # Calculate percentage in Python (not SQL) - more efficient
        percent_completed = (completed_epics / total_epics * 100) if total_epics > 0 else 0.0
        
        # Calculate tier using timeline-based logic (may return None for early PI)
        completion_tier = get_pi_completion_tier(
            percent_completed,
            start_date=start_date,
            end_date=end_date
        )
        
        # Build enhanced tooltip
        tooltip = f"PI Completion\n\nCurrent PI has {completed_epics} completed epics out of {total_epics} total epics ({percent_completed:.1f}%)"
        
        # Add tier context to tooltip based on whether PI is active
        if start_date and end_date:
            today = date.today()
            if isinstance(end_date, datetime):
                end_date_check = end_date.date()
            else:
                end_date_check = end_date
            
            if isinstance(start_date, datetime):
                start_date_check = start_date.date()
            else:
                start_date_check = start_date
            
            # Calculate progress percentage
            total_pi_days = (end_date_check - start_date_check).days
            if total_pi_days > 0:
                days_elapsed = (today - start_date_check).days
                progress_pct = (days_elapsed / total_pi_days) * 100
                
                if progress_pct <= 15:
                    tooltip += "\n\nPI is in early stage (first 15%). Tier status will appear after this initial phase."
                elif today >= end_date_check:
                    # PI has ended
                    tooltip += f"\n\nPI Completed: "
                    if completion_tier == "high":
                        tooltip += f"Excellent completion rate (≥ {PI_COMPLETION_HIGH_THRESHOLD}%)."
                    elif completion_tier == "medium":
                        tooltip += f"Good completion rate ({PI_COMPLETION_MEDIUM_THRESHOLD}-{PI_COMPLETION_HIGH_THRESHOLD-1}%)."
                    else:
                        tooltip += f"Low completion rate (< {PI_COMPLETION_MEDIUM_THRESHOLD}%)."
                else:
                    # PI is active
                    if completion_tier == "high":
                        tooltip += "\n\nOn track or ahead of schedule based on PI timeline."
                    elif completion_tier == "medium":
                        tooltip += "\n\nSlightly behind schedule. Consider focusing on completing in-progress work."
                    else:
                        tooltip += "\n\nSignificantly behind schedule. May need to adjust scope or address blockers."
        else:
            # No dates available - explain simple thresholds
            if completion_tier == "high":
                tooltip += f"\n\nExcellent completion rate (≥ {PI_COMPLETION_HIGH_THRESHOLD}%)."
            elif completion_tier == "medium":
                tooltip += f"\n\nGood completion rate ({PI_COMPLETION_MEDIUM_THRESHOLD}-{PI_COMPLETION_HIGH_THRESHOLD-1}%)."
            elif completion_tier == "low":
                tooltip += f"\n\nLow completion rate (< {PI_COMPLETION_MEDIUM_THRESHOLD}%)."
        
        return {
            "metric_id": "pi_completion",
            "label": "PI Completion",
            "value": f"{percent_completed:.1f}%",
            "tier_status": completion_tier,  # May be None for early PI
            "metric_type": "pi",
            "description": f"{validated_name}: {completed_epics} of {total_epics} epics completed in {pi_name}",
            "tooltip": tooltip,
            "trend": None,  # No trend for now
            "alternative_text": f"Remaining epics: {remaining_epics}",
            "action": {
                "type": "report",
                "report_ids": ["pi-burndown"],
                "params": {
                    "pi": pi_name,
                    "team_name": validated_name,
                    "isGroup": isGroup
                }
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting PI completion metric: {e}")
        raise


def get_pi_wip_metric(
    pi_name: str,
    team_names_list: List[str],
    validated_name: str,
    isGroup: bool,
    conn: Connection
) -> Dict:
    """
    Get PI Work in Progress metric with tier.
    Uses existing fetch_wip_data_from_db function from pis_service.
    """
    from pis_service import fetch_wip_data_from_db
    
    # Fetch WIP data
    wip_data = fetch_wip_data_from_db(
        pi=pi_name,
        team_names=team_names_list,
        project=None,
        conn=conn
    )
    
    wip_count = wip_data['in_progress_epics']
    total_epics = wip_data['total_epics']
    wip_percentage = wip_data['in_progress_percentage']
    
    # Calculate tier using Epic WIP thresholds
    wip_tier = get_epic_wip_tier(wip_percentage)
    
    # Build enhanced tooltip with tier explanation
    tooltip = f"PI Work In Progress\n\nCurrent PI has {wip_count} epics in progress out of {total_epics} total epics ({wip_percentage:.1f}%)"
    
    # Add tier context to tooltip
    if wip_percentage <= EPIC_WIP_HIGH_THRESHOLD:
        tooltip += f"\n\nHealthy WIP level (≤ {EPIC_WIP_HIGH_THRESHOLD}% of PI). Good flow with manageable work in progress."
    elif wip_percentage <= EPIC_WIP_MEDIUM_THRESHOLD:
        tooltip += f"\n\nModerate WIP level ({EPIC_WIP_HIGH_THRESHOLD}-{EPIC_WIP_MEDIUM_THRESHOLD}% of PI). Consider focusing on completing epics before starting new ones."
    else:
        tooltip += f"\n\nHigh WIP level (> {EPIC_WIP_MEDIUM_THRESHOLD}% of PI). Too much work in progress may indicate bottlenecks or scope issues."
    
    return {
        "metric_id": "pi_wip",
        "label": "PI WIP",
        "value": str(wip_count),
        "tier_status": wip_tier,
        "metric_type": "pi",
        "description": f"{validated_name}: {wip_count} epics in progress ({wip_percentage:.1f}% of {pi_name})",
        "tooltip": tooltip,
        "trend": None,  # No trend for now
        "alternative_text": f"WIP%: {round(wip_percentage)}",
        "action": {
            "type": "report",
            "report_ids": ["pi-burndown"],
            "params": {
                "pi": pi_name,
                "team_name": validated_name,
                "isGroup": isGroup
            }
        }
    }


def get_pi_inbound_dependencies_metric(
    pi_name: str,
    team_names_list: List[str],
    validated_name: str,
    isGroup: bool,
    conn: Connection
) -> Dict:
    """Get PI Inbound Dependencies as KPI metric with bar chart.
    
    Shows top 3 teams that rely on the selected team (inbound dependencies).
    Groups by team_name_of_epic (epic owner/relying team) and filters by team_name (assignee).
    """
    
    # Build parameterized query to get per-team breakdown
    placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names_list))])
    params = {"pi": pi_name}
    for i, name in enumerate(team_names_list):
        params[f"team_name_{i}"] = name
    
    # Query groups by team_name_of_epic (the relying team) instead of assignee_team
    query = text(f"""
        SELECT 
            team_name_of_epic AS relying_team,
            COUNT(issue_key) AS total_issues,
            COUNT(CASE WHEN status_category = 'Done' THEN 1 END) AS completed_issues,
            COUNT(issue_key) - COUNT(CASE WHEN status_category = 'Done' THEN 1 END) AS uncompleted_issues
        FROM jira_issues
        WHERE quarter_pi_of_epic = :pi
            AND team_name IN ({placeholders})
            AND dependency = TRUE
            AND issue_type != 'Epic'
            AND parent_key IS NOT NULL
        GROUP BY team_name_of_epic
        HAVING COUNT(issue_key) - COUNT(CASE WHEN status_category = 'Done' THEN 1 END) > 0
        ORDER BY uncompleted_issues DESC
        LIMIT 3
    """)
    
    result = conn.execute(query, params)
    rows = result.fetchall()
    
    all_teams = []
    total_all = 0
    
    for row in rows:
        total = int(row.total_issues)
        completed = int(row.completed_issues)
        uncompleted = int(row.uncompleted_issues)
        
        total_all += uncompleted
        all_teams.append({
            'label': row.relying_team,
            'value': uncompleted,
            'completed': completed,
            'total': total
        })
    
    top_3 = all_teams[:3]
    
    tooltip_lines = ["Teams That Depend On Us\n", "Top 3 teams:"]
    for item in top_3:
        tooltip_lines.append(
            f"{item['label']}: {item['value']} uncompleted "
            f"({item['completed']} completed, {item['total']} total)"
        )
    tooltip_lines.append(f"\nTotal across all teams: {total_all} dependencies")
    
    return {
        "metric_id": "pi_inbound_dependencies",
        "label": "Teams Depend On Us",
        "value": "",
        "tier_status": "",
        "metric_type": "pi",
        "description": f"Dependency heatmap for {validated_name} in {pi_name}",
        "tooltip": "\n".join(tooltip_lines),
        "trend": None,
        "chart_data": {
            "type": "bar",
            "items": top_3,
            "total_count": total_all
        },
        "alternative_text": f"Total: {total_all}",
        "action": {
            "type": "report",
            "report_ids": ["dependency-heatmap"],
            "params": {
                "pi": pi_name,
                "team_name": validated_name,
                "isGroup": isGroup
            }
        }
    }


def get_pi_outbound_dependencies_metric(
    pi_name: str,
    team_names_list: List[str],
    validated_name: str,
    isGroup: bool,
    conn: Connection
) -> Dict:
    """Get PI Outbound Dependencies as KPI metric with bar chart.
    
    Shows top 3 teams that the selected team relies on (outbound dependencies).
    Groups by team_name (assignee/relied-upon team) and filters by team_name_of_epic (owner).
    """
    
    # Build parameterized query to get per-team breakdown
    placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names_list))])
    params = {"pi": pi_name}
    for i, name in enumerate(team_names_list):
        params[f"team_name_{i}"] = name
    
    # Query groups by team_name (the relied-upon team) instead of owned_team
    query = text(f"""
        SELECT 
            team_name AS relied_upon_team,
            COUNT(issue_key) AS total_issues,
            COUNT(CASE WHEN status_category = 'Done' THEN 1 END) AS completed_issues,
            COUNT(issue_key) - COUNT(CASE WHEN status_category = 'Done' THEN 1 END) AS uncompleted_issues
        FROM jira_issues
        WHERE quarter_pi_of_epic = :pi
            AND team_name_of_epic IN ({placeholders})
            AND dependency = TRUE
            AND issue_type != 'Epic'
            AND parent_key IS NOT NULL
        GROUP BY team_name
        HAVING COUNT(issue_key) - COUNT(CASE WHEN status_category = 'Done' THEN 1 END) > 0
        ORDER BY uncompleted_issues DESC
        LIMIT 3
    """)
    
    result = conn.execute(query, params)
    rows = result.fetchall()
    
    all_teams = []
    total_all = 0
    
    for row in rows:
        total = int(row.total_issues)
        completed = int(row.completed_issues)
        uncompleted = int(row.uncompleted_issues)
        
        total_all += uncompleted
        all_teams.append({
            'label': row.relied_upon_team,
            'value': uncompleted,
            'completed': completed,
            'total': total
        })
    
    top_3 = all_teams[:3]
    
    tooltip_lines = ["Teams We Depend On\n", "Top 3 teams:"]
    for item in top_3:
        tooltip_lines.append(
            f"{item['label']}: {item['value']} uncompleted "
            f"({item['completed']} completed, {item['total']} total)"
        )
    tooltip_lines.append(f"\nTotal across all teams: {total_all} dependencies")
    
    return {
        "metric_id": "pi_outbound_dependencies",
        "label": "Teams We Depend On",
        "value": "",
        "tier_status": "",
        "metric_type": "pi",
        "description": f"Dependency heatmap for {validated_name} in {pi_name}",
        "tooltip": "\n".join(tooltip_lines),
        "trend": None,
        "chart_data": {
            "type": "bar",
            "items": top_3,
            "total_count": total_all
        },
        "alternative_text": f"Total: {total_all}",
        "action": {
            "type": "report",
            "report_ids": ["dependency-heatmap"],
            "params": {
                "pi": pi_name,
                "team_name": validated_name,
                "isGroup": isGroup
            }
        }
    }


@team_metrics_router.get("/team-metrics/general-kpis")
async def get_general_kpis(
    scope: str = Query("sprint", description="Scope: 'sprint' or 'pi'"),
    team_name: str = Query(..., description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    pi: Optional[str] = Query(None, description="PI name (required when scope='pi')"),
    metrics: Optional[str] = Query(None, description="Comma-separated list of metric IDs. If omitted, returns all available metrics for the scope."),
    sprint_count: int = Query(5, description="Number of sprints to average for velocity/predictability (default: 5, max: 20)", ge=1, le=20),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get general KPIs (Sprint or PI) in GitHub service KPI format.
    
    Supports two scopes via 'scope' parameter:
    - scope='sprint': Returns sprint-level metrics
    - scope='pi': Returns PI-level metrics
    
    Sprint metrics (scope='sprint'):
    - "sprint_velocity": Average sprint velocity (historical average over last N sprints)
    - "cycle_time": Average story cycle time (30-day period calculation with tier and trend)
    - "epic_cycle_time": Average epic cycle time (90-day/3-month period calculation with tier and trend)
    - "sprint_predictability": Average sprint predictability (historical average over last N sprints)
    - "sprint_wip": Sprint WIP (current active sprint)
    - "sprint_completion": Sprint Completion (current active sprint)
    - "sprint_days_left": Days Left in Sprint (current active sprint)
    - "open_bugs": Number of open bugs with trend (created vs resolved over last 30 days)
    
    PI metrics (scope='pi'):
    - "pi_wip": PI Work in Progress (current PI)
    - "pi_completion": PI Completion (current PI)
    - "epic_cycle_time": Average epic cycle time (90-day/3-month period)
    
    Parameters:
    - scope: 'sprint' or 'pi' (default: 'sprint')
    - team_name: Team name or group name (if isGroup=true)
    - isGroup: If true, team_name is treated as a group name
    - pi: PI name (required when scope='pi')
    - metrics: Comma-separated list of metric IDs. If omitted, returns all metrics for scope.
    - sprint_count: Number of sprints for velocity/predictability calculations (sprint scope only)
    
    Returns:
        Array of MetricResponse objects
    """
    try:
        # Validate scope parameter
        if scope not in ["sprint", "pi"]:
            raise HTTPException(
                status_code=400,
                detail="scope must be 'sprint' or 'pi'"
            )
        
        # Validate PI parameter if scope is 'pi'
        if scope == "pi" and not pi:
            raise HTTPException(
                status_code=400,
                detail="pi parameter is required when scope='pi'"
            )
        
        # Validate team_name and resolve to team_names_list
        validated_name = None
        if isGroup:
            validated_name = validate_group_name(team_name)
        else:
            validated_name = validate_team_name(team_name)
        
        team_names_list = resolve_team_names_from_filter(validated_name, isGroup, conn)
        
        # Route based on scope
        if scope == "sprint":
            # Sprint metrics
            validated_sprint_count = validate_sprint_count(sprint_count)
            
            available_metrics = ["open_bugs", "cycle_time", "epic_cycle_time", "sprint_velocity", "sprint_predictability", "sprint_wip", "sprint_completion", "sprint_days_left"]
            requested_metrics = available_metrics  # Default: return all
            
            if metrics:
                requested_metrics = [m.strip() for m in metrics.split(",")]
                requested_metrics = [m for m in requested_metrics if m in available_metrics]
            
            result_metrics = []
            for metric_id in requested_metrics:
                try:
                    if metric_id == "sprint_velocity":
                        metric = get_velocity_metric(team_names_list, validated_sprint_count, validated_name, isGroup, conn)
                    elif metric_id == "cycle_time":
                        metric = get_cycle_time_metric(team_names_list, validated_name, isGroup, conn)
                    elif metric_id == "epic_cycle_time":
                        metric = get_epic_cycle_time_metric(team_names_list, validated_name, isGroup, conn)
                    elif metric_id == "sprint_predictability":
                        # Always use 6 sprints for predictability (3 current + 3 previous for trend)
                        logger.info(f"Fetching predictability metric for team={validated_name}, isGroup={isGroup}")
                        metric = get_predictability_metric(team_names_list, 6, validated_name, isGroup, conn)
                        logger.info(f"Predictability metric result: {metric.get('value') if metric else 'None'}")
                    elif metric_id == "sprint_wip":
                        metric = get_wip_metric(team_names_list, validated_name, isGroup, conn)
                    elif metric_id == "sprint_completion":
                        metric = get_completion_metric(team_names_list, validated_name, isGroup, conn)
                    elif metric_id == "sprint_days_left":
                        metric = get_days_left_metric(team_names_list, validated_name, isGroup, conn)
                    elif metric_id == "open_bugs":
                        metric = get_open_bugs_metric(team_names_list, validated_name, isGroup, conn)
                    else:
                        continue
                    
                    if metric:
                        result_metrics.append(metric)
                except Exception as e:
                    logger.error(f"Error getting metric {metric_id}: {e}")
                    logger.exception(e)
                    continue
            
            return result_metrics
        
        else:  # scope == "pi"
            # PI metrics
            validated_pi = validate_pi(pi)
            
            available_metrics = ["pi_wip", "pi_completion", "pi_inbound_dependencies", "pi_outbound_dependencies", "epic_cycle_time"]
            requested_metrics = available_metrics  # Default: return all
            
            if metrics:
                requested_metrics = [m.strip() for m in metrics.split(",")]
                requested_metrics = [m for m in requested_metrics if m in available_metrics]
            
            result_metrics = []
            for metric_id in requested_metrics:
                try:
                    if metric_id == "pi_wip":
                        metric = get_pi_wip_metric(validated_pi, team_names_list, validated_name, isGroup, conn)
                    elif metric_id == "pi_completion":
                        metric = get_pi_completion_metric(validated_pi, team_names_list, validated_name, isGroup, conn)
                    elif metric_id == "epic_cycle_time":
                        metric = get_epic_cycle_time_metric(team_names_list, validated_name, isGroup, conn)
                    elif metric_id == "pi_inbound_dependencies":
                        metric = get_pi_inbound_dependencies_metric(validated_pi, team_names_list, validated_name, isGroup, conn)
                    elif metric_id == "pi_outbound_dependencies":
                        metric = get_pi_outbound_dependencies_metric(validated_pi, team_names_list, validated_name, isGroup, conn)
                    else:
                        continue
                    
                    if metric:
                        result_metrics.append(metric)
                except Exception as e:
                    logger.error(f"Error getting PI metric {metric_id}: {e}")
                    logger.exception(e)
                    continue
            
            return result_metrics
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_general_kpis: {e}")
        logger.exception(e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch sprint KPIs: {str(e)}"
        )
