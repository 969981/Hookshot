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

sealed record LatestRelease(ReleaseIdentity Identity, string Url, long Size, string? Digest)
{
    public const string Api = "https://api.github.com/repos/NotRexed/Hookshot/releases/latest";
    public const string Asset = "HookshotBuilder.zip";

    public static async Task<LatestRelease> FetchAsync(HttpClient http, CancellationToken token)
    {
        using var doc = JsonDocument.Parse(await http.GetStringAsync(Api, token));
        var tag = doc.RootElement.GetProperty("tag_name").GetString();
        if (string.IsNullOrWhiteSpace(tag)) throw new InvalidDataException("The release has no tag.");
        var assets = doc.RootElement.GetProperty("assets").EnumerateArray()
            .Where(a => a.GetProperty("name").GetString() == Asset).ToArray();
        if (assets.Length != 1) throw new InvalidDataException("The latest release has no unique " + Asset + ". It may still be uploading.");
        var asset = assets[0];
        var url = asset.GetProperty("browser_download_url").GetString();
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) || uri.Scheme != "https")
            throw new InvalidDataException("The release download URL is invalid.");
        string? digest = asset.TryGetProperty("digest", out var value) ? value.GetString() : null;
        if (digest != null && (digest.Length != 71 || !digest.StartsWith("sha256:", StringComparison.Ordinal) ||
                              !digest[7..].All(Uri.IsHexDigit)))
            throw new InvalidDataException("The release download digest is invalid.");
        var identity = await ReleaseIdentity.ResolveAsync(tag, (path, ct) => http.GetStringAsync(path, ct), token);
        return new(identity, url!, asset.GetProperty("size").GetInt64(), digest?.ToLowerInvariant());
    }
}
