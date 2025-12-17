"""Redis connection manager for rate limiting"""

import redis.asyncio as redis
from loguru import logger

from .config import settings


class RedisManager:
    _instance = None
    _redis_client = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    async def get_client(self) -> redis.Redis:
        """Get or create Redis client"""
        if self._redis_client is None:
            try:
                # 创建 Redis 连接
                self._redis_client = redis.Redis(
                    host=settings.redis_host,  # 注意：小写字母开头
                    port=settings.redis_port,
                    db=settings.redis_db,
                    password=settings.redis_password,
                    decode_responses=False,  # 存储二进制数据
                    socket_timeout=settings.redis_socket_timeout,
                    socket_connect_timeout=settings.redis_socket_connect_timeout,
                    retry_on_timeout=True,
                )
                
                # 测试连接
                await self._redis_client.ping()
                logger.info(f"Redis connected to {settings.redis_host}:{settings.redis_port}")
                
            except Exception as e:
                logger.error(f"Failed to connect to Redis: {e}")
                # 如果 Redis 不可用，返回 None（限流将自动禁用）
                self._redis_client = None
        
        return self._redis_client
    
    async def close(self):
        """Close Redis connection"""
        if self._redis_client:
            await self._redis_client.close()
            self._redis_client = None
            logger.info("Redis connection closed")


# 全局 Redis 管理器实例
redis_manager = RedisManager()


async def get_redis() -> redis.Redis:
    """Dependency for FastAPI to get Redis client"""
    return await redis_manager.get_client()