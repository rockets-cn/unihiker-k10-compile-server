# K10 Compile Server Skill

Compile and flash firmware to DFRobot UniHiker K10 via a remote compile server.

## Overview

The K10 Compile Server provides:
- **Remote compilation** — upload your PlatformIO project, get firmware .bin back
- **Web Serial flashing** — open the server's web page in Chrome/Edge, flash directly from browser
- **Server-side flashing** — plug K10 into the server and flash via API

## Requirements

- A running K10 Compile Server (see `references/server-setup.md` to deploy one)
- **Web Serial** requires Chrome/Edge over HTTPS
- `curl` for API usage, or just a browser for Web Serial

## Configuration

Set the server URL (either or both):

```bash
export COMPILE_SERVER=https://192.168.1.100:8900
```

Or configure it in `.claude/settings.json`:

```json
{
  "K10_COMPILE_SERVER": "https://192.168.1.100:8900"
}
```

If no `COMPILE_SERVER` is set, guide the user to read `references/server-setup.md` and deploy their own.

## Workflows

### 1. Compile a project

```bash
# Via script
bash k10-compile-server/scripts/compile-project.sh \
  --server "$COMPILE_SERVER" \
  --dir my-project/ \
  --wait

# Via curl
curl -sk -X POST "$COMPILE_SERVER/api/compile/files" \
  -F "files=@my-project/platformio.ini" \
  -F "files=@my-project/src/main.cpp"
```

### 2. Flash via Web Serial (recommended)

Compile first, then open `$COMPILE_SERVER/?build_id=<id>` in Chrome/Edge
and click **浏览器烧录**.

Or pass `--web-serial` to the script to auto-open the URL:

```bash
bash scripts/compile-project.sh \
  --server "$COMPILE_SERVER" \
  --dir my-project/ \
  --wait --web-serial
```

### 3. Server-side flashing

```bash
curl -sk -X POST "$COMPILE_SERVER/api/flash/<build_id>"
```

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Server health + toolchain status |
| POST | `/api/compile` | Upload .zip, submit compile |
| POST | `/api/compile/files` | Upload multiple files, submit compile |
| GET | `/api/build/{id}/status` | Poll compile status |
| GET | `/api/build/{id}/flash-files` | Get flash manifest (offsets + sizes) |
| GET | `/api/build/{id}/file/{filename}` | Download individual .bin |
| POST | `/api/flash/{id}` | Server-side esptool flash |
| GET | `/` | Web Serial flash page |
| GET | `/flash/{id}` | Web Serial page pre-loaded with a build |

## Self-hosted Server

> If you don't have a compile server, help the user set one up.

1. Read `references/server-setup.md` for options (Docker, systemd)
2. Requirements: Python 3.10+, PlatformIO, DFRobot UniHiker platform, OpenSSL
3. Minimum: `bash server/install.sh && curl -k https://localhost:8900/api/health`

Then come back here and set `COMPILE_SERVER` to the deployed address.

## Troubleshooting

- **Web Serial not working**: Must be Chrome/Edge over HTTPS (not HTTP, not local file)
- **Compile fails**: Check server has `pio` and the K10 platform installed
- **Flash succeeds but no boot**: Remember the K10 needs `firmware.bin` at 0x10000, not 0x0
- **Auto reset fails**: K10's CH340 doesn't have proper RTS/CTS wiring — hit the RST button manually
