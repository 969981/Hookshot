"""Read-only pre-PR checks: no game-derived files, LF endings, Conventional Commits and the test suite.

    python -I -B scripts/pr_check.py [--base main] [--repo PATH] [--include-untracked] [--no-tests]

The check never stages, commits, pushes, calls GitHub or deletes anything. It prints every problem and exits 1 when
one is found, so a pull request can be audited before it leaves the PC.

What it checks, against <base>...HEAD:

- no changed path is a game-derived file or build output: work/, gamefile/, user/, a dump, save, executable, DLL,
  library, key, log or screenshot outside the two tracked images;
- every changed text file uses LF, as .gitattributes asks (except *.bat);
- every non-merge commit has a Conventional Commit subject and both trailers from docs/design.md section 8;
- unless --no-tests, `python -I -B tests/run_all.py --strict` passes.

The commit subject parser is scripts/changelog.py's own HEADER, so the release tool and this check agree on what a
release will see.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from changelog import HEADER  # noqa: E402  (the release tool's parser, not a copy)

# (paths the project never puts in a PR; CONTRIBUTING.md has the rule)
GAME_DIRS = {"work", "gamefile", "user"}
GAME_NAMES = {"hookshot_options.ini", "hookshot_env.txt", "language.txt"}
GAME_SUFFIXES = {
    ".nup", ".nso", ".nca", ".nsp", ".xci", ".nsz", ".xcz", ".bin", ".keys", ".exe", ".dll", ".pdb", ".lib",
    ".exp", ".obj", ".o", ".log", ".ilk", ".sha1",
}
ALLOWED_PNG = re.compile(r"^(?:assets/.*\.png|builder/.*/Images/.*\.png)$", re.IGNORECASE)
TRAILERS = (re.compile(r"(?mi)^Assisted-by:\s*\S+"), re.compile(r"(?mi)^Co-Authored-By:\s*\S+"))
SKIP_RELEASE = re.compile(r"\[(?:skip release|release skip)\]", re.IGNORECASE)


def git(root: Path, *args: str, binary: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=not binary, encoding=None if binary else "utf-8", errors=None if binary else "replace",
                          timeout=120)


def run_text(root: Path, *args: str) -> str:
    result = git(root, *args)
    if result.returncode:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def changed_paths(root: Path, base: str, include_untracked: bool) -> list[str]:
    result = git(root, "diff", "--name-only", "-z", "--diff-filter=ACMRTUXB", f"{base}...HEAD", binary=True)
    if result.returncode:
        raise SystemExit(f"git diff {base}...HEAD failed: {result.stderr.decode('utf-8', 'replace').strip()}")
    paths = [os.fsdecode(item) for item in result.stdout.split(b"\0") if item]
    if include_untracked:
        result = git(root, "ls-files", "--others", "--exclude-standard", "-z", binary=True)
        if result.returncode:
            raise SystemExit(f"git ls-files failed: {result.stderr.decode('utf-8', 'replace').strip()}")
        paths += [os.fsdecode(item) for item in result.stdout.split(b"\0") if item]
    return sorted(set(path.replace("\\", "/") for path in paths))


def forbidden(path: str) -> str | None:
    """Why a changed path must not be in a PR, or None."""
    normalized = path.replace("\\", "/")
    parts = normalized.split("/")
    if parts[0] in GAME_DIRS:
        return "a game-derived folder"
    if parts[-1] in GAME_NAMES:
        return "a game-generated file"
    suffix = Path(normalized).suffix.lower()
    if suffix == ".png":
        return None if ALLOWED_PNG.match(normalized) else "a screenshot (only assets/ and builder Images/ PNGs are tracked)"
    if suffix in GAME_SUFFIXES:
        return "build output or game data"
    return None


def commit_issues(root: Path, base: str) -> list[str]:
    result = git(root, "log", "--no-merges", "--format=%H%x1f%s%x1f%b%x1e", f"{base}..HEAD")
    if result.returncode:
        return [f"git log {base}..HEAD failed: {result.stderr.strip()}"]
    issues: list[str] = []
    for record in result.stdout.split("\x1e"):
        fields = record.strip("\n").split("\x1f", 2)
        if len(fields) != 3:
            continue
        commit, subject, body = fields[0].strip(), fields[1].strip(), fields[2]
        if not subject or SKIP_RELEASE.search(subject + "\n" + body):
            continue
        short = commit[:7]
        if not HEADER.match(subject):
            issues.append(f"{short}: not a Conventional Commit: {subject!r}; try {suggest(subject)!r}")
        missing = [name for name, pattern in zip(("Assisted-by", "Co-Authored-By"), TRAILERS) if not pattern.search(body)]
        if missing:
            issues.append(f"{short}: missing {', '.join(missing)} trailer(s) in the commit body")
    return issues


def suggest(subject: str) -> str:
    subject = subject.strip()
    lowered = subject[:1].lower() + subject[1:] if subject else subject
    if subject.lower().startswith("add "):
        return "feat: " + lowered
    if subject.lower().startswith("fix "):
        return "fix: " + subject[4:]
    if subject.lower().startswith(("update ", "document ", "documenting ")):
        return "docs: " + lowered.split(" ", 1)[1]
    return "feat: " + lowered


def crlf_issues(root: Path, paths: list[str]) -> list[str]:
    issues: list[str] = []
    for path in paths:
        if path.lower().endswith(".bat"):
            continue
        file = root / path
        if not file.is_file():
            continue
        try:
            data = file.read_bytes()
        except OSError as exc:
            issues.append(f"{path}: cannot read ({exc})")
            continue
        if b"\0" in data:
            continue
        if b"\r\n" in data:
            issues.append(f"{path}: CRLF line endings (use LF; only *.bat may be CRLF)")
    return issues


def test_issue(root: Path) -> list[str]:
    command = [sys.executable, "-I", "-B", "tests/run_all.py", "--strict"]
    print("running: " + " ".join(command))
    try:
        result = subprocess.run(command, cwd=str(root), timeout=900)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"tests/run_all.py --strict could not finish: {exc}"]
    return [] if result.returncode == 0 else ["tests/run_all.py --strict failed"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="main", help="the branch or tag to compare against (default: main)")
    parser.add_argument("--repo", default=str(ROOT), help="repository to check (default: this checkout)")
    parser.add_argument("--include-untracked", action="store_true", help="also check files not staged yet")
    parser.add_argument("--no-tests", action="store_true", help="skip tests/run_all.py --strict")
    args = parser.parse_args(argv)
    root = Path(args.repo).resolve()
    if not (root / ".git").exists():
        raise SystemExit(f"not a git working tree: {root}")
    if git(root, "rev-parse", "--verify", args.base).returncode:
        raise SystemExit(f"base does not exist: {args.base}")

    paths = changed_paths(root, args.base, args.include_untracked)
    issues: list[str] = []
    for path in paths:
        reason = forbidden(path)
        if reason:
            issues.append(f"{path}: {reason}")
    issues += crlf_issues(root, paths)
    issues += commit_issues(root, args.base)

    if issues:
        print("pre-PR check failed:\n")
        for issue in issues:
            print(f"  - {issue}")
        print(f"\n{len(issues)} problem(s) in {len(paths)} changed path(s). Nothing was changed.")
        print(f"Compare: {args.base}...HEAD in {root}")
        return 1

    print(f"paths: {len(paths)} changed, none forbidden; line endings and commits look right")
    if not args.no_tests:
        issues = test_issue(root)
        if issues:
            print("\npre-PR check failed:\n")
            for issue in issues:
                print(f"  - {issue}")
            print(f"\nNothing was changed. Compare: {args.base}...HEAD in {root}")
            return 1
    print("pre-PR check passed. Nothing was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
