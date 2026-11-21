"""A game started from its icon writes its log to hookshot_log.txt beside it (host/hos_main.c, game_main): stderr is opened
again on that file. When the file cannot be opened (a log left by a run as root, a read-only game folder),
the game keeps the stderr it was started with: the C library's freopen closes stderr when its open fails, and the
next file the game opened (the guest memory's memfd) then took its descriptor, 2.

Steam's Gaming mode hands a non-Steam game its own log pipe on stderr (measured on Deck hardware 2026-11-17: fd/2 is
an anonymous FIFO drained into Steam's console-linux.txt, a sink the player cannot see). That is not an stderr the
player controls: the game writes its own log anyway, keyed on Steam's own environment markers — the case the
steam-pipe and shell-pipe runs check here (Longshot #1259).

The code is cut out of hos_main.c and run with stubs for the Windows calls it makes. The log's path is made unopenable
by a file where the game's folder would be, which no user, root included, can open a file under; the fixture checks
that before it runs the code. A second run with a real folder checks that the log is opened there at all.

python -I -B tests/test_log_open_failure.py [--cc gcc]
"""
import argparse
import os
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_SKIP = {"windows": "the fixture checks POSIX file descriptors: the code is the same on Windows"}

ROOT = Path(__file__).resolve().parents[1]

START = "HANDLE err = GetStdHandle(STD_ERROR_HANDLE);"
END = """if (freopen(path, "w", stderr)) setvbuf(stderr, NULL, _IONBF, 0);
            }
        }"""

