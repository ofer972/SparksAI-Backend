# EPIC Endpoint - SQL Queries Summary

## Endpoint
**`GET /api/v1/issues/epics-by-pi`**

## Parameters
- `pi` (required): PI name
- `team_name` (optional): Team name or group name
- `isGroup` (optional, default: false): If true, treat team_name as group name

## Key SQL Queries

### Query 1: Get Epics (with optional team filter)

**If team_name provided (after resolving group to teams):**
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

**If no team_name:**
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

---

### Query 2: Get In-Progress Date and Sprint (batch for all epics)

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

**Note**: For epics that never entered "In Progress", this query returns no rows. Handle null in code.

---

### Query 3: Get Baseline Story Count (batch for all epics, using parent_key)

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

**Note**: 
- `parent_key` links stories to their epic
- This query only returns epics that have in_progress_date (filters out epics that never entered "In Progress")
- For epics with no in_progress_date, use current story count as baseline in code

---

### Query 4: Get Current Story Metrics (batch for all epics)

**Team breakdown:**
```sql
SELECT 
    parent_key as epic_key,
    team_name,
    COUNT(*) as total,
    COUNT(CASE WHEN status_category = 'Done' THEN 1 END) as done
FROM jira_issues
WHERE parent_key IN (:epic_key_0, :epic_key_1, ..., :epic_key_N)
  AND issue_type = 'Story'
GROUP BY parent_key, team_name
ORDER BY parent_key, team_name
```

**Distinct teams involved:**
```sql
SELECT DISTINCT
    parent_key as epic_key,
    team_name
FROM jira_issues
WHERE parent_key IN (:epic_key_0, :epic_key_1, ..., :epic_key_N)
  AND issue_type = 'Story'
ORDER BY parent_key, team_name
```

**Total story counts:**
```sql
SELECT 
    parent_key as epic_key,
    COUNT(*) as current_story_count,
    COUNT(CASE WHEN status_category = 'Done' THEN 1 END) as stories_completed
FROM jira_issues
WHERE parent_key IN (:epic_key_0, :epic_key_1, ..., :epic_key_N)
  AND issue_type = 'Story'
GROUP BY parent_key
```

---

### Query 5: Get Dependency Metrics (batch for all epics)

**Number of relying teams:**
```sql
SELECT 
    epic_key,
    number_of_relying_teams
FROM epic_inbound_dependency_load_by_quarter
WHERE epic_key IN (:epic_key_0, :epic_key_1, ..., :epic_key_N)
  AND quarter_pi_of_epic = :pi
```

**Unresolved dependencies:**
```sql
SELECT 
    parent_key as epic_key,
    COUNT(*) as dependencies_unresolved
FROM jira_issues
WHERE parent_key IN (:epic_key_0, :epic_key_1, ..., :epic_key_N)
  AND dependency = true
  AND status_category != 'Done'
GROUP BY parent_key
```

---

## Implementation Flow

1. **Resolve teams** (if team_name provided):
   ```python
   from database_team_metrics import resolve_team_names_from_filter
   team_names_list = resolve_team_names_from_filter(team_name, isGroup, conn)
   ```

2. **Query 1**: Get all epics for PI (with team filter if provided)

3. **Query 2**: Get in-progress dates for all epics (batch)

4. **Query 3**: Get baseline story count for all epics with in_progress_date (batch)

5. **Query 4**: Get current story metrics for all epics (batch)

6. **Query 5**: Get dependency metrics for all epics (batch)

7. **Combine results** in Python:
   - Calculate `stories_added` = `current_story_count - stories_at_in_progress` (if positive)
   - Calculate `stories_removed` = `stories_at_in_progress - current_story_count` (if positive)
   - Calculate `stories_remaining` = `current_story_count - stories_completed`
   - Use `status_category` directly as `epic_status` (no mapping needed)
   - Build `team_progress_breakdown` from Query 4 results (use "done" field, not "closed")
   - Build `teams_involved` list from Query 4 results

---

## Edge Cases

1. **Epic never in "In Progress"**:
   - `in_progress_date` = null
   - `in_progress_sprint` = null
   - `stories_at_in_progress` = 0
   - `stories_added` = `current_story_count`
   - `stories_removed` = 0

2. **No history records for epic**:
   - Use current story count as baseline
   - `stories_at_in_progress` = `current_story_count`
   - `stories_added` = 0
   - `stories_removed` = 0

3. **Epic not in dependency view**:
   - `number_of_relying_teams` = 0

4. **No stories linked to epic**:
   - All story counts = 0
   - `teams_involved` = []
   - `team_progress_breakdown` = []

5. **Group with no teams**:
   - `resolve_team_names_from_filter` raises HTTPException 404

