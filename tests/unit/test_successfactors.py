"""Unit tests for SAP SuccessFactors parsing and scrape filters."""
import pytest
from unittest.mock import AsyncMock, patch

from app.models.job_model import Job
from app.services.career.successfactors import (
    is_successfactors_url,
    normalize_sf_title,
    company_slug_from_url,
)
from app.services.generic_career_scraper import (
    filter_jobs_by_queries,
    is_valid_job_title,
    is_valid_job_entry,
    matches_job_title,
    normalize_title_for_matching,
)
from app.services.organization_resolver import extract_sf_company_from_url, is_blocklisted


@pytest.mark.unit
class TestSuccessFactorsUrl:
    def test_is_sf_url(self):
        url = (
            "https://career55.sapsf.eu/career?company=systemvent"
            "&career_ns=job_listing_summary"
        )
        assert is_successfactors_url(url)

    def test_company_slug(self):
        url = "https://career55.sapsf.eu/career?company=systemvent"
        assert company_slug_from_url(url) == "systemvent"


@pytest.mark.unit
class TestNormalizeSfTitle:
  @pytest.mark.parametrize(
      "raw,expected",
      [
          (
              "Senior Project ManagerID:4382-Posted on 07/10/2025-Select Action",
              "Senior Project Manager",
          ),
          ("EngineerID:99", "Engineer"),
          ("Plain Title", "Plain Title"),
      ],
  )
  def test_normalize(self, raw, expected):
      assert normalize_sf_title(raw) == expected

  def test_matching_uses_normalized_title(self):
      raw = "Senior Project ManagerID:4382-Posted on 07/10/2025"
      norm = normalize_title_for_matching(raw, "https://career55.sapsf.eu/career?career_job_req_id=4382")
      assert matches_job_title(norm, "Senior Project Manager")


@pytest.mark.unit
class TestJunkFilters:
    @pytest.mark.parametrize("title", [
        "Select one or more options for Country multiselect combobox",
        "Search Jobs",
        "Sign In",
        "16 Jobs matched your search",
        "Emphasized",
        "Updating...",
        "Career Opportunities",
    ])
    def test_invalid_titles(self, title):
        assert is_valid_job_title(title) is False

    def test_sf_listing_url_rejected(self):
        job = Job(
            title="Senior Project Manager",
            company="Systemvent",
            description="",
            url="https://career55.sapsf.eu/career?company=systemvent&career_ns=job_listing_summary",
        )
        assert is_valid_job_entry(job) is False

    def test_sf_req_url_accepted(self):
        job = Job(
            title="Senior Project Manager",
            company="Systemvent",
            description="",
            url="https://career55.sapsf.eu/career?career_job_req_id=4382&company=systemvent",
        )
        assert is_valid_job_entry(job) is True


@pytest.mark.unit
class TestFilterJobsByQueries:
    def test_or_logic(self):
        jobs = [
            Job(title="Senior Project Manager", company="X", description="", url="http://a/1"),
            Job(title="Data Analyst", company="X", description="", url="http://a/2"),
            Job(title="Marketing Intern", company="X", description="", url="http://a/3"),
        ]
        out = filter_jobs_by_queries(jobs, ["Senior Project Manager", "Data Analyst"])
        titles = {j.title for j in out}
        assert titles == {"Senior Project Manager", "Data Analyst"}

    def test_sf_mangled_title_matches(self):
        jobs = [
            Job(
                title="Senior Project ManagerID:4382-Posted on 07/10/2025",
                company="X",
                description="",
                url="https://career55.sapsf.eu/career?career_job_req_id=4382",
            ),
        ]
        out = filter_jobs_by_queries(jobs, ["Senior Project Manager"])
        assert len(out) == 1


