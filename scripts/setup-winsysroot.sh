#!/usr/bin/env bash
# (hookshot) Microsoft's C library and Windows SDK for building the Windows game from a Linux PC (nx2/target.py:
# build.py / make_game.py --target windows, the default). Microsoft does not allow them to be bundled: xwin downloads
# them from Microsoft and lays them out as clang's -Xmicrosoft-windows-sys-root expects, with lower-case links for
# the headers' and libraries' mixed spellings.
#
#     scripts/setup-winsysroot.sh [--accept-license]
#
# Without --accept-license, xwin shows Microsoft's licence and asks. The folder: RX_WINSYSROOT, or
# ~/.local/share/hookshot/winsysroot (where nx2/target.py looks). Needs xwin (cargo install xwin --locked).
set -euo pipefail

dest="${RX_WINSYSROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/hookshot/winsysroot}"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/hookshot/xwin"

xwin="$(command -v xwin || true)"
[ -n "$xwin" ] || [ ! -x "$HOME/.cargo/bin/xwin" ] || xwin="$HOME/.cargo/bin/xwin"
if [ -z "$xwin" ]; then
    echo "xwin not found: install it with  cargo install xwin --locked  (or a release from github.com/Jake-Shadle/xwin)" >&2
    exit 1
fi
for tool in clang lld-link; do
    command -v "$tool" >/dev/null || { echo "$tool not found: install clang and lld" >&2; exit 1; }
done

license=()
[ "${1:-}" = "--accept-license" ] && license=(--accept-license)

mkdir -p "$(dirname "$dest")" "$cache"
"$xwin" "${license[@]}" --cache-dir "$cache" --arch x86_64 splat --output "$dest" \
    --use-winsysroot-style --preserve-ms-arch-notation

# the same check make_game.py makes before a build
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
printf '#include <stdio.h>\n#include <windows.h>\nint main(void) { MessageBoxA(0, "", "", 0); return 0; }\n' > "$tmp/t.c"
clang --target=x86_64-pc-windows-msvc -Xmicrosoft-windows-sys-root "$dest" -fuse-ld=lld "$tmp/t.c" -luser32 -o "$tmp/t.exe"
echo "Windows SDK ready at $dest"
