from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
import os
import logging
import base64

# Import service modules
from teams_service import teams_router
from recommendations_service import recommendations_router
from team_ai_cards_service import team_ai_cards_router

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

# Include service routers
app.include_router(teams_router, prefix="/api/v1", tags=["teams"])
app.include_router(recommendations_router, prefix="/api/v1", tags=["recommendations"])
app.include_router(team_ai_cards_router, prefix="/api/v1", tags=["team-ai-cards"])

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
    
    print(f"Starting server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
