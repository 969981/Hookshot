"""The unlimited real-time step's patch of the lifted per-frame clock (nx2/oot_patches.py, patch_unlimited_step).

f_80706be0 is the engine's per-frame clock: it steps by (30.0 or 60.0) / scvtf([x21, #0x2c8]) and accumulates that into the
double at [x19, #0xd58]. Every rate field the engine stores is an integer 60 / fps, so above 60 fps the step floors at 1/60 s
and the world fast-forwards. The patch takes the host's float (hos_unl_step_bits, the bits of 60.0 times the wall time between
frames) instead of the divide when it is nonzero, and records the objects it reads for the host to compare with the module
whose rate slot it holds. host/nn_vi.c realtime_step writes the float when Unlimited is chosen.

The contract tested here, with no compiler needed:
  - applies once: the divide is gone, the float and the probes are read, the file-scope externs arrive exactly once;
  - is idempotent: a second run on the patched file returns it unchanged;
  - returns None when the lifted file has no such function at all;
  - leaves the file unchanged when the function is there but its code differs (another game version), and does not add the
    externs then either;
  - does not touch any other function of the file.

python -I -B tests/test_unlimited_step_patch.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
import oot_patches  # noqa: E402

LIFTED = '''#include "a64.h"
void f_80706be0(Ctx *A64_NOESC c);
void f_80706be0(Ctx *__restrict A64_NOESC c) {
if (((c->x[19] >> 0) & 1)) { goto L_80706d10; }
L_80706cc4:; A64_POLL(c);
''' + oot_patches.STEP_OLD + '''c->x[8] = _v;
goto L_80706d10;
}
L_80706d10:; A64_POLL(c);
return;
}
void f_80707000(Ctx *__restrict A64_NOESC c) {
if (((c->x[0] >> 3) & 1)) { goto L_80707020; }
L_80707020:; A64_POLL(c);
return;
}
'''


def count(hay, needle):
    return hay.count(needle)


def main():
    assert count(LIFTED, oot_patches.STEP_OLD) == 1, "fixture: the engine's step appears once"

    # applies once
    patched = oot_patches.patch_unlimited_step(LIFTED)
    assert patched is not None and patched != LIFTED, "the patch applies to the fixture"
    assert count(patched, oot_patches.STEP_OLD) == 0, "the engine's divide is gone"
    assert count(patched, oot_patches.STEP_NEW) == 1, "the host float's branch is in"
    assert "hos_unl_step_bits" in patched and "hos_unl_step_probe" in patched, "the float and probes are read"
    assert count(patched, "extern volatile uint32_t hos_unl_step_bits;") == 1, "the externs arrive exactly once"
    assert patched.index("extern volatile uint32_t hos_unl_step_bits;") < patched.index("void f_80706be0"), \
        "the externs are file-scope, above the function"
    assert patched.count("void f_80707000") == 1 and oot_patches.STEP_OLD not in patched.split("void f_80707000")[1], \
        "the other function of the file is untouched"

    # idempotent: a second run over the patched file leaves it exactly as it is
    again = oot_patches.patch_unlimited_step(patched)
    assert again == patched, "applying the patch twice changes nothing"

    # no such function in the file at all: None (the build's [step] note tells the patcher to skip the file)
    assert oot_patches.patch_unlimited_step('#include "a64.h"\nvoid f_80000000(Ctx *c) {\n}\n') is None

    # the function, but its code differs (another game version): unchanged, and no externs added
    other = LIFTED.replace(oot_patches.STEP_OLD,
                           "{uint64_t _a=0,_b=0,_z,_v;memcpy(&_a,c->vreg[1],4);_z=0;\n"
                           "{uint32_t _t;{uint64_t _b=0;memcpy(&_b,c->x[21]+0x2c8,4);_t=(uint32_t)_b;}\n"
                           "A64F d;{uint32_t _a=60u;memcpy(&d.u32,&_a,4);uint32_t _s=_t;A64F o;o.u32=_s;d.f/=o.f;}\n"
                           "memcpy(&_v,d.u64,8);c->x[8]=_v;}}\n")
    assert count(other, oot_patches.STEP_OLD) == 0, "fixture: the other version really differs"
    out = oot_patches.patch_unlimited_step(other)
    assert out == other, "a function whose code differs keeps its own code"
    assert "hos_unl_step_bits" not in out, "and the externs are not added to it"

    print("test_unlimited_step_patch: ok")


if __name__ == "__main__":
    main()
