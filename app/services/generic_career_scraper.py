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
        '.pdf', '.mp4', '.mov', '.avi',
        '/actionworks', '/action-works',  # Patagonia Action Works
        '/jointalentcommunity', '/join-talent-community',  # Talent community pages
        '/our-brands-group',  # Company group pages
        # Product/category URLs
        '/fly-fishing', '/fly-rods', '/fly-line', '/fly-',
        '/womens', '/mens', '/kids', '/clothing', '/equipment',
        '/products', '/shop', '/store', '/catalog',
        # Content/blog URLs
        '/stories', '/blog', '/news', '/articles', '/dog-stories',
        '/wingshooting', '/content',
        # Customer service URLs
        '/customer-care', '/help', '/faq', '/contact-us',
        '/order-status', '/shipping', '/returns', '/exchanges',
        '/repairs', '/gift-card', '/rewards', '/feedback',
    ]
    
    if any(pattern in url_lower for pattern in non_job_patterns):
        return False
    
    # URLs that are just category/filter pages (not individual jobs)
    # Patterns like /c/marketing-communications-jobs, /c/product-jobs
    if '/job_categories' in url_lower or '/job-categories' in url_lower:
        return False
    if '/hourly-jobs' in url_lower or '/hourly_jobs' in url_lower:
        return False
    
    # Category pages with /c/ pattern (like Patagonia)
    if '/c/' in url_lower and '-jobs' in url_lower:
        return False
    
    # Location pages (like /pages/careers for country-specific pages)
    if '/pages/careers' in url_lower and url_lower.count('/') >= 4:
        # This might be a country-specific careers landing page
        # Allow it if it looks like a job listing page, otherwise filter
        if '/jobs/' not in url_lower and '/job/' not in url_lower:
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
    
    # Allow URLs with numeric job IDs (common patterns like /careers/40, /jobs/123, etc.)
    # Pattern: /careers/123 or /jobs/123 or /openings/123
    job_id_patterns = [
        r'/careers/\d+', r'/jobs/\d+', r'/job/\d+', r'/openings/\d+',
        r'/positions/\d+', r'/position/\d+', r'/postings/\d+', r'/posting/\d+',
        r'/vacancies/\d+', r'/vacancy/\d+', r'/opportunities/\d+', r'/opportunity/\d+'
    ]
    for pattern in job_id_patterns:
        if re.search(pattern, url_lower):
            # This is clearly a job with a numeric ID - always valid
            return True
    
    # Also check for paths that end with a number after /careers/ or /jobs/
    # This catches cases like /careers/40 or /jobs/123
    if re.search(r'/(careers|jobs|job|openings|positions|postings)/\d+', url_lower):
        return True
    
    # Allow URLs with /job/ or /jobs/ followed by location/job-name patterns
    # Examples: /job/Portland-Oregon-United-States-of-A, /jobs/software-engineer
    # These are clearly individual job postings
    if re.search(r'/(job|jobs)/[^/]+', url_lower):
        # Check that it's not just /job or /jobs (must have something after)
        # And it's not a category page
        if not url_lower.endswith('/job') and not url_lower.endswith('/jobs') and \
           not url_lower.endswith('/job/') and not url_lower.endswith('/jobs/'):
            # This looks like a specific job posting URL
            return True
    
    # Filter out URLs that are just the main careers page (unless they have job-specific paths)
    if url_lower.endswith('/careers') or url_lower.endswith('/careers/'):
        # This is the main careers page, not a specific job
        return False
    
    # Filter out URLs that end with common non-job patterns
    non_job_endings = [
        '/careers', '/jobs', '/openings', '/positions',
        '/jointalentcommunity', '/actionworks'
    ]
    for ending in non_job_endings:
        if url_lower.endswith(ending) or url_lower.endswith(ending + '/'):
            # Unless it's a job-specific path like /careers/12345
            path_parts = url.rstrip('/').split('/')
            if len(path_parts) <= 2:  # Just domain + /careers or /jobs
                return False
    
    return True


