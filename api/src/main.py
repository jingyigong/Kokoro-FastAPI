"""
FastAPI OpenAI Compatible API
"""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import torch
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from .core.config import settings
from .routers.debug import router as debug_router
from .routers.development import router as dev_router
from .routers.openai_compatible import router as openai_router
from .routers.web_player import router as web_router


def setup_logger():
    """Configure loguru logger with custom formatting"""
    valid_levels = ["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"]
    level = os.getenv("API_LOG_LEVEL", "DEBUG").upper()
    if level not in valid_levels:
        level = "DEBUG"
    print(f"Global API loguru logger level: {level}")
    config = {
        "handlers": [
            {
                "sink": sys.stdout,
                "format": "<fg #2E8B57>{time:hh:mm:ss A}</fg #2E8B57> | "
                "{level: <8} | "
                "<fg #4169E1>{module}:{line}</fg #4169E1> | "
                "{message}",
                "colorize": True,
                "level": level,
            },
        ],
    }
    logger.remove()
    logger.configure(**config)
    logger.level("ERROR", color="<red>")


# Configure logger
setup_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for model initialization"""
    from .inference.model_manager import get_manager
    from .inference.voice_manager import get_manager as get_voice_manager
    from .services.temp_manager import cleanup_temp_files
    from .core.redis_manager import redis_manager

    # Clean old temp files on startup
    await cleanup_temp_files()

    # Initialize Redis connection manager
    logger.info("Initializing Redis connection...")
    try:
        redis_client = await redis_manager.get_client()
        if redis_client:
            # Test Redis connection
            await redis_client.ping()
            logger.info(f"Redis connected successfully to {settings.redis_host}:{settings.redis_port}")
        else:
            logger.warning("Redis is not available. Rate limiting will be disabled.")
    except Exception as e:
        logger.warning(f"Failed to connect to Redis: {e}. Rate limiting will be disabled.")
    
    # Initialize rate limiter
    from .middleware.rate_limiter import get_rate_limiter
    try:
        rate_limiter = await get_rate_limiter()
        logger.info("Rate limiter initialized")
    except Exception as e:
        logger.warning(f"Failed to initialize rate limiter: {e}")

    logger.info("Loading TTS model and voice packs...")

    try:
        # Initialize managers
        model_manager = await get_manager()
        voice_manager = await get_voice_manager()

        # Initialize model with warmup and get status
        device, model, voicepack_count = await model_manager.initialize_with_warmup(
            voice_manager
        )

    except Exception as e:
        logger.error(f"Failed to initialize model: {e}")
        raise

    boundary = "░" * 2 * 12
    startup_msg = f"""

{boundary}

    ╔═╗┌─┐┌─┐┌┬┐
    ╠╣ ├─┤└─┐ │ 
    ╚  ┴ ┴└─┘ ┴
    ╦╔═┌─┐┬┌─┌─┐
    ╠╩╗│ │├┴┐│ │
    ╩ ╩└─┘┴ ┴└─┘

