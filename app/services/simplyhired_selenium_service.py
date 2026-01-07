import time
import random
import threading
import re
from typing import Optional, List
import undetected_chromedriver as uc
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options as ChromeOptions
from urllib.parse import urlparse, quote_plus
import os
import json
import zipfile
import tempfile
import subprocess
import platform
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from selenium_stealth import stealth
from selenium.webdriver import ActionChains
from app.models.job_model import Job
from app.core.config import settings
from app.core.proxy_manager import get_proxy_manager, reset_proxy_manager
import urllib3
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
import requests

# Import shared driver management functions from Indeed service
from app.services.indeed_selenium_service import (
    configure_driver_connection_pool,
    get_chrome_executable_path,
    get_chromedriver_path,
    check_chrome_process_count,
    check_system_resources,
    cleanup_zombie_processes,
    cleanup_global_driver,
    _get_proxy_urls,
    _build_proxy_auth_extension,
    get_driver as get_shared_driver,
    _perform_human_interactions,
    _progressive_scroll
)

_last_fetch = 0
_request_lock = threading.Lock()


class CloudflareBlockedError(Exception):
    """Raised when SimplyHired returns a Cloudflare/turnstile block page."""
    pass


def _format_location_for_simplyhired(location: str) -> str:
    """Format location for SimplyHired search."""
    location = location.strip()
    
    # Handle remote job types
    if location.lower() in ['remote', 'work from home', 'wfh']:
        return 'remote'
    
    # SimplyHired accepts various location formats
    # URL encode spaces and special characters
    return quote_plus(location)


def _get_simplyhired_employment_filter(employment_type: str) -> Optional[str]:
    """Get SimplyHired employment type filter parameter."""
    employment_type = employment_type.lower().strip()
    
    # SimplyHired uses 'jt' parameter for job type
    employment_filters = {
        'full-time': 'jt=fulltime',
        'fulltime': 'jt=fulltime',
        'part-time': 'jt=parttime',
        'parttime': 'jt=parttime',
        'contract': 'jt=contract',
        'temporary': 'jt=temporary',
        'internship': 'jt=internship',
        'intern': 'jt=internship'
    }
    
    return employment_filters.get(employment_type)


def _get_simplyhired_date_filter(days_old: int) -> Optional[str]:
    """Get SimplyHired date filter parameter."""
    # SimplyHired uses 'fdb' parameter for date posted
    # Values: 1 (last 24 hours), 3 (last 3 days), 7 (last 7 days), 14 (last 14 days), 30 (last 30 days)
    if days_old <= 1:
        return 'fdb=1'
    elif days_old <= 3:
        return 'fdb=3'
    elif days_old <= 7:
        return 'fdb=7'
    elif days_old <= 14:
        return 'fdb=14'
    elif days_old <= 30:
        return 'fdb=30'
    else:
        return None  # SimplyHired doesn't support > 30 days filter


def _find_job_cards_simplyhired(soup: BeautifulSoup) -> List:
    """Find job cards using SimplyHired-specific selectors."""
    job_cards = []
    
    # SimplyHired 2024+ structure - jobs are in <li> elements within job list
    # Priority 1: Find the job results list and get li items
    job_list_selectors = [
        'ul[aria-label*="job"]',  # Job list with aria label
        'ul[class*="JobList"]',  # Job list class
        'ul[class*="job-list"]',  # Alternative job list class
        'div[class*="JobList"] ul',  # Nested list
        'div[class*="job-results"] ul',  # Results list
        'main ul',  # Main content list
    ]
    
    for selector in job_list_selectors:
        job_list = soup.select_one(selector)
        if job_list:
            # Get all list items that look like job cards
            items = job_list.find_all('li', recursive=False)
            # Filter to only those with job-like content (title link or heading)
            for item in items:
                has_title = item.find(['h2', 'h3', 'h4']) or item.find('a', href=lambda x: x and '/job/' in x)
                if has_title:
                    job_cards.append(item)
            if job_cards:
                print(f"Found {len(job_cards)} job cards using selector: {selector}")
                break
    
    # Priority 2: Look for job card divs/articles
    if not job_cards:
        primary_selectors = [
            'li[class*="SerpJob"]',  # SimplyHired job card li
            'div[data-testid="job-card"]',  # Modern SimplyHired layout
            'article[data-testid="job-card"]',  # Article-based layout
            'div[class*="SerpJob"]',  # Any SerpJob class div
            'div[class*="job-card"]',  # Generic job card
            'div[class*="JobCard"]',  # Alternative naming
    ]
    
    for selector in primary_selectors:
        cards = soup.select(selector)
        if cards:
            print(f"Found {len(cards)} job cards using selector: {selector}")
            job_cards.extend(cards)
            break
    
    # Priority 3: Look for any li elements containing job title links
    if not job_cards:
        print("DEBUG - No job cards found with primary selectors, trying li with job links...")
        all_li = soup.find_all('li')
        for li in all_li:
            # Check if this li contains a job link
            job_link = li.find('a', href=lambda x: x and '/job/' in x)
            if job_link:
                # Make sure it has a heading (title)
                title_elem = li.find(['h2', 'h3', 'h4'])
                if title_elem:
                    job_cards.append(li)
        print(f"DEBUG - Found {len(job_cards)} job cards from li elements")
    
    # Priority 4: Fallback - look for anchor tags with /job/ and get parent
    if not job_cards:
        print("DEBUG - Trying anchor tag fallback...")
        title_links = soup.find_all('a', href=lambda x: x and '/job/' in x)
        print(f"DEBUG - Found {len(title_links)} job title links")
        for link in title_links:
            # Find the closest li or div parent that could be a job card
            parent = link.find_parent(['li', 'article', 'div'])
            if parent and parent not in job_cards:
                # Verify it has some job-like content
                text = parent.get_text()
                if len(text) > 50:  # Job cards have reasonable content
                    job_cards.append(parent)
        print(f"DEBUG - Found {len(job_cards)} job cards from anchor fallback")
    
    # Remove duplicates
    unique_cards = []
    seen_cards = set()
    for card in job_cards:
        card_id = id(card)
        if card_id not in seen_cards:
            seen_cards.add(card_id)
            unique_cards.append(card)
    
    if len(unique_cards) > 50:
        print(f"WARNING - Found {len(unique_cards)} job cards, limiting to first 30")
        unique_cards = unique_cards[:30]
    
    return unique_cards


def _is_valid_job_card_simplyhired(card) -> bool:
    """Validate that a card element actually contains a job listing."""
    if not card:
        return False
    
    # Check for job title link (most reliable indicator)
    title_link = card.select_one('a[href*="/job/"]')
    if title_link:
        return True
    
    # Check for company name
    company_elements = card.select('[class*="company"], [class*="Company"]')
    if company_elements:
        return True
    
    # Check for location info
    location_elements = card.select('[class*="location"], [class*="Location"]')
    if location_elements:
        return True
    
    return False


def _extract_title_simplyhired(card) -> Optional[str]:
    """Extract job title from SimplyHired job card."""
    # Priority 1: Find heading element with job title
    title_selectors = [
        'h2 a',  # Most common - heading with link
        'h3 a',
        'h4 a',
        'h2',  # Just heading
        'h3',
        'h4',
        'a[href*="/job/"]',  # Link to job page
        '[class*="jobTitle"] a',
        '[class*="JobTitle"] a',
        '[class*="job-title"] a',
        'a[class*="title"]',
    ]
    
    for selector in title_selectors:
        title_elem = card.select_one(selector)
        if title_elem:
            title = title_elem.get_text(strip=True)
            if title and len(title) > 3:
                # Clean up title - remove common badges/tags
                title = re.sub(r'\b(new|urgent|hiring|featured|sponsored)\b', '', title, flags=re.IGNORECASE).strip()
                # Remove extra whitespace
                title = re.sub(r'\s+', ' ', title).strip()
                return title
    
    # Fallback: Try to get from card's aria-label or data attribute
    aria_label = card.get('aria-label', '')
    if aria_label and len(aria_label) > 5:
        # Often aria-label contains the full job info
        parts = aria_label.split('—')  # Company separator
        if parts:
            return parts[0].strip()
    
    return None


def _extract_company_info_simplyhired(card) -> tuple[Optional[str], Optional[str]]:
    """Extract company name and URL from SimplyHired job card."""
    company = None
    company_url = None
    
    # Priority 1: Look for company link (often has /company/ or ends with —)
    company_link_selectors = [
        'a[href*="/company/"]',  # Company profile link
        'a[href*="/cmp/"]',  # Alternative company link format
    ]
    
    for selector in company_link_selectors:
        company_elem = card.select_one(selector)
        if company_elem:
            company = company_elem.get_text(strip=True)
            # Clean up - remove trailing "—" if present
            company = company.rstrip('—').rstrip(' —').strip()
            if company and len(company) > 1:
                href = company_elem.get('href', '')
                if href:
                    company_url = f"https://www.simplyhired.com{href}" if href.startswith('/') else href
                return company, company_url
    
    # Priority 2: Look for company name in text after job title
    # SimplyHired often has format: "Company — City, State Rating"
    company_selectors = [
        '[class*="companyName"]',
        '[class*="CompanyName"]',
        '[class*="company-name"]',
        '[class*="employer"]',
        '[class*="company"]',
        '[class*="Company"]',
        '[data-testid="company-name"]'
    ]
    
    for selector in company_selectors:
        company_elem = card.select_one(selector)
        if company_elem:
            company_text = company_elem.get_text(strip=True)
            if company_text and len(company_text) > 1:
                # Parse "Company — Location Rating" format
                if '—' in company_text:
                    company = company_text.split('—')[0].strip()
                else:
                    company = company_text
                # Clean company name - remove trailing punctuation
                company = company.rstrip('—').rstrip(',').strip()
                if company:
                    # Try to find link inside
                    link_elem = company_elem.find('a')
                    if link_elem:
                        href = link_elem.get('href', '')
                        if href:
                            company_url = f"https://www.simplyhired.com{href}" if href.startswith('/') else href
                    return company, company_url
    
    # Priority 3: Parse from card text - find text after title heading
    title_elem = card.find(['h2', 'h3', 'h4'])
    if title_elem:
        # Get next sibling or parent's next content
        next_elem = title_elem.find_next_sibling()
        if next_elem:
            text = next_elem.get_text(strip=True)
            if '—' in text:
                company = text.split('—')[0].strip()
                if company:
                    # Look for link in this element
                    link = next_elem.find('a')
                    if link:
                        href = link.get('href', '')
                        if href:
                            company_url = f"https://www.simplyhired.com{href}" if href.startswith('/') else href
                    return company, company_url
    
    return company, company_url


