#!/bin/bash
# Build the native arm64 libraries and Python runtime used by Hookshot's macOS builder.
set -euo pipefail
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "The macOS builder dependencies require an Apple Silicon macOS host." >&2
    exit 1
fi
cd "$(dirname "$0")/../.."

DEPS=${DEPS:-$PWD/build-macos/deps}
DL=$DEPS/downloads
mkdir -p "$DEPS" "$DL"
JOBS=$(sysctl -n hw.logicalcpu)

PY_TAG=20261026
PY_VER=3.12.16
SDL_VER=2.32.10
FFMPEG_VER=9.0.2
WGPU_VER=v29.0.1.1
MIN_MACOS=13.3

fetch() {
    local url=$1 file=$2
    [ -s "$DL/$file" ] || curl -fL --retry 5 --retry-delay 10 --proto '=https' -o "$DL/$file.part" "$url"
    [ -s "$DL/$file" ] || mv -f "$DL/$file.part" "$DL/$file"
}

# ---- arm64 Python and the builder's modules ----
if [ ! -x "$DEPS/python/bin/python3" ]; then
    fetch "https://github.com/astral-sh/python-build-standalone/releases/download/$PY_TAG/cpython-$PY_VER%2B$PY_TAG-aarch64-apple-darwin-install_only.tar.gz" python.tar.gz
    tar -xzf "$DL/python.tar.gz" -C "$DEPS"
    "$DEPS/python/bin/python3" -m pip install --no-cache-dir --only-binary :all: \
        --disable-pip-version-check -q \
        "lz4==4.4.5" "numpy==2.5.4" "capstone==5.0.9" "unicorn==2.1.4" "zstandard==0.25.0"
    rm -rf "$DEPS/python/lib/python3.12/test" "$DEPS/python/lib/python3.12/idlelib" \
        "$DEPS/python/lib/python3.12/ensurepip"
    find "$DEPS/python" -name __pycache__ -prune -exec rm -rf {} +
fi

# ---- SDL2, built as a native arm64 dylib ----
if [ ! -f "$DEPS/sdl2/lib/libSDL2-2.0.0.dylib" ]; then
    fetch "https://github.com/libsdl-org/SDL/releases/download/release-$SDL_VER/SDL2-$SDL_VER.tar.gz" sdl2.tar.gz
    rm -rf "$DEPS/src-sdl2" "$DEPS/sdl2"
    mkdir -p "$DEPS/src-sdl2"
    tar -xzf "$DL/sdl2.tar.gz" -C "$DEPS/src-sdl2" --strip-components=1
    cmake -S "$DEPS/src-sdl2" -B "$DEPS/src-sdl2/build" -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_OSX_DEPLOYMENT_TARGET="$MIN_MACOS" \
        -DSDL_SHARED=ON -DSDL_STATIC=OFF -DSDL_TEST=OFF
    cmake --build "$DEPS/src-sdl2/build" --parallel "$JOBS"
    cmake --install "$DEPS/src-sdl2/build" --prefix "$DEPS/sdl2"
    cp "$DEPS/src-sdl2/LICENSE.txt" "$DEPS/sdl2/"
    rm -rf "$DEPS/src-sdl2"
fi

# ---- FFmpeg, static and position-independent for the game executable ----
P=$DEPS/ffmpeg
if [ ! -f "$P/lib/libavcodec.a" ]; then
    rm -rf "$P" "$DEPS/src-ffmpeg"
    fetch "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VER.tar.xz" ffmpeg.tar.xz
    mkdir -p "$DEPS/src-ffmpeg"
    tar -xJf "$DL/ffmpeg.tar.xz" -C "$DEPS/src-ffmpeg" --strip-components=1
    (
        cd "$DEPS/src-ffmpeg"
        CFLAGS="-arch arm64 -mcpu=apple-m1 -mmacosx-version-min=$MIN_MACOS -fPIC" \
            LDFLAGS="-arch arm64 -mmacosx-version-min=$MIN_MACOS" \
            ./configure --prefix="$P" --arch=aarch64 --cpu=apple-m1 --target-os=darwin \
            --cc=clang --enable-static --disable-shared --enable-pic \
            --disable-programs --disable-doc --disable-network --disable-autodetect \
            --disable-everything --disable-avformat --disable-avfilter --disable-avdevice \
            --enable-avcodec --enable-swscale --enable-swresample --enable-avutil \
            --enable-decoder=vp8,vp9,av1 --enable-parser=vp8,vp9,av1
        make -j"$JOBS"
        make install
    )
    rm -rf "$DEPS/src-ffmpeg" "$P/share"
    for lib in libavcodec.a libswscale.a libswresample.a libavutil.a; do
        lipo "$P/lib/$lib" -verify_arch arm64
    done
fi

# ---- Metal-enabled arm64 wgpu-native release ----
if [ ! -f "$DEPS/wgpu-native/lib/libwgpu_native.dylib" ]; then
    fetch "https://github.com/gfx-rs/wgpu-native/releases/download/$WGPU_VER/wgpu-macos-aarch64-release.zip" wgpu.zip
    rm -rf "$DEPS/wgpu-native"
    mkdir -p "$DEPS/wgpu-native"
    unzip -q "$DL/wgpu.zip" -d "$DEPS/wgpu-native"
fi
lipo "$DEPS/sdl2/lib/libSDL2-2.0.0.dylib" -verify_arch arm64
lipo "$DEPS/wgpu-native/lib/libwgpu_native.dylib" -verify_arch arm64

# Keep the notices for the binary dependencies in the downloadable package.
L=$DEPS/licenses
mkdir -p "$L"
cp "$DEPS/sdl2/LICENSE.txt" "$L/SDL2-LICENSE.txt"
curl -fsSL --retry 5 -o "$L/FFmpeg-COPYING.LGPLv2.1" \
    "https://raw.githubusercontent.com/FFmpeg/FFmpeg/n$FFMPEG_VER/COPYING.LGPLv2.1"
curl -fsSL --retry 5 -o "$L/wgpu-native-LICENSE-APACHE" \
    "https://raw.githubusercontent.com/gfx-rs/wgpu-native/$WGPU_VER/LICENSE.APACHE"
curl -fsSL --retry 5 -o "$L/wgpu-native-LICENSE-MIT" \
    "https://raw.githubusercontent.com/gfx-rs/wgpu-native/$WGPU_VER/LICENSE.MIT"
echo "macOS builder dependencies: $(du -sh "$DEPS" | cut -f1)"
