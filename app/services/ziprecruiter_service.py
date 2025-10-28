import time
import asyncio
import random
from typing import Optional, List
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from app.models.job_model import Job # pylint: disable=import-error
from app.core.config import settings # pylint: disable=import-error

_last_fetch = 0
_request_lock = asyncio.Lock()
_driver = None


def get_driver():
    """Initialize and return an undetected Chrome WebDriver instance."""
    global _driver
    if _driver is None:
        options = uc.ChromeOptions()
        
        # Add necessary arguments
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(f"user-agent={settings.USER_AGENT}")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--start-maximized")
        options.add_argument("--disable-blink-features=AutomationControlled")
        
        # Initialize undetected chromedriver
        _driver = uc.Chrome(
            options=options,
            driver_executable_path=None,
            browser_executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            use_subprocess=True,
            version_main=None
        )
        
        # Additional stealth measures
        _driver.execute_cdp_cmd('Network.setUserAgentOverride', {
            "userAgent": settings.USER_AGENT
        })
        
        # Random viewport size
        viewport_width = random.randint(1366, 1920)
        viewport_height = random.randint(768, 1080)
        _driver.set_window_size(viewport_width, viewport_height)
    
    return _driver


async def scrape_ziprecruiter(query: str, location: Optional[str] = None, max_results: int = 20) -> List[Job]:
    """
    Scrape ZipRecruiter jobs using Selenium with undetected-chromedriver.
    
    Args:
        query: Job search query (e.g., "python developer")
        location: Job location (e.g., "remote", "New York, NY")
        max_results: Maximum number of jobs to return
    
    Returns:
        List of Job objects
    """
    global _last_fetch
    
    # Rate limiting
    async with _request_lock:
        now = time.monotonic()
        wait = settings.MIN_DELAY - (now - _last_fetch)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_fetch = time.monotonic()
    
    # Run the scraping in a thread pool to avoid blocking
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _scrape_sync, query, location, max_results)


def _scrape_sync(query: str, location: Optional[str], max_results: int) -> List[Job]:
    """Synchronous scraping function to run in thread pool."""
    driver = get_driver()
    jobs = []
    
    try:
        # Build ZipRecruiter search URL
        base_url = "https://www.ziprecruiter.com/jobs-search"
        
        # ZipRecruiter uses a different URL structure
        search_query = query.replace(' ', '-')
        if location:
            location_query = location.replace(' ', '-').replace(',', '')
            url = f"https://www.ziprecruiter.com/candidate/search?search={query}&location={location}"
        else:
            url = f"https://www.ziprecruiter.com/candidate/search?search={query}"
        
        print(f"Navigating to: {url}")
        
        # Navigate to the page
        driver.get(url)
        
        # Random delay to appear more human-like
        time.sleep(random.uniform(4, 7))
        
        # Try to wait for job results
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "article.job_result, div.job_content"))
            )
        except:
            pass  # Continue even if wait times out
        
        # Get page source and parse with BeautifulSoup
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # ZipRecruiter uses article tags with class 'job_result' or similar
        job_cards = soup.find_all('article', class_=lambda x: x and 'job' in x.lower())
        
        # Try alternative selectors if first one doesn't work
        if not job_cards:
            job_cards = soup.find_all('div', class_=lambda x: x and 'job_result' in x.lower())
        
        if not job_cards:
            job_cards = soup.find_all('div', attrs={'data-job-id': True})
        
        # Last resort: look for any article or div containing job info
        if not job_cards:
            job_cards = soup.find_all('article') or soup.find_all('div', class_=lambda x: x and 'job' in x.lower() if x else False)
        
        print(f"Found {len(job_cards)} potential job cards")
        
        for card in job_cards[:max_results]:
            try:
                # Extract job title - ZipRecruiter specific selectors
                title = None
                title_elem = (card.find('h2') or 
                             card.find('a', class_=lambda x: 'job' in x.lower() if x else False) or
                             card.find('span', attrs={'data-job-title': True}))
                
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    if not title or len(title) < 3:
                        title = title_elem.get('aria-label', '') or title_elem.get('title', '')
                
                if not title or len(title) < 3:
                    continue
                
                # Extract company name - check multiple locations
                company = None
                # Try finding company by aria-label or data attributes
                company_link = card.find('a', attrs={'aria-label': lambda x: 'hiring' in x.lower() if x else False})
                if company_link:
                    company = company_link.get_text(strip=True)
                
                if not company:
                    # Try all text elements that might contain company
                    for elem in card.find_all(['a', 'span', 'div']):
                        text = elem.get_text(strip=True)
                        # Company names are usually short and don't contain job-related keywords
                        if text and 3 < len(text) < 50 and text != title:
                            if not any(keyword in text.lower() for keyword in ['days ago', 'hours ago', 'just posted', 'salary', '$', 'remote', 'full', 'part']):
                                company = text
                                break
                
                # Extract location - ZipRecruiter shows location in various ways
                job_location = None
                # Look for location by common patterns
                for elem in card.find_all(['a', 'span', 'div', 'li']):
                    text = elem.get_text(strip=True)
                    # Locations usually contain city/state or "Remote"
                    if text and (',' in text or 'remote' in text.lower() or any(state in text for state in ['CA', 'NY', 'TX', 'FL'])):
                        if len(text) < 100 and text != title:  # Avoid long text
                            job_location = text
                            break
                
                # Extract job description/snippet
                description = None
                # ZipRecruiter puts job snippet in various places
                snippet_elem = (card.find('p') or
                               card.find('div', class_=lambda x: x and any(keyword in x.lower() for keyword in ['snippet', 'description', 'summary']) if x else False))
                if snippet_elem:
                    description = snippet_elem.get_text(strip=True)
                    # Truncate if too long
                    if description and len(description) > 500:
                        description = description[:500] + '...'
                
                # Extract job URL
                job_url = None
                link_elem = card.find('a', href=True)
                if link_elem and 'href' in link_elem.attrs:
                    href = link_elem['href']
                    if href.startswith('/'):
                        job_url = f"https://www.ziprecruiter.com{href}"
                    elif href.startswith('http'):
                        job_url = href
                
                jobs.append(Job(
                    title=title,
                    company=company,
                    location=job_location,
                    description=description,
                    url=job_url
                ))
                
                print(f"Extracted job: {title} at {company or 'Unknown'} in {job_location or 'Unknown'}")
                    
            except Exception as e:
                print(f"Error parsing job card: {e}")
                continue
        
        if not jobs:
            # Save debug info
            print("No jobs found. Saving debug info...")
            with open('/tmp/ziprecruiter_debug.html', 'w', encoding='utf-8') as f:
                f.write(driver.page_source)
            raise Exception(
                f"No jobs found. Found {len(job_cards)} potential job cards but couldn't extract data. "
                f"Page source saved to /tmp/ziprecruiter_debug.html"
            )
        
    except Exception as e:
        raise Exception(f"Failed to scrape ZipRecruiter: {str(e)}")
    
    return jobs


def close_driver():
    """Close the WebDriver when done."""
    global _driver
    if _driver:
        _driver.quit()
        _driver = None

