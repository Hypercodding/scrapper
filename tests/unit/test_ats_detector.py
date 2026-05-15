"""ATS detector unit tests."""
import pytest
from app.services.career.detector import build_ats_api_urls, detect_job_board


@pytest.mark.unit
@pytest.mark.parametrize("url,board", [
    ("https://jobs.lever.co/anthropic", "lever"),
    ("https://boards.greenhouse.io/stripe", "greenhouse"),
    ("https://jobs.ashbyhq.com/notion", "ashbyhq"),
])
def test_detect_board(url, board):
    result = detect_job_board(url)
    assert result is not None
    assert result["name"] == board


@pytest.mark.unit
def test_build_greenhouse_api():
    urls = build_ats_api_urls("https://boards.greenhouse.io/stripe")
    assert any("greenhouse.io" in u for u in urls)


@pytest.mark.unit
def test_build_lever_api():
    urls = build_ats_api_urls("https://jobs.lever.co/anthropic")
    assert any("api.lever.co" in u for u in urls)
