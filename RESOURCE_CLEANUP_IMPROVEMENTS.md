# Resource Cleanup Improvements - Edge Case Fixes

## Problem Summary
The application was experiencing "Chrome instance exited" errors and resource exhaustion, indicating that Chrome/ChromeDriver instances were not being properly cleaned up in certain edge cases, leading to memory exhaustion and process limits being reached.

## Root Causes Identified

### 1. **Navigation Timeout Thread Leaks** (Indeed Scraper)
- **Issue**: When navigation timed out using threading, the thread continued running in the background
- **Impact**: Hung threads kept references to drivers, preventing cleanup
- **Location**: `indeed_selenium_service.py` lines ~1326-1363

### 2. **Service Process Cleanup Failures** (Both Scrapers)
- **Issue**: Service process cleanup was nested in try-catch blocks that could fail silently
- **Impact**: ChromeDriver service processes remained running even after driver.quit()
- **Location**: Multiple locations in cleanup code

### 3. **Driver Creation Failure Cleanup** (Both Scrapers)
- **Issue**: When driver creation failed, the service might be created but not cleaned up
- **Impact**: Orphaned ChromeDriver service processes accumulated
- **Location**: `indeed_selenium_service.py` ~line 807, `generic_career_scraper.py` ~line 2807-2813

### 4. **Insufficient Resource Monitoring** (Both Scrapers)
- **Issue**: No early warning system for resource exhaustion
- **Impact**: Chrome would crash during startup without clear diagnostics
- **Location**: Driver initialization code

## Fixes Implemented

### Fix 1: Replaced Threading with Built-in Timeouts
**File**: `indeed_selenium_service.py`

**Changed**:
```python
# Old: Threading-based timeout (can leak)
nav_thread = threading.Thread(target=navigate_with_timeout)
nav_thread.daemon = True
nav_thread.start()
nav_thread.join(timeout=60)
```

**To**:
```python
# New: Use driver's built-in timeout + page load stop
try:
    driver.get(url)
except Exception as nav_err:
    if "timeout" in str(nav_err).lower():
        driver.execute_script("window.stop();")
        time.sleep(1.0)
```

**Benefit**: Eliminates thread leaks, more reliable resource cleanup

### Fix 2: Enhanced Service Process Cleanup
**Files**: `indeed_selenium_service.py`, `generic_career_scraper.py`

**Changed**: Separated driver and service cleanup into distinct steps:
```python
# Step 1: Cleanup driver
if temp_driver:
    try:
        temp_driver.quit()
    except Exception as quit_error:
        print(f"Error during driver.quit(): {quit_error}")
    finally:
        temp_driver = None

# Step 2: Cleanup service separately (even if driver cleanup failed)
if service:
    try:
        if hasattr(service, 'process') and service.process:
            if service.process.poll() is None:
                service.process.terminate()
                service.process.wait(timeout=3)
    except:
        service.process.kill()
        service.process.wait(timeout=2)
```

**Benefit**: Service cleanup happens even if driver cleanup fails

### Fix 3: Driver Creation Failure Cleanup
**Files**: `indeed_selenium_service.py`, `generic_career_scraper.py`

**Changed**: Track service separately and clean up on driver creation failure:
```python
service = None  # Track for cleanup
try:
    service = Service(...)
    temp_driver = webdriver.Chrome(service=service, options=chrome_options)
except Exception as init_error:
    # Clean up service if driver creation failed
    if service and hasattr(service, 'process') and service.process:
        if service.process.poll() is None:
            service.process.terminate()
            service.process.wait(timeout=3)
    raise
```

**Benefit**: No orphaned service processes when driver creation fails

### Fix 4: Resource Monitoring and Early Detection
**File**: `indeed_selenium_service.py`

**Added**: New `check_system_resources()` function:
```python
def check_system_resources() -> dict:
    """Check system resource availability and return warnings"""
    resources = {
        'memory_available': '...',
        'memory_percent': '...',
        'chrome_processes': 0,
        'warnings': []
    }
    
    # Check memory usage
    if mem.percent > 90:
        resources['warnings'].append("CRITICAL: Memory usage at 90%+")
    
    # Check Chrome process count
    if chrome_count > 20:
        resources['warnings'].append("CRITICAL: 20+ Chrome processes")
    
    return resources
```

**Usage**: Called before driver creation to detect issues early:
```python
resources = check_system_resources()
if resources['warnings']:
    print("Resource warnings detected:")
    for warning in resources['warnings']:
        print(warning)
    
    if any('CRITICAL' in w for w in resources['warnings']):
        cleanup_zombie_processes(aggressive=True)
```

