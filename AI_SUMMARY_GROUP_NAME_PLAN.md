# AI Summary Table - Add group_name Column - Implementation Plan

## Overview
Add a new `group_name VARCHAR(255)` column to the `ai_summary` table to support group-level AI cards, similar to how `team_name` is currently used.

## Database Changes

### 1. Table Schema Update
**File:** `database_table_creation.py`

**Current Table Structure:**
```sql
CREATE TABLE public.ai_summary (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    team_name VARCHAR(255) NOT NULL,
    card_name VARCHAR(255) NOT NULL,
    card_type VARCHAR(100) NOT NULL,
    priority VARCHAR(50) NOT NULL,
    source VARCHAR(255),
    source_job_id INTEGER,
    description TEXT NOT NULL,
    full_information TEXT,
    information_json TEXT,
    pi VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (date, team_name, card_name, pi)
);
```

**Updated Table Structure (for new deployments):**
```sql
CREATE TABLE public.ai_summary (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    team_name VARCHAR(255) NOT NULL,
    group_name VARCHAR(255),  -- NEW COLUMN (nullable)
    card_name VARCHAR(255) NOT NULL,
    card_type VARCHAR(100) NOT NULL,
    priority VARCHAR(50) NOT NULL,
    source VARCHAR(255),
    source_job_id INTEGER,
    description TEXT NOT NULL,
    full_information TEXT,
    information_json TEXT,
    pi VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (date, team_name, card_name, pi)
);
```

**Note:** Column is nullable to support existing records. No index needed initially (can be added later if needed for filtering).

---

## Code Changes

### 2. Database Functions (database_general.py)

#### 2.1. `create_ai_card()` - INSERT function
**Location:** `database_general.py` line ~756

**Changes:**
- Add `"group_name"` to `allowed_columns` set (line ~763-766)
- Function will automatically include `group_name` in INSERT if provided

**Current:**
```python
allowed_columns = {
    "date", "team_name", "card_name", "card_type", "priority", "source",
    "source_job_id", "description", "full_information", "information_json", "pi"
}
```

**Updated:**
```python
allowed_columns = {
    "date", "team_name", "group_name", "card_name", "card_type", "priority", "source",
    "source_job_id", "description", "full_information", "information_json", "pi"
}
```

#### 2.2. `update_ai_card_by_id()` - UPDATE function
**Location:** `database_general.py` line ~798

**Changes:**
- Add `"group_name"` to `allowed_columns` set (line ~803-805)
- Function will automatically allow updating `group_name` if provided

**Current:**
```python
allowed_columns = {
    "date", "team_name", "card_name", "card_type", "priority", "source",
    "source_job_id", "description", "full_information", "information_json", "pi"
}
```

**Updated:**
```python
allowed_columns = {
    "date", "team_name", "group_name", "card_name", "card_type", "priority", "source",
    "source_job_id", "description", "full_information", "information_json", "pi"
}
```

#### 2.3. `get_top_ai_cards_filtered()` - SELECT function
**Location:** `database_general.py` line ~150

**Changes:**
- No SQL changes needed - `SELECT *` will automatically include `group_name`
- Returned dictionaries will include `group_name` field (will be `None` for existing records)

#### 2.4. `get_top_ai_cards_with_recommendations_filtered()` - SELECT function
**Location:** `database_general.py` line ~341

**Changes:**
- No changes needed - calls `get_top_ai_cards_filtered()` which will return `group_name`

#### 2.5. `get_team_ai_card_by_id()` - SELECT by ID
**Location:** `database_general.py` line ~397

**Changes:**
- No SQL changes needed - `SELECT *` will automatically include `group_name`
- Returned dictionary will include `group_name` field

#### 2.6. `get_pi_ai_card_by_id()` - SELECT by ID
**Location:** `database_general.py` line ~539

**Changes:**
- No SQL changes needed - `SELECT *` will automatically include `group_name`
- Returned dictionary will include `group_name` field

#### 2.7. `get_formatted_job_data_for_llm_followup_insight()` - SELECT for LLM context
**Location:** `database_general.py` line ~626

**Changes:**
- No changes needed - function formats specific fields, `group_name` not included in formatted output (can be added later if needed)

---

### 3. API Endpoints

#### 3.1. Team AI Cards Endpoints (team_ai_cards_service.py)

**Endpoint 1:** `GET /api/v1/team-ai-cards/getTopCards`
- **Location:** Line ~58
- **Current:** Returns `ai_cards` from `get_top_ai_cards()`
- **Change:** No code change needed - `group_name` will be included automatically in response
- **Response:** Each card in `ai_cards` array will now include `group_name` field

**Endpoint 2:** `GET /api/v1/team-ai-cards/getTopCardsWithRecommendations`
- **Location:** Line ~109
- **Current:** Returns `ai_cards` from `get_top_ai_cards_with_recommendations_filtered()`
- **Change:** No code change needed - `group_name` will be included automatically in response
- **Response:** Each card in `ai_cards` array will now include `group_name` field

**Endpoint 3:** `GET /api/v1/team-ai-cards/{id}`
- **Location:** Line ~268
- **Current:** Returns single card from `get_team_ai_card_by_id()`
- **Change:** No code change needed - `group_name` will be included automatically in response
- **Response:** Card object will now include `group_name` field

**Endpoint 4:** `GET /api/v1/team-ai-cards/collection`
- **Location:** Line ~201
- **Current:** Returns latest 100 cards from `ai_summary` table
- **Change:** No SQL change needed - `SELECT *` will include `group_name`
- **Response:** Each card in array will now include `group_name` field

**Endpoint 5:** `POST /api/v1/team-ai-cards`
- **Location:** Line ~339
- **Current:** Creates card via `create_ai_card()`
- **Change:** Update `TeamAICardCreateRequest` model to include optional `group_name` field
- **Action:** Add `group_name: Optional[str] = None` to request model