def _extract_location_info_simplyhired(card) -> tuple[Optional[str], Optional[str]]:
    """Extract location and remote type from SimplyHired job card."""
    location = None
    remote_type = None
    
    # Priority 1: Direct location selectors
    location_selectors = [
        '[class*="location"]',
        '[class*="Location"]',
        '[data-testid="job-location"]',
        '[class*="jobLocation"]',
        '[class*="job-location"]',
    ]
    
    for selector in location_selectors:
        location_elem = card.select_one(selector)
        if location_elem:
            location = location_elem.get_text(strip=True)
            if location:
                break
    
    # Priority 2: Parse from company text (format: "Company — City, State Rating")
    if not location:
        # Look for text containing "—" separator
        company_location_selectors = [
            '[class*="company"]',
            '[class*="Company"]',
            '[class*="employer"]',
        ]
        for selector in company_location_selectors:
            elem = card.select_one(selector)
            if elem:
                text = elem.get_text(strip=True)
                if '—' in text:
                    parts = text.split('—')
                    if len(parts) >= 2:
                        # Location is after "—", might include rating
                        loc_part = parts[1].strip()
                        # Remove rating numbers (like "3.5", "4.2")
                        loc_part = re.sub(r'\s+\d+\.?\d*\s*$', '', loc_part).strip()
                        if loc_part:
                            location = loc_part
                            break
    
    # Priority 3: Look for city/state pattern in card text
    if not location:
        card_text = card.get_text()
        # Match patterns like "Tampa, FL" or "New York, NY"
        loc_pattern = r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*,\s*([A-Z]{2})\b'
        match = re.search(loc_pattern, card_text)
        if match:
            location = f"{match.group(1)}, {match.group(2)}"
    
    # Determine remote type based on card text (not just location)
    card_text_lower = card.get_text().lower()
    if 'remote' in card_text_lower:
        if 'hybrid' in card_text_lower:
            remote_type = 'Hybrid'
        elif 'on-site' in card_text_lower or 'onsite' in card_text_lower or 'in-person' in card_text_lower:
            remote_type = 'Hybrid'  # Has both remote and on-site mentioned
        else:
            remote_type = 'Remote'
    elif 'hybrid' in card_text_lower:
        remote_type = 'Hybrid'
    elif 'on-site' in card_text_lower or 'onsite' in card_text_lower or 'in-person' in card_text_lower:
        remote_type = 'On-site'
    else:
        # Default to On-site if not specified
        remote_type = 'On-site'
    
    return location, remote_type


def _extract_salary_simplyhired(card) -> Optional[str]:
    """Extract salary range from SimplyHired job card."""
    # Priority 1: Direct salary selectors
    salary_selectors = [
        '[class*="salary"]',
        '[class*="Salary"]',
        '[class*="pay"]',
        '[class*="Pay"]',
        '[class*="compensation"]',
        '[data-testid="salary"]',
        '[class*="wage"]',
    ]
    
    for selector in salary_selectors:
        salary_elem = card.select_one(selector)
        if salary_elem:
            salary_text = salary_elem.get_text(strip=True)
            if '$' in salary_text:
                salary_text = re.sub(r'\s+', ' ', salary_text).strip()
                if len(salary_text) > 3:
                    return salary_text
    
    # Priority 2: Pattern matching in text content
    # SimplyHired shows salary in various formats
    text_content = card.get_text()
    
    salary_patterns = [
        # Range patterns
        r'\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?\s*(?:a\s+year|per\s+year|annually|/\s*year|/\s*yr)',
        r'\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?\s*(?:per\s+hour|/\s*hour|/\s*hr|hourly)',
        r'\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?',
        # Single value patterns
        r'From\s+\$[\d,]+(?:K|k)?\s*(?:a\s+year|per\s+year|/\s*year|annually)',
        r'Up\s+to\s+\$[\d,]+(?:K|k)?\s*(?:a\s+year|per\s+year|/\s*year)',
        r'\$[\d,]+(?:K|k)?\s*(?:a\s+year|per\s+year|/\s*year|annually)',
        r'\$[\d,]+(?:K|k)?\s*(?:per\s+hour|/\s*hour|hourly|an\s+hour)',
        r'\$[\d,]+(?:K|k)?\s*/\s*(?:year|hour|month|week)',
        # Estimated patterns
        r'Estimated:\s*\$[\d,]+(?:K|k)?(?:\s*-\s*\$[\d,]+(?:K|k)?)?',
    ]
    
    for pattern in salary_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            salary = match.group(0).strip()
            # Clean up and standardize
            salary = re.sub(r'\s+', ' ', salary)
            return salary
    
    return None


def _extract_job_types_simplyhired(card) -> tuple[Optional[str], Optional[str]]:
    """Extract job type and employment type from SimplyHired job card."""
    job_type = None
    employment_type = None
    
    text_content = card.get_text().lower()
    
    # Check for remote type
    if 'remote' in text_content:
        if 'hybrid' in text_content:
            job_type = 'Hybrid'
        else:
            job_type = 'Remote'
    else:
        job_type = 'On-site'
    
    # Check for employment type
    if 'full-time' in text_content or 'fulltime' in text_content:
        employment_type = 'Full-Time'
    elif 'part-time' in text_content or 'parttime' in text_content:
        employment_type = 'Part-Time'
    elif 'contract' in text_content:
        employment_type = 'Contract'
    elif 'internship' in text_content or 'intern' in text_content:
        employment_type = 'Internship'
    
    return job_type, employment_type


def _extract_experience_level_simplyhired(card) -> Optional[str]:
    """Extract experience level from SimplyHired job card."""
    text_content = card.get_text().lower()
    
    if 'executive' in text_content or 'c-level' in text_content:
        return 'Executive'
    elif 'director' in text_content or 'manager' in text_content:
        return 'Director'
    elif 'senior' in text_content:
        return 'Senior'
    elif 'mid' in text_content or 'mid-level' in text_content:
        return 'Mid'
    elif 'junior' in text_content or 'entry' in text_content or 'entry-level' in text_content:
        return 'Entry'
    elif 'assistant' in text_content:
        return 'Assistant'
    elif 'intern' in text_content or 'internship' in text_content:
        return 'Intern'
    
    return None


