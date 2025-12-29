# FILE: config.py
# Central configuration file for SparksAI Backend Services
import os
from dotenv import load_dotenv

# Load environment variables from .env file (for local development)
load_dotenv()

# --- API Configuration ---
API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"

# --- Database Configuration ---
# Database connection is handled in database_connection.py
# Uses POSTGRES_* environment variables or DATABASE_URL environment variable

# --- Table Names Configuration ---
WORK_ITEMS_TABLE = "jira_issues"  # Main table name - can be changed here
RECOMMENDATIONS_TABLE = "recommendations"  # Recommendations table
PIS_TABLE = "pis"  # PIs table
CLOSED_SPRINT_VIEW = "closed_sprint_summary"  # Closed sprint summary view
AGENT_JOBS_TABLE = "agent_jobs"  # Agent jobs table
SECURITY_LOGS_TABLE = "security_logs"  # Security logs table
AI_INSIGHTS_TABLE = "ai_summary"  # Unified AI insights table (team, group, PI)
TRANSCRIPTS_TABLE = "transcripts"  # Transcripts table
AI_SUMMARY_TABLE = "ai_summary"  # AI summary table
PROMPTS_TABLE = "prompts"  # Prompts table
CHAT_HISTORY_TABLE = "chat_history"  # Chat history table
INSIGHT_TYPES_TABLE = "insight_types"  # Insight types table
ISSUE_TYPES_TABLE = "issue_types"  # Issue types table
REPORT_DEFINITIONS_TABLE = "report_definitions"  # Report definitions metadata table

# --- Service Configuration ---
# Teams Service
TEAMS_SERVICE_PREFIX = "/teams"
# Recommendations Service
RECOMMENDATIONS_SERVICE_PREFIX = "/recommendations"
# Team Metrics Service
TEAM_METRICS_SERVICE_PREFIX = "/team-metrics"
# Settings Service
SETTINGS_SERVICE_PREFIX = "/settings"
# PIs Service
PIS_SERVICE_PREFIX = "/pis"
# Agent Jobs Service
AGENT_JOBS_SERVICE_PREFIX = "/agent-jobs"
# Security Logs Service
SECURITY_LOGS_SERVICE_PREFIX = "/security-logs"
# AI Insights Service (unified)
AI_INSIGHTS_SERVICE_PREFIX = "/ai-insights"
# Transcripts Service
TRANSCRIPTS_SERVICE_PREFIX = "/transcripts"
# Prompts Service
PROMPTS_SERVICE_PREFIX = "/prompts"
# AI Chat Service
AI_CHAT_SERVICE_PREFIX = "/ai-chat"
# Issues Service
ISSUES_SERVICE_PREFIX = "/issues"
# Sprints Service
SPRINTS_SERVICE_PREFIX = "/sprints"
# Insight Types Service
INSIGHT_TYPES_SERVICE_PREFIX = "/insight-types"

# --- Error Handling Configuration ---
DEFAULT_ERROR_MESSAGE = "An unexpected error occurred"
DEFAULT_ERROR_CODE = 500

# --- Logging Configuration ---
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# --- CORS Configuration ---
CORS_ORIGINS = ["*"]  # Configure appropriately for production
CORS_METHODS = ["*"]
CORS_HEADERS = ["*"]

# --- Server Configuration ---
DEFAULT_PORT = 8000
DEFAULT_HOST = "0.0.0.0"

# --- LLM Service Configuration ---
LLM_SERVICE_URL = os.getenv("LLM_SERVICE_URL", "http://localhost:8001")

# --- SparksAI-SQL Service Configuration ---
LLM_SQL_SERVICE_URL = os.getenv("LLM_SQL_SERVICE_URL", "http://localhost:8002")

# --- SQL AI Trigger Configuration ---
SQL_AI_TRIGGER = "!"  # Trigger character to detect SQL queries in user questions (first character check)

# --- Redis Configuration ---
REDIS_HOST = os.getenv("REDIS_HOST") or "localhost"
REDIS_PORT = int(os.getenv("REDIS_PORT") or "6379")
REDIS_DB = int(os.getenv("REDIS_DB") or "0")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None
REDIS_ENABLED = (os.getenv("REDIS_ENABLED") or "true").lower() == "true"

# Cache TTL defaults (in seconds) by report type
CACHE_TTL_REALTIME = int(os.getenv("CACHE_TTL_REALTIME") or "60")  # 1 minute
CACHE_TTL_AGGREGATE = int(os.getenv("CACHE_TTL_AGGREGATE") or "300")  # 5 minutes
CACHE_TTL_HISTORICAL = int(os.getenv("CACHE_TTL_HISTORICAL") or "1800")  # 30 minutes
CACHE_TTL_DEFINITIONS = int(os.getenv("CACHE_TTL_DEFINITIONS") or "3600")  # 1 hour
CACHE_TTL_GROUPS_TEAMS = int(os.getenv("CACHE_TTL_GROUPS_TEAMS") or "3600")  # 1 hour

# --- JIRA URL Configuration ---
from typing import Optional, Dict, Any
from sqlalchemy.engine import Connection
import logging

_jira_url_logger = logging.getLogger(__name__)
_jira_url: Optional[str] = None
_jira_cloud: Optional[bool] = None

