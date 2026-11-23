"""The game profile: the one place that says which game a build is for, and which addresses, offsets and keys are
that game's rather than the engine's.

The lifter, the Horizon runtime and the Vulkan back end are general; a handful of things are not, and before this file
they were written as literals wherever they were needed: patch modules for one build of one game, offsets into one
game's graphics module, the return addresses the runtime recognised that game's calls by, one game's shader program
keys, and the one build make_game.py accepted. This is the seam the engine's forks for other games (Lakitu: Mario Kart
World, Dragoon: Kirby Air Riders) built and converged on, so that a second game is a profile and its own patch modules,
not an edit to the engine (docs/one-engine.md):

  - the game is chosen by the build id of the dump's main (for_build), as the runtime's MKW 1.8.0 check (now
    packs/mkw/host/mkw_pacer.c) always did; HOOKSHOT_GAME=<key> only overrides it, and a build no profile knows gets NONE, which patches nothing;
  - the return addresses the runtime recognises are the profile's (Hooks), and are recognised only in the build the
    profile is for (host/hos_main.c hos_game_profile_live);
  - the renderer is built once for every game, so it is handed the game's program keys at run time (HOOKSHOT_PROFILE_*);
  - every profile's patch modules are roots of the lift's cache key (nx2/make_game.py lift_sources), and load_module is
    the one place they are imported by name.

Each game's profile is its game pack's manifest, packs/<key>/pack.json (nx2/packs.py loads and checks it;
docs/game-packs.md): this file holds the Profile class, the empty profile NONE, the choice of profile and the C view, and
builds GAMES from the packs. A game's values are edited in its pack.json, then the header is emitted as below.

What belongs in a profile: the game's identity (program id, version, main's build id) as nx2/dump.py reports it; the
base of its main in the lifted image; which lift patch modules and which code patch module apply; the offsets into its
graphics module the frame pacing reads; the return addresses the runtime recognises; the shader program keys the
renderer treats specially; its default player count. What does not: anything true of every Switch 2 title, and the C
text a patch writes, which lives with the patch.

An unknown value is None, never a plausible-looking number. None switches its feature off: the runtime never captures
a graphics module it has no return address for and skips a pacing field it has no offset for, saying so once; a game
with no patch modules is lifted without patches. A wrong address, by contrast, is silent corruption in someone's game.
Every lift patch module also checks the instruction word at each of its sites and leaves a site out when it differs,
so a profile aimed at the wrong build turns its patches off rather than writing C after the wrong instruction.

Which profile: HOOKSHOT_GAME=<key> if set; else the profile whose build id the dump's main has (for_build); else, with
no build id to go by (a build or a check without a dump), DEFAULT. A dump whose build no profile knows gets NONE, which
patches nothing and hooks nothing: lifting an unknown game must not apply Ocarina's patches to it.

    python -I nx2/game_profile.py                       # what the profiles hold
    python -I nx2/game_profile.py --emit-header         # rewrite game/game_profile.h (DEFAULT's) from its pack
    python -I nx2/game_profile.py --check-header        # the committed header still matches (tests/test_game_profile.py)
    python -I nx2/game_profile.py --emit-header --game oot --out <dir>   # one game's header, as a lift writes it
"""
import argparse
import collections
import importlib
import inspect
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)   # (python -I leaves the script folder off)
import packs  # noqa: E402  (the game packs the profiles are built from)

HEADER = os.path.join(HERE, "..", "game", "game_profile.h")

# The project's own name, which is not game data: it names the release files (scripts/product_name.sh). A fork's
# rename is this one line.
PROJECT = "Hookshot"

# The offsets into the game's graphics module that host/nn_vi.c reads.
Gfx = collections.namedtuple("Gfx", "frame_limiter nominal_fps vrr_mode vrr_gate controller controller_words "
                                    "controller_rate_words")
