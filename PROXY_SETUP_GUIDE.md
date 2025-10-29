# Residential Proxy Setup Guide

## What is a Residential Proxy?
A residential proxy uses real home/business IP addresses instead of datacenter IPs. Cloudflare is less likely to block these because they appear as regular users.

## Step-by-Step Setup

### Option 1: Bright Data (Formerly Luminati) - Recommended
**Best for: High success rate, reliable service**

1. **Sign up**: https://brightdata.com
2. **Create a Residential Proxy Zone**:
   - Go to "Residential Proxies"
   - Create new zone
   - Select "Sticky IP" for consistency
   - Choose desired countries/locations

3. **Get your credentials**:
   ```
   Zone ID: your-zone-id
   Username: brightdata-account-username
   Password: your-account-password
   ```

4. **Proxy URL format**:
   ```
   http://brd-customer-CUSTOMER_ID:BRIGHTDATA_PASSWORD@zproxy.lum-superproxy.io:22225
   ```

5. **Add to `.env`**:
   ```bash
   PROXY_URL=http://brd-customer-CUSTOMER_ID:BRIGHTDATA_PASSWORD@zproxy.lum-superproxy.io:22225
   ```

### Option 2: Smartproxy - Budget-Friendly
**Best for: Good value, easy setup**

1. **Sign up**: https://smartproxy.com
2. **Create endpoint** in dashboard
3. **Get credentials**:
   ```
   Username: your-username
   Password: your-password
   Endpoint: us.smartproxy.com:10000 (or other location)
   ```

4. **Add to `.env`**:
   ```bash
   PROXY_URL=http://username:password@us.smartproxy.com:10000
   ```

### Option 3: Oxylabs
**Best for: Advanced features**

1. **Sign up**: https://oxylabs.io
2. **Configure residential proxy** in dashboard
3. **Get endpoint** from dashboard
4. **Add to `.env`**:
   ```bash
   PROXY_URL=http://customer-username:password@gate.oxylabs.io:8000
   ```

### Option 4: Free Options (Limited Success)
**Warning**: Free proxies are often already blocked by Cloudflare

**Proxy List Sites** (use at your own risk):
- https://free-proxy-list.net
- https://www.proxy-list.download
- https://openproxylist.xyz

**Quick setup** (not recommended for production):
```bash
# Example from free-proxy-list.net
PROXY_URL=http://proxy-ip:8080
```

## Environment File Setup

### Step 1: Create/Edit `.env` file
```bash
cd /Users/apple/Documents/indeed_scraper
nano .env  # or use your preferred editor
```

### Step 2: Add Proxy Configuration
```bash
# Residential Proxy Configuration
PROXY_URL=http://username:password@proxy-host:port

# Also set language for better stealth
ACCEPT_LANGUAGE=en-US,en;q=0.9
```

### Step 3: Restart the Server
```bash
# Stop current server (Ctrl+C)
# Restart with new environment
python -m uvicorn app.main:app --reload
```

## Verify Proxy is Working

### Test 1: Check Proxy Connection
```bash
# Test if proxy is reachable
curl -x http://username:password@host:port https://api.ipify.org
# Should return the proxy's IP address
```

### Test 2: Check Indeed Endpoint
```bash
# Try the Indeed scraper with proxy
curl "http://localhost:8000/api/jobs?query=python+developer&location=california&max_results=1"
```

### Test 3: Check Logs
Look in terminal output for:
```
Using proxy: http://username:***@host:port
```

## Proxy URL Formats

### HTTP Proxy
```
PROXY_URL=http://username:password@host:port
```

### SOCKS5 Proxy (if supported)
```
PROXY_URL=socks5://username:password@host:port
```

### Proxy with IP Authentication
```
PROXY_URL=http://IP_ADDRESS@gate.provider.com:port
# In credentials: username includes IP
```

## Recommended Settings

### For Indeed Scraping
```bash
# High-quality residential proxy from Bright Data or Smartproxy
PROXY_URL=http://username:password@residential-endpoint.com:port
ACCEPT_LANGUAGE=en-US,en;q=0.9
MIN_DELAY=3.0  # Be respectful with 3+ seconds between requests
```

### For ZipRecruiter (No Proxy Needed)
ZipRecruiter works without a proxy, so you can skip proxy setup for that endpoint.

## Pricing Comparison

| Provider | Starting Price | Features |
|----------|---------------|----------|
| **Smartproxy** | $75/month | Best value, easy setup |
| **Bright Data** | $500/month | Most reliable, enterprise-grade |
| **Oxylabs** | $300/month | Advanced features |
| **Free Proxies** | Free | Often blocked, unreliable |

## Troubleshooting

### Issue: "Connection refused"
**Solution**: Check proxy credentials and endpoint URL

### Issue: "Proxy authentication failed"
**Solution**: Verify username/password are correct

### Issue: "Still getting blocked"
**Solution**: 
1. Try a different proxy provider
2. Increase `MIN_DELAY` in settings
3. Use rotating/sticky IP options

### Issue: "Slow performance"
**Solution**: 
1. Use proxy closer to target server
2. Check proxy provider's status dashboard
3. Consider upgrading proxy plan

## Security Notes

⚠️ **Never commit `.env` to Git!**
- Ensure `.env` is in `.gitignore`
- Keep credentials secret
- Rotate credentials periodically

## Testing Your Setup

Run this to verify:
```bash
# 1. Check if proxy is set
echo $PROXY_URL  # Should show your proxy URL

# 2. Test Indeed endpoint
curl -v "http://localhost:8000/api/jobs?query=python&location=california&max_results=1"

# 3. Look for "Cloudflare" in error message
# If proxy works, you should get job results or "No jobs found" (not Cloudflare error)
```

## Next Steps

1. **Choose a proxy provider** based on your budget
2. **Sign up and get credentials**
3. **Add to `.env` file**
4. **Restart server**
5. **Test the Indeed endpoint**

If successful, you'll see job results instead of Cloudflare errors! 🎉

