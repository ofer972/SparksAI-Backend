# Unified Issue Details Fetching and Formatting - Implementation Plan

## Overview
Create unified functions to fetch and format issue details that work for both epic refinement and issue suggestion. If the issue is an Epic, automatically fetch children and use Epic Refinement template. If not Epic, use no template. Support issue key extraction from question (takes precedence over chat_history).

## Requirements

### Field List (Fixed - No Parameters Needed):
- `issue_key`
- `issue_type`
- `summary`
- `description`
- `status`
- `status_category`
- `resolution`
- `created_at`
- `updated_at`
- `resolved_at`
- `team_name`
- `flagged`
- `first_date_in_progress`
- `cycle_time_days`
- `dependency`
- `number_of_children`
- `number_of_completed_children`

### Behavior:
- **Always fetch**: All fields from the fixed list above
- **If Epic**: Also fetch all children AND use "Epic Refinement" template
- **If NOT Epic**: No template, just pass original question to LLM
- **Issue key priority**: If question contains issue key → use it (overrides chat_history)
- **Single function**: Same function used for epic refinement and issue suggestion

## Detection Requirements

### For Issue Suggestion (Follow-up Questions):
**ALL conditions must be met:**
1. **Follow-up question** (not initial question)
2. **Entity keyword**: One of: "issue", "PBI", "work item", "epic" (case-insensitive)
3. **Action keyword**: One of: "suggest", "recommend", "recommendation", "suggestion", "advise", "propose" (case-insensitive)
4. **Reference keyword**: One of: "this", "that" (case-insensitive) - **NEW REQUIREMENT**
5. **Issue key exists**: Either in question OR in `chat_history.issue_key` column

### Issue Key Priority:
- **First priority**: Extract issue key from question (if present)
- **Second priority**: Use `issue_key` from `chat_history` table
- If neither exists → skip issue details fetch

## Implementation Plan

### Phase 1: Create Unified Issue Fetching Function
**Location**: `ai_chat_service.py` - Replace/refactor `fetch_epic_refinement_data()`

**New Function**:
```python
def fetch_issue_details(
    issue_key: str, 
    conn: Connection
) -> Optional[Dict[str, Any]]:
    """
    Fetch issue details from JIRA issues table.
    If issue is an Epic, also fetches all children.
    
    Uses fixed field list - no parameters needed.
    
    Args:
        issue_key: JIRA issue key (e.g., 'PROJ-123')
        conn: Database connection
        
    Returns:
        Dictionary with issue data and children (if Epic), or None if issue not found
        Structure:
        {
            "issue": {
                "issue_key": "...",
                "issue_type": "...",
                "summary": "...",
                ... (all fields from fixed list)
            },
            "children": [  # Only if issue_type is Epic
                {"issue_key": "...", "summary": "..."},
                ...
            ],
            "children_count": 0  # Only if issue_type is Epic
        }
    """
    try:
        # 1. Get issue data with fixed field list
        issue_query = text(f"""
            SELECT 
                issue_key,
                issue_type,
                summary,
                description,
                status,
                status_category,
                resolution,
                created_at,
                updated_at,
                resolved_at,
                team_name,
                flagged,
                first_date_in_progress,
                cycle_time_days,
                dependency,
                number_of_children,
                number_of_completed_children
            FROM {config.WORK_ITEMS_TABLE}
            WHERE issue_key = :issue_key
            LIMIT 1
        """)
        
        logger.info(f"SQL Query: Fetching issue {issue_key}")
        issue_result = conn.execute(issue_query, {"issue_key": issue_key})
        issue_row = issue_result.fetchone()
        
        if not issue_row:
            logger.warning(f"Issue {issue_key} not found")
            return None
        
        # Map row to dictionary with fixed field list
        issue_data = {
            "issue_key": issue_row[0],
            "issue_type": issue_row[1],
            "summary": issue_row[2] or "",
            "description": issue_row[3] or "",
            "status": issue_row[4] or "",
            "status_category": issue_row[5] or "",
            "resolution": issue_row[6] or "",
            "created_at": issue_row[7],
            "updated_at": issue_row[8],
            "resolved_at": issue_row[9],
            "team_name": issue_row[10] or "",
            "flagged": issue_row[11] if issue_row[11] is not None else False,
            "first_date_in_progress": issue_row[12],
            "cycle_time_days": issue_row[13] if issue_row[13] is not None else 0.0,
            "dependency": issue_row[14] if issue_row[14] is not None else False,
            "number_of_children": issue_row[15] if issue_row[15] is not None else 0,
            "number_of_completed_children": issue_row[16] if issue_row[16] is not None else 0
        }
        
        result = {
            "issue": issue_data,
            "children": [],
            "children_count": 0
        }
        
        # 2. If Epic, get all children
        if issue_data.get("issue_type") == "Epic":
            children_query = text(f"""
                SELECT 
                    issue_key,
                    summary
                FROM {config.WORK_ITEMS_TABLE}
                WHERE parent_key = :issue_key
                ORDER BY issue_key
            """)
            
            logger.info(f"SQL Query: Fetching children of epic {issue_key}")
            children_result = conn.execute(children_query, {"issue_key": issue_key})
            children_rows = children_result.fetchall()
            
            children = [
                {
                    "issue_key": row[0],
                    "summary": row[1] or ""
                }
                for row in children_rows
            ]
            
            result["children"] = children
            result["children_count"] = len(children)
            logger.info(f"Found {len(children)} children for epic {issue_key}")
        
        return result
        
    except Exception as e:
        logger.error(f"Error fetching issue details for {issue_key}: {e}")
        return None
```

