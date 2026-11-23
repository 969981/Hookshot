/* (hookshot) Ocarina of Time's menu content for the shared renderer's Insert menu (docs/game-packs.md 2.4, game packs a4): the
   general rows it implements, its own words for some of them, its debug tools on the Debug tab with their Teleport and Inventory
   pages, its ocarina note controls, and the translations of all of it (menu_i18n.h). Compiled into Ocarina's runtime only
   (packs/oot/pack.json "host"; nx2/build.py) and handed to the renderer as MkwVkHost.game_menu (oot_pack.c NX2PackHost.menu).
   The texts and the layout are the ones gpu/vk/vk_menu.cpp held until a4, word for word: Ocarina's menu looks and acts as it
   did; what the tools do is the game side's (packs/oot/host/oot_cheats.c, oot_teleport.c). */
#include "nx2_pack.h"
#include "oot_menu.h"
#include "oot_cheat_items.h"
#include "oot_destinations.h"
#include "menu_i18n.h"

#define COUNT(a) ((int)(sizeof(a) / sizeof((a)[0])))

/* ---- the general rows' words for Ocarina (by their hookshot_options.ini key) ---- */
static const NX2MenuText oot_texts[] = {
    {"frame_rate_cap",
     "Limits frames rendered by the game. Generated frames are additional. Changes at once. "
     "30 reduces rendering work and keeps real time even with Game speed set to Per frame. Past 60 is experimental: some "
     "cutscenes and bosses can misbehave (Gohma, the intro's narration). 60 is the safe choice. "
     "Uncapped: as fast as the PC can, still at real time; with V-Sync on, the display's "
     "refresh rate is the limit."},
    {"text_size", "How large the text of dialogue, Navi and signs is drawn."},
    {"skip_opening",
     "Skips the new-game opening (the story movie and Navi waking Link) and leaves later cutscenes, the Deku Tree "
     "meeting, and the title screen alone."},
    {"microphone", "What the ocarina hears when the game's microphone option is on (playing an instrument, or humming)."},
    {"auto_run",
     "Sprint without holding A (or its key). Hold it until Link sprints, then let go: he keeps going. Let go of the "
     "stick or the movement keys, or tap A again, to stop."},
    {"debug_tools",
     "Testing tools: no-clip flight, a free camera, teleport, Link's age, the inventory, the clock, heart protection and "
     "movement speed. Off puts flight, the free camera and heart protection away and Link back to his own speed."},
    {"damage_taken",
     "How much health Link loses from a hit. Normal is unchanged. Half rounds up to the next quarter heart. "
     "Changes apply at once; healing is unchanged. Heart protection takes priority. Debug tools Off makes it Normal "
     "until they are on again."},
    {"controls_hint",
     "Gamepad: the D-pad moves, A binds, B goes back. Ocarina note keys are only notes while "
     "the ocarina waits for one; away from it they do their other jobs."},
};

/* ---- the debug tools, in the Debug tab's order: Player, Travel, Camera & world ---- */
static const char *const oot_hours[] = {"00:00", "01:00", "02:00", "03:00", "04:00", "05:00", "06:00", "07:00",
                                        "08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00",
                                        "16:00", "17:00", "18:00", "19:00", "20:00", "21:00", "22:00", "23:00"};
/* (Movement speed: the choices and their percent of Link's own speed, OOT_OPT_SPEED) */
static const char *const oot_speed_names[] = {"1x", "1.25x", "1.5x", "2x", "3x"};
static const int oot_speeds[] = {100, 125, 150, 200, 300};
static const char *const oot_ages[] = {"Kid", "Adult"};
enum { OOT_LIST_TELEPORT, OOT_LIST_INVENTORY };

