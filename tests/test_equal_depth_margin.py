"""A colour pass that tests depth EQUAL gets a margin towards the camera on Intel.

The game draws a depth prepass, then colour passes that test EQUAL against it with another program. Intel's compiler
does not give the two programs the same position to the last bit, so most ground pixels failed the test and the sky
showed through (issue 17, Arc B390). The test becomes LESS_OR_EQUAL with a depth bias towards the camera, only where depth
is tested, not written, and the game asked for EQUAL. RX_VK_EQ_BIAS=<units> chooses it on any GPU, 0 turns it off. A draw
that blends keeps EQUAL: LESS_OR_EQUAL drew a haze sheet the game keeps to its own depth over Death Mountain Trail's mountain.
python -I -B tests/test_equal_depth_margin.py
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))   # (tests/srccut.py: python -I leaves this folder off the path)
from srccut import block   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "gpu" / "vk" / "vk_draw.cpp"


def main():
    source = SOURCE.read_text(encoding="utf-8")
    state = block(source, "void dynamic_state(VkCommandBuffer cmd")
    if 'getenv("RX_VK_EQ_BIAS")' not in state:
        raise SystemExit("no RX_VK_EQ_BIAS switch")
    if "dev.props.vendorID == 0x8086 ? 64.0f : 0.0f" not in state:
        raise SystemExit("the margin is not on by default on Intel only")
    equal = "const bool equal_pass = eq_bias > 0 && d.dt && !d.dw && d.dop == VK_COMPARE_OP_EQUAL;"
    if equal not in state:
        raise SystemExit("the margin is not limited to EQUAL tests that do not write depth")
    cond = "if (equal_pass && !blends)"
    if cond not in state:
        raise SystemExit("the margin is not limited to EQUAL tests that do not write depth and do not blend")
    # (the targets' blending is looked at only for such a draw: a loop over every target of every draw, on every GPU, for a
    # margin only Intel takes by default, before the test that leaves every other draw out of it)
    if "for (int i = 0; equal_pass && i < at.n && !blends; i++) {" not in state or not state.index(equal) < state.index("for (int i = 0; equal_pass"):
        raise SystemExit("the targets' blending is looked at before the EQUAL test, for every draw")
    before = state[:state.index(cond)]
    if "(s.color.blend_en >> i & 1) && at.color_format[i]" not in before:
        raise SystemExit("the margin does not look at the targets' blending")
    # (blending on with ONE, ZERO is no blending: such colour passes keep the margin, or the ground has holes)
    if "blend_factor(b.src_rgb) != VK_BLEND_FACTOR_ONE || blend_factor(b.dst_rgb) != VK_BLEND_FACTOR_ZERO" not in before:
        raise SystemExit("blending with ONE, ZERO counts as blending")
    margin = block(state, cond)
    for line in ("d.dop = VK_COMPARE_OP_LESS_OR_EQUAL;", "d.bias_en = VK_TRUE;", "d.bias[0] = -eq_bias"):
        if line not in margin:
            raise SystemExit("the margin lacks %r" % line)
    if not state.index("d.dop = compare_op(s.ds.df);") < state.index(equal):
        raise SystemExit("the margin must come after the game's compare op is read")
    print("EQUAL depth margin ok")


if __name__ == "__main__":
    main()
