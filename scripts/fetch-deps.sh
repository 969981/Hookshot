#!/usr/bin/env bash
# (hookshot) What the runtime build and the release folders take that the repository does not hold, at pinned versions,
# every download checked against its sha256 here: the Vulkan headers, SPIR-V headers and VMA (build.py's CPM folder),
# FFmpeg's LGPL shared builds (BtbN), SDL3 (Windows' build; for Linux built here from its source), and the release folders' interpreter (python-build-standalone
# with lz4, numpy, capstone, zstandard) and compiler (an LLVM release, trimmed to clang, lld, llvm-nm, llvm-ar).
#
#     scripts/fetch-deps.sh [linux|windows|all]     (default all; run on Linux, as the builds are)
#
# Into OOT_DEPS, else work/deps:
#     cpm/<name>/<version>/            RX_CPM=<that>/cpm for nx2/build.py
#     linux-python/ win-python/        scripts/package.py --python
#     linux-llvm/ win-llvm/            scripts/package.py --llvm (and the Linux one on PATH for the builds)
#     linux-sdl3/                      RX_SDL3=<that> for nx2/build.py: libSDL3.so.0 bundled beside the Linux runtime, for
#                                      systems without SDL3 (Ubuntu 22.04, Debian 12). Needs cmake, make, a C
#                                      compiler and the headers of Wayland, X11, PipeWire, PulseAudio, ALSA and udev
#                                      (SDL loads each at run time)
#     linux-libcxx/                    RX_LIBCXX=<that> for nx2/build.py: LLVM's libc++ (from the same LLVM release), linked
#                                      into the Linux renderer: Ubuntu 22.04's libstdc++ has no <format>, and the
#                                      release then asks for no libstdc++ of a player's system
#     third_party/sdl3/SDL3.dll        (Windows: beside the runtime; into the repository's third_party/sdl3/)
# Downloads are kept in <deps>/dl and not fetched again while their hash matches. BtbN's "latest" FFmpeg builds are
# replaced daily: when the pinned one is gone, the script says so; take the new one's hash from its checksums.sha256.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
deps="${OOT_DEPS:-$root/work/deps}"
want="${1:-all}"
dl="$deps/dl"
mkdir -p "$dl" "$deps/cpm"

GH=https://github.com
PBS=20261026
PY=3.12.16
LLVM=23.1.5
FFMPEG=ffmpeg-n8.1-latest
SDL=3.4.18

# fetch <file in dl/> <url> <sha256>
fetch() {
    local f="$dl/$1"
    if [ -f "$f" ] && echo "$3  $f" | sha256sum -c --status; then return; fi
    echo "fetch $1"
    curl -fsSL --retry 3 -o "$f.part" "$2" || { echo "fetch-deps: $2 could not be downloaded" >&2; exit 1; }
    if ! echo "$3  $f.part" | sha256sum -c --status; then
        echo "fetch-deps: $1 is not the pinned file (sha256 $(sha256sum "$f.part" | cut -d' ' -f1), pinned $3)" >&2
        rm -f "$f.part"
        exit 1
    fi
    mv "$f.part" "$f"
}

# headers: <name> <github repo> <tag> <sha256>
headers() {
    fetch "$1.tar.gz" "$GH/$2/archive/refs/tags/$3.tar.gz" "$4"
    local out="$deps/cpm/$1/$3"
    [ -d "$out/include" ] && return
    mkdir -p "$out"
    tar -xzf "$dl/$1.tar.gz" -C "$out" --strip-components=1
}
headers vulkanheaders KhronosGroup/Vulkan-Headers vulkan-sdk-1.4.335.0 269e95cc5138ea0a0d52fcb0ee19102add2560fedf5a43b1b5c17780c2775764
headers spirv-headers KhronosGroup/SPIRV-Headers vulkan-sdk-1.4.335.0 1c47ca6342ebe86f57b46b8dbeb266fa655a1ca8e10d07e45370ff2d9c36312e
headers vulkanmemoryallocator GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator v3.3.0 c4f6bbe6b5a45c2eb610ca9d231158e313086d5b1a40c9922cb42b597419b14e

