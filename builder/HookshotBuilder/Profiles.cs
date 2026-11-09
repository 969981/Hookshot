using System.IO;
using System.Text.RegularExpressions;

namespace HookshotBuilder;

// The launcher's profiles, as builder/linux/profiles.py keeps them on Linux (the same profiles.txt): each one is a user of the
// game with saves of its own, save\<id> in the game's user folder (user in the game folder on Windows: host/hos_main.c).
//
// The game plays the profile HOOKSHOT_PROFILE=<id> names (host/nn_applet.c); without it, the default profile, the one user
// every build had before profiles, so its saves stay where they were. The list is profiles.txt in the user folder, one
// "<id> <name>" a line. An id is 32 hex digits, the save folder's name (host/nn_fs.c).
//
// A save from a console or an emulator is imported into a profile: the folder of the game's save data (account.sav,
// option.sav, slot_00 ... slot_05) is copied over the profile's. The profile's saves before it are kept in
// save-backups\<id>-<time> in the user folder. Removing a profile moves its saves there too: nothing is deleted.
public static class Profiles
{
    public const string Default = "5a3c9e1f0b7d24680000000000000001";   // (the uid host/nn_applet.c always had)
    const string Prefix = "5a3c9e1f0b7d2468";
    const string Title = "0400486024dec000";   // (the game's program id: an emulator keeps its saves in a folder of that name)
    const string ListName = "profiles.txt";
    public const int NameMax = 32;
    static readonly Regex Id = new("^[0-9a-f]{32}$");
    static readonly Regex Slot = new("^slot_[0-9]{2}$", RegexOptions.IgnoreCase);

    public sealed record Profile(string Id, string Name);

    /// The folder the game keeps its saves in when the launcher starts it: user in the game folder
    public static string UserDir(string folder) => Path.Combine(folder, "user");

    /// The name the profile list shows: the default profile says it is, whatever it is called, so that it is clear why it
    /// cannot be removed
    public static string ShownName(Profile p) => p.Id == Default ? $"{p.Name} (default)" : p.Name;

