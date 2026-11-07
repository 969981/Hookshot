using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Security.Cryptography;
using System.Windows;

namespace HookshotBuilder;

/// The game's update offer (host/hos_update.c) starts this app with --update <game folder> --tag <release>. When these tools are
/// not that tag AND source revision, the release's HookshotBuilder.zip is downloaded and unpacked beside this app. A downloaded
/// builder validates the expected identity before building; --fetched is not a bypass for missing or mismatched metadata.
static class Updater
{
    public static async void FromCommandLine(MainWindow w)
    {
        var a = Environment.GetCommandLineArgs();
        string? folder = null, tag = null, revision = null;
        var fetched = false;
        for (var i = 1; i < a.Length; i++)
        {
            if (a[i] == "--update" && i + 1 < a.Length) folder = a[++i];
            else if (a[i] == "--tag" && i + 1 < a.Length) tag = a[++i];
            else if (a[i] == "--revision" && i + 1 < a.Length) revision = a[++i];
            else if (a[i] == "--fetched") fetched = true;
        }
        if (folder == null) return;
        w.BeginUpdate(folder);
        var tools = Tools.Find();
        if (tools == null)
        {
            w.DownloadFailed("The Hookshot tools were not found next to this app.");
            return;
        }
        string? zip = null;
        try
        {
            if (fetched && revision != null && !ReleaseIdentity.FetchedMatches(tools.Identity, tag, revision))
                throw new InvalidDataException("The downloaded builder is a different build than requested. Download the current release and try again.");
            var token = w.DownloadToken;
            using var http = new HttpClient { Timeout = Timeout.InfiniteTimeSpan };
            http.DefaultRequestHeaders.UserAgent.ParseAdd("HookshotBuilder");
            w.Note("Asking GitHub for the latest release");
            LatestRelease latest;
            using (var ask = new CancellationTokenSource(TimeSpan.FromSeconds(30)))
            using (var both = CancellationTokenSource.CreateLinkedTokenSource(token, ask.Token))
            {
                latest = await LatestRelease.FetchAsync(http, both.Token);
            }
            if (fetched && !ReleaseIdentity.CanBuildFetched(tools.Identity, latest.Identity, tag, revision))
                throw new InvalidDataException("The release changed while downloading. Retry the update after it finishes publishing.");
            if (tools.Identity == latest.Identity)
            {
                w.UpdateBuild(tools, folder);
                return;
            }
            var name = latest.Identity.CacheName;
            var cache = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "HookshotBuilder", "update");
            Directory.CreateDirectory(cache);
            zip = Path.Combine(cache, "HookshotBuilder-" + name + "-" + Guid.NewGuid().ToString("N") + ".zip");
            w.Note($"Downloading {LatestRelease.Asset} of {latest.Identity.Tag} ({latest.Size >> 20} MB)");
            using (var response = await http.GetAsync(latest.Url, HttpCompletionOption.ResponseHeadersRead, token))
            {
                response.EnsureSuccessStatusCode();
                var total = response.Content.Headers.ContentLength ?? latest.Size;
                await using var from = await response.Content.ReadAsStreamAsync(token);
                await using var to = File.Create(zip);
                var buffer = new byte[1 << 20];
                long have = 0;
                var shown = DateTime.MinValue;
                for (int n; (n = await from.ReadAsync(buffer, token)) > 0;)
                {
                    await to.WriteAsync(buffer.AsMemory(0, n), token);
                    have += n;
                    if (total > 0 && DateTime.UtcNow - shown > TimeSpan.FromMilliseconds(200))
                    {
                        shown = DateTime.UtcNow;
                        w.DownloadProgress(Math.Min(1.0, (double)have / total));
                    }
                }
                if (total > 0 && have != total) throw new IOException($"The download ended early ({have >> 20} of {total >> 20} MB).");
            }
            if (latest.Digest != null)
            {
                await using var input = File.OpenRead(zip);
                var digest = "sha256:" + Convert.ToHexString(await SHA256.HashDataAsync(input, token)).ToLowerInvariant();
                if (digest != latest.Digest) throw new InvalidDataException("The downloaded builder does not match the release's digest. Retry the update.");
            }
            w.Note("Unpacking the new version");
            // (beside this app when that folder takes writing, else in the app data folder)
            var parent = Directory.GetParent(AppContext.BaseDirectory.TrimEnd('\\', '/'))!.FullName;
            string target;
            try
            {
                target = await BuilderArchive.UnpackAsync(zip, Path.Combine(parent, "HookshotBuilder " + name), latest.Identity);
            }
            catch (Exception e) when (e is UnauthorizedAccessException || (e is IOException && e is not InvalidDataException))
            {
                target = await BuilderArchive.UnpackAsync(zip, Path.Combine(Path.GetDirectoryName(cache)!, "builders", name), latest.Identity);
            }
            token.ThrowIfCancellationRequested();
            var exe = Path.Combine(target, "HookshotBuilder.exe");
            if (!File.Exists(exe)) throw new FileNotFoundException("The download has no HookshotBuilder.exe (your antivirus may have removed it).");
            var si = new ProcessStartInfo(exe) { UseShellExecute = false, WorkingDirectory = target };
            foreach (var arg in new[] { "--update", folder, "--tag", latest.Identity.Tag, "--revision", latest.Identity.Revision, "--fetched" }) si.ArgumentList.Add(arg);
            Process.Start(si);
            Application.Current.Shutdown();
        }
        catch (OperationCanceledException)
        {
            w.DownloadFailed("The download was cancelled. Your game folder is unchanged.");
        }
        catch (Exception e)
        {
            w.DownloadFailed(e.Message + " Your game folder is unchanged; you can also download the new version from GitHub yourself.");
        }
        finally
        {
            if (zip != null) try { File.Delete(zip); } catch (IOException) { } catch (UnauthorizedAccessException) { }
        }
    }

}
