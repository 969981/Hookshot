"""(issues 768, 807) The lift's emit starts only as many processes as the memory free holds: 16 on a 16 GB Steam Deck class PC
ran it out of memory (lift.py stopped by signal 9 or 15, its processes failing with BrokenPipeError). Each process is counted at
half the lift's own memory, with HOOKSHOT_MEM_RESERVE_GB kept free; HOOKSHOT_LIFT_JOBS still chooses.

python -I -B tests/test_emit_jobs_memory.py
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
import kit  # noqa: E402,F401  (a64-kit on the path)
import chunks  # noqa: E402

FORK = "fork" in chunks.multiprocessing.get_all_start_methods()


@unittest.skipUnless(FORK, "no fork here: the emit is one process")
class EmitJobsTests(unittest.TestCase):
    def jobs(self, free, lift, cpus=16, env=None):
        """emit_jobs() on a PC with `cpus` threads, `free` MB available and a lift of `lift` MB (None: not known)"""
        values = {"MemAvailable": free, "RssAnon": lift}
        env = dict(env or {})
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(chunks, "proc_mb", lambda path, field: values[field], create=True), \
                mock.patch.object(chunks.os, "sched_getaffinity", lambda pid: set(range(cpus)), create=True):
            for k in ("HOOKSHOT_LIFT_JOBS", "HOOKSHOT_MEM_RESERVE_GB"):
                if k not in env:
                    os.environ.pop(k, None)
            return chunks.emit_jobs()

    def test_a_small_pc_with_a_big_lift_emits_in_one_process(self):
        # (a 16 GB Steam Deck class PC: ~6 GB free once the lift holds ~5 GB; 16 forks each copying a share of it)
        self.assertEqual(self.jobs(free=6000, lift=5000), 1)

    def test_the_count_follows_the_memory_free(self):
        # (12000 - 1536) // 1500 = 6
        self.assertEqual(self.jobs(free=12000, lift=3000), 6)

    def test_less_free_than_the_reserve_is_still_one(self):
        self.assertEqual(self.jobs(free=500, lift=4000), 1)

    def test_a_big_pc_keeps_one_per_thread_up_to_16(self):
        self.assertEqual(self.jobs(free=60000, lift=3000), 16)
        self.assertEqual(self.jobs(free=60000, lift=3000, cpus=8), 8)

    def test_memory_not_known_keeps_the_thread_count(self):
        self.assertEqual(self.jobs(free=None, lift=3000), 16)
        self.assertEqual(self.jobs(free=6000, lift=None), 16)

    def test_the_reserve_is_followed(self):
        # (12000 - 6144) // 1500 = 3
        self.assertEqual(self.jobs(free=12000, lift=3000, env={"HOOKSHOT_MEM_RESERVE_GB": "6"}), 3)

    def test_hookshot_lift_jobs_still_chooses(self):
        self.assertEqual(self.jobs(free=6000, lift=5000, env={"HOOKSHOT_LIFT_JOBS": "4"}), 4)

    @unittest.skipUnless(os.path.exists("/proc/meminfo"), "the memory is read from /proc on Linux")
    def test_the_memory_reads_here(self):
        free = chunks.proc_mb("/proc/meminfo", "MemAvailable")
        lift = chunks.proc_mb("/proc/self/status", "RssAnon")
        self.assertTrue(isinstance(free, int) and free > 0, free)
        self.assertTrue(isinstance(lift, int) and lift > 0, lift)
        self.assertIsNone(chunks.proc_mb("/proc/meminfo", "NoSuchField"))
        self.assertIsNone(chunks.proc_mb("/proc/no/such/file", "MemAvailable"))


if __name__ == "__main__":
    unittest.main()
