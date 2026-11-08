using System.IO;
using System.Net.Http;
using System.Security.Cryptography;

namespace HookshotBuilder;

static class BuilderDownload
{
    public static async Task<string> GetAsync(HttpClient http, ReleaseAsset asset, string cache, CancellationToken token,
                                               Action<double>? progress = null, Action<string>? note = null, BuilderHttp.Policy? policy = null)
    {
        policy ??= BuilderHttp.Default;
        token.ThrowIfCancellationRequested();
        Directory.CreateDirectory(cache);
        if ((File.GetAttributes(cache) & FileAttributes.ReparsePoint) != 0)
            throw new InvalidDataException("The update cache must be a regular directory.");
        var key = asset.Digest?[7..] ?? Guid.NewGuid().ToString("N");
        var path = Path.Combine(cache, key + ".download");
        async Task<bool> Matches(string file)
        {
            if (!File.Exists(file)) return false;
            if ((File.GetAttributes(file) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException("The cached download must be a regular file.");
            if (new FileInfo(file).Length != asset.Size || asset.Digest == null) return false;
            await using var stream = File.OpenRead(file);
            return "sha256:" + Convert.ToHexString(await SHA256.HashDataAsync(stream, token)).ToLowerInvariant() == asset.Digest;
        }
        if (await Matches(path)) return path;
        async Task<string> Once(string url, bool apiAsset)
        {
            var partial = path + ".part-" + Guid.NewGuid().ToString("N");
            try
            {
                using var response = await BuilderHttp.OpenAsync(http, url, apiAsset, token, policy);
                if (response.Content.Headers.ContentLength is { } length && length != asset.Size)
                    throw new InvalidDataException("The download size differs from the release metadata.");
                await using (var from = await response.Content.ReadAsStreamAsync(token))
                await using (var to = new FileStream(partial, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    var buffer = new byte[1 << 20];
                    long have = 0;
                    var shown = DateTime.MinValue;
                    for (int count; (count = await BuilderHttp.ReadAsync(from, buffer, token, policy)) != 0;)
                    {
                        have = checked(have + count);
                        if (have > asset.Size) throw new InvalidDataException("The download exceeds its declared size.");
                        await to.WriteAsync(buffer.AsMemory(0, count), token);
                        if (DateTime.UtcNow - shown >= TimeSpan.FromMilliseconds(200) || have == asset.Size)
                        {
                            shown = DateTime.UtcNow;
                            progress?.Invoke((double)have / asset.Size);
                        }
                    }
                    if (have != asset.Size) throw new HttpRequestException("The download ended early.");
                }
                if (asset.Digest != null && !await Matches(partial))
                    throw new InvalidDataException("The downloaded file does not match its SHA-256.");
                File.Move(partial, path, overwrite: true);
                return path;
            }
            finally { if (File.Exists(partial)) File.Delete(partial); }
        }
        var api = asset.Api;
        HttpRequestException? last = null;
        for (var attempt = 0; attempt <= policy.Delays.Count; attempt++)
        {
            try { return await Once(asset.Url, false); }
            catch (HttpRequestException error) when (BuilderHttp.Transient(error)) { last = error; }
            try
            {
                api ??= await BuilderHttp.ApiAssetAsync(http, asset.Url, token, policy);
                if (api != null)
                {
                    note?.Invoke("Release download failed; trying GitHub's API asset URL.");
                    return await Once(api, true);
                }
            }
            catch (HttpRequestException error) when (BuilderHttp.Transient(error)) { last = error; }
            if (attempt < policy.Delays.Count) await BuilderHttp.DelayAsync(attempt, policy, token, note);
        }
        throw new HttpRequestException($"GitHub's asset download failed after {policy.Delays.Count + 1} attempts: {last?.Message}", last, last?.StatusCode);
    }
}
