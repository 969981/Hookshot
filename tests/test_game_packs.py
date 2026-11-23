"""The game packs in the tree (packs/<key>/, nx2/packs.py; docs/game-packs.md steps a1 and a2) are each game's profile, and
Ocarina's hold its Python and its pipeline lists; building the profiles from them, and moving the code into them, changed
nothing the lift writes.

What this guards, and why each check is here rather than left to review:

1. **The profiles are what they were.** FROZEN below is every profile's fields, in order, as nx2/game_profile.py held
   them before the packs (main at ac0311f9), and HEADERS the SHA-256 of each profile's emit_header: the pack-built
   profiles must equal them field for field, type for type, and emit the same bytes, so game/game_profile.h, every
   lift's header and every runtime are unchanged. A deliberate profile change edits a pack.json and this copy together.
2. **A manifest is format 1's or refused, with the file and the field named:** each tree pack loads; a good synthetic
   one does; and each kind of bad one (a field missing, an unknown one, a wrong kind, a bool for a number, a bad hex
   string or build id, two builds, a key that is not its folder's, "none", another pack API, a comment on no field, a
   field twice, not JSON) is refused with a message that names it.
3. **The tree's order and default** are the profile order and the default game, a listed pack missing is an error, an
   unlisted one comes after in key order, and two packs cannot name one build.
4. **packs/ travels and counts:** the AppImage's tools and the Windows package carry it, the source stamp reads it, and
   the lift's cache key hashes every pack.json (a profile change lifts again, as an edit to game_profile.py did).
5. **The Builders' copies:** builder/HookshotBuilder/Games.cs's table and DefaultKey, the Qt Builder's PROFILES and
   DEFAULT_KEY (builder/py/hookshot_builder/core.py) and builder/setup/setup.c's HOOKSHOT_DEFAULT_GAME are the packs'
   (also held by tests/test_builder_games.py and tests/test_launcher_games.py, which run their code).
6. **Ocarina's modules moved without changing the lift's key for them (a2).** LIFT_A1 is each of its five lift modules'
   name and sha1 as the key hashed them at main 9919310d (a1, in nx2/): the key now hashes packs/oot/lift/'s under those
   names and bytes, in the same order (the names sorted). A pack.json is hashed by its lift fields only (packs.lift_view),
   so a comment, a feature or a build step does not lift again, and a profile field does; the packs' env prefixes (OOT_)
   join the key's.
7. **The pack's folders are where the engine looks:** load_module finds a profile's modules in its pack's lift/, every
   module name is one pack's alone and shadows none of nx2/ or a64-kit's; build_steps are the packs' (Ocarina's prompts
   and pipeline list, none for Bananza and MKW), and an unknown step is refused.
8. **A game's runtime C is its pack's, in its runtime only (a3).** nx2/build.py pack_host compiles Ocarina's packs/oot/host
   into Ocarina's runtime, Mario Kart World's packs/mkw/host into its, and host/game_none.c into Bananza's and an unknown
   game's; host/ holds no oot_ file, no function of Ocarina's debug tools, teleport, opening or loading cover, and none of
   rule 3's Mario Kart World 1.8.0 literals; host/nx2_pack.h's interface version is nx2/packs.py's; every pack's table
   and game_none.c's are of that version, and each hook the shared runtime calls is one of the table's. A pack object's
   key follows every header its C includes, wherever it lives (gpu/vk/oot_cheat_items.h: an Inventory change rebuilds
   Ocarina's cheats with the menu, #144's review), and the Windows CI compiles every pack's C and host/ (build.py
   --host-only, windows-builder.yml's runtime job, which a release waits for).

    python -I -B tests/test_game_packs.py
"""
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
import game_profile  # noqa: E402
import packs  # noqa: E402
from game_profile import Gfx, Hooks, Upscale  # noqa: E402

FAILURES = []


def check(cond, what):
    if not cond:
        FAILURES.append(what)
    return bool(cond)


EMPTY = dict(gfx=Gfx(frame_limiter=None, nominal_fps=None, vrr_mode=None, vrr_gate=None, controller=None,
                     controller_words=(), controller_rate_words=()),
             upscale=Upscale(taau=(), gameplay=(), network_keep=()),
             hooks=Hooks(gfx_clock=None, present_poll=None, present_events=()))

