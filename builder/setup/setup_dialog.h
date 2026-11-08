#ifndef HOOKSHOT_SETUP_DIALOG_H
#define HOOKSHOT_SETUP_DIALOG_H

#include "setup_plan.h"
#include <commctrl.h>
#include <shlobj.h>

/* Display-only line breaks keep TaskDialog from ellipsizing an unbroken path.
   Keep components together unless one exceeds 64 display columns. The filesystem
   path is never edited, and a surrogate pair always stays on the same line. */
static inline int setup_display_path(const wchar_t *path, wchar_t *display, size_t capacity) {
    size_t length = 0;
    unsigned columns = 0;
    for (const wchar_t *at = path; *at;) {
        const wchar_t *end = at;
        while (*end && *end != L'\\') end++;
        if (*end) end++;
        unsigned component = 0;
        for (const wchar_t *part = at; part < end;) {
            unsigned units = *part >= 0xd800 && *part <= 0xdbff && part[1] >= 0xdc00 && part[1] <= 0xdfff ? 2u : 1u;
            component += *part < 128 ? 1u : 2u;
            part += units;
        }
        if (columns && columns + component > 64) {
            if (length + 1 >= capacity) return 0;
            display[length++] = L'\n';
            columns = 0;
        }
        while (at < end) {
            unsigned units = *at >= 0xd800 && *at <= 0xdbff && at[1] >= 0xdc00 && at[1] <= 0xdfff ? 2u : 1u;
            unsigned width = *at < 128 ? 1u : 2u;
            if (columns + width > 64) {
                if (length + 1 >= capacity) return 0;
                display[length++] = L'\n';
                columns = 0;
            }
            if (length + units >= capacity) return 0;
            for (unsigned i = 0; i < units; i++) display[length++] = *at++;
            columns += width;
        }
    }
    if (length >= capacity) return 0;
    display[length] = 0;
    return 1;
}

static HRESULT CALLBACK setup_dialog_ready(HWND dialog, UINT notification, WPARAM wp, LPARAM lp, LONG_PTR context) {
    (void)wp;
    (void)lp;
    if (notification == TDN_CREATED && !((const SetupInstallPlan *)context)->ready)
        SendMessageW(dialog, TDM_ENABLE_BUTTON, 1001, FALSE);
    return S_OK;
}

/* Ordinary Windows dialogs: keyboard access, DPI, text sizing and screen readers
   use the system controls. Opening or cancelling either dialog writes no files. */
static inline int setup_confirm_dialog(HWND owner, const SetupInstallPlan *plan, uint64_t available, int can_change,
                                        const wchar_t *title, const wchar_t *notice) {
    wchar_t content[1800], destination[SETUP_PLAN_PATH * 2];
    if (!setup_display_path(plan->root, destination, sizeof destination / sizeof destination[0])) return SETUP_CHOOSE_CANCEL;
    int n = swprintf(content, sizeof content / sizeof content[0],
        L"Builder and tools folder:\n%ls\n\n"
        L"Download: up to %llu bytes compressed (before cache reuse)\n"
        L"Unpacked builder and tools: %llu logical file bytes\n"
        L"Temporary file budget: %llu logical bytes\n"
        L"Available on this drive: %llu bytes\n\n"
        L"The temporary budget includes the full download and unpacked files. Filesystem overhead may require more space. "
        L"Game build work and finished games use separately selected folders; their size is not included.",
        destination, (unsigned long long)plan->compressed, (unsigned long long)plan->expanded,
        (unsigned long long)plan->logical_reserve, (unsigned long long)available);
    if (n < 0 || (size_t)n >= sizeof content / sizeof content[0]) return SETUP_CHOOSE_CANCEL;
    TASKDIALOG_BUTTON buttons[] = {{1001, L"Install"}, {1002, L"Change folder..."}, {IDCANCEL, L"Cancel"}};
    if (!can_change) buttons[1] = buttons[2];
    TASKDIALOGCONFIG config = {0};
    config.cbSize = sizeof config;
    config.hwndParent = owner;
    config.dwFlags = TDF_ALLOW_DIALOG_CANCELLATION | TDF_POSITION_RELATIVE_TO_WINDOW;
    config.cxWidth = 340;
    config.pszWindowTitle = title;
    config.pszMainInstruction = plan->ready ? L"Choose where to install the builder and tools" :
        plan->logical_reserve > available ? L"Choose a folder with more free space" : L"Choose a shorter installation path";
    config.pszContent = content;
    config.pszFooter = notice;
    config.cButtons = can_change ? 3 : 2;
    config.pButtons = buttons;
    config.nDefaultButton = IDCANCEL;
    config.pfCallback = setup_dialog_ready;
    config.lpCallbackData = (LONG_PTR)plan;
    int selected = IDCANCEL;
    if (FAILED(TaskDialogIndirect(&config, &selected, NULL, NULL))) return SETUP_CHOOSE_CANCEL;
    return selected == 1001 ? SETUP_CHOOSE_INSTALL : selected == 1002 ? SETUP_CHOOSE_CHANGE : SETUP_CHOOSE_CANCEL;
}

