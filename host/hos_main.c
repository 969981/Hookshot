/* Horizon OS layer, native host: process start and boot.

   What rtld and nn::init::Start do on hardware (see ports/zelda-totk/PORT.md, "How the game starts"), done here:
   map the guest window, load the relocated image (nx_image.py already linked it), make this host thread the
   guest main thread, run every module's DT_INIT in rtld's order (the SDK crt's _init: TLS registration with musl,
   then the module's constructors), then nninitStartup and nnMain.

   Usage: <game>.exe [image.bin] [--romfs <dir>] [--user <dir>] [--trace] [--prepare-shaders] [--compile-shaders all|most]
   Environment: HOS_TRACE=1 (log HLE calls), HOS_MEMORY_MB (memory the game is told it has),
                HOS_NVN_CONTINUE=1 (go on past nvnBootstrapLoader). */
#include "hos.h"
#include "game_profile.h"   /* (the game this runtime is compiled for) */
#ifndef GAME_SAVES_DIR   /* (a header from before the saves had a folder per game: two games would share one) */
#error "game_profile.h has no GAME_SAVES_DIR: emit it again (python -I nx2/game_profile.py --emit-header), or lift again"
#endif
#include "game_profile_check.h"
#ifndef HOOKSHOT_ENV_H   /* (env_on: the switches' one rule) */
#include "../gpu/vk/compat/hookshot_env.h"
#endif
#include "hos_paths.h"
#include "hos_wide_size.h"   /* (the widescreen picture's size, shared with the Insert menu) */
#include "ws_floor.h"        /* (the game's memory kept in RAM: HOS_WS_FLOOR_MB) */
#include "../gpu/vk/vk_api.h"
#include "nx2_pack.h"    /* (the game's pack: its start, below) */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef HOOKSHOT_VERSION   /* (nx2/build.py gives it: the release and a fingerprint of the sources, nx2/version.py) */
#define HOOKSHOT_VERSION "unknown"
#endif
const char *hos_version(void) { return HOOKSHOT_VERSION; }   /* (discord_rpc.c's status) */
#if defined(_WIN32) || defined(__EMSCRIPTEN__)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mmsystem.h>
#else
#include <windows.h>   /* (linux: host/posix's) */
#include <sys/mman.h>
#ifdef __linux__
#include <sys/prctl.h>
#include <sys/sysinfo.h>
#endif
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/syscall.h>
#endif
/* (linux) the separator of the paths the executable's folder is joined with */
#ifdef _WIN32
#define SEP 92
#define SEP_S "\\"
#else
#define SEP '/'
#define SEP_S "/"
#endif

#ifdef A64_MEM_ZERO
/* web: guest address = wasm address; the guest window is wasm memory below the host's data (-sGLOBAL_BASE) */
extern char __global_base;
uint64_t hos_space_end;
#define GUEST_SPACE (hos_space_end)
#else
#define GUEST_SPACE HOS_GUEST_SPACE
#endif
#define IMAGE_BASE hos_image_base   /* (the lift's: Eden's layout) */

#ifdef _WIN32
static void no_power_throttling(void) {
    const char *e = getenv("HOS_POWER_THROTTLING");
    if (e && atoi(e)) return;
    typedef BOOL(WINAPI * SetInfo)(HANDLE, int, LPVOID, DWORD);
    SetInfo set = (SetInfo)(void *)GetProcAddress(GetModuleHandleA("kernel32.dll"), "SetProcessInformation");
    if (!set) return;
    struct { ULONG version, control, state; } s = {1, 0x1 | 0x4, 0};
    if (!set(GetCurrentProcess(), 4, &s, sizeof s)) {
        s.control = 0x1;
        set(GetCurrentProcess(), 4, &s, sizeof s);
    }
}
#endif

#ifdef _WIN32
/* (hookshot) The game's memory kept in RAM under memory pressure: a soft working-set minimum (host/ws_floor.h, HOS_WS_FLOOR_MB) */
static void ws_floor(void) {
    uint64_t mb, ram_mb;
    unsigned long err;
    const int r = ws_floor_apply(getenv("HOS_WS_FLOOR_MB"), &mb, &ram_mb, &err);
    if (r > 0)
        hos_log("mem: the working set's minimum is %llu MB of the PC's %llu MB: Windows keeps that much of the game in RAM before "
                "it trims below it (HOS_WS_FLOOR_MB=0: Windows' own)", (unsigned long long)mb, (unsigned long long)ram_mb);
    else if (!r)
        hos_log("mem: the working set's minimum is Windows' own (HOS_WS_FLOOR_MB=0)");
    else
        hos_log("mem: a working set minimum of %llu MB was refused (error %lu): Windows' own stays", (unsigned long long)mb, err);
}
#endif

/* Whether the image this runs is the build the game profile was compiled for (game_profile.h, from
   nx2/game_profile.py; a lift writes the dump's own). Every return address and offset the profile names is that
   build's, so none of them is recognised in any other: a runtime built for one game and started on another, or built
   from the committed header without a lift, recognises nothing rather than something that only looks the same. The
   build ids are compared as far as the image's goes (gen/hos_image.c's is main's NSO build id, which may be shorter
   than the 40 digits nx2/dump.py prints), and at least 16 digits of it. */
int hos_game_profile_live(void) {
    static int live = -1;
    if (live < 0) {
        size_t n = strlen(hos_main_build_id);
        if (n > sizeof GAME_BUILD_ID - 1) n = sizeof GAME_BUILD_ID - 1;
        live = GAME_HAVE_BUILD_ID && n >= 16 && !strncmp(hos_main_build_id, GAME_BUILD_ID, n);
    }
    return live;
}

/* (its own line, after the log's first: hos_game_profile_live is first asked in game_profile_env, before stderr is
   hookshot_log.txt, and a line written then never reached a player's log) */
static void game_profile_log(void) {
    hos_log(hos_game_profile_live() ? "game profile: %s (%s), main %s"
                                    : "game profile: %s (%s) is not for main %s: none of its addresses, offsets or "
                                      "program keys are used",
            GAME_PROFILE_KEY, GAME_NAME, hos_main_build_id);
}

/* The renderer is built once for every game (the AppImage's and a build's alike) with the committed
   profile's program keys as its defaults; the runtime is compiled for its game. So the runtime hands the renderer its
   profile's keys before the renderer reads any: HOOKSHOT_PROFILE_* are not switches a player sets (hookshot_env.txt's
   RX_VK_TAAU_KEYS and RX_VK_NETWORK_KEEP still stand over them), only this channel. An image the profile is not for
   gets no keys at all. "None" is written "-", which every reader of these lists takes as none: an empty value would
   do on Linux, but on Windows _putenv("X=") removes X, and the renderer then took its built-in (Ocarina's) keys. */
static void game_profile_env(void) {
    const int live = hos_game_profile_live();
    char gameplay[18 * (GAME_GAMEPLAY_NETWORK_KEY_COUNT + 1)] = "";
    static const unsigned long long keys[] = GAME_GAMEPLAY_NETWORK_KEYS;
    for (int i = 0, at = 0; live && i < GAME_GAMEPLAY_NETWORK_KEY_COUNT; i++)
        at += snprintf(gameplay + at, sizeof gameplay - at, "%s%016llx", i ? "," : "", keys[i]);
    char parallel[12] = "";   /* (the renderer's parallel replay threads; none: its own default) */
    if (live && GAME_VK_PARALLEL >= 0) snprintf(parallel, sizeof parallel, "%d", GAME_VK_PARALLEL);
    const char *const vars[][2] = {{"HOOKSHOT_PROFILE_TAAU_KEYS", live ? GAME_UPSCALE_TAAU_KEYS : ""},
                                   {"HOOKSHOT_PROFILE_NETWORK_KEEP", live ? GAME_NETWORK_KEEP_KEYS : ""},
                                   {"HOOKSHOT_PROFILE_GAMEPLAY_KEYS", gameplay},
                                   {"HOOKSHOT_PROFILE_VK_PARALLEL", parallel}};
    for (size_t i = 0; i < sizeof vars / sizeof *vars; i++) {
        const char *value = *vars[i][1] ? vars[i][1] : "-";
#ifdef _WIN32
        char line[512];
        snprintf(line, sizeof line, "%s=%s", vars[i][0], value);
        _putenv(line);
#else
        setenv(vars[i][0], value, 1);
#endif
    }
}

/* gen/hos_image.c (build.py lift) */
extern const uint64_t hos_main_stack_size;
extern const int hos_main_priority, hos_main_core, hos_module_count;
extern const uint64_t hos_module_init[];
extern const char *const hos_module_name[];
extern const uint64_t hos_entry_nninitStartup, hos_entry_nnMain;
extern const uint64_t hos_entry_init_libc0, hos_entry_init_libc1, hos_entry_init_libc2, hos_entry_nnosInitialize,
    hos_entry_nninitInitializeSdkModule;
/* nn_fs.c */
extern const char *hos_romfs_dir, *hos_user_dir;

