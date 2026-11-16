"""Every XML file of the Windows Builder parses (the .xaml windows, the .csproj projects, the manifests).

The Builder is compiled only by the release and by hand, so a XAML file that is not valid XML (a comment holding "--",
an unclosed tag) reached main once and failed the nightly's Windows builder (MC3000). This check needs no .NET: it reads
each file with Python's XML parser, as the XAML compiler does first.

python -I -B tests/test_builder_xml_valid.py
"""
import glob
import os
import unittest
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class BuilderXml(unittest.TestCase):
    def test_every_file_parses(self):
        files = sorted(f for pattern in ("*.xaml", "*.csproj", "*.manifest")
                       for f in glob.glob(os.path.join(ROOT, "builder", "**", pattern), recursive=True))
        self.assertTrue(any(f.endswith("MainWindow.xaml") for f in files), "the Builder's MainWindow.xaml is checked")
        for f in files:
            with self.subTest(os.path.relpath(f, ROOT)):
                try:
                    ET.parse(f)
                except ET.ParseError as e:
                    self.fail("%s is not valid XML: %s" % (os.path.relpath(f, ROOT), e))


if __name__ == "__main__":
    unittest.main()
