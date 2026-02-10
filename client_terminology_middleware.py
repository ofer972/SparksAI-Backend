"""
Client-facing terminology replacement middleware.

This module provides middleware to replace internal terminology (PI) with
client-facing terminology (Quarter) in JSON responses sent to clients.

IMPORTANT: This only replaces in user-facing text fields, NOT in data fields
like pi_name, pi, etc. to avoid corrupting actual data values.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from dotenv import load_dotenv

# Load environment variables from .env file (for local development)
load_dotenv()

logger = logging.getLogger(__name__)

# Read replacement term from environment variable
REPLACEMENT_TERM = os.getenv("PI_GLOBAL_TERMINOLOGY_REPLACEMENT")

# Enable replacement only if environment variable exists
ENABLE_TERMINOLOGY_REPLACEMENT = REPLACEMENT_TERM is not None

# Debug logging
if ENABLE_TERMINOLOGY_REPLACEMENT:
    logger.info(f"✅ Terminology replacement ENABLED: 'PI' → '{REPLACEMENT_TERM}', 'PIs' → '{REPLACEMENT_TERM}s'")
else:
    logger.info("❌ Terminology replacement DISABLED: PI_GLOBAL_TERMINOLOGY_REPLACEMENT environment variable not set")

# Fields to NEVER replace (these contain actual data values).
# Uses substring matching: any field name containing one of these strings is excluded.
# This protects both specific PI data fields AND broad categories like URLs/tokens/secrets.
EXCLUDED_DATA_FIELDS = {
    # PI data fields
    'pi', 'pi_name', 'pi_names', 'pi_id', 'pi_key',
    'quarter_pi', 'quarter_pi_of_epic', 'pi_value',
    'selected_pi', 'pi_filter', 'pi_parameter',
    'job_type', 'insight_category_name', 'insight_categories',
    # Pattern-based protections: any field whose name contains these substrings is excluded.
    # Note: 'api' is already covered by the 'pi' entry above (since 'api' contains 'pi').
    'url',        # Protects all URL fields (jira_url, closed_sprint_url, active_sprint_url, etc.)
    'token',      # Protects auth tokens
    'secret',     # Protects secrets
    'password',   # Protects passwords
    'href',       # Protects hyperlinks
    'path',       # Protects file/API paths
}

# Replacement mappings (order matters - longest first to avoid partial replacements)
# Built dynamically from environment variable
if ENABLE_TERMINOLOGY_REPLACEMENT and REPLACEMENT_TERM:
    REPLACEMENTS = [
        ("PIs", f"{REPLACEMENT_TERM}s"),  # Plural first (longer)
        ("PI", REPLACEMENT_TERM),          # Singular (shorter)
    ]
else:
    REPLACEMENTS = []


def apply_replacements(text: str) -> str:
    """
    Apply all terminology replacements to a string.
    
    Uses word boundaries (\\b) to only match whole-word "PI" / "PIs",
    preventing corruption of words like "KPI", "API", "EPIC", "RAPID", etc.
    
    Args:
        text: Input string
        
    Returns:
        String with replacements applied
    """
    if not ENABLE_TERMINOLOGY_REPLACEMENT or not REPLACEMENT_TERM:
        return text
    
    # Replace plural first (longer match), then singular
    # \b ensures we only match whole-word PI/PIs, not substrings inside other words
    result = re.sub(r'\bPIs\b', f'{REPLACEMENT_TERM}s', text)
    result = re.sub(r'\bPI\b', REPLACEMENT_TERM, result)
    
    return result


def replace_terminology_safe(
    value: Any, 
    field_path: str = "",
    parent_key: Optional[str] = None
) -> Any:
    """
    Recursively replace terminology in response values.
    Only replaces in safe fields, never in data fields.
    
    Args:
        value: Value to process (can be any type)
        field_path: Dot-separated path to current field (for logging)
        parent_key: Current key name (for field checking)
        
    Returns:
        Transformed value
    """
    # Check if this field should be excluded (data field)
    if parent_key:
        key_lower = parent_key.lower()
        
        # Never replace in excluded data fields
        if any(excluded in key_lower for excluded in EXCLUDED_DATA_FIELDS):
            return value
        
        # Not a safe field, but check if it looks like user-facing text
        # (contains spaces, punctuation, etc.) vs data value
        if isinstance(value, str):
            if len(value) > 20 or ' ' in value or any(p in value for p in ['.', ',', ':', ';']):
                # Looks like descriptive text, apply replacement
                return apply_replacements(value)
            # Looks like a data value (short, no spaces), don't replace
            return value
    
    # Process based on type
    if isinstance(value, str):
        # String value - apply replacement (field is not excluded)
        return apply_replacements(value)
    
    elif isinstance(value, dict):
        # Recursively process dictionary
        return {
            k: replace_terminology_safe(
                v, 
                f"{field_path}.{k}" if field_path else k,
                k
            )
            for k, v in value.items()
        }
    
    elif isinstance(value, list):
        # Recursively process list
        return [
            replace_terminology_safe(
                item, 
                field_path,
                None  # List items don't have keys
            )
            for item in value
        ]
    
    else:
        # Numbers, booleans, None - return as-is
        return value


async def terminology_replacement_middleware(request: Request, call_next):
    """
    FastAPI middleware to replace terminology in JSON responses.
    
    Only processes JSON responses. Preserves all other response types.
    """
    # Check if replacement is enabled
    if not ENABLE_TERMINOLOGY_REPLACEMENT:
        return await call_next(request)
    
    # Process request
    response = await call_next(request)
    
    # Only process JSON responses
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("application/json"):
        logger.debug(f"Terminology: {request.url.path} - skipped (not JSON)")
        return response
    
    # Debug: Log response type and attributes
    response_type = type(response).__name__
    has_body = hasattr(response, 'body')
    has_render = hasattr(response, 'render')
    
    # Skip actual StreamingResponse (for file downloads, etc.)
    from fastapi.responses import StreamingResponse
    if isinstance(response, StreamingResponse):
        logger.debug(f"Terminology: {request.url.path} - skipped (StreamingResponse)")
        return response
    
    try:
        # Handle _StreamingResponse (internal FastAPI wrapper) or responses that need rendering
        # These are regular JSON responses wrapped by FastAPI
        body = None
        
        # For _StreamingResponse, we need to read from body_iterator
        # We'll consume it and create a new response, so this is fine
        if hasattr(response, 'body_iterator') and not hasattr(response, 'body'):
            # Read all chunks from the iterator
            body_chunks = []
            async for chunk in response.body_iterator:
                body_chunks.append(chunk)
            body = b''.join(body_chunks)
        elif hasattr(response, 'render') and callable(response.render):
            # Render the response first (this makes body available for some response types)
            await response.render()
            if hasattr(response, 'body'):
                body = response.body
        elif hasattr(response, 'body'):
            # Direct access to body property
            body = response.body
        else:
            # If we still can't access body, skip
            logger.info(f"🔄 Middleware: Skipping {request.url.path} - cannot access body (type: {response_type})")
            return response
        
        if not body:
            logger.info(f"🔄 Middleware: Skipping {request.url.path} - empty body")
            return response
        
        # Parse JSON
        try:
            data = json.loads(body.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            # Not valid JSON or not UTF-8, return as-is
            logger.info(f"🔄 Middleware: Skipping {request.url.path} - not valid JSON: {e}")
            return response
        
        # Replace terminology
        try:
            transformed_data = replace_terminology_safe(data)
            
            # Check if any replacements were made
            original_str = json.dumps(data)
            transformed_str = json.dumps(transformed_data)
            if original_str != transformed_str:
                logger.info(f"Terminology: {request.url.path} - PI→{REPLACEMENT_TERM}")
            
        except Exception as e:
            # If transformation fails, log and return original
            logger.warning(f"⚠️  Middleware: Terminology replacement failed on {request.url.path}: {e}", exc_info=True)
            return response
        
        # Create new response with transformed data
        # Remove Content-Length header as JSONResponse will recalculate it based on new body size
        new_headers = dict(response.headers)
        new_headers.pop('content-length', None)  # Remove old Content-Length
        
        return JSONResponse(
            content=transformed_data,
            status_code=response.status_code,
            headers=new_headers
        )
    
    except Exception as e:
        # If anything fails, return original response
        logger.warning(f"⚠️  Middleware: Error on {request.url.path}: {e}", exc_info=True)
        return response


# For testing
if __name__ == "__main__":
    # Test cases
    test_cases = [
        # Basic replacement
        ({"message": "PI Sync completed"}, {"message": "Quarter Sync completed"}),
        
        # Data field should NOT be replaced
        ({"pi_name": "2025-PI-1", "message": "PI Sync"}, {"pi_name": "2025-PI-1", "message": "Quarter Sync"}),
        
        # Plural replacement
        ({"message": "All PIs are complete"}, {"message": "All Quarters are complete"}),
        
        # Nested structures
        ({"data": {"message": "PI Events", "pi_name": "2025-PI-1"}}, {"data": {"message": "Quarter Events", "pi_name": "2025-PI-1"}}),
        
        # Lists
        ({"items": [{"name": "PI Sync"}, {"name": "PI Dependencies"}]}, {"items": [{"name": "Quarter Sync"}, {"name": "Quarter Dependencies"}]}),
        
        # Edge case: standalone PI
        ({"message": "The PI is complete"}, {"message": "The Quarter is complete"}),
        
        # Word boundary: KPI should NOT be replaced
        ({"message": "Track your KPIs carefully"}, {"message": "Track your KPIs carefully"}),
        
        # Word boundary: API should NOT be replaced
        ({"message": "The API integration works"}, {"message": "The API integration works"}),
        
        # Word boundary: EPIC should NOT be replaced
        ({"description": "EPIC status is green"}, {"description": "EPIC status is green"}),
        
        # URL fields should NOT be replaced (pattern-based exclusion)
        ({"closed_sprint_url": "https://jira.com/browse/PI-123", "message": "PI report ready"}, 
         {"closed_sprint_url": "https://jira.com/browse/PI-123", "message": "Quarter report ready"}),
        
        # API key fields should NOT be replaced (excluded via 'pi' in 'api')
        ({"jira_api_key": "ABCPIDEF1234567890", "message": "PI configured"}, 
         {"jira_api_key": "ABCPIDEF1234567890", "message": "Quarter configured"}),
        
        # Token fields should NOT be replaced (pattern-based exclusion)
        ({"auth_token": "PIxyz123longtoken", "message": "PI auth done"}, 
         {"auth_token": "PIxyz123longtoken", "message": "Quarter auth done"}),
    ]
    
    print("Running terminology replacement tests...")
    for input_data, expected in test_cases:
        result = replace_terminology_safe(input_data)
        if result == expected:
            print(f"✅ PASS: {input_data}")
        else:
            print(f"❌ FAIL: {input_data}")
            print(f"   Expected: {expected}")
            print(f"   Got:      {result}")

