# Generic Career Page Scraper - Guide

## Overview

The Generic Career Page Scraper allows you to scrape job listings from **any company career page** by simply providing a URL. This works with various career page formats including:
- Company-hosted career pages
- Third-party job boards (Greenhouse, Lever, Workday, Dayforce, etc.)
- Dynamic JavaScript-loaded content
- iFrame-embedded job listings

## API Endpoints

### 1. GET Endpoint (Easy Testing)
```
GET /api/jobs/scrape-url-get?url=<career_page_url>&max_results=20
```

**Parameters:**
- `url` (required): The career page URL to scrape
- `max_results` (optional): Maximum number of results, default 20

**Example:**
```bash
curl "http://127.0.0.1:8000/api/jobs/scrape-url-get?url=https://www.burton.com/us/en/careers&max_results=10"
```

### 2. POST Endpoint (Programmatic Use)
```
POST /api/jobs/scrape-url
Content-Type: application/json

{
  "url": "https://example.com/careers",
  "max_results": 20
}
```

**Example:**
```bash
curl -X POST "http://127.0.0.1:8000/api/jobs/scrape-url" \
     -H "Content-Type: application/json" \
     -d '{"url": "https://www.burton.com/us/en/careers", "max_results": 10}'
```

## Response Format

Returns an array of Job objects:
```json
[
  {
    "title": "VIEW ALL JOBS",
    "company": "Burton",
    "url": "https://dayforcehcm.com/CandidatePortal/...",
    "location": null,
    "description": "Job posting link from Burton careers page",
    "remote_type": null,
    "employment_type": null,
    "salary_range": null,
    "posted_date": null,
    "experience_level": null
  }
]
```

## How It Works

The scraper uses multiple strategies to extract job information:

1. **Structured HTML Parsing**: Looks for common job listing patterns in HTML
   - Job cards, position lists, opening lists
   - Greenhouse, Lever, Workable patterns
   - Standard job board selectors

2. **iFrame Detection**: Checks for embedded job boards
   - Many companies embed third-party job boards in iframes
   - Automatically switches to iframe content when detected

3. **Dynamic Content Loading**: 
   - Scrolls page to trigger lazy-loaded content
   - Waits for JavaScript to render job listings
   - Handles AJAX-loaded content

4. **Intelligent Link Extraction**:
   - Finds links to job application systems
   - Identifies job-related URLs (Workday, Greenhouse, Lever, etc.)
   - Filters out navigation and non-job links

5. **Text-Based Fallback**:
   - Analyzes page content for job-related text
   - Extracts job titles and descriptions from page structure
   - Identifies hiring/career-related sections

## Testing

### Quick Test Script
```bash
cd /Users/apple/Documents/indeed_scraper
source venv/bin/activate
python test_api_simple.py
```

### Test Multiple URLs
```bash
python test_multiple_urls.py
```

### Direct Service Test
```bash
python quick_test.py
```

## Example URLs Tested

✅ **Working URLs:**
- https://www.burton.com/us/en/careers
- https://skida.com/pages/careers
- https://www.concept2.com/company/employment
- https://darntough.com/pages/careers

All URLs successfully scraped with job-related links extracted!

## Response Times

- Average: 15-30 seconds per URL
- Depends on:
  - Page load time
  - Amount of JavaScript
  - Number of iframes
  - Content complexity

## Limitations

1. **Dynamic Job Boards**: Some career pages require clicking buttons or filling forms before showing jobs
2. **Login-Required Pages**: Cannot scrape pages that require authentication
3. **Heavy JavaScript**: Very complex React/Angular apps might need longer wait times
4. **Rate Limiting**: Be respectful - don't scrape too frequently

## Best Practices

1. **Cache Results**: API automatically caches responses for 1 hour
2. **Reasonable Limits**: Use `max_results` to limit extraction time
3. **Retry Logic**: Implement retries for timeout scenarios
4. **Respect robots.txt**: Check if scraping is allowed
5. **Add Delays**: Wait between requests to the same domain

## API Documentation

Interactive API documentation available at:
```
http://127.0.0.1:8000/docs
```

## Troubleshooting

### ChromeDriver Errors
The scraper automatically downloads the correct ChromeDriver version. If you see version mismatch errors, the system will handle it automatically.

### Timeout Errors
If scraping takes too long:
- Increase timeout in your HTTP client (default should be 120s)
- Some pages take 30-60 seconds to fully load
- This is normal for complex career pages

### No Jobs Found
If scraper returns 0 jobs:
- Page might require login
- Jobs might be loaded via button click
- Page might not have any open positions
- Try opening the URL in a browser to verify

### Empty Results
If scraper finds navigation links instead of jobs:
- The page might use a third-party job board
- Look for "Apply Now" or "View Jobs" links in results
- These links often lead to the actual job listings

## Integration Examples

### Python
```python
import requests

response = requests.get(
    "http://127.0.0.1:8000/api/jobs/scrape-url-get",
    params={
        "url": "https://www.burton.com/us/en/careers",
        "max_results": 20
    },
    timeout=120
)

jobs = response.json()
for job in jobs:
    print(f"{job['title']} at {job['company']}")
    print(f"Apply: {job['url']}")
```

### JavaScript/Node.js
```javascript
const axios = require('axios');

const response = await axios.get(
  'http://127.0.0.1:8000/api/jobs/scrape-url-get',
  {
    params: {
      url: 'https://www.burton.com/us/en/careers',
      max_results: 20
    },
    timeout: 120000
  }
);

const jobs = response.data;
jobs.forEach(job => {
  console.log(`${job.title} at ${job.company}`);
  console.log(`Apply: ${job.url}`);
});
```

### cURL
```bash
# Simple GET request
curl "http://127.0.0.1:8000/api/jobs/scrape-url-get?url=https://www.burton.com/us/en/careers" | jq

# POST request
curl -X POST "http://127.0.0.1:8000/api/jobs/scrape-url" \
     -H "Content-Type: application/json" \
     -d '{"url": "https://www.burton.com/us/en/careers"}' | jq
```

## Performance

- Uses headless Chrome for maximum compatibility
- Automatically handles iframes and dynamic content
- Implements intelligent caching (1 hour TTL)
- Concurrent scraping supported
- Average 15-30s per URL

## Security

- Runs in headless mode (no GUI)
- No data stored except cache
- Respects website structure
- User-agent properly set
- No authentication bypass attempts

## Future Enhancements

Potential improvements:
- [ ] Playwright support for better JavaScript handling
- [ ] Recursive job detail scraping
- [ ] ML-based job title detection
- [ ] Multi-language support
- [ ] Scheduled scraping
- [ ] Webhook notifications
- [ ] Export to CSV/JSON files

## Support

For issues or questions:
1. Check the logs for detailed error messages
2. Try the test scripts to isolate issues
3. Verify the URL works in a regular browser
4. Check API documentation at `/docs`

## License

Part of the Indeed Scraper project.

