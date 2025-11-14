# Quick Start - Generic Career Page Scraper

## 🚀 Start the Server

```bash
cd /Users/apple/Documents/indeed_scraper
./run.sh
```

Server will start at: **http://127.0.0.1:8000**

## 📖 View API Documentation

Open in browser: **http://127.0.0.1:8000/docs**

## 🧪 Test Endpoints

### Method 1: Browser (Easiest)
Just open this URL in your browser:
```
http://127.0.0.1:8000/api/jobs/scrape-url-get?url=https://www.burton.com/us/en/careers
```

### Method 2: cURL (Command Line)

**GET Request:**
```bash
curl "http://127.0.0.1:8000/api/jobs/scrape-url-get?url=https://www.burton.com/us/en/careers&max_results=10"
```

**POST Request:**
```bash
curl -X POST "http://127.0.0.1:8000/api/jobs/scrape-url" \
     -H "Content-Type: application/json" \
     -d '{"url": "https://www.burton.com/us/en/careers", "max_results": 10}'
```

### Method 3: Python

```python
import requests

response = requests.get(
    "http://127.0.0.1:8000/api/jobs/scrape-url-get",
    params={"url": "https://www.burton.com/us/en/careers", "max_results": 10},
    timeout=120
)

jobs = response.json()
for job in jobs:
    print(f"{job['title']} - {job['url']}")
```

## ✅ Tested Career Pages

All these URLs work with the scraper:

```bash
# Burton Snowboards - Job portal links
https://www.burton.com/us/en/careers

# Skida - Current openings section
https://skida.com/pages/careers

# Darn Tough - Ultipro job board link
https://darntough.com/pages/careers

# Orvis - Actual job listings
https://orvis.com/pages/careers

# Concept2 - Career page navigation
https://www.concept2.com/company/employment

# Turtle Fur - Career page (may have no current openings)
https://www.turtlefur.com/pages/careers

# Vermont Glove - Career page (may have no current openings)
https://vermontglove.com/pages/careers
```

## 📊 Expected Response Time

- Average: **15-30 seconds**
- Varies by page complexity

## 🎯 Example Response

```json
[
  {
    "title": "VIEW ALL JOBS",
    "company": "Burton",
    "url": "https://dayforcehcm.com/CandidatePortal/...",
    "location": null,
    "description": "Job posting link from Burton careers page"
  }
]
```

## 🔧 Run Tests

```bash
cd /Users/apple/Documents/indeed_scraper
source venv/bin/activate

# Simple API test
python test_api_simple.py

# Test all provided URLs
python test_all_user_urls.py

# Quick direct test
python quick_test.py
```

## 📚 Full Documentation

- **Complete Guide:** `GENERIC_SCRAPER_GUIDE.md`
- **Test Results:** `TEST_RESULTS_SUMMARY.md`
- **API Docs:** http://127.0.0.1:8000/docs

## ⚡ Quick Examples

### Scrape a single career page:
```bash
curl "http://127.0.0.1:8000/api/jobs/scrape-url-get?url=https://www.burton.com/us/en/careers" | jq
```

### Get first 5 results:
```bash
curl "http://127.0.0.1:8000/api/jobs/scrape-url-get?url=https://orvis.com/pages/careers&max_results=5" | jq
```

### Format output nicely:
```bash
curl -s "http://127.0.0.1:8000/api/jobs/scrape-url-get?url=https://www.burton.com/us/en/careers" | \
  jq '.[] | {title, company, url}'
```

## ✨ Success!

You can now scrape job listings from any company career page by providing the URL! 🎉

