// gpu/vk/driver_cache_env.h against stand-in environments: the desktop's, a headless start's, Steam's in the Steam Deck's Game
// Mode. tests/test_driver_cache_env.py builds and runs it.
#include "../gpu/vk/driver_cache_env.h"

#include <cstdio>
#include <map>
#include <set>
#include <string>

static int failed = 0;
static void check(bool ok, const char* what) {
    if (!ok) {
        failed++;
        printf("FAIL: %s\n", what);
    }
}
using Vars = std::map<std::string, std::string>;
static driver_cache_env::Env env_of(const Vars& vars) {
    return [&vars](const char* name) -> const char* {
        const auto it = vars.find(name);
        return it == vars.end() ? nullptr : it->second.c_str();
    };
}
static const std::set<std::string> files{"/tmp/radv_vrs"};
static bool exists(const std::string& p) { return files.count(p) != 0; }

int main() {
    using namespace driver_cache_env;
    const Vars desktop{{"HOME", "/srv/player"}};
    const Vars headless{{"HOME", "/srv/player"}, {"XDG_CACHE_HOME", "/srv/player/Games/Hookshot/state/cache/"}};
    const Vars game_mode{{"HOME", "/srv/player"},
                         {"MESA_SHADER_CACHE_DIR", "/srv/player/.local/share/Steam/steamapps/shadercache/1234567890"},
                         {"MESA_DISK_CACHE_SINGLE_FILE", "1"},
                         {"RADV_FORCE_VRS_CONFIG_FILE", "/tmp/radv_vrs"}};

    // ---- the folders, as Mesa and NVIDIA choose them
    check(mesa_base(env_of(desktop)).path == "/srv/player/.cache", "the default folder is ~/.cache");
    check(mesa_base(env_of(headless)).path == "/srv/player/Games/Hookshot/state/cache", "XDG_CACHE_HOME, without its slash");
    check(mesa_base(env_of(game_mode)).why == "MESA_SHADER_CACHE_DIR", "MESA_SHADER_CACHE_DIR first");
    check(mesa_base(env_of(Vars{{"HOME", "/h"}, {"MESA_GLSL_CACHE_DIR", "/g"}, {"XDG_CACHE_HOME", "/x"}})).path == "/g",
          "MESA_GLSL_CACHE_DIR before XDG_CACHE_HOME");
    check(mesa_base(env_of(Vars{{"HOME", "/h"}, {"MESA_SHADER_CACHE_DIR", ""}})).path == "/h/.cache", "an empty one is not set");
    check(mesa_layout_asked(env_of(game_mode)) == "mesa_shader_cache_sf", "Steam's single file");
    check(mesa_layout_asked(env_of(Vars{{"MESA_DISK_CACHE_SINGLE_FILE", "0"}})).empty(), "=0 asks for nothing");
    // (the old count looked in <XDG_CACHE_HOME or ~/.cache>/mesa_shader_cache_db alone)
    const std::vector<std::string> f = folders(env_of(headless));
    check(f.size() == 4 && f[0] == "/srv/player/Games/Hookshot/state/cache/nvidia/GLCache" &&
              f[1] == "/srv/player/Games/Hookshot/state/cache/mesa_shader_cache" &&
              f[2] == "/srv/player/Games/Hookshot/state/cache/mesa_shader_cache_sf" &&
              f[3] == "/srv/player/Games/Hookshot/state/cache/mesa_shader_cache_db",
          "NVIDIA's and each of Mesa's layouts");
    check(folders(env_of(game_mode))[2] == "/srv/player/.local/share/Steam/steamapps/shadercache/1234567890/mesa_shader_cache_sf",
          "Mesa's folders inside Steam's, not Steam's whole folder");

    // ---- RADV's VRS, as radv_device.c decides it
    check(!radv_vrs_forced(env_of(desktop), true, exists), "nothing set: not forced");
    check(radv_vrs_forced(env_of(game_mode), true, exists), "Game Mode's file on RADV with VRS: forced");
    check(!radv_vrs_forced(env_of(game_mode), false, exists), "not RADV, or a GPU before gfx10.3: never");
    check(!radv_vrs_forced(env_of(Vars{{"RADV_FORCE_VRS_CONFIG_FILE", "/gone"}}), true, exists), "a missing file: not forced");
    check(!radv_vrs_forced(env_of(Vars{{"RADV_FORCE_VRS_CONFIG_FILE", "/gone"}, {"RADV_FORCE_VRS", "2x2"}}), true, exists),
          "a config file set goes first, even missing: the rate is not read");
    check(!radv_vrs_forced(env_of(Vars{{"RADV_FORCE_VRS", "1x1"}}), true, exists), "1x1 is not forced");
    check(radv_vrs_forced(env_of(Vars{{"RADV_FORCE_VRS", "2x2"}}), true, exists), "2x2 is");

    // ---- the records and what the start says
    const Fingerprint d = fingerprint(env_of(desktop), true, false), h = fingerprint(env_of(headless), true, false),
                      g = fingerprint(env_of(game_mode), true, true);
    check(d.keys == h.keys && d.keys != g.keys, "keys: the desktop and a headless start agree, Game Mode does not");
    check(d.folder != h.folder && d.folder != g.folder, "folders: three");
    check(!compare({}, g).other_keys && !compare({}, g).other_folder, "nothing recorded: nothing said");
    std::vector<Fingerprint> rec = records_read(records_text({}, d));   // (the Builder on the desktop)
    check(rec.size() == 1 && rec[0] == d, "one record read back");
    check(compare(rec, g).other_keys, "then Game Mode: other keys");
    check(!compare(rec, h).other_keys && compare(rec, h).other_folder, "then a headless start: the same keys, another folder");
    check(!compare(rec, d).other_keys && !compare(rec, d).other_folder, "then the desktop again: nothing");
    rec = records_read(records_text(rec, g));   // (Game Mode built its pipelines)
    check(rec.size() == 2 && rec[0] == g && rec[1] == d, "newest first, both kept");
    check(!compare(rec, d).other_keys && !compare(rec, d).other_folder, "desktop after Game Mode: both known, no warning");
    check(!compare(rec, g).other_keys && !compare(rec, g).other_folder, "Game Mode after the desktop: both known, no warning");
    rec = records_read(records_text(rec, d));
    check(rec.size() == 2 && rec[0] == d, "a record met again moves to the front, not twice");
    std::vector<Fingerprint> many;
    for (int i = 0; i < 20; i++) many = records_read(records_text(many, {"k", "folder " + std::to_string(i)}));
    check(many.size() == kKept && many[0].folder == "folder 19", "at most kKept, the newest kept");
    check(records_read("garbage\n\tx\nk\t\n").empty(), "lines without both halves are ignored");

    // ---- the log's line
    const std::string gm = describe(env_of(game_mode), true, true, true);
    check(gm.find("MESA_SHADER_CACHE_DIR") != std::string::npos && gm.find("VRS forced") != std::string::npos, "Game Mode's");
    check(gm.find("set by Hookshot") == std::string::npos, "Steam's folder is not Hookshot's");
    Vars ours = desktop;
    ours["MESA_SHADER_CACHE_DIR"] = "/g/cache/driver";
    ours["HOOKSHOT_DRIVER_ENV_SET"] = "MESA_SHADER_CACHE_DIR";
    check(describe(env_of(ours), true, true, false).find("set by Hookshot") != std::string::npos,
          "a Mesa folder the host set is named as Hookshot's");
    // (Game Mode with the host's drop, host/hos_main.c driver_env_choose: the variable gone, named in HOOKSHOT_DRIVER_ENV_SET)
    Vars dropped = game_mode;
    dropped.erase("RADV_FORCE_VRS_CONFIG_FILE");
    dropped["HOOKSHOT_DRIVER_ENV_SET"] = "RADV_FORCE_VRS_CONFIG_FILE";
    check(!radv_vrs_forced(env_of(dropped), true, exists), "dropped: not forced");
    check(describe(env_of(dropped), true, true, false).find("RADV_FORCE_VRS_CONFIG_FILE dropped by Hookshot") != std::string::npos,
          "the drop is named as Hookshot's");
    check(fingerprint(env_of(dropped), true, false).keys == d.keys, "dropped: the keys of a start outside Game Mode");
    check(describe(env_of(Vars{{"HOME", "/h"}, {"RADV_FORCE_VRS_CONFIG_FILE", "/gone"}}), true, true, false).find("names no file") !=
              std::string::npos, "a missing file is said");

    if (!failed) printf("PASS: the drivers' caches as the environment chooses them\n");
    return failed ? 1 : 0;
}
