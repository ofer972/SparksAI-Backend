"""
Agent Jobs Service - REST API endpoints for agent job-related operations.

This service provides endpoints for managing, creating, and cancelling agent jobs.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends, Response
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
import logging
from pydantic import BaseModel
from database_connection import get_db_connection
import config

logger = logging.getLogger(__name__)

agent_jobs_router = APIRouter()


class TeamJobCreateRequest(BaseModel):
    job_type: str
    team_name: str


class PIJobCreateRequest(BaseModel):
    job_type: str
    pi: str


def validate_team_exists(team_name: str, conn: Connection):
    """Validate that the team exists in the database by checking jira_issues table"""
    try:
        query = text(f"""
            SELECT COUNT(*) 
            FROM {config.WORK_ITEMS_TABLE} 
            WHERE team_name = :team_name 
            AND team_name IS NOT NULL 
            AND team_name != ''
        """)
        result = conn.execute(query, {"team_name": team_name})
        count = result.scalar()
        
        if count == 0:
            raise HTTPException(status_code=404, detail=f"Team '{team_name}' not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error validating team existence: {e}")
        raise HTTPException(status_code=500, detail="Error validating team")


def validate_pi_exists(pi: str, conn: Connection):
    """Validate that the PI exists in the database by checking pis table"""
    try:
        query = text(f"""
            SELECT COUNT(*) 
            FROM {config.PIS_TABLE} 
            WHERE pi_name = :pi
        """)
        result = conn.execute(query, {"pi": pi})
        count = result.scalar()
        
        if count == 0:
            raise HTTPException(status_code=404, detail=f"PI '{pi}' not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error validating PI existence: {e}")
        raise HTTPException(status_code=500, detail="Error validating PI")


def validate_team_job_request(job_type: str, team_name: str, conn: Connection):
    """Validate team job creation request"""
    if not job_type or not job_type.strip():
        raise HTTPException(status_code=400, detail="job_type is required")
    
    if not team_name or not team_name.strip():
        raise HTTPException(status_code=400, detail="team_name is required")
    
    # Validate team exists in database
    validate_team_exists(team_name, conn)


def validate_pi_job_request(job_type: str, pi: str, conn: Connection):
    """Validate PI job creation request"""
    if not job_type or not job_type.strip():
        raise HTTPException(status_code=400, detail="job_type is required")
    
    if not pi or not pi.strip():
        raise HTTPException(status_code=400, detail="pi is required")
    
    # Validate PI exists in database
    validate_pi_exists(pi, conn)

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

# -----------------------
# Next pending job (static path) - must be BEFORE dynamic {job_id}
# -----------------------

@agent_jobs_router.get("/agent-jobs/next-pending")
async def get_next_pending_job(conn: Connection = Depends(get_db_connection)):
    """
    Get the oldest pending agent job (status Pending/pending).
    Returns a single row or 404 if none.
    """
    try:
        query = text(f"""
            SELECT *
            FROM {config.AGENT_JOBS_TABLE}
            WHERE status IN ('pending','Pending')
            ORDER BY created_at ASC, job_id ASC
            LIMIT 1
        """)

        result = conn.execute(query)
        row = result.fetchone()
        if not row:
            return Response(status_code=204)

        job = dict(row._mapping)
        return {
            "success": True,
            "data": {"job": job},
            "message": "Retrieved next pending job"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching next pending job: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch next pending job: {str(e)}")


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


@agent_jobs_router.post("/agent-jobs/create-team-job")
async def create_team_job(
    request: TeamJobCreateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Create a new team-based agent job.
    
    Args:
        request: TeamJobCreateRequest containing job_type and team_name
        conn: Database connection from FastAPI dependency
    
    Returns:
        JSON response with created job information
    """
    try:
        # Validate request
        validate_team_job_request(request.job_type, request.team_name, conn)
        
        # Create the job
        insert_query = text(f"""
            INSERT INTO {config.AGENT_JOBS_TABLE} 
            (job_type, team_name, pi, status, created_at, updated_at)
            VALUES (:job_type, :team_name, NULL, 'Pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING job_id, job_type, team_name, pi, status, created_at
        """)
        
        logger.info(f"Creating team job: {request.job_type} for team: {request.team_name}")
        
        result = conn.execute(insert_query, {
            "job_type": request.job_type,
            "team_name": request.team_name
        })
        
        row = result.fetchone()
        conn.commit()
        
        # Convert row to dictionary
        job = dict(row._mapping)
        
        return {
            "success": True,
            "data": {
                "job": job
            },
            "message": "Team job created successfully"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error creating team job: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create team job: {str(e)}"
        )


