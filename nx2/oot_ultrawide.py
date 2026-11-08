"""Hor+ ultrawide for Ocarina of Time, applied by lift.py to the lifted C after oot_patches.

The cameras do not read the 16:9 constant host/hos_main.c's HOS_ASPECT changes (issue 297): each
sead::PerspectiveProjection keeps its own aspect (+0xB0), copied from its camera (+0x140). At runtime, with
hos_camera_aspect set (the Ultrawide option, or HOS_UW_ASPECT), every aspect within 0.01 of 16:9 is multiplied by
hos_camera_aspect / (16/9): the matrix builds (doUpdateMatrix and its inlined copies) and getAspect write the widened
value back to the projection, the camera -> projection copies widen it in flight. The vertical view stays as it is.
The movies (MovieRenderer, drawn by fn_83b7c710's frame-target pass) are drawn into a centred 16:9 rectangle of the
viewport, the rest cleared to black.

Sites follow Jbuck7's "Ultrawide + HUD fix" exefs patch for v131072 (gamebanana.com/mods/737495). Without
hos_camera_aspect the game's own path runs.
"""
import glob
import os

HELPER = r"""
extern float hos_camera_aspect;
static float hos_uw_k(void) {
    const float a = hos_camera_aspect;
    return a > 1.8f ? a / (16.0f / 9.0f) : 1.0f;
}
static uint32_t hos_uw_widen(uint32_t bits) {
    const float k = hos_uw_k();
    float f;
    memcpy(&f, &bits, 4);
    const float d = f - 16.0f / 9.0f;
    if (k == 1.0f || !(d < 0.01f && d > -0.01f)) return bits;
    f *= k;
    memcpy(&bits, &f, 4);
    return bits;
}
"""

MOVIE = r"""
void f_83539bb0(Ctx *A64_NOESC);
void f_802a46e0(Ctx *A64_NOESC);
void f_83b82250(Ctx *A64_NOESC);
/* fn_83b7e460's tail call to the MovieRenderer draw: from fn_83b7c710's frame-target pass, the frame is drawn into the
   centred 16:9 part of that pass's viewport, the bars cleared to black, the viewport put back after */
static void hos_uw_movie(Ctx *__restrict A64_NOESC c) {
    const float k = hos_uw_k();
    if (k == 1.0f || c->x[30] != 0x83b7c7b0ULL) { TAILCALL(f_83b82250); }
    const uint64_t lr = c->x[30], fp = c->x[29], movie = c->x[0], ctx = c->x[1];
    const uint64_t space = c->x[2] & 0xFFFFFFFFULL, flag = c->x[3] & 0xFFFFFFFFULL;
    uint64_t target = recomp_load64(c, c->x[19] + 0x20);   /* (the RenderBuffer fn_83b7c710 bound) */
    if (!target) target = recomp_load64(c, c->x[19] + 0x18);
    const uint64_t sp = c->x[31] - 0x80;
    c->x[31] = sp;
    recomp_store64(c, sp + 0x70, 0);   /* opaque black */
    recomp_store64(c, sp + 0x78, 0x3f80000000000000ULL);
    c->x[0] = target, c->x[1] = ctx, c->x[2] = 0, c->x[3] = 1, c->x[4] = sp + 0x70;
    f_83539bb0(c);
    for (uint64_t i = 0; i < 0x28; i += 8) recomp_store64(c, sp + 0x48 + i, recomp_load64(c, fp - 0x28 + i));
    if (!(uint32_t)recomp_load32(c, sp + 0x60)) {   /* (posture: a rotated device is left alone) */
        uint32_t lo = (uint32_t)recomp_load32(c, sp + 0x50), hi = (uint32_t)recomp_load32(c, sp + 0x58);
        float x0, x1;
        memcpy(&x0, &lo, 4), memcpy(&x1, &hi, 4);
        const float inset = (x1 - x0) * ((1.0f - 1.0f / k) * 0.5f);
        x0 += inset, x1 -= inset;
        memcpy(&lo, &x0, 4), memcpy(&hi, &x1, 4);
        recomp_store32(c, sp + 0x50, lo);
        recomp_store32(c, sp + 0x58, hi);
    }
    c->x[0] = sp + 0x48, c->x[1] = ctx, c->x[2] = target;
    f_802a46e0(c);
    c->x[0] = movie, c->x[1] = ctx, c->x[2] = space, c->x[3] = flag, c->x[30] = lr;
    f_83b82250(c);
    c->x[0] = fp - 0x28, c->x[1] = ctx, c->x[2] = target;
    f_802a46e0(c);
    c->x[31] = sp + 0x80;
    c->x[30] = lr;
}
"""

