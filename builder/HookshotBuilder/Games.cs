using System.IO;

namespace HookshotBuilder;

/// "Your games": the games built beside the Builder's game folder, each with Play. The same rule as nx2/game_folders.py
/// (built_games), which the Linux and Qt Builders use: every game profile that builds (its game pack, packs/<key>/pack.json),
/// in its own folder, the default game's <base> and another's <base>-<key>, base being the Builder's game folder (or the base
/// it is another game's folder of). A folder is listed when it holds a built game: image.bin and the game (bin\hookshot.exe,
/// or hookshot.exe in a folder built before the launcher). Play starts that folder as `--play <base> --game <key>` does
/// (builder/setup/setup.c play_target), the path RecompLauncher's tiles take. tests/test_builder_games.py holds the profiles
/// and the folders to nx2/game_folders.py's, and tests/test_game_packs.py the table and the default key to the packs
static class Games
{
    /// nx2/packs.py DEFAULT (game_profile.DEFAULT): its folder takes no suffix, so Ocarina's stays where it always was
    public const string DefaultKey = "oot";

    /// Every pack's profile that builds, in nx2/packs.py's order: its key, the game's name (pack.json name), main's build id
    /// (builds[0].build_id). A copy of the packs' data until the Builder reads `game_folders.py --library --json`
    public static readonly (string Key, string Name, string BuildId)[] Profiles =
    {
        ("oot", "The Legend of Zelda: Ocarina of Time", "e0f3ccfaf50065ae4ea01eb5aeb13b1a45da3348"),
        ("dkb", "Donkey Kong Bananza", "e25c90c1c136638885659fd55bf8a03b3403489d"),
        ("mkw", "Mario Kart World", "9e244438c3d8ef1c703a6a85ef99c4f000000000"),
    };

    /// A built game: the profile whose folder it is (Key: Play's --game), the game the folder holds (Name), its folder, the base
    public sealed record Game(string Key, string Name, string Folder, string Base);

    /// "" for the default game, "-<key>" for any other
    public static string Suffix(string key) => key == DefaultKey ? "" : "-" + key;

    /// The game's own folder: base for the default game, base-<key> otherwise (a trailing separator on base dropped first)
    public static string Folder(string baseFolder, string key)
    {
        var b = baseFolder.TrimEnd('\\', '/');
        return (b.Length > 0 ? b : baseFolder) + Suffix(key);
    }

    /// The base the folders are named from: the Builder's game folder, or the base it is another game's folder of
    public static string BaseOf(string folder)
    {
        var f = folder.TrimEnd('\\', '/');
        if (f.Length == 0) f = folder;
        foreach (var p in Profiles)
        {
            var s = Suffix(p.Key);
            if (s.Length > 0 && f.Length > s.Length && f.EndsWith(s, StringComparison.Ordinal) && f[^(s.Length + 1)] is not ('\\' or '/'))
                return f[..^s.Length];
        }
        return f;
    }

    /// The game in a folder: bin\hookshot.exe below its launcher, or hookshot.exe in a folder built before the launcher; null
    /// when neither is there
    public static string? GameExe(string folder)
    {
        var nested = Path.Combine(folder, "bin", "hookshot.exe");
        if (File.Exists(nested)) return nested;
        var flat = Path.Combine(folder, "hookshot.exe");
        return File.Exists(flat) ? flat : null;
    }

    /// The name of the game a folder holds, from the copy of the game files it keeps for updates (.hookshot_exefs\main: an
    /// NSO whose module id is main's build id), or null. The Builder builds into the folder the player chose, so a game can
    /// sit in another's folder by the rule: it is named by what it is
    public static string? FolderGame(string folder)
    {
        try
        {
            var head = new byte[0x60];
            using (var f = File.OpenRead(Path.Combine(folder, ".hookshot_exefs", "main")))
                if (f.ReadAtLeast(head, head.Length, throwOnEndOfStream: false) < head.Length) return null;
            if (head[0] != 'N' || head[1] != 'S' || head[2] != 'O' || head[3] != '0') return null;
            var id = Convert.ToHexString(head, 0x40, 0x20).ToLowerInvariant();
            foreach (var p in Profiles)
                if (p.BuildId.StartsWith(id, StringComparison.Ordinal) || id.StartsWith(p.BuildId, StringComparison.Ordinal))
                    return p.Name;
        }
        catch (Exception) { }
        return null;
    }

    /// Each profile's folder from the Builder's game folder that holds a built game, in the profiles' order
    public static List<Game> Built(string? folder)
    {
        var games = new List<Game>();
        if (string.IsNullOrWhiteSpace(folder)) return games;
        var b = BaseOf(folder);
        foreach (var p in Profiles)
        {
            var f = Folder(b, p.Key);
            try
            {
                if (!File.Exists(Path.Combine(f, "image.bin")) || GameExe(f) == null) continue;
            }
            catch (Exception) { continue; }
            games.Add(new Game(p.Key, FolderGame(f) ?? p.Name, f, b));
        }
        return games;
    }
}
