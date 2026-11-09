/* (hookshot) Ocarina's camera, published by the lifted code so the renderer can read it.
 *
 * sead's perspective projection carries the near and far planes and the field of view, and
 * nx2/oot_camera.py injects a block into its matrix update (main+0x3485cc0, f_834a5cc0) that writes
 * them here. The renderer wants them for a temporal upscaler: FSR builds device_to_view_depth and
 * tan_half_fov from the near plane and the field of view, and camera-derived motion needs the same
 * projection. docs/upscaling.md has the field layout and how each offset was identified.
 *
 * Everything stays 0 until the game updates a projection, and hos_oot_camera_valid stays 0 with it,
 * so a reader that checks it gets the renderer's own defaults on another game version where the
 * patch site is left out. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "hos.h"

volatile float hos_oot_cam_near = 0.0f;
volatile float hos_oot_cam_far = 0.0f;
volatile float hos_oot_cam_tan_half_fov = 0.0f;
volatile int hos_oot_camera_valid = 0;
/* The guest addresses of sead's projection object and of the 4x4 it writes. Published so the view
   projection can be identified from real data: this matrix is the projection alone, and a temporal
   upscaler reprojects with view * projection. */
volatile uint64_t hos_oot_proj_object = 0;
volatile uint64_t hos_oot_proj_matrix = 0;
/* The first 48 floats of the projection object, copied at publish time. The inlined copies build it
   on the stack, so a pointer kept for later reads as zeros; a snapshot is the only honest way to
   see what is in it. */
volatile float hos_oot_cam_window[48];
volatile int hos_oot_cam_window_have = 0;

/* HOS_CAMERA_DUMP=1: once, a window of floats around each published object, so the view matrix can
   be found by reading what is there rather than by guessing an offset. Guest addresses only; no
   game data leaves the machine. */
void hos_oot_camera_dump(void) {
    static int done;
    const char *on = getenv("HOS_CAMERA_DUMP");
    if (done || !on || !*on || *on == '0' || !hos_oot_camera_valid) return;
    done = 1;
    for (int row = 0; hos_oot_cam_window_have && row < 6; row++) {
        char line[220];
        int n = snprintf(line, sizeof line, "camera: window +%#03x:", row * 32);
        for (int i = 0; i < 8; i++)
            n += snprintf(line + n, sizeof line - n, " %.5g", (double)hos_oot_cam_window[row * 8 + i]);
        hos_log("%s", line);
    }
    for (int which = 0; which < 2; which++) {
        const uint64_t base = which ? hos_oot_proj_matrix : hos_oot_proj_object;
        if (!base) continue;
        for (int row = 0; row < 8; row++) {
            char line[200];
            int n = snprintf(line, sizeof line, "camera: %s +%#03x:", which ? "matrix" : "object", row * 32);
            for (int i = 0; i < 8; i++) {
                float v;
                memcpy(&v, (const char *)GP(base + (uint64_t)row * 32 + (uint64_t)i * 4), 4);
                n += snprintf(line + n, sizeof line - n, " %.4g", (double)v);
            }
            hos_log("%s", line);
        }
    }
}

/* The view projection the scene's depth was rendered with, row major, or 0 when nothing has
   published one. sead's projection update gives the projection alone; the view is not identified
   yet (HOS_CAMERA_DUMP=1 is for finding it), so this answers 0 and camera motion stays off rather
   than reprojecting through a matrix that is not the one the depth came from. */
volatile int hos_oot_view_projection_valid = 0;
volatile float hos_oot_view_projection_rows[16];

int hos_oot_view_projection(float *row_major_4x4) {
    if (!hos_oot_view_projection_valid || !row_major_4x4) return 0;
    for (int i = 0; i < 16; i++) row_major_4x4[i] = hos_oot_view_projection_rows[i];
    return 1;
}

/* The renderer's side: the field of view in radians, or 0 when nothing has been published.
   tan_half_fov is what the game stores (1 / it becomes the projection's m11), so the angle is
   twice its arc tangent. */
float hos_oot_camera(float *near_plane, float *far_plane) {
    if (!hos_oot_camera_valid) return 0.0f;
    if (near_plane) *near_plane = hos_oot_cam_near;
    if (far_plane) *far_plane = hos_oot_cam_far;
    return hos_oot_cam_tan_half_fov;
}
