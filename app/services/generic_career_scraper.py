"""
Universal Career Page Scraper - ENHANCED VERSION
Works with ANY website architecture and handles:
- Static HTML, SPAs (React/Vue/Angular), APIs
- Infinite Scroll, Pagination, Load More buttons
- Cookie consent banners (auto-accept)
- Modal overlays (auto-close)
- Search-first websites
- Multi-step navigation
- Dynamic content loading
- Anti-bot measures
"""
import asyncio
import re
import json
import random
import time
import logging
import os
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs
import undetected_chromedriver as uc
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from app.models.job_model import Job
from app.core.config import settings

# Setup logging (configure only if not already configured)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


# ============================================================================
# CONFIGURATION & CONSTANTS
# ============================================================================

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15',
]

JOB_BOARD_PATTERNS = {
    'greenhouse': {
        'domains': ['greenhouse.io', 'boards.greenhouse.io'],
        'api_pattern': r'/boards/([^/]+)/jobs',
        'selectors': ['.opening', '[data-qa="opening"]'],
    },
    'lever': {
        'domains': ['lever.co', 'jobs.lever.co'],
        'api_pattern': r'https://api.lever.co/v0/postings/([^/]+)',
        'selectors': ['.posting', '.postings-group'],
    },
    'workday': {
        'domains': ['myworkdayjobs.com'],
        'api_pattern': r'/wday/cxs/([^/]+)/jobs',
        'selectors': ['[data-automation-id="jobTitle"]'],
    },
    'jobvite': {
        'domains': ['jobvite.com'],
        'selectors': ['.jv-job-list-item'],
    },
    'smartrecruiters': {
        'domains': ['smartrecruiters.com'],
        'api_pattern': r'https://api.smartrecruiters.com/v1/companies/([^/]+)/postings',
        'selectors': ['.opening-job'],
    },
    'bamboohr': {
        'domains': ['bamboohr.com/careers'],
        'selectors': ['.BambooHR-ATS-Jobs-Item'],
    },
    'ashbyhq': {
        'domains': ['ashbyhq.com'],
        'api_pattern': r'ashbyhq.com/api/posting-api/job-board/([^/]+)',
        'selectors': ['[class*="JobsList"]'],
    },
    'workable': {
        'domains': ['apply.workable.com'],
        'selectors': ['[data-ui="job"]', 'li[data-ui="job"]', '.jobs-list li'],
    },
}

# Common patterns for extracting job fields
LOCATION_PATTERNS = [
    r'\b(Remote|Hybrid|On-site|Onsite)\b',
    r'\b([A-Z][a-z]+,\s*[A-Z]{2})\b',  # City, ST
    r'\b([A-Z][a-z\s]+,\s*[A-Z][a-z]+)\b',  # City, Country
    r'\b([A-Z][a-z\s]+,\s*[A-Z]{2},\s*[A-Z]{2,3})\b',  # City, State, Country
]

EMPLOYMENT_TYPE_PATTERNS = {
    'Full-time': r'\bfull[- ]time\b',
    'Part-time': r'\bpart[- ]time\b',
    'Contract': r'\bcontract(or|ual)?\b',
    'Internship': r'\bintern(ship)?\b',
    'Temporary': r'\btemp(orary)?\b',
    'Freelance': r'\bfreelance\b',
}

REMOTE_TYPE_PATTERNS = {
    'Remote': r'\bremote\b',
    'Hybrid': r'\bhybrid\b',
    'On-site': r'\bon-?site\b',
}

SALARY_PATTERNS = [
    r'\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*-\s*\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)',
    r'(\d{1,3}(?:,\d{3})*)\s*-\s*(\d{1,3}(?:,\d{3})*)\s*(?:USD|EUR|GBP|per year|annually)',
    r'\$(\d{1,3}[kK])\s*-\s*\$(\d{1,3}[kK])',
]

DATE_PATTERNS = [
    r'(\d{1,2})/(\d{1,2})/(\d{4})',  # MM/DD/YYYY
    r'(\d{4})-(\d{2})-(\d{2})',  # YYYY-MM-DD
    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s+(\d{4})',
    r'(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})',
]


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_chrome_executable_path() -> Optional[str]:
    """
    Get Chrome executable path based on environment.
    Checks CHROME_BIN env var first, then common installation paths.
    """
    # Check environment variable first (set in Dockerfile)
    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin and os.path.exists(chrome_bin):
        return chrome_bin
    
    # Common Linux paths
    linux_paths = [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ]
    
    # Common macOS paths
    mac_paths = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    
    # Common Windows paths
    windows_paths = [
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    ]
    
    # Check all paths
    all_paths = linux_paths + mac_paths + windows_paths
    for path in all_paths:
        if os.path.exists(path):
            return path
    
    # If not found, return None and let undetected-chromedriver find it
    return None


def get_random_user_agent() -> str:
    """Return a random user agent"""
    return random.choice(USER_AGENTS)


def create_session_with_retries() -> requests.Session:
    """Create a requests session with retry logic"""
    session = requests.Session()
    retry = Retry(
        total=3,
        read=3,
        connect=3,
        backoff_factor=0.5,
        status_forcelist=(500, 502, 504, 429)
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session


def extract_company_name_from_url(url: str) -> str:
    """Extract company name from URL"""
    parsed = urlparse(url)
    domain = parsed.netloc
    name = domain.replace('www.', '').split('.')[0]
    return name.replace('-', ' ').replace('_', ' ').title()


def detect_job_board(url: str) -> Optional[Dict[str, Any]]:
    """Detect if URL is a known job board and return its configuration"""
    url_lower = url.lower()
    for board_name, config in JOB_BOARD_PATTERNS.items():
        if any(domain in url_lower for domain in config['domains']):
            return {'name': board_name, 'config': config}
    return None


def extract_text_field(text: str, patterns: List[str]) -> Optional[str]:
    """Extract field using regex patterns"""
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def extract_dict_field(text: str, patterns: Dict[str, str]) -> Optional[str]:
    """Extract field using dict of patterns"""
    for field_name, pattern in patterns.items():
        if re.search(pattern, text, re.IGNORECASE):
            return field_name
    return None


def clean_text(text: str) -> str:
    """Clean extracted text"""
    if not text:
        return ""
    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text)
    # Remove special characters but keep basic punctuation
    text = re.sub(r'[^\w\s\-.,;:!?()/&$%]', '', text)
    return text.strip()


def is_valid_job_url(url: str) -> bool:
    """Check if URL looks like an actual job posting URL"""
    if not url:
        return True  # If no URL, don't filter based on this
    
    url_lower = url.lower()
    
    # URLs that are clearly NOT job postings
    non_job_patterns = [
        '/about', '/teams', '/team/', '/locations', '/location/',
        '/offices', '/benefits', '/culture', '/values', '/mission',
        '/diversity', '/inclusion', '/eeo', '/equal-opportunity',
        '/how-we-hire', '/hiring-process', '/application',
        '/students', '/university', '/graduates', '/internships',
        '/rotational-program', '/leadership-program',
        '/profile', '/settings', '/messages', '/notifications',
        '/saved-jobs', '/account', '/sign-in', '/login', '/register',
        '/newsletter', '/subscribe', '/social', '/follow',
        '/privacy', '/terms', '/policy', '/legal', '/cookie',
        '/contact', '/help', '/support', '/faq',
        '/watch', '/video', '/media', '/youtube',
        '.pdf', '.mp4', '.mov', '.avi'
    ]
    
    if any(pattern in url_lower for pattern in non_job_patterns):
        return False
    
    # URLs that are just category/filter pages (not individual jobs)
    if '/job_categories' in url_lower or '/job-categories' in url_lower:
        return False
    if '/hourly-jobs' in url_lower or '/hourly_jobs' in url_lower:
        return False
    
    # Check if it's a search/results page (these aren't individual job postings)
    # But allow if it has a job ID or specific job indicator
    if '/jobs/results' in url_lower or '/search' in url_lower:
        # These might be search results pages, not individual jobs
        # unless they have a job ID
        has_job_id = any(indicator in url_lower for indicator in 
                        ['jobid=', 'job_id=', 'id=', '/job/', '/jobs/', 'req=', 'requisition'])
        if not has_job_id:
            return False
    
    return True


