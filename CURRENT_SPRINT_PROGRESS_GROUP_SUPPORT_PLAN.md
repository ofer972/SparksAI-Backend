# Plan: Add Group Support to Current Sprint Progress Endpoint

## Overview
Add `isGroup` parameter support to the `/team-metrics/current-sprint-progress` endpoint, allowing it to return aggregated sprint progress for all teams in a group, following the same pattern as the Count in Progress endpoint.

---

## Current Implementation Analysis

### Current Endpoint
- **Path**: `GET /team-metrics/current-sprint-progress`
- **Location**: `team_metrics_service.py` (lines 532-609)
- **Current Parameters**: 
  - `team_name` (required, string): Single team name
- **Current Behavior**: Returns current sprint progress for a single team

### Current Database Function
- **Function**: `get_team_current_sprint_progress(team_name: str, conn: Connection)`
- **Location**: `database_team_metrics.py` (lines 139-217)
- **Current SQL Query**: Direct SQL query (NOT a database function/view)

### Current SQL Query (Lines 154-176)

```sql
SELECT 
    s.sprint_id,
    s.name as sprint_name,
    s.start_date,
    s.end_date,
    COUNT(*) as total_issues,
    COUNT(CASE WHEN i.status_category = 'Done' THEN 1 END) as completed_issues,
    COUNT(CASE WHEN i.status_category = 'In Progress' THEN 1 END) as in_progress_issues,
    COUNT(CASE WHEN i.status_category = 'To Do' THEN 1 END) as todo_issues,
    (COUNT(CASE WHEN i.status_category = 'Done' THEN 1 END)::numeric * 100) 
        / NULLIF(COUNT(*), 0) as percent_completed
FROM 
    public.jira_issues AS i
INNER JOIN 
    public.jira_sprints AS s
    ON i.current_sprint_id = s.sprint_id
WHERE 
    i.team_name = :team_name
    AND s.state = 'active'
GROUP BY 
    s.sprint_id, s.name, s.start_date, s.end_date;
```

### How Current Sprint Progress Works
- **Method**: Direct SQL statement (NOT a view, NOT a database function)
- **Query Type**: Direct SELECT with JOIN between `jira_issues` and `jira_sprints` tables
- **Join**: `jira_issues.current_sprint_id = jira_sprints.sprint_id`
- **Filter**: `WHERE i.team_name = :team_name AND s.state = 'active'`
- **Grouping**: Groups by `sprint_id, name, start_date, end_date` to aggregate issue counts
- **Returns**: Single row (uses `fetchone()`) with aggregated counts for the team's active sprint

### Important Consideration: Multiple Teams, Multiple Sprints
When dealing with groups, different teams might have different active sprints. The SQL query groups by `sprint_id`, so:
- If all teams in the group have the **same active sprint**: Query will return **one row** with aggregated counts across all teams ✅
- If teams have **different active sprints**: Query will return **multiple rows** (one per sprint) ⚠️

**Solution**: 
- When multiple rows are returned (different sprints), **aggregate the data** instead of raising an error:
  - Sum: `total_issues`, `completed_issues`, `in_progress_issues`, `todo_issues` across all rows
  - Calculate `percent_completed` from aggregated totals: `(sum(completed_issues) / sum(total_issues)) * 100`
  - Calculate `percent_completed_status` and `in_progress_issues_status` based on aggregated data
  - **DO NOT return**: `sprint_id`, `sprint_name`, `days_left`, `days_in_sprint` (these are sprint-specific)
  - **DO return**: aggregated counts, `percent_completed`, `percent_completed_status`, `in_progress_issues_status`
- When single row is returned (same sprint), return all fields as before including sprint-specific fields

---

## Required Changes

### 1. Endpoint Changes (`team_metrics_service.py`)

**Current Signature** (line 533):
```python
async def get_current_sprint_progress(
    team_name: str = Query(..., description="Team name to get sprint progress for"),
    conn: Connection = Depends(get_db_connection)
):
```

**New Signature**:
```python
async def get_current_sprint_progress(
    team_name: str = Query(..., description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
```

**Changes Needed**:
1. Add `isGroup` parameter
2. Use `resolve_team_names_from_filter()` to resolve team names (already imported at line 24 ✅)
3. Update validation logic (validate team_name or group_name based on isGroup)
4. Pass `team_names_list` to database function instead of single `team_name`
5. Validate that all teams have the same active sprint (when isGroup=true)
6. Update response to include group metadata (group_name, teams_in_group) when isGroup=true

