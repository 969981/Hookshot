"""a64-kit's guest model (rt/a64.h, rt/a64_rt.c) with a 64-bit guest window, written next to the lifted code.

a64-kit maps all guest memory below 4 GB (`A64_MEM + (uint32_t)addr`): its own loader picks every address. MKW
does not fit: a 39-bit address space, ~9.5 GiB of planned memory on the 12 GB layout, 2x 5.7 GiB sparse GPU
regions. With A64_ADDR64 (defined by the generated header) an access is `A64_MEM + addr` over a reservation of the
whole 39-bit space (Eden's fastmem arena, or our own), and lookups of code addresses refuse anything above 4 GB
instead of truncating it (code always lies below: Eden loads it at 0x80000000). The address is masked to the 39 bits
(A64_WINDOW_MASK), so lifted code and the host's GP() cannot reach outside the window.

It also adds the interrupt flag and A64_POLL that nx2/poll.py's loop-header polls use.

The changes are text substitutions on the vendored files, each required to match exactly once, so a refreshed
a64-kit that moved these lines fails here loudly instead of silently keeping the 32-bit window.
"""
import os
import re

import kit

POLL_DECL = r"""
/* (nx2/poll.py) loop-header interrupt polls: A64_POLLING builds call a64_poll, the backend's, when c->poll is set */
void a64_poll(Ctx *A64_NOESC c);
#if defined(A64_POLLING)
#define A64_POLL(c) do { if (__builtin_expect(__atomic_load_n(&(c)->poll, __ATOMIC_RELAXED) != 0, 0)) a64_poll(c); } while (0)
#else
#define A64_POLL(c) ((void)0)
#endif"""

ADDR64 = r"""#ifndef A64_ADDR64
#define A64_ADDR64 1   /* (nx2/rtgen.py: 64-bit guest window) */
#endif
#if A64_ADDR64
/* (nx2/rtgen.py) Every guest access is masked into the 512 GB window, so no guest address - whatever a save or other
   data the game reads makes its code compute - reaches host memory outside it: an address past the window wraps into
   it (guest memory only), and the few bytes an access at its very end runs over land in the guard reserved after it
   (hos_main.c). The game's own addresses all lie inside (host/hos.h's map), and are unchanged. A64_NO_WINDOW_MASK
   (measurement only): unmasked, as before. */
#define A64_WINDOW_BITS 39
#define A64_WINDOW_MASK ((1ull << A64_WINDOW_BITS) - 1)
#if defined(A64_NO_WINDOW_MASK)
#define A64_PTR(a) (A64_MEM + (uint64_t)(a))
#define A64_PTRK(b, k) (A64_MEM + (uint64_t)(b) + (int64_t)(k))
#else
#define A64_PTR(a) (A64_MEM + ((uint64_t)(a) & A64_WINDOW_MASK))
/* (nx2/addr_split.py) a base register and an instruction's constant offset: only the base is masked, so the offset
   stays an addressing-mode displacement (masking base + offset took an add and a mask per access). The offset is
   small (the lift checks it: -64 KB to 64 KB) and the guards on both sides of the window (hos_main.c) take what it
   reaches past either end. */
#define A64_PTRK(b, k) (A64_MEM + ((uint64_t)(b) & A64_WINDOW_MASK) + (int64_t)(k))
#endif
#define A64_DIRTY_PAGES (1ull << 27)   /* (nx2/rtgen.py) A64_WRITE_TRACK: a mark per 4 KB page of the 512 GB window */
#else
#define A64_DIRTY_PAGES (1ull << 20)
#define A64_PTR(a) (A64_MEM + (uint32_t)(a))
#endif"""

RET_MISMATCH = ("void a64_ret_mismatch(Ctx *A64_NOESC c, uint64_t pc);         "
                "/* RET to something other than the caller (debug builds) */")
THREAD = "    void *thread;               /* the OS layer's thread object */\n} GuestContext;"
LOOKUP = "    uint32_t a = (uint32_t)addr, lo = 0, hi = a64_fn_count;"
# (the soft write watch's table at the window's size, the address masked into it: a stray pointer marks a wrong
# page, never memory past the table)
MARK_NATIVE = r"""extern uint8_t a64_dirty[1u << 20];
/* (Tried 2026-10-21: reading a mark first and writing it only when clear, against cache lines moving between cores:
   game threads +1.1 ms a frame, the code 18 MB bigger. Not kept.) */
#define A64_MARK(a, n) (__atomic_store_n(&a64_dirty[(uint32_t)(a) >> 12], 1, __ATOMIC_RELEASE), \
                        __atomic_store_n(&a64_dirty[(uint32_t)((a) + (n) - 1) >> 12], 1, __ATOMIC_RELEASE))"""