/* HOS_LARGE_PAGES=1 (experiment): the guest window in 2 MB pages, for fewer TLB misses (the game touches hundreds of MB
   of scattered heap; 4 KB pages cover only ~12 MB of it in the CPU's TLB). The whole window is then physical memory at
   once (4 GB, locked), and Windows needs the account to hold "Lock pages in memory" (SeLockMemoryPrivilege, Local
   Security Policy > User Rights Assignment; log off and on after): without it this logs why and uses normal pages. */
int hos_large_pages;   /* (hos_mem.c: freed pages are not discarded then) */
#if defined(_WIN32) && !defined(A64_MEM_ZERO)
#pragma comment(lib, "advapi32.lib")   /* (the token privilege calls) */
static void *reserve_large(SIZE_T size) {
    HANDLE token;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &token)) return NULL;
    TOKEN_PRIVILEGES tp = {0};
    tp.PrivilegeCount = 1;
    tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;
    const BOOL looked = LookupPrivilegeValueA(NULL, "SeLockMemoryPrivilege", &tp.Privileges[0].Luid);
    const BOOL adjusted = looked && AdjustTokenPrivileges(token, FALSE, &tp, 0, NULL, NULL);
    const DWORD err = GetLastError();
    CloseHandle(token);
    if (!adjusted || err == ERROR_NOT_ALL_ASSIGNED) {
        hos_log("mem: HOS_LARGE_PAGES: this account does not hold SeLockMemoryPrivilege (\"Lock pages in memory\"): "
                "normal pages");
        return NULL;
    }
    const SIZE_T large = GetLargePageMinimum();
    if (!large || size % large) return NULL;
    void *p = VirtualAlloc((void *)A64_HOST_BASE, size, MEM_RESERVE | MEM_COMMIT | MEM_LARGE_PAGES, PAGE_READWRITE);
    if (!p) hos_log("mem: HOS_LARGE_PAGES: %llu MB of large pages not available (error %lu): normal pages",
                    (unsigned long long)(size >> 20), GetLastError());
    return p;
}
#endif

#if !defined(_WIN32) && !defined(A64_MEM_ZERO)
extern int hos_guest_memfd;   /* (below) */
#ifndef _WIN32
/* (linux) Guest addresses below 1 MB fault, as on Windows (mkw_glue.c commit_on_touch leaves them uncommitted: "a null
   pointer: the game's own fault") and as on the console, where nothing is mapped there. The window is mapped readable and
   writable whole, so before this a game's null-pointer reads and writes went into guest page 0 without a word, and its
   crash came later and elsewhere, through whatever had been left there: Kirby Air Riders' first boot (Dragoon) read a
   "pointer" 0x3feb3100500 out of guest 0x68 in a job worker, where the fault was a null object read at guest 0x58.
   Nothing is handed out below HOS_SYSTEM_BASE (16 MB). */
static void guest_null_guard(void) {
    if (mprotect((void *)A64_HOST_BASE, 0x100000, PROT_NONE) != 0)
        hos_log("mem: guest addresses below 1 MB stay accessible (mprotect: errno %d): a null pointer does not fault", errno);
}
#endif
static int startup_stderr = -1;   /* (keep startup warnings visible if stderr becomes hookshot_log.txt) */
#endif
/* The guest window lives at the constant A64_HOST_BASE (rt/a64.h), so lifted code never loads its base. */
static int reserve_guest(void) {
#if defined(A64_MEM_ZERO)
    hos_space_end = (uint64_t)(uintptr_t)&__global_base & ~0xFFFFULL;
    return hos_space_end >= (1ULL << 30);
#elif defined(_WIN32)
    /* Committed but untouched pages cost no physical memory. Write-watched: the renderer's buffer cache asks which
       pages changed (GetWriteWatch) so that it uploads only those (soft write watch builds mark pages themselves). */
#if defined(A64_WRITE_TRACK)
    const DWORD watch = 0;
    if (env_on("HOS_LARGE_PAGES") && reserve_large(GUEST_SPACE) == (void *)A64_HOST_BASE) {
        hos_large_pages = 1;
        hos_log("mem: the guest window is in large pages (%llu MB)", (unsigned long long)(GUEST_SPACE >> 20));
        return 1;
    }
#else
    const DWORD watch = MEM_WRITE_WATCH;
#endif
    /* mkw-recomp: 512 GB reserved (write-watched: the renderer's change tracking, a 16 MB bitmap), committed as the
       runtime hands memory out (hos_commit) or on first touch */
    void hos_commit_init(void);
    hos_commit_init();
    return VirtualAlloc((void *)A64_HOST_BASE, GUEST_SPACE, MEM_RESERVE | watch, PAGE_NOACCESS) == (void *)A64_HOST_BASE;
#else
    /* (linux) the whole window readable and writable at once, its pages made as they are first touched (no commit
       step: MAP_NORESERVE). Decommitting is madvise (host/posix/win32_posix.c VirtualFree): the pages read as zero. */
    void hos_commit_init(void);
    hos_commit_init();   /* (the crash report) */
    /* The window is a memfd's, shared, so the Vulkan back end can hand the GPU the game's own pages as a dma-buf
       (udmabuf, gpu/vk/vk_memory.cpp import_dma_buf) instead of a host pointer: amdgpu checks every page of a host-pointer
       import again at each command submission, 15-45 ms a submission in Kokiri Forest. Pages of a shared mapping are freed
       with MADV_REMOVE, not MADV_DONTNEED (hos_posix_guest_discard). HOS_GUEST_MEMFD=0: anonymous memory, as before */
    {
        /* (only where /dev/udmabuf can be opened: a shared mapping's pages cannot be host-pointer imports (RADV:
           VK_ERROR_INVALID_EXTERNAL_HANDLE), so a pool udmabuf refused would be no import at all. A pool larger than udmabuf's
           size_limit_mb (64 MB unless raised) is imported as several dma-bufs bound into one sparse buffer, so no setting is
           needed. HOS_GUEST_MEMFD=1: the memfd all the same) */
        const char *e = getenv("HOS_GUEST_MEMFD");
        int usable = 0;
        {
            const int u = open("/dev/udmabuf", O_RDWR | O_CLOEXEC);
            if (u < 0 && (errno == EACCES || errno == EPERM)) {
                const int err = errno;
                static int warned;
                if (!warned) {
                    warned = 1;
                    const char *warning = "WARNING: /dev/udmabuf permission denied; GPU imports may be slower, especially on AMD/RADV. "
                        "Ask your administrator for access via its device group or a device-specific udev rule "
                        "(see README, On Linux). Do not run the game as root.";
                    if (startup_stderr >= 0) dprintf(startup_stderr, "%s (%s)\n", warning, strerror(err));
                    hos_log("%s (%s)", warning, strerror(err));
                }
            }
            if (startup_stderr >= 0) close(startup_stderr), startup_stderr = -1;
            if (u >= 0) {
                close(u);
                /* (issue 1135) usable whatever size_limit_mb says: the renderer reads the limit itself and takes udmabuf's
                   default, 64 MB, when it can't (gpu/vk/vk_memory.cpp import_dma_buf), and a limit too small only makes more
                   dma-bufs. A Flatpak's or Steam's container shows /dev/udmabuf but not /sys/module */
                FILE *f = fopen("/sys/module/udmabuf/parameters/size_limit_mb", "r");
                if (f)
                    fclose(f);
                else
                    hos_log("mem: udmabuf's size_limit_mb cannot be read (in a container?): its default, 64 MB, assumed");
                usable = 1;
            }
            if (!usable && !e)
                hos_log("mem: guest memory stays host-pointer imports for the GPU (slow submissions on AMD): "
                        "/dev/udmabuf cannot be opened");
        }
        if (e ? atoi(e) != 0 : usable) {
            const int fd = (int)syscall(SYS_memfd_create, "hookshot-guest", 1u /* MFD_CLOEXEC */ | 2u /* MFD_ALLOW_SEALING */);
            if (fd >= 0 && ftruncate(fd, (off_t)GUEST_SPACE) == 0) {
                fcntl(fd, 1033 /* F_ADD_SEALS */, 0x0002 /* F_SEAL_SHRINK */ | 0x0004 /* F_SEAL_GROW */);
                void *p = mmap((void *)A64_HOST_BASE, GUEST_SPACE, PROT_READ | PROT_WRITE,
                               MAP_SHARED | MAP_FIXED_NOREPLACE | MAP_NORESERVE, fd, 0);
                if (p == (void *)A64_HOST_BASE) {
                    guest_null_guard();
                    hos_guest_memfd = fd;
                    hos_log("mem: the guest window is a memfd's (%d), shared with the GPU as dma-bufs", fd);
                    return 1;
                }
                if (p != MAP_FAILED) munmap(p, GUEST_SPACE);
            }
            if (fd >= 0) close(fd);
            hos_log("mem: no memfd for the guest window (errno %d): anonymous memory", errno);
        }
    }
    if (mmap((void *)A64_HOST_BASE, GUEST_SPACE, PROT_READ | PROT_WRITE,
             MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE | MAP_NORESERVE, -1, 0) != (void *)A64_HOST_BASE)
        return 0;
    guest_null_guard();
    /* (issue 312) no transparent huge pages in the guest window: the renderer imports its pools as userptr memory, and every
       collapse khugepaged makes in them changes their mappings, which the kernel answers by pinning the pages again at the
       next submission: ~250 ms a submit on RADV while the GPU sat idle. HOS_THP=1: as before */
    if (!env_on("HOS_THP")) madvise((void *)A64_HOST_BASE, GUEST_SPACE, MADV_NOHUGEPAGE);
#ifdef __linux__
    /* (HOS_NO_THP=1, experiment: none outside the window either. With THP "always" 400-450 MB of the host heap's and the binary's bss are huge
       pages, each resident whole when any of its 2 MB is touched) */
    if (env_on("HOS_NO_THP")) prctl(PR_SET_THP_DISABLE, 1, 0, 0, 0);
#endif
    return 1;
#endif
}

