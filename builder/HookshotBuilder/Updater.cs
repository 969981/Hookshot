using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Windows;

namespace HookshotBuilder;

/// Game and Builder UI routes share verified preparation; the thin setup executable is never executed.
static class Updater
{
    public static async void FromCommandLine(MainWindow w)
    {
        try
        {
            if (UpdateRequest.Parse(Environment.GetCommandLineArgs()[1..]) is { } request)
                await UpdateFolderAsync(w, request);
        }
        catch (Exception error) { w.DownloadFailed(error.Message + " Your game folder is unchanged."); }
    }

    /// Quiet startup/launcher discovery. This bounds metadata checks, not a progressing asset download.
    public static async Task<ReleaseIdentity?> LatestIdentity()
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(20));
        using var http = Http();
        try { return (await LatestRelease.FetchAsync(http, timeout.Token)).Identity; }
        catch (Exception) { return null; }
    }

    public static async Task<string?> LatestTag() => (await LatestIdentity())?.Tag;

    // Retain the released UI entry point, but prepare the checked Builder itself instead of running setup.
    public static async Task RunLatestSetup(MainWindow w, IEnumerable<string> args, bool repair = false)
    {
        var token = w.DownloadToken;
        using var http = Http();
        w.Note("Asking GitHub for the latest release");
        var latest = await LatestRelease.FetchAsync(http, token, w.Note);
        var target = await PrepareBuilderAsync(w, http, latest, token, repair);
        token.ThrowIfCancellationRequested();
        Process.Start(BuilderLaunch.StartInfo(Path.Combine(target, "HookshotBuilder.exe"), args));
        Application.Current.Shutdown();
    }

    public static async void UpdateFolder(MainWindow w, string folder, string? tag, bool fetched, string? revision = null) =>
        await UpdateFolderAsync(w, new UpdateRequest(folder, tag, revision, fetched));

    static HttpClient Http()
    {
        var http = new HttpClient { Timeout = Timeout.InfiniteTimeSpan };
        http.DefaultRequestHeaders.UserAgent.ParseAdd("HookshotBuilder");
        return http;
    }

    static async Task<string> PrepareBuilderAsync(MainWindow w, HttpClient http, LatestRelease latest, CancellationToken token, bool repair = false)
    {
        var owned = InstallRoot.FromSource(AppContext.BaseDirectory);
        if (!repair && Tools.Find()?.Identity == latest.Identity)
        {
            if (owned != null) await owned.ValidateCurrentAsync(latest.Identity, token);
            else await BuilderContents.ValidateAsync(AppContext.BaseDirectory, latest.Identity, token);
            return AppContext.BaseDirectory;
        }
        if (owned != null)
            return await owned.PrepareAsync(http, latest, token, w.Note, w.DownloadProgress);
        var cache = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "HookshotBuilder", "update");
        var parent = Directory.GetParent(AppContext.BaseDirectory.TrimEnd('\\', '/'))!.FullName;
        var suffix = repair ? "-repair-" + Guid.NewGuid().ToString("N") : "";
        try
        {
            return await UpdatePreparation.PrepareAsync(http, latest, cache,
                Path.Combine(parent, "HookshotBuilder " + latest.Identity.CacheName + suffix), token, w.Note, w.DownloadProgress);
        }
        catch (Exception error) when (error is UnauthorizedAccessException || (error is IOException && error is not InvalidDataException && error is not LegacySetupManifestException))
        {
            return await UpdatePreparation.PrepareAsync(http, latest, cache,
                Path.Combine(Path.GetDirectoryName(cache)!, "builders", latest.Identity.CacheName + suffix), token, w.Note, w.DownloadProgress);
        }
    }

    static async Task UpdateFolderAsync(MainWindow w, UpdateRequest request)
    {
        var (folder, tag, revision, fetched) = request;
        w.BeginUpdate(folder, tag);
        try
        {
            var tools = Tools.Find() ?? throw new InvalidDataException("The Hookshot tools were not found next to this app.");
            if (fetched && revision != null && !ReleaseIdentity.FetchedMatches(tools.Identity, tag, revision))
                throw new InvalidDataException("The downloaded builder is a different build than requested.");
            var token = w.DownloadToken;
            using var http = Http();
            w.Note("Asking GitHub for the latest release");
            var latest = await LatestRelease.FetchAsync(http, token, w.Note);
            if (fetched && !ReleaseIdentity.CanBuildFetched(tools.Identity, latest.Identity, tag, revision))
                throw new InvalidDataException("The release changed while downloading. Retry after publishing finishes.");
            var target = await PrepareBuilderAsync(w, http, latest, token);
            if (tools.Identity == latest.Identity)
            {
                w.UpdateBuild(tools, folder);
                return;
            }
            token.ThrowIfCancellationRequested();
            Process.Start(new UpdateRequest(folder, latest.Identity.Tag, latest.Identity.Revision, true)
                .StartInfo(Path.Combine(target, "HookshotBuilder.exe")));
            Application.Current.Shutdown();
        }
        catch (OperationCanceledException) { w.DownloadFailed("The download was cancelled. Your game folder is unchanged."); }
        catch (Exception error) { w.DownloadFailed(error.Message + " Your game folder is unchanged; download the current release manually if needed."); }
    }
}
