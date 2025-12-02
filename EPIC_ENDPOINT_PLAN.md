# EPIC Endpoint Implementation Plan

## Endpoint Name
**`GET /api/v1/issues/epics-by-pi`**

## Purpose
Retrieve comprehensive information about EPICs in a specific PI, including current state, historical baseline data, story tracking, team involvement, and dependency metrics.

## Request Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `pi` | string | Yes | PI name (quarter_pi) to filter epics |
| `team_name` | string | No | Optional filter by owning team name (or group name if isGroup=true) |
| `isGroup` | boolean | No | If true, team_name is treated as a group name (default: false) |

## Response Structure

The endpoint will return a list of epic objects with the following structure:

```json
{
  "success": true,
  "data": {
    "epics": [
      {
        "epic_name": "string",
        "epic_key": "string",
        "owning_team": "string",
        "planned_for_quarter": "Yes/No",
        "epic_status": "string (status_category from database: 'Not Started', 'In Progress', or 'Done')",
        "in_progress_date": "YYYY-MM-DD or null",
        "in_progress_sprint": "string or null",
        "stories_at_in_progress": 0,
        "current_story_count": 0,
        "stories_added": 0,
        "stories_removed": 0,
        "stories_completed": 0,
        "stories_remaining": 0,
        "teams_involved": ["team1", "team2"],
        "team_progress_breakdown": [
          {
            "team_name": "string",
            "done": 0,
            "total": 0
          }
        ],
        "number_of_relying_teams": 0,
        "dependencies_unresolved": 0
      }
    ],
    "count": 0
  },
  "message": "string"
}
```

## Data Source Mapping

### From `jira_issues` Table (Current State)

| Field | Source Column | Notes |
|-------|---------------|-------|
| `epic_name` | `summary` | Where `issue_type = 'Epic'` |
| `epic_key` | `issue_key` | Epic identifier |
| `owning_team` | `team_name` | Team that owns the epic |
| `planned_for_quarter` | `quarter_pi` | Derived: "Yes" if `quarter_pi` matches requested PI, else "No" |
| `epic_status` | `status_category` | Use directly from database (no mapping needed) |
| `current_story_count` | COUNT of stories | Stories where `parent_key = epic_key` AND `issue_type = 'Story'` (parent_key is the connection between epic and children) |
| `stories_completed` | COUNT with filter | Stories where `parent_key = epic_key` AND `status_category = 'Done'` |
| `stories_remaining` | Calculated | `current_story_count - stories_completed` |
| `teams_involved` | DISTINCT `team_name` | From stories where `parent_key = epic_key` |
| `team_progress_breakdown` | GROUP BY `team_name` | For each team: COUNT(done), COUNT(total) |

**Note**: The `parent_key` column in `jira_issues` and `jira_issue_history` tables links stories to their parent epic. Stories have `parent_key = epic_key` to establish the epic-story relationship.

### From `jira_issue_history` Table (Historical Data)

| Field | Source Column | Logic |
|-------|---------------|-------|
| `in_progress_date` | `snapshot_date` | First date where `status_category = 'In Progress'` for the epic |
| `in_progress_sprint` | `sprint_ids[0]` | First sprint ID from the first "In Progress" snapshot |
| `stories_at_in_progress` | COUNT from history | Count of stories linked to epic on `in_progress_date` |
| `stories_added` | Calculated | `current_story_count - stories_at_in_progress` (if positive) |
| `stories_removed` | Calculated | `stories_at_in_progress - current_story_count` (if positive) |

**Note**: For `stories_at_in_progress`, we need to query `jira_issue_history` on the `in_progress_date` snapshot to get the count of stories that had `parent_key = epic_key` at that time. This requires joining history records for stories.

### From Dependency Views/Tables

| Field | Source | Notes |
|-------|--------|-------|
| `number_of_relying_teams` | `epic_inbound_dependency_load_by_quarter` | Count of teams that rely on this epic |
| `dependencies_unresolved` | `jira_issues` | Count of issues with `dependency = true` AND `status_category != 'Done'` AND linked to epic |

## Implementation Strategy

### Step 0: Resolve Team Names (if team_name provided)
Use `resolve_team_names_from_filter` helper function (same pattern as other endpoints):
```python
from database_team_metrics import resolve_team_names_from_filter

team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
# Returns: None (all teams), or List[str] (specific teams)
```

### Step 1: Main Epic Query (from `jira_issues`)
Get all epics for the PI, optionally filtered by team(s):

