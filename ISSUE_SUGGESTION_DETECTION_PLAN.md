# Issue Suggestion/Recommendation Detection - Implementation Plan

## Overview
Detect follow-up questions that ask for suggestions/recommendations about issues/PBIs/work items/epics, and if the chat history contains an `issue_key`, automatically fetch issue details from JIRA and add them to the LLM context.

## Requirements

### Detection Conditions (ALL must be met):
1. **Follow-up question** (not initial question)
2. **Entity keyword**: Question contains one of: "issue", "PBI", "work item", "epic" (case-insensitive)
3. **Action keyword**: Question contains one of: "suggest", "recommend", "recommendation", "suggestion", "advise", "propose" (case-insensitive)
4. **Issue key exists**: `chat_history.issue_key` column is not NULL

### Actions When Detected:
1. Read `issue_key` from `chat_history` table
2. Query JIRA issues table to get issue details
3. Format issue data
4. Add to `conversation_context` sent to LLM

## Implementation Plan

### Phase 1: Create Detection Function
**Location**: `ai_chat_service.py` - Add helper function

**Function**:
```python
def detect_issue_suggestion_request(question: str) -> bool:
    """
    Detect if follow-up question is asking for suggestions/recommendations about an issue/PBI/work item/epic.
    
    Args:
        question: User's question
        
    Returns:
        True if detected, False otherwise
    """
    if not question:
        return False
    
    question_lower = question.lower()
    
    # Check for entity keywords
    entity_keywords = ['issue', 'pbi', 'work item', 'epic']
    has_entity_keyword = any(keyword in question_lower for keyword in entity_keywords)
    
    # Check for action keywords
    action_keywords = ['suggest', 'recommend', 'recommendation', 'suggestion', 'advise', 'propose']
    has_action_keyword = any(keyword in question_lower for keyword in action_keywords)
    
    # Both must be present
    return has_entity_keyword and has_action_keyword
```

### Phase 2: Create Issue Data Fetching Function
**Location**: `ai_chat_service.py` - Add helper function

**Function**:
```python
def fetch_issue_details_for_suggestion(issue_key: str, conn: Connection) -> Optional[Dict[str, Any]]:
    """
    Fetch issue details from JIRA issues table.
    
    Args:
        issue_key: JIRA issue key (e.g., 'IDPSCAN-18951')
        conn: Database connection
        
    Returns:
        Dictionary with issue data, or None if not found
    """
    try:
        # Query issue by issue_key
        query = text(f"""
            SELECT 
                issue_key,
                issue_type,
                summary,
                description,
                status_category,
                flagged,
                dependency,
                parent_key,
                team_name,
                quarter_pi,
                assignee,
                reporter,
                created_date,
                updated_date,
                resolution_date,
                priority,
                labels,
                components
            FROM {config.WORK_ITEMS_TABLE}
            WHERE issue_key = :issue_key
            LIMIT 1
        """)
        
        logger.info(f"SQL Query: Fetching issue details for {issue_key}")
        result = conn.execute(query, {"issue_key": issue_key})
        row = result.fetchone()
        
        if not row:
            logger.warning(f"Issue {issue_key} not found")
            return None
        
        # Convert to dictionary (handle case where some columns might not exist)
        issue_data = {}
        columns = ['issue_key', 'issue_type', 'summary', 'description', 'status_category', 
                   'flagged', 'dependency', 'parent_key', 'team_name', 'quarter_pi', 
                   'assignee', 'reporter', 'created_date', 'updated_date', 'resolution_date',
                   'priority', 'labels', 'components']
        
        for i, col in enumerate(columns):
            if i < len(row):
                issue_data[col] = row[i]
            else:
                issue_data[col] = None
        
        logger.info(f"Found issue {issue_key}: {issue_data.get('summary', 'N/A')}")
        return issue_data
        
    except Exception as e:
        logger.error(f"Error fetching issue details for {issue_key}: {e}")
        return None
```

**Note**: The field list above is a placeholder. **You need to provide the exact list of fields you want to read from the JIRA issues table.**

### Phase 3: Format Issue Data for LLM
**Location**: `ai_chat_service.py` - Add helper function

