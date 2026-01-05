"""
AI Chat Service - REST API endpoints for AI chat interactions.

This service provides endpoints for AI chat functionality that connects
to the LLM service for OpenAI/Gemini API calls.
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection
from sqlalchemy import text
from typing import Optional, Dict, Any, Tuple, List
from enum import Enum
import logging
import json
import httpx
import os
import re
from datetime import datetime, date
from database_connection import get_db_connection
from database_general import get_ai_card_by_id, get_recommendation_by_id, get_prompt_by_email_and_name, get_formatted_job_data_for_llm_followup_insight, get_formatted_job_data_for_llm_followup_recommendation
from database_team_metrics import (
    get_closed_sprints_data_db,
    get_sprint_burndown_data_db,
    get_issues_trend_data_db,
    get_sprints_with_total_issues_db
)
from database_pi import (
    fetch_pi_burndown_data,
    fetch_pi_predictability_data,
    fetch_scope_changes_data
)
import config
from sparksai_sql_client import call_sparksai_sql_execute

logger = logging.getLogger(__name__)

ai_chat_router = APIRouter()

# Maximum length for AI chat question
MAX_QUESTION_LENGTH = 1000


def convert_history_to_sql_format(history_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert chat history_json format to SQL service conversation history format.
    
    Args:
        history_json: Chat history in format {'messages': [{'role': str, 'content': str}, ...]}
        
    Returns:
        List of conversation exchanges in format [{'question': str, 'sql': str, 'answer': str}]
    """
    sql_history = []
    
    if not history_json or 'messages' not in history_json:
        return sql_history
    
    messages = history_json.get('messages', [])
    
    # Extract last few exchanges (user/assistant pairs) that contain SQL trigger queries
    i = 0
    while i < len(messages):
        # Check if message starts with trigger "!"
        if messages[i].get('role') == 'user' and messages[i].get('content', '').startswith(config.SQL_AI_TRIGGER):
            # Found a SQL question
            question = messages[i].get('content', '')
            
            # Look for assistant response
            if i + 1 < len(messages) and messages[i + 1].get('role') == 'assistant':
                answer = messages[i + 1].get('content', '')
                
                # Try to extract SQL from answer (look for code blocks or formatted_for_llm format)
                sql = None
                if '```sql' in answer:
                    sql_start = answer.find('```sql') + 6
                    sql_end = answer.find('```', sql_start)
                    if sql_end > sql_start:
                        sql = answer[sql_start:sql_end].strip()
                elif '```' in answer:
                    sql_start = answer.find('```') + 3
                    sql_end = answer.find('```', sql_start)
                    if sql_end > sql_start:
                        sql = answer[sql_start:sql_end].strip()
                # Also check for SQL in formatted_for_llm format (from SQL service response)
                elif 'SQL Query:' in answer:
                    sql_start = answer.find('SQL Query:') + 10
                    sql_end = answer.find('\n\n', sql_start)
                    if sql_end > sql_start:
                        sql = answer[sql_start:sql_end].strip()
                
                sql_history.append({
                    'question': question,
                    'sql': sql,
                    'answer': answer[:200] if answer else None  # Summarize to avoid token bloat
                })
            
            i += 2
        else:
            i += 1
    
    # Return last 3 exchanges to keep token count reasonable
    return sql_history[-3:] if len(sql_history) > 3 else sql_history


# System message constant for all AI chat interactions
SYSTEM_MESSAGE = "You are AI assistant specialized in Agile, Scrum, Scaled Agile. All your answers should be brief with no more than 3 paragraphs with concrete and specific information based on the content provided"


class ChatType(str, Enum):
    """Enumeration of chat type options"""
    PI_DASHBOARD = "PI_dashboard"
    TEAM_DASHBOARD = "Team_dashboard"
    DIRECT_CHAT = "Direct_chat"
    TEAM_INSIGHTS = "Team_insights"
    PI_INSIGHTS = "PI_insights"
    RECOMMENDATION_REASON = "Recommendation_reason"


class AIChatRequest(BaseModel):
    """Request model for AI chat endpoint"""
    conversation_id: Optional[str] = Field(None, description="Conversation ID for tracking chat sessions")
    question: Optional[str] = Field(None, description="The user's question")
    user_id: Optional[str] = Field(None, description="User ID who requested the chat")
    prompt_name: Optional[str] = Field(None, description="Prompt name")
    selected_team: Optional[str] = Field(None, description="Selected team name")
    selected_pi: Optional[str] = Field(None, description="Selected PI name")
    chat_type: Optional[ChatType] = Field(None, description="Type of chat")
    recommendation_id: Optional[str] = Field(None, description="ID of recommendation")
    insights_id: Optional[str] = Field(None, description="ID of insights")
    dashboard_data: Optional[Dict[str, Any]] = Field(None, description="Dashboard layout and filters")

    class Config:
        use_enum_values = True


def get_or_create_chat_history(
    conversation_id: Optional[str],
    user_id: Optional[str],
    team: Optional[str],
    pi: Optional[str],
    chat_type: Optional[str],
    conn: Connection
) -> Tuple[str, Dict[str, Any]]:
    """
    Get existing chat history or create new chat history row.
    
    Args:
        conversation_id: Existing conversation ID (UUID)
        user_id: User ID (required for new conversations)
        team: Team name (required for new conversations)
        pi: PI name (required for new conversations)
        chat_type: Chat type (required for new conversations)
        conn: Database connection
        
    Returns:
        Tuple of (conversation_id as string, history_json dict)
    """
    # Provide defaults for required fields
    username = user_id or "unknown"
    team = team or "unknown"
    pi = pi or "unknown"
    chat_type = chat_type or "Direct_chat"
    
    # If conversation_id provided, try to fetch existing history (now integer ID)
    if conversation_id is not None and str(conversation_id).strip() != "":
        try:
            conversation_id_int = int(str(conversation_id))
            query = text(f"""
                SELECT id, history_json
                FROM {config.CHAT_HISTORY_TABLE}
                WHERE id = :conversation_id
            """)
            result = conn.execute(query, {"conversation_id": conversation_id_int})
            row = result.fetchone()
            if row:
                history_json = row[1] if row[1] is not None else {"messages": []}
                if not isinstance(history_json, dict):
                    history_json = {"messages": []}
                if "messages" not in history_json:
                    history_json["messages"] = []
                return str(row[0]), history_json
        except Exception as e:
            logger.warning(f"Error fetching chat history for conversation_id {conversation_id}: {e}")
            # If fetch fails, create new conversation
    
    # Create new chat history row
    history_json = {"messages": []}
    insert_query = text(f"""
        INSERT INTO {config.CHAT_HISTORY_TABLE}
        (username, team, pi, chat_type, history_json)
        VALUES (:username, :team, :pi, :chat_type, CAST(:history_json AS jsonb))
        RETURNING id
    """)
    
    try:
        result = conn.execute(insert_query, {
            "username": username,
            "team": team,
            "pi": pi,
            "chat_type": chat_type,
            "history_json": json.dumps(history_json)
        })
        row = result.fetchone()
        conn.commit()
        new_conversation_id = str(row[0])
        logger.info(f"Created new chat history with conversation_id: {new_conversation_id}")
    except Exception as e:
        logger.error(f"Error creating chat history: {e}")
        conn.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create chat history: {str(e)}"
        )
    
    return new_conversation_id, history_json


def update_chat_history(
    conversation_id: str,
    user_message: str,
    assistant_response: str,
    conn: Connection
) -> None:
    """
    Update chat history with new user message and assistant response.
    
    Args:
        conversation_id: Conversation ID (UUID)
        user_message: User's question
        assistant_response: Assistant's response
        conn: Database connection
    """
    try:
        # Fetch current history
        query = text(f"""
            SELECT history_json
            FROM {config.CHAT_HISTORY_TABLE}
            WHERE id = :conversation_id
        """)
        
        result = conn.execute(query, {"conversation_id": conversation_id})
        row = result.fetchone()
        
        if not row:
            logger.error(f"Chat history not found for conversation_id: {conversation_id}")
            raise HTTPException(
                status_code=404,
                detail=f"Chat history not found for conversation_id: {conversation_id}"
            )
        
        # Get current history
        history_json = row[0] if row[0] is not None else {"messages": []}
        if not isinstance(history_json, dict):
            history_json = {"messages": []}
        if "messages" not in history_json:
            history_json["messages"] = []
        
        # Add new messages
        history_json["messages"].append({
            "role": "user",
            "content": user_message
        })
        history_json["messages"].append({
            "role": "assistant",
            "content": assistant_response
        })
        
        # Update database
        update_query = text(f"""
            UPDATE {config.CHAT_HISTORY_TABLE}
            SET history_json = CAST(:history_json AS jsonb)
            WHERE id = :conversation_id
        """)
        
        conn.execute(update_query, {
            "conversation_id": conversation_id,
            "history_json": json.dumps(history_json)
        })
        conn.commit()
        
        logger.info(f"Updated chat history for conversation_id: {conversation_id}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating chat history: {e}")
        conn.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update chat history: {str(e)}"
        )