    public static string CleanName(string? name)
    {
        var words = string.Join(" ", (name ?? "").Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries));
        return words.Length > NameMax ? words[..NameMax] : words;
    }

    /// The profiles, the default one first
    public static List<Profile> Load(string user)
    {
        var list = new List<Profile>();
        try
        {
            foreach (var raw in File.ReadAllLines(Path.Combine(user, ListName)))
            {
                var line = raw.Trim();
                var space = line.IndexOf(' ');
                var id = (space < 0 ? line : line[..space]).ToLowerInvariant();
                var name = space < 0 ? "" : line[(space + 1)..];
                if (Id.IsMatch(id) && id != new string('0', 32) && list.All(p => p.Id != id))
                    list.Add(new Profile(id, CleanName(name) is { Length: > 0 } n ? n : id[^4..]));
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        var first = list.FindIndex(p => p.Id == Default);
        if (first < 0) list.Insert(0, new Profile(Default, "Player"));
        else if (first > 0)
        {
            var d = list[first];
            list.RemoveAt(first);
            list.Insert(0, d);
        }
        return list;
    }

    static void Store(string user, IEnumerable<Profile> profiles)
    {
        Directory.CreateDirectory(user);
        var path = Path.Combine(user, ListName);
        var lines = new List<string> { "# Hookshot's profiles: the save folder (save/<id>) and the name the launcher shows" };
        lines.AddRange(profiles.Select(p => $"{p.Id} {p.Name}"));
        File.WriteAllText(path + ".tmp", string.Join("\n", lines) + "\n");
        File.Move(path + ".tmp", path, overwrite: true);
    }

    /// The profile Play starts the game with: the one chosen last, while it is still on the list
    public static string Chosen(string folder, string? chosen) =>
        chosen != null && Load(UserDir(folder)).Any(p => p.Id == chosen) ? chosen : Default;

    /// A new profile, with no saves yet: the game starts it at an empty file select. Returns its id.
    public static string Add(string user, string? name)
    {
        var clean = CleanName(name);
        if (clean.Length == 0) throw new ArgumentException("Give the profile a name.");
        var list = Load(user);
        if (list.Any(p => string.Equals(p.Name, clean, StringComparison.OrdinalIgnoreCase)))
            throw new ArgumentException($"There is a profile called \"{clean}\" already.");
        var taken = new HashSet<string>(list.Select(p => p.Id));
        try
        {
            foreach (var d in Directory.EnumerateDirectories(Path.Combine(user, "save")))   // (a removed profile's folder is not reused)
                taken.Add(Path.GetFileName(d).ToLowerInvariant());
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        var n = 2L;
        while (taken.Contains($"{Prefix}{n:x16}")) n++;
        var id = $"{Prefix}{n:x16}";
        list.Add(new Profile(id, clean));
        Store(user, list);
        return id;
    }

    public static void Rename(string user, string id, string? name)
    {
        var clean = CleanName(name);
        if (clean.Length == 0) throw new ArgumentException("Give the profile a name.");
        var list = Load(user);
        if (list.Any(p => p.Id != id && string.Equals(p.Name, clean, StringComparison.OrdinalIgnoreCase)))
            throw new ArgumentException($"There is a profile called \"{clean}\" already.");
        Store(user, list.Select(p => p.Id == id ? p with { Name = clean } : p));
    }

    /// The folder "Open save folder" shows: the profile's saves, or the nearest folder above them that is there. None is
    /// made: an empty save folder would read to the game as a save that exists.
    public static string? FolderToShow(string user, string id) =>
        new[] { Path.Combine(user, "save", id), Path.Combine(user, "save"), user }.FirstOrDefault(Directory.Exists);

    /// Moves the profile's save folder to save-backups; returns where, or null when there were no saves
    static string? Backup(string user, string id)
    {
        var saves = Path.Combine(user, "save", id);
        if (!Directory.Exists(saves)) return null;
        if (!Directory.EnumerateFileSystemEntries(saves).Any())
        {
            Directory.Delete(saves);
            return null;
        }
        var root = Path.Combine(user, "save-backups");
        Directory.CreateDirectory(root);
        var stamp = DateTime.Now.ToString("yyyy-MM-dd_HH-mm-ss");
        var dest = Path.Combine(root, $"{id}-{stamp}");
        for (var k = 2; Directory.Exists(dest) || File.Exists(dest); k++) dest = Path.Combine(root, $"{id}-{stamp}-{k}");
        Directory.Move(saves, dest);
        return dest;
    }

    /// Takes the profile off the list; its saves go to save-backups. Returns where they went, or null.
    public static string? Remove(string user, string id)
    {
        if (id == Default)
            throw new ArgumentException("The default profile cannot be removed: the game plays it when it is started without the launcher.");
        var moved = Backup(user, id);
        Store(user, Load(user).Where(p => p.Id != id));
        return moved;
    }

    /// A folder of the game's save data: account.sav, option.sav or slot_NN in it
    public static bool IsSave(string path)
    {
        try
        {
            foreach (var e in Directory.EnumerateFileSystemEntries(path))
            {
                var name = Path.GetFileName(e);
                if (name.Equals("account.sav", StringComparison.OrdinalIgnoreCase) || name.Equals("option.sav", StringComparison.OrdinalIgnoreCase)
                    || (Slot.IsMatch(name) && Directory.Exists(e)))
                    return true;
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        return false;
    }

    /// The game's save folders in what the player chose: the save folder itself, or an emulator's save folder
    /// (save\0000000000000000\(user)\0400486024DEC000), one for each user it has. The newest first.
    public static List<string> FindSaves(string path, int depth = 6)
    {
        if (IsSave(path)) return new List<string> { path };
        var found = new List<string>();
        void Walk(string dir, int level)
        {
            IEnumerable<string> subs;
            try { subs = Directory.EnumerateDirectories(dir).ToList(); }
            catch (IOException) { return; }
            catch (UnauthorizedAccessException) { return; }
            foreach (var sub in subs)
            {
                if (new DirectoryInfo(sub).LinkTarget != null) continue;
                if (Path.GetFileName(sub).Equals(Title, StringComparison.OrdinalIgnoreCase) && IsSave(sub)) found.Add(sub);
                else if (level + 1 < depth) Walk(sub, level + 1);
            }
        }
        Walk(path, 0);
        return found.OrderByDescending(LastSaved).ToList();
    }

    public static DateTime LastSaved(string path)
    {
        var newest = DateTime.MinValue;
        try
        {
            foreach (var f in Directory.EnumerateFiles(path, "*", SearchOption.AllDirectories))
                if (File.GetLastWriteTime(f) is var t && t > newest) newest = t;
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        return newest;
    }

    /// One line for a save folder: where it is under the folder the player chose, how many save slots, when last saved
    public static string Describe(string path, string chosen)
    {
        var where = Path.GetRelativePath(chosen, path);
        if (where == ".") where = Path.GetFileName(Path.TrimEndingDirectorySeparator(path)) is { Length: > 0 } n ? n : path;
        var slots = 0;
        try
        {
            slots = Directory.EnumerateDirectories(path).Count(d => Slot.IsMatch(Path.GetFileName(d)) && Directory.EnumerateFileSystemEntries(d).Any());
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        var saved = LastSaved(path);
        var when = saved == DateTime.MinValue ? "never" : saved.ToString("yyyy-MM-dd HH:mm");
        return $"{where}  ({slots} save slot{(slots == 1 ? "" : "s")}, last saved {when})";
    }

    static void CopyTree(string from, string to)
    {
        Directory.CreateDirectory(to);
        foreach (var f in Directory.EnumerateFiles(from)) File.Copy(f, Path.Combine(to, Path.GetFileName(f)));
        foreach (var d in Directory.EnumerateDirectories(from)) CopyTree(d, Path.Combine(to, Path.GetFileName(d)));
    }

    static bool Within(string path, string root) =>
        path.Equals(root, StringComparison.OrdinalIgnoreCase) ||
        path.StartsWith(root.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);

    /// Copies the save folder src over the profile's saves. The profile's saves before it go to save-backups: returns where,
    /// or null when it had none. A failed copy leaves the profile's saves as they were.
    public static string? Import(string src, string user, string id)
    {
        if (!IsSave(src))
            throw new ArgumentException($"{src} has none of the game's save files (account.sav, option.sav, slot_00 ...).");
        var saves = Path.Combine(user, "save", id);
        var fullSrc = Path.GetFullPath(src);
        var fullSaves = Path.GetFullPath(saves);
        if (Within(fullSrc, fullSaves) || Within(fullSaves, fullSrc)) throw new ArgumentException("These are the profile's own saves.");
        Directory.CreateDirectory(Path.GetDirectoryName(saves)!);
        var staging = saves + ".importing";
        if (Directory.Exists(staging)) Directory.Delete(staging, recursive: true);
        try { CopyTree(src, staging); }
        catch
        {
            try { Directory.Delete(staging, recursive: true); } catch (Exception) { }
            throw;
        }
        var moved = Backup(user, id);
        Directory.Move(staging, saves);
        return moved;
    }
}
