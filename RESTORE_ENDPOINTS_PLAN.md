# Restore Missing PI Endpoints - Implementation Plan

## Overview
This plan documents two endpoints that were implemented yesterday but are now missing from the codebase. Both endpoints should be added to `pis_service.py`.

---

## Endpoint 1: Top Dependencies Summary

### Endpoint Details
- **Route**: `GET /api/v1/pis/top-dependencies-summary`
- **Location**: `pis_service.py` (add after existing PI endpoints, around line 1268)
- **Purpose**: Returns top 3 teams with most uncompleted dependencies (inbound and outbound) for a given PI

### Parameters
- `pi` (required, string): PI name (quarter_pi_of_epic) - required
- `team_name` (optional, string): Filter by team name or group name (if isGroup=true)
- `isGroup` (optional, bool, default: false): If true, team_name is treated as a group name

### Implementation Approach

#### Step 1: Import Required Functions
Add to imports at top of `pis_service.py` (line 16):
```python
from database_pi import (
    fetch_pi_predictability_data, 
    fetch_pi_burndown_data, 
    fetch_scope_changes_data, 
    fetch_pi_summary_data, 
    fetch_pi_summary_data_by_team, 
    get_pi_participating_teams_db,
    fetch_epic_inbound_dependency_data,  # ADD THIS
    fetch_epic_outbound_dependency_data   # ADD THIS
)
```

#### Step 2: Team/Group Resolution
- Use `resolve_team_names_from_filter(team_name, isGroup, conn)` to resolve team names
- This function handles:
  - Single team name → returns list with one team
  - Group name (isGroup=true) → returns list of all teams in group
  - None → returns None (no filter)

#### Step 3: Fetch Data
- Call `fetch_epic_inbound_dependency_data(pi, team_names_list, conn)` → returns ALL rows
- Call `fetch_epic_outbound_dependency_data(pi, team_names_list, conn)` → returns ALL rows
- **Important**: Each row in the view already represents one team - no grouping needed

#### Step 4: Process Inbound Dependencies
For each row in inbound data:
1. Calculate `uncompleted_issues = volume_of_work_relied_upon - completed_issues_dependent_count`
2. Only include rows where `uncompleted_issues > 0`
3. Sort all rows by `uncompleted_issues` DESC
4. Take top 3 rows

For each selected row, include in response:
- `assignee_team`: Team that owns the epic
- `volume_of_work_relied_upon`: Total dependent issues
- `completed_issues_dependent_count`: Completed issues
- `uncompleted_issues`: Calculated value (volume - completed)

#### Step 5: Process Outbound Dependencies
For each row in outbound data:
1. Calculate `uncompleted_issues = number_of_dependent_issues - completed_dependent_issues_count`
2. Only include rows where `uncompleted_issues > 0`
3. Sort all rows by `uncompleted_issues` DESC
4. Take top 3 rows

For each selected row, include in response:
- `owned_team`: Team that owns the epic
- `number_of_epics_owned`: Number of epics owned
- `number_of_dependent_issues`: Total dependent issues
- `completed_dependent_issues_count`: Completed issues
- `uncompleted_issues`: Calculated value (dependent_issues - completed)

#### Step 6: Build Response
Response structure:
```json
{
  "success": true,
  "data": {
    "top_inbound_dependencies": [
      {
        "assignee_team": "Team A",
        "volume_of_work_relied_upon": 15,
        "completed_issues_dependent_count": 5,
        "uncompleted_issues": 10
      },
      // ... up to 3 entries
    ],
    "top_outbound_dependencies": [
      {
        "owned_team": "Team B",
        "number_of_epics_owned": 8,
        "number_of_dependent_issues": 20,
        "completed_dependent_issues_count": 8,
        "uncompleted_issues": 12
      },
      // ... up to 3 entries
    ],
    "pi": "PI-2024-Q1",
    "count": {
      "inbound": 3,
      "outbound": 2
    },
    // Conditional metadata:
    // If team_name provided and isGroup=false:
    "team_name": "Team A",
    // OR if team_name provided and isGroup=true:
    "group_name": "Engineering Group",
    "teams_in_group": ["Team A", "Team B", "Team C"]
  },
  "message": "Retrieved top 3 inbound and top 2 outbound dependencies"
}
```

**Note**: Response structure is identical whether filtering by single team or group. Only metadata fields differ (team_name vs group_name/teams_in_group).

### Error Handling
- Validate `pi` parameter (required, non-empty)
- Handle HTTPException from team resolution
- Handle cases where no rows have uncompleted > 0 (return empty arrays)
- Proper error logging and HTTP exceptions

---

## Endpoint 2: Average Epic Cycle Time

### Endpoint Details
- **Route**: `GET /api/v1/pis/average-epic-cycle-time`
- **Location**: `pis_service.py` (add after top-dependencies-summary endpoint)
- **Purpose**: Returns average cycle time for completed epics over the last N months

### Parameters
- `months` (optional, int, default: 3, range: 1-12): Number of months to look back
- `team_name` (optional, string): Filter by team name or group name (if isGroup=true)
- `isGroup` (optional, bool, default: false): If true, team_name is treated as a group name

### Implementation Approach

#### Step 1: Helper Function for Status Indicator
Add new helper function in `pis_service.py` (around line 197, after `get_progress_delta_pct_status`):

