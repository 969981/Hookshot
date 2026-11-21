"""tests/run_all.py leaves no process of a check behind (Linux): a check that times out, a check that ends with a child
still running (also one whose own child has an empty environment), and a child that left the check's process group but
holds its output each end with the check, and the runner returns at its timeout. What reached the checks before still does: Ctrl-C and a kill of the runner's process
group end them, and a run under nohup is not stopped by SIGHUP.

A copy of run_all.py runs in a scratch folder with one check that starts a spinner (a child of the check, as
tests/test_recorder_sync.py starts a broken recorder that hangs on purpose) and then sleeps or exits. The spinner writes
its PID, then a line every 20 ms: once the runner returns, the file must stop growing. A spinner or check that survives
is killed here, by its PID and only while its command line still names this check's scratch folder; a spinner ends by
itself after 60 s either way.

python -I -B tests/test_run_all_reap.py
"""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPINNER = ("import os, sys, time\n"
           "f = open(sys.argv[1], 'a', buffering=1)\n"
           "f.write('%d\\n' % os.getpid())\n"
           "t0 = time.time()\n"
           "while time.time() - t0 < 60:\n"
           "    f.write('.\\n')\n"
           "    t = time.time()\n"
           "    while time.time() - t < 0.02:\n"
           "        pass\n")
# (a process between the check and the spinner, which starts the spinner with an empty environment and waits)
MIDDLE = ("import os, subprocess, sys, time\n"
          "open(sys.argv[1] + '.middle', 'w').write(str(os.getpid()))\n"
          "subprocess.Popen([sys.executable, '-I', '-c', sys.argv[2], sys.argv[1]], env={}, stdin=subprocess.DEVNULL,\n"
          "                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
          "time.sleep(60)\n")
# (argv: seconds to sleep, then how the spinner is started: "child", with none of the check's pipes, so the check's
# output ends when the check does; "leaver", in a session of its own and holding the check's output; "envless", by a
# process of the check's that starts it with an empty environment)
CHECK = ("import os, subprocess, sys, time\n"
         "here = os.path.dirname(os.path.abspath(__file__))\n"
         "open(os.path.join(here, 'check.pid'), 'w').write(str(os.getpid()))\n"
         "how, spinner, out = sys.argv[2], os.path.join(here, 'spinner.txt'), subprocess.DEVNULL\n"
         "leaver = how == 'leaver'\n"
         "if how == 'envless':\n"
         "    subprocess.Popen([sys.executable, '-I', '-c', %r, spinner, %r], stdin=out, stdout=out, stderr=out)\n"
         "else:\n"
         "    subprocess.Popen([sys.executable, '-I', '-c', %r, spinner], stdin=out, stdout=None if leaver else out,\n"
         "                     stderr=None if leaver else out, start_new_session=leaver)\n"
         "while not os.path.exists(spinner):\n"
         "    time.sleep(0.01)\n"
         "print('spinner started', flush=True)\n"
         "time.sleep(float(sys.argv[1]))\n") % (MIDDLE, SPINNER, SPINNER)


def ours(pid, tmp):
    """pid is alive (not a zombie) and still one of this test's processes (its command line names the scratch folder)"""
    try:
        state = Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()[0]
        return state != "Z" and str(tmp).encode() in Path("/proc/%d/cmdline" % pid).read_bytes()
    except (OSError, IndexError):
        return False


@unittest.skipUnless(sys.platform.startswith("linux") and os.path.isdir("/proc"),
                     "the runner finds a check's processes through /proc (Linux); elsewhere it runs checks as before")
