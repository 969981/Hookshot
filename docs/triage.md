# Issue triage runbook

This is the maintainer side of [Phase 0 in ROADMAP.md](../ROADMAP.md#phase-0---triage-and-release-hygiene-2-3-days).
The goal is that every open issue has one area, one disposition and a next action, and
that duplicate reports point at the canonical tracker instead of growing a second thread.

The roadmap appendix is the source of truth. The script in
[`scripts/triage_issues.py`](../scripts/triage_issues.py) reads it and applies the result
to GitHub. It is a dry run unless `--apply` is passed.

**Status.** The repository half of Phase 0 is here: label definitions, the dry-run
triage tool, the manual workflow, the issue forms, the known-issues page and the issue
guidelines. The version is already shown in the Insert menu and in the first line of
`hookshot_log.txt`. The upstream write steps (create labels, pin trackers, link and close
duplicates) need a token or a workflow run with `issues: write`; a read-only token can
run the dry run but cannot apply it. See "Why the token can be read-only" below.

## What Phase 0 changes

| Item | Where it lives |
|---|---|
| Label definitions | [`.github/labels.json`](../.github/labels.json) |
| Canonical trackers and dispositions | the appendix of [ROADMAP.md](../ROADMAP.md) |
| New issue forms | [`.github/ISSUE_TEMPLATE/`](../.github/ISSUE_TEMPLATE/) |
| Known issues page | [`docs/known-issues.md`](known-issues.md) |
| Triage tool | [`scripts/triage_issues.py`](../scripts/triage_issues.py) |
| Roadmap integrity check | [`tests/test_triage_plan.py`](../tests/test_triage_plan.py) |

## Labels

The script creates any missing label from `.github/labels.json` and never deletes or
renames an existing one. The schemes are:

- `area/build`, `area/runtime`, `area/rendering`, `area/audio`, `area/input`,
  `area/tools`
- `vendor/amd`, `vendor/intel`, `vendor/nvidia`
- `type/crash`, `type/regression`, `type/duplicate`, `type/feature`
- `status/fix`, `status/duplicate`, `status/needs-info`, `status/retest`,
  `status/feature`, `status/process`, `status/reference`, `status/canonical`
- `needs-log`, `needs-retest`, `needs-triage`
- `priority/p0`, `priority/p1`, `priority/p2`
- `good-first-fix`

The existing `bug`, `enhancement`, `duplicate`, `good first issue`, `help wanted`,
`invalid`, `question`, `wontfix`, `documentation` and `accessibility` labels stay as they
are.

`status/canonical` marks a root-cause tracker. New reports are routed to it and the
duplicate form is closed only after the canonical fix is verified.

## Running the script

The same tool is available from **Actions > Apply issue triage**. Its default is a dry
run; `apply` writes labels, and the other switches add comments, close linked duplicates
and pin the canonical trackers.

A dry run needs no permission and is safe at any time:

```sh
python -I scripts/triage_issues.py --dry-run
```

It prints:

- every label it would create;
- every open issue with the labels it would add;
- issues in the tracker that are not in the roadmap appendix (`#519` is the first
  example), which get `needs-triage`;
- roadmap issues that are no longer open.

To write, use a token with `issues: write` (and `metadata: read`). Pinning also needs
GraphQL `issues: write`. Do not paste a token into a command line that shells out to
another process; export it for the command only:

```sh
export GITHUB_TOKEN=...                       # PowerShell: $env:GITHUB_TOKEN = "..."
python -I scripts/triage_issues.py --apply
```

Add the optional actions separately so a mistake is easy to see:

```sh
python -I scripts/triage_issues.py --apply --comments
python -I scripts/triage_issues.py --apply --comments --close-duplicates
python -I scripts/triage_issues.py --apply --pin
```

`--comments` posts the standard duplicate, `needs-info` and retest text. `--close-duplicates`
is a separate switch because the working rule is: link first, close only when the
canonical fix is verified. `--pin` pins every issue that is a canonical target.

For a fork, test the whole path there first:

```sh
python -I scripts/triage_issues.py --repo your-name/Hookshot --apply --comments
```

## Pinned trackers

These are the trackers Phase 0 wants pinned. The script pins every issue that appears as
a canonical target in the appendix; this list is the readable version:

- Build: #503
- Installer: #493
- Null pool: #287
- Texture builder: #500
- Device lost: #150
- Black screen at the Deku Tree: #309
- Second-start / cache: #195
- Flashes and exposure: #145
- Ground lines and Skull Kid: #319
- Hyrule Field shadows: #468
- Overexposure: #36
- AMD foliage: #302
- RADV noise: #361
- Floating trees: #352
- Missing foliage and textures: #11
- VRAM: #105
- Intel: #17
- Cold cache: #388
- RADV submit: #413, #312
- No mesh-shader fallback: #148, #454

## Duplicate workflow

1. The new report gets `status/duplicate` and `type/duplicate`; the canonical issue gets
   `status/canonical`.
2. The duplicate gets the standard comment with the canonical link.
3. It stays open until the canonical fix is in a release and the reporter has been told
   what to retest. If the canonical fix is already verified, close with reason
   `duplicate`.
4. If the report adds new evidence, copy that evidence to the canonical thread before
   closing.

Do not close a report just because the title matches. The script's duplicate map is
deliberate: for example, #302 is AMD foliage, while #361 is RADV colour noise; they are
different symptoms with different fixes.

## Needs-information workflow

1. Apply `status/needs-info` and `needs-log`.
2. Post the standard comment asking for the release name (menu or first log line), GPU
   and driver, steps, and `hookshot_log.txt`.
3. Leave it open for a reasonable window. If the information never arrives, close as
   `not planned` and say what was missing. Do not guess at a fix from a title.

## Retest workflow

1. Apply `status/retest` and `needs-retest`.
2. Post the release that should contain the fix and ask for a fresh log.
3. If it passes, close with `completed` and record the hardware in the canonical thread.
4. If it fails, remove `needs-retest`, add the failure back to the canonical tracker and
   attach the new log.

## New issues

An issue that is not in the roadmap appendix is not ignored: it gets `needs-triage`.
Review these weekly and either:

- add a row to the appendix (and update the count in ROADMAP.md and the test), or
- link it to an existing canonical issue and mark it duplicate, or
- ask for the missing information.

Reports arriving after the 133-issue snapshot (for example #519, #520 and #521) get
`needs-triage`. They belong under Performance or Rendering once the report is confirmed;
until then `needs-triage` is the correct state.

## Release cadence

Phase 0 also asks for a calmer release train:

- publish releases in a batch, not while a build is in progress;
- announce the next batch and what it contains;
- do not force an update on a running build; offer it at start-up and after the build;
- include the worker count, build time and known P0 issues in the release notes;
- close issues only with the release that contains the fix and a retest request.

The template and the known-issues page already tell players not to update during a build;
the remaining part is a project habit, not code.

## Why the token can be read-only

A token with only `pull` permission can still read issues and run the dry run, but cannot
create labels, comment, close or pin. That is expected: the Phase 0 artifacts in this
repository are ready, and a maintainer with `issues: write` runs the `--apply` commands.
The dry run is deliberately the default so a contributor can check the plan without
changing the upstream tracker.
