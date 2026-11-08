#include "../gpu/vk/fsr_camera.h"
#include <cassert>
#include <limits>
#include <cstdio>
using namespace mkwvk::fsr_camera;
bool close(float a, float b, float eps = 1e-4f) { return std::abs(a - b) < eps; }
int main() {
    float identity[16]{}; for (int i = 0; i < 4; ++i) identity[i * 5] = 1;
    float inv[16], product[16], dx, dy;
    assert(inverse(identity, inv));
    for (int i = 0; i < 16; ++i) assert(close(inv[i], identity[i]));
    float projection[16]{1.2f,0,0,0, 0,2.1f,0,0, 0,0,-1.0002f,-0.20002f, 0,0,-1,0};
    float near_z, far_z, fov;
    assert(perspective(projection, near_z, far_z, fov));
    assert(close(near_z, 0.1f) && close(far_z, 1000, 0.3f));
    assert(fov > 0.8f && fov < 1.0f);
    float view[16]; std::memcpy(view, identity, sizeof view); view[3] = -7; view[7] = 4; view[11] = -30;
    float vp[16]; multiply(projection, view, vp);
    assert(inverse(vp, inv)); multiply(vp, inv, product);
    for (int i = 0; i < 16; ++i) assert(close(product[i], identity[i], 1e-4f));
    // A static scene has zero motion for all jitter phases, both depth conventions and off-center pixels.
    for (unsigned phase = 1; phase <= 8; ++phase) for (bool gl_depth : {false,true}) {
        float jx = halton(phase, 2), jy = halton(phase, 3);
        for (float z : {0.0f, 0.25f, 0.999f}) {
            assert(motion(identity, 333.5f, 877.5f, z, 2048, 1152, jx, jy, gl_depth, dx, dy));
            assert(std::abs(dx) < 0.0002f && std::abs(dy) < 0.0002f);
        }
    }
    // A previous clip position shifted right/down by one pixel gives +1,+1, not inverted vectors.
    float shift[16]; std::memcpy(shift, identity, sizeof shift);
    shift[3] = 2.0f / 2048; shift[7] = -2.0f / 1152;
    assert(motion(shift, 1024.5f, 576.5f, 0.5f, 2048, 1152, 0, 0, true, dx, dy));
    assert(close(dx, 1) && close(dy, 1));
    // Camera translation: a point at z=-10 reprojects left by 12 pixels on a 200-pixel surface.
    float previous[16]; std::memcpy(view, identity, sizeof view); view[3] = -1;
    multiply(projection, view, previous); assert(inverse(projection, inv)); multiply(previous, inv, product);
    float depth = (1.0002f - 0.20002f / 10) * 0.5f + 0.5f;
    assert(motion(product, 100, 100, depth, 200, 200, 0, 0, true, dx, dy));
    assert(close(dx, -12, 0.003f) && close(dy, 0));
    float zero[16]{}; assert(!inverse(zero, inv)); assert(!perspective(identity, near_z, far_z, fov));
    // A finite matrix can have an inverse outside the representable float range.
    float overflow[16]{1e-10f,1e30f,0,0, 0,1e-10f,0,0, 0,0,1,0, 0,0,0,1};
    assert(!inverse(overflow, inv));
    // Reprojection must decline a point behind the previous camera.
    float behind[16]; std::memcpy(behind, identity, sizeof behind); behind[15] = -1;
    assert(!motion(behind, 100, 100, 0.5f, 200, 200, 0, 0, true, dx, dy));
    identity[0] = std::numeric_limits<float>::quiet_NaN(); assert(!inverse(identity, inv));
    assert(halton(1, 2) == 0 && close(halton(1, 3), -1.0f/6));
    puts("FSR camera: inverse, projection, jitter cancellation, motion direction and invalid input passed");
}
