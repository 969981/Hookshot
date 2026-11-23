"""The Debug tab's Movement speed (1x to 3x): packs/oot/lift/oot_patches.py's patch_cct_speed hooks the places a character controller's
forward speed is set (SPEED_PLACES: Link's move f_809552f0 at its two setter calls, the setter f_80559df0 and f_80964bf0), each
once, after its anchor, with s0's unit; a file patched before is given back as it is, and a file without them is left alone.
apply makes them only in the supported build's lift (protect_hearts). packs/oot/host/oot_cheats.c's hos_oot_cct_speed scales the speed for
Link's controller alone, only while the left stick is pushed and only up to 15 m/s (a ledge climb's 55 is left alone); the menu
sends the row's percent (the pack's menu table's choice, OOT_OPT_SPEED), 100 while the debug tools are off.

python -I -B tests/test_movement_speed.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
sys.path.insert(0, str(ROOT / "packs" / "oot" / "lift"))   # (Ocarina's pack: its patch modules)
import oot_patches  # noqa: E402

BODY = "    c->x[9]=recomp_load32(c,c->x[8] + 32);\nreturn;\n}\n"


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + what)


def lifted():
    """One file with the three functions, each holding its anchors once"""
    out, done = '#include "a64.h"\n', set()
    for head, anchor, _ in oot_patches.SPEED_PLACES:
        if head in done:
            continue
        done.add(head)
        anchors = [a for h, a, _ in oot_patches.SPEED_PLACES if h == head]
        text = head + "".join(a if a != head else "" for a in anchors) + BODY
        out += text
    return out


def main():
    src = lifted()
    out, n = oot_patches.patch_cct_speed(src)
    check(n == len(oot_patches.SPEED_PLACES) == 4, "the speed's places hooked %d times, not 4" % n)
    check(out.count("hos_oot_cct_speed(c, c->x[0], 0);") == 2, "Link's move: two hooks with a frame's speed")
    check(out.count("hos_oot_cct_speed(c, c->x[0], 1);") == 2, "the setters: two hooks with a second's speed")
    for head, anchor, per_second in oot_patches.SPEED_PLACES:
        check(anchor + oot_patches.SPEED_HOOK % (per_second, "second" if per_second else "frame") in out,
              "a hook does not follow its anchor: %r" % anchor[:40])
    check(out.count("extern void hos_oot_cct_speed(Ctx *c, uint64_t controller, int per_second);") == 1, "the hook is declared once")
    check(oot_patches.patch_cct_speed(out) == (out, 4), "a file patched before was patched again")
    check(oot_patches.patch_cct_speed('#include "a64.h"\nvoid f_80000000(Ctx *__restrict A64_NOESC c) {\n' + BODY) is None,
          "a file without the places was patched")

    with tempfile.TemporaryDirectory() as gen:
        path = os.path.join(gen, "code_000.c")
        for protect, want in ((False, 0), (True, 4)):
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(src)
            stdout = sys.stdout
            sys.stdout = open(os.devnull, "w")
            try:
                oot_patches.apply(gen, protect_hearts=protect)
            finally:
                sys.stdout.close()
                sys.stdout = stdout
            with open(path, encoding="utf-8") as f:
                got = f.read().count("    hos_oot_cct_speed(c, c->x[0], ")
            check(got == want, "apply(protect_hearts=%s) made %d hooks, not %d" % (protect, got, want))

    host = (ROOT / "packs" / "oot" / "host" / "oot_cheats.c").read_text(encoding="utf-8")
    fn = host[host.index("void hos_oot_cct_speed("):]
    fn = fn[:fn.index("\n}\n")]
    check("oot_speed_controller" in fn and "hos_move_stick_held()" in fn, "the host scales only Link's speed while he is steered")
    check(re.search(r"<= 15\.0f", fn) is not None, "the host leaves speeds over 15 m/s alone")
    # (the row is the pack's menu table's (packs/oot/menu/menu.c, game packs a4): a choice whose value the game reads at
    # OOT_OPT_SPEED, its off read while the tools are off, as the renderer answers a choice (vk_menu.cpp menu_game_option))
    table = (ROOT / "packs" / "oot" / "menu" / "menu.c").read_text(encoding="utf-8")
    check("static const int oot_speeds[] = {100, 125, 150, 200, 300};" in table, "the row's choices are 1x, 1.25x, 1.5x, 2x, 3x")
    check('static const char *const oot_speed_names[] = {"1x", "1.25x", "1.5x", "2x", "3x"};' in table, "the choices' names")
    speed = table[table.index('.name = "Movement speed"'):]
    speed = speed[:speed.index("},")]
    check(".option = OOT_OPT_SPEED, .off = 100," in speed and ".values = oot_speeds" in speed,
          "the row sends its percent at OOT_OPT_SPEED, 100 while the debug tools are off")
    menu = (ROOT / "gpu" / "vk" / "vk_menu.cpp").read_text(encoding="utf-8")
    check(re.search(r"if \(t->kind == NX2_TOOL_CHOICE\) return \*out = cheats_on\(\) \? choice_value\(t, v\) : t->off, true;",
                    menu) is not None, "the renderer answers a choice with its off while the debug tools are off")
    print("PASS: movement speed hooks, host gate and menu choices")


if __name__ == "__main__":
    main()
