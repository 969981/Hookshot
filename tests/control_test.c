/* The control bridge (host/control.c, built with HOS_CONTROL_NO_GAME) against a fake game, over the real transport: a
   Unix socket on Linux, a named pipe on Windows. A thread stands in for the game (a controller read, then a present,
   every 2 ms; draws a frame as a case sets them), and the test is the client. tests/test_control.py builds and runs it.

   control_test <folder> [quit | level | mem]: the cases, quit (a child serving while the parent asks it to quit), the
   HOS_CONTROL_MEM levels, or the memory cases at the level in the environment. (Windows: quitserve <pipe> and
   servefail <pipe> are the children those start.) */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "../host/control.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <aclapi.h>
#include <sddl.h>
#else
#include <errno.h>
#include <poll.h>
#include <pthread.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#endif

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); exit(1); } } while (0)

/* ---- the platform: sleep, threads, a lock ---- */
#ifdef _WIN32
static void sleep_ms(int ms) { Sleep((DWORD)ms); }
typedef struct { void *(*fn)(void *); void *arg; } Start;
static DWORD WINAPI start_tramp(void *p) {
    Start s = *(Start *)p;
    free(p);
    s.fn(s.arg);
    return 0;
}
static void start_thread(void *(*fn)(void *), void *arg) {
    Start *s = (Start *)malloc(sizeof *s);
    s->fn = fn, s->arg = arg;
    HANDLE t = CreateThread(NULL, 0, start_tramp, s, 0, NULL);
    CHECK(t);
    CloseHandle(t);
}
static SRWLOCK log_mu = SRWLOCK_INIT;
static void log_lock(void) { AcquireSRWLockExclusive(&log_mu); }
static void log_unlock(void) { ReleaseSRWLockExclusive(&log_mu); }
#else
static void sleep_ms(int ms) {
    struct timespec t = {ms / 1000, (long)(ms % 1000) * 1000000L};
    while (nanosleep(&t, &t) && errno == EINTR) {}
}
static void start_thread(void *(*fn)(void *), void *arg) {
    pthread_t t;
    CHECK(pthread_create(&t, NULL, fn, arg) == 0);
    pthread_detach(t);
}
static pthread_mutex_t log_mu = PTHREAD_MUTEX_INITIALIZER;
static void log_lock(void) { pthread_mutex_lock(&log_mu); }
static void log_unlock(void) { pthread_mutex_unlock(&log_mu); }
#endif

/* ---- the fake game ---- */
static volatile int64_t frame;
static volatile double draws_per_frame = 2;
static volatile int64_t draws_total;
static volatile int paused;
#define FRAMES 200000
static uint64_t seen_buttons[FRAMES];
static int32_t seen_stick[FRAMES][4];
static int seen_exclusive[FRAMES];
static char logged[64][300];
static int nlogged;

static int64_t fake_frame(void) { return __atomic_load_n(&frame, __ATOMIC_ACQUIRE); }
static int fake_frame_info(int64_t *replayed, int64_t *draws, int *w, int *h) {
    *replayed = fake_frame(), *draws = draws_total, *w = 1280, *h = 720;
    return 1;
}
typedef struct { char path[1100]; void (*done)(void *, int64_t, int); void *ctx; int64_t after; } Shot;
static void *shot_thread(void *p) {
    Shot *s = (Shot *)p;
    while (fake_frame() <= s->after) sleep_ms(1);
    const int64_t at = fake_frame();
    FILE *f = fopen(s->path, "wb");
    if (f) fputs("\x89PNG fake", f), fclose(f);
    s->done(s->ctx, at, f != NULL);
    free(s);
    return NULL;
}
static int fake_shot(const char *path, int64_t after, void (*done)(void *, int64_t, int), void *ctx) {
    Shot *s = (Shot *)calloc(1, sizeof *s);
    snprintf(s->path, sizeof s->path, "%s", path);
    s->done = done, s->ctx = ctx, s->after = after;
    start_thread(shot_thread, s);
    return 1;
}
static HosControlSetting settings[2] = {
    {"text_speed", "Text speed", "Normal", "gameplay", 0, 0, 3, 1},
    {"fps_counter", "FPS counter", "Off", "gameplay", 0, 0, 1, 1}};
