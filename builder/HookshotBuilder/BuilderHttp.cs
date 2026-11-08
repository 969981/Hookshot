using System.Net.Http;
using System.Text.Json;

namespace HookshotBuilder;

/// The released retry policy, with bounded network inactivity rather than a cap on a large active download.
static class BuilderHttp
{
    internal sealed record Policy(TimeSpan Header, TimeSpan Read, IReadOnlyList<TimeSpan> Delays);
    internal static readonly Policy Default = new(TimeSpan.FromSeconds(30), TimeSpan.FromSeconds(60),
        [TimeSpan.FromSeconds(3), TimeSpan.FromSeconds(6), TimeSpan.FromSeconds(12), TimeSpan.FromSeconds(20)]);

    internal static bool Transient(HttpRequestException error) => error.StatusCode is null || (int)error.StatusCode >= 500;

    internal static async Task<HttpResponseMessage> OpenAsync(HttpClient http, string url, bool apiAsset,
        CancellationToken token, Policy policy, bool buffered = false)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, url);
        if (apiAsset) request.Headers.Accept.ParseAdd("application/octet-stream");
        else if (buffered) request.Headers.Accept.ParseAdd("application/vnd.github+json");
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token);
        timeout.CancelAfter(policy.Header);
        try
        {
            var response = await http.SendAsync(request, buffered ? HttpCompletionOption.ResponseContentRead : HttpCompletionOption.ResponseHeadersRead,
                timeout.Token);
            if (response.IsSuccessStatusCode) return response;
            var error = new HttpRequestException($"GitHub returned HTTP {(int)response.StatusCode} ({response.ReasonPhrase}).", null, response.StatusCode);
            response.Dispose();
            throw error;
        }
        catch (OperationCanceledException error) when (!token.IsCancellationRequested)
        {
            throw new HttpRequestException("The download server did not answer before the header timeout.", error);
        }
    }

    internal static async Task<int> ReadAsync(Stream stream, Memory<byte> buffer, CancellationToken token, Policy policy)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token);
        timeout.CancelAfter(policy.Read);
        try { return await stream.ReadAsync(buffer, timeout.Token); }
        catch (OperationCanceledException error) when (!token.IsCancellationRequested)
        {
            throw new HttpRequestException("The download stopped sending data before the inactivity timeout.", error);
        }
        catch (IOException error) { throw new HttpRequestException("The download connection ended unexpectedly.", error); }
    }

    internal static async Task DelayAsync(int attempt, Policy policy, CancellationToken token, Action<string>? note)
    {
        note?.Invoke($"GitHub did not answer. Retrying in {(int)policy.Delays[attempt].TotalSeconds} s...");
        await Task.Delay(policy.Delays[attempt], token);
    }

    public static async Task<string> JsonAsync(HttpClient http, string url, CancellationToken token,
        Action<string>? note = null, Policy? policy = null)
    {
        policy ??= Default;
        for (var attempt = 0; ; attempt++)
        {
            try
            {
                using var response = await OpenAsync(http, url, false, token, policy, buffered: true);
                return await response.Content.ReadAsStringAsync(token);
            }
            catch (HttpRequestException error) when (Transient(error) && attempt < policy.Delays.Count)
            {
                await DelayAsync(attempt, policy, token, note);
            }
        }
    }

    /// Checksum-addressed setup parts may come from another official GitHub release (including llvm-mingw).
    internal static async Task<string?> ApiAssetAsync(HttpClient http, string url, CancellationToken token, Policy policy)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) || uri.Scheme != "https" || uri.UserInfo.Length != 0 ||
            !uri.Host.Equals("github.com", StringComparison.OrdinalIgnoreCase)) return null;
        var parts = uri.AbsolutePath.Split('/', StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length != 6 || parts[2] != "releases") return null;
        string endpoint;
        if (parts[3] == "download") endpoint = "tags/" + parts[4];
        else if (parts[3] == "latest" && parts[4] == "download") endpoint = "latest";
        else return null;
        var api = $"https://api.github.com/repos/{parts[0]}/{parts[1]}/releases/{endpoint}";
        // A single bounded lookup lives inside the asset's retry cycle; do not multiply two retry loops.
        using var response = await OpenAsync(http, api, false, token, policy, buffered: true);
        using var document = JsonDocument.Parse(await response.Content.ReadAsStringAsync(token));
        var name = Uri.UnescapeDataString(parts[5]);
        var assets = document.RootElement.GetProperty("assets").EnumerateArray()
            .Where(asset => asset.GetProperty("name").GetString() == name).ToArray();
        if (assets.Length > 1) throw new InvalidDataException("The release has repeated download assets.");
        return assets.Length == 0 ? null : ReleaseAsset.ApiUrl(assets[0]);
    }
}
