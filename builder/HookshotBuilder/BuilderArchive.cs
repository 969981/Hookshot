using System.IO;
using System.IO.Compression;

namespace HookshotBuilder;

static class BuilderArchive
{
    static async Task<byte[]> PublishedManifestAsync(IReadOnlyList<(string Zip, SetupPart? Part)> archives, CancellationToken token)
    {
        byte[]? result = null;
        foreach (var item in archives)
        {
            if (item.Part?.IsLlvm == true) continue;
            using var zip = ZipFile.OpenRead(item.Zip);
            foreach (var entry in zip.Entries.Where(e => e.FullName == "HookshotBuilder/" + BuilderContents.ManifestName))
            {
                var type = (entry.ExternalAttributes >> 16) & 0xF000;
                if (result != null || entry.Length > 65536 || type != 0 && type != 0x8000 || (entry.ExternalAttributes & 0x400) != 0)
                    throw new InvalidDataException("The release has an invalid or repeated GUI identity manifest.");
                result = new byte[checked((int)entry.Length)];
                await using var input = entry.Open();
                await input.ReadExactlyAsync(result, token);
                var extra = new byte[1];
                if (await input.ReadAsync(extra, token) != 0) throw new InvalidDataException("The GUI identity manifest exceeds its declared size.");
            }
        }
        return result ?? throw new InvalidDataException("The release archive has no GUI identity manifest.");
    }

    static string SafePath(string path)
    {
        if (path.Length == 0 || path.Contains('\\') || path.Contains(':') || path.Any(char.IsControl))
            throw new InvalidDataException("The builder archive has an invalid path.");
        var parts = path.Split('/');
        foreach (var part in parts)
        {
            var stem = part.Split('.')[0].ToUpperInvariant();
            if (part.Length == 0 || part is "." or ".." || part.EndsWith('.') || part.EndsWith(' ') ||
                part.IndexOfAny(['<', '>', '"', '|', '?', '*']) >= 0 || stem is "CON" or "PRN" or "AUX" or "NUL" ||
                (stem.Length == 4 && (stem.StartsWith("COM", StringComparison.Ordinal) || stem.StartsWith("LPT", StringComparison.Ordinal)) &&
                 stem[3] is >= '1' and <= '9'))
                throw new InvalidDataException("The builder archive has an unsafe path.");
        }
        return string.Join(Path.DirectorySeparatorChar, parts);
    }

    static async Task ExtractAsync(string zip, string stage, SetupPart? part, HashSet<string> files, CancellationToken token)
    {
        using var archive = ZipFile.OpenRead(zip);
        var prefix = part?.IsLlvm == true ? part.From + "/" : "HookshotBuilder/";
        var destination = part?.IsLlvm == true ? "HookshotBuilder/tools/llvm-mingw/" : "HookshotBuilder/";
        long unpacked = 0;
        foreach (var entry in archive.Entries)
        {
            token.ThrowIfCancellationRequested();
            var directory = entry.FullName.EndsWith('/');
            var name = entry.FullName.TrimEnd('/');
            SafePath(name);
            var type = (entry.ExternalAttributes >> 16) & 0xF000;
            if (type != 0 && type != 0x8000 && type != 0x4000 || (entry.ExternalAttributes & 0x400) != 0)
                throw new InvalidDataException("Links and special entries are not allowed in builder archives.");
            if (directory && name == prefix.TrimEnd('/')) continue;
            if (!entry.FullName.StartsWith(prefix, StringComparison.Ordinal))
                throw new InvalidDataException("The builder archive has an unexpected root.");
            var relative = destination + name[prefix.Length..];
            var target = Path.Combine(stage, SafePath(relative));
            if (directory) { Directory.CreateDirectory(target); continue; }
            if (!files.Add(relative)) throw new InvalidDataException("Builder parts contain overlapping files.");
            unpacked = checked(unpacked + entry.Length);
            if (unpacked > 16L << 30) throw new InvalidDataException("The builder archive expands beyond its supported size.");
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            await using var input = entry.Open();
            await using var output = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None);
            await input.CopyToAsync(output, token);
        }
    }

    /// Each installation has its own identity and temporary directory. Existing folders are never replaced.
    static async Task<string> InstallAsync(IReadOnlyList<(string Zip, SetupPart? Part)> archives, string target,
                                           ReleaseIdentity expected, CancellationToken token)
    {
        if (Directory.Exists(target))
        {
            if (ReleaseIdentity.Read(Path.Combine(target, "tools")) != expected)
                throw new IOException("An existing builder folder contains a different build; it was left unchanged.");
            await BuilderContents.ValidateAsync(target, expected, token, await PublishedManifestAsync(archives, token));
            return target;
        }
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(target))!);
        var stage = target + ".part-" + Guid.NewGuid().ToString("N");
        Directory.CreateDirectory(stage);
        try
        {
            var files = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var archive in archives) await ExtractAsync(archive.Zip, stage, archive.Part, files, token);
            var inner = Path.Combine(stage, "HookshotBuilder");
            await BuilderContents.ValidateAsync(inner, expected, token);
            token.ThrowIfCancellationRequested();
            Directory.Move(inner, target);
            return target;
        }
        finally { if (Directory.Exists(stage)) Directory.Delete(stage, true); }
    }

    public static Task<string> UnpackAsync(string zip, string target, ReleaseIdentity expected, CancellationToken token = default) =>
        InstallAsync([(zip, null)], target, expected, token);

    public static Task<string> UnpackPartsAsync(IReadOnlyList<(string Zip, SetupPart? Part)> parts, string target,
                                               ReleaseIdentity expected, CancellationToken token) =>
        InstallAsync(parts, target, expected, token);
}
