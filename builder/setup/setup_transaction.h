#ifndef HOOKSHOT_SETUP_TRANSACTION_H
#define HOOKSHOT_SETUP_TRANSACTION_H

#include "setup_plan.h"

typedef enum {
    SETUP_INSTALL_OK, SETUP_INSTALL_CANCELLED, SETUP_INSTALL_INVALID,
    SETUP_INSTALL_BUSY, SETUP_INSTALL_IO, SETUP_INSTALL_PREPARE
} SetupInstallResult;

/* prepare writes only below payload/downloads and returns 1 after validating the
   complete payload, 0 on failure, or -1 on cancellation. No executable is run here. */
typedef int (*SetupPrepare)(const wchar_t *payload, const wchar_t *downloads, void *context);
typedef int (*SetupPublish)(const wchar_t *from, const wchar_t *to, DWORD flags, void *context);
typedef struct {
    SetupPrepare prepare;
    SetupPublish publish; /* NULL uses MoveFileExW; injectable for deterministic I/O failures. */
    void *context;
    void (*after_commit)(const wchar_t *downloads, void *context); /* Optional cache promotion; never runs before commit. */
} SetupInstallHooks;
typedef struct {
    wchar_t directory[SETUP_PLAN_PATH];
    DWORD error;
    int committed;
} SetupInstallOutcome;

static inline int setup_join(wchar_t *out, const wchar_t *parent, const wchar_t *name) {
    int n = swprintf(out, SETUP_PLAN_PATH, L"%ls\\%ls", parent, name);
    return n > 0 && n < SETUP_PLAN_PATH;
}

static inline int setup_new_file(const wchar_t *path, const char *bytes) {
    HANDLE file = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    DWORD written = 0, length = (DWORD)strlen(bytes);
    int ok = WriteFile(file, bytes, length, &written, NULL) && written == length && FlushFileBuffers(file);
    DWORD error = ok ? ERROR_SUCCESS : GetLastError();
    CloseHandle(file);
    if (!ok) DeleteFileW(path);
    SetLastError(error);
    return ok;
}

/* Used exclusively for directories this attempt created, or an owned version
   already detached by pruning. Never follows a junction/symlink into another tree. */
static inline int setup_remove_tree(const wchar_t *directory) {
    DWORD attributes = GetFileAttributesW(directory);
    if (attributes == INVALID_FILE_ATTRIBUTES) return GetLastError() == ERROR_FILE_NOT_FOUND;
    if (!(attributes & FILE_ATTRIBUTE_DIRECTORY) || (attributes & FILE_ATTRIBUTE_REPARSE_POINT)) return 0;
    wchar_t pattern[SETUP_PLAN_PATH], path[SETUP_PLAN_PATH];
    if (!setup_join(pattern, directory, L"*")) return 0;
    WIN32_FIND_DATAW data;
    HANDLE find = FindFirstFileW(pattern, &data);
    int ok = 1;
    if (find == INVALID_HANDLE_VALUE) {
        if (GetLastError() != ERROR_FILE_NOT_FOUND) return 0;
    } else {
        do {
            if (!wcscmp(data.cFileName, L".") || !wcscmp(data.cFileName, L"..")) continue;
            if (!setup_join(path, directory, data.cFileName)) { ok = 0; break; }
            if (data.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) {
                /* Remove the link itself, never its target. */
                if (data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) ok = RemoveDirectoryW(path) && ok;
                else ok = DeleteFileW(path) && ok;
            } else if (data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
                ok = setup_remove_tree(path) && ok;
            } else {
                if (data.dwFileAttributes & FILE_ATTRIBUTE_READONLY)
                    SetFileAttributesW(path, data.dwFileAttributes & ~(DWORD)FILE_ATTRIBUTE_READONLY);
                ok = DeleteFileW(path) && ok;
            }
        } while (FindNextFileW(find, &data));
        if (GetLastError() != ERROR_NO_MORE_FILES) ok = 0;
        FindClose(find);
    }
    return ok && RemoveDirectoryW(directory);
}

