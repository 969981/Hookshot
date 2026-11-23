"""Game packs: each game's identity and profile data as a manifest, packs/<key>/pack.json, that the engine reads without
running any of the game's code (docs/game-packs.md).

The packs are the tree's own (packs/<key>/), and this module only loads and checks them (steps a1 and a2 of that design).
nx2/game_profile.py builds its profiles (GAMES) from what it returns, so a pack's data is the one copy of a game's profile.
Beside its pack.json a pack holds, as section 2.1 lays it out:

    lift/      the Python the lifter imports: the lift patch modules and the code patch module its "lift" names
               (game_profile.load_module looks there)
    tools/     what its build_steps run: "prompts" runs tools/prompt_assets.py
    assets/    data the build ships: "pipeline_states" merges assets/pipeline_states/ into the folder's shipped list

Fetching, pinning and the cache (packs.lock.json, sync, path, verify) are step b1.

A manifest is refused, with the file and the field named, when it is not what format 1 says (FIELDS below): a field
missing, one this format does not know (a typo is an error, not a field quietly ignored), a value of the wrong kind, or
an interface version this engine does not have. Addresses and offsets are hex strings ("0x80020000"), as the docs quote
them; an unknown value is null, never a plausible-looking number (nx2/game_profile.py says why).

    python -I nx2/packs.py            # the tree's packs, checked, one line each; exit 1 on the first bad one
"""
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PACKS_DIR = os.path.normpath(os.path.join(HERE, "..", "packs"))
MANIFEST = "pack.json"

FORMAT = 1
# The engine/pack interface (docs/game-packs.md section 4): a pack's "api" says the major it was written for and the
# least minor it needs. During the migration the major is 0, where any minor may break, so a 0.x pack needs this minor
# exactly; from 1.0 a pack works with any engine of its major whose minor is at least its own.
NX2PC_PACK_API = (0, 3)   # (0.2: lift/, tools/ and assets/ read from the pack, build_steps; 0.3: host/nx2_pack.h, host/)

# Until packs.lock.json (b1) holds them: the profile order (make_game.py's known builds, --library, the launcher's
# tiles) and the default game, whose folder takes no suffix (game_profile.DEFAULT, builder/setup/setup.c
# HOOKSHOT_DEFAULT_GAME, builder/HookshotBuilder/Games.cs DefaultKey; tests/test_game_packs.py holds them the same).
# A pack in packs/ that is not listed here comes after these, in key order.
TREE_ORDER = ("oot", "dkb", "mkw")
DEFAULT = "oot"


class PackError(ValueError):
    """A manifest that is not format 1's, with the file and the field it is about in the message"""


# ---- what format 1 holds ----
#
# A field is (name, kind, required). Kinds are checked by _ok below; ("object", fields) is a nested object with its
# own fields. Every field of the profile is required, null or empty where it is unknown, so a manifest says what it does
# not know rather than leaving it to a default.

KEY = re.compile(r"[a-z0-9]{1,16}")                 # (the folder suffix and HOOKSHOT_GAME; setup.c's play_target rule)
HEX = re.compile(r"0x[0-9a-f]{1,16}")               # an address, an offset, a 64-bit key as a number
KEY_PREFIX = re.compile(r"[0-9a-f]{1,16}")          # a program key prefix, as the renderer's RX_VK_* overrides take it
BUILD_ID = re.compile(r"[0-9a-f]{40}")              # main's whole build id
PROGRAM_ID = re.compile(r"[0-9A-F]{16}")            # as nx2/dump.py reports it
SEMVER = re.compile(r"\d+\.\d+\.\d+")
RANGE = re.compile(r">=\d+\.\d+\.\d+")
MODULE = re.compile(r"[a-z_][a-z0-9_]*")            # a Python module name the lifter imports
ENV_PREFIX = re.compile(r"[A-Z][A-Z0-9]*_")
NAME = re.compile(r"[a-z][a-z0-9_]*")               # a feature
SAVES = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
LICENSES = ("GPL-3.0-or-later",)
# The optional steps of nx2/make_game.py a pack takes part in (pack.json build_steps): the button prompts of other
# controllers, drawn by the pack's tools/prompt_assets.py, and the pipeline state list made from its assets/pipeline_states/
BUILD_STEPS = ("prompts", "pipeline_states")
# A pack's folders the engine reads (section 2.1)
LIFT_DIR, TOOLS_DIR, ASSETS_DIR = "lift", "tools", "assets"
# The fields of a manifest that the lift does not read: the lift's cache key takes the rest (lift_view), so an edit to these
# (a comment, a menu feature, a build step) does not translate the game again
NOT_LIFTED = ("comments", "features", "host", "build_steps", "short_name", "license", "nx2pc")

