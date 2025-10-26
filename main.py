from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
import logging

# Import service modules
from teams_service import teams_router

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
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