def is_valid_job_title(title: str) -> bool:
    """Check if extracted title looks like a valid job title"""
    if not title or len(title) < 5 or len(title) > 200:
        return False
    
    # Skip navigation-like titles and UI elements
    nav_keywords = [
        'view all', 'working in', 'log in', 'sign up', 'careers', 
        'read more', 'apply now', 'search', 'filter', 'sort by',
        'back to', 'home', 'about us', 'contact', 'privacy',
        'tips & tricks', 'tips and tricks', 'view locations', 
        'my ideal job', 'ideal job title', 'show more', 'show less',
        'load more', 'see all', 'browse', 'explore', 'share',
        'save job', 'job alert', 'email me', 'subscribe',
        'cookie', 'terms', 'policy', 'sign in', 'register',
        'create account', 'forgot password', 'help', 'support',
        'faq', 'accessibility', 'language', 'location', 'category',
        'department', 'experience', 'salary', 'job type', 'clear all',
        'refine', 'results', 'showing', 'page', 'next', 'previous',
        'first', 'last', 'of', 'total',
        # Navigation/Landing page elements
        'working at', 'life at', 'work at', 'join our', 'meet the team',
        'our teams', 'our culture', 'our values', 'our mission',
        'why work', 'why join', 'learn more', 'find out', 'discover',
        'hiring process', 'how we hire', 'application status',
        'see jobs', 'see our', 'see how', 'take a look', 'watch',
        'skip to', 'newsletter', 'follow us', 'social media',
        'accommodations', 'equal opportunity', 'eeo', 'diversity',
        'inclusion', 'belonging', 'veterans', 'disability',
        'know your rights', 'workplace discrimination',
        # Company sections
        'teams', 'locations', 'offices', 'overview', 'about',
        'benefits', 'culture', 'values', 'mission', 'story',
        'students', 'university', 'graduates', 'internships',
        'rotational program', 'leadership program',
        # Action links
        'create profile', 'build profile', 'account settings',
        'messages', 'notifications', 'saved jobs', 'applications',
        'job categories', 'find your role', 'hourly',
        # UI elements
        'watch on', 'play video', 'see video', 'listen',
        'download', 'print', 'email', 'share this'
    ]
    
    title_lower = title.lower()
    if any(kw in title_lower for kw in nav_keywords):
        return False
    
    # Skip if it starts with common UI patterns
    ui_patterns = [
        r'^\d+\s*my\s+',  # "1My ideal job..."
        r'^view\s+',       # "View locations"
        r'^show\s+',       # "Show all"
        r'^select\s+',     # "Select location"
        r'^choose\s+',     # "Choose category"
        r'^click\s+',      # "Click here"
        r'^\d+\s*result',  # "25 results"
    ]
    
    for pattern in ui_patterns:
        if re.match(pattern, title_lower):
            return False
    
    # Must have at least one letter
    if not re.search(r'[a-zA-Z]', title):
        return False
    
    # Should not be mostly numbers (like "1" or "123")
    if len(re.findall(r'\d', title)) > len(title) / 2:
        return False
    
    # Single word titles are usually not job titles (unless they're role names)
    # Filter out common single-word navigation items
    single_word_nav = [
        'overview', 'teams', 'locations', 'offices', 'benefits',
        'culture', 'values', 'students', 'hourly', 'design',
        'engineering', 'marketing', 'sales', 'finance', 'legal',
        'operations', 'consulting', 'technology', 'business',
        'strategy', 'retail', 'corporate', 'headquarters',
        'messages', 'notifications', 'settings', 'profile',
        'dublin', 'london', 'singapore', 'hyderabad', 'munich',
        'paris', 'tokyo', 'seattle', 'austin', 'boston'
    ]
    
    word_count = len(title.split())
    if word_count == 1 and title_lower in single_word_nav:
        return False
    
    # Check if title is just a city name (usually 1-2 words, starts with capital)
    if word_count <= 2 and title[0].isupper():
        # List of major cities that appear in navigation
        cities = ['new york', 'san francisco', 'los angeles', 'mountain view',
                  'palo alto', 'menlo park', 'redmond', 'seattle', 'austin',
                  'boston', 'chicago', 'denver', 'atlanta', 'dallas', 'houston',
                  'london', 'dublin', 'paris', 'munich', 'berlin', 'amsterdam',
                  'singapore', 'tokyo', 'sydney', 'toronto', 'vancouver',
                  'bangalore', 'hyderabad', 'pune', 'mumbai', 'delhi']
        if title_lower in cities:
            return False
    
    # Titles that are just generic department names (without "role" or "position")
    generic_departments = [
        'engineering & tech', 'engineering and tech',
        'marketing & communications', 'marketing and communications',
        'business strategy', 'technical solutions',
        'data center operations', 'account management',
        'technical program management', 'silicon engineering'
    ]
    if title_lower in generic_departments:
        return False
    
    # Must contain typical job title indicators
    # Job titles usually have role indicators or are specific enough
    job_indicators = [
        'engineer', 'developer', 'manager', 'director', 'analyst',
        'specialist', 'coordinator', 'assistant', 'associate',
        'lead', 'senior', 'junior', 'principal', 'staff',
        'consultant', 'architect', 'designer', 'scientist',
        'technician', 'administrator', 'representative', 'agent',
        'officer', 'supervisor', 'operator', 'instructor',
        'programmer', 'tester', 'qa', 'devops', 'sre',
        'intern', 'co-op', 'apprentice', 'fellow'
    ]
    
    # If it's a longer title (3+ words), it should have at least one job indicator
    # OR have typical job title patterns
    if word_count >= 3:
        has_indicator = any(indicator in title_lower for indicator in job_indicators)
        if not has_indicator:
            # Check if it looks like a job title (e.g., "Retail Sales Associate")
            # or if it's just navigation text
            # Navigation typically has words like "roles", "see", "remote eligible", "more"
            nav_words_in_title = ['roles', 'see jobs', 'remote eligible', 'more', 
                                  'learn more', 'apply now', 'find out']
            if any(nav in title_lower for nav in nav_words_in_title):
                return False
    
    return True


# ============================================================================
# ENHANCED SCRAPING UTILITIES (Cookie Banners, Modals, Search)
# ============================================================================

async def handle_cookie_banner(driver) -> bool:
    """
    Detect and accept cookie consent banners automatically
    
    Returns:
        True if banner was handled, False otherwise
    """
    try:
        logger.info("Checking for cookie consent banner...")
        
        # Common cookie banner selectors
        cookie_banner_selectors = [
            '[id*="cookie" i]',
            '[class*="cookie" i]',
            '[id*="consent" i]',
            '[class*="consent" i]',
            '[role="dialog"]',
            '.modal-cookie',
            '#cookie-banner'
        ]
        
        # Check if banner exists
        banner_found = False
        for selector in cookie_banner_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements and any(elem.is_displayed() for elem in elements):
                    banner_found = True
                    break
            except Exception:
                continue
        
        if not banner_found:
            logger.info("No cookie banner detected")
            return False
        
        logger.info("Cookie banner detected - attempting to accept...")
        
        # Common accept button patterns
        accept_selectors = [
            'button[id*="accept" i]',
            'button[class*="accept" i]',
            '[id*="cookie-accept"]',
            '[class*="cookie-accept"]',
            'button:contains("Accept")',
            'button:contains("Agree")'
        ]
        
        # Try to find and click accept button by selector
        for selector in accept_selectors:
            try:
                buttons = driver.find_elements(By.CSS_SELECTOR, selector)
                for button in buttons:
                    if button.is_displayed() and button.is_enabled():
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
                        await asyncio.sleep(0.5)
                        button.click()
                        logger.info("✓ Cookie banner accepted via selector")
                        await asyncio.sleep(1)
                        return True
            except Exception:
                continue
        
        # Try by text content
        all_buttons = driver.find_elements(By.TAG_NAME, 'button')
        for btn in all_buttons:
            try:
                btn_text = btn.text.lower()
                if any(text in btn_text for text in ['accept', 'agree', 'ok', 'got it', 'allow', 'consent']):
                    if btn.is_displayed() and btn.is_enabled():
                        driver.execute_script("arguments[0].click();", btn)
                        logger.info(f"✓ Cookie banner accepted via text: {btn.text}")
                        await asyncio.sleep(1)
                        return True
            except Exception:
                continue
        
        logger.warning("Cookie banner detected but couldn't find accept button")
        return False
        
    except Exception as e:
        logger.error(f"Error handling cookie banner: {e}")
        return False


