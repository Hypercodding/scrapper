"""Standalone FastAPI application for the Indeed scraper service."""
from fastapi import FastAPI
from app.routes.indeed_routes import router

app = FastAPI(
    title="Indeed Scraper API",
    description="Async Indeed job scraping backed by Celery workers on Railway.",
    version="1.0.0",
)

app.include_router(router, prefix="/api", tags=["Indeed"])


@app.get("/")
def root():
    return {
        "service": "Indeed Scraper API",
        "endpoints": {
            "search_get": "GET /api/jobs/search?query=...&location=...&max_results=20",
            "search_post": "POST /api/jobs/search  (JSON body)",
            "poll_status": "GET /api/jobs/{job_id}",
            "cancel": "DELETE /api/jobs/{job_id}",
            "health": "GET /api/health",
            "worker_health": "GET /api/health/workers",
        },
    }
