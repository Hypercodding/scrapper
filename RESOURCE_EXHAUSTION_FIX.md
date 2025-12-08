# Chrome Driver Resource Exhaustion Fix

## Problem Summary

You were encountering `BlockingIOError: [Errno 11] Resource temporarily unavailable` when trying to initialize ChromeDriver. This error occurs when the system has exhausted its resources to create new processes.

### Root Causes:
1. **Missing psutil library** - Zombie process cleanup wasn't working
2. **Zombie Chrome/ChromeDriver processes** - Not being cleaned up between runs
3. **Driver kept alive between requests** - Chrome stayed running even when idle
4. **System resource limits** - Too many processes/file descriptors open
5. **Insufficient cleanup on failure** - Failed initialization attempts left processes behind

## What Was Fixed

### 1. Added psutil Dependency (requirements.txt)
```
psutil==6.1.0
```
This enables proper process management and cleanup.

### 2. Enhanced cleanup_zombie_processes() Function
- **Primary method**: Uses psutil for reliable process cleanup
- **Fallback method**: Uses subprocess commands (pgrep/ps/kill) when psutil unavailable
- **Aggressive cleanup**: Can force-kill stubborn processes
- **Better logging**: Shows exactly what's being cleaned up

### 3. Added cleanup_global_driver() Function
- Properly cleans up the global driver instance
- Terminates/kills service processes if needed
- Resets global state
- Waits for resources to be released

### 4. Improved Driver Initialization
- **Pre-initialization cleanup**: Aggressively cleans zombies before creating new driver
- **Process count monitoring**: Warns when too many Chrome processes detected
- **Better retry logic**: 
  - Detects BlockingIOError specifically
  - Waits 10 seconds (vs 5) when resource exhaustion detected
  - Uses aggressive cleanup on retries
- **Better error messages**: Provides specific guidance for different error types

### 5. Added Missing Imports
- `subprocess` - For fallback process cleanup
- `platform` - For OS detection in cleanup logic

### 6. **AUTOMATIC CLEANUP AFTER EACH SCRAPE** ⭐ **THIS SOLVES YOUR CONCERN!**
- **NEW**: `CLEANUP_DRIVER_AFTER_SCRAPE` setting (default: `True`)
  - When enabled, Chrome is **completely killed** after every scraping operation
  - **No Chrome processes left running when idle**
  - Prevents resource accumulation over time
  - **Answers your question**: YES, every Chrome resource is killed after completing the process
- **NEW**: `DRIVER_IDLE_TIMEOUT` setting (default: 60 seconds)
  - Automatically kills driver if idle for more than specified seconds
  - Set to `0` to keep driver alive indefinitely
- **NEW**: Startup/Shutdown hooks in `main.py`
  - Cleans up leftover processes on application startup
  - Ensures all Chrome resources freed on shutdown
- **Result**: **Zero Chrome processes when not actively scraping**

**Behavior Flow with Default Settings:**
```
Request → Launch Chrome → Scrape → Kill Chrome → Free All Resources
         (no existing)              (success)   (driver.quit() + 
                                                 process.kill() +
                                                 zombie cleanup)

Next Request → Launch Chrome again → Scrape → Kill Chrome → Free All Resources
```

**With CLEANUP_DRIVER_AFTER_SCRAPE=True, you get:**
- ✅ **0 Chrome processes** when idle
- ✅ **All resources freed** after each scrape
- ✅ **No resource buildup**
- ✅ **Clean slate for every request**

## Configuration Options

You now have **full control** over Chrome resource management via `.env` file or environment variables:

### 1. `CLEANUP_DRIVER_AFTER_SCRAPE` (default: `True`)
**Recommended: Keep as `True`**

```bash
# .env file
CLEANUP_DRIVER_AFTER_SCRAPE=True  # Kill Chrome after every scrape (recommended)
CLEANUP_DRIVER_AFTER_SCRAPE=False # Keep Chrome alive for reuse (faster but uses resources)
```

**When `True` (Recommended)**:
- ✅ Chrome is **completely killed** after each scraping operation
- ✅ Zero Chrome processes when not scraping
- ✅ No resource buildup over time
- ✅ Prevents resource exhaustion
- ⚠️ Slightly slower (needs to restart Chrome for each request)

**When `False`**:
- ⚡ Faster response times (reuses same Chrome instance)
- ⚠️ Chrome stays running between requests
- ⚠️ Resources held even when idle
- ⚠️ Risk of resource exhaustion with many requests
- 💡 Use with `DRIVER_IDLE_TIMEOUT` to auto-cleanup idle drivers

### 2. `DRIVER_IDLE_TIMEOUT` (default: `60` seconds)
**Only applies when `CLEANUP_DRIVER_AFTER_SCRAPE=False`**

```bash
# .env file
DRIVER_IDLE_TIMEOUT=60   # Kill driver after 60 seconds of inactivity
DRIVER_IDLE_TIMEOUT=300  # Kill driver after 5 minutes of inactivity
DRIVER_IDLE_TIMEOUT=0    # Keep driver alive indefinitely (not recommended)
```

**Example Configurations**:

```bash
# Configuration 1: Maximum Resource Conservation (Recommended for Production)
CLEANUP_DRIVER_AFTER_SCRAPE=True
DRIVER_IDLE_TIMEOUT=60  # This is ignored when CLEANUP_DRIVER_AFTER_SCRAPE=True

# Configuration 2: Performance Optimized (for high-traffic scenarios)
CLEANUP_DRIVER_AFTER_SCRAPE=False
DRIVER_IDLE_TIMEOUT=120  # Kill idle drivers after 2 minutes

# Configuration 3: Maximum Performance (only if you have resources)
CLEANUP_DRIVER_AFTER_SCRAPE=False
DRIVER_IDLE_TIMEOUT=300  # Kill idle drivers after 5 minutes
```