def is_valid_job_title(title: str) -> bool:
    """Check if extracted title looks like a valid job title"""
    if not title or len(title) < 5 or len(title) > 200:
        return False
    
    title_lower = title.lower().strip()
    
    # Filter out titles with job counts (category pages)
    # Patterns like "0Jobs", "(1Job )", "Marketing & Communications 0 Jobs"
    job_count_patterns = [
        r'^\d+\s*jobs?$',  # "0Jobs", "1Job", "5 Jobs"
        r'\(\d+\s*jobs?\)',  # "(1Job )", "(5 Jobs )"
        r'\d+\s*jobs?$',  # "Marketing 0 Jobs"
        r'.*\s+\(\d+\s*jobs?\)',  # "Marketing & Communications (0 Jobs )"
        r'.*\s+\d+\s*jobs?$',  # "Family Services 5 Jobs"
    ]
    
    for pattern in job_count_patterns:
        if re.search(pattern, title_lower):
            return False
    
    # Filter out titles that are just numbers or job counts
    if re.match(r'^(\d+\s*jobs?|\(?\d+\s*jobs?\)?)$', title_lower):
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
        'download', 'print', 'email', 'share this',
        # Action page titles
        'take action', 'join talent community', 'we would love to get to know you',
        'patagonia action works',
        # Marketing/CTA text
        'love where you work', 'apply today', 'thanks for visiting',
        'thanks for visiting our career site', 'sign up here',
        # Product categories (common in e-commerce sites)
        'fly rods', 'fly line', 'womens', 'mens', 'kids', 'accessories',
        'clothing', 'equipment', 'gear', 'products', 'shop', 'store',
        # Content/blog sections
        'wingshooting', 'dog stories', 'stories', 'blog', 'news', 'articles',
        # Support/customer service
        'customer care', 'help', 'faq', 'contact us', 'order status',
        'shipping information', 'returns', 'exchanges', 'repairs',
        'gift card', 'rewards', 'feedback',
        # Benefits/info sections
        'protecting your future', 'catalog', 'request a catalog',
        'generous health benefit', '401k', 'paid parental leave',
        'life & disability insurance', 'conservation leadership'
    ]
    
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
        r'^we would',      # "we would love to get to know you"
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
    # Filter out common single-word navigation items and locations
    single_word_nav = [
        'overview', 'teams', 'locations', 'offices', 'benefits',
        'culture', 'values', 'students', 'hourly', 'design',
        'engineering', 'marketing', 'sales', 'finance', 'legal',
        'operations', 'consulting', 'technology', 'business',
        'strategy', 'retail', 'corporate', 'headquarters',
        'messages', 'notifications', 'settings', 'profile',
        # Common location names that appear in navigation
        'japan', 'australia', 'dublin', 'london', 'singapore', 
        'hyderabad', 'munich', 'paris', 'tokyo', 'seattle', 
        'austin', 'boston', 'portland', 'carlsbad',
        # Department/category names
        'product', 'supply chain', 'executive management & legal',
        'information technology', 'sales & e-commerce',
        'people & culture', 'finance & accounting',
        'family services', 'environmental activism',
        'justice, equity & antiracism', 'justice, equity',
        # Product categories (single word)
        'womens', 'mens', 'kids', 'accessories', 'clothing',
        'equipment', 'gear', 'products', 'shop', 'store', 'catalog'
    ]
    
    word_count = len(title.split())
    if word_count == 1 and title_lower in single_word_nav:
        return False
    
    # Check if title is just a city/country name (usually 1-2 words, starts with capital)
    if word_count <= 2 and title[0].isupper():
        # List of major cities and countries that appear in navigation
        locations = ['new york', 'san francisco', 'los angeles', 'mountain view',
                  'palo alto', 'menlo park', 'redmond', 'seattle', 'austin',
                  'boston', 'chicago', 'denver', 'atlanta', 'dallas', 'houston',
                  'london', 'dublin', 'paris', 'munich', 'berlin', 'amsterdam',
                  'singapore', 'tokyo', 'sydney', 'toronto', 'vancouver',
                  'bangalore', 'hyderabad', 'pune', 'mumbai', 'delhi',
                  'japan', 'australia', 'portland', 'carlsbad']
        if title_lower in locations:
            return False
    
    # Titles that are just generic department names (without "role" or "position")
    generic_departments = [
        'engineering & tech', 'engineering and tech',
        'marketing & communications', 'marketing and communications',
        'business strategy', 'technical solutions',
        'data center operations', 'account management',
        'technical program management', 'silicon engineering',
        'family services', 'environmental activism',
        'justice, equity & antiracism', 'justice, equity',
        'executive management & legal', 'supply chain',
        'sales & e-commerce', 'people & culture',
        'finance & accounting', 'information technology',
        'product'
    ]
    if title_lower in generic_departments:
        return False
    
    # Filter out titles that are just department/category names
    if title_lower in ['product', 'supply chain', 'sales & e-commerce']:
        return False
    
    # Must contain typical job title indicators OR be a specific enough role
    job_indicators = [
        'engineer', 'developer', 'manager', 'director', 'analyst',
        'specialist', 'coordinator', 'assistant', 'associate',
        'lead', 'senior', 'junior', 'principal', 'staff',
        'consultant', 'architect', 'designer', 'scientist',
        'technician', 'administrator', 'representative', 'agent',
        'officer', 'supervisor', 'operator', 'instructor',
        'programmer', 'tester', 'qa', 'devops', 'sre',
        'intern', 'co-op', 'apprentice', 'fellow', 'executive',
        'vice president', 'vp', 'head of', 'chief'
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


def is_valid_job_entry(job: Job, debug: bool = False) -> bool:
    """
    Comprehensive validation of a job entry to filter out non-job entries
    
    SIMPLIFIED - Accepts jobs with valid titles and URLs that look like job postings.
    Only rejects obvious non-jobs.
    
    Args:
        job: Job object to validate
        debug: If True, print detailed debug information about why validation fails
    """
    # Basic title check - must exist and have reasonable length
    if not job.title or len(job.title.strip()) < 3 or len(job.title.strip()) > 200:
        if debug:
            print(f"       ❌ Failed: Invalid title length")
        return False
    
    title_lower = job.title.lower().strip()
    
    # Check URL - if it looks like a job URL, accept it immediately
    has_job_url = False
    if job.url:
        url_lower = job.url.lower()
        # Check if URL contains job-related patterns (case-insensitive, more lenient)
        url_has_job_pattern = any(pattern in url_lower for pattern in [
            '/job/', '/jobs/', '/careers/', '/career/', '/openings/', '/opening/',
            '/positions/', '/position/', '/postings/', '/posting/',
            '/vacancies/', '/vacancy/', '/opportunities/', '/opportunity/',
            '/recruitment/', '/apply/', '/req/', '/mdf/',  # ADP/Workday patterns
            '/job', '/jobs', '/careers', '/career', '/recruitment'  # Also check without trailing slash
        ])
        
        # Check if URL has a numeric ID (like /careers/40)
        url_has_numeric_id = re.search(r'/(careers|jobs|job|openings|positions)/\d+', url_lower)
        
        # Check if URL has location/job-name pattern (like /job/Portland-Oregon)
        # More lenient - any path after /job/ or /jobs/
        url_has_job_path = re.search(r'/(job|jobs)/[^/?]+', url_lower)
        
        # Also check for URLs ending with job ID or location
        url_ends_with_job_pattern = bool(re.search(r'/(job|jobs|careers?|openings?|positions?)/[^/?]+/?$', url_lower))
        
        if url_has_job_pattern or url_has_numeric_id or url_has_job_path or url_ends_with_job_pattern:
            has_job_url = True
            if debug:
                print(f"       ✅ URL looks like job URL: '{job.url[:80]}'")
        elif debug:
            print(f"       ⚠️  URL doesn't match job patterns: '{job.url[:80]}'")
    
    # SIMPLIFIED VALIDATION: If job has ANY URL, accept it unless obviously not a job
    # Be very lenient - most jobs with URLs are valid
    if job.url:
        # Only reject obvious non-jobs - very permissive approach
        # Check for job count in title (like "0Jobs", "5 Jobs")
        has_job_count = bool(re.search(r'^\d+\s*jobs?$|\(?\d+\s*jobs?\)?$', title_lower))
        if has_job_count:
            if debug:
                print(f"       ❌ Failed: Title is a job count")
            return False
        
        # Reject very obvious non-job titles only
        obvious_non_jobs = ['view all', 'all jobs']  # Minimal list
        if title_lower in obvious_non_jobs:
            if debug:
                print(f"       ❌ Failed: Title is obvious navigation")
            return False
        
        # If job has URL and title doesn't match obvious non-job patterns, ACCEPT IT
        # Be very lenient - trust that if there's a URL, it's probably a real job
        if debug:
            print(f"       ✅ Accepted: Has URL and valid title (lenient validation)")
        return True
    
    # If no job URL, be lenient but check title more carefully
    # Accept if title looks like a job title (at least 2 words, not obviously a non-job)
    obvious_non_jobs = [
        'fly rods', 'fly line', 'womens', 'mens', 'wingshooting', 
        'dog stories', 'customer care', 'catalog', 'protecting your future',
        'love where you work', 'apply today', 'thanks for visiting',
        'view all', 'all jobs', 'current openings'  # Navigation elements
    ]
    
    # Check for job count in title
    has_job_count = bool(re.search(r'^\d+\s*jobs?$|\(?\d+\s*jobs?\)?$', title_lower))
    
    if title_lower in obvious_non_jobs or has_job_count:
        if debug:
            print(f"       ❌ Failed: Title is obvious non-job or job count")
        return False
    
    # If title has at least 2 words and doesn't match non-job patterns, accept it
    word_count = len(title_lower.split())
    if word_count >= 2:
        if debug:
            print(f"       ✅ Accepted: Valid title (lenient validation)")
        return True
    
    # Default: reject only single-word titles that don't look like jobs
    if debug:
        print(f"       ❌ Failed: Title too short or doesn't meet criteria")
    return False
    
    # Check if description is identical to title (indicates it's not a real job listing)
    # Only check this if we have a description - jobs with valid URLs might not have descriptions yet
    if job.description and job.title:
        desc_clean = clean_text(job.description).strip()
        title_clean = clean_text(job.title).strip()
        if desc_clean.lower() == title_clean.lower():
            # Description matches title exactly - this is likely just a link, not a job listing
            return False
    
    # Check for category/department page indicators in description
    if job.description:
        desc_lower = job.description.lower()
        category_indicators = [
            ' jobs', 'job)', '(jobs)', '0 jobs', 'no jobs',
            'view all', 'see all jobs', 'browse jobs',
            'category', 'department', 'team'
        ]
        if any(indicator in desc_lower for indicator in category_indicators):
            # Check if it's describing a category page
            if any(pattern in desc_lower for pattern in ['0 jobs', ' jobs)', ' jobs ']):
                # Likely a category page with job count
                return False
        
        # Check for product category descriptions
        product_indicators = [
            'view all', 'fly fishing', 'fly rods', 'fly line',
            'freshwater', 'saltwater', 'shirts', 'jackets', 'pants',
            'shorts', 'hats', 'gloves', 'mittens', 'clays',
            'catalog', 'request a catalog', 'shop', 'store',
            'products', 'equipment', 'gear', 'accessories'
        ]
        # If description contains product indicators and looks like a product listing
        if any(indicator in desc_lower for indicator in product_indicators):
            # Check if it's a product listing (has "View All" followed by product names)
            if 'view all' in desc_lower and len(desc_lower.split()) < 30:
                # Short description with "View All" = likely product category
                return False
        
        # Check for content/blog section descriptions
        content_indicators = [
            'dog stories', 'wingshooting', 'stories', 'blog',
            'view all dog stories', 'view all stories'
        ]
        if any(indicator in desc_lower for indicator in content_indicators):
            # Likely a content/blog section, not a job
            return False
        
        # Check for customer service descriptions
        customer_service_indicators = [
            'help / faq', 'contact us', 'order status', 'shipping information',
            'size & fit', 'returns & exchanges', 'repairs', 'gift card balance',
            'rewards', 'visa', 'international orders', 'corporate sales',
            'promotional exclusions', 'give us feedback'
        ]
        if any(indicator in desc_lower for indicator in customer_service_indicators):
            # Likely a customer service page, not a job
            return False
        
        # Check for marketing/CTA descriptions
        marketing_indicators = [
            'thanks for visiting', 'sign up here', 'enjoy 15% off',
            'love where you work', 'inclusive culture', 'remote and flexible',
            'generous associate discount', 'company holidays', 'pto',
            'rod loaner program', 'this is just a placeholder copy',
            'we live to develop and share our equipment'
        ]
        if any(indicator in desc_lower for indicator in marketing_indicators):
            # Check if it's just marketing text without actual job details
            job_detail_indicators = ['responsibilities', 'requirements', 'qualifications',
                                   'experience', 'skills', 'bachelor', 'degree',
                                   'years of experience', 'salary', 'compensation']
            if not any(indicator in desc_lower for indicator in job_detail_indicators):
                # Marketing text without job details = not a real job
                return False
    
    # Filter out entries with suspicious patterns in title
    title_lower = job.title.lower()
    
    # Filter titles that are clearly category pages
    category_patterns = [
        r'.*\s+\(\d+\s*jobs?\)',  # "Marketing (5 Jobs )"
        r'.*\s+\d+\s*jobs?$',  # "Marketing 0 Jobs"
        r'^\d+\s*jobs?$',  # "0Jobs"
    ]
    for pattern in category_patterns:
        if re.match(pattern, title_lower):
            return False
    
    # Filter out location-only entries (like "Japan", "Australia")
    # These should have been caught by is_valid_job_title, but double-check
    common_locations = [
        'japan', 'australia', 'united states', 'united kingdom',
        'canada', 'germany', 'france', 'spain', 'italy'
    ]
    if title_lower in common_locations and not any(indicator in title_lower for indicator in 
                                                   ['engineer', 'manager', 'developer', 'analyst', 'specialist']):
        return False
    
    # Filter out product category titles (even if they passed title validation)
    product_category_titles = [
        'fly rods', 'fly line', 'womens', 'mens', 'kids',
        'wingshooting', 'dog stories', 'customer care',
        'catalog', 'request a catalog', 'protecting your future'
    ]
    if title_lower in product_category_titles:
        if debug:
            print(f"       ❌ Failed: Title is a product category")
        return False
    
    # Filter out marketing/CTA titles
    marketing_titles = [
        'love where you work', 'apply today',
        'thanks for visiting our career site', 'thanks for visiting'
    ]
    if title_lower in marketing_titles:
        return False
    
    # Filter "Career Opportunities" if it's clearly a section header
    # (description contains multiple job titles or section-like text)
    if title_lower == 'career opportunities' and job.description:
        desc_lower = job.description.lower()
        # If description contains multiple job titles or section indicators
        if ('retail sales associate' in desc_lower and 
            desc_lower.count('retail') > 1) or 'view all' in desc_lower:
            # This is a section header listing multiple jobs, not an individual job
            return False
    
    # Filter out titles that are clearly not job titles
    # Check if title looks like a product name or category
    job_keywords = ['associate', 'manager', 'director', 'engineer', 'developer',
                   'analyst', 'specialist', 'coordinator', 'assistant',
                   'lead', 'senior', 'junior', 'principal', 'staff',
                   'consultant', 'architect', 'designer', 'scientist',
                   'technician', 'administrator', 'representative', 'agent',
                   'officer', 'supervisor', 'operator', 'instructor',
                   'sales', 'retail', 'store', 'warehouse', 'technician']
    
    has_job_keyword = any(job_word in title_lower for job_word in job_keywords)
    if not has_job_keyword:
        # If title doesn't contain job-related words, check if it's a product/category
        product_keywords = ['fly', 'rods', 'line', 'womens', 'mens', 'kids',
                           'wingshooting', 'stories', 'catalog', 'care']
        if any(keyword in title_lower for keyword in product_keywords):
            # Likely a product/category, not a job
            if debug:
                print(f"       ❌ Failed: Title doesn't contain job keywords and matches product keywords")
            return False
        elif debug:
            print(f"       ⚠️  Warning: Title doesn't contain common job keywords, but allowing it")
    
    if debug:
        print(f"       ✅ Validation passed for '{job.title}'")
    
    return True


def filter_invalid_jobs(jobs: List[Job]) -> List[Job]:
    """
    Post-process and filter out invalid job entries
    
    Returns only valid job entries that pass all validation checks
    """
    valid_jobs = []
    filtered_count = 0
    
    for job in jobs:
        if is_valid_job_entry(job):
            valid_jobs.append(job)
        else:
            filtered_count += 1
            print(f"  ✗ Filtered invalid entry: {job.title}")
            if job.url:
                print(f"     URL: {job.url[:80] if len(job.url) > 80 else job.url}")
    
    if filtered_count > 0:
        print(f"\n  Filtered out {filtered_count} invalid job entries")
    
    return valid_jobs


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


def safe_get_page_source(driver, timeout: int = 30) -> Optional[str]:
    """
    Safely get page source with timeout protection
    
    Args:
        driver: Selenium WebDriver instance
        timeout: Maximum time to wait in seconds
        
    Returns:
        Page source as string or None if timeout/error
    """
    try:
        # Use JavaScript to get page source as it's more reliable
        page_source = driver.execute_script("return document.documentElement.outerHTML;")
        return page_source
    except Exception as e:
        logger.warning(f"Failed to get page source: {e}")
        try:
            # Fallback to regular page_source property
            return driver.page_source
        except Exception as e2:
            logger.error(f"Failed to get page source (fallback): {e2}")
            return None


def check_driver_alive(driver) -> bool:
    """
    Check if the driver is still responsive
    
    Returns:
        True if driver is alive and responsive, False otherwise
    """
    try:
        # Simple test to see if driver responds
        driver.execute_script("return true;")
        return True
    except Exception as e:
        logger.error(f"Driver is unresponsive: {e}")
        return False


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
        try:
            driver.get(url)
        except TimeoutException:
            print("  Page load timeout - stopping page load and continuing")
            driver.execute_script("window.stop();")
        
        # OPTIMIZED: Reduced wait times significantly
        await asyncio.sleep(1.5)  # Reduced from wait_time (3s) to 1.5s
        
        # Quick scroll to trigger API calls (if any)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        await asyncio.sleep(1)  # Reduced from 4s total (2+2) to 1s
        
        # Get captured requests
        captured = driver.execute_script("return window.capturedRequests;")
        
        api_urls = []
        if captured:
            print(f"Captured {len(captured)} API requests")
            for req in captured:
                api_urls.append(req['url'])
                print(f"  Found API: {req['url'][:100]}")
        else:
            print("No API endpoints found - will use HTML scraping")
        
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

def extract_job_from_element_optimized(element, element_data: dict, base_url: str, company_name: str) -> Optional[Job]:
    """
    OPTIMIZED: Extract comprehensive job information using pre-fetched element data
    This avoids expensive WebDriver API calls
    """
    try:
        # Use pre-fetched outerHTML and text
        outer_html = element_data.get('outerHTML', '')
        text = element_data.get('text', '')
        
        if not outer_html or not text:
            return None
        
        soup = BeautifulSoup(outer_html, 'html.parser')
        
        # Extract title
        title = None
        title_selectors_ordered = [
            '[data-automation-id="jobTitle"]',
            '[data-testid*="job"]',
            '[class*="job-title"]', '[class*="jobTitle"]',
            'h1', 'h2', 'h3', 'h4', 'h5',
            'a[href*="job"]', 'a[href*="position"]',
            '[class*="title"]',
            'a'
        ]
        
        for selector in title_selectors_ordered:
            try:
                elem = soup.select_one(selector)
                if elem:
                    title_text = elem.get_text(strip=True)
                    if title_text and 3 <= len(title_text) <= 200:
                        if not title or len(title_text) > len(title or ''):
                            title = title_text
            except Exception:
                continue
        
        # Fallback title extraction
        if not title:
            all_text = soup.get_text(separator='\n', strip=True)
            lines = [line.strip() for line in all_text.split('\n') if line.strip()]
            for line in lines:
                if 5 <= len(line) <= 200:
                    if not line.isupper() or len(line.split()) >= 2:
                        title = line
                        break
        
        if not title or len(title.strip()) < 3:
            return None
        
        # Extract description
        description = clean_text(text)
        if len(description) > 500:
            description = description[:500] + "..."
        
        # Extract URL from pre-fetched links
        job_url = None
        pre_fetched_links = element_data.get('links', [])
        
        for href in pre_fetched_links:
            if not href:
                continue
            full_url = urljoin(base_url, href)
            url_lower = full_url.lower()
            
            looks_like_job_url = any(pattern in url_lower for pattern in [
                '/job/', '/jobs/', '/careers/', '/career/', '/openings/',
                '/positions/', '/posting/', '/recruitment/', '/apply/', '/req/'
            ])
            
            if looks_like_job_url:
                job_url = full_url
                break
        
        # Use first link if no job-specific URL found
        if not job_url and pre_fetched_links:
            job_url = urljoin(base_url, pre_fetched_links[0])
        
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
        return None


def extract_job_from_element(element, base_url: str, company_name: str) -> Optional[Job]:
    """Extract comprehensive job information from HTML element"""
    try:
        soup = BeautifulSoup(element.get_attribute('outerHTML'), 'html.parser')
        text = soup.get_text(separator=' ', strip=True)
        
        # Also get text directly from Selenium element (might be more reliable)
        try:
            element_text = element.text.strip()
            if element_text and len(element_text) > len(text):
                text = element_text  # Use Selenium text if it's more complete
        except:
            pass  # Fall back to BeautifulSoup text
        
        # Extract title - be more lenient, extract first and validate later
        title = None
        
        # Priority order: specific selectors first, then generic
        title_selectors_ordered = [
            # Workday/ADP specific
            '[data-automation-id="jobTitle"]',
            '[data-testid*="job"]',
            '[data-testid*="title"]',
            # Specific title classes
            '[class*="job-title"]', '[class*="jobTitle"]', '[class*="JobTitle"]',
            '[class*="position-title"]', '[class*="opening-title"]',
            # Headings
            'h1', 'h2', 'h3', 'h4', 'h5',
            # Links (often contain job titles)
            'a[href*="job"]', 'a[href*="position"]', 'a[href*="career"]',
            # Generic title/position classes
            '[class*="title"]', '[class*="job"]', '[class*="position"]',
            # Any link
            'a'
        ]
        
        # Try ordered selectors first
        for selector in title_selectors_ordered:
            try:
                elem = soup.select_one(selector)
                if elem:
                    title_text = elem.get_text(strip=True)
                    if title_text and len(title_text) >= 3 and len(title_text) <= 200:
                        # Accept if it looks reasonable - validate later
                        if not title or len(title_text) > len(title or ''):
                            title = title_text
            except Exception:
                continue
        
        # If still no title, try getting text from the whole element
        if not title:
            # Get all text from element and find the longest line that looks like a title
            all_text = soup.get_text(separator='\n', strip=True)
            lines = [line.strip() for line in all_text.split('\n') if line.strip()]
            for line in lines:
                if len(line) >= 5 and len(line) <= 200:
                    # Check if line looks like it could be a title (not all caps short words)
                    if not line.isupper() or len(line.split()) >= 2:
                        title = line
                        break
        
        # If still no title, use the first meaningful text from the element
        if not title:
            # Get first line of text that's not too short or too long
            text_lines = text.strip().split('\n') if text else []
            for line in text_lines:
                line = line.strip()
                if line and len(line) >= 5 and len(line) <= 200:
                    # Skip lines that are clearly not titles
                    if not any(nav_word in line.lower() for nav_word in ['view all', 'current openings', 'search', 'filter']):
                        title = line
                        break
        
        # Last resort: use first 100 characters of element text
        if not title and text:
            first_part = text.strip()[:100].strip()
            if first_part and len(first_part) >= 5:
                # Clean it up - take first sentence or first part before common separators
                for separator in ['\n', '. ', ' - ', ' | ']:
                    if separator in first_part:
                        title = first_part.split(separator)[0].strip()
                        break
                if not title:
                    title = first_part
        
        # Final check - must have a title
        if not title or len(title.strip()) < 3:
            return None
        
        # Extract description first (before URL validation)
        description = clean_text(text)
        if len(description) > 500:
            description = description[:500] + "..."
        
        # Extract URL - look for links more thoroughly
        job_url = None
        
        # Try multiple strategies to find the job URL
        # Strategy 1: Find all links and pick the one that looks like a job URL
        all_links = soup.find_all('a', href=True)
        for link in all_links:
            href = link.get('href')
            if not href:
                continue
            full_url = urljoin(base_url, href)
            url_lower = full_url.lower()
            
            # Check if this looks like a job URL
            looks_like_job_url = any(pattern in url_lower for pattern in [
                '/job/', '/jobs/', '/careers/', '/career/', '/openings/', '/opening/',
                '/positions/', '/position/', '/postings/', '/posting/',
                '/vacancies/', '/vacancy/', '/opportunities/', '/opportunity/',
                '/recruitment/', '/apply/', '/req/'  # ADP/Workday patterns
            ]) or re.search(r'/(careers|jobs|job|openings|positions|recruitment)/', url_lower)
            
            if looks_like_job_url:
                job_url = full_url
                break
        
        # Strategy 2: If no job-like URL found, use the first link
        if not job_url and all_links:
            first_link = all_links[0]
            href = first_link.get('href')
            if href:
                job_url = urljoin(base_url, href)
        
        # Strategy 3: Use Selenium to find links directly from the element
        if not job_url:
            try:
                # Find all links within this element using Selenium
                links_in_element = element.find_elements(By.TAG_NAME, 'a')
                for link_elem in links_in_element:
                    try:
                        href = link_elem.get_attribute('href')
                        if href:
                            full_url = urljoin(base_url, href) if not href.startswith('http') else href
                            url_lower = full_url.lower()
                            
                            # Check if this looks like a job URL
                            looks_like_job_url = any(pattern in url_lower for pattern in [
                                '/job/', '/jobs/', '/careers/', '/career/', '/openings/', '/opening/',
                                '/positions/', '/position/', '/postings/', '/posting/',
                                '/vacancies/', '/vacancy/', '/opportunities/', '/opportunity/',
                                '/recruitment/', '/apply/', '/req/', '/mdf/'  # ADP/Workday patterns
                            ]) or re.search(r'/(careers|jobs|job|openings|positions|recruitment|mdf)/', url_lower)
                            
                            if looks_like_job_url:
                                job_url = full_url
                                break
                            
                            # If no job-like URL but this is the first link, use it anyway
                            if not job_url:
                                job_url = full_url
                    except:
                        continue
            except:
                pass
        
        # Be very lenient with URLs - if it looks like a job URL, keep it
        # Don't reject URLs unless they're clearly not job-related
        if job_url:
            url_lower = job_url.lower()
            looks_like_job_url = any(pattern in url_lower for pattern in [
                '/job/', '/jobs/', '/careers/', '/career/', '/openings/', '/opening/',
                '/positions/', '/position/', '/postings/', '/posting/',
                '/vacancies/', '/vacancy/', '/opportunities/', '/opportunity/',
                '/recruitment/', '/apply/', '/req/', '/mdf/'  # ADP/Workday patterns
            ]) or re.search(r'/(careers|jobs|job|openings|positions)/\d+', url_lower) or \
                re.search(r'/(job|jobs)/[^/?]+', url_lower)
            
            if not looks_like_job_url and not is_valid_job_url(job_url):
                # URL doesn't look like a job URL at all
                # For expandable sections, jobs might not have direct links
                # Only reject if we also don't have a description
                if not description or len(description.strip()) < 20:
                    # No job-like URL and no description - likely not a job listing
                    return None
                job_url = None  # Set to None but continue with extraction
            # Otherwise, keep the URL even if validation failed
        
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

async def detect_pagination(driver) -> Dict[str, Any]:
    """
    Detect pagination on the page and return information about it
    
    Returns:
        Dict with pagination info: {
            'type': 'numbered' | 'next_only' | 'none',
            'current_page': int,
            'total_pages': int or None,
            'page_numbers': List[int] or None,
            'has_next': bool,
            'has_prev': bool
        }
    """
    pagination_info = {
        'type': 'none',
        'current_page': 1,
        'total_pages': None,
        'page_numbers': None,
        'has_next': False,
        'has_prev': False
    }
    
    try:
        # Look for pagination container
        pagination_selectors = [
            '.pagination', '[class*="pagination"]', '[class*="paging"]',
            '[class*="page-navigation"]', '[role="navigation"]',
            'nav[aria-label*="pagination"]', 'nav[aria-label*="Pagination"]'
        ]
        
        pagination_container = None
        for selector in pagination_selectors:
            try:
                containers = driver.find_elements(By.CSS_SELECTOR, selector)
                for container in containers:
                    container_text = container.text.lower()
                    if any(keyword in container_text for keyword in ['page', 'next', 'prev', '1', '2', '3']):
                        pagination_container = container
                        break
                if pagination_container:
                    break
            except Exception:
                continue
        
        if not pagination_container:
            return pagination_info
        
        # Get all pagination links and buttons
        pagination_links = pagination_container.find_elements(By.TAG_NAME, 'a')
        pagination_buttons = pagination_container.find_elements(By.TAG_NAME, 'button')
        all_elements = pagination_links + pagination_buttons
        
        # Detect numbered pagination
        page_numbers = []
        current_page = 1
        has_next = False
        has_prev = False
        
        for elem in all_elements:
            try:
                if not elem.is_displayed():
                    continue
                
                elem_text = elem.text.strip()
                elem_aria = elem.get_attribute('aria-label') or ''
                
                # Check for page numbers (digits only, typically 1-999)
                if re.match(r'^\d+$', elem_text):
                    page_num = int(elem_text)
                    if 1 <= page_num <= 999:  # Reasonable page number range
                        page_numbers.append(page_num)
                        
                        # Check if it's the current/active page
                        classes = elem.get_attribute('class') or ''
                        aria_current = elem.get_attribute('aria-current')
                        if ('active' in classes.lower() or 'current' in classes.lower() or
                            'selected' in classes.lower() or aria_current == 'page' or
                            'bold' in classes.lower() or elem.tag_name == 'strong'):
                            current_page = page_num
                
                # Check for next button
                if ('next' in elem_text.lower() or '>' in elem_text or
                    'next' in elem_aria.lower() or 'next' in (elem.get_attribute('title') or '').lower()):
                    # Check if it's disabled
                    classes = elem.get_attribute('class') or ''
                    aria_disabled = elem.get_attribute('aria-disabled')
                    if 'disabled' not in classes.lower() and aria_disabled != 'true':
                        has_next = True
                
                # Check for previous button
                if ('prev' in elem_text.lower() or '<' in elem_text or
                    'prev' in elem_aria.lower() or 'previous' in elem_text.lower()):
                    has_prev = True
                    
            except Exception:
                continue
        
        # Determine pagination type
        if page_numbers:
            pagination_info['type'] = 'numbered'
            pagination_info['page_numbers'] = sorted(set(page_numbers))
            pagination_info['current_page'] = current_page
            pagination_info['total_pages'] = max(page_numbers) if page_numbers else None
        elif has_next:
            pagination_info['type'] = 'next_only'
        
        pagination_info['has_next'] = has_next
        pagination_info['has_prev'] = has_prev
        
    except Exception as e:
        print(f"Error detecting pagination: {e}")
    
    return pagination_info


async def navigate_to_page(driver, page_number: int) -> bool:
    """
    Navigate to a specific page number in pagination
    
    Returns:
        True if navigation was successful
    """
    try:
        # Look for pagination container
        pagination_selectors = [
            '.pagination', '[class*="pagination"]', '[class*="paging"]',
            '[class*="page-navigation"]', '[role="navigation"]'
        ]
        
        pagination_container = None
        for selector in pagination_selectors:
            try:
                containers = driver.find_elements(By.CSS_SELECTOR, selector)
                for container in containers:
                    if container.is_displayed():
                        pagination_container = container
                        break
                if pagination_container:
                    break
            except Exception:
                continue
        
        if not pagination_container:
            return False
        
        # Find the page number link/button
        all_elements = pagination_container.find_elements(By.TAG_NAME, 'a') + \
                       pagination_container.find_elements(By.TAG_NAME, 'button')
        
        target_element = None
        for elem in all_elements:
            try:
                if not elem.is_displayed():
                    continue
                
                elem_text = elem.text.strip()
                if elem_text == str(page_number):
                    # Check if it's not disabled
                    classes = elem.get_attribute('class') or ''
                    aria_disabled = elem.get_attribute('aria-disabled')
                    if 'disabled' not in classes.lower() and aria_disabled != 'true':
                        target_element = elem
                        break
            except Exception:
                continue
        
        if target_element:
            print(f"  Clicking page {page_number}...")
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target_element)
            await asyncio.sleep(0.5)
            driver.execute_script("arguments[0].click();", target_element)
            await asyncio.sleep(3)  # Wait for page to load
            return True
        
        return False
        
    except Exception as e:
        print(f"  Error navigating to page {page_number}: {e}")
        return False


