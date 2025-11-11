from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
import os
import logging
import base64
import time
from database_connection import _current_request_path

# Import service modules
from teams_service import teams_router
from groups_service import groups_router
from recommendations_service import recommendations_router
from team_ai_cards_service import team_ai_cards_router
from team_metrics_service import team_metrics_router
from settings_service import settings_router
from pis_service import pis_router
from agent_jobs_service import agent_jobs_router
from security_logs_service import security_logs_router
from pi_ai_cards_service import pi_ai_cards_router
from transcripts_service import transcripts_router
from prompts_service import prompts_router
from reports_service import reports_router
from ai_chat_service import ai_chat_router
from agent_llm_service import agent_llm_router
from users_service import users_router
from issues_service import issues_router
from sprints_service import sprints_router
from insight_types_service import insight_types_router

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ANSI color codes for terminal output
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    # HTTP Method colors
    GET = '\033[92m'      # Bright Green
    POST = '\033[96m'     # Cyan (Bright Cyan)
    PUT = '\033[93m'      # Yellow
    PATCH = '\033[34m'    # Dark Blue
    DELETE = '\033[95m'   # Magenta
    DEFAULT = '\033[90m'  # Gray
    # HTTP Status code colors
    STATUS_SUCCESS = '\033[92m'      # Bright Green (for 2xx)
    STATUS_CLIENT_ERROR = '\033[93m' # Yellow (for 4xx)
    STATUS_SERVER_ERROR = '\033[91m' # Red (for 5xx)

# Emoji mapping for HTTP methods
METHOD_EMOJIS = {
    'GET': '📥',
    'POST': '📤',
    'PUT': '✏️',
    'PATCH': '🔧',
    'DELETE': '🗑️',
}

def get_method_style(method: str) -> tuple[str, str]:
    """Returns (color_code, emoji) for HTTP method"""
    method_upper = method.upper()
    emoji = METHOD_EMOJIS.get(method_upper, '📡')
    
    color_map = {
        'GET': Colors.GET,
        'POST': Colors.POST,
        'PUT': Colors.PUT,
        'PATCH': Colors.PATCH,
        'DELETE': Colors.DELETE,
    }
    color = color_map.get(method_upper, Colors.DEFAULT)
    
    return color, emoji


def get_status_code_colors(status_code: int, method_color: str) -> tuple[str, str]:
    """
    Returns (log_line_color, status_code_color) based on status code.
    
    Logic:
    - 2xx (success): Keep method color for line, green (bold) for status code
    - 4xx (client error): Yellow (bold) for entire line
    - 5xx (server error): Red (bold) for entire line
    - Other: Default behavior
    
    Args:
        status_code: HTTP status code
        method_color: Original method color to preserve for 2xx
    
    Returns:
        tuple: (log_line_color, status_code_color)
    """
    if 200 <= status_code < 300:
        # 2xx - Keep method color, status code gets green (bold)
        return method_color, Colors.STATUS_SUCCESS
    elif 400 <= status_code < 500:
        # 4xx - Yellow (bold) for entire line
        return Colors.STATUS_CLIENT_ERROR, Colors.STATUS_CLIENT_ERROR
    elif status_code >= 500:
        # 5xx - Red (bold) for entire line
        return Colors.STATUS_SERVER_ERROR, Colors.STATUS_SERVER_ERROR
    else:
        # 1xx, 3xx - Default color
        return method_color, Colors.DEFAULT

# Simple comment for testing commit and push