# Every profile's fields in the order Profile sets them, as nx2/game_profile.py held them at ac0311f9 (before the packs).
FROZEN = [
    ("oot", [
        ("key", "oot"),
        ("game", "The Legend of Zelda: Ocarina of Time"),
        ("program_id", "0400486024DEC000"),
        ("version", 131072),
        ("build_id", "e0f3ccfaf50065ae4ea01eb5aeb13b1a45da3348"),
        ("main_base", 0x80020000),
        ("patch_modules", ("oot_wide", "oot_fov", "oot_jitter", "oot_occlusion")),
        ("code_patch_module", "oot_patches"),
        ("gfx", Gfx(frame_limiter=0xfc, nominal_fps=0x2c8, vrr_mode=0x231, vrr_gate=0x232, controller=0x270,
                    controller_words=(0x58, 0x28, 0x2c, 0x30), controller_rate_words=(0x58, 0x28, 0x2c))),
        ("upscale", Upscale(taau=("fd029a317a9a4b94",), gameplay=(0xfd029a317a9a4b94, 0x9dc98871eb7fc101),
                            network_keep=("fd029a317a9a4b94", "9dc98871eb7fc101"))),
        ("hooks", Hooks(gfx_clock=0x8348c770, present_poll=0x80590520, present_events=(0x805904a0, 0x80590530))),
        ("npad_players", 1),
        ("account_users", 1),
        ("video_catchup", True),
        ("vk_parallel", None),
        ("released", True),
        ("version_name", "version 131072"),
        ("graphics_scale", None),
        ("notes", "The game this engine was retargeted to; every value here was measured on its dump."),
        ("romfs_needed", ((("Pack/ZsDic.pack.zs",), "file"), (("System/Resource",), "dir"),
                          (("System/HeapSize/Product.Nin_Ounce_NVN2.HeapDef.byml.zs",
                            "System/HeapSize/Default.Nin_Ounce_NVN2.HeapDef.byml.zs"), "file"))),
    ]),
    ("dkb", [
        ("key", "dkb"),
        ("game", "Donkey Kong Bananza"),
        ("program_id", "040074A01BF12000"),
        ("version", 458752),
        ("build_id", "e25c90c1c136638885659fd55bf8a03b3403489d"),
        ("main_base", 0x80020000),
        ("patch_modules", ()),
        ("code_patch_module", None),
        ("gfx", EMPTY["gfx"]),
        ("upscale", EMPTY["upscale"]),
        ("hooks", EMPTY["hooks"]),
        ("npad_players", 1),
        ("account_users", 1),
        ("video_catchup", False),
        ("vk_parallel", None),
        ("released", True),
        ("version_name", "3.2.0"),
        ("graphics_scale", 0),
        ("notes", "Identity measured on the dump; movie catch-up off (the guide player counts every picture)."),
        ("romfs_needed", ()),
    ]),
    ("mkw", [
        ("key", "mkw"),
        ("game", "Mario Kart World"),
        ("program_id", "0400C3F00006E000"),
        ("version", 0),
        ("build_id", "9e244438c3d8ef1c703a6a85ef99c4f000000000"),
        ("main_base", 0x80020000),
        ("patch_modules", ()),
        ("code_patch_module", None),
        ("gfx", EMPTY["gfx"]),
        ("upscale", EMPTY["upscale"]),
        ("hooks", EMPTY["hooks"]),
        ("npad_players", 1),
        ("account_users", 1),
        ("video_catchup", True),
        ("vk_parallel", 4),
        ("released", True),
        ("version_name", "1.0.0"),
        ("graphics_scale", None),
        ("notes", "Identity measured on the dump; the parallel replay on 4 threads (measured in a race)."),
        ("romfs_needed", ()),
    ]),
    ("none", [
        ("key", "none"),
        ("game", "an unknown game"),
        ("program_id", None),
        ("version", None),
        ("build_id", None),
        ("main_base", None),
        ("patch_modules", ()),
        ("code_patch_module", None),
        ("gfx", EMPTY["gfx"]),
        ("upscale", EMPTY["upscale"]),
        ("hooks", EMPTY["hooks"]),
        ("npad_players", 1),
        ("account_users", 1),
        ("video_catchup", True),
        ("vk_parallel", None),
        ("released", True),
        ("version_name", ""),
        ("graphics_scale", None),
        ("notes", "No profile knows this build: lifted without patches, and the runtime recognises nothing in it."),
        ("romfs_needed", ()),
    ]),
]

# emit_header(profile)'s SHA-256 for each, at ac0311f9; and the saves folder each had
HEADERS = {
    "oot": "7d5f24bbf0da5365e56adfb5daa9fd14f0e7aa06a38f624119dada2af0c19b1d",
    "dkb": "3d449b807f5b1f74eae6bdcd957447a6615e508177ce20ee34f94afcef1565ec",
    "mkw": "0676584cf5799035d5e4b75135eaa5503212c43f39b63b1e923f6498ff2c0d70",
    "none": "90b13bc6f13b09bdddc7155e1d3394d38f6826796928e557d199bc312059e05a",
}
SAVES = {"oot": "hookshot", "dkb": "hookshot-dkb", "mkw": "hookshot-mkw", "none": "hookshot-none"}


