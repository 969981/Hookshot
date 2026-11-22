// (hookshot, Linux) The drivers' own shader caches as the environment chooses them, and what in the environment changes the keys
// RADV files a graphics pipeline under: what the start's "vk: driver shader cache:" lines report. A pipeline built in one
// environment is found in another only when both give it the same key, and what is in the driver's cache alone (the predicted
// pipelines) only when both use the same folder. The Builder's preparation and a start from the desktop run with the session's
// environment, a start from Steam with Steam's (MESA_SHADER_CACHE_DIR at steamapps/shadercache/<app id>; in the Steam Deck's Game
// Mode also RADV_FORCE_VRS_CONFIG_FILE, for Steam's Half Rate Shading).
//  - Mesa: MESA_SHADER_CACHE_DIR (MESA_GLSL_CACHE_DIR before it), else $XDG_CACHE_HOME, else ~/.cache; inside it one folder per
//    layout: mesa_shader_cache (many files), mesa_shader_cache_sf (MESA_DISK_CACHE_SINGLE_FILE), mesa_shader_cache_db
//    (MESA_DISK_CACHE_DATABASE). Which is Mesa's default has changed between versions, so every one there is counted.
//  - NVIDIA: __GL_SHADER_DISK_CACHE_PATH/GLCache, else ~/.cache/nvidia/GLCache.
//  - RADV's VRS: on a GPU where RADV can force VRS (gfx10.3 and later, where it has VK_KHR_fragment_shading_rate),
//    RADV_FORCE_VRS_CONFIG_FILE naming a file that exists (its content does not matter), or without that variable RADV_FORCE_VRS
//    other than 1x1, gives every graphics pipeline another key. Measured on a Steam Deck OLED (RADV, Mesa 26.2.0-devel): with
//    Game Mode's file (empty or "1x1") the 32,438 noted pipelines compiled at about 20 a second instead of loading in 22 s; the
//    variable naming no file, or another Mesa folder alone, changed nothing.
// Standard library only: tests/driver_cache_env_test.cpp builds it with a stand-in environment.
#pragma once
#include <cstring>
#include <functional>
#include <string>
#include <vector>