#if !defined(_WIN32) && !defined(A64_MEM_ZERO)
/* the guest window's memfd for the Vulkan back end (-1: anonymous memory), and freeing guest pages so that
   they read as zero again on either kind of mapping */
int hos_guest_memfd = -1;
int hos_posix_guest_memfd(void) { return hos_guest_memfd; }
int hos_posix_guest_discard(void *p, size_t n) {
    return madvise(p, n, hos_guest_memfd >= 0 ? MADV_REMOVE : MADV_DONTNEED);
}
#endif

/* The guest window, for the renderer (gpu/render/rx_buffer.cpp). */
uint8_t *hos_guest_base(void) { return A64_MEM; }
uint64_t hos_guest_size(void) { return GUEST_SPACE; }

/* Call guest code at `addr` as a fresh call from the top of the main thread's stack, with whatever arguments the
   caller put in x0-x7 (call_guest clears x0 and x1 first; call_guest_args keeps them). */
static void call_guest_args(Ctx *c, uint64_t addr, uint64_t sp, const char *what) {
    A64Fn f = a64_lookup(addr);
    if (!addr || !f) hos_fatal(c, "%s: no function at %#llx", what, (unsigned long long)addr);
    hos_log("boot: %s (%#llx)", what, (unsigned long long)addr);
    c->x[30] = 0;
    c->x[31] = sp;
    void hos_mem_checkpoint(const char *what);
    hos_mem_checkpoint(what);
    f(c);
}
static void call_guest(Ctx *c, uint64_t addr, uint64_t sp, const char *what) {
    c->x[0] = c->x[1] = 0;
    call_guest_args(c, addr, sp, what);
}

#ifndef _WIN32
/* (linux) Saves live in $XDG_DATA_HOME/hookshot (~/.local/share/hookshot; another game's in hookshot-<key>, below), so a
   rebuilt game folder still finds them.
   A user/ folder beside the executable from before is moved there the first time, entry by entry. Anything that
   cannot be moved keeps the old place. --user still wins.
   A symbolic link in it, to saves the player keeps elsewhere (a synced folder, another drive), is made again in the
   new place, to the same folder, and never entered. It was moved as it stood, and one written from where it stood
   ("../../saves") then named nothing; from another drive it was copied as the folder it names and delete_tree emptied
   that folder, which is not Hookshot's: the next start found the link still here, kept the old place, and showed no
   saves. A user/ that is a link itself is the player's choice of place, and is used as it is. */
static int is_dir(const char *p) {
    const DWORD a = GetFileAttributesA(p);
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
}
static int is_link(const char *p) {
    struct stat st;
    return lstat(p, &st) == 0 && S_ISLNK(st.st_mode);
}
static const char *move_old, *move_from, *move_new;   /* (the user folder on the move, as realpath names it and as given, and the new place) */
static int moved_to(char *target, const char *dir) {   /* (a target under dir: named where it will be) */
    char there[MAX_PATH];
    const size_t n = dir && dir[0] == SEP ? strlen(dir) : 0;
    if (!n || strncmp(target, dir, n) || target[n] != SEP) return 0;
    snprintf(there, sizeof there, "%s%s", move_new, target + n);
    snprintf(target, MAX_PATH, "%s", there);
    return 1;
}
static int copy_link(const char *from, const char *to) {   /* (to what it names, in full; one that names nothing: as written) */
    char target[MAX_PATH];
    if (!realpath(from, target)) {
        const ssize_t n = readlink(from, target, sizeof target - 1);
        if (n < 0) return 0;
        target[n] = 0;
    }
    /* (to an entry that moves too: where it will be. One whose entry went first names nothing by now, so its text is
       checked too: else it named the old place, and only when the folder listed it before the entry) */
    if (!moved_to(target, move_old)) moved_to(target, move_from);
    return symlink(target, to) == 0;
}
static int has_link(const char *p) {
    if (is_link(p)) return 1;
    if (!is_dir(p)) return 0;
    char pattern[MAX_PATH];
    snprintf(pattern, sizeof pattern, "%s" SEP_S "*", p);
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pattern, &fd);
    int found = 0;
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (!strcmp(fd.cFileName, ".") || !strcmp(fd.cFileName, "..")) continue;
            char a[MAX_PATH];
            snprintf(a, sizeof a, "%s" SEP_S "%s", p, fd.cFileName);
            found = has_link(a);
        } while (!found && FindNextFileA(h, &fd));
        FindClose(h);
    }
    return found;
}
static int copy_tree(const char *from, const char *to) {
    if (is_link(from)) return copy_link(from, to);
    if (!is_dir(from)) {
        FILE *in = fopen(from, "rb"), *out = in ? fopen(to, "wb") : NULL;
        int ok = in && out;
        char buf[1 << 16];
        for (size_t n; ok && (n = fread(buf, 1, sizeof buf, in)) > 0;) ok = fwrite(buf, 1, n, out) == n;
        ok = ok && in && !ferror(in);
        if (in) fclose(in);
        if (out && fclose(out)) ok = 0;
        return ok;
    }
    if (!CreateDirectoryA(to, NULL) && !is_dir(to)) return 0;
    char pattern[MAX_PATH];
    snprintf(pattern, sizeof pattern, "%s" SEP_S "*", from);
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pattern, &fd);
    int ok = 1;
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (!strcmp(fd.cFileName, ".") || !strcmp(fd.cFileName, "..")) continue;
            char a[MAX_PATH], b[MAX_PATH];
            snprintf(a, sizeof a, "%s" SEP_S "%s", from, fd.cFileName);
            snprintf(b, sizeof b, "%s" SEP_S "%s", to, fd.cFileName);
            ok = copy_tree(a, b);
        } while (ok && FindNextFileA(h, &fd));
        FindClose(h);
    }
    return ok;
}
static void delete_tree(const char *p) {
    if (!is_dir(p) || is_link(p)) {   /* (a link goes; what it names stays) */
        DeleteFileA(p);
        return;
    }
    char pattern[MAX_PATH];
    snprintf(pattern, sizeof pattern, "%s" SEP_S "*", p);
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pattern, &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (!strcmp(fd.cFileName, ".") || !strcmp(fd.cFileName, "..")) continue;
            char a[MAX_PATH];
            snprintf(a, sizeof a, "%s" SEP_S "%s", p, fd.cFileName);
            delete_tree(a);
        } while (FindNextFileA(h, &fd));
        FindClose(h);
    }
    RemoveDirectoryA(p);
}
/* Each game's saves in a folder of its own: game_profile.h's GAME_SAVES_DIR, Ocarina's "hookshot" and any other game's
   "hookshot-<profile key>". The save folder has no word of which game wrote it, and the forks of this engine all used
   "hookshot", so a second game's first run wrote into Ocarina's saves (DK Bananza fork, 4f417fd7 and 58d94fb4). The
   fallback is for tests/test_linux_saves_move.py, which compiles this code alone; the runtime's own build stops above
   on a header without the name. */