EMPTY_GFX = Gfx(frame_limiter=None, nominal_fps=None, vrr_mode=None, vrr_gate=None, controller=None,
                controller_words=(), controller_rate_words=())

# The shader program keys the renderer treats specially. A key is the 64-bit hash of a program's
# SASS, so it is per game and per build; the renderer takes prefixes, as its RX_VK_* overrides do.
#   taau:         programs the compute stand-in replaces (gpu/vk/vk_draw.cpp taau_key, RX_VK_TAAU_KEYS)
#   gameplay:     networks treated as the game's own upscale, never dropped when the movie networks are slow
#   network_keep: cooperative-matrix programs kept on cards that run them in software (gpu/vk/vk_shader.cpp)
Upscale = collections.namedtuple("Upscale", "taau gameplay network_keep")
EMPTY_UPSCALE = Upscale(taau=(), gameplay=(), network_keep=())

# Return addresses the runtime recognises a call of the game's by. Each is the address after a bl in the
# game's main, so it is one build's and nothing else's: on another game the same number is some unrelated call.
#   gfx_clock:      after the graphics module's frame-end GetSystemTick; x19 there is the module (host/hos_thread.c).
#                   Without it no module is captured and nn_vi.c's real-time step and frame-limiter work stay off.
#   present_poll:   after the SleepThread of the present's GPU-event poll (HOS_PRESENT_POLL, HOS_SLEEP_LOG, and the
#                   frame-time breakdown's poll share: host/hos_thread.c)
#   present_events: the nvnEventGetValue calls of that poll (HOS_PRESENT_NOWAIT: host/nvn_native.c)
Hooks = collections.namedtuple("Hooks", "gfx_clock present_poll present_events")
EMPTY_HOOKS = Hooks(gfx_clock=None, present_poll=None, present_events=())


class Profile(object):
    """One game. Everything optional is None or empty when the profile does not know it yet."""

    def __init__(self, key, game, program_id=None, version=None, build_id=None, main_base=None,
                 patch_modules=(), code_patch_module=None, gfx=EMPTY_GFX, upscale=EMPTY_UPSCALE, hooks=EMPTY_HOOKS,
                 npad_players=1, account_users=1, video_catchup=True, vk_parallel=None, released=True, graphics_scale=None, version_name="", notes="",
                 romfs_needed=()):
        self.key = key                                # HOOKSHOT_GAME=<this>
        self.game = game                              # the game's name, for logs
        self.program_id = program_id                  # as nx2/dump.py reports it
        self.version = version                        # the update's version number (the .nup title's)
        self.build_id = build_id                      # main's whole build id: for_build() and make_game.py's gate
        self.main_base = main_base                    # main's base in the lifted image
        self.patch_modules = tuple(patch_modules)     # nx2 modules with install(lifter, img[, build_id]), in order
        self.code_patch_module = code_patch_module    # the nx2 module whose patches chunks.edit_c applies to the C
        self.gfx = gfx
        self.upscale = upscale
        self.hooks = hooks
        # 1..8, or "auto": one per attached controller. The default player count for the forks' multi-player input
        # (Dragoon's HOS_NPAD_PLAYERS); nothing in this engine reads it yet, Ocarina's one pad being the only input here
        self.npad_players = npad_players
        self.account_users = account_users            # the console's users (nn::account), 1..8
        # a movie skips pictures behind its clock (host/nn_video.c's catch-up, for Ocarina's intro and its sound); off
        # for a game whose movie player counts every picture it submitted
        self.video_catchup = video_catchup
        # The renderer's parallel replay (gpu/vk/vk_replay.cpp par_threads): the threads that record one command buffer,
        # as RX_VK_PARALLEL gives them, or None for the renderer's own default (off). It helps a game that hands over most
        # of a frame in one command buffer and costs one that does not (docs/performance.md: Ocarina loses a fifth), so it
        # is the game's. Handed to the renderer at run time (HOOKSHOT_PROFILE_VK_PARALLEL); RX_VK_PARALLEL still overrides
        self.vk_parallel = vk_parallel
        # in the release: recomplauncher.json's games[] (nx2/game_folders.py released()). A profile main builds but the
        # release does not offer yet says False (docs/one-engine.md step 5: a game joins with its games[] row)
        self.released = released
        # the version as a player knows it ("1.0.0"), for the launcher's label of this game's copy (nx2/game_folders.py)
        self.version_name = version_name
        # what nn::oe::GetCurrentGraphicsScale answers in w0: an enum, 0 the full scale, 1 x0.8, 2 x0.667 (host/nn_applet.c);
        # None: the host's own answer (1, or 0 when an output size is set)
        self.graphics_scale = graphics_scale
        self.notes = notes
        # What this game's romfs must hold for the build and its start, each (any one of its names, "file" or "dir"):
        # make_game.py refuses a romfs without one before the build (#534, #863). A profile that names none gets only the
        # check that the romfs holds something: another game's files are its own (MKW and Bananza have no ZsDic.pack.zs)
        self.romfs_needed = tuple(romfs_needed)

    def __repr__(self):
        return "<Profile %s: %s>" % (self.key, self.game)


