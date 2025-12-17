"""Rate limiting middleware"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from loguru import logger

from ..core.config import settings


class RateLimiter:
    def __init__(self, redis_client: Optional[Redis] = None):
        self.redis = redis_client
        self.enabled = settings.rate_limit_enabled  # 注意：小写字母开头
        self.whitelist = set(ip.strip() for ip in settings.rate_limit_whitelist if ip.strip())
        
        # 限制配置
        self.requests_per_minute = settings.rate_limit_requests_per_minute  # 小写
        self.chars_per_day = settings.rate_limit_chars_per_day  # 小写
        
    def is_whitelisted(self, ip: str) -> bool:
        """检查IP是否在白名单中"""
        return ip in self.whitelist
    
    async def check_rate_limit(self, request: Request, text_length: int = 0) -> bool:
        """
        检查速率限制
        
        Args:
            request: FastAPI 请求对象
            text_length: 请求中的文本长度（字符数）
            
        Returns:
            bool: True=通过限制，False=限制通过但Redis不可用
            
        Raises:
            HTTPException: 超出限制时抛出429异常
        """
        # 如果限流被禁用，直接通过
        if not self.enabled:
            return True
            
        # 如果Redis不可用，记录警告但允许通过
        if self.redis is None:
            logger.warning("Redis not available, rate limiting disabled")
            return True
            
        # 获取客户端IP
        client_ip = request.client.host
        if not client_ip:
            logger.warning("Could not get client IP")
            client_ip = "unknown"
        
        # 检查白名单
        if self.is_whitelisted(client_ip):
            logger.debug(f"IP {client_ip} is in whitelist, bypassing rate limit")
            return True
        
        try:
            # 1. 检查每分钟请求限制
            minute_key = f"rate_limit:req:{client_ip}:minute"
            minute_count = await self.redis.incr(minute_key)
            
            # 如果是第一次设置这个键，设置过期时间
            if minute_count == 1:
                await self.redis.expire(minute_key, 60)  # 60秒过期
            
            if minute_count > self.requests_per_minute:
                logger.warning(f"IP {client_ip} exceeded minute request limit: {minute_count}/{self.requests_per_minute}")
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "rate_limit_exceeded",
                        "message": "请求过于频繁，请稍后再试",
                        "type": "rate_limit_error"
                    }
                )
            
            # 2. 检查每日字符限制
            # 获取今天的日期字符串（YYYYMMDD格式）
            today = datetime.now().strftime("%Y%m%d")
            day_key = f"rate_limit:chars:{client_ip}:{today}"
            
            # 使用INCRBY增加字符数
            day_count = await self.redis.incrby(day_key, text_length)
            
            # 如果是第一次设置这个键，设置过期时间为1天+1小时（避免边界问题）
            if day_count == text_length:
                await self.redis.expire(day_key, 25 * 3600)  # 25小时过期
            
            if day_count > self.chars_per_day:
                logger.warning(f"IP {client_ip} exceeded daily character limit: {day_count}/{self.chars_per_day}")
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "rate_limit_exceeded",
                        "message": "今日额度已用完",
                        "type": "rate_limit_error"
                    }
                )
            
            # 记录调试信息
            logger.debug(f"IP {client_ip}: minute_req={minute_count}, day_chars={day_count}")
            
            return True
            
        except HTTPException:
            raise
        except Exception as e:
            # Redis 操作出错时，记录错误但允许请求通过
            logger.error(f"Redis error in rate limiting: {e}")
            return True


# 全局速率限制器实例
_rate_limiter = None


async def get_rate_limiter() -> RateLimiter:
    """获取速率限制器实例"""
    global _rate_limiter
    if _rate_limiter is None:
        from ..core.redis_manager import get_redis
        redis_client = await get_redis()
        _rate_limiter = RateLimiter(redis_client)
    return _rate_limiter