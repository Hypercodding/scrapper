"""
Site-specific configurations for career page scraping
"""
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum


class PageType(Enum):
    """Types of career page structures"""
    DIRECT_LISTING = "direct_listing"  # Jobs shown immediately
    SEARCH_FIRST = "search_first"  # Requires search before showing jobs
    MODAL_FIRST = "modal_first"  # Shows modal/overlay before jobs
    FILTER_REQUIRED = "filter_required"  # Requires location/dept selection
    MULTI_STEP = "multi_step"  # Progressive navigation required
    

class PaginationType(Enum):
    """Types of pagination"""
    NONE = "none"
    NUMBERED = "numbered"  # Numbered page buttons
    INFINITE_SCROLL = "infinite_scroll"
    LOAD_MORE = "load_more"  # "Load More" button
    MIXED = "mixed"  # Combination of methods


@dataclass
class SelectorConfig:
    """Configuration for element selectors with fallbacks"""
    primary: str
    fallbacks: List[str] = field(default_factory=list)
    
    def all_selectors(self) -> List[str]:
        """Get all selectors in priority order"""
        return [self.primary] + self.fallbacks


@dataclass
class SiteConfig:
    """Configuration for a specific website"""
    domain: str
    page_type: PageType = PageType.DIRECT_LISTING
    pagination_type: PaginationType = PaginationType.NONE
    
    # Search configuration
    search_required: bool = False
    search_input_selectors: List[str] = field(default_factory=lambda: [
        'input[type="search"]',
        'input[placeholder*="search" i]',
        'input[placeholder*="job" i]',
        'input[name*="search" i]',
        'input[id*="search" i]'
    ])
    search_button_selectors: List[str] = field(default_factory=lambda: [
        'button[type="submit"]',
        'button[aria-label*="search" i]',
        'button:has(svg)',
        '[class*="search-button"]'
    ])
    search_default_query: str = ""
    
    # Modal/overlay configuration
    has_cookie_banner: bool = True
    cookie_accept_selectors: List[str] = field(default_factory=lambda: [
        'button[id*="accept" i]',
        'button[class*="accept" i]',
        'button:contains("Accept")',
        'button:contains("Agree")',
        'button:contains("OK")',
        '[id*="cookie-accept"]',
        '[class*="cookie-accept"]'
    ])
    modal_close_selectors: List[str] = field(default_factory=lambda: [
        '[aria-label*="close" i]',
        'button[class*="close"]',
        'button[class*="dismiss"]',
        '.modal-close',
        '[data-dismiss="modal"]',
        'button.close'
    ])
    
    # Job listing selectors
    job_container_selectors: List[str] = field(default_factory=lambda: [
        '[class*="job-list"]',
        '[class*="job-item"]',
        '[class*="job-card"]',
        '[class*="position"]',
        '[class*="opening"]',
        'article[class*="job"]',
        'li[class*="job"]',
        'div[class*="job"]'
    ])
    
    job_title_selectors: SelectorConfig = field(default_factory=lambda: SelectorConfig(
        primary='h2.job-title',
        fallbacks=[
            '[data-job-title]',
            'a.position-title',
            '.job-card h3',
            '.job-listing h2',
            '.job-listing h3',
            'h1', 'h2', 'h3', 'h4'
        ]
    ))
    
    job_location_selectors: SelectorConfig = field(default_factory=lambda: SelectorConfig(
        primary='[data-location]',
        fallbacks=[
            '[class*="location"]',
            '[class*="office"]',
            'span.location',
            '.job-card .location'
        ]
    ))
    
    job_type_selectors: SelectorConfig = field(default_factory=lambda: SelectorConfig(
        primary='[data-job-type]',
        fallbacks=[
            '[class*="job-type"]',
            '[class*="employment"]',
            'span.type'
        ]
    ))
    
    # Pagination configuration
    next_page_selectors: List[str] = field(default_factory=lambda: [
        'a[aria-label*="next" i]',
        'button[aria-label*="next" i]',
        '.pagination .next',
        '.pagination a:last-child',
        '[class*="next-page"]'
    ])
    
    load_more_selectors: List[str] = field(default_factory=lambda: [
        'button[aria-label*="load more" i]',
        '[class*="load-more"]',
        '[class*="show-more"]',
        'button:contains("Load More")',
        'button:contains("Show More")'
    ])
    
    # Wait and timing configuration
    initial_wait: float = 3.0
    search_wait: float = 3.0
    scroll_wait: float = 2.0
    page_load_wait: float = 2.0
    element_wait: float = 10.0
    
    # Rate limiting
    min_delay: float = 1.0
    max_delay: float = 3.0
    
    # Custom JavaScript to run after page load
    custom_js: Optional[str] = None
    
    # Specific handling flags
    requires_location_selection: bool = False
    requires_department_selection: bool = False
    has_infinite_scroll: bool = False
    max_scroll_attempts: int = 10
    max_pages: int = 5
    
    # Anti-detection measures
    use_undetected: bool = False
    rotate_user_agent: bool = False


