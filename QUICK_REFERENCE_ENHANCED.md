# Quick Reference - Enhanced Career Scraper

## ✅ Problem Solved!

**Before:** Getting useless navigation links like "VIEW ALL JOBS"  
**Now:** Getting actual job titles like "Paid Media Specialist" ✨

## 🚀 Quick Examples

### 1. Get All Jobs
```bash
curl "http://127.0.0.1:8000/api/jobs/scrape-url-get?url=https://www.burton.com/us/en/careers"
```

### 2. Search for Designer Jobs
```bash
curl "http://127.0.0.1:8000/api/jobs/scrape-url-get?url=https://www.burton.com/us/en/careers&search_query=designer"
```

### 3. Search for Engineering Jobs
```bash
curl "http://127.0.0.1:8000/api/jobs/scrape-url-get?url=https://company.com/careers&search_query=engineer"
```

### 4. Search for Remote Jobs
```bash
curl "http://127.0.0.1:8000/api/jobs/scrape-url-get?url=https://company.com/careers&search_query=remote"
```

## 📝 API Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `url` | ✅ Yes | - | Career page URL |
| `max_results` | ❌ No | 20 | Max jobs to return |
| `search_query` | ❌ No | null | Keyword to filter jobs |

## 🎯 Example Responses

### Without Search
```json
[
  {
    "title": "Paid Media Specialist",
    "company": "Dayforcehcm",
    "url": "https://dayforcehcm.com/.../jobs/4467"
  },
  {
    "title": "Talent Development Coordinator",
    "company": "Dayforcehcm",
    "url": "https://dayforcehcm.com/.../jobs/4454"
  }
]
```

### With Search (search_query="designer")
```json
[
  {
    "title": "Temporary-Apparel Technical Designer",
    "company": "Dayforcehcm",
    "url": "https://dayforcehcm.com/.../jobs/4400"
  }
]
```

## 🐍 Python Usage

```python
import requests

# Get all jobs
response = requests.get(
    "http://127.0.0.1:8000/api/jobs/scrape-url-get",
    params={
        "url": "https://www.burton.com/us/en/careers",
        "max_results": 10
    },
    timeout=120
)

jobs = response.json()
print(f"Found {len(jobs)} jobs")

# Search for specific jobs
response = requests.get(
    "http://127.0.0.1:8000/api/jobs/scrape-url-get",
    params={
        "url": "https://www.burton.com/us/en/careers",
        "search_query": "designer"
    },
    timeout=120
)

designer_jobs = response.json()
for job in designer_jobs:
    print(f"- {job['title']}")
```

## ⚡ What Changed

### Before Enhancement
- ❌ Returned navigation links ("VIEW ALL JOBS")
- ❌ No actual job titles
- ❌ No search feature
- ❌ Not very useful

### After Enhancement
- ✅ Automatically follows job board links
- ✅ Extracts actual job titles
- ✅ Search/filter by keyword
- ✅ Very useful! 🎉

## 📊 Supported Job Boards

Automatically detects and follows:
- Dayforce, Greenhouse, Lever, Workday, Ultipro, BambooHR, Jobvite, Taleo, iCIMS, SmartRecruiters, Jazz, RecruiterBox

## 📚 Full Documentation

- **`ENHANCED_FEATURES_GUIDE.md`** - Complete enhanced features guide
- **`GENERIC_SCRAPER_GUIDE.md`** - Original documentation
- **http://127.0.0.1:8000/docs** - Interactive API docs

## 🎯 Common Use Cases

### Find All Designer Jobs
```bash
curl "http://127.0.0.1:8000/api/jobs/scrape-url-get?url=https://company.com/careers&search_query=designer"
```

### Find Engineering Jobs
```bash
curl "http://127.0.0.1:8000/api/jobs/scrape-url-get?url=https://company.com/careers&search_query=engineer"
```

### Find Remote Positions
```bash
curl "http://127.0.0.1:8000/api/jobs/scrape-url-get?url=https://company.com/careers&search_query=remote"
```

### Find Senior Roles
```bash
curl "http://127.0.0.1:8000/api/jobs/scrape-url-get?url=https://company.com/careers&search_query=senior"
```

---

**Server:** `http://127.0.0.1:8000`  
**Status:** ✅ Running and Enhanced!

