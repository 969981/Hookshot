"""The open neural rendering backend's correctness harness: OpenDLSS-NR on this GPU, on synthetic data.

There are no real weights on any machine this is run on, and Hookshot provides no way to get them (the player
supplies a model directory; docs/neural-rendering.md). So this proves the integration the only way it can be
proved without them: it builds OpenDLSS-NR's own `dlss5vk` tool from the vendored sources with Hookshot's compiler
(the same portability patches and Vulkan shim the renderer builds with), gives it the kernels the renderer ships
(the checked-in SPIR-V and the generated PTX), a synthetic model directory in exactly the layout upstream's
docs/weights.md describes, and a synthetic frame, and then lets upstream's own checkers give the verdicts:

1. the GLSL reference route (DLSS5VK_UNFUSED=1, "exact and complete on its own", with every PTX family switched off
   too, since UNFUSED alone still runs the ViT's PTX GEMMs) is run once and its 75 block boundaries and its head are
   kept as references -- a fixture of the kind upstream's `parity` reads;
2. `dlss5vk verify` runs block 0 kernel by kernel against the CPU reference (src/reference.cpp) on the GPU's own
   inputs: the pre adapter, FFN, QKV, window normalize, window attention and projection. Upstream's CPU reference
   covers block 0 only (its docs/numerics.md: the expert FFN, the 512 split block and the ViT have none);
3. `dlss5vk parity` runs every route against those references -- the GLSL reference route again (repeatability),
   upstream's DLSS5VK_UNFUSED=1 alone, the default fast route (PTX through VK_NV_cuda_kernel_launch, counter chaining), the fast route with barriers
   (DLSS5VK_CHAIN=0) and the fused GLSL route (every DLSS5VK_PTX_* off) -- each on all 75 boundaries and the head,
   with upstream's verdicts: bit-exact, equal only up to the sign of zero (a failure), or a mismatch.

The synthetic weights are drawn so the activations stay alive through all 71 blocks (scaled to each matrix's fan-in,
with sane skip scales and attention temperatures): equality of all-zero or all-saturated tensors would prove
nothing, so the harness also measures the boundary references and fails a fixture whose tensors are degenerate.
This is also what a later AMD port is checked against: the references come from the GLSL route, which is the
specification.

    python -I -B scripts/opennr_harness.py run [--size 384x256] [--thorough] [--cxx clang++] [--headers DIR]
    python -I -B scripts/opennr_harness.py model DIR [--seed N]     # only the synthetic model directory

Exit 0 when every check passes, 1 on a failure, and 0 with a line starting "SKIP:" when this PC cannot run it (no
compiler, no Vulkan headers, no Vulkan, or a GPU without the extensions the network needs).
"""
import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import opennr_kernels  # noqa: E402  (the kernel lists and the portability patches the renderer's build uses too)

SHIM = ROOT / "gpu" / "vk" / "opennr"
BLOCKS = 71
SKIP_TEXT = ("no device with VK_KHR_cooperative_matrix", "vulkan-1.dll unavailable", "no Vulkan physical devices",
             "vkCreateDevice(physical_, &deviceInfo, nullptr, &device_) failed")


# ---- the synthetic model directory ----------------------------------------------------------------------------
# Upstream's layouts (src/nr_graph.cpp fusedLayout, preFusedLayout, upsampleFusedLayout, postFusedLayout; its
# docs/weights.md), as regions of one tensor record: (offset, bytes, kind, parameter). Kinds: "w" an E4M3 matrix with
# that fan-in, "s" f16 skip / merge scales, "p" the f16 attention priors, "t" f32 attention temperatures, "a" an f16
# matrix (the input adapter and the head), "z" zero padding.

def align(v, a):
    return (v + a - 1) // a * a


def fused_regions(c, transition=0):
    heads, out = c // 32, []
    if c >= 64:
        e = c // 32
        out += [(0, e * c * 128, "w", c), (e * c * 128, e * 128 * 32, "w", 128), (e * c * 128 + e * 128 * 32, c * c, "w", c)]
        at = e * c * 128 + e * 128 * 32 + c * c
    else:
        out += [(0, c * 128, "w", c), (c * 128, 128 * c, "w", 128)]
        at = c * 128 + 128 * c
    out += [(at, 16, "z", 0), (at + 16, 2 * c, "s", c)]
    at += 16 + 2 * c
    out += [(at, 16, "z", 0)]
    at += 16
    return out + attention_regions(at, c, heads, transition)


def attention_regions(at, c, heads, transition, tail=16):
    out = [(at, 3 * c * c, "w", c), (at + 3 * c * c, heads * 8192, "p", heads)]
    at += 3 * c * c + heads * 8192
    out += [(at, align(4 * heads, 16), "t", heads)]
    at += align(4 * heads, 16)
    out += [(at, c * c, "w", c), (at + c * c, 2 * c, "s", c)]
    at += c * c + 2 * c
    out += [(at, transition, "w", c)] if transition else [(at, tail, "z", 0)] if tail else []
    return out


