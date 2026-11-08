"""Compile AMD's FSR 3.1 upscaler passes into the SPIR-V headers gpu/vk uses.

The FidelityFX SDK ships each pass as GLSL that includes the SDK's own GPU headers, so the eleven
variants below are the same eight passes compiled with different options. They are checked in like
every other shader in gpu/vk/shaders, so a clone builds without glslang; this script regenerates
them, and --check proves the checked-in files still match their sources (tests/test_fsr3_shaders.py).

    python -I -B scripts/fsr3_shaders.py            # write gpu/vk/shaders/fsr3_*_comp_spv.h
    python -I -B scripts/fsr3_shaders.py --check    # compare instead of writing
"""
import argparse
import difflib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "third_party/fidelityfx-sdk"
GPU = SDK / "include/FidelityFX/gpu"
PASSES = SDK / "shaders/fsr3upscaler"
OUT = ROOT / "gpu/vk/shaders"

# The option set the renderer records these passes under. Each one is a promise about the inputs:
# the colour is HDR, the motion is low-resolution and not jittered, and the depth is reversed. They
# have to match what gpu/vk/vk_fsr3.cpp hands the passes, which is why they live in one place.
OPTIONS = [
    "-DFFX_GPU=1",
    "-DFFX_GLSL=1",
    "-DFFX_FSR3UPSCALER_OPTION_UPSAMPLE_USE_LANCZOS_TYPE=2",
    "-DFFX_FSR3UPSCALER_OPTION_REPROJECT_USE_LANCZOS_TYPE=0",
    "-DFFX_FSR3UPSCALER_OPTION_HDR_COLOR_INPUT=1",
    "-DFFX_FSR3UPSCALER_OPTION_LOW_RESOLUTION_MOTION_VECTORS=1",
    "-DFFX_FSR3UPSCALER_OPTION_JITTERED_MOTION_VECTORS=0",
    "-DFFX_FSR3UPSCALER_OPTION_INVERTED_DEPTH=1",
]

# name, the pass GLSL it compiles, extra defines. The _nowave variants are for devices without the
# subgroup operations the single-pass downsampler prefers.
VARIANTS = [
    ("prepare_inputs", "prepare_inputs", []),
    ("luma_pyramid", "luma_pyramid", []),
    ("luma_pyramid_nowave", "luma_pyramid", ["-DFFX_SPD_NO_WAVE_OPERATIONS=1"]),
    ("shading_change_pyramid", "shading_change_pyramid", []),
    ("shading_change_pyramid_nowave", "shading_change_pyramid", ["-DFFX_SPD_NO_WAVE_OPERATIONS=1"]),
    ("shading_change", "shading_change", []),
    ("prepare_reactivity", "prepare_reactivity", []),
    ("luma_instability", "luma_instability", []),
    ("accumulate", "accumulate", []),
    ("accumulate_sharpen", "accumulate", ["-DFFX_FSR3UPSCALER_OPTION_APPLY_SHARPENING=1"]),
    ("rcas", "rcas", []),
]


def compile_one(glslang, name, pass_name, extra, out_path, target_env, variable=True):
    """Compile one variant. With `variable`, glslang writes a C header; without it, binary SPIR-V,
    which is what spirv-val reads."""
    defines = list(OPTIONS)
    if not any("APPLY_SHARPENING" in d for d in extra):
        defines.append("-DFFX_FSR3UPSCALER_OPTION_APPLY_SHARPENING=0")
    cmd = [glslang, "-V", "--quiet", "-S", "comp",
           "-I" + str(GPU), "-I" + str(GPU / "fsr3upscaler"),
           *defines, *extra]
    if variable:
        cmd += ["--variable-name", f"FSR3_{name.upper()}_COMP_SPV"]
    cmd += ["-o", str(out_path),
            str(PASSES / f"ffx_fsr3upscaler_{pass_name}_pass.glsl"),
            "--target-env", target_env]
    subprocess.run(cmd, check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--glslang", default=None, help="glslangValidator; found on PATH by default")
    p.add_argument("--spirv-val", default=None, help="spirv-val, to validate each module")
    p.add_argument("--target-env", default="spirv1.3")
    p.add_argument("--check", action="store_true", help="compare with the checked-in headers")
    a = p.parse_args()
    glslang = a.glslang or shutil.which("glslangValidator") or shutil.which("glslang")
    if not glslang:
        print("glslangValidator not found; pass --glslang", file=sys.stderr)
        return 2

    bad = []
    with tempfile.TemporaryDirectory(prefix="fsr3-shaders-") as tmp:
        for name, pass_name, extra in VARIANTS:
            header = OUT / f"fsr3_{name}_comp_spv.h"
            made = Path(tmp) / header.name
            compile_one(glslang, name, pass_name, extra, made, a.target_env)
            if a.spirv_val:
                spv = Path(tmp) / f"{name}.spv"
                compile_one(glslang, name, pass_name, extra, spv, a.target_env, variable=False)
                subprocess.run([a.spirv_val, str(spv)], check=True)
            if a.check:
                have = header.read_text() if header.exists() else ""
                want = made.read_text()
                if have != want:
                    bad.append(name)
                    if not have:
                        print(f"{header.name}: missing", file=sys.stderr)
                    else:
                        diff = difflib.unified_diff(have.splitlines(), want.splitlines(),
                                                    "checked in", "regenerated", n=0, lineterm="")
                        print("\n".join(list(diff)[:12]), file=sys.stderr)
            else:
                shutil.copyfile(made, header)
                print(f"wrote {header.relative_to(ROOT)} ({header.stat().st_size} bytes)")
    if bad:
        print(f"{len(bad)} header(s) do not match their source: {', '.join(bad)}", file=sys.stderr)
        return 1
    print(f"fsr3 shaders: {len(VARIANTS)} {'match' if a.check else 'written'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
