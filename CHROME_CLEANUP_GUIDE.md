# Chrome Resource Cleanup - Quick Reference

## ✅ YES! Chrome is Completely Killed After Each Scrape

By default, **every Chrome resource is completely terminated** after finishing a scraping operation. No processes are left running when idle.

## How It Works

### Default Behavior (`CLEANUP_DRIVER_AFTER_SCRAPE=True`)

```
1. API Request arrives
2. Chrome launches
3. Scraping completes
4. Chrome is killed:
   - driver.quit() called
   - Service process terminated
   - Zombie processes cleaned up
5. All resources freed
6. Chrome process count: 0
```

### What Gets Cleaned Up

✅ **Chrome browser process** - Completely terminated  
✅ **ChromeDriver process** - Killed and verified dead  
✅ **Service processes** - Force-killed if needed  
✅ **Zombie processes** - Scanned and removed  
✅ **File descriptors** - Released back to system  
✅ **Memory** - Freed  

## Monitoring Chrome Processes

### Check if cleanup is working:

```bash
# Check Chrome process count
ps aux | grep -E 'chrome|chromedriver' | grep -v grep | wc -l

# Should show 0 when not actively scraping
```

### Using the cleanup script:

```bash
# Check current state
python cleanup_chrome.py --check

# Manual cleanup if needed
python cleanup_chrome.py

# Aggressive cleanup
python cleanup_chrome.py --aggressive
```

## Configuration

### Recommended (Default) - Maximum Resource Conservation
```bash
# In .env or environment variables
CLEANUP_DRIVER_AFTER_SCRAPE=True
DRIVER_IDLE_TIMEOUT=60
```
- ✅ Chrome killed after every scrape
- ✅ Zero processes when idle
- ✅ No resource buildup
- ⚠️ Slower (restarts Chrome each time)

### Alternative - Performance Mode
```bash
CLEANUP_DRIVER_AFTER_SCRAPE=False
DRIVER_IDLE_TIMEOUT=120
```
- ⚡ Faster (reuses Chrome)
- ⚠️ Chrome stays alive between requests
- ⚠️ Auto-killed after 120s of inactivity

## Verification

After deploying, you should see logs like:

```
✓ [SCRAPE] Scraping completed successfully
🧹 [SCRAPE] Cleaning up driver after scrape (CLEANUP_DRIVER_AFTER_SCRAPE=True)
   ✓ Driver quit successfully
   ✓ Driver process terminated
   ✓ Global driver reset - all Chrome resources freed
   ✓ Cleaned up 0 orphaned process(es)
```

Then check:
```bash
# Should return 0
ps aux | grep chrome | grep -v grep | wc -l
```

## Automatic Cleanup Events

### On Application Startup
```
🚀 Application starting up...
🧹 Cleaning up any leftover Chrome processes from previous runs...
   ✓ Cleaned up X leftover process(es)
```

### On Application Shutdown
```
🛑 Application shutting down...
🧹 Cleaning up Chrome resources...
   ✓ Global driver cleaned up
   ✓ Cleaned up X process(es)
   ✓ All Chrome resources cleaned up
```

## Health Check Endpoint

Monitor your Chrome processes via API:

```bash
# Add to your app (example)
curl http://localhost:8000/health/chrome
```

Returns:
```json
{
  "chrome_processes": 0,
  "status": "clean"
}
```

## Troubleshooting

### If Chrome processes persist:

1. **Check configuration**:
   ```bash
   # Verify your .env has:
   CLEANUP_DRIVER_AFTER_SCRAPE=True
   ```

2. **Manual cleanup**:
   ```bash
   python cleanup_chrome.py --aggressive
   ```

3. **Check logs** for cleanup messages

4. **Restart container** if needed:
   ```bash
   docker restart <container-name>
   ```

### Expected Chrome Process Count

- **When idle**: 0 processes
- **During scrape**: 2-4 processes (chrome + chromedriver + helpers)
- **After scrape**: 0 processes
- **Warning threshold**: > 10 processes
- **Critical threshold**: > 20 processes

## Summary

**Q: Does it kill every Chrome resource after completing the process?**  
**A: YES!** ✅

**Q: Must it not leave any resource unused?**  
**A: Correct!** With `CLEANUP_DRIVER_AFTER_SCRAPE=True` (default), **all Chrome resources are completely freed after each scraping operation.**

**Q: If it's not being used, kill it?**  
**A: Exactly!** That's what the new implementation does. Zero Chrome processes when idle.

---

**The answer to your question is: YES, all Chrome resources are killed after each operation. Nothing is left running when idle.**