def from_pack(pack):
    """A profile from a game pack's manifest (packs/<key>/pack.json, loaded and checked by nx2/packs.py): its identity
    and its one build, and its profile and lift data, hex strings as numbers where the C view wants numbers. The
    program key prefixes (upscale taau, network_keep) stay strings, as the renderer takes them."""
    b, prof, lift, h = pack["builds"][0], pack["profile"], pack["lift"], packs.hex_int
    gfx, up, hooks = prof["gfx"], prof["upscale"], prof["hooks"]
    return Profile(
        key=pack["key"],
        game=pack["name"],
        program_id=pack["program_id"],
        version=b["version"],
        build_id=b["build_id"],
        main_base=h(b["main_base"]),
        patch_modules=lift["patch_modules"],
        code_patch_module=lift["code_patch_module"],
        gfx=Gfx(frame_limiter=h(gfx["frame_limiter"]), nominal_fps=h(gfx["nominal_fps"]), vrr_mode=h(gfx["vrr_mode"]),
                vrr_gate=h(gfx["vrr_gate"]), controller=h(gfx["controller"]),
                controller_words=tuple(h(v) for v in gfx["controller_words"]),
                controller_rate_words=tuple(h(v) for v in gfx["controller_rate_words"])),
        upscale=Upscale(taau=tuple(up["taau"]), gameplay=tuple(h(v) for v in up["gameplay"]),
                        network_keep=tuple(up["network_keep"])),
        hooks=Hooks(gfx_clock=h(hooks["gfx_clock"]), present_poll=h(hooks["present_poll"]),
                    present_events=tuple(h(v) for v in hooks["present_events"])),
        npad_players=prof["npad_players"],
        account_users=prof["account_users"],
        video_catchup=prof["video_catchup"],
        vk_parallel=prof["vk_parallel"],
        released=pack["released"],
        graphics_scale=prof["graphics_scale"],
        version_name=b["version_name"],
        notes=prof["notes"],
        romfs_needed=tuple((tuple(names), kind) for names, kind in prof["romfs_needed"]),
    )


# Each game's profile is its pack's (packs/<key>/pack.json): the values, and the comments that say where each was
# measured, are there. Every value was measured on that game's dump; Ocarina's were checked by running it.
PACKS = packs.tree()
_PACK_PROFILES = [from_pack(m) for m in PACKS.values()]

# The profile for a dump whose build no profile knows: nothing patched, nothing hooked, one player.
NONE = Profile(key="none", game="an unknown game",
               notes="No profile knows this build: lifted without patches, and the runtime recognises nothing in it.")

