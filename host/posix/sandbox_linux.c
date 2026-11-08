/* (linux) The game process in a Landlock sandbox: what it may open on disk, and that it opens no TCP connection.

   The game's code reads data from outside (a save from someone else, above all). Should a crafted file ever take the
   process over, the sandbox keeps it from the player's other files: it reads the system's libraries and settings, its own
   folder, the GPU's and the input devices, and writes only its saves, its own state files, the drivers' shader caches and
   the temporary folder. Unix sockets: only the display's (Wayland, X11) and the sound server's (PipeWire, PulseAudio).

   It is applied at start, before the window's renderer and before any save is read, from the game's thread; with
   Landlock ABI 8 (Linux 6.18) to every thread of the process at once (the window's main thread was started first),
   before that to the game's thread and all it starts (the renderer, sound, the game's own threads). A kernel without
   Landlock: the game runs as before, said once in the log. HOS_SANDBOX=0 turns it off.

   For tests and debugging: HOS_SANDBOX_RW=<path>[:<path>...] adds folders or files the game may write;
   HOS_SANDBOX_PROBE=<path>[:<path>...] tries to read, write and create beside each path once the sandbox is on and
   logs the answers. The test
   harness's own paths (RX_VK_SHOTS, RX_VK_FRAMELOG's folder, HOS_INPUT_LIVE, HOS_ENV_FILE) are allowed by themselves.

   No library is needed: the three system calls, by number (the kernel's uapi header for the constants). */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE   /* (O_PATH) */
#endif
#include <errno.h>
#include <fcntl.h>
#include <linux/landlock.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <dirent.h>

void hos_log(const char *fmt, ...);

#ifndef __NR_landlock_create_ruleset
#define __NR_landlock_create_ruleset 444
#define __NR_landlock_add_rule 445
#define __NR_landlock_restrict_self 446
#endif

/* (rights newer than the build machine's header: their bits, from the kernel's) */
#define LL_REFER (1ULL << 13)
#define LL_TRUNCATE (1ULL << 14)
#define LL_IOCTL_DEV (1ULL << 15)
#define LL_RESOLVE_UNIX (1ULL << 16)
#define LL_TSYNC (1U << 3)
#define LL_SCOPE_ABSTRACT (1ULL << 0)
#define LL_SCOPE_SIGNAL (1ULL << 1)

#define R_READ (LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_READ_DIR)
#define R_EXEC LANDLOCK_ACCESS_FS_EXECUTE
#define R_FILE_WRITE (LANDLOCK_ACCESS_FS_WRITE_FILE | LL_TRUNCATE)
#define R_TREE_WRITE (R_FILE_WRITE | LANDLOCK_ACCESS_FS_REMOVE_DIR | LANDLOCK_ACCESS_FS_REMOVE_FILE | \
                      LANDLOCK_ACCESS_FS_MAKE_DIR | LANDLOCK_ACCESS_FS_MAKE_REG | LL_REFER)
/* (what a rule on a file, not a folder, may hold) */
#define R_FILE_ONLY (LANDLOCK_ACCESS_FS_EXECUTE | LANDLOCK_ACCESS_FS_WRITE_FILE | LANDLOCK_ACCESS_FS_READ_FILE | \
                     LL_TRUNCATE | LL_IOCTL_DEV | LL_RESOLVE_UNIX)

static int ruleset = -1;
static uint64_t handled;
static int nrules;
static char summary[4096];

static void note(const char *what, const char *path) {
    const size_t n = strlen(summary);
    if (n + strlen(path) + 16 < sizeof summary) snprintf(summary + n, sizeof summary - n, " %s:%s", what, path);
}