static inline int setup_tree_regular(const wchar_t *directory) {
    wchar_t safe[SETUP_PLAN_PATH], pattern[SETUP_PLAN_PATH], path[SETUP_PLAN_PATH];
    if (setup_safe_path(directory, safe, 0) != SETUP_PLAN_OK || !setup_join(pattern, safe, L"*")) return 0;
    WIN32_FIND_DATAW data;
    HANDLE find = FindFirstFileW(pattern, &data);
    if (find == INVALID_HANDLE_VALUE) return GetLastError() == ERROR_FILE_NOT_FOUND;
    int ok = 1;
    do {
        if (!wcscmp(data.cFileName, L".") || !wcscmp(data.cFileName, L"..")) continue;
        if ((data.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) || !setup_join(path, safe, data.cFileName) ||
            ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && !setup_tree_regular(path))) { ok = 0; break; }
    } while (FindNextFileW(find, &data));
    if (ok && GetLastError() != ERROR_NO_MORE_FILES) ok = 0;
    FindClose(find);
    return ok;
}

static inline int setup_claim_empty_root(const wchar_t *root) {
    wchar_t pattern[SETUP_PLAN_PATH];
    if (!setup_join(pattern, root, L"*")) return 0;
    WIN32_FIND_DATAW data;
    HANDLE find = FindFirstFileW(pattern, &data);
    if (find == INVALID_HANDLE_VALUE) return 0; /* Our lock must already exist. */
    int ok = 1;
    do {
        if (wcscmp(data.cFileName, L".") && wcscmp(data.cFileName, L"..") &&
            wcscmp(data.cFileName, L".hookshot-commit.lock")) { ok = 0; break; }
    } while (FindNextFileW(find, &data));
    if (ok && GetLastError() != ERROR_NO_MORE_FILES) ok = 0;
    FindClose(find);
    return ok;
}

typedef struct {
    const wchar_t *leaf, *nonce;
    const SetupInstallHooks *hooks;
    SetupInstallOutcome *outcome;
    SetupInstallResult result;
} SetupInstallCall;

