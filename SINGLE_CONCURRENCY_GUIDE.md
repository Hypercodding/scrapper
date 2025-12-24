# Single-Concurrency Execution System

This document describes the single-concurrency execution system implemented to ensure stable operation on Railway's constrained container environment.

## Overview

The application now enforces **strict single-concurrency execution** - only one scraping request can run at a time. This prevents resource exhaustion, zombie processes, and CPU/memory issues on Railway.

## Key Features

### 1. Global Execution Lock
- Only one scraping operation can run at any given time
- Requests that arrive while a scrape is in progress receive HTTP 429 (Too Many Requests)
- Lock is automatically released after cleanup, even on errors/crashes

### 2. Hard Process Termination
- All browser processes are hard-killed at the OS level after every scrape
- Works for both successful and failed scrapes
- Prevents zombie processes and resource leaks
- Uses `SIGKILL` when necessary to ensure cleanup

### 3. Guaranteed Cleanup
- Cleanup runs in `finally` blocks and context managers
- Executes even on exceptions, crashes, or timeouts
- Multiple safety nets to ensure browser processes are terminated

### 4. Railway-Appropriate Timeouts
- Default scrape timeout: **10 minutes** (600 seconds)
- Lock acquire timeout: **1 minute** (prevents indefinite blocking)
- Cleanup timeout: **30 seconds**

## Architecture

### Core Modules

#### `app/core/browser_executor.py`
Provides browser lifecycle management with hard process termination:

- `hard_kill_all_browsers()` - Hard-kills all Chrome/ChromeDriver processes
- `cleanup_browser(driver)` - Comprehensive cleanup for a single driver
- `verify_cleanup()` - Verifies no browser processes are running
- `managed_browser(driver)` - Context manager for browser lifecycle

#### `app/core/scrape_executor.py`
Enforces single-concurrency execution:

- `scrape_execution_context()` - Async context manager that enforces single-concurrency
- `enforce_single_concurrency()` - Decorator for async scraping functions
- `get_execution_status()` - Returns current execution status
- `execute_scrape_with_cleanup()` - Lower-level execution function

### Route Integration

All scraping endpoints are wrapped with `scrape_execution_context()`:

```python
async with scrape_execution_context():
    jobs = await scrape_function(...)
```

This ensures:
1. Lock is acquired before scraping starts
2. Cleanup happens in the `finally` block
3. Lock is released after cleanup

## Error Handling

### ScrapeInProgressError
- Raised when attempting to start a scrape while another is running
- Returns HTTP 429 to client
- Client should wait and retry

### ScrapeTimeoutError
- Raised when a scrape exceeds the timeout (10 minutes)
- Returns HTTP 504 to client
- Cleanup still executes

## API Endpoints

### Scraping Endpoints
All scraping endpoints enforce single-concurrency:
- `GET /api/jobs` (Indeed)
- `GET /api/jobs/simplyhired`
- `GET /api/jobs/ziprecruiter`
- `GET /api/jobs/ziprecruiter-enhanced`
- `POST /api/jobs/scrape-url`
- `GET /api/jobs/scrape-url-get`
- `POST /api/jobs/scrape-multiple-urls`

### Health/Status Endpoints

#### `GET /api/health/execution-status`
Returns current scraping execution status:
```json
{
  "is_locked": false,
  "scrape_in_progress": false,
  "elapsed_seconds": null,
  "timeout_seconds": 600,
  "message": "No scrape in progress"
}
```

#### `POST /api/health/cleanup`
Emergency cleanup - hard-kills all browser processes:
```json
{
  "status": "success",
  "message": "Emergency cleanup completed successfully",
  "processes_killed": 3,
  "cleanup_verified": true
}
```

#### `GET /api/health/detailed`
Returns detailed health check including Chrome process count and recommendations.

## Best Practices

### For API Clients

