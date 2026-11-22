"""The Builders' "Your games" list: each game that builds, in its own folder by nx2/game_folders.py's rule (the default game's
<base>, another's <base>-<key>, base the Builder's game folder or the base it is another game's folder of), when that folder
holds a built game (image.bin and the game), named by the copy the folder keeps (.hookshot_exefs/main's build id), with Play
starting it as `--play <base> --game <key>` does. Game-free: placeholder folders and a placeholder NSO header only.

Checked here:
  - nx2/game_folders.py built_games, base_of and folder_game over placeholder folders;
  - the Linux Builder (builder/linux/hookshot_builder.py): your_games is built_games with its own game check, the form shows the
    list in place of the one "Play the game built in" button, and its Play goes through play_folder_for (--play/--game);
  - the Qt Builder (builder/py): core.built_games is the tools' built_games, the list is filled with the form and after a build,
    and its Play goes through game_folders.folder;
  - the WPF Builder (builder/HookshotBuilder/Games.cs): its profiles are game_profile.py's that build (key, name, build id, in
    order) and its default key game_profile.DEFAULT; the window fills the list with the form and after a build and plays the
    folder by the rule; and with .NET (Windows), Games.cs compiled with tests/builder_games_test.cs lists the same games, folders
    and bases as built_games for the same placeholder folders.

    python -I -B tests/test_builder_games.py [--dotnet dotnet]
"""
import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# (no {dotnet} here: run_all would skip the whole check without it, and only the fixture needs it. On Windows it is required)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
import game_folders  # noqa: E402
import game_profile  # noqa: E402

failures = []


def check(ok, what):
    if not ok:
        failures.append(what)
        print("FAIL: " + what)


def placeholder(folder, build_id=None, exe=os.path.join("bin", "hookshot.exe"), image=True):
    """A folder that looks built: image.bin and the game's executable, empty; with build_id, a copy's main that is only an NSO
    header naming that build"""
    os.makedirs(os.path.join(folder, os.path.dirname(exe) or "."), exist_ok=True)
    open(os.path.join(folder, exe), "wb").close()
    if image:
        open(os.path.join(folder, "image.bin"), "wb").close()
    if build_id:
        os.makedirs(os.path.join(folder, ".hookshot_exefs"), exist_ok=True)
        with open(os.path.join(folder, ".hookshot_exefs", "main"), "wb") as f:
            f.write(b"NSO0" + b"\0" * 0x3c + bytes.fromhex(build_id.ljust(64, "0")))


def windows_exe(folder):
    """The WPF and Qt Builders' check (Games.cs GameExe, core.game_exe): bin/hookshot.exe, or hookshot.exe"""
    for exe in (os.path.join(folder, "bin", "hookshot.exe"), os.path.join(folder, "hookshot.exe")):
        if os.path.isfile(exe):
            return exe
    return None


def profile(key):
    return game_profile.GAMES[key]


