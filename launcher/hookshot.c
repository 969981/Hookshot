/* (hookshot) The executable a player starts: hookshot.exe on Windows, hookshot on Linux (scripts/package.py).

   Beside it lie the prebuilt runtime (hookshot_rt[.exe], nx2/module.py) and tools/ (Python, clang and lld, the build
   scripts). The game itself is not there until this PC builds it from the player's own copy:

     - the game module (hookshot_game.dll / libhookshot_game.so) is there and game.stamp matches runtime.stamp: start
       the runtime, with this program's arguments, and return its exit code;
     - otherwise build it: the game from the first argument, the path remembered in game.source, or a file dialog
       (zenity or kdialog on Linux), then tools/hookshot/nx2/make_game.py --part game into this folder, its progress
       shown as it goes (a window: the stage, a bar, the time left, and the build's output under "Show details"; on
       Linux a GTK 3 one when there is one, else a status line in the terminal), all of it also in build.log; then
       start the runtime.

   Nothing here knows the game: it is the same file for every player. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commdlg.h>
#define SEP "\\"
static const char *const RUNTIME = "hookshot_rt.exe", *const MODULE = "hookshot_game.dll";
#define WHY_KIND "Windows error"
#else
#include <errno.h>
#include <stddef.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#define SEP "/"
static const char *const RUNTIME = "hookshot_rt", *const MODULE = "libhookshot_game.so";
#define WHY_KIND "errno"
#endif

#define PATH_CAP 4096
static char here[PATH_CAP];   /* this program's folder */

static void path(char *out, const char *name) { snprintf(out, PATH_CAP, "%s" SEP "%s", here, name); }

static int exists(const char *p) {
#ifdef _WIN32
    return GetFileAttributesA(p) != INVALID_FILE_ATTRIBUTES;
#else
    struct stat st;
    return stat(p, &st) == 0;
#endif
}

static int read_line(const char *name, char *out, size_t cap) {
    char p[PATH_CAP];
    path(p, name);
    FILE *f = fopen(p, "rb");
    if (!f) return 0;
    out[0] = 0;
    if (fgets(out, (int)cap, f)) out[strcspn(out, "\r\n")] = 0;
    fclose(f);
    return out[0] != 0;
}

static void write_line(const char *name, const char *text) {
    char p[PATH_CAP];
    path(p, name);
    FILE *f = fopen(p, "wb");
    if (f) { fprintf(f, "%s\n", text); fclose(f); }
}

static void find_here(const char *argv0) {
#ifdef _WIN32
    (void)argv0;
    GetModuleFileNameA(NULL, here, PATH_CAP);
#else
    ssize_t n = readlink("/proc/self/exe", here, PATH_CAP - 1);
    if (n > 0) here[n] = 0;
    else snprintf(here, PATH_CAP, "%s", argv0);
#endif
    char *slash = strrchr(here, SEP[0]);
    if (slash) *slash = 0;
    else snprintf(here, PATH_CAP, ".");
}

static void say(const char *text) {
#ifdef _WIN32
    MessageBoxA(NULL, text, "Hookshot", MB_OK | MB_ICONINFORMATION);
#else
    fprintf(stderr, "Hookshot: %s\n", text);
    if (getenv("DISPLAY") || getenv("WAYLAND_DISPLAY")) {
        char cmd[PATH_CAP];
        snprintf(cmd, sizeof cmd, "zenity --info --title=Hookshot --text=\"%s\" 2>/dev/null || kdialog --title Hookshot --msgbox \"%s\" 2>/dev/null", text, text);
        if (system(cmd)) {}
    }
#endif
}

/* the game file: a file dialog, else a typed path */
static int ask_game(char *out, size_t cap) {
#ifdef _WIN32
    OPENFILENAMEA o;
    memset(&o, 0, sizeof o);
    out[0] = 0;
    o.lStructSize = sizeof o;
    o.lpstrFilter = "The game (*.nup)\0*.nup\0All files\0*.*\0";
    o.lpstrFile = out;
    o.nMaxFile = (DWORD)cap;
    o.lpstrTitle = "Hookshot: choose your copy of the game";
    o.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST;
    return GetOpenFileNameA(&o) != 0;
#else
    out[0] = 0;
    if (getenv("DISPLAY") || getenv("WAYLAND_DISPLAY")) {
        FILE *p = popen("zenity --file-selection --title='Hookshot: choose your copy of the game' --file-filter='*.nup' 2>/dev/null"
                        " || kdialog --title 'Hookshot: choose your copy of the game' --getopenfilename . '*.nup' 2>/dev/null", "r");
        if (p) {
            if (fgets(out, (int)cap, p)) out[strcspn(out, "\r\n")] = 0;
            pclose(p);
        }
        if (out[0]) return 1;
    }
    if (!isatty(0)) return 0;
    fprintf(stderr, "Hookshot: the path of your copy of the game (.nup): ");
    if (!fgets(out, (int)cap, stdin)) return 0;
    out[strcspn(out, "\r\n")] = 0;
    return out[0] != 0;
#endif
}

/* tools/python's interpreter, or the system's */
static void python(char *out) {
#ifdef _WIN32
    path(out, "tools\\python\\python.exe");
    if (!exists(out)) snprintf(out, PATH_CAP, "py");
#else
    path(out, "tools/python/bin/python3");
    if (!exists(out)) snprintf(out, PATH_CAP, "python3");
#endif
}

#ifdef _WIN32
static int run_wait(char *cmdline, const char *path_add) {
    if (path_add) {
        char cur[32768], buf[32768 + PATH_CAP];
        GetEnvironmentVariableA("PATH", cur, sizeof cur);
        snprintf(buf, sizeof buf, "%s;%s", path_add, cur);
        SetEnvironmentVariableA("PATH", buf);
    }
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    memset(&si, 0, sizeof si);
    si.cb = sizeof si;
    if (!CreateProcessA(NULL, cmdline, NULL, NULL, FALSE, 0, NULL, here, &si, &pi)) return -1;
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return (int)code;
}
#endif

/* the runtime and tools/ beside this program: said before the game is asked for, not after a 40-minute translation */
static int complete(void) {
    char py_script[PATH_CAP], rt[PATH_CAP], link[PATH_CAP];
    path(py_script, "tools" SEP "hookshot" SEP "nx2" SEP "make_game.py");
    path(rt, RUNTIME);
    path(link, "tools" SEP "link" SEP "runtime.stamp");
    if (exists(py_script) && exists(rt) && exists(link)) return 1;
    char text[3 * PATH_CAP];
    snprintf(text, sizeof text, "This launcher is not in a complete Hookshot folder: %s is missing beside it, in %s.\n\n"
             "Download Hookshot again and extract all of it. (A development build: make the folder with scripts/package.py.)",
             !exists(rt) ? RUNTIME : !exists(py_script) ? "tools/hookshot" : "tools/link", here);
    say(text);
    return 0;
}

