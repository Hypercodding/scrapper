# Generic Scraper - Unlimited Job Count by Default (Pagination Still Limited)

## Update Summary
Changed the generic scraper to scrape **ALL available jobs from up to 5 pages** by default when `max_results` parameter is not specified.

**Important**: Pagination is ALWAYS limited to maximum 5 pages for performance and resource management, regardless of max_results setting.

## Changes Made

### 1. Updated Function Signatures
Changed `max_results` parameter from `int = 20` to `Optional[int] = None` in all public scraping functions:

#### `scrape_generic_career_page()`
```python
# Before:
async def scrape_generic_career_page(
    url: str,
    max_results: int = 20,  # ❌ Limited to 20 jobs
    ...
)

# After:
async def scrape_generic_career_page(
    url: str,
    max_results: Optional[int] = None,  # ✅ None = get ALL jobs
    ...
)
```

#### `scrape_multiple_career_pages()`
```python
# Before:
async def scrape_multiple_career_pages(
    urls: List[str],
    max_results_per_url: int = 20,  # ❌ Limited to 20 jobs per URL
    ...
)

# After:
async def scrape_multiple_career_pages(
    urls: List[str],
    max_results_per_url: Optional[int] = None,  # ✅ None = get ALL jobs per URL
    ...
)
```

#### `scrape_with_retry_strategies()`
```python
# Before:
async def scrape_with_retry_strategies(
    url: str,
    max_results: int = 20,  # ❌ Limited to 20 jobs
    ...
)

# After:
async def scrape_with_retry_strategies(
    url: str,
    max_results: Optional[int] = None,  # ✅ None = get ALL jobs
    ...
)
```

### 2. Added Unlimited Scraping Logic

When `max_results` is `None`, it's converted to a large number (999999) internally:

```python
# Set max_results to unlimited if not specified
if max_results is None:
    max_results = 999999  # Large number to get all jobs
    print(f"ℹ️  No max_results specified - will scrape ALL available jobs")
```

### 3. Pagination Logic (ALWAYS Limited to 5 Pages)

**IMPORTANT**: Pagination is ALWAYS limited to 5 pages maximum, regardless of `max_results` setting:

```python
# Always limit pagination to 5 pages
max_pagination_pages = 5

# Numbered pagination
pages_to_scrape = list(range(start_page, min(total_pages + 1, max_pagination_pages + 1)))
print(f"  📚 Will scrape pages {start_page} to {min(total_pages, max_pagination_pages)} (limited to {max_pagination_pages} pages)")

# Next-only pagination
print(f"  ➡️ Next-only pagination: Will scrape up to {max_pagination_pages} pages")
while len(jobs) < max_results and page_num < (max_pagination_pages + 1):
    # Scrape page...
```

**Why limit pagination?**
- Prevents excessive resource usage
- Avoids timeout issues on large job boards
- Maintains reasonable scraping times
- Prevents memory exhaustion

### 4. Enhanced Console Output

The scraper now clearly indicates whether it's in unlimited or limited mode:

**Unlimited Job Count Mode (default)**:
```
ℹ️  No max_results specified - will scrape ALL available jobs
Current jobs: 45, Target: ALL
⚠️  Pagination limited to maximum 5 pages
📚 Will scrape pages 2 to 5 (limited to 5 pages)
```

**Limited Job Count Mode (when max_results is specified)**:
```
Current jobs: 15, Target: 50
⚠️  Pagination limited to maximum 5 pages
📚 Will scrape pages 2 to 5 (limited to 5 pages)
```

**Note**: Pagination is ALWAYS limited to 5 pages in both modes.

## Usage Examples

### Example 1: Get ALL Jobs from Up To 5 Pages (Default Behavior)
```python
from app.services.generic_career_scraper import scrape_generic_career_page

# No max_results specified - will get ALL jobs from up to 5 pages
jobs = await scrape_generic_career_page(
    url="https://careers.example.com/jobs"
)
# Output: ℹ️  No max_results specified - will scrape ALL available jobs
# Output: ⚠️  Pagination limited to maximum 5 pages
# Result: All available jobs from up to 5 pages
```

### Example 2: Limit to Specific Number
```python
# Specify max_results to limit
jobs = await scrape_generic_career_page(
    url="https://careers.example.com/jobs",
    max_results=50  # Limit to 50 jobs
)
# Output: Current jobs: X, Target: 50
# Output: ⚠️  Pagination limited to maximum 5 pages
# Result: Up to 50 jobs from up to 5 pages
```

### Example 3: Explicit Unlimited Job Count
```python
# Can also explicitly pass None
jobs = await scrape_generic_career_page(
    url="https://careers.example.com/jobs",
    max_results=None  # Explicitly unlimited job count
)
# Output: ℹ️  No max_results specified - will scrape ALL available jobs
# Output: ⚠️  Pagination limited to maximum 5 pages
# Result: All available jobs from up to 5 pages
```

