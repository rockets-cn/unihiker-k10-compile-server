#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# compile-project.sh
# Compile a PlatformIO project via K10 Compile Server, then
# optionally open the Web Serial flash page in the browser.
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEFAULT_SERVER="${COMPILE_SERVER:-https://localhost:8900}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Compile a PlatformIO project via K10 Compile Server.

Options:
  -s, --server URL     Compile server URL (default: \$COMPILE_SERVER or https://localhost:8900)
  -d, --dir DIR        Project directory containing platformio.ini (default: .)
  -w, --wait           Wait for compilation and print result
  -o, --open           Open Web Serial flash page in browser after compile
      --web-serial     Same as --open
  -h, --help           Show this help

Examples:
  $(basename "$0") --server https://k10.local:8900 --dir ./my-project --wait
  $(basename "$0") --dir ./examples/Blink --wait --web-serial
EOF
  exit 0
}

SERVER="$DEFAULT_SERVER"
DIR="."
WAIT=false
OPEN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--server)    SERVER="$2"; shift 2 ;;
    -d|--dir)       DIR="$2"; shift 2 ;;
    -w|--wait)      WAIT=true; shift ;;
    -o|--open|--web-serial) OPEN=true; shift ;;
    -h|--help)      usage ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

if [ ! -f "$DIR/platformio.ini" ]; then
  echo "Error: No platformio.ini found in $DIR" >&2
  exit 1
fi

echo "═══ K10 Compile ═══"
echo "Server: $SERVER"
echo "Project: $DIR"

# Build form data — upload all files in the project
TMP=$(mktemp -d)
trap "rm -rf '$TMP'" EXIT

cd "$DIR"
find . -type f \
  ! -path './.pio/*' \
  ! -path './.git/*' \
  ! -name '*.o' \
  ! -name '*.elf' \
  ! -name '*.map' \
  | sed 's|^\./||' > "$TMP/files.txt"

echo "Files: $(wc -l < "$TMP/files.txt")"

# Build curl -F args
CURL_ARGS=()
while IFS= read -r f; do
  [ -f "$f" ] && CURL_ARGS+=(-F "files=@$f;filename=$f")
done < "$TMP/files.txt"

echo "Submitting compile..."
RESP=$(curl -sk -X POST "$SERVER/api/compile/files" \
  "${CURL_ARGS[@]}")

BUILD_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('build_id',''))" 2>/dev/null || echo "")

if [ -z "$BUILD_ID" ]; then
  echo "Error: Failed to get build_id"
  echo "Response: $RESP"
  exit 1
fi

echo "build_id: $BUILD_ID"

if [ "$WAIT" = false ] && [ "$OPEN" = false ]; then
  echo ""
  echo "Submitted. Check status:"
  echo "  curl -sk '$SERVER/api/build/$BUILD_ID/status'"
  echo "Flash page:"
  echo "  $SERVER/?build_id=$BUILD_ID"
  exit 0
fi

# Poll for completion
if [ "$WAIT" = true ]; then
  echo "Waiting..."
  while true; do
    STATUS=$(curl -sk "$SERVER/api/build/$BUILD_ID/status")
    STATE=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
    if [ "$STATE" = "done" ]; then
      BIN_SIZE=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('bin_size','?'))" 2>/dev/null)
      ELAPSED=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('elapsed','?'))" 2>/dev/null)
      echo "✅ Compile complete — ${BIN_SIZE} bytes, ${ELAPSED}s"
      break
    elif [ "$STATE" = "error" ]; then
      ERROR=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error','?'))" 2>/dev/null)
      echo "❌ Compile failed: $ERROR"
      exit 1
    fi
    sleep 2
  done
fi

if [ "$OPEN" = true ]; then
  FLASH_URL="$SERVER/?build_id=$BUILD_ID"
  echo "Opening: $FLASH_URL"
  if command -v xdg-open &>/dev/null; then
    xdg-open "$FLASH_URL" 2>/dev/null || true
  elif command -v open &>/dev/null; then
    open "$FLASH_URL" 2>/dev/null || true
  else
    echo "Open this URL in Chrome/Edge:"
    echo "  $FLASH_URL"
  fi
fi