def build_team_dashboard_context(
    team_name: Optional[str],
    prompt_name: Optional[str],
    user_id: Optional[str],
    conn: Connection
) -> Optional[str]:
    """
    Build conversation context for Team_dashboard chat type.
    Fetches DB prompt (default or custom) and adds formatted team metrics data.
    
    Args:
        team_name: Team name (required for data fetching)
        prompt_name: Optional custom prompt name
        user_id: User ID for custom prompt lookup
        conn: Database connection
        
    Returns:
        Formatted conversation context string or None if no data available
    """
    conversation_context = None
    
    # Fetch DB prompt (default or custom)
    if not prompt_name or not prompt_name.strip():
        # Use default: fetch "Team_dashboard-Content" from admin
        content_prompt_name = "Team_dashboard-Content"
        try:
            content_prompt = get_prompt_by_email_and_name(
                email_address='admin',
                prompt_name=content_prompt_name,
                conn=conn,
                active=True,
                replace_placeholders=True
            )
            if content_prompt and content_prompt.get('prompt_description'):
                conversation_context = str(content_prompt['prompt_description'])
                logger.info(f"Using default DB content prompt for '{content_prompt_name}' (length: {len(conversation_context)} chars)")
        except Exception as e:
            logger.warning(f"Failed to fetch DB content prompt for '{content_prompt_name}': {e}")
    else:
        # Use custom prompt: fetch from user_id with prompt_name
        custom_prompt_name = prompt_name.strip()
        try:
            custom_prompt = get_prompt_by_email_and_name(
                email_address=user_id or 'unknown',
                prompt_name=custom_prompt_name,
                conn=conn,
                active=True,
                replace_placeholders=True
            )
            if custom_prompt and custom_prompt.get('prompt_description'):
                conversation_context = str(custom_prompt['prompt_description'])
                logger.info(f"Using custom DB prompt for '{custom_prompt_name}' (length: {len(conversation_context)} chars)")
            else:
                logger.error(f"Custom prompt not found: '{custom_prompt_name}' (user_id='{user_id}')")
        except Exception as e:
            logger.error(f"Failed to fetch custom prompt '{custom_prompt_name}': {e}")
    
    # Fetch and format team metrics data (closed sprints, burndown, bugs trend)
    if team_name:
        try:
            # 1. Fetch closed sprints (last 3 months)
            closed_sprints = []
            try:
                closed_sprints = get_closed_sprints_data_db([team_name], months=3, issue_type=None, conn=conn)
                logger.info(f"Fetched {len(closed_sprints)} closed sprints for Team_dashboard")
            except Exception as e:
                logger.warning(f"Failed to fetch closed sprints for Team_dashboard: {e}")
            
            # 2. Fetch sprint burndown (auto-select active sprint)
            burndown_data = []
            selected_sprint_name = None
            try:
                # Get active sprints and select the one with max total issues
                active_sprints = get_sprints_with_total_issues_db(team_name, "active", conn)
                if active_sprints:
                    selected_sprint = max(active_sprints, key=lambda x: x.get('total_issues', 0))
                    selected_sprint_name = selected_sprint.get('name')
                    logger.info(f"Auto-selected sprint '{selected_sprint_name}' for burndown")
                    
                    # Get burndown data
                    burndown_data = get_sprint_burndown_data_db(
                        [team_name], 
                        selected_sprint_name, 
                        issue_type="all", 
                        conn=conn
                    )
                    logger.info(f"Fetched {len(burndown_data)} burndown records for Team_dashboard")
                else:
                    logger.info("No active sprints found for Team_dashboard burndown")
            except Exception as e:
                logger.warning(f"Failed to fetch sprint burndown for Team_dashboard: {e}")
            
            # 3. Fetch bugs trend (last 6 months, issue_type='Bug')
            bugs_trend = []
            try:
                bugs_trend = get_issues_trend_data_db(team_name, months=6, issue_type="Bug", conn=conn)
                logger.info(f"Fetched {len(bugs_trend)} bugs trend records for Team_dashboard")
            except Exception as e:
                logger.warning(f"Failed to fetch bugs trend for Team_dashboard: {e}")
            
            # Format the data
            formatted_data = format_team_dashboard_data(
                closed_sprints, 
                burndown_data, 
                bugs_trend,
                sprint_name=selected_sprint_name
            )
            
            # Add today's date in markdown format
            today_date_str = date.today().strftime('%Y-%m-%d')
            today_date_markdown = f"## Today's date: {today_date_str}"
            
            # Combine with prompt text: prompt -> marker -> today's date -> formatted data
            if formatted_data:
                if conversation_context:
                    # Add marker to separate prompt from data
                    conversation_context = conversation_context + '\n\n=== DATA_STARTS_HERE ===\n\n' + today_date_markdown + '\n\n' + formatted_data
                else:
                    conversation_context = today_date_markdown + '\n\n' + formatted_data
                logger.info(f"Combined prompt and formatted data for Team_dashboard (total length: {len(conversation_context)} chars)")
            elif conversation_context:
                # If no formatted data but we have prompt, still add today's date
                conversation_context = conversation_context + '\n\n' + today_date_markdown
            
        except Exception as e:
            logger.error(f"Error fetching team metrics data for Team_dashboard: {e}")
            # Continue with just prompt text if data fetching fails
    else:
        logger.warning("team_name not provided for Team_dashboard, skipping data fetch")
    
    return conversation_context


def build_pi_dashboard_context(
    pi_name: Optional[str],
    prompt_name: Optional[str],
    user_id: Optional[str],
    conn: Connection
) -> Optional[str]:
    """
    Build conversation context for PI_dashboard chat type.
    Fetches DB prompt (default or custom) and adds formatted PI metrics data.
    
    Args:
        pi_name: PI name (required for data fetching)
        prompt_name: Optional custom prompt name
        user_id: User ID for custom prompt lookup
        conn: Database connection
        
    Returns:
        Formatted conversation context string or None if no data available
    """
    conversation_context = None
    
    # Fetch DB prompt (default or custom)
    if not prompt_name or not prompt_name.strip():
        # Use default: fetch "PI_dashboard-Content" from admin
        content_prompt_name = "PI_dashboard-Content"
        try:
            content_prompt = get_prompt_by_email_and_name(
                email_address='admin',
                prompt_name=content_prompt_name,
                conn=conn,
                active=True,
                replace_placeholders=True
            )
            if content_prompt and content_prompt.get('prompt_description'):
                conversation_context = str(content_prompt['prompt_description'])
                logger.info(f"Using default DB content prompt for '{content_prompt_name}' (length: {len(conversation_context)} chars)")
        except Exception as e:
            logger.warning(f"Failed to fetch DB content prompt for '{content_prompt_name}': {e}")
    else:
        # Use custom prompt: fetch from user_id with prompt_name
        custom_prompt_name = prompt_name.strip()
        try:
            custom_prompt = get_prompt_by_email_and_name(
                email_address=user_id or 'unknown',
                prompt_name=custom_prompt_name,
                conn=conn,
                active=True,
                replace_placeholders=True
            )
            if custom_prompt and custom_prompt.get('prompt_description'):
                conversation_context = str(custom_prompt['prompt_description'])
                logger.info(f"Using custom DB prompt for '{custom_prompt_name}' (length: {len(conversation_context)} chars)")
            else:
                logger.error(f"Custom prompt not found: '{custom_prompt_name}' (user_id='{user_id}')")
        except Exception as e:
            logger.error(f"Failed to fetch custom prompt '{custom_prompt_name}': {e}")
    
    # Fetch and format PI metrics data (burndown, predictability, scope changes)
    if pi_name:
        try:
            # 1. Fetch PI burndown (with default issue_type="Epic")
            burndown_data = []
            try:
                burndown_data = fetch_pi_burndown_data(
                    pi_name=pi_name,
                    project_keys=None,
                    issue_type="Epic",  # Default from endpoint
                    team_names=None,
                    conn=conn
                )
                logger.info(f"Fetched {len(burndown_data)} PI burndown records for PI_dashboard")
            except Exception as e:
                logger.warning(f"Failed to fetch PI burndown for PI_dashboard: {e}")
            
            # 2. Fetch PI predictability (normalize pi_name to list)
            predictability_data = []
            try:
                # Normalize to list (same logic as endpoint)
                pi_names_list = [pi_name]  # Single PI for dashboard
                predictability_data = fetch_pi_predictability_data(
                    pi_names=pi_names_list,
                    team_name=None,  # No team filter for dashboard
                    conn=conn
                )
                logger.info(f"Fetched {len(predictability_data)} predictability records for PI_dashboard")
            except Exception as e:
                logger.warning(f"Failed to fetch PI predictability for PI_dashboard: {e}")
            
            # 3. Fetch scope changes (normalize pi_name to list for pi_names)
            scope_data = []
            try:
                # Normalize to list (same logic as endpoint)
                pi_names_list = [pi_name] if pi_name else []  # Single PI for dashboard
                scope_data = fetch_scope_changes_data(
                    pi_names=pi_names_list,
                    conn=conn
                )
                logger.info(f"Fetched {len(scope_data)} scope changes records for PI_dashboard")
            except Exception as e:
                logger.warning(f"Failed to fetch scope changes for PI_dashboard: {e}")
            
            # Format the data
            formatted_data = format_pi_dashboard_data(
                burndown_data,
                predictability_data,
                scope_data,
                pi_name=pi_name
            )
            
            # Add today's date in markdown format
            today_date_str = date.today().strftime('%Y-%m-%d')
            today_date_markdown = f"## Today's date: {today_date_str}"
            
            # Combine with prompt text: prompt -> marker -> today's date -> formatted data
            if formatted_data:
                if conversation_context:
                    # Add marker to separate prompt from data
                    conversation_context = conversation_context + '\n\n=== DATA_STARTS_HERE ===\n\n' + today_date_markdown + '\n\n' + formatted_data
                else:
                    conversation_context = today_date_markdown + '\n\n' + formatted_data
            elif conversation_context:
                # If no formatted data but we have prompt, still add today's date
                conversation_context = conversation_context + '\n\n' + today_date_markdown
                logger.info(f"Combined prompt and formatted data for PI_dashboard (total length: {len(conversation_context)} chars)")
            
        except Exception as e:
            logger.error(f"Error fetching PI metrics data for PI_dashboard: {e}")
            # Continue with just prompt text if data fetching fails
    else:
        logger.warning("pi_name not provided for PI_dashboard, skipping data fetch")
    
    return conversation_context