/* ---- the build's progress, from make_game.py's @@ lines (both systems), then hookshot_rt --prepare-shaders's: the
   stage, how far the whole build is, the time it has taken and about how long it has left ---- */
static const struct { const char *id; double from, to, expect_s; } STAGES[] = {
    {"read", 0.00, 0.06, 150}, {"lift", 0.06, 0.14, 180}, {"compile", 0.14, 0.93, 2400}, {"finish", 0.93, 0.95, 60},
    {"shaders", 0.95, 1.00, 120}};
enum { NSTAGES = sizeof STAGES / sizeof *STAGES };

typedef struct {
    int stage;              /* in STAGES; -1 before the first */
    char text[256];         /* the stage, as make_game.py says it */
    double frac;            /* the stage's own progress; < 0 while it has said none */
    double t0, stage_t0;
    int failed, done;
    char error[1024];
} Progress;

static double now_s(void) {
#ifdef _WIN32
    return (double)GetTickCount64() / 1000.0;
#else
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
#endif
}

static void progress_init(Progress *p) {
    memset(p, 0, sizeof *p);
    p->stage = -1;
    p->frac = -1;
    p->t0 = p->stage_t0 = now_s();
    snprintf(p->text, sizeof p->text, "Starting");
}

/* 1 when the line is one of make_game.py's own (@@...): read here, not shown with the build's output */
static int progress_line(Progress *p, const char *line) {
    if (strncmp(line, "@@", 2)) return 0;
    double f;
    if (!strncmp(line, "@@stage ", 8)) {
        const char *id = line + 8, *text = strchr(id, ' ');
        const size_t n = text ? (size_t)(text - id) : strlen(id);
        for (int i = 0; i < NSTAGES; i++)
            if (strlen(STAGES[i].id) == n && !strncmp(STAGES[i].id, id, n)) p->stage = i;
        snprintf(p->text, sizeof p->text, "%s", text ? text + 1 : id);
        p->frac = -1;
        p->stage_t0 = now_s();
        p->done = 0;   /* (make_game.py's @@done, then the shaders' stage) */
    } else if (sscanf(line, "@@progress %lf", &f) == 1) {
        p->frac = f < 0 ? 0 : f > 1 ? 1 : f;
    } else if (!strncmp(line, "@@error ", 8)) {
        p->failed = 1;
        snprintf(p->error, sizeof p->error, "%s", line + 8);
    } else if (!strncmp(line, "@@done", 6)) {
        p->done = 1;
    }
    return 1;
}

static double progress_overall(const Progress *p) {
    if (p->done) return 1;
    if (p->stage < 0) return 0;
    const double t = now_s() - p->stage_t0, e = STAGES[p->stage].expect_s;
    const double f = p->frac >= 0 ? p->frac : (t / e < 0.9 ? t / e : 0.9);   /* (a stage that says nothing: by its usual length) */
    return STAGES[p->stage].from + (STAGES[p->stage].to - STAGES[p->stage].from) * f;
}

/* "12:04 so far, about 20 minutes left" */
static void progress_times(const Progress *p, char *out, size_t cap) {
    const int el = (int)(now_s() - p->t0);
    int n = snprintf(out, cap, "%d:%02d so far", el / 60, el % 60);
    double left = -1;
    if (p->stage >= 0 && p->frac > 0.05) {   /* (from this stage's pace, and the usual length of the ones after it) */
        left = (now_s() - p->stage_t0) * (1 - p->frac) / p->frac;
        for (int i = p->stage + 1; i < NSTAGES; i++) left += STAGES[i].expect_s;
    }
    if (left >= 90) snprintf(out + n, cap - (size_t)n, ", about %d minutes left", (int)(left / 60 + 0.5));
    else if (left >= 0) snprintf(out + n, cap - (size_t)n, ", about a minute left");
    else if (p->stage < 2) snprintf(out + n, cap - (size_t)n, " (the first build takes about 40 minutes)");
}

/* the build's output a line at a time, also into build.log beside this program (what "Show details" shows) */
typedef struct {
    Progress p;
    FILE *log;
    char part[8192];
    size_t npart;
    void (*on_output)(const char *line);   /* a line of the build's own output */
    void (*on_progress)(void);             /* the stage or the progress changed */
} Feed;

static void feed_line(Feed *f, char *line) {
    line[strcspn(line, "\r\n")] = 0;
    if (f->log) { fprintf(f->log, "%s\n", line); fflush(f->log); }
    if (progress_line(&f->p, line)) {
        if (f->p.failed && f->on_output) {   /* (the reason, in the details too) */
            char e[1100];
            snprintf(e, sizeof e, "Stopped: %s", f->p.error);
            f->on_output(e);
        }
        if (f->on_progress) f->on_progress();
    } else if (f->on_output) {
        f->on_output(line);
    }
}

static void feed(Feed *f, const char *data, size_t n) {
    for (size_t i = 0; i < n; i++) {
        if (data[i] == '\n' || f->npart == sizeof f->part - 1) {
            f->part[f->npart] = 0;
            feed_line(f, f->part);
            f->npart = 0;
            if (data[i] == '\n') continue;
        }
        f->part[f->npart++] = data[i];
    }
}

/* one command's output ended (the log goes on: the shaders' step follows the build's) */
static void feed_end(Feed *f) {
    if (f->npart) { f->part[f->npart] = 0; feed_line(f, f->part); f->npart = 0; }
    if (f->log) fflush(f->log);
}

static void feed_close(Feed *f) {
    feed_end(f);
    if (f->log) { fclose(f->log); f->log = NULL; }
}

static void feed_open(Feed *f) {
    memset(f, 0, sizeof *f);
    progress_init(&f->p);
    char p[PATH_CAP];
    path(p, "build.log");
    f->log = fopen(p, "w");
}

static const char DETAILS_HINT[] = "The whole output is in build.log beside Hookshot.";

/* ---- after the build, the shaders (hookshot_rt --prepare-shaders, gpu/vk/vk_main.cpp): offered for OFFER_S seconds,
   then started. Skipped, the runtime is told so by a file beside it, and builds each shader when first drawn (short
   stutters) instead of preparing them all at its start. Never a failed build: with no usable Vulkan, or a step that
   stops, the game prepares them at its first start as it would have. ---- */
enum { OFFER_S = 10 };
static const char OFFER_TEXT[] = "A minute or two. Skipped, the game builds each when first drawn: short stutters.";