GAMES = collections.OrderedDict((p.key, p) for p in _PACK_PROFILES + [NONE])
# (by name, for the checks that look at one game's values)
OOT, DKB, MKW = GAMES.get("oot"), GAMES.get("dkb"), GAMES.get("mkw")

# Ocarina stays the default where there is no dump to go by (the committed header, the game-free checks), because it
# is the only game whose values have been measured and checked by running it. It is nx2/packs.py's (the lock's, b1).
DEFAULT = packs.DEFAULT


def for_build(build_id):
    """The profile whose build id is main's (a prefix of 8 or more hex digits matches, as the docs quote them), or
    NONE when no profile knows that build."""
    bid = (build_id or "").strip().lower()
    if len(bid) >= 8:
        for p in GAMES.values():
            if p.build_id and (p.build_id.startswith(bid) or bid.startswith(p.build_id)):
                return p
    return NONE


def active(env=None, build_id=None):
    """The profile this lift or build is for: HOOKSHOT_GAME, else the one for main's build id, else DEFAULT."""
    env = os.environ if env is None else env
    key = (env.get("HOOKSHOT_GAME") or "").strip().lower()
    if key:
        if key not in GAMES:
            raise SystemExit("HOOKSHOT_GAME=%s: no such game profile (have %s)" % (key, ", ".join(GAMES)))
        return GAMES[key]
    if build_id:
        return for_build(build_id)
    return GAMES[DEFAULT]


def known_builds():
    """The build ids make_game.py accepts, as (profile, the 8-digit prefix the messages quote)"""
    return [(p, p.build_id[:8]) for p in GAMES.values() if p.build_id]


# ---- the lift ----

def lift_dirs():
    """Every pack's lift/ folder that is there, in the profile order: where the game's patch modules are"""
    return [d for d in (packs.folder(k, packs.LIFT_DIR) for k in PACKS) if os.path.isdir(d)]


def module_dir(name, profile=None):
    """The pack lift/ folder that holds the module name: the profile's own pack's first, then every pack's in the profile
    order; None when no pack's holds it. Module names are unique across packs and nx2/ (tests/test_game_packs.py)"""
    keys = ([profile.key] if profile is not None and profile.key in PACKS else []) + list(PACKS)
    for key in keys:
        d = packs.folder(key, packs.LIFT_DIR)
        if os.path.isfile(os.path.join(d, name + ".py")):
            return d
    return None


def module_path(name, profile=None):
    """The file of a patch module the profile names: in its pack's lift/ (module_dir), else in nx2/"""
    return os.path.join(module_dir(name, profile) or HERE, name + ".py")


def load_module(name, profile=None):
    """A game's patch module by the name its profile holds, from the profile's pack's lift/ (module_dir: the pack that holds
    it, when the caller names no profile, as the lift's edit of the C does). This is the one place the lift imports a module
    by a name made at run time; nx2/make_game.py lift_sources takes every profile's modules into the lift's cache key,
    because its walk of import statements cannot see these (tests/test_lift_cache.py)."""
    d = module_dir(name, profile)
    if d and d not in sys.path:
        sys.path.insert(0, d)   # (a pack's modules import each other by name: oot_jitter imports oot_wide)
    return importlib.import_module(name)


def build_steps(profile):
    """The optional build steps the profile's pack takes part in (packs.BUILD_STEPS): none for a profile with no pack"""
    return tuple((PACKS.get(profile.key) or {}).get("build_steps", ()))


def pack_folder(profile, name):
    """The profile's pack's folder (packs.LIFT_DIR, TOOLS_DIR or ASSETS_DIR), or None for a profile with no pack"""
    return packs.folder(profile.key, name) if profile.key in PACKS else None


def env_prefixes():
    """The environment prefixes every pack's lift reads (lift.env_prefixes): the lift's cache key takes them, beside the
    engine's own A64_ and RX_. Every pack's, not the active one's, so the key does not depend on HOOKSHOT_GAME"""
    out = []
    for m in PACKS.values():
        out += [p for p in m["lift"].get("env_prefixes", ()) if p not in out]
    return tuple(out)