#ifndef GAME_SAVES_DIR
#define GAME_SAVES_DIR "hookshot"
#endif
static const char *saves_dir(const char *beside) {
    static char dir[MAX_PATH];
    const char *xdg = getenv("XDG_DATA_HOME"), *home = getenv("HOME");
    if (is_link(beside)) return beside;
    if (xdg && xdg[0] == '/') snprintf(dir, sizeof dir, "%s", xdg);
    else if (home && home[0] == '/') {
        snprintf(dir, sizeof dir, "%s/.local", home);
        CreateDirectoryA(dir, NULL);
        snprintf(dir, sizeof dir, "%s/.local/share", home);
    } else return beside;
    CreateDirectoryA(dir, NULL);
    snprintf(dir + strlen(dir), sizeof dir - strlen(dir), "/" GAME_SAVES_DIR);
    CreateDirectoryA(dir, NULL);
    if (GetFileAttributesA(dir) == INVALID_FILE_ATTRIBUTES) return beside;
    char pattern[MAX_PATH];
    snprintf(pattern, sizeof pattern, "%s" SEP_S "*", beside);
    int moved = 0, kept = 0;
    char real[MAX_PATH];
    move_old = realpath(beside, real), move_from = beside, move_new = dir;
    /* (looked at first, then moved: with an entry the new place has already, the old place is kept, and it keeps them all.
       The others used to go all the same, and the game then ran here without them. So does one that cannot go, a copy that
       fails (a full disk): the entries that went before it come back, and a copied one's old place goes only once all are
       there. They used to stay in the new place, and the next start found them there and kept the old place without them) */
    struct { char *name; int copied; } *went = NULL;
    size_t n_went = 0;
    for (int move = 0; move < 2 && !kept; move++) {
        WIN32_FIND_DATAA fd;
        HANDLE h = FindFirstFileA(pattern, &fd);
        if (h == INVALID_HANDLE_VALUE) return dir;
        do {
            if (!strcmp(fd.cFileName, ".") || !strcmp(fd.cFileName, "..") || (move && kept)) continue;
            char from[MAX_PATH], to[MAX_PATH];
            snprintf(from, sizeof from, "%s" SEP_S "%s", beside, fd.cFileName);
            snprintf(to, sizeof to, "%s" SEP_S "%s", dir, fd.cFileName);
            if (!move) {
                kept += GetFileAttributesA(to) != INVALID_FILE_ATTRIBUTES;
                continue;
            }
            void *more = realloc(went, (n_went + 1) * sizeof *went);
            char *name = more ? strdup(fd.cFileName) : NULL;
            if (more) went = more;
            if (!name) kept++;
            else if (!has_link(from) && MoveFileExA(from, to, 0)) went[n_went].name = name, went[n_went++].copied = 0;
            else if (copy_tree(from, to)) went[n_went].name = name, went[n_went++].copied = 1;
            else delete_tree(to), free(name), kept++;
        } while (FindNextFileA(h, &fd));
        FindClose(h);
    }
    for (size_t i = 0; i < n_went; i++) {
        char from[MAX_PATH], to[MAX_PATH];
        snprintf(from, sizeof from, "%s" SEP_S "%s", beside, went[i].name);
        snprintf(to, sizeof to, "%s" SEP_S "%s", dir, went[i].name);
        if (!kept) {
            moved++;
            if (went[i].copied) delete_tree(from);
        } else if (went[i].copied) delete_tree(to);
        else if (!MoveFileExA(to, from, 0)) hos_log("saves: %s could not come back to %s", to, from);
        free(went[i].name);
    }
    free(went);
    if (kept) {
        hos_log("saves: %d of %s's entries could not go to %s: the saves stay in %s", kept, beside, dir, beside);
        return beside;
    }
    RemoveDirectoryA(beside);
    if (moved) {
        char note[MAX_PATH];
        const char *sep = strrchr(beside, SEP);
        snprintf(note, sizeof note, "%.*s" SEP_S "saves-moved.txt", sep ? (int)(sep - beside) : 0, beside);
        FILE *f = fopen(note, "w");
        if (f) {
            fprintf(f, "Hookshot's saves are now in\n%s\n\nThey moved there from the user folder that was here.\n", dir);
            fclose(f);
        }
        hos_log("saves: moved from %s to %s", beside, dir);
    }
    return dir;
}
#endif

/* hookshot_env.txt beside the game: NAME=VALUE lines set as environment variables before anything reads them, for the
   switches players are asked to test (RX_VK_NOOPT and the rest). Lines starting with # are notes. On Linux this runs
   before the window, so HOS_HEADLESS and HOS_WINDOW_SIZE are already set when the window is made.
   HOS_ENV_FILE=<path> reads that file instead: a scripted or A/B test run brings its own switches and leaves the
   player's hookshot_env.txt as it is.
   (hookshot) hookshot_env.txt is a file players pass around, so it sets only the game's own switches (RX_, HOS_,
   HOOKSHOT_, EDEN_, NVN_ and SDL_ names), never HOS_ENV_FILE, HOS_CONTROL_PEER_SID or SDL's library override, and no
   value that is a path but RX_VK_DLSS_DIR's: the system's variables (the Vulkan loader's layers, PATH and the like) can
   load code, and the dump switches write files where they name. The file HOS_ENV_FILE names sets anything. */
static int env_line_allowed(const char *name, const char *value) {
    static const char *const prefixes[] = {"RX_", "HOS_", "HOOKSHOT_", "EDEN_", "NVN_", "SDL_"};
    int ok = 0;
    for (size_t i = 0; i < sizeof prefixes / sizeof *prefixes; i++) ok |= !strncmp(name, prefixes[i], strlen(prefixes[i]));
    if (!ok || !strcmp(name, "HOS_ENV_FILE") || !strcmp(name, "SDL_DYNAMIC_API")) return 0;
    /* (hookshot) nor HOS_CONTROL_PEER_SID: it lets one more account into the control bridge's pipe (host/control.c), so a
       shared file would let another user of this PC steer the game and read its memory. The environment still sets it */
    if (!strcmp(name, "HOS_CONTROL_PEER_SID")) return 0;
    /* (hookshot) nor the libraries SDL loads by name (SDL_VULKAN_LIBRARY, SDL_OPENGL_LIBRARY, SDL_VIDEO_GL_DRIVER,
       SDL_VIDEO_EGL_DRIVER...): a bare file name is looked for beside the game and in the current folder on Windows */
    if (!strncmp(name, "SDL_", 4) && (strstr(name, "_LIB") || strstr(name, "GL_DRIVER"))) return 0;
    for (const char *p = name; *p; p++)
        if (!((*p >= 'A' && *p <= 'Z') || (*p >= '0' && *p <= '9') || *p == '_')) return 0;
    /* (hookshot) the switches that name a file to write or a folder take no value here, not even a bare name: one
       resolves in the game's folder (HOS_SAMPLE_FILE=hookshot_vk.dll emptied the renderer) */
    const size_t nl = strlen(name);
    int names_file = (nl > 5 && (!strcmp(name + nl - 5, "_FILE") || !strcmp(name + nl - 4, "_DIR"))) ||
                     !strcmp(name, "RX_VK_FRAME_LOG") || !strcmp(name, "HOS_PRESENT_TRACE");
    /* (hookshot) and the test switches whose value is a file or folder under another name (HOS_AUDIO_DUMP=image.bin
       overwrote the game's image), or that press buttons and menu keys on a timer (a shared file erased a save from
       the file select) */
    static const char *const by_name[] = {
        "HOS_AUDIO_DUMP", "HOS_AUDIO_WB_DUMP", "HOS_SHADER_DUMP", "HOS_FS_OVERLAY", "HOS_MODS", "HOS_INPUT_LIVE",
        "HOS_INPUT_SCRIPT", "HOS_SAMPLE_ALL", "HOS_CHAIN", "HOS_WATCH", "HOOKSHOT_BUILDER_HOME", "RX_VK_SHOTS", "RX_VK_SHOT_GO",
        "RX_VK_FRAMELOG", "RX_VK_HANG_DUMP", "RX_VK_DUMP", "RX_VK_DUMP_SPV", "RX_VK_DUMP_MESH_SPV", "RX_VK_DUMP_STAGES",
        "RX_VK_DUMP_FAILED", "RX_VK_IR", "RX_VK_MENU_SCRIPT", "RX_DUMP_WGSL", "RX_DUMP_SLOW_WGSL", "RX_DUMP_TICS",
        "RX_WGSL_OVERRIDE"};
    for (size_t i = 0; i < sizeof by_name / sizeof *by_name; i++) names_file |= !strcmp(name, by_name[i]);
    /* RX_VK_DLSS_DIR: a folder on this PC, not a share (\\server\x or //server/x) or a device path */
    if (!strcmp(name, "RX_VK_DLSS_DIR"))
        return !((value[0] == '\\' || value[0] == '/') && (value[1] == '\\' || value[1] == '/'));
    if (names_file) return 0;
    const int drive = ((value[0] | 0x20) >= 'a' && (value[0] | 0x20) <= 'z' && value[1] == ':');
    return !strpbrk(value, "/\\") && !drive && value[0] != '~' && strcmp(value, ".") && strcmp(value, "..");
}
static void load_hookshot_env(void) {
    char path[MAX_PATH + 32];
    const char *other = getenv("HOS_ENV_FILE");
    if (other && *other) {
        snprintf(path, sizeof path, "%s", other);
    } else {
        DWORD n = hos_game_path_a(path, MAX_PATH);
        while (n && path[n - 1] != 92 && path[n - 1] != 47) n--;
        memcpy(path + n, "hookshot_env.txt", 17);
    }
    FILE *f = fopen(path, "r");
    if (!f) return;
    /* The environment the game was started with wins over the file: a switch set for one launch (HOS_FULLSCREEN=0 in a
       shell, a script's RX_VK_PACE=1) beats the same name saved in the file. The file's own later lines still replace its
       earlier ones: the names it set are kept here (for the whole run: a second read sets them again), so they are not taken
       for the environment's */
    static char from_file[256][128];   /* (names of up to 127 characters; a file with more names than this has its later
                                          lines for the names past it taken as the environment's, logged as left out) */
    static int from_file_n;
    char line[512];
    while (fgets(line, sizeof line, f)) {
        char *s = line;
        if ((unsigned char)s[0] == 0xEF && (unsigned char)s[1] == 0xBB && (unsigned char)s[2] == 0xBF) s += 3;   /* (Notepad's BOM) */
        while (*s == ' ' || *s == 9) s++;
        size_t len = strlen(s);
        while (len && (s[len - 1] == 10 || s[len - 1] == 13 || s[len - 1] == ' ' || s[len - 1] == 9)) s[--len] = 0;
        if (!len || *s == '#' || !strchr(s, '=')) continue;
        char *eq = strchr(s, '=');
        *eq = 0;
        if (!(other && *other) && !env_line_allowed(s, eq + 1)) {
            fprintf(stderr, "env: hookshot_env.txt: %s left out (only the game's switches, and no paths, are taken from it)\n", s);
            continue;
        }
        int ours = 0;
        for (int i = 0; i < from_file_n && !ours; i++) ours = !strcmp(from_file[i], s);
        if (!ours && getenv(s)) {
            fprintf(stderr, "env: %s: %s left out (set in the environment, which goes first)\n",
                    other && *other ? "HOS_ENV_FILE" : "hookshot_env.txt", s);
            continue;
        }
        if (!ours && from_file_n < (int)(sizeof from_file / sizeof *from_file) && strlen(s) < sizeof from_file[0])
            strcpy(from_file[from_file_n++], s);
#ifdef _WIN32
        *eq = '=';
        _putenv(s);
#else
        setenv(s, eq + 1, 1);
#endif
    }
    fclose(f);
}

