"""
Core validation logic for issue validation reports.
Each function implements one validation type.
"""

from typing import Dict, Any, List
from datetime import datetime
from sqlalchemy.engine import Connection

from database_validation_queries import (
    query_old_bugs,
    query_dragged_sprints,
    query_epic_health_issues,
    query_stuck_in_progress
)


def validate_old_bugs(conn: Connection, days_back: int, old_bugs_threshold: int, **kwargs) -> Dict[str, Any]:
    """
    Find unresolved bugs older than threshold.
    """
    team_names = kwargs.get('team_names')
    rows = query_old_bugs(conn, days_back, old_bugs_threshold, count_only=False, team_names=team_names)
    
    issues = []
    for row in rows:
        days_old = (datetime.utcnow().date() - row['created_at'].date()).days
        issues.append({
            "issue_key": row['issue_key'],
            "issue_summary": row['summary'],
            "issue_type": row['issue_type'],
            "team_name": row.get('team_name'),
            "status": row['status'],
            "status_category": row.get('status_category'),
            "updated_at": row['updated_at'].isoformat() if row['updated_at'] else None,
            "additional_comment": f"Open for {days_old} days (created {row['created_at'].strftime('%Y-%m-%d')})"
        })
    
    return {
        "validation_type": "old_bugs",
        "validation_title": "Aged Unresolved Bugs",
        "issue_count": len(issues),
        "issues": issues
    }


def validate_dragged_sprints(conn: Connection, days_back: int, dragged_threshold: int, **kwargs) -> Dict[str, Any]:
    """
    Find issues that appeared in too many sprints.
    """
    team_names = kwargs.get('team_names')
    rows = query_dragged_sprints(conn, days_back, dragged_threshold, count_only=False, team_names=team_names)
    
    issues = []
    for row in rows:
        sprint_count = row['sprint_count']
        issues.append({
            "issue_key": row['issue_key'],
            "issue_summary": row['summary'],
            "issue_type": row['issue_type'],
            "team_name": row.get('team_name'),
            "status": row['status'],
            "status_category": row.get('status_category'),
            "updated_at": row['updated_at'].isoformat() if row['updated_at'] else None,
            "additional_comment": f"Appeared in {sprint_count} sprints (threshold: {dragged_threshold})"
        })
    
    return {
        "validation_type": "dragged_sprints",
        "validation_title": "Issues Dragged Across Sprints",
        "issue_count": len(issues),
        "issues": issues
    }


def validate_epic_health(conn: Connection, days_back: int, epic_max_children: int, **kwargs) -> Dict[str, Any]:
    """
    Find epics with child issue problems.
    """
    team_names = kwargs.get('team_names')
    pi_names = kwargs.get('pi_names')
    rows = query_epic_health_issues(conn, days_back, epic_max_children, count_only=False, team_names=team_names, pi_names=pi_names)
    
    issues = []
    for row in rows:
        if row['status_category'] == 'Done' and row['open_children'] > 0:
            comment = f"Epic is Done but has {row['open_children']} open child issues"
        elif row['total_children'] > epic_max_children:
            comment = f"Epic has {row['total_children']} children (threshold: {epic_max_children})"
        else:
            comment = f"Epic: {row['total_children']} total, {row['open_children']} open children"
        
        issues.append({
            "issue_key": row['issue_key'],
            "issue_summary": row['summary'],
            "issue_type": row['issue_type'],
            "team_name": row.get('team_name'),
            "status": row['status'],
            "status_category": row.get('status_category'),
            "updated_at": row['updated_at'].isoformat() if row['updated_at'] else None,
            "additional_comment": comment
        })
    
    return {
        "validation_type": "epic_health",
        "validation_title": "Epics with Child Issues Problems",
        "issue_count": len(issues),
        "issues": issues
    }


def validate_stuck_in_progress(
    conn: Connection, 
    days_back: int, 
    stories_threshold: int, 
    epics_threshold: int, 
    **kwargs
) -> Dict[str, Any]:
    """
    Find issues stuck in progress status category for too long.
    Uses different thresholds for stories (hierarchy_level=0) vs epics.
    """
    team_names = kwargs.get('team_names')
    pi_names = kwargs.get('pi_names')
    hierarchy_level = kwargs.get('hierarchy_level')
    
    rows = query_stuck_in_progress(
        conn, days_back, stories_threshold, epics_threshold, 
        count_only=False, hierarchy_level=hierarchy_level, team_names=team_names, pi_names=pi_names
    )
    
    issues = []
    for row in rows:
        days_stuck = (datetime.utcnow().date() - row['in_progress_since'].date()).days
        
        # Determine which threshold was used
        if row['issue_type'] == 'Epic':
            threshold_used = epics_threshold
        else:
            threshold_used = stories_threshold
        
        issues.append({
            "issue_key": row['issue_key'],
            "issue_summary": row['summary'],
            "issue_type": row['issue_type'],
            "team_name": row.get('team_name'),
            "status": row['status'],
            "status_category": row.get('status_category'),
            "updated_at": row['updated_at'].isoformat() if row['updated_at'] else None,
            "additional_comment": f"In Progress for {days_stuck} days (threshold: {threshold_used}d, since {row['in_progress_since'].strftime('%Y-%m-%d')})"
        })
    
    return {
        "validation_type": "stuck_in_progress",
        "validation_title": "Issues Stuck In Progress",
        "issue_count": len(issues),
        "issues": issues
    }

