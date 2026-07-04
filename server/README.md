# K10 Compile Server

> PlatformIO 编译服务 + Web Serial 烧录页面，专为 DFRobot UniHiker K10 设计。

## 特性

- **编译**: 通过 REST API 编译 PlatformIO 项目，不需要客户端安装 toolchain
- **Web Serial 烧录**: 浏览器中直接烧录到 K10，不需要安装 esptool
- **多语言**: 中文/English 界面

## 快速部署

### Docker (推荐)

```bash
# 确保已安装 Docker 和 docker-compose
# 生成证书（首次）
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem \
  -days 3650 -nodes -subj "/CN=K10"

# 启动
docker compose up -d --build

# 访问
curl -sk https://localhost:8900/api/health
```

### 原生部署 (systemd)

```bash
bash install.sh
# 选择 [1] systemd
```

## 使用

### 编译项目

```bash
# 上传 zip
curl -sk -X POST https://localhost:8900/api/compile \
  -F "file=@project.zip"

# 轮询状态
curl -sk https://localhost:8900/api/build/{build_id}/status

# 编译完成后打开烧录页
# https://localhost:8900/?build_id=xxx
```

或用客户端脚本：

```bash
bash ../k10-compile-server/scripts/compile-project.sh \
  --server https://localhost:8900 \
  --dir ../examples/Blink \
  --wait --web-serial
```

### Web Serial 烧录

1. 打开 `https://<服务器IP>:8900/?build_id=xxx`
2. 点击"浏览器烧录"
3. 选择 K10 串口
4. 等待烧录完成

## 项目结构

```
server/
├── main.py              # FastAPI 服务
├── requirements.txt     # Python 依赖
├── Dockerfile           # Docker 构建
├── docker-compose.yml   # Docker 编排
├── install.sh           # 安装脚本
├── restart.sh           # 重启脚本
├── k10-compile-server.service  # systemd 服务模板
├── static/              # 前端 JS 文件 (esptool-js, pako, atob-lite)
├── npm/                 # npm 依赖
└── stub_flasher/        # ESP32 stub flasher
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 服务状态 |
| POST | `/api/compile` | 上传 zip，提交编译 |
| POST | `/api/compile/files` | 上传多个文件，提交编译 |
| GET | `/api/build/{id}/status` | 编译状态 |
| GET | `/api/build/{id}/flash-files` | 烧录文件清单 |
| GET | `/api/build/{id}/file/{filename}` | 下载 .bin |
| POST | `/api/flash/{id}` | 服务器端烧录 |
| GET | `/` | Web Serial 烧录页面 |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `K10_SSL_CERT` | `./cert.pem` | HTTPS 证书路径 |
| `K10_SSL_KEY` | `./key.pem` | HTTPS 私钥路径 |
| `K10_STATIC_DIR` | `./static` | 静态文件目录 |

## 安全

> ⚠️ **局域网工具，不要直接暴露到公网。**

- 没有用户认证
- 通过 LAN/VPN/SSH 隧道访问
- 反向代理 + 认证可提供公网访问