static void shaders_skipped(void) {
    char p[PATH_CAP];
    path(p, "hookshot_prepare_skipped");
    FILE *f = fopen(p, "w");
    if (f) { fputs("the shaders' preparation was skipped after the build: hookshot_rt --prepare-shaders does it\n", f); fclose(f); }
}

#ifdef _WIN32
/* ---- Windows: a window with the stage, a progress bar and the times, and under them the build's output ("Show
   details", closed until asked for, or until the build stops on an error). Closing it stops the build. ---- */
#include <commctrl.h>
enum { WM_BUILD_LINE = WM_APP + 1, WM_BUILD_END, ID_DETAILS = 100, ID_CLOSE, ID_SKIP, TIMER_TICK = 1 };
enum { BUILDING, OFFERING, SHADERS };   /* (the window's phases: make_game.py, the shaders offered, then prepared) */
static struct {
    HWND win, stage, bar, times, details, log, close, skip;
    HFONT font, mono;
    HANDLE proc, job, pipe;
    Feed f;
    int running, open, result, phase, offer_left;
    int dpi;
} w;

static int px(int v) { return MulDiv(v, w.dpi, 96); }

static void win_layout(void) {
    RECT r;
    GetClientRect(w.win, &r);
    const int m = px(14), width = r.right - 2 * m;
    int y = m;
    MoveWindow(w.stage, m, y, width, px(22), TRUE); y += px(28);
    MoveWindow(w.bar, m, y, width, px(20), TRUE); y += px(28);
    MoveWindow(w.times, m, y, width, px(20), TRUE); y += px(30);
    MoveWindow(w.details, m, y, px(130), px(28), TRUE);
    MoveWindow(w.close, r.right - m - px(100), y, px(100), px(28), TRUE);
    MoveWindow(w.skip, r.right - m - px(208), y, px(100), px(28), TRUE);
    y += px(38);
    /* (sized when hidden too: an edit control lays its lines out for the size it has, and one left at 0 wide shows
       them shifted once opened) */
    MoveWindow(w.log, m, y, width, w.open && r.bottom - y - m > px(100) ? r.bottom - y - m : px(300), TRUE);
    ShowWindow(w.log, w.open ? SW_SHOW : SW_HIDE);
}

static void win_set_open(int open) {
    if (w.open == open) return;
    w.open = open;
    SetWindowTextA(w.details, open ? "Hide details" : "Show details");
    RECT r;
    GetWindowRect(w.win, &r);
    SetWindowPos(w.win, NULL, 0, 0, r.right - r.left, px(open ? 520 : 200), SWP_NOMOVE | SWP_NOZORDER);
    win_layout();
}

static void win_progress(void) {
    const double f = progress_overall(&w.f.p);
    char st[300];
    snprintf(st, sizeof st, "%s   %d%%", w.f.p.text, (int)(f * 100));
    SetWindowTextA(w.stage, w.f.p.failed ? "The build stopped" : st);
    SendMessageA(w.bar, PBM_SETPOS, (WPARAM)(f * 1000), 0);
    char t[1400];
    if (w.f.p.failed) snprintf(t, sizeof t, "%s", w.f.p.error);
    else progress_times(&w.f.p, t, sizeof t);
    SetWindowTextA(w.times, t);
}

static void win_output(const char *line) {
    const int len = GetWindowTextLengthA(w.log);
    SendMessageA(w.log, EM_SETSEL, (WPARAM)len, (LPARAM)len);
    char buf[8300];
    snprintf(buf, sizeof buf, "%s%s", len ? "\r\n" : "", line);
    SendMessageA(w.log, EM_REPLACESEL, FALSE, (LPARAM)buf);
}

static void win_stop(void) {
    if (w.running) { TerminateJobObject(w.job, 1); w.running = 0; }
}

static DWORD WINAPI win_reader(void *arg);

/* the command, its output through a pipe to the window (win_reader), in the job that ends it with the window */
static int win_spawn(char *cmdline) {
    SECURITY_ATTRIBUTES sa = {sizeof sa, NULL, TRUE};
    HANDLE wr;
    if (!CreatePipe(&w.pipe, &wr, &sa, 0)) return 0;
    SetHandleInformation(w.pipe, HANDLE_FLAG_INHERIT, 0);
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    memset(&si, 0, sizeof si);
    si.cb = sizeof si;
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdOutput = si.hStdError = wr;
    si.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    if (!CreateProcessA(NULL, cmdline, NULL, NULL, TRUE, CREATE_NO_WINDOW | CREATE_SUSPENDED, NULL, here, &si, &pi)) {
        CloseHandle(wr);
        CloseHandle(w.pipe);
        return 0;
    }
    CloseHandle(wr);
    AssignProcessToJobObject(w.job, pi.hProcess);
    ResumeThread(pi.hThread);
    CloseHandle(pi.hThread);
    if (w.proc) CloseHandle(w.proc);
    w.proc = pi.hProcess;
    w.running = 1;
    if (w.win) CreateThread(NULL, 0, win_reader, NULL, 0, NULL);   /* (the first waits for the window) */
    return 1;
}

static void win_offer_text(void) {
    char t[64];
    snprintf(t, sizeof t, "The game is built. Preparing its shaders in %d s", w.offer_left);
    SetWindowTextA(w.stage, t);
}

static void win_shaders(void) {
    char cmd[PATH_CAP + 64], rt[PATH_CAP];
    path(rt, RUNTIME);
    snprintf(cmd, sizeof cmd, "\"%s\" --prepare-shaders", rt);
    w.phase = SHADERS;
    ShowWindow(w.skip, SW_HIDE);
    SetWindowTextA(w.close, "Skip");
    if (!win_spawn(cmd)) { DestroyWindow(w.win); return; }   /* (the game prepares them at its first start) */
    win_progress();
}

static void win_offer(void) {
    w.phase = OFFERING;
    w.offer_left = OFFER_S;
    win_offer_text();
    SetWindowTextA(w.times, OFFER_TEXT);
    SetWindowTextA(w.close, "Start now");
    ShowWindow(w.skip, SW_SHOW);
}

static void win_skip(void) {
    win_stop();
    shaders_skipped();
    DestroyWindow(w.win);
}

