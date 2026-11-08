"""Ocarina's camera, published from sead's perspective projection so the renderer can read it.

The projection's matrix update (main+0x3485cc0, the whole function f_834a5cc0) has the object in x0
and has just loaded tan(fovy/2) and the aspect ratio from it. This appends a block there that also
reads the near and far planes and writes all three to host globals (host/hos_oot_camera.c), which a
temporal upscaler needs: FSR builds device_to_view_depth and tan_half_fov from the near plane and
the field of view, and camera-derived motion needs the same projection.

The field layout, and how each offset was identified from what the function does with it, is in
docs/upscaling.md. The site is shared with nx2/oot_wide.py's camera-ratio patch, so this appends to
whatever is already there rather than replacing it; lift.py installs the ratio first.

Which of +0x98 and +0x9C is the near plane depends on the byte at +0x0A, which the function uses to
swap them before taking their difference. Rather than decide what that flag means, this takes the
smaller of the two as the near plane and the larger as the far, and publishes nothing unless both
are positive and ordered. A projection with a near plane behind the eye is not one to hand an
upscaler.

It also publishes the guest addresses of the projection object and of the 4x4 it writes. The view
projection a temporal upscaler reprojects with is not this matrix -- this is the projection alone --
and rather than guess which offset of which object holds the view, HOS_CAMERA_DUMP=1 logs a window
of floats around both so the matrix can be identified from what it contains.
"""

MAIN = 0x80020000
SITE = 0x3485cc0
WORD = 0x2d558400   # (ldp s0, s1, [x0, #0xac]: the same word nx2/oot_wide.py checks)

PUBLISH = (
    "{ extern volatile float hos_oot_cam_near, hos_oot_cam_far, hos_oot_cam_tan_half_fov; "
    "extern volatile int hos_oot_camera_valid; const uint64_t _cp = c->x[0]; "
    "uint32_t _c0 = (uint32_t)recomp_load32(c, _cp + 0x98), _c1 = (uint32_t)recomp_load32(c, _cp + 0x9c), "
    "_ct = (uint32_t)recomp_load32(c, _cp + 0xac); float _f0, _f1, _ft; "
    "memcpy(&_f0, &_c0, 4); memcpy(&_f1, &_c1, 4); memcpy(&_ft, &_ct, 4); "
    "{ const float _nr = _f0 < _f1 ? _f0 : _f1, _fr = _f0 < _f1 ? _f1 : _f0; "
    "if (_nr > 0.0f && _fr > _nr && _ft > 0.0f) { hos_oot_cam_near = _nr; hos_oot_cam_far = _fr; "
    "hos_oot_cam_tan_half_fov = _ft; hos_oot_camera_valid = 1; } } "
    "{ extern volatile uint64_t hos_oot_proj_object, hos_oot_proj_matrix; "
    "hos_oot_proj_object = _cp; hos_oot_proj_matrix = c->x[1]; } }"
    "   /* (nx2/oot_camera.py: the camera a temporal upscaler needs) */"
)


def install(lifter, img):
    """Appends the publish to the projection update. 0 when the site's word differs (another game
    version), which leaves the renderer on its own defaults."""
    pc = MAIN + SITE
    try:
        have = img.word(pc)
    except Exception:
        have = None
    if have != WORD:
        print("camera: main+%#x holds %s, not %08x: left out"
              % (SITE, "?" if have is None else "%08x" % have, WORD))
        return 0
    lifter.after[pc] = lifter.after.get(pc, "") + "\n" + PUBLISH
    print("camera: the projection update publishes the near plane, far plane and field of view")
    return 1
