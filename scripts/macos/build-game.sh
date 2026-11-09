#!/bin/sh
# Run Hookshot's builder from this checkout with the dependencies scripts/macos/deps.sh made.
# Usage: scripts/macos/build-game.sh --nup /path/to/game.nup --out /path/to/game-folder [--jobs N] [--check]
set -eu
SRC=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
DEPS="$SRC/build-macos/deps"
export WGPU_NATIVE="$DEPS/wgpu-native"
export HOOKSHOT_SDL2="$DEPS/sdl2/lib/libSDL2-2.0.0.dylib"
export HOOKSHOT_FFMPEG="$DEPS/ffmpeg"
export RX_BACKEND=metal
export SDKROOT="${SDKROOT:-$(xcrun --show-sdk-path)}"
exec "$DEPS/python/bin/python3" -I "$SRC/nx2/make_game.py" "$@"