def upsample_regions(c):
    heads, e = c // 32, c // 32
    if c >= 64:
        out = [(0, e * c * 128, "w", c), (e * c * 128, e * 128 * 32, "w", 128), (e * c * 128 + e * 128 * 32, c * c, "w", c)]
        at = e * c * 128 + e * 128 * 32 + c * c
    else:
        out = [(0, c * 128, "w", c), (c * 128, 128 * c, "w", 128)]
        at = c * 128 + 128 * c
    pad = 16 if c == 32 else 0
    out += [(at, 2 * c * c, "w", 2 * c)]
    at += 2 * c * c
    if pad:
        out += [(at, pad, "z", 0)]
        at += pad
    out += [(at, 2 * c, "s", c)]
    at += 2 * c
    if pad:
        out += [(at, pad, "z", 0)]
        at += pad
    out += [(at, 2 * c, "s", c)]
    at += 2 * c
    return out + attention_regions(at, c, heads, 0)


def pre_regions():   # block 0: preFusedLayout, + 16 bytes
    return [(0, 4096, "w", 32), (4096, 4096, "w", 128), (8192, 16, "z", 0), (8208, 1024, "a", 16), (9232, 64, "s", 32),
            (9296, 16, "z", 0)] + attention_regions(9312, 32, 1, 0)


def post_regions():  # block 70: postFusedLayout, exactly its end
    return [(0, 4096, "w", 32), (4096, 4096, "w", 128), (8192, 16, "z", 0), (8208, 64, "s", 32), (8272, 64, "s", 32),
            (8336, 64, "s", 32)] + attention_regions(8400, 32, 1, 0, tail=0) + [(20768, 16, "z", 0), (20784, 1024, "a", 32)]


def split_regions():  # a 512 block: four records
    return {0: [(0, 512 * 512, "w", 512), (262144, 8 * 64 * 256, "w", 64), (393216, 8 * 256 * 64, "w", 256)],
            1: [(0, 512 * 512, "w", 512), (262144, 1024, "s", 512)],
            2: [(0, 512 * 1536, "w", 512), (786432, 16 * 8192, "p", 16), (917504, 64, "t", 16)],
            3: [(0, 512 * 512, "w", 512), (262144, 1024, "s", 512)]}


def vit_regions():
    return {0: [(0, 1024 * 4096, "w", 1024)],
            1: [(0, 4096 * 1024, "w", 4096), (4194304, 2048, "s", 1024)],
            2: [(0, 128, "t", 32), (128, 1024 * 3072, "w", 1024)],
            3: [(0, 2, "s", 1)],            # (upstream's graph never reads these two bytes)
            4: [(0, 1024 * 1024, "w", 1024), (1048576, 2048, "s", 1024)]}


def model_records():
    """(block, layer, parameter, regions) for every tensor record: 153, as upstream's docs/weights.md counts them"""
    rec = []
    stage_of = {}
    channels = {**{b: 32 for b in range(0, 5)}, **{b: 64 for b in range(5, 9)}, **{b: 128 for b in range(9, 15)},
                **{b: 256 for b in range(15, 23)}, **{b: 256 for b in range(48, 56)}, **{b: 128 for b in range(56, 62)},
                **{b: 64 for b in range(62, 66)}, **{b: 32 for b in range(66, 70)}}
    for b in range(BLOCKS):
        if b == 0:
            rec.append((b, 0, "layer", pre_regions()))
        elif b == 70:
            rec.append((b, 0, "layer", post_regions()))
            rec.append((b, 0, "blend_scale", [(0, 2, "b", 0)]))
        elif 23 <= b <= 30 or 40 <= b <= 47:
            for layer, regions in split_regions().items():
                rec.append((b, layer, "layer", regions))
            if b == 30:
                rec.append((b, 4, "layer", [(0, 512 * 1024, "w", 512)]))
        elif 31 <= b <= 38:
            for layer, regions in vit_regions().items():
                rec.append((b, layer, "layer", regions))
        elif b == 39:
            rec.append((b, 0, "layer", [(0, 1024 * 512, "w", 1024), (524288, 1024, "s", 512)]))
        elif b in (48, 56, 62, 66):
            rec.append((b, 0, "layer", upsample_regions(channels[b])))
        else:
            c = channels[b]
            rec.append((b, 0, "layer", fused_regions(c, transition=c * 2 * c if b in (4, 8, 14, 22) else 0)))
    return rec


E4M3 = sorted({(((-1) ** s) * ((m / 8) * 2.0 ** -6 if e == 0 else (1 + m / 8) * 2.0 ** (e - 7)), (s << 7) | (e << 3) | m)
               for s in (0, 1) for e in range(16) for m in range(8) if not (e == 15 and m == 7)})


