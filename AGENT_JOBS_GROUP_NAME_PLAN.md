# Agent Jobs Table - Add group_name Column - Implementation Plan

## Overview
Add a new `group_name VARCHAR(255)` column to the `agent_jobs` table to support group-level agent jobs, similar to how `team_name` is currently used.

## Database Changes

### 1. Table Schema Update
**File:** `database_table_creation.py`

**Current Table Structure:**
```sql
CREATE TABLE public.agent_jobs (
    job_id SERIAL PRIMARY KEY,
    job_type VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    claimed_by VARCHAR(100),
    claimed_at TIMESTAMP WITH TIME ZONE,
    job_data JSONB,
    result TEXT,
    error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    team_name VARCHAR(255),
    input_sent TEXT,
    pi VARCHAR(50)
);
```

**Updated Table Structure (for new deployments):**
```sql
CREATE TABLE public.agent_jobs (
    job_id SERIAL PRIMARY KEY,
    job_type VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    claimed_by VARCHAR(100),
    claimed_at TIMESTAMP WITH TIME ZONE,
    job_data JSONB,
    result TEXT,
    error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    team_name VARCHAR(255),
    group_name VARCHAR(255),  -- NEW COLUMN (nullable)
    input_sent TEXT,
    pi VARCHAR(50)
);
```

**Note:** Column is nullable to support existing records. No additional index needed initially (can be added later if needed for filtering).

### 2. ALTER TABLE Command (for manual execution)
```sql
ALTER TABLE public.agent_jobs 
ADD COLUMN group_name VARCHAR(255);
```

---

## Code Changes

### 3. Endpoints That Return Agent Jobs Data

#### 3.1. `GET /api/v1/agent-jobs` - Collection endpoint
**Location:** `agent_jobs_service.py` line ~126

**Current:** Returns selected fields (explicit SELECT list)
**Change:** Add `group_name` to SELECT list and response dictionary

**Current SELECT:**
```sql
SELECT 
    job_id,
    job_type,
    team_name,
    pi,
    status,
    claimed_by,
    claimed_at,
    result,
    error
FROM agent_jobs
```

**Updated SELECT:**
```sql
SELECT 
    job_id,
    job_type,
    team_name,
    group_name,  -- NEW
    pi,
    status,
    claimed_by,
    claimed_at,
    result,
    error
FROM agent_jobs
```

**Current Response Dictionary:**
```python
job_dict = {
    "job_id": row[0],
    "job_type": row[1],
    "team_name": row[2],
    "pi": row[3],
    "status": row[4],
    "claimed_by": row[5],
    "claimed_at": row[6],
    "result": result_text,
    "error": row[8]
}
```

**Updated Response Dictionary:**
```python
job_dict = {
    "job_id": row[0],
    "job_type": row[1],
    "team_name": row[2],
    "group_name": row[3],  -- NEW
    "pi": row[4],  -- Index shifted
    "status": row[5],  -- Index shifted
    "claimed_by": row[6],  -- Index shifted
    "claimed_at": row[7],  -- Index shifted
    "result": result_text,  -- Index shifted
    "error": row[9]  -- Index shifted
}
```

#### 3.2. `POST /api/v1/agent-jobs/claim-next` - Claim next job
**Location:** `agent_jobs_service.py` line ~201

**Current:** Uses `RETURNING *` which will automatically include `group_name`
**Change:** ✅ No code change needed - `group_name` will be included automatically

#### 3.3. `GET /api/v1/agent-jobs/{job_id}` - Get single job
**Location:** `agent_jobs_service.py` line ~264

**Current:** Uses `SELECT *` which will automatically include `group_name`
**Change:** ✅ No code change needed - `group_name` will be included automatically

#### 3.4. `POST /api/v1/agent-jobs/{job_id}/cancel` - Cancel job
**Location:** `agent_jobs_service.py` line ~511

**Current:** Uses explicit RETURNING list
**Change:** Add `group_name` to RETURNING clause

**Current RETURNING:**
```sql
RETURNING job_id, job_type, team_name, pi, status, created_at, updated_at
```