/* one path (missing ones are skipped), with the rights this kernel knows */
static void allow(const char *path, uint64_t rights, const char *what) {
    if (!path || !*path) return;
    const int fd = open(path, O_PATH | O_CLOEXEC);
    if (fd < 0) return;
    struct stat st;
    if (fstat(fd, &st) == 0 && !S_ISDIR(st.st_mode)) rights &= R_FILE_ONLY;
    struct landlock_path_beneath_attr pb = {.allowed_access = rights & handled, .parent_fd = fd};
    if (pb.allowed_access && syscall(__NR_landlock_add_rule, ruleset, LANDLOCK_RULE_PATH_BENEATH, &pb, 0) == 0) {
        nrules++;
        if (what) note(what, path);
    } else if (pb.allowed_access) {
        hos_log("sandbox: could not allow %s (errno %d)", path, errno);
    }
    close(fd);
}
static void allowf(uint64_t rights, const char *what, const char *fmt, const char *a, const char *b) {
    char p[4096];
    snprintf(p, sizeof p, fmt, a, b ? b : "");
    allow(p, rights, what);
}
/* every entry of dir whose name starts with prefix (the NVIDIA device files, hidraw nodes, Wayland sockets) */
static void allow_prefixed(const char *dir, const char *prefix, uint64_t rights, const char *what) {
    DIR *d = opendir(dir);
    if (!d) return;
    for (struct dirent *e; (e = readdir(d)) != NULL;) {
        if (strncmp(e->d_name, prefix, strlen(prefix))) continue;
        char p[4096];
        snprintf(p, sizeof p, "%s/%s", dir, e->d_name);
        allow(p, rights, what);
    }
    closedir(d);
}
static void mkdirs(const char *path) {   /* (a cache folder must exist to be allowed) */
    char p[4096];
    snprintf(p, sizeof p, "%s", path);
    for (char *q = p + 1; *q; q++)
        if (*q == '/') { *q = 0; mkdir(p, 0700); *q = '/'; }
    mkdir(p, 0700);
}
static void allow_list(const char *list, uint64_t rights, const char *what) {
    if (!list) return;
    char buf[8192];
    snprintf(buf, sizeof buf, "%s", list);
    for (char *s = strtok(buf, ":"); s; s = strtok(NULL, ":")) allow(s, rights, what);
}
static void parent_of(const char *path, char *out, size_t cap) {
    snprintf(out, cap, "%s", path ? path : "");
    char *s = strrchr(out, '/');
    if (s && s != out) *s = 0;
    else out[0] = 0;
}

/* The game folder's own state files: written in place, never created there (a process that could create files in its folder
   could also put a library or a settings file there for the next start to load: the folder is read-only otherwise).
   Missing ones are made now, empty: each reader takes an empty file as none. The options file starts as the menu writes it. */
static const char *const state_files[] = {"hookshot_options.ini", "mkw_vk_pipelines.bin", "hookshot_pipelines.bin",
                                          "hookshot_programs.bin", "hookshot_programs.bin.lock", "hookshot_prepared.bin",
                                          "hookshot_log.txt", NULL};