/* Switches this build no longer reads, still set (in the environment or hookshot_env.txt, as older issues told players):
   one log line each, naming what took its place, so a bug report shows the switch did nothing */
static void removed_switch_notes(void) {
    static const char *const gone[][2] = {
        {"RX_VK_NO_DESC_REUSE", "RX_VK_DESC_REUSE=never"},
        {"RX_VK_DESC_SLOTGEN", "RX_VK_DESC_REUSE (the same 0 and 1) or the Descriptor reuse row"},
        {"RX_VK_DESC_REUSE_DGEN", NULL},
        {"RX_VK_NO_NAN_GUARD", "RX_VK_NAN_GUARD=0"},
        {"RX_VK_FOLLOW_MESH", NULL},
        {"RX_VK_OOT", NULL},
        {"RX_VK_OOT_EXPOSURE", NULL},
        {"RX_VK_OOT_FOG", NULL},
        {"RX_VK_OOT_VISUAL", NULL},
        {"HOS_FIX_RES", NULL},
        {"HOS_SCENE_NATIVE", NULL},
        {"TOTK_MINIMAL_LAYOUT", NULL},
    };
    for (size_t i = 0; i < sizeof gone / sizeof *gone; i++)
        if (getenv(gone[i][0])) {
            if (gone[i][1]) hos_log("env: %s is no longer read: use %s", gone[i][0], gone[i][1]);
            else hos_log("env: %s is no longer read: it does nothing", gone[i][0]);
        }
}

float hos_aspect_ratio = 0;   /* (the window's default shape: host/display.c) */
/* (nx2/oot_wide.py) read by the widescreen patches in the game's code; 0: the game's own 16:9 */
volatile uint64_t hos_wide_out, hos_wide_scene;
volatile float hos_wide_ratio, hos_wide_squeeze, hos_wide_margin;
volatile uint32_t hos_wide_keep_mask = 0xf;   /* (nx2/oot_wide.py KEEP_VTABLES: the screens that keep the whole picture, a bit each; MkwVkHost.wide_menus) */
/* (hookshot, issues 1241, 2838) Swap can keep a small Linux PC from running out of backing storage without keeping it responsive.
   Warn below the existing 10 GiB floor in physical RAM there too; this is not a promise that 10 GiB is enough.
   The game starts anyway, and headless runs only log the warning. Windows keeps its commit-limit check. */
#define HOS_MEMORY_NEEDED_MB 10240ull
static int memory_notice_text(unsigned long long ram_mb, unsigned long long swap_mb, char *out, size_t cap) {
#if defined(__linux__) && !defined(_WIN32)
    if (ram_mb < HOS_MEMORY_NEEDED_MB) {
        snprintf(out, cap,
                 "This PC has %.1f GiB of RAM and %.1f GiB of swap. Hookshot recommends 16 GB of RAM or more; "
                 "the game alone can use more than 13 GiB.\n\n"
                 "Swap can prevent an out-of-memory exit, but heavy swapping can make the game and desktop unresponsive. "
                 "Close other programs before playing. More swap is not a replacement for RAM. The game starts anyway.",
                 ram_mb / 1024.0, swap_mb / 1024.0);
        return 1;
    }
#endif
    if (ram_mb + swap_mb >= HOS_MEMORY_NEEDED_MB) return 0;
    snprintf(out, cap,
             "This PC has %.1f GB of memory and %.1f GB of swap (the page file on Windows). The game needs about %llu GB of the "
             "two together; with less it can close by itself while it loads.\n\nClose other programs, or add swap (on Windows: "
             "let it manage the page file's size), then start the game again. It starts now anyway.",
             ram_mb / 1024.0, swap_mb / 1024.0, HOS_MEMORY_NEEDED_MB / 1024);
    return 1;
}
#ifndef A64_MEM_ZERO
static void memory_notice(void) {
    unsigned long long ram = 0, swap = 0;   /* (MB) */
#ifdef _WIN32
    MEMORYSTATUSEX m = {sizeof m};
    if (!GlobalMemoryStatusEx(&m)) return;
    ram = m.ullTotalPhys >> 20;
    swap = m.ullTotalPageFile > m.ullTotalPhys ? (m.ullTotalPageFile - m.ullTotalPhys) >> 20 : 0;   /* (the commit limit: RAM and page file) */
#elif defined(__linux__)
    struct sysinfo si;
    if (sysinfo(&si)) return;
    ram = ((unsigned long long)si.totalram * si.mem_unit) >> 20, swap = ((unsigned long long)si.totalswap * si.mem_unit) >> 20;
#else
    return;
#endif
    char text[512];
    if (!memory_notice_text(ram, swap, text, sizeof text)) {
        hos_log("boot: %llu MB of memory, %llu MB of swap", ram, swap);
        return;
    }
#if defined(__linux__) && !defined(_WIN32)
    hos_log("boot: LOW MEMORY: %llu MB of memory, %llu MB of swap; below %llu MB of physical RAM: heavy swapping can make "
            "the game and desktop unresponsive (16 GB RAM or more recommended)", ram, swap, HOS_MEMORY_NEEDED_MB);
#else
    hos_log("boot: LOW MEMORY: %llu MB of memory, %llu MB of swap, below the ~%llu MB the game needs", ram, swap, HOS_MEMORY_NEEDED_MB);
#endif
    if (env_on("HOS_HEADLESS")) return;
#ifndef _WIN32
    int hos_sdl_load(void);
    hos_sdl_load();   /* (host/posix MessageBoxA shows it through the SDL already loaded) */
#endif
    MessageBoxA(NULL, text, "Hookshot", MB_OK | MB_ICONWARNING);
}
#endif
/* (hookshot, linux) What driver_env_choose did with the driver's environment, said again in the game's own log once it is open
   (it runs before, when stderr is still the launcher's) */
static char driver_env_said[1024];
static void driver_env_log(void) {
    if (driver_env_said[0]) hos_log("%s", driver_env_said);
}
#ifdef _WIN32
/* (hookshot) On laptops with two GPUs, the drivers run a program on the dedicated one when it exports these (NVIDIA Optimus, AMD
   PowerXpress): without them a laptop could put the game on its integrated GPU, 5 fps (issue 176) */
__declspec(dllexport) unsigned long NvOptimusEnablement = 1;
__declspec(dllexport) int AmdPowerXpressRequestHighPerformance = 1;

/* (hookshot) The game's paths are UTF-8 char strings through Windows' A functions and fopen: its manifest (nx2/win_manifest.py)
   makes the process's code page UTF-8, from Windows 10 1903 on. Before that, or with the manifest missing, those take the system's
   ANSI code page, and a folder name it cannot spell (Japanese on a Western Windows, an emoji) comes back as "?": nothing opens and
   the game closed with no window and no log. The exe's own path through GetModuleFileNameA must name the exe (a name past MAX_PATH
   bytes fails the same way); when it does not, a message says why (none for the builder's --prepare-shaders run: its log has it). */