def _extract_posted_date_simplyhired(card) -> Optional[str]:
    """Extract posted date from SimplyHired job card."""
    # Priority 1: Direct date selectors
    date_selectors = [
        '[class*="date"]',
        '[class*="Date"]',
        '[class*="posted"]',
        '[class*="time"]',
        '[data-testid="posted-date"]'
    ]
    
    for selector in date_selectors:
        date_elem = card.select_one(selector)
        if date_elem:
            date_text = date_elem.get_text(strip=True)
            if date_text:
                return date_text
    
    # Priority 2: Pattern matching in card text
    # SimplyHired shows relative times like "1h", "6d", "7d", "14d"
    text_content = card.get_text()
    
    date_patterns = [
        r'\b(\d+h)\b',  # Hours like "1h"
        r'\b(\d+d)\b',  # Days like "6d", "7d"
        r'\b(\d+w)\b',  # Weeks like "2w"
        r'\b(\d+m)\b',  # Months like "1m"
        r'(\d+\s+(?:hour|hours|hr|hrs)\s+ago)',
        r'(\d+\s+(?:day|days)\s+ago)',
        r'(\d+\s+(?:week|weeks)\s+ago)',
        r'(\d+\s+(?:month|months)\s+ago)',
        r'(?:posted|posted on|published):\s*([^\n,]+)',
        r'\b(today)\b',
        r'\b(yesterday)\b',
        r'\b(just posted)\b',
        r'\b(just now)\b',
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            date_str = match.group(1).strip()
            # Convert shorthand to more readable format
            if re.match(r'^\d+h$', date_str):
                hours = int(date_str[:-1])
                return f"{hours} hour{'s' if hours > 1 else ''} ago"
            elif re.match(r'^\d+d$', date_str):
                days = int(date_str[:-1])
                return f"{days} day{'s' if days > 1 else ''} ago"
            elif re.match(r'^\d+w$', date_str):
                weeks = int(date_str[:-1])
                return f"{weeks} week{'s' if weeks > 1 else ''} ago"
            elif re.match(r'^\d+m$', date_str):
                months = int(date_str[:-1])
                return f"{months} month{'s' if months > 1 else ''} ago"
            return date_str
    
    return None


def _extract_description_simplyhired(card) -> Optional[str]:
    """Extract job description from SimplyHired job card."""
    description_selectors = [
        '[class*="description"]',
        '[class*="Description"]',
        '[class*="summary"]',
        '[class*="Summary"]',
        '[class*="snippet"]',
        '[data-testid="job-description"]'
    ]
    
    for selector in description_selectors:
        desc_elem = card.select_one(selector)
        if desc_elem:
            description = desc_elem.get_text(strip=True)
            if description and len(description) > 20:
                return description
    
    return None


def _extract_job_url_simplyhired(card) -> Optional[str]:
    """Extract job URL from SimplyHired job card."""
    # Priority 1: Look for direct job link
    job_link_selectors = [
        'a[href*="/job/"]',  # Most common pattern
        'h2 a[href]',  # Title link in heading
        'h3 a[href]',
        'h4 a[href]',
        'a[class*="jobTitle"]',
        'a[class*="job-title"]',
    ]
    
    for selector in job_link_selectors:
        job_link = card.select_one(selector)
        if job_link:
            href = job_link.get('href', '')
            if href and '/job/' in href:
                if href.startswith('http'):
                    return href
                else:
                    return f"https://www.simplyhired.com{href}"
    # Priority 2: First link that looks like a job page
    all_links = card.find_all('a', href=True)
    for link in all_links:
        href = link.get('href', '')
        if '/job/' in href:
            if href.startswith('http'):
                return href
            else:
                return f"https://www.simplyhired.com{href}"
    
    return None


def _extract_job_id_simplyhired(card) -> Optional[str]:
    """Extract job ID from SimplyHired job card."""
    # Try to extract from URL
    job_url = _extract_job_url_simplyhired(card)
    if job_url:
        # SimplyHired job IDs are typically in the URL path
        match = re.search(r'/job/([^/?]+)', job_url)
        if match:
            return match.group(1)
    
    # Try data attributes
    job_id = card.get('data-job-id') or card.get('data-id')
    if job_id:
        return str(job_id)
    
    return None


def _extract_skills_simplyhired(card) -> List[str]:
    """Extract skills from SimplyHired job card."""
    skills = []
    
    # SimplyHired shows skills as tags/badges - look for span/div elements with skill-like content
    # Common skill patterns found in SimplyHired cards
    skill_keywords = {
        # Programming languages
        'python', 'java', 'javascript', 'typescript', 'c++', 'c#', 'go', 'rust', 'ruby', 'php',
        'swift', 'kotlin', 'scala', 'r', 'perl', 'sql', 'nosql', 'mongodb',
        # Frameworks/Libraries
        'react', 'angular', 'vue', 'node.js', 'nodejs', 'django', 'flask', 'spring', '.net',
        'tensorflow', 'pytorch', 'pandas', 'numpy', 'scikit-learn',
        # Cloud/DevOps
        'aws', 'azure', 'gcp', 'docker', 'kubernetes', 'jenkins', 'terraform', 'ansible',
        # Tools/Skills
        'git', 'linux', 'agile', 'scrum', 'jira', 'excel', 'microsoft excel', 'microsoft word',
        'computer science', 'data analysis', 'machine learning', 'data science',
        'communication skills', 'system design', 'database', 'api',
    }
    
    # Look for tag/badge elements
    tag_selectors = [
        'span[class*="tag"]',
        'span[class*="badge"]',
        'span[class*="skill"]',
        'div[class*="tag"]',
        'div[class*="badge"]',
        'a[class*="tag"]',
    ]
    
    for selector in tag_selectors:
        tags = card.select(selector)
        for tag in tags:
            skill_text = tag.get_text(strip=True).lower()
            # Check if this looks like a skill (not job type, benefit, etc.)
            if skill_text in skill_keywords or any(kw in skill_text for kw in skill_keywords):
                formatted = skill_text.title()
                if formatted not in skills:
                    skills.append(formatted)
    
    # Also check card text for common skills
    card_text = card.get_text().lower()
    for skill in skill_keywords:
        if skill in card_text:
            formatted = skill.title()
            if formatted not in skills:
                skills.append(formatted)
    
    return skills[:15]  # Limit to 15 skills


def _extract_requirements_simplyhired(card) -> List[str]:
    """Extract requirements from SimplyHired job card."""
    requirements = []
    
    # Look for requirements section
    req_elem = card.select_one('[class*="requirement"], [class*="Requirement"]')
    if req_elem:
        req_text = req_elem.get_text()
        # Split by line breaks or bullets
        requirements = [r.strip() for r in re.split(r'[\n•\-\*]', req_text) if r.strip() and len(r.strip()) > 10]
    
    return requirements[:10]  # Limit to 10 requirements


def _extract_benefits_simplyhired(card) -> List[str]:
    """Extract benefits from SimplyHired job card."""
    benefits = []
    
    # Common benefit keywords shown in SimplyHired cards
    benefit_keywords = {
        'health insurance', 'dental insurance', 'vision insurance', 'medical insurance',
        '401k', '401(k)', '457(b)', 'retirement', 'pension',
        'paid time off', 'pto', 'vacation', 'paid vacation', 'paid holidays',
        'flexible spending account', 'fsa', 'hsa',
        'life insurance', 'disability insurance', 'ad&d insurance',
        'remote', 'hybrid', 'work from home', 'flexible schedule',
        'relocation assistance', 'commuter assistance', 'transit',
        'tuition reimbursement', 'professional development', 'training',
        'stock options', 'equity', 'bonus',
        'parental leave', 'maternity leave', 'paternity leave',
        'gym membership', 'wellness',
        'quick apply', # Not a benefit but often shown as a tag
    }
    
    # Look for tag/badge elements
    tag_selectors = [
        'span[class*="tag"]',
        'span[class*="badge"]',
        'span[class*="benefit"]',
        'div[class*="tag"]',
        'div[class*="badge"]',
    ]
    
    for selector in tag_selectors:
        tags = card.select(selector)
        for tag in tags:
            benefit_text = tag.get_text(strip=True).lower()
            # Check if this looks like a benefit
            if benefit_text in benefit_keywords or any(kw in benefit_text for kw in benefit_keywords):
                if 'quick apply' not in benefit_text:  # Skip quick apply
                    formatted = benefit_text.title()
                    if formatted not in benefits:
                        benefits.append(formatted)
    
    # Also check card text for common benefits
    card_text = card.get_text().lower()
    for benefit in benefit_keywords:
        if benefit in card_text and 'quick apply' not in benefit:
            formatted = benefit.title()
            if formatted not in benefits:
                benefits.append(formatted)
    
    return benefits[:15]  # Limit to 15 benefits


# ============================================================================
# FULL PAGE EXTRACTION FUNCTIONS (for individual job pages)
# ============================================================================

def _extract_salary_from_full_page_simplyhired(soup: BeautifulSoup) -> Optional[str]:
    """Extract salary from SimplyHired's full job page."""
    # SimplyHired salary selectors
    salary_selectors = [
        'div[data-testid="viewjob-salaryInfoValue"]',
        'span[data-testid="viewjob-salaryInfoValue"]',
        'div[class*="SalaryInfo"]',
        'span[class*="salary"]',
        'div[class*="salary"]',
        'div[class*="Salary"]',
        'div[class*="compensation"]',
        'span[class*="compensation"]',
    ]
    
    for selector in salary_selectors:
        salary_elem = soup.select_one(selector)
        if salary_elem:
            salary_text = salary_elem.get_text(strip=True)
            if '$' in salary_text or 'salary' in salary_text.lower():
                return salary_text
    
    # Pattern matching fallback in full page text
    text_content = soup.get_text()
    salary_patterns = [
        r'\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?\s*(?:a\s+year|per\s+year|annually|yearly|/\s*year)',
        r'\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?\s*(?:per\s+hour|/\s*hour|hourly)',
        r'\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?',
        r'\$[\d,]+(?:K|k)?\s*/\s*(?:year|hour|month|week)',
        r'\$[\d,]+(?:K|k)?\s*per\s*(?:year|hour|month|week)',
        r'(?:salary|pay|compensation):\s*\$[\d,]+(?:\.\d+)?(?:\s*-\s*\$[\d,]+(?:\.\d+)?)?',
    ]
    
    for pattern in salary_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            return match.group().strip()
    
    return None


def _extract_employment_from_full_page_simplyhired(soup: BeautifulSoup) -> Optional[str]:
    """Extract employment type from SimplyHired's full job page."""
    # SimplyHired employment type selectors
    employment_selectors = [
        'div[data-testid="viewjob-jobType"]',
        'span[data-testid="viewjob-jobType"]',
        'div[class*="JobType"]',
        'span[class*="job-type"]',
        'div[class*="employment"]',
        'span[class*="employment"]',
    ]
    
    for selector in employment_selectors:
        emp_elem = soup.select_one(selector)
        if emp_elem:
            emp_text = emp_elem.get_text(strip=True).lower()
            if 'full-time' in emp_text or 'fulltime' in emp_text or 'full time' in emp_text:
                return 'Full-Time'
            elif 'part-time' in emp_text or 'parttime' in emp_text or 'part time' in emp_text:
                return 'Part-Time'
            elif 'contract' in emp_text:
                return 'Contract'
            elif 'internship' in emp_text or 'intern' in emp_text:
                return 'Internship'
            elif 'temporary' in emp_text:
                return 'Temporary'
    
    # Pattern matching fallback
    text_content = soup.get_text().lower()
    
    employment_patterns = {
        'Full-Time': ['full-time', 'full time', 'fulltime', 'permanent', 'regular'],
        'Part-Time': ['part-time', 'part time', 'parttime'],
        'Contract': ['contract', 'contractor', 'freelance', 'consultant'],
        'Internship': ['internship', 'intern', 'trainee', 'co-op'],
        'Temporary': ['temporary', 'temp', 'seasonal']
    }
    
    for emp_type, patterns in employment_patterns.items():
        if any(pattern in text_content for pattern in patterns):
            return emp_type
    
    return None


def _extract_date_from_full_page_simplyhired(soup: BeautifulSoup) -> Optional[str]:
    """Extract posted date from SimplyHired's full job page."""
    # SimplyHired date selectors
    date_selectors = [
        'div[data-testid="viewjob-postedDate"]',
        'span[data-testid="viewjob-postedDate"]',
        'div[class*="PostedDate"]',
        'span[class*="posted"]',
        'div[class*="date"]',
        'time',
    ]
    
    for selector in date_selectors:
        date_elem = soup.select_one(selector)
        if date_elem:
            date_text = date_elem.get_text(strip=True)
            if date_text and ('ago' in date_text.lower() or 'posted' in date_text.lower() or 
                            'today' in date_text.lower() or 'yesterday' in date_text.lower()):
                return date_text
    
    # Pattern matching fallback
    text_content = soup.get_text()
    date_patterns = [
        r'\d+\s+(?:days?|hours?|minutes?|weeks?|months?)\s+ago',
        r'Posted\s+\d+\s+(?:days?|hours?|weeks?)\s+ago',
        r'Just\s+posted',
        r'Today',
        r'Yesterday',
        r'Posted\s+(?:today|yesterday)',
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            return match.group().strip()
    
    return None


def _extract_description_from_full_page_simplyhired(soup: BeautifulSoup) -> Optional[str]:
    """Extract full job description from SimplyHired's full job page."""
    # SimplyHired description selectors - try more specific ones first
    desc_selectors = [
        'div[data-testid="viewjob-jobDescription"]',
        'div[data-testid="jobDescription"]',
        'div[class*="JobDescription"]',
        'div[class*="jobDescription"]',
        'div[class*="job-description"]',
        'div[class*="jobDescriptionText"]',
        'div[class*="JobDescriptionText"]',
        'div[id*="jobDescription"]',
        'div[id*="JobDescription"]',
        'section[class*="description"]',
        'article[class*="description"]',
        'div[class*="description"]',
        'div[class*="Description"]',
    ]
    
    for selector in desc_selectors:
        desc_elem = soup.select_one(selector)
        if desc_elem:
            # Create a copy to avoid modifying the original
            desc_copy = BeautifulSoup(str(desc_elem), 'html.parser')
            
            # Remove "similar jobs" and other unwanted sections more carefully
            # Only remove if they're clearly separate sections, not part of the description
            for unwanted in desc_copy.find_all(['div', 'section'], class_=re.compile(r'similar|related|recommended|alert', re.I)):
                # Check if this is a separate section (has specific classes)
                if unwanted.get('class') and any('similar' in str(c).lower() or 'related' in str(c).lower() for c in unwanted.get('class')):
                    unwanted.decompose()
            
            # Remove navigation, header, footer elements
            for tag in desc_copy.find_all(['header', 'footer', 'nav', 'aside', 'button']):
                tag.decompose()
            
            description = desc_copy.get_text(strip=True)
            if description and len(description) > 100:
                # Remove "similar jobs" text patterns more carefully
                # Only remove if it's clearly a separate section
                description = re.sub(r'Our Most Similar Jobs.*?View more similar jobs', '', description, flags=re.DOTALL | re.IGNORECASE)
                description = re.sub(r'Similar Jobs.*?View more', '', description, flags=re.DOTALL | re.IGNORECASE)
                # Clean up whitespace
                description = re.sub(r'\s+', ' ', description).strip()
                if len(description) > 100:
                    return description
    
    # Fallback 1: Look for content in the right panel (SimplyHired's layout)
    right_panel = soup.select_one('div[class*="right"], div[class*="detail"], div[class*="job-detail"]')
    if right_panel:
        # Find the description section within the right panel
        desc_in_panel = right_panel.select_one('div[class*="description"], div[class*="Description"]')
        if desc_in_panel:
            # Remove unwanted sections
            for unwanted in desc_in_panel.find_all(['div', 'section'], class_=re.compile(r'similar|related|recommended|alert|sign', re.I)):
                unwanted.decompose()
            description = desc_in_panel.get_text(strip=True)
            if description and len(description) > 100:
                description = re.sub(r'Our Most Similar Jobs.*?View more similar jobs', '', description, flags=re.DOTALL | re.IGNORECASE)
                description = re.sub(r'\s+', ' ', description).strip()
                if len(description) > 100:
                    return description
    
    # Fallback 2: Look for the main content area
    main_content = soup.select_one('main, article, div[role="main"]')
    if main_content:
        # Find description within main content
        desc_in_main = main_content.select_one('div[class*="description"], div[class*="Description"], section[class*="description"]')
        if desc_in_main:
            # Remove unwanted sections
            for unwanted in desc_in_main.find_all(['div', 'section'], class_=re.compile(r'similar|related|recommended|alert|sign', re.I)):
                unwanted.decompose()
            description = desc_in_main.get_text(strip=True)
            if description and len(description) > 200:
                description = re.sub(r'Our Most Similar Jobs.*?View more similar jobs', '', description, flags=re.DOTALL | re.IGNORECASE)
                description = re.sub(r'Similar Jobs.*?$', '', description, flags=re.DOTALL | re.IGNORECASE)
                description = re.sub(r'I want to receive.*?Privacy Policy\.?', '', description, flags=re.DOTALL | re.IGNORECASE)
                description = re.sub(r'Sign In or Sign Up.*?$', '', description, flags=re.DOTALL | re.IGNORECASE)
                description = re.sub(r'\s+', ' ', description).strip()
                if len(description) > 200:
                    return description[:5000]  # Limit to 5000 chars
        
        # If no specific description section found, use main content but be more selective
        # Remove unwanted sections first
        for unwanted in main_content.find_all(['div', 'section'], class_=re.compile(r'similar|related|recommended|alert|sign|navigation|header|footer', re.I)):
            unwanted.decompose()
        
        # Remove header, footer, nav elements
        for tag in main_content.find_all(['header', 'footer', 'nav', 'aside', 'button']):
            tag.decompose()
        
        description = main_content.get_text(strip=True)
        if description and len(description) > 200:
            # Remove "similar jobs" text patterns
            description = re.sub(r'Our Most Similar Jobs.*?View more similar jobs', '', description, flags=re.DOTALL | re.IGNORECASE)
            description = re.sub(r'Similar Jobs.*?View more', '', description, flags=re.DOTALL | re.IGNORECASE)
            description = re.sub(r'I want to receive.*?Privacy Policy\.?', '', description, flags=re.DOTALL | re.IGNORECASE)
            description = re.sub(r'Sign In or Sign Up.*?$', '', description, flags=re.DOTALL | re.IGNORECASE)
            # Remove common SimplyHired footer text
            description = re.sub(r'By signing in.*?Privacy Policy\.?', '', description, flags=re.DOTALL | re.IGNORECASE)
            description = re.sub(r'\s+', ' ', description).strip()
            if len(description) > 200:
                return description[:5000]  # Limit to 5000 chars
    
    # Fallback 3: Look for any div with substantial text content that might be the description
    # This is a last resort
    all_divs = soup.find_all('div')
    for div in all_divs:
        text = div.get_text(strip=True)
        # Look for divs with substantial text (likely description)
        if text and 500 < len(text) < 10000:
            # Check if it contains job-related keywords
            text_lower = text.lower()
            if any(keyword in text_lower for keyword in ['responsibilities', 'requirements', 'qualifications', 'experience', 'skills', 'duties', 'role', 'position']):
                # Remove unwanted sections
                for unwanted in div.find_all(['div', 'section'], class_=re.compile(r'similar|related|recommended', re.I)):
                    unwanted.decompose()
                description = div.get_text(strip=True)
                description = re.sub(r'Our Most Similar Jobs.*?View more similar jobs', '', description, flags=re.DOTALL | re.IGNORECASE)
                description = re.sub(r'\s+', ' ', description).strip()
                if len(description) > 300:
                    return description[:5000]
    
    return None


def _extract_experience_from_full_page_simplyhired(soup: BeautifulSoup) -> Optional[str]:
    """Extract experience level from SimplyHired's full job page."""
    text_content = soup.get_text().lower()
    
    # Experience level patterns (order matters - more specific first)
    experience_patterns = {
        'Executive': ['executive', 'c-level', 'chief', 'vp', 'vice president', 'president'],
        'Director': ['director', 'head of'],
        'Senior': ['senior', 'lead', 'principal', 'staff', 'sr.', 'sr '],
        'Mid': ['mid-level', 'mid level', 'intermediate', '3-5 years', '4-6 years', '5+ years'],
        'Junior': ['junior', 'jr.', 'jr ', '1-2 years', '2-3 years'],
        'Entry': ['entry level', 'entry-level', 'no experience', '0-1 years', 'new grad', 'graduate'],
        'Intern': ['internship', 'intern', 'trainee', 'co-op', 'student']
    }
    
    for exp_level, patterns in experience_patterns.items():
        if any(pattern in text_content for pattern in patterns):
            return exp_level
    
    # Check for years of experience mentions
    years_match = re.search(r'(\d+)\+?\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience|exp)', text_content)
    if years_match:
        years = int(years_match.group(1))
        if years == 0:
            return 'Entry'
        elif years <= 2:
            return 'Junior'
        elif years <= 5:
            return 'Mid'
        elif years <= 10:
            return 'Senior'
        else:
            return 'Executive'
    
    return None


def _extract_benefits_from_full_page_simplyhired(soup: BeautifulSoup) -> Optional[List[str]]:
    """Extract benefits from SimplyHired's full job page - from Benefits section tags."""
    benefits = []
    
    # SimplyHired shows benefits as tags/badges in the Benefits section
    # Priority 1: Look for benefits section
    benefits_selectors = [
        'div[data-testid="viewjob-benefits"]',
        'div[class*="Benefits"]',
        'div[class*="benefits"]',
        'section[class*="benefits"]',
        'div[class*="JobDetails"] div[class*="benefit"]',  # Benefits within Job Details
    ]
    
    for selector in benefits_selectors:
        benefits_elem = soup.select_one(selector)
    if benefits_elem:
            # Look for benefit tags/badges (SimplyHired shows them as clickable or styled elements)
            tag_elements = benefits_elem.find_all(['a', 'span', 'div', 'li'], 
                class_=re.compile(r'tag|badge|chip|benefit', re.I))
            
            for tag in tag_elements:
                benefit_text = tag.get_text(strip=True)
                if benefit_text and 3 < len(benefit_text) < 100:
                    if benefit_text.lower() not in ['benefits', 'quick apply']:
                        if benefit_text not in benefits:
                            benefits.append(benefit_text)
            
            # Also look for list items if no tags found
            if not benefits:
                items = benefits_elem.find_all(['li', 'span', 'div'])
                for item in items:
                    benefit_text = item.get_text(strip=True)
                    if benefit_text and 3 < len(benefit_text) < 100:
                        if benefit_text.lower() not in ['benefits']:
                            if benefit_text not in benefits:
                                benefits.append(benefit_text)
            
            if benefits:
                return benefits[:20]  # Limit to 20 benefits
    
    # Pattern matching fallback - search for common benefit keywords
    text_content = soup.get_text().lower()
    
    benefit_keywords = [
        'health insurance', 'medical insurance', 'dental insurance', 'dental', 'vision insurance', 'vision',
        '401k', '401(k)', 'retirement plan', 'pension', 'retirement',
        'paid time off', 'pto', 'vacation', 'vacation days', 'paid vacation',
        'sick leave', 'sick days', 'sick time',
        'flexible schedule', 'flexible hours', 'flex time',
        'remote work', 'work from home', 'hybrid work', 'telecommute',
        'stock options', 'equity', 'rsu', 'espp',
        'bonus', 'performance bonus', 'annual bonus', 'signing bonus',
        'professional development', 'training', 'tuition reimbursement', 'education assistance',
        'gym membership', 'wellness program', 'fitness',
        'parental leave', 'maternity leave', 'paternity leave',
        'life insurance', 'disability insurance',
        'employee discount', 'company perks',
        'commuter benefits', 'transit benefits',
        'free lunch', 'free food', 'snacks', 'catered meals',
        'mental health', 'eap', 'counseling',
    ]
    
    for benefit in benefit_keywords:
        if benefit in text_content and benefit.title() not in benefits:
            benefits.append(benefit.title())
    
    return benefits[:15] if benefits else None


def _extract_requirements_from_full_page_simplyhired(soup: BeautifulSoup) -> Optional[List[str]]:
    """Extract requirements from SimplyHired's full job page."""
    requirements = []
    
    # SimplyHired requirements selectors
    req_selectors = [
        'div[data-testid="viewjob-requirements"]',
        'div[class*="Requirements"]',
        'div[class*="requirements"]',
        'ul[class*="requirements"]',
        'section[class*="requirements"]',
        'div[class*="Qualifications"]',
        'div[class*="qualifications"]',
    ]
    
    for selector in req_selectors:
        req_elem = soup.select_one(selector)
        if req_elem:
            # Try to find list items
            items = req_elem.find_all(['li'])
            for item in items:
                req_text = item.get_text(strip=True)
                if req_text and len(req_text) > 10 and len(req_text) < 500:
                    requirements.append(req_text)
            if requirements:
                return requirements[:10]  # Limit to 10 requirements
    
    # Pattern matching fallback
    text_content = soup.get_text()
    
    req_patterns = [
        r'Requirements?:?\s*([^\n]+)',
        r'Must have:?\s*([^\n]+)',
        r'Required:?\s*([^\n]+)',
        r'Qualifications?:?\s*([^\n]+)',
        r'What you(?:\'ll)? need:?\s*([^\n]+)',
        r'What we(?:\'re)? looking for:?\s*([^\n]+)',
    ]
    
    for pattern in req_patterns:
        matches = re.findall(pattern, text_content, re.IGNORECASE | re.MULTILINE)
        for match in matches:
            req_text = match.strip()
            if len(req_text) > 10 and len(req_text) < 300:
                requirements.append(req_text)
    
    return requirements[:10] if requirements else None


def _extract_skills_from_full_page_simplyhired(soup: BeautifulSoup) -> Optional[List[str]]:
    """Extract skills from SimplyHired's full job page - from Qualifications/Keywords section."""
    skills = []
    
    # SimplyHired shows skills/qualifications as clickable tags/badges
    # Priority 1: Look for qualifications/keywords section
    qual_selectors = [
        'div[data-testid="viewjob-qualifications"]',
        'div[data-testid="viewjob-keywords"]',
        'div[class*="Qualifications"]',
        'div[class*="qualifications"]',
        'div[class*="Keywords"]',
        'div[class*="keywords"]',
        'div[class*="Skills"]',
        'div[class*="skills"]',
        'section[class*="qualifications"]',
        'section[class*="keywords"]',
    ]
    
    for selector in qual_selectors:
        qual_elem = soup.select_one(selector)
        if qual_elem:
            # Look for clickable tags/badges/links (SimplyHired shows them as clickable elements)
            # These are typically <a>, <span>, or <div> elements with skill names
            tag_elements = qual_elem.find_all(['a', 'span', 'div', 'button'], 
                class_=re.compile(r'tag|badge|chip|keyword|skill|qualification', re.I))
            
            for tag in tag_elements:
                skill_text = tag.get_text(strip=True)
                # Filter out common non-skill text
                if (skill_text and len(skill_text) > 1 and len(skill_text) < 80 and
                    skill_text.lower() not in ['quick apply', 'apply', 'view', 'more', 'less']):
                    if skill_text not in skills:
                        skills.append(skill_text)
            
            # Also look for any text that looks like skills (comma-separated or in lists)
            if not skills:
                text = qual_elem.get_text()
        # Split by common delimiters
                potential_skills = re.split(r'[,;|•\n]', text)
                for skill in potential_skills:
                    skill = skill.strip()
                    if skill and 2 < len(skill) < 80:
                        if skill.lower() not in ['qualifications', 'keywords', 'skills', 'requirements']:
                            if skill not in skills:
                                skills.append(skill)
            
            if skills:
                return skills[:30]  # Limit to 30 skills (SimplyHired can have many)
    
    # Priority 2: Look for any section with skill-like tags anywhere on the page
    # SimplyHired often shows qualifications as clickable tags
    all_tag_elements = soup.find_all(['a', 'span', 'div'], 
        class_=re.compile(r'tag|badge|chip|keyword|skill|qualification', re.I))
    
    for tag in all_tag_elements:
        skill_text = tag.get_text(strip=True)
        # Filter out navigation and UI elements
        if (skill_text and 2 < len(skill_text) < 80 and
            skill_text.lower() not in ['quick apply', 'apply', 'view', 'more', 'less', 'share', 'save']):
            # Check if parent is in a qualifications/keywords section
            parent = tag.find_parent(['div', 'section'])
            if parent:
                parent_class = ' '.join(parent.get('class', [])).lower()
                if any(keyword in parent_class for keyword in ['qualification', 'keyword', 'skill', 'tag']):
                    if skill_text not in skills:
                        skills.append(skill_text)
    
    if skills:
        return skills[:30]
    
    # Pattern matching fallback - common technical skills
    text_content = soup.get_text().lower()
    
    skill_keywords = [
        # Programming Languages
        'python', 'java', 'javascript', 'typescript', 'c++', 'c#', 'c', 'go', 'golang',
        'rust', 'ruby', 'php', 'swift', 'kotlin', 'scala', 'r', 'perl', 'shell', 'bash',
        'powershell', 'matlab', 'lua', 'haskell', 'clojure', 'elixir', 'dart',
        # Frontend
        'react', 'reactjs', 'angular', 'vue', 'vuejs', 'svelte', 'next.js', 'nextjs',
        'gatsby', 'html', 'css', 'sass', 'scss', 'less', 'tailwind', 'bootstrap',
        'jquery', 'redux', 'graphql', 'webpack', 'vite',
        # Backend
        'node.js', 'nodejs', 'express', 'django', 'flask', 'fastapi', 'spring',
        'spring boot', '.net', 'asp.net', 'rails', 'laravel', 'symfony',
        # Databases
        'sql', 'mysql', 'postgresql', 'postgres', 'mongodb', 'redis', 'elasticsearch',
        'cassandra', 'dynamodb', 'oracle', 'sql server', 'sqlite', 'firebase',
        'neo4j', 'couchdb', 'mariadb',
        # Cloud & DevOps
        'aws', 'amazon web services', 'azure', 'gcp', 'google cloud',
        'docker', 'kubernetes', 'k8s', 'jenkins', 'terraform', 'ansible',
        'ci/cd', 'gitlab', 'github actions', 'circleci', 'travis',
        # Data & ML
        'machine learning', 'ml', 'deep learning', 'ai', 'artificial intelligence',
        'tensorflow', 'pytorch', 'keras', 'scikit-learn', 'pandas', 'numpy',
        'data science', 'data analysis', 'tableau', 'power bi', 'spark', 'hadoop',
        # Tools & Others
        'git', 'linux', 'unix', 'windows', 'macos', 'agile', 'scrum', 'jira',
        'confluence', 'slack', 'figma', 'photoshop', 'illustrator',
        'rest api', 'restful', 'microservices', 'api design',
    ]
    
    for skill in skill_keywords:
        # Check for exact word match (with word boundaries)
        if re.search(rf'\b{re.escape(skill)}\b', text_content):
            formatted_skill = skill.upper() if len(skill) <= 3 else skill.title()
            if formatted_skill not in skills:
                skills.append(formatted_skill)
    
    return skills[:20] if skills else None


def _extract_industry_from_full_page_simplyhired(soup: BeautifulSoup) -> Optional[str]:
    """Extract industry from SimplyHired's full job page - only if clearly provided."""
    # SimplyHired typically does NOT show industry as a separate field
    # Only extract if there's a specific industry section/data-testid
    industry_selectors = [
        'div[data-testid="viewjob-industry"]',
        'span[data-testid="viewjob-industry"]',
        'div[class*="Industry"]',
        'span[class*="industry"]',
    ]
    
    for selector in industry_selectors:
        industry_elem = soup.select_one(selector)
        if industry_elem:
            industry_text = industry_elem.get_text(strip=True)
            if industry_text and len(industry_text) > 3:
                return industry_text
    
    # SimplyHired doesn't typically provide industry - return None
    # Don't guess from text content as it's unreliable
    return None


def _extract_company_size_from_full_page_simplyhired(soup: BeautifulSoup) -> Optional[str]:
    """Extract company size from SimplyHired's full job page - only if clearly provided."""
    # SimplyHired typically does NOT show company size as a separate field
    # Only extract if there's a specific company size section/data-testid
    # SimplyHired company size selectors
    size_selectors = [
        'div[data-testid="viewjob-companySize"]',
        'span[data-testid="viewjob-companySize"]',
        'div[class*="CompanySize"]',
        'span[class*="company-size"]',
        'div[class*="employees"]',
    ]
    
    for selector in size_selectors:
        size_elem = soup.select_one(selector)
        if size_elem:
            size_text = size_elem.get_text(strip=True)
            if size_text and ('employee' in size_text.lower() or re.search(r'\d', size_text)):
                return size_text
    
    # SimplyHired doesn't typically provide company size - return None
    # Don't guess from text content as it's unreliable
    return None


def _extract_company_from_full_page_simplyhired(soup: BeautifulSoup) -> tuple[Optional[str], Optional[str]]:
    """Extract company name and URL from SimplyHired's full job page."""
    company = None
    company_url = None
    
    # Priority 1: Look for company name in header/title area
    company_selectors = [
        'h1 + div a[href*="/company/"]',  # Company link after title
        'h1 + div a[href*="/cmp/"]',
        'div[data-testid="viewjob-companyName"]',
        'span[data-testid="viewjob-companyName"]',
        'div[class*="CompanyName"]',
        'span[class*="companyName"]',
        'div[class*="company-name"]',
        'a[href*="/company/"]',
        'a[href*="/cmp/"]',
    ]
    
    for selector in company_selectors:
        company_elem = soup.select_one(selector)
        if company_elem:
            company = company_elem.get_text(strip=True)
            # Clean up - remove trailing "—" if present
            company = company.rstrip('—').rstrip(' —').strip()
            if company and len(company) > 1:
                # Get URL if it's a link
                if company_elem.name == 'a':
                    href = company_elem.get('href', '')
                    if href:
                        company_url = f"https://www.simplyhired.com{href}" if href.startswith('/') else href
                return company, company_url
    
    # Priority 2: Look for company in structured data or meta tags
    # Check for JSON-LD structured data
    json_ld_scripts = soup.find_all('script', type='application/ld+json')
    for script in json_ld_scripts:
        try:
            import json
            data = json.loads(script.string)
            if isinstance(data, dict):
                # Look for hiringOrganization
                org = data.get('hiringOrganization', {})
                if isinstance(org, dict):
                    name = org.get('name')
                    if name:
                        company = name
                        # Check for URL
                        url = org.get('url') or org.get('sameAs')
                        if url:
                            company_url = url
                        return company, company_url
        except:
            pass
    
    # Priority 3: Pattern matching in page text
    # Look for "Company Name" pattern near the top of the page
    page_text = soup.get_text()
    # Try to find company name in first 2000 characters
    header_text = page_text[:2000]
    
    # Look for patterns like "Company Name — Location" or "at Company Name"
    company_patterns = [
        r'at\s+([A-Z][A-Za-z0-9\s&.,-]+?)(?:\s+—|\s+in|\s*$)',
        r'([A-Z][A-Za-z0-9\s&.,-]+?)\s+—\s*[A-Z]',  # Company — Location
    ]
    
    for pattern in company_patterns:
        match = re.search(pattern, header_text)
        if match:
            potential_company = match.group(1).strip()
            # Filter out common false positives
            if len(potential_company) > 2 and len(potential_company) < 100:
                if not any(word in potential_company.lower() for word in ['job', 'apply', 'search', 'simplyhired']):
                    company = potential_company
                    break
    
    return company, company_url


def _extract_location_from_full_page_simplyhired(soup: BeautifulSoup) -> Optional[str]:
    """Extract location from SimplyHired's full job page."""
    location = None
    
    # Priority 1: Direct location selectors
    location_selectors = [
        'div[data-testid="viewjob-location"]',
        'span[data-testid="viewjob-location"]',
        'div[class*="Location"]',
        'span[class*="location"]',
        'div[class*="job-location"]',
        'span[class*="job-location"]',
    ]
    
    for selector in location_selectors:
        location_elem = soup.select_one(selector)
        if location_elem:
            location = location_elem.get_text(strip=True)
            if location and len(location) > 2:
                return location
    
    # Priority 2: Look in structured data
    json_ld_scripts = soup.find_all('script', type='application/ld+json')
    for script in json_ld_scripts:
        try:
            import json
            data = json.loads(script.string)
            if isinstance(data, dict):
                # Look for jobLocation
                job_location = data.get('jobLocation', {})
                if isinstance(job_location, dict):
                    address = job_location.get('address', {})
                    if isinstance(address, dict):
                        # Try addressLocality and addressRegion
                        city = address.get('addressLocality', '')
                        state = address.get('addressRegion', '')
                        if city and state:
                            return f"{city}, {state}"
                        elif city:
                            return city
                        # Or try addressCountry
                        country = address.get('addressCountry', '')
                        if country:
                            return country
        except:
            pass
    
    # Priority 3: Pattern matching in page text
    # Look for location patterns like "City, State" or "City, ST"
    page_text = soup.get_text()
    header_text = page_text[:2000]  # Check first 2000 chars
    
    # Common location patterns
    location_patterns = [
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*,\s*([A-Z]{2})\b',  # City, ST
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*,\s*([A-Z][a-z]+)',  # City, State
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*,\s*([A-Z][a-z]+)\s*,\s*([A-Z]{2})',  # City, State, ST
    ]
    
    for pattern in location_patterns:
        match = re.search(pattern, header_text)
        if match:
            location_parts = [g for g in match.groups() if g]
            if location_parts:
                location = ', '.join(location_parts)
                return location
    
    # Priority 4: Look for remote/hybrid indicators
    text_lower = page_text.lower()
    if 'remote' in text_lower and 'hybrid' not in text_lower:
        return 'Remote'
    elif 'hybrid' in text_lower:
        return 'Hybrid'
    
    return None


def _extract_job_id_from_full_page_simplyhired(soup: BeautifulSoup, url: str) -> Optional[str]:
    """Extract job ID from SimplyHired's full job page."""
    # Try to extract from URL first (most reliable)
    # SimplyHired URLs typically have pattern like /job/{job_id}
    id_match = re.search(r'/job/([^/?&#]+)', url)
    if id_match:
        return id_match.group(1)
    
    # Look for job ID in data attributes
    job_id_elem = soup.find(attrs={'data-job-id': True})
    if job_id_elem:
        return job_id_elem.get('data-job-id')
    
    job_id_elem = soup.find(attrs={'data-id': True})
    if job_id_elem:
        return job_id_elem.get('data-id')
    
    # Look in meta tags
    meta_elem = soup.find('meta', attrs={'property': re.compile(r'job.*id', re.I)})
    if meta_elem and meta_elem.get('content'):
        return meta_elem.get('content')
    
    return None


def _extract_complete_job_details_from_url_simplyhired(driver, job: Job, original_search_url: str) -> Optional[Job]:
    """
    Extract complete job details by navigating to the individual job page URL.
    This fetches the full job description, requirements, benefits, skills, etc.
    """
    if not job.url:
        return job
    
    try:
        # Navigate to the individual job page
        print(f"    → Navigating to job page: {job.url}")
        
        # Set a reasonable timeout
        driver.set_page_load_timeout(60)
        driver.get(job.url)
        
        # Wait for page to load
        time.sleep(random.uniform(2.0, 3.5))
        
        # Try to wait for job content
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 
                    "div[class*='description'], div[class*='Description'], article, main"))
            )
        except:
            pass  # Continue even if selector not found
        
        # Get full page content
        full_page_content = driver.page_source
        full_page_soup = BeautifulSoup(full_page_content, 'html.parser')
        
        # Extract job ID from full page if not already present
        if not job.job_id:
            enhanced_job_id = _extract_job_id_from_full_page_simplyhired(full_page_soup, driver.current_url)
            if enhanced_job_id:
                job.job_id = enhanced_job_id
                print(f"    ✓ Enhanced job ID: {enhanced_job_id}")
        
        # Extract enhanced details from the full page
        enhanced_company, enhanced_company_url = _extract_company_from_full_page_simplyhired(full_page_soup)
        enhanced_location = _extract_location_from_full_page_simplyhired(full_page_soup)
        enhanced_salary = _extract_salary_from_full_page_simplyhired(full_page_soup)
        enhanced_employment = _extract_employment_from_full_page_simplyhired(full_page_soup)
        enhanced_date = _extract_date_from_full_page_simplyhired(full_page_soup)
        enhanced_description = _extract_description_from_full_page_simplyhired(full_page_soup)
        enhanced_experience = _extract_experience_from_full_page_simplyhired(full_page_soup)
        enhanced_benefits = _extract_benefits_from_full_page_simplyhired(full_page_soup)
        enhanced_requirements = _extract_requirements_from_full_page_simplyhired(full_page_soup)
        enhanced_skills = _extract_skills_from_full_page_simplyhired(full_page_soup)
        # Note: Industry and company_size are not typically provided by SimplyHired
        # Only extract if clearly available, otherwise leave as None
        
        # Update job with enhanced details (prioritize full page data)
        if enhanced_company and not job.company:
            job.company = enhanced_company
            print(f"    ✓ Enhanced company: {enhanced_company}")
        
        if enhanced_company_url and not job.company_url:
            job.company_url = enhanced_company_url
            print(f"    ✓ Enhanced company URL: {enhanced_company_url}")
        
        if enhanced_location and not job.location:
            job.location = enhanced_location
            print(f"    ✓ Enhanced location: {enhanced_location}")
        
        if enhanced_salary and (not job.salary_range or len(enhanced_salary) > len(job.salary_range or "")):
            job.salary_range = enhanced_salary
            print(f"    ✓ Enhanced salary: {enhanced_salary}")
        
        if enhanced_employment and not job.employment_type:
            job.employment_type = enhanced_employment
            print(f"    ✓ Enhanced employment: {enhanced_employment}")
        
        if enhanced_date and not job.posted_date:
            job.posted_date = enhanced_date
            print(f"    ✓ Enhanced date: {enhanced_date}")
        
        # Always update description with full job description from detail page
        # Prioritize full page description as it's more complete
        if enhanced_description:
            if len(enhanced_description) > 100:
                job.description = enhanced_description
                print(f"    ✓ Enhanced description: {len(enhanced_description)} characters")
            elif not job.description or len(job.description) < 100:
                # Use it even if shorter if we don't have a good description yet
                job.description = enhanced_description
                print(f"    ✓ Updated description: {len(enhanced_description)} characters")
        elif not job.description:
            print(f"    ⚠️  No description found on full page")
        
        if enhanced_experience and not job.experience_level:
            job.experience_level = enhanced_experience
            print(f"    ✓ Enhanced experience: {enhanced_experience}")
        
        # Benefits - Always update if found (SimplyHired shows these as tags)
        if enhanced_benefits:
            job.benefits = enhanced_benefits
            print(f"    ✓ Enhanced benefits: {len(enhanced_benefits)} items")
        
        # Requirements - Extract from job description if not in separate section
        if enhanced_requirements and (not job.requirements or len(job.requirements or []) < len(enhanced_requirements)):
            job.requirements = enhanced_requirements
            print(f"    ✓ Enhanced requirements: {len(enhanced_requirements)} items")
        
        # Skills - Always update if found (SimplyHired shows these as qualification tags)
        if enhanced_skills:
            job.skills = enhanced_skills
            print(f"    ✓ Enhanced skills: {len(enhanced_skills)} items")
        
        # Industry and company_size - Only set if clearly available (not typically on SimplyHired)
        if enhanced_industry:
            job.industry = enhanced_industry
            print(f"    ✓ Enhanced industry: {enhanced_industry}")
        
        if enhanced_company_size:
            job.company_size = enhanced_company_size
            print(f"    ✓ Enhanced company size: {enhanced_company_size}")
        
    except Exception as e:
        print(f"    ⚠️  Error extracting enhanced details: {e}")
        # Continue with basic job data if enhancement fails
    finally:
        # Navigate back to the original search results page
        try:
            print(f"    ← Navigating back to search results")
            driver.get(original_search_url)
            time.sleep(random.uniform(2.0, 3.0))  # Wait for page to load
        except Exception as nav_error:
            print(f"    ⚠️  Warning: Could not navigate back: {nav_error}")
            # If navigation back fails, it's not critical - we can continue
    
    return job