LDP = "{{ uint64_t _b=c->x[{b}]; int64_t _o=172; {{ uint64_t _p0,_p1; recomp_ldp32(c,(_b+_o),&_p0,&_p1); a64_vset64(c->vreg[{s0}],_p0); a64_vset64(c->vreg[{s}],_p1); }}}}"
LDR = "{{ {{ uint64_t _v=recomp_load32(c,(c->x[{b}]+{o}ULL)); a64_vset64(c->vreg[{s}],_v); }} }}"

# (name, function, load, registers, write-back offset or None, set the projection's dirty flag at +8)
SITES = [
    ("doUpdateMatrix", 0x834A5CC0, LDP, dict(b=0, s0=0, s=1), 0xB0, False),
    ("getProjectionMatrix", 0x8025B910, LDP, dict(b=0, s0=0, s=3), 0xB0, False),
    ("layer render", 0x8006EC60, LDP, dict(b=21, s0=0, s=3), 0xB0, False),
    ("camera -> GPU context", 0x8035A8C0, LDP, dict(b=2, s0=0, s=1), 0xB0, False),
    ("inlined doUpdateMatrix (+0x198)", 0x8049DD00, LDR, dict(b=27, o=0x248, s=2), 0x248, False),
    ("inlined doUpdateMatrix (+0x98)", 0x8122C190, LDR, dict(b=19, o=0x148, s=2), 0x148, False),
    ("getAspect", 0x8035E910, LDR, dict(b=0, o=0xB0, s=0), 0xB0, True),
    ("inlined getAspect", 0x80255BE0, LDR, dict(b=21, o=0xB0, s=11), 0xB0, True),
    ("camera -> projection", 0x82E3B960, LDR, dict(b=19, o=0x140, s=0), None, False),
    ("camera -> projection", 0x82E3C3A0, LDR, dict(b=19, o=0x140, s=0), None, False),
    ("camera -> projection", 0x804E48C0, LDR, dict(b=19, o=0x140, s=0), None, False),
    ("camera clearance sphere", 0x829F4B10, LDR, dict(b=25, o=0x140, s=9), None, False),
]
MOVIE_FN, MOVIE_CALL = 0x83B7E460, "TAILCALL(f_83b82250);"


def widen(base, reg, back, dirty):
    s = "\n    { uint32_t _uo; memcpy(&_uo,&c->vreg[%d][0],4); const uint32_t _uw=hos_uw_widen(_uo);" % reg
    s += " if (_uw != _uo) { a64_vset64(c->vreg[%d],_uw);" % reg
    if back is not None:
        s += " recomp_store32(c,(c->x[%d]+%dULL),_uw);" % (base, back)
    if dirty:
        s += " recomp_store8(c,c->x[%d] + 8,1);" % base
    return s + " } }"


def apply(gen):
    files = sorted(glob.glob(os.path.join(gen, "code_*.c")))
    texts = {}

    def edit(fn, old, new, extra=""):
        head = "void f_%08x(Ctx *__restrict A64_NOESC c) {" % fn
        for path in files:
            s = texts.get(path)
            if s is None:
                with open(path, encoding="utf-8", newline="") as f:
                    s = f.read()
            at = s.find(head)
            if at < 0:
                continue
            end = s.find("\n}\n", at)
            body = s[at:end]
            hits = body.count(old)
            if hits != 1:
                return hits
            s = s[:at] + body.replace(old, new) + s[end:]
            if "hos_uw_widen(uint32_t" not in s:
                s = s.replace('#include "a64.h"\n', '#include "a64.h"\n' + HELPER, 1)
            if extra and extra not in s:
                s = s.replace(HELPER, HELPER + extra, 1)
            texts[path] = s
            return 1
        return 0

    done = 0
    for name, fn, form, regs, back, dirty in SITES:
        load = form.format(**regs)
        hits = edit(fn, load, load + widen(regs["b"], regs["s"], back, dirty))
        if hits == 1:
            done += 1
        else:
            print("oot_ultrawide: WARNING: %s (f_%08x): the aspect load was found %d times, left alone" % (name, fn, hits))
    movie = edit(MOVIE_FN, MOVIE_CALL, "TAILCALL(hos_uw_movie);", MOVIE)
    if movie != 1:
        print("oot_ultrawide: WARNING: the movie draw call was found %d times, left alone" % movie)
    for path, s in texts.items():
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(s)
    print("oot_ultrawide: %d of %d aspect sites, movies %s" % (done, len(SITES), "centred" if movie == 1 else "left alone"))
