using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Windows;

namespace HookshotBuilder;

/// Both partial and full-archive updates are prepared and verified here; the thin setup executable is never executed.
static class Updater
{
    public static async void FromCommandLine(MainWindow w)
    {
        try
        {
            var request = UpdateRequest.Parse(Environment.GetCommandLineArgs()[1..]);
            if (request == null) return;
            var (folder, tag, revision, fetched) = request;
            w.BeginUpdate(folder);
            var tools = Tools.Find() ?? throw new InvalidDataException("The Hookshot tools were not found next to this app.");
            if (fetched && revision != null && !ReleaseIdentity.FetchedMatches(tools.Identity, tag, revision))
                throw new InvalidDataException("The downloaded builder is a different build than requested.");
            var token = w.DownloadToken;
            using var http = new HttpClient { Timeout = Timeout.InfiniteTimeSpan };
            http.DefaultRequestHeaders.UserAgent.ParseAdd("HookshotBuilder");
            w.Note("Asking GitHub for the latest release");
            var latest = await LatestRelease.FetchAsync(http, token, w.Note);
            if (fetched && !ReleaseIdentity.CanBuildFetched(tools.Identity, latest.Identity, tag, revision))
                throw new InvalidDataException("The release changed while downloading. Retry after publishing finishes.");
            if (tools.Identity == latest.Identity)
            {
                await BuilderContents.ValidateAsync(AppContext.BaseDirectory, latest.Identity, token);
                w.UpdateBuild(tools, folder);
                return;
            }
            var cache = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "HookshotBuilder", "update");
            var parent = Directory.GetParent(AppContext.BaseDirectory.TrimEnd('\\', '/'))!.FullName;
            string target;
            try
            {
                target = await UpdatePreparation.PrepareAsync(http, latest, cache,
                    Path.Combine(parent, "HookshotBuilder " + latest.Identity.CacheName), token, w.Note, w.DownloadProgress);
            }
            catch (Exception error) when (error is UnauthorizedAccessException || (error is IOException && error is not InvalidDataException && error is not LegacySetupManifestException))
            {
                target = await UpdatePreparation.PrepareAsync(http, latest, cache,
                    Path.Combine(Path.GetDirectoryName(cache)!, "builders", latest.Identity.CacheName), token, w.Note, w.DownloadProgress);
            }
            token.ThrowIfCancellationRequested();
            var start = new UpdateRequest(folder, latest.Identity.Tag, latest.Identity.Revision, true)
                .StartInfo(Path.Combine(target, "HookshotBuilder.exe"));
            Process.Start(start);
            Application.Current.Shutdown();
        }
        catch (OperationCanceledException) { w.DownloadFailed("The download was cancelled. Your game folder is unchanged."); }
        catch (Exception error) { w.DownloadFailed(error.Message + " Your game folder is unchanged; download the current release manually if needed."); }
    }
}