### 2. Database Function Changes (`database_team_metrics.py`)

**Current Signature** (line 139):
```python
def get_team_current_sprint_progress(team_name: str, conn: Connection = None) -> Dict[str, Any]:
```

**New Signature**:
```python
def get_team_current_sprint_progress(team_names: Optional[List[str]], conn: Connection = None) -> Dict[str, Any]:
```

**SQL Query Changes**:

**Current SQL** (lines 154-176):
```sql
WHERE 
    i.team_name = :team_name
    AND s.state = 'active'
```

**New SQL** (using `team_name IN` with placeholders pattern):
```sql
-- When team_names is provided:
WHERE 
    i.team_name IN (:team_name_0, :team_name_1, ...)
    AND s.state = 'active'

-- When team_names is None (all teams):
WHERE 
    s.state = 'active'
```

**Implementation Pattern**: Use the same pattern as `sprints_service.py` (lines 241-247) and `get_team_count_in_progress()`:
- Build parameterized IN clause with placeholders
- Create params dict with `team_name_0`, `team_name_1`, etc.
- If `team_names` is None, omit the team_name filter

**Important**: The query will return multiple rows if teams have different active sprints. The endpoint needs to handle this:
- If result has **1 row**: All teams have the same sprint - return all fields including sprint-specific fields ✅
- If result has **multiple rows**: Teams have different sprints - aggregate counts across all rows, exclude sprint-specific fields ⚠️
- If result has **0 rows**: No active sprint found - return empty/default data

### 3. Multiple Sprints Aggregation Logic

When multiple rows are returned (different sprints), we need to aggregate the data instead of raising an error. This should be done in the database function:

**Aggregation Logic**:
- Sum across all rows: `total_issues`, `completed_issues`, `in_progress_issues`, `todo_issues`
- Calculate `percent_completed` from aggregated totals: `(sum(completed_issues) / sum(total_issues)) * 100`
- Set `sprint_id`, `sprint_name`, `start_date`, `end_date` to `None` (indicates multiple sprints)
- Return aggregated data with a flag indicating multiple sprints

**Return Structure**:
- If 0 rows: Return empty/default data
- If 1 row: Return that row's data with all fields
- If multiple rows: Return aggregated data with sprint-specific fields set to None

---

## Implementation Steps

### Step 1: Update Database Function (`database_team_metrics.py`)

1. Change function signature from `team_name: str` to `team_names: Optional[List[str]]`
2. Update SQL query to handle:
   - Multiple teams: `WHERE i.team_name IN (:team_name_0, :team_name_1, ...)`
   - All teams: No team_name filter
3. Build parameterized IN clause dynamically
4. Change from `fetchone()` to `fetchall()` to handle multiple sprints
5. Add aggregation logic:
   - If 0 rows: Return empty/default data
   - If 1 row: Return that row's data with all fields
   - If multiple rows: 
     - Sum: `total_issues`, `completed_issues`, `in_progress_issues`, `todo_issues` across all rows
     - Calculate `percent_completed` from aggregated totals: `(sum(completed_issues) / sum(total_issues)) * 100`
     - Set `sprint_id`, `sprint_name`, `start_date`, `end_date` to `None`
     - Return aggregated data
6. Update function docstring

### Step 2: Update Endpoint (`team_metrics_service.py`)

1. Add `isGroup: bool = Query(False, ...)` parameter
2. Update parameter description for `team_name`
3. Add validation logic (validate team_name or group_name based on isGroup)
4. Call `resolve_team_names_from_filter(team_name, isGroup, conn)` to get team list
5. Pass `team_names_list` to `get_team_current_sprint_progress()` instead of single team_name
6. Handle multiple sprints case:
   - If `sprint_id` is `None` (indicates multiple sprints), exclude sprint-specific fields from response
   - Calculate `percent_completed_status` and `in_progress_issues_status` based on aggregated data
   - Only calculate `days_left` and `days_in_sprint` if `start_date` and `end_date` are not None
7. Update response to include:
   - `group_name` and `teams_in_group` when `isGroup=true`
   - `team_name` when `isGroup=false`
8. Update docstring

---

## SQL Query Pattern Reference