async def navigate_to_next_page(driver) -> bool:
    """
    Navigate to the next page using next button
    
    Returns:
        True if navigation was successful
    """
    try:
        # Common next button selectors
        next_selectors = [
            'a[aria-label*="next" i]',
            'button[aria-label*="next" i]',
            'a[title*="next" i]',
            'button[title*="next" i]',
            '.pagination .next',
            '.pagination a:last-child',
            '[class*="next-page"]',
            '[class*="pagination-next"]',
            'a:contains(">")',
            'button:contains(">")',
            'a:contains("Next")',
            'button:contains("Next")',
        ]
        
        # Also search by text content
        all_links = driver.find_elements(By.TAG_NAME, 'a')
        all_buttons = driver.find_elements(By.TAG_NAME, 'button')
        
        next_element = None
        
        # Try selectors first
        for selector in next_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for elem in elements:
                    if elem.is_displayed() and elem.is_enabled():
                        classes = elem.get_attribute('class') or ''
                        aria_disabled = elem.get_attribute('aria-disabled')
                        if 'disabled' not in classes.lower() and aria_disabled != 'true':
                            next_element = elem
                            break
                if next_element:
                    break
            except Exception:
                continue
        
        # Try text-based search
        if not next_element:
            for elem in all_links + all_buttons:
                try:
                    if not elem.is_displayed():
                        continue
                    
                    elem_text = elem.text.strip().lower()
                    elem_aria = (elem.get_attribute('aria-label') or '').lower()
                    
                    if ('next' in elem_text or '>' in elem_text or 'next' in elem_aria):
                        classes = elem.get_attribute('class') or ''
                        aria_disabled = elem.get_attribute('aria-disabled')
                        if ('disabled' not in classes.lower() and aria_disabled != 'true' and
                            elem.is_enabled()):
                            next_element = elem
                            break
                except Exception:
                    continue
        
        if next_element:
            print(f"  Clicking 'Next' button...")
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_element)
            await asyncio.sleep(0.5)
            driver.execute_script("arguments[0].click();", next_element)
            await asyncio.sleep(3)  # Wait for page to load
            return True
        
        return False
        
    except Exception as e:
        print(f"  Error navigating to next page: {e}")
        return False


