from fastapi import FastAPI
from app.routes import job_routes # pylint: disable=import-error
from app.core.config import settings # pylint: disable=import-error

app = FastAPI(title=settings.PROJECT_NAME)

app.include_router(job_routes.router, prefix="/api", tags=["Jobs"])


@app.get("/")
def root():
    return {"message": f"Welcome to {settings.PROJECT_NAME}"}
