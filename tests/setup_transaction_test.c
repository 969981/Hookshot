#include <stdio.h>
#include "setup_transaction.h"
#include "setup_consent.h"
#include "setup_dialog.h"
#ifdef SETUP_DIALOG_PREVIEW
#include <shellapi.h>
#endif

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); return 1; } } while (0)
#ifndef ORIGINAL_INSTALL_PREFIX
typedef struct { const wchar_t *mode; int calls; } Fixture;

static int prepare(const wchar_t *payload, const wchar_t *downloads, void *opaque) {
    Fixture *fixture = (Fixture *)opaque;
    wchar_t path[SETUP_PLAN_PATH];
    fixture->calls++;
    if (!setup_join(path, payload, L"HookshotBuilder") || !CreateDirectoryW(path, NULL)) return 0;
    wchar_t exe[SETUP_PLAN_PATH];
    if (!setup_join(exe, path, L"HookshotBuilder.exe") || !setup_new_file(exe, "authored payload, never executed")) return 0;
    if (!setup_join(path, downloads, L"authored.zip") || !setup_new_file(path, "authored download, never extracted")) return 0;
    if (!wcscmp(fixture->mode, L"prepare-failure")) return 0;
    if (!wcscmp(fixture->mode, L"prepare-cancel")) return -1;
    return 1;
}

static int publish(const wchar_t *from, const wchar_t *to, DWORD flags, void *opaque) {
    Fixture *fixture = (Fixture *)opaque;
    if (!wcscmp(fixture->mode, L"publish-failure")) { SetLastError(ERROR_DISK_FULL); return 0; }
    return MoveFileExW(from, to, flags) != 0;
}

typedef struct {
    const wchar_t *mode, *original;
    wchar_t alternate[SETUP_PLAN_PATH];
    unsigned prompts, choices, invalid;
} ConsentFixture;
static int available(const wchar_t *root, uint64_t *bytes, void *opaque) {
    ConsentFixture *fixture = (ConsentFixture *)opaque;
    *bytes = !wcsncmp(fixture->mode, L"consent-low-", 12) && setup_path_equal(root, fixture->original) ? 1 : UINT64_MAX;
    return 1;
}
static int confirm(const SetupInstallPlan *plan, uint64_t bytes, int can_change, void *opaque) {
    ConsentFixture *fixture = (ConsentFixture *)opaque;
    (void)bytes;
    fixture->prompts++;
    if (!wcsncmp(fixture->mode, L"consent-low-", 12)) {
        if (plan->ready != (fixture->prompts > 1)) fixture->invalid++;
        if (!wcscmp(fixture->mode, L"consent-low-forced")) return SETUP_CHOOSE_INSTALL;
        if (!wcscmp(fixture->mode, L"consent-low-fixed")) return SETUP_CHOOSE_CHANGE;
        if (!wcscmp(fixture->mode, L"consent-low-change"))
            return fixture->prompts == 1 ? SETUP_CHOOSE_CHANGE : SETUP_CHOOSE_INSTALL;
        return SETUP_CHOOSE_CANCEL;
    }
    if (!wcscmp(fixture->mode, L"consent-install")) return SETUP_CHOOSE_INSTALL;
    if (!wcscmp(fixture->mode, L"consent-change") && can_change && fixture->prompts == 1) return SETUP_CHOOSE_CHANGE;
    return SETUP_CHOOSE_CANCEL;
}
static SetupPlanResult choose(const wchar_t *current, wchar_t *root, void *opaque) {
    ConsentFixture *fixture = (ConsentFixture *)opaque;
    fixture->choices++;
    (void)current;
    if (!wcscmp(fixture->mode, L"consent-low-change"))
        memcpy(root, fixture->alternate, (wcslen(fixture->alternate) + 1) * sizeof(wchar_t));
    return SETUP_PLAN_OK;
}

#ifdef SETUP_DIALOG_PREVIEW
typedef struct { wchar_t parent[SETUP_PLAN_PATH], initial[SETUP_PLAN_PATH]; } Preview;
static int preview_available(const wchar_t *root, uint64_t *bytes, void *opaque) {
    Preview *preview = (Preview *)opaque;
    *bytes = setup_path_equal(root, preview->initial) ? 1 : 1024;
    return 1;
}
static int preview_confirm(const SetupInstallPlan *plan, uint64_t bytes, int can_change, void *opaque) {
    (void)opaque;
    return setup_confirm_dialog(NULL, plan, bytes, can_change, L"TEST - Hookshot Builder setup",
        L"TEST: sizes and free space are authored examples. Transport, preparation and program launches are disabled.");
}
static SetupPlanResult preview_choose(const wchar_t *current, wchar_t *root, void *opaque) {
    Preview *preview = (Preview *)opaque;
    SetupPlanResult result = setup_folder_dialog(NULL, current, root, L"TEST - choose an authored preview folder");
    if (result != SETUP_PLAN_OK) return result;
    size_t n = wcslen(preview->parent);
    if (CompareStringOrdinal(root, (int)n, preview->parent, (int)n, TRUE) != CSTR_EQUAL || root[n] != L'\\')
        return SETUP_PLAN_BAD_PATH; /* Preview can never write outside its authored temporary parent. */
    return SETUP_PLAN_OK;
}
#endif
#endif

