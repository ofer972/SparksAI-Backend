# Sprint Burndown isGroup Support - Implementation Plan (REVISED)

## Overview
Add `isGroup` support to the `/team-metrics/sprint-burndown` endpoint, following the same patterns used in other endpoints like `/pis/burndown` and `/team-metrics/closed-sprints`.

## Current State

### Endpoint: `GET /api/v1/team-metrics/sprint-burndown`
- **Current Parameters:**
  - `team_name` (required, string): Single team name
  - `issue_type` (optional, default: "all")
  - `sprint_name` (optional): If not provided, auto-selects active sprint with max total issues

### Current Flow:
1. Validates `team_name` as a single team
2. If `sprint_name` not provided:
   - Calls `get_sprints_with_total_issues_db(team_name, "active")` to get active sprints for the team
   - Selects sprint with maximum `total_issues`
3. Calls `get_sprint_burndown_data_db(team_name, sprint_name, issue_type)` which uses database function `get_sprint_burndown_data_for_team(sprint_name, issue_type, team_name)`
4. Returns burndown data for that single team

### Database Function:
- `get_sprint_burndown_data_for_team(sprint_name, issue_type, team_name)` - currently accepts single team_name
- Will be modified to accept `team_names` as text array (similar to `get_pi_burndown_data`)

## Proposed Changes

### 1. Endpoint Parameter Changes
**File:** `team_metrics_service.py`

**Add:**
- `isGroup: bool = Query(False, description="If true, team_name is treated as a group name")`

**Modify:**
- `team_name` description: "Team name or group name (if isGroup=true) to get burndown data for"

### 2. Team Resolution Logic
**File:** `team_metrics_service.py`

**Add at the beginning of the endpoint:**
```python
# Resolve team names using shared helper function (handles single team, group, or None)
team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)

# If isGroup=True, team_names_list will be a list of team names
# If isGroup=False, team_names_list will be [team_name] (single team)
```

**Import:**
```python
from database_team_metrics import resolve_team_names_from_filter
```

### 3. Sprint Selection Logic (Multiple Teams) - REVISED
**File:** `team_metrics_service.py`

**Current logic (single team):**
```python
if not selected_sprint_name:
    sprints = get_sprints_with_total_issues_db(validated_team_name, "active", conn)
    if sprints:
        selected_sprint = max(sprints, key=lambda x: x['total_issues'])
        selected_sprint_name = selected_sprint['name']
        selected_sprint_id = selected_sprint['sprint_id']
```

**New logic (multiple teams - with validation):**
```python
if not selected_sprint_name:
    # Get sprints for all teams in the group
    all_sprints = []
    for team in team_names_list:
        team_sprints = get_sprints_with_total_issues_db(team, "active", conn)
        all_sprints.extend(team_sprints)
    
    if not all_sprints:
        # No active sprints found
        return {
            "success": False,
            "data": {},
            "message": "No active sprints found"
        }
    
    # Get unique sprint IDs (by sprint_id)
    unique_sprint_ids = set()
    for sprint in all_sprints:
        unique_sprint_ids.add(sprint['sprint_id'])
    
    # CRITICAL: If more than one unique sprint found, return error
    if len(unique_sprint_ids) > 1:
        return {
            "success": False,
            "data": {},
            "message": "Sprint Burndown is not shown because the group does not have one sprint for the group"
        }
    
    # Only one unique sprint - use it
    selected_sprint = all_sprints[0]  # All sprints have same sprint_id, so any one will do
    selected_sprint_name = selected_sprint['name']
    selected_sprint_id = selected_sprint['sprint_id']
```

### 4. Burndown Data Retrieval - REVISED
**File:** `database_team_metrics.py` (modify existing function)

**Current function signature:**
```python
def get_sprint_burndown_data_db(team_name: str, sprint_name: str, issue_type: str = "all", conn: Connection = None) -> List[Dict[str, Any]]:
```

**New function signature (support both single team and list of teams):**
```python
def get_sprint_burndown_data_db(team_name: Optional[Union[str, List[str]]], sprint_name: str, issue_type: str = "all", conn: Connection = None) -> List[Dict[str, Any]]:
```

**Modify SQL call to accept team_names as array (similar to PI burndown pattern):**
```python
# If team_name is a list, pass as array; otherwise pass as single value
if isinstance(team_name, list):
    # Multiple teams - pass as text array
    sql_query = """
        SELECT * FROM public.get_sprint_burndown_data_for_team(
            :sprint_name, 
            :issue_type, 
            CAST(:team_names AS text[])
        );
    """
    params = {
        "sprint_name": sprint_name,
        "issue_type": issue_type,
        "team_names": team_name
    }
else:
    # Single team - pass as string (backward compatible)
    sql_query = """
        SELECT * FROM public.get_sprint_burndown_data_for_team(
            :sprint_name, 
            :issue_type, 
            :team_name
        );
    """
    params = {
        "sprint_name": sprint_name,
        "issue_type": issue_type,
        "team_name": team_name
    }
```

