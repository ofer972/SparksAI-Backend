"""
AI Chat Service - REST API endpoints for AI chat interactions.

This service provides endpoints for AI chat functionality that will eventually
connect to an LLM service. For now, it returns mock responses.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection
from typing import Optional
from enum import Enum
import logging
import random
from database_connection import get_db_connection
import config

logger = logging.getLogger(__name__)

ai_chat_router = APIRouter()


class ChatType(str, Enum):
    """Enumeration of chat type options"""
    PI_DASHBOARD = "PI_dashboard"
    TEAM_DASHBOARD = "Team_dashboard"
    DIRECT_CHAT = "Direct_chat"
    TEAM_INSIGHTS = "Team_insights"
    PI_INSIGHTS = "PI_insights"
    RECOMMENDATION_REASON = "recommendation_reason"


class AIChatRequest(BaseModel):
    """Request model for AI chat endpoint"""
    conversation_id: Optional[str] = Field(None, description="Conversation ID for tracking chat sessions")
    question: Optional[str] = Field(None, description="The user's question")
    username: Optional[str] = Field(None, description="Username who requested the chat")
    selected_team: Optional[str] = Field(None, description="Selected team name")
    selected_pi: Optional[str] = Field(None, description="Selected PI name")
    chat_type: Optional[ChatType] = Field(None, description="Type of chat")
    recommendation_id: Optional[str] = Field(None, description="ID of recommendation")
    insights_id: Optional[str] = Field(None, description="ID of insights")

    class Config:
        use_enum_values = True


@ai_chat_router.post("/ai-chat")
async def ai_chat(
    request: AIChatRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    AI Chat endpoint that will eventually connect to LLM service.
    For now, returns a mock response with all input parameters echoed back.
    
    Args:
        request: AIChatRequest containing all optional parameters
        
    Returns:
        JSON response with mock AI answer and all input parameters
    """
    try:
        # Generate conversation_id if empty or None
        conversation_id = request.conversation_id
        if not conversation_id or (isinstance(conversation_id, str) and conversation_id.strip() == ""):
            # Generate a random number as conversation_id
            conversation_id = str(random.randint(100000, 999999))
            logger.info(f"Generated new conversation_id: {conversation_id}")
        
        # Prepare input parameters dictionary (only include provided fields)
        input_params = {}
        input_params["conversation_id"] = conversation_id
        if request.question is not None:
            input_params["question"] = request.question
        if request.username is not None:
            input_params["username"] = request.username
        if request.selected_team is not None:
            input_params["selected_team"] = request.selected_team
        if request.selected_pi is not None:
            input_params["selected_pi"] = request.selected_pi
        if request.chat_type is not None:
            input_params["chat_type"] = request.chat_type
        if request.recommendation_id is not None:
            input_params["recommendation_id"] = request.recommendation_id
        if request.insights_id is not None:
            input_params["insights_id"] = request.insights_id
        
        # Generate mock AI response based on chat type and question
        mock_response = _generate_mock_response(
            chat_type=request.chat_type,
            question=request.question,
            selected_team=request.selected_team,
            selected_pi=request.selected_pi
        )
        
        logger.info(f"AI chat request processed - Conversation ID: {conversation_id}, Chat Type: {request.chat_type}")
        
        return {
            "success": True,
            "data": {
                "conversation_id": conversation_id,
                "response": mock_response,
                "input_parameters": input_params
            },
            "message": "AI chat response generated successfully"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error processing AI chat request: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process AI chat request: {str(e)}"
        )


def _generate_mock_response(
    chat_type: Optional[ChatType] = None,
    question: Optional[str] = None,
    selected_team: Optional[str] = None,
    selected_pi: Optional[str] = None
) -> str:
    """
    Generate a mock AI response based on the chat type and context.
    
    Args:
        chat_type: Type of chat conversation
        question: User's question
        selected_team: Selected team name
        selected_pi: Selected PI name
        
    Returns:
        Mock AI response string
    """
    # Base response
    base_response = "This is a mock AI response. The LLM service integration will be implemented soon."
    
    # Context-specific responses based on chat type
    if chat_type:
        if chat_type == ChatType.PI_DASHBOARD:
            context = f"regarding the PI Dashboard"
            if selected_pi:
                context += f" for {selected_pi}"
            return f"Based on the PI Dashboard analysis{(' for ' + selected_pi) if selected_pi else ''}, " \
                   f"here are the key insights. {base_response}"
        
        elif chat_type == ChatType.TEAM_DASHBOARD:
            context = f"regarding the Team Dashboard"
            if selected_team:
                context += f" for {selected_team}"
            return f"Based on the Team Dashboard analysis{(' for ' + selected_team) if selected_team else ''}, " \
                   f"here are the team metrics and performance indicators. {base_response}"
        
        elif chat_type == ChatType.DIRECT_CHAT:
            return f"In response to your question: '{question}' {base_response}" if question else \
                   f"Direct chat response. {base_response}"
        
        elif chat_type == ChatType.TEAM_INSIGHTS:
            context = f"Team insights"
            if selected_team:
                context += f" for {selected_team}"
            return f"Based on the team insights{(' for ' + selected_team) if selected_team else ''}, " \
                   f"here are the key findings and recommendations. {base_response}"
        
        elif chat_type == ChatType.PI_INSIGHTS:
            context = f"PI insights"
            if selected_pi:
                context += f" for {selected_pi}"
            return f"Based on the PI insights{(' for ' + selected_pi) if selected_pi else ''}, " \
                   f"here are the performance indicators and trends. {base_response}"
        
        elif chat_type == ChatType.RECOMMENDATION_REASON:
            return f"Here's the reasoning behind the recommendation. " \
                   f"Based on the analysis, this recommendation was generated to address specific metrics. {base_response}"
    
    # Default response if no chat type specified
    if question:
        return f"Regarding your question: '{question}', {base_response}"
    
    return base_response