static LRESULT CALLBACK win_proc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_SIZE: win_layout(); return 0;
    case WM_TIMER:
        if (w.running) win_progress();
        else if (w.phase == OFFERING) {
            if (--w.offer_left <= 0) win_shaders();
            else win_offer_text();
        }
        return 0;
    case WM_COMMAND:
        if (LOWORD(wp) == ID_DETAILS) win_set_open(!w.open);
        else if (LOWORD(wp) == ID_SKIP) win_skip();
        else if (LOWORD(wp) == ID_CLOSE && w.phase == OFFERING) win_shaders();
        else if (LOWORD(wp) == ID_CLOSE) SendMessageA(h, WM_CLOSE, 0, 0);
        return 0;
    case WM_BUILD_LINE: {
        char *line = (char *)lp;
        feed(&w.f, line, strlen(line));
        free(line);
        return 0;
    }
    case WM_BUILD_END: {
        feed_end(&w.f);
        DWORD code = 1;
        WaitForSingleObject(w.proc, INFINITE);
        GetExitCodeProcess(w.proc, &code);
        w.running = 0;
        if (w.phase == SHADERS) { DestroyWindow(h); return 0; }   /* (prepared, or left to the game's first start) */
        if (!code && !w.f.p.failed) { w.result = 0; win_offer(); return 0; }
        if (!w.f.p.failed) {
            w.f.p.failed = 1;
            snprintf(w.f.p.error, sizeof w.f.p.error, "The build ended with code %lu.", code);
        }
        win_progress();
        win_output(DETAILS_HINT);
        SetWindowTextA(w.close, "Close");
        win_set_open(1);
        return 0;
    }
    case WM_CLOSE:
        if (w.phase != BUILDING) { win_skip(); return 0; }
        if (w.running && MessageBoxA(h, "Stop building the game?", "Hookshot", MB_YESNO | MB_ICONQUESTION) != IDYES) return 0;
        win_stop();
        DestroyWindow(h);
        return 0;
    case WM_DESTROY: PostQuitMessage(0); return 0;
    }
    return DefWindowProcA(h, msg, wp, lp);
}

static DWORD WINAPI win_reader(void *arg) {
    (void)arg;
    char buf[4096];
    DWORD n;
    while (ReadFile(w.pipe, buf, sizeof buf - 1, &n, NULL) && n) {
        buf[n] = 0;
        char *copy = _strdup(buf);
        if (copy) PostMessageA(w.win, WM_BUILD_LINE, 0, (LPARAM)copy);
    }
    PostMessageA(w.win, WM_BUILD_END, 0, 0);
    return 0;
}

static HWND win_child(const char *cls, const char *text, DWORD style, int id, HFONT font) {
    HWND c = CreateWindowExA(0, cls, text, WS_CHILD | WS_VISIBLE | style, 0, 0, 10, 10, w.win, (HMENU)(INT_PTR)id, NULL, NULL);
    SendMessageA(c, WM_SETFONT, (WPARAM)font, TRUE);
    return c;
}

/* 0 built; 1 not started; 2 stopped (and said so in the window) */
static int build_window(char *cmdline, const char *path_add) {
    char cur[32768], buf[32768 + PATH_CAP];
    GetEnvironmentVariableA("PATH", cur, sizeof cur);
    snprintf(buf, sizeof buf, "%s;%s", path_add, cur);
    SetEnvironmentVariableA("PATH", buf);

    w.job = CreateJobObjectA(NULL, NULL);   /* (the build's processes, clang's among them, end with it) */
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION li;
    memset(&li, 0, sizeof li);
    li.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    SetInformationJobObject(w.job, JobObjectExtendedLimitInformation, &li, sizeof li);
    if (!win_spawn(cmdline)) return 1;
    w.result = 2;
    feed_open(&w.f);
    w.f.on_output = win_output;
    w.f.on_progress = win_progress;

    SetProcessDPIAware();
    HDC dc = GetDC(NULL);
    w.dpi = GetDeviceCaps(dc, LOGPIXELSY);
    ReleaseDC(NULL, dc);
    INITCOMMONCONTROLSEX icc = {sizeof icc, ICC_PROGRESS_CLASS};
    InitCommonControlsEx(&icc);
    WNDCLASSA wc;
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = win_proc;
    wc.hInstance = GetModuleHandleA(NULL);
    wc.hIcon = LoadIconA(wc.hInstance, MAKEINTRESOURCEA(1));
    wc.hCursor = LoadCursorA(NULL, (LPCSTR)IDC_ARROW);
    wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
    wc.lpszClassName = "HookshotBuild";
    RegisterClassA(&wc);
    w.win = CreateWindowExA(0, wc.lpszClassName, "Hookshot: building the game", WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT,
                            px(640), px(200), NULL, NULL, wc.hInstance, NULL);
    w.font = CreateFontA(-px(15), 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, "Segoe UI");
    w.mono = CreateFontA(-px(13), 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, FIXED_PITCH, "Consolas");
    w.stage = win_child("STATIC", "Starting", SS_LEFTNOWORDWRAP, 0, w.font);
    w.bar = win_child(PROGRESS_CLASSA, "", 0, 0, w.font);
    SendMessageA(w.bar, PBM_SETRANGE32, 0, 1000);
    w.times = win_child("STATIC", "", SS_LEFTNOWORDWRAP | SS_ENDELLIPSIS, 0, w.font);
    w.details = win_child("BUTTON", "Show details", BS_PUSHBUTTON, ID_DETAILS, w.font);
    w.close = win_child("BUTTON", "Stop", BS_PUSHBUTTON, ID_CLOSE, w.font);
    w.skip = win_child("BUTTON", "Skip", BS_PUSHBUTTON, ID_SKIP, w.font);
    ShowWindow(w.skip, SW_HIDE);
    w.log = win_child("EDIT", "", WS_BORDER | WS_VSCROLL | ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL,
                      0, w.mono);
    SendMessageA(w.log, EM_SETLIMITTEXT, 0, 0);
    ShowWindow(w.log, SW_HIDE);
    win_layout();
    win_progress();
    ShowWindow(w.win, SW_SHOW);
    SetTimer(w.win, TIMER_TICK, 1000, NULL);
    CreateThread(NULL, 0, win_reader, NULL, 0, NULL);
    MSG m;
    while (GetMessageA(&m, NULL, 0, 0) > 0) {
        TranslateMessage(&m);
        DispatchMessageA(&m);
    }
    win_stop();
    feed_close(&w.f);
    return w.result;
}
#else
/* ---- Linux: the same window in GTK 3 (loaded when there, so this program needs nothing but the C library), else a
   status line in the terminal under the build's output, else zenity's progress ---- */
#include <dlfcn.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>