**Updated RETURNING:**
```sql
RETURNING job_id, job_type, team_name, group_name, pi, status, created_at, updated_at
```

#### 3.5. `PATCH /api/v1/agent-jobs/{job_id}` - Update job
**Location:** `agent_jobs_service.py` line ~579

**Current:** Uses explicit RETURNING list in two places
**Change:** Add `group_name` to both RETURNING clauses

**Current RETURNING (claimed update):**
```sql
RETURNING job_id, job_type, team_name, pi, status, claimed_by, claimed_at, job_data, input_sent, result, error, created_at, updated_at
```

**Updated RETURNING (claimed update):**
```sql
RETURNING job_id, job_type, team_name, group_name, pi, status, claimed_by, claimed_at, job_data, input_sent, result, error, created_at, updated_at
```

**Current RETURNING (regular update):**
```sql
RETURNING job_id, job_type, team_name, pi, status, claimed_by, claimed_at, job_data, input_sent, result, error, created_at, updated_at
```

**Updated RETURNING (regular update):**
```sql
RETURNING job_id, job_type, team_name, group_name, pi, status, claimed_by, claimed_at, job_data, input_sent, result, error, created_at, updated_at
```

#### 3.6. `DELETE /api/v1/agent-jobs/{job_id}` - Delete job
**Location:** `agent_jobs_service.py` line ~656

**Change:** ✅ No change needed - doesn't return job data

---

### 4. Endpoints That Create Agent Jobs

#### 4.1. `POST /api/v1/agent-jobs/create-team-job` - Create team job
**Location:** `agent_jobs_service.py` line ~318

**Current:** INSERT with explicit column list
**Change:** Add `group_name` to INSERT column list (optional, can be NULL)

**Current INSERT:**
```sql
INSERT INTO agent_jobs 
(job_type, team_name, pi, status, created_at, updated_at)
VALUES (:job_type, :team_name, NULL, 'Pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
RETURNING job_id, job_type, team_name, pi, status, created_at
```

**Updated INSERT:**
```sql
INSERT INTO agent_jobs 
(job_type, team_name, group_name, pi, status, created_at, updated_at)
VALUES (:job_type, :team_name, :group_name, NULL, 'Pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
RETURNING job_id, job_type, team_name, group_name, pi, status, created_at
```

**Request Model Change:**
- Update `TeamJobCreateRequest` to include optional `group_name: Optional[str] = None`
- Pass `group_name` in INSERT parameters (can be None)

#### 4.2. `POST /api/v1/agent-jobs/create-pi-job` - Create PI job
**Location:** `agent_jobs_service.py` line ~382

**Current:** INSERT with team_name = NULL
**Change:** ✅ No change needed - PI jobs don't use team_name or group_name

#### 4.3. `POST /api/v1/agent-jobs/create-pi-job-for-team` - Create PI job for team
**Location:** `agent_jobs_service.py` line ~446

**Current:** INSERT with explicit column list
**Change:** Add `group_name` to INSERT column list (optional, can be NULL)

**Current INSERT:**
```sql
INSERT INTO agent_jobs 
(job_type, team_name, pi, status, created_at, updated_at)
VALUES (:job_type, :team_name, :pi, 'Pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
RETURNING job_id, job_type, team_name, pi, status, created_at
```

**Updated INSERT:**
```sql
INSERT INTO agent_jobs 
(job_type, team_name, group_name, pi, status, created_at, updated_at)
VALUES (:job_type, :team_name, :group_name, :pi, 'Pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
RETURNING job_id, job_type, team_name, group_name, pi, status, created_at
```

**Request Model Change:**
- Update `PIJobForTeamCreateRequest` to include optional `group_name: Optional[str] = None`
- Pass `group_name` in INSERT parameters (can be None)

---

### 5. Request Models

#### 5.1. `TeamJobCreateRequest`
**Location:** `agent_jobs_service.py` line ~23

**Current:**
```python
class TeamJobCreateRequest(BaseModel):
    job_type: str
    team_name: str
```

**Updated:**
```python
class TeamJobCreateRequest(BaseModel):
    job_type: str
    team_name: str
    group_name: Optional[str] = None
```

