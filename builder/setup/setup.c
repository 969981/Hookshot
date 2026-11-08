/* HookshotBuilder-Setup.exe: the one file players download. It reads the latest release's hookshot-setup.txt, downloads
   the parts it names (the builder and its tools from this repository's release, llvm-mingw from its own official
   release), checks each one's SHA-256, unpacks them with Windows' own tar.exe into %LOCALAPPDATA%\HookshotBuilder\<tag>
   and starts the builder there. Parts already downloaded and checked are used again; a release already installed just
   starts. Files fetched by a program carry no "downloaded from the internet" mark, and the download itself holds no
   scripts, which is what antivirus programs' machine-learning guesses (Trojan:Script/...!ml) fired on.

   hookshot-setup.txt, one item a line:
       tag <tag>
       part <sha256> <bytes> <folder under the install> <top folder in the zip to rename, or -> <new name, or -> <url>
       run <the builder, relative to the install>
   Build: clang -O2 -mwindows setup.c setup_rc.o -lwinhttp -lbcrypt -lcomctl32 -lshell32 -lole32 -o HookshotBuilder-Setup.exe */
#define WIN32_LEAN_AND_MEAN
#ifndef UNICODE
#define UNICODE
#endif
#define _UNICODE
#include <windows.h>
#include <winhttp.h>
#include <bcrypt.h>
#include <commctrl.h>
#include <shlobj.h>
#include <shellapi.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include "setup_consent.h"
#include "setup_dialog.h"
#include "setup_location.h"
#include "setup_request.h"
#include "setup_gui.h"

#define WM_STATUS (WM_APP + 1)
#define WM_DONE (WM_APP + 2)
#define MAX_PARTS 16

static HWND g_win, g_text, g_bar;
static wchar_t g_status[512];
static volatile LONG g_percent = -1;
static DWORD g_http_status;   /* (the last answer: 0 when the server was not reached) */
static wchar_t g_cmdline[2048];
static HANDLE g_cancel_event;

typedef struct {
    char sha[65];
    unsigned long long size;
    char dest[260], from[260], to[260], url[1024];
} Part;

static void status(const wchar_t *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vswprintf(g_status, 512, fmt, ap);
    va_end(ap);
    PostMessageW(g_win, WM_STATUS, 0, 0);
}

static void fail(const wchar_t *fmt, ...) {
    wchar_t msg[1024];
    va_list ap;
    va_start(ap, fmt);
    vswprintf(msg, 1024, fmt, ap);
    va_end(ap);
    if (WaitForSingleObject(g_cancel_event, 0) != WAIT_OBJECT_0)
        MessageBoxW(g_win, msg, L"Hookshot Builder setup", MB_OK | MB_ICONERROR);
    PostMessageW(g_win, WM_DONE, 1, 0);
    ExitThread(1);
}

/* ---- HTTPS GET into a sink, following redirects (GitHub's release links go to its download host) ---- */
typedef int (*Sink)(void *user, const void *data, DWORD n);

static int http_get(const char *url8, Sink sink, void *user, unsigned long long expect, const wchar_t *accept) {
    wchar_t url[1024];
    MultiByteToWideChar(CP_UTF8, 0, url8, -1, url, 1024);
    URL_COMPONENTS uc = {sizeof uc};
    wchar_t host[256], path[1024];
    uc.lpszHostName = host, uc.dwHostNameLength = 256;
    uc.lpszUrlPath = path, uc.dwUrlPathLength = 1024;
    if (!WinHttpCrackUrl(url, 0, 0, &uc)) return 0;
    HINTERNET s = WinHttpOpen(L"HookshotBuilder-Setup/1.0", WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY, NULL, NULL, 0);
    if (!s) s = WinHttpOpen(L"HookshotBuilder-Setup/1.0", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY, NULL, NULL, 0);
    if (!s) return 0;
    /* (a stalled download fails after a minute without data, with the message, instead of waiting forever: a GitHub 504 hour) */
    WinHttpSetTimeouts(s, 30000, 30000, 30000, 60000);
    DWORD policy = WINHTTP_OPTION_REDIRECT_POLICY_DISALLOW_HTTPS_TO_HTTP;   /* (a redirect never drops to clear text) */
    WinHttpSetOption(s, WINHTTP_OPTION_REDIRECT_POLICY, &policy, sizeof policy);
    HINTERNET c = WinHttpConnect(s, host, uc.nPort, 0);
    LPCWSTR types[2] = {accept, NULL};
    HINTERNET r = c ? WinHttpOpenRequest(c, L"GET", path, NULL, WINHTTP_NO_REFERER, accept ? types : WINHTTP_DEFAULT_ACCEPT_TYPES,
                                         uc.nScheme == INTERNET_SCHEME_HTTPS ? WINHTTP_FLAG_SECURE : 0)
                    : NULL;
    int ok = 0;
    if (r && WinHttpSendRequest(r, WINHTTP_NO_ADDITIONAL_HEADERS, 0, NULL, 0, 0, 0) && WinHttpReceiveResponse(r, NULL)) {
        DWORD code = 0, len = sizeof code;
        WinHttpQueryHeaders(r, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, NULL, &code, &len, NULL);
        g_http_status = code;
        if (code == 200) {
            static char buf[1 << 16];
            unsigned long long got = 0;
            ok = 1;
            for (;;) {
                DWORD n = 0;
                if (!WinHttpReadData(r, buf, sizeof buf, &n)) { ok = 0; break; }
                if (!n) break;
                if (!sink(user, buf, n)) { ok = 0; break; }
                got += n;
                if (expect) InterlockedExchange(&g_percent, (LONG)(got * 100 / expect)), PostMessageW(g_win, WM_STATUS, 0, 0);
            }
            if (expect && got != expect) ok = 0;
        }
    }
    if (r) WinHttpCloseHandle(r);
    if (c) WinHttpCloseHandle(c);
    WinHttpCloseHandle(s);
    return ok;
}

typedef struct { char *p; size_t n, cap, limit; } Mem;
static int mem_sink(void *u, const void *d, DWORD n) {
    Mem *m = (Mem *)u;
    size_t limit = m->limit ? m->limit : 4 * 1024 * 1024;
    if (WaitForSingleObject(g_cancel_event, 0) == WAIT_OBJECT_0 || m->n > limit || n > limit - m->n) return 0;
    if (m->n + n + 1 > m->cap) {
        size_t capacity = m->n + n + 1;
        char *next = (char *)realloc(m->p, capacity);
        if (!next) return 0;
        m->cap = capacity;
        m->p = next;
    }
    memcpy(m->p + m->n, d, n);
    m->n += n;
    m->p[m->n] = 0;
    return 1;
}