BUILD = (("build_id", ("re", BUILD_ID), True), ("version", ("int", 0, None), True), ("version_name", "str", True),
         ("main_base", ("re", HEX), True))
GFX = (("frame_limiter", ("hex?",), True), ("nominal_fps", ("hex?",), True), ("vrr_mode", ("hex?",), True),
       ("vrr_gate", ("hex?",), True), ("controller", ("hex?",), True),
       ("controller_words", ("list", ("re", HEX)), True), ("controller_rate_words", ("list", ("re", HEX)), True))
UPSCALE = (("taau", ("list", ("re", KEY_PREFIX)), True), ("gameplay", ("list", ("re", HEX)), True),
           ("network_keep", ("list", ("re", KEY_PREFIX)), True))
HOOKS = (("gfx_clock", ("hex?",), True), ("present_poll", ("hex?",), True),
         ("present_events", ("list", ("re", HEX)), True))
PROFILE = (("gfx", ("object", GFX), True), ("upscale", ("object", UPSCALE), True), ("hooks", ("object", HOOKS), True),
           ("npad_players", ("players",), True), ("account_users", ("int", 1, 8), True),
           ("video_catchup", "bool", True), ("vk_parallel", ("int?", 1, 64), True),
           ("graphics_scale", ("int?", 0, 2), True), ("romfs_needed", ("romfs",), True), ("notes", "str", True))
LIFT = (("patch_modules", ("list", ("re", MODULE)), True), ("code_patch_module", ("re?", MODULE), True),
        ("env_prefixes", ("list", ("re", ENV_PREFIX)), False))
API = (("major", ("int", 0, None), True), ("minor", ("int", 0, None), True))
FIELDS = (("format", ("int", 1, 1), True), ("key", ("re", KEY), True), ("name", "str", True),
          ("short_name", "str", True), ("version", ("re", SEMVER), True), ("api", ("object", API), True),
          ("nx2pc", ("re", RANGE), False), ("released", "bool", True), ("program_id", ("re", PROGRAM_ID), True),
          ("builds", ("builds",), True), ("profile", ("object", PROFILE), True), ("lift", ("object", LIFT), True),
          ("host", ("list", "str"), False), ("features", ("list", ("re", NAME)), False),
          ("build_steps", ("list", ("one", BUILD_STEPS)), False), ("saves_dir", ("re", SAVES), False),
          ("license", ("one", LICENSES), True), ("comments", ("comments",), False))


