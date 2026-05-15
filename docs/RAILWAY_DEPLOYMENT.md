# Railway Deployment Guide

**Career Scraper API — Production deployment with 3 workers**

This document walks through deploying the Career Scraper stack on [Railway](https://railway.app) from scratch. It assumes no prior Railway experience. The target topology is:

- **1× API service** — accepts scrape requests and enqueues jobs  
- **1× Worker service** — runs Celery + Chrome; scaled to **3 replicas** (3 parallel scrapes)  
- **1× Redis** — job state and Celery message broker  

---

## Table of contents

1. [Architecture overview](#1-architecture-overview)  
2. [Prerequisites](#2-prerequisites)  
3. [How scaling works (3 workers)](#3-how-scaling-works-3-workers)  
4. [Prepare your repository](#4-prepare-your-repository)  
5. [Create a Railway project](#5-create-a-railway-project)  
6. [Add Redis](#6-add-redis)  
7. [Deploy the API service](#7-deploy-the-api-service)  
8. [Deploy the worker service (3 replicas)](#8-deploy-the-worker-service-3-replicas)  
9. [Environment variables reference](#9-environment-variables-reference)  
10. [Verify the deployment](#10-verify-the-deployment)  
11. [Using the API](#11-using-the-api)  
12. [Operations and monitoring](#12-operations-and-monitoring)  
13. [Troubleshooting](#13-troubleshooting)  
14. [Cost and capacity notes](#14-cost-and-capacity-notes)  

---

## 1. Architecture overview

When a client requests a career page scrape, the API does **not** run the scrape itself. It stores job metadata in Redis and pushes a task to a queue. Any available worker picks up the task.

```mermaid
flowchart LR
    Client([Client / App])
    API[API Service<br/>FastAPI]
    Redis[(Redis)]
    W1[Worker replica 1]
    W2[Worker replica 2]
    W3[Worker replica 3]

    Client -->|POST /api/jobs/scrape| API
    API -->|enqueue job| Redis
    W1 <-->|broker + job state| Redis
    W2 <-->|broker + job state| Redis
    W3 <-->|broker + job state| Redis
    Client -->|GET /api/jobs/id| API
    API -->|read status| Redis
```

| Component | Responsibility | Railway setting |
|-----------|----------------|-----------------|
| **API** | HTTP, validation, enqueue, status polling | 1 replica, ~512 MB–1 GB RAM |
| **Worker** | Celery + Selenium/Chrome scraping | **3 replicas**, ~2–4 GB RAM each |
| **Redis** | Queue + job store | Railway Redis plugin or external Redis Cloud |

**Capacity with this guide:**  
`3 replicas × WORKER_CONCURRENCY=1` → **up to 3 career page scrapes at the same time**. Additional requests wait in the queue until a worker is free.

---

## 2. Prerequisites

Before you begin, ensure you have:

| Requirement | Notes |
|-------------|--------|
| **GitHub account** | Railway deploys from a connected repository |
| **This repository pushed to GitHub** | `main` (or your default) branch should be up to date |
| **Railway account** | Sign up at [railway.app](https://railway.app) |
| **No secrets in git** | Use Railway Variables for `REDIS_URL`; never commit `.env` |

Optional: an existing **Redis Cloud** URL (`rediss://...`) instead of Railway’s Redis plugin.

---

## 3. How scaling works (3 workers)

Two settings are often confused:

| Setting | Where | What it does |
|---------|--------|----------------|
| **Replicas** | Railway UI → Worker service → **Settings → Deploy → Replicas** | Number of **containers** running the worker (this guide: **3**) |
| **`WORKER_CONCURRENCY`** | Environment variable on the worker service | Scrapes per **single** container (recommended: **1**) |

There is **no** environment variable for replica count in this project. Replicas are configured only in Railway.

```text
Max parallel scrapes = Replicas × WORKER_CONCURRENCY
                     = 3 × 1
                     = 3
```

Each replica is a separate machine running `start-worker.sh` and sharing the same `REDIS_URL`.

---

## 4. Prepare your repository

Your repo already includes the files Railway needs:

| File | Purpose |
|------|---------|
| `Dockerfile.railway` | Builds Python 3.13, Chrome, ChromeDriver, Playwright, and the app |
| `railway.json` | API: Dockerfile + `sh start-api.sh` |
| `railway.worker.json` | Worker: Dockerfile + `sh start-worker.sh` |
| `start-api.sh` | Starts FastAPI on `$PORT` |
| `start-worker.sh` | Starts Celery worker on queues `scrape.default`, `scrape.retry` |

**Checklist before deploy:**

1. Commit and push all changes to GitHub.  
2. Confirm `.env` is listed in `.gitignore` (it is).  
3. Do not commit API keys or `REDIS_URL` to the repository.

---

## 5. Create a Railway project

1. Log in to [Railway](https://railway.app).  
2. Click **New Project**.  
3. Choose **Deploy from GitHub repo**.  
4. Authorize Railway to access GitHub if prompted.  
5. Select this repository (`scrapper`).  
6. Railway creates a **first service** from the repo. You will treat this as the **API** service in the next steps.

Rename the service for clarity:

- Click the service → **Settings** → **General** → **Service name** → set to `api`.

---

## 6. Add Redis

All services must share the **same** Redis URL.

### Option A — Railway Redis (recommended for beginners)

1. In your project, click **+ New**.  
2. Select **Database** → **Add Redis**.  
3. Open the new **Redis** service.  
4. Go to the **Variables** or **Connect** tab and copy **`REDIS_URL`** (or the private URL Railway provides).

You will paste this value into both the **api** and **worker** services.

### Option B — Redis Cloud (external)

If you already use Redis Cloud:

1. Use your `rediss://default:password@host:port` URL.  
2. Ensure Railway services can reach it (no IP allowlist blocking Railway, or allow Railway egress).  
3. Set the same URL as `REDIS_URL` on **api** and **worker**.

---

## 7. Deploy the API service

Configure the service Railway created first (renamed `api`).

### 7.1 Build settings

1. Open the **api** service → **Settings**.  
2. Under **Build**:
   - **Builder:** Dockerfile  
   - **Dockerfile path:** `Dockerfile.railway`  
3. Under **Deploy**:
   - **Custom start command:** `sh start-api.sh`  
   - (Alternatively, rely on root `railway.json`, which already sets this.)

### 7.2 Resources

| Setting | Recommended value |
|---------|-------------------|
| **Replicas** | `1` |
| **Memory** | `512 MB` – `1 GB` |

The API does not need Chrome at runtime for the production Celery path.

### 7.3 Public URL

1. Go to **Settings → Networking** (or **Networking** tab).  
2. Click **Generate Domain**.  
3. Note your URL, e.g. `https://api-production-xxxx.up.railway.app`.

### 7.4 Environment variables (API)

Open **Variables** on the **api** service and add:

| Variable | Value | Required |
|----------|--------|----------|
| `REDIS_URL` | From Redis service (Step 6) | Yes |
| `SERVICE_ROLE` | `api` (optional; `start-api.sh` sets this) | No |
| `JOB_RESULT_TTL_SECONDS` | `86400` | Optional |
| `CELERY_BROKER_URL` | Leave empty (uses `REDIS_URL`) | No |
| `CELERY_RESULT_BACKEND` | Leave empty (uses `REDIS_URL`) | No |

Do **not** set `WORKER_CONCURRENCY` on the API unless you have a specific reason.

### 7.5 Deploy

Click **Deploy** (or push to GitHub if auto-deploy is enabled).  
Watch **Deployments → View logs** until you see Uvicorn listening on a port, e.g.:

```text
INFO:     Uvicorn running on http://0.0.0.0:8080
```

### 7.6 Quick health check

```bash
curl https://YOUR-API-DOMAIN.up.railway.app/health
```

Expected:

```json
{"status":"healthy","service":"..."}
```

---

## 8. Deploy the worker service (3 replicas)

The worker runs the same Docker image but starts Celery instead of Uvicorn.

### 8.1 Create the worker service

1. In the same Railway project, click **+ New**.  
2. Choose **GitHub Repo** → select the **same repository** again.  
3. Name the service `worker`.

You now have two services built from one repo: `api` and `worker`.

### 8.2 Build settings (worker)

1. Open **worker** → **Settings → Build**:
   - **Dockerfile path:** `Dockerfile.railway`  
2. **Settings → Deploy**:
   - **Custom start command:** `sh start-worker.sh`  

   **Optional:** If your Railway plan supports a per-service config file, set **Config file path** to `railway.worker.json`. Otherwise the custom start command above is sufficient.

### 8.3 Scale to 3 replicas

1. Open **worker** → **Settings → Deploy** (or **Scaling**).  
2. Set **Replicas** to **`3`**.  
3. Save. Railway will run three identical worker containers.

### 8.4 Resources (per replica)

Chrome and Selenium are memory-intensive. Per replica:

| Setting | Recommended value |
|---------|-------------------|
| **Memory** | `2048 MB` (2 GB) minimum; `4096 MB` (4 GB) if scrapes fail with OOM |
| **CPU** | Default or one step above default if available |

### 8.5 Environment variables (worker)

On the **worker** service → **Variables**, use the **same** `REDIS_URL` as the API:

| Variable | Value | Required |
|----------|--------|----------|
| `REDIS_URL` | Same as API | Yes |
| `WORKER_CONCURRENCY` | `1` | Yes |
| `FORCE_HEADLESS` | `true` | Yes |
| `CHROME_BIN` | `/usr/bin/google-chrome` | Yes (matches Dockerfile) |
| `CHROMEDRIVER_PATH` | `/usr/local/bin/chromedriver` | Yes |
| `MAX_WORKER_MEMORY_MB` | `3500` | Recommended |
| `JOB_RESULT_TTL_SECONDS` | `86400` | Optional |
| `CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | Optional |
| `CIRCUIT_BREAKER_WINDOW_SECONDS` | `600` | Optional |

`SERVICE_ROLE` is set automatically by `start-worker.sh` to `worker`.

### 8.6 Networking

The worker service does **not** need a public domain. Leave public networking disabled unless you need direct access for debugging.

### 8.7 Deploy and confirm logs

Deploy the worker service. In logs you should see something like:

```text
celery@... ready.
```

and queues:

```text
scrape.default, scrape.retry
```

Repeat for each replica if Railway shows separate deployment units, or one deploy scales all three.

### 8.8 Shared variables (optional)

To avoid duplicating `REDIS_URL`:

1. Project → **Variables** → **Shared Variable**.  
2. Create `REDIS_URL` once.  
3. Reference it from both `api` and `worker` services.

---

## 9. Environment variables reference

### API service

```env
REDIS_URL=redis://default:password@redis.railway.internal:6379
JOB_RESULT_TTL_SECONDS=86400
```

### Worker service (each of 3 replicas)

```env
REDIS_URL=redis://default:password@redis.railway.internal:6379
WORKER_CONCURRENCY=1
FORCE_HEADLESS=true
CHROME_BIN=/usr/bin/google-chrome
CHROMEDRIVER_PATH=/usr/local/bin/chromedriver
MAX_WORKER_MEMORY_MB=3500
JOB_RESULT_TTL_SECONDS=86400
```

Copy from `.env.example` in the repo for local development; production values live only in Railway.

---

## 10. Verify the deployment

Replace `YOUR-API-DOMAIN` with your generated Railway domain.

### 10.1 Basic health

```bash
curl -s https://YOUR-API-DOMAIN.up.railway.app/health | jq
```

### 10.2 Worker connectivity

```bash
curl -s https://YOUR-API-DOMAIN.up.railway.app/api/health/workers | jq
```

Healthy deployment example:

```json
{
  "status": "healthy",
  "workers": ["celery@hostname1", "celery@hostname2", "celery@hostname3"],
  "active_tasks": 0,
  "ping": { ... }
}
```

If `workers` is empty or `status` is `degraded`, the worker service is not connected to Redis or Celery is not running.

### 10.3 Queue depth

```bash
curl -s https://YOUR-API-DOMAIN.up.railway.app/api/health/queue | jq
```

### 10.4 End-to-end scrape test

Enqueue a scrape (Greenhouse example — usually fast and reliable):

```bash
curl -s -X POST https://YOUR-API-DOMAIN.up.railway.app/api/jobs/scrape \
  -H "Content-Type: application/json" \
  -d '{"url": "https://boards.greenhouse.io/stripe"}' | jq
```

Response (HTTP 202):

```json
{
  "job_id": "uuid-here",
  "status": "pending",
  "message": "Scrape job enqueued.",
  "status_url": "/api/jobs/uuid-here",
  "estimated_time": "5-30 minutes"
}
```

Poll status every 10–30 seconds:

```bash
curl -s https://YOUR-API-DOMAIN.up.railway.app/api/jobs/JOB_ID | jq
```

When `status` is `completed`, fetch results:

```bash
curl -s https://YOUR-API-DOMAIN.up.railway.app/api/jobs/JOB_ID/result | jq
```

### 10.5 Verify 3-way parallelism (optional)

Enqueue **four** scrapes in quick succession. With 3 replicas, you should see **three** in `processing` and one `pending` in status responses until a worker finishes.

---

## 11. Using the API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/jobs/scrape` | Enqueue scrape (body: `url`, optional `max_results`, `search_query`) |
| `GET` | `/api/jobs/{job_id}` | Status, progress, and result when complete |
| `GET` | `/api/jobs/{job_id}/result` | Jobs list only when `completed` |
| `DELETE` | `/api/jobs/{job_id}` | Cancel pending/processing job |
| `GET` | `/api/health/workers` | Celery worker ping |
| `GET` | `/api/health/queue` | Queue lengths |
| `GET` | `/health` | Simple liveness for Railway |

**Request body example:**

```json
{
  "url": "https://careers.example.com",
  "max_results": 100,
  "search_query": "engineer"
}
```

**Typical integration flow:**

1. `POST /api/jobs/scrape` → store `job_id`  
2. Poll `GET /api/jobs/{job_id}` until `status` is `completed` or `failed`  
3. Read `result` from status response or call `GET /api/jobs/{job_id}/result`  

---

## 12. Operations and monitoring

### Railway dashboards

| Service | What to watch |
|---------|----------------|
| **api** | HTTP errors, memory, deploy failures |
| **worker** | OOM restarts, Celery tracebacks, Chrome crashes |
| **redis** | Memory usage, connection limits |

### Logs

- **api:** Uvicorn access logs, enqueue errors (`503` = Redis/Celery unavailable)  
- **worker:** `Job {id} completed: N jobs` or exception stack traces  

### Scaling later

| Goal | Action |
|------|--------|
| More parallel scrapes | Increase **worker replicas** (e.g. 3 → 5) |
| Heavier single host | Not recommended; prefer more replicas with `WORKER_CONCURRENCY=1` |
| Less queue wait | Add replicas; monitor `/api/health/queue` |

### Redeploy after code changes

Push to GitHub. If both services use the same repo, Railway redeploys each connected service. Confirm both **api** and **worker** show a successful deployment.

---

## 13. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|----------------|-----|
| `503 Job queue unavailable` | Workers down or wrong `REDIS_URL` on API | Match `REDIS_URL` on api + worker; confirm worker logs show `ready` |
| Jobs stay `pending` forever | No workers or Redis unreachable | Deploy worker; check Redis URL and network |
| `workers: []` in `/api/health/workers` | Celery not running or different Redis than API | Same `REDIS_URL` on all services |
| Worker crashes / OOM | Insufficient RAM | Set worker memory to 2–4 GB per replica |
| `start-api.sh` / `start-worker.sh` not found | Old Docker image | Redeploy after latest `Dockerfile.railway` (copies startup scripts) |
| Scrape `failed` for one site | Site blocking or timeout | Check worker logs; try ATS URL (e.g. Greenhouse board URL) |
| Build takes very long | Chrome + Playwright in image | Normal (~5–15 min first build); use Railway cache |

### Useful CLI (optional)

Install [Railway CLI](https://docs.railway.app/develop/cli) and link the project:

```bash
railway login
railway link
railway logs --service worker
```

---

## 14. Cost and capacity notes

With **3 worker replicas** at ~2 GB RAM each plus API and Redis:

- You pay for **3 worker containers + 1 API + Redis** usage on Railway’s plan.  
- **Throughput:** roughly `3 ÷ (average scrape duration in minutes)` concurrent pipelines; queued jobs run when a replica frees up.  
- **Clients:** 20–30 clients sharing the pool is normal; they do not get dedicated workers unless you build that separately.

**Summary configuration for this guide:**

```text
Project
├── redis          (Railway Redis or Redis Cloud)
├── api            (1 replica, start: sh start-api.sh, public domain)
└── worker         (3 replicas, start: sh start-worker.sh, WORKER_CONCURRENCY=1)
```

---

## Quick reference checklist

- [ ] GitHub repo connected to Railway  
- [ ] Redis provisioned; `REDIS_URL` copied  
- [ ] **api** service: `Dockerfile.railway`, `sh start-api.sh`, domain generated  
- [ ] **worker** service: `Dockerfile.railway`, `sh start-worker.sh`, **replicas = 3**, 2+ GB RAM  
- [ ] Same `REDIS_URL` on api and worker  
- [ ] `WORKER_CONCURRENCY=1` on worker  
- [ ] `/health` returns healthy  
- [ ] `/api/health/workers` lists 3 workers  
- [ ] Test `POST /api/jobs/scrape` completes successfully  

---

*Document version: 1.0 — aligned with `railway.json`, `railway.worker.json`, and `Dockerfile.railway` in this repository.*