```python
def get_epic_cycle_time_status(cycle_time: Optional[float]) -> str:
    """
    Determine epic cycle time status based on value.
    
    Args:
        cycle_time: Average cycle time in days (float or None)
    
    Returns:
        "green" if cycle_time < 45
        "yellow" if 45 <= cycle_time <= 60
        "red" if cycle_time > 60
        "gray" if cycle_time is None (no data)
    """
    if cycle_time is None:
        return "gray"  # No data available
    
    if cycle_time < 45:
        return "green"
    elif cycle_time <= 60:
        return "yellow"
    else:  # cycle_time > 60
        return "red"
```

#### Step 2: Calculate Date Filter
- Calculate `start_date = datetime.now().date() - timedelta(days=months * 30)`
- Same pattern as other endpoints (e.g., `_fetch_release_predictability` in database_reports.py)

#### Step 3: Team/Group Resolution
- Use `resolve_team_names_from_filter(team_name, isGroup, conn)` to resolve team names
- Same pattern as other endpoints

#### Step 4: Build SQL Query
Query filters:
- `issue_type = 'Epic'`
- `status_category = 'Done'`
- `cycle_time_days IS NOT NULL`
- `resolved_at >= :start_date`
- `resolved_at IS NOT NULL`
- If team_name provided: `team_name IN (:team_names...)`

Query returns:
- `AVG(cycle_time_days) AS average_epic_cycle_time`
- `COUNT(*) AS epic_count`

#### Step 5: Execute Query
- Use parameterized query for security
- Handle NULL result (no epics found) → return null for average, 0 for count

#### Step 6: Calculate Status
- Call `get_epic_cycle_time_status(avg_cycle_time)` to get status indicator
- Add status to response

#### Step 7: Build Response
Response structure:
```json
{
  "success": true,
  "data": {
    "average_epic_cycle_time": 52.5,
    "average_epic_cycle_time_status": "yellow",
    "epic_count": 15,
    "months": 3,
    // Conditional metadata:
    // If team_name provided and isGroup=false:
    "team_name": "Team A",
    // OR if team_name provided and isGroup=true:
    "group_name": "Engineering Group",
    "teams_in_group": ["Team A", "Team B", "Team C"]
  },
  "message": "Retrieved average epic cycle time: 52.5 days (15 epics)"
}
```

**Edge Cases**:
- No epics found: `average_epic_cycle_time: null`, `average_epic_cycle_time_status: "gray"`, `epic_count: 0`
- Round `average_epic_cycle_time` to 2 decimal places

### Error Handling
- Validate `months` parameter (1-12 range, default 3)
- Handle HTTPException from team resolution
- Handle NULL results gracefully
- Proper error logging and HTTP exceptions

---

## Implementation Order

1. **Add imports** to `pis_service.py` (line 16)
   - Add `fetch_epic_inbound_dependency_data`
   - Add `fetch_epic_outbound_dependency_data`

2. **Add helper function** for cycle time status (around line 197)
   - `get_epic_cycle_time_status()`

3. **Add Endpoint 1**: Top Dependencies Summary (after line 1268, before `get_pi_participating_teams`)
   - Route: `/pis/top-dependencies-summary`
   - Function: `get_top_dependencies_summary()`

4. **Add Endpoint 2**: Average Epic Cycle Time (after top-dependencies-summary)
   - Route: `/pis/average-epic-cycle-time`
   - Function: `get_average_epic_cycle_time()`

---

## Key Patterns to Follow

### Team/Group Resolution Pattern
```python
from database_team_metrics import resolve_team_names_from_filter

# Resolve team names FIRST
team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
```

### Response Metadata Pattern
```python
# Build response data
response_data = {
    # ... main data fields ...
}

# Add team/group metadata (same pattern as other endpoints)
if team_name:
    if isGroup:
        response_data["group_name"] = team_name
        response_data["teams_in_group"] = team_names_list
    else:
        response_data["team_name"] = team_name

return {
    "success": True,
    "data": response_data,
    "message": "..."
}
```

### Error Handling Pattern
```python
try:
    # ... implementation ...
except HTTPException:
    # Re-raise HTTP exceptions (validation errors)
    raise
except Exception as e:
    logger.error(f"Error fetching ...: {e}")
    raise HTTPException(
        status_code=500,
        detail=f"Failed to fetch ...: {str(e)}"
    )
```

### Date Calculation Pattern
```python
from datetime import datetime, timedelta

# Calculate start date (months * 30 days)
start_date = datetime.now().date() - timedelta(days=months * 30)
```

---

## Testing Considerations

### Endpoint 1: Top Dependencies Summary
- Test with PI only (no team filter)
- Test with single team filter
- Test with group filter (isGroup=true)
- Test with PI that has no dependencies
- Test with PI that has < 3 teams with uncompleted dependencies
- Test with PI that has exactly 3 teams
- Test with PI that has > 3 teams (verify only top 3 returned)

### Endpoint 2: Average Epic Cycle Time
- Test with default (3 months)
- Test with different months values (1, 6, 12)
- Test with single team filter
- Test with group filter (isGroup=true)
- Test with no epics found (should return null, gray status)
- Test status thresholds:
  - < 45 days → green
  - 45-60 days → yellow
  - > 60 days → red
  - null → gray

---

## Summary

Both endpoints follow established patterns in the codebase:
- Use shared helper functions for team/group resolution
- Use existing database functions where possible
- Follow consistent response structure
- Include proper error handling
- Support team and group filtering
- Add status indicators where appropriate

The endpoints are ready to be implemented following this plan.
