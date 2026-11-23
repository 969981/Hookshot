"""A scaled or converting texture copy is a blit only where Vulkan allows one.

copy_t2t (gpu/vk/vk_xfer.cpp) turns a copy it cannot make with vkCmdCopyImage into vkCmdBlitImage. Vulkan allows that only
when the source format has BLIT_SRC and the destination BLIT_DST, both sides are of one class (float/normalized,
unsigned integer, signed integer), depth or stencil formats match, both images are single-sampled, and a linear filter
only where the source format supports it and is not an integer format. Outside that the result is undefined; some
drivers write garbage or lose the device. Such a copy is skipped with a warning instead.

python -I -B tests/test_blit_format_checks.py
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


class BlitFormatCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.xfer = (ROOT / "gpu" / "vk" / "vk_xfer.cpp").read_text(encoding="utf-8")
        t2t = block(cls.xfer, "void copy_t2t(")
        start = t2t.index("// scaled or converting")
        cls.blit = t2t[start:t2t.index("vkCmdBlitImage(", start)]

    def test_the_blit_is_checked_before_it_is_recorded(self):
        for check in (
            "tiling_features(s.tex->format) & VK_FORMAT_FEATURE_BLIT_SRC_BIT",
            "tiling_features(d.tex->format) & VK_FORMAT_FEATURE_BLIT_DST_BIT",
            "format_class(s.tex->format) != format_class(d.tex->format)",
            "!color && s.tex->format != d.tex->format",
            "s.tex->samples != VK_SAMPLE_COUNT_1_BIT",
            "d.tex->samples != VK_SAMPLE_COUNT_1_BIT",
        ):
            self.assertIn(check, self.blit, f"copy_t2t blits without checking {check}")
        refused = self.blit[self.blit.index('"texture blit the device cannot make: skipped"'):]
        self.assertLess(refused.index("return;"), refused.index("VkImageBlit bl"))

    def test_linear_filtering_only_where_the_source_supports_it(self):
        linear = self.blit[self.blit.index("const bool linear ="):]
        linear = linear[:linear.index(";")]
        self.assertIn("VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT", linear)
        self.assertIn("format_class(s.tex->format) == 0", linear)

    def test_format_classes_keep_integer_formats_apart(self):
        classes = block(self.xfer, "int format_class(VkFormat f)")
        uint = classes[:classes.index("return 1;")]
        sint = classes[classes.index("return 1;"):classes.index("return 2;")]
        for f in ("VK_FORMAT_R8G8B8A8_UINT", "VK_FORMAT_R16_UINT", "VK_FORMAT_R32G32B32A32_UINT", "VK_FORMAT_S8_UINT"):
            self.assertIn(f, uint)
        for f in ("VK_FORMAT_R8G8B8A8_SINT", "VK_FORMAT_R16_SINT", "VK_FORMAT_R32G32B32A32_SINT"):
            self.assertIn(f, sint)
        self.assertNotIn("UNORM", classes)
        self.assertNotIn("SFLOAT", classes)


if __name__ == "__main__":
    unittest.main()
