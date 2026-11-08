using System.Security.Cryptography;
using HookshotBuilder;

static class OwnedRootTests
{
    static int checks;
    static void Check(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
        checks++;
    }
    static async Task Throws<T>(Func<Task> action) where T : Exception
    {
        try { await action(); }
        catch (T) { checks++; return; }
        throw new Exception("Expected " + typeof(T).Name);
    }
    static Dictionary<string, string> Bytes(string root) => Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories)
        .ToDictionary(p => Path.GetRelativePath(root, p), p => Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(p))) + ":" + File.GetAttributes(p));
    static void Same(Dictionary<string, string> expected, string root, string message) =>
        Check(expected.OrderBy(p => p.Key).SequenceEqual(Bytes(root).OrderBy(p => p.Key)), message);

    public static async Task Run()
    {
        if (!OperatingSystem.IsWindows()) { Console.WriteLine("owned-root: skipped Win32 claim/sharing fixture on this platform"); return; }
        using var fixture = new PartsTests.Fixture();
        // Prove the resolved recursive-cleanup target is this fixture's authored temporary root.
        var authored = Path.GetFullPath(fixture.Root);
        Check(Path.GetDirectoryName(authored) == Path.TrimEndingDirectorySeparator(Path.GetFullPath(Path.GetTempPath())) &&
              Path.GetFileName(authored).StartsWith("hookshot-parts-", StringComparison.Ordinal), "authored temporary ownership");
        var root = Path.Combine(authored, "parent space 雪", "HookshotBuilder");
        var sourceVersion = Path.Combine(root, "versions", "v-old");
        var gui = Path.Combine(sourceVersion, "HookshotBuilder");
        Directory.CreateDirectory(Path.Combine(gui, "tools"));
        File.WriteAllText(Path.Combine(root, ".hookshot-install-root"), "HookshotInstallRoot 1\n");
        var versionMarker = Path.Combine(sourceVersion, ".hookshot-install-version");
        File.WriteAllText(versionMarker, "HookshotInstallVersion 1\n");
        File.WriteAllText(Path.Combine(gui, "HookshotBuilder.exe"), "old authored GUI, never executed");
        File.WriteAllText(Path.Combine(gui, "HookshotBuilder.dll"), "old authored DLL, never loaded");
        File.WriteAllText(Path.Combine(gui, "tools/version.txt"), "authored-release\n");
        File.WriteAllText(Path.Combine(gui, "tools/revision.txt"), new string('a', 40) + "\n");
        File.WriteAllText(Path.Combine(root, "versions/current.txt"), "v-old");
        var unknown = Path.Combine(root, "versions", "user-folder");
        Directory.CreateDirectory(unknown);
        File.WriteAllText(Path.Combine(unknown, "keep.txt"), "unknown user data");
        var sourceBefore = Bytes(sourceVersion);
        var unknownBefore = Bytes(unknown);
        var owned = InstallRoot.FromSource(gui);
        Check(owned?.DirectoryPath == root, "source path selects its own root with Unicode/spaces");
        Check(InstallRoot.FromSource(fixture.Cache) == null, "cache bootstrap does not imply source ownership");
        await Throws<IOException>(() => { InstallRoot.FromSource(Path.Combine(root, "update")); return Task.CompletedTask; });
        var legacy = Path.Combine(authored, "legacy", "HookshotBuilder");
        Directory.CreateDirectory(legacy);
        Check(InstallRoot.FromSource(legacy) == null, "unmarked legacy route remains available");
        File.Delete(versionMarker);
        await Throws<IOException>(() => { InstallRoot.FromSource(gui); return Task.CompletedTask; });
        File.WriteAllText(versionMarker, "HookshotInstallVersion 1\n");

        var latest = await LatestRelease.FetchAsync(fixture.Http, CancellationToken.None, policy: NetworkTests.Fast);
        var beforeCancel = Bytes(root);
        using (var cancelled = new CancellationTokenSource())
        {
            cancelled.Cancel();
            await Throws<OperationCanceledException>(() => owned!.PrepareAsync(fixture.Http, latest, cancelled.Token, policy: NetworkTests.Fast));
        }
        Same(beforeCancel, root, "already cancelled creates no lock/cache/attempt/current changes");
        var first = await owned!.PrepareAsync(fixture.Http, latest, CancellationToken.None, policy: NetworkTests.Fast);
        Check(first.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase), "prepared target stays under selected root");
        Check(ReleaseIdentity.Read(Path.Combine(first, "tools")) == latest.Identity, "actual preparation validates source identity");
        Check(File.ReadAllText(Path.Combine(first, "tools/llvm-mingw/bin/clang.exe")) == "authored compiler, never run", "actual LLVM destination/rename preserved");
        var currentPath = Path.Combine(root, "versions/current.txt");
        Check(Path.GetDirectoryName(first) == Path.Combine(root, "versions", File.ReadAllText(currentPath)), "actual current publication selects the prepared version");
        var receipt = File.ReadAllText(Path.Combine(Path.GetDirectoryName(first)!, ".hookshot-published-gui-sha"));
        Check(receipt == Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(Path.Combine(first, BuilderContents.ManifestName)))).ToLowerInvariant(),
              "native reuse receipt binds the already verified GUI manifest");
        Same(sourceBefore, sourceVersion, "source GUI/tools preserved after update");
        Same(unknownBefore, unknown, "unknown folders remain unprunable");
        Check(!fixture.Api.Calls.Keys.Any(p => p.EndsWith("thin.exe", StringComparison.Ordinal)), "no native installer requested/executed");

        var second = await owned.PrepareAsync(fixture.Http, latest, CancellationToken.None, policy: NetworkTests.Fast);
        Check(second != first && Directory.Exists(first), "same-tag Repair stages fresh unique payload and preserves prior one");
        Check(fixture.Api.Calls["https://fixture.invalid/app.zip"] == 1 && fixture.Api.Calls["https://fixture.invalid/llvm.zip"] == 1,
              "repeat preparation reuses verified SHA cache");
        var currentOwned = InstallRoot.FromSource(first)!;
        await currentOwned.ValidateCurrentAsync(latest.Identity, CancellationToken.None);
        checks++;
        File.WriteAllText(Path.Combine(first, "HookshotBuilder.exe"), "old authored GUI with fresh markers");
        await PartsTests.StampGui(first, latest.Identity);
        await Throws<InvalidDataException>(() => currentOwned.ValidateCurrentAsync(latest.Identity, CancellationToken.None));
        File.Copy(Path.Combine(second, "HookshotBuilder.exe"), Path.Combine(first, "HookshotBuilder.exe"), true);
        File.Copy(Path.Combine(second, BuilderContents.ManifestName), Path.Combine(first, BuilderContents.ManifestName), true);
        var versions = Bytes(Path.Combine(root, "versions"));
        using (var currentLock = new FileStream(currentPath, FileMode.Open, FileAccess.Read, FileShare.Read))
            await Throws<UnauthorizedAccessException>(() => owned.PrepareAsync(fixture.Http, latest, CancellationToken.None, policy: NetworkTests.Fast));
        Same(versions, Path.Combine(root, "versions"), "real locked current handoff rolls back only new payload");
        Check(!Directory.EnumerateDirectories(root, ".setup-*").Any(), "failed handoff removes only task-owned attempt");
        using (var ownership = new FileStream(Path.Combine(root, ".hookshot-commit.lock"), FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None, 1, FileOptions.DeleteOnClose))
            await Throws<IOException>(() => owned.PrepareAsync(fixture.Http, latest, CancellationToken.None, policy: NetworkTests.Fast));
        Same(versions, Path.Combine(root, "versions"), "busy root does not relocate or change versions");

        using (var cancelled = new CancellationTokenSource())
            await Throws<OperationCanceledException>(() => owned.PrepareAsync(fixture.Http, latest, cancelled.Token, _ => cancelled.Cancel(), policy: NetworkTests.Fast));
        Same(versions, Path.Combine(root, "versions"), "mid-preparation cancellation preserves every prior payload/current byte");
        fixture.App = new byte[] { 1, 2, 3 }; fixture.Configure();
        latest = await LatestRelease.FetchAsync(fixture.Http, CancellationToken.None, policy: NetworkTests.Fast);
        await Throws<InvalidDataException>(() => owned.PrepareAsync(fixture.Http, latest, CancellationToken.None, policy: NetworkTests.Fast));
        Same(versions, Path.Combine(root, "versions"), "bad archive cannot publish or remove existing versions");
        Check(!Directory.EnumerateDirectories(root, ".setup-*").Any(), "failed preparation cleans only its attempt");
        Console.WriteLine($"owned-root: {checks} actual discovery/preparation/commit/cancel/legacy/cache checks passed");
    }
}