# the interpreter first: its pip fetches the wheels for both targets
fetch "cpython-$PY+$PBS-x86_64-unknown-linux-gnu-install_only.tar.gz" \
    "$GH/astral-sh/python-build-standalone/releases/download/$PBS/cpython-$PY%2B$PBS-x86_64-unknown-linux-gnu-install_only.tar.gz" \
    6a79e922e50006b601f29e688407659da1c2b86ebcd4e9c7254cf234be8b89de
if [ ! -x "$deps/linux-python/bin/python3" ]; then
    rm -rf "$deps/linux-python" "$deps/.py"
    mkdir -p "$deps/.py"
    tar -xzf "$dl/cpython-$PY+$PBS-x86_64-unknown-linux-gnu-install_only.tar.gz" -C "$deps/.py"
    mv "$deps/.py/python" "$deps/linux-python"
    rmdir "$deps/.py"
fi
pyhost="$deps/linux-python/bin/python3"

# wheels: pip downloads them for the target's platform, each checked against its hash here (--require-hashes); unpacked
# as pip would install them (none has scripts or data)
wheels() {   # <site-packages> <pip platform tags, comma-separated> <requirement lines>
    local sp="$1" plat="$2"
    shift 2

    local tags=()
    for t in ${plat//,/ }; do tags+=(--platform "$t"); done   # (pip takes the tags as given: no older manylinux)
    plat="${plat%%,*}"
    "$pyhost" -I -m pip download -q --disable-pip-version-check --no-deps --only-binary :all: --require-hashes \
        "${tags[@]}" --python-version 3.12 --implementation cp -d "$dl/wheels-$plat" -r <(printf '%s\n' "$@")
    for w in "$dl/wheels-$plat"/*.whl; do
        local dist
        dist="$(basename "$w" | cut -d- -f1-2)"
        [ -d "$sp/$dist.dist-info" ] || "$pyhost" -I -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$w" "$sp"
    done
}

# llvm <release asset> <its top folder> <sha256> <folder> <files to keep...>: an LLVM release cut down to what the
# game's build runs
llvm() {
    local asset="$1" top="$2" sum="$3" out="$deps/$4"
    shift 4
    fetch "$asset" "$GH/llvm/llvm-project/releases/download/llvmorg-$LLVM/$asset" "$sum"
    [ -d "$out/bin" ] && return
    local tmp="$deps/.llvm"
    rm -rf "$tmp" && mkdir -p "$tmp"
    tar -xJf "$dl/$asset" -C "$tmp" "${@/#/$top/}" "$top/lib/clang"
    find "$tmp/$top/lib/clang" -mindepth 2 -maxdepth 2 ! -name include -exec rm -rf {} +   # (the headers: no runtimes)
    mkdir -p "$out"
    mv "$tmp/$top/bin" "$tmp/$top/lib" "$out/"
    cp "$dl/llvm-LICENSE.TXT" "$out/LICENSE.TXT"
    rm -rf "$tmp"
}

# (the releases hold no licence of their own: llvm/LICENSE.TXT at the release's tag)
fetch llvm-LICENSE.TXT "https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-$LLVM/llvm/LICENSE.TXT" 8d85c1057d742e597985c7d4e6320b015a9139385cff4cbae06ffc0ebe89afee

# ffmpeg <asset> <sha256>: BtbN's LGPL shared build, in the CPM folder where build.py looks for it
ffmpeg() {
    fetch "$1" "$GH/BtbN/FFmpeg-Builds/releases/download/latest/$1" "$2"
    local name="${1%.tar.xz}"
    name="${name%.zip}"
    [ -d "$deps/cpm/ffmpeg/$name/lib" ] && return
    mkdir -p "$deps/cpm/ffmpeg"
    case "$1" in
    *.zip) "$pyhost" -I -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$dl/$1" "$deps/cpm/ffmpeg" ;;
    *) tar -xJf "$dl/$1" -C "$deps/cpm/ffmpeg" ;;
    esac
}

if [ "$want" = linux ] || [ "$want" = all ]; then
    ffmpeg "$FFMPEG-linux64-lgpl-shared-8.1.tar.xz" 30f08d50a31abf83ba319bc2208fb96e9caf855c1b874de1c12eb3e64ba9c3d7
    wheels "$deps/linux-python/lib/python3.12/site-packages" manylinux_2_28_x86_64,manylinux_2_17_x86_64,manylinux2014_x86_64 \
        "lz4==4.4.5 --hash=sha256:24092635f47538b392c4eaeff14c7270d2c8e806bf4be2a6446a378591c5e69e" \
        "numpy==2.5.4 --hash=sha256:fbde6962867ee75b48b0ee29b2b9372ec5d617799dbaf38e82dc0596f2f7738a" \
        "capstone==5.0.9 --hash=sha256:273fd8d747d2e35c88f91450be51a603ecfaafb00d96d9f315dcb8689c86193e" \
        "zstandard==0.25.0 --hash=sha256:5a56ba0db2d244117ed744dfa8f6f5b366e14148e00de44723413b2f3938a902"
    llvm "LLVM-$LLVM-Linux-X64.tar.xz" "LLVM-$LLVM-Linux-X64" f72edb0b36580ece7f52e23858f00c18fad4e87395ea7ad05d7e55c39f8fc78a linux-llvm \
        bin/clang bin/clang++ bin/clang-23 bin/lld bin/ld.lld bin/llvm-ar bin/llvm-nm bin/llvm-cxxfilt
    if [ ! -f "$deps/linux-libcxx/lib/libc++.a" ]; then
        rm -rf "$deps/.libcxx" && mkdir -p "$deps/.libcxx"
        top="LLVM-$LLVM-Linux-X64"
        tar -xJf "$dl/LLVM-$LLVM-Linux-X64.tar.xz" -C "$deps/.libcxx" "$top/include/c++" \
            "$top/include/x86_64-unknown-linux-gnu" "$top/lib/x86_64-unknown-linux-gnu/libc++.a" \
            "$top/lib/x86_64-unknown-linux-gnu/libc++abi.a"
        mkdir -p "$deps/linux-libcxx/lib"
        mv "$deps/.libcxx/$top/include" "$deps/linux-libcxx/"
        mv "$deps/.libcxx/$top/lib/x86_64-unknown-linux-gnu/"* "$deps/linux-libcxx/lib/"
        cp "$dl/llvm-LICENSE.TXT" "$deps/linux-libcxx/LICENSE.TXT"
        rm -rf "$deps/.libcxx"
    fi
    fetch "SDL3-$SDL.tar.gz" "$GH/libsdl-org/SDL/releases/download/release-$SDL/SDL3-$SDL.tar.gz" \
        9c75cf16330322c217dedd2e0609f1124f1b54b8633e763467b4684d0f4334a3
    if [ ! -f "$deps/linux-sdl3/lib/libSDL3.so.0" ] && [ -z "${NO_SDL3_BUILD:-}" ]; then   # (NO_SDL3_BUILD=1: not needed, a Windows build)
        command -v cmake >/dev/null || { echo "fetch-deps: SDL3 is built with cmake: install it (or leave RX_SDL3 unset for the system's SDL3)" >&2; exit 1; }
        rm -rf "$deps/.sdl3" "$deps/linux-sdl3"
        mkdir -p "$deps/.sdl3"
        tar -xzf "$dl/SDL3-$SDL.tar.gz" -C "$deps/.sdl3" --strip-components=1
        # (the video and audio back ends loaded at run time, as the system's SDL does: a player needs none of them all)
        cmake -S "$deps/.sdl3" -B "$deps/.sdl3/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$deps/linux-sdl3" \
            -DCMAKE_INSTALL_LIBDIR=lib -DSDL_SHARED=ON -DSDL_STATIC=OFF -DSDL_TEST_LIBRARY=OFF -DSDL_TESTS=OFF \
            -DSDL_EXAMPLES=OFF -DSDL_DEPS_SHARED=ON >/dev/null
        conf="$(find "$deps/.sdl3/build" -name SDL_build_config.h | head -1)"
        for d in VIDEO_DRIVER_WAYLAND VIDEO_DRIVER_X11 AUDIO_DRIVER_PIPEWIRE AUDIO_DRIVER_PULSEAUDIO AUDIO_DRIVER_ALSA \
                 VIDEO_VULKAN; do
            grep -q "^#define SDL_$d 1" "$conf" || { echo "fetch-deps: SDL3 would build without $d: install its headers" >&2; exit 1; }
        done
        grep -q "^#define SDL_UDEV_DYNAMIC" "$conf" || { echo "fetch-deps: SDL3 would build without udev (controllers plugged in later): install libudev's headers" >&2; exit 1; }
        cmake --build "$deps/.sdl3/build" -j "${JOBS:-3}" >/dev/null
        cmake --install "$deps/.sdl3/build" >/dev/null
        rm -rf "$deps/.sdl3"
    fi
fi

if [ "$want" = windows ] || [ "$want" = all ]; then
    ffmpeg "$FFMPEG-win64-lgpl-shared-8.1.zip" 5566c69bb640c76627eecdd53f9586a7cb0d4cd42b58da9da249e470ba49111a
    fetch "cpython-$PY+$PBS-x86_64-pc-windows-msvc-install_only.tar.gz" \
        "$GH/astral-sh/python-build-standalone/releases/download/$PBS/cpython-$PY%2B$PBS-x86_64-pc-windows-msvc-install_only.tar.gz" \
        590dfcbe3a34c07301e6332e13e2188b98655cd072b08afa45a7998dc4c22bc5
    if [ ! -f "$deps/win-python/python.exe" ]; then
        rm -rf "$deps/win-python" "$deps/.py"
        mkdir -p "$deps/.py"
        tar -xzf "$dl/cpython-$PY+$PBS-x86_64-pc-windows-msvc-install_only.tar.gz" -C "$deps/.py"
        mv "$deps/.py/python" "$deps/win-python"
        rmdir "$deps/.py"
    fi
    wheels "$deps/win-python/Lib/site-packages" win_amd64 \
        "lz4==4.4.5 --hash=sha256:713a777de88a73425cf08eb11f742cd2c98628e79a8673d6a52e3c5f0c116f33" \
        "numpy==2.5.4 --hash=sha256:a7b1b6353e36a7e50de2973a38d705c88ee93adcf120673cee7f45a4a3fa223a" \
        "capstone==5.0.9 --hash=sha256:732cedbbb56d42e723f14d7af6387f1454194a820b4b96b56d1e53f865ef85d0" \
        "zstandard==0.25.0 --hash=sha256:ffef5a74088f1e09947aecf91011136665152e0b4b359c42be3373897fb39b01"
    llvm "clang+llvm-$LLVM-x86_64-pc-windows-msvc.tar.xz" "clang+llvm-$LLVM-x86_64-pc-windows-msvc" 62fdada7f6fa2b73856486dfbbdba3d2c1882991621615bcb55c60207611c6d4 win-llvm \
        bin/clang.exe bin/lld.exe bin/lld-link.exe bin/llvm-ar.exe bin/llvm-nm.exe bin/llvm-cxxfilt.exe
    fetch "SDL3-$SDL-win32-x64.zip" "$GH/libsdl-org/SDL/releases/download/release-$SDL/SDL3-$SDL-win32-x64.zip" \
        75c2c0fc74e7d1206aaedc22893a35887332f4f85f5c8b74fddc10d177985f2c
    "$pyhost" -I -c 'import sys, zipfile; open(sys.argv[2], "wb").write(zipfile.ZipFile(sys.argv[1]).read("SDL3.dll"))' \
        "$dl/SDL3-$SDL-win32-x64.zip" "$root/third_party/sdl3/SDL3.dll"
fi
echo "deps ready in $deps (RX_CPM=$deps/cpm)"
