"""The checked-in FSR 3.1 pass shaders still match the FidelityFX SDK sources they came from.

gpu/vk/shaders/fsr3_*_comp_spv.h are generated from third_party/fidelityfx-sdk by
scripts/fsr3_shaders.py and checked in like every other shader here, so a clone builds without
glslang. That only stays true if the headers and their sources cannot drift apart, which is what
this check is for: it recompiles every variant and compares. It also validates each module, so a
pass that compiles but is not valid SPIR-V is caught here rather than on a device.

python -I -B tests/test_fsr3_shaders.py [--glslang glslangValidator] [--spirv-val spirv-val]
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# What the renderer hands the passes (vk_draw.cpp and FSR 4.1's own inputs: tonemapped and square-root encoded colour, standard
# depth, render-pixel motion, jitter told separately). scripts/fsr3_shaders.py compiles them for exactly this; a value here
# changing means changing the renderer too.
OPTIONS_RENDERER_NEEDS = {
    "FFX_FSR3UPSCALER_OPTION_HDR_COLOR_INPUT": "0",
    "FFX_FSR3UPSCALER_OPTION_INVERTED_DEPTH": "0",
    "FFX_FSR3UPSCALER_OPTION_LOW_RESOLUTION_MOTION_VECTORS": "1",
    "FFX_FSR3UPSCALER_OPTION_JITTERED_MOTION_VECTORS": "0",
}


def check_options():
    source = (ROOT / "scripts/fsr3_shaders.py").read_text(encoding="utf-8")
    for name, value in OPTIONS_RENDERER_NEEDS.items():
        if f'"-D{name}={value}"' not in source:
            raise SystemExit(f"scripts/fsr3_shaders.py does not compile with {name}={value}")


def main():
    check_options()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--glslang")
    p.add_argument("--spirv-val")
    a = p.parse_args()
    cmd = [sys.executable, "-I", "-B", str(ROOT / "scripts/fsr3_shaders.py"), "--check"]
    if a.glslang:
        cmd += ["--glslang", a.glslang]
    if a.spirv_val:
        cmd += ["--spirv-val", a.spirv_val]
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
