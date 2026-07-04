#!/bin/bash
# K10 Compile Server 重启脚本
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Kill existing process on port 8900
fuser -k 8900/tcp 2>/dev/null
sleep 1

# Determine Python path
PYTHON_BIN="${K10_PYTHON:-python3}"

# Start server
nohup "$PYTHON_BIN" -m uvicorn main:app \
  --host 0.0.0.0 --port 8900 --log-level info \
  --ssl-certfile "${K10_SSL_CERT:-cert.pem}" \
  --ssl-keyfile "${K10_SSL_KEY:-key.pem}" \
  > /tmp/k10-server.log 2>&1 &

echo "Server started, PID: $!"
echo "Log: tail -f /tmp/k10-server.log"
