import json
import azure.functions as func
import asyncio
import sys

# Ensure project modules are importable in Azure Functions
sys.path.insert(0, "/home/site/wwwroot")
sys.path.insert(0, "/home/site/wwwroot/app")

from app.services.indeed_selenium_service import scrape_indeed_selenium


async def handle(req: func.HttpRequest) -> func.HttpResponse:
    query = req.params.get("query", "python")
    location = req.params.get("location", "USA")
    try:
        max_results = int(req.params.get("max_results", "10"))
    except Exception:
        max_results = 10

    data = await scrape_indeed_selenium(query, location, max_results)
    payload = [d.model_dump() if hasattr(d, "model_dump") else d.__dict__ for d in data]
    return func.HttpResponse(json.dumps(payload), mimetype="application/json", status_code=200)


def main(req: func.HttpRequest) -> func.HttpResponse:
    return asyncio.get_event_loop().run_until_complete(handle(req))


