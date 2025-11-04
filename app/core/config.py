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
    PROXY_URLS: str = "http://ydgjcfkg:akhpmonf8vdj@142.111.48.253:7030,http://ydgjcfkg:akhpmonf8vdj@142.111.67.146:5611,http://ydgjcfkg:akhpmonf8vdj@142.147.128.93:6593"  # Add more proxies separated by commas
    PROXY_ROTATION_INTERVAL: int = 240  # Rotate proxy every 240 seconds (4 minutes)
    
    # Legacy single proxy support (deprecated, use PROXY_URLS instead)
    PROXY_URL: str = ""  # For backwards compatibility
    
    ACCEPT_LANGUAGE: str = "en-US,en;q=0.9"
    
    # JSearch API (for Indeed jobs without scraping)
    RAPIDAPI_KEY: str = ""  # Get free key at rapidapi.com

    class Config:  # pylint: disable=R0903
        env_file = ".env"
        extra = "ignore"  # Ignore extra fields from .env file


settings = Settings()