### Phase 2: Create Unified Formatting Function
**Location**: `ai_chat_service.py` - Replace/refactor `format_epic_refinement_context()`

**New Function**:
```python
def format_issue_details_for_llm(
    issue_data: Dict[str, Any],
    template_text: Optional[str] = None,
    include_children: bool = True
) -> str:
    """
    Format issue details for LLM context.
    Works for both epic refinement and issue suggestion.
    
    Args:
        issue_data: Dictionary from fetch_issue_details()
        template_text: Optional template text (for epic refinement only)
        include_children: Whether to include children section (default: True)
        
    Returns:
        Formatted string to send as conversation_context
    """
    issue = issue_data.get("issue", {})
    children = issue_data.get("children", [])
    children_count = issue_data.get("children_count", 0)
    
    # Build issue information section
    issue_section = f"""
=== ISSUE INFORMATION ===
Issue Key: {issue.get('issue_key', 'N/A')}
Issue Type: {issue.get('issue_type', 'N/A')}
Summary: {issue.get('summary', 'N/A')}
Description: {issue.get('description', 'N/A')}
Status: {issue.get('status', 'N/A')}
Status Category: {issue.get('status_category', 'N/A')}
Resolution: {issue.get('resolution', 'N/A')}
Created At: {issue.get('created_at', 'N/A')}
Updated At: {issue.get('updated_at', 'N/A')}
Resolved At: {issue.get('resolved_at', 'N/A')}
Team Name: {issue.get('team_name', 'N/A')}
Flagged: {issue.get('flagged', False)}
First Date In Progress: {issue.get('first_date_in_progress', 'N/A')}
Cycle Time Days: {issue.get('cycle_time_days', 0.0)}
Dependency: {issue.get('dependency', False)}
Number of Children: {issue.get('number_of_children', 0)}
Number of Completed Children: {issue.get('number_of_completed_children', 0)}
"""
    
    # Add children section if Epic and include_children is True
    if include_children and children_count > 0:
        issue_section += f"""
=== ISSUE CHILDREN ({children_count} total) ===
"""
        for i, child in enumerate(children, 1):
            issue_section += f"""
{i}. {child.get('issue_key', 'N/A')}: {child.get('summary', 'N/A')}
"""
        issue_section += "\n=== END ISSUE CHILDREN ===\n"
    
    issue_section += "\n=== END ISSUE INFORMATION ===\n"
    
    # Combine template (if provided) with issue data
    if template_text:
        return f"{template_text}\n\n{issue_section}"
    else:
        return issue_section
```

