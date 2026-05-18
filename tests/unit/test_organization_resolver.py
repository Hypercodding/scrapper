"""Unit tests for organization name resolution."""
import pytest
from pathlib import Path

from bs4 import BeautifulSoup

from app.services.organization_resolver import (
    COMPANY_NAME_BLOCKLIST,
    extract_ats_slug_from_url,
    extract_sf_company_from_url,
    is_blocklisted,
    resolve_organization,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "html"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.unit
class TestBlocklist:
    @pytest.mark.parametrize("name", list(COMPANY_NAME_BLOCKLIST)[:10])
    def test_blocklisted_terms(self, name):
        assert is_blocklisted(name.title()) or is_blocklisted(name)


@pytest.mark.unit
class TestAtsSlug:
    @pytest.mark.parametrize("url,expected", [
        ("https://jobs.lever.co/anthropic", "Anthropic"),
        ("https://boards.greenhouse.io/stripe", "Stripe"),
        ("https://jobs.ashbyhq.com/notion", "Notion"),
        ("https://careers.smartrecruiters.com/Spotify", "Spotify"),
    ])
    def test_ats_url_slug(self, url, expected):
        cand = extract_ats_slug_from_url(url)
        assert cand is not None
        assert cand.name == expected
        assert cand.confidence >= 0.90

    def test_careers_subdomain_not_jobs(self):
        url = "https://careers.burton.com/us/en/careers"
        html = _load("footer_copyright.html").replace("Patagonia", "Burton Snowboards")
        html = "<html><head><title>Careers | Burton Snowboards</title></head><body></body></html>"
        org = resolve_organization(url, html)
        assert org.name.lower() != "careers"
        assert "burton" in org.name.lower()

    def test_jobs_lever_not_jobs(self):
        org = resolve_organization("https://jobs.lever.co/anthropic", _load("lever_anthropic.html"))
        assert org.name == "Anthropic"
        assert org.name.lower() != "jobs"

    def test_sf_company_param(self):
        cand = extract_sf_company_from_url(
            "https://career55.sapsf.eu/career?company=systemvent"
        )
        assert cand is not None
        assert cand.name == "Systemvent"


@pytest.mark.unit
class TestStructuredData:
    def test_jsonld_hiring_org(self):
        html = _load("jsonld_hiring_org.html")
        org = resolve_organization("https://example.com/careers", html)
        assert org.name == "Shopify"
        assert org.source == "jsonld"

    def test_og_site_name(self):
        html = _load("og_site_name_only.html")
        org = resolve_organization("https://monday.com/careers", html)
        assert org.name == "Monday.com"

    def test_footer_copyright(self):
        html = _load("footer_copyright.html")
        org = resolve_organization("https://patagonia.com/careers", html)
        assert org.name == "Patagonia"

    def test_footer_strips_policy_boilerplate(self):
        html = """
        <footer>© 2024 NETSOL Technologies. All Rights Reserved. Terms of Use Privacy Policy</footer>
        """
        org = resolve_organization("http://careers.netsoltech.com/", html)
        assert "terms of use" not in org.name.lower()
        assert "netsol" in org.name.lower()

    def test_title_careers_at_company(self):
        html = "<html><head><title>Careers at NetSol Technologies | Join Our Team</title></head></html>"
        org = resolve_organization("http://careers.netsoltech.com/", html)
        assert "NetSol" in org.name or "Netsol" in org.name
        assert "join our team" not in org.name.lower()


@pytest.mark.unit
class TestTitleCleaning:
    def test_title_strips_careers_suffix(self):
        html = "<html><head><title>Careers | Acme Corp</title></head><body></body></html>"
        org = resolve_organization("https://acme.com/careers", html)
        assert org.name == "Acme Corp"
        assert org.name.lower() != "careers"


@pytest.mark.unit
class TestRegressionMatrix:
    @pytest.mark.parametrize("url,html_file,forbidden,expected_substr", [
        ("https://jobs.lever.co/anthropic", "lever_anthropic.html", {"Jobs", "Lever"}, "anthropic"),
        ("https://boards.greenhouse.io/stripe", None, {"Boards", "Greenhouse"}, "stripe"),
    ])
    def test_must_not_resolve_to_generic(self, url, html_file, forbidden, expected_substr):
        html = _load(html_file) if html_file else ""
        org = resolve_organization(url, html)
        assert org.name not in forbidden
        assert expected_substr.lower() in org.name.lower()