**Benefit**: Proactive cleanup before Chrome crashes

### Fix 5: Improved Error Messages
**File**: `indeed_selenium_service.py`

**Enhanced**: Error message for "Chrome instance exited":
```python
elif "session not created" in error_msg.lower() or "chrome.*exited" in error_msg.lower():
    raise Exception(
        f"Chrome instance exited during startup ({process_count} processes before attempt). "
        f"This typically indicates: "
        f"1) Memory exhaustion (insufficient RAM/swap) "
        f"2) Too many processes "
        f"3) System resource limits (ulimit) "
        f"4) Missing libraries (ldd /usr/bin/google-chrome). "
        f"Solutions: 1) Restart application/container "
        f"2) Increase memory limits "
        f"3) Reduce concurrent requests "
        f"4) Check 'ulimit -a' and 'free -h'. "
        f"Error: {error_msg}"
    )
```

**Benefit**: Clear diagnostics for operators to resolve issues

## Testing Recommendations

### 1. Memory Exhaustion Test
```bash
# Monitor memory during scraping
watch -n 1 'free -h && ps aux | grep chrome | wc -l'

# Run scraping and verify cleanup
curl -X POST http://localhost:8000/api/scrape/indeed \
  -H "Content-Type: application/json" \
  -d '{"query":"software engineer","location":"Remote","max_results":100}'
```

### 2. Process Leak Test
```bash
# Count Chrome processes before
ps aux | grep chrome | wc -l

# Run multiple scrapes
for i in {1..5}; do
  curl -X POST http://localhost:8000/api/scrape/indeed ...
done

# Count Chrome processes after (should be same or very close)
ps aux | grep chrome | wc -l
```

### 3. Resource Limit Test
```bash
# Temporarily reduce limits to trigger error
ulimit -n 256  # Reduce open file descriptors
ulimit -u 100  # Reduce max processes

# Run scraping - should get clear error message
curl -X POST http://localhost:8000/api/scrape/indeed ...
```

## Expected Improvements

1. **No More Thread Leaks**: Navigation timeouts won't leave background threads running
2. **Complete Cleanup**: Service processes always cleaned up, even when driver.quit() fails
3. **Early Detection**: Resource issues detected and handled before Chrome crashes
4. **Better Diagnostics**: Clear error messages guide resolution
5. **Proactive Cleanup**: Automatic cleanup when critical resource warnings detected

## Monitoring

After deployment, monitor these metrics:

1. **Chrome Process Count**: Should return to 0-2 between requests
   ```bash
   watch -n 5 'ps aux | grep chrome | wc -l'
   ```

2. **Memory Usage**: Should not continuously grow
   ```bash
   watch -n 5 'free -h'
   ```

3. **Error Logs**: Look for these improved error messages:
   - "Chrome instance exited during startup" (with diagnostic info)
   - "Resource warnings detected" (with memory/process info)
   - "CRITICAL: Memory usage at X%" (early warning)

## Rollback Plan

If issues persist, the changes are isolated to:
- `indeed_selenium_service.py` (navigation logic and cleanup)
- `generic_career_scraper.py` (driver creation)

To rollback:
```bash
git diff HEAD^ app/services/indeed_selenium_service.py
git diff HEAD^ app/services/generic_career_scraper.py
# Review changes and revert if needed
git checkout HEAD^ -- app/services/indeed_selenium_service.py
git checkout HEAD^ -- app/services/generic_career_scraper.py
```

## Additional Recommendations

1. **Install psutil**: For better process monitoring
   ```bash
   pip install psutil
   ```

2. **Increase System Limits** (if on dedicated server):
   ```bash
   ulimit -n 65536  # Open files
   ulimit -u 4096   # Max processes
   ```

3. **Memory Allocation** (Railway/Docker):
   - Minimum: 2GB RAM
   - Recommended: 4GB+ RAM for concurrent scraping

4. **Concurrent Request Limits**:
   - Limit to 2-3 concurrent scraping requests
   - Use queue system for multiple requests

## Summary

These fixes address all identified edge cases where resources weren't being cleaned up:
- ✅ Navigation timeout threads
- ✅ Service process cleanup failures
- ✅ Driver creation failures
- ✅ Resource monitoring and early detection
- ✅ Improved error diagnostics

The application should now handle resource constraints more gracefully and provide clear diagnostics when issues occur.