typedef struct { const wchar_t *parent, *title; } SetupFolderInitial;
static int CALLBACK setup_folder_initial(HWND window, UINT message, LPARAM parameter, LPARAM data) {
    (void)parameter;
    if (message == BFFM_INITIALIZED && data) {
        const SetupFolderInitial *initial = (const SetupFolderInitial *)data;
        SendMessageW(window, BFFM_SETSELECTIONW, TRUE, (LPARAM)initial->parent);
        SetWindowTextW(window, initial->title);
    }
    return 0;
}

static inline SetupPlanResult setup_folder_dialog(HWND owner, const wchar_t *current, wchar_t root[SETUP_PLAN_PATH], const wchar_t *title) {
    wchar_t parent[SETUP_PLAN_PATH];
    if (wcslen(current) >= SETUP_PLAN_PATH) return SETUP_PLAN_PATH_TOO_LONG;
    memcpy(parent, current, (wcslen(current) + 1) * sizeof(wchar_t));
    wchar_t *slash = wcsrchr(parent, L'\\');
    if (slash && slash > parent + 2) *slash = 0;
    BROWSEINFOW browse = {0};
    browse.hwndOwner = owner;
    browse.lpszTitle = L"Choose a parent folder. Setup will use its dedicated HookshotBuilder subfolder.";
    browse.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE | BIF_NONEWFOLDERBUTTON;
    browse.lpfn = setup_folder_initial;
    SetupFolderInitial initial = {parent, title};
    browse.lParam = (LPARAM)&initial;
    if (FAILED(CoInitializeEx(NULL, COINIT_APARTMENTTHREADED))) return SETUP_PLAN_BAD_PATH;
    PIDLIST_ABSOLUTE item = SHBrowseForFolderW(&browse);
    if (!item) { CoUninitialize(); return SETUP_PLAN_CANCELLED; }
    int ok = SHGetPathFromIDListW(item, parent);
    CoTaskMemFree(item);
    CoUninitialize();
    if (!ok) return SETUP_PLAN_BAD_PATH;
    SetupRootKind kind;
    return setup_root_child(parent, root, &kind);
}

static inline int setup_available(const wchar_t *root, uint64_t *bytes) {
    wchar_t path[SETUP_PLAN_PATH];
    if (wcslen(root) >= SETUP_PLAN_PATH) return 0;
    memcpy(path, root, (wcslen(root) + 1) * sizeof(wchar_t));
    DWORD attributes = GetFileAttributesW(path);
    if (attributes == INVALID_FILE_ATTRIBUTES) {
        wchar_t *slash = wcsrchr(path, L'\\');
        if (!slash) return 0;
        slash[1] = 0;
    }
    ULARGE_INTEGER free_bytes;
    if (!GetDiskFreeSpaceExW(path, &free_bytes, NULL, NULL)) return 0;
    *bytes = free_bytes.QuadPart;
    return 1;
}

#endif
