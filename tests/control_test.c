/* The control bridge (host/control.c, built with HOS_CONTROL_NO_GAME) against a fake game, over a real Unix socket:
   a thread stands in for the game (a controller read, then a present, every 2 ms; draws a frame as a case sets them),
   and the test is the client. tests/test_control.py builds and runs it.

   control_test <folder> [quit]: the cases, or (quit) a child serving while the parent asks it to quit */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "../host/control.h"

#include <errno.h>
#include <poll.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); exit(1); } } while (0)

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
static pthread_mutex_t log_mu = PTHREAD_MUTEX_INITIALIZER;

static int64_t fake_frame(void) { return __atomic_load_n(&frame, __ATOMIC_ACQUIRE); }
static int fake_frame_info(int64_t *replayed, int64_t *draws, int *w, int *h) {
    *replayed = fake_frame(), *draws = draws_total, *w = 1280, *h = 720;
    return 1;
}
typedef struct { char path[1100]; void (*done)(void *, int64_t, int); void *ctx; int64_t after; } Shot;
static void *shot_thread(void *p) {
    Shot *s = (Shot *)p;
    while (fake_frame() <= s->after) usleep(1000);
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
    pthread_t t;
    pthread_create(&t, NULL, shot_thread, s);
    pthread_detach(t);
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
    pthread_mutex_lock(&log_mu);
    snprintf(logged[nlogged++ % 64], sizeof logged[0], "%s", line);
    pthread_mutex_unlock(&log_mu);
    hos_control_log(line);   /* (as hos_log does) */
}
static void fake_quit(void) { _exit(0); }
static const char *fake_renderer(void) { return "fake"; }
static HosControlGame game = {fake_frame, fake_frame_info, fake_shot, fake_setting, fake_setting_set, fake_log, fake_quit,
                              fake_renderer, "test"};

