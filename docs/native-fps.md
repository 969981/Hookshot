# Native frame-rate experiment

This is an experimental standalone path. The default remains 60 Hz. `HOS_FPS_TARGET` accepts integer targets from
0 through 120; values through 60 keep the existing limiter-floor behavior. A target from 61 through 120 opts into
higher native pacing when Game speed is Real time. Per frame and `HOS_NO_PACING` keep the 60-Hz policy.

`HOS_REALTIME_WINDOW` keeps the upstream averaging option: five samples by default, with values clamped to
1 through 5; 1 uses the last interval alone. A changed sampling window starts a fresh timing epoch. Target-rate
scaling is applied after that same average.

Higher native rates automatically use the last successfully committed host timestep at the verified game clock
input. No second runtime flag is needed. A missing or invalid committed sample preserves the original loaded
value. Committed rates below 60 are valid under an active higher target, so load does not force the clock back
to a 60-Hz step. The original load, conversion, quotient and downstream calls remain in place.

Movie picture acquisition temporarily keeps native pacing at 60 Hz. The cap ends 250 milliseconds after the last
real picture; empty repeated-buffer and end-of-stream-only acquisitions do not keep it active. This avoids
advancing one decoded picture per high-rate game frame faster than the movie's audio clock.

## Building and checking

Use a fresh standalone lift from the supported build (`e0f3ccfa`). `nx2/lift.py --standalone --write` applies the
single verified MAIN clock hook through `nx2/oot_patches.py`. Native builds reject stale generated code that
does not contain that hook. The non-standalone lift keeps its existing behavior. For manual patch reapplication,
pass `--native-clock` to `nx2/oot_patches.py`.

The hook must find exactly one known function and one known load. A mismatch fails before changing generated
files. Reapplying the hook leaves an already patched input unchanged. Generated game code stays local.

The authored game-free regression runs the real C policy helpers and checks insertion, idempotence, mismatches,
non-standalone behavior, stale-build rejection and preservation of the other vector bytes and integer registers:

```text
python -I -B tests/test_native_clock_patch.py --cc clang
```

On Windows, run it from a compiler development shell. `gcc` or a path to `cl.exe` can also be supplied.

## Validation status

The refresh onto upstream `7398e5ab` preserves its renderer API3, controls, text-speed patch and realtime-window
option. Both Clang and MSVC pass nine focused authored checks, including window1-5 parity with the upstream
60-Hz trace, 60/70/75 movie transitions, combined patcher idempotence and the original-load negative control.
The upstream text-speed check also passes. A matching fresh lift, full build and runtime comparison of this
refreshed source have not run yet.

The historical public implementation was built from upstream `40c3f85` plus this change, using the audited
`foliage-linux-updater` toolkit (release `170ce53`). Both Clang and MSVC passed the eight focused authored
regressions, including the original-load negative control. A fresh standalone lift in a separate work directory
installed the hook through the production patcher; reverse/reapply and idempotence checks reproduced the
generated file byte-for-byte. The native build completed without failures. No private hand-edited generation,
clock-override flag or tracing was used for those historical-source results.

Runtime checks used Windows, an RTX 5090 and Ryzen 7 9800X3D, TV/profile0, Real time and frame generation off:

| Check | Observed result |
| --- | --- |
| Requested 75, movie playback | Opening/nightmare/logo, forest/Navi and sleeping Link inside the house were observed. Two movie timestamp/wall-time slopes were 0.99937 and 1.00011; maximum logged offset was 0.02 s. This case did not reach a gameplay HUD. |
| Requested 60, frozen-save route control | 59.9 native FPS passed the stability guard. A two-second movement and X-input smoke check ended off the rock facing Mido, without a Talk prompt or apparent collision/hang. |
| Requested 75, same scene | Failed the 69-FPS guard at 68.3 FPS (earlier samples 68.6/69.0). Movement was not scheduled. |
| Requested 70, same scene | 66.9 native FPS passed the separate 64.4-FPS guard and exceeded 60. The same two-second movement reached the same qualitative endpoint. |

The single 60/70 route pair measured minimap-marker displacement of 9.01081 versus 8.86233 pixels (1.65% lower
at 70). The high-rate endpoint spread was about 0.20 pixels. Static-map registration found zero shift, but its
normalized correlations were only 0.71-0.73. Pixel/input uncertainty and the absence of same-source A/A repeats
prevent an exact-physics conclusion. The jump transient was missed in both captures.

These results do not establish whole-game 70/75-FPS performance: heavy forest cinematic windows were around
58-62 FPS, with one cold transition at 39.8 FPS. The accepted upper target of 120 is a policy bound, not a tested
support claim. Current-source Per frame and no-pacing runtime checks were not repeated; those paths have pure
policy checks and separate private-precursor coverage only. Controller input, physical audio/timing, AMD/Intel
GPUs, Linux, handheld mode, longer routes and broader progression remain unverified. Keep the feature
experimental; the default remains 60 Hz.
