using System.ComponentModel;
using System.IO;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

namespace HookshotBuilder;

/// Only roots created by the consent-based native setup opt in. Unmarked legacy
/// installations retain the existing verified updater route.
sealed class InstallRoot
{
    const string RootMarker = ".hookshot-install-root", RootMagic = "HookshotInstallRoot 1\n";
    const string VersionMarker = ".hookshot-install-version", VersionMagic = "HookshotInstallVersion 1\n";
    readonly string root, source;
    InstallRoot(string root, string source) { this.root = root; this.source = source; }
    public string DirectoryPath => root;

    static void PlainPath(string path)
    {
        if (path.Length >= 260 || path.StartsWith(@"\\", StringComparison.Ordinal))
            throw new IOException("The owned installation path is outside the supported Windows path length.");
        for (string? at = path; at != null; at = Path.GetDirectoryName(at))
            if ((File.Exists(at) || Directory.Exists(at)) && (File.GetAttributes(at) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("The owned installation must not contain linked paths.");
    }

    static bool Marker(string directory, string name, string expected)
    {
        var path = Path.Combine(directory, name);
        return File.Exists(path) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) == 0 &&
            new FileInfo(path).Length == Encoding.UTF8.GetByteCount(expected) && File.ReadAllText(path) == expected;
    }

    static bool Exists(string path)
    {
        try { _ = File.GetAttributes(path); return true; }
        catch (FileNotFoundException) { return false; }
        catch (DirectoryNotFoundException) { return false; }
    }

    static bool Leaf(string name)
    {
        if (name.Length is 0 or > 127 || name.StartsWith('.') || name.EndsWith('.') ||
            !name.All(c => char.IsAsciiLetterOrDigit(c) || c is '-' or '_' or '.' or '@')) return false;
        var stem = name.Split('.')[0].ToUpperInvariant();
        return stem is not ("CON" or "PRN" or "AUX" or "NUL") &&
            !(stem.Length == 4 && (stem.StartsWith("COM", StringComparison.Ordinal) || stem.StartsWith("LPT", StringComparison.Ordinal)) && stem[3] is >= '1' and <= '9');
    }

    public static InstallRoot? FromSource(string baseDirectory)
    {
        if (!OperatingSystem.IsWindows()) return null;
        var gui = Path.TrimEndingDirectorySeparator(Path.GetFullPath(baseDirectory));
        for (string? at = gui; at != null; at = Path.GetDirectoryName(at))
        {
            if (!Path.GetFileName(at).Equals("HookshotBuilder", StringComparison.OrdinalIgnoreCase) ||
                !Exists(Path.Combine(at, RootMarker))) continue;
            PlainPath(gui);
            if (!Marker(at, RootMarker, RootMagic)) throw new IOException("The source installation has an invalid ownership marker.");
            var version = Path.GetDirectoryName(gui);
            var versions = version == null ? null : Path.GetDirectoryName(version);
            if (version == null || versions == null || !Path.GetFileName(gui).Equals("HookshotBuilder", StringComparison.OrdinalIgnoreCase) ||
                !Path.GetFileName(versions).Equals("versions", StringComparison.OrdinalIgnoreCase) ||
                !string.Equals(Path.GetDirectoryName(versions), at, StringComparison.OrdinalIgnoreCase) ||
                !Leaf(Path.GetFileName(version)) ||
                !Marker(version, VersionMarker, VersionMagic))
                throw new IOException("The source is outside a recognized owned version. Its installation was left unchanged.");
            return new(at, version);
        }
        return null;
    }

    public async Task ValidateCurrentAsync(ReleaseIdentity expected, CancellationToken token)
    {
        var receipt = Path.Combine(source, ".hookshot-published-gui-sha");
        var gui = Path.Combine(source, "HookshotBuilder");
        var manifest = Path.Combine(gui, BuilderContents.ManifestName);
        PlainPath(receipt);
        PlainPath(manifest);
        if (!File.Exists(receipt) || new FileInfo(receipt).Length != 64 || !File.Exists(manifest) || new FileInfo(manifest).Length > 65536)
            throw new InvalidDataException("The installed GUI has no supported verified-package receipt. Use Repair.");
        var digest = await File.ReadAllTextAsync(receipt, token);
        var bytes = await File.ReadAllBytesAsync(manifest, token);
        if (Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant() != digest)
            throw new InvalidDataException("The local GUI metadata differs from its verified package. Use Repair.");
        await BuilderContents.ValidateAsync(gui, expected, token);
    }

    [DllImport("kernel32.dll", EntryPoint = "CreateDirectoryW", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool CreateDirectory(string path, IntPtr security);

    static void Claim(string path)
    {
        if (!CreateDirectory(path, IntPtr.Zero)) throw new IOException("Could not claim a new installation attempt.", new Win32Exception());
    }

    static void PlainTree(string directory)
    {
        foreach (var path in System.IO.Directory.EnumerateFileSystemEntries(directory))
        {
            var attributes = File.GetAttributes(path);
            if ((attributes & FileAttributes.ReparsePoint) != 0) throw new IOException("Installation cleanup encountered a link.");
            if ((attributes & FileAttributes.Directory) != 0) PlainTree(path);
        }
    }

    static void FinalPaths(string directory, string final)
    {
        foreach (var path in System.IO.Directory.EnumerateFileSystemEntries(directory))
        {
            var target = Path.Combine(final, Path.GetFileName(path));
            PlainPath(target);
            if ((File.GetAttributes(path) & FileAttributes.Directory) != 0) FinalPaths(path, target);
        }
    }

    // These targets were atomically claimed by this attempt. Existing versions,
    // caches and settings never enter this cleanup path on cancellation/failure.
    static void RemoveAttempt(string path)
    {
        if (!System.IO.Directory.Exists(path)) return;
        PlainPath(path);
        PlainTree(path);
        System.IO.Directory.Delete(path, true);
    }

    string Cache()
    {
        var cache = Path.Combine(root, "update");
        PlainPath(cache);
        if (!System.IO.Directory.Exists(cache))
        {
            Claim(cache);
            try { File.WriteAllText(Path.Combine(cache, ".hookshot-update-cache"), "HookshotUpdateCache 1\n"); }
            catch { RemoveAttempt(cache); throw; }
        }
        if (!Marker(cache, ".hookshot-update-cache", "HookshotUpdateCache 1\n"))
            throw new IOException("An unrecognized update cache occupies the selected installation. It was left unchanged.");
        return cache;
    }

    public async Task<string> PrepareAsync(HttpClient http, LatestRelease latest, CancellationToken token,
                                            Action<string>? note = null, Action<double>? progress = null,
                                            BuilderHttp.Policy? policy = null)
    {
        token.ThrowIfCancellationRequested();
        if (!string.Equals(FromSource(Path.Combine(source, "HookshotBuilder"))?.root, root, StringComparison.OrdinalIgnoreCase))
            throw new IOException("The selected source installation changed. No other installation was selected.");
        var lockPath = Path.Combine(root, ".hookshot-commit.lock");
        PlainPath(lockPath);
        using var ownership = new FileStream(lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None, 1, FileOptions.DeleteOnClose);
        var nonce = Guid.NewGuid().ToString("N");
        var leaf = "v-" + latest.Identity.Revision[..12] + "-" + nonce;
        var attempt = Path.Combine(root, ".setup-" + nonce);
        var payload = Path.Combine(attempt, "payload");
        var target = Path.Combine(payload, "HookshotBuilder");
        var final = Path.Combine(root, "versions", leaf);
        PlainPath(final);
        Claim(attempt);
        var published = false;
        var committed = false;
        try
        {
            await UpdatePreparation.PrepareAsync(http, latest, Cache(), target, token, note, progress, policy);
            token.ThrowIfCancellationRequested();
            PlainTree(payload);
            FinalPaths(payload, final);
            File.WriteAllText(Path.Combine(payload, VersionMarker), VersionMagic);
            var manifest = await File.ReadAllBytesAsync(Path.Combine(target, BuilderContents.ManifestName), token);
            File.WriteAllText(Path.Combine(payload, ".hookshot-published-gui-sha"), Convert.ToHexString(SHA256.HashData(manifest)).ToLowerInvariant());
            var pending = Path.Combine(attempt, "current.txt");
            await File.WriteAllTextAsync(pending, leaf, token);
            token.ThrowIfCancellationRequested();
            System.IO.Directory.Move(payload, final);
            published = true;
            var current = Path.Combine(root, "versions", "current.txt");
            PlainPath(current);
            File.Move(pending, current, true);
            committed = true;
            return Path.Combine(final, "HookshotBuilder");
        }
        finally
        {
            if (published && !committed) RemoveAttempt(final);
            RemoveAttempt(attempt);
        }
    }
}
