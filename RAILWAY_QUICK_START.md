# Railway Quick Start Guide

## 🚀 Quick Deployment (5 minutes)

### 1. Push to GitHub
```bash
git add .
git commit -m "Add Railway deployment files"
git push origin main
```

### 2. Deploy on Railway
1. Go to [railway.app](https://railway.app) → **New Project**
2. Select **"Deploy from GitHub repo"**
3. Choose your repository
4. Railway auto-detects and deploys!

### 3. Configure Environment Variables (Optional)
In Railway dashboard → **Variables** tab, add:
```env
PROXY_URLS=your_proxy_urls_here
RAPIDAPI_KEY=your_key_here
DEBUG=False
```

### 4. Get Your URL
Railway → **Settings** → **Networking** → **Generate Domain**

### 5. Test It!
```bash
curl https://your-app.up.railway.app/health
```

## 📋 Files Created for Railway

- ✅ `Dockerfile.railway` - Docker configuration with Chrome
- ✅ `railway.json` - Railway deployment config
- ✅ `Procfile` - Process definition
- ✅ `.railwayignore` - Files to exclude
- ✅ `RAILWAY_DEPLOYMENT_GUIDE.md` - Full guide

## 🔗 API Endpoints

Once deployed, test these:

```bash
# Health check
GET https://your-app.up.railway.app/health

# Scrape Indeed
GET https://your-app.up.railway.app/api/jobs?query=python&location=remote

# Scrape ZipRecruiter
GET https://your-app.up.railway.app/api/jobs/ziprecruiter-enhanced?query=python&location=remote

# Scrape career page
GET https://your-app.up.railway.app/api/jobs/scrape-url-get?url=https://example.com/careers
```

## ⚠️ Important Notes

1. **Port**: Railway sets `PORT` automatically - don't override it
2. **Memory**: Free tier has 512MB RAM - monitor usage
3. **Chrome**: Already installed in Dockerfile - no extra setup needed
4. **Proxies**: Add `PROXY_URLS` env var to avoid Cloudflare blocks

## 🆘 Troubleshooting

- **Build fails?** Check logs in Railway dashboard
- **Out of memory?** Reduce `max_results` or upgrade plan
- **Chrome errors?** Already handled in Dockerfile
- **Timeout?** Railway has request limits - use smaller batches

## 📚 Full Documentation

See `RAILWAY_DEPLOYMENT_GUIDE.md` for complete details.

---

**That's it! Your scraper is now live on Railway! 🎉**