@agent_jobs_router.post("/agent-jobs/create-pi-job")
async def create_pi_job(
    request: PIJobCreateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Create a new PI-based agent job.
    
    Args:
        request: PIJobCreateRequest containing job_type and pi
        conn: Database connection from FastAPI dependency
    
    Returns:
        JSON response with created job information
    """
    try:
        # Validate request
        validate_pi_job_request(request.job_type, request.pi, conn)
        
        # Create the job
        insert_query = text(f"""
            INSERT INTO {config.AGENT_JOBS_TABLE} 
            (job_type, team_name, pi, status, created_at, updated_at)
            VALUES (:job_type, NULL, :pi, 'Pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING job_id, job_type, team_name, pi, status, created_at
        """)
        
        logger.info(f"Creating PI job: {request.job_type} for PI: {request.pi}")
        
        result = conn.execute(insert_query, {
            "job_type": request.job_type,
            "pi": request.pi
        })
        
        row = result.fetchone()
        conn.commit()
        
        # Convert row to dictionary
        job = dict(row._mapping)
        
        return {
            "success": True,
            "data": {
                "job": job
            },
            "message": "PI job created successfully"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except Exception as e:
        logger.error(f"Error creating PI job: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create PI job: {str(e)}"
        )


@agent_jobs_router.post("/agent-jobs/{job_id}/cancel")
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


# -----------------------
# Update Agent Job
# -----------------------

class AgentJobUpdateRequest(BaseModel):
    status: Optional[str] = None
    claimed_by: Optional[str] = None
    claimed_at: Optional[str] = None  # ISO-8601 string; stored as timestamp by DB
    job_data: Optional[str] = None
    input_sent: Optional[str] = None
    result: Optional[str] = None
    error: Optional[str] = None


@agent_jobs_router.patch("/agent-jobs/{job_id}")
async def update_agent_job(
    job_id: int,
    request: AgentJobUpdateRequest,
    conn: Connection = Depends(get_db_connection)
):
    """
    Update selected fields of an agent job.

    Allowed fields: status, claimed_by, claimed_at, job_data, input_sent, result, error
    """
    try:
        updates = request.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No updatable fields provided")

        allowed = {"status", "claimed_by", "claimed_at", "job_data", "input_sent", "result", "error"}
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            raise HTTPException(status_code=400, detail="No valid fields to update")

        set_clauses = ", ".join([f"{k} = :{k}" for k in filtered.keys()])
        params = dict(filtered)
        params["job_id"] = job_id

        # Special rule: claiming is only allowed from pending
        status_value = filtered.get("status")
        if status_value is not None and status_value in ("claimed", "Claimed"):
            # Conditional update: only when current status is pending/Pending
            claimed_update_query = text(f"""
                UPDATE {config.AGENT_JOBS_TABLE}
                SET {set_clauses}, updated_at = CURRENT_TIMESTAMP
                WHERE job_id = :job_id
                AND status IN ('pending','Pending')
                RETURNING job_id, job_type, team_name, pi, status, claimed_by, claimed_at, job_data, input_sent, result, error, created_at, updated_at
            """)
            result = conn.execute(claimed_update_query, params)
            row = result.fetchone()
            conn.commit()

            if not row:
                # Determine whether it's not found vs. conflict due to status
                exists_query = text(f"SELECT status FROM {config.AGENT_JOBS_TABLE} WHERE job_id = :job_id")
                exists_row = conn.execute(exists_query, {"job_id": job_id}).fetchone()
                if not exists_row:
                    raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
                # Found but not pending -> conflict
                raise HTTPException(status_code=409, detail="Job can be claimed only from pending status")
        else:
            # Regular update path
            update_query = text(f"""
                UPDATE {config.AGENT_JOBS_TABLE}
                SET {set_clauses}, updated_at = CURRENT_TIMESTAMP
                WHERE job_id = :job_id
                RETURNING job_id, job_type, team_name, pi, status, claimed_by, claimed_at, job_data, input_sent, result, error, created_at, updated_at
            """)
            result = conn.execute(update_query, params)
            row = result.fetchone()
            conn.commit()

            if not row:
                raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        job = dict(row._mapping)
        return {
            "success": True,
            "data": {"job": job},
            "message": f"Job {job_id} updated successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating agent job {job_id}: {e}")
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update agent job: {str(e)}")
