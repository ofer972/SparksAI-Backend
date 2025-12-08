# Plan: Get Active Epic Dependencies Endpoint

## Overview
Create a new backend endpoint that retrieves active epic dependencies for a team or group. The endpoint will call the database function `get_active_epic_dependencies(p_team_names text[])` and return all fields that the function provides.

**Key Requirements:**
- ✅ Accept `team_name` parameter (optional) and `isGroup` flag
- ✅ If `isGroup=true`, resolve group to list of teams using shared function
- ✅ Call SQL function `get_active_epic_dependencies()` with team names array or NULL
- ✅ Return all fields from the database function
- ✅ **Response MUST include the original `team_name` or `group_name` parameter value**
- ✅ **If `isGroup=true`, response MUST also include `teams_in_group` list**

---

## Endpoint Details

### Route
- **Path**: `GET /api/v1/issues/active-epic-dependencies`
- **Location**: `issues_service.py` (add at the end, after `get_issue_types` endpoint)

### Parameters
- `team_name` (optional, string): Team name or group name (if `isGroup=true`)
- `isGroup` (optional, bool, default: `false`): If true, `team_name` is treated as a group name

### Behavior
1. If `team_name` is provided and `isGroup=false`: Filter by single team
2. If `team_name` is provided and `isGroup=true`: Get all teams in the group using shared function, then filter by those teams
3. If `team_name` is not provided (`None`): Return data for all teams

---

## Implementation Steps

### Step 1: Add Endpoint Function
Add the endpoint function to `issues_service.py` (after line 1545, at the end of the file):

```python
@issues_router.get("/issues/active-epic-dependencies")
async def get_active_epic_dependencies(
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get active epic dependencies for a team or group.
    
    Calls the database function get_active_epic_dependencies() which returns
    active epic dependencies filtered by team names.
    
    Args:
        team_name: Optional team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with list of active epic dependencies and their details
    """
```

### Step 2: Import Required Function
The endpoint will use `resolve_team_names_from_filter` from `database_team_metrics.py`. 
This import is already used in other endpoints in `issues_service.py` (line 1158), so we can follow the same pattern:
- Import it inside the function (as done in other endpoints) OR
- Add it to top-level imports if not already there

**Note**: Check if `resolve_team_names_from_filter` is already imported at the top of `issues_service.py`. If not, we'll import it inside the function like other endpoints do.

### Step 3: Resolve Team Names
Use the shared helper function to resolve team names:

```python
from database_team_metrics import resolve_team_names_from_filter

# Resolve team names (handles group to teams translation)
team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
```

This function:
- Returns `None` if `team_name` is `None` (meaning all teams)
- Returns `[team_name]` if `isGroup=false` (single team)
- Returns `[team1, team2, ...]` if `isGroup=true` (all teams in group)

### Step 4: Call Database Function
Call the SQL function `get_active_epic_dependencies(p_team_names text[])`:

**Pattern to follow** (based on `database_pi.py` examples):
- If `team_names_list` is provided: Pass as array using `CAST(:param AS text[])`
- If `team_names_list` is `None`: Pass `NULL` to function

```python
# Build parameters for the function call
params = {}

# Build query - pass team_names as array or NULL
if team_names_list:
    # Pass array of team names to function
    params['team_names_param'] = team_names_list
    sql_query_text = text("""
        SELECT * FROM public.get_active_epic_dependencies(
            CAST(:team_names_param AS text[])
        )
    """)
    
    logger.info(f"Executing SQL for active epic dependencies with teams: {team_names_list}")
else:
    # Pass NULL for all teams
    sql_query_text = text("""
        SELECT * FROM public.get_active_epic_dependencies(
            NULL
        )
    """)
    
    logger.info("Executing SQL for active epic dependencies for all teams")

# Execute query with parameters (SECURE: prevents SQL injection)
result = conn.execute(sql_query_text, params)
```

### Step 5: Convert Results to Dictionary
Convert SQL result rows to list of dictionaries (returning all columns):

```python
# Convert rows to list of dictionaries - return all columns as-is
dependencies = []
for row in result:
    # Get column names from result
    row_dict = dict(row._mapping)
    dependencies.append(row_dict)
```

### Step 6: Build Response
Build response with metadata following the pattern from other endpoints:

**IMPORTANT**: The response MUST include:
1. The original `team_name` or `group_name` that was passed as a parameter
2. If `isGroup=true`, also include the list of teams in that group (`teams_in_group`)