static int fake_setting(int i, HosControlSetting *out) {
    if (i < 0 || i >= 2) return 0;
    *out = settings[i];
    return 1;
}
static int fake_setting_set(int i, int v) {
    if (i < 0 || i >= 2) return 0;
    settings[i].value = v < settings[i].lo ? settings[i].lo : v > settings[i].hi ? settings[i].hi : v;
    snprintf(settings[i].text, sizeof settings[i].text, "v%d", settings[i].value);
    return 1;
}
static void fake_log(const char *line) {
    if (getenv("CONTROL_TEST_VERBOSE")) fprintf(stderr, "log: %s\n", line);
    log_lock();
    snprintf(logged[nlogged++ % 64], sizeof logged[0], "%s", line);
    log_unlock();
    hos_control_log(line);   /* (as hos_log does) */
}
/* a fake mapped region [MEM_BASE, MEM_BASE + MEM_N): the only memory the fake game has */
#define MEM_BASE 0x10000ull
#define MEM_N 4096u
static unsigned char guest[MEM_N];
static int fake_mem_region(uint64_t addr, uint64_t size) {
    uint64_t end;
    if (!size || __builtin_add_overflow(addr, size, &end)) return 0;
    return addr >= MEM_BASE && end <= MEM_BASE + MEM_N;
}
static void fake_mem_read(uint64_t addr, void *dst, uint64_t size) { memcpy(dst, guest + (addr - MEM_BASE), size); }
static void fake_mem_write(uint64_t addr, const void *src, uint64_t size) { memcpy(guest + (addr - MEM_BASE), src, size); }
int hos_control_mem_level(const char *v);   /* (host/control.c) */
static void fake_quit(void) {
    fflush(NULL);
#ifdef _WIN32
    ExitProcess(0);
#else
    _exit(0);
#endif
}
static const char *fake_renderer(void) { return "fake"; }
static HosControlGame game = {fake_frame, fake_frame_info, fake_shot, fake_setting, fake_setting_set,
                              fake_mem_region, fake_mem_read, fake_mem_write, fake_log, fake_quit, fake_renderer, "test"};

static void *game_thread(void *unused) {
    (void)unused;
    for (;;) {
        sleep_ms(2);
        if (paused) continue;
        uint64_t b = 0;
        int32_t st[4] = {0, 0, 0, 0};
        const int ex = hos_control_input(&b, st);
        const int64_t f = fake_frame();
        if (f < FRAMES) seen_buttons[f] = b, memcpy(seen_stick[f], st, sizeof st), seen_exclusive[f] = ex;
        draws_total += (int64_t)draws_per_frame;
        __atomic_store_n(&frame, f + 1, __ATOMIC_RELEASE);
    }
    return NULL;
}

/* ---- the client: a connection to the bridge, and the bytes after its last whole line ---- */
static char sock_path[300];
typedef struct {
#ifdef _WIN32
    HANDLE h;
#else
    int fd;
#endif
    char pend[1 << 16];
    size_t n;
} Client;

#ifdef _WIN32
static Client *dial(void) {
    Client *c = (Client *)calloc(1, sizeof *c);
    for (int i = 0; i < 500; i++) {   /* (the bridge has one instance waiting at a time: busy ones are waited for) */
        c->h = CreateFileA(sock_path, GENERIC_READ | GENERIC_WRITE | READ_CONTROL, 0, NULL, OPEN_EXISTING, 0, NULL);
        if (c->h != INVALID_HANDLE_VALUE) return c;
        if (GetLastError() == ERROR_PIPE_BUSY) WaitNamedPipeA(sock_path, 20);
        else sleep_ms(10);
    }
    CHECK(!"the pipe could not be opened");
    return NULL;
}
static void say_raw(Client *c, const void *p, size_t n) {
    DWORD wrote = 0;
    CHECK(WriteFile(c->h, p, (DWORD)n, &wrote, NULL) && wrote == n);
}
/* bytes waiting, ms at most: 1 some, 0 none yet, -1 the bridge's end closed */
static int wait_readable(Client *c, int ms) {
    for (int waited = 0;; waited++) {
        DWORD avail = 0;
        if (!PeekNamedPipe(c->h, NULL, 0, NULL, &avail, NULL)) return -1;
        if (avail) return 1;
        if (waited >= ms) return 0;
        sleep_ms(1);
    }
}
static long read_some(Client *c, void *p, size_t n) {
    DWORD avail = 0, got = 0;
    if (!PeekNamedPipe(c->h, NULL, 0, NULL, &avail, NULL) || !avail) return -1;
    if (!ReadFile(c->h, p, (DWORD)(avail < n ? avail : n), &got, NULL)) return -1;
    return (long)got;
}
static void hangup(Client *c) { CloseHandle(c->h), free(c); }
#else
static Client *dial(void) {
    Client *c = (Client *)calloc(1, sizeof *c);
    c->fd = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un a = {0};
    a.sun_family = AF_UNIX;
    snprintf(a.sun_path, sizeof a.sun_path, "%s", sock_path);
    CHECK(connect(c->fd, (struct sockaddr *)&a, sizeof a) == 0);
    return c;
}
static void say_raw(Client *c, const void *p, size_t n) { CHECK(write(c->fd, p, n) == (ssize_t)n); }
static int wait_readable(Client *c, int ms) {
    struct pollfd p = {c->fd, POLLIN, 0};
    const int r = poll(&p, 1, ms);
    return r < 0 ? -1 : r > 0;
}
static long read_some(Client *c, void *p, size_t n) { return (long)read(c->fd, p, n); }
static void hangup(Client *c) { close(c->fd), free(c); }
#endif
static void say(Client *c, const char *s) { say_raw(c, s, strlen(s)); }
/* one line (without the newline), waiting ms at most: 0 when none came */
static int hear(Client *c, char *out, size_t cap, int ms) {
    for (;;) {
        char *nl = (char *)memchr(c->pend, '\n', c->n);
        if (nl) {
            const size_t n = (size_t)(nl - c->pend);
            CHECK(n < cap);
            memcpy(out, c->pend, n);
            out[n] = 0;
            memmove(c->pend, nl + 1, c->n - n - 1);
            c->n -= n + 1;
            return 1;
        }
        if (wait_readable(c, ms) <= 0) return 0;
        const long r = read_some(c, c->pend + c->n, sizeof c->pend - c->n);
        if (r <= 0) return 0;
        c->n += (size_t)r;
    }
}
/* the reply with this id (events and other replies skipped) */
static void ask(Client *c, const char *req, const char *id_text, char *out, size_t cap, int ms) {
    say(c, req);
    char want[64];
    snprintf(want, sizeof want, "{\"id\":%s,", id_text);
    for (;;) {
        CHECK(hear(c, out, cap, ms));
        if (!strncmp(out, want, strlen(want))) return;
    }
}
static long long num_after(const char *s, const char *key) {
    const char *p = strstr(s, key);
    CHECK(p);
    return atoll(p + strlen(key));
}
/* a reply's "path", its JSON escapes (a Windows path's backslashes) undone */
static void path_of(const char *line, char *out, size_t cap) {
    const char *p = strstr(line, "\"path\":\"");
    CHECK(p);
    p += 8;
    size_t n = 0;
    for (; *p && *p != '"' && n + 1 < cap; p++) out[n++] = *p == '\\' && p[1] ? *++p : *p;
    out[n] = 0;
}
static int file_exists(const char *path) {
    FILE *f = fopen(path, "rb");
    if (f) fclose(f);
    return f != NULL;
}
static void wait_frames(int64_t n) { const int64_t to = fake_frame() + n; while (fake_frame() < to) sleep_ms(1); }

