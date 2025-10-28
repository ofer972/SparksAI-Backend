"""
Agent Job Cancellation Service - REST API endpoint for cancelling agent jobs.

This service provides an endpoint for cancelling agent jobs by setting their status to 'cancelled'.
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import text
from sqlalchemy.engine import Connection
import logging
from database_connection import get_db_connection
import config

logger = logging.getLogger(__name__)

agent_job_cancellation_router = APIRouter()


@agent_job_cancellation_router.post("/agent-jobs/{job_id}/cancel")
async def cancel_agent_job(
    job_id: int,
    conn: Connection = Depends(get_db_connection)
):
    """
    Cancel an agent job by setting its status to 'cancelled'.
    
    Args:
        job_id: The ID of the job to cancel
        conn: Database connection from FastAPI dependency
    
    Returns:
        JSON response with updated job information
    """
    try:
        # Cancel the job
        update_query = text(f"""
            UPDATE {config.AGENT_JOBS_TABLE} 
            SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
            WHERE job_id = :job_id
            RETURNING job_id, job_type, team_name, pi, status, created_at, updated_at
        """)
        
        logger.info(f"Cancelling job {job_id}")
        
        result = conn.execute(update_query, {"job_id": job_id})
        row = result.fetchone()
        conn.commit()
        
        if not row:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        
        # Convert row to dictionary
        job = dict(row._mapping)
        
        return {
            "success": True,
            "data": {
                "job": job
            },
            "message": f"Job {job_id} cancelled successfully"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling job {job_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to cancel job: {str(e)}"
        )