# Pre-defined configurations for known platforms
PLATFORM_CONFIGS: Dict[str, SiteConfig] = {
    'greenhouse': SiteConfig(
        domain='greenhouse.io',
        page_type=PageType.DIRECT_LISTING,
        pagination_type=PaginationType.NUMBERED,
        job_container_selectors=['.opening', '[data-qa="opening"]'],
        has_cookie_banner=True
    ),
    
    'lever': SiteConfig(
        domain='lever.co',
        page_type=PageType.DIRECT_LISTING,
        pagination_type=PaginationType.INFINITE_SCROLL,
        job_container_selectors=['.posting', '.postings-group'],
        has_infinite_scroll=True
    ),
    
    'workday': SiteConfig(
        domain='myworkdayjobs.com',
        page_type=PageType.SEARCH_FIRST,
        search_required=False,  # Can work without search
        pagination_type=PaginationType.NUMBERED,
        job_container_selectors=['[data-automation-id="jobTitle"]'],
        has_cookie_banner=True
    ),
    
    'bamboohr': SiteConfig(
        domain='bamboohr.com/careers',
        page_type=PageType.DIRECT_LISTING,
        pagination_type=PaginationType.NONE,
        job_container_selectors=['.BambooHR-ATS-Jobs-Item'],
        has_cookie_banner=False
    ),
    
    'smartrecruiters': SiteConfig(
        domain='smartrecruiters.com',
        page_type=PageType.SEARCH_FIRST,
        search_required=False,
        pagination_type=PaginationType.INFINITE_SCROLL,
        job_container_selectors=['.opening-job'],
        has_infinite_scroll=True
    ),
    
    'jobvite': SiteConfig(
        domain='jobvite.com',
        page_type=PageType.DIRECT_LISTING,
        pagination_type=PaginationType.LOAD_MORE,
        job_container_selectors=['.jv-job-list-item']
    ),
    
    'ashbyhq': SiteConfig(
        domain='ashbyhq.com',
        page_type=PageType.DIRECT_LISTING,
        pagination_type=PaginationType.NONE,
        job_container_selectors=['[class*="JobsList"]']
    ),
    
    'ultipro': SiteConfig(
        domain='ultipro.com',
        page_type=PageType.DIRECT_LISTING,
        pagination_type=PaginationType.INFINITE_SCROLL,
        job_container_selectors=['div[class*="job"]'],
        has_infinite_scroll=True,
        initial_wait=5.0
    ),
    
    'dayforce': SiteConfig(
        domain='dayforcehcm.com',
        page_type=PageType.DIRECT_LISTING,
        pagination_type=PaginationType.LOAD_MORE,
        job_container_selectors=['[class*="job"]', '[class*="position"]'],
        has_cookie_banner=True,
        initial_wait=5.0
    ),
}


def get_site_config(url: str) -> SiteConfig:
    """
    Get configuration for a specific URL
    
    Args:
        url: The URL to get config for
        
    Returns:
        SiteConfig object with appropriate settings
    """
    url_lower = url.lower()
    
    # Check for known platforms
    for platform_name, config in PLATFORM_CONFIGS.items():
        if config.domain in url_lower:
            return config
    
    # Return default configuration
    return SiteConfig(domain=url)


def detect_page_type(driver, url: str) -> PageType:
    """
    Detect the type of career page structure
    
    Args:
        driver: Selenium WebDriver instance
        url: The URL being scraped
        
    Returns:
        PageType enum value
    """
    try:
        page_source = driver.page_source.lower()
        
        # Check for search-first indicators
        search_indicators = ['search for jobs', 'find jobs', 'job search', 'search positions']
        has_search_prompt = any(indicator in page_source for indicator in search_indicators)
        
        # Check for modal indicators
        modal_indicators = ['modal', 'overlay', 'popup']
        has_modal = any(f'class="{indicator}"' in page_source or f'class*="{indicator}"' in page_source 
                       for indicator in modal_indicators)
        
        # Check for filter indicators
        filter_indicators = ['filter by location', 'select location', 'choose department']
        has_filters = any(indicator in page_source for indicator in filter_indicators)
        
        # Determine page type
        if has_modal:
            return PageType.MODAL_FIRST
        elif has_search_prompt and 'job' not in page_source[:2000]:  # No jobs in first part
            return PageType.SEARCH_FIRST
        elif has_filters:
            return PageType.FILTER_REQUIRED
        else:
            return PageType.DIRECT_LISTING
            
    except Exception:
        return PageType.DIRECT_LISTING


def detect_pagination_type(driver) -> PaginationType:
    """
    Detect the type of pagination used
    
    Args:
        driver: Selenium WebDriver instance
        
    Returns:
        PaginationType enum value
    """
    try:
        page_source = driver.page_source.lower()
        
        # Check for numbered pagination
        if 'pagination' in page_source or 'page-item' in page_source:
            return PaginationType.NUMBERED
        
        # Check for load more button
        if 'load more' in page_source or 'show more' in page_source:
            return PaginationType.LOAD_MORE
        
        # Check for infinite scroll indicators
        scroll_indicators = ['infinite-scroll', 'lazy-load', 'scroll-load']
        if any(indicator in page_source for indicator in scroll_indicators):
            return PaginationType.INFINITE_SCROLL
        
        return PaginationType.NONE
        
    except Exception:
        return PaginationType.NONE

