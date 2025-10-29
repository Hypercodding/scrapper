# Cloud Deployment Guide for Indeed Scraper

## Why Cloud Provider IPs Still Get Blocked

When you deploy to **Railway, AWS, GCP, Azure, or Heroku**, you're using **datacenter IP addresses**.

Cloudflare **specifically blocks** these IP ranges because they're:
- Not from residential/business locations
- Used by bots and scrapers
- Easy to identify as automated traffic

## IP Address Types

### ✅ Residential IP (Home/Business Internet)
- Your home ISP → **Works**
- Business office → **Works**
- Mobile carrier → **Works**
- **Why**: Cloudflare sees these as legitimate users

### ❌ Datacenter IP (Cloud Providers)
- Railway → **Blocked**
- AWS EC2 → **Blocked**
- Google Cloud → **Blocked**
- Azure → **Blocked**
- Heroku → **Blocked**
- **Why**: Cloudflare knows these are cloud servers, not real users

## Testing on Cloud Deployments

### Test Without Proxy

Deploy to Railway and test:
```bash
# Your Railway URL
curl "https://your-app.railway.app/api/jobs?query=python&location=california&max_results=1"
```

**Expected Result**: Cloudflare error 503

**Why**: Railway's datacenter IP is immediately blocked by Cloudflare's Turnstile

### Test With Proxy (Required for Cloud)

Add proxy to Railway environment variables:

1. **Go to Railway Dashboard**
2. **Settings → Environment Variables**
3. **Add**:
   ```
   PROXY_URL=http://username:password@proxy-host:port
   ```
4. **Redeploy**

**Expected Result**: Jobs scraped successfully

## Solutions for Cloud Deployment

### Option 1: Always Use Proxy (RECOMMENDED)
**Works**: Locally, Railway, AWS, GCP, Heroku, etc.

```bash
# Add to Railway environment variables
PROXY_URL=http://your-proxy-url-here
```

**Advantages**:
- Works everywhere (local, cloud)
- Consistent results
- Residential IP rotation

**Disadvantages**:
- Costs $75-500/month for proxy service
- Additional latency (~200-500ms)

### Option 2: Don't Use Indeed, Use ZipRecruiter
**Works**: Without proxy, on any cloud provider

```bash
# Endpoint that works without proxy
GET /api/jobs/ziprecruiter-enhanced
```

**Why ZipRecruiter Works**:
- Less aggressive anti-scraping
- Accepts cloud datacenter IPs
- No captcha challenges

### Option 3: Local Development, Cloud Deployment with Different Sources
Deploy to Railway but route Indeed requests through your local machine:

**Not Recommended**: Complex, requires VPN/port forwarding

## Railway-Specific Setup

### Step 1: Add Environment Variables

Go to Railway Dashboard → Your Service → Variables:

```bash
# Required for Indeed to work
PROXY_URL=http://username:password@proxy-host:port

# Optional settings
ACCEPT_LANGUAGE=en-US,en;q=0.9
MIN_DELAY=3.0
DEBUG=True
```

### Step 2: Deploy

Railway will automatically:
- Install dependencies from `requirements.txt`
- Start the FastAPI server
- Use your environment variables

### Step 3: Test

```bash
# Test Indeed endpoint (with proxy)
curl "https://your-app.railway.app/api/jobs?query=python&location=california"

# Test ZipRecruiter (no proxy needed)
curl "https://your-app.railway.app/api/jobs/ziprecruiter-enhanced?query=python&location=california"
```

## Cost Analysis

### Running on Railway WITHOUT Proxy
```bash
Railway Free Tier: $0
Indeed Scraping: BLOCKED by Cloudflare
Result: ❌ Cannot scrape Indeed
```

### Running on Railway WITH Proxy
```bash
Railway Free Tier: $0
Proxy Service (Smartproxy): $75/month
Result: ✅ Works perfectly
```

### Using ZipRecruiter Instead
```bash
Railway Free Tier: $0
Proxy: $0
Result: ✅ Works without any costs
```

## Recommended Cloud Deployment Strategy

### For Production

**Option A: Use Proxy for Indeed**
```bash
# Add to Railway/AWS/etc environment variables
PROXY_URL=http://your-residential-proxy
```

**Option B: Only Use ZipRecruiter**
```bash
# Remove Indeed endpoint, only expose ZipRecruiter
# No proxy needed, works on any cloud
```

**Option C: Hybrid Approach**
```bash
# Primary endpoint: ZipRecruiter (no proxy)
GET /api/jobs/ziprecruiter-enhanced

# Secondary endpoint: Indeed (with proxy, expensive)
GET /api/jobs
```

## Testing Cloud Deployment

### 1. Deploy to Railway

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Deploy
railway up
```

### 2. Check Environment Variables

Railway Dashboard → Your Service → Variables:
- `PROXY_URL` (if using Indeed)
- `DEBUG`
- Other settings

### 3. Test Endpoints

```bash
# Test Indeed (will fail without proxy)
curl "https://your-app.railway.app/api/jobs?query=python"

# Test ZipRecruiter (works without proxy)
curl "https://your-app.railway.app/api/jobs/ziprecruiter-enhanced?query=python"
```

## Environment Variables for Cloud

### Minimal Setup (ZipRecruiter Only)
```bash
DEBUG=True
CACHE_TTL=3600
```

### Full Setup (Indeed + ZipRecruiter)
```bash
DEBUG=True
CACHE_TTL=3600
PROXY_URL=http://username:password@proxy-host:port
ACCEPT_LANGUAGE=en-US,en;q=0.9
MIN_DELAY=3.0
```

## Real-World Scenarios

### Scenario 1: Personal Project
```bash
Recommendation: Use ZipRecruiter only
Cost: $0
Time to deploy: 5 minutes
Result: Works on Railway, free forever
```

### Scenario 2: Business Application
```bash
Recommendation: Use proxy + Indeed + ZipRecruiter
Cost: $75-500/month for proxy
Time to deploy: 10 minutes
Result: Reliable Indeed scraping
```

### Scenario 3: MVP/Demo
```bash
Recommendation: Use ZipRecruiter only
Cost: $0
Time to deploy: 5 minutes
Result: Demo works immediately
```

## Conclusion

**Short Answer**: No, Railway (or any cloud provider) will NOT bypass Cloudflare's blocking. You still need a residential proxy.

**Best Practices**:
1. Local development: Works with ZipRecruiter, needs proxy for Indeed
2. Cloud deployment: Same as local - needs proxy for Indeed
3. Cost-effective: Use ZipRecruiter only (no proxy needed)
4. Full features: Add residential proxy for Indeed

## Next Steps

1. ✅ Decide: ZipRecruiter only OR Include Indeed
2. ✅ If Indeed: Get residential proxy ($75/month minimum)
3. ✅ Deploy to Railway with environment variables
4. ✅ Test endpoints
5. ✅ Monitor costs and success rates

