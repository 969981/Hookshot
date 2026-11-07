<h1 align="center">
  <br>
  <a>
  <img src="assets/hookshot.png" alt="Hookshot" width="200">
</a>

  <br>
  <b>Hookshot</b>
  <br>
</h1>

<h4 align="center">The Legend of Zelda: Ocarina of Time (Nintendo Switch 2) running natively on Windows and Linux, with no emulator.
</h4>

<p align="center">
  <a href="#project-status">Status</a> |
  <a href="#how-to-build">How to build</a> |
  <a href="#in-game-menu">In-game menu</a> |
  <a href="#how-it-works">How it works</a> |
  <a href="#want-to-help">Want to help?</a> |
  <a href="#credits">Credits</a> |
  <a href="#license">License</a>
</p>

Hookshot is a static recompilation. The game's code is translated to C and compiled into a native Windows or Linux
program, the console's system software is reimplemented, and the Switch 2 graphics are rebuilt on Vulkan. All of this happens
**on your PC, from your own copy of the game**.

> [!IMPORTANT]
> This repository **does not contain the game, keys or an executable**, and it does not explain how to get them. You
> need your own copy. Everything the build creates (the game module, the generated code, the shader caches and your
> saves) comes from your game and stays on your PC. Never share those files.

## Project status

Experimental. It has only been tested on **one Windows PC** (Ryzen 5 7600X, RTX 4060 Ti 16 GB) and **one Linux PC**,
mostly in the area at the start of the game.

| What | Status |
|---|---|
| Kokiri Forest: reaching gameplay and walking around | ✅ Works |
| Sound, controller, keyboard and saves | ✅ Works |
| In-game menu, frame generation and upscaling | ✅ Works |
| Linux, native (Wayland or X11): the title movie, play from a save, sound, a DualSense, the menu | ✅ Works |
| Rebinding the controller and the keyboard (in the menu) | ✅ Works |
| Black flashes while moving | ✅ Fixed in testing, more play needed |
| Performance without frame generation | ⚠️ About 50 fps walking, not yet a steady 60 |
| Models with missing pieces (doors, fences, parts of trees) | ✅ Fixed where tested, more play needed |
| Stutter when entering new areas | ⚠️ Still happens, mostly the first time |
| Ultrawide | ⚠️ The 3D keeps its shape, but the HUD is stretched and the picture is softer |
| Hyrule Field and the rest of the game | ❓ Not tested yet |
| AMD and Intel cards | ❓ Never tested |

## How to build

The game does not come ready-made: it is built on your PC from your copy.

**With Hookshot Builder, the easiest:**

1. Download the HookshotBuilder zip from the Releases page, extract it, and run `HookshotBuilder.exe`. Keep the `tools`
   folder beside it.
2. Choose your game's `.nup`, or your `romfs` and `exefs` folders. The builder checks that it is the right version.
3. Choose a language and a folder for the finished game, and start the build.
4. When it finishes, open `hookshot.exe` in that folder.

**From the command line:**

```powershell
python -I nx2/make_game.py --nup "C:\path\your-game.nup" --out "C:\Games\Hookshot" --language en-US
```

Or with folders:

```powershell
python -I nx2/make_game.py --romfs "C:\path\romfs" --exefs "C:\path\exefs" --out "C:\Games\Hookshot" --language en-US
```

Add `--check` with your `.nup` or `--exefs` to only check the game's version. It builds nothing.

A build takes about 40 minutes, and you can use your PC in the meantime.

**On Linux, with the release folder:**

1. Download `Hookshot-linux.tar.gz` from the Releases page and extract it (`tar -xzf`, which keeps the files
   executable). Keep everything in the folder together.
2. Run `./hookshot`, or `./hookshot /path/to/your-game.nup`. The first time, it builds the game from your `.nup` into
   that folder, then prepares the shaders (a minute or two; you can skip it, and the game then prepares each shader
   the first time it draws it, with short stutters). It shows its progress in a window, or in the terminal when there is
   no desktop. Everything it prints is also in `build.log`.
