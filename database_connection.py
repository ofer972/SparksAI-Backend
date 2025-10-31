"""
Database connection utilities for SparksAI Backend Services.

This module handles database engine creation, connection management,
and related utilities. Uses the exact same pattern as JiraDashboard-NEWUI.
"""

import configparser
import os
import time
from sqlalchemy import create_engine, text, event
from sqlalchemy.engine import Engine
from typing import Optional
import logging
import os

logger = logging.getLogger(__name__)

# Control SQL logging via environment variable (defaults to enabled)
_sql_log_env = os.getenv('SQL_LOG_ENABLED', 'true').strip().lower()
SQL_LOG_ENABLED = _sql_log_env in ('1', 'true', 'yes', 'on')

# Global engine cache to prevent multiple engine creation
_cached_engine = None

# Add SQL query timing event listeners
@event.listens_for(Engine, "before_cursor_execute")
def receive_before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    """Log SQL queries before execution"""
    context._query_start_time = time.time()

@event.listens_for(Engine, "after_cursor_execute")
def receive_after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    """Log SQL query execution time"""
    if SQL_LOG_ENABLED and hasattr(context, '_query_start_time'):
        total_time = time.time() - context._query_start_time
        # Truncate long queries for readability
        query = statement if len(statement) < 200 else statement[:200] + "..."
        logger.info(f"SQL: {query} - EXECUTE (Duration: {total_time:.3f}s)")


def get_db_engine() -> Optional[create_engine]:
    """
    Create and configure the database engine with connection pooling and retry logic.
    Uses caching to prevent multiple engine creation.
    Uses the exact same pattern as JiraDashboard-NEWUI.
    
    Returns:
        SQLAlchemy engine or None if connection fails
    """
    global _cached_engine
    
    # Return cached engine if available
    if _cached_engine is not None:
        return _cached_engine
    
    connection_string = None
    db_url_env = os.getenv('DATABASE_URL')
    
    if db_url_env:
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
        cfg_parser = configparser.ConfigParser()
        try:
            cfg_parser.read('config.ini')
            connection_string = cfg_parser['database']['connection_string']
            
            # Add SSL mode for Railway database connections (from config.ini)
            # Only require SSL when running on Railway (production)
            if ('railway' in connection_string.lower() or 'caboose.proxy.rlwy.net' in connection_string) and os.getenv('RAILWAY_ENVIRONMENT'):
                if '?' in connection_string:
                    connection_string += '&sslmode=require'
                else:
                    connection_string += '?sslmode=require'
        except FileNotFoundError:
            logger.error("Error: config.ini not found.")
            return None
        except KeyError:
            logger.error("Error: 'database' section or 'connection_string' not found in config.ini.")
            return None

    if not connection_string:
        logger.error("Error: No database connection string configured.")
        return None
    
    # Add connection pooling and retry settings (same as JiraDashboard-NEWUI)
    max_retries = 3
    try:
        logger.info("🚀 DATABASE: Creating NEW engine (not from pool) - this should be rare!")
        print("🚀 DATABASE: Creating NEW engine (not from pool) - this should be rare!")
        engine = create_engine(
            connection_string,
            pool_size=20,        # Keep 20 connections in pool
            pool_pre_ping=True,  # Verify connections before use
            pool_recycle=300,    # Recycle connections every 5 minutes
            pool_timeout=30,     # Timeout for getting connection from pool
            max_overflow=10,     # Allow up to 10 extra connections (total: 30 connections)
            echo=False           # Set to True for SQL debugging
        )
        
        # Test connection with retry
        for attempt in range(max_retries):
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                logger.info("✅ DATABASE: New engine created and connection tested successfully.")
                
                # Initialize database tables on new engine creation (not from pool)
                try:
                    from database_table_creation import initialize_database_tables_with_engine
                    initialize_database_tables_with_engine(engine)
                except Exception as table_error:
                    logger.warning(f"Database table initialization failed: {table_error}")
                    # Continue anyway - tables might already exist
                
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


# Global engine instance
engine = get_db_engine()

# FastAPI Dependencies
from fastapi import HTTPException, Depends
from sqlalchemy.engine import Connection

def get_db_connection():
    """
    FastAPI dependency to get a DB connection from the pool.
    This handles all connection errors and ensures the connection is closed.
    """
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
