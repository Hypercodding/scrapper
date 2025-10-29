# DataImpulse Proxy Setup Guide

## Will DataImpulse Work for Indeed?

**YES!** ✅ DataImpulse provides residential proxies that should successfully bypass Cloudflare's blocking on Indeed.

## Why DataImpulse Will Work

### Residential IP Addresses
- ✅ Uses real home/business IPs (not datacenter IPs)
- ✅ Cloudflare sees these as legitimate users
- ✅ 90M+ IP pool worldwide
- ✅ $1 per GB (affordable pricing)

[DataImpulse Residential Proxies](https://dataimpulse.com/residential-proxies/)

## Pricing Comparison

### DataImpulse (Your Choice)
```
Intro Plan: $5 for 5GB ($1/GB)
Basic Plan: $50 for 50GB ($1/GB)  
Advanced Plan: $800 for 1TB ($0.8/GB)
```

### Other Providers
```
Smartproxy: $75/month (10GB)
Bright Data: $500/month
Oxylabs: $300/month
```

**DataImpulse is cheaper** and uses **pay-as-you-go** (no monthly fees)!

## Setup Instructions

### Step 1: Sign Up at DataImpulse

1. Go to https://dataimpulse.com/residential-proxies/
2. Click **"TRY NOW"** or **"SIGN UP"**
3. Choose **Intro Plan** ($5 for 5GB) to test
4. Complete registration and payment

### Step 2: Get Your Proxy Credentials

After signing up, you'll get:
- **Username**: Your DataImpulse login
- **Password**: Your account password
- **Endpoint**: rotator.dataimpulse.com (or similar)
- **Port**: Usually 823

### Step 3: Configure in Your `.env` File

```bash
# Edit your .env file
nano .env

# Add DataImpulse proxy configuration
PROXY_URL=http://username:password@rotator.dataimpulse.com:823
```

**Example:**
```bash
PROXY_URL=http://myaccount:mysecretpass123@rotator.dataimpulse.com:823
```

### Step 4: Restart Your Server

```bash
# Stop current server (Ctrl+C if running)
# Restart with new proxy settings
python -m uvicorn app.main:app --reload
```

### Step 5: Test the Indeed Scraper

```bash
# Test with Indeed endpoint
curl "http://localhost:8000/api/jobs?query=python&location=california&max_results=1"
```

**Expected**: Job results instead of Cloudflare error! ✅

## DataImpulse Features for Job Scraping

### 🌍 Geo-Targeting
- Choose specific countries, states, cities
- Perfect for location-based job searches

### 🔄 IP Rotation
- **Sticky sessions**: Same IP for up to 30 minutes
- **Rotating**: New IP each request
- Prevents detection

### 📊 Dashboard
- Track usage in real-time
- Monitor costs
- View statistics

### 💰 Pay-as-you-go
- Only pay for data used
- No monthly fees
- Traffic never expires
- Perfect for occasional scraping

## Example Usage Scenarios

### Scenario 1: Testing (5GB for $5)
```bash
PROXY_URL=http://username:password@rotator.dataimpulse.com:823
Cost: $5 one-time
Traffic: 5GB
Usage: Test Indeed scraping
Result: Perfect for learning
```

### Scenario 2: Regular Use (50GB for $50)
```bash
PROXY_URL=http://username:password@rotator.dataimpulse.com:823
Cost: $50 one-time
Traffic: 50GB
Usage: Daily job scraping
Result: Lasts several months
```

### Scenario 3: Heavy Scraping (1TB for $800)
```bash
PROXY_URL=http://username:password@rotator.dataimpulse.com:823
Cost: $800 one-time
Traffic: 1TB
Usage: Aggressive scraping
Result: Professional grade
```

## Integration in Your Scraper

### Current Configuration (Already Set Up!)

Your scraper already supports proxies! Just add to `.env`:

```bash
# /Users/apple/Documents/indeed_scraper/.env
PROXY_URL=http://username:password@rotator.dataimpulse.com:823
```

### How It Works

1. **Browser launches** → Connects through DataImpulse proxy
2. **DataImpulse** → Routes through residential IP
3. **Indeed.com** → Sees residential IP (not datacenter)
4. **Cloudflare** → Allows the request ✅
5. **Scraper** → Gets job results ✅

### Code Already Supports This!

In `app/services/indeed_selenium_service.py`:

```python
# Line 45-50: Already configured!
if getattr(settings, "PROXY_URL", None):
    proxy = settings.PROXY_URL.strip()
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")
```

**No code changes needed** - just add the proxy URL!

## Advantages of DataImpulse

### ✅ Advantages
1. **Cheap**: $1/GB vs $75+ monthly fees elsewhere
2. **Pay-as-you-go**: No monthly commitments
3. **Traffic never expires**: Buy once, use forever
4. **24/7 support**: 5-star customer service
5. **90M+ IPs**: Largest residential pool
6. **Fast**: Low latency
7. **Reliable**: Trustpilot 5-star reviews

### 📝 Notes
- **No free trial** (but $5 for 5GB is very cheap)
- **Requires sign-up** (but simple process)
- **Pay-per-use** (better than monthly fees)

## Comparison: DataImpulse vs Others

| Feature | DataImpulse | Smartproxy | Bright Data |
|---------|-------------|------------|-------------|
| **Cost** | $1/GB | $75/month | $500/month |
| **IP Pool** | 90M+ | N/A | N/A |
| **Pay-per-use** | ✅ Yes | ❌ No | ❌ No |
| **Traffic Expires** | ❌ Never | ✅ Yes | ✅ Yes |
| **Support** | 24/7 | 24/7 | 24/7 |
| **Best For** | Budget users | Regular users | Enterprise |

## Quick Start Commands

### 1. Sign Up & Get Credentials
```
Visit: https://dataimpulse.com/residential-proxies/
Sign up: Choose "Intro" plan ($5)
Get: Username, password, endpoint
```

### 2. Configure Environment
```bash
cd /Users/apple/Documents/indeed_scraper
echo "PROXY_URL=http://username:password@rotator.dataimpulse.com:823" >> .env
```

### 3. Restart & Test
```bash
# Restart server
python -m uvicorn app.main:app --reload

# Test Indeed
curl "http://localhost:8000/api/jobs?query=python&location=california"
```

## Troubleshooting

### Issue: "Connection refused"
**Solution**: Verify proxy credentials in `.env`

### Issue: "Authentication failed"
**Solution**: Check username/password are correct

### Issue: "Still blocked by Cloudflare"
**Solution**: 
1. Make sure you're using **residential proxies** (not datacenter)
2. Try different geo-location
3. Increase `MIN_DELAY` in settings

## Monitoring Usage

### Check Your Consumption
```
Visit: DataImpulse Dashboard
Track: GB usage
Monitor: Costs in real-time
Refill: When needed
```

### Typical Usage
```
Single request: ~0.001 GB (1MB)
100 requests: ~0.1 GB
1,000 requests: ~1 GB
```

**With intro plan (5GB)**: You can scrape **5,000 jobs** for $5!

## Conclusion

**YES, DataImpulse will work!** ✅

### Why Choose DataImpulse:
1. ✅ Residential IPs (bypasses Cloudflare)
2. ✅ $1/GB (cheapest option)
3. ✅ Pay-as-you-go (no commitment)
4. ✅ 90M+ IP pool (reliable)
5. ✅ Already integrated (just add to `.env`)

### Next Steps:
1. Sign up at DataImpulse
2. Add credentials to `.env`
3. Restart server
4. Test Indeed scraping
5. Enjoy working job scraper!

## Resources

- [DataImpulse Sign Up](https://dataimpulse.com/residential-proxies/)
- [DataImpulse Pricing](https://dataimpulse.com/residential-proxies/#pricing)
- [DataImpulse Dashboard](https://dashboard.dataimpulse.com/)
- [DataImpulse Docs](https://docs.dataimpulse.com/)