#### 5.2. `PIJobForTeamCreateRequest`
**Location:** `agent_jobs_service.py` line ~33

**Current:**
```python
class PIJobForTeamCreateRequest(BaseModel):
    job_type: str
    pi: str
    team_name: str
```

**Updated:**
```python
class PIJobForTeamCreateRequest(BaseModel):
    job_type: str
    pi: str
    team_name: str
    group_name: Optional[str] = None
```

#### 5.3. `AgentJobUpdateRequest`
**Location:** `agent_jobs_service.py` line ~569

**Change:** ✅ No change needed - update endpoint doesn't allow updating group_name (only specific fields)

---

## Summary of Changes

### Files to Modify:

1. **database_table_creation.py**
   - Update `create_agent_jobs_table_if_not_exists()` function
   - Add `group_name VARCHAR(255)` column to CREATE TABLE statement

2. **agent_jobs_service.py**
   - Update `GET /agent-jobs` - Add `group_name` to SELECT and response dictionary
   - Update `POST /agent-jobs/create-team-job` - Add `group_name` to INSERT and RETURNING
   - Update `POST /agent-jobs/create-pi-job-for-team` - Add `group_name` to INSERT and RETURNING
   - Update `POST /agent-jobs/{job_id}/cancel` - Add `group_name` to RETURNING
   - Update `PATCH /agent-jobs/{job_id}` - Add `group_name` to both RETURNING clauses
   - Update `TeamJobCreateRequest` model - Add `group_name: Optional[str] = None`
   - Update `PIJobForTeamCreateRequest` model - Add `group_name: Optional[str] = None`

### Endpoints That Will Automatically Return group_name:

✅ **GET endpoints (no code changes needed):**
- `POST /api/v1/agent-jobs/claim-next` - Uses `RETURNING *`
- `GET /api/v1/agent-jobs/{job_id}` - Uses `SELECT *`

### Endpoints That Need Code Changes:

🔧 **GET endpoint (needs SELECT update):**
- `GET /api/v1/agent-jobs` - Add `group_name` to SELECT list and response dict

🔧 **POST endpoints (need INSERT updates):**
- `POST /api/v1/agent-jobs/create-team-job` - Add `group_name` to INSERT and RETURNING
- `POST /api/v1/agent-jobs/create-pi-job-for-team` - Add `group_name` to INSERT and RETURNING

🔧 **UPDATE endpoints (need RETURNING updates):**
- `POST /api/v1/agent-jobs/{job_id}/cancel` - Add `group_name` to RETURNING
- `PATCH /api/v1/agent-jobs/{job_id}` - Add `group_name` to both RETURNING clauses

### Request Models That Need Updates:

🔧 **Create request models:**
- `TeamJobCreateRequest` - Add `group_name: Optional[str] = None`
- `PIJobForTeamCreateRequest` - Add `group_name: Optional[str] = None`

---

## Testing Considerations

1. **Backward Compatibility:**
   - Existing records will have `group_name = NULL` (acceptable)
   - GET endpoints will return `group_name: null` for existing records
   - No breaking changes to API responses

2. **New Records:**
   - Can create jobs with `group_name` set
   - Can create jobs without `group_name` (nullable)
   - Existing create endpoints continue to work without `group_name`

3. **Response Format:**
   - All GET endpoints will include `group_name` in response (may be `null`)
   - POST endpoints can accept `group_name` in request body (optional)

---

## Migration Notes

- **For existing deployments:** Column will need to be added manually via ALTER TABLE
- **For new deployments:** Column will be created automatically via table creation script
- **No data migration needed:** Existing records will have `group_name = NULL`

---

## Estimated Impact

- **Low Risk:** Column is nullable, no breaking changes
- **Minimal Code Changes:** Only 2 files need modification
- **Backward Compatible:** Existing functionality unchanged
- **Index Updates:** No new indexes needed initially

---

## ALTER TABLE Command (for manual execution)

```sql
ALTER TABLE public.agent_jobs 
ADD COLUMN group_name VARCHAR(255);
```

