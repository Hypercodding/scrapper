# Parallel Scraping Guide - Indeed + Generic Scraper

## ✅ Short Answer: YES, You Can Run Them in Parallel

**But you need to understand the resource implications and limitations.**

---

## 🔍 How It Works

### Current Implementation Analysis

**Indeed Scraper** (`app/services/indeed_selenium_service.py`):
- Uses a **global driver** (`_driver` variable)
- Managed by `get_driver()` function
- Reuses same Chrome instance (with rotation logic)

**Generic Scraper** (`app/services/generic_career_scraper.py`):
- Creates **local driver** instance
- Each scrape gets its own Chrome browser
- Independent from Indeed scraper

**Result**: They use **separate Chrome instances** → Can run in parallel ✅

---

## 📊 Resource Requirements

### Per Scraping Operation

| Resource | Indeed Scraper | Generic Scraper | Both Parallel |
|----------|---------------|-----------------|---------------|
| **RAM** | 300-500 MB | 300-500 MB | 600-1000 MB |
| **CPU** | 1 core | 1 core | 2 cores |
| **Chrome Processes** | 3-5 | 3-5 | 6-10 |

### Railway Plan Recommendations

| Plan | RAM | Parallel Scraping? | Max Concurrent |
|------|-----|-------------------|----------------|
| **Free** | 512 MB | ⚠️ NOT Recommended | 1 (sequential) |
| **Hobby** | 1 GB | ⚠️ Risky | 1 (sequential) |
| **Pro** | 8 GB | ✅ Yes | 2-3 |
| **Enterprise** | 32+ GB | ✅ Yes | 5+ |

---

## ⚠️ Risks of Parallel Execution

### 1. **Memory Exhaustion**
- Each Chrome instance uses 300-500 MB
- Railway free/hobby tiers have limited RAM
- **Risk**: Out of Memory (OOM) crash

**Symptoms**:
```
Error: Cannot allocate memory
Chrome process terminated unexpectedly
```

### 2. **CPU Throttling**
- Chrome is CPU-intensive during page rendering
- **Risk**: Slow response times, timeouts

### 3. **Resource Contention**
- Multiple browsers compete for system resources
- **Risk**: Both scrapes become slower

### 4. **Connection Pool Strain**
- Even with fixes, parallel operations stress connection pools
- **Risk**: Increased error rates

---

## ✅ Safe Parallel Execution Strategy

### Option 1: Sequential (Safest - Recommended for Free/Hobby)

**Don't run parallel** - use queue system:

```python
# Pseudocode
jobs = []

# Run Indeed first
indeed_jobs = await scrape_indeed_selenium(...)
jobs.extend(indeed_jobs)

# Then run Generic
generic_jobs = await scrape_generic_career_page(...)
jobs.extend(generic_jobs)

return jobs
```

**Pros**: No resource conflicts, predictable behavior
**Cons**: Takes longer (sequential)

---

### Option 2: Controlled Parallelism (For Pro/Enterprise Plans)

Add throttling to limit concurrent operations:

**Step 1**: Create throttle helper (add to `app/core/throttle.py`):

```python
import asyncio
from typing import Optional

class ScrapingThrottle:
    """Limit concurrent scraping operations"""
    
    def __init__(self, max_concurrent: int = 2):
        self.semaphore = asyncio.Semaphore(max_concurrent)
    
    async def __aenter__(self):
        await self.semaphore.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.semaphore.release()

# Global throttle - limits to 2 concurrent scrapes
_throttle = ScrapingThrottle(max_concurrent=2)

def get_throttle():
    return _throttle
```

**Step 2**: Use in endpoints (modify `app/routes/job_routes.py`):

```python
from app.core.throttle import get_throttle

@router.get("/jobs")
async def get_jobs(...):
    # Acquire throttle before scraping
    async with get_throttle():
        jobs = await scrape_indeed_selenium(...)
    return jobs

@router.get("/jobs/scrape-url-get")
async def scrape_career_page_url_get(...):
    # Acquire throttle before scraping
    async with get_throttle():
        jobs = await scrape_generic_career_page(...)
    return jobs
```

**Result**: Maximum 2 scrapes running at once, others wait in queue

---

### Option 3: Manual Coordination (Current State)

Currently, parallel requests will work but could cause issues:

```bash
# These will run simultaneously (both get Chrome instances)
curl "https://your-app/api/jobs?query=python" &
curl "https://your-app/api/jobs/scrape-url-get?url=https://example.com/careers" &
```