def _extract_industry_simplyhired(card) -> Optional[str]:
    """Extract industry from SimplyHired job card (basic extraction from card)."""
    # Industry is typically not in the card preview - will be enhanced from full page
    text_content = card.get_text().lower()
    
    industries = [
        'technology', 'healthcare', 'finance', 'education', 'retail',
        'manufacturing', 'consulting', 'nonprofit', 'government',
        'media', 'entertainment', 'real estate', 'automotive'
    ]
    
    for industry in industries:
        if industry in text_content:
            return industry.title()
    
    return None


def _extract_company_size_simplyhired(card) -> Optional[str]:
    """Extract company size from SimplyHired job card (basic extraction from card)."""
    # Company size is typically not in the card preview - will be enhanced from full page
    text_content = card.get_text()
    
    size_patterns = [
        r'(\d+)\s*-\s*(\d+)\s*employees',
        r'(\d+)\+?\s*employees',
    ]
    
    for pattern in size_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            return match.group().strip()
    
    return None


def _extract_detailed_job_info_simplyhired(card) -> Optional[Job]:
    """Extract detailed job information from a SimplyHired job card."""
    try:
        # Extract job title
        title = _extract_title_simplyhired(card)
        if not title or len(title) < 3:
            return None
        
        # Extract company information
        company, company_url = _extract_company_info_simplyhired(card)
        
        # Extract location and remote type
        location, remote_type = _extract_location_info_simplyhired(card)
        
        # Extract salary range
        salary_range = _extract_salary_simplyhired(card)
        
        # Extract job type and employment type
        job_type, employment_type = _extract_job_types_simplyhired(card)
        
        # Extract experience level
        experience_level = _extract_experience_level_simplyhired(card)
        
        # Extract posted date
        posted_date = _extract_posted_date_simplyhired(card)
        
        # Extract job description
        description = _extract_description_simplyhired(card)
        
        # Extract job URL
        job_url = _extract_job_url_simplyhired(card)
        
        # Extract job ID
        job_id = _extract_job_id_simplyhired(card)
        
        # Extract skills, requirements, and benefits
        skills = _extract_skills_simplyhired(card)
        requirements = _extract_requirements_simplyhired(card)
        benefits = _extract_benefits_simplyhired(card)
        
        # Extract industry and company size (basic from card, enhanced from full page)
        industry = _extract_industry_simplyhired(card)
        company_size = _extract_company_size_simplyhired(card)
        
        return Job(
            title=title,
            company=company,
            company_url=company_url,
            location=location,
            description=description,
            url=job_url,
            salary_range=salary_range,
            job_type=job_type,
            posted_date=posted_date,
            experience_level=experience_level,
            benefits=benefits,
            requirements=requirements,
            skills=skills,
            remote_type=remote_type,
            employment_type=employment_type,
            industry=industry,
            company_size=company_size,
            job_id=job_id
        )
        
    except Exception as e:
        print(f"Error extracting job info: {e}")
        return None