Following the pattern from `get_team_count_in_progress()` and `sprints_service.py`:

```python
if team_names:
    # Build parameterized IN clause
    placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names))])
    params = {f"team_name_{i}": name for i, name in enumerate(team_names)}
    
    sql_query = f"""
        SELECT 
            s.sprint_id,
            s.name as sprint_name,
            s.start_date,
            s.end_date,
            COUNT(*) as total_issues,
            COUNT(CASE WHEN i.status_category = 'Done' THEN 1 END) as completed_issues,
            COUNT(CASE WHEN i.status_category = 'In Progress' THEN 1 END) as in_progress_issues,
            COUNT(CASE WHEN i.status_category = 'To Do' THEN 1 END) as todo_issues,
            (COUNT(CASE WHEN i.status_category = 'Done' THEN 1 END)::numeric * 100) 
                / NULLIF(COUNT(*), 0) as percent_completed
        FROM 
            public.jira_issues AS i
        INNER JOIN 
            public.jira_sprints AS s
            ON i.current_sprint_id = s.sprint_id
        WHERE 
            i.team_name IN ({placeholders})
            AND s.state = 'active'
        GROUP BY 
            s.sprint_id, s.name, s.start_date, s.end_date;
    """
    
    result = conn.execute(text(sql_query), params)
    rows = result.fetchall()
    
    if len(rows) == 0:
        # No active sprint found
        return {
            'sprint_id': None,
            'sprint_name': None,
            'start_date': None,
            'end_date': None,
            'total_issues': 0,
            'completed_issues': 0,
            'in_progress_issues': 0,
            'todo_issues': 0,
            'percent_completed': 0.0
        }
    
    if len(rows) == 1:
        # Single row - all teams have same sprint
        row = rows[0]
        return {
            'sprint_id': int(row[0]) if row[0] else None,
            'sprint_name': str(row[1]) if row[1] else None,
            'start_date': row[2] if row[2] and hasattr(row[2], 'strftime') else None,
            'end_date': row[3] if row[3] and hasattr(row[3], 'strftime') else None,
            'total_issues': int(row[4]) if row[4] else 0,
            'completed_issues': int(row[5]) if row[5] else 0,
            'in_progress_issues': int(row[6]) if row[6] else 0,
            'todo_issues': int(row[7]) if row[7] else 0,
            'percent_completed': float(row[8]) if row[8] is not None else 0.0
        }
    
    # Multiple rows - different sprints, aggregate the data
    total_issues_sum = sum(int(row[4]) if row[4] else 0 for row in rows)
    completed_issues_sum = sum(int(row[5]) if row[5] else 0 for row in rows)
    in_progress_issues_sum = sum(int(row[6]) if row[6] else 0 for row in rows)
    todo_issues_sum = sum(int(row[7]) if row[7] else 0 for row in rows)
    
    # Calculate percent_completed from aggregated totals
    percent_completed = (completed_issues_sum / total_issues_sum * 100) if total_issues_sum > 0 else 0.0
    
    return {
        'sprint_id': None,  # Multiple sprints - no single sprint_id
        'sprint_name': None,  # Multiple sprints - no single sprint_name
        'start_date': None,  # Multiple sprints - no single start_date
        'end_date': None,  # Multiple sprints - no single end_date
        'total_issues': total_issues_sum,
        'completed_issues': completed_issues_sum,
        'in_progress_issues': in_progress_issues_sum,
        'todo_issues': todo_issues_sum,
        'percent_completed': percent_completed
    }
else:
    # No filter - return all teams (same query but no team_name filter)
    sql_query = """
        SELECT 
            s.sprint_id,
            s.name as sprint_name,
            s.start_date,
            s.end_date,
            COUNT(*) as total_issues,
            COUNT(CASE WHEN i.status_category = 'Done' THEN 1 END) as completed_issues,
            COUNT(CASE WHEN i.status_category = 'In Progress' THEN 1 END) as in_progress_issues,
            COUNT(CASE WHEN i.status_category = 'To Do' THEN 1 END) as todo_issues,
            (COUNT(CASE WHEN i.status_category = 'Done' THEN 1 END)::numeric * 100) 
                / NULLIF(COUNT(*), 0) as percent_completed
        FROM 
            public.jira_issues AS i
        INNER JOIN 
            public.jira_sprints AS s
            ON i.current_sprint_id = s.sprint_id
        WHERE 
            s.state = 'active'
        GROUP BY 
            s.sprint_id, s.name, s.start_date, s.end_date;
    """
    
    result = conn.execute(text(sql_query))
    rows = result.fetchall()
    
    # Same aggregation logic as above
    if len(rows) == 0:
        return {
            'sprint_id': None,
            'sprint_name': None,
            'start_date': None,
            'end_date': None,
            'total_issues': 0,
            'completed_issues': 0,
            'in_progress_issues': 0,
            'todo_issues': 0,
            'percent_completed': 0.0
        }
    
    if len(rows) == 1:
        row = rows[0]
        return {
            'sprint_id': int(row[0]) if row[0] else None,
            'sprint_name': str(row[1]) if row[1] else None,
            'start_date': row[2] if row[2] and hasattr(row[2], 'strftime') else None,
            'end_date': row[3] if row[3] and hasattr(row[3], 'strftime') else None,
            'total_issues': int(row[4]) if row[4] else 0,
            'completed_issues': int(row[5]) if row[5] else 0,
            'in_progress_issues': int(row[6]) if row[6] else 0,
            'todo_issues': int(row[7]) if row[7] else 0,
            'percent_completed': float(row[8]) if row[8] is not None else 0.0
        }
    
    # Multiple rows - aggregate
    total_issues_sum = sum(int(row[4]) if row[4] else 0 for row in rows)
    completed_issues_sum = sum(int(row[5]) if row[5] else 0 for row in rows)
    in_progress_issues_sum = sum(int(row[6]) if row[6] else 0 for row in rows)
    todo_issues_sum = sum(int(row[7]) if row[7] else 0 for row in rows)
    percent_completed = (completed_issues_sum / total_issues_sum * 100) if total_issues_sum > 0 else 0.0
    
    return {
        'sprint_id': None,
        'sprint_name': None,
        'start_date': None,
        'end_date': None,
        'total_issues': total_issues_sum,
        'completed_issues': completed_issues_sum,
        'in_progress_issues': in_progress_issues_sum,
        'todo_issues': todo_issues_sum,
        'percent_completed': percent_completed
    }
```

