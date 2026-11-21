"""Synthetic Linux block graphs only; no build or real device inspection.

python -I -B tests/test_disk_rotational.py
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest

RUN_ALL_SKIP = {"windows": "Synthetic Linux sysfs graphs require symlinks; checked on Linux"}

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nx2"))
import disk  # noqa: E402


@unittest.skipIf(sys.platform == "win32", "Synthetic Linux sysfs graphs require symlinks; checked on Linux")
class RotationalGraphTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="hookshot-rotational-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def device(self, name, rotational=None):
        node = self.root / name
        node.mkdir(parents=True, exist_ok=True)
        if rotational is not None:
            (node / "queue").mkdir(exist_ok=True)
            (node / "queue" / "rotational").write_text(str(rotational) + "\n", encoding="ascii")
        return node

    def link(self, upper, lower, name=None):
        slaves = upper / "slaves"
        slaves.mkdir(exist_ok=True)
        os.symlink(lower, slaves / (name or lower.name), target_is_directory=True)

    def chain(self, rotational, layers=5):
        node = self.device("leaf", rotational)
        for i in range(layers):
            upper = self.device("dm-%d" % i, 0)
            self.link(upper, node)
            node = upper
        return node

    def test_a_deep_hdd_overrides_virtual_ssd_values(self):
        self.assertIs(disk._device_rotational(self.chain(1)), True)

    def test_a_deep_ssd_overrides_virtual_hdd_values(self):
        top = self.chain(0, layers=8)
        for i in range(8):
            (self.root / ("dm-%d" % i) / "queue" / "rotational").write_text("1\n", encoding="ascii")
        self.assertIs(disk._device_rotational(top), False)

    def test_any_deep_hdd_wins_over_ssd_and_unknown_slaves(self):
        top = self.chain(1, layers=8)
        self.link(top, self.device("ssd", 0))
        self.link(top, self.device("unknown"))
        self.assertIs(disk._device_rotational(top), True)

    def test_known_ssd_is_not_overridden_by_unknown_slaves(self):
        top = self.device("dm", 1)
        self.link(top, self.device("unknown"))
        self.link(top, self.device("ssd", 0))
        self.assertIs(disk._device_rotational(top), False)

    def test_all_unknown_slaves_fall_back_to_the_device(self):
        top = self.device("dm")
        self.link(top, self.device("unknown"))
        self.assertIsNone(disk._device_rotational(top))
        self.device("dm", 1)
        self.assertIs(disk._device_rotational(top), True)
        self.device("dm", 0)
        self.assertIs(disk._device_rotational(top), False)

    def test_a_self_cycle_without_known_data_is_unknown(self):
        top = self.device("dm")
        self.link(top, top)
        self.assertIsNone(disk._device_rotational(top))

    def test_a_cycle_still_finds_a_known_hdd_slave(self):
        top = self.device("dm-a", 0)
        other = self.device("dm-b", 0)
        self.link(top, other)
        self.link(other, top)
        self.link(other, self.device("hdd", 1))
        self.assertIs(disk._device_rotational(top), True)

    def test_an_ssd_cycle_terminates(self):
        top = self.device("dm-a", 0)
        other = self.device("dm-b", 0)
        self.link(top, other)
        self.link(other, top)
        self.assertIs(disk._device_rotational(top), False)

    def test_shared_slaves_are_visited_once_in_a_dag(self):
        from unittest import mock
        top = self.device("top", 0)
        left, right = self.device("left", 0), self.device("right", 0)
        leaf = self.device("hdd", 1)
        for node in (left, right):
            self.link(top, node)
            self.link(node, leaf)
        with mock.patch.object(disk.os, "listdir", wraps=os.listdir) as listing:
            self.assertIs(disk._device_rotational(top), True)
        self.assertEqual(listing.call_args_list.count(mock.call(str(leaf / "slaves"))), 1)

    def test_a_deep_slave_partition_reads_its_parent_queue(self):
        top = self.chain(1, layers=8)
        leaf = self.root / "leaf"
        partition = leaf / "part1"
        partition.mkdir()
        link = self.root / "dm-0" / "slaves" / "leaf"
        link.unlink()
        link.symlink_to(partition, target_is_directory=True)
        self.assertIs(disk._device_rotational(top), True)
        (leaf / "queue" / "rotational").write_text("0\n", encoding="ascii")
        self.assertIs(disk._device_rotational(top), False)

    def test_a_chain_beyond_python_recursion_depth_is_walked(self):
        self.assertIs(disk._device_rotational(self.chain(1, layers=1100)), True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
