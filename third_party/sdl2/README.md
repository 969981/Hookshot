# SDL2 (runtime DLL, not in git)

`host/pad_sdl.c` loads `SDL2.dll` beside the exe to read DualSense, DualShock 4, Switch Pro and other
controllers; without it only XInput controllers (Xbox pads) work. The build copies this folder's DLL beside
`hookshot.exe` when it is here (`nx2/build.py`).

Put `SDL2.dll` (Windows x64) in this folder:

- https://github.com/libsdl-org/SDL/releases/download/release-2.32.10/SDL2-2.32.10-win32-x64.zip
  (`SDL2-2.32.10-win32-x64.zip`, 2.32.10, checked 2026-11-07; any 2.30+ works)

Only `SDL2.dll` is needed (the zip's `lib/` import library is not used; the runtime `LoadLibraryA`s it).
`LICENSE.txt` is SDL's zlib licence.
