"""Apply the ROADMAP.md appendix to the GitHub issue tracker.

The roadmap is the source of truth for Phase 0: the appendix in ROADMAP.md lists every
issue and its disposition. This script turns that table into labels, optional linking
comments, optional duplicate closures, and optional pinned canonical trackers.

It is deliberately dry-run by default. A write needs a token with issues: write (and
metadata: read); pinning also needs GraphQL issues: write.

    python -I scripts/triage_issues.py --dry-run
    python -I scripts/triage_issues.py --apply --comments
    python -I scripts/triage_issues.py --apply --comments --close-duplicates --pin

The default repository is NotRexed/Hookshot. Use --repo for a fork while testing.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROADMAP = os.path.join(ROOT, "ROADMAP.md")
LABELS = os.path.join(ROOT, ".github", "labels.json")
API = "https://api.github.com"
USER_AGENT = "hookshot-triage"

SECTIONS = (
    ("Build", "area/build", "priority/p0"),
    ("Stability", "area/runtime", "priority/p0"),
    ("Performance", "area/runtime", "priority/p1"),
    ("Rendering", "area/rendering", "priority/p1"),
    ("Feature", "area/feature", "priority/p2"),
    ("Verification", "area/runtime", "priority/p1"),
)

STATUS = {
    "Fix": ("status/fix",),
    "Dup": ("status/duplicate", "type/duplicate"),
    "Info": ("status/needs-info", "needs-log"),
    "Retest": ("status/retest", "needs-retest"),
    "Feature": ("status/feature", "type/feature"),
    "Process": ("status/process",),
    "Reference": ("status/reference",),
}

ROW = re.compile(r"^\|\s*(\d{1,3})\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$")


def read_roadmap(path=ROADMAP):
    """{issue: {section, area, priority, disposition, canonical, action}} from the appendix."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    start = text.find("## Appendix")
    if start < 0:
        raise SystemExit("ROADMAP.md has no appendix")
    rows = {}
    section = area = priority = None
    for line in text[start:].splitlines():
        if line.startswith("### "):
            heading = line[4:]
            section = heading.split(" (", 1)[0]
            area = priority = None
            for prefix, a, p in SECTIONS:
                if heading.startswith(prefix):
                    area, priority = a, p
                    break
            continue
        if not area:
            continue
        m = ROW.match(line)
        if not m:
            continue
        number = int(m.group(1))
        title = m.group(2).replace("\\|", "|")
        disposition = m.group(3).strip()
        action = m.group(4).replace("\\|", "|").strip()
        kind = disposition.split(" ", 1)[0]
        canonical = []
        if kind == "Dup":
            canonical = [int(x) for x in re.findall(r"#(\d+)", disposition)]
        rows[number] = {
            "number": number,
            "title": title,
            "section": section,
            "area": area,
            "priority": priority,
            "disposition": kind,
            "canonical": canonical,
            "action": action,
        }
    if not rows:
        raise SystemExit("the ROADMAP appendix has no issue rows")
    dupes = len(rows) - len(set(rows))
    if dupes:
        raise SystemExit("the ROADMAP appendix lists an issue twice")
    return rows


def load_labels(path=LABELS):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def request(method, url, token=None, data=None, graphql=False):
    body = None
    if data is not None:
        body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        raise RuntimeError("%s %s: %d %s" % (method, url, e.code, raw[:300]))


def get_issues(repo, token=None):
    issues = []
    page = 1
    while True:
        _, batch = request("GET", "%s/repos/%s/issues?state=open&per_page=100&page=%d" % (API, repo, page), token)
        if not batch:
            break
        issues.extend(x for x in batch if "pull_request" not in x)
        if len(batch) < 100:
            break
        page += 1
    return issues


def existing_labels(repo, token=None):
    _, labels = request("GET", "%s/repos/%s/labels?per_page=100" % (API, repo), token)
    return {x["name"] for x in labels or []}