typedef struct { HANDLE f; BCRYPT_HASH_HANDLE h; } FileSink;
static int file_sink(void *u, const void *d, DWORD n) {
    FileSink *s = (FileSink *)u;
    if (WaitForSingleObject(g_cancel_event, 0) == WAIT_OBJECT_0) return 0;
    DWORD w = 0;
    if (!WriteFile(s->f, d, n, &w, NULL) || w != n) return 0;
    return BCryptHashData(s->h, (PUCHAR)d, n, 0) == 0;
}

static void hex(const unsigned char *d, int n, char *out) {
    static const char digits[] = "0123456789abcdef";
    for (int i = 0; i < n; i++) { out[2 * i] = digits[d[i] >> 4]; out[2 * i + 1] = digits[d[i] & 15]; }
    out[2 * n] = 0;
}

/* the SHA-256 of a file already on disk (a part downloaded before) */
static int file_sha256(const wchar_t *path, char out[65]) {
    BCRYPT_ALG_HANDLE a;
    BCRYPT_HASH_HANDLE h;
    if (BCryptOpenAlgorithmProvider(&a, BCRYPT_SHA256_ALGORITHM, NULL, 0)) return 0;
    if (BCryptCreateHash(a, &h, NULL, 0, NULL, 0, 0)) { BCryptCloseAlgorithmProvider(a, 0); return 0; }
    HANDLE f = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, NULL);
    int ok = f != INVALID_HANDLE_VALUE;
    static unsigned char buf[1 << 20];
    DWORD n;
    while (ok) {
        if (!ReadFile(f, buf, sizeof buf, &n, NULL)) { ok = 0; break; }
        if (!n) break;
        if (BCryptHashData(h, buf, n, 0)) ok = 0;
    }
    if (f != INVALID_HANDLE_VALUE) CloseHandle(f);
    unsigned char d[32];
    if (BCryptFinishHash(h, d, 32, 0)) ok = 0;
    BCryptDestroyHash(h);
    BCryptCloseAlgorithmProvider(a, 0);
    if (ok) hex(d, 32, out);
    return ok;
}

static int run_wait(wchar_t *cmd, const wchar_t *dir) {
    STARTUPINFOW si = {sizeof si};
    PROCESS_INFORMATION pi;
    si.dwFlags = STARTF_USESHOWWINDOW, si.wShowWindow = SW_HIDE;
    if (!CreateProcessW(NULL, cmd, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, dir, &si, &pi)) return -1;
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread), CloseHandle(pi.hProcess);
    return (int)code;
}

static void mkdirs(const wchar_t *path) {
    wchar_t p[MAX_PATH];
    size_t length = wcslen(path);
    if (length >= MAX_PATH) return;
    memcpy(p, path, (length + 1) * sizeof(wchar_t));
    for (wchar_t *q = p + 3; *q; q++)
        if (*q == L'\\') *q = 0, CreateDirectoryW(p, NULL), *q = L'\\';
    CreateDirectoryW(p, NULL);
}

