from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Indeed Scraper"
    VERSION: str = "1.0.0"
    DEBUG: bool = True
    CACHE_TTL: int = 3600  # Cache time-to-live in seconds (1 hour)
    
    # Indeed scraping settings
    BASE_URL: str = "https://www.indeed.com/rss"
    # User-Agent. The previous default pinned Chrome/120 which Cloudflare's
    # bot-management script flags as a known-stale UA. Bumped to a current
    # stable major. Step 5 (FingerprintProfile) replaces this with a rotating
    # pool per session.
    USER_AGENT: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    MIN_DELAY: float = 2.0  # Minimum delay between requests in seconds
    PAGE_DELAY_MIN: float = 2.0  # Min per-page human think time
    PAGE_DELAY_MAX: float = 5.8  # Max per-page human think time
    HUMANIZE: bool = True  # Enable human-like interactions (mouse/scroll)
    MAX_RETRIES: int = 3  # Soft-retries when Cloudflare page detected
    BACKOFF_MIN: float = 2.0  # Min backoff between Cloudflare retries
    BACKOFF_MAX: float = 8.0  # Max backoff between Cloudflare retries
    # Proxy configuration - supports multiple proxies for rotation
    # Format: comma-separated list of proxy URLs: http://user:pass@host:port
    # http://oliqrnln:crmu361j609a@142.111.48.253:7030,http://oliqrnln:crmu361j609a@107.172.163.27:6543,http://oliqrnln:crmu361j609a@198.23.239.134:6540
    # Note: Webshare proxies - ensure bandwidth is available on your account
    # Error 402 = bandwidth limit exceeded, need to add credits or wait for reset
    # Leave empty for direct connection. Set via .env / Railway Variables on worker service.
    # Example: PROXY_URLS=http://user:pass@proxy1:port,http://user:pass@proxy2:port
    PROXY_URLS: str = ""
    PROXY_ROTATION_INTERVAL: int = 240  # Rotate proxy every 240 seconds (4 minutes)
    
    # Legacy single proxy support (deprecated, use PROXY_URLS instead)
    PROXY_URL: str = ""  # For backwards compatibility
    
    ACCEPT_LANGUAGE: str = "en-US,en;q=0.9"
    
    # Chrome driver management
    CLEANUP_DRIVER_AFTER_SCRAPE: bool = True  # Kill Chrome completely after each scrape (prevents resource buildup)
    DRIVER_IDLE_TIMEOUT: int = 60  # Seconds before killing idle driver (0 = keep alive indefinitely)
    
    # JSearch API (for Indeed jobs without scraping)
    RAPIDAPI_KEY: str = ""  # Get free key at rapidapi.com

    # Redis / queue (see also app.core.settings_workers)
    REDIS_URL: str = "redis://localhost:6379/0"

    # Description quality gate. Detail-page descriptions shorter than this
    # are treated as a parse/block failure rather than shipped as-is.
    # 500 chars ≈ one full job-description paragraph; below this is almost
    # always card-snippet fallback or Cloudflare interstitial leftovers.
    MIN_DESCRIPTION_LEN: int = 500

    # When True, jobs whose detail fetch fails after retries are dropped
    # from the primary task result (and re-enqueued via the per-jk retry
    # queue if that path is enabled). When False, they are retained with
    # detail_fetch_status != "ok" so clients can decide.
    STRICT_DESCRIPTION_MODE: bool = True

    # Cap on per-jk retries enqueued for one search batch. Prevents a
    # pathological batch (e.g. Cloudflare hard-blocked the worker) from
    # flooding scrape.indeed.retry.
    MAX_PER_JK_RETRIES_PER_BATCH: int = 25

    # When True, the worker imports `patchright` instead of `playwright`
    # and launches via Patchright's `channel="chrome"` to use the real
    # google-chrome-stable binary installed in Dockerfile.railway.
    # Closes the CDP Runtime.Enable leak that stock Playwright exposes —
    # the single largest detection signal Cloudflare's bot-management
    # script looks for.
    USE_PATCHRIGHT: bool = False

    # Chrome channel passed to launch_persistent_context / launch. Only
    # honored when USE_PATCHRIGHT=True. Valid: "chrome" | "chrome-beta" |
    # "msedge" | "" (bundled Chromium).
    BROWSER_CHANNEL: str = "chrome"

    class Config:  # pylint: disable=R0903
        env_file = ".env"
        extra = "ignore"  # Ignore extra fields from .env file


settings = Settings()
