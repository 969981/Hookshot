using System.IO;
using System.Text;
using System.Windows;

namespace HookshotBuilder;

public partial class App : Application
{
    // (RecompLauncher) --cli <make_game.py's options>: no window; its lines on stdout, and its result as the exit code.
    // Why it stopped is one "error <text>" line on stdout (the stream a launcher reads) and again on stderr
    protected override void OnStartup(StartupEventArgs e)
    {
        try { _ = BuilderHome.Base; }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException)
        {
            var why = "The Builder home could not be opened. " + error.Message;
            if (e.Args.Length > 0 && e.Args[0] == "--cli")
            {
                using var output = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true };
                output.WriteLine("error " + why);
                Console.Error.WriteLine("error " + why);
            }
            else MessageBox.Show(why, "Hookshot Builder", MessageBoxButton.OK, MessageBoxImage.Error);
            // StartupUri is processed after this handler; a rejected home must never construct MainWindow.
            Environment.Exit(1);
            return;
        }
        // (a portable home other accounts can write: said once per folder, recorded in the home, nothing changed: SharedFolder)
        var shared = BuilderHome.Portable ? SharedFolder.Due(BuilderHome.Base) : null;
        if (e.Args.Length > 0 && e.Args[0] == "--cli")
        {
            if (shared != null)
            {
                using var output = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true };
                output.WriteLine("warning " + shared);
            }
            Environment.Exit(Cli(e.Args.Skip(1).ToList()));
            return;
        }
        if (shared != null) MessageBox.Show(shared, "Hookshot Builder", MessageBoxButton.OK, MessageBoxImage.Warning);
        base.OnStartup(e);
    }

    /// (NVIDIA DLSS: RecompLauncher's tick, recomplauncher.json's prepare option) --dlss --accept-nvidia-license: the build
    /// fetches NVIDIA's DLSS SDK into the builder's ngx-sdk folder and puts NVIDIA's runtimes in each game folder's ngx folder,
    /// keeping the release's renderer, as the window's DLSS tick does (make_game.py --fetch-ngx-sdk). Without --dlss the
    /// folder's runtimes are kept; --no-dlss takes out the ones the builder placed. Returns the SDK folder for the build
    /// (HOOKSHOT_NGX_SDK), or null; error: why --dlss cannot be done. (Longshot 543846eb, #465, made its --cli fetch the SDK)
    internal static string? CliDlss(List<string> args, out string? error)
    {
        error = null;
        if (!args.Contains("--dlss")) return null;
        args.RemoveAll(a => a == "--dlss");
        if (args.Contains("--no-dlss"))
        {
            error = "--dlss and --no-dlss were both given: NVIDIA DLSS is either fetched or taken out.";
            return null;
        }
        if (!args.Contains("--accept-nvidia-license"))
        {
            error = "NVIDIA DLSS (--dlss) downloads NVIDIA's files under NVIDIA's license: it needs --accept-nvidia-license, " +
                    "given once the license was shown and accepted.";
            return null;
        }
        if (!args.Contains("--fetch-ngx-sdk")) args.Add("--fetch-ngx-sdk");
        return Storage.NgxSdk;
    }

    static int Cli(List<string> args)
    {
        using var stdout = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true };
        var tools = Tools.Find();
        if (tools == null)
        {
            const string missing = "error The build tools were not found next to this program.";
            stdout.WriteLine(missing);
            Console.Error.WriteLine(missing);
            return 1;
        }
        var ngxSdk = CliDlss(args, out var dlssError);
        if (dlssError != null)
        {
            stdout.WriteLine("error " + dlssError);
            Console.Error.WriteLine("error " + dlssError);
            return 1;
        }
        var done = new ManualResetEventSlim();
        string? failure = null, said = null;   // (said: make_game's own "error" line in --prepare mode, already passed on to stdout)
        var runner = new Runner(tools, args, ngxSdk: ngxSdk);
        runner.Line += line =>
        {
            if (line.StartsWith("error ")) said = line[6..];
            lock (stdout) stdout.WriteLine(line);
        };
        runner.Finished += result => { failure = result; done.Set(); };
        runner.Start();
        done.Wait();
        if (failure == null) return 0;
        var reason = failure == "cancelled" ? failure : said ?? failure;
        if (said == null) lock (stdout) stdout.WriteLine("error " + reason);
        Console.Error.WriteLine("error " + reason);
        return 1;
    }
}