namespace driver_cache_env {
using Env = std::function<const char*(const char*)>;            // (getenv, or a test's)
using Exists = std::function<bool(const std::string& path)>;   // (a file is there, or a test's)

inline std::string get(const Env& env, const char* name) {
    const char* v = env(name);
    return v ? v : "";
}
/// As Mesa reads a yes/no switch (debug_get_bool_option): set and not 0, n, no or false
inline bool mesa_yes(const Env& env, const char* name) {
    const char* v = env(name);
    if (!v) return false;
    for (const char* no : {"0", "n", "N", "no", "NO", "No", "false", "FALSE", "False"})
        if (!strcmp(v, no)) return false;
    return true;
}
/// HOOKSHOT_DRIVER_ENV_SET: the names the host set or dropped before Vulkan loaded (a comma-separated list), so the log does not
/// take Hookshot's choice for the player's or Steam's
inline bool set_by_hookshot(const Env& env, const char* name) {
    const std::string list = "," + get(env, "HOOKSHOT_DRIVER_ENV_SET") + ",";
    return list.find("," + std::string(name) + ",") != std::string::npos;
}
struct Folder {
    std::string path;   // (no separator at the end)
    std::string why;    // (the variable that chose it, or "the default")
};
inline std::string trimmed(std::string p) {
    while (p.size() > 1 && p.back() == '/') p.pop_back();
    return p;
}
/// The folder Mesa keeps its layouts' folders in
inline Folder mesa_base(const Env& env) {
    for (const char* name : {"MESA_SHADER_CACHE_DIR", "MESA_GLSL_CACHE_DIR"})
        if (!get(env, name).empty()) return {trimmed(get(env, name)), name};
    if (!get(env, "XDG_CACHE_HOME").empty()) return {trimmed(get(env, "XDG_CACHE_HOME")), "XDG_CACHE_HOME"};
    return {trimmed(get(env, "HOME")) + "/.cache", "the default"};
}
inline const std::vector<const char*>& mesa_layouts() {
    static const std::vector<const char*> names{"mesa_shader_cache", "mesa_shader_cache_sf", "mesa_shader_cache_db"};
    return names;
}
/// The layout asked for in the environment, or "" for Mesa's own default
inline std::string mesa_layout_asked(const Env& env) {
    if (mesa_yes(env, "MESA_DISK_CACHE_SINGLE_FILE")) return "mesa_shader_cache_sf";
    if (mesa_yes(env, "MESA_DISK_CACHE_DATABASE")) return "mesa_shader_cache_db";
    if (mesa_yes(env, "MESA_DISK_CACHE_MULTI_FILE")) return "mesa_shader_cache";
    return "";
}
inline Folder nvidia_folder(const Env& env) {
    if (!get(env, "__GL_SHADER_DISK_CACHE_PATH").empty())
        return {trimmed(get(env, "__GL_SHADER_DISK_CACHE_PATH")) + "/GLCache", "__GL_SHADER_DISK_CACHE_PATH"};
    const std::string xdg = get(env, "XDG_CACHE_HOME");
    return {(xdg.empty() ? trimmed(get(env, "HOME")) + "/.cache" : trimmed(xdg)) + "/nvidia/GLCache",
            xdg.empty() ? "the default" : "XDG_CACHE_HOME"};
}
/// Every folder the drivers' caches may be in, for their size: NVIDIA's, then each of Mesa's layouts in its base folder (only
/// Mesa's own there: Steam's folder for a game holds Fossilize's and DXVK's too)
inline std::vector<std::string> folders(const Env& env) {
    std::vector<std::string> all{nvidia_folder(env).path};
    const std::string base = mesa_base(env).path;
    for (const char* layout : mesa_layouts()) all.push_back(base + "/" + layout);
    return all;
}
/// RADV's "VRS forced" (see the top: the file must exist, as measured): radv_vrs is the device being RADV on a GPU where it can force
/// VRS. The config file goes first, and when it is set the rate variable is not read even if the file is missing
inline bool radv_vrs_forced(const Env& env, bool radv_vrs, const Exists& exists) {
    if (!radv_vrs) return false;
    if (const char* file = env("RADV_FORCE_VRS_CONFIG_FILE")) return *file && exists(file);
    const std::string rate = get(env, "RADV_FORCE_VRS");
    return !rate.empty() && rate != "1x1";
}
/// What decides whether one start's pipelines are found by another: the keys (what changes them), and the driver's folder
struct Fingerprint {
    std::string keys, folder;
    bool operator==(const Fingerprint& o) const { return keys == o.keys && folder == o.folder; }
};
inline Fingerprint fingerprint(const Env& env, bool mesa, bool vrs_forced) {
    if (!mesa) return {"NVIDIA's own keys", "NVIDIA's cache " + nvidia_folder(env).path};
    const std::string layout = mesa_layout_asked(env);
    std::string folder = "Mesa's cache in " + mesa_base(env).path + " (" + (layout.empty() ? "Mesa's default layout" : layout) + ")";
    if (mesa_yes(env, "MESA_SHADER_CACHE_DISABLE")) folder += ", turned off";
    return {vrs_forced ? "RADV's VRS forced" : "RADV's VRS not forced", folder};
}
/// hookshot_driver_env.txt: the fingerprints of the starts that built the noted pipelines here, newest first, one a line
/// ("<keys>\t<folder>"), at most kKept
constexpr size_t kKept = 8;
inline std::vector<Fingerprint> records_read(const std::string& text) {
    std::vector<Fingerprint> out;
    size_t at = 0;
    while (at < text.size() && out.size() < kKept) {
        size_t end = text.find('\n', at);
        if (end == std::string::npos) end = text.size();
        std::string line = text.substr(at, end - at);
        at = end + 1;
        if (!line.empty() && line.back() == '\r') line.pop_back();
        const size_t tab = line.find('\t');
        if (tab == std::string::npos || !tab || tab + 1 >= line.size()) continue;
        out.push_back({line.substr(0, tab), line.substr(tab + 1)});
    }
    return out;
}
inline std::string records_text(std::vector<Fingerprint> records, const Fingerprint& now) {
    std::string out = now.keys + "\t" + now.folder + "\n";
    size_t n = 1;
    for (const Fingerprint& r : records)
        if (!(r == now) && n < kKept) out += r.keys + "\t" + r.folder + "\n", n++;
    return out;
}
/// What the start says against the records: other keys than any build here had (every pipeline is another one to the caches,
/// ours included), or the same keys in a driver folder no build here used (what is in the driver's cache alone is not found)
struct Verdict {
    bool other_keys = false, other_folder = false;
    std::string keys_before, folder_before;   // (the newest record's, for the message)
};
inline Verdict compare(const std::vector<Fingerprint>& records, const Fingerprint& now) {
    Verdict v;
    if (records.empty()) return v;
    bool keys_met = false, both_met = false;
    for (const Fingerprint& r : records) keys_met |= r.keys == now.keys, both_met |= r == now;
    v.other_keys = !keys_met;
    v.other_folder = keys_met && !both_met;
    v.keys_before = records[0].keys;
    for (const Fingerprint& r : records)
        if (r.keys == now.keys) { v.folder_before = r.folder; break; }
    if (v.folder_before.empty()) v.folder_before = records[0].folder;
    return v;
}
/// The log's line for the environment this start runs with: the folder and what chose it, and what changes RADV's keys
inline std::string describe(const Env& env, bool mesa, bool radv_vrs, bool vrs_forced) {
    if (!mesa) {
        const Folder f = nvidia_folder(env);
        return "NVIDIA's cache " + f.path + " (" + f.why + ")";
    }
    const Folder f = mesa_base(env);
    const std::string layout = mesa_layout_asked(env);
    std::string s = "Mesa's cache in " + f.path + " (" + f.why + (set_by_hookshot(env, f.why.c_str()) ? ", set by Hookshot" : "") +
                    "), layout " + (layout.empty() ? "Mesa's default" : layout);
    if (mesa_yes(env, "MESA_SHADER_CACHE_DISABLE")) s += ", turned off (MESA_SHADER_CACHE_DISABLE)";
    const char* file = env("RADV_FORCE_VRS_CONFIG_FILE");
    if (vrs_forced)
        s += file ? "; RADV's VRS forced by RADV_FORCE_VRS_CONFIG_FILE (the Steam Deck's Game Mode sets it for Half Rate Shading)"
                  : "; RADV's VRS forced by RADV_FORCE_VRS=" + get(env, "RADV_FORCE_VRS");
    else if (set_by_hookshot(env, "RADV_FORCE_VRS_CONFIG_FILE"))
        s += "; RADV_FORCE_VRS_CONFIG_FILE dropped by Hookshot, so RADV's VRS is not forced (RX_VK_HALF_RATE_SHADING=1 keeps it)";
    else if (file && radv_vrs)
        s += "; RADV_FORCE_VRS_CONFIG_FILE names no file: RADV does not force VRS";
    else if (file || env("RADV_FORCE_VRS"))
        s += "; RADV's VRS switches set, without effect on this driver or GPU";
    else
        s += "; RADV's VRS not forced";
    return s;
}
}  // namespace driver_cache_env
