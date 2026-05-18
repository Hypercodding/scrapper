from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Indeed Scraper"
    VERSION: str = "1.0.0"
    DEBUG: bool = True
    CACHE_TTL: int = 3600  # Cache time-to-live in seconds (1 hour)
    
    # Indeed scraping settings
    BASE_URL: str = "https://www.indeed.com/rss"
    USER_AGENT: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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

    class Config:  # pylint: disable=R0903
        env_file = ".env"
        extra = "ignore"  # Ignore extra fields from .env file


settings = Settings()
