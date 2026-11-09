/* (hookshot) The control bridge (host/control.c, docs/control.md): HOS_CONTROL=<socket path> lets a tool on the same PC
   drive the running game over a local socket, one JSON request a line. Off unless that is set. */
#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* hos_main.c, at the start: listens when HOS_CONTROL is set (version: the build's, for status) */
void hos_control_start(const char *version);
/* nn_hid.c, each read of the controller: the bridge's presses added to buttons and stick. 1 while one of them asked for
   the player's own devices to be left out (exclusive) */
int hos_control_input(uint64_t *buttons, int32_t stick[4]);
/* hos_thread.c, each log line: for the clients that subscribed to log lines */
void hos_control_log(const char *line);
extern volatile int hos_control_on;

/* One of the Insert menu's settings (gpu/vk/vk_api.h MkwVkSetting's fields) */
typedef struct HosControlSetting {
    char key[32], name[48], text[64], tab[16];
    int value, lo, hi, step;
} HosControlSetting;

/* The game as the bridge sees it: control.c's own for the game, a fake one in tests/control_test.c */
typedef struct HosControlGame {
    int64_t (*frame)(void);   /* the game's presents so far */
    /* the presents replayed, the draws recorded so far, the picture's size: 0 when there is no such renderer */
    int (*frame_info)(int64_t *replayed, int64_t *draws, int *width, int *height);
    /* a PNG of the first present after `after`; done(ctx, present, ok) from any thread. 0: not taken */
    int (*shot)(const char *path, int64_t after, void (*done)(void *ctx, int64_t present, int ok), void *ctx);
    int (*setting)(int index, HosControlSetting *out);   /* 0 past the last */
    int (*setting_set)(int index, int value);
    /* guest memory, when a level is on (host/control.c reads HOS_CONTROL_MEM). region: 0 when [addr, addr + size) is not
       wholly in mapped guest memory (hos_mem_region's class otherwise). read/write copy between guest memory and a host
       buffer; write also marks the pages so the GPU mirror picks them up. Both are called on the game thread, at a
       controller read */
    int (*mem_region)(uint64_t addr, uint64_t size);
    void (*mem_read)(uint64_t addr, void *dst, uint64_t size);
    void (*mem_write)(uint64_t addr, const void *src, uint64_t size);
    void (*log)(const char *line);
    void (*quit)(void);   /* does not return */
    const char *(*renderer)(void);   /* the renderer's description, or NULL */
    const char *version;
} HosControlGame;

/* Listens on path and serves on a thread of its own. 0 when it could not (logged) */
int hos_control_serve(const char *path, const HosControlGame *game);

#ifdef __cplusplus
}
#endif
