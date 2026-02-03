"""
Database queries for validation reports.
Simple SQL queries for issue validation checks.
Each query supports both full issue list and count-only modes.
"""

from typing import List, Dict, Any, Union, Optional
from sqlalchemy import text
from sqlalchemy.engine import Connection


def query_old_bugs(
    conn: Connection, 
    days_back: int, 
    threshold_days: int,
    count_only: bool = False,
    team_names: Optional[List[str]] = None
) -> Union[List[Dict[str, Any]], int]:
    """
    Query for unresolved bugs older than threshold.
    
    Args:
        conn: Database connection
        days_back: Number of days to look back
        threshold_days: Days threshold for considering bug "old"
        count_only: If True, return only count. If False, return full issue list.
        team_names: Optional list of team names to filter by
    
    Returns:
        If count_only=True: int (count of issues)
        If count_only=False: List[Dict] (full issue details)
    """
    params = {"threshold_days": threshold_days, "days_back": days_back}
    
    if count_only:
        select_clause = "SELECT COUNT(*) as count"
        order_limit_clause = ""
    else:
        select_clause = """
            SELECT 
                issue_key,
                summary,
                issue_type,
                team_name,
                status,
                status_category,
                created_at,
                updated_at
        """
        order_limit_clause = """
            ORDER BY created_at ASC
            LIMIT 1000
        """
    
    # Build team filter
    team_filter = ""
    if team_names:
        if len(team_names) == 1:
            team_filter = "AND team_name = :team_name"
            params["team_name"] = team_names[0]
        else:
            placeholders = ', '.join([f":team_{i}" for i in range(len(team_names))])
            team_filter = f"AND team_name IN ({placeholders})"
            for i, team in enumerate(team_names):
                params[f"team_{i}"] = team
    
    query = text(f"""
        {select_clause}
        FROM jira_issues
        WHERE issue_type = 'Bug'
          AND status_category != 'Done'
          AND created_at < NOW() - (:threshold_days || ' days')::interval
          AND updated_at >= NOW() - (:days_back || ' days')::interval
          {team_filter}
        {order_limit_clause}
    """)
    
    result = conn.execute(query, params)
    
    if count_only:
        return result.scalar() or 0
    else:
        return [dict(row._mapping) for row in result]


def query_dragged_sprints(
    conn: Connection, 
    days_back: int, 
    threshold: int,
    count_only: bool = False,
    team_names: Optional[List[str]] = None
) -> Union[List[Dict[str, Any]], int]:
    """
    Query for issues appearing in too many sprints.
    Uses sprint_ids array column.
    
    Args:
        conn: Database connection
        days_back: Number of days to look back
        threshold: Sprint count threshold
        count_only: If True, return only count. If False, return full issue list.
        team_names: Optional list of team names to filter by
    
    Returns:
        If count_only=True: int (count of issues)
        If count_only=False: List[Dict] (full issue details)
    """
    params = {"threshold": threshold, "days_back": days_back}
    
    if count_only:
        select_clause = "SELECT COUNT(*) as count"
        order_limit_clause = ""
    else:
        select_clause = """
            SELECT 
                issue_key,
                summary,
                issue_type,
                team_name,
                status,
                status_category,
                updated_at,
                array_length(sprint_ids, 1) as sprint_count
        """
        order_limit_clause = """
            ORDER BY array_length(sprint_ids, 1) DESC
            LIMIT 1000
        """
    
    # Build team filter
    team_filter = ""
    if team_names:
        if len(team_names) == 1:
            team_filter = "AND team_name = :team_name"
            params["team_name"] = team_names[0]
        else:
            placeholders = ', '.join([f":team_{i}" for i in range(len(team_names))])
            team_filter = f"AND team_name IN ({placeholders})"
            for i, team in enumerate(team_names):
                params[f"team_{i}"] = team
    
    query = text(f"""
        {select_clause}
        FROM jira_issues
        WHERE sprint_ids IS NOT NULL
          AND array_length(sprint_ids, 1) > :threshold
          AND updated_at >= NOW() - (:days_back || ' days')::interval
          AND status_category != 'Done'
          {team_filter}
        {order_limit_clause}
    """)
    
    result = conn.execute(query, params)
    
    if count_only:
        return result.scalar() or 0
    else:
        return [dict(row._mapping) for row in result]


