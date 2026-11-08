using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using HookshotBuilder;

static class NetworkTests
{
    public static readonly BuilderHttp.Policy Fast = new(TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(2),
        [TimeSpan.Zero, TimeSpan.Zero, TimeSpan.Zero, TimeSpan.Zero]);
    static readonly BuilderHttp.Policy Short = Fast with { Header = TimeSpan.FromMilliseconds(250), Read = TimeSpan.FromMilliseconds(250) };
    static readonly byte[] Data = Encoding.UTF8.GetBytes("authored download bytes");
    static readonly string Digest = "sha256:" + Convert.ToHexString(SHA256.HashData(Data)).ToLowerInvariant();
    static void Check(bool value, string message) { if (!value) throw new Exception(message); }
    static async Task<T> Throws<T>(Func<Task> action) where T : Exception
    {
        try { await action().WaitAsync(TimeSpan.FromSeconds(5)); } catch (T error) { return error; }
        throw new Exception("Expected " + typeof(T).Name);
    }

    sealed class Handler(Func<HttpRequestMessage, int, CancellationToken, Task<HttpResponseMessage>> reply) : HttpMessageHandler
    {
        public readonly List<(string Url, string Accept)> Calls = [];
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            Calls.Add((request.RequestUri!.AbsoluteUri, request.Headers.Accept.ToString()));
            return reply(request, Calls.Count, token);
        }
    }

    sealed class SlowStream(byte[] bytes, bool stall, int delayMs = 0) : Stream
    {
        int offset;
        public bool Disposed;
        public override bool CanRead => true;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => offset; set => throw new NotSupportedException(); }
        public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken token = default)
        {
            if (offset == bytes.Length)
            {
                if (stall) await Task.Delay(Timeout.InfiniteTimeSpan, token);
                return 0;
            }
            if (delayMs > 0) await Task.Delay(delayMs, token);
            var count = Math.Min(2, bytes.Length - offset);
            bytes.AsMemory(offset, count).CopyTo(buffer);
            offset += count;
            return count;
        }
        protected override void Dispose(bool disposing) { Disposed = true; base.Dispose(disposing); }
        public override int Read(byte[] b, int o, int n) => throw new NotSupportedException();
        public override void Flush() => throw new NotSupportedException();
        public override long Seek(long o, SeekOrigin s) => throw new NotSupportedException();
        public override void SetLength(long n) => throw new NotSupportedException();
        public override void Write(byte[] b, int o, int n) => throw new NotSupportedException();
    }
    static HttpResponseMessage Bytes(byte[] bytes) => new(HttpStatusCode.OK) { Content = new ByteArrayContent(bytes) };
    static HttpResponseMessage Stream(SlowStream stream) => new(HttpStatusCode.OK) { Content = new StreamContent(stream) };
    static Task<HttpResponseMessage> Reply(HttpResponseMessage response) => Task.FromResult(response);

    public static async Task Run()
    {
        Check(BuilderHttp.Default.Header == TimeSpan.FromSeconds(30) && BuilderHttp.Default.Read == TimeSpan.FromSeconds(60)
            && BuilderHttp.Default.Delays.Select(t => t.TotalSeconds).SequenceEqual(new double[] { 3, 6, 12, 20 }), "released retry/JSON timing preserved");
        var root = Path.Combine(Path.GetTempPath(), "hookshot-network-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        var asset = new ReleaseAsset("https://fixture.invalid/data", Data.Length, Digest);
        try
        {
            using (var handler = new Handler(async (_, _, token) => { await Task.Delay(Timeout.InfiniteTimeSpan, token); return Bytes(Data); }))
            using (var http = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan })
            {
                await Throws<HttpRequestException>(() => BuilderDownload.GetAsync(http, asset, Path.Combine(root, "headers"), default, policy: Short));
                Check(handler.Calls.Count == 5, "stalled headers retry exactly five times");
            }
            using (var handler = new Handler(async (_, _, token) => { await Task.Delay(Timeout.InfiniteTimeSpan, token); return Bytes(Data); }))
            using (var http = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan })
            {
                await Throws<HttpRequestException>(() => BuilderHttp.JsonAsync(http, LatestRelease.Api, default, policy: Short));
                Check(handler.Calls.Count == 5, "metadata requests retain bounded five-cycle retries");
            }
            var stalled = new List<SlowStream>();
            using (var handler = new Handler((_, _, _) => { var stream = new SlowStream(Data[..2], true); stalled.Add(stream); return Reply(Stream(stream)); }))
            using (var http = new HttpClient(handler))
            {
                var cache = Path.Combine(root, "body");
                await Throws<HttpRequestException>(() => BuilderDownload.GetAsync(http, asset, cache, default, policy: Short));
                Check(handler.Calls.Count == 5 && stalled.All(s => s.Disposed), "body timeout releases each response/stream and retains five attempts");
                Check(!Directory.EnumerateFiles(cache).Any(), "timed-out partials never become cached success");
            }
            using (var handler = new Handler((_, count, _) => Reply(count == 1 ? Stream(new SlowStream(Data[..2], true)) : Bytes(Data))))
            using (var http = new HttpClient(handler))
            {
                var file = await BuilderDownload.GetAsync(http, asset, Path.Combine(root, "recovered"), default, policy: Short);
                Check(handler.Calls.Count == 2 && File.ReadAllBytes(file).SequenceEqual(Data), "body timeout can recover without weakening digest checks");
            }
            using (var handler = new Handler((_, _, _) => Reply(Stream(new SlowStream(Data, false, 60)))))
            using (var http = new HttpClient(handler))
            {
                var started = System.Diagnostics.Stopwatch.StartNew();
                var file = await BuilderDownload.GetAsync(http, asset, Path.Combine(root, "active"), default, policy: Short);
                Check(started.Elapsed > Short.Read && handler.Calls.Count == 1 && File.ReadAllBytes(file).SequenceEqual(Data), "active download may outlast inactivity window");
            }
            const string apiAsset = "https://api.github.com/repos/authored/release/releases/assets/1";
            using (var handler = new Handler((request, _, _) => Reply(request.RequestUri!.AbsoluteUri == asset.Url
                ? new(HttpStatusCode.ServiceUnavailable) : Bytes(Data))))
            using (var http = new HttpClient(handler))
            {
                await BuilderDownload.GetAsync(http, asset with { Api = apiAsset }, Path.Combine(root, "alternate"), default, policy: Fast);
                Check(handler.Calls.Select(c => c.Url).SequenceEqual(new[] { asset.Url, apiAsset })
                    && handler.Calls[1].Accept == "application/octet-stream", "released primary-to-API fallback retained");
            }
            const string part = "https://github.com/authored/tools/releases/download/tag/part.zip";
            using (var handler = new Handler((request, _, _) => Reply(request.RequestUri!.Host == "github.com" ? new(HttpStatusCode.BadGateway)
                : request.RequestUri.AbsolutePath.Contains("/releases/tags/")
                    ? Bytes(JsonSerializer.SerializeToUtf8Bytes(new { assets = new[] { new { name = "part.zip", url = apiAsset } } })) : Bytes(Data))))
            using (var http = new HttpClient(handler))
            {
                await BuilderDownload.GetAsync(http, asset with { Url = part }, Path.Combine(root, "part"), default, policy: Fast);
                Check(handler.Calls.Count == 3 && handler.Calls[^1].Url == apiAsset, "setup-part URL resolves its same named GitHub API asset");
            }
            using (var handler = new Handler((_, count, _) => Reply(count == 1 ? Stream(new SlowStream(Data[..2], false)) : Bytes(Data))))
            using (var http = new HttpClient(handler))
            {
                await BuilderDownload.GetAsync(http, asset, Path.Combine(root, "eof"), default, policy: Fast);
                Check(handler.Calls.Count == 2, "early EOF remains retryable");
            }
            foreach (var body in new[] { false, true })
            {
                using var cancelled = new CancellationTokenSource(TimeSpan.FromMilliseconds(30));
                using var handler = new Handler(async (_, _, token) => {
                    if (!body) await Task.Delay(Timeout.InfiniteTimeSpan, token);
                    return Stream(new SlowStream(Data[..2], true));
                });
                using var http = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };
                await Throws<OperationCanceledException>(() => BuilderDownload.GetAsync(http, asset, Path.Combine(root, "cancel" + body), cancelled.Token, policy: Fast));
                Check(handler.Calls.Count == 1, "user cancellation is not converted into a retry");
            }
            using (var cancel = new CancellationTokenSource())
            using (var handler = new Handler((_, _, _) => Reply(new(HttpStatusCode.ServiceUnavailable))))
            using (var http = new HttpClient(handler))
            {
                var waiting = Fast with { Delays = [TimeSpan.FromSeconds(10)] };
                await Throws<OperationCanceledException>(() => BuilderDownload.GetAsync(http, asset, Path.Combine(root, "cancel-backoff"),
                    cancel.Token, note: _ => cancel.Cancel(), policy: waiting));
                Check(handler.Calls.Count == 1, "cancellation during retry backoff starts no new attempt");
            }
            foreach (var status in new[] { HttpStatusCode.Forbidden, HttpStatusCode.NotFound })
            {
                using var handler = new Handler((_, _, _) => Reply(new(status)));
                using var http = new HttpClient(handler);
                var failure = await Throws<HttpRequestException>(() => BuilderDownload.GetAsync(http, asset with { Api = apiAsset }, Path.Combine(root, "status" + status), default, policy: Fast));
                Check(failure.StatusCode == status && handler.Calls.Count == 1, "403/404 must not retry or try an alternate path");
            }
            foreach (var bytes in new[] { Data.Select(b => (byte)(b ^ 1)).ToArray(), Data[..2] })
            {
                using var handler = new Handler((_, _, _) => Reply(Bytes(bytes)));
                using var http = new HttpClient(handler);
                await Throws<InvalidDataException>(() => BuilderDownload.GetAsync(http, asset with { Api = apiAsset }, Path.Combine(root, "invalid" + bytes.Length), default, policy: Fast));
                Check(handler.Calls.Count == 1, "digest or declared-size failures do not retry/fall back");
            }
            var request = new UpdateRequest("C:/authored folder with spaces", "authored", new string('a', 40), true);
            var start = request.StartInfo("C:/authored builder/HookshotBuilder.exe");
            Check(!start.UseShellExecute && Path.GetFullPath(start.WorkingDirectory) == Path.GetFullPath("C:/authored builder") &&
                UpdateRequest.Parse(start.ArgumentList.ToArray()) == request && !start.ArgumentList.Contains("--nup"),
                "actual handoff preserves exact update folder/revision without selecting a discovered ROM");
            Console.WriteLine("update transport: stalled header/body, active progress, API fallback, early EOF, cancellation, validation and actual handoff checks passed");
        }
        finally { Directory.Delete(root, true); }
    }
}
