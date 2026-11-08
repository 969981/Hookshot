"""Opt-in native base render scale for OoT v131072, before resource sizing.

The graphics configuration has a scale table distinct from the GPU-time
controller. Its mode 1 uses 0.8 for both axes even at fixed Resolution 100%.
f_80ea2f30 reads this table to size the scene. f_806702e0 selects the mode and
notifies dependent resources when it changes. Keep that mode and its output
size; replace only its base scale, then follow the original notification path.
No game instructions/data or SDK binaries are distributed by this module.
"""
import glob
import os

HEAD = "void f_806702e0(Ctx *__restrict A64_NOESC c) {"
ANCHOR = "    c->x[14]=recomp_load8(c,c->x[0] + 1354);\n"
DECL = "extern volatile int hos_native_scene;\nextern void hos_log(const char *, ...);\n"
PATCH = """    // nx2/oot_native_resolution.py: mode 1 base scale, not the dynamic scale.
    if (hos_native_scene) {
        for (unsigned _profile = 0; _profile < 2; ++_profile) {
            const uint64_t _table = c->x[0] + 724 + _profile * 520;
            // Verify the complete stock table (1, .8, 2/3), both axes.
            // Unknown layouts and already-patched tables are left alone.
            if (recomp_load64(c, _table) == 0x3f8000003f800000ULL &&
                recomp_load64(c, _table + 8) == 0x3f4ccccd3f4ccccdULL &&
                recomp_load64(c, _table + 16) == 0x3f2aaaab3f2aaaabULL) {
                recomp_store64(c, _table + 8, 0x3f8000003f800000ULL);
                // Invalidate the old-mode comparison only in this invocation.
                // The original mode assignment, dirty flags and callbacks below
                // then run; no live render target or guest mode is forced here.
                c->x[14] = 0xff;
                hos_log("native-scene: profile %u mode 1 base scale 0.8 -> 1.0; engine resource refresh requested", _profile);
            }
        }
    }
"""


def patch(source):
    """Patch this exact configuration function, with fail-closed layout checks."""
    if HEAD not in source:
        return None
    if source.count(HEAD) != 1:
        raise ValueError("Expected exactly one native-resolution configuration function")
    start = source.index(HEAD)
    end = source.find("\nvoid f_", start + len(HEAD))
    body = source[start:end if end >= 0 else len(source)]
    if PATCH in body:
        return source
    if body.count(ANCHOR) != 1 or "goto L_80670648;" not in body:
        raise ValueError("Native-resolution configuration layout differs")
    header = '#include "a64.h"\n'
    if source.count(header) != 1:
        raise ValueError("Expected one translated-code header")
    at = start + body.index(ANCHOR) + len(ANCHOR)
    source = source[:at] + PATCH + source[at:]
    return source.replace(header, header + DECL, 1)


def apply(gen, strict=False):
    candidates = []
    for path in sorted(glob.glob(os.path.join(gen, "code_*.c"))):
        with open(path, encoding="utf-8", newline="") as f:
            before = f.read()
        after = patch(before)
        if after is None:
            continue
        candidates.append((path, before, after))
    # Validate the complete scan before changing anything (including duplicate
    # definitions split across chunks). Other games/partial test fixtures skip it.
    if len(candidates) > 1 or (strict and not candidates):
        raise ValueError("Expected one configuration function, found %d" % len(candidates))
    if not candidates:
        print("oot_native_resolution: WARNING: configuration function not found; native base scale unavailable")
    for path, before, after in candidates:
        if after != before:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(after)
        print("oot_native_resolution: configuration base-scale opt-in in", os.path.basename(path))