/* a folder and everything in it (a version folder, a half-unpacked one) */
static void rmtree(const wchar_t *dir) {
    wchar_t pat[MAX_PATH], p[MAX_PATH];
    WIN32_FIND_DATAW fd;
    swprintf(pat, MAX_PATH, L"%ls\\*", dir);
    HANDLE h = FindFirstFileW(pat, &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (!wcscmp(fd.cFileName, L".") || !wcscmp(fd.cFileName, L"..")) continue;
            swprintf(p, MAX_PATH, L"%ls\\%ls", dir, fd.cFileName);
            if ((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && !(fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) rmtree(p);
            else if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) RemoveDirectoryW(p);   /* (a link: never what it points to) */
            else SetFileAttributesW(p, FILE_ATTRIBUTE_NORMAL), DeleteFileW(p);
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    RemoveDirectoryW(dir);
}

static void read_small(const wchar_t *path, char *out, int n) {
    out[0] = 0;
    HANDLE f = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (f == INVALID_HANDLE_VALUE) return;
    DWORD got = 0;
    ReadFile(f, out, (DWORD)n - 1, &got, NULL);
    CloseHandle(f);
    out[got] = 0;
    for (char *q = out; *q; q++)
        if (*q == '\r' || *q == '\n') { *q = 0; break; }
}

static void write_small(const wchar_t *path, const char *text) {
    HANDLE f = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    if (f == INVALID_HANDLE_VALUE) return;
    DWORD n;
    WriteFile(f, text, (DWORD)strlen(text), &n, NULL);
    CloseHandle(f);
}

/* Keep the released current-only policy for recognized owned versions. Unknown
   folders stay untouched; rename-denied busy versions can be retried later. */
static void prune(const wchar_t *root, const wchar_t *versions, const wchar_t *downloads, const char *tag, const Part *parts, int nparts) {
    wchar_t cur[MAX_PATH], prev[MAX_PATH], pat[MAX_PATH], p[MAX_PATH];
    char was[128], before[128];
    swprintf(cur, MAX_PATH, L"%ls\\current.txt", versions);
    swprintf(prev, MAX_PATH, L"%ls\\previous.txt", versions);
    read_small(cur, was, sizeof was);
    if (strcmp(was, tag)) {
        if (was[0]) write_small(prev, was);
        write_small(cur, tag);
    }
    read_small(prev, before, sizeof before);
    WIN32_FIND_DATAW fd;
    swprintf(pat, MAX_PATH, L"%ls\\*", versions);
    HANDLE h = FindFirstFileW(pat, &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || !wcscmp(fd.cFileName, L".") || !wcscmp(fd.cFileName, L"..")) continue;
            if (!setup_owned_version(root, fd.cFileName)) continue;
            char name[260];
            WideCharToMultiByte(CP_UTF8, 0, fd.cFileName, -1, name, sizeof name, NULL, NULL);
            /* (issue 611: only the version installed stays; the one before was 1.3 GB nobody rolled back to. A folder still in use
               (the builder that started this, closing; a build it left) cannot be renamed: it stays, and goes at a later run) */
            (void)before;
            if (!strcmp(name, tag)) continue;
            swprintf(p, MAX_PATH, L"%ls\\%ls", versions, fd.cFileName);
            wchar_t gone[MAX_PATH];
            swprintf(gone, MAX_PATH, L"%ls.gone", p);
            if (wcslen(fd.cFileName) > 5 && !wcscmp(fd.cFileName + wcslen(fd.cFileName) - 5, L".gone")) rmtree(p);
            else if (MoveFileW(p, gone)) rmtree(gone);
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    wchar_t safe_downloads[MAX_PATH];
    if (setup_safe_path(downloads, safe_downloads, 0) != SETUP_PLAN_OK ||
        !setup_marker(downloads, L".hookshot-download-cache", "HookshotDownloadCache 1\n")) return;
    swprintf(pat, MAX_PATH, L"%ls\\*.zip", downloads);
    h = FindFirstFileW(pat, &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            char name[260];
            WideCharToMultiByte(CP_UTF8, 0, fd.cFileName, -1, name, sizeof name, NULL, NULL);
            if (strlen(name) != 68 || strcmp(name + 64, ".zip")) continue;
            name[64] = 0;
            int owned = setup_sha(name);
            name[64] = '.';
            if (!owned || (fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) continue;
            int keep = 0;
            for (int i = 0; i < nparts; i++)
                if (!strncmp(name, parts[i].sha, 64)) keep = 1;
            if (!keep) swprintf(p, MAX_PATH, L"%ls\\%ls", downloads, fd.cFileName), DeleteFileW(p);
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
}

static void w(const char *s8, wchar_t *out, int n) { MultiByteToWideChar(CP_UTF8, 0, s8, -1, out, n); }

/* (HOOKSHOT_SETUP_LIST=<url>: another list, for testing a release before it is the latest) */
/* ---- The GitHub API's link for a release file (after PR 546 by nicholasgower, issue 544). GitHub answered the release
   page's cached link (releases/latest/download/...) with 504 for hours on 2026-11-08 while its API still answered: the API
   gives the file's own asset link, which redirects to a fresh download. Used when the release link fails. ---- */
static int json_space(char c) { return c == 32 || c == 9 || c == 10 || c == 13; }

static int json_string_after(const char *p, const char *end, char *out, int n) {
    while (p < end && json_space(*p)) p++;
    if (p >= end || *p != ':') return 0;
    for (p++; p < end && json_space(*p); p++) {}
    if (p >= end || *p != '"') return 0;
    int i = 0;
    for (p++; p < end && *p != '"'; p++) {
        if (*p == '\\' && p + 1 < end) p++;
        if (i + 1 < n) out[i++] = *p;
    }
    if (p >= end) return 0;
    out[i] = 0;
    return 1;
}

/* The API link of the asset called name in a release's JSON (in an asset object its own "url" comes before its "name") */
static int asset_from_json(const char *json, size_t len, const char *name, char *out, int n) {
    const char *end = json + len, *at = NULL;
    char v[600];
    for (const char *p = json; (p = strstr(p, "\"name\"")) && p < end; p += 6)
        if (json_string_after(p + 6, end, v, sizeof v) && !strcmp(v, name)) { at = p; break; }
    if (!at) return 0;
    const char *url = NULL;
    for (const char *p = json; p + 5 <= at; p++)
        if (!strncmp(p, "\"url\"", 5)) url = p;
    return url && json_string_after(url + 5, end, out, n) && !strncmp(out, "https://api.github.com/", 23);
}

/* repo "owner/name", tag NULL for the latest release */
static int api_asset_url(const char *repo, const char *tag, const char *name, char *out, int n) {
    char api[1024];
    if (tag) snprintf(api, sizeof api, "https://api.github.com/repos/%s/releases/tags/%s", repo, tag);
    else snprintf(api, sizeof api, "https://api.github.com/repos/%s/releases/latest", repo);
    Mem m = {0};
    const int found = http_get(api, mem_sink, &m, 0, L"application/vnd.github+json") && m.p && asset_from_json(m.p, m.n, name, out, n);
    free(m.p);
    return found;
}

/* https://github.com/<owner>/<repo>/releases/download/<tag>/<name> or .../releases/latest/download/<name> -> its API link */
static int api_link_for(const char *url, char *out, int n) {
    static const char pre[] = "https://github.com/";
    char repo[256], tag[256], name[260];
    if (strncmp(url, pre, sizeof pre - 1)) return 0;
    const char *p = url + sizeof pre - 1, *slash = strchr(p, '/');
    if (!slash || !(slash = strchr(slash + 1, '/'))) return 0;
    if ((size_t)(slash - p) >= sizeof repo) return 0;
    memcpy(repo, p, slash - p), repo[slash - p] = 0;
    const char *t;
    int latest = 0;
    if (!strncmp(slash, "/releases/latest/download/", 26)) latest = 1, t = slash + 26;
    else if (!strncmp(slash, "/releases/download/", 19)) {
        t = slash + 19;
        const char *e = strchr(t, '/');
        if (!e || (size_t)(e - t) >= sizeof tag) return 0;
        memcpy(tag, t, e - t), tag[e - t] = 0;
        t = e + 1;
    } else return 0;
    if (!*t || strlen(t) >= sizeof name || strpbrk(t, "/?#")) return 0;
    strcpy(name, t);
    return api_asset_url(repo, latest ? NULL : tag, name, out, n);
}

/* an answer worth trying again: none (no connection), a server error, a download cut off half way (200), a timeout, or the API's
   rate limit (403/429) */
static int retryable(DWORD status) {
    return !status || status >= 500 || status == 200 || status == 408 || status == 429 || status == 403;
}

static const char *manifest_url(void) {
    static char u[1024];
    const char *e = getenv("HOOKSHOT_SETUP_LIST");
    snprintf(u, sizeof u, "%s", e && *e ? e : "https://github.com/NotRexed/Hookshot/releases/latest/download/hookshot-setup.txt");
    return u;
}


static const int waits[] = {3, 6, 12, 20, 30, 45, 60};
enum { TRIES = 8 };
typedef struct {
    Part *parts;
    int nparts;
    const char *tag;
    const char *revision;
    wchar_t run[MAX_PATH], root[MAX_PATH], cache[MAX_PATH], versions[MAX_PATH], leaf[128];
    wchar_t error[1024];
} PrepareContext;

static int prepare_error(PrepareContext *context, const wchar_t *format, ...) {
    va_list args;
    va_start(args, format);
    vswprintf(context->error, sizeof context->error / sizeof context->error[0], format, args);
    va_end(args);
    return 0;
}

static int prepare_payload(const wchar_t *install, const wchar_t *downloads, void *opaque) {
    PrepareContext *context = (PrepareContext *)opaque;
    Part *parts = context->parts;
    int nparts = context->nparts;
    for (int i = 0; i < nparts; i++) {
            Part *p = &parts[i];
            wchar_t zip[MAX_PATH], wsha[65];
            w(p->sha, wsha, 65);
            swprintf(zip, MAX_PATH, L"%ls\\%ls.zip", downloads, wsha);
            char have[65] = "";
            /* A prior cache is read only. This attempt owns every file it extracts. */
            wchar_t cached[MAX_PATH];
            swprintf(cached, MAX_PATH, L"%ls\\%ls.zip", context->cache, wsha);
            DWORD attributes = GetFileAttributesW(cached);
            if (attributes != INVALID_FILE_ATTRIBUTES && !(attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) &&
                file_sha256(cached, have) && !strcmp(have, p->sha)) CopyFileW(cached, zip, TRUE);
            if (!(GetFileAttributesW(zip) != INVALID_FILE_ATTRIBUTES && file_sha256(zip, have) && !strcmp(have, p->sha))) {
                wchar_t part[MAX_PATH];
                swprintf(part, MAX_PATH, L"%ls.part", zip);
                int got = 0;
                for (int attempt = 0; attempt < TRIES && !got; attempt++) {   /* (a server error or a dropped connection: again) */
                    if (WaitForSingleObject(g_cancel_event, 0) == WAIT_OBJECT_0) return -1;
                    if (attempt) {
                        status(L"Part %d of %d: GitHub didn't answer (%lu). Trying again in %d s...", i + 1, nparts, g_http_status,
                               waits[attempt - 1]);
                        if (WaitForSingleObject(g_cancel_event, waits[attempt - 1] * 1000) == WAIT_OBJECT_0) return -1;
                    }
                    status(L"Downloading part %d of %d (%llu MB)...", i + 1, nparts, p->size >> 20);
                    FileSink fs;
                    BCRYPT_ALG_HANDLE a;
                    fs.f = CreateFileW(part, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_FLAG_SEQUENTIAL_SCAN, NULL);
                    if (fs.f == INVALID_HANDLE_VALUE) return prepare_error(context, L"Could not write to %ls.", downloads);
                    if (BCryptOpenAlgorithmProvider(&a, BCRYPT_SHA256_ALGORITHM, NULL, 0)) {
                        CloseHandle(fs.f);
                        return prepare_error(context, L"Could not start download verification.");
                    }
                    if (BCryptCreateHash(a, &fs.h, NULL, 0, NULL, 0, 0)) {
                        CloseHandle(fs.f); BCryptCloseAlgorithmProvider(a, 0);
                        return prepare_error(context, L"Could not start download verification.");
                    }
                    g_http_status = 0;
                    /* (after a failed try the API's link to the same file, every other try) */
                    char api[1024];
                    if (attempt % 2 == 1 && api_link_for(p->url, api, sizeof api))
                        got = http_get(api, file_sink, &fs, p->size, L"application/octet-stream");
                    else
                        got = http_get(p->url, file_sink, &fs, p->size, NULL);
                    CloseHandle(fs.f);
                    unsigned char d[32];
                    int hashed = BCryptFinishHash(fs.h, d, 32, 0) == 0;
                    BCryptDestroyHash(fs.h);
                    BCryptCloseAlgorithmProvider(a, 0);
                    if (!hashed) return prepare_error(context, L"Could not finish download verification.");
                    InterlockedExchange(&g_percent, -1);
                    hex(d, 32, have);
                    /* (a download cut off half way answered 200 and stopped the tries at once: only a definite no stops them) */
                    if (!got && !retryable(g_http_status)) break;
                }
                if (!got) { DeleteFileW(part); return prepare_error(context, retryable(g_http_status)
                    ? L"Downloading part %d of %d failed: GitHub's download servers are failing right now (tried 8 times over 3 "
                      L"minutes). Previously cached parts and the previous installation are unchanged. Try again later."
                    : L"Downloading part %d of %d failed. Check your connection and free space, then try again.", i + 1, nparts); }
                if (strcmp(have, p->sha)) { DeleteFileW(part); return prepare_error(context, L"Part %d of %d did not download correctly (its checksum differs). Try again.", i + 1, nparts); }
                DeleteFileW(zip);
                if (!MoveFileW(part, zip)) return prepare_error(context, L"Could not prepare the verified download.");
            }
            if (WaitForSingleObject(g_cancel_event, 0) == WAIT_OBJECT_0) return -1;
            status(L"Unpacking part %d of %d...", i + 1, nparts);
            wchar_t dest[MAX_PATH], wdest[260], tarcmd[2 * MAX_PATH + 64], sys[MAX_PATH];
            w(p->dest, wdest, 260);
            if (!strcmp(p->dest, ".")) memcpy(dest, install, (wcslen(install) + 1) * sizeof(wchar_t));
            else swprintf(dest, MAX_PATH, L"%ls\\%ls", install, wdest);
            for (wchar_t *q = dest; *q; q++) if (*q == L'/') *q = L'\\';
            mkdirs(dest);
            GetSystemDirectoryW(sys, MAX_PATH);
            swprintf(tarcmd, 2 * MAX_PATH + 64, L"\"%ls\\tar.exe\" -xf \"%ls\" -C \"%ls\"", sys, zip, dest);
            if (run_wait(tarcmd, dest) != 0) return prepare_error(context, L"Unpacking part %d of %d failed (Windows' tar.exe). Is there enough free space in the selected folder?", i + 1, nparts);
            if (strcmp(p->from, "-") && strcmp(p->to, "-")) {   /* (llvm-mingw-<version>-ucrt-x86_64 -> llvm-mingw) */
                wchar_t from[MAX_PATH], to[MAX_PATH], wf[260], wt[260];
                w(p->from, wf, 260), w(p->to, wt, 260);
                swprintf(from, MAX_PATH, L"%ls\\%ls", dest, wf);
                swprintf(to, MAX_PATH, L"%ls\\%ls", dest, wt);
                if (GetFileAttributesW(to) == INVALID_FILE_ATTRIBUTES && !MoveFileW(from, to))
                    return prepare_error(context, L"Could not set up part %d of %d (renaming %ls).", i + 1, nparts, wf);
            }
        }

    wchar_t exe[MAX_PATH], marker[MAX_PATH];
    if (!setup_join(exe, install, context->run)) return prepare_error(context, L"The installed builder path is too long.");
    for (wchar_t *p = exe; *p; p++) if (*p == L'/') *p = L'\\';
    DWORD attributes = GetFileAttributesW(exe);
    if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)))
        return prepare_error(context, L"The builder is missing after unpacking. The previous installation is unchanged.");
    wchar_t gui[MAX_PATH];
    if (!setup_join(gui, install, L"HookshotBuilder") || !setup_gui_validate(gui, context->tag, context->revision, file_sha256, 0))
        return prepare_error(context, L"The prepared GUI does not match the release identity and hashes. The previous installation is unchanged.");
    char gui_sha[65];
    if (!setup_join(marker, gui, L"builder-identity.json") || !file_sha256(marker, gui_sha) ||
        !setup_join(marker, install, L".hookshot-published-gui-sha") || !setup_new_file(marker, gui_sha))
        return prepare_error(context, L"Could not record the verified GUI identity.");
    if (!setup_join(marker, install, L"installed.txt") || !setup_new_file(marker, context->tag))
        return prepare_error(context, L"Could not mark the prepared builder. The previous installation is unchanged.");
    return WaitForSingleObject(g_cancel_event, 0) == WAIT_OBJECT_0 ? -1 : 1;
}

static void cache_and_prune(const wchar_t *pending, void *opaque) {
    PrepareContext *context = (PrepareContext *)opaque;
    char leaf[128];
    WideCharToMultiByte(CP_UTF8, 0, context->leaf, -1, leaf, sizeof leaf, NULL, NULL);
    wchar_t safe[MAX_PATH];
    if (setup_safe_path(context->cache, safe, 1) != SETUP_PLAN_OK) {
        prune(context->root, context->versions, context->cache, leaf, context->parts, context->nparts);
        return;
    }
    if (CreateDirectoryW(context->cache, NULL)) {
        wchar_t marker[MAX_PATH];
        if (setup_join(marker, context->cache, L".hookshot-download-cache")) setup_new_file(marker, "HookshotDownloadCache 1\n");
    }
    if (setup_marker(context->cache, L".hookshot-download-cache", "HookshotDownloadCache 1\n")) {
        for (int i = 0; i < context->nparts; i++) {
            wchar_t sha[65], name[72], from[MAX_PATH], to[MAX_PATH];
            w(context->parts[i].sha, sha, 65);
            swprintf(name, 72, L"%ls.zip", sha);
            if (setup_join(from, pending, name) && setup_join(to, context->cache, name))
                MoveFileExW(from, to, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
        }
    }
    prune(context->root, context->versions, context->cache, leaf, context->parts, context->nparts);
}

static int consent_available(const wchar_t *root, uint64_t *bytes, void *unused) {
    (void)unused;
    return setup_available(root, bytes);
}
static int consent_confirm(const SetupInstallPlan *plan, uint64_t bytes, int can_change, void *unused) {
    (void)unused;
    if (WaitForSingleObject(g_cancel_event, 0) == WAIT_OBJECT_0) return SETUP_CHOOSE_CANCEL;
    return setup_confirm_dialog(g_win, plan, bytes, can_change, L"Hookshot Builder setup",
                                L"This location is remembered only after a successful installation.");
}
static SetupPlanResult consent_choose(const wchar_t *current, wchar_t *root, void *unused) {
    (void)unused;
    return setup_folder_dialog(g_win, current, root, L"Hookshot Builder setup");
}

static int memory_sha256(const Mem *memory, char digest[65]) {
    BCRYPT_ALG_HANDLE algorithm;
    BCRYPT_HASH_HANDLE hash;
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, NULL, 0)) return 0;
    int ok = BCryptCreateHash(algorithm, &hash, NULL, 0, NULL, 0, 0) == 0;
    if (ok) {
        unsigned char bytes[32];
        ok = BCryptHashData(hash, (PUCHAR)memory->p, (ULONG)memory->n, 0) == 0 && BCryptFinishHash(hash, bytes, sizeof bytes, 0) == 0;
        if (ok) hex(bytes, sizeof bytes, digest);
        BCryptDestroyHash(hash);
    }
    BCryptCloseAlgorithmProvider(algorithm, 0);
    return ok;
}

