# K10 Compile Server — Skill Guide

> 编译 UniHiker K10 固件并通过 Web Serial 烧录到设备的 Skill。
> 用户不需要安装 PlatformIO、esptool 或任何 toolchain，只需要 Chrome/Edge 浏览器。

## 工作流程

```
用户项目 (platformio.ini + .cpp)
        │
        ▼
  POST /api/compile ────→  K10 Compile Server
        │                      │
        │                      ▼
        │              pio run (编译)
        │                      │
        ▼                      ▼
  build_id  ←───   GET /api/build/{id}/status
        │
        ▼
  打开浏览器 URL:
  https://<server>:8900/?build_id=xxx
        │
        ▼
  点击"浏览器烧录"
  选择串口 → Web Serial 写入三段 bin
```

## 环境变量 / 配置

```bash
# Skill 读取的环境变量
COMPILE_SERVER=https://<server-ip>:8900   # 编译服务器地址
K10_LANG=zh-CN                             # 页面语言 (zh-CN / en)
```

## 使用方式

### 方式 A: 使用现有编译服务器

如果已有 K10 Compile Server，设置环境变量：

```bash
export COMPILE_SERVER=https://192.168.1.100:8900
```

然后让 AI 帮你编译项目。AI 会自动：
1. 调用 `POST $COMPILE_SERVER/api/compile/files` 上传项目文件
2. 轮询 `GET $COMPILE_SERVER/api/build/{id}/status` 等编译完成
3. 指导用户打开 `$COMPILE_SERVER/?build_id=xxx` 烧录

### 方式 B: 自建服务器

如果没有现成的编译服务器，参考 `references/server-setup.md` 部署一个。

**推荐 Docker 一键部署：**

```bash
git clone <repo-url> k10-compile-server
cd k10-compile-server/server
bash install.sh    # 选择 Docker
```

然后设置 `COMPILE_SERVER=https://<本机IP>:8900` 即可使用。

## API 速查

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/compile` | 上传 zip 项目 |
| POST | `/api/compile/files` | 上传多个文件 |
| GET | `/api/build/{id}/status` | 查询编译状态 |
| GET | `/api/build/{id}/flash-files` | 获取烧录文件清单 |
| GET | `/api/build/{id}/file/{filename}` | 下载 .bin 文件 |
| GET | `/api/build/{id}/download` | 下载 firmware.bin |
| POST | `/api/flash/{id}` | 服务器端烧录 |
| GET | `/` | Web Serial 烧录页面 |

详细 API 文档见 `references/server-api.md`。

## Web Serial 烧录

这是本 Skill 的核心价值：用户只需要 Chrome/Edge 浏览器，不需要安装任何开发工具。

流程：
1. 编译完成后，指导用户打开烧录页 URL
2. 用户点击"浏览器烧录"按钮
3. 浏览器弹出串口选择框（Web Serial API）
4. 选择 K10 对应的串口
5. 自动下载三段 bin 并写入（bootloader @ 0x0, partitions @ 0x8000, firmware @ 0x10000）
6. 烧录完成后自动重启 K10

### 烧录页 URL 参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `build_id` | 加载已编译的 build | `/?build_id=abc123` |
| `build` | build_id 的别名 | `/?build=abc123` |
| `lang` | 语言 (zh-CN/en) | `/?build_id=abc123&lang=en` |

页面的路径 `/flash/{build_id}` 也支持。

### 浏览器兼容性

- ✅ Chrome 89+
- ✅ Edge 89+
- ❌ Firefox (不支持 Web Serial)
- ❌ Safari (不支持 Web Serial)

### 安全提示

- 页面必须通过 HTTPS 访问（localhost 例外）
- 用户必须手动点击按钮选择串口（Web Serial 安全限制）
- 不能完全静默自动烧录

## 故障排除

见 `references/troubleshooting.md`。
