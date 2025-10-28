"""
Transcripts Service - REST API endpoints for transcript-related operations.

This service provides endpoints for managing and retrieving transcript information.
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

transcripts_router = APIRouter()

@transcripts_router.get("/transcripts")
async def get_transcripts(conn: Connection = Depends(get_db_connection)):
    """
    Get the latest 100 transcripts from transcripts table.
    Uses parameterized queries to prevent SQL injection.
    
    Returns:
        JSON response with list of transcripts and count
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        # Only return selected fields for the collection endpoint
        query = text(f"""
            SELECT 
                id,
                transcript_date_time,
                team_name,
                type,
                file_name,
                raw_text,
                origin
            FROM {config.TRANSCRIPTS_TABLE}
            ORDER BY id DESC 
            LIMIT 100
        """)
        
        logger.info(f"Executing query to get latest 100 transcripts from {config.TRANSCRIPTS_TABLE}")
        
        # Execute query with connection from dependency
        result = conn.execute(query)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        transcripts = []
        for row in rows:
            # Truncate raw_text to first 200 characters with ellipsis when longer
            raw_text_content = row[5]
            if isinstance(raw_text_content, str) and len(raw_text_content) > 200:
                raw_text_content = raw_text_content[:200] + "..."

            transcript_dict = {
                "id": row[0],
                "transcript_date_time": row[1],
                "team_name": row[2],
                "type": row[3],
                "file_name": row[4],
                "raw_text": raw_text_content,
                "origin": row[6]
            }
            transcripts.append(transcript_dict)
        
        return {
            "success": True,
            "data": {
                "transcripts": transcripts,
                "count": len(transcripts)
            },
            "message": f"Retrieved {len(transcripts)} transcripts"
        }
    
    except Exception as e:
        logger.error(f"Error fetching transcripts: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch transcripts: {str(e)}"
        )

@transcripts_router.get("/transcripts/{id}")
async def get_transcript(id: int, conn: Connection = Depends(get_db_connection)):
    """
    Get a single transcript by ID from transcripts table.
    Uses parameterized queries to prevent SQL injection.
    
    Args:
        id: The ID of the transcript to retrieve
    
    Returns:
        JSON response with single transcript or 404 if not found
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT * 
            FROM {config.TRANSCRIPTS_TABLE} 
            WHERE id = :id
        """)
        
        logger.info(f"Executing query to get transcript with ID {id} from {config.TRANSCRIPTS_TABLE}")
        
        # Execute query with connection from dependency
        result = conn.execute(query, {"id": id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Transcript with ID {id} not found"
            )
        
        # Convert row to dictionary - get all fields from database
        transcript = dict(row._mapping)
        
        return {
            "success": True,
            "data": {
                "transcript": transcript
            },
            "message": f"Retrieved transcript with ID {id}"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (like the 404 error above)
        raise
    except Exception as e:
        logger.error(f"Error fetching transcript {id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch transcript: {str(e)}"
        )
