"""
SparksAI-SQL Service Client - HTTP client for calling the SparksAI-SQL service.

This module provides functionality to call the SparksAI-SQL service's /sql/execute endpoint.
"""

import httpx
from fastapi import HTTPException
from typing import Optional, Dict, Any, List
import logging
import config

logger = logging.getLogger(__name__)


async def call_sparksai_sql_execute(
    question: str,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    include_formatted: bool = True,
    report_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Call SparksAI-SQL service /sql/execute endpoint to generate and execute SQL from natural language question.
    
    Args:
        question: Natural language question (may contain trigger "!")
        conversation_history: Previous conversation exchanges in format [{'question': str, 'sql': str, 'answer': str}]
        include_formatted: Whether to include formatted_for_llm in response (default: True)
        report_context: Optional {pi_name, team_name} to scope generated SQL to current report/dashboard.
        
    Returns:
        Dictionary with 'success', 'data' keys where 'data' contains:
        - 'sql': Generated SQL query
        - 'results': Query results as list of dicts
        - 'status': 'success' or 'error'
        - 'error': Error message if status is 'error'
        - 'formatted_for_llm': Formatted text string for LLM context (if include_formatted=True)
    """
    sql_service_url = f"{config.LLM_SQL_SERVICE_URL}/sql/execute"
    
    payload = {
        "question": question,
        "include_formatted": include_formatted
    }
    
    if conversation_history:
        payload["conversation_history"] = conversation_history
    if report_context:
        payload["report_context"] = report_context
    
    logger.info(">>> SQL CLIENT: Calling SparksAI-SQL service: %s", sql_service_url)
    logger.info(">>> SQL CLIENT: Question: %s", question[:150] + ("..." if len(question) > 150 else ""))
    logger.info(f"Conversation history: {len(conversation_history) if conversation_history else 0} exchanges")
    logger.debug(f"Payload keys: {list(payload.keys())}")
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:  # Longer timeout for SQL generation and execution
            response = await client.post(sql_service_url, json=payload)
            logger.info(">>> SQL CLIENT: Received response from SQL service, status_code=%s", response.status_code)
            response.raise_for_status()
            result = response.json()
            
            # Log response summary
            if result.get("success"):
                data = result.get("data", {})
                logger.info(f"SparksAI-SQL service returned: status={data.get('status')}, sql_length={len(data.get('sql', ''))}, results_count={len(data.get('results', []))}")
            else:
                logger.warning(f"SparksAI-SQL service returned error: {result.get('data', {}).get('error', 'Unknown error')}")
            
            return result
            
    except httpx.HTTPStatusError as e:
        logger.error(f"SparksAI-SQL service HTTP error: {e.response.status_code} - {e.response.text}")
        
        # Try to parse error response
        try:
            error_data = e.response.json()
            error_message = error_data.get("detail", {}).get("message", "Unknown error") if isinstance(error_data.get("detail"), dict) else str(error_data.get("detail", "Unknown error"))
        except:
            error_message = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
        
        raise HTTPException(
            status_code=502,
            detail={
                "code": "SQL_SERVICE_ERROR",
                "message": f"SparksAI-SQL service error: {error_message}",
                "details": {}
            }
        )
    except httpx.TimeoutException:
        logger.error("SparksAI-SQL service timeout (120s)")
        raise HTTPException(
            status_code=504,
            detail={
                "code": "SQL_SERVICE_TIMEOUT",
                "message": "SparksAI-SQL service request timed out",
                "details": {}
            }
        )
    except httpx.HTTPError as e:
        logger.error(f"HTTP error calling SparksAI-SQL service: {e}")
        raise HTTPException(
            status_code=502,
            detail={
                "code": "SQL_SERVICE_CONNECTION_ERROR",
                "message": f"Failed to connect to SparksAI-SQL service: {str(e)}",
                "details": {}
            }
        )
    except Exception as e:
        logger.error(f"Error calling SparksAI-SQL service: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"Failed to call SparksAI-SQL service: {str(e)}",
                "details": {}
            }
        )