**What happens**:
- ✅ Both start scraping
- ⚠️ RAM usage doubles (600-1000 MB)
- ⚠️ CPU usage increases significantly
- ⚠️ May hit resource limits on free/hobby plans

---

## 🎯 Recommended Approach by Railway Plan

### Free Tier (512 MB RAM)
```bash
❌ DO NOT run parallel scraping
✅ Use sequential approach
✅ Keep max_results low (5-10)
✅ Monitor health endpoint frequently
```

**Example Safe Usage**:
```bash
# Run one at a time
curl "https://your-app/api/jobs?query=developer&max_results=5"
# Wait for completion, then:
curl "https://your-app/api/jobs/scrape-url-get?url=https://example.com/careers&max_results=5"
```

---

### Hobby Plan (1 GB RAM)
```bash
⚠️ Parallel scraping possible but risky
✅ Better to use sequential
✅ If parallel needed, limit to 2 concurrent max
✅ Keep max_results moderate (10-20)
```

**If You Must Run Parallel**:
- Implement throttling (Option 2 above)
- Monitor health endpoint after each operation
- Run emergency cleanup if process count > 10

---

### Pro Plan (8 GB RAM)
```bash
✅ Parallel scraping fully supported
✅ Can handle 2-3 concurrent operations safely
✅ Normal max_results (20+) is fine
```

**Optimal Setup**:
- Implement throttling with `max_concurrent=2`
- Monitor health endpoint periodically
- Run cleanup if process count > 15

---

### Enterprise Plan (32+ GB RAM)
```bash
✅ Full parallel support
✅ Can handle 5+ concurrent operations
✅ High max_results (50+) supported
```

---

## 🔧 Implementation: Add Throttling (Recommended)

### Step 1: Create Throttle Module

```bash
touch /Users/latif/Documents/scrapper/app/core/throttle.py
```

```python
# app/core/throttle.py
import asyncio
import os

class ScrapingThrottle:
    """Limit concurrent scraping to prevent resource exhaustion"""
    
    def __init__(self, max_concurrent: int = 2):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.max_concurrent = max_concurrent
    
    async def __aenter__(self):
        await self.semaphore.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.semaphore.release()
    
    @property
    def available_slots(self) -> int:
        return self.semaphore._value

# Auto-detect Railway plan and set appropriate limit
def get_max_concurrent_from_env() -> int:
    """Detect Railway plan and return safe concurrent limit"""
    # Check if running on Railway
    if not os.environ.get("RAILWAY_ENVIRONMENT"):
        return 2  # Local development
    
    # Check available memory (Railway sets this)
    memory_limit = os.environ.get("RAILWAY_SERVICE_MEMORY_LIMIT_MB")
    if memory_limit:
        memory_mb = int(memory_limit)
        if memory_mb <= 512:
            return 1  # Free tier
        elif memory_mb <= 1024:
            return 1  # Hobby tier
        elif memory_mb <= 8192:
            return 2  # Pro tier
        else:
            return 5  # Enterprise tier
    
    return 1  # Conservative default

# Global throttle instance
_throttle = ScrapingThrottle(max_concurrent=get_max_concurrent_from_env())

def get_throttle() -> ScrapingThrottle:
    return _throttle
```

### Step 2: Update Scraping Services

**Modify `app/services/indeed_selenium_service.py`**:

```python
# Add import at top
from app.core.throttle import get_throttle

# Modify scrape_indeed_selenium function
async def scrape_indeed_selenium(...):
    """Enhanced Indeed scraper..."""
    
    # Acquire throttle to limit concurrent scraping
    async with get_throttle():
        global _last_fetch
        
        # ... rest of existing code ...
```

**Modify `app/services/generic_career_scraper.py`**:

```python
# Add import at top
from app.core.throttle import get_throttle

# Modify scrape_generic_career_page function
async def scrape_generic_career_page(...):
    """Universal scraper for any career page"""
    
    # Acquire throttle to limit concurrent scraping
    async with get_throttle():
        # ... rest of existing code ...
```

### Step 3: Add Throttle Status Endpoint

**Add to `app/routes/job_routes.py`**:

```python
@router.get("/health/throttle-status")
async def get_throttle_status():
    """Check scraping throttle status"""
    from app.core.throttle import get_throttle
    
    throttle = get_throttle()
    return {
        "max_concurrent": throttle.max_concurrent,
        "available_slots": throttle.available_slots,
        "active_scrapes": throttle.max_concurrent - throttle.available_slots,
        "message": f"Can run {throttle.available_slots} more concurrent scrape(s)"
    }
```

