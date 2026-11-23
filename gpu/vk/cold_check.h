// (hookshot) The lost-cache check's record (vk_draw.cpp's notes_lost_by_driver, Longshot #1188): the driver the notes were last
// built again for, because its own cache had lost them. The check asks the driver's cache alone, so with that cache off (NVIDIA's
// shader cache disabled, __GL_SHADER_DISK_CACHE=0, AMD's off) or too small for the notes it finds them lost at every start, and
// every start built them all again before the title. Now they are built again once per driver: a start whose driver is the
// recorded one does not ask, and a new driver (an update, another card) asks again. tests/test_cold_cache_check.py runs it
#pragma once

#include <cstdint>
#include <cstdio>
#include <string>

namespace cold_check {

/// The driver as the record has it: vendor, device, driver version and the pipeline cache's UUID, one line
inline std::string stamp(uint32_t vendor, uint32_t device, uint32_t driver, const uint8_t (&uuid)[16]) {
    char s[96];
    int n = std::snprintf(s, sizeof s, "%08x %08x %08x ", vendor, device, driver);
    for (uint8_t b : uuid) n += std::snprintf(s + n, sizeof s - (size_t)n, "%02x", b);
    return std::string(s, (size_t)n);
}

/// The record at `path`: the stamp of the driver the notes were last built again for, empty when there is none
inline std::string recorded(const std::string& path) {
    std::string r;
    if (FILE* f = std::fopen(path.c_str(), "rb")) {
        char buf[128];
        const size_t n = std::fread(buf, 1, sizeof buf, f);
        std::fclose(f);
        r.assign(buf, n);
        while (!r.empty() && (r.back() == '\n' || r.back() == '\r')) r.pop_back();
    }
    return r;
}

/// Whether this start asks the driver's cache: not when the notes were built again for this driver already
inline bool ask(const std::string& path, const std::string& now) { return recorded(path) != now; }

/// The notes are built again at this start, for the driver `now`: recorded, so that the next start with it does not ask
inline void record(const std::string& path, const std::string& now) {
    if (FILE* f = std::fopen(path.c_str(), "wb")) {
        std::fprintf(f, "%s\n", now.c_str());
        std::fclose(f);
    }
}

}  // namespace cold_check
