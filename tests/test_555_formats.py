"""The NVN 5-5-5 and BGRA8 formats map to the Vulkan format with their bit layout, and sampled views fix up the rest.

gpu/vk/vk_formats.cpp left BGR5, BGR5A1, A1BGR5, BGRA8 and BGRA8_SRGB UNDEFINED, and mapped RGB5 to B5G5R5A1, whose bits
run the other way. NVN's 16-bit formats put the first named component in the low bits (as RGB565 -> B5G6R5_PACK16).
So RGB5A1 and RGB5 (R low) live in A1R5G5B5 (B low), and the sampled view swaps R and B. RGB5 and BGR5 also read
alpha as one. This reads both switches from the source and checks each format's mapping and its view swizzle.

python -I -B tests/test_555_formats.py [--source <checkout>]
"""
import argparse
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

WANT = {
    "RGB5": "A1R5G5B5_UNORM_PACK16", "RGB5A1": "A1R5G5B5_UNORM_PACK16",
    "BGR5": "A1R5G5B5_UNORM_PACK16", "BGR5A1": "A1R5G5B5_UNORM_PACK16", "A1BGR5": "R5G5B5A1_UNORM_PACK16",
    "RGBA4": "A4B4G4R4_UNORM_PACK16", "BGRA8": "B8G8R8A8_UNORM", "BGRA8_SRGB": "B8G8R8A8_SRGB",
}
ALPHA_ONE = {"RGB5", "BGR5"}
SWAP_RB = {"RGB5", "RGB5A1"}
# (formats without mandatory sampled support: each must be asked of the device, else UNDEFINED)
OPTIONAL = {"A1BGR5": "R5G5B5A1_UNORM_PACK16"}
GATES = {}


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def mapping(src):
    """NVN format name -> the VkFormat vk_format returns for it (case labels that fall through share a return)"""
    body = block(src, "VkFormatInfo vk_format(int nvn_format) {")
    out, pending = {}, []
    for m in re.finditer(r"case NVN_FORMAT_(\w+):|return \{(?:(\w+)\(\) \? )?VK_FORMAT_(\w+)|default:", body):
        if m.group(1):
            pending.append(m.group(1))
        elif m.group(3):
            for name in pending:
                out.setdefault(name, m.group(3))
                if m.group(2):
                    GATES[name] = m.group(2)
            pending = []
        else:
            pending = []
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, default=ROOT)
    a = p.parse_args()
    fmt = (a.source / "gpu/vk/vk_formats.cpp").read_text(encoding="utf-8")
    tex = (a.source / "gpu/vk/vk_texture.cpp").read_text(encoding="utf-8")
    bad = []
    got = mapping(fmt)
    for name, want in WANT.items():
        if got.get(name) != want:
            bad.append("NVN_FORMAT_%s maps to %s, wanted %s" % (name, got.get(name, "UNDEFINED"), want))
    for name, vk in OPTIONAL.items():
        gate = GATES.get(name)
        fn = re.search(r"bool %s\(\) \{(.*?)\n\}" % gate, fmt, re.S) if gate else None
        at = fmt.index("case NVN_FORMAT_%s:" % name)
        ret = fmt[at:fmt.index(";", at)]
        if not fn or "VK_FORMAT_%s" % vk not in fn.group(1) or "VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT" not in fn.group(1) \
                or ": VK_FORMAT_UNDEFINED" not in ret:
            bad.append("NVN_FORMAT_%s maps to %s without asking the device for its sampled support "
                       "(no mandatory support: VUID-VkImageCreateInfo-imageCreateMaxMipLevels-02251)" % (name, vk))
    view = tex[tex.index("// (X formats read alpha as one)"):]
    switch = view[:view.index("default: break;")]
    for name in ALPHA_ONE:
        if "case NVN_FORMAT_%s:" % name not in switch:
            bad.append("the sampled view of NVN_FORMAT_%s does not read alpha as one" % name)
    swap = re.search(r"if \((.*?)\)\s*for \(unsigned& s : sw\)\s*s = s == NVN_TEXTURE_SWIZZLE_R \? "
                     r"NVN_TEXTURE_SWIZZLE_B : s == NVN_TEXTURE_SWIZZLE_B \? NVN_TEXTURE_SWIZZLE_R : s;", view)
    for name in SWAP_RB:
        if not swap or "nf == NVN_FORMAT_%s" % name not in swap.group(1):
            bad.append("the sampled view of NVN_FORMAT_%s does not swap R and B" % name)
    # (hookshot: only for texels uploaded from the game's memory; an image the GPU drew, its attachment views writing it
    # unswizzled, is sampled as written)
    if swap and "!tex->gpu_written" not in swap.group(1):
        bad.append("the R and B swap of RGB5A1 and RGB5 is made for images the GPU drew too (their attachments wrote them unswizzled)")
    if bad:
        raise SystemExit("\n".join(bad))
    print("5-5-5, 4-4-4-4 and BGRA8 formats map with their bit layout: ok")


if __name__ == "__main__":
    main()