def check_frozen():
    have = [(k, list(vars(p).items())) for k, p in game_profile.GAMES.items()]
    check([k for k, _ in have] == [k for k, _ in FROZEN], "the profiles and their order are not oot, dkb, mkw, none: %r"
          % [k for k, _ in have])
    for (key, fields), (_, want) in zip(have, FROZEN):
        check([n for n, _ in fields] == [n for n, _ in want], "%s: the profile's fields or their order changed" % key)
        for (name, value), (_, frozen) in zip(fields, want):
            # (repr too: a list for a tuple, True for 1 or a str for a number compare equal or nearly, and are not the same)
            check(value == frozen and repr(value) == repr(frozen),
                  "%s.%s is %r, not %r as before the packs" % (key, name, value, frozen))
    for key, digest in HEADERS.items():
        text = game_profile.emit_header(game_profile.GAMES[key])
        check(hashlib.sha256(text.encode("utf-8")).hexdigest() == digest,
              "%s: emit_header's output is not the bytes it was before the packs" % key)
        check(game_profile.saves_dir(game_profile.GAMES[key]) == SAVES[key], "%s: the saves folder changed" % key)
    committed = (ROOT / "game" / "game_profile.h").read_bytes()
    check(hashlib.sha256(committed).hexdigest() == HEADERS[game_profile.DEFAULT],
          "game/game_profile.h is not the bytes it was before the packs")
    check(game_profile.OOT is game_profile.GAMES["oot"] and game_profile.DKB is game_profile.GAMES["dkb"]
          and game_profile.MKW is game_profile.GAMES["mkw"], "game_profile.OOT, DKB and MKW are not the packs' profiles")
    check(game_profile.saves_dir(game_profile.Profile("zz", "another")) == "hookshot-zz",
          "a profile with no pack does not get hookshot-<key>")


# ---- the schema ----

def good():
    """A whole manifest of a made-up game (no game's values), every optional field present"""
    return {
        "format": 1, "key": "zz", "name": "A Made-up Game", "short_name": "Made-up", "version": "0.1.0",
        "api": {"major": packs.NX2PC_PACK_API[0], "minor": packs.NX2PC_PACK_API[1]}, "nx2pc": ">=3.0.0",
        "released": False, "program_id": "0100000000000000",
        "builds": [{"build_id": "ab" * 20, "version": 0, "version_name": "1.0.0", "main_base": "0x80020000"}],
        "profile": {
            "gfx": {"frame_limiter": "0x10", "nominal_fps": None, "vrr_mode": None, "vrr_gate": None, "controller": None,
                    "controller_words": ["0x4"], "controller_rate_words": []},
            "upscale": {"taau": ["0123abcd"], "gameplay": ["0x0123456789abcdef"], "network_keep": []},
            "hooks": {"gfx_clock": None, "present_poll": "0x80000004", "present_events": []},
            "npad_players": "auto", "account_users": 8, "video_catchup": True, "vk_parallel": None,
            "graphics_scale": 2, "romfs_needed": [[["a", "b"], "file"], [["c"], "dir"]], "notes": "made up",
        },
        "lift": {"patch_modules": ["zz_wide"], "code_patch_module": None, "env_prefixes": ["ZZ_"]},
        "host": ["host/*.c"], "features": ["fov"], "build_steps": ["prompts"], "saves_dir": "zz-saves",
        "license": "GPL-3.0-or-later", "comments": {"builds.0.main_base": "made up", "profile.gfx": "made up"},
    }


def mutated(path, value=KeyError):
    """good() with one field (a dotted path) set to value, or removed when value is KeyError"""
    m = copy.deepcopy(good())
    *parents, last = path.split(".")
    node = m
    for part in parents:
        node = node[int(part)] if isinstance(node, list) else node[part]
    if value is KeyError:
        del node[last]
    elif isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return m