app = FastAPI(
    title="SparksAI Backend Services",
    description="Backend API services for SparksAI - REST API endpoints for various services",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Paths that should skip START and END message logging
_SKIP_LOG_PATHS = {"/api/v1/agent-jobs/claim-next"}

# Add timing middleware to log request/response times
@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    start_time = time.time()
    color, emoji = get_method_style(request.method)
    request_path = request.url.path
    
    # Set request path in context variable for SQL logging control
    _current_request_path.set(request_path)
    
    try:
        # Skip START logging for specific paths
        if request_path not in _SKIP_LOG_PATHS:
            logger.info(f"{color}{emoji} REQUEST: {request.method} {request_path} - START{Colors.RESET}")
        
        response = await call_next(request)
        
        end_time = time.time()
        duration = end_time - start_time
        status_code = response.status_code
        
        # Always log errors (4xx, 5xx) even for suppressed paths
        # Skip successful responses (2xx, 3xx) for specific paths
        should_log = request_path not in _SKIP_LOG_PATHS or status_code >= 400
        
        if should_log:
            # Get colors based on status code
            # For 2xx: keep method color, for 4xx/5xx: override with status color
            log_line_color, status_color = get_status_code_colors(status_code, color)
            
            # Apply bold formatting
            if 400 <= status_code < 500:
                # 4xx - Entire line yellow (bold)
                bold_prefix = Colors.BOLD
                status_bold = Colors.BOLD
            elif status_code >= 500:
                # 5xx - Entire line red (bold)
                bold_prefix = Colors.BOLD
                status_bold = Colors.BOLD
            elif 200 <= status_code < 300:
                # 2xx - Status code green (bold), line keeps method color (no bold on line)
                bold_prefix = ""
                status_bold = Colors.BOLD
            else:
                # 1xx, 3xx - Default, no bold
                bold_prefix = ""
                status_bold = ""
            
            logger.info(
                f"{log_line_color}{bold_prefix}{emoji} REQUEST: {request.method} {request_path} - "
                f"END (Duration: {duration:.3f}s) - Status: {status_color}{status_bold}{status_code}{Colors.RESET}"
            )
        
        return response
    finally:
        # Clear context variable after request
        _current_request_path.set(None)

# Include service routers
app.include_router(teams_router, prefix="/api/v1", tags=["teams"])
app.include_router(groups_router, prefix="/api/v1", tags=["groups"])
app.include_router(recommendations_router, prefix="/api/v1", tags=["recommendations"])
app.include_router(team_ai_cards_router, prefix="/api/v1", tags=["team-ai-cards"])
app.include_router(team_metrics_router, prefix="/api/v1", tags=["team-metrics"])
app.include_router(settings_router, prefix="/api/v1", tags=["settings"])
app.include_router(pis_router, prefix="/api/v1", tags=["pis"])
app.include_router(agent_jobs_router, prefix="/api/v1", tags=["agent-jobs"])
app.include_router(security_logs_router, prefix="/api/v1", tags=["security-logs"])
app.include_router(pi_ai_cards_router, prefix="/api/v1", tags=["pi-ai-cards"])
app.include_router(transcripts_router, prefix="/api/v1", tags=["transcripts"])
app.include_router(prompts_router, prefix="/api/v1", tags=["prompts"])
app.include_router(ai_chat_router, prefix="/api/v1", tags=["ai-chat"])
app.include_router(agent_llm_router, prefix="/api/v1", tags=["agent-llm"])
app.include_router(users_router, prefix="/api/v1", tags=["users"])
app.include_router(issues_router, prefix="/api/v1", tags=["issues"])
app.include_router(sprints_router, prefix="/api/v1", tags=["sprints"])
app.include_router(insight_types_router, prefix="/api/v1", tags=["insight-types"])
app.include_router(reports_router, prefix="/api/v1", tags=["reports"])

@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "message": "SparksAI Backend Services API", 
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "SparksAI Backend"}

@app.get("/favicon.ico")
async def favicon():
    """Dashboard favicon endpoint"""
    # Create a simple dashboard icon (16x16 pixel favicon)
    # This is a minimal SVG-based favicon representing a dashboard
    favicon_svg = """
    <svg width="16" height="16" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">
        <rect width="16" height="16" fill="#007bff"/>
        <rect x="2" y="2" width="4" height="4" fill="white" rx="1"/>
        <rect x="8" y="2" width="4" height="4" fill="white" rx="1"/>
        <rect x="2" y="8" width="4" height="4" fill="white" rx="1"/>
        <rect x="8" y="8" width="4" height="4" fill="white" rx="1"/>
        <circle cx="14" cy="14" r="1.5" fill="white"/>
    </svg>
    """
    
    return Response(
        content=favicon_svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=31536000"}
    )

# Global exception handler for consistent error responses
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler for consistent error responses"""
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "message": "An unexpected error occurred",
            "code": 500
        }
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """HTTP exception handler for consistent error responses"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "code": exc.status_code
        }
    )

if __name__ == "__main__":
    import uvicorn
    import sys
    
    # Check for port argument in command line first
    port = None
    if len(sys.argv) > 1:
        for i, arg in enumerate(sys.argv):
            if arg == "--port" and i + 1 < len(sys.argv):
                try:
                    port = int(sys.argv[i + 1])
                    break
                except ValueError:
                    print(f"Invalid port number: {sys.argv[i + 1]}")
                    sys.exit(1)
    
    # If no command line port, use environment variable, then default
    if port is None:
        port = int(os.getenv("PORT", 8000))
    
    # Use 1 worker by default (can override with WORKERS env var)
    # Note: Multiple workers require app as import string (use: workers=1 for development)
    workers = int(os.getenv("WORKERS", 1))
    
    print(f"Starting server on port {port} with {workers} worker(s)")
    uvicorn.run(app, host="0.0.0.0", port=port, workers=workers, access_log=False)