**Function**:
```python
def format_issue_details_for_llm(issue_data: Dict[str, Any]) -> str:
    """
    Format issue data for LLM context.
    
    Args:
        issue_data: Dictionary from fetch_issue_details_for_suggestion()
        
    Returns:
        Formatted string to add to conversation_context
    """
    formatted = """
=== ISSUE DETAILS FOR SUGGESTION ===
"""
    
    # Format each field (skip None values)
    if issue_data.get('issue_key'):
        formatted += f"Issue Key: {issue_data['issue_key']}\n"
    if issue_data.get('issue_type'):
        formatted += f"Issue Type: {issue_data['issue_type']}\n"
    if issue_data.get('summary'):
        formatted += f"Summary: {issue_data['summary']}\n"
    if issue_data.get('description'):
        formatted += f"Description: {issue_data['description']}\n"
    if issue_data.get('status_category'):
        formatted += f"Status: {issue_data['status_category']}\n"
    if issue_data.get('team_name'):
        formatted += f"Team: {issue_data['team_name']}\n"
    if issue_data.get('quarter_pi'):
        formatted += f"PI: {issue_data['quarter_pi']}\n"
    if issue_data.get('assignee'):
        formatted += f"Assignee: {issue_data['assignee']}\n"
    if issue_data.get('priority'):
        formatted += f"Priority: {issue_data['priority']}\n"
    # Add other fields as needed
    
    formatted += "\n=== END ISSUE DETAILS ===\n"
    
    return formatted
```

### Phase 4: Main Handler Function
**Location**: `ai_chat_service.py` - Add main function

**Function**:
```python
def handle_issue_suggestion_request(
    question: str,
    conversation_id: str,
    conn: Connection
) -> Optional[str]:
    """
    Handle issue suggestion request if detected in follow-up question.
    
    Args:
        question: User's follow-up question
        conversation_id: Conversation ID
        conn: Database connection
        
    Returns:
        Formatted issue details string if detected and issue_key found, None otherwise
    """
    # 1. Detect if this is an issue suggestion request
    if not detect_issue_suggestion_request(question):
        return None
    
    logger.info("Issue suggestion request detected in follow-up question")
    
    # 2. Get issue_key from chat_history
    query = text(f"""
        SELECT issue_key
        FROM {config.CHAT_HISTORY_TABLE}
        WHERE id = :conversation_id
    """)
    
    result = conn.execute(query, {"conversation_id": int(conversation_id)})
    row = result.fetchone()
    
    if not row or not row[0]:
        logger.info("No issue_key found in chat_history, skipping issue details fetch")
        return None
    
    issue_key = row[0]
    logger.info(f"Found issue_key in chat_history: {issue_key}")
    
    # 3. Fetch issue details
    issue_data = fetch_issue_details_for_suggestion(issue_key, conn)
    if not issue_data:
        logger.warning(f"Could not fetch issue details for {issue_key}")
        return None
    
    # 4. Format issue data
    formatted_issue_details = format_issue_details_for_llm(issue_data)
    logger.info(f"Issue details formatted for LLM (length: {len(formatted_issue_details)} chars)")
    
    return formatted_issue_details
```

### Phase 5: Integration into AI Chat Endpoint
**Location**: `ai_chat_service.py` - In `/ai-chat` endpoint, after determining if it's a follow-up

**Integration Point**: After line ~1625 (after `is_initial_call` is determined), before building conversation_context

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

## Fields to Read from JIRA Issues Table

**PLEASE PROVIDE THE EXACT LIST OF FIELDS YOU WANT TO READ**

Common fields available (based on codebase):
- `issue_key`
- `issue_type`
- `summary`
- `description`
- `status_category`
- `flagged`
- `dependency`
- `parent_key`
- `team_name`
- `quarter_pi`
- `assignee`
- `reporter`
- `created_date`
- `updated_date`
- `resolution_date`
- `priority`
- `labels`
- `components`

**Which fields do you want included?**

## Detection Examples

**Will trigger:**
- "Can you suggest improvements for this issue?"
- "What do you recommend for this PBI?"
- "Any suggestions for this work item?"
- "Recommend actions for this epic"

**Will NOT trigger:**
- "What is the status?" (no action keyword)
- "Tell me about issues" (no action keyword)
- "Suggest improvements" (no entity keyword)
- Initial question (must be follow-up)

## Error Handling
1. If issue_key not found in chat_history: Log info, return None (don't add context)
2. If issue not found in database: Log warning, return None (don't add context)
3. If database error: Log error, return None (don't block chat)
4. If detection fails: Continue normally (no special handling)

## Files to Modify
1. `ai_chat_service.py`
   - Add `detect_issue_suggestion_request()` function
   - Add `fetch_issue_details_for_suggestion()` function
   - Add `format_issue_details_for_llm()` function
   - Add `handle_issue_suggestion_request()` main function
   - Integrate into `/ai-chat` endpoint

## Implementation Order
1. ✅ Create detection function
2. ✅ Create issue fetching function (waiting for field list)
3. ✅ Create formatting function (waiting for field list)
4. ✅ Create main handler function
5. ✅ Integrate into `/ai-chat` endpoint
6. ✅ Add error handling and logging

## Notes
- Only processes follow-up questions (not initial)
- Requires both entity keyword AND action keyword
- Requires issue_key to exist in chat_history
- Adds issue details to conversation_context (not replaces)
- Non-breaking: if detection fails or issue not found, chat continues normally

