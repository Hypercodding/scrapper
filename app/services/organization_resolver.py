"""
Multi-source organization name resolution for career pages.

Extracts company names from ATS URL slugs, JSON-LD, OpenGraph, footer branding,
page titles, and registrable domains — with blocklist validation and confidence scoring.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, List, Optional
from urllib.parse import urlparse

import tldextract
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Generic terms that must never be used as company names
COMPANY_NAME_BLOCKLIST = frozenset({
    "careers", "jobs", "job", "apply", "hiring", "vacancies", "vacancy",
    "opportunities", "opportunity", "boards", "board", "jobsite", "workday",
    "join", "team", "talent", "recruiting", "recruitment", "employment",
    "openings", "opening", "positions", "position", "work", "home",
    "about", "company", "corporate", "portal", "site", "www",
    "greenhouse", "lever", "ashby", "ashbyhq", "smartrecruiters",
    "bamboohr", "workable", "jobvite", "myworkdayjobs",
})

TITLE_SUFFIX_PATTERN = re.compile(
    r"\s*[\|\-–—:]\s*(careers?|jobs?|hiring|join\s+us|work\s+with\s+us|"
    r"employment|opportunities|vacancies|team|talent).*$",
    re.IGNORECASE,
)

FOOTER_COPYRIGHT_PATTERN = re.compile(
    r"©\s*(?:\d{4}(?:\s*[-–]\s*\d{4})?)\s+"
    r"(.+?)"
    r"(?=\s+All [Rr]ights|\s+Terms\s+of|\s+Privacy|\.\s|$)",
    re.IGNORECASE,
)

FOOTER_NAME_STOP_PHRASES = (
    "all rights reserved",
    "terms of use",
    "privacy policy",
    "human rights policy",
    "modern slavery",
    "cookie policy",
    " | ",
)

ATS_SLUG_PATTERNS = [
    # (host substring, path regex, confidence)
    ("boards.greenhouse.io", re.compile(r"/([^/?#]+)"), 0.95),
    ("greenhouse.io", re.compile(r"/boards/([^/?#]+)"), 0.95),
    ("jobs.lever.co", re.compile(r"/([^/?#]+)"), 0.95),
    ("lever.co", re.compile(r"/([^/?#]+)"), 0.93),
    ("jobs.ashbyhq.com", re.compile(r"/([^/?#]+)"), 0.95),
    ("ashbyhq.com", re.compile(r"/([^/?#]+)"), 0.93),
    ("careers.smartrecruiters.com", re.compile(r"/([^/?#]+)"), 0.95),
    ("smartrecruiters.com", re.compile(r"/([^/?#]+)"), 0.93),
    ("apply.workable.com", re.compile(r"/([^/?#]+)"), 0.95),
    ("myworkdayjobs.com", re.compile(r"/([^/?#]+)"), 0.95),
    ("bamboohr.com", re.compile(r"/careers/(\d+)"), 0.85),  # numeric id only
]

GENERIC_SUBDOMAINS = frozenset({
    "www", "careers", "jobs", "job", "apply", "hiring", "talent",
    "recruiting", "employment", "work", "boards", "board",
})


@dataclass
class OrganizationCandidate:
    name: str
    confidence: float
    source: str
    company_url: Optional[str] = None


@dataclass
class ResolvedOrganization:
    name: str
    confidence: float
    source: str
    company_url: Optional[str] = None


def _sanitize_footer_company_name(name: str) -> str:
    """Trim legal/footer boilerplate accidentally captured with company name."""
    text = name.strip()
    lower = text.lower()
    cut = len(text)
    for phrase in FOOTER_NAME_STOP_PHRASES:
        idx = lower.find(phrase)
        if idx > 2:
            cut = min(cut, idx)
    text = text[:cut].strip().rstrip(",.")
    # Lazy import avoids circular import with normalization.pipeline
    from app.services.normalization.company import normalize_company_name
    return normalize_company_name(_normalize_name(text))


def _normalize_name(raw: str) -> str:
    if not raw:
        return ""
    name = re.sub(r"\s+", " ", raw.strip())
    name = re.sub(r"^[\|\-–—:\s]+|[\|\-–—:\s]+$", "", name)
    return name.strip()


def _slug_to_name(slug: str) -> str:
    if not slug or slug.isdigit():
        return ""
    name = slug.replace("-", " ").replace("_", " ")
    return " ".join(w.capitalize() for w in name.split())


def is_blocklisted(name: str) -> bool:
    if not name:
        return True
    normalized = name.lower().strip()
    if normalized in COMPANY_NAME_BLOCKLIST:
        return True
    # Single generic word
    words = normalized.split()
    if len(words) == 1 and words[0] in COMPANY_NAME_BLOCKLIST:
        return True
    return False


def extract_ats_slug_from_url(url: str) -> Optional[OrganizationCandidate]:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = parsed.path or ""

    for host_part, pattern, confidence in ATS_SLUG_PATTERNS:
        if host_part not in host:
            continue
        match = pattern.search(path)
        if not match:
            continue
        slug = match.group(1)
        if host_part == "bamboohr.com" and slug.isdigit():
            continue
        name = _slug_to_name(slug)
        if name and not is_blocklisted(name):
            return OrganizationCandidate(
                name=name, confidence=confidence, source="ats_url_slug",
                company_url=None,  # filled from job URLs or page metadata, not ATS host
            )
    return None


def extract_jsonld_organization(soup: BeautifulSoup) -> List[OrganizationCandidate]:
    candidates: List[OrganizationCandidate] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            org = _jsonld_org_from_item(item)
            if org:
                candidates.append(org)
    return candidates


def _jsonld_org_from_item(item: dict) -> Optional[OrganizationCandidate]:
    org_keys = ("hiringOrganization", "employer", "organization", "publisher")
    for key in org_keys:
        org = item.get(key)
        if isinstance(org, dict):
            name = org.get("name")
            url = org.get("url") or org.get("sameAs")
            if name:
                name = _normalize_name(str(name))
                if name and not is_blocklisted(name):
                    return OrganizationCandidate(
                        name=name, confidence=0.90, source="jsonld",
                        company_url=str(url) if url else None,
                    )
        elif isinstance(org, str) and org:
            name = _normalize_name(org)
            if name and not is_blocklisted(name):
                return OrganizationCandidate(name=name, confidence=0.85, source="jsonld")
    if item.get("@type") in ("Organization", "Corporation"):
        name = item.get("name")
        if name:
            name = _normalize_name(str(name))
            if name and not is_blocklisted(name):
                return OrganizationCandidate(
                    name=name, confidence=0.88, source="jsonld",
                    company_url=item.get("url"),
                )
    return None


def extract_opengraph(soup: BeautifulSoup) -> List[OrganizationCandidate]:
    candidates: List[OrganizationCandidate] = []
    og_site = soup.find("meta", property="og:site_name")
    if og_site and og_site.get("content"):
        name = _normalize_name(og_site["content"])
        if name and not is_blocklisted(name):
            candidates.append(OrganizationCandidate(
                name=name, confidence=0.75, source="opengraph_site_name",
            ))
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        name = _clean_title(str(og_title["content"]))
        if name and not is_blocklisted(name):
            candidates.append(OrganizationCandidate(
                name=name, confidence=0.70, source="opengraph_title",
            ))
    return candidates


def extract_footer_branding(soup: BeautifulSoup) -> List[OrganizationCandidate]:
    candidates: List[OrganizationCandidate] = []
    footer = soup.find("footer") or soup.find(id=re.compile(r"footer", re.I))
    search_roots = [footer] if footer else []
    if not search_roots:
        search_roots = [soup]

    for root in search_roots:
        if root is None:
            continue
        text = root.get_text(" ", strip=True)
        match = FOOTER_COPYRIGHT_PATTERN.search(text)
        if match:
            name = _sanitize_footer_company_name(match.group(1))
            if name and not is_blocklisted(name):
                candidates.append(OrganizationCandidate(
                    name=name, confidence=0.70, source="footer_copyright",
                ))
                break
    return candidates


def _clean_title(title: str) -> str:
    name = _normalize_name(title)
    careers_at = re.match(r"^careers?\s+at\s+(.+)$", name, re.IGNORECASE)
    if careers_at:
        name = careers_at.group(1).split("|")[0].strip()
    if "|" in name or " - " in name:
        parts = re.split(r"\s*[\|\-–—]\s*", name)
        parts = [p.strip() for p in parts if p.strip()]
        non_generic = [p for p in parts if not is_blocklisted(p)]
        if non_generic:
            name = non_generic[-1] if len(non_generic) == 1 else non_generic[-1]
        elif parts:
            name = parts[-1]
    name = TITLE_SUFFIX_PATTERN.sub("", name).strip()
    return _normalize_name(name)


def extract_page_title(soup: BeautifulSoup) -> List[OrganizationCandidate]:
    title_tag = soup.find("title")
    if not title_tag or not title_tag.string:
        return []
    name = _clean_title(title_tag.string)
    if name and not is_blocklisted(name):
        return [OrganizationCandidate(name=name, confidence=0.65, source="page_title")]
    return []


def extract_registrable_domain(url: str) -> Optional[OrganizationCandidate]:
    extracted = tldextract.extract(url)
    domain = extracted.domain
    if not domain or domain.lower() in GENERIC_SUBDOMAINS:
        # Try subdomain if generic (e.g. careers.acme.com -> acme)
        subdomain = extracted.subdomain
        if subdomain:
            parts = [p for p in subdomain.split(".") if p.lower() not in GENERIC_SUBDOMAINS]
            if parts:
                domain = parts[-1]
    if not domain or domain.lower() in GENERIC_SUBDOMAINS:
        return None
    name = _slug_to_name(domain)
    if name and not is_blocklisted(name):
        return OrganizationCandidate(
            name=name, confidence=0.50, source="registrable_domain",
            company_url=f"https://{extracted.registered_domain}" if extracted.registered_domain else None,
        )
    return None


def collect_candidates(url: str, page_html: str, soup: Optional[BeautifulSoup] = None) -> List[OrganizationCandidate]:
    if soup is None:
        soup = BeautifulSoup(page_html or "", "lxml")

    candidates: List[OrganizationCandidate] = []

    ats = extract_ats_slug_from_url(url)
    if ats:
        candidates.append(ats)

    candidates.extend(extract_jsonld_organization(soup))
    candidates.extend(extract_opengraph(soup))
    candidates.extend(extract_footer_branding(soup))
    candidates.extend(extract_page_title(soup))

    domain = extract_registrable_domain(url)
    if domain:
        candidates.append(domain)

    return candidates


def resolve_organization(
    url: str,
    page_html: str = "",
    soup: Optional[BeautifulSoup] = None,
) -> ResolvedOrganization:
    """
    Resolve the best organization name from URL and page content.
    Falls back to registrable domain if no better source is found.
    """
    candidates = collect_candidates(url, page_html, soup)

    if not candidates:
        extracted = tldextract.extract(url)
        fallback = _slug_to_name(extracted.domain or "unknown")
        return ResolvedOrganization(
            name=fallback or "Unknown",
            confidence=0.30,
            source="fallback",
        )

    # Sort by confidence descending; prefer non-blocklisted
    valid = [c for c in candidates if not is_blocklisted(c.name)]
    if not valid:
        valid = candidates

    best = max(valid, key=lambda c: c.confidence)
    logger.info(
        "Resolved organization: %s (confidence=%.2f, source=%s) for %s",
        best.name, best.confidence, best.source, url,
    )
    return ResolvedOrganization(
        name=best.name,
        confidence=best.confidence,
        source=best.source,
        company_url=best.company_url,
    )


def resolve_organization_name(url: str, page_html: str = "", soup: Optional[BeautifulSoup] = None) -> str:
    """Convenience: return resolved company name string."""
    return resolve_organization(url, page_html, soup).name
