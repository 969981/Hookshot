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

volatile float hos_oot_cam_near = 0.0f;
volatile float hos_oot_cam_far = 0.0f;
volatile float hos_oot_cam_tan_half_fov = 0.0f;
volatile int hos_oot_camera_valid = 0;

/* The renderer's side: the field of view in radians, or 0 when nothing has been published.
   tan_half_fov is what the game stores (1 / it becomes the projection's m11), so the angle is
   twice its arc tangent. */
float hos_oot_camera(float *near_plane, float *far_plane) {
    if (!hos_oot_camera_valid) return 0.0f;
    if (near_plane) *near_plane = hos_oot_cam_near;
    if (far_plane) *far_plane = hos_oot_cam_far;
    return hos_oot_cam_tan_half_fov;
}