async def handle_pagination(driver, max_pages: int = 10) -> List[int]:
    """
    Comprehensive pagination handler that collects jobs from all pages
    
    Returns:
        List of page numbers that were successfully scraped
    """
    scraped_pages = [1]  # Start with page 1
    
    try:
        print("\n🔍 Detecting pagination...")
        pagination_info = await detect_pagination(driver)
        
        if pagination_info['type'] == 'none':
            print("  No pagination detected")
            return scraped_pages
        
        print(f"  Pagination type: {pagination_info['type']}")
        if pagination_info['total_pages']:
            print(f"  Total pages: {pagination_info['total_pages']}")
        print(f"  Current page: {pagination_info['current_page']}")
        
        if pagination_info['type'] == 'numbered' and pagination_info['page_numbers']:
            # Numbered pagination - navigate through all pages
            all_page_numbers = pagination_info['page_numbers']
            total_pages = pagination_info['total_pages'] or max(all_page_numbers)
            
            # If we detected page numbers, try to navigate through them
            pages_to_scrape = list(range(1, min(total_pages + 1, max_pages + 1)))
            
            for page_num in pages_to_scrape:
                if page_num == 1:
                    continue  # Already on page 1
                
                if len(scraped_pages) >= max_pages:
                    break
                
                print(f"\n📄 Navigating to page {page_num}...")
                success = await navigate_to_page(driver, page_num)
                
                if success:
                    scraped_pages.append(page_num)
                    # Wait for content to load
                    await asyncio.sleep(2)
                else:
                    print(f"  ⚠️  Could not navigate to page {page_num}, stopping pagination")
                    break
        
        elif pagination_info['type'] == 'next_only' or pagination_info['has_next']:
            # Next-only pagination - click next until no more pages
            print("\n📄 Navigating through pages using 'Next' button...")
            
            page_num = 1
            consecutive_failures = 0
            max_consecutive_failures = 2
            
            while page_num < max_pages:
                page_num += 1
                print(f"\n📄 Attempting to navigate to page {page_num}...")
                
                success = await navigate_to_next_page(driver)
                
                if success:
                    scraped_pages.append(page_num)
                    consecutive_failures = 0
                    # Wait for content to load
                    await asyncio.sleep(2)
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= max_consecutive_failures:
                        print(f"  ⚠️  No more pages found after {consecutive_failures} attempts")
                        break
        
        print(f"\n✓ Successfully scraped {len(scraped_pages)} page(s): {scraped_pages}")
        
    except Exception as e:
        print(f"⚠️  Error in pagination handling: {e}")
        import traceback
        traceback.print_exc()
    
    return scraped_pages


