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
    python -I nx2/game_folders.py --library ~/Games/Hookshot [--json]   # every game: installed, its version, an update due

The library (library below) is what one program that manages every game reads: each buildable() game in profile order,
where under the base it is installed, the version it was built with and whether this engine would update it. A game is
found in any of the base's folders (<base> and every <base>-<key>), its own folder first: a Builder used to build into the
folder the player chose (--out), so a game can sit in another's folder, and it is still that game, played and updated
there. A folder is the game its kept copy is (.hookshot_exefs/main's build id), or, keeping none (built before updates came
in), the game whose folder it is by the rule. It is what `nx2/make_game.py --update-all` loops over, and `--json` is the
form the Builders read. --json prints a list, one object per game, with these fields (stable: a field is added, never
renamed or retyped):

    key               the profile key (oot, mkw, dkb)
    name              the game (the profile's name, "The Legend of Zelda: Ocarina of Time")
    folder            where the game is installed: its own folder when it is there or not installed at all, else the
                      base's folder that holds it. Play starts this folder
    base              the base the folders are named from (base_of: a game folder of another game gives its base)
    state             "built" (installed in folder) or "missing" (in none of the base's folders)
    built             true when state is "built" (image.bin and the game's executable in folder)
    holds             the key of the game whose copy folder keeps, or null: no copy kept, a build no profile knows, or
                      not installed (another game in own_folder is own_folder_holds)
    other_folders     the base's other folders that hold this game too, built (a list, mostly empty): --update-all
                      updates each of them as well
    own_folder        the game's own folder by the rule (<base>, or <base>-<key>): where an install of it goes
    own_folder_holds  the key of another game whose copy sits in own_folder, or null. When set, installing this game
                      under this base is refused until that folder is freed (or the game goes under another base)
    version           the Hookshot version folder was built with (hookshot_version.txt version=), or null; only when built
    builder           the Builder that built it (hookshot_version.txt builder=, the Builder's executable), or null
    kept              true when folder keeps the game files an update builds from (.hookshot_exefs and romfs); false
                      when not installed. A built folder without them (built before updates came in) can't be updated:
                      the game's files are to be chosen once to build it again (--update-all says "rebuild")
    update            true when this engine is to update the game (its version is older: version.py update_due, the
                      Linux updater's rule, which never takes a game back to an older version; the Windows Builder's
                      Updater.cs rebuilds a differing version, a newer one too), false when it is current (or newer),
                      null when it can't be told (no version in the folder, or this engine names none). Always false when
                      not built, or not kept
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


def game_exe(folder):
    """The game's executable in a folder, either platform's (a Linux build for Windows has hookshot.exe): bin/ under the
    launcher, else the folder itself (a folder built before the launcher). None when there is none. The Builders pass
    their own check to built_games and library; this is the default"""
    for rel in (("bin", "hookshot"), ("bin", "hookshot.exe"), ("hookshot",), ("hookshot.exe",)):
        path = os.path.join(folder, *rel)
        if os.path.isfile(path):
            return path
    return None


def version_file(game_folder):
    """hookshot_version.txt's version= and builder= (the build writes them: nx2/make_game.py), each None when absent"""
    found = {}
    try:
        with open(os.path.join(game_folder, "hookshot_version.txt"), encoding="utf-8", errors="replace") as f:
            for line in f:
                k, sep, v = line.strip().partition("=")
                if sep and k in ("version", "builder") and k not in found:
                    found[k] = v.strip() or None
    except OSError:
        pass
    return found.get("version"), found.get("builder")


def folder_identity(f, rule_key, game_exe=game_exe):
    """The game a folder of the base is, when it holds a built one: its kept copy's profile key, or rule_key (the game whose
    folder it is by the rule) when it keeps no copy, or no profile knows the copy. None when nothing is built there"""
    if not (os.path.isfile(os.path.join(f, "image.bin")) and game_exe(f)):
        return None
    held = folder_game(f)
    return held.key if held else rule_key


def kept_copy(f):
    """The folder keeps the game files an update builds from (.hookshot_exefs and romfs: make_game.py --update)"""
    return os.path.isdir(os.path.join(f, ".hookshot_exefs")) and os.path.isdir(os.path.join(f, "romfs"))


def library(base, game_exe=game_exe, engine=None):
    """Every game that builds (buildable(), in profile order), installed or not, under base: a dict for each with the fields
    the module's docstring lists (key, name, folder, base, state, built, holds, other_folders, own_folder,
    own_folder_holds, version, builder, kept, update). base is the default game's folder, or another game's folder of it
    (base_of). engine is the version the update flags compare against: default this engine's (version.py release()).
    Nothing for an empty base"""
    if not base:
        return []
    import version   # (nx2/version.py; release() may ask git, so only when no engine is given)
    if engine is None:
        engine = version.release()
    base = base_of(base)
    found = [(folder(base, p.key), folder_identity(folder(base, p.key), p.key, game_exe)) for p in buildable()]
    games = []
    for p in buildable():
        own = folder(base, p.key)
        there = [f for f, is_ in found if is_ == p.key]
        there.sort(key=lambda f: f != own)   # (its own folder first, the others in profile order: sort keeps their order)
        f = there[0] if there else own
        built = bool(there)
        held = folder_game(f) if built else None   # (not installed: what sits in its own folder is own_folder_holds)
        own_held = folder_game(own)
        have, builder = version_file(f) if built else (None, None)
        kept = built and kept_copy(f)
        games.append({"key": p.key, "name": p.game, "folder": f, "base": base, "state": "built" if built else "missing",
                      "built": built, "holds": held.key if held else None, "other_folders": there[1:], "own_folder": own,
                      "own_folder_holds": own_held.key if own_held and own_held.key != p.key else None,
                      "version": have, "builder": builder, "kept": kept,
                      "update": version.update_due(have, engine) if kept else False})
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
    ap.add_argument("--library", metavar="BASE", help="every game under this base: installed or not, its version, an update due")
    ap.add_argument("--json", action="store_true", help="with --library: as JSON (the fields in this file's docstring)")
    a = ap.parse_args(argv)
    if a.library is not None:
        games = library(os.path.abspath(os.path.expanduser(a.library)))
        if a.json:   # (ASCII, a folder's other letters as \u escapes: the same bytes whatever the console's code page)
            print(json.dumps(games, indent=2))
            return 0
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # (-I ignores PYTHONIOENCODING: a folder's letters)
        except (AttributeError, ValueError):
            pass
        for g in games:
            state = "not installed"
            if g["built"] and not g["kept"]:
                state = "installed (%s), its files to choose again to update" % (g["version"] or "no version")
            elif g["built"]:
                state = "installed (%s%s)" % (g["version"] or "no version",
                                              {True: ", update due", False: "", None: ", can't tell"}[g["update"]])
            print("%-4s %-40s %-34s %s" % (g["key"], g["name"], state, g["folder"]))
            for f in g["other_folders"]:
                print("%-4s %-40s %-34s %s" % ("", "", "installed here too", f))
            if g["own_folder_holds"]:
                print("%-4s %-40s %-34s %s" % ("", "", "its own folder holds " + game_profile.GAMES[g["own_folder_holds"]].game,
                                               g["own_folder"]))
        return 0
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
        ap.error("--folder and --key, --launcher-games or --library")
    print(folder(os.path.expanduser(a.folder), a.key))
    return 0


if __name__ == "__main__":
    sys.exit(main())
