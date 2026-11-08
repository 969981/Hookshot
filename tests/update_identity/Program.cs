using System.IO.Compression;
using System.Net;
using System.Text;
using System.Text.Json;
using HookshotBuilder;

static class Test
{
    const string Old = "e900faa8bf49547bd9d5a98aa5be5a9e7dea042e";
    const string Next = "170ce53ca3d69309b3f5151a334136be8b4c38d6";
    const string Tag = "foliage-linux-updater";
    static void Check(string name, bool condition) { if (!condition) throw new Exception(name); }
    static async Task Throws<T>(string name, Func<Task> action) where T : Exception
    {
        try { await action(); } catch (T) { return; }
        throw new Exception(name + ": expected " + typeof(T).Name);
    }

    sealed class Api(Dictionary<string, string> replies) : HttpMessageHandler
    {
        public readonly List<string> Calls = [];
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var url = request.RequestUri!.AbsoluteUri;
            Calls.Add(url);
            if (!replies.TryGetValue(url, out var reply)) throw new HttpRequestException("Fixture is offline: " + url);
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(reply, Encoding.UTF8, "application/json") });
        }
    }

    static string Ref(string sha, string type = "commit") => JsonSerializer.Serialize(new { @object = new { sha, type } });
    static string Latest(bool assets = true) => JsonSerializer.Serialize(new {
        tag_name = Tag, target_commitish = "main", assets = assets ? new object[] {
            new { name = "Other.AppImage", browser_download_url = "https://example.invalid/other", size = 1, digest = "sha256:" + new string('c', 64) },
            new { name = LatestRelease.Asset, browser_download_url = "https://example.invalid/builder.zip", size = 2, digest = "sha256:" + new string('b', 64) }
        } : Array.Empty<object>()
    });

    static void Stamp(string tools, ReleaseIdentity identity)
    {
        Directory.CreateDirectory(tools);
        File.WriteAllText(Path.Combine(tools, "version.txt"), identity.Tag + "\n");
        File.WriteAllText(Path.Combine(tools, "revision.txt"), identity.Revision + "\n");
    }

    static async Task Main(string[] args)
    {
        if (args.Length == 3) { await PartsTests.VerifyRealOldGui(args[0], args[1], args[2]); return; }
        var old = ReleaseIdentity.Create(Tag, Old)!;
        var next = ReleaseIdentity.Create(Tag, Next)!;
        Check("same tag corrected source differs", old != next);
        Check("same exact identity reuses tools", next == ReleaseIdentity.Create(Tag, Next.ToUpperInvariant()));
        Check("legacy identity is unknown", ReleaseIdentity.Create(Tag, null) == null);
        Check("not game build id", ReleaseIdentity.Create(Tag, "e0f3ccfa") == null);
        Check("fetched mismatch rejected", !ReleaseIdentity.FetchedMatches(old, Tag, Next));
        Check("missing requested revision is not identity proof", !ReleaseIdentity.FetchedMatches(next, Tag, null));
        Check("matching fetched accepted", ReleaseIdentity.FetchedMatches(next, Tag, Next));
        Check("legacy handoff verified against latest", ReleaseIdentity.CanBuildFetched(next, next, Tag, null));
        Check("legacy handoff with unknown files rejected", !ReleaseIdentity.CanBuildFetched(null, next, Tag, null));
        Check("legacy handoff with stale files rejected", !ReleaseIdentity.CanBuildFetched(old, next, Tag, null));
        Check("legacy handoff offline cannot claim current", !ReleaseIdentity.CanBuildFetched(next, null, Tag, null));
        Check("explicit mismatch never bypassed", !ReleaseIdentity.CanBuildFetched(next, next, Tag, Old));
        Check("corrected build cache differs", old.CacheName != next.CacheName);
        Check("tag sanitization collisions avoided", ReleaseIdentity.Create("a/b", Next)!.CacheName != ReleaseIdentity.Create("ab", Next)!.CacheName);

        var refUrl = "https://api.github.com/repos/NotRexed/Hookshot/git/ref/tags/" + Tag;
        var tagUrl = "https://api.github.com/repos/NotRexed/Hookshot/git/tags/" + Old;
        using var api = new Api(new() { [LatestRelease.Api] = Latest(), [refUrl] = Ref(Old, "tag"),
            [tagUrl] = JsonSerializer.Serialize(new { sha = Old, @object = new { sha = Next, type = "commit" } }) });
        using var http = new HttpClient(api);
        var release = await LatestRelease.FetchAsync(http, CancellationToken.None);
        Check("annotated ref resolved", release.Identity == next && api.Calls.Count == 3);
        Check("correct asset selected", release.Digest == "sha256:" + new string('b', 64));
        using var missing = new Api(new() { [LatestRelease.Api] = Latest(false) });
        using var missingHttp = new HttpClient(missing);
        await Throws<InvalidDataException>("publishing release without asset", () => LatestRelease.FetchAsync(missingHttp, CancellationToken.None));
        Check("missing asset not treated as current", missing.Calls.Count == 1);
        using var offline = new Api(new());
        using var offlineHttp = new HttpClient(offline);
        await Throws<HttpRequestException>("offline does not imply current", () => LatestRelease.FetchAsync(offlineHttp, CancellationToken.None, policy: NetworkTests.Fast));
        var cyclicCalls = 0;
        await Throws<InvalidDataException>("annotated cycle bounded", () => ReleaseIdentity.ResolveAsync(Tag, (_, _) => {
            cyclicCalls++;
            return Task.FromResult(Ref(Old, "tag"));
        }, CancellationToken.None));
        Check("at most five ref requests", cyclicCalls == 5);

        var temp = Path.Combine(Path.GetTempPath(), "hookshot-update-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(temp);
        try
        {
            var stage = Path.Combine(temp, "stage", "HookshotBuilder");
            Stamp(Path.Combine(stage, "tools"), next);
            File.WriteAllText(Path.Combine(stage, "HookshotBuilder.exe"), "authored fixture, not executable");
            File.WriteAllText(Path.Combine(stage, "HookshotBuilder.dll"), "authored fixture, not a managed assembly");
            await PartsTests.StampGui(stage, next);
            var archive = Path.Combine(temp, "builder.zip");
            ZipFile.CreateFromDirectory(Path.GetDirectoryName(stage)!, archive);
            var oldFolder = Path.Combine(temp, old.CacheName);
            Stamp(Path.Combine(oldFolder, "tools"), old);
            File.WriteAllText(Path.Combine(oldFolder, "owner.txt"), "preserve");
            var newFolder = Path.Combine(temp, next.CacheName);
            await BuilderArchive.UnpackAsync(archive, newFolder, next);
            Check("matching archive extracted", ReleaseIdentity.Read(Path.Combine(newFolder, "tools")) == next);
            Check("previous builder intact", File.ReadAllText(Path.Combine(oldFolder, "owner.txt")) == "preserve");
            await Throws<IOException>("wrong existing identity preserved", () => BuilderArchive.UnpackAsync(archive, oldFolder, next));
            Check("existing folder still intact", File.ReadAllText(Path.Combine(oldFolder, "owner.txt")) == "preserve");
            var mismatch = Path.Combine(temp, "mismatch");
            await Throws<InvalidDataException>("wrong package rejected", () => BuilderArchive.UnpackAsync(archive, mismatch, old));
            Check("wrong package not installed", !Directory.Exists(mismatch) && !Directory.EnumerateDirectories(temp, "*.part-*").Any());
            File.Delete(Path.Combine(newFolder, "tools", "revision.txt"));
            Check("legacy files remain unknown", ReleaseIdentity.Read(Path.Combine(newFolder, "tools")) == null);
        }
        finally { Directory.Delete(temp, true); }
        Console.WriteLine("update identity: Builder/ref/fetched/archive checks passed");
        await PartsTests.Run();
        await NetworkTests.Run();
        RouteTests.Run();
    }
}