#ifdef ORIGINAL_INSTALL_PREFIX
#include "original_install_prefix.h"
#endif

#ifdef SETUP_DIALOG_PREVIEW
static int setup_preview_main(int argc, wchar_t **argv) {
#else
int wmain(int argc, wchar_t **argv) {
#endif
    CHECK(argc == 3);
    wchar_t root[SETUP_PLAN_PATH], path[SETUP_PLAN_PATH];
    CHECK(setup_join(root, argv[1], L"HookshotBuilder"));
#ifdef ORIGINAL_INSTALL_PREFIX
    wchar_t install[MAX_PATH], final_dir[MAX_PATH];
    CHECK(setup_join(final_dir, root, L"versions\\v1"));
    CHECK(setup_join(install, root, L"versions\\v1.part"));
    original_install_prefix(install, final_dir);
    CHECK(setup_join(path, final_dir, L"HookshotBuilder\\HookshotBuilder.exe"));
    CHECK(GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES);
    return 0;
#else
    wchar_t expected[SETUP_PLAN_PATH];
    SetupSpace space = {0};
    memset(space.manifest, 'a', 64);
    space.count = 1;
    memset(space.parts[0].sha, 'b', 64);
    space.parts[0].expanded = 100;
    space.parts[0].files = 1;
    space.parts[0].entries = 1;
    space.parts[0].max_relative_units = 60;
    SetupPlanPart part = {space.parts[0].sha, 100};
    SetupInstallPlan plan;
    SetupInstallOutcome outcome;
    Fixture fixture = {argv[2], 0};
    SetupInstallHooks hooks = {prepare, publish, &fixture, NULL};
    const wchar_t *leaf = !wcscmp(argv[2], L"collision") ? L"v1" : L"v1-0123456789abcdef";
    const wchar_t *nonce = L"0123456789abcdef0123456789abcdef";
    if (!wcscmp(argv[2], L"path-text")) {
        wchar_t input[260] = {0}, original[260], display[520], recovered[260];
        for (unsigned i = 0; i < 250; i++) input[i] = L'W';
        input[18] = 0xd83d; input[19] = 0xde00; input[40] = L'\\'; input[80] = 0x96ea; input[250] = 0;
        memcpy(original, input, sizeof input);
        CHECK(setup_display_path(input, display, sizeof display / sizeof display[0]));
        unsigned got = 0, columns = 0;
        for (unsigned i = 0; display[i]; i++) {
            if (display[i] == L'\n') { CHECK(columns <= 64); columns = 0; continue; }
            CHECK(got < 259);
            recovered[got++] = display[i];
            if (display[i] >= 0xd800 && display[i] <= 0xdbff) {
                CHECK(display[i + 1] >= 0xdc00 && display[i + 1] <= 0xdfff);
                recovered[got++] = display[++i]; columns += 2;
            } else columns += display[i] < 128 ? 1u : 2u;
        }
        recovered[got] = 0;
        CHECK(columns <= 64 && !wcscmp(recovered, original) && !memcmp(input, original, sizeof input));
        const wchar_t *components = L"C:\\Users\\authored\\AppData\\Local\\Temp\\setup-transaction-preview-0123456789abcdef0123456789abcdef\\HookshotBuilder";
        CHECK(setup_display_path(components, display, sizeof display / sizeof display[0]));
        for (unsigned i = 0; display[i]; i++) if (display[i] == L'\n') CHECK(i && display[i - 1] == L'\\');
        CHECK(!setup_display_path(input, display, 3));
        puts("PASS: full display path wraps without changing any filesystem path character");
        return 0;
    }
#ifdef SETUP_DIALOG_PREVIEW
    if (!wcscmp(argv[2], L"preview")) {
        wchar_t temporary[SETUP_PLAN_PATH], safe[SETUP_PLAN_PATH];
        CHECK(GetTempPathW(SETUP_PLAN_PATH, temporary));
        CHECK(setup_safe_path(argv[1], safe, 0) == SETUP_PLAN_OK);
        size_t n = wcslen(temporary);
        CHECK(wcslen(safe) > n && CompareStringOrdinal(safe, (int)n, temporary, (int)n, TRUE) == CSTR_EQUAL);
        CHECK(!wcsncmp(safe + n, L"setup-transaction-preview-", 26) && !wcschr(safe + n, L'\\'));
        Preview preview;
        memcpy(preview.parent, safe, (wcslen(safe) + 1) * sizeof(wchar_t));
        memcpy(preview.initial, root, (wcslen(root) + 1) * sizeof(wchar_t));
        CHECK(SUCCEEDED(CoInitializeEx(NULL, COINIT_APARTMENTTHREADED)));
        SetupConsentHooks consent = {preview_available, preview_confirm, preview_choose, &preview};
        SetupPlanResult chosen = setup_consent(root, 0, leaf, space.manifest, &space, &part, 1, &consent, &plan);
        if (chosen == SETUP_PLAN_CANCELLED) { CoUninitialize(); return 0; }
        CHECK(chosen == SETUP_PLAN_OK);
        /* Only record the consent. Do not prepare files, persist a location, download,
           prune, or load/execute a GUI/tool, even after pressing Install. */
        CHECK(setup_join(path, argv[1], L"install-record.txt"));
        CHECK(setup_new_file(path, "Install explicitly selected; all installer effects and launcher disabled\n"));
        CoUninitialize();
        return 0;
    }
#endif
    if (!wcsncmp(argv[2], L"consent-", 8)) {
        ConsentFixture state = {0};
        state.mode = argv[2];
        state.original = root;
        CHECK(setup_join(state.alternate, argv[1], L"alternate\\HookshotBuilder"));
        SetupConsentHooks consent = {available, confirm, choose, &state};
        int fixed = !wcscmp(argv[2], L"consent-low-fixed");
        SetupPlanResult chosen = setup_consent(root, fixed, leaf, space.manifest, &space, &part, 1, &consent, &plan);
        if (!wcscmp(argv[2], L"consent-low-change")) {
            CHECK(chosen == SETUP_PLAN_OK && plan.ready && setup_path_equal(plan.root, state.alternate));
        } else if (!wcscmp(argv[2], L"consent-install")) CHECK(chosen == SETUP_PLAN_OK && plan.ready);
        else if (!wcscmp(argv[2], L"consent-low-forced")) CHECK(chosen == SETUP_PLAN_NO_SPACE && !plan.ready);
        else CHECK(chosen == SETUP_PLAN_CANCELLED && !plan.ready);
        int changed = !wcscmp(argv[2], L"consent-change") || !wcscmp(argv[2], L"consent-low-change");
        CHECK(state.prompts == (changed ? 2u : 1u));
        CHECK(state.choices == (changed ? 1u : 0u) && !state.invalid);
        puts("PASS: actual consent loop, with no install/write callback");
        return 0;
    }
    CHECK(setup_plan_create(root, leaf, space.manifest, &space, &part, 1, UINT64_MAX, &plan) == SETUP_PLAN_OK);
    HANDLE busy = INVALID_HANDLE_VALUE;
    if (!wcscmp(argv[2], L"busy")) {
        CHECK(setup_join(path, root, L".hookshot-commit.lock"));
        busy = CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 0, NULL, CREATE_NEW,
                           FILE_ATTRIBUTE_NORMAL | FILE_FLAG_DELETE_ON_CLOSE, NULL);
        CHECK(busy != INVALID_HANDLE_VALUE);
    }
    int choice = !wcscmp(argv[2], L"cancel") ? SETUP_CHOOSE_CANCEL :
                 !wcscmp(argv[2], L"change") ? SETUP_CHOOSE_CHANGE : SETUP_CHOOSE_INSTALL;
    SetupInstallResult result = setup_install(&plan, choice, leaf, nonce, &hooks, &outcome);
    if (busy != INVALID_HANDLE_VALUE) CloseHandle(busy);
    if (!wcscmp(argv[2], L"success") || !wcscmp(argv[2], L"new-root")) {
        CHECK(result == SETUP_INSTALL_OK && outcome.committed && fixture.calls == 1);
        CHECK(setup_join(expected, root, L"versions\\v1-0123456789abcdef"));
        CHECK(setup_path_equal(outcome.directory, expected));
        CHECK(setup_owned_version(root, leaf));
        CHECK(setup_join(path, expected, L"HookshotBuilder\\HookshotBuilder.exe"));
        CHECK(GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES);
    } else {
        CHECK(!outcome.committed && !outcome.directory[0]);
        if (choice != SETUP_CHOOSE_INSTALL) CHECK(result == SETUP_INSTALL_CANCELLED && fixture.calls == 0);
        else if (!wcscmp(argv[2], L"busy")) CHECK(result == SETUP_INSTALL_BUSY && fixture.calls == 0);
        else if (!wcscmp(argv[2], L"prepare-failure")) CHECK(result == SETUP_INSTALL_PREPARE && fixture.calls == 1);
        else if (!wcscmp(argv[2], L"prepare-cancel")) CHECK(result == SETUP_INSTALL_CANCELLED && fixture.calls == 1);
        else if (!wcscmp(argv[2], L"publish-failure")) CHECK(result == SETUP_INSTALL_IO && outcome.error == ERROR_DISK_FULL);
        else if (!wcscmp(argv[2], L"collision")) CHECK(result == SETUP_INSTALL_BUSY);
        else CHECK(0);
    }
    puts("PASS: actual setup transaction with authored preparation; no payload execution");
    return 0;
#endif
}

#ifdef SETUP_DIALOG_PREVIEW
int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, LPWSTR command, int show) {
    (void)instance; (void)previous; (void)command; (void)show;
    int argc;
    wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) return 1;
    int result = setup_preview_main(argc, argv);
    LocalFree(argv);
    return result;
}
#endif
