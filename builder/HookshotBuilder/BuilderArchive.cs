using System.IO;
using System.IO.Compression;

namespace HookshotBuilder;

static class BuilderArchive
{
    /// Never replace a running builder or an unrelated existing directory.
    public static async Task<string> UnpackAsync(string zip, string target, ReleaseIdentity expected)
    {
        if (Directory.Exists(target))
        {
            if (ReleaseIdentity.Read(Path.Combine(target, "tools")) == expected) return target;
            throw new IOException("An existing builder folder contains a different build; it was left unchanged.");
        }
        var part = target + ".part-" + Guid.NewGuid().ToString("N");
        try
        {
            await Task.Run(() => ZipFile.ExtractToDirectory(zip, part));
            var inner = Directory.Exists(Path.Combine(part, "HookshotBuilder")) ? Path.Combine(part, "HookshotBuilder") : part;
            if (ReleaseIdentity.Read(Path.Combine(inner, "tools")) != expected)
                throw new InvalidDataException("The downloaded build does not match the current release. Retry after the release finishes publishing.");
            if (!File.Exists(Path.Combine(inner, "HookshotBuilder.exe")))
                throw new InvalidDataException("The download has no HookshotBuilder.exe.");
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            Directory.Move(inner, target);
            return target;
        }
        finally
        {
            if (Directory.Exists(part)) Directory.Delete(part, true);
        }
    }
}
