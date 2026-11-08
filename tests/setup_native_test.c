#include <stdarg.h>
#include <stdio.h>
#include "setup_gui.h"
#include "setup_location.h"
#include "setup_request.h"
#include <shellapi.h>
#include <bcrypt.h>
#include "native_types.h"
#include "native_data.h"

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); return 1; } } while (0)
static HANDLE g_cancel_event;
static DWORD g_http_status;
static volatile LONG g_percent;
static wchar_t authored[SETUP_PLAN_PATH];
static const wchar_t *mode;
static unsigned requests, extractions;

static void status(const wchar_t *format, ...) { (void)format; }
static int api_link_for(const char *url, char *out, int length) { (void)url; (void)out; (void)length; return 0; }
static int http_get(const char *url, Sink sink, void *user, unsigned long long expected, const wchar_t *accept) {
    (void)accept;
    if (strcmp(url, "https://fixture.invalid/authored.zip")) return 0;
    requests++;
    wchar_t path[SETUP_PLAN_PATH];
    if (!setup_join(path, authored, L"authored.zip")) return 0;
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    unsigned char bytes[4096];
    DWORD got;
    unsigned long long total = 0;
    int ok = 1;
    while (ReadFile(file, bytes, sizeof bytes, &got, NULL) && got) {
        if (!wcscmp(mode, L"bad-hash") && !total) bytes[0] ^= 1;
        total += got;
        if (!sink(user, bytes, got)) { ok = 0; break; }
    }
    CloseHandle(file);
    g_http_status = 200;
    return ok && total == expected;
}

/* Authored replacement for extraction only: copies the tiny ZIP's authored
   source files into the supplied attempt. No tar/tool/GUI payload is executed. */
static int run_wait(wchar_t *command, const wchar_t *destination) {
    (void)command;
    extractions++;
    wchar_t gui[SETUP_PLAN_PATH], tools[SETUP_PLAN_PATH], from[SETUP_PLAN_PATH], to[SETUP_PLAN_PATH];
    if (!setup_join(gui, destination, L"HookshotBuilder") || !CreateDirectoryW(gui, NULL) ||
        !setup_join(tools, gui, L"tools") || !CreateDirectoryW(tools, NULL)) return 1;
    const wchar_t *files[] = {L"HookshotBuilder.exe", L"HookshotBuilder.dll", L"builder-identity.json", L"tools\\version.txt", L"tools\\revision.txt"};
    for (unsigned i = 0; i < sizeof files / sizeof files[0]; i++) {
        wchar_t source[SETUP_PLAN_PATH];
        if (!setup_join(source, authored, L"payload-source") || !setup_join(from, source, files[i]) ||
            !setup_join(to, gui, files[i]) || !CopyFileW(from, to, TRUE)) return 1;
    }
    if (!wcscmp(mode, L"stale-prepared")) {
        if (!setup_join(from, authored, L"old-gui.txt") || !setup_join(to, gui, L"HookshotBuilder.exe") || !CopyFileW(from, to, FALSE)) return 1;
    }
    return 0;
}

/* Exact current production type/function bodies, extracted by the Python driver. */
#include "native_helpers.h"

