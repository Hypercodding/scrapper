# n8n + Azure Functions Setup Guide

## Short Answer
**No, this won't bypass Cloudflare**. Azure Functions still use datacenter IPs, and n8n cloud also uses cloud infrastructure.

## The Problem

### IP Address Type Matters
```
Azure Functions → Datacenter IP → BLOCKED by Cloudflare ❌
n8n Cloud → Datacenter IP → BLOCKED by Cloudflare ❌
Your Home → Residential IP → Works ✅
```

**Cloudflare blocks ALL datacenter IPs** regardless of:
- Which cloud provider (Azure, AWS, Railway, etc.)
- How you trigger it (n8n, direct API call, HTTP trigger)
- Azure Functions, AWS Lambda, GCP Cloud Functions → All blocked

## Solutions

### Option 1: Use n8n with Proxy (Recommended)
Trigger Azure Function → Azure Function uses residential proxy → Cloudflare allows ✅

**Setup**:

1. **Add proxy to Azure Function environment variables**:
   ```bash
   PROXY_URL=http://username:password@proxy-host:port
   ```

2. **Connect n8n to Azure Function**:
   - Use HTTP Request node
   - Call your Azure Function endpoint
   - Azure Function handles Indeed scraping with proxy

3. **Result**: Works perfectly

**Cost**: 
- Azure Functions Free Tier: $0
- n8n Cloud: $0 (community) or $20/month (starter)
- Residential Proxy: $75-500/month
- **Total**: $75-500/month

### Option 2: Use ZipRecruiter Instead (Best for n8n)
**No proxy needed!**

**Setup**:

1. **Deploy your scraper to Azure Functions**
2. **Trigger from n8n**:
   ```javascript
   // n8n HTTP Request node
   POST https://your-function.azurewebsites.net/api/scrape-ziprecruiter
   
   Body: {
     "query": "python developer",
     "location": "california",
     "max_results": 20
   }
   ```

3. **Result**: Works without proxy!

**Cost**:
- Azure Functions Free Tier: $0
- n8n Cloud: $0-20/month
- Proxy: $0 (not needed!)
- **Total**: $0-20/month ✅

### Option 3: Use n8n's HTTP Request Node with Proxy
Skip Azure Functions entirely!

**Setup in n8n**:

1. **n8n HTTP Request node** with proxy
2. **Directly scrape ZipRecruiter** (no Azure needed)
3. **Use residential proxy** if you want Indeed

**Example n8n workflow**:
```javascript
// HTTP Request Node
URL: https://www.ziprecruiter.com/candidate/search?search={{$json.query}}

Headers:
  User-Agent: Mozilla/5.0...

// Parse with HTML node
// Extract job data
```

**Cost**: n8n only ($0-20/month)

## Why n8n + Proxy is Actually Better

### n8n + Residential Proxy Setup

**Architecture**:
```
n8n Workflow
  ↓
  Schedule Trigger (daily/hourly)
  ↓
  HTTP Request Node
  ↓
  (With residential proxy)
  ↓
  Indeed.com
  ↓
  Get job data
  ↓
  Store in database/excel/webhook
```

**Advantages**:
- ✅ Visual workflow editor
- ✅ Built-in scheduling
- ✅ Error handling & retries
- ✅ No server management
- ✅ Cheap ($0-20/month)

**Setup**:

1. **Install Residential Proxy**:
   - Sign up for Smartproxy or Bright Data
   - Get proxy URL

2. **Configure n8n HTTP Request**:
   ```yaml
   Node: HTTP Request
   Method: GET
   URL: https://www.indeed.com/jobs?q={{$json.query}}
   
   Authentication:
     Type: Proxy
     Proxy URL: http://username:password@proxy-host:port
   
   Options:
     Follow Redirects: true
     Ignore SSL: false
   ```

3. **Parse HTML**:
   - Add HTML Extract node
   - Extract job cards, titles, companies, etc.

4. **Store Results**:
   - Add Database node (save to Postgres)
   - OR Add Google Sheets node
   - OR Add Webhook node (send to your app)

## Implementation Examples

### Example 1: n8n → ZipRecruiter (No Proxy)
**Cost**: $0/month ✅

```javascript
// n8n Workflow
[
  {
    "type": "schedule",
    "cron": "0 9 * * *" // Every day at 9 AM
  },
  {
    "type": "http",
    "method": "GET",
    "url": "https://www.ziprecruiter.com/candidate/search",
    "params": {
      "search": "python developer",
      "location": "remote",
      "page": "1"
    }
  },
  {
    "type": "html-extract",
    "selector": ".job_result",
    "fields": {
      "title": ".job_title",
      "company": ".company_name",
      "location": ".job_location"
    }
  },
  {
    "type": "google-sheets",
    "action": "append",
    "sheet": "Jobs",
    "data": "{{$json}}"
  }
]
```

