"""Shared pytest fixtures."""
import pytest


@pytest.fixture
def sample_html_burton():
    return """
    <html><head>
    <title>Careers | Burton Snowboards</title>
    <meta property="og:site_name" content="Burton Snowboards"/>
    </head><body><footer>© 2024 Burton Snowboards, Inc.</footer></body></html>
    """


@pytest.fixture
def sample_html_jsonld():
    return """
    <html><head><script type="application/ld+json">
    {"@type":"JobPosting","hiringOrganization":{"@type":"Organization","name":"Shopify","url":"https://shopify.com"}}
    </script></head><body></body></html>
    """