def install_patches(lifter, img, profile, build_id="", log=print):
    """The profile's lift patch modules, in its order; lift.py names no game's module itself. A module's install
    takes (lifter, img), or (lifter, img, build_id) when it gates on the build as Ocarina's oot_fov does."""
    if not profile.patch_modules:
        log("patches: the %s profile has no lift patch modules" % profile.key)
        return 0
    total = 0
    for name in profile.patch_modules:
        install = load_module(name, profile).install
        n = len(inspect.signature(install).parameters)
        total += (install(lifter, img, build_id) if n >= 3 else install(lifter, img)) or 0
    return total


# ---- the C view ----
#
# host/ and gpu/ cannot import Python, so a profile is emitted as game_profile.h. DEFAULT's is committed in game/, so a
# build or a check with no dump needs no generation step (tests/test_game_profile.py fails when it drifts). A lift of a
# dump writes its own profile's header beside the lifted C (<work>/sa/game/game_profile.h), and nx2/build.py puts that
# folder ahead of game/, so the runtime and the renderer are compiled for the game they will run.

def _keys(prefixes):
    return ",".join(prefixes)


def _init(fmt, values):
    """A C array initializer. Empty is one zero, because `{ }` is not C and the count beside it is 0."""
    return "{ %s }" % (", ".join(fmt % v for v in values) if values else "0")


def saves_dir(profile):
    """The saves' folder name under $XDG_DATA_HOME on Linux: the pack's saves_dir where it has one (Ocarina's "hookshot",
    where its players' saves have always been); any other game's "hookshot-<key>". The save folder holds save/<user id>/ with the game's own files in it and
    nothing that says which game wrote them, so two games sharing one would read, and overwrite, each other's
    (DK Bananza fork, 4f417fd7)."""
    pack = PACKS.get(profile.key) or {}
    return pack.get("saves_dir") or "hookshot-" + profile.key   # (a pack's saves_dir: Ocarina's legacy folder)


