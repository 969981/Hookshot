using System.IO;
using System.Net.Http;

namespace HookshotBuilder;

/// Download and validate without executing an installer or a downloaded GUI.
static class UpdatePreparation
{
    public static async Task<string> PrepareAsync(HttpClient http, LatestRelease latest, string cache, string target,
                                                  CancellationToken token, Action<string>? note = null, Action<double>? progress = null)
    {
        token.ThrowIfCancellationRequested();
        SetupManifest? manifest = null;
        if (latest.Manifest is { } list)
        {
            if (list.Size > 262144) throw new InvalidDataException("The setup list is too large.");
            note?.Invoke("Checking the release's setup list");
            var path = await BuilderDownload.GetAsync(http, list, Path.Combine(cache, "lists"), token);
            try { manifest = SetupManifest.Parse(await File.ReadAllTextAsync(path, token), latest.Identity); }
            catch (LegacySetupManifestException) when (latest.Zip != null) { /* A checked full archive supports older list formats. */ }
        }
        string result;
        if (manifest != null)
        {
            var parts = new List<(string Zip, SetupPart? Part)>();
            for (var i = 0; i < manifest.Parts.Count; i++)
            {
                var part = manifest.Parts[i];
                note?.Invoke($"Checking update part {i + 1} of {manifest.Parts.Count}");
                var zip = await BuilderDownload.GetAsync(http, part.Asset, Path.Combine(cache, "parts"), token, progress);
                parts.Add((zip, part));
            }
            result = await BuilderArchive.UnpackPartsAsync(parts, target, latest.Identity, token);
        }
        else if (latest.Zip is { } full)
        {
            note?.Invoke("Downloading the full builder archive");
            var zip = await BuilderDownload.GetAsync(http, full, Path.Combine(cache, "archives"), token, progress);
            result = await BuilderArchive.UnpackAsync(zip, target, latest.Identity, token);
        }
        else throw new InvalidDataException("This release needs revision-aware setup metadata before it can update automatically. Download it manually.");
        using var refresh = CancellationTokenSource.CreateLinkedTokenSource(token);
        refresh.CancelAfter(TimeSpan.FromSeconds(30));
        if ((await LatestRelease.FetchAsync(http, refresh.Token)).Identity != latest.Identity)
            throw new InvalidDataException("The release changed while preparing the update. Retry after publishing finishes.");
        await BuilderContents.ValidateAsync(result, latest.Identity, token);
        return result;
    }
}
