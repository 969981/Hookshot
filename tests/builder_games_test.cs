using HookshotBuilder;

// Games.cs over placeholder folders tests/test_builder_games.py made (no game): for each argument, a game folder as the Builder's
// form holds it, the games Games.Built lists, one line each (key, name, folder, base, tab-separated) under a "== <folder>" line;
// then the folder rule's answers for the pairs after "--folder" (base, key) and "--base" (folder); and the library's readers:
// "--key" (a folder's game), "--shortcut" (a key's shortcut name), "--title" (a name's two lines), "--line" (an @@game line),
// "--copy" (a check's sentence), "--library" (a file of --library --json), "--results" (a file of --update-all lines),
// "--outcome" (how such a run ended), "--fallback" (the library without the tools' answer) and "--shortcut-folder". The test
// compares every line with nx2/game_folders.py's
static class Program
{
    static string Or(string? s) => s ?? "None";

    static int Main(string[] args)
    {
        var mode = "built";
        for (var i = 0; i < args.Length; i++)
        {
            if (args[i] is "--folder" or "--base" or "--key" or "--shortcut" or "--title" or "--line" or "--copy" or "--library" or "--results"
                or "--outcome" or "--fallback" or "--shortcut-folder")
            {
                mode = args[i];
                continue;
            }
            switch (mode)
            {
                case "built":
                    Console.WriteLine("== " + args[i]);
                    foreach (var g in Games.Built(args[i]))
                        Console.WriteLine($"{g.Key}\t{g.Name}\t{g.Folder}\t{g.Base}");
                    break;
                case "--folder":
                    Console.WriteLine($"folder\t{args[i]}\t{args[i + 1]}\t{Games.Folder(args[i], args[i + 1])}");
                    i++;
                    break;
                case "--base":
                    Console.WriteLine($"base\t{args[i]}\t{Games.BaseOf(args[i])}");
                    break;
                case "--key":
                    Console.WriteLine($"key\t{args[i]}\t{Games.FolderKey(args[i])}");
                    break;
                case "--shortcut":
                    Console.WriteLine($"shortcut\t{args[i]}\t{Games.ShortcutName(args[i])}");
                    break;
                case "--title":
                    var (series, title) = Games.TitleParts(args[i]);
                    Console.WriteLine($"title\t{args[i]}\t{series}\t{title}");
                    break;
                case "--line":
                    var l = Games.ReadGameLine(args[i]);
                    Console.WriteLine(l == null ? $"line\tNone" :
                                      l.Kind == "start" ? $"line\tstart\t{l.Key}\t{l.Folder}" : $"line\tend\t{l.Key}\t{l.Result}\t{Or(l.Text)}");
                    break;
                case "--copy":
                    Console.WriteLine($"copy\t{args[i]}\t{Or(Games.CopyKey(args[i]))}");
                    break;
                case "--library":
                    var games = Games.ParseLibrary(File.ReadAllText(args[i]));
                    foreach (var g in games)
                        Console.WriteLine($"lib\t{g.Key}\t{g.Name}\t{g.Folder}\t{g.Base}\t{g.Built}\t{string.Join("|", g.OtherFolders)}\t{g.OwnFolder}\t" +
                                          $"{Or(g.OwnFolderHolds)}\t{Or(g.Version)}\t{g.Kept}\t{(g.Update == null ? "None" : g.Update.ToString())}");
                    Console.WriteLine("installed\t" + string.Join("|", Games.InstalledFolders(games)));
                    break;
                case "--outcome":   // (a file of Update all's lines, stopped 0 or 1, the folders it was to go through, |-separated)
                    var (done, unfinished) = Games.Outcome(Games.Results(File.ReadAllLines(args[i])),
                                                           args[i + 2].Split('|', StringSplitOptions.RemoveEmptyEntries), args[i + 1] == "1");
                    Console.WriteLine($"outcome\t{done}\t{string.Join("|", unfinished)}");
                    i += 2;
                    break;
                case "--fallback":
                    foreach (var g in Games.Fallback(args[i]))
                        Console.WriteLine($"fallback\t{g.Key}\t{g.Folder}\t{g.Built}\t{g.Kept}\t{Or(g.Version)}\t{(g.Update == null ? "None" : g.Update.ToString())}");
                    break;
                case "--shortcut-folder":
                    Console.WriteLine($"shortcut-folder\t{args[i]}\t{Games.ShortcutFolder(args[i])}");
                    break;
                default:
                    foreach (var r in Games.Results(File.ReadAllLines(args[i])))
                        Console.WriteLine($"result\t{r.Key}\t{r.Folder}\t{r.Result}\t{Or(r.Text)}");
                    break;
            }
        }
        return 0;
    }
}
