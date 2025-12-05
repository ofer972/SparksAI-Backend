"""
Agent LLM Service - REST API endpoint for agent-to-LLM communication.

This service provides a dedicated endpoint for agents to communicate
with the LLM service using pre-prepared prompts.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import logging
import httpx
import config

logger = logging.getLogger(__name__)

agent_llm_router = APIRouter()


class AgentLLMProcessRequest(BaseModel):
    """Request model for agent LLM process endpoint"""
    prompt: str = Field(..., description="Complete formatted prompt prepared by agent")
    job_type: str = Field(..., description="Type of job (any string value)")
    system_prompt: Optional[str] = Field(None, description="System prompt for AI behavior/context")
    job_id: Optional[int] = Field(None, description="Job ID for logging/tracking")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional context for logging")


async def call_llm_service_process_single(
    prompt: str,
    system_prompt: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Call LLM service /processSingle endpoint with prompt and optional system prompt.
    
    Args:
        prompt: Complete formatted prompt
        system_prompt: Optional system prompt for AI behavior
        metadata: Optional metadata for logging
        
    Returns:
        LLM service response dict
    """
    llm_service_url = f"{config.LLM_SERVICE_URL}/processSingle"
    
    payload = {
        "prompt": prompt,
    }
    
    if system_prompt:
        payload["system_prompt"] = system_prompt
    if metadata:
        payload["metadata"] = metadata
    
    logger.info(f"Calling LLM service processSingle endpoint: {llm_service_url}")
    logger.info(f"Prompt length: {len(prompt)} chars")
    if system_prompt:
        logger.info(f"System prompt length: {len(system_prompt)} chars")
    else:
        logger.info("No system prompt provided")
    logger.debug(f"Payload keys: {list(payload.keys())}")
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:  # Longer timeout for agent jobs
            response = await client.post(llm_service_url, json=payload)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"LLM service HTTP error: {e.response.status_code} - {e.response.text}")
        
        # Try to parse error response
        try:
            error_data = e.response.json()
            error_code = error_data.get("error", {}).get("code", "LLM_ERROR")
            error_message = error_data.get("error", {}).get("message", "Unknown error")
        except:
            error_code = "LLM_ERROR"
            error_message = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
        
        raise HTTPException(
            status_code=502,
            detail={
                "code": error_code,
                "message": error_message,
                "details": {}
            }
        )
    except httpx.TimeoutException:
        logger.error("LLM service timeout (120s)")
        raise HTTPException(
            status_code=504,
            detail={
                "code": "TIMEOUT",
                "message": "LLM service request timed out",
                "details": {}
            }
        )
    except httpx.HTTPError as e:
        logger.error(f"HTTP error calling LLM service: {e}")
        raise HTTPException(
            status_code=502,
            detail={
                "code": "LLM_CONNECTION_ERROR",
                "message": f"Failed to connect to LLM service: {str(e)}",
                "details": {}
            }
        )
    except Exception as e:
        logger.error(f"Error calling LLM service: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"Failed to call LLM service: {str(e)}",
                "details": {}
            }
        )


@agent_llm_router.post("/agent-llm-process")
async def agent_llm_process(request: AgentLLMProcessRequest):
    """
    Process agent prompt through LLM service.
    
    Agent prepares all data and prompts client-side. This endpoint
    only handles LLM provider communication.
    
    Args:
        request: AgentLLMProcessRequest with prompt, system_prompt, and options
        
    Returns:
        JSON response with LLM result
    """
    try:
        logger.info("=" * 60)
        logger.info("AGENT LLM PROCESS - Incoming Request")
        logger.info("=" * 60)
        logger.info(f"  job_type: {request.job_type}")
        logger.info(f"  job_id: {request.job_id}")
        logger.info(f"  prompt_length: {len(request.prompt)} chars")
        logger.info(f"  system_prompt_length: {len(request.system_prompt) if request.system_prompt else 0} chars")
        logger.info("=" * 60)
        
        # Validate prompt is not empty
        if not request.prompt or not request.prompt.strip():
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "INVALID_REQUEST",
                    "message": "Prompt cannot be empty",
                    "details": {}
                }
            )
        
        # Prepare metadata for LLM service
        llm_metadata = {
            **(request.metadata or {}),
            "job_type": request.job_type,
        }
        if request.job_id is not None:
            llm_metadata["job_id"] = request.job_id
        
        # Call LLM service
        llm_response = await call_llm_service_process_single(
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            metadata=llm_metadata
        )
        
        # Validate response structure
        if not llm_response.get("success"):
            error_data = llm_response.get("error", {})
            error_code = error_data.get("code", "LLM_ERROR")
            error_message = error_data.get("message", "Unknown error")
            error_details = error_data.get("details", {})
            
            raise HTTPException(
                status_code=502,
                detail={
                    "code": error_code,
                    "message": error_message,
                    "details": error_details
                }
            )
        
        # Extract response data
        response_data = llm_response.get("data", {})
        
        logger.info(f"Agent LLM process completed successfully - Job ID: {request.job_id}")
        logger.info(f"  Model used: {response_data.get('model_used', 'N/A')}")
        logger.info(f"  Provider: {response_data.get('provider', 'N/A')}")
        logger.info(f"  Response length: {len(response_data.get('response', ''))} chars")
        
        return {
            "success": True,
            "data": response_data
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing agent LLM request: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"Failed to process agent LLM request: {str(e)}",
                "details": {}
            }
        )

