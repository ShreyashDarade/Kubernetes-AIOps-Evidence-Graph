"""
Alert deduplicator - prevents duplicate incidents from the same alert.
Uses Redis for fast fingerprint lookups with TTL.
"""
from datetime import timedelta
from typing import Optional, Tuple
import structlog
import redis.asyncio as redis

from src.config import settings


logger = structlog.get_logger()


class AlertDeduplicator:
    """Deduplicates alerts based on fingerprint."""
    
    # TTL for fingerprints (how long to consider an alert as duplicate)
    FINGERPRINT_TTL = timedelta(hours=4)
    
    _redis_client: Optional[redis.Redis] = None
    
    @classmethod
    async def get_redis(cls) -> redis.Redis:
        """Get or create Redis client."""
        if cls._redis_client is None:
            cls._redis_client = redis.from_url(
                settings.redis_connection_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return cls._redis_client
    
    @classmethod
    async def close(cls) -> None:
        """Close Redis connection."""
        if cls._redis_client is not None:
            await cls._redis_client.close()
            cls._redis_client = None
    
    @classmethod
    async def check_duplicate(
        cls, 
        fingerprint: str,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if an alert with this fingerprint already exists.
        
        Returns:
            Tuple of (is_duplicate, existing_incident_id)
        """
        try:
            client = await cls.get_redis()
            key = f"aiops:fingerprint:{fingerprint}"
            
            existing_id = await client.get(key)
            
            if existing_id:
                logger.debug(
                    "Duplicate alert detected",
                    fingerprint=fingerprint,
                    existing_id=existing_id,
                )
                return True, existing_id
            
            return False, None
            
        except Exception as e:
            logger.error("Redis error during deduplication", error=str(e))
            # Fail open - don't block incident creation on Redis errors
            return False, None
    
    @classmethod
    async def register_fingerprint(
        cls,
        fingerprint: str,
        incident_id: str,
        ttl: Optional[timedelta] = None,
    ) -> bool:
        """
        Register a fingerprint for an incident.
        
        Args:
            fingerprint: The alert fingerprint
            incident_id: The created incident ID
            ttl: Time-to-live for the fingerprint
        """
        try:
            client = await cls.get_redis()
            key = f"aiops:fingerprint:{fingerprint}"
            ttl = ttl or cls.FINGERPRINT_TTL
            
            await client.set(key, incident_id, ex=int(ttl.total_seconds()))
            
            logger.debug(
                "Registered fingerprint",
                fingerprint=fingerprint,
                incident_id=incident_id,
            )
            return True
            
        except Exception as e:
            logger.error("Redis error during fingerprint registration", error=str(e))
            return False
    
    @classmethod
    async def remove_fingerprint(cls, fingerprint: str) -> bool:
        """Remove a fingerprint (e.g., when incident is resolved)."""
        try:
            client = await cls.get_redis()
            key = f"aiops:fingerprint:{fingerprint}"
            
            await client.delete(key)
            return True
            
        except Exception as e:
            logger.error("Redis error during fingerprint removal", error=str(e))
            return False
    
    @classmethod
    async def extend_fingerprint(
        cls,
        fingerprint: str,
        additional_ttl: Optional[timedelta] = None,
    ) -> bool:
        """Extend the TTL of an existing fingerprint."""
        try:
            client = await cls.get_redis()
            key = f"aiops:fingerprint:{fingerprint}"
            ttl = additional_ttl or cls.FINGERPRINT_TTL
            
            # Only extend if key exists
            if await client.exists(key):
                await client.expire(key, int(ttl.total_seconds()))
                return True
            return False
            
        except Exception as e:
            logger.error("Redis error during TTL extension", error=str(e))
            return False


class RateLimiter:
    """Rate limiter for webhook endpoints."""
    
    @classmethod
    async def check_rate_limit(
        cls,
        key: str,
        limit: int,
        window_seconds: int = 60,
    ) -> Tuple[bool, int]:
        """
        Check if rate limit is exceeded.
        
        Returns:
            Tuple of (is_allowed, remaining_count)
        """
        try:
            client = await AlertDeduplicator.get_redis()
            rate_key = f"aiops:ratelimit:{key}"
            
            pipe = client.pipeline()
            pipe.incr(rate_key)
            pipe.expire(rate_key, window_seconds)
            results = await pipe.execute()
            
            current_count = results[0]
            remaining = max(0, limit - current_count)
            
            return current_count <= limit, remaining
            
        except Exception as e:
            logger.error("Redis error during rate limiting", error=str(e))
            # Fail open
            return True, limit