**Endpoint 6:** `PUT /api/v1/team-ai-cards/{id}`
- **Location:** Line ~364
- **Current:** Updates card via `update_ai_card_by_id()`
- **Change:** Update `TeamAICardUpdateRequest` model to include optional `group_name` field
- **Action:** Add `group_name: Optional[str] = None` to request model

#### 3.2. PI AI Cards Endpoints (pi_ai_cards_service.py)

**Endpoint 1:** `GET /api/v1/pi-ai-cards/getTopCards`
- **Location:** Line ~62
- **Current:** Returns `ai_cards` from `get_top_ai_cards_filtered()`
- **Change:** No code change needed - `group_name` will be included automatically in response
- **Response:** Each card in `ai_cards` array will now include `group_name` field

**Endpoint 2:** `GET /api/v1/pi-ai-cards/getTopCardsWithRecommendations`
- **Location:** Line ~113
- **Current:** Returns `ai_cards` from `get_top_ai_cards_with_recommendations_filtered()`
- **Change:** No code change needed - `group_name` will be included automatically in response
- **Response:** Each card in `ai_cards` array will now include `group_name` field

**Endpoint 3:** `GET /api/v1/pi-ai-cards/{id}`
- **Location:** Line ~278
- **Current:** Returns single card from `get_pi_ai_card_by_id()`
- **Change:** No code change needed - `group_name` will be included automatically in response
- **Response:** Card object will now include `group_name` field

**Endpoint 4:** `GET /api/v1/pi-ai-cards/collection`
- **Location:** Line ~203
- **Current:** Returns latest 100 cards from `ai_summary` table
- **Change:** No SQL change needed - `SELECT *` will include `group_name`
- **Response:** Each card in array will now include `group_name` field

**Endpoint 5:** `POST /api/v1/pi-ai-cards`
- **Location:** Line ~347
- **Current:** Creates card via `create_ai_card()`
- **Change:** Update `PIAICardCreateRequest` model to include optional `group_name` field
- **Action:** Add `group_name: Optional[str] = None` to request model

**Endpoint 6:** `PUT /api/v1/pi-ai-cards/{id}`
- **Location:** Line ~376
- **Current:** Updates card via `update_ai_card_by_id()`
- **Change:** Update `PIAICardUpdateRequest` model to include optional `group_name` field
- **Action:** Add `group_name: Optional[str] = None` to request model

---

## Summary of Changes

### Files to Modify:

1. **database_table_creation.py**
   - Update `create_ai_summary_table_if_not_exists()` function
   - Add `group_name VARCHAR(255)` column to CREATE TABLE statement

2. **database_general.py**
   - Update `create_ai_card()` - add `"group_name"` to `allowed_columns`
   - Update `update_ai_card_by_id()` - add `"group_name"` to `allowed_columns`
   - No changes needed to SELECT functions (they use `SELECT *`)

3. **team_ai_cards_service.py**
   - Update `TeamAICardCreateRequest` model - add `group_name: Optional[str] = None`
   - Update `TeamAICardUpdateRequest` model - add `group_name: Optional[str] = None`
   - No changes needed to GET endpoints (automatic inclusion)

4. **pi_ai_cards_service.py**
   - Update `PIAICardCreateRequest` model - add `group_name: Optional[str] = None`
   - Update `PIAICardUpdateRequest` model - add `group_name: Optional[str] = None`
   - No changes needed to GET endpoints (automatic inclusion)

### Endpoints That Will Automatically Return group_name:

✅ **GET endpoints (no code changes needed):**
- `GET /api/v1/team-ai-cards/getTopCards`
- `GET /api/v1/team-ai-cards/getTopCardsWithRecommendations`
- `GET /api/v1/team-ai-cards/{id}`
- `GET /api/v1/team-ai-cards/collection`
- `GET /api/v1/pi-ai-cards/getTopCards`
- `GET /api/v1/pi-ai-cards/getTopCardsWithRecommendations`
- `GET /api/v1/pi-ai-cards/{id}`
- `GET /api/v1/pi-ai-cards/collection`

### Endpoints That Need Model Updates:

🔧 **POST/PUT endpoints (need request model updates):**
- `POST /api/v1/team-ai-cards` - Add `group_name` to `TeamAICardCreateRequest`
- `PUT /api/v1/team-ai-cards/{id}` - Add `group_name` to `TeamAICardUpdateRequest`
- `POST /api/v1/pi-ai-cards` - Add `group_name` to `PIAICardCreateRequest`
- `PUT /api/v1/pi-ai-cards/{id}` - Add `group_name` to `PIAICardUpdateRequest`

---

## Testing Considerations

1. **Backward Compatibility:**
   - Existing records will have `group_name = NULL` (acceptable)
   - GET endpoints will return `group_name: null` for existing records
   - No breaking changes to API responses

2. **New Records:**
   - Can create cards with `group_name` set
   - Can create cards without `group_name` (nullable)
   - Can update existing cards to add/change `group_name`

3. **Response Format:**
   - All GET endpoints will include `group_name` in response (may be `null`)
   - POST/PUT endpoints can accept `group_name` in request body

---

## Migration Notes

- **For existing deployments:** Column will need to be added manually via ALTER TABLE
- **For new deployments:** Column will be created automatically via table creation script
- **No data migration needed:** Existing records will have `group_name = NULL`

---

## Estimated Impact

- **Low Risk:** Column is nullable, no breaking changes
- **Minimal Code Changes:** Only 4 files need modification
- **Automatic Inclusion:** Most endpoints automatically return new field
- **Backward Compatible:** Existing functionality unchanged

