# 🚀 Proxy Rotation - Quick Reference

## Add Your Proxies (30 seconds)

### 1. Open config file
```bash
nano app/core/config.py
```

### 2. Find line 21 and update it:
```python
PROXY_URLS: str = "PROXY1,PROXY2,PROXY3"
```

### 3. Replace with your actual proxies:
```python
PROXY_URLS: str = "http://ydgjcfkg:akhpmonf8vdj@142.111.48.253:7030,http://user2:pass2@host2:port,http://user3:pass3@host3:port"
```

### 4. Save and run your scraper!

---

## What You'll See

### ✅ Normal Operation
```
🔄 Using proxy: ydg***:***@142.111.48.253:7030
✓ WebDriver initialized successfully
```

### 🔄 Automatic Rotation (every 4 minutes)
```
⏰ Proxy rotation interval reached, creating new driver with next proxy...
✓ Rotated to proxy 2/3: use***:***@host2:port
```

### ⚠️ Cloudflare Challenge
```
⚠️  Cloudflare challenge detected - proxy marked as failed
✓ Rotated to proxy 2/3: use***:***@host2:port
```

---

## Common Adjustments

### Change rotation time (line 22):
```python
# Every 3 minutes
PROXY_ROTATION_INTERVAL: int = 180

# Every 5 minutes  
PROXY_ROTATION_INTERVAL: int = 300

# Every 2 minutes
PROXY_ROTATION_INTERVAL: int = 120
```

### Add more proxies:
```python
PROXY_URLS: str = "proxy1,proxy2,proxy3,proxy4,proxy5"
```

---

## Test Your Setup
```bash
python test_proxy_rotation.py
```

Should see: `✓ ALL TESTS PASSED!`

---

## Need Help?

- **Full Guide**: `PROXY_ROTATION_GUIDE.md`
- **Setup Instructions**: `PROXY_SETUP_INSTRUCTIONS.md`
- **Implementation Details**: `IMPLEMENTATION_SUMMARY.md`

---

## Proxy URL Format
```
http://username:password@hostname:port
```

Example:
```
http://myuser:mypass@142.111.48.253:7030
```

---

## That's It! 🎉

Add your proxies → Save → Run scraper → Done!

