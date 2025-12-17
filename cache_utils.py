"""
Cache utilities for Redis-based caching of reports and other data.
"""

import hashlib
import json
import redis
from typing import Any, Optional
import logging
import time
import config

logger = logging.getLogger(__name__)

# Initialize Redis client (singleton pattern)
_redis_client = None

# Failure state cache - track when Redis failed to avoid repeated connection attempts
_redis_failed_until = None  # Timestamp when we can retry again (None = no failure recorded)
_redis_cooldown_logged = False  # Track if we've already logged the cooldown message for this period
REDIS_FAILURE_COOLDOWN_SECONDS = 1800  # 30 minutes cooldown period


def get_redis_client():
    """
    Get or create the Redis client instance.
    Returns None if Redis is disabled or if we're in a failure cooldown period.
    """
    global _redis_client, _redis_failed_until, _redis_cooldown_logged
    if not config.REDIS_ENABLED:
        return None
    
    # Check if we're in failure cooldown period
    current_time = time.time()
    if _redis_failed_until is not None and current_time < _redis_failed_until:
        # Still in cooldown - skip connection attempt
        # Only log once per cooldown period
        if not _redis_cooldown_logged:
            cooldown_minutes = REDIS_FAILURE_COOLDOWN_SECONDS // 60
            logger.info(f"⏸️  Redis connection will not be attempted in the next {cooldown_minutes} minutes")
            _redis_cooldown_logged = True
        return None
    
    # Reset failure state if cooldown expired
    if _redis_failed_until is not None and current_time >= _redis_failed_until:
        _redis_failed_until = None
        _redis_cooldown_logged = False  # Reset log flag for new cooldown period
        logger.info("🔄 Redis failure cooldown expired, attempting reconnection...")
    
    if _redis_client is None:
        try:
            _redis_client = redis.Redis(
                host=config.REDIS_HOST,
                port=config.REDIS_PORT,
                db=config.REDIS_DB,
                password=config.REDIS_PASSWORD,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
                socket_keepalive=True,
                socket_keepalive_options={}
            )
            # Test connection with explicit timeout
            _redis_client.ping()
            logger.info(f"✅ Redis client connected to {config.REDIS_HOST}:{config.REDIS_PORT}")
            # Reset failure state on successful connection
            _redis_failed_until = None
            _redis_cooldown_logged = False
        except Exception as e:
            logger.warning(f"⚠️  Redis connection failed: {e}. Caching disabled for {REDIS_FAILURE_COOLDOWN_SECONDS}s.")
            _redis_client = None
            # Set failure cooldown period (10 minutes from now)
            _redis_failed_until = current_time + REDIS_FAILURE_COOLDOWN_SECONDS
            _redis_cooldown_logged = False  # Reset log flag so we log on next cooldown period
            return None
    
    return _redis_client


def generate_cache_key(report_id: str, filters: dict) -> str:
    """
    Generate a deterministic cache key from report_id and filters.
    
    Args:
        report_id: The report identifier
        filters: Dictionary of filter parameters
    
    Returns:
        A cache key string in the format: report:{report_id}:{hash}
    """

    sorted_filters = json.dumps(filters, sort_keys=True)
    hash_obj = hashlib.md5(f"{report_id}:{sorted_filters}".encode())
    return f"report:{report_id}:{hash_obj.hexdigest()}"


def get_cached_report(cache_key: str) -> Optional[dict]:
    """
    Retrieve cached report data from Redis.
    
    Args:
        cache_key: The cache key to lookup
    
    Returns:
        Cached data as dict if found, None otherwise
    """
    try:
        client = get_redis_client()
        if not client:
            return None
        
        cached = client.get(cache_key)
        if cached:
            logger.info(f"🎯 Cache HIT: {cache_key}")
            return json.loads(cached)
        else:
            logger.info(f"❌ Cache MISS: {cache_key}")
    except Exception as e:
        logger.warning(f"Cache retrieval error for key {cache_key}: {e}")
    
    return None


def set_cached_report(cache_key: str, data: dict, ttl: int = 300):
    """
    Cache report data in Redis with a TTL.
    
    Args:
        cache_key: The cache key to store under
        data: The data to cache (will be JSON serialized)
        ttl: Time-to-live in seconds (default: 5 minutes)
    """
    try:
        client = get_redis_client()
        if not client:
            return
        
        # Use default=str to handle dates, decimals, and other non-serializable objects
        client.setex(cache_key, ttl, json.dumps(data, default=str))
        logger.info(f"💾 Cache SET: {cache_key} (TTL={ttl}s)")
    except Exception as e:
        logger.warning(f"Cache set error for key {cache_key}: {e}")


def invalidate_report_cache(report_id: Optional[str] = None) -> int:
    """
    Invalidate cached reports.
    
    Args:
        report_id: If provided, only clear caches for this report.
                   If None, clear all report caches.
    
    Returns:
        Number of cache entries deleted
    """
    try:
        client = get_redis_client()
        if not client:
            return 0
        
        pattern = f"report:{report_id}:*" if report_id else "report:*"
        keys = list(client.scan_iter(match=pattern))
        
        if keys:
            deleted = client.delete(*keys)
            logger.info(f"🗑️  Invalidated {deleted} cache entries for pattern: {pattern}")
            return deleted
        else:
            logger.info(f"No cache entries found for pattern: {pattern}")
            return 0
    except Exception as e:
        logger.warning(f"Cache invalidation error for pattern {pattern}: {e}")
    
    return 0


def get_report_cache_ttl(report_id: str) -> int:
    """
    Get the appropriate cache TTL for a report based on its type.
    
    Args:
        report_id: The report identifier
    
    Returns:
        TTL in seconds
    """
    # Smart defaults based on report type
    if any(x in report_id for x in ["current", "progress", "wip"]):
        # Real-time reports: 1 minute
        return config.CACHE_TTL_REALTIME
    elif any(x in report_id for x in ["burndown", "trend", "predictability", "active"]):
        # Aggregate reports: 5 minutes
        return config.CACHE_TTL_AGGREGATE
    elif any(x in report_id for x in ["closed", "historical", "summary"]):
        # Historical reports: 30 minutes
        return config.CACHE_TTL_HISTORICAL
    
    # Default to aggregate TTL
    return config.CACHE_TTL_AGGREGATE

