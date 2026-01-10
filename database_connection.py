"""
Database connection utilities for SparksAI Backend Services.

This module handles database engine creation, connection management,
and related utilities.
"""

import os
import time
from sqlalchemy import create_engine, text, event
from sqlalchemy.engine import Engine
from typing import Optional, Dict
import logging
from contextvars import ContextVar
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv

# Load environment variables from .env file (must be before get_connection_string is called)
load_dotenv()

logger = logging.getLogger(__name__)

# Control SQL logging via environment variable (defaults to enabled)
_sql_log_env = os.getenv('SQL_LOG_ENABLED', 'true').strip().lower()
SQL_LOG_ENABLED = _sql_log_env in ('1', 'true', 'yes', 'on')

# Context variable to store current request path for SQL logging control
_current_request_path: ContextVar[Optional[str]] = ContextVar('current_request_path', default=None)

# Paths that should skip SQL logging
_SKIP_SQL_LOG_PATHS = {"/api/v1/agent-jobs/claim-next"}

# Global engine cache to prevent multiple engine creation
_cached_engine = None

# Global flag to track if database creation has been attempted
_database_creation_attempted = False

# Add SQL query timing event listeners
@event.listens_for(Engine, "before_cursor_execute")
def receive_before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    """Log SQL queries before execution"""
    context._query_start_time = time.time()

@event.listens_for(Engine, "after_cursor_execute")
def receive_after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    """Log SQL query execution time"""
    if SQL_LOG_ENABLED and hasattr(context, '_query_start_time'):
        # Check if we should skip SQL logging for this request path
        current_path = _current_request_path.get()
        if current_path in _SKIP_SQL_LOG_PATHS:
            return  # Skip SQL logging for this path
        
        # Skip logging for table/index creation and existence checks (during initialization)
        statement_upper = statement.upper().strip()
        if any([
            statement_upper.startswith('CREATE TABLE'),
            statement_upper.startswith('CREATE INDEX'),
            statement_upper.startswith('SELECT EXISTS'),
            'SELECT EXISTS' in statement_upper and 'information_schema.tables' in statement_upper,
            # Skip INSERT statements for prompts table (bulk inserts during initialization)
            'INSERT INTO' in statement_upper and 'PROMPTS' in statement_upper,
            # Skip INSERT statements for insight_types table (bulk inserts during initialization)
            'INSERT INTO' in statement_upper and 'INSIGHT_TYPES' in statement_upper,
            # Skip INSERT statements for report_definitions table (bulk inserts during initialization)
            'INSERT INTO' in statement_upper and 'REPORT_DEFINITIONS' in statement_upper
        ]):
            return  # Skip logging for initialization SQL
        
        total_time = time.time() - context._query_start_time
        # Format SQL with proper indentation for readability
        formatted_query = statement.replace('\n', '\n            ')
        logger.info(f"SQL:\n            {formatted_query}\n            - EXECUTE (Duration: {total_time:.3f}s)")


def get_connection_string() -> Optional[str]:
    """
    Get database connection string with priority order:
    1. POSTGRES_* environment variables (first)
    2. DATABASE_URL environment variable (second)
    
    Returns:
        Connection string or None if not configured
    """
    connection_string = None
    
    # Priority 1: Check individual POSTGRES_* environment variables first
    pg_host = os.getenv('POSTGRES_HOST')
    pg_port = os.getenv('POSTGRES_PORT', '5432')
    pg_user = os.getenv('POSTGRES_USER')
    pg_password = os.getenv('POSTGRES_PASSWORD')
    pg_db = os.getenv('POSTGRES_DB')
    
    if all([pg_host, pg_user, pg_password, pg_db]):
        connection_string = f"postgresql://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_db}"
        logger.info(f"🔧 Built DATABASE_URL from POSTGRES_* variables: postgresql://{pg_user}:****@{pg_host}:{pg_port}/{pg_db}")
    else:
        # Priority 2: Check DATABASE_URL environment variable
        db_url_env = os.getenv('DATABASE_URL')
        
        # Check if DATABASE_URL exists and doesn't have unexpanded variables
        if db_url_env and '$(POSTGRES_PASSWORD)' not in db_url_env and '${POSTGRES_PASSWORD}' not in db_url_env:
            connection_string = db_url_env
            if connection_string.startswith('postgres://'):
                connection_string = connection_string.replace('postgres://', 'postgresql://', 1)
            # Add SSL mode for Railway database - require SSL for secure connections
            if 'railway' in connection_string.lower() or 'caboose.proxy.rlwy.net' in connection_string:
                if '?' in connection_string:
                    connection_string += '&sslmode=require'
                else:
                    connection_string += '?sslmode=require'
        else:
            logger.error("Error: Neither POSTGRES_* variables nor DATABASE_URL are configured.")
            return None
    
    return connection_string


