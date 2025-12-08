from fastapi import FastAPI
from app.routes import job_routes # pylint: disable=import-error
from app.core.config import settings # pylint: disable=import-error

app = FastAPI(title=settings.PROJECT_NAME)

app.include_router(job_routes.router, prefix="/api", tags=["Jobs"])


@app.on_event("startup")
async def startup_event():
    """Clean up any leftover Chrome processes from previous runs"""
    from app.services.indeed_selenium_service import cleanup_zombie_processes
    print("🚀 Application starting up...")
    print("🧹 Cleaning up any leftover Chrome processes from previous runs...")
    killed = cleanup_zombie_processes(aggressive=True)
    if killed > 0:
        print(f"   ✓ Cleaned up {killed} leftover process(es)")
    else:
        print("   ✓ No leftover processes found")


@app.on_event("shutdown")
async def shutdown_event():
    """Ensure all Chrome resources are cleaned up on shutdown"""
    from app.services.indeed_selenium_service import cleanup_global_driver, cleanup_zombie_processes
    print("🛑 Application shutting down...")
    print("🧹 Cleaning up Chrome resources...")
    
    # Clean up global driver
    cleanup_global_driver()
    
    # Clean up any remaining zombie processes
    killed = cleanup_zombie_processes(aggressive=True)
    if killed > 0:
        print(f"   ✓ Cleaned up {killed} process(es)")
    
    print("   ✓ All Chrome resources cleaned up")


@app.get("/")
def root():
    return {
        "message": f"Welcome to {settings.PROJECT_NAME}",
        "version": settings.VERSION,
        "status": "running"
    }


@app.get("/health")
def health():
    """Health check endpoint for Railway monitoring"""
    return {"status": "healthy", "service": settings.PROJECT_NAME}