# (the header's first lines still say to edit this file: a game's values are its pack.json now, but changing the
# wording would change every header's bytes, and so every runtime, for nothing. docs/game-packs.md a1 keeps them)
def emit_header(profile=None):
    p = GAMES[DEFAULT] if profile is None else profile
    out = []
    w = out.append
    w("/* The game profile, as C. GENERATED by nx2/game_profile.py --emit-header: do not edit.")
    w(" *")
    w(" * Edit nx2/game_profile.py and run `python -I nx2/game_profile.py --emit-header`. The committed copy is")
    w(" * checked against that file by tests/test_game_profile.py, so an edit here fails the suite. A lift writes the")
    w(" * dump's own profile beside its C, and the build reads that one first.")
    w(" *")
    w(" * A GAME_HAVE_<x> of 0 means this profile does not know that value. The code it guards must then leave the")
    w(" * thing alone rather than guess: an unknown offset is a feature that is off, not a number to invent. */")
    w("#ifndef GAME_PROFILE_H")
    w("#define GAME_PROFILE_H")
    w("")
    w("/* the project, which is not game data. GAME_PROJECT_W is the same name for the Win32 wide-string calls. */")
    w('#define GAME_PROJECT "%s"' % PROJECT)
    w('#define GAME_PROJECT_W L"%s"' % PROJECT)
    w("")
    w('#define GAME_PROFILE_KEY "%s"' % p.key)
    w('#define GAME_NAME "%s"' % p.game)
    w('#define GAME_PROGRAM_ID "%s"' % (p.program_id or ""))
    w("#define GAME_HAVE_PROGRAM_ID %d" % (1 if p.program_id else 0))
    w("#define GAME_VERSION %d" % (p.version or 0))
    w("#define GAME_HAVE_VERSION %d" % (1 if p.version else 0))
    w("/* main's build id: the runtime checks the image it runs against it before it recognises any address */")
    w('#define GAME_BUILD_ID "%s"' % (p.build_id or ""))
    w("#define GAME_HAVE_BUILD_ID %d" % (1 if p.build_id else 0))
    w("")
    w("/* main's base in the lifted image */")
    w("#define GAME_MAIN_BASE 0x%xull" % (p.main_base or 0))
    w("#define GAME_HAVE_MAIN_BASE %d" % (1 if p.main_base is not None else 0))
    w("")
    w("/* offsets into the game's graphics module (host/nn_vi.c's hos_gfx_module) */")
    for name, value in (("FRAME_LIMITER", p.gfx.frame_limiter), ("NOMINAL_FPS", p.gfx.nominal_fps),
                        ("VRR_MODE", p.gfx.vrr_mode), ("VRR_GATE", p.gfx.vrr_gate),
                        ("CONTROLLER", p.gfx.controller)):
        w("#define GAME_GFX_%s 0x%x" % (name, value or 0))
        w("#define GAME_GFX_HAVE_%s %d" % (name, 1 if value is not None else 0))
    w("#define GAME_GFX_CONTROLLER_WORDS %s" % _init("0x%x", p.gfx.controller_words))
    w("#define GAME_GFX_CONTROLLER_WORD_COUNT %d" % len(p.gfx.controller_words))
    w("#define GAME_GFX_CONTROLLER_RATE_WORDS %s" % _init("0x%x", p.gfx.controller_rate_words))
    w("#define GAME_GFX_CONTROLLER_RATE_WORD_COUNT %d" % len(p.gfx.controller_rate_words))
    w("")
    w("/* return addresses the runtime recognises the game's calls by (host/hos_thread.c, host/nvn_native.c) */")
    w("#define GAME_LR_GFX_CLOCK 0x%xull" % (p.hooks.gfx_clock or 0))
    w("#define GAME_HAVE_LR_GFX_CLOCK %d" % (1 if p.hooks.gfx_clock is not None else 0))
    w("#define GAME_LR_PRESENT_POLL 0x%xull" % (p.hooks.present_poll or 0))
    w("#define GAME_HAVE_LR_PRESENT_POLL %d" % (1 if p.hooks.present_poll is not None else 0))
    w("#define GAME_LR_PRESENT_EVENTS %s" % _init("0x%xull", p.hooks.present_events))
    w("#define GAME_LR_PRESENT_EVENT_COUNT %d" % len(p.hooks.present_events))
    w("")
    w("/* shader program keys this game's renderer treats specially; empty: none of them */")
    w('#define GAME_UPSCALE_TAAU_KEYS "%s"' % _keys(p.upscale.taau))
    w('#define GAME_NETWORK_KEEP_KEYS "%s"' % _keys(p.upscale.network_keep))
    w("#define GAME_GAMEPLAY_NETWORK_KEYS %s" % _init("0x%016xull", p.upscale.gameplay))
    w("#define GAME_GAMEPLAY_NETWORK_KEY_COUNT %d" % len(p.upscale.gameplay))
    w("")
    w("/* Npads served when HOS_NPAD_PLAYERS is unset: a count, or -1 for one per attached controller */")
    w("#define GAME_NPAD_PLAYERS %d" % (-1 if p.npad_players == "auto" else int(p.npad_players)))
    w("")
    w("/* the saves' folder on Linux, under $XDG_DATA_HOME (host/hos_main.c saves_dir): Ocarina's stays where its")
    w("   players' saves are, every other game has its own, so no two games ever write one save folder */")
    w('#define GAME_SAVES_DIR "%s"' % saves_dir(p))
    w("")
    w("/* the console's users (nn::account) when HOS_ACCOUNT_USERS is unset: host/nn_applet.c */")
    w("#define GAME_ACCOUNT_USERS %d" % int(p.account_users))
    w("")
    w("/* 1: a movie skips pictures behind its clock (host/nn_video.c; HOS_VIDEO_NO_CATCHUP=1 turns it off); 0: never, for")
    w("   a game whose movie player counts every picture it submitted */")
    w("#define GAME_VIDEO_CATCHUP %d" % (1 if p.video_catchup else 0))
    w("")
    w("/* the renderer's parallel replay threads (RX_VK_PARALLEL's default, handed over at run time as")
    w("   HOOKSHOT_PROFILE_VK_PARALLEL by host/hos_main.c); -1: the renderer's own default (off) */")
    w("#define GAME_VK_PARALLEL %d" % (-1 if p.vk_parallel is None else int(p.vk_parallel)))
    w("")
    w("/* what nn::oe::GetCurrentGraphicsScale answers in w0 (0 the full scale, 1 x0.8, 2 x0.667); -1: the host's own answer */")
    w("#define GAME_GRAPHICS_SCALE %d" % (-1 if p.graphics_scale is None else int(p.graphics_scale)))
    w("")
    w("#endif /* GAME_PROFILE_H */")
    return "\n".join(out) + "\n"


