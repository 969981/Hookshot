#ifndef HOOKSHOT_SETUP_PLAN_H
#define HOOKSHOT_SETUP_PLAN_H

/* Read-only preflight contract. Installation and persistence belong behind the explicit gates below. */
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <wchar.h>

#define SETUP_PLAN_PARTS 16
#define SETUP_PLAN_PATH 260
#define SETUP_ROOT_MARKER L".hookshot-install-root"
#define SETUP_ROOT_MAGIC "HookshotInstallRoot 1\n"
#define SETUP_VERSION_MARKER L".hookshot-install-version"
#define SETUP_VERSION_MAGIC "HookshotInstallVersion 1\n"

typedef enum {
    SETUP_PLAN_OK, SETUP_PLAN_BAD_METADATA, SETUP_PLAN_MISSING_SPACE, SETUP_PLAN_OVERFLOW,
    SETUP_PLAN_BAD_PATH, SETUP_PLAN_UNKNOWN_ROOT, SETUP_PLAN_PATH_TOO_LONG, SETUP_PLAN_NO_SPACE,
    SETUP_PLAN_CANCELLED, SETUP_PLAN_STALE
} SetupPlanResult;
typedef enum { SETUP_ROOT_NEW, SETUP_ROOT_EMPTY, SETUP_ROOT_OWNED } SetupRootKind;
typedef struct {
    char sha[65];
    uint64_t expanded, files, entries, max_relative_units;
} SetupSpacePart;
typedef struct {
    char manifest[65];
    unsigned count;
    SetupSpacePart parts[SETUP_PLAN_PARTS];
} SetupSpace;
typedef struct { const char *sha; uint64_t compressed; } SetupPlanPart;
typedef struct {
    wchar_t root[SETUP_PLAN_PATH];
    SetupRootKind root_kind;
    uint64_t compressed, expanded, files, entries, logical_reserve, max_relative_units;
    int ready;
} SetupInstallPlan;

static inline int setup_sha(const char *text) {
    if (strlen(text) != 64) return 0;
    for (unsigned i = 0; i < 64; i++)
        if (!((text[i] >= '0' && text[i] <= '9') || (text[i] >= 'a' && text[i] <= 'f'))) return 0;
    return 1;
}
static inline int setup_u64(const char *text, uint64_t *value) {
    uint64_t n = 0;
    if (!*text) return 0;
    for (; *text; text++) {
        if (*text < '0' || *text > '9' || n > (UINT64_MAX - (unsigned)(*text - '0')) / 10) return 0;
        n = n * 10 + (unsigned)(*text - '0');
    }
    *value = n;
    return 1;
}
static inline int setup_add(uint64_t *total, uint64_t value) {
    if (*total > UINT64_MAX - value) return 0;
    *total += value;
    return 1;
}
static inline SetupPlanResult setup_space_parse(const char *text, size_t length, const char *manifest_sha, SetupSpace *out) {
    SetupSpace parsed = {0};
    unsigned line_number = 0;
    memset(out, 0, sizeof *out);
    if (length > 262144 || !setup_sha(manifest_sha)) return SETUP_PLAN_BAD_METADATA;
    while (length) {
        char line[256], *fields[8];
        size_t n = 0;
        while (n < length && text[n] != '\n') n++;
        if (n >= sizeof line || memchr(text, 0, n)) return SETUP_PLAN_BAD_METADATA;
        memcpy(line, text, n);
        line[n] = 0;
        text += n;
        length -= n;
        if (length) text++, length--;
        if (n && line[n - 1] == '\r') line[--n] = 0;
        if (!n) continue;
        unsigned count = 0;
        char *p = line;
        while (*p) {
            while (*p == ' ') p++;
            if (!*p) break;
            if (count == 8) return SETUP_PLAN_BAD_METADATA;
            fields[count++] = p;
            while (*p && *p != ' ') p++;
            if (*p) *p++ = 0;
        }
        if (line_number == 0) {
            if (count != 2 || strcmp(fields[0], "HookshotSetupSpace") || strcmp(fields[1], "1")) return SETUP_PLAN_BAD_METADATA;
        } else if (line_number == 1) {
            if (count != 2 || strcmp(fields[0], "manifest") || strcmp(fields[1], manifest_sha)) return SETUP_PLAN_BAD_METADATA;
            memcpy(parsed.manifest, manifest_sha, 65);
        } else {
            if (count != 6 || strcmp(fields[0], "space") || !setup_sha(fields[1]) || parsed.count == SETUP_PLAN_PARTS)
                return SETUP_PLAN_BAD_METADATA;
            SetupSpacePart *part = &parsed.parts[parsed.count];
            memcpy(part->sha, fields[1], 65);
            if (!setup_u64(fields[2], &part->expanded) || !setup_u64(fields[3], &part->files) ||
                !setup_u64(fields[4], &part->entries) || !setup_u64(fields[5], &part->max_relative_units) ||
                part->files > part->entries || part->max_relative_units > 32767 ||
                (!part->entries && (part->expanded || part->files || part->max_relative_units)) ||
                (part->entries && !part->max_relative_units) || (!part->files && part->expanded)) return SETUP_PLAN_BAD_METADATA;
            for (unsigned i = 0; i < parsed.count; i++)
                if (!strcmp(parsed.parts[i].sha, part->sha)) return SETUP_PLAN_BAD_METADATA;
            parsed.count++;
        }
        line_number++;
    }
    if (line_number < 2) return SETUP_PLAN_BAD_METADATA;
    *out = parsed;
    return SETUP_PLAN_OK;
}

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <stdio.h>