1. **Handle HTTP 429 gracefully**: If you receive a 429, wait a few seconds and retry
2. **Use execution-status endpoint**: Check `/api/health/execution-status` before starting a long scrape
3. **Implement retry logic**: With exponential backoff for 429/504 errors
4. **Use async endpoints**: For long-running scrapes, use the async job pattern endpoints

### For Development

1. **Test cleanup**: After each scrape, verify no browser processes remain
2. **Monitor logs**: Watch for cleanup messages to ensure processes are being killed
3. **Check execution status**: Use the status endpoint to debug concurrency issues
4. **Use health endpoints**: Regularly check `/api/health/detailed` for process count

## Process Cleanup Details

The cleanup process follows these steps:

1. **Graceful Quit**: Attempts `driver.quit()` for clean shutdown
2. **Process Tree Kill**: Kills the driver service process and all its children
3. **Hard-Kill All**: Uses OS-level commands to kill all Chrome/ChromeDriver processes
4. **Verification**: Checks that no browser processes remain
5. **Resource Release**: Waits for system resources to be released

### Process Detection

The system identifies browser processes by:
- Process name containing "chrome" or "chromedriver"
- Command line arguments like:
  - `--test-type`
  - `--enable-automation`
  - `--remote-debugging-port`
  - `--headless`

## Railway Configuration

### Recommended Settings

1. **Memory**: Minimum 1GB RAM (2GB recommended for stability)
2. **CPU**: Standard Railway CPU allocation is sufficient
3. **Timeout**: The 10-minute scrape timeout works well with Railway's request timeout

### Monitoring

Monitor these metrics on Railway:
- Memory usage (should return to baseline after each scrape)
- CPU usage (should return to baseline after each scrape)
- Process count (should be 0 when idle)
- Request latency (may be higher if another scrape is in progress)

## Troubleshooting

### Issue: HTTP 429 on every request
**Cause**: A previous scrape didn't complete or cleanup failed
**Solution**: 
1. Check `/api/health/execution-status` to see if a scrape is stuck
2. Call `/api/health/cleanup` to force cleanup
3. Check logs for errors in cleanup

### Issue: High process count
**Cause**: Browser processes not being killed properly
**Solution**:
1. Call `/api/health/cleanup` endpoint
2. Check logs for cleanup errors
3. Verify `browser_executor.py` is being used correctly

### Issue: Scrape times out
**Cause**: Target site is slow or unresponsive
**Solution**:
1. Timeout is set to 10 minutes - this should be sufficient for most cases
2. Consider using async job pattern endpoints for very long scrapes
3. Check target site status

### Issue: Resource exhaustion
**Cause**: Multiple scrapes running simultaneously (shouldn't happen with this system)
**Solution**:
1. Verify all endpoints use `scrape_execution_context()`
2. Check that the lock is being acquired correctly
3. Review logs for concurrent execution attempts

## Implementation Notes

### Why Single-Concurrency?

1. **Railway Constraints**: Railway containers have limited CPU, memory, and process limits
2. **Resource Stability**: One browser instance is more predictable and stable
3. **Cleanup Reliability**: Easier to ensure cleanup when only one browser exists
4. **Memory Management**: Prevents memory leaks from multiple browser instances

### Why Hard-Kill?

1. **Guaranteed Cleanup**: Soft termination can fail or hang
2. **Zombie Prevention**: Hard-kill prevents zombie processes
3. **Resource Recovery**: Ensures OS resources are immediately released
4. **Railway Compatibility**: Works reliably in containerized environments

### Why 10-Minute Timeout?

1. **Railway Limits**: Prevents extremely long-running operations
2. **Resource Management**: Ensures resources are freed in reasonable time
3. **User Experience**: Prevents indefinite waits
4. **Practical Limit**: Most scrapes complete in 1-5 minutes

## Future Improvements

Potential enhancements:
1. Configurable timeout per endpoint
2. Priority queue for scraping requests
3. Metrics/telemetry for execution tracking
4. Automatic retry for failed cleanups
5. Process count alerts

