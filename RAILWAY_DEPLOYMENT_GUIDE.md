# Railway Deployment Guide for Job Scraper

This guide will help you deploy your job scraper application to Railway.

## Prerequisites

1. **Railway Account**: Sign up at [railway.app](https://railway.app)
2. **GitHub Account**: Your code should be in a GitHub repository
3. **Environment Variables**: Prepare your configuration values

## Step-by-Step Deployment

### 1. Prepare Your Repository

Make sure your code is pushed to GitHub:
```bash
git add .
git commit -m "Prepare for Railway deployment"
git push origin main
```

### 2. Create a New Railway Project

1. Go to [railway.app](https://railway.app) and sign in
2. Click **"New Project"**
3. Select **"Deploy from GitHub repo"**
4. Choose your repository
5. Railway will automatically detect the Dockerfile

### 3. Configure Environment Variables

In your Railway project dashboard:

1. Go to your service → **Variables** tab
2. Add the following environment variables:

#### Required Variables (Optional but Recommended):
```env
# Project Configuration
PROJECT_NAME=Job Scraper API
DEBUG=False

# Proxy Configuration (if using proxies)
PROXY_URLS=http://user:pass@host:port,http://user:pass@host2:port2
PROXY_ROTATION_INTERVAL=240

# RapidAPI Key (optional, for Indeed API alternative)
RAPIDAPI_KEY=your_rapidapi_key_here

# Cache Settings
CACHE_TTL=3600
```

#### Important Notes:
- **PROXY_URLS**: Add your proxy URLs here if you want to avoid Cloudflare blocks
- **RAPIDAPI_KEY**: Get a free key from [rapidapi.com](https://rapidapi.com) for JSearch API
- **PORT**: Railway automatically sets this, don't override it

### 4. Deploy

Railway will automatically:
1. Build your Docker image using `Dockerfile.railway`
2. Install all dependencies
3. Start your FastAPI application
4. Assign a public URL

### 5. Access Your Application

1. Go to your service → **Settings** → **Networking**
2. Click **"Generate Domain"** to get a public URL
3. Your API will be available at: `https://your-app-name.up.railway.app`

### 6. Test Your Deployment

Test the API endpoints:

```bash
# Health check
curl https://your-app-name.up.railway.app/

# Scrape Indeed jobs
curl "https://your-app-name.up.railway.app/api/jobs?query=python%20developer&location=remote&max_results=10"

# Scrape ZipRecruiter jobs
curl "https://your-app-name.up.railway.app/api/jobs/ziprecruiter-enhanced?query=python%20developer&location=remote&max_results=10"

# Scrape a career page
curl "https://your-app-name.up.railway.app/api/jobs/scrape-url-get?url=https://example.com/careers&max_results=20"
```

## API Endpoints

Once deployed, your API will have these endpoints:

### 1. Health Check
- **GET** `/` - Returns welcome message

### 2. Indeed Jobs
- **GET** `/api/jobs?query={query}&location={location}&max_results={max}`
- Supports filters: `job_type`, `salary_min`, `salary_max`, `experience_level`, `employment_type`, `days_old`

### 3. ZipRecruiter Jobs
- **GET** `/api/jobs/ziprecruiter-enhanced?query={query}&location={location}&max_results={max}`
- Supports `job_type` filter

### 4. Generic Career Page Scraper
- **GET** `/api/jobs/scrape-url-get?url={url}&max_results={max}&search_query={query}`
- **POST** `/api/jobs/scrape-url` - Body: `{"url": "...", "max_results": 20, "search_query": "..."}`

### 5. Multiple Career Pages
- **POST** `/api/jobs/scrape-multiple-urls` - Body: `{"urls": ["...", "..."], "max_results_per_url": 20}`

## Configuration Tips

### Memory and CPU
- Railway free tier: 512MB RAM, 1 vCPU
- For heavy scraping, consider upgrading to a paid plan
- Monitor usage in Railway dashboard

### Scaling
- Railway can auto-scale based on traffic
- Configure in **Settings** → **Scaling**

### Logs
- View logs in Railway dashboard → **Deployments** → Click on a deployment
- Logs show scraping progress and errors

### Custom Domain
1. Go to **Settings** → **Networking**
2. Click **"Custom Domain"**
3. Add your domain and configure DNS

## Troubleshooting

### Issue: Chrome/ChromeDriver not found
**Solution**: The Dockerfile installs Chrome automatically. If issues persist, check logs.

### Issue: Out of memory errors
**Solution**: 
- Upgrade Railway plan for more RAM
- Reduce `max_results` in API calls
- Add delays between requests

### Issue: Cloudflare blocking
**Solution**: 
- Configure `PROXY_URLS` environment variable
- Use proxies from a reliable provider
- Increase delays in scraping

### Issue: Timeout errors
**Solution**:
- Railway has request timeout limits
- For long-running scrapes, consider:
  - Using background jobs
  - Breaking into smaller requests
  - Using Railway's cron jobs feature

### Issue: Port binding errors
**Solution**: Railway sets `PORT` automatically. Don't hardcode port 8000.

## Monitoring

### View Metrics
- **Metrics** tab: CPU, Memory, Network usage
- **Logs** tab: Real-time application logs

### Set Up Alerts
1. Go to **Settings** → **Notifications**
2. Configure alerts for:
   - Deployment failures
   - High resource usage
   - Application errors

## Cost Optimization

1. **Use Caching**: The app caches results (default 1 hour)
2. **Optimize Scraping**: Reduce `max_results` when possible
3. **Monitor Usage**: Check Railway dashboard regularly
4. **Free Tier Limits**: 
   - $5 free credit monthly
   - 512MB RAM
   - 1 vCPU

## Next Steps

1. **Set up CI/CD**: Railway auto-deploys on git push
2. **Add Monitoring**: Integrate with monitoring services
3. **Set up Cron Jobs**: Use Railway cron for scheduled scraping
4. **Add Authentication**: Protect your API endpoints
5. **Database Integration**: Store scraped jobs in a database

## Support

- Railway Docs: [docs.railway.app](https://docs.railway.app)
- Railway Discord: [discord.gg/railway](https://discord.gg/railway)
- Check application logs in Railway dashboard for debugging

## Example Usage

### Using cURL:
```bash
# Scrape Indeed
curl "https://your-app.up.railway.app/api/jobs?query=software%20engineer&location=remote&max_results=20"

# Scrape career page
curl "https://your-app.up.railway.app/api/jobs/scrape-url-get?url=https://company.com/careers&max_results=50"
```

### Using Python:
```python
import requests

# Scrape Indeed jobs
response = requests.get(
    "https://your-app.up.railway.app/api/jobs",
    params={
        "query": "python developer",
        "location": "remote",
        "max_results": 20,
        "job_type": "remote"
    }
)
jobs = response.json()
print(f"Found {len(jobs)} jobs")
```

### Using JavaScript/Node.js:
```javascript
const response = await fetch(
  'https://your-app.up.railway.app/api/jobs?query=python%20developer&location=remote&max_results=20'
);
const jobs = await response.json();
console.log(`Found ${jobs.length} jobs`);
```

---

**Happy Scraping! 🚀**