# (each bad manifest, and what the refusal must name)
BAD = [
    (lambda: mutated("profile.notes"), "profile.notes is missing"),
    (lambda: mutated("builds.0.main_base"), "builds.0.main_base is missing"),
    (lambda: mutated("lift.code_patch_module"), "lift.code_patch_module is missing"),
    (lambda: mutated("profile.gfx.frame_limter", "0x10"), "profile.gfx.frame_limter is not a field"),
    (lambda: mutated("colour", "red"), "colour is not a field"),
    (lambda: mutated("format", 2), "format is 2"),
    (lambda: mutated("profile.account_users", True), "profile.account_users is true"),
    (lambda: mutated("profile.account_users", 9), "profile.account_users is 9"),
    (lambda: mutated("profile.npad_players", 0), "profile.npad_players is 0"),
    (lambda: mutated("profile.video_catchup", 1), "profile.video_catchup is 1"),
    (lambda: mutated("profile.graphics_scale", 3), "profile.graphics_scale is 3"),
    (lambda: mutated("profile.vk_parallel", "4"), "profile.vk_parallel is \"4\""),
    (lambda: mutated("profile.gfx.frame_limiter", 16), "profile.gfx.frame_limiter is 16"),
    (lambda: mutated("profile.gfx.frame_limiter", "0X10"), "profile.gfx.frame_limiter is \"0X10\""),
    (lambda: mutated("profile.hooks.present_events", ["80000004"]), "profile.hooks.present_events is"),
    (lambda: mutated("profile.upscale.taau", ["0x0123abcd"]), "profile.upscale.taau is"),
    (lambda: mutated("profile.upscale.gameplay", "0x1"), "profile.upscale.gameplay is"),
    (lambda: mutated("profile.upscale.gameplay", ["0x1" + "0" * 16]), "profile.upscale.gameplay is"),
    (lambda: mutated("profile.romfs_needed", [[["a"], "folder"]]), "profile.romfs_needed is"),
    (lambda: mutated("profile.romfs_needed", [[[], "file"]]), "profile.romfs_needed is"),
    (lambda: mutated("builds.0.build_id", "abcd1234"), "builds.0.build_id is \"abcd1234\""),
    (lambda: mutated("builds.0.version", -1), "builds.0.version is -1"),
    (lambda: mutated("builds", []), "builds must hold exactly one build"),
    (lambda: mutated("builds", good()["builds"] * 2), "builds must hold exactly one build"),
    (lambda: mutated("program_id", "0100000000000000x"), "program_id is"),
    (lambda: mutated("program_id", "01000000000000ab"), "program_id is"),
    (lambda: mutated("name", " "), "name is \" \""),
    (lambda: mutated("version", "1.0"), "version is \"1.0\""),
    (lambda: mutated("nx2pc", "3.0.0"), "nx2pc is \"3.0.0\""),
    (lambda: mutated("key", "ZZ"), "key is \"ZZ\""),
    (lambda: mutated("key", "none"), "key \"none\" is the empty profile's"),
    (lambda: mutated("lift.patch_modules", ["zz-wide"]), "lift.patch_modules is"),
    (lambda: mutated("lift.env_prefixes", ["zz_"]), "lift.env_prefixes is"),
    (lambda: mutated("saves_dir", "../saves"), "saves_dir is"),
    (lambda: mutated("license", "MIT"), "license is \"MIT\""),
    (lambda: mutated("build_steps", ["shaders"]), "build_steps is [\"shaders\"], not a list of prompts, pipeline_states"),
    (lambda: mutated("api", {"major": packs.NX2PC_PACK_API[0] + 1, "minor": 0}), "needs pack API"),
    (lambda: mutated("api.minor", packs.NX2PC_PACK_API[1] + 1), "needs pack API"),
    (lambda: mutated("api.minor", "1"), "api.minor is"),
    (lambda: mutated("profile", []), "profile is not an object"),
    (lambda: mutated("comments", {"profile.gfx.nope": "x"}), "comments.profile.gfx.nope names no field"),
    (lambda: mutated("comments", {"builds.1": "x"}), "comments.builds.1 names no field"),
    (lambda: mutated("comments", {"comments": "x"}), "comments.comments names no field"),
    (lambda: mutated("comments", {"key": ""}), "comments.key is not a non-empty string"),
]


def refusal(manifest, where="packs/zz/pack.json", key=None):
    try:
        packs.validate(manifest, where, key)
    except packs.PackError as e:
        return str(e)
    return None


def check_schema():
    check(refusal(good(), key="zz") is None, "a good manifest is refused: %s" % refusal(good(), key="zz"))
    minimal = good()
    for name in ("nx2pc", "host", "features", "build_steps", "saves_dir", "comments"):
        del minimal[name]
    del minimal["lift"]["env_prefixes"]
    check(refusal(minimal) is None, "a manifest without its optional fields is refused: %s" % refusal(minimal))
    for make, want in BAD:
        got = refusal(make())
        check(got is not None and got.startswith("packs/zz/pack.json: ") and want in got,
              "a bad manifest is not refused naming %r: %r" % (want, got))
    got = refusal(good(), key="yy")
    check(got and "key is 'zz', but the pack's folder is 'yy'" in got, "a key that is not its folder's: %r" % got)
    check(packs.api_ok({"major": 1, "minor": 2}, (1, 3)) and not packs.api_ok({"major": 1, "minor": 4}, (1, 3))
          and not packs.api_ok({"major": 0, "minor": 1}, (0, 2)) and packs.api_ok({"major": 0, "minor": 2}, (0, 2)),
          "the API rule: same major, the pack's minor at most the engine's, and from 0.x exactly")
    check(packs.hex_int("0x80020000") == 0x80020000 and packs.hex_int(None) is None, "hex_int")
    with tempfile.TemporaryDirectory(prefix="packs-") as tmp:
        root = Path(tmp)
        path = root / "zz" / "pack.json"
        path.parent.mkdir()
        path.write_text(json.dumps(good()).replace('"format": 1,', '"format": 1, "format": 1,'), encoding="utf-8")
        got = load_refusal(path, "zz")
        check(got and "the field 'format' twice" in got, "a field twice is not refused: %r" % got)
        path.write_text(json.dumps(good())[:-1], encoding="utf-8")
        got = load_refusal(path, "zz")
        check(got and "is not JSON" in got, "a file that is not JSON is not refused: %r" % got)
        got = load_refusal(root / "yy" / "pack.json", "yy")
        check(got and "cannot be read" in got, "a missing file is not refused: %r" % got)


def load_refusal(path, key):
    try:
        packs.load(str(path), key)
    except packs.PackError as e:
        return str(e)
    return None


def write(root, key, **changes):
    m = good()
    m["key"] = key
    m.update(changes)
    (root / key).mkdir()
    (root / key / "pack.json").write_text(json.dumps(m), encoding="utf-8")