async def close_modals_and_overlays(driver) -> int:
    """
    Close any modal dialogs or overlays that might block content
    
    Returns:
        Number of modals closed
    """
    try:
        logger.info("Checking for modal overlays...")
        closed_count = 0
        
        # Modal indicators
        modal_selectors = [
            '[role="dialog"]',
            '.modal',
            '[class*="modal"]',
            '[class*="overlay"]',
            '[class*="popup"]',
            '.dialog'
        ]
        
        modals_found = []
        for selector in modal_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for elem in elements:
                    if elem.is_displayed():
                        modals_found.append(elem)
            except Exception:
                continue
        
        if not modals_found:
            logger.info("No modals detected")
            return 0
        
        logger.info(f"Found {len(modals_found)} potential modal(s)")
        
        # Close button selectors
        close_selectors = [
            '[aria-label*="close" i]',
            'button[class*="close"]',
            'button[class*="dismiss"]',
            '.modal-close',
            '[data-dismiss="modal"]',
            'button.close',
            '[class*="close-button"]'
        ]
        
        # Try to close each modal
        for modal in modals_found[:3]:  # Limit to first 3
            try:
                # Look for close button within modal
                for selector in close_selectors:
                    try:
                        close_btns = modal.find_elements(By.CSS_SELECTOR, selector)
                        for btn in close_btns:
                            if btn.is_displayed() and btn.is_enabled():
                                driver.execute_script("arguments[0].click();", btn)
                                logger.info("✓ Closed modal overlay")
                                await asyncio.sleep(1)
                                closed_count += 1
                                break
                        if closed_count > 0:
                            break
                    except Exception:
                        continue
            except Exception:
                continue
        
        # Try ESC key as fallback
        if len(modals_found) > closed_count:
            try:
                from selenium.webdriver.common.action_chains import ActionChains
                actions = ActionChains(driver)
                actions.send_keys(Keys.ESCAPE).perform()
                await asyncio.sleep(1)
                logger.info("Sent ESC key to close remaining modals")
                closed_count += 1
            except Exception:
                pass
        
        return closed_count
        
    except Exception as e:
        logger.error(f"Error closing modals: {e}")
        return 0


async def enhanced_search_functionality(driver, search_query: str) -> bool:
    """
    Enhanced search with multiple strategies for search-first websites
    
    Returns:
        True if search was performed successfully
    """
    if not search_query:
        return False
    
    try:
        logger.info(f"Looking for search functionality to apply query: '{search_query}'")
        
        # Comprehensive search input selectors
        search_selectors = [
            'input[type="search"]',
            'input[placeholder*="search" i]',
            'input[placeholder*="find" i]',
            'input[placeholder*="job" i]',
            'input[name*="search" i]',
            'input[name*="query" i]',
            'input[name*="keyword" i]',
            'input[id*="search" i]',
            'input[id*="query" i]',
            'input[class*="search" i]',
            '[role="searchbox"]',
            'input[type="text"][placeholder*="search" i]'
        ]
        
        search_input = None
        for selector in search_selectors:
            try:
                inputs = driver.find_elements(By.CSS_SELECTOR, selector)
                for inp in inputs:
                    if inp.is_displayed() and inp.is_enabled():
                        # Check if it's actually a search field
                        placeholder = inp.get_attribute('placeholder') or ''
                        name = inp.get_attribute('name') or ''
                        if 'search' in placeholder.lower() or 'search' in name.lower() or 'job' in placeholder.lower():
                            search_input = inp
                            break
                if search_input:
                    break
            except Exception:
                continue
        
        if not search_input:
            logger.info("No search input found - site may show all jobs by default")
            return False
        
        logger.info("Found search input - entering search query")
        search_input.clear()
        await asyncio.sleep(0.5)
        search_input.send_keys(search_query)
        await asyncio.sleep(1)
        
        # Try multiple submission methods
        search_submitted = False
        
        # Method 1: Press Enter
        try:
            search_input.send_keys(Keys.RETURN)
            logger.info("Submitted search with Enter key")
            await asyncio.sleep(3)
            search_submitted = True
        except Exception:
            pass
        
        # Method 2: Find and click search button
        if not search_submitted:
            search_button_selectors = [
                'button[type="submit"]',
                'button[aria-label*="search" i]',
                'button[class*="search"]',
                'button:has(svg)',  # Icon buttons
                '[class*="search"][class*="button"]',
                '[class*="search-btn"]',
                'button[title*="search" i]'
            ]
            
            for selector in search_button_selectors:
                try:
                    buttons = driver.find_elements(By.CSS_SELECTOR, selector)
                    for btn in buttons:
                        if btn.is_displayed() and btn.is_enabled():
                            btn.click()
                            logger.info("Clicked search button")
                            await asyncio.sleep(3)
                            search_submitted = True
                            break
                    if search_submitted:
                        break
                except Exception:
                    continue
        
        # Method 3: Wait for auto-filter/autocomplete
        if not search_submitted:
            logger.info("Waiting for auto-filter to apply")
            await asyncio.sleep(2)
            search_submitted = True
        
        if search_submitted:
            logger.info("✓ Search completed successfully")
            # Wait for results to load
            await asyncio.sleep(2)
            return True
        
        return False
            
    except Exception as e:
        logger.error(f"Error performing search: {e}")
        return False