class Reaped(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="run-all-reap-"))
        (self.tmp / "tests").mkdir()
        (self.tmp / "nx2").mkdir()
        shutil.copy(ROOT / "tests" / "run_all.py", self.tmp / "tests" / "run_all.py")
        self.spinner = self.tmp / "tests" / "spinner.txt"
        self.check_pid = self.tmp / "tests" / "check.pid"

    def tearDown(self):
        middle = self.tmp / "tests" / "spinner.txt.middle"
        for f in (self.spinner, middle, self.check_pid):   # (whatever survived the runner)
            try:
                pid = int(f.read_text().split()[0])
            except (OSError, ValueError, IndexError):
                continue
            if ours(pid, self.tmp):
                os.kill(pid, signal.SIGKILL)
        time.sleep(0.2)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def runner(self, sleep, how="child", *args):
        (self.tmp / "tests" / "test_spins.py").write_text(CHECK + "\nRUN_ALL_ARGS = [%r, %r]\n" % (str(sleep), how),
                                                         encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "GITHUB_"))}
        return [sys.executable, "-I", "-B", str(self.tmp / "tests" / "run_all.py"), "--jobs", "1", "--exact", *args,
                "tests/test_spins.py"], env

    def wait_for_spinner(self, p):
        t = time.time()
        while not self.spinner.exists() and time.time() - t < 30 and p.poll() is None:
            time.sleep(0.05)
        self.assertTrue(self.spinner.exists(), "the check never started its spinner")
        time.sleep(0.2)   # (the check past its print, into its sleep)

    def assert_gone(self, check_too=False):
        self.assertTrue(self.spinner.exists(), "the check never started its spinner")
        time.sleep(0.3)   # (a write in flight as it was ended)
        before = self.spinner.stat().st_size
        time.sleep(1.0)
        grew = self.spinner.stat().st_size - before
        self.assertEqual(grew, 0, "the check's spinner still runs after the runner returned (%d bytes in 1 s)" % grew)
        if check_too:
            self.assertFalse(ours(int(self.check_pid.read_text()), self.tmp), "the check still runs after the runner")

    def stop(self, p):
        if p.poll() is None:
            p.kill()
            p.wait()

    def test_timed_out_check(self):
        cmd, env = self.runner(30, "child", "--timeout", "3")
        t = time.time()
        r = subprocess.run(cmd, cwd=self.tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        out = r.stdout.decode("utf-8", "replace")
        self.assertIn("TIMEOUT", out)
        self.assertLess(time.time() - t, 20, out)
        self.assert_gone(check_too=True)

    def test_check_that_ends_with_a_child_running(self):
        cmd, env = self.runner(0)
        r = subprocess.run(cmd, cwd=self.tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        self.assertIn("pass", r.stdout.decode("utf-8", "replace"))
        self.assert_gone()

    def test_grandchild_with_an_empty_environment(self):
        """a check that ends with a child running, whose own child it started with an empty environment"""
        cmd, env = self.runner(0, "envless")
        r = subprocess.run(cmd, cwd=self.tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        self.assertIn("pass", r.stdout.decode("utf-8", "replace"))
        self.assert_gone()

    def test_child_that_left_the_group_holding_the_output(self):
        """a child in a session of its own, holding the check's output: ended, and the runner still returns at its
        timeout"""
        cmd, env = self.runner(30, "leaver", "--timeout", "2")
        t = time.time()
        r = subprocess.run(cmd, cwd=self.tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
        secs, out = time.time() - t, r.stdout.decode("utf-8", "replace")
        self.assertIn("TIMEOUT", out)
        self.assertLess(secs, 15, "the runner waited %.1f s for a child holding the check's output\n%s" % (secs, out))
        self.assert_gone(check_too=True)

    def signalled(self, sig, sleep, pre):
        """the runner in a session of its own (so its process group can be signalled), started with pre's signal
        dispositions; sig sent once the check's spinner runs, by pre's rule (to the runner, or to its group)"""
        cmd, env = self.runner(sleep)
        return subprocess.Popen(cmd, cwd=self.tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True, preexec_fn=pre)

    def test_ctrl_c(self):
        """Ctrl-C: SIGINT to the runner's process group, with SIGINT not ignored (an agent's shell may start it so)"""
        p = self.signalled(signal.SIGINT, 30, lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
        try:
            self.wait_for_spinner(p)
            os.killpg(p.pid, signal.SIGINT)
            p.wait(timeout=15)
        finally:
            self.stop(p)
        self.assert_gone(check_too=True)

    def test_group_kill_of_the_runner(self):
        """the runner's process group killed (timeout -s KILL, an agent's command timeout): its checks end with it"""
        p = self.signalled(signal.SIGKILL, 30, None)
        try:
            self.wait_for_spinner(p)
            os.killpg(p.pid, signal.SIGKILL)
            p.wait(timeout=15)
        finally:
            self.stop(p)
        self.assert_gone(check_too=True)

    def test_nohup(self):
        """SIGHUP ignored (nohup): a SIGHUP to the runner stops nothing, and the run finishes"""
        p = self.signalled(signal.SIGHUP, 2, lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN))
        try:
            self.wait_for_spinner(p)
            p.send_signal(signal.SIGHUP)
            out = p.communicate(timeout=60)[0].decode("utf-8", "replace")
        finally:
            self.stop(p)
        self.assertEqual(p.returncode, 0, out)
        self.assertRegex(out, r"\n1 passed, 0 failed")
        self.assert_gone()


if __name__ == "__main__":
    unittest.main()
