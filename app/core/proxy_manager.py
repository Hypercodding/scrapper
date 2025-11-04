import time
import random
from typing import Optional, List
from urllib.parse import urlparse


class ProxyManager:
    """Manages proxy rotation to avoid Cloudflare challenges."""
    
    def __init__(self, proxy_urls: List[str], rotation_interval: int = 240):
        """
        Initialize the ProxyManager.
        
        Args:
            proxy_urls: List of proxy URLs (format: http://user:pass@host:port)
            rotation_interval: Time in seconds before rotating to next proxy (default: 240 = 4 minutes)
        """
        if not proxy_urls:
            raise ValueError("At least one proxy URL must be provided")
        
        self.proxy_urls = proxy_urls
        self.rotation_interval = rotation_interval
        self.current_proxy_index = 0
        self.last_rotation_time = time.time()
        self.proxy_failures = {url: 0 for url in proxy_urls}
        self.max_failures = 3  # Max failures before marking proxy as unhealthy
        
        # Validate proxy URLs
        for proxy_url in proxy_urls:
            self._validate_proxy_url(proxy_url)
    
    def _validate_proxy_url(self, proxy_url: str) -> bool:
        """Validate that proxy URL has the correct format."""
        try:
            parsed = urlparse(proxy_url)
            if not parsed.hostname or not parsed.port:
                raise ValueError(f"Invalid proxy URL '{proxy_url}': must include host and port")
            return True
        except Exception as e:
            raise ValueError(f"Invalid proxy URL '{proxy_url}': {str(e)}") from e
    
    def get_current_proxy(self) -> str:
        """Get the current active proxy URL."""
        return self.proxy_urls[self.current_proxy_index]
    
    def should_rotate(self) -> bool:
        """Check if it's time to rotate to the next proxy."""
        time_elapsed = time.time() - self.last_rotation_time
        return time_elapsed >= self.rotation_interval
    
    def rotate_proxy(self, force: bool = False) -> str:
        """
        Rotate to the next healthy proxy.
        
        Args:
            force: Force rotation even if time interval hasn't elapsed
            
        Returns:
            The new proxy URL
        """
        if not force and not self.should_rotate():
            return self.get_current_proxy()
        
        # Find next healthy proxy
        attempts = 0
        max_attempts = len(self.proxy_urls)
        
        while attempts < max_attempts:
            # Move to next proxy
            self.current_proxy_index = (self.current_proxy_index + 1) % len(self.proxy_urls)
            current_proxy = self.proxy_urls[self.current_proxy_index]
            
            # Check if proxy is healthy
            if self.proxy_failures[current_proxy] < self.max_failures:
                self.last_rotation_time = time.time()
                print(f"✓ Rotated to proxy {self.current_proxy_index + 1}/{len(self.proxy_urls)}: {self._mask_proxy(current_proxy)}")
                return current_proxy
            
            attempts += 1
        
        # If all proxies are unhealthy, reset failure counts and use first proxy
        print("⚠️  All proxies marked as unhealthy. Resetting failure counts...")
        self.reset_failures()
        self.current_proxy_index = 0
        self.last_rotation_time = time.time()
        return self.proxy_urls[0]
    
    def mark_proxy_failure(self, proxy_url: Optional[str] = None):
        """
        Mark a proxy as failed. If failures exceed threshold, proxy is marked unhealthy.
        
        Args:
            proxy_url: The proxy URL that failed (defaults to current proxy)
        """
        if proxy_url is None:
            proxy_url = self.get_current_proxy()
        
        if proxy_url in self.proxy_failures:
            self.proxy_failures[proxy_url] += 1
            failures = self.proxy_failures[proxy_url]
            
            if failures >= self.max_failures:
                print(f"⚠️  Proxy marked as unhealthy after {failures} failures: {self._mask_proxy(proxy_url)}")
                # Automatically rotate to next proxy
                self.rotate_proxy(force=True)
            else:
                print(f"⚠️  Proxy failure {failures}/{self.max_failures}: {self._mask_proxy(proxy_url)}")
    
    def mark_proxy_success(self, proxy_url: Optional[str] = None):
        """
        Mark a proxy as successful, resetting its failure count.
        
        Args:
            proxy_url: The proxy URL that succeeded (defaults to current proxy)
        """
        if proxy_url is None:
            proxy_url = self.get_current_proxy()
        
        if proxy_url in self.proxy_failures:
            # Reset failure count on success
            if self.proxy_failures[proxy_url] > 0:
                print(f"✓ Proxy recovered: {self._mask_proxy(proxy_url)}")
            self.proxy_failures[proxy_url] = 0
    
    def reset_failures(self):
        """Reset all proxy failure counts."""
        self.proxy_failures = {url: 0 for url in self.proxy_urls}
        print("✓ All proxy failure counts reset")
    
    def get_random_proxy(self) -> str:
        """Get a random healthy proxy (useful for parallel requests)."""
        healthy_proxies = [
            url for url in self.proxy_urls 
            if self.proxy_failures[url] < self.max_failures
        ]
        
        if not healthy_proxies:
            # Reset and return first proxy
            self.reset_failures()
            return self.proxy_urls[0]
        
        return random.choice(healthy_proxies)
    
    def get_proxy_stats(self) -> dict:
        """Get statistics about proxy health and usage."""
        stats = {
            "total_proxies": len(self.proxy_urls),
            "current_proxy_index": self.current_proxy_index,
            "current_proxy": self._mask_proxy(self.get_current_proxy()),
            "rotation_interval": self.rotation_interval,
            "time_since_last_rotation": time.time() - self.last_rotation_time,
            "proxy_health": {}
        }
        
        for url in self.proxy_urls:
            masked_url = self._mask_proxy(url)
            failures = self.proxy_failures[url]
            is_healthy = failures < self.max_failures
            stats["proxy_health"][masked_url] = {
                "failures": failures,
                "healthy": is_healthy
            }
        
        return stats
    
    def _mask_proxy(self, proxy_url: str) -> str:
        """Mask proxy credentials for logging."""
        try:
            parsed = urlparse(proxy_url)
            if parsed.username and parsed.password:
                # Mask password
                masked_user = parsed.username[:3] + "***" if len(parsed.username) > 3 else "***"
                return f"{parsed.scheme}://{masked_user}:***@{parsed.hostname}:{parsed.port}"
            return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        except Exception:  # pylint: disable=broad-except
            return "***masked***"


# Global proxy manager instance
_proxy_manager: Optional[ProxyManager] = None


def get_proxy_manager(proxy_urls: Optional[List[str]] = None, rotation_interval: int = 240) -> ProxyManager:
    """
    Get or create the global ProxyManager instance.
    
    Args:
        proxy_urls: List of proxy URLs (only used on first call)
        rotation_interval: Rotation interval in seconds (only used on first call)
        
    Returns:
        ProxyManager instance
    """
    global _proxy_manager  # pylint: disable=global-statement
    
    if _proxy_manager is None:
        if not proxy_urls:
            raise ValueError("proxy_urls must be provided on first call to get_proxy_manager")
        _proxy_manager = ProxyManager(proxy_urls, rotation_interval)
    
    return _proxy_manager


def reset_proxy_manager():
    """Reset the global proxy manager (useful for testing)."""
    global _proxy_manager  # pylint: disable=global-statement
    _proxy_manager = None