**In endpoint (`team_metrics_service.py`):**
```python
# Pass team_names_list directly to the function (it will handle both single and multiple teams)
burndown_data = get_sprint_burndown_data_db(team_names_list, selected_sprint_name, issue_type, conn)
```

**Note:** The SQL function `get_sprint_burndown_data_for_team` will need to be updated in the database to accept either a single team_name (text) or team_names (text[]). The function should aggregate data internally when team_names array is provided.

### 5. Response Structure Updates
**File:** `team_metrics_service.py`

**Current response:**
```python
{
    "success": True,
    "data": {
        "sprint_id": selected_sprint_id,
        "sprint_name": selected_sprint_name,
        "start_date": start_date,
        "end_date": end_date,
        "burndown_data": burndown_data,
        "team_name": validated_team_name,
        "issue_type": issue_type,
        "total_issues_in_sprint": total_issues_in_sprint
    },
    "message": f"Retrieved sprint burndown data for team '{validated_team_name}' and sprint '{selected_sprint_name}'"
}
```

**New response (following PI burndown pattern):**
```python
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
```

## Implementation Details

### Dependencies
- **Existing function:** `resolve_team_names_from_filter()` from `database_team_metrics.py` (already exists)
- **Existing function:** `get_sprints_with_total_issues_db()` from `database_team_metrics.py` (already exists, accepts single team)
- **Function to modify:** `get_sprint_burndown_data_db()` from `database_team_metrics.py` (needs to accept list of teams)

### Key Considerations

1. **Sprint Selection Validation:**
   - **CRITICAL:** When `isGroup=True`, if sprint selection returns more than one unique sprint (different sprint_ids), return error message: "Sprint Burndown is not shown because the group does not have one sprint for the group"
   - Return only the message, no data: `{"success": False, "data": {}, "message": "..."}`
   - Only proceed if exactly one unique sprint is found across all teams

2. **Burndown Data:**
   - **NO Python aggregation needed**
   - Call SQL function `get_sprint_burndown_data_for_team` with list of teams (as text array)
   - SQL function will handle aggregation internally
   - Function returns data in same format as before

3. **Database Function Update:**
   - The SQL function `get_sprint_burndown_data_for_team` needs to be updated to accept either:
     - `team_name TEXT` (single team - backward compatible)
     - `team_names TEXT[]` (multiple teams - new)
   - When `team_names` array is provided, function should aggregate burndown data across all teams
   - This is a database-level change (SQL function modification)

4. **Error Handling:**
   - If group has no teams, `resolve_team_names_from_filter` will raise HTTPException 404
   - If no active sprints found across all teams, return appropriate error message
   - If multiple unique sprints found, return specific error message (see item 1)
   - If sprint_name is provided but doesn't exist for any team, handle gracefully

## Testing Considerations

1. **Single Team (isGroup=False):**
   - Verify behavior remains unchanged
   - Test with and without sprint_name parameter

2. **Group (isGroup=True) - Single Sprint:**
   - Test with group containing 2+ teams that all have the same active sprint
   - Verify sprint selection succeeds
   - Verify burndown data is returned (aggregated by SQL function)
   - Verify response includes `group_name` and `teams_in_group`

3. **Group (isGroup=True) - Multiple Sprints:**
   - Test with group containing teams with different active sprints
   - Verify error message: "Sprint Burndown is not shown because the group does not have one sprint for the group"
   - Verify response has `success: False` and empty `data: {}`

4. **Edge Cases:**
   - Group with no teams (should return 404 from `resolve_team_names_from_filter`)
   - Group with teams that have no active sprints
   - Sprint that exists for some teams but not others

## Files to Modify

1. **`team_metrics_service.py`**
   - Add `isGroup` parameter to endpoint
   - Add team resolution logic
   - Modify sprint selection logic with validation (return error if multiple sprints)
   - Update burndown data call to pass team list
   - Update response structure

2. **`database_team_metrics.py`**
   - Modify `get_sprint_burndown_data_db()` to accept `Union[str, List[str]]` for team_name
   - Update SQL call to pass team_names as array when list is provided
   - Maintain backward compatibility for single team

3. **Database SQL Function (separate task):**
   - Update `get_sprint_burndown_data_for_team()` function to accept either `team_name TEXT` or `team_names TEXT[]`
   - When `team_names` array provided, aggregate burndown data across all teams in SQL

## Summary

**Key Changes:**
1. Add `isGroup` parameter to endpoint
2. Resolve group to team names using `resolve_team_names_from_filter`
3. **Sprint Selection:** If multiple unique sprints found, return error message (no data)
4. **Burndown Data:** Call SQL function with list of teams - SQL handles aggregation (no Python aggregation)
5. Update response to include group/team information

**Critical Requirement:**
- If group has multiple different sprints → return error: "Sprint Burndown is not shown because the group does not have one sprint for the group"
- No data returned, only the error message

**Database Change Required:**
- SQL function `get_sprint_burndown_data_for_team` needs to be updated to accept team_names array and aggregate internally