def format_team_dashboard_data(
    closed_sprints: list,
    burndown_data: list,
    bugs_trend: list,
    sprint_name: Optional[str] = None
) -> str:
    """
    Format team dashboard data for LLM context.
    
    Args:
        closed_sprints: List of closed sprint dictionaries
        burndown_data: List of burndown daily snapshots
        bugs_trend: List of bugs trend data (monthly)
        sprint_name: Optional sprint name for burndown section
        
    Returns:
        Formatted string for LLM context
    """
    formatted_parts = []
    
    # Format closed sprints
    if closed_sprints:
        formatted_parts.append("=== CLOSED SPRINTS (Last 3 months) ===")
        for sprint in closed_sprints:
            sprint_line = (
                f"Sprint {sprint.get('sprint_name', 'Unknown')}: "
                f"{sprint.get('start_date')} to {sprint.get('end_date')} | "
                f"Completed: {sprint.get('completed_percentage', 0.0):.1f}% | "
                f"Issues: {sprint.get('issues_at_start', 0)} planned, "
                f"{sprint.get('issues_added', 0)} added, "
                f"{sprint.get('issues_done', 0)} done, "
                f"{sprint.get('issues_remaining', 0)} remaining"
            )
            if sprint.get('sprint_goal'):
                sprint_line += f" | Goal: {sprint.get('sprint_goal')}"
            formatted_parts.append(sprint_line)
    else:
        formatted_parts.append("=== CLOSED SPRINTS (Last 3 months) ===")
        formatted_parts.append("No closed sprints found")
    
    formatted_parts.append("")  # Empty line
    
    # Format sprint burndown
    if burndown_data:
        sprint_info = f"=== SPRINT BURNDOWN (Active Sprint: {sprint_name or 'Unknown'}) ==="
        if burndown_data[0].get('start_date') and burndown_data[0].get('end_date'):
            sprint_info += f" | Dates: {burndown_data[0].get('start_date')} to {burndown_data[0].get('end_date')}"
        if burndown_data[0].get('total_issues'):
            sprint_info += f" | Total Issues: {burndown_data[0].get('total_issues')}"
        formatted_parts.append(sprint_info)
        
        for day in burndown_data:
            day_line = (
                f"Date: {day.get('snapshot_date')} | "
                f"Remaining: {day.get('remaining_issues', 0)} | "
                f"Completed Today: {day.get('issues_completed_on_day', 0)} | "
                f"Added Today: {day.get('issues_added_on_day', 0)} | "
                f"Removed Today: {day.get('issues_removed_on_day', 0)}"
            )
            formatted_parts.append(day_line)
    else:
        formatted_parts.append(f"=== SPRINT BURNDOWN (Active Sprint: {sprint_name or 'N/A'}) ===")
        formatted_parts.append("No burndown data found")
    
    formatted_parts.append("")  # Empty line
    
    # Format bugs trend
    if bugs_trend:
        formatted_parts.append("=== BUGS CREATED AND RESOLVED OVER TIME (Last 6 months) ===")
        for month_data in bugs_trend:
            # Extract month and other fields from the dictionary - try multiple possible column names
            report_month = (
                month_data.get('report_month') or 
                month_data.get('Report_Month') or 
                month_data.get('month') or 
                'Unknown'
            )
            
            # Try multiple possible column names for created count
            created = (
                month_data.get('created') or 
                month_data.get('Created') or 
                month_data.get('issues_created') or 
                month_data.get('bugs_created') or 
                0
            )
            
            # Try multiple possible column names for resolved count
            resolved = (
                month_data.get('resolved') or 
                month_data.get('Resolved') or 
                month_data.get('issues_resolved') or 
                month_data.get('bugs_resolved') or 
                0
            )
            
            # Try multiple possible column names for open count
            open_count = (
                month_data.get('open') or 
                month_data.get('Open') or 
                month_data.get('cumulative_open') or 
                month_data.get('Cumulative_Open') or 
                month_data.get('open_count') or 
                0
            )
            
            trend_line = (
                f"Month: {report_month} | "
                f"Created: {created} | "
                f"Resolved: {resolved} | "
                f"Open: {open_count}"
            )
            formatted_parts.append(trend_line)
    else:
        formatted_parts.append("=== BUGS CREATED AND RESOLVED OVER TIME (Last 6 months) ===")
        formatted_parts.append("No bugs trend data found")
    
    return "\n".join(formatted_parts)


def format_pi_dashboard_data(
    burndown_data: list,
    predictability_data: list,
    scope_data: list,
    pi_name: Optional[str] = None
) -> str:
    """
    Format PI dashboard data for LLM context.
    
    Args:
        burndown_data: List of PI burndown daily snapshots
        predictability_data: List of PI predictability records
        scope_data: List of scope changes records
        pi_name: Optional PI name for section headers
        
    Returns:
        Formatted string for LLM context
    """
    formatted_parts = []
    
    # Format PI burndown
    if burndown_data:
        header = f"=== PI BURNDOWN CHART (PI: {pi_name or 'Unknown'}) ==="
        formatted_parts.append(header)
        
        for day in burndown_data:
            # Generic: print all fields returned from database function
            day_fields = []
            for field_name, field_value in day.items():
                if field_value is not None:
                    day_fields.append(f"{field_name}: {field_value}")
            
            if day_fields:
                formatted_parts.append(" | ".join(day_fields))
            else:
                formatted_parts.append("No data available")
    else:
        formatted_parts.append(f"=== PI BURNDOWN CHART (PI: {pi_name or 'N/A'}) ===")
        formatted_parts.append("No burndown data found")
    
    formatted_parts.append("")  # Empty line
    
    # Format PI predictability
    if predictability_data:
        formatted_parts.append(f"=== PI PREDICTABILITY (PI: {pi_name or 'Unknown'}) ===")
        for record in predictability_data:
            # Generic: print all fields returned from database function
            record_fields = []
            for field_name, field_value in record.items():
                if field_value is not None:
                    record_fields.append(f"{field_name}: {field_value}")
            
            if record_fields:
                formatted_parts.append(" | ".join(record_fields))
            else:
                formatted_parts.append("No data available")
    else:
        formatted_parts.append(f"=== PI PREDICTABILITY (PI: {pi_name or 'N/A'}) ===")
        formatted_parts.append("No predictability data found")
    
    formatted_parts.append("")  # Empty line
    
    # Format epic scope changes
    if scope_data:
        formatted_parts.append(f"=== EPIC SCOPE CHANGES CHART (PI: {pi_name or 'Unknown'}) ===")
        for record in scope_data:
            # Generic: print all fields returned from database function
            record_fields = []
            for field_name, field_value in record.items():
                if field_value is not None:
                    record_fields.append(f"{field_name}: {field_value}")
            
            if record_fields:
                formatted_parts.append(" | ".join(record_fields))
            else:
                formatted_parts.append("No data available")
    else:
        formatted_parts.append(f"=== EPIC SCOPE CHANGES CHART (PI: {pi_name or 'N/A'}) ===")
        formatted_parts.append("No scope changes data found")
    
    return "\n".join(formatted_parts)


# ============================================================================
# Epic Refinement Request Handling
# ============================================================================


def detect_epic_refinement_request(question: str) -> Optional[str]:
    """
    Detect if question is requesting epic refinement/recommendation.
    
    Returns:
        JIRA issue key if detected, None otherwise
    """
    if not question:
        return None
    
    question_lower = question.lower()
    
    # Check for refinement keywords
    has_refinement_keyword = any(
        keyword in question_lower 
        for keyword in ['recommend', 'refined', 'refine']
    )
    
    # Check for epic keyword
    has_epic_keyword = 'epic' in question_lower
    
    # Extract JIRA issue key (format: UP_TO_10_CHARS-NUMBER)
    # JIRA project keys can be 1-10 characters, but we allow up to 10 for flexibility
    jira_key_pattern = r'\b[A-Z]{1,10}-\d+\b'
    jira_keys = re.findall(jira_key_pattern, question.upper())
    
    # All conditions must be met
    if has_refinement_keyword and has_epic_keyword and jira_keys:
        return jira_keys[0]  # Return first matching key
    
    return None