### Example 2: n8n → Indeed with Proxy
**Cost**: $75-500/month

```javascript
// n8n Workflow
[
  {
    "type": "schedule",
    "cron": "0 9 * * *"
  },
  {
    "type": "http",
    "method": "GET",
    "url": "https://www.indeed.com/jobs",
    "params": {
      "q": "python developer",
      "l": "california"
    },
    "proxy": "http://username:password@proxy-host:port"
  },
  // ... rest of workflow
]
```

## Why n8n is Actually Perfect for This

### Advantages of n8n Approach

1. **No server management**
   - n8n handles hosting
   - Automatic backups
   - Built-in monitoring

2. **Visual workflow editor**
   - Drag-and-drop
   - Easy to modify
   - No code needed

3. **Built-in scheduling**
   - Cron triggers
   - No need for external schedulers

4. **Multiple data destinations**
   - Database
   - Google Sheets
   - Airtable
   - Webhooks
   - Slack notifications

5. **Error handling**
   - Automatic retries
   - Alerts
   - Logs

## Recommended Setup

### Best Choice: n8n Self-Hosted or n8n Cloud

**Architecture**:
```
n8n (self-hosted or cloud)
  ↓
Schedule trigger (daily)
  ↓
HTTP Request to ZipRecruiter
  ↓
Parse HTML
  ↓
Send to your API/database
```

**Cost**: $0-20/month (vs $75-500 with proxy)

**Steps**:

1. **Deploy n8n** (self-hosted or cloud):
   ```bash
   # Self-hosted (free)
   docker run -d n8nio/n8n
   
   # OR use n8n cloud ($20/month starter)
   ```

2. **Create workflow**:
   - Add Schedule Trigger
   - Add HTTP Request node
   - Connect to ZipRecruiter
   - Add data processing nodes

3. **No proxy needed** if using ZipRecruiter!

### Alternative: Keep Your Azure Function + n8n Trigger

If you want to keep your current scraper:

**Architecture**:
```
n8n
  ↓
HTTP Trigger
  ↓
Azure Function (with proxy)
  ↓
Indeed/ZipRecruiter
  ↓
Return results to n8n
```

**Setup in n8n**:
- Add HTTP Request node
- Call Azure Function: `https://your-function.azurewebsites.net/api/jobs`
- Pass query parameters
- Parse results
- Store in database/sheets

## Cost Comparison

### n8n Cloud + Azure + Proxy
```
n8n Cloud: $20/month
Azure Functions: $0 (free tier)
Residential Proxy: $75/month
Total: $95/month ❌
```

### n8n Cloud + ZipRecruiter
```
n8n Cloud: $20/month
Proxy: $0
Total: $20/month ✅
```

### n8n Self-Hosted + ZipRecruiter
```
n8n Self-hosted: $0 (free)
Proxy: $0
Total: $0/month 🎉
```

## My Recommendation

### Best Option: n8n + ZipRecruiter (No Azure, No Proxy)

**Why**:
- ✅ Works without proxy
- ✅ Costs $0-20/month
- ✅ Visual workflow editor
- ✅ Built-in scheduling
- ✅ Easy to modify

**Skip**:
- ❌ Azure Functions (unnecessary complexity)
- ❌ Residential proxy (not needed for ZipRecruiter)
- ❌ Indeed (blocked without expensive proxy)

**Workflow**:
1. Deploy n8n (self-hosted or cloud)
2. Create workflow to scrape ZipRecruiter
3. Store results in database/sheets
4. Done!

## Quick Start

### n8n Workflow for Job Scraping

```yaml
name: "Daily Job Scraper"

nodes:
  - name: "Daily Trigger"
    type: "schedule"
    cron: "0 9 * * *"  # 9 AM daily
    
  - name: "Scrape ZipRecruiter"
    type: "http"
    method: "GET"
    url: "https://www.ziprecruiter.com/candidate/search"
    params:
      search: "python developer"
      location: "remote"
    
  - name: "Parse Jobs"
    type: "html-extract"
    selector: ".job_result"
    
  - name: "Save to Database"
    type: "postgres"
    operation: "insert"
    table: "jobs"
```

**Cost**: $0 if self-hosted, $20 if n8n cloud

**Result**: Daily job scraping without proxy! ✅

## Conclusion

**Answer**: Using Azure Functions via n8n WON'T bypass Cloudflare.

**Best Solution**: 
- Use **n8n directly** (no Azure Functions)
- Use **ZipRecruiter** (not Indeed)
- No proxy needed
- Costs $0-20/month

**Workflow**:
```
n8n → ZipRecruiter → Parse → Store
(No proxy, no Azure, no Cloudflare blocks!)
```

