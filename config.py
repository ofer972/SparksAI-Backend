# FILE: config.py
# Central configuration file for SparksAI Backend Services

# --- API Configuration ---
API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"

# --- Database Configuration ---
# Database connection is handled in database_connection.py
# Uses config.ini with fallback to DATABASE_URL environment variable

# --- Service Configuration ---
# Teams Service
TEAMS_SERVICE_PREFIX = "/teams"

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
