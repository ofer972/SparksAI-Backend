from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
import os
import logging
import base64
import time

# Import service modules
from teams_service import teams_router
from recommendations_service import recommendations_router
from team_ai_cards_service import team_ai_cards_router
from team_metrics_service import team_metrics_router
from settings_service import settings_router
from pis_service import pis_router
from agent_jobs_service import agent_jobs_router

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

# Add timing middleware to log request/response times
@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    start_time = time.time()
    logger.info(f"REQUEST: {request.method} {request.url.path} - START")
    
    response = await call_next(request)
    
    end_time = time.time()
    duration = end_time - start_time
    logger.info(f"REQUEST: {request.method} {request.url.path} - END (Duration: {duration:.3f}s)")
    
    return response

# Include service routers
app.include_router(teams_router, prefix="/api/v1", tags=["teams"])
app.include_router(recommendations_router, prefix="/api/v1", tags=["recommendations"])
app.include_router(team_ai_cards_router, prefix="/api/v1", tags=["team-ai-cards"])
app.include_router(team_metrics_router, prefix="/api/v1", tags=["team-metrics"])
app.include_router(settings_router, prefix="/api/v1", tags=["settings"])
app.include_router(pis_router, prefix="/api/v1", tags=["pis"])
app.include_router(agent_jobs_router, prefix="/api/v1", tags=["agent-jobs"])

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
    uvicorn.run(app, host="0.0.0.0", port=port, workers=workers)
