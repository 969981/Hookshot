#ifndef HOOKSHOT_SETUP_GUI_H
#define HOOKSHOT_SETUP_GUI_H

#include "setup_transaction.h"
#include <stdlib.h>

static inline void setup_json_space(const char **text) {
    while (**text == ' ' || **text == '\t' || **text == '\r' || **text == '\n') (*text)++;
}
static inline int setup_json_char(const char **text, char expected) {
    setup_json_space(text);
    if (**text != expected) return 0;
    (*text)++;
    return 1;
}
/* Publisher keys, safe tag, revision and digest strings contain no escapes. */
static inline int setup_json_string(const char **text, char *out, size_t capacity) {
    if (!setup_json_char(text, '"')) return 0;
    size_t n = 0;
    while (**text && **text != '"') {
        unsigned char c = (unsigned char)**text;
        if (c < 32 || c >= 127 || c == '\\' || n + 1 >= capacity) return 0;
        out[n++] = *(*text)++;
    }
    out[n] = 0;
    return setup_json_char(text, '"');
}

static inline int setup_gui_parse(const char *text, const char *tag, const char *revision, char digests[2][65]) {
    unsigned fields = 0;
    if (!setup_json_char(&text, '{')) return 0;
    do {
        char key[32], value[128];
        if (!setup_json_string(&text, key, sizeof key) || !setup_json_char(&text, ':')) return 0;
        unsigned field;
        if (!strcmp(key, "format")) {
            field = 1;
            if (!setup_json_char(&text, '1')) return 0;
        } else if (!strcmp(key, "tag")) {
            field = 2;
            if (!setup_json_string(&text, value, sizeof value) || strcmp(value, tag)) return 0;
        } else if (!strcmp(key, "revision")) {
            field = 4;
            if (!setup_json_string(&text, value, sizeof value) || strcmp(value, revision)) return 0;
        } else if (!strcmp(key, "files")) {
            field = 8;
            unsigned files = 0;
            if (!setup_json_char(&text, '{')) return 0;
            do {
                if (!setup_json_string(&text, key, sizeof key) || !setup_json_char(&text, ':') ||
                    !setup_json_string(&text, value, sizeof value) || !setup_sha(value)) return 0;
                unsigned index = !strcmp(key, "HookshotBuilder.exe") ? 0u : !strcmp(key, "HookshotBuilder.dll") ? 1u : 2u;
                if (index == 2 || (files & (1u << index))) return 0;
                files |= 1u << index;
                memcpy(digests[index], value, 65);
                setup_json_space(&text);
                if (*text != ',') break;
                text++;
            } while (1);
            if (files != 3 || !setup_json_char(&text, '}')) return 0;
        } else return 0;
        if (fields & field) return 0;
        fields |= field;
        setup_json_space(&text);
        if (*text != ',') break;
        text++;
    } while (1);
    if (fields != 15 || !setup_json_char(&text, '}')) return 0;
    setup_json_space(&text);
    return !*text;
}

static inline char *setup_read_regular(const wchar_t *path, DWORD limit) {
    DWORD attributes = GetFileAttributesW(path);
    if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT))) return NULL;
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (file == INVALID_HANDLE_VALUE) return NULL;
    LARGE_INTEGER length;
    DWORD got = 0;
    char *text = NULL;
    if (GetFileSizeEx(file, &length) && length.QuadPart >= 0 && (uint64_t)length.QuadPart <= limit) {
        text = (char *)malloc((size_t)length.QuadPart + 1);
        if (text && ReadFile(file, text, (DWORD)length.QuadPart, &got, NULL) && got == (DWORD)length.QuadPart && !memchr(text, 0, got))
            text[got] = 0;
        else { free(text); text = NULL; }
    }
    CloseHandle(file);
    return text;
}

typedef int (*SetupFileHash)(const wchar_t *, char digest[65]);

static inline int setup_line_matches(const wchar_t *path, const char *expected) {
    char *text = setup_read_regular(path, 256);
    if (!text) return 0;
    size_t length = strlen(text);
    if (length && text[length - 1] == '\n') text[--length] = 0;
    if (length && text[length - 1] == '\r') text[--length] = 0;
    int ok = !strcmp(text, expected);
    free(text);
    return ok;
}

/* The receipt anchors reusable local GUI metadata to bytes verified while the
   archive was prepared. Neither this check nor its parser loads a PE image. */
static inline int setup_gui_validate(const wchar_t *directory, const char *tag, const char *revision,
                                      SetupFileHash hash, int require_receipt) {
    wchar_t path[SETUP_PLAN_PATH], safe[SETUP_PLAN_PATH];
    char digests[2][65], manifest_sha[65];
    if (setup_safe_path(directory, safe, 0) != SETUP_PLAN_OK || !setup_join(path, safe, L"builder-identity.json")) return 0;
    char *text = setup_read_regular(path, 65536);
    if (!text) return 0;
    int ok = setup_gui_parse(text, tag, revision, digests) && hash(path, manifest_sha);
    free(text);
    if (!ok) return 0;
    wchar_t tools[SETUP_PLAN_PATH];
    if (!setup_join(tools, safe, L"tools") || setup_safe_path(tools, path, 0) != SETUP_PLAN_OK ||
        !setup_join(path, tools, L"version.txt") || !setup_line_matches(path, tag) ||
        !setup_join(path, tools, L"revision.txt") || !setup_line_matches(path, revision)) return 0;
    if (require_receipt) {
        if (!setup_join(path, safe, L"..\\.hookshot-published-gui-sha")) return 0;
        text = setup_read_regular(path, 64);
        ok = text && !strcmp(text, manifest_sha);
        free(text);
        if (!ok) return 0;
    }
    const wchar_t *names[] = {L"HookshotBuilder.exe", L"HookshotBuilder.dll"};
    for (unsigned i = 0; i < 2; i++) {
        char actual[65];
        if (!setup_join(path, safe, names[i])) return 0;
        DWORD attributes = GetFileAttributesW(path);
        if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) ||
            !hash(path, actual) || strcmp(actual, digests[i])) return 0;
    }
    return 1;
}

#endif