@pytest.mark.unit
class TestOrganizationResolverSf:
    def test_sf_company_param(self):
        url = "https://career55.sapsf.eu/career?company=systemvent"
        cand = extract_sf_company_from_url(url)
        assert cand is not None
        assert cand.name == "Systemvent"
        assert cand.confidence >= 0.85

    def test_career_opportunities_blocklisted(self):
        assert is_blocklisted("Career Opportunities")


@pytest.mark.unit
class TestSingleScrapeMultiTitle:
    @pytest.mark.asyncio
    async def test_no_per_title_selenium_loop(self):
        url = "https://career55.sapsf.eu/career?company=systemvent"
        with patch(
            "app.services.generic_career_scraper.try_scrape_via_ats_api",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.services.generic_career_scraper.scrape_with_selenium",
            new_callable=AsyncMock,
            return_value=[
                Job(title="Senior Project Manager", company="X", description="", url="http://a/1"),
                Job(title="Data Analyst", company="X", description="", url="http://a/2"),
            ],
        ) as mock_selenium, patch(
            "app.services.generic_career_scraper.fetch_initial_page_html",
            new_callable=AsyncMock,
            return_value="<html></html>",
        ), patch(
            "app.services.generic_career_scraper.resolve_organization",
            return_value=type("Org", (), {
                "name": "Systemvent",
                "confidence": 0.9,
                "source": "test",
                "company_url": None,
            })(),
        ), patch(
            "app.core.throttle.get_scraping_throttle",
        ) as mock_throttle:
            class _FakeThrottle:
                async def __aenter__(self):
                    return None

                async def __aexit__(self, *args):
                    return None

            mock_throttle.return_value = _FakeThrottle()

            from app.services.generic_career_scraper import scrape_generic_career_page

            await scrape_generic_career_page(
                url,
                max_results=10,
                search_query="Senior Project Manager, Data Analyst",
            )

        assert mock_selenium.await_count == 1
        call_kwargs = mock_selenium.await_args.kwargs
        assert call_kwargs.get("search_query") is None

    @pytest.mark.asyncio
    async def test_workday_search_fallback_to_full_listing(self):
        """Single-title browser search returning nothing should retry without search."""
        url = "https://aig.wd1.myworkdayjobs.com/aig"
        pm_job = Job(
            title="Portfolio Analyst",
            company="Aig",
            description="",
            url="https://aig.wd1.myworkdayjobs.com/en-US/aig/job/Bangkok/Portfolio-Analyst_JR2601827",
        )
        with patch(
            "app.services.generic_career_scraper.try_scrape_via_ats_api",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.services.generic_career_scraper.scrape_with_selenium",
            new_callable=AsyncMock,
            side_effect=[[], [pm_job]],
        ) as mock_selenium, patch(
            "app.services.generic_career_scraper.fetch_initial_page_html",
            new_callable=AsyncMock,
            return_value="<html></html>",
        ), patch(
            "app.services.generic_career_scraper.resolve_organization",
            return_value=type("Org", (), {
                "name": "Aig",
                "confidence": 0.9,
                "source": "test",
                "company_url": None,
            })(),
        ), patch(
            "app.services.generic_career_scraper.normalize_and_dedupe_jobs",
            side_effect=lambda jobs, _org: jobs,
        ), patch(
            "app.core.throttle.get_scraping_throttle",
        ) as mock_throttle:
            class _FakeThrottle:
                async def __aenter__(self):
                    return None

                async def __aexit__(self, *args):
                    return None

            mock_throttle.return_value = _FakeThrottle()

            from app.services.generic_career_scraper import scrape_generic_career_page

            result = await scrape_generic_career_page(
                url,
                max_results=10,
                search_query="Portfolio Analyst",
            )

        assert mock_selenium.await_count == 2
        assert mock_selenium.await_args_list[0].kwargs["search_query"] == "Portfolio Analyst"
        assert mock_selenium.await_args_list[1].kwargs["search_query"] is None
        assert len(result) == 1
        assert result[0].title == "Portfolio Analyst"