### Phase 3: Create Detection Function with Issue Key Extraction
**Location**: `ai_chat_service.py` - Add new function

**Function**:
```python
def detect_issue_suggestion_request(question: str) -> Tuple[bool, Optional[str]]:
    """
    Detect if follow-up question is asking for suggestions/recommendations about an issue/PBI/work item/epic.
    Also extracts issue key from question if present (takes precedence over chat_history).
    
    Args:
        question: User's question
        
    Returns:
        Tuple of (is_detected: bool, issue_key_from_question: Optional[str])
        - is_detected: True if all keywords detected
        - issue_key_from_question: Issue key if found in question, None otherwise
    """
    if not question:
        return False, None
    
    question_lower = question.lower()
    
    # Check for entity keywords
    entity_keywords = ['issue', 'pbi', 'work item', 'epic']
    has_entity_keyword = any(keyword in question_lower for keyword in entity_keywords)
    
    # Check for action keywords
    action_keywords = ['suggest', 'recommend', 'recommendation', 'suggestion', 'advise', 'propose']
    has_action_keyword = any(keyword in question_lower for keyword in action_keywords)
    
    # Check for reference keywords (NEW REQUIREMENT)
    reference_keywords = ['this', 'that']
    has_reference_keyword = any(keyword in question_lower for keyword in reference_keywords)
    
    # All three must be present
    is_detected = has_entity_keyword and has_action_keyword and has_reference_keyword
    
    # Extract issue key from question if present (takes precedence)
    issue_key_from_question = extract_issue_key_from_response(question)
    
    return is_detected, issue_key_from_question
```

### Phase 4: Refactor Epic Refinement to Use Unified Functions
**Location**: `ai_chat_service.py` - Update `handle_epic_refinement_request()`

**Changes**:
- Replace `fetch_epic_refinement_data()` call with `fetch_issue_details()`
- Replace `format_epic_refinement_context()` call with `format_issue_details_for_llm()`
- Keep template retrieval logic (Epic Refinement template)
- Keep detection logic

**Updated Code**:
```python
def handle_epic_refinement_request(
    question: str,
    conn: Connection
) -> Optional[str]:
    """
    Handle epic refinement request if detected in question.
    Uses unified fetch_issue_details() and format_issue_details_for_llm().
    Always uses Epic Refinement template.
    """
    # 1. Detect if this is an epic refinement request
    epic_key = detect_epic_refinement_request(question)
    if not epic_key:
        return None
    
    logger.info(f"Epic refinement detected for epic: {epic_key}")
    
    # 2. Fetch issue data (unified function - will fetch children if Epic)
    issue_data = fetch_issue_details(epic_key, conn)
    if not issue_data:
        raise HTTPException(
            status_code=404,
            detail=f"Epic {epic_key} not found"
        )
    
    # Verify it's an Epic
    if issue_data.get("issue", {}).get("issue_type") != "Epic":
        raise HTTPException(
            status_code=400,
            detail=f"Issue {epic_key} is not an Epic type"
        )
    
    # 3. Get Epic Refinement template (required - no fallback)
    logger.info("Fetching template 'Epic Refinement' for admin")
    refinement_template = get_prompt_by_email_and_name(
        email_address='admin',
        prompt_name='Epic Refinement',
        conn=conn,
        active=True,
        replace_placeholders=True
    )
    
    if not refinement_template or not refinement_template.get('prompt_description'):
        raise HTTPException(
            status_code=404,
            detail="Template 'Epic Refinement' not found for admin or is not active"
        )
    
    template_text = str(refinement_template['prompt_description'])
    logger.info(f"Template retrieved (length: {len(template_text)} chars)")
    
    # 4. Format context (unified function with Epic Refinement template)
    conversation_context = format_issue_details_for_llm(
        issue_data,
        template_text=template_text,
        include_children=True
    )
    logger.info(f"Epic refinement context prepared (length: {len(conversation_context)} chars)")
    
    return conversation_context
```