---

## 📊 Monitoring Parallel Execution

### Before Running Parallel Scrapes

```bash
# Check current system state
curl https://your-app/api/health/detailed
curl https://your-app/api/health/throttle-status  # (if throttling implemented)
```

**Expected**: Low process count, available throttle slots

### During Parallel Execution

```bash
# Monitor in real-time
watch -n 2 'curl -s https://your-app/api/health/detailed | jq'
```

**Watch for**:
- Process count staying manageable (< 15)
- No "warning" or "critical" status

### After Parallel Execution

```bash
# Verify cleanup happened
curl https://your-app/api/health/detailed
```

**Expected**: Process count back to 0 or very low

---

## 🚨 Troubleshooting Parallel Issues

### Issue: Out of Memory Errors

**Symptoms**:
```
Error: Cannot allocate memory
Chrome process killed
Deployment crashed
```

**Solutions**:
1. Stop running parallel scrapes
2. Implement throttling (max_concurrent=1)
3. Upgrade Railway plan
4. Reduce max_results

### Issue: Both Scrapes Slow/Timeout

**Symptoms**:
- Both operations take 2x longer
- Timeout errors increase

**Solutions**:
1. Reduce max_results on both
2. Implement throttling
3. Run sequential instead

### Issue: High Chrome Process Count

**Symptoms**:
```
curl /api/health/detailed
{
  "status": "critical",
  "chrome_processes": 25
}
```

**Solution**:
```bash
# Emergency cleanup
curl -X POST https://your-app/api/health/cleanup

# Then run scrapes sequentially
```

---

## 📝 Testing Parallel Execution

### Test 1: Sequential Baseline

```bash
# Test sequential (always safe)
time curl "https://your-app/api/jobs?query=python&max_results=5"
# Note the time

time curl "https://your-app/api/jobs/scrape-url-get?url=https://example.com/careers&max_results=5"
# Note the time

# Check health
curl https://your-app/api/health/detailed
```

**Expected**: Low process count, predictable timing

### Test 2: Parallel Execution

```bash
# Start both simultaneously
(
  time curl "https://your-app/api/jobs?query=python&max_results=5" > indeed.json &
  time curl "https://your-app/api/jobs/scrape-url-get?url=https://example.com/careers&max_results=5" > generic.json &
  wait
)

# Immediately check health
curl https://your-app/api/health/detailed
```

**Expected on Pro Plan**:
- Both complete (may take longer than sequential)
- Process count returns to low after completion
- No OOM errors

**Expected on Free/Hobby Plan**:
- May cause OOM crash
- Deployment may restart
- NOT RECOMMENDED

---

## 🎯 Final Recommendation

### For Your Current Railway Setup:

**If Free/Hobby Plan (< 1GB RAM)**:
```bash
❌ DO NOT run parallel scraping
✅ Use sequential approach
✅ Implement request queuing if needed
```

**If Pro Plan (8GB RAM)**:
```bash
✅ Parallel scraping is safe
✅ Implement throttling (max_concurrent=2)
✅ Monitor health endpoint
```

**If Enterprise Plan (32GB+ RAM)**:
```bash
✅ Full parallel support
✅ Throttle with max_concurrent=5
✅ Can handle high load
```

---

## 🔄 Quick Decision Matrix

| Scenario | Free/Hobby | Pro | Enterprise |
|----------|-----------|-----|------------|
| **1 Indeed scrape** | ✅ Safe | ✅ Safe | ✅ Safe |
| **1 Generic scrape** | ✅ Safe | ✅ Safe | ✅ Safe |
| **Both sequential** | ✅ Safe | ✅ Safe | ✅ Safe |
| **Both parallel** | ❌ Risky | ✅ Safe* | ✅ Safe |
| **3+ parallel** | ❌ No | ❌ Risky | ✅ Safe |

*With throttling implemented

---

## 📞 Summary

**YES, you CAN run Indeed and Generic scraping in parallel**, but:

1. **Resource awareness is critical** - each Chrome instance uses 300-500 MB
2. **Railway plan matters** - Free/Hobby should avoid parallel, Pro+ can handle it
3. **Throttling is recommended** - prevents accidental resource exhaustion
4. **Monitoring is essential** - use health endpoints to track system state
5. **Cleanup still works** - each scrape closes its browser regardless

**With the fixes I implemented, the "pool full" error won't happen even with parallel scraping** - but you may hit memory limits on smaller Railway plans.

**My recommendation**: Start sequential, upgrade to parallel only when you upgrade to Railway Pro or higher.

