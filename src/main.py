"""
src/main.py

FastAPI application entry point for the AI SEO Agent MVP.

This module:
  - Creates the FastAPI application instance
  - Mounts the static files directory (HTML, CSS, JS for the UI)
  - Registers all API routers
  - Adds a /health endpoint for basic liveness checking
  - Configures startup and shutdown logging

Run the application locally with:
    uvicorn src.main:app --reload
"""

import contextlib  # contextlib.asynccontextmanager used for the lifespan event handler
import logging  # Standard Python logging used throughout the application
import os  # Used to create the reports directory if it does not exist

from fastapi import FastAPI  # Core FastAPI class that creates the ASGI application
from fastapi.middleware.cors import CORSMiddleware  # CORS middleware allows the UI to call the API from a browser
from fastapi.staticfiles import StaticFiles  # StaticFiles mounts a directory so FastAPI can serve HTML/CSS/JS
from fastapi.responses import JSONResponse  # JSONResponse lets us return structured JSON from the health endpoint

from src.config import get_settings  # Import the settings singleton — reads .env once
from src.api.routes.audit import router as audit_router  # Import the audit route group

# Module-level logger — captures application lifecycle events (startup, shutdown, errors)
logger = logging.getLogger(__name__)  # __name__ resolves to "src.main" in log output

# Load settings once at module import time; all configuration comes from here
settings = get_settings()  # Reads GEMINI_API_KEY, reports_dir, timeouts, etc. from .env

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,  # DEBUG in development, INFO in production
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",  # Structured, easy-to-read log format
)

# ---------------------------------------------------------------------------
# FastAPI application instance
# ---------------------------------------------------------------------------

@contextlib.asynccontextmanager
async def lifespan(application: FastAPI):  # type: ignore[type-arg]
    """Log startup and shutdown messages using the modern lifespan event pattern."""
    logger.info("AI SEO Agent %s started successfully.", settings.app_version)  # Logged when uvicorn is ready
    yield  # Application runs here — yield separates startup logic from shutdown logic
    logger.info("AI SEO Agent shutting down.")  # Logged when uvicorn receives SIGTERM or Ctrl+C


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.app_title,  # "AI SEO Agent" — shown in the /docs Swagger UI header
    version=settings.app_version,  # "0.1.0" — shown in /docs
    description=(
        "AI-powered SEO auditing platform. "
        "Enter a website URL, receive a professional SEO audit report, "
        "and download it as a PDF."
    ),  # Long description shown at the top of /docs
    docs_url="/docs",  # Swagger UI available at http://127.0.0.1:8000/docs
    redoc_url="/redoc",  # ReDoc UI available at http://127.0.0.1:8000/redoc
    lifespan=lifespan,  # Modern lifespan replaces the deprecated on_event decorators
)

# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow any origin in development; tighten this in production
    allow_credentials=False,  # Credentials (cookies/auth) are not used in the MVP
    allow_methods=["GET", "POST"],  # Only the HTTP methods the API uses
    allow_headers=["Content-Type"],  # Only the headers the API uses
)

# ---------------------------------------------------------------------------
# Reports directory
# ---------------------------------------------------------------------------

# Ensure the reports/ directory exists before any audit tries to write files into it
os.makedirs(settings.reports_dir, exist_ok=True)  # exist_ok=True means no error if it already exists
logger.info("Reports directory ready: %s", settings.reports_dir)  # Confirm the directory path at startup

# ---------------------------------------------------------------------------
# API routers
# ---------------------------------------------------------------------------
# IMPORTANT: routers must be registered BEFORE the static files mount.
# In Starlette, routes are evaluated in registration order.  The StaticFiles
# mount at "/" matches every URL (all paths start with "/").  If it is
# registered first, it intercepts ALL requests — including API POST calls —
# and StaticFiles returns 405 for non-GET methods before the router is reached.
# Registering API routes first ensures they are matched before the catch-all mount.

app.include_router(
    audit_router,  # The router defined in src/api/routes/audit.py
    prefix="/api/v1",  # All audit endpoints are prefixed with /api/v1 → /api/v1/audits/
)

# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

@app.get(
    "/health",  # Standard liveness endpoint — confirms the app is running
    tags=["health"],  # Groups this endpoint separately in /docs
    summary="Application health check",
    description="Returns a simple JSON response confirming the application is running.",
)
async def health() -> JSONResponse:
    """
    Return a basic health status response.

    Used to verify the application started correctly.
    Call http://127.0.0.1:8000/health after starting the server.
    """
    return JSONResponse(
        content={
            "status": "ok",  # Confirm the app is running
            "version": settings.app_version,  # Include the version for quick verification
            "app": settings.app_title,  # Include the app name so the caller knows which service responded
        }
    )

# ---------------------------------------------------------------------------
# Static files (UI) — MUST be last
# ---------------------------------------------------------------------------
# Registered after all API routes so the catch-all mount at "/" does not
# intercept API POST requests before the router can handle them.

_static_dir = "src/static"  # Path to the folder that holds index.html, styles.css, app.js
if os.path.isdir(_static_dir):
    app.mount(
        "/",  # Mount at the root path so http://127.0.0.1:8000/ serves index.html
        StaticFiles(directory=_static_dir, html=True),  # html=True means index.html is the default file
        name="static",  # Internal name used by FastAPI to refer to this mount
    )
    logger.info("Static UI mounted from: %s", _static_dir)  # Confirm the UI directory was found
else:
    logger.warning("Static UI directory not found (%s).", _static_dir)

