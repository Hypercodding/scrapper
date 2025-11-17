"""
Enhanced utilities for career page scraping
Handles modals, search, pagination, and other interactions
"""
import asyncio
import random
import time
from typing import List, Optional, Tuple, Dict, Any
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from bs4 import BeautifulSoup
import logging

from app.services.scraper_config import SiteConfig, PageType, PaginationType

# Setup logging
logger = logging.getLogger(__name__)


class ScrapeMetadata:
    """Metadata about the scraping operation"""
    def __init__(self, url: str):
        self.url = url
        self.scraped_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.total_jobs = 0
        self.success_rate = 1.0
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.page_type_detected: Optional[str] = None
        self.pagination_type_detected: Optional[str] = None
        self.modals_closed = 0
        self.pages_scraped = 1
        self.time_taken: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "url": self.url,
            "scrapedAt": self.scraped_at,
            "totalJobs": self.total_jobs,
            "successRate": self.success_rate,
            "errors": self.errors,
            "warnings": self.warnings,
            "pageTypeDetected": self.page_type_detected,
            "paginationTypeDetected": self.pagination_type_detected,
            "modalsClosed": self.modals_closed,
            "pagesScraped": self.pages_scraped,
            "timeTaken": round(self.time_taken, 2)
        }


async def random_delay(config: SiteConfig):
    """Add random delay for politeness and anti-detection"""
    delay = random.uniform(config.min_delay, config.max_delay)
    await asyncio.sleep(delay)


async def handle_cookie_banner(driver, config: SiteConfig, metadata: ScrapeMetadata) -> bool:
    """
    Detect and accept cookie consent banners
    
    Args:
        driver: Selenium WebDriver instance
        config: Site configuration
        metadata: Scraping metadata object
        
    Returns:
        True if banner was handled, False otherwise
    """
    if not config.has_cookie_banner:
        return False
    
    try:
        logger.info("Checking for cookie consent banner...")
        
        # Common cookie banner indicators
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
        
        # Try to find and click accept button
        for selector in config.cookie_accept_selectors:
            try:
                # Try to find accept button
                buttons = driver.find_elements(By.CSS_SELECTOR, selector)
                
                # Also try by text content
                all_buttons = driver.find_elements(By.TAG_NAME, 'button')
                for btn in all_buttons:
                    btn_text = btn.text.lower()
                    if any(text in btn_text for text in ['accept', 'agree', 'ok', 'got it', 'allow']):
                        if btn.is_displayed() and btn.is_enabled():
                            buttons.append(btn)
                
                for button in buttons:
                    try:
                        if button.is_displayed() and button.is_enabled():
                            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
                            await asyncio.sleep(0.5)
                            button.click()
                            logger.info("✓ Cookie banner accepted")
                            await asyncio.sleep(1)
                            metadata.modals_closed += 1
                            return True
                    except Exception:
                        continue
                        
            except Exception as e:
                continue
        
        logger.warning("Cookie banner detected but couldn't find accept button")
        metadata.warnings.append("Cookie banner detected but couldn't accept")
        return False
        
    except Exception as e:
        logger.error(f"Error handling cookie banner: {e}")
        return False


async def close_modals_and_overlays(driver, config: SiteConfig, metadata: ScrapeMetadata) -> int:
    """
    Close any modal dialogs or overlays
    
    Args:
        driver: Selenium WebDriver instance
        config: Site configuration
        metadata: Scraping metadata object
        
    Returns:
        Number of modals closed
    """
    try:
        logger.info("Checking for modal overlays...")
        closed_count = 0
        
        # Check for modal indicators
        modal_indicators = [
            '[role="dialog"]',
            '.modal',
            '[class*="modal"]',
            '[class*="overlay"]',
            '[class*="popup"]',
            '.dialog'
        ]
        
        modals_found = []
        for selector in modal_indicators:
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
        
        # Try to close each modal
        for modal in modals_found[:3]:  # Limit to first 3
            try:
                # Look for close button within modal
                for selector in config.modal_close_selectors:
                    try:
                        close_btns = modal.find_elements(By.CSS_SELECTOR, selector)
                        
                        for btn in close_btns:
                            if btn.is_displayed() and btn.is_enabled():
                                driver.execute_script("arguments[0].click();", btn)
                                logger.info("✓ Closed modal overlay")
                                await asyncio.sleep(1)
                                closed_count += 1
                                metadata.modals_closed += 1
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
            except Exception:
                pass
        
        return closed_count
        
    except Exception as e:
        logger.error(f"Error closing modals: {e}")
        return 0


