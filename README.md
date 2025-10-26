# SparksAI Backend Services

A Python FastAPI backend service that provides REST API endpoints for various services, with PostgreSQL database integration. Uses the exact same database connection pattern as JiraDashboard-NEWUI.

## Features

- FastAPI framework for high-performance REST APIs
- PostgreSQL database integration with connection pooling
- Modular service architecture (domain-based)
- Railway deployment ready
- Local development support
- CORS enabled for frontend integration
- Consistent error handling with best practices

## Project Structure

```
├── main.py                    # Main FastAPI application
├── requirements.txt           # Python dependencies
├── config.ini                 # Database configuration (same as JiraDashboard-NEWUI)
├── config.py                  # Application configuration
├── database_connection.py     # Database connection handling (same pattern as JiraDashboard-NEWUI)
├── teams_service.py           # Teams service endpoints
├── Dockerfile                 # Docker configuration
├── railway.json              # Railway deployment config
└── README.md                 # This file
```

## Setup

### Local Development

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Database Configuration:**
   The application uses the same database connection pattern as JiraDashboard-NEWUI:
   - Uses `config.ini` with `[database]` section and `connection_string`
   - Falls back to `DATABASE_URL` environment variable if config.ini doesn't exist
   - Automatically handles Railway SSL requirements
   - Uses connection pooling (pool_size=5, pool_pre_ping=True, pool_recycle=300)

3. **Run the application:**
   ```bash
   python main.py
   # or
   uvicorn main:app --reload
   ```

The API will be available at `http://localhost:8000`

### Railway Deployment

1. **Connect your GitHub repository to Railway**
2. **Add PostgreSQL service in Railway dashboard**
3. **Set environment variables in Railway:**
   - `DATABASE_URL`: Your PostgreSQL connection string (optional, uses config.ini by default)
   - `PORT`: Railway will set this automatically
4. **Deploy**: Railway will automatically build and deploy using the Dockerfile

## API Endpoints

### Teams Service

- `GET /api/v1/teams/getNames` - Get all distinct team names from jira_users table
- `GET /api/v1/teams/{team_name}/users` - Get users for a specific team
- `GET /api/v1/teams/stats` - Get team statistics

### General Endpoints

- `GET /` - API information
- `GET /health` - Health check
- `GET /docs` - Interactive API documentation (Swagger UI)
- `GET /redoc` - Alternative API documentation

## API Response Format

### Success Response
```json
{
  "success": true,
  "data": {
    "teams": ["Team A", "Team B", "Team C"],
    "count": 3
  },
  "message": "Retrieved 3 team names"
}
```

### Error Response
```json
{
  "error": "Database connection not available",
  "code": 500
}
```

## Adding New Services

To add a new service (e.g., issues service):

1. **Create a new service file** (e.g., `issues_service.py`):
   ```python
   from fastapi import APIRouter, HTTPException
   from sqlalchemy import text
   from database_connection import engine
   import logging
   
   logger = logging.getLogger(__name__)
   issues_router = APIRouter()
   
   @issues_router.get("/issues/getAll")
   async def get_all_issues():
       # Your endpoint logic here
       pass
   ```

2. **Import and include the router in `main.py`**:
   ```python
   from issues_service import issues_router
   app.include_router(issues_router, prefix="/api/v1", tags=["issues"])
   ```

## Database Connection

The application uses the exact same database connection pattern as JiraDashboard-NEWUI:

- **Connection Pooling**: 5 connections in pool with pre-ping enabled
- **Retry Logic**: 3 attempts with 2-second delays
- **SSL Handling**: Automatic SSL mode for Railway connections
- **Caching**: Engine caching to prevent multiple engine creation
- **Error Handling**: Comprehensive error handling and logging

## Environment Variables

- `DATABASE_URL`: PostgreSQL connection string (optional, uses config.ini by default)
- `PORT`: Server port (default: 8000)
- `RAILWAY_ENVIRONMENT`: Set by Railway for production deployment

## Service Organization

Services are organized by domain:
- **Teams Service**: Team management, user retrieval, team statistics
- **Issues Service**: Issue management, sprint data, burndown charts (future)
- **Reports Service**: Various reports and analytics (future)
- **Analytics Service**: Predictability, cycle time metrics (future)

Each service can have multiple endpoints and is contained in its own file for easy maintenance and scaling to 30-40 services.
