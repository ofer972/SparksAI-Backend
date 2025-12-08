# Plan: Standardize PI Services Endpoints (team → team_name + isGroup support)

## Overview
Standardize 4 PI service endpoints to use `team_name` parameter and add `isGroup` support:
1. `/pis/get-pi-status-for-today`
2. `/pis/get-pi-status-for-today-by-team`
3. `/pis/WIP`
4. `/pis/get-pi-progress`

---

## Analysis Summary

### Database Functions/Views Used:
- ✅ `get_pi_summary_data()` - **Already accepts array of team names** (via `target_team_names` parameter)
- ✅ `get_pi_summary_data_by_team()` - **Already accepts array of team names** (via `target_team_names` parameter)
- ❌ `fetch_wip_data_from_db()` - **Uses DIRECT SQL** - needs modification to accept team list

### Direct SQL Queries Found:
1. **`fetch_wip_data_from_db()`** (pis_service.py:243-319)
   - Uses: `SELECT FROM public.jira_issues WHERE ...`
   - Current: Accepts single `team` parameter
   - Needs: Accept `team_names_list` and use `team_name = ANY(:team_names)`

2. **WIP query in `/pis/get-pi-status-for-today-by-team`** (pis_service.py:934-942)
   - Uses: `SELECT FROM public.jira_issues WHERE ...`
   - Current: ✅ Already supports `team_name = ANY(:team_names)` when `team_names_list` is provided
   - Status: **No changes needed**

---

## Detailed Plan by Endpoint

### 1. `/pis/get-pi-status-for-today` (pis_service.py:756-858)

**Current State:**
- Parameter: `team: str` (line 761)
- Has `isGroup` support (line 762)
- Uses `resolve_team_names_from_filter()` correctly (line 794)
- Calls `fetch_pi_summary_data()` with `team_names_list` ✅
- Calls `fetch_wip_data_from_db()` with single team only ❌

**Changes Required:**
1. ✅ Change parameter from `team` to `team_name` (line 761)
2. ✅ Update docstring to reflect `team_name` (line 775)
3. ✅ Update logging to use `team_name` (line 797)
4. ❌ **Modify `fetch_wip_data_from_db()` to accept `team_names_list`** (see helper function changes)
5. ✅ Update response metadata to use `team_name` instead of `team` (if any)

**Database Function Status:**
- `get_pi_summary_data()` - ✅ Already supports team array - NO CHANGES NEEDED

---

### 2. `/pis/get-pi-status-for-today-by-team` (pis_service.py:861-1011)

**Current State:**
- Parameter: `team: str` (line 866)
- Has `isGroup` support (line 867)
- Uses `resolve_team_names_from_filter()` correctly (line 898)
- Calls `fetch_pi_summary_data_by_team()` with `team_names_list` ✅
- WIP query already supports `team_name = ANY(:team_names)` ✅

**Changes Required:**
1. ✅ Change parameter from `team` to `team_name` (line 866)
2. ✅ Update docstring to reflect `team_name` (line 881)
3. ✅ Update logging to use `team_name` (line 901)
4. ✅ Update response metadata (lines 988-995) to use `team_name` instead of `team`
5. ✅ No changes needed to WIP query - already supports team list

**Database Function Status:**
- `get_pi_summary_data_by_team()` - ✅ Already supports team array - NO CHANGES NEEDED

---

### 3. `/pis/WIP` (pis_service.py:1014-1077)

**Current State:**
- Parameter: `team: str` (line 1017)
- ❌ **NO `isGroup` support**
- Calls `fetch_wip_data_from_db()` with single team only ❌

**Changes Required:**
1. ✅ Change parameter from `team` to `team_name` (line 1017)
2. ✅ Add `isGroup: bool = Query(False)` parameter (new line after 1017)
3. ✅ Import and use `resolve_team_names_from_filter()` to resolve teams
4. ✅ Update docstring to include `team_name` and `isGroup` (lines 1027-1030)
5. ✅ Update logging to include `team_name` and `isGroup` (add logging)
6. ❌ **Modify `fetch_wip_data_from_db()` to accept `team_names_list`** (see helper function changes)
7. ✅ Update response metadata to use `team_name` instead of `team` (line 1063)

**Database Function Status:**
- Uses DIRECT SQL via `fetch_wip_data_from_db()` - **NEEDS MODIFICATION**

---

### 4. `/pis/get-pi-progress` (pis_service.py:1080-1255)

**Current State:**
- Parameter: `team: str` (line 1085)
- ❌ **NO `isGroup` support**
- ❌ **BUG**: Line 1125 passes single `team` string instead of `team_names_list` to `fetch_pi_summary_data()`

