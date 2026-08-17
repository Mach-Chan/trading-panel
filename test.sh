#!/bin/zsh
# 跑代理的解析逻辑测试（uv 自动准备依赖，无需预装）
cd "$(dirname "$0")"
exec uv run --quiet --with pytest --with fastapi --with uvicorn python -m pytest tests/ "$@"
