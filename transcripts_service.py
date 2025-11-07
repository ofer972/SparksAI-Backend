"""
Transcripts Service - REST API endpoints for transcript-related operations.

This service provides endpoints for managing and retrieving transcript information.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
import logging
from datetime import datetime
import re
from database_connection import get_db_connection
import config

logger = logging.getLogger(__name__)

transcripts_router = APIRouter()


def validate_name(value: Optional[str], field: str) -> str:
    """Basic non-empty validation and sanitization similar to other services."""
    if value is None or not isinstance(value, str) or value.strip() == "":
        raise HTTPException(status_code=400, detail=f"{field} is required")
    sanitized = re.sub(r'[^a-zA-Z0-9\s\-_]', '', value.strip())
    if not sanitized:
        raise HTTPException(status_code=400, detail=f"{field} contains invalid characters")
    if len(sanitized) > 255:
        raise HTTPException(status_code=400, detail=f"{field} is too long (max 255 characters)")
    return sanitized


def parse_date_from_filename(filename: str) -> Optional[str]:
    """
    Parse date from filename.
    
    Checks beginning and end of filename for date patterns.
    Supports formats: DDMMYYYY, DD-MM-YYYY, DD/MM/YYYY, DD.MM.YYYY,
                      YYYYMMDD, YYYY-MM-DD, YYYY/MM/DD, YYYY.MM.DD
    
    Args:
        filename: The filename to parse (without path)
        
    Returns:
        Date string in YYYY-MM-DD format, or None if not found
    """
    if not filename:
        return None
    
    # Remove file extension
    name_without_ext = re.sub(r'\.[^.]*$', '', filename)
    
    # Date format patterns to try (pattern, format_string)
    # Order matters: try more specific patterns first
    date_formats = [
        # DD-MM-YYYY, DD/MM/YYYY, DD.MM.YYYY (07-11-2025, 07/11/2025, 07.11.2025) - beginning
        (r'^\d{2}[-/.]\d{2}[-/.]\d{4}', '%d-%m-%Y'),
        # DD-MM-YYYY, DD/MM/YYYY, DD.MM.YYYY - end
        (r'\d{2}[-/.]\d{2}[-/.]\d{4}$', '%d-%m-%Y'),
        # YYYY-MM-DD, YYYY/MM/DD, YYYY.MM.DD (2025-11-07, 2025/11/07, 2025.11.07) - beginning
        (r'^\d{4}[-/.]\d{2}[-/.]\d{2}', '%Y-%m-%d'),
        # YYYY-MM-DD, YYYY/MM/DD, YYYY.MM.DD - end
        (r'\d{4}[-/.]\d{2}[-/.]\d{2}$', '%Y-%m-%d'),
        # DDMMYYYY (07112025) - beginning - try this before YYYYMMDD
        (r'^\d{8}', '%d%m%Y'),
        # DDMMYYYY (07112025) - end
        (r'\d{8}$', '%d%m%Y'),
        # YYYYMMDD (20251107) - beginning
        (r'^\d{8}', '%Y%m%d'),
        # YYYYMMDD (20251107) - end
        (r'\d{8}$', '%Y%m%d'),
    ]
    
    # Try each pattern
    for pattern, date_format in date_formats:
        match = re.search(pattern, name_without_ext)
        if match:
            # Extract the matched date string
            date_str = match.group(0)
            # Normalize separators to match format (replace / and . with -)
            normalized_date = re.sub(r'[/.]', '-', date_str)
            
            try:
                # Parse the date
                parsed_date = datetime.strptime(normalized_date, date_format)
                # Return in YYYY-MM-DD format
                return parsed_date.strftime('%Y-%m-%d')
            except ValueError:
                # Invalid date (e.g., 32nd day, 13th month), try next pattern
                continue
    
    return None

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
                transcript_date,
                team_name,
                type,
                file_name,
                raw_text,
                origin,
                pi,
                created_at,
                updated_at
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
                "pi": row[7],
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

@transcripts_router.get("/transcripts/getLatest")
async def get_latest_transcripts(
    type: Optional[str] = Query(None, description="Transcript type: 'Daily' or 'PI Sync'"),
    team_name: Optional[str] = Query(None, description="Team name (required if type='Daily')"),
    pi_name: Optional[str] = Query(None, description="PI name (required if type='PI Sync')"),
    limit: int = Query(1, ge=1, le=100, description="Number of transcripts to return (default: 1, max: 100)"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get latest transcripts with optional filtering by type, team, or PI.
    
    Args:
        type: Transcript type ('Daily' or 'PI Sync'). If not provided, returns all types.
        team_name: Team name (required if type='Daily')
        pi_name: PI name (required if type='PI Sync')
        limit: Number of transcripts to return (default: 1, max: 100)
    
    Returns:
        JSON response with transcripts list and count, or 204 if no results found
    """
    try:
        # Validate type if provided
        if type:
            type = type.strip()
            if type not in ['Daily', 'PI Sync']:
                raise HTTPException(
                    status_code=400,
                    detail="type must be 'Daily' or 'PI Sync'"
                )
        
        # Validate required parameters based on type
        if type == 'Daily':
            if not team_name:
                raise HTTPException(
                    status_code=400,
                    detail="team_name is required when type='Daily'"
                )
            validated_team = validate_name(team_name, "team_name")
        elif type == 'PI Sync':
            if not pi_name:
                raise HTTPException(
                    status_code=400,
                    detail="pi_name is required when type='PI Sync'"
                )
            validated_pi = validate_name(pi_name, "pi_name")
        
        # Build WHERE clause conditions
        where_conditions = []
        params = {"limit": limit}
        
        if type:
            where_conditions.append("type = :type")
            params["type"] = type
        
        if type == 'Daily' and team_name:
            where_conditions.append("team_name = :team_name")
            params["team_name"] = validated_team
        elif type == 'PI Sync' and pi_name:
            where_conditions.append("pi = :pi_name")
            params["pi_name"] = validated_pi
        
        # Build WHERE clause
        where_clause = ""
        if where_conditions:
            where_clause = "WHERE " + " AND ".join(where_conditions)
        
        # Build query
        query = text(f"""
            SELECT *
            FROM {config.TRANSCRIPTS_TABLE}
            {where_clause}
            ORDER BY transcript_date DESC
            LIMIT :limit
        """)
        
        logger.info(f"Executing query to get latest transcripts: type={type}, team_name={team_name}, pi_name={pi_name}, limit={limit}")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        if not rows:
            from fastapi import Response
            return Response(status_code=204)
        
        # Convert rows to list of dictionaries
        transcripts = []
        for row in rows:
            transcripts.append(dict(row._mapping))
        
        return {
            "success": True,
            "data": {
                "transcripts": transcripts,
                "count": len(transcripts)
            },
            "message": f"Retrieved {len(transcripts)} transcript(s)"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching latest transcripts: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch latest transcripts: {str(e)}"
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


@transcripts_router.post("/transcripts/upload-team")
async def upload_team_transcript(
    raw_data: UploadFile = File(...),
    file_name: Optional[str] = Form(None),
    team_name: Optional[str] = Form(None),
    type: Optional[str] = Form(None),
    origin: Optional[str] = Form(None),
    transcript_date: Optional[str] = Form(None),
    conn: Connection = Depends(get_db_connection)
):
    """
    Upload a team transcript file to the database.
    
    Args:
        raw_data: The uploaded file content
        file_name: Optional custom file name (defaults to uploaded filename)
        team_name: Optional team name
        type: Optional transcript type
        origin: Optional origin information
        transcript_date: Optional transcript date (defaults to current date)
        conn: Database connection dependency
    
    Returns:
        Dict containing the uploaded transcript information
    """
    try:
        # Check file size (limit to 2MB)
        MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB
        file_content = await raw_data.read()
        
        if len(file_content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum size allowed is {MAX_FILE_SIZE / (1024*1024):.1f}MB"
            )
        
        # Convert file content to text
        try:
            raw_text = file_content.decode('utf-8')
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=400,
                detail="File must be a valid text file (UTF-8 encoded)"
            )
        
        # Insert or update transcript into database (UPSERT)
        query = text(f"""
            INSERT INTO {config.TRANSCRIPTS_TABLE} 
            (transcript_date, team_name, type, file_name, raw_text, origin, pi, created_at, updated_at)
            VALUES (:transcript_date, :team_name, :type, :file_name, :raw_text, :origin, :pi, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT ON CONSTRAINT unique_team_transcript
            DO UPDATE SET 
                type = EXCLUDED.type,
                file_name = EXCLUDED.file_name,
                raw_text = EXCLUDED.raw_text,
                origin = EXCLUDED.origin,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id, transcript_date, team_name, type, file_name, origin, pi, created_at, updated_at
        """)
        
        # Use provided file_name or fallback to uploaded filename
        final_file_name = file_name if file_name else raw_data.filename
        
        # Determine transcript_date: first try filename, then parameter, then current date
        date_source = None
        parsed_date = parse_date_from_filename(final_file_name)
        
        if parsed_date:
            final_date = parsed_date
            date_source = "filename"
        elif transcript_date:
            final_date = transcript_date
            date_source = "parameter"
        else:
            final_date = "CURRENT_DATE"
            date_source = "current_date"
        
        logger.info(f"Uploading transcript file: {final_file_name}, transcript_date: {final_date} (source: {date_source}), team_name: {team_name}, type: {type}, pi: None")
        
        result = conn.execute(query, {
            "transcript_date": final_date,
            "team_name": team_name,
            "type": type,
            "file_name": final_file_name,
            "raw_text": raw_text,
            "origin": origin,
            "pi": None  # Team transcripts don't have PI
        })
        
        row = result.fetchone()
        conn.commit()
        
        return {
            "id": row[0],
            "transcript_date": row[1],
            "team_name": row[2],
            "type": row[3],
            "file_name": row[4],
            "origin": row[5],
            "pi": row[6],
            "created_at": row[7],
            "updated_at": row[8],
            "file_size": len(file_content)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading team transcript: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload team transcript: {str(e)}"
        )


