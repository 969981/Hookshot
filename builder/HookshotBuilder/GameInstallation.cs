using System.IO;

namespace HookshotBuilder;

enum BuilderMode { Build, UpdateGame, Play, UpdateBuilder }

/// Shared by the files page and the --launch page. A display tag alone never proves a folder is current.
sealed record GameInstallation(string? Tag, ReleaseIdentity? Identity, bool CanRebuild)
{
    public static GameInstallation Read(string folder)
    {
        string? tag = null, revision = null;
        try
        {
            foreach (var line in File.ReadAllLines(Path.Combine(folder, "hookshot_version.txt")))
            {
                if (line.StartsWith("version=")) tag = line[8..].Trim();
                else if (line.StartsWith("revision=")) revision = line[9..].Trim();
            }
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException) { }
        return new(string.IsNullOrWhiteSpace(tag) ? null : tag, ReleaseIdentity.Create(tag, revision),
            Directory.Exists(Path.Combine(folder, ".hookshot_exefs")) && Directory.Exists(Path.Combine(folder, "romfs")));
    }

    public bool Current(ReleaseIdentity? latest) => latest != null && Identity == latest;
    public bool UpdateAvailable(ReleaseIdentity? latest) => CanRebuild && Tag != null && latest != null && Identity != latest;

    public BuilderMode Mode(ReleaseIdentity? latest, string? builderTag, ReleaseIdentity? builderIdentity, bool sourcePicked)
    {
        if (!sourcePicked && UpdateAvailable(latest)) return BuilderMode.UpdateGame;
        if (!sourcePicked && Tag != null) return BuilderMode.Play;
        if (latest != null && builderTag != null && builderIdentity != latest) return BuilderMode.UpdateBuilder;
        return BuilderMode.Build;
    }

    public static string Executable(string folder)
    {
        var binary = Path.Combine(folder, "bin", "hookshot.exe");
        return File.Exists(binary) ? binary : Path.Combine(folder, "hookshot.exe");
    }
}