static inline int setup_path_equal(const wchar_t *a, const wchar_t *b) {
    return CompareStringOrdinal(a, -1, b, -1, TRUE) == CSTR_EQUAL;
}
static inline int setup_leaf(const wchar_t *leaf) {
    size_t n = wcslen(leaf), base = 0;
    if (!n || n > 127 || leaf[0] == L'.' || leaf[n - 1] == L'.') return 0;
    while (base < n && leaf[base] != L'.') base++;
    const wchar_t *reserved[] = {L"CON", L"PRN", L"AUX", L"NUL", L"COM1", L"COM2", L"COM3", L"COM4", L"COM5",
                                L"COM6", L"COM7", L"COM8", L"COM9", L"LPT1", L"LPT2", L"LPT3", L"LPT4", L"LPT5",
                                L"LPT6", L"LPT7", L"LPT8", L"LPT9"};
    for (unsigned i = 0; i < sizeof reserved / sizeof reserved[0]; i++)
        if (base == wcslen(reserved[i]) && CompareStringOrdinal(leaf, (int)base, reserved[i], (int)base, TRUE) == CSTR_EQUAL) return 0;
    for (size_t i = 0; i < n; i++)
        if (!((leaf[i] >= L'a' && leaf[i] <= L'z') || (leaf[i] >= L'A' && leaf[i] <= L'Z') ||
              (leaf[i] >= L'0' && leaf[i] <= L'9') || leaf[i] == L'-' || leaf[i] == L'_' || leaf[i] == L'.' || leaf[i] == L'@')) return 0;
    return 1;
}
static inline int setup_marker(const wchar_t *directory, const wchar_t *name, const char *magic) {
    wchar_t path[SETUP_PLAN_PATH];
    char bytes[64];
    int n = swprintf(path, SETUP_PLAN_PATH, L"%ls\\%ls", directory, name);
    if (n < 0 || n >= SETUP_PLAN_PATH) return 0;
    DWORD attributes = GetFileAttributesW(path);
    if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT))) return 0;
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                              NULL, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    DWORD got = 0;
    int ok = ReadFile(file, bytes, sizeof bytes, &got, NULL) && got == strlen(magic) && !memcmp(bytes, magic, got);
    CloseHandle(file);
    return ok;
}
static inline SetupPlanResult setup_safe_path(const wchar_t *input, wchar_t output[SETUP_PLAN_PATH], int allow_absent) {
    if (wcslen(input) < 3 || !((input[0] >= L'A' && input[0] <= L'Z') || (input[0] >= L'a' && input[0] <= L'z')) ||
        input[1] != L':' || (input[2] != L'\\' && input[2] != L'/')) return SETUP_PLAN_BAD_PATH;
    DWORD length = GetFullPathNameW(input, SETUP_PLAN_PATH, output, NULL);
    if (!length) return SETUP_PLAN_BAD_PATH;
    if (length >= SETUP_PLAN_PATH) return SETUP_PLAN_PATH_TOO_LONG;
    while (length > 3 && output[length - 1] == L'\\') output[--length] = 0;
    for (DWORD i = 3; i <= length; i++) {
        if (output[i] != L'\\' && output[i]) continue;
        wchar_t saved = output[i];
        output[i] = 0;
        DWORD attributes = GetFileAttributesW(output);
        DWORD error = attributes == INVALID_FILE_ATTRIBUTES ? GetLastError() : 0;
        output[i] = saved;
        if (attributes == INVALID_FILE_ATTRIBUTES) {
            if (allow_absent && i == length && (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND)) return SETUP_PLAN_OK;
            return SETUP_PLAN_BAD_PATH;
        }
        if (!(attributes & FILE_ATTRIBUTE_DIRECTORY) || (attributes & FILE_ATTRIBUTE_REPARSE_POINT)) return SETUP_PLAN_BAD_PATH;
    }
    return SETUP_PLAN_OK;
}
static inline SetupPlanResult setup_root_validate(const wchar_t *input, wchar_t root[SETUP_PLAN_PATH], SetupRootKind *kind) {
    SetupPlanResult result = setup_safe_path(input, root, 1);
    if (result != SETUP_PLAN_OK) return result;
    const wchar_t *name = wcsrchr(root, L'\\');
    if (!name || !setup_path_equal(name + 1, L"HookshotBuilder")) return SETUP_PLAN_BAD_PATH;
    DWORD attributes = GetFileAttributesW(root);
    if (attributes == INVALID_FILE_ATTRIBUTES) { *kind = SETUP_ROOT_NEW; return SETUP_PLAN_OK; }
    if (setup_marker(root, SETUP_ROOT_MARKER, SETUP_ROOT_MAGIC)) { *kind = SETUP_ROOT_OWNED; return SETUP_PLAN_OK; }
    wchar_t pattern[SETUP_PLAN_PATH];
    int n = swprintf(pattern, SETUP_PLAN_PATH, L"%ls\\*", root);
    if (n < 0 || n >= SETUP_PLAN_PATH) return SETUP_PLAN_PATH_TOO_LONG;
    WIN32_FIND_DATAW data;
    HANDLE find = FindFirstFileW(pattern, &data);
    if (find == INVALID_HANDLE_VALUE) {
        if (GetLastError() != ERROR_FILE_NOT_FOUND) return SETUP_PLAN_BAD_PATH;
        *kind = SETUP_ROOT_EMPTY;
        return SETUP_PLAN_OK;
    }
    int empty = 1;
    do { if (wcscmp(data.cFileName, L".") && wcscmp(data.cFileName, L"..")) empty = 0; } while (FindNextFileW(find, &data));
    DWORD error = GetLastError();
    FindClose(find);
    if (error != ERROR_NO_MORE_FILES) return SETUP_PLAN_BAD_PATH;
    if (!empty) return SETUP_PLAN_UNKNOWN_ROOT;
    *kind = SETUP_ROOT_EMPTY;
    return SETUP_PLAN_OK;
}
static inline SetupPlanResult setup_root_child(const wchar_t *parent, wchar_t root[SETUP_PLAN_PATH], SetupRootKind *kind) {
    wchar_t canonical[SETUP_PLAN_PATH], child[SETUP_PLAN_PATH];
    SetupPlanResult result = setup_safe_path(parent, canonical, 0);
    if (result != SETUP_PLAN_OK) return result;
    int n = swprintf(child, SETUP_PLAN_PATH, L"%ls\\HookshotBuilder", canonical);
    if (n < 0 || n >= SETUP_PLAN_PATH) return SETUP_PLAN_PATH_TOO_LONG;
    return setup_root_validate(child, root, kind);
}
static inline int setup_owned_version(const wchar_t *root, const wchar_t *version) {
    wchar_t owned[SETUP_PLAN_PATH], path[SETUP_PLAN_PATH], canonical[SETUP_PLAN_PATH];
    SetupRootKind kind;
    if (!setup_leaf(version) || setup_root_validate(root, owned, &kind) != SETUP_PLAN_OK || kind != SETUP_ROOT_OWNED) return 0;
    int n = swprintf(path, SETUP_PLAN_PATH, L"%ls\\versions\\%ls", owned, version);
    return n > 0 && n < SETUP_PLAN_PATH && setup_safe_path(path, canonical, 0) == SETUP_PLAN_OK &&
           setup_marker(canonical, SETUP_VERSION_MARKER, SETUP_VERSION_MAGIC);
}
static inline int setup_source_root(const wchar_t *module, wchar_t root[SETUP_PLAN_PATH]) {
    wchar_t path[SETUP_PLAN_PATH], original[SETUP_PLAN_PATH], safe[SETUP_PLAN_PATH];
    DWORD n = GetFullPathNameW(module, SETUP_PLAN_PATH, original, NULL);
    if (!n || n >= SETUP_PLAN_PATH) return 0;
    DWORD attributes = GetFileAttributesW(original);
    if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT))) return 0;
    memcpy(path, original, (n + 1) * sizeof(wchar_t));
    wchar_t *parent = wcsrchr(path, L'\\');
    if (!parent) return 0;
    *parent = 0;
    if (setup_safe_path(path, safe, 0) != SETUP_PLAN_OK) return 0;
    memcpy(path, original, (n + 1) * sizeof(wchar_t));
    for (wchar_t *slash = wcsrchr(path, L'\\'); slash; slash = wcsrchr(path, L'\\')) {
        *slash = 0;
        SetupRootKind kind;
        wchar_t candidate[SETUP_PLAN_PATH];
        if (setup_root_validate(path, candidate, &kind) == SETUP_PLAN_OK && kind == SETUP_ROOT_OWNED) {
            size_t length = wcslen(candidate);
            if (CompareStringOrdinal(original, (int)length, candidate, (int)length, TRUE) != CSTR_EQUAL ||
                wcsncmp(original + length, L"\\versions\\", 10)) return 0;
            const wchar_t *version = original + length + 10, *end = wcschr(version, L'\\');
            if (!end || end == version || end - version > 127) return 0;
            wchar_t leaf[128];
            memcpy(leaf, version, (size_t)(end - version) * sizeof(wchar_t));
            leaf[end - version] = 0;
            if (!setup_leaf(leaf) || (!setup_path_equal(end, L"\\HookshotBuilder\\HookshotBuilder.exe") &&
                                    !setup_path_equal(end, L"\\HookshotBuilder\\HookshotBuilder-Setup.exe"))) return 0;
            if (!setup_owned_version(candidate, leaf)) return 0;
            memcpy(root, candidate, (length + 1) * sizeof(wchar_t));
            return 1;
        }
    }
    return 0;
}
static inline int setup_under_owned_root(const wchar_t *module) {
    wchar_t path[SETUP_PLAN_PATH], root[SETUP_PLAN_PATH];
    DWORD n = GetFullPathNameW(module, SETUP_PLAN_PATH, path, NULL);
    if (!n || n >= SETUP_PLAN_PATH) return 0;
    for (wchar_t *slash = wcsrchr(path, L'\\'); slash; slash = wcsrchr(path, L'\\')) {
        *slash = 0;
        SetupRootKind kind;
        if (setup_root_validate(path, root, &kind) == SETUP_PLAN_OK && kind == SETUP_ROOT_OWNED) return 1;
    }
    return 0;
}
static inline int setup_preferred_root(const wchar_t *module, const wchar_t *saved, int existing_install, wchar_t root[SETUP_PLAN_PATH]) {
    root[0] = 0;
    if (setup_source_root(module, root)) return 1;
    if (existing_install) return 0; /* Update/Repair must not relocate an unrecognized source installation. */
    SetupRootKind kind;
    wchar_t candidate[SETUP_PLAN_PATH];
    if (!saved || setup_root_validate(saved, candidate, &kind) != SETUP_PLAN_OK || kind != SETUP_ROOT_OWNED) return 0;
    memcpy(root, candidate, (wcslen(candidate) + 1) * sizeof(wchar_t));
    return 2;
}
static inline SetupPlanResult setup_plan_create(const wchar_t *root, const wchar_t *version, const char *manifest_sha,
                                               const SetupSpace *space, const SetupPlanPart *parts, unsigned count,
                                               uint64_t available_bytes, SetupInstallPlan *out) {
    SetupInstallPlan plan = {0};
    memset(out, 0, sizeof *out);
    if (!setup_leaf(version) || !setup_sha(manifest_sha) || strcmp(space->manifest, manifest_sha)) return SETUP_PLAN_BAD_METADATA;
    SetupPlanResult result = setup_root_validate(root, plan.root, &plan.root_kind);
    if (result != SETUP_PLAN_OK) return result;
    if (!count || count > SETUP_PLAN_PARTS || space->count != count) return SETUP_PLAN_MISSING_SPACE;
    for (unsigned i = 0; i < count; i++) {
        if (!setup_sha(parts[i].sha) || !parts[i].compressed) return SETUP_PLAN_BAD_METADATA;
        for (unsigned j = 0; j < i; j++) if (!strcmp(parts[i].sha, parts[j].sha)) return SETUP_PLAN_BAD_METADATA;
        const SetupSpacePart *hint = NULL;
        for (unsigned j = 0; j < space->count; j++) if (!strcmp(parts[i].sha, space->parts[j].sha)) hint = &space->parts[j];
        if (!hint) return SETUP_PLAN_MISSING_SPACE;
        if (hint->max_relative_units > 32767) return SETUP_PLAN_BAD_METADATA;
        if (!setup_add(&plan.compressed, parts[i].compressed) || !setup_add(&plan.expanded, hint->expanded) ||
            !setup_add(&plan.files, hint->files) || !setup_add(&plan.entries, hint->entries)) return SETUP_PLAN_OVERFLOW;
        if (hint->max_relative_units > plan.max_relative_units) plan.max_relative_units = hint->max_relative_units;
    }
    plan.logical_reserve = plan.compressed;
    if (!setup_add(&plan.logical_reserve, plan.expanded)) return SETUP_PLAN_OVERFLOW;
    /* Final payload, unique pending payload, and pending SHA-named downloads must all fit the native path buffers. */
    uint64_t length = (uint64_t)wcslen(plan.root);
    uint64_t final_prefix = (uint64_t)wcslen(L"\\versions\\") + (uint64_t)wcslen(version) + 1;
    uint64_t pending_prefix = (uint64_t)wcslen(L"\\.setup-") + 32;
    uint64_t payload_suffix = (uint64_t)wcslen(L"\\payload\\");
    uint64_t cache_suffix = (uint64_t)wcslen(L"\\downloads\\") + 64 + (uint64_t)wcslen(L".zip");
    /* Keep validated totals available to the chooser; these plans cannot install. */
    *out = plan;
    if (length + final_prefix + plan.max_relative_units >= SETUP_PLAN_PATH ||
        length + pending_prefix + payload_suffix + plan.max_relative_units >= SETUP_PLAN_PATH ||
        length + pending_prefix + cache_suffix >= SETUP_PLAN_PATH) return SETUP_PLAN_PATH_TOO_LONG;
    if (plan.logical_reserve > available_bytes) return SETUP_PLAN_NO_SPACE;
    plan.ready = 1;
    *out = plan;
    return SETUP_PLAN_OK;
}
typedef int (*SetupPlanEffect)(const SetupInstallPlan *, void *);
enum { SETUP_CHOOSE_INSTALL = 1, SETUP_CHOOSE_CHANGE = 2, SETUP_CHOOSE_CANCEL = 3 };
static inline SetupPlanResult setup_plan_begin(const SetupInstallPlan *plan, int choice, SetupPlanEffect begin, void *context) {
    if (choice != SETUP_CHOOSE_INSTALL) return SETUP_PLAN_CANCELLED;
    if (!plan->ready) return SETUP_PLAN_MISSING_SPACE;
    wchar_t root[SETUP_PLAN_PATH];
    SetupRootKind kind;
    SetupPlanResult result = setup_root_validate(plan->root, root, &kind);
    if (result != SETUP_PLAN_OK || kind != plan->root_kind) return SETUP_PLAN_STALE;
    return begin(plan, context) ? SETUP_PLAN_OK : SETUP_PLAN_STALE;
}
static inline int setup_plan_remember(const SetupInstallPlan *plan, int committed, SetupPlanEffect remember, void *context) {
    return committed && plan->ready ? remember(plan, context) : 0;
}
#endif
#endif
