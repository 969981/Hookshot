"""SDIV in lifted code gives what AArch64 gives when the most negative number is divided by -1: the dividend. Game-free.

The quotient does not fit there, and AArch64 wraps it: INT64_MIN / -1 is INT64_MIN, and INT32_MIN / -1 is INT32_MIN in
the low word with the high word clear. In C that division is undefined, and x86's idiv traps on it (#DE): the lifted
code suyu's translator wrote for SDIV divided as C does, so a game dividing like that died with no message (SIGFPE on
Linux, 0xC0000095 on Windows). nx2/insn.py writes SDIV's C now, the same but for that case.

This builds suyu's translator (a64emit.cpp, at -O0 into a temporary folder, as tests/test_fpx_sticky.py does), installs
nx2/insn.py's wrappers as nx2/lift.py does, translates hand-encoded SDIV words through a64emit.translate (X and W
forms, a register read as XZR, and UDIV beside them, which must stay as it was) and compiles each C text into a small
program at -O0 and at -O2, the lifted code's level. Every case then runs in a process of its own, so a trap is that
case's failure and names it: the two overflows, a divisor of -1 with other dividends, a divisor of 0 (0, as on the
console), and ordinary quotients, each against the value AArch64 defines, worked out here in Python.

    python -I -B tests/test_sdiv_overflow.py [--cc clang] [--cxx clang++]

Skipped (exit 0) when there is no C or C++ compiler.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc", "--cxx", "{cxx}"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, "third_party", "a64-kit")
sys.path.insert(0, os.path.join(KIT, "lift"))
sys.path.insert(0, os.path.join(KIT, "tools"))
sys.path.append(os.path.join(ROOT, "nx2"))

M64, M32 = (1 << 64) - 1, (1 << 32) - 1
MIN64, MIN32 = 1 << 63, 1 << 31


def word(sf, opcode, rm, rn, rd):
    """A data-processing (2 source) instruction: UDIV is opcode 2, SDIV 3"""
    return (sf << 31) | 0x1AC00000 | (rm << 16) | (opcode << 10) | (rn << 5) | rd


# (name, the instruction, the registers it reads as (n, m); 31 is XZR, which reads 0 whatever c->x[31] holds)
FORMS = [("sdiv x0, x1, x2", word(1, 3, 2, 1, 0), (1, 2)),
         ("sdiv w0, w1, w2", word(0, 3, 2, 1, 0), (1, 2)),
         ("sdiv x0, xzr, x2", word(1, 3, 2, 31, 0), (31, 2)),
         ("sdiv x0, x1, xzr", word(1, 3, 31, 1, 0), (1, 31)),
         ("sdiv x7, x7, x7", word(1, 3, 7, 7, 7), (7, 7)),
         ("udiv x0, x1, x2", word(1, 2, 2, 1, 0), (1, 2)),
         ("udiv w0, w1, w2", word(0, 2, 2, 1, 0), (1, 2))]

# (dividend, divisor) as the registers hold them, 64 bits; the W forms read the low words
VALUES = [(MIN64, M64), (MIN32, M32), (MIN64 | MIN32, M64), (0xFFFFFFFF80000000, 0xFFFFFFFFFFFFFFFF),
          (MIN64, 1), (MIN64, 2), (MIN64 + 1, M64), (MIN32 + 1, M32), (7, M64), (M64 - 6, M64), (0, M64),
          (7, 0), (MIN64, 0), (0, 0), (100, 7), (M64 - 99, 7), (100, M64 - 6), (M64 - 99, M64 - 6),
          (0x123456789ABCDEF0, 0x1000), (0x00000001FFFFFFF9, 0x0000000700000002)]


def signed(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def expected(name, a, b):
    """What AArch64 leaves in the destination register (64 bits): the quotient rounded toward zero, 0 for a divisor
    of 0, and a result that does not fit wrapped to the register's size; a W form clears the high word"""
    bits = 64 if " x" in name else 32
    mask = (1 << bits) - 1
    a, b = a & mask, b & mask
    if name.startswith("udiv"):
        return a // b if b else 0
    sa, sb = signed(a, bits), signed(b, bits)
    if not sb:
        return 0
    q = abs(sa) // abs(sb)
    return (-q if (sa < 0) != (sb < 0) else q) & mask


def skip(why):
    print("sdiv overflow: skipped (%s)" % why)
    sys.exit(0)


def find(given, env, names):
    for c in (given, os.environ.get(env)):
        if c:
            return shutil.which(c) or (c if os.path.isfile(c) else None)
    for n in names:
        if shutil.which(n):
            return shutil.which(n)
    return None


