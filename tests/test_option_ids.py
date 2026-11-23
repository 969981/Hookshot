"""All named and legacy numeric host/renderer option IDs must be distinct.

Compile cases from the actual API enums so implicit enum increments count too.
Keep pending Walk and Item wheel IDs reserved until they land (#2345, #2261).
A game's own ids (its pack's, packs/<key>/menu/oot_menu.h: Ocarina's tools since game packs a4) are in the pack range,
HOS_OPT_GAME_BASE and up, and distinct from each other and from the engine's.
"""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc")
    args = parser.parse_args()
    api = (ROOT / "gpu/vk/vk_api.h").read_text(encoding="utf-8")
    api = re.sub(r"/\*.*?\*/|//[^\n]*", "", api, flags=re.S)
    enums = re.findall(r"enum\s*\{[^}]*\}\s*;", api)
    declarations = [enum for enum in enums if "HOS_OPT_" in enum]
    names = re.findall(r"\bHOS_OPT_\w+\b", "\n".join(declarations))
    assert names, "No option declarations found"
    assert len(names) == len(set(names)), "Repeated option declaration"
    replay = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    option = replay.split("t.option =", 1)[1].split("};", 1)[0]
    numeric = re.findall(r"\bid\s*==\s*(\d+)\b", option)
    # (the game's own: Ocarina's pack's enum of option ids, each in the pack range)
    pack = (ROOT / "packs/oot/menu/oot_menu.h").read_text(encoding="utf-8")
    pack = re.sub(r"/\*.*?\*/|//[^\n]*", "", pack, flags=re.S)
    pack_enums = [enum for enum in re.findall(r"enum\s*\{[^}]*\}\s*;", pack) if "OOT_OPT_" in enum]
    pack_names = re.findall(r"\bOOT_OPT_\w+\b", "\n".join(pack_enums))
    assert len(pack_names) >= 7 and len(pack_names) == len(set(pack_names)), pack_names
    # (HOS_OPT_GAME_BASE is the pack range's start, not an id: a game's first id is it)
    cases = [n for n in names if n != "HOS_OPT_GAME_BASE"] + numeric + pack_names
    # Skip-title already owns 44; Walk and Item wheel are on concurrent branches.
    for name, value in (("HOS_OPT_SKIP_TITLE", 44), ("HOS_OPT_WALK_MODE", 49), ("HOS_OPT_WALK_SPEED", 50),
                        ("HOS_OPT_ITEM_WHEEL", 60)):
        if name not in names:
            cases.append(str(value))
    source = "\n".join(declarations + pack_enums) + "\n"
    source += "\n".join("_Static_assert((int)%s >= (int)HOS_OPT_GAME_BASE, \"%s: not in the pack range\");" % (n, n) for n in pack_names)
    source += "\nint option_id(int id) { switch (id) {\n"
    source += "\n".join(f"case {name}: return 0;" for name in cases)
    source += "\ndefault: return -1; } }\n"
    with tempfile.TemporaryDirectory(prefix="option-ids-") as temporary:
        path = Path(temporary) / "ids.c"
        obj = Path(temporary) / "ids.o"
        def compile(text):
            path.write_text(text, encoding="utf-8")
            return subprocess.run([args.cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-c", str(path), "-o", str(obj)], capture_output=True, text=True)
        result = compile(source)
        assert result.returncode == 0, result.stderr
        # Prove the guard catches the original collision, an implicit enum value,
        # and a legacy numeric ID, not just collisions among explicit constants.
        for collision in (49, 50, 60, 19, 7):   # (19: HOS_OPT_GYRO_SENS_X, an implicit one)
            broken = source.replace("HOS_OPT_FOV = 51", f"HOS_OPT_FOV = {collision}")
            assert broken != source
            assert compile(broken).returncode != 0, f"Missed ID collision at {collision}"
        broken = source.replace("OOT_OPT_SPEED,", "OOT_OPT_SPEED = 5,")   # (a game's id in the engine's range)
        assert broken != source and compile(broken).returncode != 0, "a game's id below HOS_OPT_GAME_BASE was not refused"
    print(f"PASS: {len(cases)} option IDs are unique; explicit, implicit and legacy collisions rejected")


if __name__ == "__main__":
    main()