## How to Deploy the Fix

### Option 1: Rebuild Container (Recommended)
If you're using Docker/Railway:

```bash
# Stop the current container
docker-compose down

# Rebuild with new requirements
docker-compose build

# Start fresh
docker-compose up -d
```

### Option 2: Install psutil in Running Container
If you can't rebuild immediately:

```bash
# SSH into container
docker exec -it <container-name> /bin/bash

# Install psutil
pip install psutil==6.1.0

# Restart the application
```

### Option 3: Manual Cleanup (Emergency)
If the container is stuck with too many processes:

```bash
# SSH into container
docker exec -it <container-name> /bin/bash

# Kill all Chrome processes
pkill -9 chrome
pkill -9 chromedriver

# Restart the application
supervisorctl restart fastapi  # or your restart command
```

## Testing the Fix

After deployment, monitor the logs. You should see:

```
🧹 Checking for zombie Chrome/ChromeDriver processes...
   Killing zombie process: chrome (PID: 12345)
   Killing zombie process: chromedriver (PID: 12346)
✓ Cleaned up 2 zombie Chrome/ChromeDriver process(es)
   Waiting for system resources to be released after killing 2 processes...
   Found 0 existing Chrome processes
```

## Prevention Best Practices

### 1. Set Resource Limits
Add to your Docker container or Railway config:

```bash
# Limit maximum processes
ulimit -u 512

# Limit open files
ulimit -n 4096
```

### 2. Use Driver Pooling
Consider implementing a driver pool if you have high concurrent usage:

```python
# In your application startup
from app.services.indeed_selenium_service import cleanup_zombie_processes

@app.on_event("startup")
async def startup_event():
    # Clean up any leftover processes from previous run
    cleanup_zombie_processes(aggressive=True)

@app.on_event("shutdown")
async def shutdown_event():
    # Clean up on shutdown
    from app.services.indeed_selenium_service import cleanup_global_driver
    cleanup_global_driver()
    cleanup_zombie_processes(aggressive=True)
```

### 3. Monitor Process Count
Add health check endpoint:

```python
from app.services.indeed_selenium_service import check_chrome_process_count

@app.get("/health/processes")
async def check_processes():
    count = check_chrome_process_count()
    return {
        "chrome_processes": count,
        "status": "warning" if count > 10 else "ok"
    }
```

### 4. Rate Limiting
Limit concurrent scraping requests to prevent resource exhaustion:

```python
from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_concurrent=3):
        super().__init__(app)
        self.semaphore = asyncio.Semaphore(max_concurrent)
    
    async def dispatch(self, request, call_next):
        if request.url.path.startswith("/api/jobs"):
            async with self.semaphore:
                return await call_next(request)
        return await call_next(request)

# In main.py
app.add_middleware(RateLimitMiddleware, max_concurrent=3)
```

## Troubleshooting

### If errors persist after fix:

1. **Check psutil is installed**:
   ```bash
   pip list | grep psutil
   ```

2. **Check system limits**:
   ```bash
   ulimit -a
   ```

3. **Check current Chrome processes**:
   ```bash
   ps aux | grep -E 'chrome|chromedriver' | wc -l
   ```

4. **Check available resources**:
   ```bash
   free -h  # Memory
   df -h    # Disk
   cat /proc/sys/kernel/threads-max  # Process limit
   ```

5. **Nuclear option - restart container**:
   ```bash
   docker restart <container-name>
   ```

## Additional Recommendations

### For Railway/Cloud Deployments:
- Increase memory allocation if possible (2GB+ recommended)
- Use health checks to auto-restart unhealthy containers
- Set auto-restart policy for containers

### For Local Development:
- Run cleanup manually if you notice slowdowns:
  ```python
  from app.services.indeed_selenium_service import cleanup_zombie_processes
  cleanup_zombie_processes(aggressive=True)
  ```

## Success Indicators

After the fix, you should see:
- ✅ No more "Resource temporarily unavailable" errors
- ✅ Proper process cleanup in logs
- ✅ Stable Chrome process count (0-2 processes typically)
- ✅ Successful scraping operations
- ✅ No memory leaks over time

## Files Modified

1. **`/app/services/indeed_selenium_service.py`**
   - ✅ Enhanced cleanup functions with fallback support
   - ✅ Better error handling and retry logic
   - ✅ Automatic cleanup after scrape (configurable)
   - ✅ Idle timeout detection
   - ✅ Process count monitoring
   
2. **`/requirements.txt`**
   - ✅ Added psutil==6.1.0

3. **`/app/core/config.py`**
   - ✅ Added `CLEANUP_DRIVER_AFTER_SCRAPE` setting (default: True)
   - ✅ Added `DRIVER_IDLE_TIMEOUT` setting (default: 60 seconds)

4. **`/app/main.py`**
   - ✅ Added startup event handler (cleanup leftover processes)
   - ✅ Added shutdown event handler (ensure clean exit)

5. **`/RESOURCE_EXHAUSTION_FIX.md`** (this file)
   - 📚 Comprehensive documentation

6. **`/cleanup_chrome.py`**
   - 🛠️ Manual cleanup utility script

## Next Steps

1. **Deploy the changes** (rebuild container or install psutil)
2. **Monitor the logs** for the first few requests
3. **Test scraping endpoints** to verify functionality
4. **Consider implementing rate limiting** if not already done
5. **Set up monitoring** for Chrome process count

---

**Need Help?**
If issues persist, check:
- Container memory limits
- System ulimits
- Number of concurrent requests
- Chrome/ChromeDriver compatibility