/* the transport's own name for this run: a socket in the folder, or a pipe named after the process */
static void transport_name(const char *folder, const char *tag) {
#ifdef _WIN32
    (void)folder;
    snprintf(sock_path, sizeof sock_path, "\\\\.\\pipe\\hookshot-control-test-%lu-%s", (unsigned long)GetCurrentProcessId(), tag);
#else
    snprintf(sock_path, sizeof sock_path, "%s/%s.sock", folder, tag);
#endif
}

#ifdef _WIN32
/* this program again, as a child (quitserve, servefail): its exit code, or the running process */
static HANDLE spawn_self(const char *mode, const char *pipe) {
    char exe[MAX_PATH], cmd[MAX_PATH * 2 + 400];
    CHECK(GetModuleFileNameA(NULL, exe, MAX_PATH));
    snprintf(cmd, sizeof cmd, "\"%s\" . %s %s", exe, mode, pipe);
    STARTUPINFOA si = {sizeof si};
    PROCESS_INFORMATION pi;
    CHECK(CreateProcessA(exe, cmd, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi));
    CloseHandle(pi.hThread);
    return pi.hProcess;
}
static DWORD wait_exit(HANDLE p, DWORD ms) {
    DWORD code = 999;
    CHECK(WaitForSingleObject(p, ms) == WAIT_OBJECT_0);
    GetExitCodeProcess(p, &code);
    CloseHandle(p);
    return code;
}
#endif

static void quit_case(const char *folder) {
    transport_name(folder, "quit");
#ifdef _WIN32
    HANDLE child = spawn_self("quitserve", sock_path);
#else
    const pid_t child = fork();
    if (child == 0) {
        if (!hos_control_serve(sock_path, &game)) _exit(3);
        start_thread(game_thread, NULL);
        for (;;) pause();
    }
    struct stat st;
    for (int i = 0; i < 500 && stat(sock_path, &st); i++) sleep_ms(2);
#endif
    Client *c = dial();
    char line[4096];
    ask(c, "{\"id\":9,\"cmd\":\"quit\"}\n", "9", line, sizeof line, 2000);
    CHECK(strstr(line, "\"ok\":true") && strstr(line, "\"quitting\":true"));
#ifdef _WIN32
    CHECK(wait_exit(child, 5000) == 0);
    hangup(c);
    CHECK(CreateFileA(sock_path, GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL) == INVALID_HANDLE_VALUE);
    puts("PASS: quit: the reply, then a clean exit, the pipe gone");
#else
    int status = 0;
    CHECK(waitpid(child, &status, 0) == child);
    CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    CHECK(stat(sock_path, &st) != 0);   /* (the socket removed) */
    puts("PASS: quit: the reply, then a clean exit, the socket removed");
#endif
}

