using System.IO.Compression;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using HookshotBuilder;

static class PartsTests
{
    const string Tag = "authored-release";
    const string Revision = "1234567890123456789012345678901234567890";
    public static readonly ReleaseIdentity Identity = ReleaseIdentity.Create(Tag, Revision)!;
    static byte[] Bytes(string text) => Encoding.UTF8.GetBytes(text);
    static string Sha(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    static void Check(bool value, string name) { if (!value) throw new Exception(name); }
    static async Task Throws<T>(Func<Task> action) where T : Exception
    {
        try { await action(); } catch (T) { return; }
        throw new Exception("Expected " + typeof(T).Name);
    }

    static byte[] GuiManifest(ReleaseIdentity identity, IReadOnlyDictionary<string, byte[]> files) =>
        JsonSerializer.SerializeToUtf8Bytes(new { format = 1, tag = identity.Tag, revision = identity.Revision,
            files = BuilderContents.GuiFiles.ToDictionary(name => name, name => Sha(files[name])) });

    public static async Task StampGui(string root, ReleaseIdentity identity)
    {
        var files = new Dictionary<string, byte[]>();
        foreach (var name in BuilderContents.GuiFiles) files[name] = await File.ReadAllBytesAsync(Path.Combine(root, name));
        await File.WriteAllBytesAsync(Path.Combine(root, BuilderContents.ManifestName), GuiManifest(identity, files));
    }

    static Dictionary<string, byte[]> Files()
    {
        var gui = new Dictionary<string, byte[]> {
            ["HookshotBuilder.exe"] = Bytes("authored GUI, never executable"),
            ["HookshotBuilder.dll"] = Bytes("authored managed GUI, never loaded") };
        return new() {
            ["HookshotBuilder/HookshotBuilder.exe"] = gui["HookshotBuilder.exe"],
            ["HookshotBuilder/HookshotBuilder.dll"] = gui["HookshotBuilder.dll"],
            ["HookshotBuilder/builder-identity.json"] = GuiManifest(Identity, gui),
            ["HookshotBuilder/tools/version.txt"] = Bytes(Tag + "\n"),
            ["HookshotBuilder/tools/revision.txt"] = Bytes(Revision + "\n") };
    }

    static byte[] Zip(IReadOnlyDictionary<string, byte[]> files, int? attributes = null)
    {
        using var stream = new MemoryStream();
        using (var zip = new ZipArchive(stream, ZipArchiveMode.Create, leaveOpen: true))
            foreach (var (name, bytes) in files) {
                var entry = zip.CreateEntry(name);
                if (attributes != null) entry.ExternalAttributes = attributes.Value;
                using var output = entry.Open();
                output.Write(bytes);
            }
        return stream.ToArray();
    }

    sealed class UnknownLength(byte[] bytes) : HttpContent
    {
        protected override bool TryComputeLength(out long length) { length = 0; return false; }
        protected override Task SerializeToStreamAsync(Stream stream, TransportContext? context) => stream.WriteAsync(bytes).AsTask();
    }
    internal sealed class Api : HttpMessageHandler
    {
        public readonly Dictionary<string, byte[]> Replies = [];
        public readonly Dictionary<string, int> Calls = [];
        public bool UnknownLengths, ChangeRevision;
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            var url = request.RequestUri!.AbsoluteUri;
            Calls[url] = Calls.GetValueOrDefault(url) + 1;
            if (!Replies.TryGetValue(url, out var bytes)) throw new HttpRequestException("Offline fixture has no reply: " + url);
            if (ChangeRevision && url.EndsWith("/git/ref/tags/" + Tag, StringComparison.Ordinal) && Calls[url] > 1)
                bytes = JsonSerializer.SerializeToUtf8Bytes(new { @object = new { sha = new string('b', 40), type = "commit" } });
            HttpContent content = UnknownLengths ? new UnknownLength(bytes) : new ByteArrayContent(bytes);
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = content });
        }
    }

    internal sealed class Fixture : IDisposable
    {
        public readonly string Root = Path.Combine(Path.GetTempPath(), "hookshot-parts-" + Guid.NewGuid().ToString("N"));
        public readonly Api Api = new();
        public readonly HttpClient Http;
        public byte[] App = Zip(Files()), Llvm = Zip(new Dictionary<string, byte[]> { ["llvm-mingw-fixture/bin/clang.exe"] = Bytes("authored compiler, never run") });
        public string Cache => Path.Combine(Root, "cache");
        public string Target => Path.Combine(Root, Identity.CacheName);
        public string Manifest(bool legacy = false) => $"tag {Tag}\n" + (legacy ? "" : $"identity 1 {Revision}\n") +
            $"part {Sha(App)} {App.Length} . - - https://fixture.invalid/app.zip\n" +
            $"part {Sha(Llvm)} {Llvm.Length} HookshotBuilder/tools llvm-mingw-fixture llvm-mingw https://fixture.invalid/llvm.zip\n" +
            "run HookshotBuilder/HookshotBuilder.exe\n";
        public Fixture() { Http = new(Api); Directory.CreateDirectory(Root); Configure(); }
        public void Configure(bool manifest = true, bool zip = false, bool legacy = false, string? text = null)
        {
            var list = Bytes(text ?? Manifest(legacy));
            var assets = new List<object> {
                new { name = "HookshotBuilder.exe", browser_download_url = "https://fixture.invalid/thin.exe", size = 1, digest = "sha256:" + new string('a', 64) } };
            if (manifest) assets.Add(new { name = LatestRelease.SetupList, browser_download_url = "https://fixture.invalid/list", size = list.Length, digest = "sha256:" + Sha(list) });
            if (zip) assets.Add(new { name = LatestRelease.Asset, browser_download_url = "https://fixture.invalid/full.zip", size = App.Length, digest = "sha256:" + Sha(App) });
            Api.Replies[LatestRelease.Api] = JsonSerializer.SerializeToUtf8Bytes(new { tag_name = Tag, assets });
            Api.Replies["https://api.github.com/repos/NotRexed/Hookshot/git/ref/tags/" + Tag] =
                JsonSerializer.SerializeToUtf8Bytes(new { @object = new { sha = Revision, type = "commit" } });
            Api.Replies["https://fixture.invalid/list"] = list;
            Api.Replies["https://fixture.invalid/app.zip"] = App;
            Api.Replies["https://fixture.invalid/llvm.zip"] = Llvm;
            Api.Replies["https://fixture.invalid/full.zip"] = App;
        }
        public async Task<string> Prepare(string? target = null) => await UpdatePreparation.PrepareAsync(Http,
            await LatestRelease.FetchAsync(Http, CancellationToken.None, policy: NetworkTests.Fast), Cache, target ?? Target,
            CancellationToken.None, policy: NetworkTests.Fast);
        public void Dispose() { Http.Dispose(); Directory.Delete(Root, true); }
    }

    public static async Task Run()
    {
        Check(UpdateRequest.Parse([]) == null, "normal start has no update handoff");
        var handoff = UpdateRequest.Parse(["--update", "C:/authored game", "--tag", Tag, "--revision", Revision, "--fetched"]);
        Check(handoff == new UpdateRequest("C:/authored game", Tag, Revision, true), "complete handoff retained");
        foreach (var bad in new[] { new[] { "--update" }, new[] { "--fetched" }, new[] { "--update", "x", "--update", "y" },
                                    new[] { "--update", "x", "--tag", Tag, "--revision", "main" }, new[] { "--update", "x", "--unknown" } })
            await Throws<InvalidDataException>(() => { UpdateRequest.Parse(bad); return Task.CompletedTask; });
        using (var f = new Fixture())
        {
            var release = await LatestRelease.FetchAsync(f.Http, CancellationToken.None);
            Check(release.Manifest != null && release.Zip == null, "manifest-only release supported");
            await f.Prepare();
            Check(File.ReadAllText(Path.Combine(f.Target, "tools/llvm-mingw/bin/clang.exe")) == "authored compiler, never run", "LLVM destination and rename");
            Check(!f.Api.Calls.ContainsKey("https://fixture.invalid/thin.exe"), "thin executable is never requested");
            await f.Prepare(Path.Combine(f.Root, "second"));
            Check(f.Api.Calls["https://fixture.invalid/app.zip"] == 1 && f.Api.Calls["https://fixture.invalid/llvm.zip"] == 1, "SHA cache reused");
            File.WriteAllText(Path.Combine(f.Cache, "parts", Sha(f.App) + ".download"), "bad cache");
            await f.Prepare(Path.Combine(f.Root, "third"));
            Check(f.Api.Calls["https://fixture.invalid/app.zip"] == 2, "corrupt cache fetched again");
            File.WriteAllText(Path.Combine(f.Target, "HookshotBuilder.exe"), "stale GUI with fresh markers");
            await Throws<InvalidDataException>(() => f.Prepare());
            await StampGui(f.Target, Identity);
            await Throws<InvalidDataException>(() => f.Prepare());
        }
        using (var f = new Fixture()) { f.Configure(manifest: false, zip: true); await f.Prepare(); }
        using (var f = new Fixture()) { f.Configure(zip: true, legacy: true); await f.Prepare(); Check(f.Api.Calls.ContainsKey("https://fixture.invalid/full.zip"), "legacy list full-ZIP fallback"); }
        using (var f = new Fixture()) { f.Configure(legacy: true); await Throws<LegacySetupManifestException>(() => f.Prepare()); Check(!Directory.Exists(f.Target), "legacy metadata never installed"); }
        using (var f = new Fixture()) { f.Configure(manifest: false); await Throws<InvalidDataException>(() => f.Prepare()); }
        using (var f = new Fixture()) { f.Api.ChangeRevision = true; await Throws<InvalidDataException>(() => f.Prepare()); }
        using (var f = new Fixture()) {
            using var cancelled = new CancellationTokenSource(); cancelled.Cancel();
            await Throws<OperationCanceledException>(() => UpdatePreparation.PrepareAsync(f.Http,
                new LatestRelease(Identity, null, new("https://fixture.invalid/list", 1, null)), f.Cache, f.Target, cancelled.Token));
            Check(!Directory.Exists(f.Target), "cancelled preparation leaves no installation");
        }
        using (var f = new Fixture()) {
            var body = f.App.ToArray(); body[body.Length / 2] ^= 1;
            f.Api.Replies["https://fixture.invalid/app.zip"] = body;
            await Throws<InvalidDataException>(() => f.Prepare());
            Check(!Directory.Exists(f.Target) && !Directory.EnumerateFiles(f.Cache, "*.part-*", SearchOption.AllDirectories).Any(), "hash failure cleanup");
        }
        using (var f = new Fixture()) {
            f.Api.UnknownLengths = true;
            f.Api.Replies["https://fixture.invalid/app.zip"] = f.App[..^1];
            await Throws<HttpRequestException>(() => f.Prepare());
            Check(f.Api.Calls["https://fixture.invalid/app.zip"] == 5, "early EOF retains released five-cycle retry");
            Check(!Directory.EnumerateFiles(f.Cache, "*.part-*", SearchOption.AllDirectories).Any(), "truncated attempts leave no owned partial");
        }
        using (var f = new Fixture()) {
            foreach (var bad in new[] {
                f.Manifest().Replace("identity 1 ", "identity 2 "), f.Manifest() + "tag duplicate\n",
                f.Manifest().Replace(Revision, new string('b', 40)), f.Manifest().Replace(" . - - ", " ../escape - - "),
                f.Manifest().Replace("llvm-mingw-fixture llvm-mingw", "llvm-mingw-fixture ../outside"),
                f.Manifest().Replace("https://fixture.invalid/app.zip", "http://fixture.invalid/app.zip"),
                f.Manifest().Replace("run HookshotBuilder/HookshotBuilder.exe", "run ../outside.exe") }) {
                f.Configure(zip: true, text: bad);
                await Throws<InvalidDataException>(() => f.Prepare());
                Check(!f.Api.Calls.ContainsKey("https://fixture.invalid/full.zip"), "malformed list does not silently fall back");
            }
        }
        foreach (var path in new[] { "../outside", "/absolute", "C:/absolute", "HookshotBuilder/../outside",
                                    "HookshotBuilder/tools\\outside", "HookshotBuilder/file:stream", "HookshotBuilder/CON", "OtherRoot/file" })
            using (var f = new Fixture()) {
                var files = Files(); files[path] = Bytes("authored unsafe entry"); f.App = Zip(files); f.Configure();
                await Throws<InvalidDataException>(() => f.Prepare());
                Check(!Directory.Exists(f.Target) && !Directory.EnumerateDirectories(f.Root, "*.part-*").Any(), "unsafe extraction cleanup");
            }
        using (var f = new Fixture()) {
            f.App = Zip(new Dictionary<string, byte[]> { ["HookshotBuilder/link"] = Bytes("../outside") }, unchecked((int)0xA1FF0000)); f.Configure();
            await Throws<InvalidDataException>(() => f.Prepare());
        }
        using (var f = new Fixture()) {
            f.Llvm = Zip(new Dictionary<string, byte[]> { ["wrong-root/bin/clang.exe"] = Bytes("wrong") }); f.Configure();
            await Throws<InvalidDataException>(() => f.Prepare());
        }
        using (var f = new Fixture()) {
            var files = Files(); files["HookshotBuilder/hookshotbuilder.EXE"] = Bytes("case collision"); f.App = Zip(files); f.Configure();
            await Throws<InvalidDataException>(() => f.Prepare());
        }
        using (var f = new Fixture()) {
            var files = Files(); files["HookshotBuilder/tools/llvm-mingw/bin/clang.exe"] = Bytes("overlapping part"); f.App = Zip(files); f.Configure();
            await Throws<InvalidDataException>(() => f.Prepare());
        }
        foreach (var malformed in new[] { "{}", "not JSON", "[]", "{\"format\":1,\"format\":1,\"tag\":\"x\",\"revision\":\"x\",\"files\":{}}" })
            using (var f = new Fixture()) {
                var files = Files(); files["HookshotBuilder/builder-identity.json"] = Bytes(malformed); f.App = Zip(files); f.Configure();
                await Throws<InvalidDataException>(() => f.Prepare());
            }
        Console.WriteLine("managed parts: manifest/ZIP/cache/legacy/hashes/LLVM/boundary/stale-GUI checks passed");
    }

    public static async Task VerifyRealOldGui(string publish, string oldExe, string revision)
    {
        using var f = new Fixture();
        var identity = ReleaseIdentity.Create("real-GUI-fixture", revision)!;
        var stage = Path.Combine(f.Root, "real"); Directory.CreateDirectory(Path.Combine(stage, "tools"));
        foreach (var file in BuilderContents.GuiFiles) File.Copy(Path.Combine(publish, file), Path.Combine(stage, file));
        File.WriteAllText(Path.Combine(stage, "tools/version.txt"), identity.Tag);
        File.WriteAllText(Path.Combine(stage, "tools/revision.txt"), identity.Revision);
        await StampGui(stage, identity);
        await BuilderContents.ValidateAsync(stage, identity);
        File.Copy(oldExe, Path.Combine(stage, "HookshotBuilder.exe"), overwrite: true);
        await Throws<InvalidDataException>(() => BuilderContents.ValidateAsync(stage, identity));
        Console.WriteLine("real preserved old GUI rejected before launch; fresh markers and GUI manifest retained");
    }
}