async def detect_and_clear_no_results(driver) -> bool:
    """Detect if we're on a 'no results' page and try to clear filters"""
    try:
        page_source = safe_get_page_source(driver)
        if not page_source:
            return False
        page_text = page_source.lower()
        
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
            
            # Try to find and click "Clear All" or "Show All Jobs" buttons - OPTIMIZED
            clear_button = driver.execute_script("""
                const keywords = ['clear all', 'clear filters', 'reset filters', 
                'show all jobs', 'view all jobs', 'see all jobs',
                                'remove filters', 'reset'];
                const elements = [...document.querySelectorAll('button, a[role="button"], a')];
                
                for (const el of elements) {
                    if (el.offsetParent === null) continue; // skip hidden
                    const text = el.textContent.toLowerCase();
                    if (keywords.some(kw => text.includes(kw))) {
                        return el;
                    }
                }
                return null;
            """)
            
            if clear_button:
                try:
                    print(f"  Found clear/reset button - clicking...")
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'}); arguments[0].click();", clear_button)
                    await asyncio.sleep(2)  # Reduced from 3s
                    print("  ✓ Clicked - page should reload with all jobs")
                    return True
                except Exception:
                    pass
            
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
    """Try to use search/filter inputs on the career page - OPTIMIZED"""
    try:
        print(f"Looking for search filter to apply query: '{search_query}'")
        
        # Use JavaScript to find search input faster
        search_input = driver.execute_script("""
            const selectors = [
            'input[type="search"]',
            'input[placeholder*="search" i]',
            'input[placeholder*="find" i]',
            'input[name*="search" i]',
            'input[id*="search" i]',
            '[role="searchbox"]',
                'input[type="text"]'
            ];
            
            for (const selector of selectors) {
                const elements = document.querySelectorAll(selector);
                for (const el of elements) {
                    if (el.offsetParent !== null && !el.disabled) {
                        return el;
                    }
                }
            }
            return null;
        """)
        
        if search_input:
            print("Found search input - entering search query")
            search_input.clear()
            search_input.send_keys(search_query)
            await asyncio.sleep(0.5)  # Reduced from 1s
            
            # Try to submit search
            # Strategy 1: Press Enter
            try:
                search_input.send_keys(Keys.RETURN)
                print("Submitted search with Enter key")
                await asyncio.sleep(2)  # Reduced from 3s
                return True
            except Exception:
                pass
            
            # Strategy 2: Find and click search button - OPTIMIZED
            search_button = driver.execute_script("""
                const selectors = [
                'button[type="submit"]',
                'button[aria-label*="search" i]',
                '[class*="search"][class*="button"]',
                '[class*="search-btn"]',
                    'button'
                ];
                
                for (const selector of selectors) {
                    const elements = document.querySelectorAll(selector);
                    for (const el of elements) {
                        if (el.offsetParent !== null && !el.disabled) {
                            const text = el.textContent.toLowerCase();
                            if (text.includes('search') || text.includes('find')) {
                                return el;
                            }
                        }
                    }
                }
                return null;
            """)
            
            if search_button:
                try:
                    search_button.click()
                    print("Clicked search button")
                    await asyncio.sleep(2)  # Reduced from 3s
                    return True
                except Exception:
                    pass
            
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
    """Handle infinite scroll by scrolling down - OPTIMIZED"""
    last_height = driver.execute_script("return document.body.scrollHeight")
    no_change_count = 0
    
    for scroll_num in range(max_scrolls):
        # Scroll down
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        await asyncio.sleep(1.5)  # Reduced from 2 seconds
        
        # Calculate new scroll height
        new_height = driver.execute_script("return document.body.scrollHeight")
        
        if new_height == last_height:
            no_change_count += 1
            if no_change_count >= 2:  # Stop after 2 consecutive no-changes
                print(f"Reached end of infinite scroll after {scroll_num + 1} scrolls")
                break
        else:
            no_change_count = 0
            print(f"Infinite scroll {scroll_num + 1}/{max_scrolls}")
        
        last_height = new_height


