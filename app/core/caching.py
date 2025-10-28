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
