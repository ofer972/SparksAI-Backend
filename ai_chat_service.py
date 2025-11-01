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
from database_connection import get_db_connection
from database_general import get_team_ai_card_by_id, get_recommendation_by_id, get_prompt_by_email_and_name, get_pi_ai_card_by_id
import config

logger = logging.getLogger(__name__)

ai_chat_router = APIRouter()

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
    else:
        logger.info("No conversation context provided")
    if system_message:
        logger.info(f"System message included: {len(system_message)} chars")
    else:
        logger.info("No system message provided")
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
                # Extract full_information from card
                full_information = card.get('full_information', '')
                if not full_information:
                    logger.warning(f"Team AI card {insights_id_int} has empty full_information field")
                    conversation_context = None
                else:
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
                        content_intro = "This is previous discussion we have in a different chat. Read this information as I want to ask follow up questions."
                    conversation_context = content_intro + '\n\n' + full_information
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
                full_information = card.get('full_information', '')
                if not full_information:
                    logger.warning(f"PI AI card {insights_id_int} has empty full_information field")
                    conversation_context = None
                else:
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
                        content_intro = "This is previous discussion we have in a different chat. Read this information as I want to ask follow up questions."
                    conversation_context = content_intro + '\n\n' + full_information
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
                # Extract action_text and full_information from recommendation
                action_text = recommendation.get('action_text', '')
                full_information = recommendation.get('full_information', '')
                content_prompt_name = "Recommendation_reason-Content"
                context_built = False
                try:
                    content_prompt = get_prompt_by_email_and_name(
                        email_address='admin',
                        prompt_name=content_prompt_name,
                        conn=conn,
                        active=True
                    )
                    if content_prompt and content_prompt.get('prompt_description'):
                        # Use DB prompt + action_text + full_information as context
                        conversation_context = (
                            f"{content_prompt['prompt_description']}\n\"{action_text}\"\n\n{full_information}"
                        )
                        logger.info(f"Using DB content prompt for Recommendation_reason-Content (length: {len(conversation_context)} chars)")
                        context_built = True
                    else:
                        logger.info(f"No active DB content prompt found for Recommendation_reason-Content, using fallback context.")
                except Exception as e:
                    logger.warning(f"Failed to fetch DB content prompt for Recommendation_reason-Content: {e}. Using fallback.")
                if not context_built:
                    # Fallback: the original multi-part string
                    conversation_context = (
                        "This is previous discussion we have in a different chat. Read this information as I want to ask follow up questions.\n\n"
                        f"Please explain the reason for recommendation --> \"{action_text}\"\n\n"
                        "Explain in a brief and short description the reason for recommendation\n\n"
                        f"{full_information}"
                    )
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

        # 3. Call LLM service
        llm_response = await call_llm_service(
            conversation_id=conversation_id,
            question=request.question,
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
        update_chat_history(
            conversation_id=conversation_id,
            user_message=request.question,
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
