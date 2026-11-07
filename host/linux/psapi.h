/* (hookshot) Linux: the process's memory counters (host/linux/win32.c, from /proc/self/status) */
#include <windows.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    DWORD cb, PageFaultCount;
    SIZE_T PeakWorkingSetSize, WorkingSetSize, QuotaPeakPagedPoolUsage, QuotaPagedPoolUsage, QuotaPeakNonPagedPoolUsage,
        QuotaNonPagedPoolUsage, PagefileUsage, PeakPagefileUsage;
} PROCESS_MEMORY_COUNTERS;
typedef struct {
    DWORD cb, PageFaultCount;
    SIZE_T PeakWorkingSetSize, WorkingSetSize, QuotaPeakPagedPoolUsage, QuotaPagedPoolUsage, QuotaPeakNonPagedPoolUsage,
        QuotaNonPagedPoolUsage, PagefileUsage, PeakPagefileUsage, PrivateUsage;
} PROCESS_MEMORY_COUNTERS_EX;
BOOL K32GetProcessMemoryInfo(HANDLE process, PROCESS_MEMORY_COUNTERS *out, DWORD size);
#define GetProcessMemoryInfo K32GetProcessMemoryInfo
#ifdef __cplusplus
}
#endif