def create_labels(repo, labels, token, apply):
    have = existing_labels(repo, token)
    missing = [x for x in labels if x["name"] not in have]
    for label in missing:
        print("label: create %-20s %s" % (label["name"], label["description"]))
        if apply:
            request("POST", "%s/repos/%s/labels" % (API, repo), token, label)
            time.sleep(0.15)
    if not missing:
        print("labels: all %d present" % len(labels))
    return have | {x["name"] for x in missing}


def vendor_labels(text):
    low = text.lower()
    found = []
    if re.search(r"\bamd\b|radeon|\brx\s?[5-9]\d{2,3}\b|radv|adrenalin|gfx\d+", low):
        found.append("vendor/amd")
    if re.search(r"\bintel\b|\barc\b|\biris\b|core ultra|\bxe\b", low):
        found.append("vendor/intel")
    if re.search(r"nvidia|geforce|\brtx\b|\bgtx\b|quadro", low):
        found.append("vendor/nvidia")
    return found


def area_hints(text):
    low = text.lower()
    found = []
    if re.search(r"\baudio\b|sound|music|voice|speaker", low):
        found.append("area/audio")
    if re.search(r"controller|gamepad|joystick|keyboard|mouse|gyro|rumble|xinput|steam input", low):
        found.append("area/input")
    return found


def labels_for(issue, row):
    title = issue.get("title", "")
    text = title + "\n" + (issue.get("body") or "")
    labels = [row["area"], row["priority"]]
    labels += list(STATUS.get(row["disposition"], ()))
    labels += vendor_labels(text)
    labels += area_hints(title)
    return list(dict.fromkeys(x for x in labels if x))


def comment_for(row, repo):
    root = "https://github.com/" + repo + "/blob/main/"
    if row["disposition"] == "Dup":
        refs = ", ".join("#%d" % n for n in row["canonical"]) or "the canonical issue"
        return (
            "This looks like the same root cause as %s, which is the tracker for it in "
            "[ROADMAP.md](%sROADMAP.md). I am linking it here so the reports stay together; "
            "add your log and hardware details there. It stays open until the fix has been "
            "verified." % (refs, root)
        )
    if row["disposition"] == "Info":
        return (
            "To move this forward we still need the information that is missing from the "
            "report: the release name from the Insert menu or the first line of "
            "`hookshot_log.txt`, the GPU and driver version, the exact steps to reproduce, "
            "and the log from the run that shows the problem. Without those there is usually "
            "nothing to reproduce. See [known issues](%sdocs/known-issues.md) for the usual "
            "causes." % root
        )
    if row["disposition"] == "Retest":
        return (
            "This should be fixed in the newest release. Please retest on it and reply with "
            "the release name and a fresh `hookshot_log.txt`; if it still happens, say what "
            "changed. Otherwise this can be closed."
        )
    return None


def add_labels(repo, issue, labels, token, apply):
    if not labels:
        return
    print("  #%-3d %-22s %s" % (issue["number"], issue.get("title", "")[:22], ", ".join(labels)))
    if apply:
        request("POST", "%s/repos/%s/issues/%d/labels" % (API, repo, issue["number"]), token,
                {"labels": labels})
        time.sleep(0.15)


def comment(repo, issue, body, token, apply):
    print("  #%-3d comment: %s" % (issue["number"], body.split(".")[0][:100] + "."))
    if apply:
        request("POST", "%s/repos/%s/issues/%d/comments" % (API, repo, issue["number"]), token,
                {"body": body})
        time.sleep(0.15)


def close_duplicate(repo, issue, token, apply):
    print("  #%-3d close as duplicate" % issue["number"])
    if apply:
        for reason in ("duplicate", "not_planned"):
            try:
                request("PATCH", "%s/repos/%s/issues/%d" % (API, repo, issue["number"]), token,
                        {"state": "closed", "state_reason": reason})
                break
            except RuntimeError:
                if reason == "not_planned":
                    raise
        time.sleep(0.15)


