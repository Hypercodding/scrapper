# Indeed Scraping Solutions

Indeed has very strong anti-scraping measures. Here are your realistic options:

## ⚠️ Current Status
The Selenium approach we just implemented **should** work, but Indeed is actively blocking automated browsers. The code is in place at `/api/jobs`, but you may experience:
- CAPTCHA challenges
- IP blocking
- Empty results
- Rate limiting

## 🎯 Recommended Solutions (In Order of Success Rate)

### 1. **Use ScraperAPI** (Easiest & Most Reliable)
ScraperAPI handles proxies, headers, and CAPTCHAs for you.

```python
# Add to requirements.txt
requests==2.31.0

# New service file: app/services/scraperapi_indeed.py
import httpx
from typing import List, Optional
from app.models.job_model import Job

SCRAPER_API_KEY = "your_api_key_here"  # Get free tier at scraperapi.com

async def scrape_indeed_scraperapi(query: str, location: Optional[str] = None):
    url = f"https://www.indeed.com/jobs?q={query}"
    if location:
        url += f"&l={location}"
    
    api_url = f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}&url={url}"
    
    async with httpx.AsyncClient() as client:
        response = await client.get(api_url, timeout=60)
        # Parse response.text with BeautifulSoup
        ...
```

**Cost**: Free tier: 1,000 requests/month
**Success Rate**: ~95%

---

### 2. **Use Bright Data (formerly Luminati)** (Most Professional)
Enterprise-grade scraping infrastructure.

```python
# Use their web unlocker or scraping browser
# They handle all anti-bot measures
```

**Cost**: Pay-as-you-go starting at $500/month
**Success Rate**: ~99%

---

### 3. **Use Residential Proxies with Selenium** (DIY Advanced)
Rotate through residential IPs to avoid blocks.

Services:
- Bright Data Residential Proxies
- Smartproxy
- Oxylabs

```python
# Add proxy to Chrome options
chrome_options.add_argument(f'--proxy-server={proxy_url}')
```

**Cost**: ~$50-200/month for proxies
**Success Rate**: ~70-80%

---

### 4. **Playwright Instead of Selenium** (Better Stealth)
Playwright has better anti-detection capabilities.

```bash
pip install playwright
python -m playwright install chromium
```

```python
from playwright.async_api import async_playwright

async def scrape_with_playwright(query, location):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0...",
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()
        
        # Add stealth mode
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined})
        """)
        
        await page.goto(f"https://www.indeed.com/jobs?q={query}&l={location}")
        await page.wait_for_timeout(3000)
        
        content = await page.content()
        # Parse with BeautifulSoup
        ...
```

**Cost**: Free
**Success Rate**: ~50-60%

---

### 5. **Use Indeed's Official Publisher API** (Best Legal Option)
Register at: https://www.indeed.com/publisher

**Pros**:
- Legal and reliable
- No blocking
- Good documentation

**Cons**:
- Requires approval
- Usage limits
- May have fees

---

### 6. **Use Job Aggregator APIs**
Instead of scraping Indeed directly, use APIs that aggregate from multiple sources:

- **Adzuna API**: https://developer.adzuna.com/
- **The Muse API**: https://www.themuse.com/developers/api/v2
- **JSearch (RapidAPI)**: Aggregates Indeed, LinkedIn, Glassdoor
  ```
  https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
  ```

**Cost**: Free tiers available
**Success Rate**: 100% (they handle the scraping)

---

## 🔧 Improving Current Selenium Implementation

If you want to stick with the current Selenium approach, try these improvements:

### A. Add Random Delays
```python
import random
time.sleep(random.uniform(3, 7))  # Random 3-7 second delay
```

### B. Use Undetected ChromeDriver
```bash
pip install undetected-chromedriver
```

```python
import undetected_chromedriver as uc

driver = uc.Chrome(options=chrome_options)
```

### C. Rotate User Agents
```python
user_agents = [
    "Mozilla/5.0 (Macintosh...",
    "Mozilla/5.0 (Windows NT...",
    # ... more user agents
]
chrome_options.add_argument(f"user-agent={random.choice(user_agents)}")
```

### D. Handle CAPTCHAs
- Use 2Captcha or Anti-Captcha service
- Manually solve during development

---

## 💡 My Recommendation

**For Production**: Use ScraperAPI or JSearch (RapidAPI)
- Costs $29-50/month
- Saves weeks of development time
- 95%+ success rate
- No maintenance headaches

**For Learning/Testing**: Try Playwright with undetected-chromedriver
- Free
- Good learning experience
- 50-60% success rate

**Long Term**: Apply for Indeed's official API
- Most sustainable
- Legal compliance
- Best for business use

---

## 📝 Legal Note

Web scraping can violate Terms of Service. Indeed's ToS prohibits automated access. Consider:
1. Using official APIs when available
2. Respecting robots.txt
3. Not overwhelming servers with requests
4. Consulting with legal counsel for commercial use

---

## 🚀 Quick Win: Use RapidAPI's JSearch Right Now

This is the fastest way to get Indeed jobs without scraping:

```python
# app/services/jsearch_service.py
import httpx
from typing import List, Optional
from app.models.job_model import Job

RAPIDAPI_KEY = "your_key_here"  # Get free key at rapidapi.com

async def search_jobs_jsearch(query: str, location: Optional[str] = None):
    url = "https://jsearch.p.rapidapi.com/search"
    
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
    }
    
    params = {
        "query": f"{query} in {location}" if location else query,
        "page": "1",
        "num_pages": "1"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, params=params)
        data = response.json()
        
        jobs = []
        for job in data.get('data', []):
            jobs.append(Job(
                title=job.get('job_title'),
                company=job.get('employer_name'),
                location=job.get('job_city') or job.get('job_state'),
                description=job.get('job_description'),
                url=job.get('job_apply_link')
            ))
        return jobs
```

**Free Tier**: 2,500 requests/month
**Includes**: Indeed, LinkedIn, Glassdoor, ZipRecruiter
**Success Rate**: 100%

Would you like me to implement any of these solutions?