async def perform_search(driver, config: SiteConfig, search_query: Optional[str], metadata: ScrapeMetadata) -> bool:
    """
    Perform search if required by the site
    
    Args:
        driver: Selenium WebDriver instance
        config: Site configuration
        search_query: Search term (or None for default)
        metadata: Scraping metadata object
        
    Returns:
        True if search was performed successfully
    """
    try:
        if not config.search_required and not search_query:
            return False
        
        logger.info("Attempting to perform search...")
        
        # Use provided query or default
        query = search_query if search_query else config.search_default_query
        
        # Find search input
        search_input = None
        for selector in config.search_input_selectors:
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
        
        if not search_input:
            logger.warning("Search input not found")
            metadata.warnings.append("Search input not found")
            return False
        
        logger.info(f"Found search input - entering query: '{query}'")
        
        # Clear and enter search query
        search_input.clear()
        await asyncio.sleep(0.5)
        
        if query:
            search_input.send_keys(query)
        else:
            # Try empty search or wildcard
            search_input.send_keys("")
        
        await asyncio.sleep(1)
        
        # Try to submit search
        search_submitted = False
        
        # Method 1: Press Enter
        try:
            search_input.send_keys(Keys.RETURN)
            logger.info("Submitted search with Enter key")
            await asyncio.sleep(config.search_wait)
            search_submitted = True
        except Exception:
            pass
        
        # Method 2: Find and click search button
        if not search_submitted:
            for selector in config.search_button_selectors:
                try:
                    buttons = driver.find_elements(By.CSS_SELECTOR, selector)
                    for btn in buttons:
                        if btn.is_displayed() and btn.is_enabled():
                            btn.click()
                            logger.info("Clicked search button")
                            await asyncio.sleep(config.search_wait)
                            search_submitted = True
                            break
                    if search_submitted:
                        break
                except Exception:
                    continue
        
        if search_submitted:
            logger.info("✓ Search completed successfully")
            return True
        else:
            logger.warning("Could not submit search")
            metadata.warnings.append("Could not submit search")
            return False
            
    except Exception as e:
        logger.error(f"Error performing search: {e}")
        metadata.errors.append(f"Search error: {str(e)}")
        return False


async def wait_for_content_load(driver, config: SiteConfig, timeout: int = 10):
    """
    Wait for dynamic content to load
    
    Args:
        driver: Selenium WebDriver instance
        config: Site configuration
        timeout: Maximum wait time in seconds
    """
    try:
        # Wait for any of the job container selectors to appear
        for selector in config.job_container_selectors[:3]:
            try:
                WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                logger.info(f"Content loaded - found elements with selector: {selector}")
                return
            except TimeoutException:
                continue
        
        logger.info(f"Waited {timeout}s for content (may not have loaded)")
        
    except Exception as e:
        logger.warning(f"Error waiting for content: {e}")


async def handle_pagination_numbered(driver, config: SiteConfig, metadata: ScrapeMetadata) -> bool:
    """
    Handle numbered pagination
    
    Args:
        driver: Selenium WebDriver instance
        config: Site configuration
        metadata: Scraping metadata object
        
    Returns:
        True if next page was loaded
    """
    try:
        next_button = None
        
        for selector in config.next_page_selectors:
            try:
                buttons = driver.find_elements(By.CSS_SELECTOR, selector)
                for btn in buttons:
                    if btn.is_displayed() and btn.is_enabled():
                        # Check if it's not disabled
                        classes = btn.get_attribute('class') or ''
                        aria_disabled = btn.get_attribute('aria-disabled')
                        
                        if 'disabled' not in classes.lower() and aria_disabled != 'true':
                            next_button = btn
                            break
                if next_button:
                    break
            except Exception:
                continue
        
        if next_button:
            logger.info(f"Navigating to page {metadata.pages_scraped + 1}")
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_button)
            await asyncio.sleep(0.5)
            next_button.click()
            await asyncio.sleep(config.page_load_wait)
            metadata.pages_scraped += 1
            return True
        
        return False
        
    except Exception as e:
        logger.error(f"Error in numbered pagination: {e}")
        return False


