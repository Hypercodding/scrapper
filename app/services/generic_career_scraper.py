"""
Generic Career Page Scraper - Works with any company career page
"""
import asyncio
import re
from typing import List, Optional
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from app.models.job_model import Job  # pylint: disable=import-error
from app.core.config import settings  # pylint: disable=import-error
import undetected_chromedriver as uc


def extract_job_info_from_element(element, base_url: str, company_name: Optional[str] = None) -> Optional[Job]:
    """Extract job information from a single HTML element"""
    try:
        soup = BeautifulSoup(element.get_attribute('outerHTML'), 'html.parser')
        
        # Try to find job title - look in headers and specific classes first
        title = None
        # Look for title in specific job title classes
        title_elem = soup.find(['h2', 'h3', 'h4', 'div', 'span'], class_=lambda x: x and ('title' in x.lower() or 'job' in x.lower()))
        if title_elem:
            title = title_elem.get_text(strip=True)
        
        # Fallback to headers and links
        if not title:
            for tag in soup.find_all(['h2', 'h3', 'h4', 'a']):
                text = tag.get_text(strip=True)
                if text and len(text) > 5 and len(text) < 150:  # Reasonable title length
                    # Skip if it looks like navigation text
                    skip_keywords = ['shop', 'collections', 'new arrivals', 'featured', 'sign up', 'login', 'cart']
                    if not any(keyword in text.lower() for keyword in skip_keywords):
                        title = text
                        break
        
        if not title or len(title) < 5:
            return None
        
        # Try to find job URL
        job_url = None
        link = soup.find('a', href=True)
        if link:
            href = link.get('href')
            job_url = urljoin(base_url, href)
        
        # Extract location
        location = None
        location_patterns = [
            r'\b(Remote|Hybrid|On-site|Onsite)\b',
            r'\b([A-Z][a-z]+,\s*[A-Z]{2})\b',  # City, ST
            r'\b([A-Z][a-z\s]+,\s*[A-Z][a-z]+)\b'  # City, Country
        ]
        text = soup.get_text()
        for pattern in location_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                location = match.group(0)
                break
        
        # Extract job type / remote type
        remote_type = None
        if re.search(r'\bremote\b', text, re.IGNORECASE):
            remote_type = "Remote"
        elif re.search(r'\bhybrid\b', text, re.IGNORECASE):
            remote_type = "Hybrid"
        elif re.search(r'\bon-?site\b', text, re.IGNORECASE):
            remote_type = "On-site"
        
        # Extract employment type
        employment_type = None
        if re.search(r'\bfull[- ]time\b', text, re.IGNORECASE):
            employment_type = "Full-time"
        elif re.search(r'\bpart[- ]time\b', text, re.IGNORECASE):
            employment_type = "Part-time"
        elif re.search(r'\bcontract\b', text, re.IGNORECASE):
            employment_type = "Contract"
        elif re.search(r'\bintern(ship)?\b', text, re.IGNORECASE):
            employment_type = "Internship"
        
        # Get description
        description = soup.get_text(separator=' ', strip=True)
        if len(description) > 500:
            description = description[:500] + "..."
        
        return Job(
            title=title,
            company=company_name,
            location=location,
            description=description,
            url=job_url,
            remote_type=remote_type,
            employment_type=employment_type
        )
    except (AttributeError, TypeError, ValueError) as e:
        print(f"Error extracting job info: {e}")
        return None


def extract_company_name_from_url(url: str) -> str:
    """Extract company name from URL"""
    parsed = urlparse(url)
    domain = parsed.netloc
    # Remove www. and common TLDs
    name = domain.replace('www.', '').split('.')[0]
    return name.title()


async def scrape_job_board_page(driver, url: str, max_results: int) -> List[Job]:
    """
    Scrape jobs from a job board page (like Greenhouse, Lever, Dayforce)
    
    Args:
        driver: Selenium WebDriver instance
        url: Job board URL
        max_results: Maximum jobs to extract
        
    Returns:
        List of Job objects
    """
    jobs = []
    company_name = extract_company_name_from_url(url)
    
    try:
        print(f"Following job board link: {url}")
        driver.get(url)
        await asyncio.sleep(3)
        
        # Scroll to load content
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
        await asyncio.sleep(1)
        
        # Common job board selectors
        job_board_selectors = [
            '[data-qa="opening"]',  # Greenhouse
            '.opening',
            '.job-title',
            '.position-title',
            '[class*="job-title"]',
            '[class*="position-title"]',
            'a[href*="job"]',
            'a[href*="position"]',
            'div[class*="job-item"]',
            'li[class*="job"]',
        ]
        
        elements_found = []
        for selector in job_board_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    elements_found.extend(elements)
                    if len(elements_found) >= max_results * 2:
                        break
            except Exception:  # pylint: disable=broad-except
                continue
        
        print(f"Found {len(elements_found)} potential job elements on job board")
        
        # Extract unique jobs
        seen_titles = set()
        skipped_count = 0
        for element in elements_found[:max_results * 3]:
            job = extract_job_info_from_element(element, url, company_name)
            if job:
                if job.title in seen_titles:
                    skipped_count += 1
                    continue
                    
                # Skip navigation-like titles and very short titles
                nav_keywords = ['view all', 'working in', 'log in', 'sign up', 'careers', 'read more']
                if any(kw in job.title.lower() for kw in nav_keywords) or len(job.title) < 5:
                    print(f"  Skipping: {job.title}")
                    skipped_count += 1
                    continue
                    
                seen_titles.add(job.title)
                jobs.append(job)
                print(f"  ✓ Extracted: {job.title}")
                
                if len(jobs) >= max_results:
                    break
        
        print(f"Extracted {len(jobs)} unique jobs (skipped {skipped_count} duplicates/nav items)")
        return jobs
        
    except Exception as e:
        print(f"Error scraping job board {url}: {e}")
        return []


