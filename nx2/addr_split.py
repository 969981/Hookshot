"""Guest accesses at a base register plus a constant offset, lifted as `recomp_<op>_k(c, base, offset, ...)`.

Every guest address is masked into the 512 GB guest window (rtgen.py A64_PTR), so that no address the game's code
computes reaches host memory outside it. Masked as a whole, `base + offset` costs an add and a mask per access: the
offset can no longer be the access's displacement, and neighbouring accesses off one base are no longer one base
register. The `_k` helpers (rtgen.py split_helpers) mask the base alone and add the offset after, which keeps the
displacement. The offset is the instruction's immediate (AArch64 addressing: -512 to 32760 here), bounded here to
+-64 KB, and the guards reserved on both sides of the window (host/hos_main.c) take what base + offset reaches past
either end: a fault, as an access outside the window must be.

The forms rewritten, as the translator writes them (anything else keeps the whole address masked):
    recomp_load64(c,c->x[9] + 2440)          -> recomp_load64_k(c,c->x[9],2440)
    recomp_load64(c,(c->x[31]+80ULL))        -> recomp_load64_k(c,c->x[31],80)
    recomp_ldp64(c,(_b+_o),..) / (_b+_o)+16  -> recomp_ldp64_k(c,_b,_o,..) / recomp_ldp64_k(c,_b,_o+16,..)
      (only where every _o of the function is a constant: `int64_t _o=<number>;`)

install() wraps Lifter.emit_function. A64_NO_ADDR_SPLIT=1 at lift time: not rewritten.
"""
import os
import re

import a64_lift

OPS = r"recomp_(?:load|store|ldp|stp)(?:8|16|32|64)(?:_sp)?"
LIMIT = 1 << 16
REG_PLUS = re.compile(r"\b(" + OPS + r")\(c,(c->x\[\d+\]) \+ (\d+)(?=[,)])")
REG_PLUS_ULL = re.compile(r"\b(" + OPS + r")\(c,\((c->x\[\d+\])\+(\d+)ULL\)(?=[,)])")
BO_PLUS = re.compile(r"\b(" + OPS + r")\(c,\(_b\+_o\)\+(\d+)(?=[,)])")
BO = re.compile(r"\b(" + OPS + r")\(c,\(_b\+_o\)(?=[,)])")
O_DEF = re.compile(r"int64_t _o=([^;]*);")
CONST = re.compile(r"-?\d+")


def split(text):
    def reg(m):
        k = int(m.group(3))
        if k >= LIMIT:
            return m.group(0)
        return "%s_k(c,%s,%d" % (m.group(1), m.group(2), k)
    text = REG_PLUS.sub(reg, text)
    text = REG_PLUS_ULL.sub(reg, text)
    offsets = O_DEF.findall(text)
    if offsets and all(CONST.fullmatch(o) and abs(int(o)) < LIMIT for o in offsets):
        text = BO_PLUS.sub(lambda m: "%s_k(c,_b,_o+%s" % (m.group(1), m.group(2)) if int(m.group(2)) < LIMIT
                           else m.group(0), text)
        text = BO.sub(lambda m: "%s_k(c,_b,_o" % m.group(1), text)
    return text


_installed = False


def install():
    global _installed
    if _installed or os.environ.get("A64_NO_ADDR_SPLIT"):
        return
    _installed = True
    base = a64_lift.Lifter.emit_function

    def emit_function(self, e, texts):
        text, refs = base(self, e, texts)
        return split(text), refs

    a64_lift.Lifter.emit_function = emit_function
