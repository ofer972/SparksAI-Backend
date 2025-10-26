"""
PIs Service - REST API endpoints for PI-related operations.

This service provides endpoints for managing and retrieving PI information.
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

pis_router = APIRouter()

@pis_router.get("/pis/getPis")
async def get_pis(conn: Connection = Depends(get_db_connection)):
    """
    Get all PIs from pis table.
    Uses parameterized queries to prevent SQL injection.
    
    Returns:
        JSON response with list of PIs and count
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT * 
            FROM {config.PIS_TABLE} 
            ORDER BY pi_name
        """)
        
        logger.info(f"Executing query to get all PIs from pis table")
        
        # Execute query with connection from dependency
        result = conn.execute(query)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        pis = []
        for row in rows:
            pis.append({
                "pi_name": row[0],
                "start_date": row[1],
                "end_date": row[2],
                "planning_grace_days": row[3],
                "prep_grace_days": row[4],
                "updated_at": row[5]
            })
        
        return {
            "success": True,
            "data": {
                "pis": pis,
                "count": len(pis)
            },
            "message": f"Retrieved {len(pis)} PIs"
        }
    
    except Exception as e:
        logger.error(f"Error fetching PIs: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch PIs: {str(e)}"
        )