async def smart_content_wait(driver, timeout: int = 3):
    """
    Intelligently wait for dynamic content to load
    
    Uses multiple strategies to detect when content is ready
    """
    try:
        # Common job container selectors
        job_container_selectors = [
            '[class*="job"]',
            '[class*="position"]',
            '[class*="opening"]',
            '[class*="career"]',
            '[data-job-id]',
            'article',
            '.posting'
        ]
        
        logger.info("Waiting for job listings to load...")
        
        # Try to wait for any job container to appear (use shorter timeout per selector)
        for selector in job_container_selectors[:5]:
            try:
                WebDriverWait(driver, 2).until(  # Only 2 seconds per selector
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                logger.info(f"Content loaded - found elements with: {selector}")
                return
            except TimeoutException:
                continue
        
        # Fallback: just wait a bit
        logger.info(f"No specific containers found quickly, continuing anyway")
        await asyncio.sleep(1)
        
    except Exception as e:
        logger.warning(f"Error in smart content wait: {e}")


async def random_delay(min_seconds: float = 1.0, max_seconds: float = 3.0):
    """Add random delay for rate limiting and anti-detection"""
    delay = random.uniform(min_seconds, max_seconds)
    await asyncio.sleep(delay)


def retry_on_failure(max_attempts: int = 3, delay_seconds: float = 2.0):
    """
    Decorator to retry async functions on failure
    
    Usage:
        @retry_on_failure(max_attempts=3, delay_seconds=2.0)
        async def my_function():
            # code that might fail
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_attempts:
                        logger.warning(f"Attempt {attempt} failed for {func.__name__}: {e}. Retrying in {delay_seconds}s...")
                        await asyncio.sleep(delay_seconds)
                    else:
                        logger.error(f"All {max_attempts} attempts failed for {func.__name__}")
            raise last_exception
        return wrapper
    return decorator


# ============================================================================
# API DETECTION & SCRAPING
# ============================================================================

async def intercept_api_calls(driver, url: str, wait_time: int = 5) -> List[str]:
    """Intercept XHR/Fetch API calls to find job data endpoints"""
    print("Attempting to intercept API calls...")
    
    # Inject script to capture XHR/Fetch requests
    intercept_script = """
    window.capturedRequests = [];
    
    // Intercept XMLHttpRequest
    (function() {
        var originalOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function(method, url) {
            this._url = url;
            return originalOpen.apply(this, arguments);
        };
        
        var originalSend = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.send = function() {
            this.addEventListener('load', function() {
                if (this._url && (this._url.includes('job') || this._url.includes('position') || 
                    this._url.includes('career') || this._url.includes('opening'))) {
                    window.capturedRequests.push({
                        url: this._url,
                        method: 'XHR',
                        response: this.responseText
                    });
                }
            });
            return originalSend.apply(this, arguments);
        };
    })();
    
    // Intercept Fetch
    (function() {
        var originalFetch = window.fetch;
        window.fetch = function() {
            return originalFetch.apply(this, arguments).then(function(response) {
                var url = arguments[0];
                if (url && (url.includes('job') || url.includes('position') || 
                    url.includes('career') || url.includes('opening'))) {
                    response.clone().text().then(function(text) {
                        window.capturedRequests.push({
                            url: url,
                            method: 'Fetch',
                            response: text
                        });
                    });
                }
                return response;
            });
        };
    })();
    """
    
    try:
        driver.execute_script(intercept_script)
        driver.get(url)
        await asyncio.sleep(wait_time)
        
        # Scroll to trigger more API calls
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
        await asyncio.sleep(2)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        await asyncio.sleep(2)
        
        # Get captured requests
        captured = driver.execute_script("return window.capturedRequests;")
        
        api_urls = []
        if captured:
            print(f"Captured {len(captured)} API requests")
            for req in captured:
                api_urls.append(req['url'])
                print(f"  Found API: {req['url'][:100]}")
        
        return api_urls
        
    except Exception as e:
        print(f"Error intercepting API calls: {e}")
        return []


async def scrape_api_endpoint(session: requests.Session, api_url: str, company_name: str) -> List[Job]:
    """Scrape jobs from an API endpoint"""
    jobs = []
    
    try:
        print(f"Fetching API endpoint: {api_url}")
        response = session.get(
            api_url,
            headers={'User-Agent': get_random_user_agent()},
            timeout=10
        )
        
        if response.status_code != 200:
            print(f"API returned status {response.status_code}")
            return jobs
        
        data = response.json()
        
        # Try to find job listings in common JSON structures
        job_arrays = []
        
        # Direct array
        if isinstance(data, list):
            job_arrays.append(data)
        
        # Common nested structures
        for key in ['jobs', 'positions', 'openings', 'postings', 'results', 'data', 'items']:
            if isinstance(data, dict) and key in data:
                if isinstance(data[key], list):
                    job_arrays.append(data[key])
        
        # Extract jobs from found arrays
        for job_array in job_arrays:
            for item in job_array:
                if not isinstance(item, dict):
                    continue
                
                # Extract fields from JSON
                title = None
                for title_key in ['title', 'name', 'position', 'jobTitle', 'job_title', 'positionTitle']:
                    if title_key in item:
                        title = item[title_key]
                        break
                
                if not title or not is_valid_job_title(str(title)):
                    continue
                
                # Extract other fields
                location = None
                for loc_key in ['location', 'city', 'office', 'workLocation', 'locations']:
                    if loc_key in item:
                        location = item[loc_key]
                        if isinstance(location, list):
                            location = ', '.join(str(l) for l in location)
                        break
                
                description = None
                for desc_key in ['description', 'summary', 'details', 'content', 'descriptionPlain']:
                    if desc_key in item:
                        description = item[desc_key]
                        break
                
                url = None
                for url_key in ['url', 'link', 'applyUrl', 'apply_url', 'absoluteUrl', 'hostedUrl']:
                    if url_key in item:
                        url = item[url_key]
                        break
                
                employment_type = None
                for type_key in ['employmentType', 'type', 'jobType', 'commitment']:
                    if type_key in item:
                        employment_type = item[type_key]
                        break
                
                salary = None
                for salary_key in ['salary', 'compensation', 'salaryRange', 'pay']:
                    if salary_key in item:
                        salary = item[salary_key]
                        if isinstance(salary, dict):
                            salary = f"{salary.get('min', '')} - {salary.get('max', '')}"
                        break
                
                posted_date = None
                for date_key in ['postedDate', 'createdAt', 'publishedAt', 'datePosted']:
                    if date_key in item:
                        posted_date = item[date_key]
                        break
                
                job = Job(
                    title=clean_text(str(title)),
                    company=company_name,
                    location=clean_text(str(location)) if location else None,
                    description=clean_text(str(description)[:500]) if description else None,
                    url=url,
                    employment_type=employment_type,
                    salary=str(salary) if salary else None,
                    posted_date=posted_date
                )
                jobs.append(job)
                print(f"  ✓ API Job: {job.title}")
        
        print(f"Extracted {len(jobs)} jobs from API")
        
    except Exception as e:
        print(f"Error scraping API endpoint: {e}")
    
    return jobs


# ============================================================================
# JOB EXTRACTION FROM HTML ELEMENTS
# ============================================================================

def extract_job_from_element(element, base_url: str, company_name: str) -> Optional[Job]:
    """Extract comprehensive job information from HTML element"""
    try:
        soup = BeautifulSoup(element.get_attribute('outerHTML'), 'html.parser')
        text = soup.get_text(separator=' ', strip=True)
        
        # Extract title
        title = None
        title_selectors = [
            ('h1', None), ('h2', None), ('h3', None), ('h4', None),
            ('[class*="title"]', None), ('[class*="job"]', None),
            ('[class*="position"]', None), ('a', None)
        ]
        
        for tag, attr in title_selectors:
            elem = soup.select_one(tag)
            if elem:
                title_text = elem.get_text(strip=True)
                if is_valid_job_title(title_text):
                    title = title_text
                    break
        
        if not title:
            return None
        
        # Extract URL
        job_url = None
        link = soup.find('a', href=True)
        if link:
            href = link.get('href')
            job_url = urljoin(base_url, href)
            
            # Validate URL - if it's not a valid job URL, skip this element
            if not is_valid_job_url(job_url):
                return None
        
        # Extract location
        location = extract_text_field(text, LOCATION_PATTERNS)
        
        # Extract employment type
        employment_type = extract_dict_field(text, EMPLOYMENT_TYPE_PATTERNS)
        
        # Extract remote type
        remote_type = extract_dict_field(text, REMOTE_TYPE_PATTERNS)
        
        # Extract salary
        salary = None
        for pattern in SALARY_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                salary = match.group(0)
                break
        
        # Extract description
        description = clean_text(text)
        if len(description) > 500:
            description = description[:500] + "..."
        
        # Extract posting date
        posted_date = None
        for pattern in DATE_PATTERNS:
            match = re.search(pattern, text)
            if match:
                posted_date = match.group(0)
                break
        
        # Extract requirements
        requirements = None
        req_indicators = ['requirements', 'qualifications', 'you have', 'what you bring']
        for indicator in req_indicators:
            if indicator in text.lower():
                # Extract section after indicator
                idx = text.lower().find(indicator)
                req_text = text[idx:idx+500]
                requirements = clean_text(req_text)
                break
        
        return Job(
            title=clean_text(title),
            company=company_name,
            location=location,
            description=description,
            url=job_url,
            remote_type=remote_type,
            employment_type=employment_type,
            salary=salary,
            posted_date=posted_date,
            requirements=requirements
        )
        
    except Exception as e:
        print(f"Error extracting job: {e}")
        return None


# ============================================================================
# PAGINATION & INFINITE SCROLL HANDLING
# ============================================================================

async def handle_pagination(driver, max_pages: int = 5) -> None:
    """Handle pagination by clicking next buttons"""
    for page_num in range(max_pages):
        try:
            # Common pagination selectors
            next_selectors = [
                'a[aria-label*="next"]',
                'button[aria-label*="next"]',
                '.pagination .next',
                '.pagination a:last-child',
                '[class*="next-page"]',
                '[class*="pagination-next"]',
            ]
            
            next_button = None
            for selector in next_selectors:
                try:
                    next_button = WebDriverWait(driver, 2).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )
                    break
                except TimeoutException:
                    continue
            
            if next_button:
                print(f"Navigating to page {page_num + 2}")
                driver.execute_script("arguments[0].scrollIntoView(true);", next_button)
                await asyncio.sleep(0.5)
                next_button.click()
                await asyncio.sleep(2)
            else:
                print("No more pages found")
                break
                
        except Exception as e:
            print(f"Pagination ended: {e}")
            break


async def detect_and_clear_no_results(driver) -> bool:
    """Detect if we're on a 'no results' page and try to clear filters"""
    try:
        page_text = driver.page_source.lower()
        
        # Check for common "no results" messages
        no_results_patterns = [
            'no jobs found',
            'no results',
            'could not find any jobs',
            'no matching jobs',
            'no positions available',
            '0 jobs',
            'no open positions',
            'no current openings',
            'no opportunities',
        ]
        
        has_no_results = any(pattern in page_text for pattern in no_results_patterns)
        
        if has_no_results:
            print("⚠️  Detected 'no results' page - attempting to clear filters...")
            
            # Try to find and click "Clear All" or "Show All Jobs" buttons
            clear_button_texts = [
                'clear all', 'clear filters', 'reset filters', 
                'show all jobs', 'view all jobs', 'see all jobs',
                'remove filters', 'reset'
            ]
            
            buttons = driver.find_elements(By.TAG_NAME, 'button')
            links = driver.find_elements(By.TAG_NAME, 'a')
            
            for element in buttons + links:
                try:
                    element_text = element.text.lower()
                    if any(text in element_text for text in clear_button_texts):
                        if element.is_displayed() and element.is_enabled():
                            print(f"  Found button: '{element.text}' - clicking...")
                            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
                            await asyncio.sleep(0.5)
                            element.click()
                            await asyncio.sleep(3)
                            print("  ✓ Clicked - page should reload with all jobs")
                            return True
                except Exception:
                    continue
            
            # Try to find search input and clear it
            try:
                search_inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="search"], input[type="text"]')
                for inp in search_inputs:
                    if inp.is_displayed() and inp.get_attribute('value'):
                        print("  Found search input with value - clearing...")
                        inp.clear()
                        inp.send_keys(Keys.RETURN)
                        await asyncio.sleep(3)
                        print("  ✓ Cleared search input")
                        return True
            except Exception:
                pass
            
            print("  Could not find clear/reset buttons")
            return False
        
        return False
        
    except Exception as e:
        print(f"Error in no results detection: {e}")
        return False


