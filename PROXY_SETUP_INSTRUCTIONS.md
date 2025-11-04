# Quick Setup: Adding Your 2 New Proxies

## Current Setup
You currently have 1 proxy configured:
```
http://ydgjcfkg:akhpmonf8vdj@142.111.48.253:7030
```

## Adding 2 More Proxies

### Method 1: Update config.py (Quick)

Open `app/core/config.py` and update line 21:

**Before:**
```python
PROXY_URLS: str = "http://ydgjcfkg:akhpmonf8vdj@142.111.48.253:7030"
```

**After (add your 2 new proxies):**
```python
PROXY_URLS: str = "http://ydgjcfkg:akhpmonf8vdj@142.111.48.253:7030,http://user2:pass2@proxy2.com:port,http://user3:pass3@proxy3.com:port"
```

Replace `user2:pass2@proxy2.com:port` and `user3:pass3@proxy3.com:port` with your actual proxy credentials.

### Method 2: Create .env file (Recommended for production)

Create a file named `.env` in the project root directory:

```bash
# .env file
PROXY_URLS="http://ydgjcfkg:akhpmonf8vdj@142.111.48.253:7030,http://user2:pass2@proxy2.com:port,http://user3:pass3@proxy3.com:port"
PROXY_ROTATION_INTERVAL=240
```

## Configuration Options

### Rotation Interval
You mentioned getting Cloudflare challenges after 4 minutes. The default is set to 240 seconds (4 minutes).

To rotate **more frequently** (e.g., every 3 minutes):
```python
PROXY_ROTATION_INTERVAL: int = 180  # 3 minutes
```

To rotate **less frequently** (e.g., every 5 minutes):
```python
PROXY_ROTATION_INTERVAL: int = 300  # 5 minutes
```

## Testing Your Setup

1. Update the config with your 3 proxies
2. Run your scraper
3. Watch the console output:

```
🔄 Using proxy: ydg***:***@142.111.48.253:7030
✓ WebDriver initialized successfully
... (scraping happens) ...
⏰ Proxy rotation interval reached, creating new driver with next proxy...
✓ Rotated to proxy 2/3: use***:***@proxy2.com:port
✓ WebDriver initialized successfully
```

## What Happens When Cloudflare Blocks?

If Cloudflare challenges appear:
```
⚠️  Cloudflare challenge detected - proxy marked as failed
⚠️  Proxy failure 1/3: ydg***:***@142.111.48.253:7030
✓ Rotated to proxy 2/3: use***:***@proxy2.com:port
```

After 3 failures, a proxy is marked as unhealthy and skipped until others fail too.

## Example with 3 Proxies

```python
# In app/core/config.py, line 21:
PROXY_URLS: str = "http://ydgjcfkg:akhpmonf8vdj@142.111.48.253:7030,http://newuser1:newpass1@123.456.789.10:8080,http://newuser2:newpass2@98.765.432.10:9000"
```

This gives you:
- **Proxy 1**: 142.111.48.253:7030 (your current one)
- **Proxy 2**: 123.456.789.10:8080 (your first new proxy)  
- **Proxy 3**: 98.765.432.10:9000 (your second new proxy)

The scraper will rotate through these every 4 minutes.

## Need Help?

See the full guide: `PROXY_ROTATION_GUIDE.md`