static int narrow_paths_ok(int argc, char **argv) {
    char a[MAX_PATH];
    const DWORD n = GetModuleFileNameA(NULL, a, MAX_PATH);
    if (n && n < MAX_PATH && GetFileAttributesA(a) != INVALID_FILE_ATTRIBUTES) return 1;
    wchar_t w[MAX_PATH * 2] = L"";
    GetModuleFileNameW(NULL, w, MAX_PATH * 2);
    fprintf(stderr, "hookshot: this folder's path cannot be opened (code page %u, %s): move the game to a folder with a plain name\n",
            GetACP(), n >= MAX_PATH ? "too long" : "has characters the code page lacks");
    int quiet = env_on("HOS_HEADLESS");
    for (int i = 1; i < argc; i++) quiet |= !strcmp(argv[i], "--prepare-shaders");
    if (!quiet) {
        wchar_t text[MAX_PATH * 2 + 640];
        _snwprintf(text, sizeof text / sizeof text[0],
                   L"Hookshot can't open its files in this folder:\n\n%ls\n\n"
                   L"%ls\n\nMove the game folder somewhere with a shorter name of plain letters and digits (for example C:\\Games\\Hookshot), "
                   L"or update Windows.",
                   w, GetACP() == CP_UTF8 ? L"The path is too long."
                                          : L"Its name has characters this Windows can only give the game from Windows 10 version 1903 on.");
        text[sizeof text / sizeof text[0] - 1] = 0;
        MessageBoxW(NULL, text, L"Hookshot", MB_OK | MB_ICONERROR | MB_SETFOREGROUND);
    }
    return 0;
}