def check_game_folders(tmp):
    games = os.path.join(tmp, "Games")
    base = os.path.join(games, "Hookshot")
    placeholder(base, profile("oot").build_id)
    placeholder(base + "-mkw", profile("mkw").build_id, exe="hookshot.exe")   # (a folder built before the launcher)
    placeholder(base + "-dkb", profile("dkb").build_id, image=False)          # (not built: no image.bin)
    other = os.path.join(games, "Elsewhere")
    placeholder(other, profile("mkw").build_id)                                # (MKW built into the folder the player chose)
    placeholder(os.path.join(games, "NoCopy"))                                 # (no copy kept: named by the rule's profile)

    check([p.key for p in game_folders.buildable()] == [p.key for p in game_profile.GAMES.values() if p.build_id],
          "buildable() is every profile that names a build, in order")
    want = [("oot", profile("oot").game, base), ("mkw", profile("mkw").game, base + "-mkw")]
    for out in (base, base + os.sep, base + "-mkw", base + "-mkw" + os.sep, base + "-dkb"):
        got = [(g["key"], g["name"], g["folder"]) for g in game_folders.built_games(out, windows_exe)]
        check(got == want, "built_games(%r): %r, not %r" % (out, got, want))
        check(all(g["base"] == base for g in game_folders.built_games(out, windows_exe)), "built_games(%r)'s base is %r" % (out, base))
    got = game_folders.built_games(other, windows_exe)
    check([(g["key"], g["name"], g["folder"]) for g in got] == [("oot", profile("mkw").game, other)],
          "a game in another's folder by the rule is named by its copy: %r" % (got,))
    got = game_folders.built_games(os.path.join(games, "NoCopy"), windows_exe)
    check([(g["key"], g["name"]) for g in got] == [("oot", profile("oot").game)], "a folder with no copy is named by its profile: %r" % (got,))
    for empty in ("", None):
        check(game_folders.built_games(empty, windows_exe) == [], "nothing for an empty game folder (%r)" % (empty,))
    check(game_folders.built_games(os.path.join(games, "Missing"), windows_exe) == [], "nothing for a folder that is not there")
    check(game_folders.folder_game(base + "-dkb").key == "dkb", "folder_game reads the copy's build id")
    check(game_folders.folder_game(os.path.join(games, "NoCopy")) is None, "folder_game is None without a copy")
    bad = os.path.join(games, "Bad")
    placeholder(bad, "0123456789abcdef")
    check(game_folders.folder_game(bad) is None, "folder_game is None for a build no profile knows")
    check(game_folders.base_of(base + "-mkw") == base and game_folders.base_of(base) == base, "base_of strips another game's suffix only")
    check(game_folders.base_of(os.path.join(games, "-mkw")) == os.path.join(games, "-mkw"), "base_of keeps a folder named only by a suffix")
    return [base, base + "-mkw", other, os.path.join(games, "NoCopy"), os.path.join(games, "Missing"), base + "-dkb"]


