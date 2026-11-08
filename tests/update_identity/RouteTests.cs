using HookshotBuilder;

static class RouteTests
{
    static int checks;
    static void Check(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
        checks++;
    }

    public static void Run()
    {
        var root = Path.Combine(Path.GetTempPath(), "hookshot-ui-routes-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        var latest = new ReleaseIdentity("authored", new string('b', 40));
        var old = latest with { Revision = new string('a', 40) };
        void Stamp(string? revision)
        {
            File.WriteAllText(Path.Combine(root, "hookshot_version.txt"), "version=authored\n" +
                (revision == null ? "" : "revision=" + revision + "\n"));
        }
        try
        {
            Directory.CreateDirectory(Path.Combine(root, ".hookshot_exefs"));
            Directory.CreateDirectory(Path.Combine(root, "romfs"));
            Stamp(old.Revision);
            var game = GameInstallation.Read(root);
            Check(game.CanRebuild && game.Identity == old, "read actual kept-folder revision");
            Check(!game.Current(latest) && game.UpdateAvailable(latest), "same tag, changed source offers update");
            Check(game.Mode(latest, latest.Tag, latest, false) == BuilderMode.UpdateGame, "files page uses changed-source update route");
            Check(!game.Current(null) && !game.UpdateAvailable(null), "offline is never current or a verified update");
            Check(game.Mode(null, old.Tag, old, false) == BuilderMode.Play, "offline game stays playable without a current claim");
            Stamp(latest.Revision);
            game = GameInstallation.Read(root);
            Check(game.Current(latest) && !game.UpdateAvailable(latest), "tag and revision together are current");
            Check(game.Mode(latest, latest.Tag, latest, false) == BuilderMode.Play, "current game keeps Play");
            Check(game.Mode(latest, latest.Tag, latest, true) == BuilderMode.Build, "explicit source selection keeps fresh-build route");
            Check(game.Mode(latest, old.Tag, old, true) == BuilderMode.UpdateBuilder, "source build still upgrades an older packaged Builder");

            foreach (var revision in new string?[] { null, "not-a-revision" })
            {
                Stamp(revision);
                game = GameInstallation.Read(root);
                Check(game.Identity == null && !game.Current(latest), "legacy/malformed metadata is not silently current");
                Check(game.UpdateAvailable(latest) && game.Mode(latest, latest.Tag, latest, false) == BuilderMode.UpdateGame,
                    "kept legacy files can be manually verified by one checked rebuild");
            }
            Directory.Delete(Path.Combine(root, ".hookshot_exefs"));
            game = GameInstallation.Read(root);
            Check(!game.CanRebuild && !game.UpdateAvailable(latest), "missing kept files never offer an in-place rebuild");
            Check(game.Mode(latest, latest.Tag, latest, false) == BuilderMode.Play, "legacy folder without kept files stays playable");

            File.Delete(Path.Combine(root, "hookshot_version.txt"));
            game = GameInstallation.Read(root);
            Check(game.Tag == null && !game.Current(latest), "missing metadata is unknown");
            Check(game.Mode(latest, old.Tag, old, false) == BuilderMode.UpdateBuilder, "new game uses newer Builder first");
            Check(game.Mode(latest, old.Tag, null, false) == BuilderMode.UpdateBuilder, "legacy packaged Builder needs verification too");
            Check(game.Mode(latest, null, null, false) == BuilderMode.Build, "unidentified source Builder retains ordinary build mode");

            var flat = Path.Combine(root, "hookshot.exe");
            File.WriteAllText(flat, "authored launcher or legacy game, never executed");
            Check(GameInstallation.Executable(root) == flat, "legacy flat game route retained");
            Directory.CreateDirectory(Path.Combine(root, "bin"));
            var binary = Path.Combine(root, "bin", "hookshot.exe");
            File.WriteAllText(binary, "authored game, never executed");
            Check(GameInstallation.Executable(root) == binary, "v2 launches the bin game rather than the root launcher");

            var gui = Path.Combine(root, "authored Builder", "HookshotBuilder.exe");
            var start = BuilderLaunch.StartInfo(gui, Array.Empty<string>());
            Check(!start.UseShellExecute && start.ArgumentList.Count == 0 && start.WorkingDirectory == Path.GetDirectoryName(gui),
                "Builder-only update opens the checked GUI without selecting a game or running setup");
            start = BuilderLaunch.StartInfo(gui, new[] { "--launch", root });
            Check(start.ArgumentList.SequenceEqual(new[] { "--launch", root }), "launch-folder handoff keeps exact argv");
            var update = new UpdateRequest(root, latest.Tag, latest.Revision, true);
            start = update.StartInfo(gui);
            Check(UpdateRequest.Parse(start.ArgumentList.ToArray()) == update && !start.ArgumentList.Contains("--nup"),
                "checked game-update handoff preserves root folder, revision and fetched state");
            Console.WriteLine($"update UI routes: {checks} actual decision/folder/argv checks passed; no UI or process ran");
        }
        finally { Directory.Delete(root, true); }
    }
}