static void *game_thread(void *unused) {
    (void)unused;
    for (;;) {
        usleep(2000);
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

/* ---- the client ---- */
static char sock_path[100];
static char pends[64][1 << 16];   /* (what came after the last line, by descriptor) */
static size_t pend_ns[64];
static int dial(void) {
    const int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 64) pend_ns[fd] = 0;
    struct sockaddr_un a = {0};
    a.sun_family = AF_UNIX;
    snprintf(a.sun_path, sizeof a.sun_path, "%s", sock_path);
    CHECK(connect(fd, (struct sockaddr *)&a, sizeof a) == 0);
    return fd;
}
static void say(int fd, const char *s) { CHECK(write(fd, s, strlen(s)) == (ssize_t)strlen(s)); }
/* one line (without the newline), waiting ms at most: 0 when none came */
static int hear(int fd, char *out, size_t cap, int ms) {
    CHECK(fd < 64);
    char *pend = pends[fd];
    size_t pend_n = pend_ns[fd];
    for (;;) {
        char *nl = memchr(pend, '\n', pend_n);
        if (nl) {
            const size_t n = (size_t)(nl - pend);
            CHECK(n < cap);
            memcpy(out, pend, n);
            out[n] = 0;
            memmove(pend, nl + 1, pend_n - n - 1);
            pend_ns[fd] = pend_n - n - 1;
            return 1;
        }
        struct pollfd p = {fd, POLLIN, 0};
        if (poll(&p, 1, ms) <= 0) return pend_ns[fd] = pend_n, 0;
        const ssize_t r = read(fd, pend + pend_n, sizeof pends[0] - pend_n);
        if (r <= 0) return pend_ns[fd] = pend_n, 0;
        pend_n += (size_t)r;
    }
}
/* the reply with this id (events and other replies skipped) */
static void ask(int fd, const char *req, const char *id_text, char *out, size_t cap, int ms) {
    say(fd, req);
    char want[64];
    snprintf(want, sizeof want, "{\"id\":%s,", id_text);
    for (;;) {
        CHECK(hear(fd, out, cap, ms));
        if (!strncmp(out, want, strlen(want))) return;
    }
}
static long long num_after(const char *s, const char *key) {
    const char *p = strstr(s, key);
    CHECK(p);
    return atoll(p + strlen(key));
}
static void wait_frames(int64_t n) { const int64_t to = fake_frame() + n; while (fake_frame() < to) usleep(1000); }

static void quit_case(const char *folder) {
    snprintf(sock_path, sizeof sock_path, "%s/quit.sock", folder);
    const pid_t child = fork();
    if (child == 0) {
        if (!hos_control_serve(sock_path, &game)) _exit(3);
        pthread_t t;
        pthread_create(&t, NULL, game_thread, NULL);
        for (;;) pause();
    }
    struct stat st;
    for (int i = 0; i < 500 && stat(sock_path, &st); i++) usleep(2000);
    const int fd = dial();
    char line[4096];
    ask(fd, "{\"id\":9,\"cmd\":\"quit\"}\n", "9", line, sizeof line, 2000);
    CHECK(strstr(line, "\"ok\":true") && strstr(line, "\"quitting\":true"));
    int status = 0;
    CHECK(waitpid(child, &status, 0) == child);
    CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    CHECK(stat(sock_path, &st) != 0);   /* (the socket removed) */
    puts("PASS: quit: the reply, then a clean exit, the socket removed");
}

int main(int argc, char **argv) {
    CHECK(argc >= 2);
    if (argc > 2 && !strcmp(argv[2], "quit")) return quit_case(argv[1]), 0;
    char line[70000];

    /* a file that is not a socket is never replaced */
    snprintf(sock_path, sizeof sock_path, "%s/plain", argv[1]);
    FILE *f = fopen(sock_path, "w");
    fputs("keep", f), fclose(f);
    CHECK(!hos_control_serve(sock_path, &game));
    f = fopen(sock_path, "r");
    CHECK(f && fgets(line, sizeof line, f) && !strcmp(line, "keep"));
    fclose(f);
    puts("PASS: an existing file that is not a socket is left alone");

    snprintf(sock_path, sizeof sock_path, "%s/game.sock", argv[1]);
    CHECK(hos_control_serve(sock_path, &game));
    struct stat st;
    CHECK(!lstat(sock_path, &st) && S_ISSOCK(st.st_mode) && (st.st_mode & 0777) == 0600);
    puts("PASS: the socket is 0600");
    pthread_t t;
    pthread_create(&t, NULL, game_thread, NULL);
    const int fd = dial();

    ask(fd, "{\"id\":1,\"cmd\":\"status\"}\n", "1", line, sizeof line, 2000);
    CHECK(strstr(line, "\"ok\":true") && strstr(line, "\"frame\":") && strstr(line, "\"version\":\"test\"") &&
          strstr(line, "\"renderer\":\"fake\"") && strstr(line, "\"window\":{\"width\":1280,\"height\":720}"));
    puts("PASS: status");

    /* malformed requests: an error, and the connection goes on */
    say(fd, "{not json\n");
    CHECK(hear(fd, line, sizeof line, 2000) && strstr(line, "\"id\":null") && strstr(line, "\"error\":\"bad_json\""));
    ask(fd, "{\"id\":\"x\",\"cmd\":\"fly\"}\n", "\"x\"", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"unknown_cmd\""));
    say(fd, "[1,2]\n");
    CHECK(hear(fd, line, sizeof line, 2000) && strstr(line, "\"error\":\"bad_request\""));
    ask(fd, "{\"id\":{\"a\":[1,true]}}\n", "{\"a\":[1,true]}", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"bad_request\""));
    ask(fd, "{\"id\":2,\"cmd\":\"press\",\"buttons\":\"A+NOPE\"}\n", "2", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"bad_request\"") && strstr(line, "NOPE"));
    ask(fd, "{\"id\":3,\"cmd\":\"press\",\"buttons\":\"A\",\"frames\":0}\n", "3", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"bad_request\""));
    say(fd, "\n   \r\n");   /* (empty lines: nothing) */
    ask(fd, "{\"id\":\"tab\\t\\u00e9\",\"cmd\":\"status\"}\n", "\"tab\\t\xc3\xa9\"", line, sizeof line, 2000);
    puts("PASS: malformed JSON, unknown commands, bad arguments: errors, the connection kept");

    {   /* a line past 64 KB: refused, and the next request is served */
        static char big[100000];
        memset(big, 'x', sizeof big);
        CHECK(write(fd, big, sizeof big) == (ssize_t)sizeof big);
        say(fd, "\n");
        CHECK(hear(fd, line, sizeof line, 2000) && strstr(line, "\"error\":\"too_long\""));
        ask(fd, "{\"id\":4,\"cmd\":\"status\"}\n", "4", line, sizeof line, 2000);
        CHECK(strstr(line, "\"ok\":true"));
        puts("PASS: an oversized line is refused, the next one served");
    }

    /* press: held exactly its frames, from the first controller read after the request */
    ask(fd, "{\"id\":5,\"cmd\":\"press\",\"buttons\":[\"A\"],\"frames\":5}\n", "5", line, sizeof line, 3000);
    CHECK(strstr(line, "\"ok\":true"));
    {
        const long long s = num_after(line, "\"start\":"), e = num_after(line, "\"end\":");
        CHECK(e == s + 5 && s >= 0);
        CHECK((s == 0 || !(seen_buttons[s - 1] & 1)) && !(seen_buttons[e] & 1));
        for (long long i = s; i < e; i++) CHECK(seen_buttons[i] == 1 && !seen_exclusive[i]);
        CHECK(num_after(line, "\"frame\":") >= e);
    }
    puts("PASS: press A for 5 frames: exactly frames start..end-1");

    ask(fd, "{\"id\":6,\"cmd\":\"stick\",\"stick\":\"R\",\"x\":-1,\"y\":0.5,\"frames\":3,\"exclusive\":true}\n", "6", line,
        sizeof line, 3000);
    {
        const long long s = num_after(line, "\"start\":"), e = num_after(line, "\"end\":");
        CHECK(e == s + 3);
        for (long long i = s; i < e; i++)
            CHECK(seen_stick[i][2] == -32767 && seen_stick[i][3] == 16384 && seen_stick[i][0] == 0 && seen_exclusive[i]);
        CHECK(!seen_exclusive[e]);
    }
    puts("PASS: stick R (-1, 0.5) for 3 frames, exclusive while it lasts");

    ask(fd, "{\"id\":7,\"cmd\":\"sequence\",\"script\":\"0:A/3,10:B+LX=-16000/2\"}\n", "7", line, sizeof line, 3000);
    {
        const long long s = num_after(line, "\"start\":"), e = num_after(line, "\"end\":");
        CHECK(e == s + 12);
        CHECK(seen_buttons[s] == 1 && seen_buttons[s + 2] == 1 && seen_buttons[s + 3] == 0);
        CHECK(seen_buttons[s + 10] == 2 && seen_stick[s + 10][0] == -16000 && seen_buttons[s + 12] == 0);
    }
    ask(fd, "{\"id\":8,\"cmd\":\"sequence\",\"steps\":[{\"at\":0,\"buttons\":\"ZL\",\"frames\":4},{\"at\":2,\"lx\":1,\"frames\":1}]}\n",
        "8", line, sizeof line, 3000);
    {
        const long long s = num_after(line, "\"start\":");
        CHECK(seen_buttons[s] == 1 << 8 && seen_stick[s + 2][0] == 32767 && seen_stick[s + 3][0] == 0);
    }
    ask(fd, "{\"id\":81,\"cmd\":\"sequence\",\"script\":\"1:A/100\",\"units\":\"hos\"}\n", "81", line, sizeof line, 3000);
    CHECK(num_after(line, "\"end\":") - num_after(line, "\"start\":") == 6);   /* (100 ms at 60 frames a second, from 1 s on) */
    puts("PASS: sequence: a script in frames, steps, HOS_INPUT units");

    ask(fd, "{\"id\":9,\"cmd\":\"press\",\"buttons\":\"X\",\"frames\":50,\"wait\":false}\n", "9", line, sizeof line, 2000);
    CHECK(strstr(line, "\"queued\":1"));
    wait_frames(5);
    ask(fd, "{\"id\":10,\"cmd\":\"release\"}\n", "10", line, sizeof line, 2000);
    CHECK(strstr(line, "\"released\":1"));
    wait_frames(3);
    CHECK(seen_buttons[fake_frame() - 1] == 0);
    puts("PASS: press without waiting, and release");

    /* waits */
    ask(fd, "{\"id\":11,\"cmd\":\"wait\",\"event\":\"present\",\"frames\":20}\n", "11", line, sizeof line, 3000);
    CHECK(strstr(line, "\"ok\":true") && num_after(line, "\"waited\":") >= 20);
    ask(fd, "{\"id\":12,\"cmd\":\"wait\",\"event\":\"in_play\",\"timeout\":0.3}\n", "12", line, sizeof line, 3000);
    CHECK(strstr(line, "\"error\":\"timeout\"") && strstr(line, "\"state\":\"intro\""));
    say(fd, "{\"id\":13,\"cmd\":\"wait\",\"event\":\"in_play\",\"timeout\":10}\n");
    draws_per_frame = 40;   /* a menu */
    ask(fd, "{\"id\":14,\"cmd\":\"wait\",\"event\":\"state\",\"state\":\"menu\",\"timeout\":5}\n", "14", line, sizeof line, 6000);
    CHECK(strstr(line, "\"ok\":true"));
    draws_per_frame = 900;   /* play */
    CHECK(hear(fd, line, sizeof line, 6000) && strstr(line, "\"id\":13") && strstr(line, "\"state\":\"play\""));
    say(fd, "{\"id\":15,\"cmd\":\"wait\",\"event\":\"loading_start\",\"timeout\":5}\n");
    say(fd, "{\"id\":16,\"cmd\":\"wait\",\"event\":\"loading_end\",\"timeout\":5}\n");
    usleep(100000);
    draws_per_frame = 5;   /* loading */
    CHECK(hear(fd, line, sizeof line, 6000) && strstr(line, "\"id\":15") && strstr(line, "\"state\":\"loading\""));
    draws_per_frame = 1000;
    CHECK(hear(fd, line, sizeof line, 6000) && strstr(line, "\"id\":16") && strstr(line, "\"state\":\"play\""));
    ask(fd, "{\"id\":17,\"cmd\":\"wait\",\"event\":\"sunrise\"}\n", "17", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"bad_request\""));
    puts("PASS: wait for frames, states, loading's start and end, with timeouts");

    /* shots */
    ask(fd, "{\"id\":18,\"cmd\":\"shot\"}\n", "18", line, sizeof line, 3000);
    CHECK(strstr(line, "\"ok\":true") && strstr(line, "/shots/shot_0001_f"));
    {
        const char *p = strstr(line, "\"path\":\"") + 8;
        char path[1100];
        snprintf(path, sizeof path, "%.*s", (int)(strchr(p, '"') - p), p);
        CHECK(!stat(path, &st));
    }
    const long long target = fake_frame() + 30;
    char req[300];
    snprintf(req, sizeof req, "{\"id\":19,\"cmd\":\"shot\",\"path\":\"named.png\",\"after\":%lld}\n", target);
    ask(fd, req, "19", line, sizeof line, 3000);
    CHECK(num_after(line, "\"present\":") > target);
    snprintf(req, sizeof req, "%s/named.png", argv[1]);
    CHECK(!stat(req, &st));
    ask(fd, "{\"id\":20,\"cmd\":\"shot\",\"path\":\"/nonexistent-dir/x.png\"}\n", "20", line, sizeof line, 3000);
    CHECK(strstr(line, "\"error\":\"failed\""));
    puts("PASS: shots: the default folder, a relative path (the socket's folder), after a frame, an unwritable path");

    /* the Insert menu's settings */
    ask(fd, "{\"id\":21,\"cmd\":\"option_get\"}\n", "21", line, sizeof line, 2000);
    CHECK(strstr(line, "\"options\":[") && strstr(line, "\"key\":\"text_speed\"") && strstr(line, "\"key\":\"fps_counter\""));
    ask(fd, "{\"id\":22,\"cmd\":\"option_set\",\"name\":\"Text speed\",\"value\":9}\n", "22", line, sizeof line, 2000);
    CHECK(strstr(line, "\"was\":0") && strstr(line, "\"value\":3") && strstr(line, "\"text\":\"v3\""));
    ask(fd, "{\"id\":23,\"cmd\":\"option_set\",\"name\":\"fps-counter\",\"value\":true}\n", "23", line, sizeof line, 2000);
    CHECK(strstr(line, "\"value\":1"));
    ask(fd, "{\"id\":24,\"cmd\":\"option_get\",\"name\":\"text_speed\"}\n", "24", line, sizeof line, 2000);
    CHECK(strstr(line, "\"option\":{\"key\":\"text_speed\"") && strstr(line, "\"value\":3"));
    ask(fd, "{\"id\":25,\"cmd\":\"option_set\",\"name\":\"nope\",\"value\":1}\n", "25", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"not_found\""));
    puts("PASS: option_get and option_set by key or name");

    ask(fd, "{\"id\":26,\"cmd\":\"mem_read\",\"addr\":0,\"size\":4}\n", "26", line, sizeof line, 2000);
    CHECK(strstr(line, "\"error\":\"unavailable\""));
    puts("PASS: guest memory is not served");

    /* subscribe: presents, state changes, log lines with a filter */
    {
        const int sub = dial();
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
        close(sub);
        puts("PASS: subscribe: presents, state changes, filtered log lines; unsubscribe");
    }

    /* a client gone in the middle of a press: its input is let go */
    {
        const int gone = dial();
        say(gone, "{\"id\":1,\"cmd\":\"press\",\"buttons\":\"Y\",\"frames\":100000}\n");
        wait_frames(10);
        CHECK(seen_buttons[fake_frame() - 2] == 1 << 3);
        close(gone);
        wait_frames(20);
        CHECK(seen_buttons[fake_frame() - 2] == 0);
        ask(fd, "{\"id\":27,\"cmd\":\"status\"}\n", "27", line, sizeof line, 2000);
        CHECK(strstr(line, "\"queued\":0"));
        puts("PASS: a client that disconnects mid-press: the press is let go, the bridge goes on");
    }

    /* a client that sends half a request and goes; too many clients */
    {
        const int half = dial();
        say(half, "{\"id\":1,\"cmd\":\"sta");
        close(half);
        int many[12], busy = 0;
        for (int i = 0; i < 12; i++) many[i] = dial();
        for (int i = 0; i < 12; i++) {
            struct pollfd p = {many[i], POLLIN, 0};
            if (poll(&p, 1, 200) > 0 && read(many[i], line, sizeof line - 1) > 0 && strstr(line, "too many clients")) busy++;
        }
        CHECK(busy >= 4);
        for (int i = 0; i < 12; i++) close(many[i]);
        usleep(50000);
        ask(fd, "{\"id\":28,\"cmd\":\"status\"}\n", "28", line, sizeof line, 2000);
        CHECK(strstr(line, "\"ok\":true"));
        puts("PASS: a half request then a disconnect, and too many clients: the bridge goes on");
    }
    /* the game never waits for a silent client: one that reads nothing while presents stream to it */
    {
        const int deaf = dial();
        say(deaf, "{\"id\":1,\"cmd\":\"subscribe\",\"every\":1,\"events\":[\"present\"]}\n");
        const int64_t f0 = fake_frame();
        wait_frames(300);
        CHECK(fake_frame() - f0 >= 300);
        close(deaf);
        puts("PASS: a client that never reads does not hold the game");
    }
    paused = 1;
    return 0;
}
