# Experimental native scene base scale

The supported OoT v131072 configuration has a base scale table separate from
Hookshot's fixed/dynamic Resolution control. Mode 1 contains a 0.8 multiplier
on both axes: a 2560x1440 base can therefore create a 2048x1152 scene even when
the selected fixed scale is 100%.

`HOS_SCENE_NATIVE=1` opts into replacing that one table entry with 1.0 in both
graphics profiles. It is disabled by default. It does not force a window size,
select a different graphics mode or change antialiasing, textures or frame
generation. It can be used independently of an FSR experiment.

This removes only the base multiplier. Reaching a deterministic fixed 100% also
requires the separate stale-scale refresh proposed in
[PR #329](https://github.com/NotRexed/Hookshot/pull/329), enabled with
`HOS_FIX_RES=1`, or an equivalent upstream fix. A current-main test without that
dependency changed both base tables but still rendered a 1920x1080 scene into
a 2560x1440 output. This branch alone is therefore not a complete fixed-1440p
solution. The #329 patch is not copied into this contribution.

## Trying it

Re-lift and build the branch from your own supported dump so the generated code
contains the hook. Add this line to the local `hookshot_env.txt`, then restart:

```text
HOS_SCENE_NATIVE=1
```

For a first comparison, use fixed Resolution 100%, 16:9, with ultrawide and
handheld modes off. Those are the settings used in the initial local test.
Changing the setting requires a restart; remove it or set it to zero to restore
the original table.

The log reports a `native-scene: profile ... mode 1 base scale 0.8 -> 1.0` line
only when the exact stock table was found and changed. That confirms the
configuration change, not the dimensions of every later render target. Inspect
actual scene color/depth dimensions when validating another mode or area.

Rendering 2560x1440 instead of 2048x1152 adds 56.25% more scene pixels. Expect
increased GPU work and memory use; this is a quality option, not a free
performance improvement. Do not enable it automatically on lower-end GPUs.

## Why patch configuration updates

`f_80ea2f30` consumes the table when sizing scene resources. The hook is placed in
the configuration update `f_806702e0`, before its old/new mode comparison. It
checks the complete stock table (1.0, 0.8, 2/3, each on both axes), changes only
mode 1 and invalidates the comparison for that invocation. The original mode
assignment, dirty flags and callbacks then refresh dependent resources.

The actual mode is preserved. Unrecognised tables and already-patched tables
are left alone. The patcher validates the translated-code anchor and all
candidate chunks before writing; duplicate definitions fail without a partial
patch. No lifted game source is distributed.

This is a different layer from the stale fixed-resolution cache discussed in
[#329](https://github.com/NotRexed/Hookshot/pull/329). It neither imports nor
replaces that proposal. The wider resolution investigation is tracked in
[#68](https://github.com/NotRexed/Hookshot/issues/68). Coordinate changes there
before broadening this hook to more modes or percentages.

## Validation and remaining work

The initial local prototype ran on Windows, RX 9070 XT, driver 32.0.32015.7029,
in Kokiri Forest near Saria. Before this hook, renderer logs showed 2048x1152;
after it, color/depth/Native AA dispatches reached 2560x1440 and the player
reported clearer detail. That prototype also carried the fixed-scale refresh
from #329 and the separate FSR experiment. The isolated current-main test
confirmed the table changes and was visually approved by the player, but logged
1920x1080 -> 2560x1440 with no FSR context: it did not validate fixed native 1440p
without #329. The proposal is available for review with that dependency explicit.
Menu/area/mode transitions, handheld, ultrawide and other hardware remain
unvalidated.

A second isolated run on 2026-11-08 restored the existing #329 correction to
the same `3f5b0aa` base (integration source fingerprint `9882ef6b`). Both profile
edits executed, the native network report showed 2560x1440 input/output, and
FSR 4.1.2 completed at least 2,520 dispatches with matched 2560x1440 color/depth/
output guards. No FSR SDK error, dispatch failure or device-lost entry was
observed in the snapshot, and the player approved the result. This verifies the
combination at fixed 100% on that GPU; it does not establish that this branch
alone fixes the cached scale. Later upstream merges are checked separately and
are not covered by that gameplay session.

The game-free tests compile the actual hook in a memory fixture and cover the
disabled path, both profiles, table guards, all three modes, one-time resource
refresh, normal subsequent transitions, patcher failure atomicity and repeated
application:

```sh
python -I -B tests/test_native_resolution.py --cc gcc
python -I -B tests/test_text_speed.py --cc gcc
```

An optional local check executes the mode-selection and notification blocks
from your own lifted code instead of the fixture:

```sh
python -I -B tests/test_native_resolution.py --cc gcc --lifted-source work/sa/gen/code_005.c
```

Keep that generated source and all build artifacts out of commits.
