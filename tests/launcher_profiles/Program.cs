// The Windows launcher's profiles (builder/HookshotBuilder/Profiles.cs): each one has saves of its own, a save from an
// emulator is imported into one without losing the saves it had, and profiles.txt reads as the Linux launcher writes it
// (builder/linux/profiles.py, tests/test_launcher_profiles.py).
//
//     dotnet run --project tests/launcher_profiles/LauncherProfilesTests.csproj
using HookshotBuilder;

static class Program
{
    static int checks;

    static void Require(bool ok, string message)
    {
        if (!ok) throw new Exception(message);
        checks++;
    }

    static void Throws<T>(Action action, string message) where T : Exception
    {
        try { action(); }
        catch (T) { checks++; return; }
        throw new Exception(message);
    }

    /// A save folder of the game as the console and emulators keep it
    static string Save(string folder, int slots = 2, string marker = "a")
    {
        Directory.CreateDirectory(folder);
        File.WriteAllText(Path.Combine(folder, "account.sav"), marker);
        File.WriteAllText(Path.Combine(folder, "option.sav"), marker);
        for (var n = 0; n < slots; n++)
        {
            var slot = Path.Combine(folder, $"slot_{n:00}");
            Directory.CreateDirectory(slot);
            File.WriteAllText(Path.Combine(slot, "progress.sav"), marker);
            File.WriteAllText(Path.Combine(slot, "caption.sav"), marker);
        }
        return folder;
    }

    static int Main()
    {
        var root = Path.Combine(Path.GetTempPath(), "hookshot-profiles-" + Guid.NewGuid().ToString("N"));
        try
        {
            Run(root);
        }
        catch (Exception x)
        {
            Console.Error.WriteLine("FAIL: " + x.Message);
            return 1;
        }
        finally
        {
            try { Directory.Delete(root, recursive: true); } catch (Exception) { }
        }
        Console.WriteLine($"PASS: {checks} checks");
        return 0;
    }

