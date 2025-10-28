"""
Agent Jobs Service - REST API endpoints for agent job-related operations.

This service provides endpoints for managing and retrieving agent job information.
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

agent_jobs_router = APIRouter()

@agent_jobs_router.get("/agent-jobs")
async def get_agent_jobs(conn: Connection = Depends(get_db_connection)):
    """
    Get the latest 100 agent jobs from agent_jobs table.
    Uses parameterized queries to prevent SQL injection.
    
    Returns:
        JSON response with list of agent jobs and count
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        # Only return selected fields for the collection endpoint
        query = text(f"""
            SELECT 
                job_id,
                job_type,
                team_name,
                status,
                claimed_by,
                claimed_at,
                result,
                error
            FROM {config.AGENT_JOBS_TABLE}
            ORDER BY job_id DESC 
            LIMIT 100
        """)
        
        logger.info(f"Executing query to get latest 100 agent jobs from {config.AGENT_JOBS_TABLE}")
        
        # Execute query with connection from dependency
        result = conn.execute(query)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        jobs = []
        for row in rows:
            # Truncate result to first 200 characters with ellipsis when longer
            result_text = row[6]
            if isinstance(result_text, str) and len(result_text) > 200:
                result_text = result_text[:200] + "..."

            job_dict = {
                "job_id": row[0],
                "job_type": row[1],
                "team_name": row[2],
                "status": row[3],
                "claimed_by": row[4],
                "claimed_at": row[5],
                "result": result_text,
                "error": row[7]
            }
            jobs.append(job_dict)
        
        return {
            "success": True,
            "data": {
                "jobs": jobs,
                "count": len(jobs)
            },
            "message": f"Retrieved {len(jobs)} agent jobs"
        }
    
    except Exception as e:
        logger.error(f"Error fetching agent jobs: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch agent jobs: {str(e)}"
        )

@agent_jobs_router.get("/agent-jobs/{job_id}")
async def get_agent_job(job_id: int, conn: Connection = Depends(get_db_connection)):
    """
    Get a single agent job by ID from agent_jobs table.
    Uses parameterized queries to prevent SQL injection.
    
    Args:
        job_id: The ID of the agent job to retrieve
    
    Returns:
        JSON response with single agent job or 404 if not found
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT * 
            FROM {config.AGENT_JOBS_TABLE} 
            WHERE job_id = :job_id
        """)
        
        logger.info(f"Executing query to get agent job with ID {job_id} from {config.AGENT_JOBS_TABLE}")
        
        # Execute query with connection from dependency
        result = conn.execute(query, {"job_id": job_id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Agent job with ID {job_id} not found"
            )
        
        # Convert row to dictionary - get all fields from database
        job = dict(row._mapping)
        
        return {
            "success": True,
            "data": {
                "job": job
            },
            "message": f"Retrieved agent job with ID {job_id}"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (like the 404 error above)
        raise
    except Exception as e:
        logger.error(f"Error fetching agent job {job_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch agent job: {str(e)}"
        )