static inline int setup_install_effect(const SetupInstallPlan *plan, void *opaque) {
    SetupInstallCall *call = (SetupInstallCall *)opaque;
    wchar_t root[SETUP_PLAN_PATH], attempt[SETUP_PLAN_PATH] = L"", payload[SETUP_PLAN_PATH], downloads[SETUP_PLAN_PATH];
    wchar_t versions[SETUP_PLAN_PATH], final[SETUP_PLAN_PATH], path[SETUP_PLAN_PATH], current[SETUP_PLAN_PATH];
    wchar_t pending_name[40];
    HANDLE lock = INVALID_HANDLE_VALUE, root_handle = INVALID_HANDLE_VALUE;
    int made_root = 0, made_marker = 0, made_versions = 0, made_attempt = 0, published = 0;
    SetupRootKind kind;
    call->result = SETUP_INSTALL_IO;
    if (setup_root_validate(plan->root, root, &kind) != SETUP_PLAN_OK || kind != plan->root_kind) {
        call->result = SETUP_INSTALL_INVALID;
        goto done;
    }
    if (kind == SETUP_ROOT_NEW) {
        if (!CreateDirectoryW(root, NULL)) goto done;
        made_root = 1;
    }
    root_handle = CreateFileW(root, FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL,
                              OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (root_handle == INVALID_HANDLE_VALUE) goto done;
    if (!setup_join(path, root, L".hookshot-commit.lock")) goto done;
    {
        DWORD attributes = GetFileAttributesW(path);
        if (attributes != INVALID_FILE_ATTRIBUTES && (attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT))) goto done;
    }
    lock = CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_ALWAYS,
                       FILE_ATTRIBUTE_NORMAL | FILE_FLAG_DELETE_ON_CLOSE | FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (lock == INVALID_HANDLE_VALUE) {
        if (GetLastError() == ERROR_SHARING_VIOLATION) call->result = SETUP_INSTALL_BUSY;
        goto done;
    }
    if (kind != SETUP_ROOT_OWNED) {
        if (!setup_claim_empty_root(root)) goto done;
        if (!setup_join(path, root, SETUP_ROOT_MARKER) || !setup_new_file(path, SETUP_ROOT_MAGIC)) goto done;
        made_marker = 1;
    } else if (!setup_marker(root, SETUP_ROOT_MARKER, SETUP_ROOT_MAGIC)) goto done;
    swprintf(pending_name, sizeof pending_name / sizeof pending_name[0], L".setup-%ls", call->nonce);
    if (!setup_join(attempt, root, pending_name) || !CreateDirectoryW(attempt, NULL)) goto done;
    made_attempt = 1;
    if (!setup_join(payload, attempt, L"payload") || !CreateDirectoryW(payload, NULL) ||
        !setup_join(downloads, attempt, L"downloads") || !CreateDirectoryW(downloads, NULL)) goto done;
    {
        int prepared = call->hooks->prepare(payload, downloads, call->hooks->context);
        if (prepared != 1) {
            call->result = prepared < 0 ? SETUP_INSTALL_CANCELLED : SETUP_INSTALL_PREPARE;
            goto done;
        }
    }
    if (!setup_tree_regular(payload) || !setup_join(path, payload, SETUP_VERSION_MARKER) ||
        !setup_new_file(path, SETUP_VERSION_MAGIC)) goto done;
    if (!setup_join(versions, root, L"versions")) goto done;
    if (CreateDirectoryW(versions, NULL)) made_versions = 1;
    else if (GetLastError() != ERROR_ALREADY_EXISTS) goto done;
    {
        wchar_t safe[SETUP_PLAN_PATH];
        if (setup_safe_path(versions, safe, 0) != SETUP_PLAN_OK) goto done;
    }
    if (!setup_join(final, versions, call->leaf) || !setup_join(current, versions, L"current.txt") ||
        !setup_join(path, attempt, L"current.txt")) goto done;
    {
        char leaf[128];
        if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, call->leaf, -1, leaf, sizeof leaf, NULL, NULL) ||
            !setup_new_file(path, leaf)) goto done;
    }
    /* No existing version is replaced, even for Repair of the same release. */
    if (!MoveFileExW(payload, final, MOVEFILE_WRITE_THROUGH)) {
        if (GetLastError() == ERROR_ALREADY_EXISTS || GetLastError() == ERROR_ACCESS_DENIED) call->result = SETUP_INSTALL_BUSY;
        goto done;
    }
    published = 1;
    {
        DWORD attributes = GetFileAttributesW(current);
        if (attributes != INVALID_FILE_ATTRIBUTES && (attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT))) goto done;
    }
    if (call->hooks->publish ? !call->hooks->publish(path, current, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH, call->hooks->context)
                             : !MoveFileExW(path, current, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) goto done;
    call->outcome->committed = 1;
    memcpy(call->outcome->directory, final, (wcslen(final) + 1) * sizeof(wchar_t));
    if (call->hooks->after_commit) call->hooks->after_commit(downloads, call->hooks->context);
    call->result = SETUP_INSTALL_OK;
done:
    call->outcome->error = call->result == SETUP_INSTALL_OK ? ERROR_SUCCESS : GetLastError();
    if (!call->outcome->committed && published) setup_remove_tree(final);
    if (made_attempt) setup_remove_tree(attempt);
    if (!call->outcome->committed && made_versions) RemoveDirectoryW(versions);
    if (!call->outcome->committed && made_marker && setup_join(path, root, SETUP_ROOT_MARKER)) DeleteFileW(path);
    if (lock != INVALID_HANDLE_VALUE) CloseHandle(lock);
    if (root_handle != INVALID_HANDLE_VALUE) CloseHandle(root_handle);
    if (!call->outcome->committed && made_root) RemoveDirectoryW(root);
    return call->outcome->committed;
}

static inline SetupInstallResult setup_install(const SetupInstallPlan *plan, int choice, const wchar_t *leaf,
                                               const wchar_t *nonce, const SetupInstallHooks *hooks, SetupInstallOutcome *outcome) {
    memset(outcome, 0, sizeof *outcome);
    if (choice != SETUP_CHOOSE_INSTALL) return SETUP_INSTALL_CANCELLED;
    if (!hooks || !hooks->prepare || !setup_leaf(leaf) || wcslen(nonce) != 32) return SETUP_INSTALL_INVALID;
    for (unsigned i = 0; i < 32; i++)
        if (!((nonce[i] >= L'0' && nonce[i] <= L'9') || (nonce[i] >= L'a' && nonce[i] <= L'f'))) return SETUP_INSTALL_INVALID;
    if (wcslen(plan->root) + wcslen(L"\\versions\\") + wcslen(leaf) + 1 + plan->max_relative_units >= SETUP_PLAN_PATH)
        return SETUP_INSTALL_INVALID;
    SetupInstallCall call = {leaf, nonce, hooks, outcome, SETUP_INSTALL_INVALID};
    if (setup_plan_begin(plan, choice, setup_install_effect, &call) != SETUP_PLAN_OK && call.result == SETUP_INSTALL_OK)
        return SETUP_INSTALL_INVALID;
    return call.result;
}

#endif