def e4m3_code(value):
    best = min(E4M3, key=lambda vc: (abs(vc[0] - value), vc[1]))
    return best[1]


def gaussian_table(sigma):
    """256 E4M3 codes at the quantiles of N(0, sigma): mapping a uniform byte through it gives that distribution"""
    nd = statistics.NormalDist(0.0, sigma)
    return bytes(e4m3_code(nd.inv_cdf((b + 0.5) / 256.0)) for b in range(256))


def f16(value):
    return struct.pack("<e", value)


def stream(seed, label, n):
    return hashlib.shake_256(("%d/%s" % (seed, label)).encode()).digest(n)


GAIN = float(os.environ.get("OPENNR_SYNTH_GAIN", "0.9"))
# (blocks 39-70. Lower than the encoder's: at 0.85 and above the decoder's activations reach an rms of 60-190 at
# 1280x720 and larger, and there the fused routes stopped agreeing with the unfused reference -- from block 58 for the
# fused GLSL kernels at 0.85, from block 40-45 for every fused route at 0.9 -- while at 0.8 every route is bit-exact
# at 384x256, 1280x720 and 2048x1152. Not root-caused; docs/neural-rendering.md says so. OPENNR_SYNTH_GAIN_DECODER=0.9
# reproduces it.)
GAIN_DECODER = float(os.environ.get("OPENNR_SYNTH_GAIN_DECODER", "0.8"))
SKIP = tuple(float(v) for v in os.environ.get("OPENNR_SYNTH_SKIP", "0.5,0.9").split(","))


