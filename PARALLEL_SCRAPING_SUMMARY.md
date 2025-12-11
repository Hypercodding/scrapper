# Can You Run Indeed and Generic Scraping in Parallel? - Quick Answer

## ✅ YES, You Can!

Indeed scraper and Generic scraper use **separate Chrome instances**, so they CAN run in parallel on the same Railway container.

**BUT** - there are important resource considerations depending on your Railway plan.

---

## 📊 What Happens When Running in Parallel

### Resource Usage (Per Scraping Operation)

| Resource | Single Scrape | Parallel (Both) |
|----------|--------------|-----------------|
| RAM | 300-500 MB | 600-1000 MB |
| CPU | ~50% of 1 core | ~100% of 1 core |
| Chrome Processes | 3-5 | 6-10 |

### By Railway Plan

| Railway Plan | RAM | Can Run Parallel? | Recommendation |
|--------------|-----|-------------------|----------------|
| **Free** (512 MB) | 512 MB | ❌ **NO** - Will likely crash | Sequential only |
| **Hobby** (1 GB) | 1 GB | ⚠️ **Risky** - May cause OOM | Sequential recommended |
| **Pro** (8 GB) | 8 GB | ✅ **YES** - Safe | 2-3 parallel OK |
| **Enterprise** (32+ GB) | 32+ GB | ✅ **YES** - Fully supported | 5+ parallel OK |

---

## 🎯 Your Specific Situation

### Current State (No Throttling)

**What Happens Now**:
```bash
# If you make these two requests simultaneously:
curl "https://your-app/api/jobs?query=python&max_results=10" &
curl "https://your-app/api/jobs/scrape-url-get?url=https://example.com/careers" &
```

✅ **Both will start immediately**
- Each gets its own Chrome browser
- Memory usage: ~600-1000 MB combined
- They run independently
- Both browsers close after completion (thanks to the pool fix)

⚠️ **On Free/Hobby Railway**:
- High risk of Out of Memory (OOM)
- May cause deployment crash
- **Not recommended**

✅ **On Pro/Enterprise Railway**:
- Will work fine
- Both complete successfully
- Resources cleaned up properly

---

## 🚀 How to Check Your Railway Plan

```bash
# Method 1: Check Railway dashboard
# Go to your project → Settings → View plan

# Method 2: Check via API (if throttle module is deployed)
curl https://your-app.railway.app/api/health/throttle-status
```

**Response will show**:
```json
{
  "railway_plan": "pro",  // or "free", "hobby", "enterprise"
  "max_concurrent": 2,
  "available_slots": 2,
  "active_scrapes": 0
}
```

---

## 💡 Recommended Approach

### If on Free/Hobby Plan: Sequential Execution

**DON'T** make parallel requests. Instead, coordinate on your end:

```python
# Client-side coordination (your application)
async def get_all_jobs():
    # Run Indeed first
    indeed_jobs = await fetch_indeed_jobs("python developer")
    
    # Then run Generic
    generic_jobs = await fetch_generic_jobs("https://example.com/careers")
    
    # Combine results
    return indeed_jobs + generic_jobs
```

**OR** use a queue system to ensure sequential execution.

---

### If on Pro/Enterprise Plan: Optional Throttling

You CAN run parallel without issues, but throttling is still recommended to prevent accidental overload.

**I've created a throttle module for you**: `app/core/throttle.py`

**To enable it** (optional but recommended):

1. **Deploy the throttle module** (already created)

2. **Add to Indeed scraper** (`app/services/indeed_selenium_service.py`):
   
   Add import:
   ```python
   from app.core.throttle import get_scraping_throttle
   ```
   
   Wrap main scraping function:
   ```python
   async def scrape_indeed_selenium(...):
       async with get_scraping_throttle():
           # existing scraping code
           ...
   ```

3. **Add to Generic scraper** (`app/services/generic_career_scraper.py`):
   
   Add import:
   ```python
   from app.core.throttle import get_scraping_throttle
   ```
   
   Wrap main scraping function:
   ```python
   async def scrape_generic_career_page(...):
       async with get_scraping_throttle():
           # existing scraping code
           ...
   ```

4. **Check throttle status**:
   ```bash
   curl https://your-app/api/health/throttle-status
   ```

---

## 🧪 Testing Parallel Execution

### Test 1: Check Current State

```bash
# Check health before any scraping
curl https://your-app.railway.app/api/health/detailed
```

**Expected**: `"chrome_processes": 0`

---

### Test 2: Try Parallel Execution

