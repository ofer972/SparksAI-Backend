# Create Group Job Endpoint - Implementation Plan

## Overview
Add a new endpoint `POST /api/v1/agent-jobs/create-group-job` that creates agent jobs for groups (similar to `create-team-job` but for groups). Also revert `group_name` from `TeamJobCreateRequest`.

## Changes Summary

### 1. Revert Changes to TeamJobCreateRequest
**File:** `agent_jobs_service.py`

- **Remove** `group_name: Optional[str] = None` from `TeamJobCreateRequest` model
- **Update** `POST /api/v1/agent-jobs/create-team-job` endpoint:
  - Remove `group_name` from INSERT column list
  - Remove `group_name` from INSERT VALUES
  - Remove `group_name` from RETURNING clause
  - Remove `group_name` from INSERT parameters

### 2. Create New Request Model
**File:** `agent_jobs_service.py`

**New Model:**
```python
class GroupJobCreateRequest(BaseModel):
    job_type: str
    group_name: str
```

### 3. Create Group Validation Function
**File:** `agent_jobs_service.py`

**New Function:**
```python
def validate_group_exists(group_name: str, conn: Connection):
    """Validate that the group exists in the database by checking groups table"""
    from groups_teams_cache import group_exists_by_name_in_db
    
    if not group_exists_by_name_in_db(group_name, conn):
        raise HTTPException(status_code=404, detail=f"Group '{group_name}' not found")
```

### 4. Create Group Job Validation Function
**File:** `agent_jobs_service.py`

**New Function:**
```python
def validate_group_job_request(job_type: str, group_name: str, conn: Connection):
    """Validate group job creation request"""
    # Validate job_type is not empty
    # Validate group_name is not empty
    # Call validate_group_exists(group_name, conn)
```

### 5. Create New Endpoint
**File:** `agent_jobs_service.py`

**New Endpoint:** `POST /api/v1/agent-jobs/create-group-job`

**Functionality:**
- Accept `GroupJobCreateRequest` with `job_type` and `group_name`
- Normalize job_type (convert "Daily Agent" to "Daily Progress")
- Validate request using `validate_group_job_request()`
- INSERT into `agent_jobs` table:
  - Set `group_name` = request.group_name
  - Set `team_name` = NULL
  - Set `pi` = NULL
  - Set `status` = 'Pending'
- RETURN job with `group_name` included
- Return success response

**SQL INSERT:**
```sql
INSERT INTO agent_jobs 
(job_type, team_name, group_name, pi, status, created_at, updated_at)
VALUES (:job_type, NULL, :group_name, NULL, 'Pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
RETURNING job_id, job_type, team_name, group_name, pi, status, created_at
```

## Files to Modify

1. **agent_jobs_service.py**
   - Remove `group_name` from `TeamJobCreateRequest`
   - Update `create_team_job()` endpoint to remove `group_name` handling
   - Add `GroupJobCreateRequest` model
   - Add `validate_group_exists()` function
   - Add `validate_group_job_request()` function
   - Add `create_group_job()` endpoint

## Endpoint Comparison

### Current: `POST /api/v1/agent-jobs/create-team-job`
- Input: `job_type`, `team_name`
- Database: Sets `team_name`, `group_name` = NULL, `pi` = NULL
- Validates: Team exists

### New: `POST /api/v1/agent-jobs/create-group-job`
- Input: `job_type`, `group_name`
- Database: Sets `group_name`, `team_name` = NULL, `pi` = NULL
- Validates: Group exists

## Testing Considerations

1. **Backward Compatibility:**
   - `create-team-job` endpoint unchanged (except removing group_name support)
   - Existing team job creation continues to work

2. **New Functionality:**
   - Can create jobs for groups via new endpoint
   - Group validation ensures group exists before creating job

3. **Database:**
   - Jobs created via `create-group-job` will have `group_name` set and `team_name` = NULL
   - Jobs created via `create-team-job` will have `team_name` set and `group_name` = NULL

## Estimated Impact

- **Low Risk:** New endpoint, doesn't affect existing functionality
- **Minimal Code Changes:** ~50-60 lines of code
- **Backward Compatible:** Existing endpoints unchanged