def detect_issue_suggestion_request(question: str) -> Tuple[bool, Optional[str]]:
    """
    Detect if follow-up question is asking for suggestions/recommendations about an issue/PBI/work item/epic.
    Also extracts issue key from question if present (takes precedence over chat_history).
    
    All keywords are case-insensitive.
    
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
    
    # Check for entity keywords (case-insensitive)
    entity_keywords = ['issue', 'pbi', 'work item', 'epic']
    has_entity_keyword = any(keyword in question_lower for keyword in entity_keywords)
    found_entity_keyword = next((kw for kw in entity_keywords if kw in question_lower), None)
    
    # Check for action keywords (case-insensitive)
    action_keywords = ['suggest', 'recommend', 'recommendation', 'suggestion', 'advise', 'propose', 'what is', 'what are', 'how']
    has_action_keyword = any(keyword in question_lower for keyword in action_keywords)
    
    # Check for reference keywords (case-insensitive)
    reference_keywords = ['this', 'that']
    has_reference_keyword = any(keyword in question_lower for keyword in reference_keywords)
    
    # All three must be present
    is_detected = has_entity_keyword and has_action_keyword and has_reference_keyword
    
    # Extract issue key from question if present (takes precedence)
    issue_key_from_question = extract_issue_key_from_response(question)
    
    return is_detected, issue_key_from_question


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
        
        issue_result = conn.execute(issue_query, {"issue_key": issue_key})
        issue_row = issue_result.fetchone()
        
        if not issue_row:
            logger.warning(f"Issue {issue_key} not found")
            return None
        
        logger.info(f"Issue {issue_key} fetched successfully")
        
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
            logger.info(f"Epic {issue_key}: fetched {len(children)} children successfully")
        
        return result
        
    except Exception as e:
        logger.error(f"Error fetching issue details for {issue_key}: {e}")
        return None


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


def handle_epic_refinement_request(
    question: str,
    conn: Connection
) -> Optional[str]:
    """
    Handle epic refinement request if detected in question.
    Uses unified fetch_issue_details() and format_issue_details_for_llm().
    Always uses Epic Refinement template.
    
    Args:
        question: User's question
        conn: Database connection
        
    Returns:
        Formatted conversation_context string if epic refinement detected, None otherwise
        
    Raises:
        HTTPException: If epic refinement detected but template not found or epic not found
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
    # Note: We need to add the "=== EPIC INFORMATION FOR REFINEMENT ===" marker for backward compatibility
    formatted_context = format_issue_details_for_llm(
        issue_data,
        template_text=template_text,
        include_children=True
    )
    
    # Replace "=== ISSUE INFORMATION ===" with "=== EPIC INFORMATION FOR REFINEMENT ===" for backward compatibility
    conversation_context = formatted_context.replace(
        "=== ISSUE INFORMATION ===",
        "=== EPIC INFORMATION FOR REFINEMENT ==="
    )
    
    logger.info(f"Epic refinement context prepared (length: {len(conversation_context)} chars)")
    
    return conversation_context


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
    
    logger.info("Issue suggestion request detected - fetching issue details")
    
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
    children_count = issue_data.get("children_count", 0)
    
    # 4. Determine template usage based on issue_type
    template_text = None
    if issue_type == "Epic":
        # Epic: Use Epic Refinement template
        refinement_template = get_prompt_by_email_and_name(
            email_address='admin',
            prompt_name='Epic Refinement',
            conn=conn,
            active=True,
            replace_placeholders=True
        )
        
        if refinement_template and refinement_template.get('prompt_description'):
            template_text = str(refinement_template['prompt_description'])
    
    # 5. Format issue data (unified function)
    formatted_issue_details = format_issue_details_for_llm(
        issue_data,
        template_text=template_text,
        include_children=True  # Include children if Epic
    )
    
    # Log successful fetch summary
    if issue_type == "Epic":
        logger.info(f"Issue details fetched: {issue_key} (Epic with {children_count} children, {len(formatted_issue_details)} chars)")
    else:
        logger.info(f"Issue details fetched: {issue_key} ({issue_type}, {len(formatted_issue_details)} chars)")
    
    return formatted_issue_details


def extract_issue_key_from_response(response: str) -> Optional[str]:
    """
    Extract first JIRA issue key from LLM response.
    
    Issue key format: [JIRA_PROJECT]-[NUMBER]
    Where NUMBER must be 2+ digits.
    
    Args:
        response: LLM response text
        
    Returns:
        First matching issue key if found, None otherwise
    """
    if not response:
        return None
    
    # Pattern: 1-10 uppercase letters, dash, 2+ digits
    # \b ensures word boundaries
    pattern = r'\b[A-Z]{1,10}-\d{2,}\b'
    
    matches = re.findall(pattern, response.upper())
    
    if matches:
        return matches[0]  # Return first occurrence
    
    return None


async def call_llm_service(
    conversation_id: str,
    question: str,
    history_json: Dict[str, Any],
    user_id: Optional[str],
    selected_team: Optional[str],
    selected_pi: Optional[str],
    chat_type: Optional[str],
    conversation_context: Optional[str] = None,
    system_message: Optional[str] = None
) -> Dict[str, Any]:
    """
    Call LLM service with minimal payload.
    
    Args:
        conversation_id: Conversation ID
        question: User's question
        history_json: Chat history JSON
        user_id: User ID
        selected_team: Team name
        selected_pi: PI name
        chat_type: Chat type
        conversation_context: Optional additional context to include (e.g., for Team_insights)
        system_message: Optional system message to set AI behavior/context (controlled by backend)
        
    Returns:
        LLM service response dict
    """
    llm_service_url = f"{config.LLM_SERVICE_URL}/chat"
    
    # OPTIMIZATION: Strip stored context from history_json before sending to LLM
    # This prevents duplicate data: stored context is already sent via conversation_context parameter
    # Keep stored context in database, but only send messages to LLM to reduce token usage
    history_json_for_llm = None
    if history_json and isinstance(history_json, dict):
        # Create stripped version with only messages (conversation history)
        # Remove stored context fields that are already sent via conversation_context
        history_json_for_llm = {
            "messages": history_json.get("messages", [])
        }
        
        # Log the optimization
        original_size = len(json.dumps(history_json))
        stripped_size = len(json.dumps(history_json_for_llm))
        reduction = original_size - stripped_size
        if reduction > 0:
            logger.info(f"Optimized history_json: reduced from {original_size} to {stripped_size} chars ({reduction} chars removed, {100 * reduction / original_size:.1f}% reduction)")
    else:
        history_json_for_llm = history_json
    
    payload = {
        "conversation_id": conversation_id,
        "question": question,
        "history_json": history_json_for_llm,
        "username": user_id,
        "selected_team": selected_team,
        "selected_pi": selected_pi,
        "chat_type": chat_type,
        "conversation_context": conversation_context,
        "system_message": system_message
    }
    
    # ANSI color codes for bold/color
    BOLD = '\033[1m'
    CYAN = '\033[96m'
    YELLOW = '\033[93m'
    RESET = '\033[0m'
    
    logger.info(f"Calling LLM service: {llm_service_url}")
    
    # Calculate total chars being sent (using stripped history_json)
    total_chars_sent = len(question) if question else 0
    if conversation_context:
        total_chars_sent += len(conversation_context)
    if system_message:
        total_chars_sent += len(system_message)
    if history_json_for_llm:
        history_str = json.dumps(history_json_for_llm) if isinstance(history_json_for_llm, dict) else str(history_json_for_llm)
        total_chars_sent += len(history_str)
    
    if conversation_context:
        logger.info(f"Conversation context included: {len(conversation_context)} chars")
    else:
        logger.info("No conversation context provided")
    if system_message:
        logger.info(f"System message included: {len(system_message)} chars")
    else:
        logger.info("No system message provided")
    
    logger.info(f"{BOLD}{YELLOW}Question: {question}{RESET}")
    logger.info(f"{BOLD}{CYAN}Total chars sent to LLM: {total_chars_sent}{RESET}")
    logger.debug(f"Payload: {payload}")
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(llm_service_url, json=payload)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as e:
        logger.error(f"HTTP error calling LLM service: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"LLM service error: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Error calling LLM service: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to call LLM service: {str(e)}"
        )