static void level_unit(void) {
    struct { const char *v; int lvl; } cases[] = {
        {NULL, 0}, {"", 0}, {"ro", 1}, {"RO", 1}, {"r", 1}, {"read", 1}, {"Read", 1},
        {"rw", 2}, {"RW", 2}, {"readwrite", 2}, {"ReadWrite", 2},
        {"w", -1}, {"rwx", -1}, {"yes", -1}, {"1", -1}, {"readwritex", -1}};
    for (unsigned i = 0; i < sizeof cases / sizeof *cases; i++)
        CHECK(hos_control_mem_level(cases[i].v) == cases[i].lvl);
    puts("PASS: HOS_CONTROL_MEM levels (ro/r/read, rw/readwrite, bad values)");
}

static void mem_case(const char *folder) {
    const int level = hos_control_mem_level(getenv("HOS_CONTROL_MEM"));   /* what this run was started with */
    for (unsigned i = 0; i < MEM_N; i++) guest[i] = (unsigned char)(i * 7 + 3);
    transport_name(folder, "mem");
    CHECK(hos_control_serve(sock_path, &game));
    start_thread(game_thread, NULL);
    Client *c = dial();
    static char line[70000];
    if (level <= 0) {   /* unset, empty, or a bad value: every mem command is forbidden, with the hint */
        ask(c, "{\"id\":1,\"cmd\":\"mem_read\",\"addr\":65536,\"size\":4}\n", "1", line, sizeof line, 2000);
        CHECK(strstr(line, "\"error\":\"forbidden\"") && strstr(line, "HOS_CONTROL_MEM"));
        puts("PASS: memory off: mem_read is forbidden with the hint");
        paused = 1;
        return;
    }
    /* a read in range (ro and rw) */
    ask(c, "{\"id\":2,\"cmd\":\"mem_read\",\"addr\":65536,\"size\":8}\n", "2", line, sizeof line, 2000);
    CHECK(strstr(line, "\"ok\":true") && strstr(line, "\"format\":\"hex\"") && strstr(line, "\"data\":\"030a11181f262d34\""));
    ask(c, "{\"id\":3,\"cmd\":\"mem_read\",\"addr\":65536,\"size\":4,\"format\":\"base64\"}\n", "3", line, sizeof line, 2000);
    CHECK(strstr(line, "\"data\":\"AwoRGA==\""));
    /* out of range: below, above, and crossing the end */
    ask(c, "{\"id\":4,\"cmd\":\"mem_read\",\"addr\":0,\"size\":4}\n", "4", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"out_of_range\""));
    ask(c, "{\"id\":5,\"cmd\":\"mem_read\",\"addr\":69632,\"size\":1}\n", "5", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"out_of_range\""));
    ask(c, "{\"id\":6,\"cmd\":\"mem_read\",\"addr\":69630,\"size\":4}\n", "6", line, sizeof line, 2000);   /* crosses MEM_BASE+MEM_N */
    CHECK(strstr(line, "\"error\":\"out_of_range\""));
    ask(c, "{\"id\":7,\"cmd\":\"mem_read\",\"addr\":65536,\"size\":2000000}\n", "7", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"bad_request\""));   /* over 1 MB */
    /* many ranges in one snapshot */
    ask(c, "{\"id\":8,\"cmd\":\"mem_read_many\",\"ranges\":[{\"addr\":65536,\"size\":2},{\"addr\":65792,\"size\":2}]}\n",
        "8", line, sizeof line, 2000);
    CHECK(strstr(line, "\"ranges\":[") && strstr(line, "\"addr\":65536") && strstr(line, "\"addr\":65792"));
    if (level < 2) {   /* ro: writes refused */
        ask(c, "{\"id\":9,\"cmd\":\"mem_write\",\"addr\":65536,\"hex\":\"ff\"}\n", "9", line, sizeof line, 2000);
        CHECK(strstr(line, "\"error\":\"forbidden\"") && strstr(line, "rw"));
        CHECK(guest[0] == 3);   /* unchanged */
        puts("PASS: HOS_CONTROL_MEM=ro: reads work, bounds enforced, writes forbidden");
        paused = 1;
        return;
    }
    /* rw: a write, then read it back */
    ask(c, "{\"id\":10,\"cmd\":\"mem_write\",\"addr\":65536,\"hex\":\"deadbeef\"}\n", "10", line, sizeof line, 2000);
    CHECK(strstr(line, "\"ok\":true") && strstr(line, "\"wrote\":4"));
    ask(c, "{\"id\":11,\"cmd\":\"mem_read\",\"addr\":65536,\"size\":4}\n", "11", line, sizeof line, 2000);
    CHECK(strstr(line, "\"data\":\"deadbeef\""));
    CHECK(guest[0] == 0xde && guest[3] == 0xef);
    ask(c, "{\"id\":12,\"cmd\":\"mem_write\",\"addr\":0,\"hex\":\"00\"}\n", "12", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"out_of_range\""));
    ask(c, "{\"id\":13,\"cmd\":\"mem_write\",\"addr\":65536,\"hex\":\"zz\"}\n", "13", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"bad_request\""));
    puts("PASS: HOS_CONTROL_MEM=rw: a write lands and reads back; bounds and bad bytes refused");
    paused = 1;
}

