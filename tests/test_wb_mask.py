"""Masked write-backs: only the words the GPU changed go back to guest memory (the targeting cursor's missing arrows)."""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEMORY = ROOT / "gpu" / "vk" / "vk_memory.cpp"


class WbMaskTests(unittest.TestCase):
    def test_ranges_and_ring(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("no C++ compiler")
        with tempfile.TemporaryDirectory(prefix="hookshot-wb-mask-") as tmp:
            binary = Path(tmp) / "wb_mask_test"
            compile = subprocess.run(
                [compiler, "-std=c++20", "-Wall", "-Wextra", "-Werror",
                 "-I", str(ROOT / "gpu" / "vk"),
                 str(ROOT / "tests" / "wb_mask_test.cpp"), "-o", str(binary)],
                cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(compile.returncode, 0, compile.stderr)
            run = subprocess.run([str(binary)], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_write_back_is_masked(self):
        src = MEMORY.read_text(encoding="utf-8")
        # a written binding notes its range for a pre-image beside the write-back
        self.assertRegex(src, r"writebacks\.push_back\(\{p, off, [^\n]*\}\);\s*pre_note\(p, off,")
        # the write-back splits its runs by the pre-images and copies the masked parts with the shader
        self.assertIn("pc->second.split(r.srcOffset, r.srcOffset + r.size, masked, plain);", src)
        self.assertIn("wb_masked_copy(cmd, *p, masked);", src)
        # the pre-images are taken by the upload, after the syncs: texture_uploads_recorded records them
        tex = (ROOT / "gpu" / "vk" / "vk_texture.cpp").read_text(encoding="utf-8")
        body = re.search(r"bool texture_uploads_recorded\(\) \{(.*?)\n\}", tex, re.S).group(1)
        self.assertLess(body.index("memory_rb_refresh"), body.index("memory_preimages_record"))
        # and both lists start empty with each submission
        begin = re.search(r"void memory_begin_submission\(\) \{(.*?)\n\}", src, re.S).group(1)
        self.assertIn("pre_cover.clear(), pre_pending.clear();", begin)


if __name__ == "__main__":
    raise SystemExit(unittest.main(verbosity=1))