async def try_search_filter(driver, search_query: str) -> bool:
    """Try to use search/filter inputs on the career page"""
    try:
        print(f"Looking for search filter to apply query: '{search_query}'")
        
        # Common search input selectors
        search_selectors = [
            'input[type="search"]',
            'input[placeholder*="search" i]',
            'input[placeholder*="find" i]',
            'input[name*="search" i]',
            'input[name*="query" i]',
            'input[id*="search" i]',
            'input[class*="search" i]',
            '[role="searchbox"]',
            'input[type="text"]',  # Fallback
        ]
        
        search_input = None
        for selector in search_selectors:
            try:
                inputs = driver.find_elements(By.CSS_SELECTOR, selector)
                for inp in inputs:
                    if inp.is_displayed() and inp.is_enabled():
                        search_input = inp
                        break
                if search_input:
                    break
            except Exception:
                continue
        
        if search_input:
            print("Found search input - entering search query")
            search_input.clear()
            search_input.send_keys(search_query)
            await asyncio.sleep(1)
            
            # Try to submit search
            # Strategy 1: Press Enter
            try:
                search_input.send_keys(Keys.RETURN)
                print("Submitted search with Enter key")
                await asyncio.sleep(3)
                return True
            except Exception:
                pass
            
            # Strategy 2: Find and click search button
            search_button_selectors = [
                'button[type="submit"]',
                'button[aria-label*="search" i]',
                'button:has(svg)',  # Icon buttons
                '[class*="search"][class*="button"]',
                '[class*="search-btn"]',
            ]
            
            for selector in search_button_selectors:
                try:
                    buttons = driver.find_elements(By.CSS_SELECTOR, selector)
                    for btn in buttons:
                        if btn.is_displayed() and btn.is_enabled():
                            btn.click()
                            print("Clicked search button")
                            await asyncio.sleep(3)
                            return True
                except Exception:
                    continue
            
            # If no explicit submit, just wait for auto-filter
            print("Waiting for auto-filter to apply")
            await asyncio.sleep(2)
            return True
        else:
            print("No search filter found on page")
            return False
            
    except Exception as e:
        print(f"Error trying search filter: {e}")
        return False


async def handle_infinite_scroll(driver, max_scrolls: int = 10) -> None:
    """Handle infinite scroll by scrolling down"""
    last_height = driver.execute_script("return document.body.scrollHeight")
    
    for scroll_num in range(max_scrolls):
        # Scroll down
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        await asyncio.sleep(2)
        
        # Calculate new scroll height
        new_height = driver.execute_script("return document.body.scrollHeight")
        
        if new_height == last_height:
            print(f"Reached end of infinite scroll after {scroll_num + 1} scrolls")
            break
        
        last_height = new_height
        print(f"Infinite scroll {scroll_num + 1}/{max_scrolls}")


async def handle_load_more_button(driver, max_clicks: int = 10) -> None:
    """Handle 'Load More' buttons"""
    for click_num in range(max_clicks):
        try:
            # Find load more buttons using multiple strategies
            load_more = None
            
            # Strategy 1: Find by text content
            try:
                buttons = driver.find_elements(By.TAG_NAME, 'button')
                for btn in buttons:
                    btn_text = btn.text.lower()
                    if any(text in btn_text for text in ['load more', 'show more', 'see more', 'view more', 'load']):
                        if btn.is_displayed() and btn.is_enabled():
                            load_more = btn
                            break
            except Exception:
                pass
            
            # Strategy 2: Find by common class patterns
            if not load_more:
                load_more_selectors = [
                    '[class*="load-more"]',
                    '[class*="loadmore"]',
                    '[class*="show-more"]',
                    '[class*="showmore"]',
                    '[id*="load-more"]',
                    '[id*="loadmore"]',
                ]
                
                for selector in load_more_selectors:
                    try:
                        found = driver.find_elements(By.CSS_SELECTOR, selector)
                        for elem in found:
                            if elem.is_displayed() and elem.is_enabled():
                                load_more = elem
                                break
                        if load_more:
                            break
                    except Exception:
                        continue
            
            if load_more:
                print(f"Clicking 'Load More' button (click {click_num + 1})")
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", load_more)
                await asyncio.sleep(0.5)
                driver.execute_script("arguments[0].click();", load_more)
                await asyncio.sleep(3)
            else:
                print("No more 'Load More' button found")
                break
                
        except Exception as e:
            print(f"'Load More' handling ended: {e}")
            break


# ============================================================================
# MAIN SCRAPING FUNCTIONS
# ============================================================================

