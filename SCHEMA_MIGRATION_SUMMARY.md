# Database Schema Migration: Many-to-Many Teams and Groups

## Summary
Successfully refactored the database schema to support many-to-many relationships between teams and groups. Teams can now belong to multiple groups simultaneously.

## Schema Changes

### Old Schema (One-to-Many)
```
┌─────────────┐
│ team_groups │ (Group hierarchy)
│─────────────│
│ group_key   │ PK
│ group_name  │
│ parent_key  │ FK → team_groups
└─────────────┘
       ↑
       │ (one-to-many)
       │
┌─────────────┐
│   teams     │
│─────────────│
│ team_key    │ PK
│ team_name   │
│ group_key   │ FK → team_groups (nullable)
└─────────────┘
```

### New Schema (Many-to-Many)
```
┌─────────────┐
│   groups    │ (Group hierarchy, renamed from team_groups)
│─────────────│
│ group_key   │ PK
│ group_name  │
│ parent_key  │ FK → groups
└─────────────┘
       ↑
       │
       │ (many-to-many)
       │
┌─────────────┐      ┌─────────────┐
│ team_groups │      │   teams     │
│─────────────│      │─────────────│
│ team_id     │ FK ← │ team_key    │ PK
│ group_id    │ FK → │ team_name   │
│ PK(team_id, │      │ (no group_  │
│    group_id)│      │  key column)│
└─────────────┘      └─────────────┘
```

## Migration Strategy

The `create_teams_and_team_groups_tables_if_not_exists()` function now includes automatic migration:

1. **Detects old schema**: Checks if `team_groups` table exists but `groups` doesn't
2. **Renames table**: `team_groups` → `groups`
3. **Updates constraints**: Renames self-referencing FK
4. **Creates junction table**: New `team_groups` table with (team_id, group_id)
5. **Migrates data**: Copies existing team-group relationships to junction table
6. **Removes old column**: Drops `group_key` from teams table

## Files Modified

### 1. `database_table_creation.py`
- ✅ Added migration logic to `create_teams_and_team_groups_tables_if_not_exists()`
- ✅ Handles both fresh installs and migrations from old schema
- ✅ Creates proper indexes on junction table

### 2. `groups_service.py`
- ✅ Updated all queries to reference `groups` instead of `team_groups`
- ✅ `get_all_groups()`: References `public.groups`
- ✅ `get_teams_in_group()`: Uses JOIN with `team_groups` junction table
- ✅ `create_group()`: Inserts into `public.groups`
- ✅ `update_group()`: Updates `public.groups`
- ✅ `delete_group()`: Deletes from `public.groups` and removes associations

### 3. `teams_service.py`
- ✅ Updated Pydantic models: `group_key` → `group_keys` (list)
- ✅ `get_all_teams()`: Returns `group_keys` and `group_names` arrays
- ✅ `create_team()`: Creates team + inserts associations into junction table
- ✅ `update_team()`: Updates team + replaces associations in junction table
- ✅ `batch_assign_teams_to_group()`: Inserts into junction table
- ✅ `remove_team_from_group()`: Deletes from junction table

### 4. `database_team_metrics.py`
- ✅ Updated `resolve_team_names_from_filter()`: Uses new JOIN pattern with `team_groups` junction table and `groups` table

### 5. `database_reports.py`
- ✅ No changes needed (no direct references to old schema)

## API Changes

### Request/Response Format Changes

#### Teams Endpoints

**Before:**
```json
{
  "team_key": 1,
  "team_name": "Team Alpha",
  "group_key": 5,
  "group_name": "Engineering"
}
```

**After:**
```json
{
  "team_key": 1,
  "team_name": "Team Alpha",
  "group_keys": [5, 7],
  "group_names": ["Engineering", "Product"]
}
```

#### Create/Update Team

**Before:**
```json
{
  "team_name": "Team Beta",
  "number_of_team_members": 8,
  "group_key": 3
}
```

**After:**
```json
{
  "team_name": "Team Beta",
  "number_of_team_members": 8,
  "group_keys": [3, 6]
}
```

## Testing Checklist

- [ ] Test fresh installation (new database)
- [ ] Test migration from existing database
- [ ] Test creating teams with multiple groups
- [ ] Test updating team's groups
- [ ] Test batch assigning teams to groups
- [ ] Test removing teams from groups
- [ ] Test deleting groups (should remove associations)
- [ ] Test deleting teams (should remove associations via CASCADE)
- [ ] Test group hierarchy queries
- [ ] Test filtering teams by group
- [ ] Test frontend compatibility (TeamsGroupsContext should still work)

## Backward Compatibility Notes

### Breaking Changes
1. **API Response Format**: Teams now return `group_keys` (array) instead of `group_key` (single value)
2. **API Request Format**: Creating/updating teams now expects `group_keys` (array) instead of `group_key`

### Frontend Impact
The frontend `TeamsGroupsContext` should be mostly compatible because:
- It fetches teams and groups separately via `/teams` and `/groups` endpoints
- The context builds its own tree structure
- However, you may need to update team display to show multiple groups

## SQL Examples

### Query teams with their groups
```sql
SELECT 
    t.team_key,
    t.team_name,
    array_agg(g.group_name) as group_names
FROM teams t
LEFT JOIN team_groups tg ON t.team_key = tg.team_id
LEFT JOIN groups g ON tg.group_id = g.group_key
GROUP BY t.team_key, t.team_name;
```

### Query groups with their teams
```sql
SELECT 
    g.group_key,
    g.group_name,
    array_agg(t.team_name) as team_names
FROM groups g
LEFT JOIN team_groups tg ON g.group_key = tg.group_id
LEFT JOIN teams t ON tg.team_id = t.team_key
GROUP BY g.group_key, g.group_name;
```

### Add team to multiple groups
```sql
INSERT INTO team_groups (team_id, group_id) 
VALUES (1, 5), (1, 7), (1, 9)
ON CONFLICT DO NOTHING;
```

## Rollback Plan

If you need to rollback to the old schema:

```sql
-- 1. Add group_key column back to teams
ALTER TABLE teams ADD COLUMN group_key INT;

-- 2. Migrate first group association back (if team had multiple)
UPDATE teams t
SET group_key = (
    SELECT group_id 
    FROM team_groups 
    WHERE team_id = t.team_key 
    LIMIT 1
);

-- 3. Drop junction table
DROP TABLE team_groups;

-- 4. Rename groups back to team_groups
ALTER TABLE groups RENAME TO team_groups;

-- 5. Add FK constraint
ALTER TABLE teams 
ADD CONSTRAINT teams_group_key_fkey 
FOREIGN KEY (group_key) REFERENCES team_groups(group_key);
```

**Note**: Rolling back will lose information about teams belonging to multiple groups!

## Next Steps

1. **Frontend Updates**: Update `TeamsGroupsContext` and any components that display team-group relationships
2. **API Documentation**: Update API docs to reflect new request/response formats
3. **Integration Testing**: Test the entire flow from UI to database
4. **Data Validation**: Verify all existing team-group relationships migrated correctly

---

**Migration Date**: 2024
**Author**: AI Assistant
**Status**: ✅ Complete

