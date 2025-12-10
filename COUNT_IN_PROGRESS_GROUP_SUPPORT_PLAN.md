# Plan: Add Group Support to Count-In-Progress Endpoint

## Overview
Add `isGroup` parameter support to the `/team-metrics/count-in-progress` endpoint, allowing it to return counts for all teams in a group, following the same pattern as other endpoints.

---

## Current Implementation Analysis

### Current Endpoint
- **Path**: `GET /team-metrics/count-in-progress`
- **Location**: `team_metrics_service.py` (lines 467-508)
- **Current Parameters**: 
  - `team_name` (required, string): Single team name
- **Current Behavior**: Returns count of issues in progress for a single team

### Current Database Function
- **Function**: `get_team_count_in_progress(team_name: str, conn: Connection)`
- **Location**: `database_team_metrics.py` (lines 68-115)
- **Current SQL Query**: Direct SQL query (NOT a database function/view)
  ```sql
  SELECT 
      issue_type,
      COUNT(*) as type_count
  FROM public.jira_issues
  WHERE team_name = :team_name 
  AND status_category = 'In Progress'
  GROUP BY issue_type
  ORDER BY type_count DESC;
  ```

### How Count-In-Progress Works
- **Method**: Direct SQL statement (NOT a view, NOT a database function)
- **Query Type**: Direct SELECT from `public.jira_issues` table
- **Filter**: `WHERE team_name = :team_name AND status_category = 'In Progress'`
- **Grouping**: Groups by `issue_type` and counts occurrences

---

## Required Changes

### 1. Endpoint Changes (`team_metrics_service.py`)

**Current Signature** (line 468):
```python
async def get_count_in_progress(
    team_name: str = Query(..., description="Team name to get count for"),
    conn: Connection = Depends(get_db_connection)
):
```

**New Signature**:
```python
async def get_count_in_progress(
    team_name: str = Query(..., description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
```

**Changes Needed**:
1. Add `isGroup` parameter
2. Import `resolve_team_names_from_filter` (already imported at line 24 ✅)
3. Resolve team names using `resolve_team_names_from_filter(team_name, isGroup, conn)`
4. Update validation logic (validate team_name or group_name based on isGroup)
5. Pass `team_names_list` (or None) to database function instead of single `team_name`
6. Update response to include group metadata (group_name, teams_in_group) when isGroup=true

### 2. Database Function Changes (`database_team_metrics.py`)

**Current Signature** (line 68):
```python
def get_team_count_in_progress(team_name: str, conn: Connection = None) -> Dict[str, Any]:
```

**New Signature**:
```python
def get_team_count_in_progress(team_names: Optional[List[str]], conn: Connection = None) -> Dict[str, Any]:
```

**SQL Query Changes**:

**Current SQL** (lines 82-91):
```sql
SELECT 
    issue_type,
    COUNT(*) as type_count
FROM public.jira_issues
WHERE team_name = :team_name 
AND status_category = 'In Progress'
GROUP BY issue_type
ORDER BY type_count DESC;
```

**New SQL** (using `team_name IN` with placeholders pattern):
```sql
-- When team_names is provided:
SELECT 
    issue_type,
    COUNT(*) as type_count
FROM public.jira_issues
WHERE team_name IN (:team_name_0, :team_name_1, ...) 
AND status_category = 'In Progress'
GROUP BY issue_type
ORDER BY type_count DESC;

-- When team_names is None (all teams):
SELECT 
    issue_type,
    COUNT(*) as type_count
FROM public.jira_issues
WHERE status_category = 'In Progress'
GROUP BY issue_type
ORDER BY type_count DESC;
```

**Implementation Pattern**: Use the same pattern as `sprints_service.py` (lines 241-247):
- Build parameterized IN clause with placeholders
- Create params dict with `team_name_0`, `team_name_1`, etc.
- If `team_names` is None, omit the WHERE clause for team_name

---

## Implementation Steps

### Step 1: Update Database Function (`database_team_metrics.py`)

1. Change function signature from `team_name: str` to `team_names: Optional[List[str]]`
2. Update SQL query to handle:
   - Multiple teams: `WHERE team_name IN (:team_name_0, :team_name_1, ...)`
   - All teams: No team_name filter