MARK_NATIVE_64 = r"""extern uint8_t a64_dirty[A64_DIRTY_PAGES];
/* (Tried 2026-10-21: reading a mark first and writing it only when clear, against cache lines moving between cores:
   game threads +1.1 ms a frame, the code 18 MB bigger. Not kept.) */
#define A64_DIRTY_AT(a) (((uint64_t)(a) >> 12) & (A64_DIRTY_PAGES - 1))
#define A64_MARK(a, n) (__atomic_store_n(&a64_dirty[A64_DIRTY_AT(a)], 1, __ATOMIC_RELEASE), \
                        __atomic_store_n(&a64_dirty[A64_DIRTY_AT((a) + (n) - 1)], 1, __ATOMIC_RELEASE))"""

SUBST = {
    "a64.h": [
        (THREAD, THREAD.replace("\n} GuestContext;", "\n    uint32_t poll;              "
                                "/* (nx2/rtgen.py) interrupt request: A64_POLL calls a64_poll when set */\n} GuestContext;")),
        (RET_MISMATCH, RET_MISMATCH + POLL_DECL),
        ("#define A64_PTR(a) (A64_MEM + (uint32_t)(a))", ADDR64),
        (MARK_NATIVE, MARK_NATIVE_64),
    ],
    "a64_rt.c": [
        (LOOKUP, "#if A64_ADDR64\n    if (addr >> 32) return NULL;   /* (code lies below 4 GB: never a truncated match) */\n"
                 "#endif\n" + LOOKUP),
        ("uint8_t a64_dirty[1u << 20];", "uint8_t a64_dirty[A64_DIRTY_PAGES];"),
    ],
}


# (nx2/addr_split.py) each memory helper again as <name>_k(c, base, offset, ...): the address is base + offset, the base
# alone masked (A64_PTRK)
HELPER = re.compile(r"^A64_INLINE (\w+) (recomp_(?:load|store|ldp|stp)\w*)\(GuestContext \*c, uint64_t a(.*)$", re.M)


def split_helpers(text):
    out = []
    for m in HELPER.finditer(text):
        line = m.group(0)
        body = line[line.index("{"):]
        body = body.replace("A64_PTR(a)", "A64_PTRK(b, k)").replace("A64_MARK(a,", "A64_MARK(b + k,")
        body = re.sub(r"(recomp_\w+)\(c, a \+ (\d+)", r"\1_k(c, b, k + \2", body)
        body = re.sub(r"(recomp_\w+)\(c, a,", r"\1_k(c, b, k,", body)
        body = re.sub(r"(recomp_\w+)\(c, a\)", r"\1_k(c, b, k)", body)
        if re.search(r"\ba\b", body.replace("_a", "")):
            raise SystemExit("rtgen: %s uses its address in a way split_helpers does not know" % m.group(2))
        params = m.group(3)[:m.group(3).index(")")]
        out.append("A64_INLINE %s %s_k(GuestContext *c, uint64_t b, int64_t k%s) %s" % (m.group(1), m.group(2), params, body))
    if len(out) < 16:
        raise SystemExit("rtgen: only %d memory helpers found in a64-kit rt/a64.h: update nx2/rtgen.py" % len(out))
    anchor = "\n/* Everything is lifted at its final guest address"
    if text.count(anchor) != 1:
        raise SystemExit("rtgen: no place for the split helpers in a64-kit rt/a64.h: update nx2/rtgen.py")
    return text.replace(anchor, "\n/* (nx2/rtgen.py) base + offset forms (nx2/addr_split.py) */\n" + "\n".join(out) + "\n" + anchor)


def write(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for name, subs in SUBST.items():
        text = open(os.path.join(kit.KIT, "rt", name), encoding="utf-8").read()
        for old, new in subs:
            n = text.count(old)
            if n != 1:
                raise SystemExit("rtgen: %r found %d times in a64-kit rt/%s (expected once): update nx2/rtgen.py"
                                 % (old[:60], n, name))
            text = text.replace(old, new)
        if name == "a64.h":
            text = split_helpers(text)
        head = "/* Generated by mkw-recomp nx2/rtgen.py from a64-kit rt/%s (64-bit guest window). Do not edit. */\n" % name
        with open(os.path.join(out_dir, name), "w", newline="\n", encoding="utf-8") as f:
            f.write(head + text)
    return out_dir