{boundary}
                """
    startup_msg += f"\nModel warmed up on {device}: {model}"
    if device == "mps":
        startup_msg += "\nUsing Apple Metal Performance Shaders (MPS)"
    elif device == "cuda":
        startup_msg += f"\nCUDA: {torch.cuda.is_available()}"
    else:
        startup_msg += "\nRunning on CPU"
    startup_msg += f"\n{voicepack_count} voice packs loaded"

    # Add rate limiting info
    if settings.rate_limit_enabled:
        startup_msg += f"\nRate limiting: {settings.rate_limit_requests_per_minute} req/min, {settings.rate_limit_chars_per_day} chars/day"
        if settings.rate_limit_whitelist:
            startup_msg += f"\nIP Whitelist: {', '.join(settings.rate_limit_whitelist)}"
    else:
        startup_msg += "\nRate limiting: disabled"

    # Add web player info if enabled
    if settings.enable_web_player:
        startup_msg += (
            f"\n\nBeta Web Player: http://{settings.host}:{settings.port}/web/"
        )
        startup_msg += f"\nor http://localhost:{settings.port}/web/"
    else:
        startup_msg += "\n\nWeb Player: disabled"

    startup_msg += f"\n{boundary}\n"
    logger.info(startup_msg)

    # App is running
    yield
    
    # Cleanup on shutdown
    logger.info("Shutting down application...")
    
    # Close Redis connection
    try:
        await redis_manager.close()
        logger.info("Redis connection closed")
    except Exception as e:
        logger.error(f"Error closing Redis connection: {e}")
    
    # Cleanup temp files
    try:
        await cleanup_temp_files()
        logger.info("Temporary files cleaned up")
    except Exception as e:
        logger.error(f"Error cleaning temp files: {e}")


# Initialize FastAPI app
app = FastAPI(
    title=settings.api_title,
    description=settings.api_description,
    version=settings.api_version,
    lifespan=lifespan,
    openapi_url="/openapi.json",  # Explicitly enable OpenAPI schema
)

# Add CORS middleware if enabled
if settings.cors_enabled:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Include routers
app.include_router(openai_router, prefix="/v1")
app.include_router(dev_router)  # Development endpoints
app.include_router(debug_router)  # Debug endpoints
if settings.enable_web_player:
    app.include_router(web_router, prefix="/web")  # Web player static files


# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    # Check Redis connection if rate limiting is enabled
    from .core.redis_manager import redis_manager
    
    health_status = {"status": "healthy", "services": {}}
    
    try:
        redis_client = await redis_manager.get_client()
        if redis_client:
            await redis_client.ping()
            health_status["services"]["redis"] = "connected"
        else:
            health_status["services"]["redis"] = "not_available"
    except Exception as e:
        logger.warning(f"Redis health check failed: {e}")
        health_status["services"]["redis"] = "error"
        health_status["status"] = "degraded"
    
    # Check TTS service
    try:
        from .services.tts_service import TTSService
        tts_service_instance = None
        # Try to get existing instance or create temporary one
        health_status["services"]["tts"] = "available"
    except Exception as e:
        logger.warning(f"TTS service health check failed: {e}")
        health_status["services"]["tts"] = "error"
        health_status["status"] = "unhealthy"
    
    return health_status


@app.get("/v1/test")
async def test_endpoint():
    """Test endpoint to verify routing"""
    return {"status": "ok"}


# Rate limiting info endpoint
@app.get("/v1/rate_limit/info")
async def rate_limit_info():
    """Get rate limiting configuration information"""
    from .middleware.rate_limiter import get_rate_limiter
    
    try:
        rate_limiter = await get_rate_limiter()
        
        info = {
            "enabled": settings.rate_limit_enabled,
            "limits": {
                "requests_per_minute": settings.rate_limit_requests_per_minute,
                "chars_per_day": settings.rate_limit_chars_per_day,
            },
            "whitelist": settings.rate_limit_whitelist,
            "redis_available": rate_limiter.redis is not None,
        }
        
        return info
    except Exception as e:
        logger.error(f"Failed to get rate limit info: {e}")
        raise


# Redis test endpoint (for debugging)
@app.get("/debug/redis")
async def test_redis():
    """Test Redis connection and basic operations"""
    from .core.redis_manager import redis_manager
    
    try:
        redis_client = await redis_manager.get_client()
        if not redis_client:
            return {"status": "error", "message": "Redis client not available"}
        
        # Test ping
        pong = await redis_client.ping()
        
        # Test set/get
        test_key = "test:ping"
        await redis_client.set(test_key, "pong", ex=10)  # 10秒过期
        value = await redis_client.get(test_key)
        
        return {
            "status": "success",
            "redis": {
                "connected": True,
                "ping": pong,
                "test_key_value": value.decode() if value else None,
                "host": settings.redis_host,
                "port": settings.redis_port,
                "db": settings.redis_db,
            }
        }
    except Exception as e:
        logger.error(f"Redis test failed: {e}")
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    uvicorn.run("api.src.main:app", host=settings.host, port=settings.port, reload=True)