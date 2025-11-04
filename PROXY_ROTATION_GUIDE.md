# Proxy Rotation Guide

This guide explains how to configure and use the proxy rotation feature to avoid Cloudflare challenges.

## Overview

The proxy rotation system automatically switches between multiple proxies to avoid getting blocked by Cloudflare. By default, it rotates every 4 minutes (240 seconds), which you mentioned is around when you start getting Cloudflare challenges.

## Configuration

### Adding Multiple Proxies

You can configure multiple proxies in two ways:

#### Option 1: Environment Variables (Recommended)

Create or update your `.env` file:

```bash
# Multiple proxies (comma-separated)
PROXY_URLS="http://user1:pass1@proxy1.com:7030,http://user2:pass2@proxy2.com:7030,http://user3:pass3@proxy3.com:7030"

# Rotation interval in seconds (default: 240 seconds = 4 minutes)
PROXY_ROTATION_INTERVAL=240
```

#### Option 2: Direct Configuration

Edit `app/core/config.py`:

```python
PROXY_URLS: str = "http://user1:pass1@proxy1.com:7030,http://user2:pass2@proxy2.com:7030,http://user3:pass3@proxy3.com:7030"
PROXY_ROTATION_INTERVAL: int = 240  # seconds
```

### Proxy URL Format

Each proxy URL must follow this format:
```
http://username:password@hostname:port
```

Example:
```
http://ydgjcfkg:akhpmonf8vdj@142.111.48.253:7030
```

## How It Works

1. **Automatic Rotation**: The system automatically rotates to the next proxy after the configured interval (default 4 minutes)

2. **Health Monitoring**: Each proxy is monitored for failures:
   - If a proxy fails 3 times, it's marked as unhealthy
   - The system automatically switches to the next healthy proxy
   - Failed proxies are given a chance to recover

3. **Cloudflare Detection**: When a Cloudflare challenge is detected:
   - The current proxy is marked as failed
   - The system may rotate to the next proxy sooner

4. **Success Tracking**: When scraping succeeds:
   - The proxy's failure count is reset
   - This helps identify which proxies are working well

## Features

### Automatic Rotation
- Proxies rotate automatically every N seconds (configurable)
- A new browser instance is created with the new proxy

### Health Monitoring
- Failed proxies are tracked and avoided
- Healthy proxies are prioritized
- All proxies get a chance to recover

### Logging
- Clear console output shows which proxy is being used
- Proxy credentials are masked in logs for security
- Rotation events are logged

## Example Usage

### Single Proxy (No Rotation)
```bash
PROXY_URLS="http://user:pass@proxy.com:7030"
```

### Multiple Proxies with Rotation
```bash
# Add your 3 proxies (current + 2 new ones)
PROXY_URLS="http://user1:pass1@proxy1.com:7030,http://user2:pass2@proxy2.com:7030,http://user3:pass3@proxy3.com:7030"

# Rotate every 4 minutes (240 seconds)
PROXY_ROTATION_INTERVAL=240
```

### Faster Rotation
```bash
# Rotate every 2 minutes if you're getting blocked sooner
PROXY_ROTATION_INTERVAL=120
```

## Testing Your Setup

1. Start your scraper with multiple proxies configured
2. Watch the console output for proxy rotation messages:
   ```
   🔄 Using proxy: ydg***:***@142.111.48.253:7030
   ⏰ Proxy rotation interval reached, creating new driver with next proxy...
   ✓ Rotated to proxy 2/3: ydg***:***@142.111.49.253:7030
   ```

3. If a proxy fails, you'll see:
   ```
   ⚠️  Proxy failure 1/3: ydg***:***@142.111.48.253:7030
   ⚠️  Cloudflare challenge detected - proxy marked as failed
   ```

4. When rotation happens:
   ```
   ✓ Rotated to proxy 2/3: ydg***:***@142.111.49.253:7030
   ✓ WebDriver initialized successfully
   ```

## Troubleshooting

### All Proxies Getting Blocked
If all proxies are getting blocked:
- Reduce rotation interval (e.g., 120 seconds)
- Add more proxies to the rotation
- Verify proxies are from different IP ranges

### Proxies Not Rotating
- Check that `PROXY_URLS` contains multiple comma-separated URLs
- Verify `PROXY_ROTATION_INTERVAL` is set to a reasonable value
- Check console logs for error messages

### Invalid Proxy Format
If you see validation errors:
- Ensure each proxy URL includes: `http://username:password@hostname:port`
- Remove any spaces from the URL
- Verify hostname and port are correct

## Advanced Configuration

### Manual Proxy Rotation
If you want to force an immediate proxy rotation, you can call:
```python
from app.core.proxy_manager import get_proxy_manager, reset_proxy_manager

# Get current proxy manager
proxy_manager = get_proxy_manager()

# Force rotation to next proxy
proxy_manager.rotate_proxy(force=True)

# Get proxy statistics
stats = proxy_manager.get_proxy_stats()
print(stats)
```

### Reset Proxy Manager
To reset the proxy manager and start fresh:
```python
from app.core.proxy_manager import reset_proxy_manager
reset_proxy_manager()
```

## Best Practices

1. **Use 3+ Proxies**: Having at least 3 proxies gives you better rotation coverage
2. **Different IP Ranges**: Try to use proxies from different providers/IP ranges
3. **Monitor Logs**: Watch the console output to see which proxies work best
4. **Adjust Timing**: If 4 minutes is too long, reduce the interval to 2-3 minutes
5. **Test Individually**: Test each proxy individually first to ensure they work

## Security Notes

- Proxy credentials are automatically masked in logs
- Never commit `.env` files with real credentials
- Use environment variables for production deployments
- Rotate proxy passwords regularly

## Getting More Proxies

If you need recommendations for proxy providers:
- Residential proxies work better for avoiding detection
- Rotating proxies from providers like Bright Data, Oxylabs, or Smartproxy
- Avoid free proxies as they're often blocked

## Support

If you encounter issues:
1. Check the console logs for specific error messages
2. Verify your proxy credentials are correct
3. Test proxies individually using a tool like `curl`:
   ```bash
   curl -x http://user:pass@proxy.com:7030 https://httpbin.org/ip
   ```
4. Ensure proxies support HTTPS connections

