"""The NVN shims check the whole object they read from the game's memory, not its first byte (host/nvn_native.c, the
2026-11-14 audit's V8).

P(i) gives the host pointer of a guest pointer argument with one byte of it checked against the guest window. The shims
read whole objects and arrays through it: an object that ended past the window's end was read and written past it. Now
what reads through P says how much: a cast to a type takes PN(i, sizeof(type)), and the typed macros (CB, TX, CTX, VW,
CVW, QB, WB, SB, CSB, CSP, POOL, the texture builder) check their type's size with in_window. Checks, on the source:

- no "(T *)P(i)" read of a type T other than void is left (each is PN(i, sizeof(T)));
- each typed macro that turns a pointer into an object checks its size with in_window.

python -I -B tests/test_nvn_object_sizes.py
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

MACROS = {
    "TX": "nvn_tex", "CTX": "nvn_tex", "VW": "nvn_view", "CVW": "nvn_view", "QB": "nvn_queue_builder", "CB": "nvn_cmd",
    "WB": "nvn_win_builder", "SB": "nvn_samp_builder", "CSB": "nvn_samp_builder", "CSP": "nvn_samp", "POOL": "nvn_pool",
}


def main():
    src = (ROOT / "host/nvn_native.c").read_text(encoding="utf-8")
    bad = []
    left = [m.group(0) for m in re.finditer(r"\((?:const )?([A-Za-z_][A-Za-z0-9_]*) \*\)P\([0-9]+\)", src) if m.group(1) != "void"]
    if left:
        bad.append("%d typed reads through P(i) check one byte, e.g. %s" % (len(left), ", ".join(sorted(set(left))[:4])))
    for name, typ in MACROS.items():
        m = re.search(r"^#define %s\((\w)\) (.*)$" % name, src, re.M)
        if not m:
            bad.append("no #define %s: update this check with the source" % name)
        elif "in_window(" not in m.group(2) or "sizeof(%s)" % typ not in m.group(2):
            bad.append("%s(...) reads a %s without checking its size: %s" % (name, typ, m.group(2).strip()))
    tb = src[src.index("static nvn_tex_builder *tb_or_scratch("):]
    tb = tb[:tb.index("\n}\n")]
    if "in_window(p, sizeof(nvn_tex_builder))" not in tb:
        bad.append("the texture builder (tb_or_scratch) is read without checking its size")
    for b in bad:
        print("FAIL:", b)
    if bad:
        raise SystemExit(1)
    print("PASS: the NVN shims check the size of every object they read from the game's memory")


if __name__ == "__main__":
    main()