**If team_name provided (single team or group):**
```sql
SELECT 
    issue_key as epic_key,
    summary as epic_name,
    team_name as owning_team,
    quarter_pi,
    status_category,
    issue_id
FROM jira_issues
WHERE issue_type = 'Epic'
  AND quarter_pi = :pi
  AND team_name IN (:team_name_0, :team_name_1, ..., :team_name_N)
ORDER BY issue_key
```

**If no team_name provided (all teams):**
```sql
SELECT 
    issue_key as epic_key,
    summary as epic_name,
    team_name as owning_team,
    quarter_pi,
    status_category,
    issue_id
FROM jira_issues
WHERE issue_type = 'Epic'
  AND quarter_pi = :pi
ORDER BY issue_key
```

**Parameters:**
- `:pi` - PI name (required)
- `:team_name_0, :team_name_1, ...` - Team names from resolved list (if team_name provided)

### Step 2: Find In-Progress Date (from `jira_issue_history`)
For each epic, find the first date it entered "In Progress" and the sprint:

**Query for single epic:**
```sql
SELECT 
    issue_key,
    MIN(snapshot_date) as in_progress_date
FROM jira_issue_history
WHERE issue_key = :epic_key
  AND status_category = 'In Progress'
GROUP BY issue_key
```

**Get sprint from first In Progress snapshot:**
```sql
SELECT 
    sprint_ids[1] as in_progress_sprint
FROM jira_issue_history
WHERE issue_key = :epic_key
  AND snapshot_date = :in_progress_date
  AND status_category = 'In Progress'
ORDER BY snapshot_date
LIMIT 1
```

**Parameters:**
- `:epic_key` - Epic issue_key
- `:in_progress_date` - Date from first query

**Alternative: Combined query for all epics at once:**
```sql
SELECT 
    h1.issue_key,
    h1.snapshot_date as in_progress_date,
    h1.sprint_ids[1] as in_progress_sprint
FROM (
    SELECT 
        issue_key,
        MIN(snapshot_date) as min_date
    FROM jira_issue_history
    WHERE issue_key IN (:epic_key_0, :epic_key_1, ..., :epic_key_N)
      AND status_category = 'In Progress'
    GROUP BY issue_key
) first_in_progress
INNER JOIN jira_issue_history h1 
    ON h1.issue_key = first_in_progress.issue_key
    AND h1.snapshot_date = first_in_progress.min_date
    AND h1.status_category = 'In Progress'
ORDER BY h1.snapshot_date
```

**Parameters:**
- `:epic_key_0, :epic_key_1, ...` - All epic keys from Step 1

### Step 3: Get Baseline Story Count (from `jira_issue_history`)
Count stories linked to each epic on its in-progress date (using parent_key relationship) - batch query for all epics:

**Single batch query:**
```sql
SELECT 
    h.parent_key as epic_key,
    COUNT(DISTINCT h.issue_key) as story_count
FROM jira_issue_history h
INNER JOIN (
    SELECT 
        h1.issue_key,
        h1.snapshot_date as in_progress_date
    FROM (
        SELECT 
            issue_key,
            MIN(snapshot_date) as min_date
        FROM jira_issue_history
        WHERE issue_key IN (:epic_key_0, :epic_key_1, ..., :epic_key_N)
          AND status_category = 'In Progress'
        GROUP BY issue_key
    ) first_in_progress
    INNER JOIN jira_issue_history h1 
        ON h1.issue_key = first_in_progress.issue_key
        AND h1.snapshot_date = first_in_progress.min_date
        AND h1.status_category = 'In Progress'
) epic_dates ON h.parent_key = epic_dates.issue_key
    AND h.snapshot_date = epic_dates.in_progress_date
WHERE h.issuetype = 'Story'
GROUP BY h.parent_key
```

**Parameters:**
- `:epic_key_0, :epic_key_1, ...` - All epic keys from Step 1

**Note**: 
- This query joins each epic with its specific in_progress_date from Step 2
- Only returns epics that have an in_progress_date (epics that entered "In Progress")
- For epics without in_progress_date, use current story count as baseline in code

### Step 4: Get Current Story Metrics (from `jira_issues`)
Get current story counts and team breakdown for all epics:

**Query for all epics at once:**
```sql
SELECT 
    parent_key as epic_key,
    team_name,
    COUNT(*) as total,
    COUNT(CASE WHEN status_category = 'Done' THEN 1 END) as closed
FROM jira_issues
WHERE parent_key IN (:epic_key_0, :epic_key_1, ..., :epic_key_N)
  AND issue_type = 'Story'
GROUP BY parent_key, team_name
ORDER BY parent_key, team_name
```

