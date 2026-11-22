"""Where each game's own folders go, so that one builder and one launcher entry can hold several games
(docs/one-engine.md, step 4) without moving the default game's.

One rule, kept the same in every place that picks a game's folder:

    the default game (game_profile.DEFAULT, Ocarina) keeps the folder it has always had: <base>
    any other game gets <base>-<key>

<base> is whatever the caller would have used for the default game: ~/Games/Hookshot for the game folder (the
launcher's start.args name it, nx2/make_game.py --prepare defaults to it), ~/.cache/hookshot-builder for the builder's
work folder. So Ocarina's game folder, its settings (hookshot_options.ini, in the game folder), its pipeline caches (in
the game folder) and its work folder stay exactly where they are, and a second game's sit beside them under its own
name. The saves follow the same idea on Linux through the profile's GAME_SAVES_DIR (nx2/game_profile.py saves_dir:
"hookshot", else "hookshot-<key>"); on Windows they are beside each game's executable, so in its own game folder.

The places that apply it:
  - nx2/make_game.py --prepare (RecompLauncher's step), when no --out is given: the dump decides the game
    (game_profile.for_build), and the folder is that game's;
  - Play with a game: `--play <folder> --game <key>` starts <folder> for the default game and <folder>-<key> for
    another (builder/linux/hookshot-launch, builder/setup/setup.c play_folder, builder/linux/hookshot_builder.py). The
    launcher entry's games[] adds `--game <key>` after start.args' `--play {home}/Games/Hookshot`, so a tile starts its
    own game's folder. (`--game` here is a profile key; nx2/make_game.py's own --game is still the copy's path, which
    RecompLauncher's prepare step passes: the two never meet, since Play is handled before make_game.py runs.)
  - recomplauncher.json games[] (launcher_games below), once more than one game is in the release;
  - the Builders' "Your games" list (built_games below; builder/HookshotBuilder/Games.cs on Windows): each game that
    builds, in its folder by this rule from the Builder's game folder, when that folder holds a built game.

    python -I nx2/game_folders.py --folder ~/Games/Hookshot --key mkw   # ~/Games/Hookshot-mkw
    python -I nx2/game_folders.py --launcher-games                     # the games[] the released profiles give
    python -I nx2/game_folders.py --write-launcher recomplauncher.json  # the entry with every released game in it
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import game_profile  # noqa: E402

KEY = re.compile(r"^[a-z0-9]{1,16}$")   # (RecompLauncher's games[].id pattern; a profile key is one too)


def suffix(key):
    """"" for the default game, "-<key>" for any other"""
    if not KEY.match(key or ""):
        raise ValueError("not a game key: %r" % (key,))
    return "" if key == game_profile.DEFAULT else "-" + key


def folder(base, key):
    """The game's own folder beside the default game's: base for the default game, base-<key> otherwise. A trailing
    separator on base is dropped first, so ~/Games/Hookshot/ and ~/Games/Hookshot give the same folder."""
    base = base.rstrip("/\\") or base
    return base + suffix(key)


def released():
    """The profiles a release offers: every profile that names a build (the empty one, NONE, builds nothing) and is in
    the release (a profile main builds but the release does not offer yet says released=False)"""
    return [p for p in game_profile.GAMES.values() if p.build_id and getattr(p, "released", True)]


def buildable():
    """Every profile that builds a game (it names a build id), released or not: oot, mkw and dkb today, in the profiles'
    order"""
    return [p for p in game_profile.GAMES.values() if p.build_id]


def base_of(out):
    """The base the games' folders are named from, given the Builder's game folder: that folder, or the base it is
    another game's folder of (<base>-<key>), so the list is the same whichever game's folder the Builder last built"""
    out = out.rstrip("/\\") or out
    for p in buildable():
        s = suffix(p.key)
        if s and len(out) > len(s) and out.endswith(s) and out[-len(s) - 1] not in "/\\":
            return out[:-len(s)]
    return out


def folder_game(game_folder):
    """The profile of the game a folder holds, from the copy of its game files it keeps for updates
    (.hookshot_exefs/main: an NSO whose module id is main's build id), or None when it keeps none or no profile knows it.
    A Builder passes the folder the player chose (--out), so a game can sit in another's folder by the rule: the list
    names it by what it is"""
    try:
        with open(os.path.join(game_folder, ".hookshot_exefs", "main"), "rb") as f:
            head = f.read(0x60)
    except OSError:
        return None
    if len(head) < 0x60 or head[:4] != b"NSO0":
        return None
    p = game_profile.for_build(head[0x40:0x60].hex())
    return p if p.build_id else None


def built_games(out, game_exe):
    """The Builders' "Your games": for each buildable() profile, its folder by the rule from base_of(out), when that folder
    holds a built game (image.bin, and the game's executable: game_exe(folder) is its path or None). Each a dict:
    key (the profile whose folder it is: Play is `--play <base> --game <key>`), name (the game the folder holds, else the
    profile's), folder and base. Nothing for an empty out"""
    if not out:
        return []
    base = base_of(out)
    games = []
    for p in buildable():
        f = folder(base, p.key)
        if not os.path.isfile(os.path.join(f, "image.bin")) or not game_exe(f):
            continue
        held = folder_game(f)
        games.append({"key": p.key, "name": (held or p).game, "folder": f, "base": base})
    return games


def copy_label(p):
    """What the launcher says when it asks for this game's copy: the game, its exact version and main's build"""
    return ("Your copy of %s for Nintendo Switch 2, %s (build %s): the .nup file, or the folder that holds its romfs and "
            "exefs." % (p.game, p.version_name, p.build_id[:8]))


def launcher_inputs(profiles=None, named=None):
    """recomplauncher.json's inputs[]: one named input per game (its id the profile key), for the games in named
    (default: every released one). RecompLauncher's per-game setup asks each game's tile for its own input only
    (games[].inputs), and prepare gets it as --game-<key>={input:<key>}. Inputs take no nup_build_id: the builder checks
    each copy against its profile (make_game.py --game-<key>)."""
    profiles = released() if profiles is None else list(profiles)
    named = {p.key for p in profiles} if named is None else set(named)
    return [{"id": p.key, "label": copy_label(p), "role": "game",
             "formats": [{"type": "file", "extensions": ["nup"]}, {"type": "folder"}]}
            for p in profiles if p.key in named]


def prepare_args(profiles=None, named=None):
    """prepare.args' per-game part: each named copy in one argument, so a game whose input is absent (another game's
    setup) drops out whole rather than leaving a flag behind (RecompLauncher SETUP-INPUTS.md)"""
    profiles = released() if profiles is None else list(profiles)
    named = {p.key for p in profiles} if named is None else set(named)
    return ["--game-%s={input:%s}" % (p.key, p.key) for p in profiles if p.key in named]


def launcher_games(profiles=None, home="{home}/Games/Hookshot", local="{local}", named=None):
    """recomplauncher.json's games[] for these profiles (default: released()), or None when there is only one game.

    RecompLauncher's schema wants two games or more in games[]; with one, the recomp shows as one game. Each game's
    args add `--game <key>` after start.args (Play only: games[].args are not passed to prepare). Each game's saves
    are its own: on Linux the profile's GAME_SAVES_DIR under {local}, on Windows its game folder's user/. The default
    game's are the paths the entry's top-level saves have always named, so nothing moves. Each game in named (default:
    all) lists its own input (launcher_inputs), so its tile asks for its copy alone."""
    profiles = released() if profiles is None else list(profiles)
    if len(profiles) < 2:
        return None
    named = {p.key for p in profiles} if named is None else set(named)
    games = []
    for p in profiles:
        g = {"id": p.key, "name": p.game, "args": ["--game", p.key]}
        if p.key in named:
            g["inputs"] = [p.key]
        g["saves"] = ["%s/%s" % (local, game_profile.saves_dir(p)), "%s/user" % folder(home, p.key)]
        games.append(g)
    return games


# The oldest RecompLauncher whose per-game setup this entry needs: games[] with each game's own inputs and saves, and
# named inputs in prepare.args (0.90 Beta 36: games.rs inputs_for / scope / each game's inputs_ready)
LAUNCHER_MIN = "0.90.0-beta.36"


def dlss_option():
    """prepare.options' NVIDIA DLSS tick on Windows: ticked, HookshotBuilder.exe --cli --dlss --accept-nvidia-license
    fetches NVIDIA's pinned DLSS runtimes into each game folder's ngx\\ and keeps the release's renderer (NGX, Streamline
    and FSR 4 compiled in). Unticked passes nothing, and a prepare without either flag keeps the folder's runtimes. Off
    until ticked (it carries NVIDIA's license, the one scripts/ngx_sdk.py downloads under); shown with an RTX card. Not on
    Linux: the AppImage's renderer has no DLSS"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("ngx_sdk", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "ngx_sdk.py"))
    ngx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ngx)
    return {"id": "dlss", "label": "NVIDIA DLSS: download NVIDIA's DLSS files and accept their license",
            "note": "NVIDIA's, not Hookshot's: the build downloads them from NVIDIA and puts DLSS in each game folder.",
            "args": ["--dlss", "--accept-nvidia-license"], "os": ["windows"], "gpu": "nvidia",
            "license": {"name": "NVIDIA RTX SDKs license (NVIDIA DLSS)", "url": ngx.LICENSE_URL}}


def launcher_entry(entry, profiles=None):
    """recomplauncher.json with every released game in it: its copy as a named input (no legacy `game`: the launcher
    would then ask every game's tile for that one copy too), its tile in games[], and its copy in prepare.args in one
    argument each. What the entry holds besides is left as it is. A player's old setup (the legacy copy) does not carry
    over to the named input: RecompLauncher asks for the Ocarina copy once more after the update
    (.github/release-notice.md)."""
    profiles = released() if profiles is None else list(profiles)
    out = {}
    for k, v in entry.items():
        if k == "game":   # (the legacy single copy: its place in the file goes to inputs)
            out["inputs"] = launcher_inputs(profiles)
            continue
        out[k] = v
        if k == "repo":
            out["min"] = LAUNCHER_MIN
    out.setdefault("min", LAUNCHER_MIN)
    out["inputs"] = launcher_inputs(profiles) + [i for i in entry.get("inputs", []) if i["id"] not in {p.key for p in profiles}]
    args = [a for a in out["prepare"]["args"] if a not in ("--game", "{game}") and not a.startswith("--game-")]
    options = [o for o in out["prepare"].get("options", []) if o.get("id") != "dlss"] + [dlss_option()]
    out["prepare"] = dict(out["prepare"], args=args + prepare_args(profiles), options=options)
    games = launcher_games(profiles)
    if games:
        out["games"] = games
    else:
        out.pop("games", None)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--folder", help="a base folder: the game's own folder for --key is printed")
    ap.add_argument("--key", help="a game profile key")
    ap.add_argument("--launcher-games", action="store_true", help="print the games[] the released profiles give")
    ap.add_argument("--write-launcher", metavar="JSON", help="rewrite this recomplauncher.json with the released games in it")
    a = ap.parse_args(argv)
    if a.write_launcher:
        with open(a.write_launcher, encoding="utf-8") as f:
            entry = json.load(f)
        with open(a.write_launcher, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(launcher_entry(entry), indent=2, ensure_ascii=False) + "\n")
        return 0
    if a.launcher_games:
        print(json.dumps(launcher_games(), indent=2))
        return 0
    if a.folder is None or not a.key:
        ap.error("--folder and --key, or --launcher-games")
    print(folder(os.path.expanduser(a.folder), a.key))
    return 0


if __name__ == "__main__":
    sys.exit(main())
