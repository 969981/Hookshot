#ifndef HOOKSHOT_SETUP_LOCATION_H
#define HOOKSHOT_SETUP_LOCATION_H

#include "setup_transaction.h"

#define SETUP_LOCATION_MAGIC "HookshotInstallLocation 1\n"

static inline int setup_read_location(const wchar_t *preferences, wchar_t root[SETUP_PLAN_PATH]) {
    wchar_t path[SETUP_PLAN_PATH], safe[SETUP_PLAN_PATH], candidate[SETUP_PLAN_PATH];
    char bytes[1024];
    root[0] = 0;
    if (setup_safe_path(preferences, safe, 0) != SETUP_PLAN_OK || !setup_join(path, safe, L"install-location.txt")) return 0;
    DWORD attributes = GetFileAttributesW(path);
    if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT))) return 0;
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                              NULL, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    DWORD got = 0;
    int ok = ReadFile(file, bytes, sizeof bytes, &got, NULL);
    CloseHandle(file);
    size_t prefix = sizeof SETUP_LOCATION_MAGIC - 1;
    if (!ok || got <= prefix + 1 || got >= sizeof bytes || memcmp(bytes, SETUP_LOCATION_MAGIC, prefix) ||
        bytes[got - 1] != '\n' || memchr(bytes + prefix, '\n', got - (DWORD)prefix - 1) || memchr(bytes, 0, got)) return 0;
    bytes[got - 1] = 0;
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, bytes + prefix, -1, candidate, SETUP_PLAN_PATH)) return 0;
    SetupRootKind kind;
    return setup_root_validate(candidate, root, &kind) == SETUP_PLAN_OK && kind == SETUP_ROOT_OWNED;
}

static inline int setup_write_location(const wchar_t *preferences, const SetupInstallPlan *plan,
                                       const SetupInstallOutcome *outcome, const wchar_t *nonce) {
    wchar_t safe[SETUP_PLAN_PATH], root[SETUP_PLAN_PATH], current[SETUP_PLAN_PATH], pending[SETUP_PLAN_PATH], name[80];
    SetupRootKind kind;
    if (!outcome->committed || !plan->ready || setup_root_validate(plan->root, root, &kind) != SETUP_PLAN_OK ||
        kind != SETUP_ROOT_OWNED || wcslen(nonce) != 32 || setup_safe_path(preferences, safe, 1) != SETUP_PLAN_OK) return 0;
    for (unsigned i = 0; i < 32; i++)
        if (!((nonce[i] >= L'0' && nonce[i] <= L'9') || (nonce[i] >= L'a' && nonce[i] <= L'f'))) return 0;
    char text[1024] = SETUP_LOCATION_MAGIC;
    int prefix = sizeof SETUP_LOCATION_MAGIC - 1;
    int length = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, root, -1, text + prefix,
                                     (int)sizeof text - prefix - 1, NULL, NULL);
    if (!length) return 0;
    text[prefix + length - 1] = '\n';
    text[prefix + length] = 0;
    swprintf(name, sizeof name / sizeof name[0], L".install-location-%ls.txt", nonce);
    if (!setup_join(current, safe, L"install-location.txt") || !setup_join(pending, safe, name)) return 0;
    DWORD attributes = GetFileAttributesW(current);
    if (attributes != INVALID_FILE_ATTRIBUTES && (attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT))) return 0;
    int made = CreateDirectoryW(safe, NULL) != 0;
    if (!made && GetLastError() != ERROR_ALREADY_EXISTS) return 0;
    if (!setup_new_file(pending, text)) { if (made) RemoveDirectoryW(safe); return 0; }
    int ok = MoveFileExW(pending, current, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) != 0;
    if (!ok) { DeleteFileW(pending); if (made) RemoveDirectoryW(safe); }
    return ok;
}

#endif