```python
# Build response data
response_data = {
    "dependencies": dependencies,
    "count": len(dependencies)
}

# Add team/group information to response (following pattern from pis_service.py)
# This ensures the original parameter value is always included in the response
if team_name:
    if isGroup:
        # When isGroup=true, include the original group name AND the list of teams
        response_data["group_name"] = team_name  # Original group name passed
        response_data["teams_in_group"] = team_names_list  # List of teams in the group
    else:
        # When isGroup=false, include the original team name
        response_data["team_name"] = team_name  # Original team name passed
else:
    # No filter was provided
    response_data["team_name"] = None

return {
    "success": True,
    "data": response_data,
    "message": f"Retrieved {len(dependencies)} active epic dependencies"
}
```

### Step 7: Error Handling
Wrap in try-except block following the pattern from other endpoints:

```python
try:
    # ... implementation ...
except HTTPException:
    raise
except Exception as e:
    logger.error(f"Error fetching active epic dependencies: {e}")
    raise HTTPException(
        status_code=500,
        detail=f"Failed to fetch active epic dependencies: {str(e)}"
    )
```

---

## Response Format

### Success Response

**Response always includes the original parameter values:**
- If `team_name` was provided with `isGroup=false`: Response includes `team_name` with the original value
- If `team_name` was provided with `isGroup=true`: Response includes `group_name` (original value) AND `teams_in_group` (list of teams)
- If `team_name` was not provided: Response includes `team_name: null`

#### Example 1: Single Team (`team_name="Team A"`, `isGroup=false`)
```json
{
  "success": true,
  "data": {
    "dependencies": [
      {
        // All fields returned by get_active_epic_dependencies() function
      }
    ],
    "count": 5,
    "team_name": "Team A"
  },
  "message": "Retrieved 5 active epic dependencies"
}
```

#### Example 2: Group (`team_name="Engineering Group"`, `isGroup=true`)
```json
{
  "success": true,
  "data": {
    "dependencies": [
      {
        // All fields returned by get_active_epic_dependencies() function
      }
    ],
    "count": 8,
    "group_name": "Engineering Group",
    "teams_in_group": ["Team A", "Team B", "Team C"]
  },
  "message": "Retrieved 8 active epic dependencies"
}
```

#### Example 3: All Teams (no `team_name` provided)
```json
{
  "success": true,
  "data": {
    "dependencies": [
      {
        // All fields returned by get_active_epic_dependencies() function
      }
    ],
    "count": 20,
    "team_name": null
  },
  "message": "Retrieved 20 active epic dependencies"
}
```

### Error Responses
- **400**: Invalid parameters (handled by `resolve_team_names_from_filter`)
- **404**: Team or group not found (handled by `resolve_team_names_from_filter`)
- **500**: Database or server error

---

## Key Patterns to Follow

### 1. Team/Group Resolution Pattern
```python
from database_team_metrics import resolve_team_names_from_filter

# Resolve team names FIRST
team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
```

### 2. SQL Function Call Pattern
```python
from sqlalchemy import text

# Handle array parameter or NULL
if team_names_list:
    params['team_names_param'] = team_names_list
    sql_query_text = text("""
        SELECT * FROM public.get_active_epic_dependencies(
            CAST(:team_names_param AS text[])
        )
    """)
else:
    sql_query_text = text("""
        SELECT * FROM public.get_active_epic_dependencies(
            NULL
        )
    """)

result = conn.execute(sql_query_text, params)
```

### 3. Response Metadata Pattern
```python
# Add team/group metadata (same pattern as other endpoints)
# CRITICAL: Always include the original parameter value in the response
if team_name:
    if isGroup:
        # Include original group name AND list of teams in the group
        response_data["group_name"] = team_name  # Original parameter value
        response_data["teams_in_group"] = team_names_list  # Resolved list of teams
    else:
        # Include original team name
        response_data["team_name"] = team_name  # Original parameter value
else:
    # No filter provided
    response_data["team_name"] = None
```

---

## Files to Modify

1. **`issues_service.py`**
   - Add new endpoint function at the end of the file (after `get_issue_types`)

---

## Testing Considerations

After implementation, test:
1. ✅ Endpoint with `team_name` and `isGroup=false` (single team)
   - Verify response includes `team_name` with original value
2. ✅ Endpoint with `team_name` and `isGroup=true` (group)
   - Verify response includes `group_name` with original value
   - Verify response includes `teams_in_group` with list of teams
3. ✅ Endpoint without `team_name` (all teams)
   - Verify response includes `team_name: null`
4. ✅ Invalid team name (should return 404)
5. ✅ Invalid group name (should return 404)
6. ✅ Verify all fields from database function are returned
7. ✅ Verify response structure matches expected format
8. ✅ Verify original parameter values are preserved in response metadata

---

## Notes

- The database function signature: `get_active_epic_dependencies(p_team_names text[] DEFAULT NULL::text[])`
- The function accepts `NULL` for all teams, or an array of team names
- We use `SELECT *` to return all fields that the function provides
- The endpoint follows the same patterns as other endpoints in the codebase (e.g., `/pis/burndown`, `/issues/epics-by-pi`)
