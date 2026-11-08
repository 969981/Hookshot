# Issue guidelines

Hookshot is tested on one Windows PC and one Linux PC. Reports from other graphics
cards, distributions and areas of the game are how the project finds the rest. This page
is what makes a report actionable.

## Before opening an issue

1. **Try the newest release.** Every release includes the fixes before it. The release
   name is beside "Hookshot" in the Insert menu and in the first line of
   `hookshot_log.txt`.
2. **Read [known issues](known-issues.md).** Black screens, black movies, exposure
   flicker, AMD/RADV performance, cache resets and build stops are covered there, with the
   canonical issue for each one.
3. **Search the open issues.** If the problem is already there, add your log and hardware
   to that thread. A second thread splits the evidence.
4. **Use the right form:** Game problem, Build problem, or Idea. The form asks for the
   fields that are usually missing.
5. **English, please.** One problem per issue. Do not mix a crash, a graphics bug and a
   feature request in one report.

## What to include

| Field | Why it matters |
|---|---|
| Release name | `latest` does not identify a build. Use the menu line or the first log line. |
| GPU, driver and OS | The same symptom has different causes on NVIDIA, AMD and Intel. |
| CPU, RAM and VRAM | Explains stutter, black textures and out-of-memory behaviour. |
| Where in the game | Area, cutscene, menu, and what you were doing. |
| Steps to reproduce | The shortest sequence, starting from a new game or a named save. |
| `hookshot_log.txt` | The only record of what the run did. Attach the run that shows the problem. |
| Screenshot or video | Useful for a visual bug; not a substitute for the log. |
| Settings | Insert-menu changes and lines in `hookshot_env.txt`. |
| Mods and injectors | ReShade, overlays, shader-cache tools and frame-generation injectors change behaviour. |

**Never attach game files, keys, `hookshot.exe`, `image.bin`, the `.bin` caches, romfs or
exefs files, or saves that contain game data.** A log, a screenshot and a video are fine.

## Build reports

Say which route you used: Hookshot Builder on Windows, the Linux AppImage, or the command
line. Include:

- the full stopped-step text, not a screenshot of it;
- the lines starting with `build:` and `the compiler:` (worker count, threads, free
  memory, compiler);
- the stage where it stopped (read, lift, compile, finish);
- free space on the output drive;
- for a command-line build, `RX_CPM` and `EMSDK`/`HOOKSHOT_MINGW` if you set them.

If the build is simply slow, put the worker line and the wall time in #503 instead of a
new issue. Do not update the builder while a build is running.

## Feature requests

Say what problem the feature solves, not only the feature. Check
[ROADMAP.md](../ROADMAP.md) for what is already planned and what is waiting behind the
crashes and performance work. A request may be declined for now without being a judgement
on the idea.

## What happens after a report

- It gets an `area/*`, `vendor/*` and `priority/*` label, and one status label:
  `status/fix`, `status/duplicate`, `status/needs-info`, `status/retest`,
  `status/feature`, `status/process` or `status/reference`.
- If a log or a step is missing, it gets `needs-log` and a request for the missing
  information. It may be closed as `not planned` if the information never arrives.
- If it is the same root cause as another report, it gets `status/duplicate` and a link to
  the canonical issue. It stays open until the canonical fix is verified.
- If a fix is believed to be in a release, it gets `needs-retest` and a request for a
  fresh log.
- A closed issue links to the commit or PR and to the release to try.

The full process is in [docs/triage.md](triage.md). The labels and the canonical trackers
are in [ROADMAP.md](../ROADMAP.md).

## Security and privacy

Do not post keys, title keys, decrypted game files or personal paths that contain a
username. Do not ask where to get the game. The repository does not contain the game and
never will; the build reads your own copy.