def translate(cxx, tmp):
    """Each form's C, by suyu's translator built into tmp, with nx2/insn.py's wrappers as the lift installs them"""
    dll = os.path.join(tmp, "a64emit.dll" if os.name == "nt" else "liba64emit.so")
    r = subprocess.run([cxx, "-std=c++20", "-O0", "-shared", "-Wno-everything", "-o", dll,
                        os.path.join(KIT, "lift", "a64emit.cpp")] + ([] if os.name == "nt" else ["-fPIC"]),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    if r.returncode:
        print(r.stdout)
        raise SystemExit("sdiv overflow: building suyu's translator failed")
    import build_emit
    build_emit.build = lambda force=False: dll   # (not a64emit.dll beside the source)
    import a64emit
    import insn
    insn.install()   # (as nx2/lift.py does)
    try:
        texts, flags = a64emit.translate([(0x1000 + 4 * k, w) for k, (_, w, _) in enumerate(FORMS)])
    finally:
        if a64emit._lib is not None:   # (unloaded, so the folder can go)
            import _ctypes
            (_ctypes.FreeLibrary if os.name == "nt" else _ctypes.dlclose)(a64emit._lib._handle)
            a64emit._lib = None
    for (name, w, _), text, flag in zip(FORMS, texts, flags):
        if flag or "c->x[" not in text:
            raise SystemExit("sdiv overflow: %s (%08x) was not translated: flag %d, %r" % (name, w, flag, text[:80]))
    return texts


PROGRAM = r"""
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef struct { uint64_t x[32]; } Ctx;
%s
static void (*const forms[])(Ctx *) = { %s };
/* <form> <n> <m> <dividend> <divisor>: the registers n and m set (the dividend last when both are one register), every
   other one holding a mark, the form run, and its destination printed. The values come from the command line, so
   the compiler cannot work the division out itself. */
int main(int argc, char **argv) {
    if (argc != 7) return 2;
    Ctx c;
    for (int i = 0; i < 32; i++) c.x[i] = 0x5A5A5A5A5A5A5A5AULL;
    const int form = atoi(argv[1]), n = atoi(argv[2]), m = atoi(argv[3]), d = atoi(argv[4]);
    c.x[m] = strtoull(argv[6], NULL, 16);
    c.x[n] = strtoull(argv[5], NULL, 16);
    forms[form](&c);
    printf("%%016" PRIx64 "\n", c.x[d]);
    return 0;
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cc")
    ap.add_argument("--cxx")
    a = ap.parse_args()
    cc = find(a.cc, "CC", ("gcc", "clang", "cc"))
    cxx = find(a.cxx, "CXX", ("clang++", "g++", "c++"))
    if not cc or not cxx:
        skip("no C or C++ compiler")
    failures, runs = [], 0
    with tempfile.TemporaryDirectory(prefix="hookshot-sdiv-") as tmp:
        texts = translate(cxx, tmp)
        bodies = "\n".join("static void form%d(Ctx *c) { %s }" % (k, t.strip()) for k, t in enumerate(texts))
        source = PROGRAM % (bodies, ", ".join("form%d" % k for k in range(len(texts))))
        with open(os.path.join(tmp, "t.c"), "w", encoding="utf-8") as f:
            f.write(source)
        for level in ("-O0", "-O2"):
            exe = os.path.join(tmp, "t%s.exe" % level)
            r = subprocess.run([cc, "-std=gnu11", level, "-fno-strict-aliasing", os.path.join(tmp, "t.c"), "-o", exe],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
            if r.returncode:
                print(r.stdout)
                raise SystemExit("sdiv overflow: the translated C does not compile at %s" % level)
            for k, (name, _, (n, m)) in enumerate(FORMS):
                d = 7 if "x7" in name else 0
                for va, vb in VALUES:
                    # (what the instruction reads: XZR is 0, and one register for both operands holds the dividend)
                    ra, rb = (0 if n == 31 else va), (0 if m == 31 else va if m == n else vb)
                    want = expected(name, ra, rb)
                    r = subprocess.run([exe, str(k), str(n), str(m), str(d), "%x" % va, "%x" % vb],
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                                       timeout=60)
                    runs += 1
                    got = r.stdout.strip()
                    if r.returncode:
                        failures.append("%s at %s, %016x / %016x: the program stopped (%s), AArch64 gives %016x"
                                        % (name, level, ra, rb, "signal %d" % -r.returncode if r.returncode < 0
                                           else "exit status %#x" % (r.returncode & 0xFFFFFFFF), want))
                    elif got != "%016x" % want:
                        failures.append("%s at %s, %016x / %016x: %s, AArch64 gives %016x"
                                        % (name, level, ra, rb, got, want))
    if failures:
        print("\n".join(failures[:24]))
        raise SystemExit("FAIL: %d of %d runs" % (len(failures), runs))
    print("PASS: %d runs of %d forms: SDIV and UDIV give what AArch64 gives, the overflows too" % (runs, len(FORMS)))


if __name__ == "__main__":
    main()
