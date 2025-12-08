# Plan: Get Active Sprint Stories by Epic Endpoint

## Overview
Create a new backend endpoint that retrieves active sprint stories by epic for a team or group. The endpoint will call a database function (assumed to be `get_active_sprint_stories_by_epic(p_team_names text[])`) and return all fields that the function provides directly in the data array (without a wrapper key).

**Key Requirements:**
- ✅ Accept `team_name` parameter (optional) and `isGroup` flag
- ✅ If `isGroup=true`, resolve group to list of teams using shared function
- ✅ Call SQL function `get_active_sprint_stories_by_epic()` with team names array or NULL
- ✅ Return all fields from the database function **directly in the data array** (no wrapper like "dependencies")
- ✅ **Response MUST include the original `team_name` or `group_name` parameter value**
- ✅ **If `isGroup=true`, response MUST also include `teams_in_group` list**

---

## Endpoint Details

### Route
- **Path**: `GET /api/v1/issues/active-sprint-stories-by-epic`
- **Location**: `issues_service.py` (add right after `get_active_epic_dependencies` endpoint, before `/issues/{issue_id}` route)

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
Add the endpoint function to `issues_service.py` (right after `get_active_epic_dependencies`, around line 1530):

```python
@issues_router.get("/issues/active-sprint-stories-by-epic")
async def get_active_sprint_stories_by_epic(
    team_name: Optional[str] = Query(None, description="Team name or group name (if isGroup=true)"),
    isGroup: bool = Query(False, description="If true, team_name is treated as a group name"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get active sprint stories by epic for a team or group.
    
    Calls the database function get_active_sprint_stories_by_epic() which retrieves
    active sprint stories grouped by epic.
    
    Args:
        team_name: Optional team name or group name (if isGroup=true)
        isGroup: If true, team_name is treated as a group name
    
    Returns:
        JSON response with list of active sprint stories by epic and their details
    """
```

### Step 2: Import Required Function
The endpoint will use `resolve_team_names_from_filter` from `database_team_metrics.py`. 
Import it inside the function (same pattern as other endpoints).

### Step 3: Resolve Team Names
Use the shared helper function to resolve team names:

```python
from database_team_metrics import resolve_team_names_from_filter

# Resolve team names (handles group to teams translation)
team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
```

### Step 4: Call Database Function
Call the SQL function `get_active_sprint_stories_by_epic(p_team_names text[])`:

```python
# Build parameters for the function call
params = {}

# Build query - pass team_names as array or NULL
if team_names_list:
    # Pass array of team names to function
    params['team_names_param'] = team_names_list
    sql_query_text = text("""
        SELECT * FROM public.get_active_sprint_stories_by_epic(
            CAST(:team_names_param AS text[])
        )
    """)
    
    logger.info(f"Executing SQL for active sprint stories by epic with teams: {team_names_list}")
else:
    # Pass NULL for all teams
    sql_query_text = text("""
        SELECT * FROM public.get_active_sprint_stories_by_epic(
            NULL
        )
    """)
    
    logger.info("Executing SQL for active sprint stories by epic for all teams")

# Execute query with parameters (SECURE: prevents SQL injection)
result = conn.execute(sql_query_text, params)
```

### Step 5: Convert Results to Dictionary
Convert SQL result rows to list of dictionaries (returning all columns):

```python
# Convert rows to list of dictionaries - return all columns as-is
stories = []
for row in result:
    row_dict = dict(row._mapping)
    stories.append(row_dict)
```

### Step 6: Build Response
**KEY DIFFERENCE**: Unlike the previous endpoint, the response data should contain the stories array directly, NOT wrapped in a key like "dependencies".

```python
# Build response data - stories array goes directly in data, not wrapped
response_data = {
    "count": len(stories),
    "isGroup": isGroup
}

# Add team/group information to response
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
    "data": stories,  # Direct array of story objects, NOT wrapped in a key
    "count": len(stories),
    "message": f"Retrieved {len(stories)} active sprint stories by epic"
}
```

**Wait - need to clarify structure. Let me check the requirement again...**

Actually, re-reading the requirement: "In the response, I do not want to have the dependencies part just show The columns that are returned inside the data in the response."

This means the response should be:
```json
{
  "success": true,
  "data": [
    { /* columns from SQL function */ },
    { /* columns from SQL function */ }
  ],
  "count": 2,
  "team_name": "Team A",
  "isGroup": false
}
```

But we also need metadata. Let me revise:

```python
# Build response - stories array goes directly in data
# Metadata goes at the top level alongside data
response_data = {
    "count": len(stories),
    "isGroup": isGroup
}

# Add team/group information to response metadata
if team_name:
    if isGroup:
        response_data["group_name"] = team_name
        response_data["teams_in_group"] = team_names_list
    else:
        response_data["team_name"] = team_name
else:
    response_data["team_name"] = None

return {
    "success": True,
    "data": stories,  # Direct array - no wrapper key
    **response_data,  # Spread metadata (count, isGroup, team_name/group_name, teams_in_group)
    "message": f"Retrieved {len(stories)} active sprint stories by epic"
}
```

Actually, looking at other endpoints, the pattern is usually:
```json
{
  "success": true,
  "data": { /* main data object */ },
  "message": "..."
}
```

But the user specifically said "just show The columns that are returned inside the data in the response" - so I think they want:
```json
{
  "success": true,
  "data": [ /* array of story objects */ ],
  "count": 2,
  "team_name": "Team A",
  "isGroup": false,
  "message": "..."
}
```

Let me structure it properly:

```python
# Convert rows to list of dictionaries
stories = []
for row in result:
    row_dict = dict(row._mapping)
    stories.append(row_dict)

# Build response - stories array goes directly in data
# Metadata (count, team/group info) goes at top level
response = {
    "success": True,
    "data": stories,  # Direct array of story objects
    "count": len(stories),
    "isGroup": isGroup
}

# Add team/group information to response
if team_name:
    if isGroup:
        response["group_name"] = team_name
        response["teams_in_group"] = team_names_list
    else:
        response["team_name"] = team_name
else:
    response["team_name"] = None

response["message"] = f"Retrieved {len(stories)} active sprint stories by epic"

return response
```

### Step 7: Error Handling
Wrap in try-except block following the pattern from other endpoints:

```python
try:
    # ... implementation ...
except HTTPException:
    raise
except Exception as e:
    logger.error(f"Error fetching active sprint stories by epic: {e}")
    raise HTTPException(
        status_code=500,
        detail=f"Failed to fetch active sprint stories by epic: {str(e)}"
    )
```

---

## Response Format

### Success Response

**Key Difference**: The `data` field contains the array of story objects directly, NOT wrapped in a key like "dependencies" or "stories".

**Response always includes the original parameter values:**
- If `team_name` was provided with `isGroup=false`: Response includes `team_name` with the original value
- If `team_name` was provided with `isGroup=true`: Response includes `group_name` (original value) AND `teams_in_group` (list of teams)
- If `team_name` was not provided: Response includes `team_name: null`

#### Example 1: Single Team (`team_name="Team A"`, `isGroup=false`)
```json
{
  "success": true,
  "data": [
    {
      // All columns returned by get_active_sprint_stories_by_epic() function
      // e.g., epic_key, epic_name, story_key, story_name, team_name, sprint_id, etc.
    },
    {
      // Another story object
    }
  ],
  "count": 2,
  "team_name": "Team A",
  "isGroup": false,
  "message": "Retrieved 2 active sprint stories by epic"
}
```

#### Example 2: Group (`team_name="Engineering Group"`, `isGroup=true`)
```json
{
  "success": true,
  "data": [
    {
      // All columns returned by get_active_sprint_stories_by_epic() function
    }
  ],
  "count": 5,
  "group_name": "Engineering Group",
  "teams_in_group": ["Team A", "Team B", "Team C"],
  "isGroup": true,
  "message": "Retrieved 5 active sprint stories by epic"
}
```

#### Example 3: All Teams (no `team_name` provided)
```json
{
  "success": true,
  "data": [
    {
      // All columns returned by get_active_sprint_stories_by_epic() function
    }
  ],
  "count": 20,
  "team_name": null,
  "isGroup": false,
  "message": "Retrieved 20 active sprint stories by epic"
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
        SELECT * FROM public.get_active_sprint_stories_by_epic(
            CAST(:team_names_param AS text[])
        )
    """)
else:
    sql_query_text = text("""
        SELECT * FROM public.get_active_sprint_stories_by_epic(
            NULL
        )
    """)

result = conn.execute(sql_query_text, params)
```

### 3. Response Structure Pattern (KEY DIFFERENCE)
```python
# Convert results to array
stories = []
for row in result:
    stories.append(dict(row._mapping))

# Build response - data contains array directly, metadata at top level
response = {
    "success": True,
    "data": stories,  # Direct array, NOT wrapped in a key
    "count": len(stories),
    "isGroup": isGroup
}

# Add team/group metadata
if team_name:
    if isGroup:
        response["group_name"] = team_name
        response["teams_in_group"] = team_names_list
    else:
        response["team_name"] = team_name
else:
    response["team_name"] = None

response["message"] = f"Retrieved {len(stories)} active sprint stories by epic"
return response
```

---

## Files to Modify

1. **`issues_service.py`**
   - Add new endpoint function right after `get_active_epic_dependencies` (around line 1530)
   - Must be placed BEFORE the `/issues/{issue_id}` route to avoid route conflicts

---

## Testing Considerations

After implementation, test:
1. ✅ Endpoint with `team_name` and `isGroup=false` (single team)
   - Verify response includes `team_name` with original value
   - Verify `data` is a direct array (not wrapped in a key)
2. ✅ Endpoint with `team_name` and `isGroup=true` (group)
   - Verify response includes `group_name` with original value
   - Verify response includes `teams_in_group` with list of teams
   - Verify `data` is a direct array
3. ✅ Endpoint without `team_name` (all teams)
   - Verify response includes `team_name: null`
   - Verify `data` is a direct array
4. ✅ Invalid team name (should return 404)
5. ✅ Invalid group name (should return 404)
6. ✅ Verify all fields from database function are returned
7. ✅ Verify response structure matches expected format (data is array, not object with wrapper key)
8. ✅ Verify original parameter values are preserved in response metadata

---

## Notes

- **Assumed database function**: `get_active_sprint_stories_by_epic(p_team_names text[] DEFAULT NULL::text[])`
- The function accepts `NULL` for all teams, or an array of team names
- We use `SELECT *` to return all fields that the function provides
- **Key difference from previous endpoint**: Response `data` field contains the array directly, NOT wrapped in a key like "dependencies"
- The endpoint follows the same patterns as other endpoints in the codebase, but with a flatter response structure
- Must be placed BEFORE `/issues/{issue_id}` route to avoid route matching conflicts

---

## Response Structure Comparison

### Previous Endpoint (`get_active_epic_dependencies`):
```json
{
  "success": true,
  "data": {
    "dependencies": [...],  // Wrapped in "dependencies" key
    "count": 5,
    "isGroup": false,
    "team_name": "Team A"
  },
  "message": "..."
}
```

### New Endpoint (`get_active_sprint_stories_by_epic`):
```json
{
  "success": true,
  "data": [...],  // Direct array, no wrapper key
  "count": 5,
  "isGroup": false,
  "team_name": "Team A",
  "message": "..."
}
```
