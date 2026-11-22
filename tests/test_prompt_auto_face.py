"""Game-free: the button prompts' Auto face for a controller's name and SDL type (host/prompts.c face_of_pad, face_of_type,
contains). A name that says the maker's buttons wins; else the type SDL gives a pad (from its USB ids, or through Steam Input
from the pad behind the stand-in), in SDL3's numbers and SDL2's (the Switch's: host/hd_rumble.h's): a DualSense or a
DualShock 4 that SDL names "Wireless Controller" got the Xbox's. A Steam Controller
gets the Steam Deck's face, as the Deck does: SDL3 names it "Steam Controller", which fell through to the Xbox's. Under
Steam, Steam Input's stand-in pad for a Steam Controller has the same name, so it gets the Deck's face too. A pad named
"Steam Virtual Gamepad" stays on the Xbox's, since any controller can be behind it. And auto_face, which choose_face and live_face
ask: with no controller, the keyboard's face when the build made it and XInput has no controller either, else the Switch's.

python -I -B tests/test_prompt_auto_face.py [--cc gcc]
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (the controller's name as SDL gives it, the SDL loaded (3, 2), its type in that SDL's numbers (0 unknown), the face Auto picks)
NAMES = [
    ("", 3, 0, "HOS_PROMPT_SWITCH"),
    ("Steam Controller", 3, 0, "HOS_PROMPT_STEAMDECK"),
    ("Steam Controller", 3, 2, "HOS_PROMPT_STEAMDECK"),   # (Steam Input's stand-in for one: an Xbox 360's type)
    ("Steam Deck", 3, 0, "HOS_PROMPT_STEAMDECK"),
    ("Steam Deck Controller", 3, 1, "HOS_PROMPT_STEAMDECK"),
    ("Steam Virtual Gamepad", 3, 0, "HOS_PROMPT_XBOX"),
    ("Steam Virtual Gamepad", 3, 6, "HOS_PROMPT_PS5"),   # (a DualSense behind Steam Input)
    ("DualSense Wireless Controller", 3, 0, "HOS_PROMPT_PS5"),
    ("PS4 Controller", 3, 0, "HOS_PROMPT_PS4"),
    ("Nintendo Switch Pro Controller", 3, 0, "HOS_PROMPT_SWITCH"),
    ("Xbox 360 Controller", 3, 0, "HOS_PROMPT_XBOX"),
    ("8BitDo Ultimate 2C", 3, 0, "HOS_PROMPT_XBOX"),
    # (by type: SDL3's numbers)
    ("Wireless Controller", 3, 6, "HOS_PROMPT_PS5"),
    ("Wireless Controller", 3, 5, "HOS_PROMPT_PS4"),
    ("PS3 Controller", 3, 4, "HOS_PROMPT_PS4"),
    ("Xbox Series X Controller", 3, 3, "HOS_PROMPT_XBOX"),
    ("Pro Controller", 3, 7, "HOS_PROMPT_SWITCH"),
    ("Joy-Con (L)", 3, 8, "HOS_PROMPT_SWITCH"),
    ("Joy-Con Pair", 3, 10, "HOS_PROMPT_SWITCH"),
    ("Generic USB Joystick", 3, 1, "HOS_PROMPT_XBOX"),   # (standard: by name)
    ("Wireless Controller", 3, 0, "HOS_PROMPT_XBOX"),   # (no type: by name, as before)
    # (by type: SDL2's)
    ("Wireless Controller", 2, 7, "HOS_PROMPT_PS5"),
    ("Wireless Controller", 2, 4, "HOS_PROMPT_PS4"),
    ("Xbox One Controller", 2, 2, "HOS_PROMPT_XBOX"),
    ("Pro Controller", 2, 5, "HOS_PROMPT_SWITCH"),
    ("Joy-Con (R)", 2, 12, "HOS_PROMPT_SWITCH"),
    ("Virtual Controller", 2, 6, "HOS_PROMPT_XBOX"),   # (virtual: by name)
    ("PS5 Controller", 2, 6, "HOS_PROMPT_PS5"),
    # (a name that says the maker's buttons wins over the type: Steam Input's stand-in on SDL2 is typed an Xbox 360's)
    ("PS4 Controller", 2, 1, "HOS_PROMPT_PS4"),
    ("Nintendo Switch Pro Controller", 3, 2, "HOS_PROMPT_SWITCH"),
    # (no SDL loaded: by name)
    ("Wireless Controller", 0, 6, "HOS_PROMPT_XBOX"),
]


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + what)


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    a = p.parse_args()
    src = (ROOT / "host" / "prompts.c").read_text(encoding="utf-8")
    check("static int face_of_pad(" in src, "host/prompts.c has no face_of_pad")
    check("static int face_of_type(" in src, "host/prompts.c has no face_of_type")
    check("static int auto_face(" in src, "host/prompts.c has no auto_face")
    check("auto_face(name, sdl, type)" in block(src, "static int choose_face("), "choose_face does not ask auto_face")
    check("auto_face(name, sdl, type)" in block(src, "static int live_face("), "live_face does not ask auto_face")
    check("face_of_pad(name, sdl, type)" in block(src, "static int auto_face("), "auto_face does not ask face_of_pad")
    check("hos_sdl_load" not in src, "host/prompts.c loads SDL itself (pad_sdl.c says which SDL a pad came from)")
    check("face_of_type(sdl, type)" in block(src, "static int face_of_pad("), "face_of_pad does not ask face_of_type")

    harness = "\n".join([
        "#include <ctype.h>", "#include <stdio.h>", "#include <string.h>", '#include "gpu/vk/vk_api.h"', '#include "host/hd_rumble.h"',
        "static int pads_xinput;",
        "static int hos_xinput_pads(void) { return pads_xinput; }",
        "static int keyboard_built;",
        block(src, "static int contains("),
        block(src, "static int face_of_type("),
        block(src, "static int face_of_pad("),
        block(src, "static int auto_face("),
        "int main(void) {",
        *('    printf("%%d %%d\\n", face_of_pad("%s", %d, %d), %s);' % n for n in NAMES),
        # (Auto with no controller: the keyboard's face when the build made it and XInput has none either, else the Switch's;
        # a controller's name: its face, whatever the keyboard)
        "    keyboard_built = 1;",
        '    printf("%d %d\\n", auto_face("", 0, 0), HOS_PROMPT_KEYBOARD);',
        '    printf("%d %d\\n", auto_face("Xbox 360 Controller", 3, 0), HOS_PROMPT_XBOX);',
        "    pads_xinput = 1;",
        '    printf("%d %d\\n", auto_face("", 0, 0), HOS_PROMPT_SWITCH);',
        "    pads_xinput = 0, keyboard_built = 0;",
        '    printf("%d %d\\n", auto_face("", 0, 0), HOS_PROMPT_SWITCH);',
        '    printf("%d %d\\n", auto_face("Wireless Controller", 3, 6), HOS_PROMPT_PS5);',
        '    printf("%d %d\\n", auto_face("Wireless Controller", 2, 7), HOS_PROMPT_PS5);',
        '    printf("%d %d\\n", auto_face("Wireless Controller", 2, 6), HOS_PROMPT_XBOX);',
        '    printf("%d %d\\n", auto_face("Wireless Controller", 0, 6), HOS_PROMPT_XBOX);',
        "    return 0;",
        "}", ""])
    with tempfile.TemporaryDirectory() as tmp:
        c, exe = Path(tmp) / "face.c", Path(tmp) / "face"
        c.write_text(harness, encoding="utf-8")
        subprocess.run([a.cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT), str(c), "-o", str(exe)], check=True)
        out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout.split("\n")
    for (name, sdl, kind, face), line in zip(NAMES, out):
        got, want = line.split()
        check(got == want, '"%s", SDL%d type %d: face %s, not %s (%s)' % (name, sdl, kind, got, want, face))
    autos = ["no controller, the keyboard face built: the keyboard's", "a controller: its face, the keyboard face built",
             "no controller through SDL, one through XInput: the Switch's", "no controller, no keyboard face built: the Switch's",
             "a pad SDL3 says is a DualSense, by another name: the PlayStation 5's",
             "a pad SDL2 says is a DualSense (7), by another name: the PlayStation 5's",
             "SDL2's 6 (virtual), not SDL3's DualSense: by its name, the Xbox's",
             "no SDL: the type is not read, by its name, the Xbox's"]
    for what, line in zip(autos, out[len(NAMES):]):
        got, want = line.split()
        check(got == want, "auto_face, %s: face %s, not %s" % (what, got, want))
    print("ok: %d controller names and types, each with its face; Auto's keyboard face in %d cases" % (len(NAMES), len(autos)))


if __name__ == "__main__":
    main()