def write_header(folder, profile):
    """The profile's header into folder/game_profile.h (a lift's <work>/sa/game), only when it changes, so a build
    after an unchanged lift does not rebuild the runtime for nothing. Returns the path."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "game_profile.h")
    text = emit_header(profile)
    try:
        with open(path, encoding="utf-8", newline="") as f:
            if f.read() == text:
                return path
    except OSError:
        pass
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def header_path():
    return os.path.normpath(HEADER)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--emit-header", action="store_true", help="write game_profile.h (game/'s, or --out's)")
    p.add_argument("--check-header", action="store_true", help="exit 1 when the committed header does not match")
    p.add_argument("--game", help="a profile key (default: %s for the committed header)" % DEFAULT)
    p.add_argument("--build-id", help="choose the profile by main's build id, as a lift does")
    p.add_argument("--out", help="with --emit-header: the folder to write into instead of game/")
    a = p.parse_args(argv)
    if a.game:
        if a.game not in GAMES:
            p.error("no profile %r (have %s)" % (a.game, ", ".join(GAMES)))
        profile = GAMES[a.game]
    elif a.build_id:
        profile = for_build(a.build_id)
    else:
        profile = GAMES[DEFAULT]
    if a.check_header:
        text = emit_header(GAMES[DEFAULT])
        with open(header_path(), encoding="utf-8", newline="") as f:
            have = f.read()
        if have != text:
            print("%s is not what nx2/game_profile.py emits: run python -I nx2/game_profile.py --emit-header"
                  % header_path())
            return 1
        print("%s matches nx2/game_profile.py" % header_path())
        return 0
    if a.emit_header:
        if a.out:
            print("wrote %s for the %s profile" % (write_header(a.out, profile), profile.key))
            return 0
        if profile.key != DEFAULT:
            p.error("game/game_profile.h is %s's; write another game's with --out <folder>" % DEFAULT)
        with open(header_path(), "w", encoding="utf-8", newline="\n") as f:
            f.write(emit_header(profile))
        print("wrote %s for the %s profile" % (header_path(), profile.key))
        return 0
    for key, prof in GAMES.items():
        print("%s%s: %s" % ("* " if prof is profile else "  ", key, prof.game))
        print("    program %s, version %s, build %s" % (prof.program_id, prof.version, prof.build_id))
        print("    main base %s" % ("%#x" % prof.main_base if prof.main_base is not None else None))
        print("    lift patches %s, code patches %s" % (prof.patch_modules or None, prof.code_patch_module))
        print("    graphics module %s" % (prof.gfx,))
        print("    hooks %s" % (prof.hooks,))
        print("    upscale keys %s" % (prof.upscale,))
        print("    npad players %s, users %s" % (prof.npad_players, prof.account_users))
        print("    %s" % prof.notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
