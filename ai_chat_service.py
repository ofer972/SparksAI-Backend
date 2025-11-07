"""
AI Chat Service - REST API endpoints for AI chat interactions.

This service provides endpoints for AI chat functionality that connects
to the LLM service for OpenAI/Gemini API calls.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection
from sqlalchemy import text
from typing import Optional, Dict, Any, Tuple
from enum import Enum
import logging
import json
import httpx
import socket
import os
from datetime import datetime, date
from pathlib import Path
from database_connection import get_db_connection
from database_general import get_team_ai_card_by_id, get_recommendation_by_id, get_prompt_by_email_and_name, get_pi_ai_card_by_id, get_formatted_job_data_for_llm_followup_insight, get_formatted_job_data_for_llm_followup_recommendation
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
from vanna_service import (
    call_vanna_ai,
    convert_history_to_vanna_format,
    format_vanna_results_for_llm,
    VANNA_AI_TRIGGER
)

logger = logging.getLogger(__name__)

ai_chat_router = APIRouter()


def is_localhost():
    """
    Detect if running on localhost by checking hostname patterns.
    Returns True if localhost, False if Railway/production.
    """
    try:
        hostname = socket.gethostname().lower()
        
        # Railway/production hostnames typically contain these patterns
        production_keywords = [
            'railway',
            'railway-app',
            'replica',
            'prod',
            'production'
        ]
        
        # If hostname contains production keywords, it's NOT localhost
        for keyword in production_keywords:
            if keyword in hostname:
                return False
        
        # Otherwise, assume it's localhost
        return True
    except Exception:
        # If we can't determine, default to False (safer - won't write files in production)
        return False


def write_llm_context_debug_file(
    chat_type: str,
    conversation_id: str,
    conversation_context: Optional[str],
    system_message: Optional[str],
    request: Any,
    history_json: Optional[Dict[str, Any]] = None
) -> None:
    """
    Generic function to write LLM context to debug file (only on localhost).
    
    Args:
        chat_type: Chat type string (e.g., "Team_insights", "Team_dashboard")
        conversation_id: Conversation ID
        conversation_context: Conversation context string
        system_message: System message string
        request: AIChatRequest object with all request details
    """
    if not is_localhost():
        logger.debug(f"Skipping debug file write for {chat_type} (not running on localhost)")
        return
    
    try:
        # Build entity identifier for filename
        entity_id = None
        if request.insights_id:
            entity_id = f"_insights{request.insights_id}"
        elif request.recommendation_id:
            entity_id = f"_rec{request.recommendation_id}"
        elif request.selected_team and chat_type == "Team_dashboard":
            # For Team_dashboard, use team name (sanitized for filename)
            team_sanitized = request.selected_team.replace(' ', '_').replace('/', '_')[:30]
            entity_id = f"_team{team_sanitized}"
        
        entity_suffix = entity_id if entity_id else ""
        filename = f"llm_context_debug_{chat_type}_conv{conversation_id}{entity_suffix}.txt"
        file_path = Path(filename)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write(f"LLM CONTEXT DEBUG - {chat_type}\n")
            f.write(f"Timestamp: {datetime.now().isoformat()}\n")
            f.write(f"Conversation ID: {conversation_id}\n")
            f.write(f"User ID: {request.user_id}\n")
            f.write(f"Team: {request.selected_team}\n")
            f.write(f"PI: {request.selected_pi}\n")
            if request.insights_id:
                f.write(f"Insights ID: {request.insights_id}\n")
            if request.recommendation_id:
                f.write(f"Recommendation ID: {request.recommendation_id}\n")
            f.write("=" * 80 + "\n\n")
            
            f.write("SYSTEM MESSAGE:\n")
            f.write("-" * 80 + "\n")
            f.write(system_message or "(None)" + "\n\n")
            
            f.write("CONVERSATION CONTEXT:\n")
            f.write("-" * 80 + "\n")
            f.write(conversation_context or "(None)" + "\n\n")
            
            f.write("USER QUESTION:\n")
            f.write("-" * 80 + "\n")
            f.write(request.question or "(Empty)" + "\n\n")
            
            # Include chat history if available (last 5 messages)
            if history_json:
                f.write("CHAT HISTORY (Last 5 messages):\n")
                f.write("-" * 80 + "\n")
                messages = history_json.get('messages', [])
                for msg in messages[-5:]:
                    role = msg.get('role', 'unknown')
                    content = msg.get('content', '')
                    f.write(f"[{role}]: {content[:200]}...\n")
                f.write("\n" + "=" * 80 + "\n")
        
        logger.info(f"TEMPORARY DEBUG: Wrote LLM context to file: {filename}")
    except Exception as e:
        logger.warning(f"Failed to write LLM context debug file for {chat_type}: {e}")

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
                active=True
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
                active=True
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
                closed_sprints = get_closed_sprints_data_db(team_name, months=3, conn=conn)
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
                        team_name, 
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
            
            # Combine with prompt text: prompt -> today's date -> formatted data
            if formatted_data:
                if conversation_context:
                    conversation_context = conversation_context + '\n\n' + today_date_markdown + '\n\n' + formatted_data
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
                active=True
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
                active=True
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
            
            # 3. Fetch scope changes (normalize pi_name to list for quarters)
            scope_data = []
            try:
                # Normalize to list (same logic as endpoint)
                quarters_list = [pi_name]  # Single PI/quarter for dashboard
                scope_data = fetch_scope_changes_data(
                    quarters=quarters_list,
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
            
            # Combine with prompt text: prompt -> today's date -> formatted data
            if formatted_data:
                if conversation_context:
                    conversation_context = conversation_context + '\n\n' + today_date_markdown + '\n\n' + formatted_data
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
            # Extract month and other fields from the dictionary
            report_month = month_data.get('report_month', 'Unknown')
            created = month_data.get('created', 0)
            resolved = month_data.get('resolved', 0)
            open_count = month_data.get('open', 0) or month_data.get('cumulative_open', 0)
            
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
    
    payload = {
        "conversation_id": conversation_id,
        "question": question,
        "history_json": history_json,
        "username": user_id,
        "selected_team": selected_team,
        "selected_pi": selected_pi,
        "chat_type": chat_type,
        "conversation_context": conversation_context,
        "system_message": system_message
    }
    
    logger.info(f"Calling LLM service: {llm_service_url}")
    if conversation_context:
        logger.info(f"Conversation context included: {len(conversation_context)} chars")
        logger.debug(f"Conversation context preview: {conversation_context[:200]}...")
        # Show last 200 characters of conversation context
        if len(conversation_context) > 200:
            last_200 = conversation_context[-200:]
            logger.info(f"This is the end of the this is the last 200 of characters of the message sent to the LLM: {last_200}")
        else:
            logger.info(f"Full conversation context (less than 200 chars): {conversation_context}")
    else:
        logger.info("No conversation context provided")
    if system_message:
        logger.info(f"System message included: {len(system_message)} chars")
    else:
        logger.info("No system message provided")
    logger.info(f"Question being sent to LLM: {question}")
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


@ai_chat_router.post("/ai-chat")
async def ai_chat(
    request: AIChatRequest,
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
        # Allow empty questions; log for visibility
        if not request.question or not request.question.strip():
            logger.info("Empty question received; continuing without user question")
        
        # DEBUG: Log incoming parameters
        logger.info("=" * 60)
        logger.info("AI CHAT SERVICE - Incoming Request")
        logger.info("=" * 60)
        logger.info(f"  conversation_id: {request.conversation_id}")
        logger.info(f"  question: {request.question}")
        logger.info(f"  user_id: {request.user_id}")
        logger.info(f"  prompt_name: {request.prompt_name}")
        logger.info(f"  selected_team: {request.selected_team}")
        logger.info(f"  selected_pi: {request.selected_pi}")
        logger.info(f"  chat_type: {request.chat_type}")
        logger.info("=" * 60)
        
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
        
        # 2. Handle Team_insights chat type - fetch card data and build context
        conversation_context = None
        if chat_type_str == "Team_insights":
            # Validate insights_id is provided
            if not request.insights_id:
                raise HTTPException(
                    status_code=400,
                    detail="insights_id is required when chat_type is Team_insights"
                )
            try:
                # Convert insights_id to int
                insights_id_int = int(request.insights_id)
                # Fetch team AI card using shared helper function
                logger.info(f"Fetching team AI card with ID: {insights_id_int}")
                card = get_team_ai_card_by_id(insights_id_int, conn)
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
                        active=True
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
                
                # Extract description from card
                description = card.get('description', '')
                
                # Build conversation_context: content_intro + description + input_sent
                conversation_context = content_intro + '\n\n' + description + '\n\n' + formatted_job_data
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
        
        # 2.3. Handle PI_insights chat type - fetch PI card and build context
        elif chat_type_str == "PI_insights":
            if not request.insights_id:
                raise HTTPException(
                    status_code=400,
                    detail="insights_id is required when chat_type is PI_insights"
                )
            try:
                insights_id_int = int(request.insights_id)
                logger.info(f"Fetching PI AI card with ID: {insights_id_int}")
                card = get_pi_ai_card_by_id(insights_id_int, conn)
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
                        active=True
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
                
                # Extract description from card
                description = card.get('description', '')
                
                # Build conversation_context: content_intro + description + input_sent
                conversation_context = content_intro + '\n\n' + description + '\n\n' + formatted_job_data
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

        # 2.5. Handle Recommendation_reason chat type - fetch recommendation data and build context
        elif chat_type_str == "Recommendation_reason":
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
                        active=True
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
                
                # Build conversation_context: content_intro + action_text + input_sent
                conversation_context = content_intro + '\n\n' + action_text + '\n\n' + formatted_job_data
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
                    active=True
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

        # 2.8.5. Handle dashboard chat types (PI_dashboard, Team_dashboard)
        if chat_type_str == "Team_dashboard":
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
        try:
            is_initial_call = not history_json.get('messages')
            if is_initial_call:
                if 'initial_request_system_message' not in history_json:
                    history_json['initial_request_system_message'] = system_message
                if 'initial_request_conversation_context' not in history_json:
                    history_json['initial_request_conversation_context'] = conversation_context

                # Also seed initial messages so follow-ups carry full context
                history_json.setdefault('messages', [])
                if system_message:
                    history_json['messages'].append({
                        'role': 'system',
                        'content': system_message
                    })
                if conversation_context:
                    history_json['messages'].append({
                        'role': 'user',
                        'content': conversation_context
                    })
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

        # 2.10. TEMPORARY: Write context to file for testing (AI Chat Insights/Recommendations/Dashboard)
        # Only write debug files on localhost (not on Railway/production)
        # One file per conversation (overwrites on each request for same conversation_id)
        if chat_type_str in ["Team_insights", "PI_insights", "Recommendation_reason", "Team_dashboard", "PI_dashboard"]:
            write_llm_context_debug_file(
                chat_type=chat_type_str,
                conversation_id=conversation_id,
                conversation_context=conversation_context,
                system_message=system_message,
                request=request,
                history_json=history_json
            )

        # 2.11. Check for Vanna AI trigger and process if needed
        # Initialize variables to track Vanna data for chat history
        vanna_was_triggered = False
        vanna_formatted_for_history = None
        
        if request.question and VANNA_AI_TRIGGER in request.question:
            try:
                logger.info(f"Vanna AI trigger detected in question: {request.question[:100]}...")
                
                # Convert history to Vanna format
                vanna_history = convert_history_to_vanna_format(history_json)
                logger.info(f"Converted {len(vanna_history)} previous Vanna exchanges to history")
                
                # Call Vanna AI
                vanna_result = await call_vanna_ai(
                    question=request.question,
                    conversation_history=vanna_history if vanna_history else None,
                    conn=conn
                )
                
                # Log what Vanna returned
                logger.info(f"=== VANNA AI RETURNED ===")
                logger.info(f"Status: {vanna_result.get('status')}")
                logger.info(f"SQL: {vanna_result.get('sql', 'N/A')}")
                logger.info(f"Results count: {len(vanna_result.get('results', []))}")
                if vanna_result.get('error'):
                    logger.info(f"Error: {vanna_result.get('error')}")
                logger.info(f"=== END VANNA AI RETURNED ===")
                
                # Clean question for Vanna formatting (remove trigger)
                clean_question_for_vanna = request.question.replace(VANNA_AI_TRIGGER, "").strip()
                # Remove any remaining standalone "X" at the start
                if clean_question_for_vanna.startswith("X "):
                    clean_question_for_vanna = clean_question_for_vanna[2:].strip()
                elif clean_question_for_vanna.startswith("X"):
                    clean_question_for_vanna = clean_question_for_vanna[1:].strip()
                
                # Format Vanna results with question explicitly paired
                vanna_formatted = format_vanna_results_for_llm(vanna_result, question=clean_question_for_vanna)
                
                # Store Vanna data for chat history
                vanna_was_triggered = True
                vanna_formatted_for_history = vanna_formatted
                
                # CRITICAL FIX: Inject Vanna results into history_json BEFORE calling LLM
                # The LLM service processes history_json correctly, but doesn't process conversation_context correctly
                # So we add Vanna data as a user message in history so LLM sees it immediately
                # Format: Make it clear this is the answer to the upcoming question
                history_json.setdefault('messages', [])
                history_json['messages'].append({
                    'role': 'user',
                    'content': f"Here is the database query result that answers the question '{clean_question_for_vanna}':\n\n{vanna_formatted}"
                })
                logger.info(f"Injected Vanna results into history_json (status: {vanna_result.get('status')})")
                
                # Also add to conversation_context for backward compatibility
                if conversation_context:
                    conversation_context = conversation_context + '\n\n' + vanna_formatted
                else:
                    conversation_context = vanna_formatted
                
                logger.info(f"Vanna AI results appended to conversation context (status: {vanna_result.get('status')})")
                
            except Exception as e:
                logger.warning(f"Vanna AI processing failed: {e}")
                # Continue without Vanna data - don't block the chat flow
                # Optionally add error message to context
                vanna_error_msg = f"\n\n=== Vanna AI Error ===\nFailed to process database query: {str(e)}\n=== End Vanna AI Error ==="
                if conversation_context:
                    conversation_context = conversation_context + vanna_error_msg
                else:
                    conversation_context = vanna_error_msg

        # 3. Call LLM service
        # Remove "XXX" trigger from question if present before sending to LLM
        question_to_send = request.question
        if request.question and VANNA_AI_TRIGGER in request.question:
            # Remove all occurrences of "XXX" and clean up any remaining X's
            question_to_send = request.question.replace(VANNA_AI_TRIGGER, "").strip()
            # Remove any remaining standalone "X" at the start (in case user typed "XXXX" or "XXX X")
            if question_to_send.startswith("X "):
                question_to_send = question_to_send[2:].strip()
            elif question_to_send.startswith("X"):
                question_to_send = question_to_send[1:].strip()
            logger.info(f"Cleaned question for LLM (removed trigger): '{question_to_send}'")
        
        llm_response = await call_llm_service(
            conversation_id=conversation_id,
            question=question_to_send,
            history_json=history_json,
            user_id=request.user_id,
            selected_team=request.selected_team,
            selected_pi=request.selected_pi,
            chat_type=chat_type_str,
            conversation_context=conversation_context,
            system_message=system_message
        )
        
        if not llm_response.get("success"):
            raise HTTPException(
                status_code=502,
                detail=f"LLM service returned error: {llm_response.get('detail', 'Unknown error')}"
            )
        
        ai_response = llm_response.get("response", "")
        
        # 4. Update chat history with new exchange
        # Remove "XXX" trigger from question before saving to history
        clean_question_for_history = request.question
        if request.question and VANNA_AI_TRIGGER in request.question:
            # Remove all occurrences of "XXX" and clean up any remaining X's
            clean_question_for_history = request.question.replace(VANNA_AI_TRIGGER, "").strip()
            # Remove any remaining standalone "X" at the start (in case user typed "XXXX" or "XXX X")
            if clean_question_for_history.startswith("X "):
                clean_question_for_history = clean_question_for_history[2:].strip()
            elif clean_question_for_history.startswith("X"):
                clean_question_for_history = clean_question_for_history[1:].strip()
        
        # If Vanna was triggered, append Vanna results to user message
        if vanna_was_triggered and vanna_formatted_for_history:
            # Combine cleaned question + Vanna results
            user_message_with_vanna = f"{clean_question_for_history}\n\n{vanna_formatted_for_history}"
            user_message_to_save = user_message_with_vanna
            logger.info(f"Appending Vanna results to chat history (question length: {len(clean_question_for_history)}, Vanna data length: {len(vanna_formatted_for_history)})")
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
        
        return {
            "success": True,
            "data": {
                "conversation_id": conversation_id,
                "response": ai_response,
                "input_parameters": input_params,
                "provider": llm_response.get("provider"),
                "model": llm_response.get("model"),
                "tokens_used": llm_response.get("tokens_used")
            },
            "message": "AI chat response generated successfully"
        }
    
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