async def expand_collapsible_sections(driver, max_expansions: int = 20) -> int:
    """
    Find and expand collapsible/accordion sections that might contain job listings
    OPTIMIZED: Uses JavaScript for faster DOM queries
    
    Returns:
        Number of sections expanded
    """
    try:
        print("Checking for expandable/collapsible sections...")
        
        # Use JavaScript to find expandable buttons much faster
        js_script = """
        const expandButtons = [];
        
        // Query for specific collapsed buttons only
        const selectors = [
            'button[aria-expanded="false"]',
            '[role="button"][aria-expanded="false"]',
            '.accordion-button.collapsed',
            '.accordion-header button',
        ];
        
        const foundElements = new Set();
        
        for (const selector of selectors) {
            try {
                const elements = document.querySelectorAll(selector);
                elements.forEach(el => {
                    if (el.offsetParent !== null) { // visible check
                        foundElements.add(el);
                    }
                });
            } catch (e) {}
        }
        
        return Array.from(foundElements);
        """
        
        expand_buttons = driver.execute_script(js_script)
        
        print(f"Found {len(expand_buttons)} potential expandable sections")
        
        if len(expand_buttons) == 0:
            return 0
        
        expanded_count = 0
        
        # Expand each section (limit to max_expansions)
        for i, btn in enumerate(expand_buttons[:max_expansions]):
            try:
                # Check if still collapsed before clicking
                aria_expanded = btn.get_attribute('aria-expanded')
                if aria_expanded == 'true':
                    continue
                
                # Click to expand using JavaScript (faster)
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'}); arguments[0].click();", btn)
                expanded_count += 1
                print(f"  ✓ Expanded section {i+1}/{min(len(expand_buttons), max_expansions)}")
                await asyncio.sleep(0.3)  # Reduced wait time
                
            except Exception as e:
                print(f"  ✗ Could not expand section {i+1}: {e}")
                continue
        
        if expanded_count > 0:
            print(f"Expanded {expanded_count} collapsible sections")
            await asyncio.sleep(1)  # Reduced from 2 seconds
        
        return expanded_count
        
    except Exception as e:
        print(f"Error expanding collapsible sections: {e}")
        return 0


async def handle_load_more_button(driver, max_clicks: int = 10) -> None:
    """Handle 'Load More' buttons - OPTIMIZED with JavaScript"""
    for click_num in range(max_clicks):
        try:
            # Use JavaScript to find load more button faster
            js_script = """
            const keywords = ['load more', 'show more', 'see more', 'view more'];
            const selectors = [
                '[class*="load-more"]', '[class*="loadmore"]',
                '[class*="show-more"]', '[class*="showmore"]',
                '[id*="load-more"]', '[id*="loadmore"]',
                'button', 'a[role="button"]'
            ];
            
            for (const selector of selectors) {
                const elements = document.querySelectorAll(selector);
                for (const el of elements) {
                    if (el.offsetParent === null) continue; // skip hidden
                    
                    const text = el.textContent.toLowerCase();
                    const hasKeyword = keywords.some(kw => text.includes(kw));
                    
                    if (hasKeyword) {
                        return el;
                    }
                }
            }
            return null;
            """
            
            load_more = driver.execute_script(js_script)
            
            if load_more:
                print(f"Clicking 'Load More' button (click {click_num + 1})")
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'}); arguments[0].click();", load_more)
                await asyncio.sleep(2)  # Reduced from 3 seconds
            else:
                print("No more 'Load More' button found")
                break
                
        except Exception as e:
            print(f"'Load More' handling ended: {e}")
            break


# ============================================================================
# MAIN SCRAPING FUNCTIONS
# ============================================================================

