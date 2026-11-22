"""What the game's draw state names is checked against Vulkan and the device before a pipeline is made from it
(gpu/vk/vk_draw.cpp build_graphics; the divisor limit asked in gpu/vk/vk_main.cpp). Audit 2026-11-14, PR-C F6.

The state reaches the driver as the game set it, or as a pipeline state list (a file) says it. Four values went through
unchecked: a patch list with no tessellation stage (never translated), a sample count the device has no framebuffer for,
a vertex attribute offset or stride past the device's limits, and an instance divisor past VK_EXT_vertex_attribute_divisor's
maxVertexAttribDivisor. The first two leave the pipeline out (said once); an attribute past the limits reads zeros, as an
unset one does; the divisor is clamped. tests/test_fallback_pipeline_state.py builds build_graphics itself with these limits.

python -I -B tests/test_pipeline_state_limits.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def body(text, head):
    i = text.index(head)
    return text[i:text.index("\n}\n", i)]


def main():
    draw = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    b = body(draw, "VkPipeline build_graphics(const Program& p, const NvnState& s, const Targets& at, VkPrimitiveTopology topo, "
                   "int part, bool* cold) {")
    stages = b.index("for (const Stage& st : p.stages)")
    fails = []
    i = b.find("if (topo == VK_PRIMITIVE_TOPOLOGY_PATCH_LIST) {")
    if i < 0 or i > stages:
        fails.append("a patch list must be left out before any stage is set up (no tessellation is translated)")
    if "framebufferColorSampleCounts" not in b[:stages] or "framebufferDepthSampleCounts" not in b[:stages]:
        fails.append("the sample count must be checked against the device's framebuffer sample counts")
    if "maxVertexInputAttributeOffset" not in b or "maxVertexInputBindingStride" not in b:
        fails.append("vertex attribute offsets and strides must be checked against the device's limits")
    if "std::min(s.vstream[b].divisor, dev.max_vertex_divisor)" not in b:
        fails.append("an instance divisor must be clamped to the device's maxVertexAttribDivisor")
    main_cpp = (ROOT / "gpu/vk/vk_main.cpp").read_text(encoding="utf-8")
    if "dev.max_vertex_divisor = dp.maxVertexAttribDivisor;" not in main_cpp:
        fails.append("the device's maxVertexAttribDivisor is no longer asked")
    if fails:
        raise SystemExit("\n".join(fails))
    print("PASS: patches, sample counts, attribute offsets and strides, and divisors checked against the device")


if __name__ == "__main__":
    main()