### Phase 5: Create Issue Suggestion Handler
**Location**: `ai_chat_service.py` - Add new function

**Function**:
```python
def handle_issue_suggestion_request(
    question: str,
    conversation_id: str,
    conn: Connection
) -> Optional[str]:
    """
    Handle issue suggestion request if detected in follow-up question.
    Uses unified fetch_issue_details() and format_issue_details_for_llm().
    
    Issue key priority:
    1. From question (if present) - takes precedence
    2. From chat_history.issue_key column
    
    Template usage:
    - If Epic: Use Epic Refinement template
    - If NOT Epic: No template, just pass original question
    
    Args:
        question: User's follow-up question
        conversation_id: Conversation ID
        conn: Database connection
        
    Returns:
        Formatted issue details string if detected and issue_key found, None otherwise
    """
    # 1. Detect if this is an issue suggestion request and extract issue key from question
    is_detected, issue_key_from_question = detect_issue_suggestion_request(question)
    if not is_detected:
        return None
    
    logger.info("Issue suggestion request detected in follow-up question")
    
    # 2. Determine which issue_key to use (priority: question > chat_history)
    issue_key = None
    if issue_key_from_question:
        issue_key = issue_key_from_question
        logger.info(f"Using issue_key from question: {issue_key}")
    else:
        # Get issue_key from chat_history
        query = text(f"""
            SELECT issue_key
            FROM {config.CHAT_HISTORY_TABLE}
            WHERE id = :conversation_id
        """)
        
        result = conn.execute(query, {"conversation_id": int(conversation_id)})
        row = result.fetchone()
        
        if row and row[0]:
            issue_key = row[0]
            logger.info(f"Using issue_key from chat_history: {issue_key}")
        else:
            logger.info("No issue_key found in question or chat_history, skipping issue details fetch")
            return None
    
    # 3. Fetch issue details (unified function - will fetch children if Epic)
    issue_data = fetch_issue_details(issue_key, conn)
    if not issue_data:
        logger.warning(f"Could not fetch issue details for {issue_key}")
        return None
    
    issue_type = issue_data.get("issue", {}).get("issue_type", "")
    
    # 4. Determine template usage based on issue_type
    template_text = None
    if issue_type == "Epic":
        # Epic: Use Epic Refinement template
        logger.info("Issue is Epic - fetching Epic Refinement template")
        refinement_template = get_prompt_by_email_and_name(
            email_address='admin',
            prompt_name='Epic Refinement',
            conn=conn,
            active=True,
            replace_placeholders=True
        )
        
        if refinement_template and refinement_template.get('prompt_description'):
            template_text = str(refinement_template['prompt_description'])
            logger.info(f"Epic Refinement template retrieved (length: {len(template_text)} chars)")
        else:
            logger.warning("Epic Refinement template not found, proceeding without template")
    else:
        # NOT Epic: No template, just pass original question
        logger.info(f"Issue is {issue_type} - no template, will pass original question to LLM")
    
    # 5. Format issue data (unified function)
    formatted_issue_details = format_issue_details_for_llm(
        issue_data,
        template_text=template_text,
        include_children=True  # Include children if Epic
    )
    logger.info(f"Issue details formatted for LLM (length: {len(formatted_issue_details)} chars)")
    
    return formatted_issue_details
```

### Phase 6: Integration into AI Chat Endpoint
**Location**: `ai_chat_service.py` - In `/ai-chat` endpoint

**Integration Point**: After determining if it's a follow-up (after line ~1625), before building conversation_context

**Code to add**:
```python
# Check for issue suggestion request in follow-up questions
issue_details_context = None
if not is_initial_call:
    try:
        issue_details_context = handle_issue_suggestion_request(
            request.question,
            conversation_id,
            conn
        )
        if issue_details_context:
            logger.info("Using issue details context for suggestion request")
    except Exception as e:
        logger.error(f"Error handling issue suggestion request: {e}")
        # Continue without issue details - don't block chat

# Add issue details to conversation_context if available
if issue_details_context:
    if conversation_context:
        conversation_context = conversation_context + "\n\n" + issue_details_context
    else:
        conversation_context = issue_details_context
```

