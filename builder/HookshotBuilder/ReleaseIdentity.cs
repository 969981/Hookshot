using System.IO;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace HookshotBuilder;

/// A tag can be moved. The packaged source revision identifies the sources being rebuilt.
sealed record ReleaseIdentity(string Tag, string Revision)
{
    public static bool ValidRevision(string? value) => value is { Length: 40 } &&
        value.All(c => c is >= '0' and <= '9' or >= 'a' and <= 'f' or >= 'A' and <= 'F');

    public static ReleaseIdentity? Create(string? tag, string? revision) =>
        !string.IsNullOrWhiteSpace(tag) && !tag.Any(char.IsControl) && ValidRevision(revision)
            ? new(tag, revision!.ToLowerInvariant()) : null;

    public static ReleaseIdentity? Read(string tools)
    {
        try
        {
            var version = Path.Combine(tools, "version.txt");
            var revision = Path.Combine(tools, "revision.txt");
            return File.Exists(version) && File.Exists(revision)
                ? Create(File.ReadAllText(version).Trim(), File.ReadAllText(revision).Trim()) : null;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { return null; }
    }

    // A corrected build gets a different directory. Keep the tag hash too: sanitizing
    // e.g. a/b and ab to the same name must not replace the running builder's folder.
    public string CacheName => new string(Tag.Where(c => char.IsAsciiLetterOrDigit(c) || c is '-' or '_' or '.').Take(32).ToArray()) + "-" +
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(Tag))).ToLowerInvariant()[..12] + "-" + Revision;

    public static bool FetchedMatches(ReleaseIdentity? installed, string? tag, string? revision) =>
        Create(tag, revision) is { } expected && installed == expected;

    // Older builders hand over --fetched --tag without --revision. That may
    // bootstrap only after the package's own revision matches a fresh API result.
    public static bool CanBuildFetched(ReleaseIdentity? installed, ReleaseIdentity? latest, string? tag, string? revision) =>
        latest != null && installed == latest && tag == latest.Tag &&
        (revision == null || FetchedMatches(installed, tag, revision));

    public static async Task<ReleaseIdentity> ResolveAsync(string tag,
        Func<string, CancellationToken, Task<string>> getJson, CancellationToken token)
    {
        var path = "https://api.github.com/repos/NotRexed/Hookshot/git/ref/tags/" + Uri.EscapeDataString(tag);
        for (var depth = 0; depth < 5; depth++)
        {
            using var doc = JsonDocument.Parse(await getJson(path, token));
            var target = doc.RootElement.GetProperty("object");
            var sha = target.GetProperty("sha").GetString();
            var type = target.GetProperty("type").GetString();
            if (!ValidRevision(sha)) throw new InvalidDataException("The release's build information is missing or invalid.");
            if (type == "commit") return Create(tag, sha) ?? throw new InvalidDataException("The release tag is invalid.");
            if (type != "tag") throw new InvalidDataException("The release tag does not identify source code.");
            path = "https://api.github.com/repos/NotRexed/Hookshot/git/tags/" + sha;
        }
        throw new InvalidDataException("The release tag chain is too long.");
    }
}

sealed record ReleaseAsset(string Url, long Size, string? Digest, string? Api = null)
{
    internal static string? ApiUrl(JsonElement asset)
    {
        if (!asset.TryGetProperty("url", out var field)) return null;
        var value = field.GetString();
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri) || uri.Scheme != "https" || uri.UserInfo.Length != 0 ||
            !uri.Host.Equals("api.github.com", StringComparison.OrdinalIgnoreCase) || uri.Query.Length != 0 || uri.Fragment.Length != 0)
            throw new InvalidDataException("The API asset URL is invalid.");
        var parts = uri.AbsolutePath.Split('/', StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length != 6 || parts[0] != "repos" || parts[3] != "releases" || parts[4] != "assets" || !parts[5].All(char.IsAsciiDigit))
            throw new InvalidDataException("The API asset URL is invalid.");
        return value;
    }

    public static ReleaseAsset Read(JsonElement asset)
    {
        var url = asset.GetProperty("browser_download_url").GetString();
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) || uri.Scheme != "https" || uri.UserInfo.Length != 0)
            throw new InvalidDataException("The release download URL is invalid.");
        var size = asset.GetProperty("size").GetInt64();
        if (size <= 0) throw new InvalidDataException("The release download size is invalid.");
        string? digest = asset.TryGetProperty("digest", out var value) ? value.GetString() : null;
        if (digest != null && (digest.Length != 71 || !digest.StartsWith("sha256:", StringComparison.Ordinal) ||
                              !digest[7..].All(Uri.IsHexDigit)))
            throw new InvalidDataException("The release download digest is invalid.");
        return new(url!, size, digest?.ToLowerInvariant(), ApiUrl(asset));
    }
}

