#!/bin/bash
set -uo pipefail

# ═══════════════════════════════════════════════════════════
# K10 Compile Server 安装脚本
# 支持两种部署方式: systemd (原生) 或 Docker
# ═══════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="/opt/k10-compile-server"
SERVICE_NAME="k10-compile-server"

echo "========================================"
echo " K10 Compile Server"
echo " 选择部署方式:"
echo "   1) systemd (原生) — 需要 Python 3.10+ & PlatformIO"
echo "   2) Docker        — 需要 Docker & docker-compose"
echo "   3) 退出"
echo "========================================"
read -p "请输入 [1/2/3]: " DEPLOY_MODE

case "$DEPLOY_MODE" in
  2)
    echo ""
    echo "[Docker] 部署中..."
    if ! command -v docker &>/dev/null; then
      echo "请先安装 Docker: https://docs.docker.com/engine/install/"
      exit 1
    fi
    cd "$SCRIPT_DIR"
    if [ ! -f cert.pem ] || [ ! -f key.pem ]; then
      echo "  生成自签名 SSL 证书..."
      openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem \
        -days 3650 -nodes -subj "/CN=K10 Compile Server" 2>/dev/null
    fi
    docker compose up -d --build
    echo "  Docker 服务已启动: https://localhost:8900"
    exit 0
    ;;
  1|"") ;;
  *) exit 0 ;;
esac

echo "[systemd 部署]"

# Python check
PYTHON_BIN=""
for p in python3.11 python3.10 python3; do
  if command -v "$p" &>/dev/null; then
    PYTHON_BIN="$p"
    break
  fi
done
if [ -z "$PYTHON_BIN" ]; then echo "需要 Python 3.10+"; exit 1; fi
echo "  Python: $($PYTHON_BIN --version 2>&1)"

# PlatformIO
if ! command -v pio &>/dev/null; then
  "$PYTHON_BIN" -m pip install platformio -q
fi
if ! pio platform show "unihiker" &>/dev/null 2>&1; then
  pio platform install "https://github.com/DFRobot/UniHiker_K10_Arduino.git"
fi
echo "  PlatformIO & K10 平台已就绪"

# Install deps
"$PYTHON_BIN" -m pip install -r "$SCRIPT_DIR/requirements.txt" -q

# Deploy
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/main.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR/static" "$INSTALL_DIR/" 2>/dev/null
cp -r "$SCRIPT_DIR/npm" "$INSTALL_DIR/" 2>/dev/null
cp -r "$SCRIPT_DIR/stub_flasher" "$INSTALL_DIR/" 2>/dev/null

# SSL cert
if [ ! -f "$INSTALL_DIR/cert.pem" ]; then
  openssl req -x509 -newkey rsa:2048 -keyout "$INSTALL_DIR/key.pem" \
    -out "$INSTALL_DIR/cert.pem" -days 3650 -nodes -subj "/CN=K10" 2>/dev/null
fi

# systemd service
sed -e "s|__PYTHON_BIN__|${PYTHON_BIN}|g" \
    -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
    "$SCRIPT_DIR/k10-compile-server.service" > /etc/systemd/system/$SERVICE_NAME.service
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
sleep 2
systemctl --quiet is-active "$SERVICE_NAME" && echo " 服务运行中" || echo " 检查: journalctl -u $SERVICE_NAME -f"

echo ""
echo " 网页: https://localhost:8900"
echo " API:  https://localhost:8900/api/health"
