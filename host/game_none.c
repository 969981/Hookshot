/* (hookshot) The pack of a game whose pack has no C (packs/<key>/pack.json without "host": Donkey Kong Bananza, and an unknown
   game): it takes none of the runtime's hooks (host/nx2_pack.h), so the runtime does what it does without a game. nx2/build.py
   compiles this into such a game's runtime in place of a pack's host/ files. */
#include "nx2_pack.h"

static const NX2PackHost none = {
    .version = NX2_PACK_API_MAJOR << 16 | NX2_PACK_API_MINOR,
    .size = sizeof(NX2PackHost),
    .key = "none",
};

const NX2PackHost *nx2_pack_host(void) { return &none; }
