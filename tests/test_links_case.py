"""Links whose path differs in letter case from the file on disk.

    python -I -B tests/test_links_case.py

`tests/test_docs.py` checks that a relative link resolves; on Windows that is case-insensitive, so `DESIGN.md` passes
here and fails the Linux CI where the file is `design.md` (review of #1025). This check compares every component of the
link's path with the directory entries' spelling, so the difference is caught on any OS.

Stdlib only and no `mcp` import: it runs even where the package is missing (which skips tests/test_docs.py).
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def tracked_markdown():
    result = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", "*.md"],
                            capture_output=True, check=True)
    files = []
    for item in result.stdout.decode("utf-8", "replace").split("\0"):
        if item and not item.replace("\\", "/").startswith("third_party/"):   # (vendored upstream docs)
            files.append(ROOT / item)
    return files


def without_fences(text):
    """The lines outside ``` fences, with their numbers, as tests/test_docs.py does."""
    kept = []
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            kept.append((number, line))
    return kept


def mismatched_component(target: str, folder: Path) -> tuple[str, str] | None:
    """The first path component spelled differently on disk, as (in the link, on disk), or None."""
    joined = os.path.normpath(os.path.join(str(folder), target.replace("\\", "/")))
    try:
        relative = os.path.relpath(joined, str(ROOT))
    except ValueError:   # (another drive: not a repository link)
        return None
    if relative.startswith(".."):
        return None
    current = ROOT
    for part in relative.split(os.sep):
        if part in ("", "."):
            continue
        try:
            entries = os.listdir(current)
        except OSError:
            return None
        if part in entries:
            current = current / part
            continue
        exact = {entry.lower(): entry for entry in entries}
        if part.lower() in exact:
            return part, exact[part.lower()]
        return None   # (missing altogether: tests/test_docs.py reports those)
    return None


def case_problems(files):
    problems = []
    for file in files:
        text = file.read_text(encoding="utf-8", errors="replace")
        for number, line in without_fences(text):
            for raw in LINK.findall(line):
                target = raw.strip().split(" ", 1)[0].strip("<>")
                if not target or target.startswith(("#", "http://", "https://", "mailto:", "data:")):
                    continue
                target = target.split("#", 1)[0]
                if not target:
                    continue
                mismatch = mismatched_component(target, file.parent)
                if mismatch:
                    link, disk = mismatch
                    problems.append(f"{file.relative_to(ROOT)}:{number}: link case mismatch: {raw} "
                                    f"({link!r} is {disk!r} on disk)")
    return problems


def main():
    files = tracked_markdown()
    problems = case_problems(files)
    if problems:
        print("link case check failed:\n")
        for problem in problems:
            print(f"  - {problem}")
        raise SystemExit(1)
    print(f"PASS: {len(files)} Markdown files: every relative link matches the path case on disk")


if __name__ == "__main__":
    main()