def pin(repo, issue, token, apply):
    print("  #%-3d pin canonical tracker" % issue["number"])
    if not apply:
        return
    query = "mutation($id:ID!){pinIssue(input:{issueId:$id}){issue{number}}}"
    try:
        _, data = request("POST", "%s/graphql" % API, token,
                          {"query": query, "variables": {"id": issue["node_id"]}}, graphql=True)
        if data and data.get("errors"):
            raise RuntimeError(data["errors"][0].get("message", "GraphQL error"))
    except RuntimeError as e:
        print("  pin failed for #%d (needs issues: write): %s" % (issue["number"], e))
    time.sleep(0.15)


def main():
    ap = argparse.ArgumentParser(description="Apply ROADMAP.md dispositions to GitHub issues.")
    ap.add_argument("--repo", default="NotRexed/Hookshot")
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
    ap.add_argument("--apply", action="store_true", help="write to GitHub; default is a dry run")
    ap.add_argument("--dry-run", action="store_true", help="explicitly keep the default dry-run mode")
    ap.add_argument("--comments", action="store_true", help="post the duplicate/needs-info/retest comment")
    ap.add_argument("--close-duplicates", action="store_true", help="close duplicates after commenting")
    ap.add_argument("--pin", action="store_true", help="pin the canonical trackers")
    ap.add_argument("--skip-labels", action="store_true", help="do not create or apply labels")
    args = ap.parse_args()

    roadmap = read_roadmap()
    print("roadmap: %d issues, %d duplicate links" %
          (len(roadmap), sum(1 for r in roadmap.values() if r["disposition"] == "Dup")))
    if args.apply and not args.token:
        raise SystemExit("--apply needs GITHUB_TOKEN, GH_TOKEN or --token")

    labels = load_labels()
    if not args.skip_labels:
        create_labels(args.repo, labels, args.token, args.apply)

    issues = get_issues(args.repo, args.token)
    live = {x["number"]: x for x in issues}
    unknown = sorted(set(live) - set(roadmap))
    missing = sorted(set(roadmap) - set(live))
    print("tracker: %d open issues, %d in the roadmap, %d new, %d closed/missing" %
          (len(issues), len(set(live) & set(roadmap)), len(unknown), len(missing)))
    if unknown:
        print("new (needs triage): " + ", ".join("#%d" % n for n in unknown[:30]) +
              (" ..." if len(unknown) > 30 else ""))

    canonical = set()
    for row in roadmap.values():
        canonical.update(row["canonical"])
    for row in roadmap.values():
        if row["number"] in canonical:
            row["canonical_tracker"] = True

    applied = 0
    for number in sorted(set(live) & set(roadmap)):
        issue = live[number]
        row = roadmap[number]
        labels = labels_for(issue, row) + (["status/canonical"] if row.get("canonical_tracker") else [])
        print("#%-3d %s" % (number, issue.get("title", "")))
        add_labels(args.repo, issue, labels, args.token, args.apply)
        if args.comments and row["disposition"] in ("Dup", "Info", "Retest"):
            body = comment_for(row, args.repo)
            if body:
                comment(args.repo, issue, body, args.token, args.apply)
        if args.close_duplicates and row["disposition"] == "Dup":
            close_duplicate(args.repo, issue, args.token, args.apply)
        applied += 1

    for number in unknown:
        issue = live[number]
        print("#%-3d %s" % (number, issue.get("title", "")))
        add_labels(args.repo, issue, ["needs-triage"], args.token, args.apply)

    if args.pin:
        print("pinning canonical trackers")
        for number in sorted(canonical):
            if number in live:
                pin(args.repo, live[number], args.token, args.apply)

    print("done: %d issues %s" % (applied, "updated" if args.apply else "planned (dry run)"))
    if not args.apply:
        print("run with --apply to write; add --comments, --close-duplicates or --pin as needed")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print("error: %s" % e, file=sys.stderr)
        sys.exit(1)
