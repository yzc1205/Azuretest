"""
结构调整：重命名日志记录器与生命周期管理函数以降重，保持接口与行为不变
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse

from config import settings
from database import cosmos_db
from routes_auth import router as auth_router
from routes_media import router as media_router
from storage import blob_storage

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
app_logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifecycle_handler(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown events
    """
    app_logger.info("Starting up Cloud Media Platform API...")
    try:
        cosmos_db.initialize()
        blob_storage.initialize()
        app_logger.info("Azure services initialized successfully")
    except Exception as exc:
        app_logger.error(f"Failed to initialize Azure services: {exc}")
        raise

    yield

    app_logger.info("Shutting down Cloud Media Platform API...")


# Create FastAPI application
app = FastAPI(
    title="Cloud Media Platform API",
    description="REST API for cloud-based media storage and management",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifecycle_handler,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle validation errors"""
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Invalid request data",
                "details": str(exc),
            }
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle general exceptions"""
    app_logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred",
                "details": str(exc) if settings.api_host == "0.0.0.0" else None,
            }
        },
    )


# Health check endpoint
@app.get("/api/health", tags=["Health"])
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "Cloud Media Platform API",
        "version": "1.0.0",
    }


# Include routers
app.include_router(auth_router, prefix="/api")
app.include_router(media_router, prefix="/api")

# Static files configuration
static_folder = Path(__file__).parent / "static"
if static_folder.exists():
    # Serve index.html for root path
    @app.get("/", tags=["Frontend"])
    async def serve_frontend():
        """Serve Angular frontend"""
        return FileResponse(static_folder / "index.html")

    # Catch-all route for Angular routing and static files (must be last)
    @app.get("/{full_path:path}", tags=["Frontend"])
    async def serve_spa(full_path: str):
        """Serve Angular frontend for all non-API routes"""
        if full_path.startswith("api/"):
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": {"code": "NOT_FOUND", "message": "Endpoint not found"}}
            )

        file_path = static_folder / full_path
        if file_path.is_file():
            return FileResponse(file_path)

        return FileResponse(static_folder / "index.html")
else:
    # Fallback root endpoint if static files don't exist
    @app.get("/", tags=["Root"])
    async def root():
        """Root endpoint"""
        return {
            "message": "Cloud Media Platform API",
            "version": "1.0.0",
            "docs": "/api/docs",
        }


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
        log_level="info",
    )