def check_tree():
    tree = packs.tree()
    check(list(tree) == ["oot", "dkb", "mkw"], "the tree's packs are not oot, dkb, mkw in that order: %r" % list(tree))
    check(packs.DEFAULT == "oot" and game_profile.DEFAULT == packs.DEFAULT, "the default game is not Ocarina's pack")
    check([os.path.relpath(p, ROOT).replace(os.sep, "/") for p in packs.manifest_paths()] ==
          ["packs/dkb/pack.json", "packs/mkw/pack.json", "packs/oot/pack.json"], "manifest_paths")
    for key, m in tree.items():
        check(m["format"] == packs.FORMAT and packs.api_ok(m["api"]) and m["license"] == "GPL-3.0-or-later",
              "%s: not format 1, this engine's API or GPL-3.0-or-later" % key)
        for path in m.get("comments", {}):
            check(packs._resolve(m, path), "%s: comments.%s names no field" % (key, path))
    check("saves_dir" not in tree["dkb"] and "saves_dir" not in tree["mkw"] and tree["oot"]["saves_dir"] == "hookshot",
          "only Ocarina's pack keeps a saves folder of its own")
    with tempfile.TemporaryDirectory(prefix="packs-") as tmp:
        root = Path(tmp)
        write(root, "bb", builds=[dict(good()["builds"][0], build_id="bb" * 20)])
        write(root, "aa", builds=[dict(good()["builds"][0], build_id="aa" * 20)])
        write(root, "cc", builds=[dict(good()["builds"][0], build_id="cc" * 20)])
        check(list(packs.tree(str(root), order=("cc",))) == ["cc", "aa", "bb"],
              "an unlisted pack does not come after the listed ones, in key order")
        try:
            packs.tree(str(root), order=("cc", "dd"))
            check(False, "a listed pack that is missing is not refused")
        except packs.PackError as e:
            check("no pack for dd" in str(e), "a missing listed pack: %s" % e)
        write(root, "dd", builds=[dict(good()["builds"][0], build_id="aa" * 20)])
        try:
            packs.tree(str(root), order=())
            check(False, "two packs with one build are not refused")
        except packs.PackError as e:
            check("packs/aa and packs/dd both name build" in str(e), "two packs, one build: %s" % e)
        check(packs.tree(str(root / "nothing"), order=()) == {}, "no packs folder is no packs")


# ---- packs/ travels, and counts ----

def check_travels():
    appimage = (ROOT / "scripts" / "linux" / "appimage.sh").read_text(encoding="utf-8")
    check(re.search(r"^cp -a [^\n]*\bnx2\b[^\n]*\bpacks\b[^\n]*\"\$T/\"$", appimage, re.M),
          "scripts/linux/appimage.sh does not copy packs/ into the AppImage's tools")
    parts = (ROOT / "scripts" / "release_parts.py").read_text(encoding="utf-8")
    check(re.search(r'tracked\([^)]*"nx2", "packs"', parts), "scripts/release_parts.py does not package packs/")
    import version
    check("packs" in version.SOURCES and ".json" in version.SOURCE_EXT, "nx2/version.py's source stamp does not read packs/")
    import make_game
    sources = {os.path.abspath(f) for f in make_game.lift_sources()}
    manifests = {os.path.abspath(f) for f in packs.manifest_paths()}
    check(len(manifests) == 3 and manifests <= sources, "the lift's key does not hash every pack.json")
    check(str(ROOT / "nx2" / "packs.py") in sources, "the lift's key does not hash nx2/packs.py")


# ---- Ocarina's code in its pack (a2) ----

# Each of Ocarina's lift modules as the lift's key hashed it at a1 (main 9919310d, nx2/<name>): its name and its bytes' sha1
LIFT_A1 = {
    "nx2/oot_fov.py": "5a0e7d2798d058a06e0d33e111ec24b6a29dc9ea",
    "nx2/oot_jitter.py": "50522fc8f94a43762d362c98626944486243b56c",
    "nx2/oot_occlusion.py": "c3b9d80a430901dc724a201a4f31d2cf5e8a9b33",
    "nx2/oot_patches.py": "de0255d394b59de7f1aba4773f71cad9fa9c8918",
    "nx2/oot_wide.py": "79ba7a9d67e0985c7c005b1b4677e2a20a451ce4",
}
# The ones a later step changed on purpose, and what each is hashed as now: the key changed with them, once (a3: a comment
# naming where hos_wide_cover went, in the release that also brought pack API 0.3)
LIFT_SINCE_A1 = {
    "nx2/oot_wide.py": ("a3", "7e2c6b444a96540a62e67f5a6eaca75c56f1b033"),
}


