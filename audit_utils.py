"""
Audit utilities for logging changes to goals and settings.
"""

import os
import json
import logging
import requests
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def create_change_audit_log(
    change_type: str,  # "goal_update" or "settings_update"
    entity_id: Optional[int],  # goal_id for goals, None for settings
    changes: List[Dict[str, Any]],  # [{"field": "...", "from": "...", "to": "..."}]
    user_email: str,
    endpoint_path: str,
    status_code: int = 200,
    response_time_seconds: float = 0.0
) -> None:
    """
    Create special audit log for goal/settings changes.
    Uses http_method="UPDATE-GOAL" or "UPDATE-SETTINGS" to distinguish from normal audits.
    Stores change data in body_raw JSONB field.
    
    Args:
        change_type: "goal_update" or "settings_update"
        entity_id: goal_id for goals, None for settings
        changes: List of change objects with field, from, to
        user_email: Email of user making the change
        endpoint_path: API endpoint path
        status_code: HTTP status code (default 200)
        response_time_seconds: Response time in seconds (default 0.0)
    """
    audit_service_url = os.environ.get("AUDIT_SERVICE_URL")
    if not audit_service_url or not audit_service_url.strip():
        logger.warning("⚠️  WARNING: AUDIT_SERVICE_URL not configured. Change audit logging skipped.")
        return
    
    # Log that we're attempting to create audit log
    logger.debug(f"Creating change audit log: {change_type}, user={user_email}, changes={len(changes)}")
    
    # Determine http_method based on change_type
    http_method = "UPDATE-GOAL" if change_type == "goal_update" else "UPDATE-SETTINGS"
    
    # Build body_raw JSON structure
    body_raw_data = {
        "change_type": change_type,
        "entity_id": entity_id,
        "changes": changes,
        "user_email": user_email
    }
    body_raw_json = json.dumps(body_raw_data)
    
    # Build audit log entry
    audit_log: Dict[str, Any] = {
        "endpoint_path": endpoint_path,
        "http_method": http_method,
        "status_code": status_code,
        "response_time_seconds": response_time_seconds,
        "user_id": user_email,
        "body_raw": body_raw_json,
        "severity": "NONE"
    }
    
    payload = {
        "logs": [audit_log]
    }
    
    try:
        url = f"{audit_service_url.rstrip('/')}/api/audit-logs"
        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5
        )
        
        if response.status_code == 202:
            logger.debug(f"✅ Successfully queued change audit log for {change_type} (user: {user_email})")
        else:
            logger.warning(f"⚠️  WARNING: Audit service returned status {response.status_code} for change audit log. Response: {response.text}")
    
    except requests.exceptions.Timeout:
        logger.warning("Audit service unavailable (timeout after 5s). Change audit logging skipped.")
    except requests.exceptions.RequestException as req_err:
        logger.warning(f"Audit service unavailable ({req_err}). Change audit logging skipped.")
    except Exception as e:
        logger.warning(f"Failed to create change audit log: {e}. Change audit logging skipped.")