**Important Note for Non-Epic Issues**:
When issue is NOT Epic and no template is used, the `conversation_context` will contain only the issue details. The original question will still be sent to LLM via the `question` parameter, so the LLM will see both the question and the issue details.

## Summary of Changes

### Functions to Create/Refactor:
1. **`fetch_issue_details(issue_key, conn)`** - Unified function (replaces `fetch_epic_refinement_data()`)
   - Fetches fixed field list
   - Automatically fetches children if Epic
   
2. **`format_issue_details_for_llm(issue_data, template_text=None, include_children=True)`** - Unified function (replaces `format_epic_refinement_context()`)
   - Formats issue data
   - Optional template (for Epic only)
   - Optional children section

3. **`detect_issue_suggestion_request(question)`** - New detection function
   - Returns (is_detected, issue_key_from_question)
   - Checks for entity + action + reference keywords
   - Extracts issue key from question if present

4. **`handle_issue_suggestion_request(question, conversation_id, conn)`** - New handler function
   - Uses issue_key from question (priority) or chat_history
   - Uses Epic Refinement template if Epic
   - No template if NOT Epic

### Functions to Update:
1. **`handle_epic_refinement_request()`** - Refactor to use unified functions

### Functions to Remove:
1. **`fetch_epic_refinement_data()`** - Replaced by `fetch_issue_details()`
2. **`format_epic_refinement_context()`** - Replaced by `format_issue_details_for_llm()`

## Detection Examples

**Will trigger (with "this" or "that"):**
- "Can you suggest improvements for this issue?"
- "What do you recommend for that PBI?"
- "Any suggestions for this work item?"
- "Recommend actions for that epic"

**Will NOT trigger:**
- "What is the status?" (no action keyword, no reference)
- "Tell me about issues" (no action keyword, no reference)
- "Suggest improvements" (no entity keyword, no reference)
- "Can you suggest for issue PROJ-123?" (no "this"/"that")
- Initial question (must be follow-up)

## Issue Key Priority Examples

**Example 1**: Question has issue key
- Question: "Can you suggest improvements for this issue PROJ-456?"
- chat_history.issue_key: "PROJ-123"
- **Result**: Uses "PROJ-456" (from question, takes precedence)

**Example 2**: Question has no issue key
- Question: "Can you suggest improvements for this issue?"
- chat_history.issue_key: "PROJ-123"
- **Result**: Uses "PROJ-123" (from chat_history)

**Example 3**: Neither has issue key
- Question: "Can you suggest improvements for this issue?"
- chat_history.issue_key: NULL
- **Result**: Returns None (no issue details fetched)

## Template Usage

**Epic Issues:**
- Uses "Epic Refinement" template
- Format: `template + issue_details`

**Non-Epic Issues:**
- No template
- Format: `issue_details` only
- Original question still sent to LLM via `question` parameter

## Files to Modify
1. `ai_chat_service.py`
   - Create `fetch_issue_details()` (unified)
   - Create `format_issue_details_for_llm()` (unified)
   - Create `detect_issue_suggestion_request()` (returns tuple)
   - Create `handle_issue_suggestion_request()` (with template logic)
   - Refactor `handle_epic_refinement_request()` to use unified functions
   - Remove `fetch_epic_refinement_data()` and `format_epic_refinement_context()`
   - Integrate issue suggestion into `/ai-chat` endpoint

## Benefits
- ✅ No duplication - single set of functions
- ✅ Automatic children fetching for Epics
- ✅ Fixed field list (no parameters needed)
- ✅ Works for both epic refinement and issue suggestion
- ✅ Issue key from question takes precedence
- ✅ Template only for Epics
- ✅ Easier to maintain - one place to update field list
