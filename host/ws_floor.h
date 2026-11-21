/* (hookshot) The game's memory kept in RAM: a working-set minimum (HOS_WS_FLOOR_MB), Windows only.

   When the PC runs short of memory, Windows trims the processes' working sets, and of each it keeps at least its minimum: a few
   hundred KB unless the process asks for more. A player's session with other programs open had the game trimmed to about
   1.1 GB of its 20 GB of private memory; every touch of the game's memory then faulted it back in, the frame's job threads and
   the submit's capture copies slowed, and the main thread waited for them: frames of 35-150 ms with the renderer idle, 24 a
   thousand frames while under 2 GB stayed resident against 3.5 above. The game's working set capped at that 1.1 GB here gave
   the same frames (hitches of 33 ms or more 21 -> 91 on one walk); at 2 GB or more it did not.

   The minimum asked for is a soft one (QUOTA_LIMITS_HARDWS_MIN_DISABLE): Windows trims below it when memory is critically
   short, so it cannot starve the PC; until then it trims the other processes and the game's own pages above the minimum
   first. Raising it needs SeIncreaseWorkingSetPrivilege, which Windows gives every account, disabled: it is enabled for the
   call. The maximum is left soft too (QUOTA_LIMITS_HARDWS_MAX_DISABLE): the working set grows past it as before.

   Default: a quarter of the RAM, at most 4096 MB. HOS_WS_FLOOR_MB=<MB>: that minimum (at most half the RAM); 0, off, no or
   false: Windows' own minimum, as before. Anything else is the default. */
#ifndef HOS_WS_FLOOR_H
#define HOS_WS_FLOOR_H
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* The minimum in MB for a PC with ram_mb of RAM and the switch's value env (NULL or empty: not set); 0: none */
static inline uint64_t ws_floor_mb(uint64_t ram_mb, const char *env) {
    uint64_t mb = ram_mb / 4 < 4096 ? ram_mb / 4 : 4096;
    if (env && *env) {
        if (*env >= '0' && *env <= '9') mb = strtoull(env, NULL, 10);
        else if (!strcmp(env, "off") || !strcmp(env, "no") || !strcmp(env, "false")) mb = 0;
    }
    return mb > ram_mb / 2 ? ram_mb / 2 : mb;
}

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
/* Sets this process's working-set minimum by the rule above, from env (getenv("HOS_WS_FLOOR_MB")): 1 set, 0 none asked for,
   -1 refused (*err the error). *mb and *ram_mb: the minimum and the PC's RAM, MB */
static inline int ws_floor_apply(const char *env, uint64_t *mb, uint64_t *ram_mb, unsigned long *err) {
    MEMORYSTATUSEX ms;
    ms.dwLength = sizeof ms;
    *mb = *ram_mb = 0, *err = 0;
    if (!GlobalMemoryStatusEx(&ms)) return *err = GetLastError(), -1;
    *ram_mb = ms.ullTotalPhys >> 20;
    *mb = ws_floor_mb(*ram_mb, env);
    if (!*mb) return 0;
    HANDLE tok;
    if (OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &tok)) {
        TOKEN_PRIVILEGES tp;
        tp.PrivilegeCount = 1;
        tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;
        if (LookupPrivilegeValueA(NULL, "SeIncreaseWorkingSetPrivilege", &tp.Privileges[0].Luid))
            AdjustTokenPrivileges(tok, FALSE, &tp, 0, NULL, NULL);
        CloseHandle(tok);
    }
    SIZE_T lo = 0, hi = 0;
    DWORD flags = 0;
    GetProcessWorkingSetSizeEx(GetCurrentProcess(), &lo, &hi, &flags);
    const SIZE_T want = (SIZE_T)*mb << 20, room = (SIZE_T)256 << 20;
    const SIZE_T top = hi > want + room ? hi : want + room;   /* (the maximum at least the minimum: the call's rule) */
    if (SetProcessWorkingSetSizeEx(GetCurrentProcess(), want, top, QUOTA_LIMITS_HARDWS_MIN_DISABLE | QUOTA_LIMITS_HARDWS_MAX_DISABLE))
        return 1;
    *err = GetLastError();
    return -1;
}
#endif
#endif