static const NX2MenuTool oot_tools[] = {
    /* Player */
    {.kind = NX2_TOOL_TOGGLE, .name = "Heart protection", .help = "Protects Link's hearts. Hits can still knock him back.",
     .confirm = "Toggle", .option = OOT_OPT_HEARTS, .off = 0, .on = 1, .on_max = 1, .start = -1, .env = "HOS_OOT_PROTECT_HEARTS"},
    {.kind = NX2_TOOL_ROW, .name = "damage_taken"},
    {.kind = NX2_TOOL_TOGGLE, .name = "No-clip flight",
     .help = "Enable double-tap Jump for takeoff and landing. Hold Jump to climb or L to descend.", .confirm = "Toggle",
     .option = OOT_OPT_FLIGHT, .off = -1, .on = 0, .on_max = 1, .start = -1},
    {.kind = NX2_TOOL_CHOICE, .name = "Movement speed",
     .help = "How fast Link walks, runs and swims, as a multiple of his own speed. Choose with Left or Right.",
     .option = OOT_OPT_SPEED, .off = 100, .names = oot_speed_names, .values = oot_speeds, .count = COUNT(oot_speeds),
     .widest = "1.25x"},
    {.kind = NX2_TOOL_SWITCH, .name = "Switch age",
     .help = "Swaps Link between child and adult and reloads this map in the other age (the Market when this map has no match).",
     .confirm = "Switch", .button = "Switch", .option = OOT_OPT_AGE, .names = oot_ages, .count = 2, .action = OOT_AGE_ACTION,
     .closes = 1},
    {.kind = NX2_TOOL_LIST, .name = "Inventory",
     .help = "Enter opens the inventory editor: unlock or relock equipment, items, upgrades and quest items, and change amounts.",
     .confirm = "Open", .button = "Edit items", .list = OOT_LIST_INVENTORY},
    /* Travel */
    {.kind = NX2_TOOL_LIST, .name = "Teleport",
     .help = "Opens the map list. Link travels through the game's own map change, in his current age.", .confirm = "Open list",
     .button = "Choose map", .list = OOT_LIST_TELEPORT},
    {.kind = NX2_TOOL_ACTION, .name = "Mark position", .help = "Remember Link's position for this loaded scene.",
     .confirm = "Apply", .button = "Mark", .action = OOT_MARK_ACTION},
    {.kind = NX2_TOOL_ACTION, .name = "Return to mark", .help = "Teleport Link to the marked position in the current scene.",
     .confirm = "Apply", .button = "Return", .action = OOT_RETURN_ACTION},
    /* Camera & world */
    {.kind = NX2_TOOL_TOGGLE, .name = "Free camera",
     .help = "Move and look with your usual controls. Page Up / Page Down or R / L changes height.", .confirm = "Toggle",
     .option = OOT_OPT_CAMERA, .off = 0, .on = 1, .on_max = 1, .start = 0},
    {.kind = NX2_TOOL_CHOICE, .name = "Set time", .help = "Choose an hour with Left or Right. Enter sets the world clock.",
     .confirm = "Set", .names = oot_hours, .count = COUNT(oot_hours), .start = 12, .wrap = 1, .action = OOT_TIME_ACTION},
};
static const NX2MenuGroup oot_groups[] = {{"Player", 6}, {"Travel", 3}, {"Camera & world", 2}};

/* ---- the Teleport and Inventory pages: the destinations (oot_destinations.h) and the items (oot_cheat_items.h) ---- */
#define OOT_NO_SECTION(title)
#define OOT_DEST_CARD(name, note, child_banc, child_start, child_boot, adult_banc, adult_start, adult_boot) {name, note, 0},
static const NX2MenuCard oot_places[] = {OOT_DESTINATIONS(OOT_NO_SECTION, OOT_DEST_CARD)};
#define OOT_ITEM_CARD(label, name, kind) {label, 0, kind},
static const NX2MenuCard oot_items[] = {OOT_CHEAT_ITEMS(OOT_NO_SECTION, OOT_ITEM_CARD)};
/* (each section's title and its first card, counted from the lists' X macros: oot_menu, once) */
static NX2MenuSection oot_place_sections[4], oot_item_sections[8];
static NX2MenuList oot_lists[2];