**Parameters:**
- `:epic_key_0, :epic_key_1, ...` - All epic keys from Step 1

**Also get distinct teams involved:**
```sql
SELECT DISTINCT
    parent_key as epic_key,
    team_name
FROM jira_issues
WHERE parent_key IN (:epic_key_0, :epic_key_1, ..., :epic_key_N)
  AND issue_type = 'Story'
ORDER BY parent_key, team_name
```

### Step 5: Get Dependency Metrics

**Number of relying teams (from dependency view):**
```sql
SELECT 
    epic_key,
    number_of_relying_teams
FROM epic_inbound_dependency_load_by_quarter
WHERE epic_key IN (:epic_key_0, :epic_key_1, ..., :epic_key_N)
  AND quarter_pi_of_epic = :pi
```

**Parameters:**
- `:epic_key_0, :epic_key_1, ...` - All epic keys from Step 1
- `:pi` - PI name

**Unresolved dependencies (from jira_issues):**
```sql
SELECT 
    parent_key as epic_key,
    COUNT(*) as unresolved_count
FROM jira_issues
WHERE parent_key IN (:epic_key_0, :epic_key_1, ..., :epic_key_N)
  AND dependency = true
  AND status_category != 'Done'
GROUP BY parent_key
```

**Parameters:**
- `:epic_key_0, :epic_key_1, ...` - All epic keys from Step 1

## Query Optimization Approach

**Recommended: Batch Approach**
1. **Step 1**: Single query to get all epics (with team filter if provided)
2. **Step 2**: Batch query to get in-progress dates for all epics at once
3. **Step 3**: Single batch query for baseline story count (joins with Step 2 results)
4. **Step 4**: Single batch query for current story metrics (all epics)
5. **Step 5**: Single batch query for dependency metrics (all epics)

This minimizes database round trips - only 5 queries total regardless of number of epics.

## Edge Cases to Handle

1. **Epic never entered "In Progress"**: 
   - `in_progress_date` = null
   - `stories_at_in_progress` = 0
   - `stories_added` = `current_story_count`
   - `stories_removed` = 0

2. **No stories linked to epic**:
   - All story counts = 0
   - `teams_involved` = []
   - `team_progress_breakdown` = []

3. **Epic not in dependency views**:
   - `number_of_relying_teams` = 0

4. **History data missing**:
   - If `jira_issue_history` has no records for epic, use current state as baseline
   - `stories_at_in_progress` = `current_story_count`
   - `stories_added` = 0
   - `stories_removed` = 0

## Dependencies Reference

Based on existing endpoints:
- **Group/Team resolution**: Use `resolve_team_names_from_filter(team_name, isGroup, conn)` from `database_team_metrics.py` (same pattern as `/issues/issue-status-duration`, `/pis/pi-burndown`, etc.)
- **Dependency views**: Use `epic_inbound_dependency_load_by_quarter` (similar to `/issues/epic-inbound-dependency-load-by-quarter`)
- **Dependency flag**: Use `dependency` column from `jira_issues` (boolean)
- **Pattern**: Follow the pattern used in `/issues/epic-outbound-dependency-metrics-by-quarter`
- **Parent-Child relationship**: Stories link to epics via `parent_key = epic_key` in both `jira_issues` and `jira_issue_history` tables

## File Location
- **Service File**: `issues_service.py`
- **Endpoint Path**: `/issues/epics-by-pi`
- **Router**: `issues_router` (already exists)

## Testing Considerations

1. Test with PI that has multiple epics
2. Test with epic that has never been "In Progress"
3. Test with epic that has no stories
4. Test with epic that has stories across multiple teams
5. Test with epic that has dependencies
6. Test with epic that has no history records
7. Test filtering by `team_name` (single team)
8. Test filtering by `team_name` with `isGroup=true` (group of teams)
9. Test with group that has multiple teams
10. Test with group that has nested child groups

## Performance Considerations

- Consider adding indexes on:
  - `jira_issues(issue_type, quarter_pi, parent_key)`
  - `jira_issue_history(issue_key, status_category, snapshot_date)`
  - `jira_issue_history(parent_key, snapshot_date)`
- For PIs with many epics, consider pagination (future enhancement)
- Cache results if this endpoint is called frequently

## Next Steps After Approval

1. Implement the endpoint in `issues_service.py`
2. Add proper error handling and validation
3. Add logging
4. Test with sample data
5. Update API documentation