---

## Response Format Changes

### Current Response:
```json
{
    "success": true,
    "data": {
        "sprint_id": 123,
        "sprint_name": "Sprint 1",
        "days_left": 5,
        "days_in_sprint": 14,
        "total_issues": 20,
        "completed_issues": 8,
        "in_progress_issues": 7,
        "todo_issues": 5,
        "percent_completed": 40.0,
        "percent_completed_status": "yellow",
        "in_progress_issues_status": "yellow",
        "team_name": "Team Alpha"
    },
    "message": "Retrieved current sprint progress for team 'Team Alpha'"
}
```

### New Response (single team, isGroup=false):
```json
{
    "success": true,
    "data": {
        "sprint_id": 123,
        "sprint_name": "Sprint 1",
        "days_left": 5,
        "days_in_sprint": 14,
        "total_issues": 20,
        "completed_issues": 8,
        "in_progress_issues": 7,
        "todo_issues": 5,
        "percent_completed": 40.0,
        "percent_completed_status": "yellow",
        "in_progress_issues_status": "yellow",
        "team_name": "Team Alpha"
    },
    "message": "Retrieved current sprint progress for team 'Team Alpha'"
}
```

### New Response (group, isGroup=true, same sprint):
```json
{
    "success": true,
    "data": {
        "sprint_id": 123,
        "sprint_name": "Sprint 1",
        "days_left": 5,
        "days_in_sprint": 14,
        "total_issues": 65,
        "completed_issues": 28,
        "in_progress_issues": 22,
        "todo_issues": 15,
        "percent_completed": 43.1,
        "percent_completed_status": "yellow",
        "in_progress_issues_status": "yellow",
        "group_name": "Engineering Group",
        "teams_in_group": ["Team Alpha", "Team Beta", "Team Gamma"]
    },
    "message": "Retrieved current sprint progress for group 'Engineering Group'"
}
```

### New Response (group, isGroup=true, different sprints - aggregated):
```json
{
    "success": true,
    "data": {
        "total_issues": 85,
        "completed_issues": 35,
        "in_progress_issues": 30,
        "todo_issues": 20,
        "percent_completed": 41.2,
        "percent_completed_status": "yellow",
        "in_progress_issues_status": "yellow",
        "group_name": "Engineering Group",
        "teams_in_group": ["Team Alpha", "Team Beta", "Team Gamma"]
    },
    "message": "Retrieved current sprint progress for group 'Engineering Group'"
}
```