def _is_end_of_results_simplyhired(soup: BeautifulSoup) -> bool:
    """Check if we've reached the end of SimplyHired results."""
    # Look for "no more results" indicators
    no_results_indicators = [
        'no results found',
        'no jobs found',
        'try different keywords',
        'no matching jobs'
    ]
    
    page_text = soup.get_text().lower()
    for indicator in no_results_indicators:
        if indicator in page_text:
            return True
    
    return False


def _create_job_id(job: Job) -> str:
    """Create a unique job ID from job data."""
    # Use title + company + location as unique identifier
    parts = [job.title or '', job.company or '', job.location or '']
    return '|'.join(parts).lower().strip()


def scrape_simplyhired_selenium(
    query: str,
    location: Optional[str] = None,
    max_results: int = 20,
    job_type: Optional[str] = None,
    salary_min: Optional[int] = None,
    salary_max: Optional[int] = None,
    experience_level: Optional[str] = None,
    employment_type: Optional[str] = None,
    days_old: Optional[int] = None,
    fetch_full_details: bool = True
) -> List[Job]:
    """
    Enhanced SimplyHired scraper using Selenium with comprehensive data extraction and advanced filtering.
    
    Args:
        query: Job search query (e.g., "python developer")
        location: Job location (e.g., "remote", "New York, NY", "USA", "California")
        max_results: Maximum number of jobs to return
        job_type: Job type filter ('remote', 'hybrid', 'On-site')
        salary_min: Minimum salary filter (e.g., 50000)
        salary_max: Maximum salary filter (e.g., 100000)
        experience_level: Experience level filter ('intern', 'assistant', 'entry', 'junior', 'mid', 'mid-senior', 'senior', 'director', 'executive')
        employment_type: Employment type filter ('Full-Time', 'Part-Time', 'Contract', 'Internship')
        days_old: Filter jobs posted within last N days (e.g., 30 for last 30 days)
        fetch_full_details: If True, visit each job page to extract complete details (slower but more data).
                           If False, only extract data from search results page (faster but less data).
    
    Returns:
        List of Job objects with detailed information including:
        - title, company, company_url, location
        - description (full from job page if fetch_full_details=True)
        - salary_range, job_type, employment_type
        - posted_date, experience_level, remote_type
        - benefits, requirements, skills
        - industry, company_size, job_id
    """
    global _last_fetch
    
    # Rate limiting
    with _request_lock:
        now = time.monotonic()
        jitter = random.uniform(0, 0.75)
        wait = settings.MIN_DELAY + jitter - (now - _last_fetch)
        if wait > 0:
            time.sleep(wait)
        _last_fetch = time.monotonic()
    
    return _scrape_sync_simplyhired(query, location, max_results, job_type, salary_min, salary_max, experience_level, employment_type, days_old, fetch_full_details)