async def extract_jobs_from_current_page(
    driver, url: str, company_name: str, max_results: int, 
    seen_titles: set, iframe_switched: bool = False
) -> List[Job]:
    """
    Extract jobs from the current page state
    
    Args:
        driver: Selenium WebDriver instance
        url: Base URL
        company_name: Company name
        max_results: Maximum number of jobs to extract
        seen_titles: Set of already seen job titles (to avoid duplicates)
        iframe_switched: Whether we're currently in an iframe context
    
    Returns:
        List of Job objects extracted from current page
    """
    page_jobs = []
    
    try:
        # Comprehensive job selectors
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
            
            # Shopify/theme-specific patterns
            '[class*="collapsible"]', '[class*="accordion"]',
            '[class*="collapsible-content"]', '[class*="accordion-content"]',
            '[class*="collapsible-trigger"]', '[class*="accordion-trigger"]',
            'details', 'details summary',  # HTML5 details element
            
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
            
            # Headings that might be job titles (for expandable sections)
            'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
            'strong', 'b',  # Bold text often used for job titles
            
            # Broader fallback - any link with job-like text
            'a[href*="job"]', 'a[href*="position"]', 'a[href*="career"]', 'a[href*="opening"]',
        ]
        
        elements = []
        for selector in job_selectors:
            try:
                found = driver.find_elements(By.CSS_SELECTOR, selector)
                if found:
                    elements.extend(found)
            except Exception:
                continue
        
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
        
        # Fallback: If no elements found, try text-based search
        if len(unique_elements) == 0:
            job_title_patterns = ["Director", "Manager", "Engineer", "Developer", "Analyst",
                                "Specialist", "Coordinator", "Assistant", "Associate"]
            
            for pattern in job_title_patterns[:5]:
                try:
                    xpath_query = f"//*[contains(text(), '{pattern}') and string-length(text()) > 10 and string-length(text()) < 200]"
                    found = driver.find_elements(By.XPATH, xpath_query)
                    for elem in found:
                        try:
                            text = elem.text.strip()
                            if is_valid_job_title(text):
                                unique_elements.append(elem)
                        except:
                            continue
                except Exception:
                    continue
        
        # Extract jobs from elements
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
        
        # Process elements with links first
        for element in elements_with_links + elements_without_links:
            if len(page_jobs) >= max_results:
                break
            
            job = extract_job_from_element(element, url, company_name)
            if job:
                if job.title in seen_titles:
                    continue
                if not is_valid_job_entry(job):
                    continue
                
                seen_titles.add(job.title)
                page_jobs.append(job)
        
    except Exception as e:
        print(f"  Error extracting jobs from page: {e}")
    
    return page_jobs


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
        chrome_options.add_argument('--disable-setuid-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--disable-software-rasterizer')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument(f'user-agent={get_random_user_agent()}')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--remote-debugging-port=9222')
        chrome_options.add_argument('--remote-debugging-address=0.0.0.0')
        chrome_options.add_argument('--disable-extensions')
        chrome_options.add_argument('--disable-background-networking')
        chrome_options.add_argument('--disable-background-timer-throttling')
        chrome_options.add_argument('--disable-backgrounding-occluded-windows')
        chrome_options.add_argument('--disable-breakpad')
        chrome_options.add_argument('--disable-client-side-phishing-detection')
        chrome_options.add_argument('--disable-default-apps')
        chrome_options.add_argument('--disable-hang-monitor')
        chrome_options.add_argument('--disable-popup-blocking')
        chrome_options.add_argument('--disable-prompt-on-repost')
        chrome_options.add_argument('--disable-sync')
        chrome_options.add_argument('--metrics-recording-only')
        chrome_options.add_argument('--no-first-run')
        chrome_options.add_argument('--safebrowsing-disable-auto-update')
        chrome_options.add_argument('--password-store=basic')
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
        
        # Set timeouts to prevent hanging - OPTIMIZED
        driver.set_page_load_timeout(30)  # Reduced from 60s - Max 30 seconds for page load
        driver.set_script_timeout(15)  # Reduced from 30s - Max 15 seconds for script execution
        driver.implicitly_wait(5)  # Reduced from 10s - Max 5 seconds for element finding
        
        print(f"Loading career page: {url}")
        
        # Try to intercept API calls first (with reduced wait time for speed)
        print("Checking for API endpoints...")
        try:
            api_urls = await intercept_api_calls(driver, url, wait_time=3)  # Reduced from 5 to 3 seconds
        except TimeoutException as e:
            print(f"Page load timeout during API interception (continuing with HTML scraping): {e}")
            try:
                # Stop page loading and continue
                driver.execute_script("window.stop();")
                await asyncio.sleep(2)
            except:
                pass
            api_urls = []
        except Exception as e:
            print(f"API interception failed (continuing with HTML scraping): {e}")
            try:
                driver.get(url)
                await asyncio.sleep(2)
            except TimeoutException:
                print("Page load timeout - stopping page load and continuing")
                driver.execute_script("window.stop();")
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
            # Check if driver is still responsive
            if not check_driver_alive(driver):
                print("⚠️  Driver is unresponsive - aborting scrape")
                return jobs
            
            # Wait a bit for page to load (page already loaded from API check)
            print("Waiting for page to load...")
            await asyncio.sleep(0.5)  # OPTIMIZED: Reduced from 2s - page already loaded
            
            # Check if we're on a "no results" page and try to clear filters
            cleared_filters = await detect_and_clear_no_results(driver)
            if cleared_filters:
                await asyncio.sleep(2)  # Wait for page to reload
            
            # Step 5: Try to click "View All Jobs" or "See All Openings" buttons - OPTIMIZED
            try:
                # Use JavaScript to find "View All" button faster
                view_all_button = driver.execute_script("""
                    const keywords = ['view all', 'see all', 'all jobs', 'all openings', 'browse jobs', 'job openings'];
                    const jobKeywords = ['job', 'opening', 'position', 'career'];
                    const elements = [...document.querySelectorAll('button, a[role="button"], a[href*="job"]')];
                    
                    for (const el of elements) {
                        if (el.offsetParent === null) continue; // skip hidden
                        const text = el.textContent.toLowerCase();
                        const hasViewAll = keywords.some(kw => text.includes(kw));
                        const hasJobKeyword = jobKeywords.some(kw => text.includes(kw));
                        
                        if (hasViewAll && hasJobKeyword) {
                            return el;
                        }
                    }
                    return null;
                """)
                
                if view_all_button:
                    print(f"  Found 'View All' button - clicking...")
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'}); arguments[0].click();", view_all_button)
                    await asyncio.sleep(2)  # Reduced from 3s
                    print("  ✓ Clicked 'View All' button")
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
                        await asyncio.sleep(2)  # Give iframe more time to load
                        
                        # Check if iframe has job-related content
                        iframe_source = safe_get_page_source(driver)
                        if not iframe_source:
                            driver.switch_to.default_content()
                            continue
                        iframe_source = iframe_source.lower()
                        has_job_keywords = any(kw in iframe_source for kw in ['job', 'position', 'career', 'opening', 'vacancy', 'director', 'manager', 'marketing', 'operations'])
                        
                        # Also check for visible job-related elements
                        try:
                            job_elements_in_iframe = driver.find_elements(By.XPATH, 
                                "//*[contains(text(), 'Director') or contains(text(), 'Manager') or contains(text(), 'Marketing') or contains(text(), 'Operations')]")
                            has_visible_jobs = len(job_elements_in_iframe) > 0
                        except:
                            has_visible_jobs = False
                        
                        if has_job_keywords or has_visible_jobs:
                            print(f"Found job content in iframe #{i+1} - staying in this context")
                            iframe_switched = True
                            # Wait a bit more for iframe content to fully load
                            await asyncio.sleep(2)
                            break
                        else:
                            driver.switch_to.default_content()
                    except Exception as e:
                        print(f"Error checking iframe #{i+1}: {e}")
                        driver.switch_to.default_content()
            
            # Handle different loading patterns
            print("Waiting for initial content to load...")
            await asyncio.sleep(3)  # Reduced from 5 to 3 seconds
            
            # Scroll to trigger lazy-loaded content
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
            await asyncio.sleep(1)  # Reduced from 2 seconds
            
            # Expand any collapsible/accordion sections that might contain jobs
            await expand_collapsible_sections(driver, max_expansions=15)  # Reduced from 20
            
            # Scroll again after expansion to ensure all content is visible
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            await asyncio.sleep(1)  # Reduced from 2 seconds
            
            # Try load more button first
            await handle_load_more_button(driver, max_clicks=5)
            
            # Try infinite scroll
            await handle_infinite_scroll(driver, max_scrolls=5)
            
            # Wait a bit more for any delayed content before extraction
            await asyncio.sleep(1.5)  # Reduced from 3 seconds
            
            print("\nExtracting job listings from page 1...")
            
            # OPTIMIZED: Use JavaScript to find all job elements in ONE query
            job_elements_data = driver.execute_script("""
                const selectors = [
                    // Specific selectors (high priority)
                    '[data-automation-id="jobTitle"]',
                    '[data-ui="job"]', 'li[data-ui="job"]',
                    '.BambooHR-ATS-Jobs-Item',
                    '.postings-group', '.posting',
                    '.jv-job-list-item', '.opening-job',
                    '[data-job-id]', '[data-posting-id]',
                    
                    // Generic job containers
                    '[class*="job-card"]', '[class*="jobCard"]',
                    '[class*="job-item"]', '[class*="job-posting"]',
                    '[class*="jobPosting"]', '[class*="JobPosting"]',
                    'article[class*="job"]', 'li[class*="job"]',
                    'div[class*="job"]',
                    
                    // Position/Opening containers
                    '[class*="position"]', '[class*="opening"]',
                    
                    // Links
                    'a[href*="/job"]', 'a[href*="/req"]',
                    'a[href*="job"]', 'a[href*="position"]'
                ];
                
                const foundElements = new Set();
                const headerPatterns = ['current openings', 'all openings', 'job openings', 
                                       'search', 'filter', 'select location', 'select job type'];
                const jobIndicators = ['full time', 'part time', 'contract', 'remote', 
                                      'manager', 'director', 'engineer', 'days ago'];
                
                // Find elements
                for (const selector of selectors) {
                    try {
                        const elements = document.querySelectorAll(selector);
                        elements.forEach(el => {
                            if (el.offsetParent !== null) { // visible check
                                foundElements.add(el);
                            }
                        });
                    } catch (e) {}
                }
                
                // Filter and deduplicate
                const uniqueElements = [];
                const seenPositions = new Set();
                const seenTexts = new Set();
                
                foundElements.forEach(el => {
                    const text = el.textContent.toLowerCase().substring(0, 200);
                    const textKey = text.substring(0, 50);
                    
                    // Skip headers/navigation
                    const isHeader = headerPatterns.some(p => text.includes(p)) && 
                                    text.split('\\n').length < 3;
                    if (isHeader) return;
                    
                    // Skip short UI elements
                    if (text.length < 10) return;
                    
                    // Check for job indicators
                    const hasJobIndicators = jobIndicators.some(ind => text.includes(ind));
                    if (!hasJobIndicators && text.length < 30) return;
                    
                    // Deduplicate by position
                    const rect = el.getBoundingClientRect();
                    const posKey = rect.x + ',' + rect.y;
                    
                    if (!seenPositions.has(posKey) && !seenTexts.has(textKey)) {
                        seenPositions.add(posKey);
                        seenTexts.add(textKey);
                        uniqueElements.push(el);
                    }
                });
                
                return uniqueElements;
            """)
            
            elements = job_elements_data if job_elements_data else []
            print(f"Found {len(elements)} unique job elements (after filtering)")
            
            unique_elements = elements  # Already filtered by JavaScript
            
            # Fallback: If no elements found with standard selectors, try text-based search - OPTIMIZED
            if len(unique_elements) == 0:
                print("\n⚠️  No elements found with standard selectors. Trying text-based fallback...")
                try:
                    # Use JavaScript to find elements with job title patterns (much faster)
                    fallback_elements = driver.execute_script("""
                        const patterns = ['Director', 'Manager', 'Engineer', 'Developer', 'Analyst',
                                        'Specialist', 'Coordinator', 'Assistant', 'Associate'];
                        const found = new Set();
                        
                        // Search through all visible elements
                        const allElements = document.querySelectorAll('*');
                        for (const el of allElements) {
                            if (el.offsetParent === null) continue; // skip hidden
                            
                            const text = el.textContent;
                            if (text.length < 10 || text.length > 200) continue;
                            
                            // Check if contains job title pattern
                            const hasPattern = patterns.some(p => text.includes(p));
                            if (hasPattern) {
                                // Avoid nested duplicates - prefer leaf nodes
                                const isLeaf = el.children.length === 0 || 
                                             [...el.children].every(child => child.offsetParent === null);
                                if (isLeaf) {
                                    found.add(el);
                                    if (found.size >= 50) break; // Limit results
                                }
                            }
                        }
                        
                        return Array.from(found);
                    """)
                    
                    if fallback_elements:
                        print(f"  Found {len(fallback_elements)} potential job titles via text search")
                        unique_elements = fallback_elements[:max_results * 2]  # Limit to avoid too many
                except Exception as e:
                    print(f"  Fallback text search failed: {e}")
            
            # If still no elements found, save page source for debugging
            if len(unique_elements) == 0:
                print("\n⚠️  No job elements found. Saving page source for debugging...")
                page_source = safe_get_page_source(driver)
                if not page_source:
                    print("  ⚠️  Could not retrieve page source (timeout/error)")
                    page_source = "<html><body>Failed to retrieve page source</body></html>"
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
            
            # Extract jobs - prioritize elements with links to job detail pages - OPTIMIZED
            seen_titles = set()
            
            # Use JavaScript to check which elements have links (much faster)
            elements_link_status = driver.execute_script("""
                return arguments[0].map(el => ({
                    element: el,
                    hasLink: el.tagName === 'A' || el.querySelector('a') !== null
                }));
            """, unique_elements)
            
            elements_with_links = [item['element'] for item in elements_link_status if item['hasLink']]
            elements_without_links = [item['element'] for item in elements_link_status if not item['hasLink']]
            
            print(f"  Elements with links: {len(elements_with_links)}, without links: {len(elements_without_links)}")
            
            # Process elements with links first (more likely to be real jobs)
            print(f"\n  Attempting to extract jobs from {len(elements_with_links + elements_without_links)} elements...")
            extraction_failures = 0
            
            # Pre-fetch all element data in one batch (MAJOR OPTIMIZATION)
            all_elements = elements_with_links + elements_without_links
            print(f"  Pre-fetching element data...")
            
            element_data_batch = driver.execute_script("""
                return arguments[0].map(el => {
                    const links = [...el.querySelectorAll('a[href]')];
                    return {
                        outerHTML: el.outerHTML,
                        text: (el.textContent || '').trim(),
                        textPreview: (el.textContent || '').trim().substring(0, 100),
                        links: links.map(a => a.href)
                    };
                });
            """, all_elements)
            
            print(f"  Extracting job data from elements...")
            
            for i, element in enumerate(all_elements):
                if len(jobs) >= max_results:
                    break
                
                try:
                    # Get element data (already fetched)
                    element_data = element_data_batch[i] if i < len(element_data_batch) else {}
                    element_text = element_data.get('textPreview', 'No text')
                    
                    job = extract_job_from_element_optimized(element, element_data, url, company_name)
                    if job:
                        if job.title in seen_titles:
                            continue
                        # Comprehensive validation using is_valid_job_entry
                        validation_result = is_valid_job_entry(job, debug=True)
                        if not validation_result:
                            print(f"  ✗ Skipped (invalid entry): {job.title}")
                            if job.url:
                                print(f"     URL: {job.url[:80] if len(job.url) > 80 else job.url}")
                            continue
                        # All checks passed
                        seen_titles.add(job.title)
                        jobs.append(job)
                        print(f"  ✓ Extracted: {job.title}")
                    else:
                        extraction_failures += 1
                        if extraction_failures <= 3:  # Only show first 3 failures
                            print(f"  ⚠️  Failed to extract job from element {i+1}: '{element_text}'")
                except Exception as e:
                    extraction_failures += 1
                    if extraction_failures <= 3:
                        print(f"  ⚠️  Error extracting from element {i+1}: {e}")
                    continue
            
            if extraction_failures > 0:
                print(f"  ⚠️  Failed to extract jobs from {extraction_failures} elements")
            
            # Switch back to default content if we were in an iframe
            # (Pagination is usually in the main document, not in iframes)
            if iframe_switched:
                try:
                    driver.switch_to.default_content()
                    print("Switched back to default content for pagination detection")
                    iframe_switched = False  # Reset flag since we're now in default content
                    await asyncio.sleep(1)  # Wait for context switch
                except Exception:
                    pass
            
            # Now handle pagination and extract jobs from each page
            if len(jobs) < max_results:
                print(f"\n{'='*60}")
                print(f"Handling pagination to collect more jobs...")
                print(f"Current jobs: {len(jobs)}, Target: {max_results}")
                print(f"{'='*60}")
                
                # Detect pagination (make sure we're in default content, not in an iframe)
                print("🔍 Detecting pagination...")
                pagination_info = await detect_pagination(driver)
                
                print(f"  Pagination type: {pagination_info['type']}")
                if pagination_info['total_pages']:
                    print(f"  Total pages detected: {pagination_info['total_pages']}")
                if pagination_info['current_page']:
                    print(f"  Current page: {pagination_info['current_page']}")
                if pagination_info['page_numbers']:
                    print(f"  Page numbers found: {pagination_info['page_numbers'][:10]}")  # Show first 10
                
                if pagination_info['type'] == 'none':
                    print("  ⚠️  No pagination detected - only scraping page 1")
                
                if pagination_info['type'] != 'none':
                    if pagination_info['type'] == 'numbered' and pagination_info['page_numbers']:
                        # Numbered pagination
                        all_page_numbers = pagination_info['page_numbers']
                        total_pages = pagination_info['total_pages'] or max(all_page_numbers)
                        current_page = pagination_info['current_page']
                        
                        # Start from page 2 if we're on page 1
                        start_page = 2 if current_page == 1 else current_page + 1
                        pages_to_scrape = list(range(start_page, min(total_pages + 1, 20)))  # Limit to 20 pages
                        
                        for page_num in pages_to_scrape:
                            if len(jobs) >= max_results:
                                break
                            
                            print(f"\n📄 Extracting jobs from page {page_num}...")
                            success = await navigate_to_page(driver, page_num)
                            
                            if not success:
                                print(f"  ⚠️  Could not navigate to page {page_num}, stopping pagination")
                                break
                            
                            # Expand sections and wait for content
                            await expand_collapsible_sections(driver, max_expansions=10)  # Reduced from 20
                            await asyncio.sleep(1)  # Reduced from 2 seconds
                            
                            # Extract jobs from this page using the same logic
                            page_jobs = await extract_jobs_from_current_page(
                                driver, url, company_name, max_results - len(jobs), seen_titles, iframe_switched
                            )
                            
                            # Add page jobs to main jobs list
                            for job in page_jobs:
                                if job.title not in seen_titles:
                                    seen_titles.add(job.title)
                                    jobs.append(job)
                                    print(f"  ✓ Extracted: {job.title}")
                            
                            print(f"  Found {len(page_jobs)} jobs on page {page_num} (Total: {len(jobs)})")
                    
                    elif pagination_info['has_next'] or pagination_info['type'] == 'next_only':
                        # Next-only pagination
                        page_num = 1
                        consecutive_failures = 0
                        max_consecutive_failures = 2
                        
                        while len(jobs) < max_results and page_num < 20:  # Limit to 20 pages
                            page_num += 1
                            print(f"\n📄 Extracting jobs from page {page_num}...")
                            
                            success = await navigate_to_next_page(driver)
                            
                            if not success:
                                consecutive_failures += 1
                                if consecutive_failures >= max_consecutive_failures:
                                    print(f"  ⚠️  No more pages found after {consecutive_failures} attempts")
                                    break
                                continue
                            
                            consecutive_failures = 0
                            
                            # Expand sections and wait for content
                            await expand_collapsible_sections(driver, max_expansions=10)  # Reduced from 20
                            await asyncio.sleep(1)  # Reduced from 2 seconds
                            
                            # Extract jobs from this page
                            page_jobs = await extract_jobs_from_current_page(
                                driver, url, company_name, max_results - len(jobs), seen_titles, iframe_switched
                            )
                            
                            # Add page jobs to main jobs list
                            for job in page_jobs:
                                if job.title not in seen_titles:
                                    seen_titles.add(job.title)
                                    jobs.append(job)
                                    print(f"  ✓ Extracted: {job.title}")
                            
                            print(f"  Found {len(page_jobs)} jobs on page {page_num} (Total: {len(jobs)})")
        
        print(f"\nTotal jobs extracted: {len(jobs)}")
        
        # Apply comprehensive filtering to remove invalid entries
        if jobs:
            print(f"\nApplying comprehensive validation filters...")
            jobs = filter_invalid_jobs(jobs)
            print(f"Jobs after filtering: {len(jobs)}")
        
    except TimeoutException as e:
        print(f"Timeout error in Selenium scraping: {e}")
        print("The page took too long to load or respond")
        # Try to get whatever jobs we have so far
        print(f"Returning {len(jobs)} jobs collected before timeout")
    
    except Exception as e:
        print(f"Error in Selenium scraping: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        if driver:
            try:
                driver.quit()
            except Exception as e:
                print(f"Error closing driver: {e}")
                # Force kill if needed
                try:
                    driver.service.process.kill()
                except:
                    pass
    
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
    
    # Apply final validation filter to catch any remaining invalid entries
    if jobs:
        print(f"\nApplying final validation filter...")
        jobs = filter_invalid_jobs(jobs)
        print(f"Jobs after final filtering: {len(jobs)}")
    
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