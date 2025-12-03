# Sprint Burndown - Original vs Current Comparison

## Database Function: `get_sprint_burndown_data_db`

### ORIGINAL (eb5edcb):
```python
def get_sprint_burndown_data_db(team_name: str, sprint_name: str, issue_type: str = "all", conn: Connection = None)
```

**SQL Query:**
```sql
SELECT * FROM get_sprint_burndown_data_for_team(:sprint_name, :issue_type, :team_name);
```

**Row Processing:**
```python
burndown_data.append({
    'snapshot_date': row[0],           # Positional indexing
    'start_date': row[1],              # Positional indexing
    'end_date': row[2],                # Positional indexing
    'remaining_issues': int(row[3]) if row[3] else 0,
    'ideal_remaining': int(row[4]) if row[4] else 0,
    'total_issues': int(row[5]) if row[5] else 0,
    'issues_added_on_day': int(row[6]) if row[6] else 0,
    'issues_removed_on_day': int(row[7]) if row[7] else 0,
    'issues_completed_on_day': int(row[8]) if row[8] else 0
})
```

**Date Handling:**
- Dates are returned as-is from database (could be `datetime.date` objects or strings)
- No explicit date formatting/conversion

---

### CURRENT:
```python
def get_sprint_burndown_data_db(team_names: List[str], sprint_name: str, issue_type: str = "all", conn: Connection = None)
```

**SQL Query:**
```sql
SELECT * FROM get_sprint_burndown_data_for_team(:sprint_name, :issue_type, CAST(:team_names AS text[]));
```

**Row Processing:**
```python
row_dict = dict(row._mapping)  # Named column access
burndown_data.append({
    'snapshot_date': row_dict.get('snapshot_date'),           # Named access
    'start_date': row_dict.get('start_date'),                 # Named access
    'end_date': row_dict.get('end_date'),                     # Named access
    'remaining_issues': int(row_dict.get('remaining_issues', 0)) if row_dict.get('remaining_issues') else 0,
    'ideal_remaining': int(row_dict.get('ideal_remaining', 0)) if row_dict.get('ideal_remaining') else 0,
    'total_issues': int(row_dict.get('total_issues', 0)) if row_dict.get('total_issues') else 0,
    'issues_added_on_day': int(row_dict.get('issues_added_on_day', 0)) if row_dict.get('issues_added_on_day') else 0,
    'issues_removed_on_day': int(row_dict.get('issues_removed_on_day', 0)) if row_dict.get('issues_removed_on_day') else 0,
    'issues_completed_on_day': int(row_dict.get('issues_completed_on_day', 0)) if row_dict.get('issues_completed_on_day') else 0
})
```

**Date Handling:**
- Dates are returned as-is from database (same as original)
- No explicit date formatting/conversion (same as original)

**CHANGES:**
1. ✅ Parameter changed: `team_name: str` → `team_names: List[str]`
2. ✅ SQL changed: `:team_name` → `CAST(:team_names AS text[])`
3. ✅ Row access changed: Positional (`row[0]`) → Named (`row_dict.get('column_name')`)
4. ✅ **Date handling: UNCHANGED** - dates are still returned as-is

---

## Endpoint: `/team-metrics/sprint-burndown`

### ORIGINAL (eb5edcb):

**Parameters:**
- `team_name: str` (required)
- `issue_type: str` (optional, default: "all")
- `sprint_name: str` (optional)

**Date Extraction:**
```python
if burndown_data:
    total_issues_in_sprint = burndown_data[0].get('total_issues', 0)
    start_date = burndown_data[0].get('start_date')      # Direct from burndown_data
    end_date = burndown_data[0].get('end_date')          # Direct from burndown_data
```

**Response:**
```python
{
    "success": True,
    "data": {
        "sprint_id": selected_sprint_id,
        "sprint_name": selected_sprint_name,
        "start_date": start_date,                        # As-is from burndown_data
        "end_date": end_date,                            # As-is from burndown_data
        "burndown_data": burndown_data,
        "team_name": validated_team_name,
        "issue_type": issue_type,
        "total_issues_in_sprint": total_issues_in_sprint
    },
    "message": f"Retrieved sprint burndown data for team '{validated_team_name}' and sprint '{selected_sprint_name}'"
}
```

---

### CURRENT:

**Parameters:**
- `team_name: str` (required)
- `issue_type: str` (optional, default: "all")
- `sprint_name: str` (optional)
- `isGroup: bool` (optional, default: False) **NEW**

**Date Extraction:**
```python
if burndown_data:
    total_issues_in_sprint = burndown_data[0].get('total_issues', 0)
    start_date = burndown_data[0].get('start_date')      # Direct from burndown_data (SAME)
    end_date = burndown_data[0].get('end_date')          # Direct from burndown_data (SAME)
```

**Response:**
```python
{
    "success": True,
    "data": {
        "sprint_id": selected_sprint_id,
        "sprint_name": selected_sprint_name,
        "start_date": start_date,                        # As-is from burndown_data (SAME)
        "end_date": end_date,                            # As-is from burndown_data (SAME)
        "burndown_data": burndown_data,
        "issue_type": issue_type,
        "total_issues_in_sprint": total_issues_in_sprint,
        "isGroup": isGroup,                              # NEW
        # Conditional fields:
        "group_name": team_name,                         # NEW (if isGroup=True)
        "teams_in_group": team_names_list,               # NEW (if isGroup=True)
        "team_name": team_name                           # NEW (if isGroup=False)
    },
    "message": f"Retrieved sprint burndown data for group/team '{team_name}' and sprint '{selected_sprint_name}'"
}
```

---

## Summary of Changes

### ✅ UNCHANGED (Date Handling):
1. **Date extraction**: Still extracts `start_date` and `end_date` directly from `burndown_data[0]` without any formatting
2. **Date return format**: Dates are returned as-is from the database (no conversion to string, no timezone changes)
3. **Date fields in response**: `start_date` and `end_date` in response are exactly as returned from database function

### ✅ CHANGED:
1. **Function parameter**: `team_name: str` → `team_names: List[str]`
2. **SQL parameter**: `:team_name` → `CAST(:team_names AS text[])`
3. **Row access method**: Positional indexing → Named column access (for safety)
4. **Endpoint**: Added `isGroup` parameter and group support
5. **Response structure**: Added `isGroup`, `group_name`, `teams_in_group` fields conditionally

### ⚠️ POTENTIAL ISSUE:
The **date handling is identical** - dates are returned as-is from the database. If the database returns `datetime.date` objects, they will be returned as-is in the response. If the original worked, the current should work the same way, **UNLESS** the SQL function returns dates in a different format when called with an array parameter.

**The error you're seeing suggests the SQL function might be returning dates where integers are expected, or the column order is different when called with an array.**

