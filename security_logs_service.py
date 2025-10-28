"""
Security Logs Service - REST API endpoints for security log-related operations.

This service provides endpoints for managing and retrieving security log information.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any
import logging
from database_connection import get_db_connection
import config

logger = logging.getLogger(__name__)

security_logs_router = APIRouter()

@security_logs_router.get("/security-logs")
async def get_security_logs(conn: Connection = Depends(get_db_connection)):
    """
    Get the latest 100 security logs from security_logs table.
    Uses parameterized queries to prevent SQL injection.
    
    Returns:
        JSON response with list of security logs and count
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        # Only return selected fields for the collection endpoint
        query = text(f"""
            SELECT 
                id,
                timestamp,
                event_type,
                email,
                ip_address,
                details,
                severity
            FROM {config.SECURITY_LOGS_TABLE}
            ORDER BY id DESC 
            LIMIT 100
        """)
        
        logger.info(f"Executing query to get latest 100 security logs from {config.SECURITY_LOGS_TABLE}")
        
        # Execute query with connection from dependency
        result = conn.execute(query)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        logs = []
        for row in rows:
            # Truncate details to first 200 characters with ellipsis when longer
            details_text = row[5]
            if isinstance(details_text, str) and len(details_text) > 200:
                details_text = details_text[:200] + "..."

            log_dict = {
                "id": row[0],
                "timestamp": row[1],
                "event_type": row[2],
                "email": row[3],
                "ip_address": row[4],
                "details": details_text,
                "severity": row[6]
            }
            logs.append(log_dict)
        
        return {
            "success": True,
            "data": {
                "logs": logs,
                "count": len(logs)
            },
            "message": f"Retrieved {len(logs)} security logs"
        }
    
    except Exception as e:
        logger.error(f"Error fetching security logs: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch security logs: {str(e)}"
        )

@security_logs_router.get("/security-logs/{id}")
async def get_security_log(id: int, conn: Connection = Depends(get_db_connection)):
    """
    Get a single security log by ID from security_logs table.
    Uses parameterized queries to prevent SQL injection.
    
    Args:
        id: The ID of the security log to retrieve
    
    Returns:
        JSON response with single security log or 404 if not found
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT * 
            FROM {config.SECURITY_LOGS_TABLE} 
            WHERE id = :id
        """)
        
        logger.info(f"Executing query to get security log with ID {id} from {config.SECURITY_LOGS_TABLE}")
        
        # Execute query with connection from dependency
        result = conn.execute(query, {"id": id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Security log with ID {id} not found"
            )
        
        # Convert row to dictionary - get all fields from database
        log = dict(row._mapping)
        
        return {
            "success": True,
            "data": {
                "log": log
            },
            "message": f"Retrieved security log with ID {id}"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (like the 404 error above)
        raise
    except Exception as e:
        logger.error(f"Error fetching security log {id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch security log: {str(e)}"
        )