**Changes Required:**
1. ✅ Change parameter from `team` to `team_name` (line 1085)
2. ✅ Add `isGroup: bool = Query(False)` parameter (new line after 1085)
3. ✅ Import and use `resolve_team_names_from_filter()` to resolve teams
4. ✅ Update docstring to include `team_name` and `isGroup` (lines 1098-1102)
5. ✅ Update logging to use `team_name` and `isGroup` (line 1118)
6. ✅ **FIX BUG**: Change line 1125 from `target_team_names=team` to `target_team_names=team_names_list`
7. ✅ Update response metadata to use `team_name` instead of `team` (line 1240)

**Database Function Status:**
- `get_pi_summary_data()` - ✅ Already supports team array - NO CHANGES NEEDED

---

## Helper Function Changes

### `fetch_wip_data_from_db()` (pis_service.py:243-319)

**Current Signature:**
```python
def fetch_wip_data_from_db(
    pi: str,
    team: Optional[str] = None,  # Single team only
    project: Optional[str] = None,
    conn: Connection = None
) -> Dict[str, Any]:
```

**New Signature:**
```python
def fetch_wip_data_from_db(
    pi: str,
    team_names: Optional[List[str]] = None,  # Changed to list
    project: Optional[str] = None,
    conn: Connection = None
) -> Dict[str, Any]:
```

**SQL Changes Required:**
- Current (line 277-279):
  ```python
  if team:
      where_conditions.append("team_name = :team")
      params["team"] = team
  ```

- New:
  ```python
  if team_names:
      where_conditions.append("team_name = ANY(:team_names)")
      params["team_names"] = team_names
  ```

**Backward Compatibility:**
- Consider keeping old signature with deprecation warning, OR
- Update all callers (only 2 places: line 817 and line 1048)

---

## Implementation Checklist

### Phase 1: Helper Function Update
- [ ] Modify `fetch_wip_data_from_db()` to accept `team_names: Optional[List[str]]`
- [ ] Update SQL query to use `team_name = ANY(:team_names)`
- [ ] Update function docstring
- [ ] Update all callers (2 locations)

### Phase 2: Endpoint 1 - `/pis/get-pi-status-for-today`
- [ ] Change `team` → `team_name` parameter
- [ ] Update docstring
- [ ] Update logging
- [ ] Update call to `fetch_wip_data_from_db()` to pass `team_names_list`
- [ ] Test with single team
- [ ] Test with group (isGroup=true)

### Phase 3: Endpoint 2 - `/pis/get-pi-status-for-today-by-team`
- [ ] Change `team` → `team_name` parameter
- [ ] Update docstring
- [ ] Update logging
- [ ] Update response metadata to use `team_name`
- [ ] Test with single team
- [ ] Test with group (isGroup=true)

### Phase 4: Endpoint 3 - `/pis/WIP`
- [ ] Change `team` → `team_name` parameter
- [ ] Add `isGroup: bool = Query(False)` parameter
- [ ] Add `resolve_team_names_from_filter()` call
- [ ] Update docstring
- [ ] Update logging
- [ ] Update call to `fetch_wip_data_from_db()` to pass `team_names_list`
- [ ] Update response metadata
- [ ] Test with single team
- [ ] Test with group (isGroup=true)

### Phase 5: Endpoint 4 - `/pis/get-pi-progress`
- [ ] Change `team` → `team_name` parameter
- [ ] Add `isGroup: bool = Query(False)` parameter
- [ ] Add `resolve_team_names_from_filter()` call
- [ ] **FIX BUG**: Change `target_team_names=team` → `target_team_names=team_names_list`
- [ ] Update docstring
- [ ] Update logging
- [ ] Update response metadata
- [ ] Test with single team
- [ ] Test with group (isGroup=true)

---

## Database Functions/Views Status

### ✅ NO CHANGES NEEDED (Already support team arrays):
1. `get_pi_summary_data()` - Accepts `target_team_names` as `text[]`
2. `get_pi_summary_data_by_team()` - Accepts `target_team_names` as `text[]`

### ❌ NEEDS MODIFICATION (Direct SQL):
1. `fetch_wip_data_from_db()` - Currently uses single team filter, needs `ANY(:team_names)`

---

## Testing Plan

For each endpoint, test:
1. ✅ No team filter (team_name=None)
2. ✅ Single team (team_name="TeamA", isGroup=false)
3. ✅ Group (team_name="GroupX", isGroup=true)
4. ✅ Verify SQL queries use correct team filtering
5. ✅ Verify response includes correct team_name metadata

---

## Notes

- All database functions already support team arrays - no database changes needed
- Only `fetch_wip_data_from_db()` helper function needs SQL modification
- Pattern matches existing implementation in `/pis/get-pi-status-for-today-by-team` WIP query
- Backward compatibility: Consider keeping `team` as deprecated alias (optional)
