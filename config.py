# FILE: config.py
# Central configuration file for SparksAI Backend Services

# --- API Configuration ---
API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"

# --- Database Configuration ---
# Database connection is handled in database_connection.py
# Uses config.ini with fallback to DATABASE_URL environment variable

# --- Table Names Configuration ---
WORK_ITEMS_TABLE = "jira_issues"  # Main table name - can be changed here
RECOMMENDATIONS_TABLE = "recommendations"  # Recommendations table
TEAM_AI_CARDS_TABLE = "team_ai_summary_cards"  # Team AI cards table
PIS_TABLE = "pis"  # PIs table

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