@transcripts_router.post("/transcripts/upload-pi")
async def upload_pi_transcript(
    raw_data: UploadFile = File(...),
    pi: str = Form(...),
    file_name: Optional[str] = Form(None),
    type: Optional[str] = Form(None),
    origin: Optional[str] = Form(None),
    transcript_date: Optional[str] = Form(None),
    conn: Connection = Depends(get_db_connection)
):
    """
    Upload a PI transcript file to the database.
    
    Args:
        raw_data: The uploaded file content
        pi: PI name (required)
        file_name: Optional custom file name (defaults to uploaded filename)
        type: Optional transcript type
        origin: Optional origin information
        transcript_date: Optional transcript date (defaults to current date)
        conn: Database connection dependency
    
    Returns:
        Dict containing the uploaded transcript information
    """
    try:
        # Check file size (limit to 2MB)
        MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB
        file_content = await raw_data.read()
        
        if len(file_content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum size allowed is {MAX_FILE_SIZE / (1024*1024):.1f}MB"
            )
        
        # Convert file content to text
        try:
            raw_text = file_content.decode('utf-8')
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=400,
                detail="File must be a valid text file (UTF-8 encoded)"
            )
        
        # Insert or update transcript into database (UPSERT)
        query = text(f"""
            INSERT INTO {config.TRANSCRIPTS_TABLE} 
            (transcript_date, team_name, type, file_name, raw_text, origin, pi, created_at, updated_at)
            VALUES (:transcript_date, :team_name, :type, :file_name, :raw_text, :origin, :pi, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT ON CONSTRAINT unique_pi_transcript
            DO UPDATE SET 
                type = EXCLUDED.type,
                file_name = EXCLUDED.file_name,
                raw_text = EXCLUDED.raw_text,
                origin = EXCLUDED.origin,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id, transcript_date, team_name, type, file_name, origin, pi, created_at, updated_at
        """)
        
        # Use provided file_name or fallback to uploaded filename
        final_file_name = file_name if file_name else raw_data.filename
        
        # Determine transcript_date: first try filename, then parameter, then current date
        date_source = None
        parsed_date = parse_date_from_filename(final_file_name)
        
        if parsed_date:
            final_date = parsed_date
            date_source = "filename"
        elif transcript_date:
            final_date = transcript_date
            date_source = "parameter"
        else:
            final_date = "CURRENT_DATE"
            date_source = "current_date"
        
        logger.info(f"Uploading transcript file: {final_file_name}, transcript_date: {final_date} (source: {date_source}), team_name: None, type: {type}, pi: {pi}")
        
        result = conn.execute(query, {
            "transcript_date": final_date,
            "team_name": None,  # PI transcripts don't have team
            "type": type,
            "file_name": final_file_name,
            "raw_text": raw_text,
            "origin": origin,
            "pi": pi  # Required for PI transcripts
        })
        
        row = result.fetchone()
        conn.commit()
        
        return {
            "id": row[0],
            "transcript_date": row[1],
            "team_name": row[2],
            "type": row[3],
            "file_name": row[4],
            "origin": row[5],
            "pi": row[6],
            "created_at": row[7],
            "updated_at": row[8],
            "file_size": len(file_content)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading PI transcript: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload PI transcript: {str(e)}"
        )


@transcripts_router.delete("/transcripts/{id}")
async def delete_transcript(
    id: int,
    conn: Connection = Depends(get_db_connection)
):
    """
    Delete a transcript by ID.
    
    Args:
        id: The ID of the transcript to delete
    
    Returns:
        JSON response with success message or 404 if not found
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            DELETE FROM {config.TRANSCRIPTS_TABLE} 
            WHERE id = :id
        """)
        
        logger.info(f"Deleting transcript with ID {id} from {config.TRANSCRIPTS_TABLE}")
        
        result = conn.execute(query, {"id": id})
        conn.commit()
        
        if result.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail=f"Transcript with ID {id} not found"
            )
        
        return {
            "success": True,
            "data": {"id": id},
            "message": f"Transcript {id} deleted successfully"
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions (like the 404 error above)
        raise
    except Exception as e:
        logger.error(f"Error deleting transcript {id}: {e}")
        conn.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete transcript: {str(e)}"
        )