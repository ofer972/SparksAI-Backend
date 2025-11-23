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
# Uses config.ini with fallback to DATABASE_URL environment variable

# --- Table Names Configuration ---
WORK_ITEMS_TABLE = "jira_issues"  # Main table name - can be changed here
RECOMMENDATIONS_TABLE = "recommendations"  # Recommendations table
TEAM_AI_CARDS_TABLE = "ai_summary"  # Team AI cards table (uses ai_summary)
PIS_TABLE = "pis"  # PIs table
CLOSED_SPRINT_VIEW = "closed_sprint_summary"  # Closed sprint summary view
AGENT_JOBS_TABLE = "agent_jobs"  # Agent jobs table
SECURITY_LOGS_TABLE = "security_logs"  # Security logs table
PI_AI_CARDS_TABLE = "ai_summary"  # PI AI cards table
TRANSCRIPTS_TABLE = "transcripts"  # Transcripts table
AI_SUMMARY_TABLE = "ai_summary"  # AI summary table
PROMPTS_TABLE = "prompts"  # Prompts table
CHAT_HISTORY_TABLE = "chat_history"  # Chat history table
INSIGHT_TYPES_TABLE = "insight_types"  # Insight types table
REPORT_DEFINITIONS_TABLE = "report_definitions"  # Report definitions metadata table

# --- Service Configuration ---
# Teams Service
TEAMS_SERVICE_PREFIX = "/teams"
# Recommendations Service
RECOMMENDATIONS_SERVICE_PREFIX = "/recommendations"
# Team AI Cards Service
TEAM_AI_CARDS_SERVICE_PREFIX = "/team-ai-cards"
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
# PI AI Cards Service
PI_AI_CARDS_SERVICE_PREFIX = "/pi-ai-cards"
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