static int start_builder(const wchar_t *exe, const wchar_t *arguments) {
    wchar_t command[4096], directory[MAX_PATH];
    if (wcslen(exe) >= MAX_PATH || wcslen(exe) + wcslen(arguments) + 5 >= 4096) return 0;
    memcpy(directory, exe, (wcslen(exe) + 1) * sizeof(wchar_t));
    wchar_t *slash = wcsrchr(directory, L'\\');
    if (!slash) return 0;
    *slash = 0;
    swprintf(command, 4096, L"\"%ls\" %ls", exe, arguments);
    STARTUPINFOW startup = {0};
    startup.cb = sizeof startup;
    PROCESS_INFORMATION process;
    if (!CreateProcessW(NULL, command, NULL, NULL, FALSE, 0, NULL, directory, &startup, &process)) return 0;
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 1;
}

static int current_builder(const wchar_t *root, const char *tag, const char *revision, wchar_t exe[MAX_PATH]) {
    wchar_t path[MAX_PATH], leaf[128], gui[MAX_PATH];
    char current[128];
    if (!setup_join(path, root, L"versions\\current.txt")) return 0;
    read_small(path, current, sizeof current);
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, current, -1, leaf, 128) || !setup_owned_version(root, leaf)) return 0;
    int n = swprintf(gui, MAX_PATH, L"%ls\\versions\\%ls\\HookshotBuilder", root, leaf);
    return n > 0 && n < MAX_PATH && setup_gui_validate(gui, tag, revision, file_sha256, 1) &&
           setup_join(exe, gui, L"HookshotBuilder.exe");
}

