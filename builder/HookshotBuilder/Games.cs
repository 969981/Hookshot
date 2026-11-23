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

    /// The profile key of the game a folder holds, from the copy of the game files it keeps for updates (.hookshot_exefs\main: an
    /// NSO whose module id is main's build id), or null. The Builder builds into the folder the player chose, so a game can
    /// sit in another's folder by the rule: it is named by what it is
    public static string? FolderGameKey(string folder)
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
                    return p.Key;
        }
        catch (Exception) { }
        return null;
    }

    /// The name of the game a folder holds (FolderGameKey's), or null
    public static string? FolderGame(string folder) => FolderGameKey(folder) is { } key ? NameOf(key) : null;

    /// A profile's game (its name), or the key itself for one no profile has
    public static string NameOf(string key)
    {
        foreach (var p in Profiles)
            if (p.Key == key) return p.Name;
        return key;
    }

    /// The game a folder is: its kept copy's (FolderGameKey), else the game whose folder it is by the rule (base-key is that
    /// key's, any other folder the default game's). The launcher page and the shortcuts are named by it (game_folders.py
    /// folder_key)
    public static string FolderKey(string folder)
    {
        if (FolderGameKey(folder) is { } held) return held;
        var f = folder.TrimEnd('\\', '/');
        if (f.Length == 0) f = folder;
        var b = BaseOf(f);
        foreach (var p in Profiles)
            if (string.Equals(Folder(b, p.Key), f, StringComparison.Ordinal)) return p.Key;
        return DefaultKey;
    }

    /// A game's shortcut name (its .lnk): "Hookshot" for the default game, the name its shortcut always had, so a player's
    /// Hookshot.lnk is the one made again; "<game> (Hookshot)" for another, without the letters a file name cannot hold
    /// (game_folders.py shortcut_name)
    public static string ShortcutName(string key) =>
        key == DefaultKey ? "Hookshot" : System.Text.RegularExpressions.Regex.Replace(NameOf(key), "[\\\\/:*?\"<>|]", "").Trim() + " (Hookshot)";

    /// The launcher page's two lines for a game: the series above, in capitals, and the title, split at the name's first ": "
    /// ("THE LEGEND OF ZELDA", "Ocarina of Time"); ("", name) for a name without one (game_folders.py title_parts)
    public static (string Series, string Title) TitleParts(string name)
    {
        var at = name.IndexOf(": ", StringComparison.Ordinal);
        return at > 0 && at + 2 < name.Length ? (name[..at].ToUpperInvariant(), name[(at + 2)..]) : ("", name);
    }

    /// The profile key of a copy make_game.py --check found supported, from its sentence ("... (build e0f3ccfa)."), or null
    /// (game_folders.py copy_key): a copy of another game than the folder's goes to its own folder (--base)
    public static string? CopyKey(string? checkText)
    {
        var m = System.Text.RegularExpressions.Regex.Match(checkText ?? "", @"\(build ([0-9a-f]{8,64})\)");
        if (!m.Success) return null;
        foreach (var p in Profiles)
            if (p.BuildId.StartsWith(m.Groups[1].Value, StringComparison.Ordinal)) return p.Key;
        return null;
    }

    /// One of make_game.py --update-all's framing lines: "@@game start <key> <folder>" (Folder) or "@@game end <key> <result>
    /// [<text>]" (Result, Text); null for any other line (game_folders.py update_line)
    public sealed record GameLine(string Kind, string Key, string Folder, string Result, string? Text);

    public static GameLine? ReadGameLine(string line)
    {
        var m = System.Text.RegularExpressions.Regex.Match(line.TrimEnd('\r', '\n'), @"^@@game (start|end) ([a-z0-9]{1,16})(?: (.*))?$");
        if (!m.Success) return null;
        var rest = m.Groups[3].Success ? m.Groups[3].Value : "";
        if (m.Groups[1].Value == "start") return new GameLine("start", m.Groups[2].Value, rest, "", null);
        var space = rest.IndexOf(' ');
        return space < 0 ? new GameLine("end", m.Groups[2].Value, "", rest, null)
                         : new GameLine("end", m.Groups[2].Value, "", rest[..space], rest[(space + 1)..] is { Length: > 0 } t ? t : null);
    }

    /// Update all's outcomes from its lines: each "end" line, with the folder of the "start" line before it
    public static List<GameLine> Results(IEnumerable<string> lines)
    {
        var results = new List<GameLine>();
        var folder = "";
        foreach (var line in lines)
            if (ReadGameLine(line) is { } g)
            {
                if (g.Kind == "start") folder = g.Folder;
                else results.Add(g with { Folder = folder });
            }
        return results;
    }

    /// One game of the library (nx2/game_folders.py --library --json, its fields): every game that builds, installed or not,
    /// where under the base, its version, and whether this engine would update it (Update: null when it can't be told)
    public sealed record LibraryGame(string Key, string Name, string Folder, string Base, bool Built, string[] OtherFolders,
                                     string OwnFolder, string? OwnFolderHolds, string? Version, bool Kept, bool? Update);

    /// --library --json read. Throws on text that is not that list
    public static List<LibraryGame> ParseLibrary(string json)
    {
        using var doc = System.Text.Json.JsonDocument.Parse(json);
        var games = new List<LibraryGame>();
        static string? Text(System.Text.Json.JsonElement e, string name) =>
            e.TryGetProperty(name, out var v) && v.ValueKind == System.Text.Json.JsonValueKind.String ? v.GetString() : null;
        foreach (var g in doc.RootElement.EnumerateArray())
        {
            var update = g.TryGetProperty("update", out var u) && u.ValueKind is System.Text.Json.JsonValueKind.True or System.Text.Json.JsonValueKind.False
                ? u.GetBoolean() : (bool?)null;
            var others = g.TryGetProperty("other_folders", out var o) && o.ValueKind == System.Text.Json.JsonValueKind.Array
                ? o.EnumerateArray().Select(x => x.GetString() ?? "").ToArray() : Array.Empty<string>();
            games.Add(new LibraryGame(Text(g, "key") ?? "", Text(g, "name") ?? "", Text(g, "folder") ?? "", Text(g, "base") ?? "",
                                      g.TryGetProperty("built", out var b) && b.ValueKind == System.Text.Json.JsonValueKind.True, others,
                                      Text(g, "own_folder") ?? "", Text(g, "own_folder_holds"), Text(g, "version"),
                                      g.TryGetProperty("kept", out var k) && k.ValueKind == System.Text.Json.JsonValueKind.True, update));
        }
        return games;
    }

    /// The library when the tools could not answer (no Python, or an answer that did not read): the games Built finds, the others
    /// not installed; each folder's kept game files and version read here, and no update flag (null: "Installed", with Update).
    /// Never "built before updates came in" because the answer failed: only for a folder that keeps no game files
    public static List<LibraryGame> Fallback(string? folder)
    {
        var built = Built(folder);
        var games = new List<LibraryGame>();
        if (string.IsNullOrWhiteSpace(folder)) return games;
        var b = BaseOf(folder);
        foreach (var p in Profiles)
        {
            var g = built.FirstOrDefault(x => x.Key == p.Key);
            var f = g?.Folder ?? Folder(b, p.Key);
            var kept = g != null && Directory.Exists(Path.Combine(f, ".hookshot_exefs")) && Directory.Exists(Path.Combine(f, "romfs"));
            games.Add(new LibraryGame(p.Key, p.Name, f, b, g != null, Array.Empty<string>(), Folder(b, p.Key), null,
                                      g != null ? VersionIn(f) : null, kept, g != null && kept ? null : false));
        }
        return games;
    }

    /// hookshot_version.txt's version= in a folder, or null
    public static string? VersionIn(string folder)
    {
        try
        {
            foreach (var line in File.ReadAllLines(Path.Combine(folder, "hookshot_version.txt")))
                if (line.StartsWith("version=", StringComparison.Ordinal)) return line[8..].Trim() is { Length: > 0 } v ? v : null;
        }
        catch (Exception) { }
        return null;
    }

    /// How an Update all ended (game_folders.py update_all_outcome): Done only when it was not stopped (a failure, a non-zero
    /// exit, a cancel), every folder it was to go through has an outcome and none failed; Unfinished the folders it did not reach
    public static (bool Done, List<string> Unfinished) Outcome(IReadOnlyCollection<GameLine> results, IEnumerable<string> folders,
                                                              bool stopped)
    {
        var reached = new HashSet<string>(results.Select(r => r.Folder), StringComparer.OrdinalIgnoreCase);
        var unfinished = folders.Where(f => !reached.Contains(f)).ToList();
        return (!stopped && results.Count > 0 && unfinished.Count == 0 && !results.Any(r => r.Result == "failed"), unfinished);
    }

    /// The folder a game's shortcuts start (game_folders.py shortcut_folder): its own folder by the rule when that holds the
    /// game, so a game installed in two folders has its shortcut on its own one, never the other; else the folder given
    public static string ShortcutFolder(string folder)
    {
        var key = FolderKey(folder);
        var own = Folder(BaseOf(folder), key);
        try
        {
            var same = string.Equals(Path.GetFullPath(own).TrimEnd('\\', '/'), Path.GetFullPath(folder).TrimEnd('\\', '/'),
                                     StringComparison.OrdinalIgnoreCase);
            if (!same && File.Exists(Path.Combine(own, "image.bin")) && GameExe(own) != null && FolderGameKey(own) == key) return own;
        }
        catch (Exception) { }
        return folder;
    }

    /// The built folders a library names: each built game's folder and its other folders
    public static List<string> InstalledFolders(IEnumerable<LibraryGame> games) =>
        games.Where(g => g.Built).SelectMany(g => new[] { g.Folder }.Concat(g.OtherFolders)).ToList();

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