3. Build parameterized IN clause dynamically
4. Update function docstring

### Step 2: Update Endpoint (`team_metrics_service.py`)

1. Add `isGroup: bool = Query(False, ...)` parameter
2. Update parameter description for `team_name`
3. Add validation logic (validate team_name or group_name based on isGroup)
4. Call `resolve_team_names_from_filter(team_name, isGroup, conn)` to get team list
5. Pass `team_names_list` to `get_team_count_in_progress()` instead of single team_name
6. Update response to include:
   - `group_name` and `teams_in_group` when `isGroup=true`
   - `team_name` when `isGroup=false`
7. Update docstring

---

## SQL Query Pattern Reference

Following the pattern from `sprints_service.py` (lines 241-247):

```python
if team_names_list:
    # Build parameterized IN clause
    placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names_list))])
    params = {f"team_name_{i}": name for i, name in enumerate(team_names_list)}
    
    query = text(f"""
        SELECT 
            issue_type,
            COUNT(*) as type_count
        FROM public.jira_issues
        WHERE team_name IN ({placeholders})
        AND status_category = 'In Progress'
        GROUP BY issue_type
        ORDER BY type_count DESC;
    """)
    
    result = conn.execute(query, params)
else:
    # No filter - return all teams
    query = text("""
        SELECT 
            issue_type,
            COUNT(*) as type_count
        FROM public.jira_issues
        WHERE status_category = 'In Progress'
        GROUP BY issue_type
        ORDER BY type_count DESC;
    """)
    
    result = conn.execute(query)
```

---

## Response Format Changes

### Current Response:
```json
{
    "success": true,
    "data": {
        "total_in_progress": 15,
        "count_by_type": {
            "Story": 8,
            "Bug": 5,
            "Task": 2
        },
        "team_name": "Team Alpha"
    },
    "message": "Retrieved count in progress for team 'Team Alpha'"
}
```

### New Response (single team, isGroup=false):
```json
{
    "success": true,
    "data": {
        "total_in_progress": 15,
        "count_by_type": {
            "Story": 8,
            "Bug": 5,
            "Task": 2
        },
        "team_name": "Team Alpha"
    },
    "message": "Retrieved count in progress for team 'Team Alpha'"
}
```

### New Response (group, isGroup=true):
```json
{
    "success": true,
    "data": {
        "total_in_progress": 42,
        "count_by_type": {
            "Story": 25,
            "Bug": 12,
            "Task": 5
        },
        "group_name": "Engineering Group",
        "teams_in_group": ["Team Alpha", "Team Beta", "Team Gamma"]
    },
    "message": "Retrieved count in progress for group 'Engineering Group'"
}
```

---

## Summary

### What Needs to Change:

1. **SQL Statement**: 
   - Change from `WHERE team_name = :team_name` to `WHERE team_name IN (:team_name_0, :team_name_1, ...)`
   - Use parameterized IN clause pattern (same as `sprints_service.py`)
   - Handle None case (all teams) by omitting team_name filter

2. **Database Function**:
   - Change parameter from `team_name: str` to `team_names: Optional[List[str]]`
   - Update SQL query logic to handle list of teams

3. **Endpoint**:
   - Add `isGroup` parameter
   - Use `resolve_team_names_from_filter()` to resolve teams
   - Update response metadata to include group info when applicable

### How Count-In-Progress Works:
- **Direct SQL query** (NOT a view, NOT a database function)
- Queries `public.jira_issues` table directly
- Filters by `status_category = 'In Progress'`
- Groups by `issue_type` and counts

---

## Testing Checklist

- [ ] Single team (isGroup=false) - should work as before
- [ ] Group with multiple teams (isGroup=true) - should aggregate counts
- [ ] Group with single team (isGroup=true) - should work correctly
- [ ] Invalid team name - should return 404
- [ ] Invalid group name - should return 404
- [ ] Empty group - should return 404
- [ ] Verify counts are aggregated correctly across teams
- [ ] Verify response includes correct metadata (group_name vs team_name)