def check_lift_key():
    import make_game
    sources = make_game.lift_sources()
    names = [make_game.lift_source_name(f) for f in sources]
    check(names == sorted(names) and len(set(names)) == len(names),
          "the lift's key does not hash its files in their names' order, each name once")
    lift = ROOT / "packs" / "oot" / "lift"
    for name, sha in LIFT_A1.items():
        sha = LIFT_SINCE_A1.get(name, (None, sha))[1]
        path = lift / name.split("/")[-1]
        check(str(path) in sources, "the lift's key does not hash %s" % path.relative_to(ROOT))
        check(make_game.lift_source_name(str(path)) == name, "%s is not hashed as %s" % (path.relative_to(ROOT), name))
        check(make_game.lift_source_digest(str(path)).hex() == sha,
              "%s is not hashed as the bytes %s had at a1 (or since, LIFT_SINCE_A1): Ocarina's lift key would change"
              % (path.relative_to(ROOT), name))
    check(not any(n.startswith("packs/oot/lift/") for n in names), "a pack's lift/ file is hashed under its new name")
    check(not (ROOT / "nx2" / "oot_patches.py").exists(), "nx2/oot_patches.py is still there beside the pack's")
    # (a pack.json: its lift fields only)
    manifest = json.loads((ROOT / "packs" / "oot" / "pack.json").read_text(encoding="utf-8"))
    view = packs.lift_view(manifest)
    for field, value in (("comments", {}), ("build_steps", []), ("features", ["fov"]), ("host", ["host/*.c"])):
        other = dict(manifest, **{field: value})
        check(packs.lift_view(other) == view, "a change to pack.json's %s changes the lift's key" % field)
    other = copy.deepcopy(manifest)
    other["profile"]["gfx"]["frame_limiter"] = "0xfd"
    check(packs.lift_view(other) != view, "a change to a profile field leaves the lift's key")
    other = copy.deepcopy(manifest)
    other["lift"]["patch_modules"] = other["lift"]["patch_modules"][:-1]
    check(packs.lift_view(other) != view, "a change to the lift's modules leaves the lift's key")
    check(make_game.lift_source_digest(str(ROOT / "packs" / "oot" / "pack.json")) ==
          hashlib.sha1(view, usedforsecurity=False).digest(), "the key does not hash pack.json's lift view")
    check(game_profile.env_prefixes() == ("OOT_",), "the packs' env prefixes are not Ocarina's OOT_: %r" % (game_profile.env_prefixes(),))
    src = (ROOT / "nx2" / "make_game.py").read_text(encoding="utf-8")
    body = src[src.index("def lift_key("):src.index("def gen_print(")]
    check('"OOT_"' not in body and "game_profile.env_prefixes()" in body, "lift_key names Ocarina's OOT_ itself")


def check_pack_folders():
    oot = game_profile.GAMES["oot"]
    lift = ROOT / "packs" / "oot" / "lift"
    for name in oot.patch_modules + (oot.code_patch_module,):
        check(game_profile.module_dir(name, oot) == str(lift), "%s is not found in Ocarina's pack's lift/" % name)
        check(game_profile.module_dir(name) == str(lift), "%s is not found in its pack without a profile" % name)
        check(Path(game_profile.load_module(name, oot).__file__).resolve() == (lift / (name + ".py")).resolve(),
              "load_module(%s) is not the pack's file" % name)
    check(game_profile.module_dir("no_such_module") is None, "a module no pack holds has a folder")
    check(game_profile.lift_dirs() == [str(lift)], "the packs' lift/ folders are not Ocarina's alone: %r" % game_profile.lift_dirs())
    # (module names: one pack's each, and none of the engine's, which the lift's path also holds)
    engine = {p.stem for d in (ROOT / "nx2", ROOT / "third_party" / "a64-kit" / "lift", ROOT / "third_party" / "a64-kit" / "tools",
                               ROOT / "third_party" / "a64-kit" / "rt", ROOT / "third_party" / "a64-kit" / "hos") for p in d.glob("*.py")}
    seen = {}
    for d in game_profile.lift_dirs() + [str(p) for p in sorted((ROOT / "packs").glob("*/" + packs.TOOLS_DIR))]:
        for p in Path(d).glob("*.py"):
            check(p.stem not in engine, "%s has the name of an engine module" % p.relative_to(ROOT))
            check(p.stem not in seen, "%s and %s share a module name" % (p.relative_to(ROOT), seen.get(p.stem)))
            seen[p.stem] = p.relative_to(ROOT)
    steps = {k: game_profile.build_steps(p) for k, p in game_profile.GAMES.items()}
    check(steps == {"oot": ("prompts", "pipeline_states"), "dkb": (), "mkw": (), "none": ()},
          "the build steps are not Ocarina's prompts and pipeline list alone: %r" % steps)
    check(game_profile.pack_folder(oot, packs.TOOLS_DIR) == str(ROOT / "packs" / "oot" / "tools")
          and (ROOT / "packs" / "oot" / "tools" / "prompt_assets.py").is_file(), "the prompts step's tool is not in Ocarina's pack")
    check(game_profile.pack_folder(game_profile.GAMES["none"], packs.TOOLS_DIR) is None, "the empty profile has a pack folder")
    import pipeline_states
    check(pipeline_states.shipping_packs() == ["oot"] and Path(pipeline_states.sources_dir()) ==
          ROOT / "packs" / "oot" / "assets" / "pipeline_states" and pipeline_states.sources(pipeline_states.sources_dir()),
          "the pipeline lists are not Ocarina's pack's alone")
    check(not (ROOT / "assets" / "pipeline_states").exists(), "assets/pipeline_states/ is still there beside the pack's")