void hos_sandbox_apply(const char *game_dir, const char *user_dir) {
    const char *off = getenv("HOS_SANDBOX");
    if (off && !strcmp(off, "0")) {
        hos_log("sandbox: off (HOS_SANDBOX=0)");
        return;
    }
    const long abi = syscall(__NR_landlock_create_ruleset, NULL, 0, LANDLOCK_CREATE_RULESET_VERSION);
    if (abi < 1) {
        hos_log("sandbox: this kernel has no Landlock (errno %d): the game runs without its sandbox", errno);
        return;
    }
    handled = (1ULL << 13) - 1;   /* (ABI 1: EXECUTE .. MAKE_SYM) */
    if (abi >= 2) handled |= LL_REFER;
    if (abi >= 3) handled |= LL_TRUNCATE;
    if (abi >= 5) handled |= LL_IOCTL_DEV;
    if (abi >= 9) handled |= LL_RESOLVE_UNIX;
    struct { uint64_t fs, net, scoped; } attr = {handled, 0, 0};
    size_t attr_size = sizeof(uint64_t);
    if (abi >= 4) attr.net = LANDLOCK_ACCESS_NET_BIND_TCP | LANDLOCK_ACCESS_NET_CONNECT_TCP, attr_size = 2 * sizeof(uint64_t);
    if (abi >= 6) attr.scoped = LL_SCOPE_SIGNAL | LL_SCOPE_ABSTRACT, attr_size = 3 * sizeof(uint64_t);
    ruleset = (int)syscall(__NR_landlock_create_ruleset, &attr, attr_size, 0);
    if (ruleset < 0) {
        hos_log("sandbox: Landlock ABI %ld refused the rules (errno %d): the game runs without its sandbox", abi, errno);
        return;
    }
    const uint64_t RW = R_READ | R_TREE_WRITE;
    const uint64_t DEV = R_READ | R_FILE_WRITE | LL_IOCTL_DEV;
    const char *home = getenv("HOME"), *cache = getenv("XDG_CACHE_HOME"), *run = getenv("XDG_RUNTIME_DIR");
    const char *tmp = getenv("TMPDIR");
    char cache_dir[4096] = "", p[4096];
    if (cache && cache[0] == '/') snprintf(cache_dir, sizeof cache_dir, "%s", cache);
    else if (home && home[0] == '/') snprintf(cache_dir, sizeof cache_dir, "%s/.cache", home);

    /* the system: libraries, drivers, their settings (Vulkan's ICD and layer files, ld.so's cache, fonts), the kernel's views */
    for (const char *const *d = (const char *const[]){"/usr", "/lib", "/lib64", "/lib32", "/opt", "/nix/store", NULL}; *d; d++)
        allow(*d, R_READ | R_EXEC, NULL);
    note("rx", "/usr,/lib*,/opt");
    for (const char *const *d = (const char *const[]){"/etc", "/sys", "/run/udev", "/dev", NULL}; *d; d++)
        allow(*d, R_READ, "r");
    if (home && home[0] == '/') {
        allowf(R_READ | R_EXEC, "rx", "%s/.local/share/vulkan", home, NULL);   /* (implicit layers a user installed) */
        allowf(R_READ, "r", "%s/.config/vulkan", home, NULL);
        allowf(R_READ, "r", "%s/.local/share/fonts", home, NULL);
        allowf(R_READ, "r", "%s/.fonts", home, NULL);
        allowf(R_READ, "r", "%s/.config/pulse", home, NULL);   /* (PulseAudio's cookie) */
        allowf(R_READ, "r", "%s/.Xauthority", home, NULL);
    }
    allow(getenv("XAUTHORITY"), R_READ, "r");
    /* (the threads' names: pthread_setname_np on another thread writes /proc/self/task/<tid>/comm. A rule on that folder
       does not hold: procfs makes its inodes anew. Writing another process's files there needs ptrace access, which
       Landlock refuses outside the sandbox) */
    allow("/proc", R_READ | LANDLOCK_ACCESS_FS_WRITE_FILE, "w");

    /* the game: its folder read (and run: hookshot_vk.so, bin/), its saves read and written */
    allow(game_dir, R_READ | R_EXEC, "rx");
    for (int i = 0; state_files[i]; i++) {
        snprintf(p, sizeof p, "%s/%s", game_dir, state_files[i]);
        if (access(p, F_OK) != 0) {
            const int fd = open(p, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0644);
            if (fd >= 0) {
                if (!strcmp(state_files[i], "hookshot_options.ini") && write(fd, "options_version=3\n", 18) != 18) {}
                close(fd);
            }
        }
        struct stat st;
        if (lstat(p, &st) == 0 && S_ISREG(st.st_mode)) allow(p, R_READ | R_FILE_WRITE, NULL);
    }
    note("w", "<game folder>/hookshot_options.ini,*.bin caches,hookshot_log.txt");
    allow(user_dir, RW, "rw");

    /* the drivers' shader caches (NVIDIA, Mesa's), made if missing; the temporary folder; shared memory */
    if (cache_dir[0]) {
        for (const char *const *d = (const char *const[]){"nvidia", "mesa_shader_cache", "mesa_shader_cache_db",
                                                         "radv_builtin_shaders64", "fontconfig", NULL}; *d; d++) {
            snprintf(p, sizeof p, "%s/%s", cache_dir, *d);
            if (!strcmp(*d, "nvidia") || !strcmp(*d, "mesa_shader_cache")) mkdirs(p);   /* (the drivers' defaults) */
            allow(p, RW, "rw");
        }
    }
    allow(getenv("__GL_SHADER_DISK_CACHE_PATH"), RW, "rw");
    allow(getenv("MESA_SHADER_CACHE_DIR"), RW, "rw");
    allow(tmp && tmp[0] == '/' ? tmp : "/tmp", RW | LL_RESOLVE_UNIX, "rw");   /* (with /tmp/.X11-unix, X11's sockets) */
    if (tmp && tmp[0] == '/' && strcmp(tmp, "/tmp")) allow("/tmp/.X11-unix", R_READ | LL_RESOLVE_UNIX, "unix");
    allow("/dev/shm", RW, "rw");

    /* devices: the GPU, the controllers (evdev, hidraw: SDL and its HIDAPI), sound, udmabuf (host/hos_main.c) */
    allow("/dev/dri", DEV, "dev");
    allow_prefixed("/dev", "nvidia", DEV, NULL);
    note("dev", "/dev/nvidia*");
    allow("/dev/input", DEV, "dev");
    allow_prefixed("/dev", "hidraw", DEV, NULL);
    note("dev", "/dev/hidraw*");
    allow("/dev/snd", DEV, "dev");
    allow("/dev/udmabuf", DEV, "dev");
    for (const char *const *d = (const char *const[]){"/dev/null", "/dev/zero", "/dev/urandom", "/dev/random", "/dev/tty", NULL}; *d; d++)
        allow(*d, R_READ | R_FILE_WRITE, NULL);

    /* the display's and the sound server's sockets in the runtime folder (not the session bus, nor the rest there) */
    if (run && run[0] == '/') {
        allow(run, R_READ, "r");
        allow_prefixed(run, "wayland-", R_READ | LL_RESOLVE_UNIX, NULL);
        allow_prefixed(run, "pipewire-", R_READ | LL_RESOLVE_UNIX, NULL);
        allowf(R_READ | LL_RESOLVE_UNIX, NULL, "%s/pulse", run, NULL);
        note("unix", "$XDG_RUNTIME_DIR/{wayland-*,pipewire-*,pulse/}");
    }

    /* the test harness's and a debugging session's own paths */
    allow(getenv("RX_VK_SHOTS"), RW, "rw");
    parent_of(getenv("RX_VK_FRAMELOG"), p, sizeof p);
    allow(p, RW, "rw");
    allow(getenv("HOS_INPUT_LIVE"), R_READ, "r");
    allow(getenv("HOS_ENV_FILE"), R_READ, "r");
    allow_list(getenv("HOS_SANDBOX_RW"), RW, "rw");

    prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);
    const unsigned flags = abi >= 8 ? LL_TSYNC : 0;
    const long r = syscall(__NR_landlock_restrict_self, ruleset, flags);
    const int err = errno;
    close(ruleset);
    ruleset = -1;
    if (r != 0) {
        hos_log("sandbox: Landlock ABI %ld could not be applied (errno %d): the game runs without its sandbox", abi, err);
        return;
    }
    hos_log("sandbox: Landlock ABI %ld on, %s, %d rules (TCP %s):%s", abi,
            flags ? "every thread" : "the game's thread and those it starts", nrules, abi >= 4 ? "none" : "not limited", summary);

    /* HOS_SANDBOX_PROBE=<path>[:<path>...]: each path opened for reading and for writing (nothing written) and a new file
       "<path>.sandbox-probe" made beside it (removed again when it could be), with what the kernel answered */
    const char *probe = getenv("HOS_SANDBOX_PROBE");
    if (probe && *probe) {
        char list[8192];
        snprintf(list, sizeof list, "%s", probe);
        for (char *one = strtok(list, ":"); one; one = strtok(NULL, ":")) {
            const int a = open(one, O_RDONLY | O_CLOEXEC);
            const int ea = errno;
            if (a >= 0) close(a);
            const int w = open(one, O_WRONLY | O_APPEND | O_CLOEXEC);
            const int ew = errno;
            if (w >= 0) close(w);
            char made[4096];
            snprintf(made, sizeof made, "%s.sandbox-probe", one);
            const int b = open(made, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
            const int eb = errno;
            if (b >= 0) close(b), unlink(made);
            hos_log("sandbox: probe %s: read %s, write %s, create beside %s", one, a >= 0 ? "allowed" : strerror(ea),
                    w >= 0 ? "allowed" : strerror(ew), b >= 0 ? "allowed" : strerror(eb));
        }
    }
}
