# In-game actions: the mechanisms behind the MCP tools

Research notes for [the MCP server](mcp.md)'s player actions (equip, held item, ocarina song), from the build the port
supports today (`e0f3ccfa`). The addresses are the guest image's (`I:\Hookshot\image.bin`, loaded at `0x80000000`, the
main module at `0x80020000`) and are **build specific**: a new dump build moves them, so treat them as a map of what to
look for, not constants.

Nothing here is needed to build or play the game, and nothing here is game data: they are addresses, offsets and the
game's own class and field names.

## Why this exists

A player's equipment, the item on the item button and the ocarina are not port settings: they live in the running
player and in the game's GameData. The fourth design rule ([design notes, section 8](design.md#8-rules)) asks every player
action to be reachable through the MCP server, so these are the seams such an action needs.

## The game's one-shot action classes

The names in the binary are classes in the game's action system. Each has a descriptor block in data whose +0xE0 slot
holds the entry function; the name getter (`std::type_info`-style, just returns the string) is at +0x40. Found by taking
each string's address and scanning the image for the single ADRP+ADD that references it.

| Action | name getter | descriptor | entry |
|---|---|---|---|
| `OneShotAddOrRemovePouchContent` (the port's pouch edit calls this) | `0x828d51f0` | `0x84a10b50` | `0x828d4a70` |
| `OneShotPlayerSetSelectedItem` | `0x829310f0` | `0x84a408c0` | `0x82931000` |
| `OneShotSyncPlayerEquipmentAndPouch` | `0x82972050` | `0x84a603a0` | `0x82971f50` |

Calling one: `x0` is the action object; the result word is written to `[x0+0x18]` as `0x80000000 | flags` (the pouch
edit reads it as `>> 30`). `OneShotSyncPlayerEquipmentAndPouch` takes no other input: it reloads the pouch singleton
(`0x84c9e0d0`) and syncs. `OneShotPlayerSetSelectedItem` does not take an item either — it applies whatever the pause
menu left in the player status.

## The pause menu's item selection

```
sel = [[player + 0x270] + 0x358] + 0x7c0        (player is the actor the port's player hook is given)
sel + 0x110  = the player itself                  (the chain is self-consistent)
sel + 0x10   = the item id under the cursor        (4 while the Hookshot was selected)
sel + 0x18   = the applier's result word
```

`0x83036730(sel)` reads `[sel+0x10]` and calls `0x83035be0(sel, id)`, which stores the id at `[sel+0x18]` and passes
`(current = [status+0x1334], new = id)` to the item UI's `vtable[0x70]`. The port calls it; it does **not** change the
armed item in play, because that UI only exists while the pause menu is open — and the pause menu pauses the player
update, which is where a live command's action is taken.

The item grid is 32 ids at `status+0x1300..0x137C` (from this save: `2, 6, 12, 14, 13, 3, 15, 4, 5, 17, 18, 19, 21, 23,
22, 7, 8, 9, 25, 16, 29, 30, 31, 26, 32, 24, 10, 11, 20, 28, 27, 33`). Entry 13 (`status+0x1334`) is the slingshot
(id 23) and is the value the HUD and the applier treat as the current item. Writing it puts that item in Link's hand,
but the grid is a *copy* of GameData: reopening the pause menu rebuilds it, so the button assignment persists only if
GameData is written.

## Equipment

The current equipment ids are player words:

| Word | Values seen | Meaning |
|---|---|---|
| `player+0x20c` | `0x79` Deku Shield, `0xb6` Hylian Shield | **shield** (confirmed: writing it swaps the shield on Link's back and the Equipment tab follows) |
| `player+0x218` | same as `+0x20c` | mirror of the shield |
| `player+0x210`, `+0x214`, `+0x21C` | `0xa1`, `0xb5`, `0` | the other slots (sword / tunic / boots), not yet mapped |
| `player+0x1e0..0x20c` | `0x6e..0x79`, consecutive | fixed slot/type ids, unaffected by equipping |

Writing `0x79` to `+0x20c` and `+0x218` while the game is unpaused is enough: no function call is needed. The item ids
are the game's item ids; the ids of equipment this save does not own are still unknown.

## The pause menu's navigation

D-pad moves between the cells of a page; at a row's edge it switches tab (Collection, Equipment, Items, System). A gold
cell marks the equipped item on the Equipment page and the quick-slot item on the Items page. `A` on a cell acts on it:
on the Equipment page it equips (that is how the shield word above was proved). `ZR` inside the menu switches tab; `A`
on an Items cell was not found to assign the item. In play, `R`/`L` quick-select do nothing while only one item is in the
quick slots.

## The ocarina

`OcarinaMgr`'s class metadata is at `0x84bc1938`: name getter `0x832b94b0`, methods from `0x832b9390` to `0x832bbe80`.
The melody enum's labels are the strings `OcarinaMelodyZeldaLullaby`, `OcarinaMelodySariaSong`, `OcarinaMelodyTimeSong`,
`OcarinaMelodyStormSong`, `OcarinaMelodySunSong`, `OcarinaMelodyEponaSong`, `OcarinaMelodyForestMinuet`,
`OcarinaMelodyFireBolero`, `OcarinaMelodyWaterSerenade`, `OcarinaMelodyShadowNocturne`, `OcarinaMelodySpiritRequiem`,
`OcarinaMelodyLightPrelude` and `OcarinaMelodyScarecrowSong`. `Dm_SY_Ocarina` is the event a scripted performance uses
(the same event route that made cross-map teleport possible). Neither the manager's play method nor the event's
parameters were identified.

## What is still open

- The GameData setter (a sibling of the lookup `0x8022d9c0`) and the hash of the item shortcut field: writing GameData
  is what makes a held-item change persist and reach the HUD.
- The equipment words for sword, tunic and boots, and the ids of equipment not in this save.
- `OcarinaMgr`'s play-melody method or the `Dm_SY_Ocarina` parameters.

The commands used to probe all this (numeric live actions: `9001`-`9004` dumps, `9100+id` menu applier, `9200+id` equip,
`9500+id` armed item, `9700/9800/9900` quick slots) are development-only and are not part of the MCP server.