FIXTURE = r'''#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#define MAX_PATH 4096
typedef unsigned long DWORD;
typedef void *HANDLE;
#define STD_ERROR_HANDLE ((DWORD)-12)
#define INVALID_HANDLE_VALUE ((HANDLE)-1)
#define FILE_TYPE_UNKNOWN 0
#define INVALID_FILE_ATTRIBUTES ((DWORD)-1)
#define MOVEFILE_REPLACE_EXISTING 1
#define CHECK(x) do { if (!(x)) { printf("FAIL: %s (%s)\n", #x, why); return 1; } } while (0)
static const char *game_exe;
static int startup_stderr = -1;
static int file_type = FILE_TYPE_UNKNOWN;   /* (what the run says stderr is: an icon run's /dev/null, or Steam's log pipe) */
#define FILE_TYPE_PIPE 3
/* (the block reads argv for --prepare-shaders: the fixture hands it a played game by default (argc 1: no build-step
   flag), or the builder's command line for the steam-prepare case) */
static char arg0[] = "hookshot", argp[] = "--prepare-shaders";
static int argc = 1;
static char *argv[3] = {arg0, argp, NULL};
static HANDLE GetStdHandle(DWORD which) { (void)which; return (HANDLE)&startup_stderr; }
static DWORD GetFileType(HANDLE h) { (void)h; return (DWORD)file_type; }
static DWORD hos_game_path_a(char *out, DWORD size) { snprintf(out, size, "%s", game_exe); return (DWORD)strlen(out); }
static int MoveFileExA(const char *from, const char *to, DWORD flags) { (void)flags; return rename(from, to) == 0; }
static void open_log(void) {
''' + "{body}" + r'''
}
int main(int real_argc, char **real_argv) {
    const char *why = "";
    (void)argc; (void)argv;   /* (used by the cut block when it scans for --prepare-shaders; keeps the fixture compiling against any revision of the block) */
    game_exe = real_argv[1];
    const char *mode = real_argv[2];
    const int unopenable = !strcmp(mode, "unopenable");
    const int steampipe = !strcmp(mode, "steam-pipe");
    const int shellpipe = !strcmp(mode, "shell-pipe");
    const int steamtty = !strcmp(mode, "steam-tty");
    const int steamprep = !strcmp(mode, "steam-prepare");
    if (steamprep) argc = 2;   /* (the builder's command line: this run carries --prepare-shaders) */
    char log[MAX_PATH];
    snprintf(log, sizeof log, "%s", game_exe);
    *strrchr(log, '/') = 0;
    strcat(log, "/hookshot_log.txt");
    if (steampipe || shellpipe || steamtty || steamprep) {
        /* (stderr as a pipe, like Steam's srt-logger FIFO or a shell's `game | tee`. Steam sets its marker variable in
           everything it starts; the game's own log replaces an invisible Steam pipe (Longshot #1259) and leaves a chosen pipe
           alone. stdin tells Steam's seat from a developer's session holding Steam's markers in a terminal: Steam gives
           its started programs /dev/null on stdin (measured on Deck hardware), a terminal session has a tty there) */
        int fds[2];
        why = "pipe"; CHECK(pipe(fds) == 0);
        why = "stderr is the pipe"; CHECK(dup2(fds[1], 2) == 2 && close(fds[1]) == 0);
        file_type = FILE_TYPE_PIPE;
        if (steamtty) {
            const int m = posix_openpt(O_RDWR | O_NOCTTY);
            char *sl = m >= 0 ? ptsname(m) : NULL;
            why = "pty for the developer-session stdin"; CHECK(m >= 0 && sl && grantpt(m) == 0 && unlockpt(m) == 0);
            const int s = open(sl, O_RDWR);
            why = "stdin is the pty"; CHECK(s >= 0 && dup2(s, 0) == 0);
        } else {
            const int nul0 = open("/dev/null", O_RDONLY);   /* (Steam's stdin for what it starts) */
            why = "stdin is /dev/null"; CHECK(nul0 >= 0 && dup2(nul0, 0) == 0 && close(nul0) == 0);
        }
        if (steampipe || steamtty || steamprep) setenv("SteamGameId", "14308674106319437824", 1);   /* (a real non-Steam tile id from the Deck) */
    } else {
        const int null = open("/dev/null", O_WRONLY);   /* (stderr as a desktop launcher leaves it) */
        why = "set up";
        CHECK(null >= 0 && dup2(null, 2) == 2 && close(null) == 0);
    }
    if (unopenable) {
        FILE *f = fopen(log, "w");
        why = "the log must not be openable for this case";
        CHECK(!f && errno == ENOTDIR);
    }
    open_log();
    fprintf(stderr, "the line after\n");
    fflush(stderr);
    if (unopenable) {
        why = "stderr was closed: the next file the game opens takes descriptor 2";
        CHECK(fcntl(2, F_GETFD) != -1);
        const int next = open("/dev/null", O_RDONLY);
        CHECK(next != 2);
        printf("PASS: an unopenable log leaves the stderr the game was started with\n");
    } else if (steampipe || shellpipe || steamtty || steamprep) {
        FILE *f = fopen(log, "r");
        char line[64] = "";
        struct stat st;
        if (steampipe) {
            why = "Steam's log pipe is not an stderr the player can see: the game must write its own log anyway";
            CHECK(f && fgets(line, sizeof line, f) && !strcmp(line, "the line after\n"));
            fclose(f);
            why = "and stderr is the log file now";
            CHECK(fstat(2, &st) == 0 && S_ISREG(st.st_mode));
            printf("PASS: a Steam gaming-mode log pipe still gets the game's own hookshot_log.txt\n");
        } else {
            why = (shellpipe ? "a pipe the player chose (game | tee) is an external stderr: no file, stderr still the pipe"
                             : steamtty ? "Steam's markers in a developer's own terminal session (stdin a tty): stream, no surprise file"
                                        : "the builder's --prepare-shaders step under Steam's markers: its merged stdout/stderr pipe is read line by line by make_game.py: no file, stderr still the pipe");
            CHECK(!f);
            CHECK(fstat(2, &st) == 0 && S_ISFIFO(st.st_mode));
            printf("PASS: %s\n", shellpipe ? "a shell-piped stderr is left exactly as the player made it"
                                           : steamtty ? "a Steam-marked terminal session keeps its live stream and writes no file"
                                                      : "a Steam-marked --prepare-shaders build step keeps its pipe: the builder still reads its stderr");
        }
    } else {
        FILE *f = fopen(log, "r");
        char line[64] = "";
        why = "the log was not opened beside the game";
        CHECK(f && fgets(line, sizeof line, f) && !strcmp(line, "the line after\n"));
        printf("PASS: the log is opened beside the game\n");
    }
    return 0;
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    a = p.parse_args()
    text = (ROOT / "host" / "hos_main.c").read_text(encoding="utf-8")
    start = text.index(START)
    end = text.index(END, start) + len(END)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "fixture.c"
        src.write_text(FIXTURE.replace("{body}", text[start:end]), encoding="utf-8")
        exe = tmp / "fixture"
        subprocess.run([a.cc, "-std=c11", "-D_XOPEN_SOURCE=700", "-Wall", "-Werror", str(src), "-o", str(exe)],
                       check=True)
        failed = 0
        base_env = {k: v for k, v in os.environ.items() if k not in ("SteamGameId", "SteamAppId")}   # (a dev machine with Steam open: the fixture sets the marker per case itself)
        for case in ("openable", "unopenable", "steam-pipe", "shell-pipe", "steam-tty", "steam-prepare"):
            game = tmp / case
            if case != "unopenable":
                game.mkdir()
            else:
                game.write_text("a file where the game's folder would be\n", encoding="utf-8")
            r = subprocess.run([str(exe), str(game / "hookshot"), case], stdout=subprocess.PIPE, text=True, env=base_env)
            print(r.stdout.strip())
            failed |= r.returncode != 0
        raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
