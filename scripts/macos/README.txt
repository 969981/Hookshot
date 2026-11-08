Hookshot Builder for macOS (Apple Silicon)

This package includes a macOS app and an x86-64 command-line builder that run
under Rosetta 2 and use WebGPU's Metal backend. It includes Hookshot source, Python
runtime and modules, SDL2, static FFmpeg libraries, and wgpu-native.

It does not include game data or a built game. You must provide your own
legally obtained .nup dump. Xcode Command Line Tools and Rosetta 2 are required
on the Mac where you build.
The generated executable requires macOS 13.3 or newer.

Double-click HookshotBuilder.app to open the Hookshot Builder interface. It
matches the existing builder flow: choose your dump and output folder, then
watch the build progress. For command-line use, run:

  ./HookshotBuilder.command --nup /path/to/your-game.nup --out /path/to/output

Add --jobs 2 (or another suitable value) to limit parallel compiler memory use.
The output folder contains the built game. Keep this builder package available
if you later use the builder's --update option.
