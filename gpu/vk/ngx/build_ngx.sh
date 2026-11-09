#!/bin/sh
# hookshot_ngx.dll: the NGX bridge (hookshot_ngx.cpp) for the MSVC target. The Vulkan renderer is built with
# llvm-mingw and does not link NVIDIA's SDK. This DLL does, and the game loads it only when it is beside hookshot_vk.dll.
#
# NVIDIA's DLSS SDK is not part of Hookshot. Its license does not allow putting the headers, the static library or
# nvngx_dlss.dll in this GPL repository. Check the SDK out somewhere else and point this script at it. Do not copy it
# into the tree.
#
#   https://github.com/NVIDIA/DLSS
#
# On Linux, llvm-mingw's clang and lld-link with the MSVC CRT and Windows SDK that xwin splats
# (https://github.com/Jake-Shadle/xwin):
#   MINGW=<llvm-mingw> XWIN=<xwin splat> DLSS_SDK=<NVIDIA/DLSS checkout> VK_HEADERS=<Vulkan-Headers> \
#     OUT=<dir> ./build_ngx.sh
# OUT defaults to ./out next to this script (gitignored: *.dll). The game also needs nvngx_dlss.dll from the same SDK,
# copied by you into the game's bin folder. This script does not copy it.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
: "${OUT:=$HERE/out}"
missing=
for v in MINGW XWIN DLSS_SDK VK_HEADERS; do
    eval "val=\${$v-}"
    if [ -z "$val" ]; then
        echo "build_ngx.sh: set $v (see the comment at the top)" >&2
        missing=1
    fi
done
[ -z "$missing" ] || exit 1
if [ ! -f "$DLSS_SDK/include/nvsdk_ngx_vk.h" ] || [ ! -f "$DLSS_SDK/lib/Windows_x86_64/x64/nvsdk_ngx_s.lib" ]; then
    echo "build_ngx.sh: $DLSS_SDK does not look like NVIDIA's DLSS SDK (include/nvsdk_ngx_vk.h and lib/Windows_x86_64/x64/nvsdk_ngx_s.lib)" >&2
    exit 1
fi
case "$DLSS_SDK" in
    "$HERE"/*|"$HERE") echo "build_ngx.sh: keep the SDK outside this repository" >&2; exit 1 ;;
esac
mkdir -p "$OUT"
"$MINGW/bin/clang" --target=x86_64-pc-windows-msvc -fms-compatibility -fms-extensions -std=c++17 -O2 -DNDEBUG -DHSNGX_BUILDING \
  -D_MT -Xclang --dependent-lib=libcmt -Xclang --dependent-lib=libcpmt -fno-exceptions -Wno-everything \
  -isystem "$XWIN/crt/include" -isystem "$XWIN/sdk/include/ucrt" -isystem "$XWIN/sdk/include/um" -isystem "$XWIN/sdk/include/shared" \
  -I "$VK_HEADERS/include" -I "$DLSS_SDK/include" -I "$HERE" -c "$HERE/hookshot_ngx.cpp" -o "$OUT/hookshot_ngx.obj"
"$MINGW/bin/lld-link" /dll /out:"$OUT/hookshot_ngx.dll" /machine:x64 /opt:ref /opt:icf \
  /libpath:"$XWIN/crt/lib/x86_64" /libpath:"$XWIN/sdk/lib/um/x86_64" /libpath:"$XWIN/sdk/lib/ucrt/x86_64" \
  "$OUT/hookshot_ngx.obj" "$DLSS_SDK/lib/Windows_x86_64/x64/nvsdk_ngx_s.lib" libcmt.lib libcpmt.lib libucrt.lib libvcruntime.lib \
  kernel32.lib user32.lib advapi32.lib version.lib shell32.lib ole32.lib uuid.lib setupapi.lib cfgmgr32.lib
echo "built $OUT/hookshot_ngx.dll"
echo "copy nvngx_dlss.dll from the SDK (lib/Windows_x86_64/) into the game's bin folder yourself; it is not built here"