int main(int argc, char **argv) {
#else
static int game_main(int argc, char **argv) {
#endif
#ifdef _WIN32
    if (!narrow_paths_ok(argc, argv)) return 1;
    {
        void hos_relaunch_keep_env(void);   /* (host/hos_relaunch.c: the environment before the file's lines, for a restart) */
        hos_relaunch_keep_env();
    }
    load_hookshot_env();
    game_profile_env();
#endif
    hos_log("game pack: %s", nx2_pack_host()->key);
    hos_game_start();   /* (the game's pack: its own switches, read once: Ocarina's occlusion and hearts) */
#ifdef _WIN32
    {   // (the whole process, not only the window's thread: the renderer asks the window's size from its own, and on a 4K screen at 150%
        // Windows answered such a thread 2560 x 1440: the picture was that size, in the corner of the window. Hookshot issues 15, 18, 20)
        typedef BOOL(WINAPI * SetAware)(void *);
        SetAware set = (SetAware)(void *)GetProcAddress(GetModuleHandleA("user32.dll"), "SetProcessDpiAwarenessContext");
        if (set && !env_on("HOS_DPI_UNAWARE")) set((void *)(intptr_t)-4);
    }
#endif
    {   // (started from its icon the game has nowhere to log to: hookshot_log.txt beside it, last run only, for players to send in)
#ifdef _WIN32
        {   /* (started again by the Insert menu's Restart now (host/hos_relaunch.c): the run before still has its log open until
               it exits, so wait for it (up to 10 s) before the log moves to .1, or the move fails and its log is overwritten) */
            const char *of = getenv("HOS_RESTART_OF");
            if (of && *of) {
                HANDLE before = OpenProcess(SYNCHRONIZE, FALSE, (DWORD)strtoul(of, NULL, 10));
                if (before) WaitForSingleObject(before, 10000), CloseHandle(before);
                SetEnvironmentVariableA("HOS_RESTART_OF", NULL);   /* (not passed on: a later restart sets its own) */
                _putenv("HOS_RESTART_OF=");
            }
        }
#endif
        HANDLE err = GetStdHandle(STD_ERROR_HANDLE);
        const DWORD err_kind = (!err || err == INVALID_HANDLE_VALUE) ? FILE_TYPE_UNKNOWN : GetFileType(err);
        /* Steam markers say the process came from Steam, not that its stderr is Steam's log pipe: the launcher's build
           steps (nx2/make_game.py's run: stdout=PIPE, stderr merged into it, its lines read for @@progress and its
           silence accounted) keep Steam's markers through game_env's filter, and their stderr is a pipe the builder
           reads. A --prepare-shaders run is that builder subprocess, never a played game: its pipe stays the
           builder's, the log gate does not fire for it. (An icon start under UNKNOWN still writes its log as always;
           only the new PIPE arm steps aside for the build step.) */
        int prepare_only = 0;
        for (int i = 1; i < argc; i++)
            if (!strcmp(argv[i], "--prepare-shaders")) prepare_only = 1;
        /* (Steam's Gaming mode hands the game its own log pipe on stderr — measured on Deck hardware 2026-11-17: fd/2 is an
           anonymous FIFO shared with Steam, drained into Steam's console-linux.txt. The player cannot see that sink, and a
           Gaming-mode crash came with no game log (Longshot #1259): the game saw "external stderr" and wrote no file.
           Steam's own pipe counts as nowhere-to-log-to like /dev/null does: the game writes its own log, exactly as in
           Desktop mode. Two things say this pipe is STEAM's rather than a
           pipe the player chose or a developer's live stream: Steam's marker variables (SteamGameId/SteamAppId, set in
           everything Steam starts, non-Steam games and the launcher's routed Play included), and stdin — Steam gives
           the programs it starts /dev/null there (measured), while a developer's own terminal session has a tty and
           keeps its live stream even with Steam's markers in the environment. A player's `game | tee` carries neither
           marker; a terminal stderr streams; a redirect to a real file (DISK) is honored as it was) */
        if (err_kind == FILE_TYPE_UNKNOWN
#ifndef _WIN32
            || (!prepare_only && err_kind == FILE_TYPE_PIPE &&
                (getenv("SteamGameId") || getenv("SteamAppId")) && !isatty(STDIN_FILENO))
#endif
            ) {
            char path[MAX_PATH + 32];
            DWORD n = hos_game_path_a(path, MAX_PATH);
            while (n && path[n - 1] != 92 && path[n - 1] != 47) n--;
            /* (the four runs before stay as hookshot_log.1.txt (the last) to .4.txt: a player who started the game again after a
               crash sent in the new run's log, and the crash was gone) */
            char from[MAX_PATH + 32], to[MAX_PATH + 32];
            for (int k = 4; k >= 1; k--) {
                if (k == 1) memcpy(from + n, "hookshot_log.txt", 17), memcpy(from, path, n);
                else memcpy(from, path, n), snprintf(from + n, 32, "hookshot_log.%d.txt", k - 1);
                memcpy(to, path, n), snprintf(to + n, 32, "hookshot_log.%d.txt", k);
                MoveFileExA(from, to, MOVEFILE_REPLACE_EXISTING);
            }
            memcpy(path + n, "hookshot_log.txt", 17);
#if !defined(_WIN32) && !defined(A64_MEM_ZERO)
            startup_stderr = fcntl(STDERR_FILENO, F_DUPFD_CLOEXEC, 0);
#endif
            /* (opened first: freopen closes stderr when it cannot open the file, and the next file opened would take its
               descriptor. A log that cannot be written leaves the stderr the game was started with) */
            FILE *log = fopen(path, "w");
            if (log) {
                fclose(log);
                if (freopen(path, "w", stderr)) setvbuf(stderr, NULL, _IONBF, 0);
            }
        }
    }
    hos_log("hookshot %s", HOOKSHOT_VERSION);   /* (the log's first line: which build a report comes from) */
    game_profile_log();
    driver_env_log();
    removed_switch_notes();
    void hos_control_start(const char *version);
    hos_control_start(HOOKSHOT_VERSION);   /* (HOS_CONTROL=<socket>: the control bridge, host/control.c) */
    const char *image = NULL;
    /* (hookshot) --compile-shaders all or most: what a preparation of this run builds (gpu/vk/vk_draw.cpp compile_all), over
       compile_shaders= in hookshot_options.ini; as RX_VK_COMPILE_SHADERS, set before --prepare-shaders runs, wherever it stands */
    for (int i = 1; i + 1 < argc; i++)
        if (!strcmp(argv[i], "--compile-shaders")) {
#ifdef _WIN32
            char set[64];
            snprintf(set, sizeof set, "RX_VK_COMPILE_SHADERS=%s", argv[i + 1]);
            _putenv(set);
#else
            setenv("RX_VK_COMPILE_SHADERS", argv[i + 1], 1);
#endif
        }
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--romfs") && i + 1 < argc) hos_romfs_dir = argv[++i];
        else if (!strcmp(argv[i], "--compile-shaders") && i + 1 < argc) i++;   /* (taken above) */
        else if (!strcmp(argv[i], "--user") && i + 1 < argc) hos_user_dir = argv[++i];
        else if (!strcmp(argv[i], "--trace")) hos_trace = 1;
        else if (!strcmp(argv[i], "--prepare-shaders")) {   /* (hookshot) a build's last step: no game */
#ifdef _WIN32
            _putenv("HOOKSHOT_PREPARE_ONLY=1");
#else
            setenv("HOOKSHOT_PREPARE_ONLY", "1", 1);
#endif
            int rx_prepare_shaders(void);
            if (rx_prepare_shaders()) {
                printf("@@done shaders\n");
                return 0;
            }
            printf("@@error no usable Vulkan here: the game prepares its shaders at its first start\n");
            return 3;
        }
        else if (!image) image = argv[i];
    }
#ifdef _WIN32
    {   /* (hookshot) the Discord status (discord_rpc.c), a game run only (a --prepare-shaders run returned above). Linux starts
           it in its own main, below; on Windows it was never started: no status and no "discord:" line in any log */
        void hos_discord_start(void);
        hos_discord_start();
    }
#endif
    static char beside_image[MAX_PATH], beside_romfs[MAX_PATH], beside_user[MAX_PATH];
    if (!image) {   /* (hookshot) started with nothing, as a game is: everything beside the executable (the builder's folder) */
        char dir[MAX_PATH];
        const DWORD len = hos_game_path_a(dir, MAX_PATH);
        char *slash = len ? strrchr(dir, SEP) : NULL;
        if (slash) {
            *slash = 0;
            SetCurrentDirectoryA(dir);
            snprintf(beside_image, MAX_PATH, "%s" SEP_S "image.bin", dir);
            snprintf(beside_romfs, MAX_PATH, "%s" SEP_S "romfs", dir);
            snprintf(beside_user, MAX_PATH, "%s" SEP_S "user", dir);
            if (GetFileAttributesA(beside_image) != INVALID_FILE_ATTRIBUTES) {
                image = beside_image;
                if (!hos_romfs_dir) hos_romfs_dir = beside_romfs;
#ifndef _WIN32
                if (!hos_user_dir) hos_user_dir = saves_dir(beside_user);
#else
                if (!hos_user_dir) hos_user_dir = beside_user;
#endif
            }
        }
    }
    if (!image) {
        fprintf(stderr, "usage: %s [image.bin] [--romfs <dir>] [--user <dir>] [--trace] [--prepare-shaders] [--compile-shaders all|most]\n",
                argv[0]);
        return 2;
    }
    if (env_on("HOS_TRACE")) hos_trace = 1;
    if (!hos_user_dir) hos_user_dir = "user";
    CreateDirectoryA(hos_user_dir, NULL);
    if (!reserve_guest()) {
        fprintf(stderr, "cannot reserve the guest window\n");
        return 1;
    }
#ifdef A64_MEM_ZERO
    web_fs_init(NULL, hos_romfs_dir);
    /* (the whole file, no range: the server sends it brotli-compressed, 85 -> 26 MB) */
    const int got = web_fetch_all(image, A64_MEM + IMAGE_BASE, GUEST_SPACE - IMAGE_BASE);
    if (got < 0) {
        fprintf(stderr, "cannot fetch %s\n", image);
        return 1;
    }
    size_t n = (size_t)got;
#else
    FILE *f = fopen(image, "rb");
    if (!f) {
        fprintf(stderr, "cannot open %s\n", image);
        return 1;
    }
    /* exactly the file's size: a read into "the rest of the guest window" (~4 GB) made Windows commit physical memory
       for ~2 GB of it before the game ran (the whole heap was resident from the start, ~1.3 GB of it never used) */
    fseek(f, 0, SEEK_END);
    const long long file_size = _ftelli64(f);
    fseek(f, 0, SEEK_SET);
    if (file_size > 0) hos_commit(IMAGE_BASE, (uint64_t)file_size);
    size_t n = file_size > 0 && (uint64_t)file_size <= GUEST_SPACE - IMAGE_BASE
                   ? fread(A64_MEM + IMAGE_BASE, 1, (size_t)file_size, f) : 0;
    fclose(f);
#endif
    if (IMAGE_BASE + n != hos_image_end) {
        fprintf(stderr, "%s: %zu bytes, the lifted code expects %#llx: lift and image do not match\n", image, n,
                (unsigned long long)(hos_image_end - IMAGE_BASE));
        return 1;
    }
    hos_mem_init(hos_image_end);
    {   /* (hookshot) Widescreen and ultrawide (nx2/oot_wide.py): HOS_ASPECT=<ratio> (2.3889 for 3440x1440), else the Insert menu's
           Aspect ratio (on unless it says 0) takes the main monitor's shape. The game's picture takes that shape at its own height (the
           world wider at the sides on an ultrawide screen, narrower on a 16:10 or 4:3 one), its 16:9 cameras the picture's ratio.
           An output that is not 16:9 takes precedence over both HOS_ASPECT and the Aspect ratio, matching the menu's picture. Wider
           than 16:9 the HUD, menus and movies keep the middle 16:9; narrower they fill the picture's width. Until now the one 16:9
           constant in the game's data was changed and the 16:9 picture stretched to the window. */
        const char *e = getenv("HOS_ASPECT");
        float want = e ? (float)atof(e) : 0.0f;
        if (!e) {
            int saved = -1, version = 0;   /* (the Aspect ratio's ultrawide= and options_version=, -1 / 0 without them) */
            char path[MAX_PATH];
            const DWORD len = hos_game_path_a(path, MAX_PATH);
            char *slash = len ? strrchr(path, SEP) : NULL;
            if (slash) {
                snprintf(slash + 1, (size_t)(path + MAX_PATH - slash - 1), "hookshot_options.ini");
                FILE *o = fopen(path, "r");
                if (o) {
                    char line[80];
                    while (fgets(line, sizeof line, o)) {
                        if (!strncmp(line, "ultrawide=", 10)) saved = atoi(line + 10) != 0;
                        if (!strncmp(line, "options_version=", 16)) version = atoi(line + 16);
                    }
                    fclose(o);
                }
            }
            const int sw = GetSystemMetrics(SM_CXSCREEN), sh = GetSystemMetrics(SM_CYSCREEN);
            /* (no line: the monitor's shape; an older file's 16:9 on a screen narrower than 16:9 too: hos_aspect_on) */
            const int on = hos_aspect_on(saved, version, sw, sh);
            if (on && sh > 0) want = (float)sw / (float)sh;
        }
        extern volatile uint64_t hos_wide_out, hos_wide_scene;
        extern volatile float hos_wide_ratio, hos_wide_squeeze, hos_wide_margin;
        uint32_t hos_output_size(int height);
        const uint32_t output_w = hos_output_size(0), output_h = hos_output_size(1);
        /* An output of a shape of its own (the Insert menu's Aspect ratio: 21:9, 16:10, 4:3, a 32:9 surround...) asks for that
           shape, over HOS_ASPECT and Ultrawide, even if the OS reports one monitor (host/hos_wide_size.h) */
        if (hos_output_own_shape(output_w, output_h)) want = (float)output_w / (float)output_h;
        const double game = 16.0 / 9.0;
        /* (a screen narrower than 16:9 too, 16:10 to 5:4: the picture keeps the game's height and is narrower, the
           cameras take its ratio, and the HUD fills its width instead of a middle 16:9 wider than the screen. A 4:3 CRT
           at the 1440 output: picture 1920 x 1440, scene 1440 x 1080, HUD 100%) */
        if (want > game * 1.01 || (want > 1.0f && want < game * 0.99)) {
            const double r = hos_wide_clamp(want);
            /* (the height kept, the width at the screen's ratio, at most hos_wide_max_px() pixels (5120 x 2160's, a 21:9 screen's
               native at the 4K output) with the shape kept: host/hos_wide_size.h, which the Insert menu's Output and Render
               resolution rows share; issue 1031) */
            unsigned ow, oh;
            hos_wide_size(output_h, r, hos_wide_max_px(), &ow, &oh);
            const unsigned sh = oh * 3 / 4, sw = (unsigned)(ow * 0.75 / 8 + 0.5) * 8;   /* (the scene, 3/4 of the picture as shipped) */
            hos_wide_out = (uint64_t)oh << 32 | ow;
            hos_wide_scene = (uint64_t)sh << 32 | sw;
            hos_wide_ratio = (float)ow / (float)oh;
            hos_wide_squeeze = (float)fmin(1.0, game / ((double)ow / oh));   /* (a narrower screen: the whole width) */
            hos_wide_margin = (1.0f - hos_wide_squeeze) / 2.0f;
            hos_aspect_ratio = hos_wide_ratio;
            hos_log("boot: widescreen: picture %ux%u, scene %ux%u, camera ratio %.4f, HUD and movies in the middle %.1f%%", ow, oh, sw, sh,
                    (double)hos_wide_ratio, hos_wide_squeeze * 100.0);
        } else if (want > 1.0f) {
            hos_log("boot: widescreen: a %.4f screen is 16:9: the game's own picture", (double)want);
        }
    }
    hos_log("boot: image %zu MB at %#llx, %llu MB of memory for the game", n >> 20, IMAGE_BASE,
            (unsigned long long)(hos_total_memory >> 20));
#ifndef A64_MEM_ZERO
    memory_notice();
#endif
    void hos_mods_init(const char *image_path);
    hos_mods_init(image);   /* mods.c: <exe folder>\mods (RomFS files) */

    Ctx *c = hos_boot_main_thread(hos_main_stack_size);
    uint64_t sp = c->x[31];
    void hos_prof_start(void);
    hos_prof_start();   /* HOS_PROFILE=<seconds> */
    void hos_chain_start(void);
    hos_chain_start();  /* HOS_CHAIN=<path> */
#ifdef _WIN32
    timeBeginPeriod(1);   /* 1 ms timer resolution: the 60 Hz vsync and the game's frame pacing sleep in short steps */
    no_power_throttling();
    ws_floor();   /* (the game's memory kept in RAM under memory pressure: host/ws_floor.h) */
#endif
    void hos_display_start(void);
    hos_display_start();  /* the game's window; HOS_HEADLESS=1 for none */
    /* nn::init::Start's order (sdk+0xfa060), with rtld's three callbacks done here: f1 only marks rtld initialized,
       f2 is every module's DT_INIT, f3 the DT_FINIs (not needed: we never return from nnMain to unload). */
    call_guest(c, hos_entry_init_libc0, sp, "_init_libc0");
    c->x[0] = hos_thread_handle(hos_self());   /* the main thread's handle */
    c->x[1] = 0;                               /* program arguments: none (an unmapped address) */
    call_guest_args(c, hos_entry_nnosInitialize, sp, "nnosInitialize");
    void hos_thread_adopt_guest(HosThread *t);
    hos_thread_adopt_guest(hos_self());
    call_guest(c, hos_entry_init_libc1, sp, "_init_libc1");
    /* nninitInitializeSdkModule (sdk+0xfa000) only opens the SDK's service clients (sm:, diag, oe, aoc, err, fs,
       pctl) over IPC; the HLE stands in for every one of them, so it is not run. */
    call_guest(c, hos_entry_nninitStartup, sp, "nninitStartup");
    call_guest(c, hos_entry_init_libc2, sp, "_init_libc2");
    for (int i = 0; i < hos_module_count; i++) {
        char what[64];
        snprintf(what, sizeof what, "%s DT_INIT", hos_module_name[i]);
        call_guest(c, hos_module_init[i], sp, what);
    }
    call_guest(c, hos_entry_nnMain, sp, "nnMain");
    hos_log("nnMain returned");
    return 0;
}

