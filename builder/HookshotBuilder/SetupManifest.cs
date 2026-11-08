using System.IO;

namespace HookshotBuilder;

sealed class LegacySetupManifestException() : IOException("This release's setup list predates revision-aware updates.");
sealed record SetupPart(string Sha256, long Size, string Destination, string From, string To, string Url)
{
    public ReleaseAsset Asset => new(Url, Size, "sha256:" + Sha256);
    public bool IsLlvm => Destination == "HookshotBuilder/tools";
}

sealed record SetupManifest(IReadOnlyList<SetupPart> Parts)
{
    public static SetupManifest Parse(string text, ReleaseIdentity expected)
    {
        if (text.Length > 262144) throw new InvalidDataException("The setup list is too large.");
        string? tag = null, revision = null, run = null;
        var parts = new List<SetupPart>();
        foreach (var line in text.Split('\n'))
        {
            var fields = line.TrimEnd('\r').Split(' ', StringSplitOptions.RemoveEmptyEntries);
            if (fields.Length == 0) continue;
            if (fields[0] == "tag" && fields.Length == 2 && tag == null) tag = fields[1];
            else if (fields[0] == "identity" && fields.Length == 3 && fields[1] == "1" && revision == null) revision = fields[2];
            else if (fields[0] == "run" && fields.Length == 2 && run == null) run = fields[1];
            else if (fields[0] == "part" && fields.Length == 7 && parts.Count < 16)
            {
                if (fields[1].Length != 64 || !fields[1].All(Uri.IsHexDigit) ||
                    !long.TryParse(fields[2], out var size) || size <= 0 ||
                    !Uri.TryCreate(fields[6], UriKind.Absolute, out var url) || url.Scheme != "https" || url.UserInfo.Length != 0)
                    throw new InvalidDataException("The setup list has an invalid part.");
                var internalPart = fields[3] == "." && fields[4] == "-" && fields[5] == "-";
                var llvmPart = fields[3] == "HookshotBuilder/tools" && fields[5] == "llvm-mingw" &&
                    fields[4].StartsWith("llvm-mingw-", StringComparison.Ordinal) &&
                    fields[4].All(c => char.IsAsciiLetterOrDigit(c) || c is '-' or '_' or '.');
                if (!internalPart && !llvmPart) throw new InvalidDataException("The setup list has an unsupported destination or rename.");
                parts.Add(new(fields[1].ToLowerInvariant(), size, fields[3], fields[4], fields[5], fields[6]));
            }
            else throw new InvalidDataException("The setup list has an unsupported or duplicate field.");
        }
        if (tag != expected.Tag || run != "HookshotBuilder/HookshotBuilder.exe" || parts.Count == 0)
            throw new InvalidDataException("The setup list does not describe the requested builder.");
        if (revision == null) throw new LegacySetupManifestException();
        if (ReleaseIdentity.Create(tag, revision) != expected || parts.Select(p => p.Sha256).Distinct().Count() != parts.Count ||
            parts.Count(p => p.IsLlvm) > 1)
            throw new InvalidDataException("The setup list has a different revision or duplicate parts.");
        return new(parts);
    }
}