def query_epic_health_issues(
    conn: Connection, 
    days_back: int, 
    max_children: int,
    count_only: bool = False,
    team_names: Optional[List[str]] = None,
    pi_names: Optional[List[str]] = None
) -> Union[List[Dict[str, Any]], int]:
    """
    Query for epics with child issue problems.
    Finds epics that are Done but have open children, or have too many children.
    
    Args:
        conn: Database connection
        days_back: Number of days to look back
        max_children: Maximum children threshold
        count_only: If True, return only count. If False, return full issue list.
        team_names: Optional list of team names to filter by
        pi_names: Optional list of PI names to filter by
    
    Returns:
        If count_only=True: int (count of issues)
        If count_only=False: List[Dict] (full issue details)
    """
    params = {"days_back": days_back, "max_children": max_children}
    
    # Build team filter
    team_filter = ""
    if team_names:
        if len(team_names) == 1:
            team_filter = "AND parent.team_name = :team_name"
            params["team_name"] = team_names[0]
        else:
            placeholders = ', '.join([f":team_{i}" for i in range(len(team_names))])
            team_filter = f"AND parent.team_name IN ({placeholders})"
            for i, team in enumerate(team_names):
                params[f"team_{i}"] = team
    
    # Build PI filter
    pi_filter = ""
    if pi_names:
        if len(pi_names) == 1:
            pi_filter = "AND parent.quarter_pi = :pi_name"
            params["pi_name"] = pi_names[0]
        else:
            placeholders = ', '.join([f":pi_{i}" for i in range(len(pi_names))])
            pi_filter = f"AND parent.quarter_pi IN ({placeholders})"
            for i, pi in enumerate(pi_names):
                params[f"pi_{i}"] = pi
    
    # CTE is shared between both modes
    cte = f"""
        WITH epic_stats AS (
            SELECT 
                parent.issue_key,
                parent.summary,
                parent.issue_type,
                parent.team_name,
                parent.status,
                parent.status_category,
                parent.updated_at,
                COUNT(child.issue_key) as total_children,
                COUNT(CASE WHEN child.status_category != 'Done' THEN 1 END) as open_children
            FROM jira_issues parent
            LEFT JOIN jira_issues child ON child.parent_key = parent.issue_key
            WHERE parent.issue_type = 'Epic'
              AND parent.updated_at >= NOW() - (:days_back || ' days')::interval
              {team_filter}
              {pi_filter}
            GROUP BY parent.issue_key, parent.summary, parent.issue_type, 
                     parent.team_name, parent.status, parent.status_category, parent.updated_at
        )
    """
    
    if count_only:
        query = text(f"""
            {cte}
            SELECT COUNT(*) as count
            FROM epic_stats
            WHERE (status_category = 'Done' AND open_children > 0)
               OR total_children > :max_children
        """)
    else:
        query = text(f"""
            {cte}
            SELECT 
                issue_key,
                summary,
                issue_type,
                team_name,
                status,
                updated_at,
                total_children,
                open_children,
                status_category
            FROM epic_stats
            WHERE (status_category = 'Done' AND open_children > 0)
               OR total_children > :max_children
            ORDER BY open_children DESC, total_children DESC
            LIMIT 1000
        """)
    
    result = conn.execute(query, params)
    
    if count_only:
        return result.scalar() or 0
    else:
        return [dict(row._mapping) for row in result]