def check_linux_builder(tmp):
    src = (ROOT / "builder" / "linux" / "hookshot_builder.py").read_text(encoding="utf-8")
    check("Play the game built in" not in src, "the Linux Builder's form has the list, not its one Play button")
    form = src[src.index("    def show_form(self):"):src.index("    def games_list(self, parent, games):")]
    check("games = your_games(self.cfg.get(\"out\"))" in form and "self.games_list(f, games)" in form,
          "the Linux Builder's form shows Your games from its game folder")
    lst = src[src.index("    def games_list(self, parent, games):"):src.index("    def scrolled(self, parent, padding):")]
    check("self.play_and_close(play_folder_for(g[\"base\"], g[\"key\"]))" in lst,
          "the Linux Builder's Play starts the folder as --play <base> --game <key> does")
    check('subprocess.Popen(["xdg-open", f])' in lst, "the Linux Builder's rows open their folder")
    try:
        import tkinter  # noqa: F401  (the Builder imports it)
    except ImportError:
        print("the Linux Builder's your_games not run: no tkinter here")
        return
    spec = importlib.util.spec_from_file_location("hookshot_builder_games", ROOT / "builder" / "linux" / "hookshot_builder.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    base = os.path.join(tmp, "Linux", "Hookshot")
    placeholder(base, profile("oot").build_id, exe=os.path.join("bin", "hookshot"))
    placeholder(base + "-dkb", profile("dkb").build_id, exe="hookshot")
    placeholder(base + "-mkw", exe=os.path.join("bin", "hookshot.exe"))   # (a Windows game: not one the Linux Builder plays)
    got = [(g["key"], g["folder"]) for g in builder.your_games(base + "-dkb")]
    check(got == [("oot", base), ("dkb", base + "-dkb")], "the Linux Builder lists its built games: %r" % (got,))
    check(builder.play_folder_for(base, "dkb") == base + "-dkb" and builder.play_folder_for(base, "oot") == base,
          "the Linux Builder's Play folders are the rule's")


def check_qt_builder():
    core = (ROOT / "builder" / "py" / "hookshot_builder" / "core.py").read_text(encoding="utf-8")
    window = (ROOT / "builder" / "py" / "hookshot_builder" / "window.py").read_text(encoding="utf-8")
    check("return game_folders.built_games(out, lambda f: game_exe(f) if os.path.isfile(game_exe(f)) else None)" in core,
          "the Qt Builder's list is the tools' built_games with its own game check")
    check("return game_folders.folder(base, key)" in core, "the Qt Builder's Play folder is the rule's")
    refresh = window[window.index("    def refresh(self, *_):"):window.index("    def dragEnterEvent(self, e):")]
    check("self.fill_games()" in refresh, "the Qt Builder fills Your games with the form")
    check(re.search(r"self\.fill_games\(\)[^\n]*\n\s*self\.show_page\(self\.ready_page\)", window) is not None,
          "the Qt Builder fills Your games again when a build ends")
    check("core.start_game(core.play_folder(g[\"base\"], g[\"key\"]))" in window, "the Qt Builder's Play starts the folder by the rule")
    check("self.games_box, found, card" in window, "the Qt Builder's form has Your games above the game files")


def cs_profiles():
    games = (ROOT / "builder" / "HookshotBuilder" / "Games.cs").read_text(encoding="utf-8")
    table = games[games.index("Profiles =\n"):games.index("};", games.index("Profiles =\n"))]
    default = re.search(r'public const string DefaultKey = "([a-z0-9]+)";', games)
    return re.findall(r'\("([a-z0-9]+)", "([^"]+)", "([0-9a-f]+)"\)', table), default and default.group(1)


def check_wpf_builder():
    rows, default = cs_profiles()
    want = [(p.key, p.game, p.build_id) for p in game_folders.buildable()]
    check(rows == want, "Games.cs's profiles are game_profile.py's that build: %r, not %r" % (rows, want))
    check(default == game_profile.DEFAULT, "Games.cs's default key is game_profile.DEFAULT (%r)" % (default,))
    window = (ROOT / "builder" / "HookshotBuilder" / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    fill = window[window.index("    void FillGames()"):window.index("    Border Chip(string path)")]
    check("Games.Built(OutPath.Text.Length > 2 ? OutPath.Text : null)" in fill, "the WPF Builder's list is from its game folder")
    check("StartGame(Games.Folder(g.Base, g.Key));" in fill, "the WPF Builder's Play starts the folder as --play <base> --game <key> does")
    refresh = window[window.index("    void Refresh()"):window.index("    string? HardDriveWarning()")]
    check("FillGames();" in refresh, "the WPF Builder fills Your games with the form")
    check(re.search(r"FillGames\(\);[^\n]*\n\s*ShowPage\(ReadyPage\);", window) is not None,
          "the WPF Builder fills Your games again when a build ends")
    xaml = (ROOT / "builder" / "HookshotBuilder" / "MainWindow.xaml").read_text(encoding="utf-8")
    for name in ("GamesBox", "GameList"):
        check('x:Name="%s"' % name in xaml, "MainWindow.xaml has %s" % name)


def check_wpf_fixture(dotnet, outs):
    """Games.cs and tests/builder_games_test.cs, compiled without WPF, over the same placeholder folders: every game, folder and
    base the same as built_games', and the folder rule's answers the same"""
    pairs = [("C:\\Games\\Hookshot", "oot"), ("C:\\Games\\Hookshot\\", "mkw"), ("C:\\Games\\Hookshot/", "dkb"), ("", "mkw")]
    bases = ["C:\\Games\\Hookshot-mkw", "C:\\Games\\Hookshot-dkb\\", "C:\\Games\\Hookshot", "C:\\Games\\-mkw", "D:\\x-oot"]
    want = []
    for out in outs:
        want.append("== " + out)
        want += ["%s\t%s\t%s\t%s" % (g["key"], g["name"], g["folder"], g["base"]) for g in game_folders.built_games(out, windows_exe)]
    want += ["folder\t%s\t%s\t%s" % (b, k, game_folders.folder(b, k)) for b, k in pairs]
    want += ["base\t%s\t%s" % (b, game_folders.base_of(b)) for b in bases]
    with tempfile.TemporaryDirectory(prefix="builder-games-") as folder:
        root = Path(folder)
        shutil.copyfile(ROOT / "tests" / "builder_games_test.cs", root / "Program.cs")
        shutil.copyfile(ROOT / "builder" / "HookshotBuilder" / "Games.cs", root / "Games.cs")
        (root / "NuGet.Config").write_text("<configuration><packageSources><clear/></packageSources></configuration>", encoding="utf-8")
        project = root / "Fixture.csproj"
        args = list(outs) + ["--folder"] + [x for pair in pairs for x in pair] + ["--base"] + bases
        r = run_fixture(dotnet, project, args, "net8.0")
        if r.returncode and no_net8_pack(r.stdout + r.stderr) and os.environ.get("GITHUB_ACTIONS") != "true":
            # (a PC whose .NET SDK is a later one alone, as tests/test_updater_asset_digest.py: built for that SDK's
            # framework, with a line saying so. On a GitHub runner .NET 8 is installed, so it fails instead)
            sdk = subprocess.run([dotnet, "--version"], capture_output=True, text=True, check=True).stdout.strip()
            framework = "net%s.0" % sdk.split(".")[0]
            print("this .NET SDK (%s) has no .NET 8 reference pack: Games.cs is built for %s, not the Builder's net8.0"
                  % (sdk, framework), flush=True)
            r = run_fixture(dotnet, project, args, framework)
        if r.returncode:
            print(r.stdout + r.stderr)
            check(False, "Games.cs's fixture did not build or run")
            return
        got = [line for line in r.stdout.splitlines() if line.strip()]
        check(got == want, "Games.cs lists what game_folders.py lists:\n  got  %r\n  want %r" % (got, want))


def run_fixture(dotnet, project, args, framework):
    project.write_text('''<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>
<OutputType>Exe</OutputType><TargetFramework>%s</TargetFramework><ImplicitUsings>enable</ImplicitUsings>
<Nullable>enable</Nullable><TreatWarningsAsErrors>true</TreatWarningsAsErrors><NuGetAudit>false</NuGetAudit>
</PropertyGroup></Project>''' % framework, encoding="utf-8")
    return subprocess.run([dotnet, "run", "--project", str(project), "--configuration", "Release", "--"] + args,
                          capture_output=True, text=True, encoding="utf-8")


def no_net8_pack(out):
    """A build that failed only for want of .NET 8's reference pack: NU1100 with the sources cleared, or NU1101 for the
    pack where the SDK keeps a package folder of its own (Ubuntu's: /usr/lib/dotnet/library-packs)"""
    return "NU1100" in out or "NU1101: Unable to find package Microsoft.NETCore.App.Ref" in out


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dotnet", default="dotnet")
    a = p.parse_args()
    with tempfile.TemporaryDirectory(prefix="builder-games-") as tmp:
        outs = check_game_folders(tmp)
        check_linux_builder(tmp)
        check_qt_builder()
        check_wpf_builder()
        dotnet = shutil.which(a.dotnet)
        if dotnet:
            check_wpf_fixture(dotnet, outs)
        elif os.name == "nt":
            check(False, "no .NET 8 SDK here: Games.cs's fixture is the Windows run's to check")
        else:
            print("Games.cs not compiled: no .NET here (checked on Windows)")
    if failures:
        sys.exit(1)
    print("PASS: Your games lists each built game in its folder by the rule, in every Builder, and plays it as --play/--game does")


if __name__ == "__main__":
    main()
