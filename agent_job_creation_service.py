"""
Agent Job Creation Service - REST API endpoints for creating agent jobs.

This service provides endpoints for creating team-based and PI-based agent jobs.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import Dict, Any
import logging
from pydantic import BaseModel
from database_connection import get_db_connection
import config

logger = logging.getLogger(__name__)

agent_job_creation_router = APIRouter()


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


@agent_job_creation_router.post("/agent-jobs/create-team-job")
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
            VALUES (:job_type, :team_name, NULL, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
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


@agent_job_creation_router.post("/agent-jobs/create-pi-job")
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
            VALUES (:job_type, NULL, :pi, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
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