int wmain(int argc, wchar_t **argv) {
    CHECK(argc == 3);
    mode = argv[2];
    CHECK(wcslen(argv[1]) < SETUP_PLAN_PATH);
    memcpy(authored, argv[1], (wcslen(argv[1]) + 1) * sizeof(wchar_t));
    if (!wcscmp(mode, L"argv")) {
        wchar_t *input[] = {L"setup.exe", L"--repair", L"--launch", L"C:\\authored --repair space \u96ea\\tail\\", L"quoted\"name", L"--tag", L"fixture"};
        wchar_t forward[2048], command[2100] = L"setup.exe ";
        int repair;
        CHECK(setup_request(7, input, &repair, forward, 2048) && repair);
        memcpy(command + 10, forward, (wcslen(forward) + 1) * sizeof(wchar_t));
        int count;
        wchar_t **parsed = CommandLineToArgvW(command, &count);
        CHECK(parsed && count == 6);
        for (int i = 1; i < count; i++) CHECK(!wcscmp(parsed[i], input[i + 1]));
        LocalFree(parsed);
        wchar_t *duplicate[] = {L"setup.exe", L"--repair", L"--repair"};
        CHECK(!setup_request(3, duplicate, &repair, forward, 2048));
        CHECK(!setup_request(7, input, &repair, forward, 2));
        puts("PASS: actual native argument parsing/quoting round-trips through Windows argv");
        return 0;
    }
    g_cancel_event = CreateEventW(NULL, TRUE, FALSE, NULL);
    CHECK(g_cancel_event);
    wchar_t root[SETUP_PLAN_PATH], leaf[] = L"v-bbbbbbbbbbbb-0123456789abcdef0123456789abcdef";
    wchar_t nonce[] = L"0123456789abcdef0123456789abcdef";
    CHECK(setup_join(root, authored, L"HookshotBuilder"));
    SetupSpace space = {0};
    memset(space.manifest, 'a', 64);
    space.count = 1;
    memcpy(space.parts[0].sha, PART_SHA, 65);
    space.parts[0].expanded = 2048; space.parts[0].files = 5; space.parts[0].entries = 5; space.parts[0].max_relative_units = 60;
    SetupPlanPart part = {PART_SHA, PART_SIZE};
    SetupInstallPlan plan;
    CHECK(setup_plan_create(root, leaf, space.manifest, &space, &part, 1, UINT64_MAX, &plan) == SETUP_PLAN_OK);
    Part native_part = {PART_SHA, PART_SIZE, ".", "-", "-", "https://fixture.invalid/authored.zip"};
    PrepareContext context = {0};
    context.parts = &native_part; context.nparts = 1; context.tag = "fixture"; context.revision = TEST_REVISION;
    w("HookshotBuilder/HookshotBuilder.exe", context.run, MAX_PATH);
    SetupInstallOutcome outcome;
    SetupPlanResult selected = !wcscmp(mode, L"cancel") ? SETUP_PLAN_CANCELLED : SETUP_PLAN_OK;
    HANDLE busy = INVALID_HANDLE_VALUE;
    wchar_t path[SETUP_PLAN_PATH];
    if (!wcscmp(mode, L"locked-prune")) {
        CHECK(setup_join(path, root, L"versions\\v1\\HookshotBuilder\\HookshotBuilder.exe"));
        busy = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
        CHECK(busy != INVALID_HANDLE_VALUE);
    }
    SetupInstallResult installed = install_selected(selected, &plan, leaf, nonce, &context, &outcome);
    if (busy != INVALID_HANDLE_VALUE) CloseHandle(busy);
    if (!wcscmp(mode, L"cancel")) {
        CHECK(installed == SETUP_INSTALL_CANCELLED && !requests && !extractions);
        CHECK(setup_join(path, authored, L"preferences\\HookshotBuilder"));
        CHECK(!setup_write_location(path, &plan, &outcome, nonce));
        CHECK(GetFileAttributesW(path) == INVALID_FILE_ATTRIBUTES);
    }
    else if (!wcscmp(mode, L"bad-hash") || !wcscmp(mode, L"stale-prepared")) {
        CHECK(installed == SETUP_INSTALL_PREPARE && !outcome.committed && context.error[0]);
        CHECK(requests == 1 && extractions == (!wcscmp(mode, L"bad-hash") ? 0u : 1u));
    } else {
        CHECK(installed == SETUP_INSTALL_OK && outcome.committed && requests == 1 && extractions == 1);
        CHECK(current_builder(root, "fixture", TEST_REVISION, path));
        if (!wcscmp(mode, L"locator")) {
            wchar_t preferences[SETUP_PLAN_PATH], read_root[SETUP_PLAN_PATH];
            CHECK(setup_join(preferences, authored, L"preferences\\HookshotBuilder"));
            CHECK(setup_write_location(preferences, &plan, &outcome, nonce));
            CHECK(setup_read_location(preferences, read_root) && setup_path_equal(root, read_root));
            CHECK(setup_join(path, preferences, L"install-location.txt"));
            HANDLE held = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
            CHECK(held != INVALID_HANDLE_VALUE);
            CHECK(!setup_write_location(preferences, &plan, &outcome, nonce));
            CloseHandle(held);
            CHECK(setup_read_location(preferences, read_root) && setup_path_equal(root, read_root));
        }
        if (!wcscmp(mode, L"stale-reuse")) {
            wchar_t from[SETUP_PLAN_PATH], manifest[SETUP_PLAN_PATH];
            CHECK(setup_join(from, authored, L"old-gui.txt") && CopyFileW(from, path, FALSE));
            CHECK(!current_builder(root, "fixture", TEST_REVISION, path));
            CHECK(setup_join(from, authored, L"forged-identity.json"));
            CHECK(setup_join(manifest, outcome.directory, L"HookshotBuilder\\builder-identity.json") && CopyFileW(from, manifest, FALSE));
            CHECK(!current_builder(root, "fixture", TEST_REVISION, path));
        }
    }
    CloseHandle(g_cancel_event);
    puts("PASS: actual native install coordinator, preparation, GUI hash/receipt and cleanup helpers");
    return 0;
}