async def scrape_generic_career_page(url: str, max_results: int = 20, search_query: Optional[str] = None) -> List[Job]:
    """
    Scrape jobs from a generic career page URL
    
    Args:
        url: The career page URL to scrape
        max_results: Maximum number of jobs to return
        search_query: Optional search term to filter jobs (e.g., "software engineer")
        
    Returns:
        List of Job objects
    """
    jobs = []
    driver = None
    
    try:
        # Setup Chrome options
        chrome_options = Options()
        chrome_options.add_argument('--headless=new')  # Use new headless mode
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument(f'user-agent={settings.USER_AGENT}')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--disable-software-rasterizer')
        chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
        
        # Use regular Chrome driver with webdriver_manager for automatic version matching
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        print(f"Loading career page: {url}")
        driver.get(url)
        
        # Wait for page to load initially
        await asyncio.sleep(2)
        
        # Scroll down to trigger lazy loading
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
        await asyncio.sleep(1)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        await asyncio.sleep(2)
        
        # Check for iframes (many job boards use them)
        iframes = driver.find_elements(By.TAG_NAME, 'iframe')
        if iframes:
            print(f"Found {len(iframes)} iframe(s), checking for job content...")
            for iframe in iframes:
                try:
                    driver.switch_to.frame(iframe)
                    # Check if iframe has job-related content
                    iframe_source = driver.page_source
                    if any(keyword in iframe_source.lower() for keyword in ['job', 'position', 'career', 'opening', 'apply']):
                        print("Found job-related iframe content")
                        break
                except Exception:  # pylint: disable=broad-except
                    pass
                finally:
                    driver.switch_to.default_content()
        
        # Additional wait for dynamic content
        await asyncio.sleep(2)
        
        # Try to find common job listing patterns
        # More specific selectors first, generic ones last
        job_selectors = [
            # Specific job posting selectors
            '[class*="job-list"]',
            '[class*="job-item"]',
            '[class*="job-post"]',
            '[class*="job-card"]',
            '[class*="position-list"]',
            '[class*="opening-list"]',
            '[class*="vacancy"]',
            '[data-job-id]',
            '[data-job]',
            'div[class*="job"]:not([class*="nav"]):not([class*="header"]):not([class*="footer"])',
            'article[class*="job"]',
            'li[class*="job"]',
            'tr[class*="job"]',
            # Greenhouse, Lever, Workable patterns
            '.opening',
            '.position',
            '.job-listing',
            '.career-listing',
        ]
        
        # Extract company name from URL
        company_name = extract_company_name_from_url(url)
        
        # Try each selector
        elements_found = []
        for selector in job_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements and len(elements) > 0:
                    elements_found.extend(elements)
            except Exception:  # pylint: disable=broad-except
                continue
        
        # Remove duplicates by position
        unique_elements = []
        seen_positions = set()
        for elem in elements_found:
            try:
                pos = elem.location
                pos_key = (pos['x'], pos['y'])
                if pos_key not in seen_positions:
                    seen_positions.add(pos_key)
                    unique_elements.append(elem)
            except Exception:  # pylint: disable=broad-except
                continue
        
        print(f"Found {len(unique_elements)} potential job elements")
        
        # Extract job information from elements
        for element in unique_elements[:max_results * 2]:  # Get more than needed to filter
            job = extract_job_info_from_element(element, url, company_name)
            if job:
                jobs.append(job)
                if len(jobs) >= max_results:
                    break
        
        # If no structured jobs found, try to extract from page text
        if not jobs:
            print("No structured jobs found, trying intelligent text extraction...")
            page_source = driver.page_source
            soup = BeautifulSoup(page_source, 'html.parser')
            
            # Look for common job board indicators
            job_indicators = soup.find_all(text=re.compile(r'(no.{0,10}(openings?|positions?|jobs?|vacancies)|currently.{0,10}hiring|we.{0,10}re.{0,10}hiring|join.{0,10}team|open.{0,10}positions?)', re.IGNORECASE))
            
            if job_indicators:
                print(f"Found job-related text: {job_indicators[0][:100]}")
            
            # Look for links that might lead to job listings
            all_links = soup.find_all('a', href=True)
            job_links = []
            for link in all_links:
                href = link.get('href', '')
                text = link.get_text(strip=True)
                # Look for job-related links
                if any(keyword in href.lower() + text.lower() for keyword in ['job', 'career', 'position', 'opening', 'apply', 'workday', 'greenhouse', 'lever', 'bamboohr', 'applicant']):
                    if text and len(text) > 3 and len(text) < 150:
                        full_url = urljoin(url, href)
                        job_links.append({'title': text, 'url': full_url})
            
            # If we found job-related links, try to follow them to get actual jobs
            if job_links:
                print(f"Found {len(job_links)} job-related links")
                
                # Check if links point to job boards - if so, follow them
                job_board_patterns = ['greenhouse', 'lever', 'workday', 'dayforce', 'ultipro', 
                                     'bamboohr', 'applicantstack', 'jobvite', 'taleo', 
                                     'icims', 'smartrecruiters', 'jazz', 'recruiterbox']
                
                board_link = None
                for jl in job_links[:3]:  # Check first 3 links
                    if any(pattern in jl['url'].lower() for pattern in job_board_patterns):
                        board_link = jl['url']
                        print(f"Detected job board link: {board_link}")
                        break
                
                # If we found a job board link, follow it to get actual jobs
                if board_link:
                    board_jobs = await scrape_job_board_page(driver, board_link, max_results)
                    if board_jobs:
                        jobs.extend(board_jobs)
                        print(f"Extracted {len(board_jobs)} jobs from job board")
                    else:
                        # Fallback to navigation links if board scraping failed
                        print("Job board scraping failed, returning navigation links")
                        for jl in job_links[:max_results]:
                            job = Job(
                                title=jl['title'],
                                company=company_name,
                                url=jl['url'],
                                description=f"Job board link - visit to see openings"
                            )
                            jobs.append(job)
                else:
                    # No job board detected, return the links we found
                    for jl in job_links[:max_results]:
                        job = Job(
                            title=jl['title'],
                            company=company_name,
                            url=jl['url'],
                            description=f"Career page section - visit to see openings"
                        )
                        jobs.append(job)
            else:
                # Fallback: look for any text that might be job titles
                print("No job links found, analyzing page text...")
                # Remove script and style elements
                for script in soup(["script", "style", "nav", "header", "footer"]):
                    script.decompose()
                
                # Look specifically for paragraphs and sections that mention hiring/jobs
                job_sections = soup.find_all(['div', 'section', 'p'], text=re.compile(r'(hiring|position|job|career|opening|vacancy)', re.IGNORECASE))
                
                if job_sections:
                    for section in job_sections[:max_results]:
                        title_text = section.get_text(strip=True)[:100]
                        if title_text and len(title_text) > 10:
                            job = Job(
                                title=title_text,
                                company=company_name,
                                url=url,
                                description="Extracted from careers page content"
                            )
                            jobs.append(job)
        
        # Filter by search query if provided
        if search_query and jobs:
            print(f"Filtering jobs by search query: '{search_query}'")
            print(f"Jobs before filtering: {len(jobs)}")
            
            # Debug: Print first few job titles
            for i, job in enumerate(jobs[:3]):
                print(f"  Job {i+1}: {job.title}")
            
            search_lower = search_query.lower()
            filtered_jobs = []
            for job in jobs:
                # Search in title, description, and other fields
                # Make sure to handle None values properly
                searchable_text = ' '.join(filter(None, [
                    job.title,
                    job.description,
                    job.location,
                    job.employment_type,
                    job.remote_type,
                    job.job_type
                ])).lower()
                
                # Also check individual fields for better matching
                title_match = job.title and search_lower in job.title.lower()
                desc_match = job.description and search_lower in job.description.lower()
                location_match = job.location and search_lower in job.location.lower()
                
                if search_lower in searchable_text or title_match or desc_match or location_match:
                    filtered_jobs.append(job)
                    print(f"  ✓ Matched: {job.title}")
            
            print(f"Found {len(filtered_jobs)} jobs matching '{search_query}'")
            jobs = filtered_jobs
        
        print(f"Successfully extracted {len(jobs)} jobs from {url}")
        
    except Exception as e:
        print(f"Error scraping {url}: {e}")
        raise
    finally:
        if driver:
            driver.quit()
    
    return jobs[:max_results]  # Ensure we don't exceed max_results

