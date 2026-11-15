/* nn::ro: the modules a game loads at run time (romfs .nro files), answered from the lift.

A static recompilation cannot load code at run time. So the lift takes the dump's NROs as more modules, after sdk
(nx2/nro.py, nx2/image.py), links them against the rest and lifts them with the game: their code and data are in the
image from the start, at the address the lift gave them, and gen/hos_image.c lists them (hos_nro_*). LoadModule finds
the one the game hands in by its build id (the NRO header's, at 0x40), and runs its DT_INIT the first time, as the
console's loader runs a module's initializers; the image the game read from its romfs is not used past that. Each
module is loaded once: it stays, an UnloadModule leaves it as it is and a LoadModule after it finds it initialized.
LookupModuleSymbol reads the module's own dynamic symbol table, in guest memory, as the console's does.

Found on Kirby Air Riders (Dragoon), whose effect scripts are one NRO (romfs Bin/Module/EffectScript.nro): with no
answers, GetBufferSize left its size as stack garbage and the game asked for 0x7fe8080000 bytes. */
#include "hos.h"
#include <string.h>

/* gen/hos_image.c (nx2/standalone.py write_image_c) */
extern const int hos_nro_count;
extern const char *const hos_nro_name[], *const hos_nro_build_id[];
extern const uint64_t hos_nro_base[], hos_nro_init[];

#define RO_MODULE_MAGIC 0x4D4F5248u        /* "HROM": a Module this file filled (its first word) */
#define RO_RESULT_NOT_FOUND (22 | (1u << 9))    /* (ro module 22; a module this lift does not have) */
#define RO_RESULT_NO_SYMBOL (22 | (2u << 9))    /* (ro module 22; no such symbol in the module) */

static int nro_inited[64];   /* (each module's DT_INIT run once: an atomic flag) */

HLE(_ZN2nn2ro10InitializeEv) { RET(0); }
HLE(_ZN2nn2ro8FinalizeEv) { RET(0); }

/* RegisterModuleInfo(RegistrationInfo*, void const* nrr) -> Result: the .nrr lists the hashes of the NROs the game may
   load; every lifted one may, so it is taken as it is */
HLE(_ZN2nn2ro18RegisterModuleInfoEPNS0_16RegistrationInfoEPKv) {
    static int told;
    if (!told++) hos_log("ro: RegisterModuleInfo (%d module%s lifted with the game)", hos_nro_count,
                         hos_nro_count == 1 ? "" : "s");
    RET(0);
}
HLE(_ZN2nn2ro20UnregisterModuleInfoEPNS0_16RegistrationInfoE) { RET(0); }

/* GetBufferSize(size_t* out, void const* nro) -> Result: the buffer the console's loader wants for the module's bss,
   the NRO header's bss size page-aligned (its bss lives in the lifted image here, so the buffer is not used) */
HLE(_ZN2nn2ro13GetBufferSizeEPmPKv) {
    const uint64_t out = ARG(0), image = ARG(1);
    uint64_t size = 0;
    if (image && hos_guest_range(image, 0x60)) size = ((uint64_t)G32(image + 0x38) + 0xFFF) & ~0xFFFULL;
    if (out && hos_guest_range(out, 8)) G64(out) = size;
    RET(0);
}

static int nro_of_image(uint64_t image) {
    if (!image || !hos_guest_range(image, 0x60) || memcmp(GP(image + 0x10), "NRO0", 4)) return -1;
    char hex[65];
    const uint8_t *id = (const uint8_t *)GP(image + 0x40);
    for (int i = 0; i < 32; i++) snprintf(hex + 2 * i, 3, "%02x", id[i]);
    for (int i = 0; i < hos_nro_count; i++) {   /* (the lift's ids are the header's with its trailing zeros cut) */
        const size_t n = strlen(hos_nro_build_id[i]);
        if (n && !strncmp(hex, hos_nro_build_id[i], n)) return i;
    }
    return -1;
}

