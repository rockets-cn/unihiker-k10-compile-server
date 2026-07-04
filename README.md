# K10 Compile Server

> 编译 UniHiker K10 固件，通过 Web Serial 在浏览器中直接烧录。  
> **客户端不需要安装 PlatformIO、esptool 或任何 toolchain，只需要 Chrome/Edge。**

## 项目结构

```
k10-compile-server/
├── SKILL.md                     # Skill 使用指南（AI / 人类）
├── scripts/
│   ├── compile-project.sh       # 编译脚本 (Linux/macOS)
│   └── compile-project.ps1      # 编译脚本 (Windows)
├── references/
│   ├── server-setup.md          # 服务器部署指南
│   ├── server-api.md            # API 文档
│   └── troubleshooting.md       # 故障排除
├── server/
│   ├── main.py                  # FastAPI 服务
│   ├── Dockerfile               # Docker 构建
│   ├── docker-compose.yml       # Docker 一键部署
│   ├── install.sh               # 安装脚本 (systemd/Docker)
│   ├── requirements.txt         # Python 依赖
│   └── ...                      # 静态文件、npm 依赖
├── examples/
│   ├── Blink/                   # 示例项目：LED 闪烁
│   └── HelloScreen/             # 示例项目：屏幕显示
└── README.md
```

## 快速开始 (30 秒)

### 方式 A: Docker 一键部署

```bash
cd server
bash install.sh          # 选择 [2] Docker
curl -sk https://localhost:8900/api/health
```

### 方式 B: 原生部署

```bash
cd server
bash install.sh          # 选择 [1] systemd
curl -sk https://localhost:8900/api/health
```

部署完成后，用任意客户端脚本测试：

```bash
bash scripts/compile-project.sh \
  --server https://localhost:8900 \
  --dir examples/Blink \
  --wait --web-serial
```

## 核心理念

| 传统方式 | 本方案 |
|----------|--------|
| 需要安装 PlatformIO | ✅ 只需浏览器 |
| 需要安装 esptool | ✅ Web Serial 内嵌 |
| 需要了解 ESP32 烧录流程 | ✅ 点击按钮即可 |
| 多机协作需要分别配置环境 | ✅ 编译服务器集中处理 |

## Web Serial 烧录流程

```
客户端/CI ──POST /api/compile──→ K10 Compile Server
        │                              │
        │                     pio run (服务器端编译)
        │                              │
        ◄─── build_id ────────────────│
        │
        ▼
打开浏览器 https://server:8900/?build_id=xxx
        │
        ▼
点击"浏览器烧录" → 选串口 → 自动写入
```

## 要求

- **服务器**: Linux, Python 3.10+, PlatformIO, Docker 或 systemd
- **客户端**: Chrome / Edge 89+ (Web Serial 支持)
- **网络**: HTTPS (Web Serial 强制要求)

## 安全

> ⚠️ **局域网开发工具，不要直接暴露到公网。**
> 通过 LAN/VPN/SSH 隧道访问。没有内置用户认证。