**Note**: When multiple sprints are found, the following fields are **NOT** returned:
- `sprint_id`
- `sprint_name`
- `days_left`
- `days_in_sprint`

---

## Summary

### What Needs to Change:

1. **SQL Statement**: 
   - Change from `WHERE i.team_name = :team_name` to `WHERE i.team_name IN (:team_name_0, :team_name_1, ...)`
   - Use parameterized IN clause pattern (same as `get_team_count_in_progress()`)
   - Handle None case (all teams) by omitting team_name filter

2. **Database Function**:
   - Change parameter from `team_name: str` to `team_names: Optional[List[str]]`
   - Update SQL query logic to handle list of teams
   - Change from `fetchone()` to `fetchall()` to handle multiple sprints
   - **Aggregation Logic for Multiple Sprints**:
     - If 1 row: Return all fields including sprint-specific fields
     - If multiple rows: 
       - Sum: `total_issues`, `completed_issues`, `in_progress_issues`, `todo_issues` across all rows
       - Calculate `percent_completed` from aggregated totals: `(sum(completed_issues) / sum(total_issues)) * 100`
       - Set `sprint_id`, `sprint_name`, `start_date`, `end_date` to `None`
       - Return aggregated data

3. **Endpoint**:
   - Add `isGroup` parameter
   - Use `resolve_team_names_from_filter()` to resolve teams
   - **Conditional Field Inclusion**:
     - If `sprint_id` is not None: Include `sprint_id`, `sprint_name`, `days_left`, `days_in_sprint`
     - If `sprint_id` is None: Exclude sprint-specific fields from response
   - Calculate `percent_completed_status` and `in_progress_issues_status` based on aggregated data
   - Update response metadata to include group info when applicable

### Key Behavior Changes:

**When Multiple Sprints Found (Multiple Rows)**:
- ✅ **Aggregate** counts across all sprints (sum total_issues, completed_issues, in_progress_issues, todo_issues)
- ✅ **Calculate** percent_completed from aggregated totals
- ✅ **Calculate** percent_completed_status and in_progress_issues_status from aggregated data
- ❌ **Exclude** sprint_id, sprint_name, days_left, days_in_sprint from response
- ✅ **Include** aggregated counts, percent_completed, status indicators, group metadata

**When Single Sprint Found (Single Row)**:
- ✅ **Return** all fields as before (including sprint-specific fields)
- ✅ **Calculate** all derived fields (days_left, days_in_sprint, status indicators)

### How Current Sprint Progress Works:
- **Direct SQL query** (NOT a view, NOT a database function)
- Queries `public.jira_issues` table with JOIN to `public.jira_sprints`
- Filters by `i.team_name = :team_name AND s.state = 'active'`
- Groups by `sprint_id, name, start_date, end_date` and counts issues by status
- Returns single row with aggregated counts for the team's active sprint

### Key Challenge:
- When `isGroup=true`, different teams might have different active sprints
- SQL query groups by `sprint_id`, so multiple rows = different sprints
- Solution: When multiple rows are returned, aggregate the counts and exclude sprint-specific fields

---

## Testing Checklist

- [ ] Single team (isGroup=false) - should work as before
- [ ] Group with multiple teams, same sprint (isGroup=true) - should aggregate counts, include all fields
- [ ] Group with multiple teams, different sprints (isGroup=true) - should aggregate counts, exclude sprint-specific fields
- [ ] Group with single team (isGroup=true) - should work correctly
- [ ] Invalid team name - should return 404
- [ ] Invalid group name - should return 404
- [ ] Empty group - should return 404
- [ ] Team with no active sprint - should return empty/default data
- [ ] Verify counts are aggregated correctly across teams (when same sprint)
- [ ] Verify counts are aggregated correctly across sprints (when different sprints)
- [ ] Verify percent_completed is calculated correctly from aggregated totals
- [ ] Verify percent_completed_status and in_progress_issues_status are calculated from aggregated data
- [ ] Verify sprint-specific fields (sprint_id, sprint_name, days_left, days_in_sprint) are excluded when multiple sprints
- [ ] Verify response includes correct metadata (group_name vs team_name)

