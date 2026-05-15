"""Career page scraping package."""
from app.services.career.detector import build_ats_api_urls, detect_job_board

__all__ = [
    "detect_job_board",
    "build_ats_api_urls",
]


def scrape_generic_career_page(*args, **kwargs):
    from app.services.generic_career_scraper import scrape_generic_career_page as _fn
    return _fn(*args, **kwargs)


def scrape_multiple_career_pages(*args, **kwargs):
    from app.services.generic_career_scraper import scrape_multiple_career_pages as _fn
    return _fn(*args, **kwargs)
