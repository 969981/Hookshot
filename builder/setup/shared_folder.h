/* Whether other accounts on this PC can change files in a folder: the portable Builder's home sits wherever the player put
   it, and a folder that every account may write (C:\Games under the root's Authenticated Users: Modify, C:\Users\Public)
   lets another account replace the Builder or its tools. Nothing is changed (a portable home moves between PCs, where an
   owner-only ACL would lock it); the setup and the Builder say so once (builder/HookshotBuilder/SharedFolder.cs reads it
   the same way).

   hb_others_can_write(folder, who, n): 1 when an allow ACE for Everyone, Users or Authenticated Users grants a write right
   on the folder or on what it holds (who: that group's name), 0 when not, and 0 on a removable drive or a file system
   without ACLs (FAT, exFAT), where it means nothing. tests/test_portable_shared_warning.py compiles this header alone. */
#ifndef HB_SHARED_FOLDER_H
#define HB_SHARED_FOLDER_H
#include <windows.h>
#include <aclapi.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

#define HB_SHARED_WARNING L".hookshot-shared-folder.txt"
#define HB_SHARED_TEXT L"Other accounts on this PC can change files in %ls. If you share this PC, move the Builder to a folder only you can write."   /* (in the home: the warning's record, and that it was said) */

static int hb_others_can_write(const wchar_t *folder, wchar_t *who, size_t n) {
    if (who && n) who[0] = 0;
    wchar_t volume[MAX_PATH];
    DWORD flags = 0;
    if (!GetVolumePathNameW(folder, volume, MAX_PATH)) return 0;
    if (GetDriveTypeW(volume) == DRIVE_REMOVABLE) return 0;
    if (!GetVolumeInformationW(volume, NULL, 0, NULL, NULL, &flags, NULL, 0) || !(flags & FILE_PERSISTENT_ACLS)) return 0;
    PACL dacl = NULL;
    PSECURITY_DESCRIPTOR sd = NULL;
    if (GetNamedSecurityInfoW(folder, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, NULL, NULL, &dacl, NULL, &sd) != ERROR_SUCCESS)
        return 0;
    static const struct { WELL_KNOWN_SID_TYPE type; const wchar_t *name; } groups[] = {
        {WinWorldSid, L"Everyone"}, {WinBuiltinUsersSid, L"Users"}, {WinAuthenticatedUserSid, L"Authenticated Users"}};
    const DWORD write = FILE_WRITE_DATA | FILE_APPEND_DATA | FILE_DELETE_CHILD | WRITE_DAC | WRITE_OWNER | DELETE |
                        GENERIC_WRITE | GENERIC_ALL;
    int found = 0;
    if (!dacl) {   /* (no DACL at all: everyone may do anything) */
        found = 1;
        if (who && n) wcsncpy(who, L"Everyone", n - 1), who[n - 1] = 0;
    }
    for (DWORD i = 0; dacl && !found && i < dacl->AceCount; i++) {
        ACE_HEADER *ace;
        if (!GetAce(dacl, i, (void **)&ace) || ace->AceType != ACCESS_ALLOWED_ACE_TYPE) continue;
        ACCESS_ALLOWED_ACE *allow = (ACCESS_ALLOWED_ACE *)ace;
        if (!(allow->Mask & write)) continue;
        for (size_t g = 0; g < sizeof groups / sizeof *groups && !found; g++) {
            BYTE sid[SECURITY_MAX_SID_SIZE];
            DWORD size = sizeof sid;
            if (CreateWellKnownSid(groups[g].type, NULL, sid, &size) && EqualSid(sid, (PSID)&allow->SidStart)) {
                found = 1;
                if (who && n) wcsncpy(who, groups[g].name, n - 1), who[n - 1] = 0;
            }
        }
    }
    LocalFree(sd);
    return found;
}

/* The warning, in the player's words: home is the portable home's folder */
static void hb_shared_text(wchar_t *out, size_t n, const wchar_t *home) {
    swprintf(out, n, HB_SHARED_TEXT, home);
}

/* 1 when the warning is to be said now: others can write home, and its record does not name this folder yet (said once
   per folder: a home moved elsewhere is looked at again). The record is written then: the time, the folder, the group */
static int hb_shared_warning_due(const wchar_t *home, wchar_t *text, size_t n) {
    wchar_t who[64], record[MAX_PATH];
    if (!hb_others_can_write(home, who, 64)) return 0;
    if (swprintf(record, MAX_PATH, L"%ls\\%ls", home, HB_SHARED_WARNING) < 0) return 0;
    char have[4 * MAX_PATH] = "", want[4 * MAX_PATH];
    if (WideCharToMultiByte(CP_UTF8, 0, home, -1, want, (int)sizeof want, NULL, NULL) <= 0) return 0;
    HANDLE f = CreateFileW(record, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (f != INVALID_HANDLE_VALUE) {
        DWORD got = 0;
        ReadFile(f, have, sizeof have - 1, &got, NULL);
        have[got] = 0;
        CloseHandle(f);
        if (strstr(have, want)) return 0;
    }
    hb_shared_text(text, n, home);
    f = CreateFileW(record, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f != INVALID_HANDLE_VALUE) {
        SYSTEMTIME t;
        GetSystemTime(&t);
        char line[6 * MAX_PATH], whom[256];
        WideCharToMultiByte(CP_UTF8, 0, who, -1, whom, (int)sizeof whom, NULL, NULL);
        const int len = snprintf(line, sizeof line, "%04u-%02u-%02u %02u:%02uZ: %s may write %s\r\n", t.wYear, t.wMonth, t.wDay,
                                 t.wHour, t.wMinute, whom, want);
        DWORD wrote;
        if (len > 0) WriteFile(f, line, (DWORD)len, &wrote, NULL);
        CloseHandle(f);
    }
    return 1;
}
#endif