/* LoadModule(Module* out, void const* nro, void* buffer, size_t size, int bind) -> Result */
HLE(_ZN2nn2ro10LoadModuleEPNS0_6ModuleEPKvPvmi) {
    const uint64_t out = ARG(0), image = ARG(1);
    const int i = nro_of_image(image);
    if (i < 0 || !out || !hos_guest_range(out, 0x20)) {
        hos_log("ro: LoadModule of a module this lift does not have (image %#llx, lr %#llx): answered not found",
                (unsigned long long)image, (unsigned long long)c->x[30]);
        RET(RO_RESULT_NOT_FOUND);
        return;
    }
    memset(GP(out), 0, 0x20);
    G32(out) = RO_MODULE_MAGIC;
    G32(out + 4) = (uint32_t)i;
    G64(out + 8) = hos_nro_base[i];
    int unset = 0;
    if (i < (int)(sizeof nro_inited / sizeof *nro_inited) &&
        __atomic_compare_exchange_n(&nro_inited[i], &unset, 1, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
        hos_log("ro: LoadModule %s at %#llx (lr %#llx)%s", hos_nro_name[i], (unsigned long long)hos_nro_base[i],
                (unsigned long long)c->x[30], hos_nro_init[i] ? ", its DT_INIT run" : "");
        if (hos_nro_init[i]) {
            void *const saved = __builtin_alloca(sizeof c->x);   /* (the caller's registers, as the call left them) */
            memcpy(saved, c->x, sizeof c->x);
            uint64_t hos_call_guest(Ctx *c, uint64_t fn, uint64_t a0, uint64_t a1, uint64_t a2, uint64_t a3);
            hos_call_guest(c, hos_nro_init[i], 0, 0, 0, 0);
            memcpy(c->x, saved, sizeof c->x);
        }
    }
    RET(0);
}

/* LookupModuleSymbol(uintptr_t* out, Module const*, char const* name) -> Result: from the module's .dynsym (its
   MOD0 -> .dynamic -> DT_SYMTAB / DT_STRTAB, offsets from the module's base, as the console's loader reads them) */
HLE(_ZN2nn2ro18LookupModuleSymbolEPmPKNS0_6ModuleEPKc) {
    const uint64_t out = ARG(0), mod = ARG(1), name_at = ARG(2);
    if (!mod || !hos_guest_range(mod, 0x10) || G32(mod) != RO_MODULE_MAGIC || !name_at) {
        RET(RO_RESULT_NOT_FOUND);
        return;
    }
    const uint64_t base = G64(mod + 8);
    const char *name = (const char *)GP(name_at);
    const uint64_t mod0 = base + G32(base + 4);
    uint64_t symtab = 0, strtab = 0, syment = 24;
    for (uint64_t d = mod0 + (int32_t)G32(mod0 + 4); hos_guest_range(d, 16); d += 16) {
        const int64_t tag = (int64_t)G64(d);
        if (!tag) break;
        if (tag == 5) strtab = G64(d + 8);        /* DT_STRTAB */
        else if (tag == 6) symtab = G64(d + 8);   /* DT_SYMTAB */
        else if (tag == 11) syment = G64(d + 8);  /* DT_SYMENT */
    }
    if (symtab && strtab > symtab && syment)
        for (uint64_t s = base + symtab; s < base + strtab; s += syment) {
            const uint16_t shndx = *(const uint16_t *)GP(s + 6);
            if (shndx && !strcmp((const char *)GP(base + strtab + G32(s)), name)) {
                if (out && hos_guest_range(out, 8)) G64(out) = base + G64(s + 8);
                RET(0);
                return;
            }
        }
    static int told;
    if (told++ < 8) hos_log("ro: LookupModuleSymbol %s: not in the module (lr %#llx)", name, (unsigned long long)c->x[30]);
    RET(RO_RESULT_NO_SYMBOL);
}

/* UnloadModule(Module*) -> Result: the module stays (its code is the lifted image's); the Module is let go */
HLE(_ZN2nn2ro12UnloadModuleEPNS0_6ModuleE) {
    if (ARG(0) && hos_guest_range(ARG(0), 4) && G32(ARG(0)) == RO_MODULE_MAGIC) G32(ARG(0)) = 0;
    RET(0);
}