async def fetch_dashboard_reports_data(
    dashboard_data: Dict[str, Any],
    conn: Connection
) -> str:
    """
    Fetch all report data from dashboard layout config.
    
    Args:
        dashboard_data: Dashboard state with layoutConfig, topBarFilters, reportFilters, pinnedFilters
        conn: Database connection
        
    Returns:
        Formatted string containing all report data for LLM context
    """
    import json
    from datetime import datetime, date
    from decimal import Decimal
    from database_reports import get_report_definition_by_id, resolve_report_data
    from cache_utils import generate_cache_key, get_cached_report, set_cached_report, get_report_cache_ttl
    
    # Custom JSON encoder to handle datetime and Decimal objects
    class DateTimeEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            if isinstance(obj, Decimal):
                return float(obj)
            return super().default(obj)
    
    logger.info("DASHBOARD DATA COLLECTION - Starting")
    
    layout_config = dashboard_data.get('layoutConfig')
    if not layout_config:
        logger.warning("No dashboard layout configured")
        return "No dashboard layout configured."
    
    top_bar_filters = dashboard_data.get('topBarFilters', {})
    report_filters = dashboard_data.get('reportFilters', {})
    
    def normalize_filters(filters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize frontend filter names to backend filter names.
        Converts:
        - selectedTeam -> team_name
        - selectedTreeType ('team' or 'group') -> isGroup (boolean)
        - selectedSprint -> sprint_name
        - selectedPI -> pi and pi_names (for reports that use pi_names array)
        """
        normalized = {}
        for key, value in filters.items():
            if key == 'selectedTeam':
                normalized['team_name'] = value
            elif key == 'selectedTreeType':
                # Convert 'team'/'group' string to boolean isGroup
                normalized['isGroup'] = (value == 'group')
            elif key == 'selectedSprint':
                normalized['sprint_name'] = value
            elif key == 'selectedPI':
                normalized['pi'] = value
                normalized['pi_names'] = [value] if value else []  # Also set pi_names for reports that use it
            else:
                # Keep other filters as-is
                normalized[key] = value
        return normalized
    
    # Normalize top bar filters
    top_bar_filters = normalize_filters(top_bar_filters)
    
    # Normalize report filters (each report's filters)
    normalized_report_filters = {}
    for report_id, filters in report_filters.items():
        normalized_report_filters[report_id] = normalize_filters(filters)
    report_filters = normalized_report_filters
    logger.info(f"Normalized report filters: {json.dumps(report_filters, indent=2, cls=DateTimeEncoder)}")
    
    # Extract all report IDs from layout
    report_ids = []
    for row in layout_config.get('rows', []):
        report_ids.extend(row.get('reportIds', []))
    
    if not report_ids:
        logger.warning("No reports in dashboard layout")
        return "No reports in dashboard layout."
    
    logger.info(f"Fetching data for {len(report_ids)} reports: {report_ids}")
    
    # Fetch data for each report
    formatted_reports = []
    for idx, report_id in enumerate(report_ids, 1):
        # TEMPORARY FIX: Skip Epic Hierarchy report for LLM context
        # TODO: Replace with proper solution using report definition flag
        # Proper fix should:
        # 1. Add a flag in report definition (e.g., "exclude_from_llm" or "send_to_llm: false") 
        #    that indicates "Do not send this report to LLM"
        # 2. UI should not show reports with this flag in the list of reports that will be sent to LLM
        # 3. Code should use this flag generically to make the decision instead of hardcoding report_id checks
        if report_id == "issues-epics-hierarchy":
            logger.info(f"Skipping report '{report_id}' for LLM context (temporary fix)")
            continue
        
        try:
            logger.info(f"[{idx}/{len(report_ids)}] Processing report: {report_id}")
            
            # Get report definition
            definition = get_report_definition_by_id(report_id, conn)
            if not definition:
                logger.warning(f"Report '{report_id}' not found, skipping")
                continue
            
            # Merge filters: default < top bar < report-specific
            default_filters = definition.get("default_filters", {})
            merged_filters = {**default_filters, **top_bar_filters}
            
            # Apply report-specific filters if any
            if report_id in report_filters:
                merged_filters.update(report_filters[report_id])
            
            # Check cache first
            cache_key = generate_cache_key(report_id, merged_filters)
            cached_data = get_cached_report(cache_key)
            
            if cached_data:
                logger.info(f"  ✓ Using cached data for report '{report_id}'")
                report_data = cached_data
            else:
                # Fetch fresh data
                logger.info(f"  → Fetching fresh data for report '{report_id}'")
                resolved_payload = resolve_report_data(definition["data_source"], merged_filters, conn)
                
                report_data = {
                    "definition": {
                        "report_id": definition["report_id"],
                        "report_name": definition["report_name"],
                        "chart_type": definition["chart_type"],
                        "description": definition.get("description"),
                    },
                    "filters": merged_filters,
                    "result": resolved_payload.get("data"),
                    "meta": resolved_payload.get("meta", {}),
                }
                
                # Cache the result
                ttl = get_report_cache_ttl(report_id)
                set_cached_report(cache_key, report_data, ttl=ttl)
                logger.info(f"  ✓ Cached report data with TTL: {ttl}s")
            
            # Format report data for LLM
            report_name = report_data["definition"]["report_name"]
            report_desc = report_data["definition"].get("description", "")
            report_result = report_data.get("result", [])
            
            # Truncate data if max_records_for_llm is set (to limit LLM context size)
            max_records = definition.get("max_records_for_llm")
            if max_records and isinstance(report_result, list) and len(report_result) > max_records:
                original_count = len(report_result)
                report_result = report_result[:max_records]
                logger.info(f"  Truncated report '{report_id}' data from {original_count} to {max_records} records for LLM")
            
            formatted_report = f"\n## {report_name}\n"
            if report_desc:
                formatted_report += f"Description: {report_desc}\n"
            formatted_report += f"Filters: {json.dumps(merged_filters, indent=2, cls=DateTimeEncoder)}\n"
            formatted_report += f"Data: {json.dumps(report_result, indent=2, cls=DateTimeEncoder)}\n"
            
            formatted_reports.append(formatted_report)
            
        except Exception as e:
            logger.error(f"Error fetching report '{report_id}': {e}")
            formatted_reports.append(f"\n## {report_id}\nError: Failed to fetch data - {str(e)}\n")
    
    final_context = "\n".join(formatted_reports)
    total_chars = len(final_context)
    # ANSI color codes for bold/color
    BOLD = '\033[1m'
    CYAN = '\033[96m'
    RESET = '\033[0m'
    logger.info(f"Successfully formatted {len(formatted_reports)} reports for LLM context")
    logger.info(f"{BOLD}{CYAN}Total context length: {total_chars} characters{RESET}")
    logger.info("DASHBOARD DATA COLLECTION - Complete")
    return final_context


@ai_chat_router.post("/ai-chat")
async def ai_chat(
    request: AIChatRequest,
    http_request: Request,
    conn: Connection = Depends(get_db_connection)
):
    """
    AI Chat endpoint that connects to LLM service.
    
    Args:
        request: AIChatRequest containing all optional parameters
        
    Returns:
        JSON response with AI answer and conversation details
    """
    try:
        # Log POST body data (limited to 10KB)
        try:
            request_dict = request.model_dump() if hasattr(request, 'model_dump') else request.dict()
            body_json = json.dumps(request_dict, indent=2, default=str)
            if len(body_json) > 10000:
                body_json = body_json[:10000] + "... [truncated]"
            logger.info(f"[AI_CHAT_POST_BODY] POST body:\n{body_json}")
            print(f"[AI_CHAT_POST_BODY] POST body:\n{body_json}")
        except Exception as e:
            logger.warning(f"[AI_CHAT_POST_BODY] Failed to log POST body: {e}")
        
        # DEBUG: Log all AI chat parameters
        ai_chat_params = {
            "conversation_id": request.conversation_id,
            "user_id": request.user_id,
            "question": request.question[:100] + "..." if request.question and len(request.question) > 100 else request.question,
            "selected_team": request.selected_team,
            "selected_pi": request.selected_pi,
            "chat_type": request.chat_type,
            "prompt_name": request.prompt_name,
            "insights_id": request.insights_id,
            "recommendation_id": request.recommendation_id,
            "dashboard_data": "present" if request.dashboard_data else "not present"
        }
        logger.info(f"[AI_CHAT_DEBUG] AI Chat Request Parameters: {json.dumps(ai_chat_params, indent=2, default=str)}")
        print(f"[AI_CHAT_DEBUG] AI Chat Request Parameters: {json.dumps(ai_chat_params, indent=2, default=str)}")
        
        # Validate question length
        if request.question and len(request.question) > MAX_QUESTION_LENGTH:
            raise HTTPException(
                status_code=422,
                detail="Question exceeds maximum length. Please shorten your question or contact your administrator."
            )
        
        # Allow empty questions; log for visibility
        if not request.question or not request.question.strip():
            logger.info("Empty question received; continuing without user question")
        
        # Normalize chat_type to a string for downstream usage
        chat_type_str = None
        if request.chat_type is not None:
            try:
                # If ChatType enum, use its value; otherwise assume it's already a string
                chat_type_str = request.chat_type.value  # type: ignore[attr-defined]
            except AttributeError:
                chat_type_str = str(request.chat_type)

        # 1. Get or create chat history
        conversation_id, history_json = get_or_create_chat_history(
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            team=request.selected_team,
            pi=request.selected_pi,
            chat_type=chat_type_str,
            conn=conn
        )
        
        logger.info(f"Conversation ID: {conversation_id}")
        logger.info(f"History messages count: {len(history_json.get('messages', []))}")
        
        # 2. Check for epic refinement request (before other chat type handlers)
        conversation_context = None
        try:
            epic_refinement_context = handle_epic_refinement_request(request.question, conn)
            if epic_refinement_context:
                conversation_context = epic_refinement_context
                logger.info("Using epic refinement context")
        except HTTPException:
            raise  # Re-raise HTTP exceptions (template/epic not found)
        except Exception as e:
            logger.error(f"Error handling epic refinement request: {e}")
            # Continue without special context - let LLM handle the question normally
        
        # 3. Handle Team_insights chat type - fetch card data and build context
        if not conversation_context and chat_type_str == "Team_insights":
            # Validate insights_id is provided
            if not request.insights_id:
                raise HTTPException(
                    status_code=400,
                    detail="insights_id is required when chat_type is Team_insights"
                )
            try:
                # Convert insights_id to int
                insights_id_int = int(request.insights_id)
                # Fetch AI card using unified helper function
                logger.info(f"Fetching AI card with ID: {insights_id_int}")
                card = get_ai_card_by_id(insights_id_int, conn)
                if not card:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Team AI card with ID {insights_id_int} not found"
                    )
                # Extract source_job_id from card
                source_job_id = card.get('source_job_id')
                # Get formatted job data from view (always returns a string - either data or message)
                formatted_job_data = get_formatted_job_data_for_llm_followup_insight(insights_id_int, source_job_id, conn)
                
                # NEW: Try prompt from DB before fallback
                content_prompt_name = f"{chat_type_str}-Content"
                content_intro = None
                try:
                    content_prompt = get_prompt_by_email_and_name(
                        email_address='admin',
                        prompt_name=content_prompt_name,
                        conn=conn,
                        active=True,
                        replace_placeholders=True
                    )
                    if content_prompt and content_prompt.get('prompt_description'):
                        content_intro = str(content_prompt['prompt_description'])
                        logger.info(f"Using DB content prompt for prompt_name='{content_prompt_name}' (length: {len(content_intro)} chars)")
                    else:
                        logger.info(f"No active DB content prompt found for prompt_name='{content_prompt_name}', using fallback context intro")
                except Exception as e:
                    logger.warning(f"Failed to fetch DB content prompt for prompt_name='{content_prompt_name}': {e}. Using fallback.")
                if not content_intro:
                    content_intro = "This is previous discussion we had in a different chat. Read this information as I want to ask follow up questions."
                
                # Extract full_information from card
                full_information = card.get('full_information', '')
                
                # Build conversation_context: content_intro + marker + full_information + input_sent
                # Add marker to separate prompt from data
                conversation_context = content_intro + '\n\n=== DATA_STARTS_HERE ===\n\n' + full_information + '\n\n' + formatted_job_data
                logger.info(f"Built conversation context from team AI card {insights_id_int} with intro (length: {len(conversation_context)} chars)")
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid insights_id format: {request.insights_id}. Must be an integer."
                )
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error fetching team AI card for Team_insights: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to fetch team AI card: {str(e)}"
                )
        
        # 3.1. Handle PI_insights chat type - fetch PI card and build context
        elif not conversation_context and chat_type_str == "PI_insights":
            if not request.insights_id:
                raise HTTPException(
                    status_code=400,
                    detail="insights_id is required when chat_type is PI_insights"
                )
            try:
                insights_id_int = int(request.insights_id)
                logger.info(f"Fetching AI card with ID: {insights_id_int}")
                card = get_ai_card_by_id(insights_id_int, conn)
                if not card:
                    raise HTTPException(
                        status_code=404,
                        detail=f"PI AI card with ID {insights_id_int} not found"
                    )
                # Extract source_job_id from card
                source_job_id = card.get('source_job_id')
                # Get formatted job data from view (always returns a string - either data or message)
                formatted_job_data = get_formatted_job_data_for_llm_followup_insight(insights_id_int, source_job_id, conn)
                
                content_prompt_name = f"{chat_type_str}-Content"
                content_intro = None
                try:
                    content_prompt = get_prompt_by_email_and_name(
                        email_address='admin',
                        prompt_name=content_prompt_name,
                        conn=conn,
                        active=True,
                        replace_placeholders=True
                    )
                    if content_prompt and content_prompt.get('prompt_description'):
                        content_intro = str(content_prompt['prompt_description'])
                        logger.info(f"Using DB content prompt for prompt_name='{content_prompt_name}' (length: {len(content_intro)} chars)")
                    else:
                        logger.info(f"No active DB content prompt found for prompt_name='{content_prompt_name}', using fallback context intro")
                except Exception as e:
                    logger.warning(f"Failed to fetch DB content prompt for prompt_name='{content_prompt_name}': {e}. Using fallback.")
                if not content_intro:
                    content_intro = "This is previous discussion we had in a different chat. Read this information as I want to ask follow up questions."
                
                # Extract full_information from card
                full_information = card.get('full_information', '')
                
                # Build conversation_context: content_intro + marker + full_information + input_sent
                # Add marker to separate prompt from data
                conversation_context = content_intro + '\n\n=== DATA_STARTS_HERE ===\n\n' + full_information + '\n\n' + formatted_job_data
                logger.info(f"Built conversation context from PI AI card {insights_id_int} (length: {len(conversation_context)} chars)")
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid insights_id format: {request.insights_id}. Must be an integer."
                )
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error fetching PI AI card for PI_insights: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to fetch PI AI card: {str(e)}"
                )

        # 3.2. Handle Recommendation_reason chat type - fetch recommendation data and build context
        elif not conversation_context and chat_type_str == "Recommendation_reason":
            # Validate recommendation_id is provided
            if not request.recommendation_id:
                raise HTTPException(
                    status_code=400,
                    detail="recommendation_id is required when chat_type is Recommendation_reason"
                )
            try:
                # Convert recommendation_id to int
                recommendation_id_int = int(request.recommendation_id)
                # Fetch recommendation using shared helper function
                logger.info(f"Fetching recommendation with ID: {recommendation_id_int}")
                recommendation = get_recommendation_by_id(recommendation_id_int, conn)
                if not recommendation:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Recommendation with ID {recommendation_id_int} not found"
                    )
                # Extract source_job_id from recommendation (same pattern as Team Insights)
                source_job_id = recommendation.get('source_job_id')
                # Get formatted job data from recommendations table (always returns a string - either data or message)
                formatted_job_data = get_formatted_job_data_for_llm_followup_recommendation(
                    recommendation_id_int, 
                    source_job_id, 
                    conn
                )
                
                # Build conversation_context (same pattern as Team Insights)
                content_prompt_name = "Recommendation_reason-Content"
                content_intro = None
                try:
                    content_prompt = get_prompt_by_email_and_name(
                        email_address='admin',
                        prompt_name=content_prompt_name,
                        conn=conn,
                        active=True,
                        replace_placeholders=True
                    )
                    if content_prompt and content_prompt.get('prompt_description'):
                        content_intro = str(content_prompt['prompt_description'])
                        logger.info(f"Using DB content prompt for prompt_name='{content_prompt_name}' (length: {len(content_intro)} chars)")
                    else:
                        logger.info(f"No active DB content prompt found for prompt_name='{content_prompt_name}', using fallback context intro")
                except Exception as e:
                    logger.warning(f"Failed to fetch DB content prompt for prompt_name='{content_prompt_name}': {e}. Using fallback.")
                if not content_intro:
                    content_intro = "This is previous discussion we had in a different chat. Read this information as I want to ask follow up questions."
                
                # Extract action_text from recommendation
                action_text = recommendation.get('action_text', '')
                
                # Build conversation_context: content_intro + marker + action_text + input_sent
                # Add marker to separate prompt from data
                conversation_context = content_intro + '\n\n=== DATA_STARTS_HERE ===\n\n' + action_text + '\n\n' + formatted_job_data
                logger.info(f"Built conversation context from recommendation {recommendation_id_int} with intro (length: {len(conversation_context)} chars)")
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid recommendation_id format: {request.recommendation_id}. Must be an integer."
                )
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error fetching recommendation for Recommendation_reason: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to fetch recommendation: {str(e)}"
                )
        
        # 2.8. Resolve system message by chat type using DB prompt (admin + active)
        system_message = SYSTEM_MESSAGE
        if chat_type_str:
            prompt_name = f"{chat_type_str}-System"
            try:
                prompt_row = get_prompt_by_email_and_name(
                    email_address='admin',
                    prompt_name=prompt_name,
                    conn=conn,
                    active=True,
                    replace_placeholders=True
                )
                if prompt_row and prompt_row.get('prompt_description'):
                    system_message = str(prompt_row['prompt_description'])
                    logger.info(f"Using DB system prompt for prompt_name='{prompt_name}' (length: {len(system_message)} chars)")
                else:
                    logger.info(f"No active DB prompt found for prompt_name='{prompt_name}', using default system message")
            except Exception as e:
                logger.warning(f"Failed to fetch DB system prompt for prompt_name='{prompt_name}': {e}. Using default.")
        else:
            logger.info("chat_type not provided; using default system message")

        # 2.8.5. Handle dashboard data if provided (for Team_dashboard or PI_dashboard chat types)
        if request.dashboard_data and conversation_context is None:
            logger.info("Processing dashboard data for AI chat")
            try:
                dashboard_context = await fetch_dashboard_reports_data(request.dashboard_data, conn)
                
                # Get content intro prompt from DB
                content_prompt_name = "Team_dashboard-Content" if request.selected_team else "PI_dashboard-Content"
                content_intro = None
                try:
                    content_prompt = get_prompt_by_email_and_name(
                        email_address='admin',
                        prompt_name=content_prompt_name,
                        conn=conn,
                        active=True,
                        replace_placeholders=True
                    )
                    if content_prompt and content_prompt.get('prompt_description'):
                        content_intro = str(content_prompt['prompt_description'])
                        logger.info(f"Using DB content prompt for '{content_prompt_name}'")
                except Exception as e:
                    logger.warning(f"Failed to fetch DB content prompt: {e}")
                
                if not content_intro:
                    content_intro = "Here is the current dashboard data. Please analyze it and provide insights."
                
                # Build conversation context
                conversation_context = content_intro + '\n\n=== DASHBOARD DATA STARTS HERE ===\n\n' + dashboard_context
                logger.info(f"Built dashboard conversation context (length: {len(conversation_context)} chars)")
                
            except Exception as e:
                logger.error(f"Error processing dashboard data: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to process dashboard data: {str(e)}"
                )
        # Fallback to old dashboard context builders if no dashboard_data provided
        elif chat_type_str == "Team_dashboard":
            if conversation_context is None:  # Only set if not already set by other chat types
                conversation_context = build_team_dashboard_context(
                    team_name=request.selected_team,
                    prompt_name=request.prompt_name,
                    user_id=request.user_id,
                    conn=conn
                )
        elif chat_type_str == "PI_dashboard":
            if conversation_context is None:  # Only set if not already set by other chat types
                conversation_context = build_pi_dashboard_context(
                    pi_name=request.selected_pi,
                    prompt_name=request.prompt_name,
                    user_id=request.user_id,
                    conn=conn
                )

        # 2.9. On initial call, persist initial system/context snapshot into chat history
        # Determine if this is the initial call (no messages in history yet)
        is_initial_call = not history_json.get('messages') or len(history_json.get('messages', [])) == 0
        
        try:
            if is_initial_call:
                if 'initial_request_system_message' not in history_json:
                    history_json['initial_request_system_message'] = system_message
                if 'initial_request_conversation_context' not in history_json:
                    history_json['initial_request_conversation_context'] = conversation_context
                # Store data-only version (without content_intro) for follow-up calls
                # This allows LLM to access data without the confusing prompt instructions
                # Applies to: Team_insights, PI_insights, Recommendation_reason, Team_dashboard, PI_dashboard, Epic Refinement
                if 'initial_request_data_only' not in history_json and conversation_context:
                    # Extract data-only version using marker: check for either "=== DATA_STARTS_HERE ===" or "=== DASHBOARD DATA STARTS HERE ==="
                    # This works for all chat types: Team_insights, PI_insights, Recommendation_reason, Team_dashboard, PI_dashboard
                    marker = None
                    if "=== DASHBOARD DATA STARTS HERE ===" in conversation_context:
                        marker = "=== DASHBOARD DATA STARTS HERE ==="
                    elif "=== DATA_STARTS_HERE ===" in conversation_context:
                        marker = "=== DATA_STARTS_HERE ==="
                    
                    if marker:
                        marker_index = conversation_context.find(marker)
                        data_only = conversation_context[marker_index + len(marker):].strip()
                        history_json['initial_request_data_only'] = data_only
                        logger.info(f"Stored data-only context for {chat_type_str} follow-ups using marker (length: {len(data_only)} chars)")
                    else:
                        # Fallback: if marker not found, try old method (for backward compatibility)
                        logger.warning(f"Marker not found in {chat_type_str} context, trying fallback method")
                        if chat_type_str == "Team_insights" and request.insights_id:
                            try:
                                insights_id_int = int(request.insights_id)
                                card = get_ai_card_by_id(insights_id_int, conn)
                                full_information = card.get('full_information', '')
                                source_job_id = card.get('source_job_id')
                                formatted_job_data = get_formatted_job_data_for_llm_followup_insight(insights_id_int, source_job_id, conn)
                                data_only = full_information + '\n\n' + formatted_job_data if full_information else formatted_job_data
                                history_json['initial_request_data_only'] = data_only
                                logger.info(f"Stored data-only context for Team_insights follow-ups using fallback (length: {len(data_only)} chars)")
                            except Exception as e:
                                logger.warning(f"Failed to build data-only context for Team_insights: {e}")
                        elif chat_type_str == "PI_insights" and request.insights_id:
                            try:
                                insights_id_int = int(request.insights_id)
                                card = get_ai_card_by_id(insights_id_int, conn)
                                full_information = card.get('full_information', '')
                                source_job_id = card.get('source_job_id')
                                formatted_job_data = get_formatted_job_data_for_llm_followup_insight(insights_id_int, source_job_id, conn)
                                data_only = full_information + '\n\n' + formatted_job_data if full_information else formatted_job_data
                                history_json['initial_request_data_only'] = data_only
                                logger.info(f"Stored data-only context for PI_insights follow-ups using fallback (length: {len(data_only)} chars)")
                            except Exception as e:
                                logger.warning(f"Failed to build data-only context for PI_insights: {e}")
                        elif chat_type_str == "Recommendation_reason" and request.recommendation_id:
                            try:
                                recommendation_id_int = int(request.recommendation_id)
                                recommendation = get_recommendation_by_id(recommendation_id_int, conn)
                                action_text = recommendation.get('action_text', '')
                                source_job_id = recommendation.get('source_job_id')
                                formatted_job_data = get_formatted_job_data_for_llm_followup_recommendation(recommendation_id_int, source_job_id, conn)
                                data_only = action_text + '\n\n' + formatted_job_data if action_text else formatted_job_data
                                history_json['initial_request_data_only'] = data_only
                                logger.info(f"Stored data-only context for Recommendation_reason follow-ups using fallback (length: {len(data_only)} chars)")
                            except Exception as e:
                                logger.warning(f"Failed to build data-only context for Recommendation_reason: {e}")
                        elif chat_type_str in ["Team_dashboard", "PI_dashboard"]:
                            # Fallback: if marker not found, use full context (shouldn't happen but safe fallback)
                            history_json['initial_request_data_only'] = conversation_context
                            logger.warning(f"Marker not found in {chat_type_str} context, using full context as fallback")
                        elif conversation_context and "=== EPIC INFORMATION FOR REFINEMENT ===" in conversation_context:
                            # Handle Epic Refinement: extract data-only (epic data without template)
                            # Epic refinement format: template_text + "\n\n" + epic_section
                            # We want to extract just the epic_section part
                            epic_marker = "=== EPIC INFORMATION FOR REFINEMENT ==="
                            epic_marker_index = conversation_context.find(epic_marker)
                            if epic_marker_index >= 0:
                                # Extract everything from the epic marker onwards (the data part)
                                data_only = conversation_context[epic_marker_index:].strip()
                                history_json['initial_request_data_only'] = data_only
                                logger.info(f"Stored data-only context for Epic Refinement follow-ups (length: {len(data_only)} chars)")
                            else:
                                # Fallback: use full context if marker not found
                                history_json['initial_request_data_only'] = conversation_context
                                logger.warning("Epic marker not found in Epic Refinement context, using full context as fallback")

                # Seed initial messages into history_json for follow-ups
                history_json.setdefault('messages', [])
                if system_message:
                    history_json['messages'].append({
                        'role': 'system',
                        'content': system_message
                    })
                # Note: conversation_context is sent as a parameter, not seeded into history_json
                # This matches the original working approach
                snapshot_update_query = text(f"""
                    UPDATE {config.CHAT_HISTORY_TABLE}
                    SET history_json = CAST(:history_json AS jsonb)
                    WHERE id = :conversation_id
                """)
                conn.execute(snapshot_update_query, {
                    'conversation_id': int(conversation_id),
                    'history_json': json.dumps(history_json)
                })
                conn.commit()
                logger.info("Stored initial system/context and seeded messages in chat history")
        except Exception as e:
            logger.warning(f"Failed to store initial request snapshot: {e}")

        # 2.10. Check for SQL AI trigger and process if needed
        # Initialize variables to track SQL data for chat history
        sql_was_triggered = False
        sql_was_attempted = False  # Track if SQL was attempted (even if it failed)
        sql_formatted_for_history = None
        
        if request.question and request.question.startswith(config.SQL_AI_TRIGGER):
            sql_was_attempted = True  # Mark that SQL was attempted
            try:
                logger.info(f"SQL AI trigger detected in question: {request.question[:100]}...")
                
                # Convert history to SQL service format
                sql_history = convert_history_to_sql_format(history_json)
                logger.info(f"Converted {len(sql_history)} previous SQL exchanges to history")
                
                # Call SparksAI-SQL service
                sql_response = await call_sparksai_sql_execute(
                    question=request.question,
                    conversation_history=sql_history if sql_history else None,
                    include_formatted=True
                )
                
                # Extract data from response
                if not sql_response.get("success"):
                    raise Exception(f"SQL service returned error: {sql_response.get('data', {}).get('error', 'Unknown error')}")
                
                sql_data = sql_response.get("data", {})
                sql_status = sql_data.get("status", "error")
                
                # Log what SQL service returned
                logger.info(f"=== SPARKSAI-SQL SERVICE RETURNED ===")
                logger.info(f"Status: {sql_status}")
                logger.info(f"SQL: {sql_data.get('sql', 'N/A')}")
                logger.info(f"Results count: {len(sql_data.get('results', []))}")
                if sql_data.get('error'):
                    logger.info(f"Error: {sql_data.get('error')}")
                logger.info(f"=== END SPARKSAI-SQL SERVICE RETURNED ===")
                
                # Clean question for formatting (remove trigger from start)
                clean_question_for_sql = request.question
                if clean_question_for_sql.startswith(config.SQL_AI_TRIGGER):
                    clean_question_for_sql = clean_question_for_sql[1:].strip()
                
                # Get formatted_for_llm from response (already formatted by SQL service)
                sql_formatted = sql_data.get("formatted_for_llm")
                if not sql_formatted:
                    # Fallback: format manually if not provided
                    logger.warning("formatted_for_llm not in response, formatting manually")
                    sql = sql_data.get('sql', 'N/A')
                    results = sql_data.get('results', [])
                    row_count = len(results)
                    results_json = json.dumps(results[:100], indent=2, default=str) if results else "[]"
                    sql_formatted = f"""=== DATABASE QUERY ===
Question: {clean_question_for_sql}
Answer:
SQL Query:
{sql}

Results ({row_count} row{'s' if row_count != 1 else ''}):
{results_json}
=== END DATABASE QUERY ==="""
                
                # Store SQL data for chat history
                sql_was_triggered = True
                sql_formatted_for_history = sql_formatted
                
                # CRITICAL FIX: Inject SQL results into history_json BEFORE calling LLM
                # The LLM service processes history_json correctly, but doesn't process conversation_context correctly
                # So we add SQL data as a user message in history so LLM sees it immediately
                # Format: Make it clear this is the answer to the upcoming question
                history_json.setdefault('messages', [])
                history_json['messages'].append({
                    'role': 'user',
                    'content': f"Here is the database query result that answers the question '{clean_question_for_sql}':\n\n{sql_formatted}"
                })
                logger.info(f"Injected SQL results into history_json (status: {sql_status})")
                
            except Exception as e:
                logger.warning(f"SQL service processing failed: {e}")
                # Continue without SQL data - don't block the chat flow
                # Inject error message into history_json so LLM can see it
                sql_error_msg = f"=== SQL Service Error ===\nFailed to process database query: {str(e)}\n=== End SQL Service Error ==="
                history_json.setdefault('messages', [])
                history_json['messages'].append({
                    'role': 'user',
                    'content': sql_error_msg
                })
                logger.info(f"Injected SQL error message into history_json")

        # 3. Call LLM service
        # Remove "!" trigger from question if present before sending to LLM
        question_to_send = request.question
        if request.question and request.question.startswith(config.SQL_AI_TRIGGER):
            # Remove trigger from start
            question_to_send = request.question[1:].strip()
            logger.info(f"Cleaned question for LLM (removed trigger): '{question_to_send}'")
        
        # HYBRID APPROACH: Combine SQL results with question parameter for immediate LLM visibility
        # This ensures LLM sees SQL results both in the question parameter AND in history_json
        if sql_was_triggered and sql_formatted_for_history:
            # Combine question + SQL results so LLM sees them together
            question_to_send = f"{question_to_send}\n\n=== DATABASE QUERY RESULTS ===\n{sql_formatted_for_history}\n=== END DATABASE QUERY RESULTS ==="
            logger.info(f"Combined SQL results with question for LLM (question length: {len(question_to_send)} chars)")
        
        # Check for issue suggestion request in follow-up questions (before building conversation_context_for_llm)
        issue_details_context = None
        if not is_initial_call and not sql_was_attempted:
            try:
                issue_details_context = handle_issue_suggestion_request(
                    request.question,
                    conversation_id,
                    conn
                )
                if issue_details_context:
                    logger.info("Issue details context added for suggestion request")
            except Exception as e:
                logger.error(f"Error handling issue suggestion request: {e}")
                # Continue without issue details - don't block chat
        
        # Determine conversation_context parameter
        # REVERTED TO SIMPLE APPROACH: Always send conversation_context (except for SQL calls)
        # This matches the original working behavior before recent changes
        # SQL calls: Don't send conversation_context (SQL results are in question parameter)
        if sql_was_attempted:
            # SQL calls: don't send conversation_context parameter
            # SQL results are already combined with question parameter
            conversation_context_for_llm = None
        else:
            # All other calls (initial and follow-up): send conversation_context
            if is_initial_call:
                # Initial call: send full conversation_context (with content_intro prompt)
                conversation_context_for_llm = conversation_context
            else:
                # Follow-up: check if this is an epic refinement request
                # Epic refinement is always a follow-up, and we need to send the template on every call
                if conversation_context and "=== EPIC INFORMATION FOR REFINEMENT ===" in conversation_context:
                    # Epic refinement: use fresh context (includes template) on every follow-up call
                    conversation_context_for_llm = conversation_context
                    logger.info(f"Using fresh epic refinement context for follow-up (includes template, length: {len(conversation_context)} chars)")
                else:
                    # Other follow-ups: send data-only version (without content_intro prompt)
                    # This gives LLM access to data without confusing prompt instructions
                    data_only = history_json.get('initial_request_data_only')
                    if data_only:
                        conversation_context_for_llm = data_only
                        logger.info(f"Using data-only context for follow-up (length: {len(data_only)} chars, no prompt)")
                    else:
                        # Fallback: use full context if data-only not available
                        stored_context = history_json.get('initial_request_conversation_context')
                        conversation_context_for_llm = stored_context if stored_context else conversation_context
                        logger.warning("Data-only context not found, using full context as fallback")
        
        # Add issue details to conversation_context if available (for issue suggestion)
        if issue_details_context:
            if conversation_context_for_llm:
                conversation_context_for_llm = conversation_context_for_llm + "\n\n" + issue_details_context
            else:
                conversation_context_for_llm = issue_details_context
        
        # DIAGNOSTIC LOGGING: Verify what's in history_json for follow-up calls
        if not is_initial_call:
            logger.info("=" * 80)
            logger.info("FOLLOW-UP CALL DIAGNOSTICS")
            logger.info("=" * 80)
            messages = history_json.get('messages', [])
            logger.info(f"Total messages in history_json: {len(messages)}")
            logger.info(f"SQL was attempted: {sql_was_attempted}")
            logger.info(f"conversation_context_for_llm: {'SET' if conversation_context_for_llm else 'None'}")
            
            # Log first few messages to see if context is there
            if messages:
                logger.info("First 3 messages in history_json['messages']:")
                for i, msg in enumerate(messages[:3]):
                    role = msg.get('role', 'unknown')
                    content_preview = msg.get('content', '')[:200] if msg.get('content') else '(empty)'
                    logger.info(f"  [{i}] role={role}, content_length={len(msg.get('content', ''))}, preview={content_preview}...")
            
            # Check if initial context is stored
            stored_context = history_json.get('initial_request_conversation_context')
            if stored_context:
                logger.info(f"initial_request_conversation_context found: length={len(stored_context)} chars")
                logger.info(f"  Preview: {stored_context[:200]}...")
            else:
                logger.warning("initial_request_conversation_context NOT found in history_json")
            
            logger.info("=" * 80)
        
        # DIAGNOSTIC LOGGING: Show what we're sending to LLM service
        logger.info("=" * 80)
        logger.info("SENDING TO LLM SERVICE")
        logger.info("=" * 80)
        logger.info(f"question length: {len(question_to_send)} chars")
        logger.info(f"question preview: {question_to_send[:200]}...")
        logger.info(f"conversation_context: {'SET' if conversation_context_for_llm else 'None'}")
        if conversation_context_for_llm:
            logger.info(f"conversation_context length: {len(conversation_context_for_llm)} chars")
            logger.info(f"conversation_context preview: {conversation_context_for_llm[:200]}...")
        logger.info(f"history_json['messages'] count: {len(history_json.get('messages', []))}")
        logger.info(f"system_message: {'SET' if system_message else 'None'}")
        logger.info("=" * 80)
        
        llm_response = await call_llm_service(
            conversation_id=conversation_id,
            question=question_to_send,
            history_json=history_json,
            user_id=request.user_id,
            selected_team=request.selected_team,
            selected_pi=request.selected_pi,
            chat_type=chat_type_str,
            conversation_context=conversation_context_for_llm,
            system_message=system_message
        )
        
        if not llm_response.get("success"):
            raise HTTPException(
                status_code=502,
                detail=f"LLM service returned error: {llm_response.get('detail', 'Unknown error')}"
            )
        
        ai_response = llm_response.get("response", "")
        
        # Log LLM response details
        logger.info("=" * 80)
        logger.info("LLM RESPONSE RECEIVED")
        logger.info("=" * 80)
        logger.info(f"Response length: {len(ai_response)} chars")
        if ai_response:
            preview_length = min(500, len(ai_response))
            logger.info(f"Response preview (first {preview_length} chars): {ai_response[:preview_length]}...")
            if len(ai_response) > preview_length:
                logger.info(f"... (truncated, {len(ai_response) - preview_length} more chars)")
        else:
            logger.warning("LLM response is empty")
        logger.info(f"Provider: {llm_response.get('provider', 'N/A')}")
        logger.info(f"Model: {llm_response.get('model', 'N/A')}")
        logger.info(f"Tokens used: {llm_response.get('tokens_used', 'N/A')}")
        logger.info("=" * 80)
        
        # 3.5. Extract and save issue key from LLM response (always extract from final LLM answer)
        extracted_issue_key = extract_issue_key_from_response(ai_response)
        if extracted_issue_key:
            # Update chat_history table with issue_key
            update_issue_key_query = text(f"""
                UPDATE {config.CHAT_HISTORY_TABLE}
                SET issue_key = :issue_key
                WHERE id = :conversation_id
            """)
            
            try:
                conn.execute(update_issue_key_query, {
                    "issue_key": extracted_issue_key,
                    "conversation_id": int(conversation_id)
                })
                conn.commit()
                logger.info(f"Updated issue_key in chat_history: {extracted_issue_key}")
            except Exception as e:
                logger.error(f"Error updating issue_key in chat_history: {e}")
                # Don't fail the request if issue_key update fails
                conn.rollback()
        
        # 4. Update chat history with new exchange
        # Remove "!" trigger from question before saving to history
        clean_question_for_history = request.question
        if request.question and request.question.startswith(config.SQL_AI_TRIGGER):
            # Remove trigger from start
            clean_question_for_history = request.question[1:].strip()
        
        # If SQL was triggered, append SQL results to user message
        if sql_was_triggered and sql_formatted_for_history:
            # Combine cleaned question + SQL results
            user_message_with_sql = f"{clean_question_for_history}\n\n{sql_formatted_for_history}"
            user_message_to_save = user_message_with_sql
            logger.info(f"Appending SQL results to chat history (question length: {len(clean_question_for_history)}, SQL data length: {len(sql_formatted_for_history)})")
        else:
            user_message_to_save = clean_question_for_history
        
        update_chat_history(
            conversation_id=conversation_id,
            user_message=user_message_to_save,
            assistant_response=ai_response,
            conn=conn
        )
        
        # 5. Prepare response
        input_params = {
            "conversation_id": conversation_id,
            "question": request.question
        }
        if request.user_id:
            input_params["user_id"] = request.user_id
        if request.prompt_name:
            input_params["prompt_name"] = request.prompt_name
        if request.selected_team:
            input_params["selected_team"] = request.selected_team
        if request.selected_pi:
            input_params["selected_pi"] = request.selected_pi
        if chat_type_str:
            input_params["chat_type"] = chat_type_str
        if request.recommendation_id:
            input_params["recommendation_id"] = request.recommendation_id
        if request.insights_id:
            input_params["insights_id"] = request.insights_id
        
        logger.info(f"AI chat request processed successfully - Conversation ID: {conversation_id}")
        
        tokens_used = llm_response.get("tokens_used")
        
        # Prepare response data
        response_data = {
            "success": True,
            "data": {
                "conversation_id": conversation_id,
                "response": ai_response,
                "input_parameters": input_params,
                "provider": llm_response.get("provider"),
                "model": llm_response.get("model"),
                "tokens_used": tokens_used
            },
            "message": "AI chat response generated successfully"
        }
        
        # DEBUG: Log response including conversation_id
        response_debug = {
            "conversation_id": conversation_id,
            "success": response_data["success"],
            "response_length": len(ai_response) if ai_response else 0,
            "provider": llm_response.get("provider"),
            "model": llm_response.get("model"),
            "tokens_used": tokens_used
        }
        
        # Add response headers for gateway audit logging
        headers = {}
        
        # Extract tokens_used value (handle dict or int)
        tokens_value = None
        if isinstance(tokens_used, dict):
            tokens_value = tokens_used.get("total_tokens")
        elif isinstance(tokens_used, int):
            tokens_value = tokens_used
        
        if tokens_value is not None:
            headers["SA-Token"] = str(tokens_value)
        
        if request.insights_id:
            headers["SA-InsightID"] = str(request.insights_id)
        
        if conversation_id:
            headers["SA-ChatID"] = str(conversation_id)
        
        return JSONResponse(content=response_data, headers=headers)
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error processing AI chat request: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process AI chat request: {str(e)}"
        )
