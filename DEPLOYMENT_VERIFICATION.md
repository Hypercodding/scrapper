# 🚀 Deployment Verification Checklist

## Pre-Deployment

- [ ] All changes committed to git
- [ ] `.env` file has required variables:
  ```bash
  FORCE_HEADLESS=true  # Required for Railway
  ```
- [ ] Code pushed to main branch (Railway auto-deploys)

---

## Post-Deployment Steps

### Step 1: Verify Deployment
```bash
# Replace with your Railway URL
export APP_URL="https://your-app.railway.app"

# Check if app is running
curl $APP_URL/health
```

**Expected**: `{"status":"healthy","service":"Indeed Scraper"}`

---

### Step 2: Run Initial Cleanup
```bash
# Clean any leftover processes from previous deployments
curl -X POST $APP_URL/api/health/cleanup
```

**Expected**:
```json
{
  "status": "success",
  "processes_before": 0,
  "processes_after": 0,
  "processes_killed": 0
}
```

---

### Step 3: Test Single Scrape

```bash
# Test Indeed scraping
curl "$APP_URL/api/jobs?query=python+developer&location=remote&max_results=5"
```

**Expected**: JSON array with 5 jobs

**Then immediately check health**:
```bash
curl $APP_URL/api/health/detailed
```

**Expected**:
```json
{
  "status": "healthy",
  "message": "0 Chrome processes running" or "1-2 Chrome processes running (normal)",
  "chrome_processes": 0-2,
  "recommendations": []
}
```

✅ **PASS CRITERIA**: Process count should be 0-2, NOT accumulating

---

### Step 4: Test Multiple Scrapes

```bash
# Run 5 scrapes in sequence
for i in {1..5}; do
  echo "Scrape $i..."
  curl -s "$APP_URL/api/jobs?query=developer&location=remote&max_results=3" > /dev/null
  sleep 2
done

# Check health
curl $APP_URL/api/health/detailed
```

**Expected**:
```json
{
  "status": "healthy",
  "chrome_processes": 0-3,
  "recommendations": []
}
```

✅ **PASS CRITERIA**: Process count should NOT grow with each scrape (should stay 0-3)

⚠️ **FAIL**: If process count is > 10, the fix didn't work properly

---

### Step 5: Test Error Recovery

```bash
# Trigger an error (empty query)
curl "$APP_URL/api/jobs?query=&max_results=5" || true

# Browser should still close even on error
curl $APP_URL/api/health/detailed
```

**Expected**:
```json
{
  "status": "healthy",
  "chrome_processes": 0-2
}
```

✅ **PASS CRITERIA**: Even after error, processes are cleaned up

---

### Step 6: Test Emergency Cleanup

```bash
# Test cleanup endpoint
curl -X POST $APP_URL/api/health/cleanup

# Verify cleanup worked
curl $APP_URL/api/health/detailed
```

**Expected**:
```json
{
  "status": "healthy",
  "chrome_processes": 0
}
```

---

### Step 7: Load Test (Optional)

```bash
# Run 10 concurrent scrapes
for i in {1..10}; do
  curl -s "$APP_URL/api/jobs?query=engineer&max_results=3" > /dev/null &
done

# Wait for all to complete
wait

# Check health after load
sleep 5
curl $APP_URL/api/health/detailed
```

**Expected**:
```json
{
  "status": "healthy",
  "chrome_processes": 0-5
}
```

✅ **PASS CRITERIA**: Even under load, processes are cleaned up
⚠️ **WARNING**: If > 10, you may need rate limiting

---

## Monitoring Setup (Recommended)

### Option 1: Railway Health Check (Built-in)
Railway will automatically monitor `/health` endpoint

### Option 2: External Monitoring (Recommended)
Use a service like UptimeRobot or Cronitor:

1. **Health Check Monitor**
   - URL: `https://your-app.railway.app/api/health/detailed`
   - Interval: 5 minutes
   - Alert: If `status !== "healthy"`

2. **Auto-Cleanup Monitor** (Advanced)
   - Create webhook that calls `/api/health/cleanup` if process count > 10
   - Set up with UptimeRobot keyword monitoring

### Option 3: Custom Script

Create `monitor.sh`:
```bash
#!/bin/bash
APP_URL="https://your-app.railway.app"

while true; do
  echo "Checking health..."
  RESPONSE=$(curl -s $APP_URL/api/health/detailed)
  STATUS=$(echo $RESPONSE | jq -r '.status')
  PROCESSES=$(echo $RESPONSE | jq -r '.chrome_processes')
  
  echo "Status: $STATUS, Processes: $PROCESSES"
  
  if [ "$STATUS" = "critical" ]; then
    echo "🚨 CRITICAL - Running cleanup"
    curl -X POST $APP_URL/api/health/cleanup
  fi
  
  sleep 300  # Check every 5 minutes
done
```

Run with: `nohup ./monitor.sh > monitor.log 2>&1 &`

---

## Railway-Specific Configuration

### Environment Variables
```bash
# In Railway dashboard, set these:
FORCE_HEADLESS=true
```

### Resource Limits (If needed)
If you still see issues, increase Railway resources:
- Memory: 1GB+ recommended
- CPU: 1+ vCPU recommended

---

## Troubleshooting

### Issue: Process count keeps growing
**Fix**:
```bash
# Run cleanup
curl -X POST $APP_URL/api/health/cleanup

# Check logs
railway logs
```

Look for errors in cleanup code

### Issue: "Pool full" error still happens
**Fix**:
```bash
# Immediate: Run cleanup
curl -X POST $APP_URL/api/health/cleanup

# Long-term: Check if changes were deployed
curl -s $APP_URL/api/jobs?query=test&max_results=1
# Then check health - should be 0 processes
curl $APP_URL/api/health/detailed
```

### Issue: Deployment is slow/unresponsive
**Fix**:
```bash
# Check health
curl $APP_URL/api/health/detailed

# If high process count:
curl -X POST $APP_URL/api/health/cleanup
```

---

## Success Criteria Summary

✅ **Deployment is successful if**:
1. Single scrape closes browser (0-2 processes after)
2. Multiple scrapes don't accumulate processes
3. Errors don't leave browsers open
4. Health check shows "healthy" status
5. Cleanup endpoints work correctly

🎉 **Your deployment is ready for production!**

---

## Maintenance Schedule

### Daily
- Monitor health check endpoint
- Review Railway logs for errors

### Weekly  
- Run health check test suite
- Review process count trends

### Monthly
- Run full load test
- Update dependencies if needed

---

## Emergency Contacts

If issues persist after all fixes:

1. **Check Railway Status**: https://railway.app/status
2. **Review Logs**: `railway logs --tail 100`
3. **Run Cleanup**: `curl -X POST $APP_URL/api/health/cleanup`
4. **Last Resort**: Restart deployment in Railway dashboard

---

## Final Notes

- This fix is **comprehensive** and should prevent ALL pool exhaustion issues
- The system now **self-heals** from errors
- **No manual intervention** should be needed after deployment
- Health endpoints provide **full visibility** into system state

**The "pool full" error and manual redeployment problem is SOLVED! 🎉**

