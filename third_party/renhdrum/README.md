renhdrum.dll — build and deployment notes
==========================================

The DualSense haptics backend (host/haptic.c) loads `renhdrum.dll` from
the game's folder, by absolute path, if it is there. Without it the game
starts fine and every pad keeps its standard rumble.

Source and version
------------------
Upstream: https://github.com/trzz1214/RenHDrum
Tag:      **v0.3.0** (RENHDRUM_VERSION 0x000300; host/haptic.c also
accepts 0x000301 and refuses anything else)
License:  core is MIT OR Apache-2.0 — see NOTICE in this folder.

Expected file
-------------
    renhdrum.dll  414,208 bytes
    SHA-256 3f3cfa954a2842f118d9c3fe29c1ba264115268de0484831340a70436c5f7940

Build it yourself (reproducible)
--------------------------------
From the RenHDrum checkout at the v0.3.0 tag, on Windows with a Rust
toolchain:

    set RUSTFLAGS=-C target-feature=+crt-static
    cargo build --release

The static CRT matters: the shipped DLL then depends only on
api-ms-win-core-synch-l1-2-0.dll, KERNEL32.dll and ntdll.dll (no
VCRUNTIME140 / MSVCP140 / api-ms-win-crt-*), so players need no extra
runtime installed. Copy `target/release/renhdrum.dll` into this folder.

How it reaches the game
-----------------------
The DLL is not committed (the repository's .gitignore excludes *.dll,
like SDL2.dll/SDL3.dll). It travels by the same channel as those:

* `nx2/build.py` copies `third_party/renhdrum/renhdrum.dll` next to
  out/hookshot.exe after a build, when the file is present.
* `nx2/make_game.py` stages it into the game folder when packaging,
  when present (not on Linux).
* `scripts/release_parts.py package` carries untracked prebuilt files
  from third_party into the release's third_party part (PREBUILT_EXT
  includes .dll) — the same mechanism SDL2.dll/SDL3.dll ride.

So for a release: place the built DLL in `third_party/renhdrum/`
before packaging, or carry it over from the previous release's
third_party part. A missing DLL never breaks startup or packaging;
the haptics backend then reports "Haptic backend unavailable" and the
standard rumble plays.

Verification checklist for a fresh environment
----------------------------------------------
1. `python -I -B nx2/make_game.py --romfs ... --exefs ... --out ...`
   → `renhdrum.dll` (414,208 bytes) appears in the output folder.
2. Run the game, check hookshot_log.txt:
   `haptic: renhdrum.dll 0.3.0 loaded` (or the standard-rumble-only
   note when the DLL is absent — no crash either way).
