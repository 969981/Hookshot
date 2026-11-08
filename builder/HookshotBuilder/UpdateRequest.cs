using System.IO;
using System.Diagnostics;

namespace HookshotBuilder;

sealed record UpdateRequest(string Folder, string? Tag, string? Revision, bool Fetched)
{
    public ProcessStartInfo StartInfo(string executable)
    {
        var start = new ProcessStartInfo(executable) { UseShellExecute = false, WorkingDirectory = Path.GetDirectoryName(executable)! };
        start.ArgumentList.Add("--update"); start.ArgumentList.Add(Folder);
        if (Tag != null) { start.ArgumentList.Add("--tag"); start.ArgumentList.Add(Tag); }
        if (Revision != null) { start.ArgumentList.Add("--revision"); start.ArgumentList.Add(Revision); }
        if (Fetched) start.ArgumentList.Add("--fetched");
        return start;
    }

    public static UpdateRequest? Parse(string[] args)
    {
        if (args.Length == 0) return null;
        string? folder = null, tag = null, revision = null;
        var fetched = false;
        var seen = new HashSet<string>(StringComparer.Ordinal);
        for (var i = 0; i < args.Length; i++)
        {
            var name = args[i];
            if (!seen.Add(name)) throw new InvalidDataException("The update handoff repeats an argument.");
            if (name == "--fetched") { fetched = true; continue; }
            if (name is not ("--update" or "--tag" or "--revision") || i + 1 >= args.Length ||
                string.IsNullOrWhiteSpace(args[i + 1]) || args[i + 1].StartsWith("--", StringComparison.Ordinal))
                throw new InvalidDataException("The update handoff has an invalid argument.");
            var value = args[++i];
            if (name == "--update") folder = value;
            else if (name == "--tag") tag = value;
            else revision = value;
        }
        if (folder == null || (fetched && tag == null) || (revision != null && ReleaseIdentity.Create(tag, revision) == null))
            throw new InvalidDataException("The update handoff has no valid folder or release identity.");
        return new(folder, tag, revision, fetched);
    }
}
