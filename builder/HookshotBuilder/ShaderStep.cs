using System.Text.RegularExpressions;

namespace HookshotBuilder;

/// The build's last step, the shaders' preparation (nx2/make_game.py prepare_shaders): the argument that leaves it to the game's
/// first start, and what its row says once the build is done
static class ShaderStep
{
    public const string Id = "shaders";
    public const string Later = "--no-prepare-shaders";
    /// "Compile shaders" (make_game.py --compile-shaders, kept in the folder's hookshot_options.ini as compile_shaders=, which the
    /// launcher's setting writes too): All (the default) builds the predicted pipelines too, Most every shader and the pipelines
    /// the lists name
    public const string Compile = "--compile-shaders";
    public static readonly string[] Levels = { "all", "most" };
    public const string DefaultLevel = "all";   // (hookshot: as before Compile shaders; Most is the choice until measured in play)
    public const string Tip = "All (the default): every shader, the pipelines the game is known to use and the predicted ones. Most: without the predicted pipelines, 20-30% shorter (19% on an RTX 5060 Ti, 30% on an RTX 3090); on one route it cost nothing in play, but more may be compiled the first time you visit a place.";
    public static string Level(string? saved) => saved != null && Array.IndexOf(Levels, saved) >= 0 ? saved : DefaultLevel;
    // (make_game.py NOT_PREPARED: a plain line, so a builder that does not know it only shows it)
    static readonly Regex NotPrepared = new(@"^the shaders were not prepared now \((.*)\): (.*)$");
    const string FirstStart = "the game prepares them at its first start";   // (make_game.py FIRST_START)

    /// make_game's arguments with the player's choice (unticked: the game prepares its shaders at its first start). level: the
    /// Compile shaders shown, for a build (make_game keeps it in the folder); null for an update, which shows none: the folder's
    /// own (the launcher's setting, or the last build's) then stands
    public static List<string> Args(List<string> args, bool prepare, string? level = null)
    {
        if (!prepare) args.Add(Later);
        if (level != null) args.AddRange(new[] { Compile, Level(level) });
        return args;
    }

    /// What the row says when make_game's line says the shaders were not prepared, else null: left to the first start and why,
    /// or (the GPU driver stopped responding) why and what happens instead
    public static string? Note(string line) => NotPrepared.Match(line) is { Success: true } m
        ? m.Groups[2].Value == FirstStart ? $"Left to the game's first start: {m.Groups[1].Value}"
        : $"Not prepared: {m.Groups[1].Value}. {char.ToUpperInvariant(m.Groups[2].Value[0])}{m.Groups[2].Value[1..]}"
        : null;

    /// The line under the row of a finished build when the shaders were not prepared, or null when they were (a check mark).
    /// began: the game's @@stage shaders came; note: Note of make_game's not-prepared line
    public static string? Skipped(bool began, string? note) => note ?? (began ? null : "Left to the game's first start");
}