### Example 4: Multiple URLs with Unlimited Job Count
```python
from app.services.generic_career_scraper import scrape_multiple_career_pages

# Scrape multiple URLs, getting ALL jobs from up to 5 pages each
jobs = await scrape_multiple_career_pages(
    urls=[
        "https://careers.company1.com/jobs",
        "https://careers.company2.com/jobs"
    ]
)
# Output: ℹ️  No max_results_per_url specified - will scrape ALL available jobs per URL
# Output: ⚠️  Pagination limited to maximum 5 pages (per URL)
# Result: All jobs from up to 5 pages per URL, combined
```

## Behavior Comparison

| Scenario | Before | After |
|----------|--------|-------|
| No `max_results` specified | Gets 20 jobs, 5 pages max | ✅ Gets ALL jobs, 5 pages max |
| `max_results=50` | Gets 50 jobs, 5 pages max | Gets 50 jobs, 5 pages max |
| `max_results=None` | N/A (would error) | ✅ Gets ALL jobs, 5 pages max |
| Large number (e.g., 1000) | Gets 1000 jobs, 5 pages max | Gets 1000 jobs, 5 pages max |

**Key Point**: Pagination is ALWAYS limited to 5 pages for all scenarios.

## Migration Notes

### Breaking Changes
⚠️ **This is a breaking change for existing code that relies on the default limit of 20 jobs.**

If you have code that depends on the old behavior (limiting to 20 jobs), you must now explicitly specify:
```python
# Old code (no longer works the same way):
jobs = await scrape_generic_career_page(url)  # Used to get 20 jobs

# New code (to maintain old behavior):
jobs = await scrape_generic_career_page(url, max_results=20)  # Explicitly limit to 20
```

### Recommended Approach

1. **Default behavior is now safe** - Gets all jobs from up to 5 pages:
```python
jobs = await scrape_generic_career_page(url)  # Safe, limited to 5 pages
```

2. **Specify `max_results`** when you want fewer jobs:
```python
jobs = await scrape_generic_career_page(url, max_results=20)  # Get ~20 jobs
```

3. **Pagination is always limited** - You'll get at most jobs from 5 pages:
```python
# Even with large max_results, only 5 pages will be scraped
jobs = await scrape_generic_career_page(url, max_results=1000)  # Up to 1000 jobs from 5 pages
```

## Performance Considerations

### Scraping Time (Always Limited to 5 Pages)
- **1-2 pages**: ~10-20 seconds
- **3-4 pages**: ~30-45 seconds  
- **5 pages (max)**: ~1-1.5 minutes

**Note**: Times are consistent since pagination is always limited to 5 pages max.

### Memory Usage
- Each job object uses ~1-2 KB of memory
- Typical scrape (50-150 jobs): ~100-300 KB
- Large scrape from 5 pages (~300 jobs): ~300-600 KB

### Why 5 Pages Max?
1. **Prevents timeouts**: Keeps scraping time reasonable
2. **Avoids resource exhaustion**: Limits memory and CPU usage
3. **Better reliability**: Reduces chance of detection/blocking
4. **Reasonable coverage**: Most job boards show 10-20 jobs per page = 50-100 total jobs

## Testing

### Test 1: Verify Unlimited Job Count (5 Pages Max)
```python
# Should scrape all jobs from up to 5 pages
jobs = await scrape_generic_career_page("https://careers.example.com/jobs")
print(f"Total jobs scraped: {len(jobs)}")

# Check console output for:
# ✅ "No max_results specified - will scrape ALL available jobs"
# ✅ "Pagination limited to maximum 5 pages"
```

### Test 2: Verify Limited Scraping Still Works
```python
# Should stop at 30 jobs or 5 pages
jobs = await scrape_generic_career_page(
    "https://careers.example.com/jobs", 
    max_results=30
)
print(f"Total jobs scraped: {len(jobs)}")  # Should be ≤ 30

# Check console output for:
# ✅ "Target: 30"
# ✅ "Pagination limited to maximum 5 pages"
```

### Test 3: Verify Explicit None Works
```python
# Should work same as no parameter
jobs = await scrape_generic_career_page(
    "https://careers.example.com/jobs",
    max_results=None
)
print(f"Total jobs scraped: {len(jobs)}")

# Check console output for:
# ✅ "No max_results specified - will scrape ALL available jobs"
```

## Summary

✅ **Default behavior changed**: No limit on job count (within 5 pages)  
✅ **Pagination ALWAYS limited**: Maximum 5 pages for all scenarios  
✅ **Clear console output**: Shows job target and pagination limit  
✅ **Backward compatible**: Can still specify limits explicitly  
✅ **Safe by default**: 5-page limit prevents resource issues  
⚠️ **Breaking change**: Old default of 20 jobs no longer applies

**To maintain old behavior**, always specify `max_results=20` explicitly.

**To use new behavior**, simply omit `max_results` parameter or pass `None`.

**Important**: Regardless of `max_results`, pagination is ALWAYS limited to 5 pages maximum.