def _kind_name(kind):
    if kind == "str":
        return "a non-empty string"
    if kind == "bool":
        return "true or false"
    tag = kind[0]
    if tag in ("re", "re?"):
        return "a string like %s%s" % (kind[1].pattern, " or null" if tag == "re?" else "")
    if tag == "hex?":
        return "a hex string like 0x1f, or null"
    if tag in ("int", "int?"):
        lo, hi = kind[1], kind[2]
        if lo == hi:
            return "%d" % lo
        span = ("%d..%d" % (lo, hi) if hi is not None else "%d or more" % lo)
        return "a whole number %s%s" % (span, " or null" if tag == "int?" else "")
    if tag == "list":
        item = kind[1]
        if item == "str":
            return "a list of non-empty strings"
        if item[0] == "one":
            return "a list of %s" % ", ".join(item[1])
        return "a list of strings like %s" % item[1].pattern
    if tag == "one":
        return "one of %s" % ", ".join(kind[1])
    if tag == "object":
        return "an object"
    if tag == "players":
        return "a whole number 1..8 or \"auto\""
    if tag == "romfs":
        return "a list of [[name, ...], \"file\" or \"dir\"]"
    if tag == "builds":
        return "a list of one build"
    if tag == "comments":
        return "an object of field paths to strings"
    raise AssertionError(kind)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _ok(v, kind):
    """Whether v is of kind (not an object, builds or comments: _object and validate check those)"""
    if kind == "str":
        return isinstance(v, str) and v.strip() != ""
    if kind == "bool":
        return isinstance(v, bool)
    tag = kind[0]
    if tag == "re":
        return isinstance(v, str) and kind[1].fullmatch(v) is not None
    if tag == "re?":
        return v is None or _ok(v, ("re", kind[1]))
    if tag == "hex?":
        return v is None or _ok(v, ("re", HEX))
    if tag in ("int", "int?"):
        if v is None:
            return tag == "int?"
        return _is_int(v) and v >= kind[1] and (kind[2] is None or v <= kind[2])
    if tag == "list":
        return isinstance(v, list) and all(_ok(x, kind[1]) for x in v)
    if tag == "one":
        return v in kind[1]
    if tag == "players":
        return v == "auto" or (_is_int(v) and 1 <= v <= 8)
    if tag == "romfs":
        return isinstance(v, list) and all(
            isinstance(e, list) and len(e) == 2 and isinstance(e[0], list) and e[0]
            and all(_ok(n, "str") for n in e[0]) and e[1] in ("file", "dir") for e in v)
    raise AssertionError(kind)


def _object(obj, fields, where, path):
    if not isinstance(obj, dict):
        raise PackError("%s: %s is not an object" % (where, path or "the manifest"))
    known = [f[0] for f in fields]
    for name in obj:
        if name not in known:
            raise PackError("%s: %s is not a field of format %d (have %s)"
                            % (where, _join(path, name), FORMAT, ", ".join(known)))
    for name, kind, required in fields:
        here = _join(path, name)
        if name not in obj:
            if required:
                raise PackError("%s: %s is missing" % (where, here))
            continue
        v = obj[name]
        if isinstance(kind, tuple) and kind[0] == "object":
            _object(v, kind[1], where, here)
        elif kind == ("builds",):
            if not isinstance(v, list) or len(v) != 1:
                raise PackError("%s: %s must hold exactly one build (format %d: one profile for one build)"
                                % (where, here, FORMAT))
            for i, b in enumerate(v):
                _object(b, BUILD, where, "%s.%d" % (here, i))
        elif kind == ("comments",):
            pass   # (checked once the rest is known good: its paths must name fields)
        elif not _ok(v, kind):
            raise PackError("%s: %s is %s, not %s" % (where, here, json.dumps(v)[:80], _kind_name(kind)))


def _join(path, name):
    return "%s.%s" % (path, name) if path else name


def _resolve(manifest, path):
    node = manifest
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return False
    return True


def api_ok(api, engine=NX2PC_PACK_API):
    """Whether this engine has the interface a pack's api asks for (docs/game-packs.md section 4)"""
    major, minor = api["major"], api["minor"]
    if major != engine[0]:
        return False
    return minor == engine[1] if major == 0 else minor <= engine[1]


def validate(manifest, where=MANIFEST, key=None):
    """manifest (a pack.json, loaded) checked against format 1; returns it, or raises PackError naming where and the
    field. key: the folder it was found in, which the manifest's key must be."""
    _object(manifest, FIELDS, where, "")
    if key is not None and manifest["key"] != key:
        raise PackError("%s: key is %r, but the pack's folder is %r" % (where, manifest["key"], key))
    if manifest["key"] == "none":
        raise PackError("%s: key \"none\" is the empty profile's, for a build no pack knows" % where)
    if not api_ok(manifest["api"]):
        raise PackError("%s: needs pack API %d.%d; this engine has %d.%d"
                        % ((where, manifest["api"]["major"], manifest["api"]["minor"]) + NX2PC_PACK_API))
    comments = manifest.get("comments", {})
    if not isinstance(comments, dict):
        raise PackError("%s: comments is not an object" % where)
    for path, text in comments.items():
        if path == "comments" or path.startswith("comments.") or not _resolve(manifest, path):
            raise PackError("%s: comments.%s names no field of this manifest" % (where, path))
        if not _ok(text, "str"):
            raise PackError("%s: comments.%s is not a non-empty string" % (where, path))
    return manifest