def get_jira_url(conn: Optional[Connection] = None) -> Dict[str, Any]:
    """
    Get JIRA URL and Cloud flag from cache or database.
    Returns both together since they're connected.
    
    - Returns cached values if available
    - If null and connection provided, retries from database (loads both URL and cloud)
    
    Args:
        conn: Optional database connection for retry
        
    Returns:
        Dict with "url" and "is_cloud" keys, or {"url": None, "is_cloud": None} if not found
    """
    global _jira_url, _jira_cloud
    
    # Return cached values if available
    if _jira_url is not None or _jira_cloud is not None:
        return {"url": _jira_url, "is_cloud": _jira_cloud}
    
    # If null, retry if connection available - load both URL and cloud together
    if conn is not None:
        try:
            from database_general import get_etl_setting_from_db
            db_url = get_etl_setting_from_db(conn, "jira_url", None)
            _jira_url = db_url
            if db_url:
                _jira_url_logger.info(f"✅ JIRA URL loaded from ETL settings: {db_url}")
            
            # Also load cloud setting when loading URL (they're connected)
            cloud_setting = get_etl_setting_from_db(conn, "jira_cloud", None)
            if cloud_setting:
                cloud_setting_lower = cloud_setting.lower().strip()
                _jira_cloud = cloud_setting_lower in ("true", "cloud", "1", "yes")
                if _jira_cloud is not None:
                    _jira_url_logger.info(f"✅ JIRA Cloud setting loaded from ETL settings: {_jira_cloud}")
            
            return {"url": _jira_url, "is_cloud": _jira_cloud}
        except Exception as e:
            _jira_url_logger.warning(f"⚠️  Failed to load JIRA settings from database: {e}")
            return {"url": None, "is_cloud": None}
    
    return {"url": None, "is_cloud": None}

def set_jira_url(url: Optional[str], is_cloud: Optional[bool] = None) -> None:
    """
    Set JIRA URL and Cloud flag (called at startup).
    They're set together since they're connected.
    
    Args:
        url: JIRA URL to cache (can be None)
        is_cloud: JIRA Cloud flag (True for Cloud, False for Data Center, None if not found)
    """
    global _jira_url, _jira_cloud
    _jira_url = url
    if is_cloud is not None:
        _jira_cloud = is_cloud

def get_jira_sprint_report_url(project_key: Optional[str], board_id: str, sprint_id: str, conn: Optional[Connection] = None) -> Optional[str]:
    """
    Construct JIRA sprint report URL.
    
    Args:
        project_key: Project key (required for Cloud, not needed for Data Center)
        board_id: Board ID (required for both)
        sprint_id: Sprint ID (required for both - used in sprint parameter)
        conn: Optional database connection for retry
        
    Returns:
        Full sprint report URL or None
    """
    jira_settings = get_jira_url(conn)
    jira_url = jira_settings.get("url")
    is_cloud = jira_settings.get("is_cloud")
    
    if not jira_url or not board_id or not sprint_id:
        return None
    
    # Auto-detect if is_cloud is None
    if is_cloud is None:
        is_cloud = "atlassian.net" in jira_url.lower()
    
    if is_cloud:
        # Cloud format: /jira/software/c/projects/[project_key]/boards/[board_id]/reports/sprint-retrospective?sprint=[sprint_id]
        if not project_key:
            return None
        base_url = jira_url.rstrip("/")
        url = f"{base_url}/jira/software/c/projects/{project_key}/boards/{board_id}/reports/sprint-retrospective?sprint={sprint_id}"
        return url
    else:
        # Data Center format: /secure/RapidBoard.jspa?rapidView=[board_id]&view=reporting&chart=sprintRetrospective&sprint=[sprint_id]
        base_url = jira_url.rstrip("/")
        url = f"{base_url}/secure/RapidBoard.jspa?rapidView={board_id}&view=reporting&chart=sprintRetrospective&sprint={sprint_id}"
        return url

def get_jira_closed_sprint_report_url(project_key: Optional[str], board_id: str, conn: Optional[Connection] = None) -> Optional[str]:
    """
    Construct JIRA closed sprint report URL (no sprint parameter).
    
    Args:
        project_key: Project key (required for Cloud, not needed for Data Center)
        board_id: Board ID (required for both)
        conn: Optional database connection for retry
        
    Returns:
        Full closed sprint report URL or None
    """
    jira_settings = get_jira_url(conn)
    jira_url = jira_settings.get("url")
    is_cloud = jira_settings.get("is_cloud")
    
    if not jira_url or not board_id:
        return None
    
    # Auto-detect if is_cloud is None
    if is_cloud is None:
        is_cloud = "atlassian.net" in jira_url.lower()
    
    if is_cloud:
        # Cloud format - needs project_key
        if not project_key:
            return None
        base_url = jira_url.rstrip("/")
        return f"{base_url}/jira/software/projects/{project_key}/boards/{board_id}/reports/sprint-report"
    else:
        # Data Center format - no project_key needed
        base_url = jira_url.rstrip("/")
        return f"{base_url}/secure/RapidBoard.jspa?rapidView={board_id}&view=reporting&chart=sprintRetrospective"