static SetupInstallResult install_selected(SetupPlanResult selected, const SetupInstallPlan *plan, const wchar_t *leaf,
                                           const wchar_t *nonce, PrepareContext *context, SetupInstallOutcome *outcome) {
    memset(outcome, 0, sizeof *outcome);
    if (selected == SETUP_PLAN_CANCELLED) return SETUP_INSTALL_CANCELLED;
    if (selected != SETUP_PLAN_OK) return SETUP_INSTALL_INVALID;
    memcpy(context->root, plan->root, (wcslen(plan->root) + 1) * sizeof(wchar_t));
    memcpy(context->leaf, leaf, (wcslen(leaf) + 1) * sizeof(wchar_t));
    if (!setup_join(context->cache, plan->root, L"downloads") || !setup_join(context->versions, plan->root, L"versions"))
        return SETUP_INSTALL_INVALID;
    SetupInstallHooks hooks = {prepare_payload, NULL, context, cache_and_prune};
    return setup_install(plan, SETUP_CHOOSE_INSTALL, leaf, nonce, &hooks, outcome);
}

static DWORD WINAPI work(LPVOID unused) {
    (void)unused;
    int argc, repair;
    wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    int request_ok = argv && setup_request(argc, argv, &repair, g_cmdline, sizeof g_cmdline / sizeof g_cmdline[0]);
    if (argv) LocalFree(argv);
    if (!request_ok) fail(L"The setup handoff has invalid or repeated arguments. No files were changed.");
    wchar_t base[MAX_PATH];
    if (SHGetFolderPathW(NULL, CSIDL_LOCAL_APPDATA, NULL, 0, base) != S_OK) fail(L"No local application data folder.");
    if (wcslen(base) + wcslen(L"\\HookshotBuilder") >= MAX_PATH) fail(L"The default installation path is too long.");
    wcscat(base, L"\\HookshotBuilder");

    status(L"Looking for the latest Hookshot Builder...");
    Mem man = {0};
    man.limit = 262144;
    /* (tried again on a server error or no answer: GitHub's release downloads answered 504 for an hour, 2026-11-08, and every
       start failed at once) */
    /* (low-vram, issue 558: 5 tries in 41 s were not enough while GitHub's site and API both failed on and off for hours; 8 tries
       over ~3 minutes now, alternating the release link and the API's) */
    int ok = 0;
    for (int attempt = 0; attempt < TRIES && !ok; attempt++) {
        if (attempt) {
            status(L"GitHub didn't answer (%lu). Trying again in %d s...", g_http_status, waits[attempt - 1]);
            if (WaitForSingleObject(g_cancel_event, waits[attempt - 1] * 1000) == WAIT_OBJECT_0) { free(man.p); PostMessageW(g_win, WM_DONE, 0, 0); return 0; }
            status(L"Looking for the latest Hookshot Builder...");
        }
        free(man.p);
        man = (Mem){0};
        man.limit = 262144;
        g_http_status = 0;
        ok = http_get(manifest_url(), mem_sink, &man, 0, NULL) && man.p;
        const DWORD first = g_http_status;
        char api[1024];
        if (!ok && (!first || first >= 500) && api_link_for(manifest_url(), api, sizeof api)) {   /* (the API's link instead) */
            free(man.p);
            man = (Mem){0};
            man.limit = 262144;
            ok = http_get(api, mem_sink, &man, 0, L"application/octet-stream") && man.p;
            if (!ok) g_http_status = first;
        }
        if (!ok && !retryable(g_http_status)) break;   /* (404 and the like: trying again changes nothing) */
    }
    if (!ok)
        fail(g_http_status == 404
                 ? L"The latest Hookshot release has no setup files yet. Download HookshotBuilder.zip from the releases page for now: "
                   L"https://github.com/NotRexed/Hookshot/releases/latest"
             : g_http_status ? L"GitHub's download servers are failing right now (an error 500 or above, tried 8 times over 3 minutes). "
                               L"This is on GitHub's side: try again in a few minutes, or later today."
                             : L"Could not reach GitHub to get the Hookshot Builder. Check your internet connection and try again.");

    char manifest_digest[65];
    if (!memory_sha256(&man, manifest_digest)) fail(L"Could not verify the setup metadata.");
    char tag[128] = "", run[260] = "", revision[41] = "";
    Part parts[MAX_PARTS];
    int nparts = 0;
    for (char *line = strtok(man.p, "\r\n"); line; line = strtok(NULL, "\r\n")) {
        if (!strncmp(line, "tag ", 4)) sscanf(line + 4, "%127s", tag);
        else if (!strncmp(line, "run ", 4)) sscanf(line + 4, "%259s", run);
        else if (!strncmp(line, "identity ", 9)) {
            int format;
            char extra;
            if (revision[0] || sscanf(line + 9, "%d %40s %c", &format, revision, &extra) != 2 || format != 1)
                fail(L"The setup list has invalid or repeated source identity.");
        }
        else if (!strncmp(line, "part ", 5) && nparts < MAX_PARTS) {
            Part *p = &parts[nparts];
            if (sscanf(line + 5, "%64s %llu %259s %259s %259s %1023s", p->sha, &p->size, p->dest, p->from, p->to, p->url) == 6) nparts++;
        }
    }
    if (!tag[0] || !run[0] || !nparts || strlen(revision) != 40) fail(L"The release's setup list has no supported source identity.");
    for (unsigned i = 0; i < 40; i++)
        if (!((revision[i] >= '0' && revision[i] <= '9') || (revision[i] >= 'a' && revision[i] <= 'f')))
            fail(L"The setup list has invalid source identity.");


    if (strcmp(run, "HookshotBuilder/HookshotBuilder.exe")) fail(L"The release has an unsupported builder path.");
    wchar_t wrevision[41], leaf[128], nonce[33], module[MAX_PATH], saved[MAX_PATH], source_root[MAX_PATH];
    unsigned char random[16];
    char random_hex[33];
    w(revision, wrevision, 41);
    if (BCryptGenRandom(NULL, random, sizeof random, BCRYPT_USE_SYSTEM_PREFERRED_RNG))
        fail(L"Could not prepare a unique installation path.");
    hex(random, sizeof random, random_hex);
    w(random_hex, nonce, 33);
    swprintf(leaf, 128, L"v-%.12ls-%ls", wrevision, nonce);
    char manifest_sha[65];
    /* strtok above modified the downloaded bytes: the digest was captured before parsing. */
    memcpy(manifest_sha, manifest_digest, sizeof manifest_sha);
    char space_url[1100], *name;
    snprintf(space_url, sizeof space_url, "%s", manifest_url());
    name = strrchr(space_url, '/');
    if (!name || strlen(space_url) + 32 >= sizeof space_url) fail(L"The setup metadata URL is unsupported.");
    strcpy(name + 1, "hookshot-setup-space.txt");
    Mem hints = {0};
    hints.limit = 262144;
    int hints_ok = http_get(space_url, mem_sink, &hints, 0, NULL) && hints.p;
    char api[1024];
    if (!hints_ok && api_link_for(space_url, api, sizeof api)) {
        free(hints.p);
        hints = (Mem){0}; hints.limit = 262144;
        hints_ok = http_get(api, mem_sink, &hints, 0, L"application/octet-stream") && hints.p;
    }
    SetupSpace space;
    SetupPlanResult parsed = hints_ok ? setup_space_parse(hints.p, hints.n, manifest_sha, &space) : SETUP_PLAN_MISSING_SPACE;
    free(hints.p);
    free(man.p);
    if (parsed != SETUP_PLAN_OK) fail(L"This release has no valid size information bound to its setup list. Nothing was installed. Try a newer release.");
    SetupPlanPart plan_parts[MAX_PARTS];
    for (int i = 0; i < nparts; i++) {
        Part *part = &parts[i];
        wchar_t from[260];
        if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, part->from, -1, from, 260)) fail(L"The release has an invalid part path.");
        if ((!strcmp(part->dest, ".") && (strcmp(part->from, "-") || strcmp(part->to, "-"))) ||
            (strcmp(part->dest, ".") && (strcmp(part->dest, "HookshotBuilder/tools") || strcmp(part->to, "llvm-mingw") ||
             strncmp(part->from, "llvm-mingw-", 11) || !setup_leaf(from))) || strncmp(part->url, "https://", 8)) fail(L"The release has an unsupported part destination.");
        plan_parts[i] = (SetupPlanPart){part->sha, part->size};
    }
    DWORD module_length = GetModuleFileNameW(NULL, module, MAX_PATH);
    if (!module_length || module_length >= MAX_PATH) fail(L"The setup executable path is too long.");
    int fixed_root = setup_source_root(module, source_root);
    if (!fixed_root && setup_under_owned_root(module)) fail(L"The source version has invalid ownership. Its installation was left unchanged.");
    if (fixed_root) memcpy(base, source_root, (wcslen(source_root) + 1) * sizeof(wchar_t));
    else if (!repair && setup_read_location(base, saved)) memcpy(base, saved, (wcslen(saved) + 1) * sizeof(wchar_t));
    wchar_t canonical[MAX_PATH];
    SetupRootKind kind;
    if ((!fixed_root && repair) || setup_root_validate(base, canonical, &kind) != SETUP_PLAN_OK) {
        if (fixed_root) fail(L"The source installation is not a supported owned folder. It was left unchanged.");
        MessageBoxW(g_win, L"An older or unrelated folder already uses the default location. Choose a different parent folder for a new dedicated HookshotBuilder installation. The old folder will be left unchanged.",
                    L"Choose an installation folder", MB_OK | MB_ICONINFORMATION);
        SetupPlanResult selected = setup_folder_dialog(g_win, base, base, L"Hookshot Builder setup");
        if (selected == SETUP_PLAN_CANCELLED) { PostMessageW(g_win, WM_DONE, 0, 0); return 0; }
        if (selected != SETUP_PLAN_OK) fail(L"Choose a new or empty HookshotBuilder folder, or a recognized installation. No files were changed.");
    }
    wchar_t existing_exe[MAX_PATH];
    if (!repair && current_builder(base, tag, revision, existing_exe)) {
        if (!start_builder(existing_exe, g_cmdline)) fail(L"Could not start the verified installed builder. Its files were left unchanged.");
        PostMessageW(g_win, WM_DONE, 0, 0);
        return 0;
    }
    SetupConsentHooks consent = {consent_available, consent_confirm, consent_choose, NULL};
    SetupInstallPlan plan;
    SetupPlanResult selected = setup_consent(base, fixed_root, leaf, manifest_sha, &space, plan_parts, (unsigned)nparts, &consent, &plan);
    if (selected == SETUP_PLAN_CANCELLED) { PostMessageW(g_win, WM_DONE, 0, 0); return 0; }
    if (selected != SETUP_PLAN_OK) fail(L"This folder cannot hold the prepared installation (invalid ownership, unsupported path length, or insufficient free space). No files were changed. Run setup again and choose another parent folder.");
    PrepareContext context = {0};
    context.parts = parts; context.nparts = nparts; context.tag = tag; context.revision = revision;
    w(run, context.run, MAX_PATH);
    SetupInstallOutcome outcome;
    SetupInstallResult installed = install_selected(selected, &plan, leaf, nonce, &context, &outcome);
    if (installed == SETUP_INSTALL_CANCELLED) { PostMessageW(g_win, WM_DONE, 0, 0); return 0; }
    if (installed != SETUP_INSTALL_OK) fail(context.error[0] ? L"%ls" : L"The installation could not be committed (a folder may be busy). The previous installation was left unchanged. Close other builders and try again.", context.error);
    wchar_t preferences[MAX_PATH];
    if (SHGetFolderPathW(NULL, CSIDL_LOCAL_APPDATA, NULL, 0, preferences) != S_OK ||
        wcslen(preferences) + wcslen(L"\\HookshotBuilder") >= MAX_PATH) fail(L"The builder is installed, but its location could not be remembered.");
    wcscat(preferences, L"\\HookshotBuilder");
    if (!setup_write_location(preferences, &plan, &outcome, nonce)) fail(L"The builder is installed at %ls, but its location could not be remembered. Open the builder there.", outcome.directory);
    wchar_t exe[MAX_PATH];
    if (!setup_join(exe, outcome.directory, L"HookshotBuilder\\HookshotBuilder.exe")) fail(L"The installed builder path is too long.");
    if (WaitForSingleObject(g_cancel_event, 0) == WAIT_OBJECT_0) { PostMessageW(g_win, WM_DONE, 0, 0); return 0; }
    status(L"Starting the Hookshot Builder...");
    if (!start_builder(exe, g_cmdline)) fail(L"Could not start %ls.", exe);
    PostMessageW(g_win, WM_DONE, 0, 0);
    return 0;
}

