using System.IO;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;

namespace HookshotBuilder;

/// (portable homes) Whether other accounts on this PC can change files in the Builder's home: an allow rule for Everyone,
/// Users or Authenticated Users with a write right. Nothing is changed (a portable home moves between PCs, where an owner-only
/// ACL would lock it): the Builder says so once per folder, as the setup does (builder/setup/shared_folder.h, the same rule and
/// the same record in the home). A removable drive or a file system without ACLs (FAT, exFAT) is skipped: it means nothing there.
static class SharedFolder
{
    public const string Record = ".hookshot-shared-folder.txt";
    static readonly (WellKnownSidType Type, string Name)[] Groups =
        { (WellKnownSidType.WorldSid, "Everyone"), (WellKnownSidType.BuiltinUsersSid, "Users"), (WellKnownSidType.AuthenticatedUserSid, "Authenticated Users") };
    // (FILE_WRITE_DATA, FILE_APPEND_DATA, FILE_DELETE_CHILD, DELETE, WRITE_DAC, WRITE_OWNER, GENERIC_ALL, GENERIC_WRITE)
    const int Write = 0x2 | 0x4 | 0x40 | 0x10000 | 0x40000 | 0x80000 | 0x10000000 | 0x40000000;

    /// The group that may write folder, or null
    public static string? OthersCanWrite(string folder)
    {
        try
        {
            var drive = new DriveInfo(Path.GetPathRoot(Path.GetFullPath(folder))!);
            if (drive.DriveType == DriveType.Removable || drive.DriveFormat.StartsWith("FAT", StringComparison.OrdinalIgnoreCase) ||
                drive.DriveFormat.Equals("exFAT", StringComparison.OrdinalIgnoreCase))
                return null;
            var rules = new DirectoryInfo(folder).GetAccessControl().GetAccessRules(true, true, typeof(SecurityIdentifier));
            foreach (FileSystemAccessRule rule in rules)
            {
                if (rule.AccessControlType != AccessControlType.Allow || ((int)rule.FileSystemRights & Write) == 0) continue;
                foreach (var (type, name) in Groups)
                    if (rule.IdentityReference.Equals(new SecurityIdentifier(type, null))) return name;
            }
        }
        catch (Exception) { }   // (an unreadable ACL or drive: nothing said, as the setup does)
        return null;
    }

    public static string Text(string home) =>
        $"Other accounts on this PC can change files in {home}. If you share this PC, move the Builder to a folder only you can write.";

    /// The warning when it is to be said now (others may write home, and its record does not name this folder yet), the record
    /// written then; null otherwise
    public static string? Due(string home)
    {
        var who = OthersCanWrite(home);
        if (who == null) return null;
        var record = Path.Combine(home, Record);
        try
        {
            if (File.Exists(record) && File.ReadAllText(record, Encoding.UTF8).Contains(home, StringComparison.OrdinalIgnoreCase)) return null;
            File.WriteAllText(record, $"{DateTime.UtcNow:yyyy-MM-dd HH:mm}Z: {who} may write {home}\r\n", new UTF8Encoding(false));
        }
        catch (Exception) { }
        return Text(home);
    }
}
