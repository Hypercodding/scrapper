"""API enqueue tests."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.integration
@patch("app.workers.tasks.scrape_career_page_task")
@patch("app.routes.job_routes.get_job_store")
def test_enqueue_returns_job_id(mock_store_fn, mock_task, client):
    mock_store = MagicMock()
    mock_store.create.return_value = "test-job-123"
    mock_store_fn.return_value = mock_store
    mock_task.delay.return_value = MagicMock(id="celery-1")

    response = client.post(
        "/api/jobs/scrape",
        json={"url": "https://boards.greenhouse.io/acme"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == "test-job-123"
    assert body["status"] == "pending"