#ifdef _WIN32
/* The pipe's DACL, read back through a client's handle: protected, one ACE, allowing this user alone */
static void dacl_case(Client *c) {
    PACL dacl = NULL;
    PSECURITY_DESCRIPTOR sd = NULL;
    CHECK(GetSecurityInfo(c->h, SE_KERNEL_OBJECT, DACL_SECURITY_INFORMATION, NULL, NULL, &dacl, NULL, &sd) == ERROR_SUCCESS);
    CHECK(dacl && dacl->AceCount == 1);
    SECURITY_DESCRIPTOR_CONTROL control = 0;
    DWORD revision = 0;
    CHECK(GetSecurityDescriptorControl(sd, &control, &revision) && (control & SE_DACL_PROTECTED));
    ACCESS_ALLOWED_ACE *ace = NULL;
    CHECK(GetAce(dacl, 0, (void **)&ace) && ace->Header.AceType == ACCESS_ALLOWED_ACE_TYPE);
    HANDLE token;
    CHECK(OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token));
    char info[256];
    DWORD len = 0;
    CHECK(GetTokenInformation(token, TokenUser, info, sizeof info, &len));
    CloseHandle(token);
    CHECK(EqualSid((PSID)&ace->SidStart, ((TOKEN_USER *)info)->User.Sid));
    LocalFree(sd);
    puts("PASS: the pipe's DACL: protected, one ACE, this user alone");
}
#endif