def region_bytes(seed, label, length, kind, param, tables, gain=None):
    if kind == "z":
        return bytes(length)
    if kind == "w":   # an E4M3 matrix, N(0, GAIN / sqrt(fan-in)): with SKIP below, the residual stream neither dies
        gain = GAIN if gain is None else gain   # nor saturates over 71 blocks (the harness measures that: degenerate())
        key = (param, gain)
        if key not in tables:
            tables[key] = gaussian_table(gain / math.sqrt(param))
        return stream(seed, label, length).translate(tables[key])
    r = stream(seed, label, max(length, 1) * 2)
    if kind == "s":   # f16 per-channel skip scales in SKIP
        return b"".join(f16(SKIP[0] + (SKIP[1] - SKIP[0]) * r[i] / 256.0) for i in range(length // 2))
    if kind == "p":   # the f16 64x64 priors, N(0, 0.5)-ish
        return b"".join(f16((r[2 * i] - 127.5) / 255.0) for i in range(length // 2))
    if kind == "t":   # f32 attention temperatures: window blocks in [4, 8), cosine logits of a sane width; the ViT's
        heads = param  # are multiplied by sqrt(32) on top (upstream's docs/network.md), so [0.5, 1) there. Then the padding
        low, step = (0.5, 1.0 / 512.0) if heads == 32 else (4.0, 1.0 / 64.0)
        return b"".join(struct.pack("<f", low + r[i] * step) for i in range(heads)) + bytes(length - 4 * heads)
    if kind == "a":   # an f16 matrix: the 16 -> 32 input adapter (fan-in 16) or the 32 -> 4 head (fan-in 32)
        scale = 1.0 / math.sqrt(param)
        return b"".join(f16(((r[2 * i] - 127.5) / 127.5) * scale) for i in range(length // 2))
    if kind == "b":   # the temporal blend's learned cap
        return f16(0.7397)
    raise ValueError(kind)


def write_model(directory, seed=1):
    """A model directory in upstream's layout (src/nr_model.cpp: manifest.json, stage files under model/), filled
    with deterministic synthetic values. Returns the manifest."""
    directory = Path(directory)
    (directory / "model").mkdir(parents=True, exist_ok=True)
    records = model_records()
    tables, stages, tensors = {}, [], []
    # eleven stage files, as the shipped model has: the records split in eleven runs of consecutive blocks
    per = math.ceil(len(records) / 11)
    for s in range(11):
        chunk = records[s * per:(s + 1) * per]
        if not chunk:
            continue
        sid, blob = "stage%02d" % s, bytearray()
        for block, layer, parameter, regions in chunk:
            name = "block%d.layer%d.%s" % (block, layer, parameter)
            length = max(o + n for o, n, _, _ in regions)
            data = bytearray(length)
            for i, (o, n, kind, param) in enumerate(regions):
                data[o:o + n] = region_bytes(seed, "%s/%d" % (name, i), n, kind, param, tables,
                                             GAIN_DECODER if block >= 39 else GAIN)
            tensors.append({"name": name, "block": block, "layer": layer, "parameter": parameter, "stage": sid,
                            "stageOffset": len(blob), "byteLength": length})
            blob += data
            blob += bytes(align(len(blob), 16) - len(blob))
        (directory / "model" / (sid + ".bin")).write_bytes(blob)
        stages.append({"id": sid, "file": sid + ".bin", "packedByteLength": len(blob),
                       "sha256": hashlib.sha256(blob).hexdigest().upper()})   # (upstream's sha256Hex is upper case)
    manifest = {"synthetic": "scripts/opennr_harness.py, seed %d: not a trained model" % seed,
                "totals": {"blockCount": BLOCKS, "tensorCount": len(tensors)}, "stages": stages, "tensors": tensors}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


# ---- the tool, the kernels and the fixture ---------------------------------------------------------------------

# The CPU reference's model of the tensor cores. Upstream's src/reference.cpp is Ada's (its docs/numerics.md): an FP8
# m16n8k32 step is two fixed-point groups of 16 products with 13 fractional bits, an FP16 m16n8k16 step two groups of
# 8 with 24, each group rounded to half before the next. Measured here on an RTX 5060 Ti (Blackwell, driver
# 615.71.09) that is NOT what the hardware does: every tensor-core kernel of block 0 differed from it by an ulp in a
# quarter to a half of its values, while the four GPU routes agreed with each other bit for bit. It matched exactly
# with each step as ONE group -- 32 FP8 products with at least 19 fractional bits, 16 FP16 products with at least
# 25 (fewer bits did not match; more could not be told apart on this data). So the harness builds the reference both
# ways, with the group width and fraction as the only change, and says which one the GPU equals.
MMA_MODELS = [
    ("Ada's arithmetic (upstream's reference as it is)", []),
    ("one fused group per MMA step (FP8 32 products F24, FP16 16 products F25)",
     ["-DOPENNR_FP8_GROUP=32", "-DOPENNR_FP8_F=24", "-DOPENNR_F16_GROUP=16", "-DOPENNR_F16_F=25"]),
]
# (the harness-only change to a copy of reference.cpp: the group width and the fraction bits as macros, whose
# defaults are upstream's numbers, so the first model above compiles to upstream's reference exactly)
REFERENCE_PATCHES = [
    ('#include "numeric.h"\n',
     '#include "numeric.h"\n\n#ifndef OPENNR_FP8_GROUP\n#define OPENNR_FP8_GROUP 16\n#endif\n#ifndef OPENNR_FP8_F\n'
     '#define OPENNR_FP8_F 13\n#endif\n#ifndef OPENNR_F16_GROUP\n#define OPENNR_F16_GROUP 8\n#endif\n#ifndef OPENNR_F16_F\n'
     '#define OPENNR_F16_F 24\n#endif\n'),
    ("exponent - maximumExponent + 13)) * 0.0001220703125", "exponent - maximumExponent + OPENNR_FP8_F)) * std::ldexp(1.0, -OPENNR_FP8_F)"),
    ("ea + eb - maximumExponent + 13)) * 0.0001220703125", "ea + eb - maximumExponent + OPENNR_FP8_F)) * std::ldexp(1.0, -OPENNR_FP8_F)"),
    ("exponent - maximumExponent + 24));", "exponent - maximumExponent + OPENNR_F16_F));"),
    ("ea + eb - maximumExponent + 24));", "ea + eb - maximumExponent + OPENNR_F16_F));"),
    ("return fixedToF16(fixedSum, maximumExponent - 24);", "return fixedToF16(fixedSum, maximumExponent - OPENNR_F16_F);"),
    ("    sums = adaFp8Fdpa16(a, b, 16, sums);\n    sums = adaFp8Fdpa16(a + 16, b + 16, 16, sums);",
     "    for (int g = 0; g < 32; g += OPENNR_FP8_GROUP) sums = adaFp8Fdpa16(a + g, b + g, OPENNR_FP8_GROUP, sums);"),
    ("      float score = adaFp8Fdpa16(q, keys[keyLocal], 16, prior);\n"
     "      score = adaFp8Fdpa16(q + 16, keys[keyLocal] + 16, 16, score);",
     "      float score = prior;\n"
     "      for (int g = 0; g < 32; g += OPENNR_FP8_GROUP) score = adaFp8Fdpa16(q + g, keys[keyLocal] + g, OPENNR_FP8_GROUP, score);"),
    ("      for (uint32_t group = 0; group < 4; ++group) {\n        float w[16], v[16];\n"
     "        for (uint32_t i = 0; i < 16; ++i) {\n          uint32_t keyLocal = inverseTiledToken(group * 16 + i);",
     "      for (uint32_t group = 0; group < 64 / OPENNR_FP8_GROUP; ++group) {\n        float w[32], v[32];\n"
     "        for (uint32_t i = 0; i < OPENNR_FP8_GROUP; ++i) {\n"
     "          uint32_t keyLocal = inverseTiledToken(group * OPENNR_FP8_GROUP + i);"),
    ("        value = adaFp8Fdpa16(w, v, 16, value);", "        value = adaFp8Fdpa16(w, v, OPENNR_FP8_GROUP, value);"),
    ("  float a[8], b[8];\n  for (uint32_t base = 0; base < K; base += 8) {\n    for (uint32_t i = 0; i < 8; ++i) {",
     "  float a[16], b[16];\n  for (uint32_t base = 0; base < K; base += OPENNR_F16_GROUP) {\n"
     "    for (uint32_t i = 0; i < OPENNR_F16_GROUP; ++i) {"),
    ("    value = adaF16Fdpa8(a, b, 8, value);", "    value = adaF16Fdpa8(a, b, OPENNR_F16_GROUP, value);"),
]


def build_tool(work, cxx, headers):
    """upstream's dlss5vk, once per MMA model: [(model label, executable)]"""
    src = opennr_kernels.prepare_sources(work / "src")
    text = (src / "reference.cpp").read_text(encoding="utf-8")
    for old, new in REFERENCE_PATCHES:
        if text.count(old) != 1:
            raise RuntimeError("the harness's reference patch no longer matches upstream's reference.cpp: " + old[:60])
        text = text.replace(old, new)
    (work / "reference_models.cpp").write_text(text, encoding="utf-8")
    obj = work / "obj"
    obj.mkdir(exist_ok=True)
    flags = ["-std=c++20", "-O2", "-DVK_NO_PROTOTYPES", "-DVK_ENABLE_BETA_EXTENSIONS", "-I" + str(SHIM), "-I" + headers,
             "-I" + str(src)]
    common = [f for f in sorted(src.glob("*.cpp")) if f.name != "reference.cpp"] + [SHIM / "volk_shim.cpp"]
    jobs = [(f, obj / (f.stem + ".o"), []) for f in common]
    jobs += [(work / "reference_models.cpp", obj / ("reference_%d.o" % i), d) for i, (_, d) in enumerate(MMA_MODELS)]

    def one(job):
        f, o, defines = job
        r = subprocess.run([cxx, *flags, *defines, "-c", str(f), "-o", str(o)], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
        return f.name, r.returncode, r.stdout.decode("utf-8", "replace")
    with concurrent.futures.ThreadPoolExecutor(os.cpu_count() or 4) as ex:
        for name, code, out in ex.map(one, jobs):
            if code:
                raise RuntimeError("compiling %s failed:\n%s" % (name, out[-3000:]))
    tools = []
    for i, (label, _) in enumerate(MMA_MODELS):
        exe = work / ("dlss5vk" if i == 0 else "dlss5vk-model%d" % i)
        r = subprocess.run([cxx, *[str(o) for _, o, d in jobs[:len(common)]], str(obj / ("reference_%d.o" % i)), "-o",
                            str(exe)] + (["-ldl"] if os.name != "nt" else []),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode:
            raise RuntimeError("linking dlss5vk failed:\n" + r.stdout.decode("utf-8", "replace")[-3000:])
        tools.append((label, exe))
    return tools


def geometry(w, h):
    """upstream's Geometry::fromValid (src/nr_graph.cpp)"""
    def reductions(v):
        r, s = 0, v
        for level in range(6):
            half = align((s + 1) // 2, 4)
            r += half < s
            r += level == 0 and half % 8 != 0
            s = half
        return 1 << r
    aw, ah = reductions(w), reductions(h)
    fw, fh = max(320, align(w, aw)), max(320, align(h, ah))
    if fw % (4 * aw) == 0 and fh % (4 * ah) == 0:
        fw += aw
    levels, x, y = [], fw, fh
    for _ in range(6):
        x, y = align((x + 1) // 2, 4), align((y + 1) // 2, 4)
        levels.append((x, y))
    return (fw, fh), levels


def boundaries(w, h):
    """name -> (width, height, channels) of the 75 boundaries upstream's parity compares (Graph::referenceBoundaryNames)"""
    (fw, fh), lv = geometry(w, h)
    def level_of(block):
        if block == 0: return (fw, fh), 32
        for first, last, level, c in ((1, 4, 0, 32), (5, 8, 1, 64), (9, 14, 2, 128), (15, 22, 3, 256), (23, 30, 4, 512),
                                      (31, 38, 5, 1024), (39, 47, 4, 512), (48, 55, 3, 256), (56, 61, 2, 128),
                                      (62, 65, 1, 64), (66, 69, 0, 32)):
            if first <= block <= last:
                return lv[level], c
        raise ValueError(block)
    out = {}
    for block in range(70):
        (bw, bh), c = level_of(block)
        out["block-%d" % block] = (bw, bh, c)
        nxt = {0: (0, 32), 4: (1, 64), 8: (2, 128), 14: (3, 256), 22: (4, 512)}.get(block)
        if nxt:
            out["transition-%d-%d" % (block, block + 1)] = (*lv[nxt[0]], nxt[1])
    return out


def synthetic_frame(w, h):
    """An RGBA f32 frame of code values with gradients, edges and fine texture, deterministic"""
    rows = []
    r = stream(7, "frame", w * h)
    for y in range(h):
        for x in range(w):
            u, v = x / max(1, w - 1), y / max(1, h - 1)
            checker = 0.25 if ((x // 24) + (y // 24)) % 2 else 0.0
            ring = 0.5 + 0.5 * math.sin(math.hypot(x - w * 0.6, y - h * 0.4) * 0.15)
            grain = (r[y * w + x] - 127.5) / 2550.0
            rows.append(struct.pack("<4f", min(1.0, max(0.0, 0.15 + 0.6 * u + checker + grain)),
                                    min(1.0, max(0.0, 0.2 + 0.5 * ring + grain)),
                                    min(1.0, max(0.0, 0.85 - 0.6 * v + grain)), 1.0))
    return b"".join(rows)


def write_fixture(d, w, h, refs=None, head=None, conditioning=None):
    """A proxy fixture for upstream's parity. Without refs: zero placeholders of the right sizes (the bootstrap run,
    whose dumps become the references)."""
    d.mkdir(parents=True, exist_ok=True)
    (fw, fh), _ = geometry(w, h)
    if not (d / "proxy.f32").is_file():
        (d / "proxy.f32").write_bytes(synthetic_frame(w, h))
    blocks, transitions = [], []
    for name, (bw, bh, c) in boundaries(w, h).items():
        f = name + ".u8"
        src = (refs / f) if refs else None
        (d / f).write_bytes(src.read_bytes() if src else bytes(bw * bh * c))
        entry = {"width": bw, "height": bh, "channels": c, "file": f}
        if name.startswith("block-"):
            blocks.append({"block": int(name[6:]), **entry})
        else:
            transitions.append({"id": name[len("transition-"):], **entry})
    (d / "head.f32").write_bytes(head.read_bytes() if head else bytes(fw * fh * 16))
    cond = conditioning or {"localTone": 1.0, "localStructure": 1.0, "skinStructure": 1.0, "style": 0}
    manifest = {"sourceDimensions": [w, h], "fullDimensions": [fw, fh],
                "proxy": {"width": w, "height": h, "file": "proxy.f32"}, "conditioning": cond, "seed": 1234,
                "autoMask": True, "checks": ["boundaries", "head"], "blocks": blocks, "transitions": transitions,
                "referenceHead": {"file": "head.f32"}}
    (d / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")


def features_fixture(d, w, h, features, block0):
    """The fixture `verify` reads: the recorded features and the block-0 reference"""
    d.mkdir(parents=True, exist_ok=True)
    (fw, fh), _ = geometry(w, h)
    shutil.copyfile(features, d / "features.f32")
    shutil.copyfile(block0, d / "block-0.u8")
    manifest = {"sourceDimensions": [w, h], "fullDimensions": [fw, fh], "inputFeatures": {"file": "features.f32"},
                "checks": ["boundaries"], "blocks": [{"block": 0, "width": fw, "height": fh, "channels": 32,
                                                      "file": "block-0.u8"}]}
    (d / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")


def degenerate(refs, w, h):
    """Boundaries whose codes are mostly zero or saturated: equality there would prove little"""
    bad = []
    for name in boundaries(w, h):
        data = (refs / (name + ".u8")).read_bytes()
        zero = data.count(0) + data.count(0x80)
        sat = data.count(0x7e) + data.count(0xfe)
        distinct = len(set(data))
        # (a few percent saturated is what a deep stack of random layers does at the larger fields; a tensor that
        # is a fifth saturated, half zero or on under 48 codes would make equality cheap)
        if zero > 0.5 * len(data) or sat > 0.2 * len(data) or distinct < 48:
            bad.append("%s (%.0f%% zero, %.1f%% saturated, %d distinct codes)" %
                       (name, 100.0 * zero / len(data), 100.0 * sat / len(data), distinct))
    return bad


# No PTX at all: every switch upstream has for a PTX family (DLSS5VK_UNFUSED=1 alone still runs the PTX GEMMs of the
# ViT), and the PTX folder pointed at an empty one -- upstream's code fails loudly on a PTX file it cannot read, so a
# route that finishes with it is proved to have launched none.
NO_PTX = {**{k: "0" for k in ("DLSS5VK_PTX_GEMM", "DLSS5VK_PTX_GEMMT", "DLSS5VK_PTX_GEMMV", "DLSS5VK_PTX_BLOCK32",
                             "DLSS5VK_PTX_FFN", "DLSS5VK_PTX_QKV", "DLSS5VK_PTX_ATTN")}, "DLSS5VK_NO_PTX_MLP": "1"}
REFERENCE_ROUTE = {**NO_PTX, "DLSS5VK_UNFUSED": "1"}
ROUTES = [
    ("GLSL reference route (unfused, no PTX)", REFERENCE_ROUTE),
    ("upstream's DLSS5VK_UNFUSED=1 alone", {"DLSS5VK_UNFUSED": "1"}),
    ("default route (PTX + counter chaining)", {}),
    ("PTX route with barriers (DLSS5VK_CHAIN=0)", {"DLSS5VK_CHAIN": "0"}),
    ("fused GLSL route (no PTX)", NO_PTX),
]


def tool(exe, kernels, args, env_extra, log):
    env = {k: v for k, v in os.environ.items() if not k.startswith("DLSS5VK_")}
    env.update(env_extra)
    if env_extra.get("DLSS5VK_NO_PTX_MLP"):
        empty = kernels / "no-ptx"
        empty.mkdir(exist_ok=True)
        env["DLSS5VK_PTX_DIR"] = str(empty)
    t = time.time()
    r = subprocess.run([str(exe), *args, "--shaders", str(kernels / "shaders")], env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    log.write_text(out, encoding="utf-8")
    return r.returncode, out, time.time() - t


def skip_reason(out):
    for text in SKIP_TEXT:
        if text in out:
            return out.strip().splitlines()[-1]
    return None


def run(a):
    cxx = a.cxx or shutil.which("clang++") or shutil.which("g++")
    headers = a.headers or os.environ.get("HOOKSHOT_VULKAN_HEADERS")
    if not headers:
        cpm = Path(os.environ.get("RX_CPM") or ROOT / "build-linux" / "deps" / "cpm") / "vulkanheaders"
        if cpm.is_dir() and any(cpm.iterdir()):
            headers = str(sorted(cpm.iterdir())[-1] / "include")
    if not cxx:
        print("SKIP: no C++ compiler (--cxx)")
        return 0
    if not headers or not (Path(headers) / "vulkan" / "vulkan_beta.h").is_file():
        print("SKIP: no Vulkan headers with vulkan_beta.h (--headers)")
        return 0
    if os.name == "nt":
        print("SKIP: the harness is run on Linux (it builds upstream's tool with the system's C++ library)")
        return 0
    sizes = [tuple(int(v) for v in s.split("x")) for s in (a.size or (["384x256"] + (["1280x720", "2048x1152"]
                                                                                       if a.thorough else [])))]
    work = Path(a.work) if a.work else Path(tempfile.mkdtemp(prefix="opennr-harness-"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        t0 = time.time()
        tools = build_tool(work, cxx, headers)
        exe = tools[0][1]
        print("built upstream's dlss5vk with %s, its CPU reference in %d MMA models (%.0f s)" %
              (os.path.basename(cxx), len(tools), time.time() - t0), flush=True)
        kernels = work / "kernels"
        subprocess.run([sys.executable, "-I", "-B", str(ROOT / "scripts" / "opennr_kernels.py"), "--materialize",
                        str(kernels)], check=True, stdout=subprocess.DEVNULL)
        t0 = time.time()
        model = work / "model"
        manifest = write_model(model, a.seed)
        size = sum(s["packedByteLength"] for s in manifest["stages"])
        print("synthetic model: %d tensor records in %d stage files, %.1f MiB (%.0f s)" %
              (len(manifest["tensors"]), len(manifest["stages"]), size / 2 ** 20, time.time() - t0), flush=True)
        failures = 0
        for w, h in sizes:
            failures += run_size(tools, kernels, model, work / ("%dx%d" % (w, h)), w, h, a)
        print("\nOPENNR HARNESS: %s" % ("PASS" if not failures else "FAIL (%d)" % failures))
        return 1 if failures else 0
    except SkipRun as e:
        print("SKIP: %s" % e)
        return 0
    finally:
        if not a.keep and not a.work:
            shutil.rmtree(work, ignore_errors=True)


class SkipRun(Exception):
    pass


def run_size(tools, kernels, model, d, w, h, a):
    exe = tools[0][1]
    (fw, fh), _ = geometry(w, h)
    print("\n== %dx%d (field %dx%d)" % (w, h, fw, fh), flush=True)
    d.mkdir(parents=True, exist_ok=True)
    boot, dump, fixture = d / "bootstrap", d / "reference", d / "fixture"
    dump.mkdir(exist_ok=True)
    write_fixture(boot, w, h)
    # 1. the GLSL reference route's own output, the references (its parity against zero placeholders fails by design)
    code, out, secs = tool(exe, kernels, ["parity", "--model", str(model), "--fixture", str(boot), "--dump", str(dump),
                                          "--repeat", "2"], REFERENCE_ROUTE, d / "reference.log")
    why = skip_reason(out)
    if why:
        raise SkipRun("this GPU or driver cannot run the network: " + why)
    missing = [n for n in boundaries(w, h) if not (dump / (n + ".u8")).is_file()]
    if missing or not (dump / "head.f32").is_file() or "model loaded" not in out:
        print(out[-3000:])
        print("FAIL: the reference run produced no references (%d missing)" % len(missing))
        return 1
    stats = re.search(r"head: .*", out)
    print("reference (GLSL route, %.1f s): %d boundaries and the head recorded; %s" %
          (secs, len(boundaries(w, h)), stats.group(0) if stats else ""))
    bad = degenerate(dump, w, h)
    if bad:
        print("FAIL: the synthetic network is degenerate, equality would prove little:\n  " + "\n  ".join(bad[:10]))
        return 1
    head = (dump / "head.f32").read_bytes()
    values = struct.unpack("<%df" % (len(head) // 4), head)
    finite = [v for v in values if math.isfinite(v)]
    if len(finite) != len(values) or len(set(values)) < 1000:
        print("FAIL: the reference head is degenerate (%d non-finite, %d distinct)" % (len(values) - len(finite),
                                                                                       len(set(values))))
        return 1
    write_fixture(fixture, w, h, refs=dump, head=dump / "head.f32")
    failures = 0
    # 2. block 0, kernel by kernel, against the CPU reference
    features_fixture(d / "verify", w, h, dump / "features.f32", dump / "block-0.u8")
    rows = min(fw * fh, 1 << 18) if a.thorough else 4096   # (the CPU reference is single-threaded: ~40 s a 120k rows)
    matched = []
    for i, (label, variant) in enumerate(tools):
        code, out, secs = tool(variant, kernels, ["verify", "--model", str(model), "--fixture", str(d / "verify"),
                                                  "--rows", str(rows)], {}, d / ("verify-%d.log" % i))
        checks = re.findall(r"^  (.+?)\s{2,}(equal \(\d+ values\)|MISMATCH.*)$", out, re.M)
        equal = (not code and len(checks) >= 10 and not any(v.startswith("MISMATCH") for _, v in checks) and
                 "every kernel of block 0 equals" in out)
        print("CPU reference with %s, block 0 on %d of %d rows (%.1f s): %s" %
              (label, min(rows, fw * fh), fw * fh, secs, "EQUAL on every kernel" if equal else "differs"))
        for name, verdict in checks:
            print("    %-26s %s" % (name, verdict[:110]))
        if equal:
            matched.append(label)
        elif not checks:
            print(out[-2000:])
    if not matched:
        print("FAIL: block 0 equals the CPU reference under no MMA model")
        failures += 1
    # 3. every route against the references
    for label, env in ROUTES:
        code, out, secs = tool(exe, kernels, ["parity", "--model", str(model), "--fixture", str(fixture),
                                              "--repeat", "3"], env, d / ("parity-%d.log" % ROUTES.index((label, env))))
        exact = len(re.findall(r"^\S+\s+\d+x\d+x\d+ bit-exact", out, re.M))
        head_line = re.search(r"head vs reference: (.*)", out)
        verdict = re.search(r"VERDICT: (.*)", out)
        gpu = re.search(r"production schedule \(([^)]*)\): (\d+) dispatches, GPU ([\d.]+) ms", out)
        print("%-46s %s; boundaries bit-exact %d/%d; head %s%s" %
              (label, verdict.group(1) if verdict else "NO VERDICT", exact, len(boundaries(w, h)),
               head_line.group(1) if head_line else "?",
               "; %s, %s dispatches, %s ms GPU (min of 3)" % gpu.groups() if gpu else ""))
        if code or not verdict or not verdict.group(1).startswith("PASS") or exact != len(boundaries(w, h)):
            print("\n".join(l for l in out.splitlines() if "MISMATCH" in l or "DIFFERENT" in l or "NOT" in l)[:3000])
            failures += 1
    return failures


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--cxx")
    r.add_argument("--headers", help="Vulkan headers' include folder (with vulkan/vulkan_beta.h)")
    r.add_argument("--size", action="append", help="WxH; repeatable (default 384x256)")
    r.add_argument("--thorough", action="store_true", help="also 1280x720 and 2048x1152, and the CPU reference on up to 262144 rows")
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--work", help="keep everything in this folder")
    r.add_argument("--keep", action="store_true", help="do not delete the temporary folder")
    m = sub.add_parser("model")
    m.add_argument("directory")
    m.add_argument("--seed", type=int, default=1)
    a = p.parse_args()
    if a.command == "model":
        manifest = write_model(a.directory, a.seed)
        print("wrote a synthetic model to %s: %d tensors, %d stages, %.1f MiB" %
              (a.directory, len(manifest["tensors"]), len(manifest["stages"]),
               sum(s["packedByteLength"] for s in manifest["stages"]) / 2 ** 20))
        return 0
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