# ---- a game's runtime C (a3) ----

def check_pack_host():
    import build
    header = (ROOT / "host" / "nx2_pack.h").read_text(encoding="utf-8")
    api = tuple(int(re.search(r"#define NX2_PACK_API_%s (\d+)" % n, header).group(1)) for n in ("MAJOR", "MINOR"))
    check(api == packs.NX2PC_PACK_API, "host/nx2_pack.h's interface is %d.%d, nx2/packs.py's %d.%d" % (api + packs.NX2PC_PACK_API))
    with tempfile.TemporaryDirectory(prefix="pack-host-") as tmp:
        for key, want in (("oot", sorted((ROOT / "packs" / "oot" / "host").glob("*.c"))),
                          ("mkw", sorted((ROOT / "packs" / "mkw" / "host").glob("*.c"))),
                          ("dkb", [ROOT / "host" / "game_none.c"]), ("none", [ROOT / "host" / "game_none.c"])):
            folder = Path(tmp, key)
            folder.mkdir()
            game_profile.write_header(str(folder), game_profile.GAMES[key])
            got_key, got = build.pack_host(str(folder))
            check(got_key == key and [Path(f) for f in got] == [w.resolve() for w in want] and want,
                  "%s's runtime C is %r, not its pack's %r" % (key, [os.path.relpath(f, ROOT) for f in got],
                                                               [os.path.relpath(f, ROOT) for f in want]))
    check(len(list((ROOT / "packs" / "oot" / "host").glob("*.c"))) >= 11, "Ocarina's pack has not its runtime C")
    # (none of it left in host/)
    shared = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8", errors="replace")
              for p in sorted((ROOT / "host").rglob("*")) if p.suffix in (".c", ".h")}
    check(not [n for n in shared if Path(n).name.startswith("oot_")], "host/ still has Ocarina's files")
    for name in ("hos_oot_teleport_ask", "hos_oot_opening(", "oot_update_player_cheats", "hos_wide_cover(void) {",
                 "oot_destinations[]", "occlusion_switch", "HOS_OOT_PROTECT_HEARTS", "hos_oot_live_action",
                 "hos_oot_player_free", "hos_oot_flight_shortcut_ready", "oot_cheat_items.h"):
        where = [n for n, text in shared.items() if name in game_profile_code(text)]
        check(not where, "%s is in the shared runtime: %s" % (name, where))
    for literal in ("hos_game_is_mkw", "0x806bce28", "0x825c6844", "0x837de940", "HOS_MKW_INTERVAL", "hos_mkw_pacer",
                    "d00e4f399e411d0613aa8d26b1c02fef"):
        where = [n for n, text in shared.items() if literal in game_profile_code(text)]
        check(not where, "Mario Kart World 1.8.0's %s is in the shared runtime: %s" % (literal, where))
    mkw = (ROOT / "packs" / "mkw" / "host" / "mkw_pacer.c").read_text(encoding="utf-8")
    for literal in ("0x806bce28", "0x825c6844", "0x837de940", "HOS_MKW_INTERVAL", "d00e4f399e411d0613aa8d26b1c02fef"):
        check(literal in mkw, "Mario Kart World's pack lost %s" % literal)
    # (the pacer is noted only in 1.8.0, behind the check, and the present hooks do nothing without it)
    check(mkw.count("mkw_180() &&") == 2 and "if (hos_mkw_pacer && forced" in mkw and "if (pacer && freq)" in mkw,
          "Mario Kart World 1.8.0's hooks are not behind its build check")
    # (every pack's table and game_none.c's: the version and size, and only the table's own members)
    members = re.findall(r"\(\*(\w+)\)\(", header[header.index("typedef struct NX2PackHost"):header.index("} NX2PackHost;")])
    check(members[:1] == ["start"] and len(members) >= 10, "NX2PackHost's hooks: %r" % members)
    tables = [p for p in sorted((ROOT / "packs").glob("*/host/*.c")) if "static const NX2PackHost" in p.read_text(encoding="utf-8")]
    check([p.relative_to(ROOT).as_posix() for p in tables] == ["packs/mkw/host/mkw_pacer.c", "packs/oot/host/oot_pack.c"],
          "the packs' tables are not Mario Kart World's and Ocarina's: %r" % tables)
    for path in [ROOT / "host" / "game_none.c"] + tables:
        text = path.read_text(encoding="utf-8")
        check(".version = NX2_PACK_API_MAJOR << 16 | NX2_PACK_API_MINOR," in text and ".size = sizeof(NX2PackHost)," in text
              and "const NX2PackHost *nx2_pack_host(void)" in text, "%s's table has no version, size or nx2_pack_host" % path.name)
        used = set(re.findall(r"^\s*\.(\w+) = ", text[text.index("static const NX2PackHost"):], re.M))
        check(used <= set(members) | {"version", "size", "key"}, "%s sets a member the table has not: %r" % (path.name, used))
    check((ROOT / "packs" / "mkw" / "host" / "mkw_pacer.c").read_text(encoding="utf-8").count("const NX2PackHost *nx2_pack_host(void)") == 1,
          "Mario Kart World's pack has no table")