def parse_connection_string(connection_string: str) -> Dict[str, Optional[str]]:
    """
    Parse a PostgreSQL connection string into components.
    
    Args:
        connection_string: PostgreSQL connection string (postgresql://user:pass@host:port/dbname)
        
    Returns:
        Dictionary with keys: user, password, host, port, database, sslmode
    """
    try:
        # Parse the URL
        parsed = urlparse(connection_string)
        
        # Extract components
        user = parsed.username
        password = parsed.password
        host = parsed.hostname
        port = parsed.port or '5432'
        database = parsed.path.lstrip('/') if parsed.path else None
        
        # Parse query parameters for SSL mode
        query_params = parse_qs(parsed.query)
        sslmode = query_params.get('sslmode', [None])[0]
        
        return {
            'user': user,
            'password': password,
            'host': host,
            'port': str(port),
            'database': database,
            'sslmode': sslmode
        }
    except Exception as e:
        logger.error(f"Error parsing connection string: {e}")
        return {}


def ensure_database_exists(connection_string: str) -> bool:
    """
    Ensure the database exists. If it doesn't, create it.
    Connects to the default 'postgres' database to create the target database.
    
    Args:
        connection_string: Full PostgreSQL connection string including database name
        
    Returns:
        True if database exists or was created successfully, False otherwise
    """
    global _database_creation_attempted
    
    # Only attempt once per application lifecycle
    if _database_creation_attempted:
        return True
    
    _database_creation_attempted = True
    
    target_database = None
    try:
        # Parse the connection string
        conn_params = parse_connection_string(connection_string)
        
        if not all([conn_params.get('user'), conn_params.get('password'), 
                   conn_params.get('host'), conn_params.get('database')]):
            logger.warning("Cannot ensure database exists: incomplete connection parameters")
            return True  # Continue anyway, let connection attempt fail naturally
        
        target_database = conn_params['database']
        user = conn_params['user']
        password = conn_params['password']
        host = conn_params['host']
        port = conn_params['port']
        sslmode = conn_params.get('sslmode')
        
        # Build connection string to default 'postgres' database
        default_db_conn_str = f"postgresql://{user}:{password}@{host}:{port}/postgres"
        if sslmode:
            default_db_conn_str += f"?sslmode={sslmode}"
        
        logger.info(f"🔍 Checking if database '{target_database}' exists...")
        
        # Connect to default database to check/create target database
        default_engine = create_engine(
            default_db_conn_str,
            isolation_level="AUTOCOMMIT"  # Required for CREATE DATABASE
        )
        
        try:
            with default_engine.connect() as conn:
                # Check if database exists
                check_db_sql = text("""
                    SELECT 1 FROM pg_database WHERE datname = :dbname
                """)
                result = conn.execute(check_db_sql, {"dbname": target_database})
                db_exists = result.scalar() is not None
                
                if db_exists:
                    logger.info(f"✅ Database '{target_database}' already exists")
                else:
                    logger.info(f"📦 Database '{target_database}' does not exist, creating...")
                    # Create the database
                    # Note: CREATE DATABASE cannot be executed in a transaction block
                    create_db_sql = text(f'CREATE DATABASE "{target_database}"')
                    conn.execute(create_db_sql)
                    logger.info(f"✅ Database '{target_database}' created successfully")
                
                return True
        finally:
            default_engine.dispose()
            
    except Exception as e:
        # Log the error but don't fail - the database might already exist
        # or we might not have permissions to create it
        error_msg = str(e).lower()
        db_name = target_database if target_database else 'unknown'
        
        if "already exists" in error_msg or "duplicate" in error_msg:
            logger.info(f"✅ Database '{db_name}' already exists (detected during creation attempt)")
            return True
        elif "permission denied" in error_msg or "access denied" in error_msg:
            logger.warning(f"⚠️  Cannot create database '{db_name}': insufficient permissions. "
                          f"Assuming database exists or will be created manually.")
            return True  # Continue anyway
        else:
            logger.warning(f"⚠️  Could not ensure database exists: {e}. "
                          f"Will attempt to connect anyway - database might already exist.")
            return True  # Continue anyway - let the connection attempt handle the error