async def scrape_with_selenium(
    url: str,
    company_name: str,
    max_results: int,
    search_query: Optional[str] = None,
    use_undetected: bool = False,
    use_enhanced_features: bool = False  # NEW: Enable enhanced scraping features
) -> List[Job]:
    """Scrape jobs using Selenium/undetected-chromedriver"""
    jobs = []
    driver = None
    
    try:
        # Setup Chrome options
        chrome_options = Options()
        chrome_options.add_argument('--headless=new')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument(f'user-agent={get_random_user_agent()}')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        # Use undetected-chromedriver for anti-bot protection
        if use_undetected:
            print("Using undetected-chromedriver for anti-bot protection")
            chrome_path = get_chrome_executable_path()
            driver = uc.Chrome(options=chrome_options, browser_executable_path=chrome_path)
        else:
            service = Service(ChromeDriverManager().install())
            chrome_path = get_chrome_executable_path()
            if chrome_path:
                chrome_options.binary_location = chrome_path
            driver = webdriver.Chrome(service=service, options=chrome_options)
        
        print(f"Loading career page: {url}")
        
        # Try to intercept API calls first (with reduced wait time for speed)
        print("Checking for API endpoints...")
        try:
            api_urls = await intercept_api_calls(driver, url, wait_time=3)  # Reduced from 5 to 3 seconds
        except Exception as e:
            print(f"API interception failed (continuing with HTML scraping): {e}")
            driver.get(url)
            await asyncio.sleep(2)
            api_urls = []
        
        if api_urls:
            session = create_session_with_retries()
            for api_url in api_urls[:3]:  # Try first 3 API endpoints
                api_jobs = await scrape_api_endpoint(session, api_url, company_name)
                jobs.extend(api_jobs)
                if len(jobs) >= max_results:
                    break
        
        # If API scraping didn't work or didn't get enough jobs, try HTML scraping
        if len(jobs) < max_results:
            print("Attempting HTML-based scraping...")
            
            # === ENHANCED SCRAPING WORKFLOW (Optional) ===
            
            if use_enhanced_features:
                print("Using enhanced scraping features...")
                
                # Step 1: Handle cookie consent banners
                try:
                    await handle_cookie_banner(driver)
                except Exception as e:
                    print(f"Cookie banner handling error (non-fatal): {e}")
                
                # Step 2: Close any modal overlays
                try:
                    await close_modals_and_overlays(driver)
                except Exception as e:
                    print(f"Modal closing error (non-fatal): {e}")
            
            # Standard workflow continues...
            # Wait a bit for page to load
            print("Waiting for page to load...")
            await asyncio.sleep(2)
            
            # Check if we're on a "no results" page and try to clear filters
            cleared_filters = await detect_and_clear_no_results(driver)
            if cleared_filters:
                await asyncio.sleep(2)  # Wait for page to reload
            
            # Step 5: Try to click "View All Jobs" or "See All Openings" buttons
            try:
                view_all_texts = ['view all', 'see all', 'all jobs', 'all openings', 'browse jobs', 'job openings']
                buttons = driver.find_elements(By.TAG_NAME, 'button')
                links = driver.find_elements(By.TAG_NAME, 'a')
                
                for element in buttons + links:
                    try:
                        element_text = element.text.lower()
                        # Check if it's a "view all jobs" type button (but not "view all locations" etc.)
                        if any(text in element_text for text in view_all_texts):
                            if 'job' in element_text or 'opening' in element_text or 'position' in element_text or 'career' in element_text:
                                if element.is_displayed() and element.is_enabled():
                                    print(f"  Found 'View All' button: '{element.text}' - clicking...")
                                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
                                    await asyncio.sleep(0.5)
                                    element.click()
                                    await asyncio.sleep(3)
                                    print("  ✓ Clicked 'View All' button")
                                    break
                    except Exception:
                        continue
            except Exception:
                pass
            
            # Try to interact with search filter if search_query is provided
            if search_query:
                if use_enhanced_features:
                    try:
                        search_success = await enhanced_search_functionality(driver, search_query)
                        if not search_success:
                            # Fallback to old method
                            await try_search_filter(driver, search_query)
                    except Exception as e:
                        print(f"Enhanced search error (trying fallback): {e}")
                        await try_search_filter(driver, search_query)
                else:
                    await try_search_filter(driver, search_query)
            
            # Check for iframes and switch to the one with job content
            iframe_switched = False
            iframes = driver.find_elements(By.TAG_NAME, 'iframe')
            if iframes:
                print(f"Found {len(iframes)} iframe(s)")
                for i, iframe in enumerate(iframes):
                    try:
                        driver.switch_to.frame(iframe)
                        await asyncio.sleep(1)
                        iframe_source = driver.page_source
                        if any(kw in iframe_source.lower() for kw in ['job', 'position', 'career', 'opening', 'vacancy']):
                            print(f"Found job content in iframe #{i+1} - staying in this context")
                            iframe_switched = True
                            break
                        else:
                            driver.switch_to.default_content()
                    except Exception as e:
                        print(f"Error checking iframe #{i+1}: {e}")
                        driver.switch_to.default_content()
            
            # Handle different loading patterns
            print("Waiting for initial content to load...")
            await asyncio.sleep(5)  # Increased from 3 to 5 seconds for JS-heavy pages
            
            # Scroll to trigger lazy-loaded content
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
            await asyncio.sleep(2)
            
            # Try load more button first
            await handle_load_more_button(driver, max_clicks=5)
            
            # Try infinite scroll
            await handle_infinite_scroll(driver, max_scrolls=5)
            
            # Try pagination
            await handle_pagination(driver, max_pages=3)
            
            # Wait a bit more for any delayed content
            await asyncio.sleep(3)  # Increased from 2 to 3 seconds
            
            print("\nExtracting job listings from page...")
            
            # Extract jobs from page with comprehensive selectors
            job_selectors = [
                # Board-specific selectors FIRST (most accurate)
                '[data-ui="job"]', 'li[data-ui="job"]',  # Workable
                '.BambooHR-ATS-Jobs-Item',  # BambooHR
                '.opening', '.postings-group',  # Greenhouse, Lever
                '.posting',  # Lever
                '[data-automation-id="jobTitle"]',  # Workday
                '.jv-job-list-item',  # Jobvite
                '.opening-job',  # SmartRecruiters
                '[class*="JobsList"]',  # Ashby
                
                # Generic job containers
                '[class*="job-list"]', '[class*="job-item"]', '[class*="job-card"]',
                '[class*="job-posting"]', '[class*="jobPosting"]', '[class*="JobPosting"]',
                '[id*="job-list"]', '[id*="job-item"]', '[id*="joblist"]',
                
                # Position/Opening containers
                '[class*="position"]', '[class*="opening"]', '[class*="vacancy"]',
                '[class*="career"]', '[class*="role"]',
                
                # More generic selectors
                '.job-listing', '.job', '.position',
                '[data-job-id]', '[data-posting-id]', '[data-position-id]',
                
                # Common HTML structures
                'article[class*="job"]', 'li[class*="job"]', 'div[class*="job"]',
                'article[class*="position"]', 'li[class*="position"]', 'div[class*="position"]',
                'article[class*="career"]', 'li[class*="opening"]', 'div[class*="posting"]',
                
                # List items that might contain jobs
                'ul[class*="job"] > li', 'ul[class*="position"] > li',
                'ul[class*="opening"] > li', 'ul[class*="career"] > li',
                
                # Table rows (some sites use tables)
                'table[class*="job"] tr', 'table[class*="career"] tr',
                'tr[class*="job"]', 'tr[class*="position"]',
                
                # Broader fallback - any link with job-like text
                'a[href*="job"]', 'a[href*="position"]', 'a[href*="career"]', 'a[href*="opening"]',
            ]
            
            elements = []
            found_with_selector = {}
            
            for selector in job_selectors:
                try:
                    found = driver.find_elements(By.CSS_SELECTOR, selector)
                    if found:
                        elements.extend(found)
                        found_with_selector[selector] = len(found)
                except Exception as e:
                    pass
            
            # Show which selectors found elements
            if found_with_selector:
                print(f"\nSelectors that found elements:")
                for sel, count in list(found_with_selector.items())[:5]:  # Show top 5
                    print(f"  • {sel}: {count} elements")
            
            # Remove duplicates by position
            unique_elements = []
            seen_positions = set()
            for elem in elements:
                try:
                    pos = elem.location
                    pos_key = (pos['x'], pos['y'])
                    if pos_key not in seen_positions:
                        seen_positions.add(pos_key)
                        unique_elements.append(elem)
                except Exception:
                    continue
            
            print(f"Found {len(unique_elements)} unique job elements")
            
            # If no elements found, save page source for debugging
            if len(unique_elements) == 0:
                print("\n⚠️  No job elements found. Saving page source for debugging...")
                page_source = driver.page_source
                debug_file = "/tmp/career_page_debug.html"
                try:
                    with open(debug_file, "w", encoding="utf-8") as f:
                        f.write(page_source)
                    print(f"Page source saved to: {debug_file}")
                    
                    # Try to extract any visible text to see what's on the page
                    soup = BeautifulSoup(page_source, 'html.parser')
                    
                    # Remove scripts and styles
                    for script in soup(["script", "style"]):
                        script.decompose()
                    
                    visible_text = soup.get_text(separator=' ', strip=True)
                    
                    # Check if this is a marketing/landing page vs job listings page
                    landing_page_indicators = [
                        'work at', 'life at', 'join our team', 'why work', 
                        'explore careers', 'learn about', 'our culture',
                        'meet the team', 'see our teams', 'our values'
                    ]
                    is_landing_page = any(indicator in visible_text.lower()[:1000] for indicator in landing_page_indicators)
                    
                    if is_landing_page:
                        print(f"  ⚠️  This appears to be a LANDING/MARKETING page, not a job listings page")
                        print(f"  💡 Try looking for a 'Search Jobs' or 'View All Jobs' link to get actual listings")
                        
                        # Look for job search links
                        search_links = soup.find_all('a', href=True)
                        potential_job_urls = []
                        for link in search_links[:50]:
                            href = link.get('href', '').lower()
                            link_text = link.get_text(strip=True).lower()
                            if any(kw in href or kw in link_text for kw in ['search', 'listings', 'openings', 'browse', 'all jobs']):
                                full_url = urljoin(url, link.get('href'))
                                potential_job_urls.append(full_url)
                        
                        if potential_job_urls:
                            print(f"  💡 Found {len(potential_job_urls)} potential job listing pages:")
                            for purl in potential_job_urls[:3]:
                                print(f"     • {purl}")
                    
                    # Check if there's job-related content at all
                    job_keywords = ['job', 'position', 'career', 'opening', 'vacancy', 'role', 'hiring']
                    found_keywords = [kw for kw in job_keywords if kw in visible_text.lower()]
                    
                    if found_keywords:
                        print(f"  Page contains job-related keywords: {', '.join(found_keywords)}")
                        print(f"  Visible text sample (first 500 chars): {visible_text[:500]}")
                    else:
                        print("  Page doesn't seem to contain job listings")
                        
                except Exception as e:
                    print(f"  Could not save debug file: {e}")
            
            # Extract jobs - prioritize elements with links to job detail pages
            seen_titles = set()
            
            # First pass: Extract jobs from elements that have links
            elements_with_links = []
            elements_without_links = []
            
            for element in unique_elements:
                try:
                    has_link = element.find_elements(By.TAG_NAME, 'a')
                    if has_link:
                        elements_with_links.append(element)
                    else:
                        elements_without_links.append(element)
                except Exception:
                    elements_without_links.append(element)
            
            print(f"  Elements with links: {len(elements_with_links)}, without links: {len(elements_without_links)}")
            
            # Process elements with links first (more likely to be real jobs)
            for element in elements_with_links + elements_without_links:
                if len(jobs) >= max_results:
                    break
                
                job = extract_job_from_element(element, url, company_name)
                if job:
                    if job.title in seen_titles:
                        continue
                    # Double-check title is valid (additional validation)
                    if not is_valid_job_title(job.title):
                        print(f"  ✗ Skipped (invalid title): {job.title}")
                        continue
                    # Triple-check URL is valid
                    if job.url and not is_valid_job_url(job.url):
                        print(f"  ✗ Skipped (invalid URL): {job.title}")
                        print(f"     URL: {job.url[:80] if len(job.url) > 80 else job.url}")
                        continue
                    # All checks passed
                    seen_titles.add(job.title)
                    jobs.append(job)
                    print(f"  ✓ Extracted: {job.title}")
            
            # Switch back to default content if we were in an iframe
            if iframe_switched:
                try:
                    driver.switch_to.default_content()
                    print("Switched back to default content")
                except Exception:
                    pass
        
        print(f"\nTotal jobs extracted: {len(jobs)}")
        
    except Exception as e:
        print(f"Error in Selenium scraping: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        if driver:
            driver.quit()
    
    return jobs


async def scrape_generic_career_page(
    url: str,
    max_results: int = 20,
    search_query: Optional[str] = None,
    use_undetected: bool = False
) -> List[Job]:
    """
    Universal scraper for any career page
    
    Args:
        url: Career page URL
        max_results: Maximum number of jobs to return
        search_query: Optional search term to filter jobs
        use_undetected: Use undetected-chromedriver for anti-bot protection
        
    Returns:
        List of Job objects
    """
    company_name = extract_company_name_from_url(url)
    print(f"\n{'='*80}")
    print(f"Starting scrape for: {company_name}")
    print(f"URL: {url}")
    print(f"Search query: {search_query if search_query else 'None (will return all jobs)'}")
    print(f"{'='*80}\n")
    
    # Detect job board
    job_board = detect_job_board(url)
    if job_board:
        print(f"Detected job board: {job_board['name']}")
    
    # Scrape jobs
    jobs = await scrape_with_selenium(
        url=url,
        company_name=company_name,
        max_results=max_results * 2,  # Get extra to filter
        search_query=search_query,
        use_undetected=use_undetected
    )
    
    # Filter by search query
    if search_query and jobs:
        print(f"\nFiltering {len(jobs)} jobs by search query: '{search_query}'")
        search_lower = search_query.lower()
        filtered_jobs = []
        
        for job in jobs:
            searchable_text = ' '.join(filter(None, [
                job.title,
                job.description,
                job.location,
                job.employment_type,
                job.remote_type,
                job.requirements
            ])).lower()
            
            if search_lower in searchable_text:
                filtered_jobs.append(job)
                print(f"  ✓ Matched: {job.title}")
        
        print(f"Found {len(filtered_jobs)} jobs matching '{search_query}'")
        jobs = filtered_jobs
    elif not search_query and jobs:
        print(f"\nNo search query provided - returning all {len(jobs)} jobs found")
    
    # Return limited results
    final_jobs = jobs[:max_results]
    
    print(f"\n{'='*80}")
    print(f"Scraping complete: {len(final_jobs)} jobs extracted")
    print(f"{'='*80}\n")
    
    return final_jobs


async def scrape_multiple_career_pages(
    urls: List[str],
    max_results_per_url: int = 20,
    search_query: Optional[str] = None,
    use_undetected: bool = False,
    total_max_results: Optional[int] = None
) -> List[Job]:
    """
    Scrape multiple career page URLs
    
    Args:
        urls: List of career page URLs to scrape
        max_results_per_url: Maximum number of jobs to extract per URL
        search_query: Optional search term to filter jobs
        use_undetected: Use undetected-chromedriver for anti-bot protection
        total_max_results: Optional total maximum results across all URLs
        
    Returns:
        Combined list of Job objects from all URLs
    """
    all_jobs = []
    successful_scrapes = 0
    failed_scrapes = 0
    
    print(f"\n{'#'*80}")
    print(f"MULTI-URL SCRAPING SESSION")
    print(f"Total URLs to scrape: {len(urls)}")
    print(f"Max results per URL: {max_results_per_url}")
    print(f"Search query: {search_query if search_query else 'None (will return all jobs)'}")
    if total_max_results:
        print(f"Total max results: {total_max_results}")
    print(f"{'#'*80}\n")
    
    for index, url in enumerate(urls, 1):
        try:
            print(f"\n{'>'*80}")
            print(f"Processing URL {index}/{len(urls)}")
            print(f"{'>'*80}")
            
            # Check if we've reached total max results
            if total_max_results and len(all_jobs) >= total_max_results:
                print(f"\n✓ Reached total max results ({total_max_results}). Stopping.")
                break
            
            # Calculate remaining slots if total_max_results is set
            remaining_slots = max_results_per_url
            if total_max_results:
                remaining_slots = min(max_results_per_url, total_max_results - len(all_jobs))
            
            # Scrape this URL
            jobs = await scrape_generic_career_page(
                url=url,
                max_results=remaining_slots,
                search_query=search_query,
                use_undetected=use_undetected
            )
            
            if jobs:
                all_jobs.extend(jobs)
                successful_scrapes += 1
                print(f"\n✓ URL {index}: Successfully extracted {len(jobs)} jobs")
            else:
                print(f"\n⚠️  URL {index}: No jobs found")
            
            print(f"📊 Progress: {len(all_jobs)} total jobs collected so far")
            
            # Add a small delay between URLs to be respectful
            if index < len(urls):
                print(f"\nWaiting 3 seconds before next URL...")
                await asyncio.sleep(3)
                
        except Exception as e:
            failed_scrapes += 1
            print(f"\n❌ URL {index} failed: {e}")
            print(f"   URL: {url}")
            # Continue to next URL even if this one fails
            continue
    
    # Final summary
    print(f"\n{'#'*80}")
    print(f"MULTI-URL SCRAPING COMPLETE")
    print(f"{'#'*80}")
    print(f"Total URLs processed: {len(urls)}")
    print(f"Successful scrapes: {successful_scrapes}")
    print(f"Failed scrapes: {failed_scrapes}")
    print(f"Total jobs collected: {len(all_jobs)}")
    print(f"{'#'*80}\n")
    
    # Remove duplicates based on title and company
    unique_jobs = []
    seen = set()
    
    for job in all_jobs:
        job_key = (job.title.lower(), job.company.lower())
        if job_key not in seen:
            seen.add(job_key)
            unique_jobs.append(job)
    
    if len(unique_jobs) < len(all_jobs):
        print(f"Removed {len(all_jobs) - len(unique_jobs)} duplicate jobs")
        print(f"Final unique jobs: {len(unique_jobs)}\n")
    
    return unique_jobs


# ============================================================================
# FALLBACK STRATEGIES
# ============================================================================

async def scrape_with_requests_fallback(url: str, company_name: str, max_results: int) -> List[Job]:
    """Fallback: Simple requests-based scraping for static HTML"""
    print("Attempting requests-based fallback scraping...")
    jobs = []
    
    try:
        session = create_session_with_retries()
        response = session.get(
            url,
            headers={'User-Agent': get_random_user_agent()},
            timeout=10
        )
        
        if response.status_code != 200:
            print(f"Request failed with status {response.status_code}")
            return jobs
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Remove navigation, header, footer
        for tag in soup(['nav', 'header', 'footer', 'script', 'style']):
            tag.decompose()
        
        # Find all links that might be job postings
        links = soup.find_all('a', href=True)
        
        for link in links[:max_results * 2]:
            text = link.get_text(strip=True)
            if is_valid_job_title(text):
                href = link.get('href')
                job_url = urljoin(url, href)
                
                # Get parent element for more context
                parent = link.find_parent(['div', 'li', 'article', 'section'])
                context = parent.get_text(separator=' ', strip=True) if parent else text
                
                job = Job(
                    title=clean_text(text),
                    company=company_name,
                    description=clean_text(context[:500]),
                    url=job_url
                )
                jobs.append(job)
                print(f"  ✓ Fallback extracted: {job.title}")
                
                if len(jobs) >= max_results:
                    break
        
        print(f"Fallback extraction complete: {len(jobs)} jobs")
        
    except Exception as e:
        print(f"Error in fallback scraping: {e}")
    
    return jobs


async def scrape_with_retry_strategies(
    url: str,
    max_results: int = 20,
    search_query: Optional[str] = None
) -> List[Job]:
    """
    Scrape with multiple fallback strategies
    
    Strategy order:
    1. Selenium with API interception
    2. Undetected-chromedriver (for anti-bot sites)
    3. Simple requests-based scraping
    """
    company_name = extract_company_name_from_url(url)
    
    # Strategy 1: Normal Selenium with API interception
    print("\n[Strategy 1] Attempting Selenium with API interception...")
    jobs = await scrape_generic_career_page(
        url=url,
        max_results=max_results,
        search_query=search_query,
        use_undetected=False
    )
    
    if jobs:
        return jobs
    
    # Strategy 2: Undetected-chromedriver for anti-bot protection
    print("\n[Strategy 2] Attempting undetected-chromedriver...")
    jobs = await scrape_generic_career_page(
        url=url,
        max_results=max_results,
        search_query=search_query,
        use_undetected=True
    )
    
    if jobs:
        return jobs
    
    # Strategy 3: Simple requests fallback
    print("\n[Strategy 3] Attempting requests-based fallback...")
    jobs = await scrape_with_requests_fallback(
        url=url,
        company_name=company_name,
        max_results=max_results
    )
    
    # Filter by search query if provided
    if search_query and jobs:
        print(f"\nFiltering {len(jobs)} jobs by search query: '{search_query}'")
        search_lower = search_query.lower()
        jobs = [
            job for job in jobs
            if search_lower in ' '.join(filter(None, [
                job.title, job.description, job.location
            ])).lower()
        ]
        print(f"Found {len(jobs)} jobs matching '{search_query}'")
    elif not search_query and jobs:
        print(f"\nNo search query provided - returning all {len(jobs)} jobs found")
    
    return jobs[:max_results]


# ============================================================================
# PROXY SUPPORT (Optional)
# ============================================================================

class ProxyRotator:
    """Simple proxy rotator - add your proxy list here"""
    
    def __init__(self, proxy_list: Optional[List[str]] = None):
        self.proxies = proxy_list or []
        self.current_index = 0
    
    def get_next_proxy(self) -> Optional[Dict[str, str]]:
        """Get next proxy in rotation"""
        if not self.proxies:
            return None
        
        proxy = self.proxies[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.proxies)
        
        return {
            'http': proxy,
            'https': proxy
        }
    
    def add_to_chrome_options(self, options: Options) -> Options:
        """Add proxy to Chrome options"""
        proxy = self.get_next_proxy()
        if proxy:
            proxy_str = proxy['http'].replace('http://', '')
            options.add_argument(f'--proxy-server={proxy_str}')
        return options


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

async def main():
    """Example usage"""
    
    # Example 1: Scrape a single career page
    jobs = await scrape_generic_career_page(
        url="https://www.greenhouse.io/careers",
        max_results=10,
        search_query="software engineer"
    )
    
    print("\n" + "="*80)
    print("SINGLE URL RESULTS")
    print("="*80)
    for i, job in enumerate(jobs, 1):
        print(f"\n{i}. {job.title}")
        print(f"   Company: {job.company}")
        print(f"   Location: {job.location or 'Not specified'}")
        print(f"   Type: {job.employment_type or 'Not specified'}")
        print(f"   Remote: {job.remote_type or 'Not specified'}")
        print(f"   Salary: {job.salary or 'Not specified'}")
        print(f"   URL: {job.url or 'Not specified'}")
        if job.description:
            print(f"   Description: {job.description[:100]}...")
    
    # Example 2: Scrape multiple career pages
    career_urls = [
        "https://www.greenhouse.io/careers",
        "https://jobs.lever.co/anthropic",
        "https://careers.google.com/jobs/results/",
    ]
    
    multi_jobs = await scrape_multiple_career_pages(
        urls=career_urls,
        max_results_per_url=10,
        search_query="engineer",
        total_max_results=25  # Optional: limit total results across all URLs
    )
    
    print("\n" + "="*80)
    print("MULTIPLE URLs RESULTS")
    print("="*80)
    for i, job in enumerate(multi_jobs, 1):
        print(f"\n{i}. {job.title}")
        print(f"   Company: {job.company}")
        print(f"   Location: {job.location or 'Not specified'}")
    
    # Example 3: Scrape with retry strategies (single URL)
    jobs_with_retry = await scrape_with_retry_strategies(
        url="https://jobs.lever.co/anthropic",
        max_results=5,
        search_query="research"
    )
    
    print(f"\n\nFound {len(jobs_with_retry)} jobs with retry strategies")


if __name__ == "__main__":
    asyncio.run(main())