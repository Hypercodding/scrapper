"""ATS API parser unit tests."""
import pytest
from app.services.career.api_parser import (
    extract_api_location,
    extract_api_url,
    parse_api_job_item,
)


@pytest.mark.unit
def test_greenhouse_location_dict():
    item = {"location": {"name": "San Francisco, CA"}}
    assert extract_api_location(item) == "San Francisco, CA"


@pytest.mark.unit
def test_greenhouse_absolute_url():
    item = {"absolute_url": "https://stripe.com/jobs/search?gh_jid=7532733"}
    assert extract_api_url(item) == "https://stripe.com/jobs/search?gh_jid=7532733"


@pytest.mark.unit
def test_greenhouse_full_parse():
    item = {
        "title": "Account Executive, AI Sales",
        "absolute_url": "https://stripe.com/jobs/search?gh_jid=7532733",
        "location": {"name": "Chicago"},
        "company_name": "Stripe",
        "first_published": "2026-02-03T15:19:01-05:00",
    }
    fields = parse_api_job_item(item, "Stripe", "https://stripe.com")
    assert fields["title"] == "Account Executive, AI Sales"
    assert fields["url"] == "https://stripe.com/jobs/search?gh_jid=7532733"
    assert fields["location"] == "Chicago"
    assert fields["company"] == "Stripe"
    assert fields["company_url"] == "https://stripe.com"


@pytest.mark.unit
def test_multiple_offices():
    item = {"offices": [{"name": "Atlanta"}, {"name": "New York"}]}
    assert extract_api_location(item) == "Atlanta; New York"
