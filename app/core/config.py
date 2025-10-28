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
    PROXY_URL: str = ""  # Optional proxy, e.g., http://user:pass@host:port
    ACCEPT_LANGUAGE: str = "en-US,en;q=0.9"
    
    # JSearch API (for Indeed jobs without scraping)
    RAPIDAPI_KEY: str = ""  # Get free key at rapidapi.com

    class Config:
        env_file = ".env"
        extra = "ignore"  # Ignore extra fields from .env file


settings = Settings()
