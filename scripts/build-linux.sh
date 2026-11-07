#!/usr/bin/env bash
# Build Hookshot on Linux from a user's extracted game files.
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON=${PYTHON:-python3}
EXEFS=${OOT_EXEFS:-}
WORK=${OOT_WORK:-"$ROOT/work"}
JOBS=${JOBS:-1}
SKIP_LIFT=0
NO_PIP=0

usage() {
    cat <<EOF
Usage: $0 --exefs PATH [options]

Builds the standalone Linux executable and Vulkan renderer. PATH must be the
exefs directory from your own extracted game copy.

Options:
  --exefs PATH   extracted exefs directory (also accepts OOT_EXEFS)
  --work PATH    generated files/build cache (default: OOT_WORK or work/)
  --jobs N       parallel compile jobs (default: $JOBS or 1)
  --skip-lift    reuse an existing completed lift in WORK/sa/
  --no-pip       do not create a local Python venv/install missing modules
  -h, --help     show this help
EOF
}

while (($#)); do
    case "$1" in
        --exefs) EXEFS=${2:?--exefs needs a path}; shift 2 ;;
        --work) WORK=${2:?--work needs a path}; shift 2 ;;
        --jobs) JOBS=${2:?--jobs needs a number}; shift 2 ;;
        --skip-lift) SKIP_LIFT=1; shift ;;
        --no-pip) NO_PIP=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ -z "$EXEFS" ]]; then
    echo "error: pass --exefs /path/to/exefs (or set OOT_EXEFS)" >&2
    exit 2
fi
EXEFS=$(cd "$EXEFS" && pwd)
WORK=$(mkdir -p "$WORK" && cd "$WORK" && pwd)
[[ -f "$EXEFS/main" ]] || { echo "error: $EXEFS/main is missing" >&2; exit 2; }

for tool in clang clang++ pkg-config; do
    command -v "$tool" >/dev/null || { echo "error: missing $tool" >&2; exit 1; }
done
command -v llvm-ar >/dev/null || { echo "error: missing llvm-ar" >&2; exit 1; }

if ! pkg-config --exists sdl3 libavcodec libavutil libswscale libswresample; then
    cat >&2 <<'EOF'
error: SDL3 and FFmpeg development packages are required.
On Arch-like systems, install SDL3, FFmpeg, pkgconf, clang, llvm,
vulkan-headers, vulkan-loader, and Vulkan Memory Allocator.
EOF
    exit 1
fi
if [[ ! -f /usr/include/vulkan/vulkan.h && ! -f /usr/local/include/vulkan/vulkan.h ]]; then
    echo "error: Vulkan headers were not found" >&2
    exit 1
fi
if [[ ! -f /usr/include/vk_mem_alloc.h && ! -f /usr/local/include/vk_mem_alloc.h ]]; then
    echo "error: vk_mem_alloc.h was not found (install Vulkan Memory Allocator)" >&2
    exit 1
fi

if ! "$PYTHON" -c 'import capstone, lz4, numpy, unicorn' >/dev/null 2>&1; then
    ((NO_PIP)) && {
        echo "error: Python modules capstone, lz4, numpy, and unicorn are required" >&2
        exit 1
    }
    VENV="$ROOT/.venv-linux"
    if [[ ! -x "$VENV/bin/python" ]]; then
        "$PYTHON" -m venv "$VENV"
    fi
    "$VENV/bin/python" -m pip install --upgrade capstone lz4 numpy unicorn
    PYTHON="$VENV/bin/python"
fi

git -C "$ROOT" submodule update --init --recursive

if ((SKIP_LIFT == 0)) && [[ ! -f "$WORK/sa/image/image.json" || ! -f "$WORK/sa/gen/a64_hooks.c" || ! -f "$WORK/sa/gen/code_000.c" ]]; then
    echo "== lifting game =="
    "$PYTHON" -I "$ROOT/nx2/lift.py" --exefs "$EXEFS" --work "$WORK" --standalone --write
else
    echo "== reusing existing lift in $WORK/sa =="
fi

echo "== compiling Linux Vulkan build =="
OOT_WORK="$WORK" "$PYTHON" -I "$ROOT/nx2/build.py" --standalone --jobs "$JOBS"

BUILD="$WORK/sa/build"
if [[ -f "$WORK/sa/image/image.bin" ]]; then
    cp -f "$WORK/sa/image/image.bin" "$BUILD/image.bin"
fi
cat > "$BUILD/run-hookshot.sh" <<'EOF'
#!/usr/bin/env bash
set -e
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
exec "$DIR/hookshot" "$DIR/image.bin" --romfs "${HOOKSHOT_ROMFS:-$DIR/romfs}" --user "${HOOKSHOT_USER:-$DIR/user}" "$@"
EOF
chmod +x "$BUILD/run-hookshot.sh"

echo
echo "Build complete: $BUILD/hookshot"
echo "Vulkan renderer: $BUILD/hookshot_vk.so"
echo "Run with: HOOKSHOT_ROMFS=/path/to/romfs $BUILD/run-hookshot.sh"
