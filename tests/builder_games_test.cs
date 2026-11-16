using HookshotBuilder;

// Games.cs over placeholder folders tests/test_builder_games.py made (no game): for each argument, a game folder as the Builder's
// form holds it, the games Games.Built lists, one line each (key, name, folder, base, tab-separated) under a "== <folder>" line;
// then the folder rule's answers for the pairs after "--folder" (base, key) and "--base" (folder). The test compares every line
// with nx2/game_folders.py's
static class Program
{
    static int Main(string[] args)
    {
        var mode = "built";
        for (var i = 0; i < args.Length; i++)
        {
            if (args[i] is "--folder" or "--base") { mode = args[i]; continue; }
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
                default:
                    Console.WriteLine($"base\t{args[i]}\t{Games.BaseOf(args[i])}");
                    break;
            }
        }
        return 0;
    }
}