def get_db_engine() -> Optional[create_engine]:
    """
    Create and configure the database engine with connection pooling and retry logic.
    Uses caching to prevent multiple engine creation.
    
    Returns:
        SQLAlchemy engine or None if connection fails
    """
    global _cached_engine
    
    # Return cached engine if available
    if _cached_engine is not None:
        return _cached_engine
    
    # Get connection string using shared function
    connection_string = get_connection_string()
    
    if not connection_string:
        logger.error("Error: No database connection string configured.")
        return None
    
    # Add connection pooling and retry settings
    max_retries = 3
    try:
        logger.info("🚀 DATABASE: Creating NEW engine (not from pool) - this should be rare!")
        print("🚀 DATABASE: Creating NEW engine (not from pool) - this should be rare!")
        engine = create_engine(
            connection_string,
            pool_size=10,        # Keep 10 connections in pool
            pool_pre_ping=True,  # Verify connections before use
            pool_recycle=300,    # Recycle connections every 5 minutes
            pool_timeout=30,     # Timeout for getting connection from pool
            max_overflow=5,      # Allow up to 5 extra connections (total: 15 connections)
            echo=False           # Set to True for SQL debugging
        )
        
        # Test connection with retry
        for attempt in range(max_retries):
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                logger.info("✅ DATABASE: New engine created and connection tested successfully.")
                
                # Note: Table initialization is now done explicitly in startup event, not here
                
                logger.info("✅ DATABASE: Engine cached for future reuse (pool connections available)")
                # Cache the engine and return it
                _cached_engine = engine
                return engine
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Database connection attempt {attempt + 1} failed, retrying...")
                    time.sleep(2)  # Wait 2 seconds before retry
                else:
                    raise e
    except Exception as e:
        logger.error(f"Error connecting to database after {max_retries} attempts: {e}")
        # Don't print full traceback to reduce noise
        return None


def get_fresh_db_engine() -> Optional[create_engine]:
    """
    Get a fresh database engine (bypasses any cached engine).
    
    Returns:
        SQLAlchemy engine or None if connection fails
    """
    global _cached_engine
    _cached_engine = None  # Clear cache to force new engine creation
    return get_db_engine()


def safe_db_query(query_func, *args, **kwargs):
    """
    Execute a database query with proper error handling and connection management.
    
    Args:
        query_func: Function that performs the database query
        *args: Positional arguments to pass to query_func
        **kwargs: Keyword arguments to pass to query_func
        
    Returns:
        Result of query_func or None if error occurs
    """
    try:
        return query_func(*args, **kwargs)
    except Exception as e:
        logger.error(f"Database query error: {e}")
        import traceback
        traceback.print_exc()
        return None


# Global engine instance - will be created lazily in startup event
# Set to None initially to prevent creation at module import time
engine = None

# FastAPI Dependencies
from fastapi import HTTPException, Depends
from sqlalchemy.engine import Connection

def get_db_connection():
    """
    FastAPI dependency to get a DB connection from the pool.
    This handles all connection errors and ensures the connection is closed.
    """
    # If engine is None, try to get it (lazy initialization fallback)
    global engine
    if engine is None:
        engine = get_db_engine()
    
    if engine is None:
        logger.error("Database engine is not initialized.")
        raise HTTPException(status_code=503, detail="Database connection not available")
    
    conn = None
    try:
        # Get a connection from the pool
        conn = engine.connect()
        # 'yield' passes the connection to the endpoint function
        yield conn
    except Exception as e:
        # Only log as database error if it's actually a database-related exception
        if "psycopg2" in str(type(e)) or "sqlalchemy" in str(type(e)) or "database" in str(e).lower():
            logger.error(f"Database connection error: {e}")
            raise HTTPException(status_code=503, detail="Database connection error")
        else:
            # Re-raise non-database exceptions (like validation errors) without modification
            raise e
    finally:
        # This code runs *after* the endpoint is finished
        if conn:
            conn.close()  # Returns the connection to the pool