typedef void *GP;
static struct {
    int (*init_check)(int *, char ***);
    GP (*window_new)(int);
    void (*window_set_title)(GP, const char *);
    void (*window_set_default_size)(GP, int, int);
    void (*window_resize)(GP, int, int);
    void (*container_set_border_width)(GP, unsigned);
    void (*container_add)(GP, GP);
    GP (*box_new)(int, int);
    void (*box_pack_start)(GP, GP, int, int, unsigned);
    void (*box_pack_end)(GP, GP, int, int, unsigned);
    GP (*label_new)(const char *);
    void (*label_set_text)(GP, const char *);
    void (*label_set_xalign)(GP, float);
    void (*label_set_line_wrap)(GP, int);
    GP (*progress_bar_new)(void);
    void (*progress_bar_set_fraction)(GP, double);
    void (*progress_bar_set_text)(GP, const char *);
    void (*progress_bar_set_show_text)(GP, int);
    void (*widget_set_halign)(GP, int);
    GP (*expander_new)(const char *);
    void (*expander_set_expanded)(GP, int);
    void (*expander_set_resize_toplevel)(GP, int);
    GP (*scrolled_window_new)(GP, GP);
    void (*scrolled_window_set_min_content_height)(GP, int);
    GP (*text_view_new)(void);
    void (*text_view_set_editable)(GP, int);
    void (*text_view_set_cursor_visible)(GP, int);
    void (*text_view_set_monospace)(GP, int);
    GP (*text_view_get_buffer)(GP);
    void (*text_view_scroll_mark_onscreen)(GP, GP);
    void (*text_buffer_get_end_iter)(GP, void *);
    void (*text_buffer_insert)(GP, void *, const char *, int);
    GP (*text_buffer_create_mark)(GP, const char *, const void *, int);
    GP (*button_new_with_label)(const char *);
    void (*button_set_label)(GP, const char *);
    void (*widget_show_all)(GP);
    void (*widget_show)(GP);
    void (*widget_hide)(GP);
    void (*widget_destroy)(GP);
    void (*main)(void);
    void (*main_quit)(void);
    unsigned long (*signal_connect_data)(GP, const char *, void (*)(void), GP, void *, int);
    unsigned (*timeout_add)(unsigned, int (*)(GP), GP);
    unsigned (*unix_fd_add)(int, int, int (*)(int, int, GP), GP);
} gtk;

static int gtk_load(void) {
    if (!getenv("DISPLAY") && !getenv("WAYLAND_DISPLAY")) return 0;
    void *so = dlopen("libgtk-3.so.0", RTLD_NOW | RTLD_LOCAL);
    if (!so) return 0;
    static const struct { const char *name; size_t at; } fns[] = {
#define F(field, sym) {sym, offsetof(__typeof__(gtk), field)}
        F(init_check, "gtk_init_check"), F(window_new, "gtk_window_new"), F(window_set_title, "gtk_window_set_title"),
        F(window_set_default_size, "gtk_window_set_default_size"), F(window_resize, "gtk_window_resize"),
        F(container_set_border_width, "gtk_container_set_border_width"), F(container_add, "gtk_container_add"),
        F(box_new, "gtk_box_new"), F(box_pack_start, "gtk_box_pack_start"), F(box_pack_end, "gtk_box_pack_end"),
        F(label_new, "gtk_label_new"), F(label_set_text, "gtk_label_set_text"), F(label_set_xalign, "gtk_label_set_xalign"),
        F(label_set_line_wrap, "gtk_label_set_line_wrap"), F(progress_bar_new, "gtk_progress_bar_new"),
        F(progress_bar_set_fraction, "gtk_progress_bar_set_fraction"), F(progress_bar_set_text, "gtk_progress_bar_set_text"),
        F(progress_bar_set_show_text, "gtk_progress_bar_set_show_text"), F(widget_set_halign, "gtk_widget_set_halign"), F(expander_new, "gtk_expander_new"),
        F(expander_set_expanded, "gtk_expander_set_expanded"), F(expander_set_resize_toplevel, "gtk_expander_set_resize_toplevel"),
        F(scrolled_window_new, "gtk_scrolled_window_new"),
        F(scrolled_window_set_min_content_height, "gtk_scrolled_window_set_min_content_height"),
        F(text_view_new, "gtk_text_view_new"), F(text_view_set_editable, "gtk_text_view_set_editable"),
        F(text_view_set_cursor_visible, "gtk_text_view_set_cursor_visible"), F(text_view_set_monospace, "gtk_text_view_set_monospace"),
        F(text_view_get_buffer, "gtk_text_view_get_buffer"), F(text_view_scroll_mark_onscreen, "gtk_text_view_scroll_mark_onscreen"),
        F(text_buffer_get_end_iter, "gtk_text_buffer_get_end_iter"), F(text_buffer_insert, "gtk_text_buffer_insert"),
        F(text_buffer_create_mark, "gtk_text_buffer_create_mark"), F(button_new_with_label, "gtk_button_new_with_label"),
        F(button_set_label, "gtk_button_set_label"), F(widget_show_all, "gtk_widget_show_all"),
        F(widget_show, "gtk_widget_show"), F(widget_hide, "gtk_widget_hide"),
        F(widget_destroy, "gtk_widget_destroy"), F(main, "gtk_main"), F(main_quit, "gtk_main_quit"),
        F(signal_connect_data, "g_signal_connect_data"), F(timeout_add, "g_timeout_add"), F(unix_fd_add, "g_unix_fd_add"),
#undef F
    };
    for (size_t i = 0; i < sizeof fns / sizeof *fns; i++) {
        void *f = dlsym(so, fns[i].name);   /* (GLib's and GObject's through GTK's own dependencies) */
        if (!f) return 0;
        memcpy((char *)&gtk + fns[i].at, &f, sizeof f);
    }
    return gtk.init_check(NULL, NULL);
}

/* the command in its own process group (stopping it stops all of it), its output and errors on *out; no shell, so any
   path works as it is */
static pid_t spawn(char *const args[], int *out) {
    int fd[2];
    if (pipe(fd)) return -1;
    const pid_t pid = fork();
    if (pid < 0) { close(fd[0]); close(fd[1]); return -1; }
    if (pid == 0) {
        setpgid(0, 0);
        dup2(fd[1], 1);
        dup2(fd[1], 2);
        close(fd[0]);
        close(fd[1]);
        execvp(args[0], args);
        perror(args[0]);
        _exit(127);
    }
    close(fd[1]);
    fcntl(fd[0], F_SETFD, FD_CLOEXEC);
    *out = fd[0];
    return pid;
}

static pid_t spawn_shaders(int *out) {
    static char rt[PATH_CAP];
    path(rt, RUNTIME);
    char *args[] = {rt, "--prepare-shaders", NULL};
    return spawn(args, out);
}

enum { BUILDING, OFFERING, SHADERS, ENDED };   /* (the window's phases: make_game.py, the shaders offered, prepared) */
static struct {
    GP win, stage, bar, times, expander, buffer, view, mark, button, skip;
    Feed f;
    pid_t pid;
    int fd, running, result, phase, offer_left;
} g;

