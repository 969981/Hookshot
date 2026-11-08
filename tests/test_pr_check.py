"""scripts/pr_check.py against throwaway repositories: a bad PR is caught, a clean one passes.

    python -I -B tests/test_pr_check.py

No network, no real repository and no files outside the temporary folder are changed.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pr_check.py"
TRAILERS = "Assisted-by: Test Agent\nCo-Authored-By: Test Agent <test@example.com>"


def git(root, *args, check=True):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if check and result.returncode:
        raise SystemExit(f"git {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result


def commit(root, subject, body=""):
    args = ["commit", "-m", subject]
    if body:
        args += ["-m", body]
    git(root, *args)


def check(root):
    return subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--repo", str(root), "--base", "main",
                           "--no-tests"], capture_output=True, text=True, encoding="utf-8", errors="replace")


def init_repo(root):
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "PR Check Test")
    git(root, "config", "user.email", "pr-check@example.com")
    git(root, "config", "core.autocrlf", "false")   # (the CRLF case must reach the worktree as written)
    (root / "README.md").write_text("# test\n", encoding="utf-8", newline="\n")
    git(root, "add", "README.md")
    commit(root, "docs: initial commit", TRAILERS)


def bad_case(root):
    git(root, "checkout", "-b", "feature")
    (root / "game.nup").write_bytes(b"not a game\n")
    (root / "notes.txt").write_bytes(b"one\r\ntwo\r\n")
    git(root, "add", "game.nup", "notes.txt")
    commit(root, "Add a bad thing")
    bad = git(root, "rev-parse", "--short", "HEAD").stdout.strip()

    result = check(root)
    output = result.stdout + result.stderr
    if result.returncode == 0:
        raise SystemExit(f"the bad PR passed:\n{output}")
    for needle in (bad, "not a Conventional Commit", "feat: add a bad thing", "game.nup", "CRLF",
                   "missing Assisted-by, Co-Authored-By trailer"):
        if needle not in output:
            raise SystemExit(f"the bad PR check did not name {needle!r}:\n{output}")


def clean_case(root):
    git(root, "checkout", "main")
    git(root, "checkout", "-b", "clean")
    (root / "src").mkdir()
    (root / "src" / "thing.txt").write_text("one\ntwo\n", encoding="utf-8", newline="\n")
    git(root, "add", "src/thing.txt")
    commit(root, "test: add a clean fixture", TRAILERS)

    result = check(root)
    output = result.stdout + result.stderr
    if result.returncode != 0:
        raise SystemExit(f"the clean PR failed:\n{output}")

    work = root / "work"
    work.mkdir()
    (work / "save.bin").write_bytes(b"game data\n")
    result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--repo", str(root), "--base", "main",
                             "--include-untracked", "--no-tests"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    output = result.stdout + result.stderr
    if result.returncode == 0 or "work/save.bin" not in output:
        raise SystemExit(f"an untracked game file was not caught:\n{output}")


def main():
    with tempfile.TemporaryDirectory(prefix="pr-check-") as tmp:
        root = Path(tmp)
        init_repo(root)
        bad_case(root)
        clean_case(root)
    print("PASS: pr_check catches a bad PR and accepts a clean one")


if __name__ == "__main__":
    main()