3. After that, `./hookshot` starts the game. It builds again only when the folder is updated.

The folder brings everything it needs: its own Python, compiler and helpers, SDL3 and FFmpeg. Nothing needs to be
installed.

**On Linux, from the command line:**

```sh
scripts/fetch-deps.sh linux        # the pinned dependencies, into work/deps
python3 -I nx2/make_game.py --target linux --nup ~/your-game.nup --out ~/Games/Hookshot --language en-US
```

This needs clang, lld, pkg-config, SDL3's and FreeType's development files and the Python packages below. Then start
`hookshot_rt` in the output folder.

The Windows game can also be built on Linux (and played with Proton or Wine). Microsoft's C library and Windows SDK
are downloaded once by [xwin](https://github.com/Jake-Shadle/xwin), which asks you to accept Microsoft's licence:

```sh
cargo install xwin --locked
scripts/setup-winsysroot.sh
python3 -I nx2/make_game.py --target windows --nup ~/your-game.nup --out ~/Games/Hookshot --language en-US
```

This needs clang and lld (with `lld-link`).

### What you need

- On Windows: 64-bit Windows 10 or 11, and the Visual Studio C++ tools and the Windows SDK. Microsoft does not allow
  these to be bundled, so install [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/) with
  "Desktop development with C++". The builder tells you if they are missing.
- On Linux: 64-bit, with glibc 2.35 or newer (Ubuntu 22.04, Debian 12 or later), Wayland or X11, and the Vulkan
  driver for your card. The release folder needs nothing else.
- A graphics card with Vulkan 1.3 and mesh shaders, with an up-to-date driver. Only NVIDIA has been tested.
- 16 GB of RAM or more. A run uses about 10 GB.
- About 60 GB free while building.
- **Your copy of the game at the supported version:** program `0400486024DEC000`, update v131072, build `e0f3ccfa`.
  Other versions do not work.

To build from the command line you also need:

- Python 3 with `lz4`, `numpy`, `capstone`, `zstandard` and `unicorn`.
- The clang from [emsdk](https://github.com/emscripten-core/emsdk), at `C:\emsdk`.
- Visual Studio 2022 with .NET 8, only if you want to change the builder itself.
- The prebuilt tools that go in `third_party/`: `a64emit`, `lld-link`, `zbic` and SDL3.

### What this does on your PC

- The build reads your game and never changes it. It writes to the folder you choose and to
  `%LOCALAPPDATA%\HookshotBuilder` (Linux: `~/.local/share/HookshotBuilder`).
- The romfs is hard-linked instead of copied where the disk allows it.
- Your saves are kept where your system keeps games' saves: `Documents\My Games\Hookshot` on Windows (where
  Documents is, OneDrive included), `~/.local/share/hookshot` on Linux (`$XDG_DATA_HOME/hookshot`). A new or rebuilt
  game folder finds them there. Saves from an older version, in a `user` folder beside the game, are moved there the
  first time, and `saves-moved.txt` beside the game says where. `--user <folder>` uses another folder.
- On Linux, the launcher adds Hookshot to your desktop's menus with its icon
  (`~/.local/share/applications/io.github.NotRexed.Hookshot.desktop`), and updates it if you move the folder. Set
  `HOOKSHOT_NO_DESKTOP=1` to leave it out.
- It compiles with half your processor's threads. With little memory, use fewer: set `HOOKSHOT_JOBS`, for example
  `HOOKSHOT_JOBS=3 ./hookshot`.
- To uninstall, delete the game folder and `%LOCALAPPDATA%\HookshotBuilder` (Linux: `~/.local/share/HookshotBuilder`
  and the desktop file above). Your saves stay until you delete their folder too.

### If something goes wrong

| Problem | What to do |
|---|---|
| "Hookshot supports build e0f3ccfa" | Check that you chose the right game with its update installed |
| The game opens and closes | Update your graphics driver |
| Stutter the first time you see something | Normal for now: shaders are being prepared. It gets better when you pass through the same place again |
| The game is in the wrong language | Change the code in `language.txt` beside the game. The file lists them all |

If the problem continues, report it with where you were in the game, your graphics card and a screenshot or video.
Never send game files.

## In-game menu

Press <kbd>Insert</kbd> or <kbd>F1</kbd> in game. On a controller, press its Home button (PS on a DualSense), or Select
and Start together. Everything is off by default, and your choices are saved in `hookshot_options.ini` beside the game.

The arrow keys or the D-pad choose and change. <kbd>Enter</kbd> or the bottom face button selects, and <kbd>Esc</kbd>
or the right face button goes back.

| Option | Choices |
|---|---|
| Fullscreen | Off, On (also <kbd>F11</kbd>) |
| Frame generation | Off, 2x, 3x, 4x |
| Frame pacing | Off, On |
| Resolution | Auto (the game's own dynamic resolution), 100%, 75%, 67%, 50% |
| Upscaling | Off, Catmull-Rom, Lanczos |
| Sharpness | 0 to 100% |
| Game speed | Per frame, Real time |
| Handheld profile | Off, On |
| Ultrawide | 16:9, Monitor's shape |
| FPS counter | Off, On |
| Controls | Opens the Controls page |

With Game speed on Per frame, the game runs slower whenever it is below 60 fps. Real time keeps it at normal speed.

### Controls

Use a controller, or the keyboard and mouse. On the keyboard, <kbd>W</kbd> <kbd>A</kbd> <kbd>S</kbd> <kbd>D</kbd> move
and the arrow keys turn the camera. Click in the window to turn the camera with the mouse; <kbd>Esc</kbd> lets it go.

The menu's **Controls** page lists each of the Switch's buttons with what presses it on a controller and on the
keyboard. Select one and press the new button or key. <kbd>Delete</kbd> clears it, and "Reset to defaults" puts every
one back. A controller button taken from another one swaps with it, and a key is taken from any other button. The
mouse buttons and the movement and camera keys cannot be changed.

## How it works

1. `nx2/lift.py` translates the game's ARM64 code to C.
2. `nx2/build.py` compiles that C together with `host/`, which plays the part of the console's system.
3. `host/nvn_native.c` records the game's NVN2 graphics calls, and `gpu/vk/` replays them on Vulkan.
4. Switch 2 Ampere shaders are lowered to Maxwell (`lower_to_maxwell.h`) and translated to SPIR-V by Eden's shader
   recompiler.

More detail is in the [development guide](docs/development.md) and the [design notes](docs/design.md).

## Want to help?

Testing on other graphics cards and reporting the result already helps a lot, and so does playing further into the
game than anyone has yet.

Bigger ways to help:

- **Linux on other PCs.** The native Linux build has been played on one PC so far. Reports from other distributions,
  desktops and graphics cards help.
- **Fixes in your own fork.** Fork the project, fix what you find and send the change back. Keep game files, keys and
  anything generated from your game out of it.

## Credits

Hookshot exists thanks to the work of many people:

- **NotRexed**: Hookshot, BlueShell (Mario Kart World), Skybound (Tears of the Kingdom) and the a64-kit tools.
- **[Eden](https://git.eden-emu.dev/eden-emu/eden)** and **Eglantine**: the shader recompiler and Switch 2 shader work.
- **suyu**: the graphics decoders and the AArch64 translator.
- **[seedds/nxnative](https://github.com/seedds/nxnative)**: reference for how task and mesh shaders work.
- **[zortos293/Lichii](https://github.com/zortos293/Lichii)**: reference for Ampere shader lowering.
- **[kinnay/zbic](https://github.com/kinnay/zbic)**, **Opus** and **SDL**: decompression, audio and controllers.

## License

Hookshot's own code is under the [MIT License](LICENSE).

It also includes code from Eden and suyu, which stays under the GPL, so a compiled game as a whole is covered by
GPL-3.0-or-later. Third-party parts keep their own licenses. The [LICENSE](LICENSE) file lists which folders are which.

Hookshot is an unofficial fan project, not affiliated with or endorsed by Nintendo. The Legend of Zelda and Ocarina of
Time are trademarks of Nintendo.