static const NX2MenuControl oot_controls[] = {
    {HOS_KEY_OC_NOTE_L, "Ocarina note (L)"}, {HOS_KEY_OC_NOTE_R, "Ocarina note (R)"}, {HOS_KEY_OC_NOTE_A, "Ocarina note (A)"},
    {HOS_KEY_OC_NOTE_Y, "Ocarina note (Y)"}, {HOS_KEY_OC_NOTE_X, "Ocarina note (X)"}};

static const NX2GameMenu oot_game_menu = {
    .version = NX2_GAME_MENU_VERSION,
    .size = sizeof(NX2GameMenu),
    .name = "Ocarina of Time",
    .features = NX2_FEATURE_FOV | NX2_FEATURE_TEXT_SPEED | NX2_FEATURE_TEXT_SIZE | NX2_FEATURE_SKIP_OPENING |
                NX2_FEATURE_CAMERA_SPEED | NX2_FEATURE_CAMERA_CENTERING | NX2_FEATURE_AUTO_RUN | NX2_FEATURE_DAMAGE_TAKEN |
                NX2_FEATURE_DEBUG_TOOLS,
    .texts = oot_texts,
    .text_count = COUNT(oot_texts),
    .tools_env = "HOS_OOT_CHEATS",
    .action_option = OOT_OPT_ACTION,
    .tools = oot_tools,
    .tool_count = COUNT(oot_tools),
    .groups = oot_groups,
    .group_count = COUNT(oot_groups),
    .lists = oot_lists,
    .list_count = COUNT(oot_lists),
    .controls = oot_controls,
    .control_count = COUNT(oot_controls),
    .langs = oot_menu_langs,
    .lang_count = COUNT(oot_menu_langs),
};

/* NX2PackHost.menu (oot_pack.c): the table, its lists' sections counted from the lists' X macros at the first call (the
   renderer's, once) */
const NX2GameMenu *oot_menu(void) {
    if (!oot_lists[0].style) {
        int sections = 0, cards = 0;
#define OOT_PLACE_SECTION(title) \
    if (sections < COUNT(oot_place_sections)) oot_place_sections[sections].name = title, oot_place_sections[sections++].first = cards;
#define OOT_PLACE_COUNT(name, note, child_banc, child_start, child_boot, adult_banc, adult_start, adult_boot) cards++;
        OOT_DESTINATIONS(OOT_PLACE_SECTION, OOT_PLACE_COUNT)
        oot_lists[OOT_LIST_TELEPORT] = (NX2MenuList){
            .style = NX2_LIST_PLACES,
            .title = "Teleport",
            .help = "Select a destination, then Travel (or Enter) to go to its map entrance. Link keeps his current age.",
            .cards = oot_places,
            .count = COUNT(oot_places),
            .sections = oot_place_sections,
            .section_count = sections,
            .action = OOT_TELEPORT_ACTION,
        };
        sections = cards = 0;
#define OOT_ITEM_SECTION(title) \
    if (sections < COUNT(oot_item_sections)) oot_item_sections[sections].name = title, oot_item_sections[sections++].first = cards;
#define OOT_ITEM_COUNT_ONE(label, name, kind) cards++;
        OOT_CHEAT_ITEMS(OOT_ITEM_SECTION, OOT_ITEM_COUNT_ONE)
        oot_lists[OOT_LIST_INVENTORY] = (NX2MenuList){
            .style = NX2_LIST_ITEMS,
            .title = "Inventory",
            .help = "Load a save to edit the inventory.",
            .cards = oot_items,
            .count = COUNT(oot_items),
            .sections = oot_item_sections,
            .section_count = sections,
            .action = OOT_ITEM_ACTION,
            .counts = OOT_OPT_ITEMS,
        };
    }
    return &oot_game_menu;
}
