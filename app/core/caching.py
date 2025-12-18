import asyncio
import time

_cache = {}
_cache_lock = asyncio.Lock()


async def get_cache(key: str):
    async with _cache_lock:
        entry = _cache.get(key)
        if not entry:
            return None
        ts, data, ttl = entry
        if time.time() - ts > ttl:
            del _cache[key]
            return None
        return data


async def set_cache(key: str, data, ttl: int = 300):
    async with _cache_lock:
        _cache[key] = (time.time(), data, ttl)


async def clear_cache(key: str = None):
    """
    Clear cache entries.
    
    Args:
        key: If provided, clears only that specific cache key.
             If None, clears all cache entries.
    
    Returns:
        int: Number of cache entries cleared
    """
    async with _cache_lock:
        if key is not None:
            if key in _cache:
                del _cache[key]
                return 1
            return 0
        else:
            count = len(_cache)
            _cache.clear()
            return count


async def get_cache_stats():
    """
    Get cache statistics.
    
    Returns:
        dict: Cache statistics including total entries and memory usage estimate
    """
    async with _cache_lock:
        total_entries = len(_cache)
        expired_count = 0
        current_time = time.time()
        
        for entry in _cache.values():
            ts, _, ttl = entry
            if current_time - ts > ttl:
                expired_count += 1
        
        return {
            "total_entries": total_entries,
            "expired_entries": expired_count,
            "active_entries": total_entries - expired_count
        }