def _scrape_sync_simplyhired(
    query: str,
    location: Optional[str],
    max_results: int,
    job_type: Optional[str] = None,
    salary_min: Optional[int] = None,
    salary_max: Optional[int] = None,
    experience_level: Optional[str] = None,
    employment_type: Optional[str] = None,
    days_old: Optional[int] = None,
    fetch_full_details: bool = True
) -> List[Job]:
    """Enhanced synchronous scraping function for SimplyHired with full job detail extraction."""
    driver = None
    
    # Check for too many Chrome processes before starting
    process_count = check_chrome_process_count()
    if process_count > 15:
        print(f"⚠️  WARNING: {process_count} Chrome processes detected before scraping!")
        cleanup_zombie_processes(aggressive=True)
        time.sleep(2.0)
    
    try:
        print(f"🔍 [SIMPLYHIRED] Starting scrape: query='{query}', location='{location}', max_results={max_results}")
        print("🔄 [SIMPLYHIRED] Getting driver instance...")
        driver = get_shared_driver(force_new=True)
        print("✓ [SIMPLYHIRED] Driver obtained successfully")
        
        jobs = []
        seen_job_ids = set()
        page = 0
        max_pages = 15  # SimplyHired typically shows multiple jobs per page
        
        while len(jobs) < max_results and page < max_pages:
            # Build SimplyHired search URL
            base_url = "https://www.simplyhired.com/search"
            params = f"?q={quote_plus(query)}"
            
            if location:
                location_param = _format_location_for_simplyhired(location)
                params += f"&l={location_param}"
                print(f"DEBUG - Location '{location}' formatted as '{location_param}'")
            
            # Add employment type filter
            if employment_type:
                employment_filter = _get_simplyhired_employment_filter(employment_type)
                if employment_filter:
                    params += f"&{employment_filter}"
                    print(f"DEBUG - Employment type '{employment_type}' mapped to filter '{employment_filter}'")
            
            # Add date posted filter
            if days_old:
                date_filter = _get_simplyhired_date_filter(days_old)
                if date_filter:
                    params += f"&{date_filter}"
                    print(f"DEBUG - Date filter '{days_old} days' mapped to '{date_filter}'")
            
            # Add pagination
            if page > 0:
                params += f"&pn={page + 1}"  # SimplyHired uses pn parameter for page number
            
            url = base_url + params
            print(f"Navigating to page {page + 1}: {url}")
            
            # Navigate to the page
            retries = 0
            max_navigation_retries = getattr(settings, "MAX_RETRIES", 3)
            cloudflare_wait_done = False
            
            while True:
                try:
                    print(f"   [SIMPLYHIRED] Attempting navigation to: {url}")
                    driver.get(url)
                    print(f"✓ [SIMPLYHIRED] Navigation successful")
                    
                    # Initial wait for page to start loading
                    time.sleep(2.0)
                    
                    # Check if we hit Cloudflare challenge - need to wait for it to resolve
                    page_html = driver.page_source or ""
                    
                    # Detect Cloudflare challenge
                    is_cloudflare_challenge = (
                        "Just a moment" in page_html
                        or "Checking your browser" in page_html
                        or "Enable JavaScript and cookies to continue" in page_html
                        or "challenge-platform" in page_html
                        or len(page_html) < 1000  # Very short page = likely challenge
                    )
                    
                    if is_cloudflare_challenge and not cloudflare_wait_done:
                        print("⏳ [SIMPLYHIRED] Cloudflare challenge detected, waiting for resolution...")
                        # Wait for Cloudflare to resolve (usually 3-10 seconds)
                        for wait_attempt in range(15):
                            time.sleep(2.0)
                            page_html = driver.page_source or ""
                            
                            # Check if challenge resolved
                            has_job_content = (
                                '/job/' in page_html
                                or 'SimplyHired' in page_html
                                or 'jobs in' in page_html.lower()
                                or len(page_html) > 10000
                            )
                            
                            if has_job_content:
                                print(f"✓ [SIMPLYHIRED] Cloudflare resolved after {(wait_attempt + 1) * 2}s")
                                cloudflare_wait_done = True
                                break
                            
                            if wait_attempt % 3 == 2:
                                print(f"   Still waiting... ({(wait_attempt + 1) * 2}s)")
                        
                        if not cloudflare_wait_done:
                            # Check one more time after full wait
                            page_html = driver.page_source or ""
                    
                    # Final content check
                    page_html = driver.page_source or ""
                    if not page_html or len(page_html) < 500:
                        raise Exception(f"Page source too short ({len(page_html)} chars)")
                    
                    print(f"✓ [SIMPLYHIRED] Page source retrieved: {len(page_html)} characters")
                    break
                    
                except Exception as nav_error:
                    error_msg = str(nav_error)
                    print(f"❌ [SIMPLYHIRED] Navigation failed: {error_msg}")
                    
                    if retries < max_navigation_retries:
                        retries += 1
                        print(f"⚠️  Retrying navigation ({retries}/{max_navigation_retries})...")
                        time.sleep(3.0)
                        continue
                    else:
                        raise
            
            # Final Cloudflare check
            page_html = driver.page_source or ""
            has_cloudflare_indicators = (
                "Checking your browser" in page_html
                or "Enable JavaScript and cookies to continue" in page_html
                or ("challenge-platform" in page_html and "Just a moment" in page_html)
                or len(page_html) < 1000
            )
            has_simplyhired_content = (
                '/job/' in page_html
                or 'SimplyHired' in page_html
                or 'job' in page_html.lower() and 'search' in page_html.lower()
            )
            
            is_actually_blocked = has_cloudflare_indicators and not has_simplyhired_content
            
            if is_actually_blocked:
                raise CloudflareBlockedError("SimplyHired blocked by Cloudflare (captcha/turnstile) - page didn't load properly")
            
            # Random delay to appear more human-like
            time.sleep(random.uniform(getattr(settings, "PAGE_DELAY_MIN", 2.0), getattr(settings, "PAGE_DELAY_MAX", 4.0)))
            if getattr(settings, "HUMANIZE", True):
                _perform_human_interactions(driver)
            
            # Wait for job results with better selectors
            try:
                WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 
                        "li h2 a[href*='/job/'], "  # Job title link in list item
                        "a[href*='/job/'], "  # Any job link
                        "ul li h2, "  # Heading in list
                        "[class*='job'], "  # Any job class
                        "[class*='Job']"  # Any Job class
                    ))
                )
                print("✓ [SIMPLYHIRED] Job elements detected on page")
            except Exception as wait_err:
                print(f"⚠️  Wait for job elements timed out: {wait_err}")
                # Continue anyway - might still have content
            
            # Additional scrolling to load lazy content
            if getattr(settings, "HUMANIZE", True):
                _progressive_scroll(driver)
            
            # Extra wait for any lazy-loaded content
            time.sleep(1.5)
            
            # Get page source and parse with BeautifulSoup
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            
            # Find job cards
            job_cards = _find_job_cards_simplyhired(soup)
            
            print(f"Found {len(job_cards)} potential job cards on page {page + 1}")
            
            # If no job cards found, we've reached the end
            if not job_cards:
                print(f"DEBUG - No job cards found on page {page + 1}, reached end of results")
                break
            
            # Check if we've reached the end
            if _is_end_of_results_simplyhired(soup):
                print(f"Reached end of results on page {page + 1}")
                break
            
            # Process job cards
            page_jobs_added = 0
            browser_alive = True  # Track if browser is still usable for fetching details
            
            if not fetch_full_details:
                print("ℹ️  Fast mode: skipping job detail pages (using search results data only)")
            
            for i, card in enumerate(job_cards):
                try:
                    # Validate card
                    if not _is_valid_job_card_simplyhired(card):
                        print(f"DEBUG - Skipping card {i+1} - doesn't appear to be a valid job listing")
                        continue
                    
                    # Extract job info from search results card
                    job = _extract_detailed_job_info_simplyhired(card)
                    
                    if not job or not job.title:
                        continue
                    
                    # Create unique job ID
                    job_unique_id = _create_job_id(job)
                    
                    # Skip duplicates
                    if job_unique_id in seen_job_ids:
                        continue
                    seen_job_ids.add(job_unique_id)
                    
                    # Fetch full details from job page if enabled and browser is alive
                    if fetch_full_details and browser_alive and job.url:
                        print(f"  → Fetching complete data from job page: {job.title}")
                        try:
                            enhanced_job = _extract_complete_job_details_from_url_simplyhired(driver, job, url)
                            if enhanced_job:
                                job = enhanced_job
                            # Add delay to be respectful to SimplyHired's servers
                            time.sleep(random.uniform(1.5, 3.0))
                        except Exception as enhance_error:
                            error_msg = str(enhance_error).lower()
                            if "closed" in error_msg or "target" in error_msg or "session" in error_msg:
                                # Browser/driver was closed - stop trying to navigate
                                print(f"  ⚠️  Browser session lost during job detail extraction - using basic data for remaining jobs")
                                browser_alive = False
                            else:
                                print(f"  ⚠️  Error enhancing job details: {enhance_error}")
                            # Continue with basic job data if enhancement fails
                    
                    # Apply filters
                    if job_type:
                        job_type_lower = job_type.lower()
                        if job_type_lower == 'remote' and job.remote_type != 'Remote':
                            continue
                        elif job_type_lower == 'hybrid' and job.remote_type != 'Hybrid':
                            continue
                        elif job_type_lower in ['onsite', 'on-site'] and job.remote_type != 'On-site':
                            continue
                    
                    # Salary filtering
                    if salary_min or salary_max:
                        if job.salary_range:
                            # Extract salary numbers from salary_range string
                            salary_numbers = re.findall(r'\$?([\d,]+)', job.salary_range.replace(',', ''))
                            if salary_numbers:
                                try:
                                    # Use average if range, or single value
                                    salaries = [int(s) for s in salary_numbers]
                                    avg_salary = sum(salaries) / len(salaries)
                                    
                                    if salary_min and avg_salary < salary_min:
                                        continue
                                    if salary_max and avg_salary > salary_max:
                                        continue
                                except:
                                    pass  # If parsing fails, include the job
                    
                    # Experience level filtering
                    if experience_level and job.experience_level:
                        exp_levels = {
                            'intern': ['intern'],
                            'assistant': ['assistant'],
                            'entry': ['entry', 'junior'],
                            'junior': ['entry', 'junior'],
                            'mid': ['mid'],
                            'mid-senior': ['mid', 'senior'],
                            'senior': ['senior'],
                            'director': ['director'],
                            'executive': ['executive']
                        }
                        exp_lower = experience_level.lower()
                        if exp_lower in exp_levels:
                            if job.experience_level.lower() not in exp_levels[exp_lower]:
                                continue
                    
                    # Employment type filtering
                    if employment_type and job.employment_type:
                        if employment_type.lower() != job.employment_type.lower():
                            continue
                    
                    jobs.append(job)
                    page_jobs_added += 1
                    
                    if len(jobs) >= max_results:
                        break
                        
                except Exception as e:
                    print(f"Error processing job card {i+1}: {e}")
                    continue
            
            print(f"Added {page_jobs_added} jobs from page {page + 1} (total: {len(jobs)})")
            
            if len(jobs) >= max_results:
                break
            
            if page_jobs_added == 0:
                print("No new jobs added on this page, stopping pagination")
                break
            
            page += 1
        
        print(f"✓ [SIMPLYHIRED] Scraping complete: {len(jobs)} jobs found")
        return jobs[:max_results]
        
    except CloudflareBlockedError:
        raise
    except Exception as e:
        error_msg = str(e)
        print(f"❌ [SIMPLYHIRED] Error during scraping: {error_msg}")
        import traceback
        print(f"   Full traceback:\n{traceback.format_exc()}")
        raise Exception(f"Failed to scrape SimplyHired: {error_msg}")
    finally:
        # Cleanup is handled by the shared driver management
        pass


def close_driver():
    """Close the WebDriver when done."""
    # Use shared cleanup function
    cleanup_global_driver()


def force_cleanup_all():
    """Force cleanup of all Chrome/ChromeDriver resources."""
    # Use shared cleanup function
    from app.services.indeed_selenium_service import force_cleanup_all as shared_force_cleanup
    return shared_force_cleanup()

