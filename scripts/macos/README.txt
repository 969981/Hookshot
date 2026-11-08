Hookshot Builder for macOS (Apple Silicon)

This package is an x86-64 command-line builder that runs under Rosetta 2 and
uses WebGPU's Metal backend. It includes the Hookshot builder source, Python
runtime and modules, SDL2, static FFmpeg/dav1d libraries, and wgpu-native.

It does not include game data or a built game. You must provide your own
legally obtained .nup dump. Xcode Command Line Tools and Rosetta 2 are required
on the Mac where you build.

From Terminal, run:

  ./HookshotBuilder.command --nup /path/to/your-game.nup --out /path/to/output

Add --jobs 2 (or another suitable value) to limit parallel compiler memory use.
The output folder contains the built game. Keep this builder package available
if you later use the builder's --update option.