def query_stuck_in_progress(
    conn: Connection, 
    days_back: int, 
    stories_threshold: int,
    epics_threshold: int,
    count_only: bool = False,
    hierarchy_level: Optional[int] = None,
    team_names: Optional[List[str]] = None,
    pi_names: Optional[List[str]] = None
) -> Union[List[Dict[str, Any]], int]:
    """
    Query for issues stuck in progress status category.
    Stories (hierarchy_level=0): Use stories_threshold
    Epics (hierarchy_level=1): Use epics_threshold
    
    Args:
        conn: Database connection
        days_back: Number of days to look back
        stories_threshold: Days threshold for stories
        epics_threshold: Days threshold for epics
        count_only: If True, return only count. If False, return full issue list.
        hierarchy_level: Optional filter - 0 for stories/bugs/tasks, 1 for epics, None for both
        team_names: Optional list of team names to filter by
        pi_names: Optional list of PI names to filter by (applies only to epics)
    
    Returns:
        If count_only=True: int (count of issues)
        If count_only=False: List[Dict] (full issue details)
    """
    params = {
        "days_back": days_back,
        "stories_threshold": stories_threshold,
        "epics_threshold": epics_threshold
    }
    
    if count_only:
        select_clause = "SELECT COUNT(*) as count"
        order_limit_clause = ""
    else:
        select_clause = """
            SELECT 
                ji.issue_key,
                ji.summary,
                ji.issue_type,
                ji.team_name,
                ji.status,
                ji.status_category,
                ji.updated_at,
                ji.first_date_in_progress as in_progress_since
        """
        order_limit_clause = """
            ORDER BY ji.first_date_in_progress ASC
            LIMIT 1000
        """
    
    # Build team filter
    team_filter = ""
    if team_names:
        if len(team_names) == 1:
            team_filter = "AND ji.team_name = :team_name"
            params["team_name"] = team_names[0]
        else:
            placeholders = ', '.join([f":team_{i}" for i in range(len(team_names))])
            team_filter = f"AND ji.team_name IN ({placeholders})"
            for i, team in enumerate(team_names):
                params[f"team_{i}"] = team
    
    # Build PI filter (only for epics, hierarchy_level=1)
    pi_filter = ""
    if pi_names and hierarchy_level == 1:
        if len(pi_names) == 1:
            pi_filter = "AND ji.quarter_pi = :pi_name"
            params["pi_name"] = pi_names[0]
        else:
            placeholders = ', '.join([f":pi_{i}" for i in range(len(pi_names))])
            pi_filter = f"AND ji.quarter_pi IN ({placeholders})"
            for i, pi in enumerate(pi_names):
                params[f"pi_{i}"] = pi
    
    # Build WHERE clause using hierarchy_level parameter directly
    # hierarchy_level=0: stories, bugs, tasks (uses stories_threshold)
    # hierarchy_level=1: epics (uses epics_threshold)
    # hierarchy_level=None: both (uses OR condition with both thresholds)
    if hierarchy_level is not None:
        # Use parameter directly - cleaner and more flexible
        params["hierarchy_level"] = hierarchy_level
        # Determine threshold based on hierarchy_level
        threshold = stories_threshold if hierarchy_level == 0 else epics_threshold
        params["threshold"] = threshold
        type_filter = f"""
            AND it."hierarchyLevel" = :hierarchy_level
            AND ji.first_date_in_progress < NOW() - (:threshold || ' days')::interval
            {pi_filter}
        """
    else:
        # Both stories and epics (PI filter not applicable here since it's mixed)
        type_filter = """
            AND (
                (it."hierarchyLevel" = 0 
                 AND ji.first_date_in_progress < NOW() - (:stories_threshold || ' days')::interval)
                OR 
                (it."hierarchyLevel" = 1 
                 AND ji.first_date_in_progress < NOW() - (:epics_threshold || ' days')::interval)
            )
        """
    
    query = text(f"""
        {select_clause}
        FROM jira_issues ji
        LEFT JOIN issue_types it ON ji.issue_type = it.issue_type
        WHERE ji.status_category = 'In Progress'
          AND ji.first_date_in_progress IS NOT NULL
          AND ji.updated_at >= NOW() - (:days_back || ' days')::interval
          {team_filter}
          {type_filter}
        {order_limit_clause}
    """)
    
    result = conn.execute(query, params)
    
    if count_only:
        return result.scalar() or 0
    else:
        return [dict(row._mapping) for row in result]