    static void Run(string root)
    {
        var game = Path.Combine(root, "game");
        var user = Profiles.UserDir(game);
        Require(user == Path.Combine(game, "user"), "The saves are not in the game folder's user folder.");

        // the default profile is the user every build had (host/nn_applet.c)
        var first = Profiles.Load(user);
        Require(first.Count == 1 && first[0] == new Profiles.Profile(Profiles.Default, "Player"), "The default profile is not Player.");
        Require(Profiles.Default == "5a3c9e1f0b7d24680000000000000001", "The default profile's id changed.");

        // new profiles get save folders of their own
        var a = Profiles.Add(user, "  Link  ");
        var b = Profiles.Add(user, "Zelda");
        var list = Profiles.Load(user);
        Require(list.Select(p => p.Name).SequenceEqual(new[] { "Player", "Link", "Zelda" }), "The new profiles are not on the list.");
        Require(new HashSet<string> { Profiles.Default, a, b }.Count == 3, "Two profiles share a save folder.");
        Require(a == "5a3c9e1f0b7d24680000000000000002", "The second profile's id is not the one profiles.py makes.");
        Throws<ArgumentException>(() => Profiles.Add(user, "link"), "A second profile called Link was made.");
        Throws<ArgumentException>(() => Profiles.Add(user, "   "), "A profile with no name was made.");
        Profiles.Rename(user, Profiles.Default, "Tim");
        Require(Profiles.Load(user)[0].Name == "Tim", "The rename did not stick.");
        Require(Profiles.ShownName(Profiles.Load(user)[0]) == "Tim (default)" && Profiles.ShownName(Profiles.Load(user)[1]) == "Link",
                "The list does not say which profile is the default.");
        Require(Profiles.Chosen(game, b) == b && Profiles.Chosen(game, "gone") == Profiles.Default && Profiles.Chosen(game, null) == Profiles.Default,
                "Play does not start the chosen profile, or the default one when it is gone.");

        // "Open save folder": the profile's saves, else the nearest folder above them; none is made
        Require(Profiles.FolderToShow(user, b) == user && !Directory.Exists(Path.Combine(user, "save", b)), "Open save folder made a folder.");
        Require(Profiles.FolderToShow(Path.Combine(root, "none"), b) == null, "Open save folder found a folder that is not there.");
        Save(Path.Combine(user, "save", b));
        Require(Profiles.FolderToShow(user, b) == Path.Combine(user, "save", b), "Open save folder does not show the profile's saves.");
        Directory.Delete(Path.Combine(user, "save", b), recursive: true);

        // profiles.txt as the Linux launcher writes it
        var file = File.ReadAllLines(Path.Combine(user, "profiles.txt"));
        Require(file.Length == 4 && file[0].StartsWith('#') && file[2] == a + " Link", "profiles.txt is not in profiles.py's form.");
        File.WriteAllText(Path.Combine(user, "profiles.txt"),
                          "# a comment\n5A3C9E1F0B7D24680000000000000005 Epona\nnot-an-id x\n5a3c9e1f0b7d24680000000000000001 Tim\n");
        list = Profiles.Load(user);
        Require(list.Count == 2 && list[0].Id == Profiles.Default && list[1] == new Profiles.Profile("5a3c9e1f0b7d24680000000000000005", "Epona"),
                "A profiles.txt written by hand is not read as profiles.py reads it.");

        // a removed profile keeps its saves, and its folder is not reused
        var epona = list[1].Id;
        Save(Path.Combine(user, "save", epona));
        var moved = Profiles.Remove(user, epona);
        Require(moved != null && File.Exists(Path.Combine(moved, "slot_00", "progress.sav")), "The removed profile's saves were not kept.");
        Require(!Directory.Exists(Path.Combine(user, "save", epona)), "The removed profile's save folder is still there.");
        Throws<ArgumentException>(() => Profiles.Remove(user, Profiles.Default), "The default profile was removed.");
        Save(Path.Combine(user, "save", "5a3c9e1f0b7d24680000000000000002"));
        Require(Profiles.Add(user, "Again") == "5a3c9e1f0b7d24680000000000000003", "A new profile took a folder that has saves.");

        // an emulator's save folder lists one save for each of its users, the newest first
        var emu = Path.Combine(root, "2026-11-09 save");
        var users = Path.Combine(emu, "0000000000000000");
        var older = Save(Path.Combine(users, "45EB517681BCEAF4B834E0FA511E3212", "0400486024DEC000"), slots: 6);
        var newer = Save(Path.Combine(users, "533C42419DD767B01211726DADA4670A", "0400486024DEC000"), slots: 6, marker: "new");
        Save(Path.Combine(users, "45EB517681BCEAF4B834E0FA511E3212", "0400C3F00006E000", "Play"));   // (another game's)
        foreach (var f in Directory.EnumerateFiles(older, "*", SearchOption.AllDirectories)) File.SetLastWriteTime(f, new DateTime(2026, 11, 8, 22, 25, 0));
        var found = Profiles.FindSaves(emu);
        Require(found.SequenceEqual(new[] { newer, older }), "The emulator's two saves were not found, newest first.");
        Require(Profiles.FindSaves(newer).SequenceEqual(new[] { newer }), "A save folder chosen itself was not found.");
        Require(Profiles.FindSaves(Path.Combine(root, "nothing")).Count == 0, "A save was found where there is none.");
        var line = Profiles.Describe(older, emu);
        Require(line == Path.Combine("0000000000000000", "45EB517681BCEAF4B834E0FA511E3212", "0400486024DEC000") +
                        "  (6 save slots, last saved 2026-11-08 22:25)", "The save's line reads: " + line);

        // an import replaces the profile's saves and keeps the old ones; the source is only read
        var own = Save(Path.Combine(user, "save", a), slots: 1, marker: "old");
        moved = Profiles.Import(newer, user, a);
        Require(File.ReadAllText(Path.Combine(own, "slot_05", "progress.sav")) == "new", "The import did not bring the save in.");
        Require(moved != null && File.ReadAllText(Path.Combine(moved, "slot_00", "progress.sav")) == "old", "The profile's saves before were not kept.");
        Require(File.Exists(Path.Combine(newer, "account.sav")), "The import moved the source.");
        Require(!Directory.Exists(own + ".importing"), "The import left its staging folder.");
        Require(Profiles.Import(newer, user, b) == null, "A profile with no saves kept a backup.");
        Throws<ArgumentException>(() => Profiles.Import(own, user, a), "A profile's own saves were imported into it.");
        Throws<ArgumentException>(() => Profiles.Import(root, user, a), "A folder with no save was imported.");
    }
}