static void g_progress(void) {
    gtk.label_set_text(g.stage, g.f.p.failed ? "The build stopped" : g.f.p.text);
    const double f = progress_overall(&g.f.p);
    char pct[16];
    snprintf(pct, sizeof pct, "%d%%", (int)(f * 100));
    gtk.progress_bar_set_fraction(g.bar, f);
    gtk.progress_bar_set_text(g.bar, pct);
    char t[1400];
    if (g.f.p.failed) snprintf(t, sizeof t, "%s", g.f.p.error);
    else progress_times(&g.f.p, t, sizeof t);
    gtk.label_set_text(g.times, t);
}

static void g_output(const char *line) {
    char iter[256];   /* (a GtkTextIter: 80 bytes on 64-bit, room to spare) */
    gtk.text_buffer_get_end_iter(g.buffer, iter);
    gtk.text_buffer_insert(g.buffer, iter, line, -1);
    gtk.text_buffer_get_end_iter(g.buffer, iter);
    gtk.text_buffer_insert(g.buffer, iter, "\n", 1);
    gtk.text_view_scroll_mark_onscreen(g.view, g.mark);
}

static void g_stop(void) {
    if (g.running) { kill(-g.pid, SIGTERM); waitpid(g.pid, NULL, 0); g.running = 0; }
}

static int g_readable(int fd, int cond, GP data);

static void g_offer_text(void) {
    char t[64];
    snprintf(t, sizeof t, "The game is built. Preparing its shaders in %d s", g.offer_left);
    gtk.label_set_text(g.stage, t);
}

static void g_shaders(void) {
    g.phase = SHADERS;
    gtk.widget_hide(g.skip);
    gtk.button_set_label(g.button, "Skip");
    g.pid = spawn_shaders(&g.fd);
    if (g.pid < 0) { g.phase = ENDED; gtk.widget_destroy(g.win); return; }   /* (the game prepares them at its first start) */
    g.running = 1;
    gtk.unix_fd_add(g.fd, 1 | 16 | 8, g_readable, NULL);
    g_progress();
}

static void g_offer(void) {
    g.phase = OFFERING;
    g.offer_left = OFFER_S;
    g_offer_text();
    gtk.label_set_text(g.times, OFFER_TEXT);
    gtk.button_set_label(g.button, "Start now");
    gtk.widget_show(g.skip);
}

static int g_tick(GP data) {
    (void)data;
    if (g.phase == ENDED) return 0;
    if (g.running) g_progress();
    else if (g.phase == OFFERING) {
        if (--g.offer_left <= 0) g_shaders();
        else g_offer_text();
    }
    return 1;
}

static int g_readable(int fd, int cond, GP data) {
    (void)cond, (void)data;
    char buf[4096];
    const ssize_t n = read(fd, buf, sizeof buf);
    if (n > 0) { feed(&g.f, buf, (size_t)n); return 1; }
    if (n < 0 && (errno == EINTR || errno == EAGAIN)) return 1;
    feed_end(&g.f);   /* (the build ended) */
    close(fd);
    int status = 1;
    waitpid(g.pid, &status, 0);
    g.running = 0;
    if (g.phase == SHADERS) { g.phase = ENDED; gtk.widget_destroy(g.win); return 0; }   /* (prepared, or left to the game) */
    if (!g.f.p.failed && WIFEXITED(status) && !WEXITSTATUS(status)) { g.result = 0; g_offer(); return 0; }
    if (!g.f.p.failed) {
        g.f.p.failed = 1;
        snprintf(g.f.p.error, sizeof g.f.p.error, "The build ended with code %d.", WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status));
    }
    g_progress();
    g_output(DETAILS_HINT);
    gtk.button_set_label(g.button, "Close");
    gtk.expander_set_expanded(g.expander, 1);
    return 0;
}

static void g_close(void) {
    if (g.phase == OFFERING) g_shaders();
    else gtk.widget_destroy(g.win);   /* (while building: stops it; while preparing: skips) */
}
static void g_skip(void) { gtk.widget_destroy(g.win); }
static void g_destroyed(void) {
    if (g.phase == OFFERING || g.phase == SHADERS) shaders_skipped();
    g.phase = ENDED;
    g_stop();
    gtk.main_quit();
}

/* 0 built; 2 stopped (said so in the window) */
static int build_gtk(pid_t pid, int fd) {
    memset(&g, 0, sizeof g);
    g.pid = pid, g.fd = fd, g.running = 1, g.result = 2;
    feed_open(&g.f);
    g.f.on_output = g_output;
    g.f.on_progress = g_progress;
    g.win = gtk.window_new(0);
    gtk.window_set_title(g.win, "Hookshot: building the game");
    gtk.window_set_default_size(g.win, 640, -1);
    gtk.container_set_border_width(g.win, 14);
    GP box = gtk.box_new(1, 8);
    gtk.container_add(g.win, box);
    g.stage = gtk.label_new("Starting");
    gtk.label_set_xalign(g.stage, 0);
    gtk.box_pack_start(box, g.stage, 0, 0, 0);
    g.bar = gtk.progress_bar_new();
    gtk.progress_bar_set_show_text(g.bar, 1);
    gtk.box_pack_start(box, g.bar, 0, 0, 0);
    g.times = gtk.label_new("");
    gtk.label_set_xalign(g.times, 0);
    gtk.label_set_line_wrap(g.times, 1);
    gtk.box_pack_start(box, g.times, 0, 0, 0);
    GP row = gtk.box_new(0, 8);
    gtk.box_pack_start(box, row, 1, 1, 0);
    g.expander = gtk.expander_new("Show details");
    gtk.expander_set_resize_toplevel(g.expander, 1);
    gtk.box_pack_start(row, g.expander, 1, 1, 0);
    GP scroll = gtk.scrolled_window_new(NULL, NULL);
    gtk.scrolled_window_set_min_content_height(scroll, 320);
    g.view = gtk.text_view_new();
    gtk.text_view_set_editable(g.view, 0);
    gtk.text_view_set_cursor_visible(g.view, 0);
    gtk.text_view_set_monospace(g.view, 1);
    g.buffer = gtk.text_view_get_buffer(g.view);
    char iter[256];
    gtk.text_buffer_get_end_iter(g.buffer, iter);
    g.mark = gtk.text_buffer_create_mark(g.buffer, NULL, iter, 0);   /* (right gravity: stays at the end) */
    gtk.container_add(scroll, g.view);
    gtk.container_add(g.expander, scroll);
    GP buttons = gtk.box_new(0, 8);
    gtk.widget_set_halign(buttons, 2 /* GTK_ALIGN_END */);
    gtk.box_pack_end(box, buttons, 0, 0, 0);
    g.skip = gtk.button_new_with_label("Skip");
    gtk.box_pack_start(buttons, g.skip, 0, 0, 0);
    g.button = gtk.button_new_with_label("Stop");
    gtk.box_pack_start(buttons, g.button, 0, 0, 0);
    gtk.signal_connect_data(g.button, "clicked", (void (*)(void))g_close, NULL, NULL, 0);
    gtk.signal_connect_data(g.skip, "clicked", (void (*)(void))g_skip, NULL, NULL, 0);
    gtk.signal_connect_data(g.win, "destroy", (void (*)(void))g_destroyed, NULL, NULL, 0);
    gtk.timeout_add(1000, g_tick, NULL);
    gtk.unix_fd_add(fd, 1 | 16 | 8 /* G_IO_IN | G_IO_HUP | G_IO_ERR */, g_readable, NULL);
    g_progress();
    gtk.widget_show_all(g.win);
    gtk.widget_hide(g.skip);
    gtk.main();
    g_stop();
    feed_close(&g.f);
    return g.result;
}

