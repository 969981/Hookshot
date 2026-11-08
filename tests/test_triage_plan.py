"""ROADMAP.md and the Phase 0 triage files, game-free.

    python -I -B tests/test_triage_plan.py

The roadmap is the source of truth for the triage script: every issue must have one row,
one disposition and, for a duplicate, a canonical issue that is also in the table.
"""

import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "triage_issues.py")
LABELS = os.path.join(ROOT, ".github", "labels.json")
KNOWN = os.path.join(ROOT, "docs", "known-issues.md")
GUIDELINES = os.path.join(ROOT, "docs", "issue-guidelines.md")

ALLOWED = {"Fix", "Dup", "Info", "Retest", "Feature", "Process", "Reference"}


def load_triage():
    spec = importlib.util.spec_from_file_location("triage_issues", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    triage = load_triage()
    rows = triage.read_roadmap()

    assert len(rows) == 133, "the snapshot has 133 issues, found %d" % len(rows)
    assert sum(1 for r in rows.values() if r["disposition"] == "Dup") == 61, "duplicate count changed"

    for number, row in rows.items():
        assert row["disposition"] in ALLOWED, "#%d: %s" % (number, row["disposition"])
        assert row["area"] and row["priority"], "#%d has no area or priority" % number
        assert row["title"], "#%d has no title" % number
        assert row["action"], "#%d has no next action" % number
        for target in row["canonical"]:
            assert target in rows, "#%d points at #%d, which is not in the appendix" % (number, target)

    assert len({r["number"] for r in rows.values()}) == len(rows), "an issue is listed twice"

    with open(LABELS, encoding="utf-8") as f:
        labels = json.load(f)
    names = [x["name"] for x in labels]
    assert len(names) == len(set(names)), "duplicate label name"
    for label in labels:
        assert label["name"] and label["description"], label
        assert len(label["color"]) == 6 and all(c in "0123456789abcdef" for c in label["color"]), label

    with open(KNOWN, encoding="utf-8") as f:
        known = f.read()
    for number in (503, 493, 287, 500, 150, 309, 195, 145, 319, 468, 36, 302, 105, 17, 159):
        assert "#%d" % number in known, "known-issues.md does not mention #%d" % number

    with open(GUIDELINES, encoding="utf-8") as f:
        guidelines = f.read()
    assert "hookshot_log.txt" in guidelines and "Never attach game files" in guidelines
    assert "known-issues.md" in guidelines and "ROADMAP.md" in guidelines

    print("triage plan: ok (%d issues, %d duplicates, %d labels)" %
          (len(rows), sum(1 for r in rows.values() if r["disposition"] == "Dup"), len(labels)))


if __name__ == "__main__":
    main()
