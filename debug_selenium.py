#!/usr/bin/env python3
"""Debug script to see what Indeed returns when using Selenium"""

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import time

# Setup Chrome options
chrome_options = Options()
chrome_options.binary_location = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
chrome_options.add_argument("--headless")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--disable-blink-features=AutomationControlled")
chrome_options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
chrome_options.add_argument("--window-size=1920,1080")
chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
chrome_options.add_experimental_option('useAutomationExtension', False)

# Create driver
service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=chrome_options)

# Make browser appear more human-like
driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

try:
    # Navigate to Indeed
    url = "https://www.indeed.com/jobs?q=python+developer&l=remote"
    print(f"Navigating to: {url}")
    driver.get(url)
    
    # Wait for page to load
    time.sleep(5)
    
    # Get page title
    print(f"Page title: {driver.title}")
    
    # Save page source to file
    with open('indeed_page_source.html', 'w', encoding='utf-8') as f:
        f.write(driver.page_source)
    print("Page source saved to indeed_page_source.html")
    
    # Take a screenshot
    driver.save_screenshot('indeed_screenshot.png')
    print("Screenshot saved to indeed_screenshot.png")
    
    # Check for specific elements
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    
    # Look for common Indeed selectors
    job_beacons = soup.find_all('div', class_='job_seen_beacon')
    result_content = soup.find_all('td', class_='resultContent')
    job_titles = soup.find_all('a', class_=lambda x: x and 'jcs-JobTitle' in x if x else False)
    
    print(f"\nFound {len(job_beacons)} job_seen_beacon elements")
    print(f"Found {len(result_content)} resultContent elements")
    print(f"Found {len(job_titles)} job title links")
    
    # Print first 500 chars of body
    body = soup.find('body')
    if body:
        text = body.get_text()[:500]
        print(f"\nFirst 500 chars of page:\n{text}")
    
finally:
    driver.quit()
    print("\nDriver closed")

