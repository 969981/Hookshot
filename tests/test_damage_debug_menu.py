"""The Debug tab's tools and their order: the game's, from its menu table (docs/game-packs.md a4).

The shared renderer's Debug tab (gpu/vk/vk_menu.cpp) draws the tools the running game's menu table lists (NX2GameMenu, the
pack's packs/<key>/menu/menu.c) in the table's order, in its groups, with Damage taken (a general row of the game's, a ROW
entry) beside Heart protection in Player, not under Developer. The renderer's real rows, visibility, order, groups and
keyboard traversal are compiled with Ocarina's real table (packs/oot/menu/menu.c, as C), handed over as the host does
(MkwVkHost.game_menu), and without a table (a game with none: Mario Kart World, Donkey Kong Bananza): no tools, no Debug tools
row, no Damage taken, the Developer rows alone. The tools' list pages, through the real cheat_press: Teleport keeps its choice
across a visit to the Inventory, which opens on All at its first card. The rows are counted first with no host, as kTop's
static initializer does, and the table is still found once the host is there. No GPU, no game.
"""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def between(text, start, end):
    a = text.index(start)
    return text[a:text.index(end, a)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    cc = "clang" if "clang" in args.cxx else "gcc"
    menu = (ROOT / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8")
    header = (ROOT / "gpu/vk/vk_menu.h").read_text(encoding="utf-8")
    table = block(menu, "Row rows[] = {")
    entries = re.findall(r'\{TAB_DEBUG,[^{}]*\}', table)
    assert len(entries) >= 18
    assert '{TAB_GAMEPLAY, "Damage taken"' not in table
    assert sum('"Damage taken"' in entry for entry in entries) == 1
    # (no game's tools in the renderer: a slot each, named from the game's table)
    for name in ("Heart protection", "No-clip flight", "Teleport", "Switch age", "Movement speed", "Set time"):
        assert '"%s"' % name not in table, name
    source = '#include <algorithm>\n#include <atomic>\n#include <cassert>\n#include <cctype>\n#include <cstdarg>\n#include <cstdio>\n'
    source += '#include <cstring>\n#include <iterator>\n#include <string>\n'
    source += '#define _Static_assert static_assert   // (rx_nvn.h, which vk_api.h includes, is C)\n#include "vk_api.h"\n'
    source += 'struct HostStub { uint32_t version; const NX2GameMenu* (*game_menu)(void); };\n'
    # (no host until mkwvk_init: the rows are counted by a static initializer before it, kTop)
    source += 'static HostStub host_stub = {25, nullptr};\nstatic HostStub* host = nullptr;\n'
    source += 'static void logf(const char*, ...) {}\n'
    source += 'extern "C" const NX2GameMenu* oot_menu(void);\n'
    source += block(header, "struct Options {") + ';\nOptions opts;\n'
    source += block(menu, "enum MenuTab {") + ';\n'
    source += block(menu, "struct Row {") + ';\n'
    for name in ("tx_onoff", "tx_damage_taken", "tx_robust", "tx_intel_compiles"):
        source += block(menu, "const char* " + name + "(") + '\n'
    # Help text is not used by this navigation fixture.
    source += 'const char* hn_robust(int, int) { return nullptr; }\n'
    source += 'const char* hn_astc_rgba8(int, int) { return nullptr; }\n'
    source += 'const char* hn_intel_compiles(int, int) { return nullptr; }\n'
    source += 'Row rows[] = {' + ',\n'.join(entries) + '};\n'
    # (the game's menu table, the tools' state and the list pages: as the renderer has them)
    data = between(menu, "// ---- (game packs a4, docs/game-packs.md 2.4) the running game's menu content",
                   "/// A switch's half (0 or 1) as its name")
    data = data.replace("}  // namespace\n", "").replace("namespace {\n", "")
    source += data
    source += 'constexpr int kRows = sizeof rows / sizeof rows[0];\n'
    source += '''
bool reflex_available() { return false; }
bool antilag_available() { return false; }
bool intel_compiler_row() { return false; }   // (not Intel graphics on Windows: the Developer section's rows as before)
int upscalers_available() { return 1; }
constexpr int DLSS_OK = 0;
int dlss_why() { return 1; }
namespace ur {
bool upscaler_shown(int n) { return n > 1; }
bool model_shown(bool available) { return available; }
}
'''
    for name in ("bool row_shown(", "int tab_row_count(", "int tab_row_to_global(", "const char* ini_key(",
                 "int menu_damage_taken("):
        source += block(menu, name) + '\n'
    source += between(menu, "int tool_total() {", "/// (hookshot) The Debug tab's rows from top down")
    # (a tool's row pressed: the list pages it opens, as menu_tick's confirm does)
    source += 'int page = 0;\nstd::atomic<bool> open_now{true};\n'
    source += block(menu, "void cheat_press(int i, const bool* press, bool& changed) {") + '\n'
    source += r'''
/// The table's tool of that name (its slot), -1 none
static int tool_named(const char* name) {
    const NX2GameMenu* m = menu_game();
    for (int i = 0; m && i < m->tool_count; ++i)
        if (m->tools[i].name && !std::strcmp(m->tools[i].name, name)) return i;
    return -1;
}
static void confirm(int tool) {
    bool press[17] = {}, changed = false;
    press[7] = true;
    cheat_press(tool, press, changed);
    assert(changed);
}
/// Teleport keeps its choice across a visit to the Inventory (and back to the Debug tab between them), as it did when the
/// two pages were the renderer's own; the Inventory opens on All at its first card, as it always has
static void list_choices() {
    const int teleport = tool_named("Teleport"), inventory = tool_named("Inventory");
    assert(teleport >= 0 && inventory >= 0);
    confirm(teleport);
    assert(page == 3 && teleport_choice == 0 && teleport_count() == 31);
    teleport_choice = 5;   // (Lost Woods, as the keys leave it)
    page = 0;
    confirm(inventory);
    assert(page == 4 && item_choice == 0 && inventory_filter == 0 && item_count() > 50);
    item_choice = 7, inventory_filter = 1;
    page = 0;
    confirm(teleport);
    assert(page == 3 && teleport_choice == 5 && !std::strcmp(teleport_name(teleport_choice), "Lost Woods"));
    page = 0;
    confirm(inventory);
    assert(page == 4 && item_choice == 0 && inventory_filter == 0);
}
'''
    source += r'''
static void tools_in() {   // (menu_game_init's part this fixture needs: the slots named, their first values)
    const NX2GameMenu* m = menu_game();
    for (Row& r : rows)
        if (m && r.cheat >= 0 && r.cheat < m->tool_count && m->tools[r.cheat].kind != NX2_TOOL_ROW)
            r.name = m->tools[r.cheat].name, tool_value[r.cheat].store(m->tools[r.cheat].start);
}
int main(int argc, char** argv) {
    const bool game = argc > 1;   // (with Ocarina's table, or none)
    if (game) host_stub.game_menu = oot_menu;
    // (the rows counted before the host is there, as kTop's static initializer does: the table is not asked then, nor is a
    // null kept for later; a4's first run in the game had Ocarina's tools hidden so)
    assert(tab_row_count(TAB_DEBUG) > 0 && !menu_game());
    host = &host_stub;
    assert(game == (menu_game() != nullptr));
    tools_in();
    int order[64], dev, heading;
    const char* expected[] = {"Debug tools", "Heart protection", "Damage taken", "No-clip flight",
        "Movement speed", "Switch age", "Inventory", "Teleport", "Mark position", "Return to mark",
        "Free camera", "Set time"};
    for (int env = 0; env < 2; ++env) for (int enabled = 0; enabled < 2; ++enabled) {
        cheats_env = env;
        opts.debug_tools = enabled;
        opts.damage_taken = 3;
        const int n = debug_rows(order, &dev, &heading);
        assert(heading >= 0);
        if (!game) {   // (no table: no switch, no tools, no Damage taken; the Developer rows alone, and they walk round)
            assert(dev == 0 && n == 5 && !cheats_on());
            for (int i = 0; i < n; ++i) assert(rows[tab_row_to_global(TAB_DEBUG, order[i])].value != &opts.debug_tools);
            for (int i = 0; i < n; ++i) assert(debug_step(order[i], 1) == order[(i + 1) % n]);
            assert(menu_damage_taken() == 1);
            continue;
        }
        assert(dev == (env || enabled ? (int)std::size(expected) : 1));
        assert(n == dev + 5); // Only the existing Developer rows follow.
        for (int i = 0; i < dev; ++i) {
            const Row& row = rows[tab_row_to_global(TAB_DEBUG, order[i])];
            assert(!std::strcmp(row.name, expected[i]));
            assert(debug_step(order[i], 1) == order[i + 1]);
            assert(debug_step(order[i + 1], -1) == order[i]);
            if (i == 2) {
                assert(row.value == &opts.damage_taken && row.cheat == -1);
                assert(row.lo == 0 && row.hi == 3 && row.step == 1);
                assert(!row.restart && row.preset_exempt);
                assert(!std::strcmp(row.text(1), "Normal"));
                assert(!std::strcmp(row.text(3), "Quadruple"));
            }
        }
        assert(debug_step(order[n - 1], 1) == order[0]);
        assert(debug_step(order[0], -1) == order[n - 1]);
        assert(opts.damage_taken == 3); // Hiding the tools must not reset the saved option.
        assert(menu_damage_taken() == (env || enabled ? 3 : 1)); // Hidden means Normal for the host.
    }
    if (game) list_choices();   // (the list pages' choices kept across visits)
    if (game) {   // (the groups as before: Player, Travel, Camera & world, of 6, 3 and 2 tools)
        const NX2GameMenu* m = menu_game();
        assert(m->group_count == 3 && tool_total() == 11);
        assert(!std::strcmp(m->groups[0].title, "Player") && !std::strcmp(m->groups[1].title, "Travel") &&
               !std::strcmp(m->groups[2].title, "Camera & world"));
        const int group_of[] = {0, 0, 0, 0, 0, 0, 1, 1, 1, 2, 2};
        for (int t = 0; t < 11; ++t) assert(tool_group(t) == group_of[t]);
    }
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix="damage-debug-menu-") as tmp:
        stub = Path(tmp) / "nx2_pack.h"   # (the pack's menu C needs only the renderer's interface of the engine's headers)
        stub.write_text('#include "vk_api.h"\n', encoding="utf-8")
        cpp = Path(tmp) / "menu.cpp"
        exe = Path(tmp) / "menu.exe"
        obj = Path(tmp) / "oot_menu.o"
        cpp.write_text(source, encoding="utf-8")
        inc = ["-I", tmp, "-I", str(ROOT / "gpu/vk")]
        subprocess.run([cc, "-std=c11", "-Wall", "-Werror", "-c", *inc, str(ROOT / "packs/oot/menu/menu.c"), "-o", str(obj)],
                       check=True)
        subprocess.run([args.cxx, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-Wno-unused-parameter",
                        "-Wno-missing-field-initializers", *inc, str(cpp), str(obj), "-o", str(exe)], check=True)
        subprocess.run([str(exe), "oot"], check=True)
        subprocess.run([str(exe)], check=True)
    print("PASS: Debug Player order, visibility, choices, default and keyboard traversal, from Ocarina's table and with none")


if __name__ == "__main__":
    main()
