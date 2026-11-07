wgpu-native, gfx-rs's C wrapper of wgpu. NOT vendored as a binary: `*.dll`/`*.lib` are
`.gitignore`d, the two downloads below are the whole difference. Third-party notice:
third_party/a64-kit/NOTICE.TOTKPort.md.

- `include/webgpu/` — vendored here (from the same release as the binaries).
- `lib/wgpu_native.dll` + `lib/wgpu_native.dll.lib` — download and drop them in:

    https://github.com/gfx-rs/wgpu-native/releases/download/v29.0.1.1/wgpu-windows-x86_64-msvc-release.zip

`nx2/build.py` finds this folder (or TOTKPort's, or WGPU_NATIVE); without the lib the
game links minus the WebGPU renderer.