/* a terminal: the build's output scrolls, and under it one line that keeps saying how far it is */
static Feed t;
static int t_tty;
static void t_status(void) {
    if (!t_tty) return;
    char times[1400];
    progress_times(&t.p, times, sizeof times);
    const double f = progress_overall(&t.p);
    char bar[31];
    for (int i = 0; i < 30; i++) bar[i] = i < (int)(f * 30) ? '#' : '-';
    bar[30] = 0;
    fprintf(stderr, "\r\033[K[%s] %3d%%  %s, %s", bar, (int)(f * 100), t.p.text, times);
    fflush(stderr);
}
static void t_output(const char *line) {
    if (t_tty) fprintf(stderr, "\r\033[K");
    fprintf(stderr, "%s\n", line);
    t_status();
}
static void t_progress(void) {
    static int said = -1;   /* (no terminal: each stage once) */
    if (!t_tty && t.p.stage >= 0 && t.p.stage != said) fprintf(stderr, "Hookshot: %s\n", t.p.text);
    said = t.p.stage;
    t_status();
}
/* a line typed on the terminal, if one came: its first character ('\n' for Enter alone), else 0 */
static int t_key(struct pollfd *in) {
    if (in->fd < 0 || !(in->revents & POLLIN)) return 0;
    char line[64];
    const ssize_t n = read(in->fd, line, sizeof line);
    if (n <= 0) { in->fd = -1; return 0; }
    return line[0];
}

/* the command's output until it ends, the status line under it; shaders: a line starting with s skips them */
static int t_follow(pid_t pid, int fd, FILE *bar, int shaders, int *skipped) {
    int last_stage = -2;
    struct pollfd pf[2] = {{fd, POLLIN, 0}, {shaders && t_tty ? 0 : -1, POLLIN, 0}};
    for (;;) {
        const int r = poll(pf, 2, 1000);
        if (r < 0 && errno == EINTR) continue;
        if (t_key(&pf[1]) == 's') {
            kill(-pid, SIGTERM);
            *skipped = 1;
            break;
        }
        if (r > 0 && pf[0].revents) {
            char buf[4096];
            const ssize_t n = read(fd, buf, sizeof buf);
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) break;
            feed(&t, buf, (size_t)n);
        }
        t_status();
        if (bar) {   /* (zenity: the whole build's share, and the stage with the times) */
            char times[1400];
            progress_times(&t.p, times, sizeof times);
            fprintf(bar, "%d\n", (int)(progress_overall(&t.p) * 99));
            if (t.p.stage != last_stage || r == 0) fprintf(bar, "# %s (%s)\n", t.p.text, times);
            last_stage = t.p.stage;
            fflush(bar);
        }
    }
    feed_end(&t);
    close(fd);
    if (t_tty) fprintf(stderr, "\n");
    int status = 1;
    waitpid(pid, &status, 0);
    return !t.p.failed && WIFEXITED(status) && !WEXITSTATUS(status);
}

static FILE *zenity_bar(void) {
    const int zenity = (getenv("DISPLAY") || getenv("WAYLAND_DISPLAY")) && !isatty(2) && system("command -v zenity >/dev/null 2>&1") == 0;
    return zenity ? popen("zenity --progress --title=Hookshot --text='Building the game' --auto-close --no-cancel 2>/dev/null", "w") : NULL;
}

static void zenity_end(FILE *bar) {
    if (bar) { fprintf(bar, "100\n"); pclose(bar); }
}

static int build_terminal(pid_t pid, int fd) {
    FILE *bar = zenity_bar();
    feed_open(&t);
    t_tty = isatty(2) && !bar;
    t.on_output = bar ? NULL : t_output;
    t.on_progress = t_progress;
    int skipped = 0;
    const int ok = t_follow(pid, fd, bar, 0, &skipped);
    zenity_end(bar);
    if (!ok) {
        if (t.p.failed) fprintf(stderr, "Hookshot: the build stopped: %s\n%s\n", t.p.error, DETAILS_HINT);
        feed_close(&t);
        return 1;
    }
    /* the shaders: on a terminal offered first (Enter starts them now, s skips), else started */
    if (t_tty && isatty(0)) {
        fprintf(stderr, "Hookshot: the game is built. %s\nEnter starts preparing them now, s then Enter skips; they start "
                        "by themselves in %d s.\n", OFFER_TEXT, OFFER_S);
        struct pollfd in = {0, POLLIN, 0};
        for (int left = OFFER_S * 10; left > 0; left--) {
            if (poll(&in, 1, 100) > 0) {
                const int k = t_key(&in);
                if (k == 's') { shaders_skipped(); fprintf(stderr, "Hookshot: shaders skipped.\n"); feed_close(&t); return 0; }
                if (k) break;
            }
        }
        fprintf(stderr, "Hookshot: preparing the shaders (s then Enter skips).\n");
    }
    int out;
    const pid_t sp = spawn_shaders(&out);
    if (sp > 0) {
        bar = zenity_bar();
        t.on_output = bar ? NULL : t_output;
        t_tty = isatty(2) && !bar;
        if (!t_follow(sp, out, bar, 1, &skipped) && !skipped)
            fprintf(stderr, "Hookshot: the shaders were not prepared now: %s\n",
                    t.p.failed ? t.p.error : "the step stopped. The game prepares them at its first start.");
        zenity_end(bar);
        if (skipped) { waitpid(sp, NULL, 0); shaders_skipped(); fprintf(stderr, "Hookshot: shaders skipped.\n"); }
    }
    feed_close(&t);
    return 0;
}
#endif