static LRESULT CALLBACK wndproc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_STATUS: {
        SetWindowTextW(g_text, g_status);
        const LONG p = g_percent;
        if (p < 0) SendMessageW(g_bar, PBM_SETMARQUEE, TRUE, 30);
        else SendMessageW(g_bar, PBM_SETMARQUEE, FALSE, 0), SendMessageW(g_bar, PBM_SETPOS, (WPARAM)p, 0);
        return 0;
    }
    case WM_CLOSE:
        SetEvent(g_cancel_event);
        SetWindowTextW(g_text, L"Cancelling setup...");
        EnableWindow(h, FALSE);
        return 0;
    case WM_DONE: DestroyWindow(h); return 0;
    case WM_DESTROY: PostQuitMessage(0); return 0;
    }
    return DefWindowProcW(h, msg, wp, lp);
}

/* (launch mode) "Hookshot Launcher.exe" in a game folder (nx2/make_game.py puts this file there under that name): the installed
   builder opened as the game's launcher (--launch <that folder>: Play, and Update when a newer release is out), at once, with no
   window, download or network of its own. With no builder installed yet, the setup runs as usual and then opens the launcher. */
static int launch_installed(const wchar_t *args) {
    wchar_t base[MAX_PATH], cur[MAX_PATH], exe[MAX_PATH], dir[MAX_PATH];
    if (SHGetFolderPathW(NULL, CSIDL_LOCAL_APPDATA, NULL, 0, base) != S_OK) return 0;
    if (wcslen(base) + wcslen(L"\\HookshotBuilder") >= MAX_PATH) return 0;
    wcscat(base, L"\\HookshotBuilder");
    wchar_t saved[MAX_PATH];
    int custom = setup_read_location(base, saved);
    if (custom) memcpy(base, saved, (wcslen(saved) + 1) * sizeof(wchar_t));
    char tag[128] = "";
    swprintf(cur, MAX_PATH, L"%ls\\versions\\current.txt", base);
    read_small(cur, tag, sizeof tag);
    if (!tag[0]) return 0;
    wchar_t wtag[128];
    MultiByteToWideChar(CP_UTF8, 0, tag, -1, wtag, 128);
    if (!setup_leaf(wtag) || (custom && !setup_owned_version(base, wtag))) return 0;
    swprintf(dir, MAX_PATH, L"%ls\\versions\\%ls\\HookshotBuilder", base, wtag);
    swprintf(exe, MAX_PATH, L"%ls\\HookshotBuilder.exe", dir);
    if (GetFileAttributesW(exe) == INVALID_FILE_ATTRIBUTES) return 0;
    if (custom) {
        char release_tag[128], revision[64];
        if (!setup_join(cur, dir, L"tools\\version.txt")) return 0;
        read_small(cur, release_tag, sizeof release_tag);
        if (!setup_join(cur, dir, L"tools\\revision.txt")) return 0;
        read_small(cur, revision, sizeof revision);
        if (!setup_gui_validate(dir, release_tag, revision, file_sha256, 1)) return 0;
    }
    return start_builder(exe, args);
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, LPWSTR cmdline, int show) {
    (void)prev;
    wcsncpy(g_cmdline, cmdline ? cmdline : L"", 2047);
    {
        wchar_t self[MAX_PATH];
        GetModuleFileNameW(NULL, self, MAX_PATH);
        const wchar_t *name = wcsrchr(self, L'\\');
        name = name ? name + 1 : self;
        if (!_wcsicmp(name, L"hookshot.exe") && !wcsstr(g_cmdline, L"--")) {   /* (the game folder's launcher) */
            wchar_t *slash = wcsrchr(self, L'\\');
            if (slash) *slash = 0;
            swprintf(g_cmdline, 2047, L"--launch \"%ls\"", self);
            if (launch_installed(g_cmdline)) return 0;
            /* (no builder installed on this PC: the game itself, at once; nothing is downloaded to show a Play button) */
            wchar_t game[MAX_PATH], cmd[MAX_PATH + 4];
            swprintf(game, MAX_PATH, L"%ls\\bin\\hookshot.exe", self);
            swprintf(cmd, MAX_PATH + 4, L"\"%ls\"", game);
            STARTUPINFOW si = {sizeof si};
            PROCESS_INFORMATION pi;
            if (CreateProcessW(NULL, cmd, NULL, NULL, FALSE, 0, NULL, self, &si, &pi)) {
                CloseHandle(pi.hThread), CloseHandle(pi.hProcess);
                return 0;
            }
            MessageBoxW(NULL, L"The game (bin\\hookshot.exe) is missing from this folder. Build it again with the Hookshot Builder.", L"Hookshot",
                        MB_ICONERROR);
            return 1;
        }
        if (wcsstr(g_cmdline, L"--launch") && launch_installed(g_cmdline)) return 0;
    }
    INITCOMMONCONTROLSEX icc = {sizeof icc, ICC_PROGRESS_CLASS};
    InitCommonControlsEx(&icc);
    WNDCLASSW wc = {0};
    wc.lpfnWndProc = wndproc, wc.hInstance = inst, wc.lpszClassName = L"HookshotSetup";
    wc.hIcon = LoadIconW(inst, MAKEINTRESOURCEW(1));
    wc.hCursor = LoadCursorW(NULL, (LPCWSTR)IDC_ARROW);
    wc.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    RegisterClassW(&wc);
    const int ww = 460, wh = 140;
    g_win = CreateWindowW(L"HookshotSetup", L"Hookshot Builder setup", WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
                          (GetSystemMetrics(SM_CXSCREEN) - ww) / 2, (GetSystemMetrics(SM_CYSCREEN) - wh) / 2, ww, wh, NULL, NULL, inst, NULL);
    g_text = CreateWindowW(L"STATIC", L"Starting...", WS_CHILD | WS_VISIBLE, 16, 16, ww - 48, 22, g_win, NULL, inst, NULL);
    g_bar = CreateWindowW(PROGRESS_CLASSW, NULL, WS_CHILD | WS_VISIBLE | PBS_MARQUEE, 16, 48, ww - 48, 20, g_win, NULL, inst, NULL);
    HFONT f = CreateFontW(-15, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Segoe UI");
    SendMessageW(g_text, WM_SETFONT, (WPARAM)f, TRUE);
    SendMessageW(g_bar, PBM_SETMARQUEE, TRUE, 30);
    ShowWindow(g_win, show);
    g_cancel_event = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (!g_cancel_event) return 1;
    HANDLE worker = CreateThread(NULL, 0, work, NULL, 0, NULL);
    if (!worker) { CloseHandle(g_cancel_event); DestroyWindow(g_win); return 1; }
    CloseHandle(worker);
    MSG m;
    while (GetMessageW(&m, NULL, 0, 0) > 0) TranslateMessage(&m), DispatchMessageW(&m);
    CloseHandle(g_cancel_event);
    return 0;
}