sealed record LatestRelease(ReleaseIdentity Identity, ReleaseAsset? Zip, ReleaseAsset? Manifest)
{
    public const string Api = "https://api.github.com/repos/NotRexed/Hookshot/releases/latest";
    public const string Asset = "HookshotBuilder.zip";
    public const string SetupList = "hookshot-setup.txt";
    public string? Digest => Zip?.Digest;

    /// The released download-service fallback is a tag hint only, never verified source identity.
    public static async Task<string?> SetupTagAsync(HttpClient http, CancellationToken token, BuilderHttp.Policy? policy = null)
    {
        policy ??= BuilderHttp.Default;
        for (var attempt = 0; attempt < 2; attempt++)
        {
            try
            {
                using var response = await BuilderHttp.OpenAsync(http,
                    "https://github.com/NotRexed/Hookshot/releases/latest/download/" + SetupList, false, token, policy);
                if (response.Content.Headers.ContentLength > 262144) throw new InvalidDataException("The setup list is too large.");
                await using var stream = await response.Content.ReadAsStreamAsync(token);
                using var bytes = new MemoryStream();
                var buffer = new byte[4096];
                for (int count; (count = await BuilderHttp.ReadAsync(stream, buffer, token, policy)) != 0;)
                {
                    if (bytes.Length + count > 262144) throw new InvalidDataException("The setup list is too large.");
                    bytes.Write(buffer, 0, count);
                }
                var tags = Encoding.UTF8.GetString(bytes.ToArray()).Split('\n').Where(line => line.StartsWith("tag "))
                    .Select(line => line[4..].Trim()).ToArray();
                if (tags.Length != 1 || string.IsNullOrWhiteSpace(tags[0]) || tags[0].Any(char.IsControl))
                    throw new InvalidDataException("The setup list has no unambiguous release tag.");
                return tags[0];
            }
            catch (HttpRequestException error) when (BuilderHttp.Transient(error) && attempt == 0)
            {
                await Task.Delay(policy.Delays[0], token);
            }
        }
        return null;
    }

    public static async Task<LatestRelease> FetchAsync(HttpClient http, CancellationToken token,
        Action<string>? note = null, BuilderHttp.Policy? policy = null)
    {
        using var doc = JsonDocument.Parse(await BuilderHttp.JsonAsync(http, Api, token, note, policy));
        var tag = doc.RootElement.GetProperty("tag_name").GetString();
        if (string.IsNullOrWhiteSpace(tag)) throw new InvalidDataException("The release has no tag.");
        ReleaseAsset? Find(string name)
        {
            var matches = doc.RootElement.GetProperty("assets").EnumerateArray()
                .Where(a => a.GetProperty("name").GetString() == name).ToArray();
            if (matches.Length > 1) throw new InvalidDataException("The release has duplicate " + name + " assets.");
            return matches.Length == 0 ? null : ReleaseAsset.Read(matches[0]);
        }
        var zip = Find(Asset);
        var manifest = Find(SetupList);
        if (zip == null && manifest == null)
            throw new InvalidDataException("The latest release has no builder archive or setup list. It may still be uploading.");
        var identity = await ReleaseIdentity.ResolveAsync(tag, (path, ct) => BuilderHttp.JsonAsync(http, path, ct, note, policy), token);
        return new(identity, zip, manifest);
    }
}