int main(int argc, char **argv) {
    CHECK(argc >= 2);
#ifdef _WIN32
    if (argc > 3 && !strcmp(argv[2], "quitserve")) {   /* (quit_case's child) */
        snprintf(sock_path, sizeof sock_path, "%s", argv[3]);
        if (!hos_control_serve(sock_path, &game)) return 3;
        start_thread(game_thread, NULL);
        for (;;) sleep_ms(1000);
    }
    if (argc > 3 && !strcmp(argv[2], "servefail")) {   /* (a second bridge on a name in use: refused) */
        snprintf(sock_path, sizeof sock_path, "%s", argv[3]);
        return hos_control_serve(sock_path, &game) ? 0 : 3;
    }
#endif
    if (argc > 2 && !strcmp(argv[2], "quit")) return quit_case(argv[1]), 0;
    if (argc > 2 && !strcmp(argv[2], "level")) return level_unit(), 0;
    if (argc > 2 && !strcmp(argv[2], "mem")) return mem_case(argv[1]), 0;
    static char line[70000];

#ifndef _WIN32
    /* a file that is not a socket is never replaced */
    snprintf(sock_path, sizeof sock_path, "%s/plain", argv[1]);
    FILE *f = fopen(sock_path, "w");
    fputs("keep", f), fclose(f);
    CHECK(!hos_control_serve(sock_path, &game));
    f = fopen(sock_path, "r");
    CHECK(f && fgets(line, sizeof line, f) && !strcmp(line, "keep"));
    fclose(f);
    puts("PASS: an existing file that is not a socket is left alone");
#endif

    transport_name(argv[1], "game");
    CHECK(hos_control_serve(sock_path, &game));
#ifdef _WIN32
    {   /* the name is this bridge's: a first instance by anyone else is refused, and so is a second bridge */
        HANDLE squat = CreateNamedPipeA(sock_path, PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE, PIPE_TYPE_BYTE, 1, 4096,
                                        4096, 0, NULL);
        CHECK(squat == INVALID_HANDLE_VALUE && GetLastError() == ERROR_ACCESS_DENIED);
        CHECK(wait_exit(spawn_self("servefail", sock_path), 10000) == 3);
        puts("PASS: the pipe's name: another first instance, and a second bridge, refused");
    }
#else
    struct stat st;
    CHECK(!lstat(sock_path, &st) && S_ISSOCK(st.st_mode) && (st.st_mode & 0777) == 0600);
    puts("PASS: the socket is 0600");
#endif
    start_thread(game_thread, NULL);
    Client *c = dial();
#ifdef _WIN32
    dacl_case(c);
#endif

    ask(c, "{\"id\":1,\"cmd\":\"status\"}\n", "1", line, sizeof line, 2000);
    CHECK(strstr(line, "\"ok\":true") && strstr(line, "\"frame\":") && strstr(line, "\"version\":\"test\"") &&
          strstr(line, "\"renderer\":\"fake\"") && strstr(line, "\"window\":{\"width\":1280,\"height\":720}"));
    puts("PASS: status");

    /* malformed requests: an error, and the connection goes on */
    say(c, "{not json\n");
    CHECK(hear(c, line, sizeof line, 2000) && strstr(line, "\"id\":null") && strstr(line, "\"error\":\"bad_json\""));
    ask(c, "{\"id\":\"x\",\"cmd\":\"fly\"}\n", "\"x\"", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"unknown_cmd\""));
    say(c, "[1,2]\n");
    CHECK(hear(c, line, sizeof line, 2000) && strstr(line, "\"error\":\"bad_request\""));
    ask(c, "{\"id\":{\"a\":[1,true]}}\n", "{\"a\":[1,true]}", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"bad_request\""));
    ask(c, "{\"id\":2,\"cmd\":\"press\",\"buttons\":\"A+NOPE\"}\n", "2", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"bad_request\"") && strstr(line, "NOPE"));
    ask(c, "{\"id\":3,\"cmd\":\"press\",\"buttons\":\"A\",\"frames\":0}\n", "3", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"bad_request\""));
    say(c, "\n   \r\n");   /* (empty lines: nothing) */
    ask(c, "{\"id\":\"tab\\t\\u00e9\",\"cmd\":\"status\"}\n", "\"tab\\t\xc3\xa9\"", line, sizeof line, 2000);
    puts("PASS: malformed JSON, unknown commands, bad arguments: errors, the connection kept");

    {   /* a line past 64 KB: refused, and the next request is served */
        static char big[100000];
        memset(big, 'x', sizeof big);
        say_raw(c, big, sizeof big);
        say(c, "\n");
        CHECK(hear(c, line, sizeof line, 2000) && strstr(line, "\"error\":\"too_long\""));
        ask(c, "{\"id\":4,\"cmd\":\"status\"}\n", "4", line, sizeof line, 2000);
        CHECK(strstr(line, "\"ok\":true"));
        puts("PASS: an oversized line is refused, the next one served");
    }

    /* press: held exactly its frames, from the first controller read after the request */
    ask(c, "{\"id\":5,\"cmd\":\"press\",\"buttons\":[\"A\"],\"frames\":5}\n", "5", line, sizeof line, 3000);
    CHECK(strstr(line, "\"ok\":true"));
    {
        const long long s = num_after(line, "\"start\":"), e = num_after(line, "\"end\":");
        CHECK(e == s + 5 && s >= 0);
        CHECK((s == 0 || !(seen_buttons[s - 1] & 1)) && !(seen_buttons[e] & 1));
        for (long long i = s; i < e; i++) CHECK(seen_buttons[i] == 1 && !seen_exclusive[i]);
        CHECK(num_after(line, "\"frame\":") >= e);
    }
    puts("PASS: press A for 5 frames: exactly frames start..end-1");

    ask(c, "{\"id\":6,\"cmd\":\"stick\",\"stick\":\"R\",\"x\":-1,\"y\":0.5,\"frames\":3,\"exclusive\":true}\n", "6", line,
        sizeof line, 3000);
    {
        const long long s = num_after(line, "\"start\":"), e = num_after(line, "\"end\":");
        CHECK(e == s + 3);
        for (long long i = s; i < e; i++)
            CHECK(seen_stick[i][2] == -32767 && seen_stick[i][3] == 16384 && seen_stick[i][0] == 0 && seen_exclusive[i]);
        CHECK(!seen_exclusive[e]);
    }
    puts("PASS: stick R (-1, 0.5) for 3 frames, exclusive while it lasts");

    ask(c, "{\"id\":7,\"cmd\":\"sequence\",\"script\":\"0:A/3,10:B+LX=-16000/2\"}\n", "7", line, sizeof line, 3000);
    {
        const long long s = num_after(line, "\"start\":"), e = num_after(line, "\"end\":");
        CHECK(e == s + 12);
        CHECK(seen_buttons[s] == 1 && seen_buttons[s + 2] == 1 && seen_buttons[s + 3] == 0);
        CHECK(seen_buttons[s + 10] == 2 && seen_stick[s + 10][0] == -16000 && seen_buttons[s + 12] == 0);
    }
    ask(c, "{\"id\":8,\"cmd\":\"sequence\",\"steps\":[{\"at\":0,\"buttons\":\"ZL\",\"frames\":4},{\"at\":2,\"lx\":1,\"frames\":1}]}\n",
        "8", line, sizeof line, 3000);
    {
        const long long s = num_after(line, "\"start\":");
        CHECK(seen_buttons[s] == 1 << 8 && seen_stick[s + 2][0] == 32767 && seen_stick[s + 3][0] == 0);
    }
    ask(c, "{\"id\":81,\"cmd\":\"sequence\",\"script\":\"1:A/100\",\"units\":\"hos\"}\n", "81", line, sizeof line, 3000);
    CHECK(num_after(line, "\"end\":") - num_after(line, "\"start\":") == 6);   /* (100 ms at 60 frames a second, from 1 s on) */
    puts("PASS: sequence: a script in frames, steps, HOS_INPUT units");

    ask(c, "{\"id\":9,\"cmd\":\"press\",\"buttons\":\"X\",\"frames\":50,\"wait\":false}\n", "9", line, sizeof line, 2000);
    CHECK(strstr(line, "\"queued\":1"));
    wait_frames(5);
    ask(c, "{\"id\":10,\"cmd\":\"release\"}\n", "10", line, sizeof line, 2000);
    CHECK(strstr(line, "\"released\":1"));
    wait_frames(3);
    CHECK(seen_buttons[fake_frame() - 1] == 0);
    puts("PASS: press without waiting, and release");

    /* waits */
    ask(c, "{\"id\":11,\"cmd\":\"wait\",\"event\":\"present\",\"frames\":20}\n", "11", line, sizeof line, 3000);
    CHECK(strstr(line, "\"ok\":true") && num_after(line, "\"waited\":") >= 20);
    ask(c, "{\"id\":12,\"cmd\":\"wait\",\"event\":\"in_play\",\"timeout\":0.3}\n", "12", line, sizeof line, 3000);
    CHECK(strstr(line, "\"error\":\"timeout\"") && strstr(line, "\"state\":\"intro\""));
    say(c, "{\"id\":13,\"cmd\":\"wait\",\"event\":\"in_play\",\"timeout\":10}\n");
    draws_per_frame = 40;   /* a menu */
    ask(c, "{\"id\":14,\"cmd\":\"wait\",\"event\":\"state\",\"state\":\"menu\",\"timeout\":5}\n", "14", line, sizeof line, 6000);
    CHECK(strstr(line, "\"ok\":true"));
    draws_per_frame = 900;   /* play */
    CHECK(hear(c, line, sizeof line, 6000) && strstr(line, "\"id\":13") && strstr(line, "\"state\":\"play\""));
    say(c, "{\"id\":15,\"cmd\":\"wait\",\"event\":\"loading_start\",\"timeout\":5}\n");
    say(c, "{\"id\":16,\"cmd\":\"wait\",\"event\":\"loading_end\",\"timeout\":5}\n");
    sleep_ms(100);
    draws_per_frame = 5;   /* loading */
    CHECK(hear(c, line, sizeof line, 6000) && strstr(line, "\"id\":15") && strstr(line, "\"state\":\"loading\""));
    draws_per_frame = 1000;
    CHECK(hear(c, line, sizeof line, 6000) && strstr(line, "\"id\":16") && strstr(line, "\"state\":\"play\""));
    ask(c, "{\"id\":17,\"cmd\":\"wait\",\"event\":\"sunrise\"}\n", "17", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"bad_request\""));
    puts("PASS: wait for frames, states, loading's start and end, with timeouts");

    /* shots */
    {
        char path[1100];
        ask(c, "{\"id\":18,\"cmd\":\"shot\"}\n", "18", line, sizeof line, 3000);
        CHECK(strstr(line, "\"ok\":true"));
        path_of(line, path, sizeof path);
#ifdef _WIN32
        CHECK(strstr(path, "\\shots\\shot_0001_f"));
#else
        CHECK(strstr(path, "/shots/shot_0001_f"));
#endif
        CHECK(file_exists(path));
        const long long target = fake_frame() + 30;
        char req[300];
        snprintf(req, sizeof req, "{\"id\":19,\"cmd\":\"shot\",\"path\":\"named.png\",\"after\":%lld}\n", target);
        ask(c, req, "19", line, sizeof line, 3000);
        CHECK(num_after(line, "\"present\":") > target);
        path_of(line, path, sizeof path);   /* (relative: the run folder's, the socket's own on Linux) */
        CHECK(file_exists(path) && strlen(path) > 9 && !strcmp(path + strlen(path) - 9, "named.png"));
#ifndef _WIN32
        snprintf(req, sizeof req, "%s/named.png", argv[1]);
        CHECK(!strcmp(path, req));
#endif
        ask(c, "{\"id\":20,\"cmd\":\"shot\",\"path\":\"/nonexistent-dir/x.png\"}\n", "20", line, sizeof line, 3000);
        CHECK(strstr(line, "\"error\":\"failed\""));
        puts("PASS: shots: the default folder, a relative path (the run folder), after a frame, an unwritable path");
    }

    /* the Insert menu's settings */
    ask(c, "{\"id\":21,\"cmd\":\"option_get\"}\n", "21", line, sizeof line, 2000);
    CHECK(strstr(line, "\"options\":[") && strstr(line, "\"key\":\"text_speed\"") && strstr(line, "\"key\":\"fps_counter\""));
    ask(c, "{\"id\":22,\"cmd\":\"option_set\",\"name\":\"Text speed\",\"value\":9}\n", "22", line, sizeof line, 2000);
    CHECK(strstr(line, "\"was\":0") && strstr(line, "\"value\":3") && strstr(line, "\"text\":\"v3\""));
    ask(c, "{\"id\":23,\"cmd\":\"option_set\",\"name\":\"fps-counter\",\"value\":true}\n", "23", line, sizeof line, 2000);
    CHECK(strstr(line, "\"value\":1"));
    ask(c, "{\"id\":24,\"cmd\":\"option_get\",\"name\":\"text_speed\"}\n", "24", line, sizeof line, 2000);
    CHECK(strstr(line, "\"option\":{\"key\":\"text_speed\"") && strstr(line, "\"value\":3"));
    ask(c, "{\"id\":25,\"cmd\":\"option_set\",\"name\":\"nope\",\"value\":1}\n", "25", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"not_found\""));
    puts("PASS: option_get and option_set by key or name");

    ask(c, "{\"id\":26,\"cmd\":\"mem_read\",\"addr\":0,\"size\":4}\n", "26", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"forbidden\""));   /* (this run has no HOS_CONTROL_MEM) */
    ask(c, "{\"id\":261,\"cmd\":\"symbols\"}\n", "261", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"unavailable\""));
    puts("PASS: memory off without HOS_CONTROL_MEM; symbols not served");

    /* subscribe: presents, state changes, log lines with a filter */
    {
        Client *sub = dial();
        ask(sub, "{\"id\":\"s\",\"cmd\":\"subscribe\",\"events\":[\"present\",\"state\",\"log\"],\"every\":10,\"log\":\"needle\"}\n",
            "\"s\"", line, sizeof line, 2000);
        CHECK(strstr(line, "\"events\":[\"present\",\"state\",\"log\"]"));
        int presents = 0, logs = 0, states = 0;
        game.log("a haystack line");
        game.log("a needle line");
        draws_per_frame = 30;
        for (int i = 0; i < 400 && (presents < 3 || !logs || !states); i++) {
            if (!hear(sub, line, sizeof line, 50)) continue;
            CHECK(strstr(line, "\"sub\":\"s\""));
            presents += strstr(line, "\"event\":\"present\"") != NULL;
            if (strstr(line, "\"event\":\"log\"")) {
                CHECK(strstr(line, "needle") && !strstr(line, "haystack"));
                logs++;
            }
            states += strstr(line, "\"event\":\"state\"") && strstr(line, "\"state\":\"menu\"");
        }
        CHECK(presents >= 3 && logs == 1 && states >= 1);
        ask(sub, "{\"id\":\"u\",\"cmd\":\"unsubscribe\"}\n", "\"u\"", line, sizeof line, 2000);
        hangup(sub);
        puts("PASS: subscribe: presents, state changes, filtered log lines; unsubscribe");
    }

    /* a client gone in the middle of a press: its input is let go */
    {
        Client *gone = dial();
        say(gone, "{\"id\":1,\"cmd\":\"press\",\"buttons\":\"Y\",\"frames\":100000}\n");
        wait_frames(10);
        CHECK(seen_buttons[fake_frame() - 2] == 1 << 3);
        hangup(gone);
        wait_frames(20);
        CHECK(seen_buttons[fake_frame() - 2] == 0);
        ask(c, "{\"id\":27,\"cmd\":\"status\"}\n", "27", line, sizeof line, 2000);
        CHECK(strstr(line, "\"queued\":0"));
        puts("PASS: a client that disconnects mid-press: the press is let go, the bridge goes on");
    }

    /* a client that sends half a request and goes; too many clients */
    {
        Client *half = dial();
        say(half, "{\"id\":1,\"cmd\":\"sta");
        hangup(half);
        sleep_ms(50);
        Client *many[12];
        int busy = 0;
        for (int i = 0; i < 12; i++) many[i] = dial();
        for (int i = 0; i < 12; i++) {
            char got[512] = "";
            if (wait_readable(many[i], 300) > 0) {
                const long r = read_some(many[i], got, sizeof got - 1);
                if (r > 0) got[r] = 0;
            }
            busy += strstr(got, "too many clients") != NULL;
        }
        CHECK(busy >= 4);
        for (int i = 0; i < 12; i++) hangup(many[i]);
        sleep_ms(50);
        ask(c, "{\"id\":28,\"cmd\":\"status\"}\n", "28", line, sizeof line, 2000);
        CHECK(strstr(line, "\"ok\":true"));
        puts("PASS: a half request then a disconnect, and too many clients: the bridge goes on");
    }
    /* the game never waits for a silent client: one that reads nothing while presents stream to it */
    {
        Client *deaf = dial();
        say(deaf, "{\"id\":1,\"cmd\":\"subscribe\",\"every\":1,\"events\":[\"present\"]}\n");
        const int64_t f0 = fake_frame();
        wait_frames(300);
        CHECK(fake_frame() - f0 >= 300);
        hangup(deaf);
        puts("PASS: a client that never reads does not hold the game");
    }
    paused = 1;
    return 0;
}