def _no_duplicates(pairs):
    seen = collections.OrderedDict()
    for k, v in pairs:
        if k in seen:
            raise ValueError("the field %r twice" % k)
        seen[k] = v
    return seen


def load(path, key=None):
    """One manifest from its file, checked (validate). A file that is not JSON, or holds one field twice, is refused
    too: JSON lets the second silently win."""
    top = os.path.dirname(PACKS_DIR) + os.sep
    where = os.path.abspath(path)
    where = where[len(top):].replace(os.sep, "/") if where.startswith(top) else path   # (packs/oot/pack.json)
    try:
        with open(path, encoding="utf-8") as f:
            manifest = json.load(f, object_pairs_hook=_no_duplicates)
    except OSError as e:
        raise PackError("%s: cannot be read (%s)" % (where, e.strerror or e))
    except ValueError as e:
        raise PackError("%s: is not JSON this format reads (%s)" % (where, e))
    return validate(manifest, where, key)


def manifest_paths(root=PACKS_DIR):
    """Every packs/<key>/pack.json, sorted by key (nx2/make_game.py lift_sources hashes them: a profile change lifts again)"""
    if not os.path.isdir(root):
        return []
    return [os.path.join(root, d, MANIFEST) for d in sorted(os.listdir(root))
            if os.path.isfile(os.path.join(root, d, MANIFEST))]


def tree(root=PACKS_DIR, order=TREE_ORDER):
    """The tree's packs as {key: manifest}, in the profile order: order's keys first, then any other in key order. A
    listed key with no pack, or two packs with one build, is an error."""
    found = collections.OrderedDict()
    for path in manifest_paths(root):
        key = os.path.basename(os.path.dirname(path))
        found[key] = load(path, key)
    missing = [k for k in order if k not in found]
    if missing:
        raise PackError("%s: no pack for %s, which the profile order lists (nx2/packs.py TREE_ORDER)"
                        % (root, ", ".join(missing)))
    keys = list(order) + sorted(k for k in found if k not in order)
    packs = collections.OrderedDict((k, found[k]) for k in keys)
    builds = {}
    for k, m in packs.items():
        for b in m["builds"]:
            other = builds.setdefault(b["build_id"], k)
            if other != k:
                raise PackError("packs/%s and packs/%s both name build %s" % (other, k, b["build_id"]))
    return packs


def folder(key, name, root=PACKS_DIR):
    """A pack's folder (LIFT_DIR, TOOLS_DIR or ASSETS_DIR): packs/<key>/<name>, there or not"""
    return os.path.join(root, key, name)


def lift_view(manifest):
    """What of a manifest the lift reads, as the bytes its cache key hashes: every field but NOT_LIFTED, as sorted JSON"""
    view = {k: v for k, v in manifest.items() if k not in NOT_LIFTED}
    return json.dumps(view, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def hex_int(s):
    """A manifest's hex string as the number, None as None"""
    return None if s is None else int(s, 16)


def main(argv=None):
    try:
        packs = tree()
    except PackError as e:
        print("bad pack: %s" % e)
        return 1
    for key, m in packs.items():
        b = m["builds"][0]
        print("%s%-4s %s %s (format %d, api %d.%d): build %s, %s%s" % (
            "* " if key == DEFAULT else "  ", key, m["short_name"], m["version"], m["format"], m["api"]["major"],
            m["api"]["minor"], b["build_id"][:8], b["version_name"], "" if m["released"] else ", not released"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
