"""SAP SuccessFactors career site extraction and search."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

from app.models.job_model import Job

logger = logging.getLogger(__name__)

SF_HOST_MARKERS = ("sapsf.", "successfactors.com", "successfactors.eu")

# Raw row text: "Senior Project ManagerID:4382-Posted on 07/10/2025-..."
SF_ROW_PATTERN = re.compile(
    r"^(?P<title>.+?)"
    r"(?:ID:\s*(?P<job_id>\d+))?"
    r"(?:\s*-\s*Posted on\s+(?P<posted>[\d/]+))?",
    re.IGNORECASE | re.DOTALL,
)

SF_TITLE_CLEAN_PATTERN = re.compile(
    r"^(?P<title>.+?)(?:\s*ID:\s*\d+.*)?$",
    re.IGNORECASE | re.DOTALL,
)


def is_successfactors_url(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return any(marker in host for marker in SF_HOST_MARKERS)


def company_slug_from_url(url: str) -> Optional[str]:
    qs = parse_qs(urlparse(url).query)
    for key in ("company", "career_company", "career_ns"):
        vals = qs.get(key)
        if vals and vals[0] and key != "career_ns":
            return vals[0].strip()
    company_vals = qs.get("company")
    if company_vals:
        return company_vals[0].strip()
    return None


def normalize_sf_title(raw_title: str) -> str:
    """Strip SuccessFactors listing suffixes for display and matching."""
    if not raw_title:
        return ""
    text = re.sub(r"\s+", " ", raw_title.strip())
    text = re.sub(r"Select Action\s*$", "", text, flags=re.IGNORECASE).strip()
    m = SF_TITLE_CLEAN_PATTERN.match(text)
    if m:
        text = m.group("title").strip()
    text = re.sub(r"\s*ID:\s*\d+.*$", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s*-\s*Posted on\s+[\d/]+.*$", "", text, flags=re.IGNORECASE).strip()
    return text


def parse_sf_row_text(raw_text: str, base_url: str, company_name: str) -> Optional[Job]:
    """Parse concatenated SF table row text into a Job."""
    if not raw_text or len(raw_text) < 8:
        return None
    text = re.sub(r"\s+", " ", raw_text.strip())
    m = SF_ROW_PATTERN.match(text)
    title = normalize_sf_title(m.group("title") if m else text)
    if not title or len(title) < 4:
        return None
    job_id = m.group("job_id") if m else None
    posted = m.group("posted") if m else None
    return Job(
        title=title,
        company=company_name,
        description=title,
        url=None,
        posted_date=posted,
        job_id=job_id,
    )


def _absolute_url(href: str, page_url: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    parsed_page = urlparse(page_url)
    base = f"{parsed_page.scheme}://{parsed_page.netloc}"
    if href.startswith("/"):
        return base + href
    if href.startswith("?"):
        path = parsed_page.path or "/career"
        return base + path + href
    return base + "/" + href.lstrip("/")


def extract_jobs_from_driver(driver, page_url: str, company_name: str) -> List[Job]:
    """Extract jobs from SF listing via career_job_req_id links."""
    jobs: List[Job] = []
    seen: set = set()
    try:
        links = driver.find_elements(
            "css selector",
            "a[href*='career_job_req_id'], a[href*='jobReqId']",
        )
    except Exception as exc:
        logger.warning("SF link extraction failed: %s", exc)
        return jobs

    for link in links:
        try:
            href = link.get_attribute("href") or ""
            if "career_job_req_id=" not in href.lower() and "jobreqid" not in href.lower():
                continue
            text = (link.text or link.get_attribute("innerText") or "").strip()
            # Walk up for fuller row text if link text is short
            if len(text) < 15:
                try:
                    row = link.find_element("xpath", "./ancestor::tr[1]")
                    text = (row.text or text).strip()
                except Exception:
                    pass
            abs_url = _absolute_url(href, page_url)
            job_id_m = re.search(r"career_job_req_id=(\d+)", href, re.I)
            job_id = job_id_m.group(1) if job_id_m else None
            title = normalize_sf_title(text.split("ID:")[0] if "ID:" in text else text)
            if not title:
                title = normalize_sf_title(text)
            if not title or len(title) < 4:
                continue
            key = (job_id or title.lower(), abs_url)
            if key in seen:
                continue
            seen.add(key)
            posted_m = re.search(r"Posted on\s+([\d/]+)", text, re.I)
            jobs.append(
                Job(
                    title=title,
                    company=company_name,
                    description=re.sub(r"\s+", " ", text)[:500],
                    url=abs_url,
                    posted_date=posted_m.group(1) if posted_m else None,
                    job_id=job_id,
                )
            )
        except Exception:
            continue
    logger.info("SuccessFactors extractor found %s jobs", len(jobs))
    return jobs


async def apply_keyword_search(driver, search_query: str) -> bool:
    """Type into SF keyword search and submit (single query only)."""
    if not search_query:
        return False
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys

        # Prefer keyword/job title inputs, not filter multiselects
        input_el = driver.execute_script(
            """
            const selectors = [
                'input[id*="keyword" i]', 'input[name*="keyword" i]',
                'input[placeholder*="job title" i]', 'input[placeholder*="keyword" i]',
                'input[aria-label*="search" i]:not([aria-label*="country" i])',
                'input[type="search"]', 'input[type="text"]'
            ];
            for (const sel of selectors) {
                for (const el of document.querySelectorAll(sel)) {
                    const label = (el.getAttribute('aria-label') || '').toLowerCase();
                    if (label.includes('multiselect') || label.includes('country')
                        || label.includes('city') || label.includes('experience required')) {
                        continue;
                    }
                    if (el.offsetParent !== null && !el.disabled) return el;
                }
            }
            return null;
            """
        )
        if not input_el:
            logger.info("SF keyword search input not found")
            return False
        input_el.clear()
        await asyncio.sleep(0.2)
        input_el.send_keys(search_query)
        await asyncio.sleep(0.3)
        try:
            input_el.send_keys(Keys.RETURN)
        except Exception:
            pass
        # Click "Search Jobs" if present
        driver.execute_script(
            """
            const buttons = [...document.querySelectorAll('button, a, input[type="button"], input[type="submit"]')];
            for (const b of buttons) {
                const t = (b.innerText || b.value || '').toLowerCase();
                if (t.includes('search jobs') || t === 'search') {
                    if (b.offsetParent !== null) { b.click(); return true; }
                }
            }
            return false;
            """
        )
        await asyncio.sleep(3)
        logger.info("SF keyword search submitted for: %s", search_query)
        return True
    except Exception as exc:
        logger.warning("SF keyword search failed: %s", exc)
        return False