/* the build: make_game.py --part game for this launcher's own system, its progress shown as it goes. 0 built; 1 not
   (to be said); 2 not, and the build's window said why */
static int build(const char *game) {
    char py[PATH_CAP], script[PATH_CAP], llvm[PATH_CAP];
    python(py);
    path(script, "tools" SEP "hookshot" SEP "nx2" SEP "make_game.py");
    path(llvm, "tools" SEP "llvm" SEP "bin");
    const char *kind = strstr(game, ".nup") || strstr(game, ".NUP") ? "--nup" : "--dump";
    fprintf(stderr, "Hookshot: building the game from %s into %s. This takes about 40 minutes the first time.\n", game, here);
#ifdef _WIN32
    char cmd[4 * PATH_CAP];
    snprintf(cmd, sizeof cmd, "\"%s\" -I \"%s\" --part game --target windows %s \"%s\" --out \"%s\"", py, script, kind, game, here);
    return build_window(cmd, llvm);
#else
    char env_path[2 * PATH_CAP + 4096];
    snprintf(env_path, sizeof env_path, "%s:%s", llvm, getenv("PATH") ? getenv("PATH") : "/usr/bin:/bin");
    setenv("PATH", env_path, 1);
    const int use_gtk = gtk_load();
    char *args[] = {py, "-I", script, "--part", "game", "--target", "linux", (char *)kind, (char *)game, "--out", here, NULL};
    int fd;
    const pid_t pid = spawn(args, &fd);
    if (pid < 0) return 1;
    return use_gtk ? build_gtk(pid, fd) : build_terminal(pid, fd);
#endif
}

#ifndef _WIN32
/* Linux: the game in the desktop's menus, with its icon (hookshot.png here, scripts/package.py), as
   $XDG_DATA_HOME/applications/io.github.NotRexed.Hookshot.desktop: the name of the window's app id (host/linux/
   display_sdl.c), so the compositor puts the icon on the window too. Written again when this folder moved;
   HOOKSHOT_NO_DESKTOP=1 leaves it out */
static void desktop_entry(void) {
    char icon[PATH_CAP], dir[PATH_CAP], file[PATH_CAP + 64];
    path(icon, "hookshot.png");
    struct stat st;
    if (getenv("HOOKSHOT_NO_DESKTOP") || stat(icon, &st)) return;
    const char *xdg = getenv("XDG_DATA_HOME"), *home = getenv("HOME");
    if (xdg && xdg[0] == '/') snprintf(dir, sizeof dir, "%s/applications", xdg);
    else if (home && home[0] == '/') snprintf(dir, sizeof dir, "%s/.local/share/applications", home);
    else return;
    char exec[PATH_CAP * 2], *e = exec;   /* (the spec's quoting: ", `, $ and \ escaped inside the quotes) */
    *e++ = '"';
    for (const char *c = here; *c && e < exec + sizeof exec - 16; c++) {
        if (strchr("\"`$\\", *c)) *e++ = '\\';
        *e++ = *c;
    }
    snprintf(e, (size_t)(exec + sizeof exec - e), "/hookshot\"");
    char want[PATH_CAP * 4];
    snprintf(want, sizeof want,
             "[Desktop Entry]\nType=Application\nName=Hookshot\nComment=The Legend of Zelda: Ocarina of Time, built on this PC\n"
             "Exec=%s\nIcon=%s\nTerminal=false\nCategories=Game;\nStartupWMClass=io.github.NotRexed.Hookshot\n", exec, icon);
    snprintf(file, sizeof file, "%s/io.github.NotRexed.Hookshot.desktop", dir);
    char have[sizeof want] = "";
    FILE *f = fopen(file, "r");
    if (f) {
        have[fread(have, 1, sizeof have - 1, f)] = 0;
        fclose(f);
    }
    if (!strcmp(have, want)) return;
    for (char *c = dir + 1; *c; c++)   /* (the folders on the way, as mkdir -p) */
        if (*c == '/') *c = 0, mkdir(dir, 0755), *c = '/';
    mkdir(dir, 0755);
    if ((f = fopen(file, "w"))) {
        fputs(want, f);
        fclose(f);
    }
}
#endif

static int start(int argc, char **argv) {
    char rt[PATH_CAP];
    path(rt, RUNTIME);
#ifdef _WIN32
    char cmd[32768];
    int n = snprintf(cmd, sizeof cmd, "\"%s\"", rt);
    for (int i = 1; i < argc && n < (int)sizeof cmd; i++) n += snprintf(cmd + n, sizeof cmd - n, " \"%s\"", argv[i]);
    const int code = run_wait(cmd, NULL);
    if (code != -1) return code;
    const int why = (int)GetLastError();
#else
    (void)argc;
    desktop_entry();
    argv[0] = rt;
    execv(rt, argv);
    const int why = errno;
#endif
    char msg[PATH_CAP + 200];   /* (said, not only printed: nobody reads a launcher's stderr) */
    snprintf(msg, sizeof msg, "The game could not be started: %s (%s %d). Extracting the release again puts it back.",
             rt, WHY_KIND, why);
    say(msg);
    return 1;
}

int main(int argc, char **argv) {
    find_here(argv[0]);
    char p[PATH_CAP], want[128], have[128], game[PATH_CAP];
    path(p, MODULE);
    const int ready = exists(p) && read_line("runtime.stamp", want, sizeof want) && read_line("game.stamp", have, sizeof have) &&
                      !strcmp(want, have);
    if (ready) return start(argc, argv);
    if (!complete()) return 1;
    /* the game to build from: given, remembered, or asked for */
    if (argc > 1 && exists(argv[1])) {   /* (taken for the build: not passed on to the game) */
        /* (absolute: the build runs in this folder, and game.source is read from any) */
#ifdef _WIN32
        if (!GetFullPathNameA(argv[1], sizeof game, game, NULL)) snprintf(game, sizeof game, "%s", argv[1]);
#else
        if (!realpath(argv[1], game)) snprintf(game, sizeof game, "%s", argv[1]);
#endif
        argv[1] = NULL;
        argc = 1;
    }
    else if (!(read_line("game.source", game, sizeof game) && exists(game))) {
        if (exists(p)) say("Hookshot was updated: the game has to be built again from your copy.");
        else say("Hookshot builds the game from your own copy, once. Choose your .nup file.");
        if (!ask_game(game, sizeof game)) return 1;
    }
    write_line("game.source", game);
    const int built = build(game);
    if (built == 1) say("The build did not finish. Why is in build.log, beside Hookshot.");
    if (built) return 1;
    return start(argc, argv);
}