#ifndef _WIN32
/* (linux) The window belongs on the process's main thread (SDL), so the game runs on a thread of its own, with the
   stack the guest main thread's lifted code needs (the main thread has the system's 8 MB): host/posix/display_sdl.c */
static int game_argc;
static char **game_argv;
static DWORD WINAPI game_thread(void *p) {
    (void)p;
    ExitProcess((UINT)game_main(game_argc, game_argv));
}
/* (hookshot, linux) Room in the drivers' own shader caches for what the preparation builds. The pipelines the notes predict (~43,000
   in OoT, vk_draw.cpp predict_run) are built into the driver's disk cache alone, not into mkw_vk_pipelines.bin, which is read in whole
   at every start. NVIDIA's cache holds 1 GiB by default and cleans itself down to that as it is written: a full preparation filled
   it to the limit (1026 MB) and the pipelines it dropped were compiled again in play. Mesa's (RADV, ANV) has the same 1 GiB default.
   Raised for this process only, before anything loads Vulkan; a value the player set (or hookshot_env.txt) is kept. Windows: the
   NVIDIA driver takes its size from the control panel's Shader Cache Size, which a program cannot set (readme) */
static void driver_cache_room(void) {
    setenv("__GL_SHADER_DISK_CACHE_SIZE", "10737418240", 0);   /* (10 GiB, bytes) */
    setenv("MESA_SHADER_CACHE_MAX_SIZE", "10G", 0);
}
#include <errno.h>
#include <sys/stat.h>
/* (hookshot, linux) NVIDIA's driver keeps no shader cache at all when __GL_SHADER_DISK_CACHE_PATH names a folder that does not exist:
   it neither creates it nor falls back to ~/.cache/nvidia, and every start compiles every pipeline again (a test of 300 pipelines:
   7.9 ms each at the first start and at the second, against 0.06 ms from the cache; driver 620.70). The folder a player or a
   launcher named is made here, before anything loads Vulkan. Mesa makes its own (MESA_SHADER_CACHE_DIR, parents too). Not needed:
   NVIDIA files the cache by the program's file name alone (bin/hookshot), not its folder, bytes or date, nor VkApplicationInfo, so
   an update in place, a folder moved or copied and every Hookshot folder on the PC share one cache (a cache of another driver or
   GPU is the driver's to drop) */
static void driver_cache_folder(void) {
    const char *dir = getenv("__GL_SHADER_DISK_CACHE_PATH");
    if (!dir || !*dir || strlen(dir) >= 4096) return;
    char path[4096];
    strcpy(path, dir);
    for (char *p = path + 1;; p++) {   /* (each parent, then the folder itself: mkdir -p) */
        const char c = *p;
        if (c != '/' && c) continue;
        *p = 0;
        if (mkdir(path, 0700) != 0 && errno != EEXIST) {
            fprintf(stderr, "vk: __GL_SHADER_DISK_CACHE_PATH %s: cannot make %s (%s): NVIDIA's driver keeps no shader cache\n", dir,
                    path, strerror(errno));
            return;
        }
        if (!c) {   /* (a file already at that name counts as made for mkdir, but the driver can't use it) */
            struct stat st;
            if (stat(path, &st) != 0 || !S_ISDIR(st.st_mode))
                fprintf(stderr, "vk: __GL_SHADER_DISK_CACHE_PATH %s: cannot make it, a file has that name: NVIDIA's driver keeps no shader cache\n", dir);
            return;
        }
        *p = '/';
    }
}
/* (hookshot, linux) RADV_FORCE_VRS_CONFIG_FILE dropped before anything loads Vulkan. The Steam Deck's Game Mode sets it for
   Steam and everything it starts, naming a file it writes the Half Rate Shading rate to (/tmp/radv_vrs.<random>, "1x1" when
   off). When that file exists, RADV forces VRS for the device and puts that into every graphics pipeline's key, so nothing
   built without it is found, in the driver's cache or in ours: measured on a Steam Deck OLED (RADV, Mesa 26.2.0-devel), the
   same install compiled its 32,438 noted pipelines at about 20 a second (25-30 minutes) with the variable naming a file, empty
   or "1x1", and loaded them in 22 s without it or with it naming no file. The preparation (the Builder, a first start from the
   desktop) runs without it, so every start from Game Mode compiled again what it built. Dropped only when it names a file that
   exists, which is when RADV reads it; no other driver reads it. RX_VK_HALF_RATE_SHADING=1 keeps it: Steam's Half Rate Shading
   works for the game then (little: RADV leaves a fragment program that reads its position at full rate, and nearly all of
   ours do), and the starts in Game Mode compile their own pipelines. RADV_FORCE_VRS, a rate someone set by hand, is kept.
   The variable is named in HOOKSHOT_DRIVER_ENV_SET for the renderer's driver cache line. A restart (hos_relaunch.c) starts
   again from the first environment and drops it again */
static void driver_env_choose(void) {
    const char *file = getenv("RADV_FORCE_VRS_CONFIG_FILE");
    struct stat st;
    if (!file || !*file || stat(file, &st) != 0) return;
    const char *keep = getenv("RX_VK_HALF_RATE_SHADING");
    if (keep && !strcmp(keep, "1")) {
        snprintf(driver_env_said, sizeof driver_env_said,
                 "vk: RADV_FORCE_VRS_CONFIG_FILE kept (RX_VK_HALF_RATE_SHADING=1): Steam's Half Rate Shading reaches the game, "
                 "and RADV gives every graphics pipeline another key than the preparation's, so they are compiled again");
    } else {
        snprintf(driver_env_said, sizeof driver_env_said,
                 "vk: RADV_FORCE_VRS_CONFIG_FILE=%.300s dropped: with it RADV gives every graphics pipeline another key than the "
                 "preparation's and compiles them all again; Steam's Half Rate Shading has no effect on the game "
                 "(RX_VK_HALF_RATE_SHADING=1 keeps it)", file);
        unsetenv("RADV_FORCE_VRS_CONFIG_FILE");
        setenv("HOOKSHOT_DRIVER_ENV_SET", "RADV_FORCE_VRS_CONFIG_FILE", 1);
    }
    fprintf(stderr, "%s\n", driver_env_said);
}
int main(int argc, char **argv) {
    int hos_display_init(void);
    void hos_display_loop(void);
    load_hookshot_env();
    game_profile_env();
    driver_cache_room();
    driver_cache_folder();
    driver_env_choose();
    game_argc = argc, game_argv = argv;
    int prepare_shaders = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--prepare-shaders")) prepare_shaders = 1;
        else if (!strcmp(argv[i], "--romfs") || !strcmp(argv[i], "--user") || !strcmp(argv[i], "--compile-shaders")) i++;
    }
    /* The builder's preparation worker needs Vulkan, but no SDL display. */
    const int window = prepare_shaders ? 0 : hos_display_init();
    if (window < 0) {
        hos_log_flush();
        return 1;
    }
    if (!prepare_shaders) {   /* (hookshot) the Discord status (discord_rpc.c): only a game run */
        void hos_discord_start(void);
        hos_discord_start();
    }
    HANDLE t = CreateThread(NULL, 64u << 20, game_thread, NULL, 0, NULL);
    if (!t) {
        fprintf(stderr, "cannot start the game's thread\n");
        return 1;
    }
    SetThreadDescription(t, L"Game");
    if (window) hos_display_loop();   /* (does not return: the window's events until the process ends) */
    WaitForSingleObject(t, INFINITE);
    return 0;
}
#endif
