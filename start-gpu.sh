#!/usr/bin/env bash

# Function to check if Redis is running
check_redis() {
    if command -v redis-cli &> /dev/null; then
        if redis-cli ping &> /dev/null; then
            echo "Redis is already running."
            return 0
        else
            echo "Redis is not running. Attempting to start Redis server..."
            if command -v redis-server &> /dev/null; then
                redis-server --daemonize yes
                sleep 2
                if redis-cli ping &> /dev/null; then
                    echo "Redis server started successfully."
                    return 0
                else
                    echo "Failed to start Redis server."
                    return 1
                fi
            else
                echo "redis-server command not found. Please install Redis server."
                return 1
            fi
        fi
    else
        echo "redis-cli command not found. Please install Redis client."
        return 1
    fi
}

# Get project root directory
PROJECT_ROOT=$(pwd)

# Check and start Redis if needed
if ! check_redis; then
    echo "Redis is required for the application. Please ensure Redis is installed and running."
    exit 1
fi

# Set environment variables
export USE_GPU=true
export USE_ONNX=false
export PYTHONPATH=$PROJECT_ROOT:$PROJECT_ROOT/api
export MODEL_DIR=src/models
export VOICES_DIR=src/voices/v1_0
export WEB_PLAYER_PATH=$PROJECT_ROOT/web

# Run FastAPI with GPU extras using uv run
# Note: espeak may still require manual installation,
uv pip install -e ".[gpu]"
uv run --no-sync python docker/scripts/download_model.py --output api/src/models/v1_0
uv run --no-sync uvicorn api.src.main:app --host 0.0.0.0 --port 8880
