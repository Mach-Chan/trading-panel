#!/bin/zsh
# 一键：启动 Claude 代理 → 用原版 TradingAgents 跑分析
#
#   ./run.sh                 打开原版交互式看板（自己选股票和分析师）
#   ./run.sh RKLB            直接分析某只股票，输出完整报告
#   ./run.sh RKLB 2026-08-17 指定分析基准日
#
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
UP="$DIR/upstream/TradingAgents"
PY="$UP/.venv/bin/python"

[[ -x "$PY" ]] || { echo "✗ 原版未安装。先跑：cd $UP && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python ."; exit 1; }

# ── 1. 确保代理在跑 ──────────────────────────────
if curl -s -o /dev/null --max-time 3 http://127.0.0.1:8788/health; then
  echo "[1/2] Claude 代理已在运行"
else
  echo "[1/2] 启动 Claude 代理 (127.0.0.1:8788)..."
  nohup "$DIR/proxy/claude_proxy.py" > "$DIR/proxy/proxy.out" 2>&1 &
  for i in {1..30}; do
    sleep 1
    curl -s -o /dev/null --max-time 2 http://127.0.0.1:8788/health && break
  done
  curl -s -o /dev/null --max-time 2 http://127.0.0.1:8788/health || { echo "✗ 代理启动失败，看 $DIR/proxy/proxy.out"; exit 1; }
fi

# ── 2. 加载配置并运行原版 ─────────────────────────
set -a; source "$DIR/.env"; set +a

if [[ -z "$1" ]]; then
  echo "[2/2] 打开原版交互式看板..."
  exec "$UP/.venv/bin/tradingagents"
else
  echo "[2/2] 分析 $1 ${2:+（基准日 $2）}..."
  exec "$PY" "$DIR/analyze.py" "$1" ${2:+"$2"}
fi
