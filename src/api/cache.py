"""Simple in-memory TTL cache for expensive API queries.

No external dependencies — just a dict with timestamp-based expiry.
Thread-safe via a lock. Use for read-heavy endpoints like /people, /topics.
"""

import threading
import time
from typing import Any, Callable

_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()

# Default TTL: 60 seconds. Hot data stays cached, stale data expires.
DEFAULT_TTL = 60


def cached(key: str, ttl: int = DEFAULT_TTL) -> Callable:
    """Decorator that caches the return value of a function by key.

    Usage:
        @cached("people_list")
        def list_people(db):
            ...
    """
    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            with _lock:
                if key in _cache:
                    expires_at, value = _cache[key]
                    if time.monotonic() < expires_at:
                        return value

            # Cache miss — execute and store
            result = func(*args, **kwargs)
            with _lock:
                _cache[key] = (time.monotonic() + ttl, result)
            return result

        wrapper.__wrapped__ = func
        return wrapper

    return decorator


def invalidate(key: str) -> None:
    """Remove a specific key from the cache."""
    with _lock:
        _cache.pop(key, None)


def invalidate_all() -> None:
    """Clear the entire cache. Call after admin mutations."""
    with _lock:
        _cache.clear()


def get_cache_stats() -> dict:
    """Return cache stats for debugging."""
    now = time.monotonic()
    with _lock:
        return {
            "entries": len(_cache),
            "keys": list(_cache.keys()),
            "active": sum(1 for _, (exp, _) in _cache.items() if exp > now),
        }