async def handle_pagination_load_more(driver, config: SiteConfig, metadata: ScrapeMetadata) -> bool:
    """
    Handle "Load More" button pagination
    
    Args:
        driver: Selenium WebDriver instance
        config: Site configuration
        metadata: Scraping metadata object
        
    Returns:
        True if more content was loaded
    """
    try:
        load_more_button = None
        
        # Find load more buttons by text
        buttons = driver.find_elements(By.TAG_NAME, 'button')
        for btn in buttons:
            try:
                btn_text = btn.text.lower()
                if any(text in btn_text for text in ['load more', 'show more', 'see more', 'view more']):
                    if btn.is_displayed() and btn.is_enabled():
                        load_more_button = btn
                        break
            except Exception:
                continue
        
        # Try selectors as fallback
        if not load_more_button:
            for selector in config.load_more_selectors:
                try:
                    buttons = driver.find_elements(By.CSS_SELECTOR, selector)
                    for btn in buttons:
                        if btn.is_displayed() and btn.is_enabled():
                            load_more_button = btn
                            break
                    if load_more_button:
                        break
                except Exception:
                    continue
        
        if load_more_button:
            logger.info("Clicking 'Load More' button")
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", load_more_button)
            await asyncio.sleep(0.5)
            driver.execute_script("arguments[0].click();", load_more_button)
            await asyncio.sleep(config.scroll_wait)
            metadata.pages_scraped += 1
            return True
        
        return False
        
    except Exception as e:
        logger.error(f"Error in load more pagination: {e}")
        return False


async def handle_pagination_infinite_scroll(driver, config: SiteConfig, metadata: ScrapeMetadata) -> bool:
    """
    Handle infinite scroll pagination
    
    Args:
        driver: Selenium WebDriver instance
        config: Site configuration
        metadata: Scraping metadata object
        
    Returns:
        True if new content was loaded
    """
    try:
        # Get current height
        last_height = driver.execute_script("return document.body.scrollHeight")
        
        # Scroll to bottom
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        await asyncio.sleep(config.scroll_wait)
        
        # Get new height
        new_height = driver.execute_script("return document.body.scrollHeight")
        
        if new_height > last_height:
            logger.info(f"Infinite scroll loaded more content (page {metadata.pages_scraped + 1})")
            metadata.pages_scraped += 1
            return True
        
        return False
        
    except Exception as e:
        logger.error(f"Error in infinite scroll: {e}")
        return False


def find_element_with_fallbacks(element, selectors: List[str]) -> Optional[str]:
    """
    Try multiple selectors to find element text
    
    Args:
        element: BeautifulSoup element or Selenium WebElement
        selectors: List of CSS selectors to try
        
    Returns:
        Text content if found, None otherwise
    """
    try:
        # Handle BeautifulSoup element
        if hasattr(element, 'select_one'):
            for selector in selectors:
                try:
                    found = element.select_one(selector)
                    if found:
                        text = found.get_text(strip=True)
                        if text:
                            return text
                except Exception:
                    continue
        
        # Handle Selenium WebElement
        elif hasattr(element, 'find_element'):
            for selector in selectors:
                try:
                    found = element.find_element(By.CSS_SELECTOR, selector)
                    if found:
                        text = found.text.strip()
                        if text:
                            return text
                except Exception:
                    continue
        
        return None
        
    except Exception:
        return None


async def retry_operation(operation, max_retries: int = 3, backoff_factor: float = 1.5):
    """
    Retry an operation with exponential backoff
    
    Args:
        operation: Async function to retry
        max_retries: Maximum number of retry attempts
        backoff_factor: Multiplier for wait time between retries
        
    Returns:
        Result of operation if successful
        
    Raises:
        Last exception if all retries fail
    """
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            return await operation()
        except Exception as e:
            last_exception = e
            if attempt < max_retries - 1:
                wait_time = backoff_factor ** attempt
                logger.warning(f"Operation failed (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
    
    raise last_exception


def take_screenshot_on_error(driver, url: str, error_type: str):
    """
    Take screenshot when critical error occurs
    
    Args:
        driver: Selenium WebDriver instance
        url: URL being scraped
        error_type: Type of error for filename
    """
    try:
        import hashlib
        from datetime import datetime
        
        # Create filename
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"/tmp/scraper_error_{error_type}_{url_hash}_{timestamp}.png"
        
        driver.save_screenshot(filename)
        logger.info(f"Screenshot saved: {filename}")
        return filename
        
    except Exception as e:
        logger.error(f"Could not save screenshot: {e}")
        return None

