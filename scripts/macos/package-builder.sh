#!/bin/bash
# Assemble a downloadable macOS CLI builder; no game dump or game build is included.
set -euo pipefail
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "The macOS builder package requires an Apple Silicon macOS host." >&2
    exit 1
fi
cd "$(dirname "$0")/../.."

DEPS=${DEPS:-$PWD/build-macos/deps}
OUT=${OUT:-$PWD/build-macos}
STAGE=$OUT/package
ROOT=$STAGE/HookshotBuilder
SOURCE=$ROOT/tools/source

test -x "$DEPS/python/bin/python3"
test -f "$DEPS/sdl2/lib/libSDL2-2.0.0.dylib"
test -f "$DEPS/ffmpeg/lib/libavcodec.a"
test -f "$DEPS/wgpu-native/lib/libwgpu_native.dylib"
command -v lipo >/dev/null
for binary in "$DEPS/python/bin/python3" "$DEPS/sdl2/lib/libSDL2-2.0.0.dylib" \
    "$DEPS/wgpu-native/lib/libwgpu_native.dylib" "$DEPS/ffmpeg/lib/libavcodec.a" \
    "$DEPS/ffmpeg/lib/libswscale.a" "$DEPS/ffmpeg/lib/libswresample.a" "$DEPS/ffmpeg/lib/libavutil.a"; do
    lipo "$binary" -verify_arch arm64
done
rm -rf "$STAGE"
mkdir -p "$SOURCE" "$ROOT/tools"
git archive --format=tar HEAD | tar -xf - -C "$SOURCE"
cp -R "$DEPS/python" "$DEPS/sdl2" "$DEPS/ffmpeg" "$DEPS/wgpu-native" "$DEPS/licenses" "$ROOT/tools/"
cp LICENSE "$ROOT/LICENSE"

cat > "$ROOT/HookshotBuilder.command" <<'EOF'
#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TOOLS="$ROOT/tools"
export WGPU_NATIVE="$TOOLS/wgpu-native"
export HOOKSHOT_SDL2="$TOOLS/sdl2/lib/libSDL2-2.0.0.dylib"
export HOOKSHOT_FFMPEG="$TOOLS/ffmpeg"
export RX_BACKEND=metal
exec "$TOOLS/python/bin/python3" -I "$TOOLS/source/nx2/make_game.py" "$@"
EOF
chmod +x "$ROOT/HookshotBuilder.command"
cp scripts/macos/README.txt "$ROOT/README.txt"

APP=$ROOT/HookshotBuilder.app
osacompile -o "$APP" scripts/macos/Builder.applescript
mkdir -p "$APP/Contents/Resources"
ln -s ../../../tools "$APP/Contents/Resources/tools"
test -x "$APP/Contents/MacOS/applet"
lipo "$APP/Contents/MacOS/applet" -verify_arch arm64

mkdir -p "$OUT"
cd "$STAGE"
ditto -c -k --sequesterRsrc --keepParent HookshotBuilder "$OUT/HookshotBuilder-macos-arm64.zip"
echo "Created $OUT/HookshotBuilder-macos-arm64.zip"