```bash
# Start both simultaneously
curl "https://your-app/api/jobs?query=python&max_results=5" > indeed.json &
curl "https://your-app/api/jobs/scrape-url-get?url=https://example.com/careers&max_results=5" > generic.json &

# Wait for both to complete
wait

# Check health immediately after
curl https://your-app/api/health/detailed
```

**On Free/Hobby** - You'll likely see:
- One or both requests fail
- Error: "Cannot allocate memory"
- Or deployment crashes/restarts

**On Pro/Enterprise** - You'll see:
- Both requests succeed
- Process count returns to 0-2 after completion
- No errors

---

### Test 3: Monitor During Execution

```bash
# In terminal 1 - start monitoring
watch -n 1 'curl -s https://your-app/api/health/detailed | jq'

# In terminal 2 - run parallel scrapes
curl "https://your-app/api/jobs?query=python&max_results=10" &
curl "https://your-app/api/jobs/scrape-url-get?url=https://example.com/careers" &
```

**Watch the process count**:
- Should peak at 6-10 during execution
- Should drop back to 0 after completion
- If it stays high or reaches 20+, run cleanup

---

## 🔍 Current State of Your System

**With the pool fixes I implemented**:
- ✅ Browsers ALWAYS close after scraping (even in parallel)
- ✅ No "pool full" errors will occur
- ✅ Each scraping operation cleans up after itself
- ✅ Health monitoring works for parallel operations

**What you still need to consider**:
- ⚠️ Railway plan memory limits
- ⚠️ Coordinating requests if on Free/Hobby plan
- ✅ Optional throttling for Pro/Enterprise (recommended but not required)

---

## 📋 Decision Matrix

| Your Railway Plan | Parallel Scraping Status | What You Should Do |
|-------------------|--------------------------|-------------------|
| **Free (512 MB)** | ❌ Not safe | Use sequential execution on client side |
| **Hobby (1 GB)** | ⚠️ Risky | Use sequential execution on client side |
| **Pro (8 GB)** | ✅ Safe | Can use parallel, optional throttling recommended |
| **Enterprise (32+ GB)** | ✅ Fully supported | Can use parallel freely, throttling optional |

---

## 🎯 Quick Recommendations

### Scenario 1: You're on Free/Hobby Plan
**Action**: Coordinate requests on your client side - don't make parallel API calls

```bash
# DON'T do this:
curl .../api/jobs?query=python &
curl .../api/jobs/scrape-url-get?url=... &

# DO this instead:
curl .../api/jobs?query=python
# Wait for completion, then:
curl .../api/jobs/scrape-url-get?url=...
```

---

### Scenario 2: You're on Pro/Enterprise Plan
**Action**: Parallel works fine as-is, optionally enable throttling for safety

```bash
# These will work fine simultaneously:
curl .../api/jobs?query=python &
curl .../api/jobs/scrape-url-get?url=... &

# Check status:
curl .../api/health/detailed
curl .../api/health/throttle-status
```

---

### Scenario 3: You Don't Know Your Plan
**Action**: Check first, then decide

```bash
# Check throttle status (shows detected plan)
curl https://your-app/api/health/throttle-status

# Or check Railway dashboard
```

---

## 📊 Summary

| Question | Answer |
|----------|--------|
| **Can they run in parallel?** | ✅ YES - technically possible |
| **Do they share Chrome instances?** | ❌ NO - separate browsers |
| **Will pool exhaustion happen?** | ❌ NO - fixed with cleanup changes |
| **Will OOM crash happen?** | ⚠️ MAYBE - depends on Railway plan |
| **Should I do it?** | Depends on your Railway plan (see table above) |
| **Is throttling required?** | ❌ NO - but recommended for Pro+ |

---

## 📞 Next Steps

1. **Check your Railway plan** (Dashboard or via API)

2. **If Free/Hobby**: 
   - ❌ Don't run parallel
   - ✅ Coordinate sequential on client side
   - ✅ Current setup will work fine sequentially

3. **If Pro/Enterprise**:
   - ✅ Can run parallel safely
   - ✅ Works as-is with current fixes
   - 💡 Optional: Enable throttling for extra safety (see guide)

4. **Test it**: Use the test commands above to verify

5. **Monitor**: Use health endpoints to watch system state

---

## 🎉 Bottom Line

**YES, you can run Indeed and Generic scraping in parallel**, and with my fixes, browsers will always close properly so you won't get "pool full" errors.

**HOWEVER**, whether you *should* run them in parallel depends entirely on your Railway plan's memory limits.

The system is now robust enough to handle parallel execution without crashes **IF** you have sufficient RAM. On smaller plans, the OS will kill your process due to OOM before pool exhaustion becomes an issue.

**My recommendation**: Check your plan, test conservatively, and use the monitoring endpoints I created to ensure everything stays healthy! 🚀

