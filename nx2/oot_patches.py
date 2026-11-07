"""Changes to the lifted C for Ocarina of Time, applied by lift.py after it writes the code (and by hand: python -I nx2/oot_patches.py).

Each is a runtime switch where it can be, so the game's own path stays reachable.

- Dynamic resolution (Lichii's built-in "Max Resolution" patch, main+0x805BD0): the engine's config flag at +0x94 picks the path that
  follows the GPU-time controller; with hos_fixed_resolution set it takes the other path, the 3D at full scale. The controller made
  a new set of render targets at each size, ~11 images a frame at times, and held the GPU near its budget.
"""
import argparse
import glob
import os
import re

DECL = "extern volatile int hos_fixed_resolution;\nextern volatile int hos_dynres_runs;\nextern volatile uint32_t hos_fixed_scale_bits;\n"
OLD = "if (!((c->x[8] >> 0) & 1)) { goto L_8082640c; }"
NEW = "if (hos_fixed_resolution || !((c->x[8] >> 0) & 1)) { goto L_8082640c; }\n    hos_dynres_runs++;"
LABEL = "L_8082640c:;"
SCALE_OLD = "c->x[9] = 0x3f800000ULL;"
SCALE_NEW = "c->x[9] = hos_fixed_scale_bits;"   # (the fixed path's scale, 1.0 as shipped)

CLOCK_FUNCTION = re.compile(r"(?m)^void\s+f_80706be0\s*\(\s*Ctx\s*\*\s*__restrict\s+A64_NOESC\s+c\s*\)\s*\{")
CLOCK_LOAD = "{ { uint64_t _v=recomp_load32(c,(c->x[21]+712ULL)); a64_vset64(c->vreg[0],_v); } }"
CLOCK_MARKER = "/* native clock input: preserve the load and upper vector bytes */"
CLOCK_PATCH = """    /* native clock input: preserve the load and upper vector bytes */
    extern uint32_t hos_vi_clock_input(uint32_t original);
    uint32_t hos_clock_original;
    memcpy(&hos_clock_original, &c->vreg[0][0], 4);
    const uint32_t hos_clock_applied = hos_vi_clock_input(hos_clock_original);
    if (hos_clock_applied != hos_clock_original)
        memcpy(&c->vreg[0][0], &hos_clock_applied, 4);
"""
_C_TOKEN = re.compile(r'''//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[{}]''', re.S)


def patch_native_clock(source):
    """Patch only the verified MAIN consumer, retaining its load and all later code."""
    functions = list(CLOCK_FUNCTION.finditer(source))
    if len(functions) != 1:
        raise RuntimeError("native clock: expected one MAIN f_80706be0 definition; found %d" % len(functions))
    function = functions[0]
    depth = 1
    end = None
    for token in _C_TOKEN.finditer(source, function.end()):
        if token.group() == "{": depth += 1
        elif token.group() == "}": depth -= 1
        if not depth:
            end = token.end()
            break
    if end is None:
        raise RuntimeError("native clock: MAIN consumer has no closing brace")
    body = source[function.start():end]
    loads = list(re.finditer(r"(?m)^[ \t]*" + re.escape(CLOCK_LOAD) + r"[ \t]*(?:\r?\n|$)", body))
    if len(loads) != 1:
        raise RuntimeError("native clock: expected one verified low32 nominal load in MAIN; found %d; regenerate from the supported build" % len(loads))
    newline = "\r\n" if "\r\n" in body else "\n"
    patch = CLOCK_PATCH.replace("\n", newline)
    at = loads[0].end()
    if CLOCK_MARKER in body:
        if body.count(CLOCK_MARKER) != 1 or body[at:at + len(patch)] != patch:
            raise RuntimeError("native clock: existing MAIN hook is incomplete or unexpected; regenerate it")
        return source
    if "hos_vi_clock_input" in body or "hos_clock_original" in body:
        raise RuntimeError("native clock: unexpected partial MAIN hook; regenerate it")
    return source[:function.start()] + body[:at] + patch + body[at:] + source[end:]


def _native_clock_target(paths):
    count = 0
    target = None
    for path in paths:
        with open(path, encoding="utf-8", newline="") as f:
            source = f.read()
        found = len(CLOCK_FUNCTION.findall(source))
        count += found
        if count > 1:
            raise RuntimeError("native clock: multiple MAIN consumer definitions; no files changed")
        if found:
            target = path, source
    if count != 1:
        raise RuntimeError("native clock: MAIN consumer was not found; no files changed; regenerate the supported build")
    return target


def check_native_clock(gen):
    """Reject stale standalone generated code before starting the native compile."""
    _, source = _native_clock_target(sorted(glob.glob(os.path.join(gen, "code_*.c"))))
    if patch_native_clock(source) != source:
        raise RuntimeError("native clock: generated code is missing its clock hook; rerun nx2/lift.py --standalone --write before building")


def apply(gen, native_clock=False):
    paths = sorted(glob.glob(os.path.join(gen, "code_*.c")))
    clock_path = clock_source = None
    if native_clock:
        # Validate the complete native target before changing any generated file.
        clock_path, source = _native_clock_target(paths)
        clock_source = patch_native_clock(source)
    done = 0
    for path in paths:
        with open(path, encoding="utf-8", newline="") as f:
            original = f.read()
        s = clock_source if path == clock_path else original
        if OLD not in s:
            if s != original:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(s)
            continue
        s = s.replace(OLD, NEW, 1)
        at = s.index(LABEL)
        scale = s.index(SCALE_OLD, at)
        s = s[:scale] + SCALE_NEW + s[scale + len(SCALE_OLD):]
        s = s.replace('#include "a64.h"\n', '#include "a64.h"\n' + DECL, 1)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(s)
        done += 1
        print("oot_patches: dynamic resolution switch in", os.path.basename(path))
    if done != 1:
        print("oot_patches: WARNING: the dynamic resolution branch was found %d times" % done)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gen", nargs="?", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "work", "sa", "gen"))
    parser.add_argument("--native-clock", action="store_true", help="also apply the standalone native-rate clock hook")
    args = parser.parse_args()
    apply(args.gen, native_clock=args.native_clock)