def check_pack_object_key():
    """A pack object's key follows every header its C includes: the Inventory's items (gpu/vk/oot_cheat_items.h, which the
    menu and Ocarina's cheats both read) changed, oot_cheats.o is compiled again with vk_menu.cpp"""
    import build
    key, srcs = build.pack_host_files("oot", packs.tree()["oot"])
    dirs = sorted({os.path.dirname(f) for f in srcs}) + [build.HOST, build.GAME]
    headers = [os.path.realpath(h) for h in build.included_headers(srcs, dirs)]
    items = os.path.realpath(ROOT / "gpu" / "vk" / "oot_cheat_items.h")
    check(items in headers, "Ocarina's pack's C does not see gpu/vk/oot_cheat_items.h: %r" % headers[:8])
    check(os.path.realpath(ROOT / "host" / "nx2_pack.h") in headers and os.path.realpath(ROOT / "gpu" / "vk" / "vk_api.h") in headers,
          "the pack's headers miss nx2_pack.h or vk_api.h (included through it)")
    paths = build.included_headers(srcs, dirs)
    before = build.headers_bytes(paths)
    after = build.headers_bytes(paths, read=lambda path: open(path, "rb").read() + (b"\n/* an item */\n" if os.path.realpath(path) == items else b""))
    cheats = next(f for f in srcs if f.endswith("oot_cheats.c"))
    check(before != after and build.object_key(cheats, ["-O2"], before) != build.object_key(cheats, ["-O2"], after),
          "a change to gpu/vk/oot_cheat_items.h leaves the key of Ocarina's oot_cheats.o")
    check(build.show_path(cheats) == "packs/oot/host/oot_cheats.c", "build.show_path: %r" % build.show_path(cheats))
    hosts = dict(build.all_pack_hosts())
    check(sorted(hosts) == ["mkw", "none", "oot"] and hosts["none"][0].endswith("game_none.c")
          and len(hosts["oot"]) == len(srcs), "--host-only does not compile every pack's C and game_none.c: %r" % sorted(hosts))
    yml = (ROOT / ".github" / "workflows" / "windows-builder.yml").read_text(encoding="utf-8")
    job = yml[yml.index("\n  runtime:\n"):]
    job = job[:job.index("\n  dav1d:\n")]
    check("nx2/build.py --standalone --host-only" in job and "runs-on: windows-2022" in job and "release_parts.py part" in job,
          "windows-builder.yml has no job compiling the runtime's and the packs' C for Windows")
    check("needs: [package, vulkan, runtime]" in yml, "a Windows release does not wait for the runtime's C to compile")


def game_profile_code(text):
    """C without its comments: a comment may say where a thing went"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


# ---- the Builders' copies ----

def check_builders():
    games = (ROOT / "builder" / "HookshotBuilder" / "Games.cs").read_text(encoding="utf-8")
    table = games[games.index("Profiles =\n"):games.index("};", games.index("Profiles =\n"))]
    rows = re.findall(r'\("([a-z0-9]+)", "([^"]+)", "([0-9a-f]+)"\)', table)
    want = [(k, m["name"], m["builds"][0]["build_id"]) for k, m in packs.tree().items()]
    check(rows == want, "Games.cs's Profiles are not the packs' (key, name, build id, in order): %r" % rows)
    default = re.search(r'public const string DefaultKey = "([a-z0-9]+)";', games)
    check(default and default.group(1) == packs.DEFAULT, "Games.cs's DefaultKey is not the packs' default")
    setup = (ROOT / "builder" / "setup" / "setup.c").read_text(encoding="utf-8")
    default = re.search(r'#define HOOKSHOT_DEFAULT_GAME "([a-z0-9]+)"', setup)
    check(default and default.group(1) == packs.DEFAULT, "setup.c's HOOKSHOT_DEFAULT_GAME is not the packs' default")
    core = (ROOT / "builder" / "py" / "hookshot_builder" / "core.py").read_text(encoding="utf-8")
    table = core[core.index("PROFILES = (\n"):core.index("\n)\n", core.index("PROFILES = (\n"))]
    rows = re.findall(r'\("([a-z0-9]+)", "([^"]+)", "([0-9a-f]+)"\)', table)
    check(rows == want, "the Qt Builder's PROFILES (core.py) are not the packs' (key, name, build id, in order): %r" % rows)
    default = re.search(r'^DEFAULT_KEY = "([a-z0-9]+)"$', core, re.M)
    check(default and default.group(1) == packs.DEFAULT, "the Qt Builder's DEFAULT_KEY is not the packs' default")


def main():
    check_frozen()
    check_schema()
    check_tree()
    check_travels()
    check_lift_key()
    check_pack_folders()
    check_pack_host()
    check_pack_object_key()
    check_builders()
    for f in FAILURES:
        print("FAIL: " + f)
    if FAILURES:
        sys.exit(1)
    print("PASS: game packs: format 1 checked, the profiles and headers as before, packs/ shipped and in the lift key, Ocarina's "
          "modules hashed as at a1, the packs' folders and build steps")


if __name__ == "__main__":
    main()
