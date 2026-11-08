using System.IO;
using System.Security.Cryptography;
using System.Text.Json;

namespace HookshotBuilder;

/// Publisher-verified source identity and GUI bytes. This file excludes its own hash.
static class BuilderContents
{
    public const string ManifestName = "builder-identity.json";
    public static readonly string[] GuiFiles = ["HookshotBuilder.exe", "HookshotBuilder.dll"];

    static JsonDocument Parse(string text)
    {
        try { return JsonDocument.Parse(text); }
        catch (JsonException error) { throw new InvalidDataException("The builder GUI identity manifest is malformed.", error); }
    }

    public static async Task ValidateAsync(string root, ReleaseIdentity expected, CancellationToken token = default)
    {
        foreach (var path in new[] { root, Path.Combine(root, "tools"), Path.Combine(root, "tools/version.txt"),
                                    Path.Combine(root, "tools/revision.txt"), Path.Combine(root, ManifestName) })
            if ((File.Exists(path) || Directory.Exists(path)) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException("The builder must not contain identity links.");
        if (ReleaseIdentity.Read(Path.Combine(root, "tools")) != expected)
            throw new InvalidDataException("The downloaded build does not match the current release.");
        var manifest = Path.Combine(root, ManifestName);
        if (!File.Exists(manifest) || new FileInfo(manifest).Length > 65536)
            throw new InvalidDataException("This builder has no supported GUI identity manifest. Download a revision-aware release.");
        using var doc = Parse(await File.ReadAllTextAsync(manifest, token));
        var data = doc.RootElement;
        if (data.ValueKind != JsonValueKind.Object) throw new InvalidDataException("The builder GUI identity manifest is invalid.");
        var fields = data.EnumerateObject().Select(p => p.Name).ToArray();
        if (fields.Length != 4 || fields.Distinct(StringComparer.Ordinal).Count() != 4 ||
            fields.Any(p => p is not ("format" or "tag" or "revision" or "files")))
            throw new InvalidDataException("The builder GUI identity manifest has unexpected or repeated fields.");
        if (data.GetProperty("format").ValueKind != JsonValueKind.Number || !data.GetProperty("format").TryGetInt32(out var format) || format != 1 ||
            data.GetProperty("tag").ValueKind != JsonValueKind.String || data.GetProperty("revision").ValueKind != JsonValueKind.String ||
            data.GetProperty("files").ValueKind != JsonValueKind.Object ||
            ReleaseIdentity.Create(data.GetProperty("tag").GetString(), data.GetProperty("revision").GetString()) != expected)
            throw new InvalidDataException("The builder GUI identity does not match the requested source revision.");
        var entries = data.GetProperty("files").EnumerateObject().ToArray();
        if (entries.Length != GuiFiles.Length || entries.Select(e => e.Name).Distinct(StringComparer.Ordinal).Count() != GuiFiles.Length ||
            entries.Any(e => !GuiFiles.Contains(e.Name, StringComparer.Ordinal)))
            throw new InvalidDataException("The builder GUI manifest has unexpected files.");
        foreach (var entry in entries)
        {
            var digest = entry.Value.ValueKind == JsonValueKind.String ? entry.Value.GetString() : null;
            var path = Path.Combine(root, entry.Name);
            if (digest is not { Length: 64 } || !digest.All(Uri.IsHexDigit) || !File.Exists(path) ||
                (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException("The builder GUI manifest or file is invalid: " + entry.Name);
            await using var input = File.OpenRead(path);
            var actual = Convert.ToHexString(await SHA256.HashDataAsync(input, token));
            if (!actual.Equals(digest, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("The builder GUI file does not match its verified package: " + entry.Name);
        }
    }
}
