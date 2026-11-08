// Game-independent row-major camera math. No guest data or Vulkan dependencies.
#pragma once
#include <algorithm>
#include <cmath>
#include <cstring>

namespace mkwvk::fsr_camera {
inline bool inverse(const float* a, float* out) {
    double m[4][8]{};
    for (int r = 0; r < 4; ++r) for (int c = 0; c < 4; ++c) {
        if (!std::isfinite(a[r * 4 + c])) return false;
        m[r][c] = a[r * 4 + c]; m[r][c + 4] = r == c;
    }
    for (int c = 0; c < 4; ++c) {
        int pivot = c;
        for (int r = c + 1; r < 4; ++r) if (std::abs(m[r][c]) > std::abs(m[pivot][c])) pivot = r;
        if (std::abs(m[pivot][c]) < 1e-12) return false;
        for (int k = 0; k < 8; ++k) std::swap(m[c][k], m[pivot][k]);
        double scale = m[c][c];
        for (double& v : m[c]) v /= scale;
        for (int r = 0; r < 4; ++r) if (r != c) {
            double factor = m[r][c];
            for (int k = 0; k < 8; ++k) m[r][k] -= factor * m[c][k];
        }
    }
    for (int r = 0; r < 4; ++r) for (int c = 0; c < 4; ++c) {
        const float value = float(m[r][c + 4]);
        if (!std::isfinite(value)) return false;
        out[r * 4 + c] = value;
    }
    return true;
}
inline void multiply(const float* a, const float* b, float* out) {
    float result[16]{};
    for (int r = 0; r < 4; ++r) for (int c = 0; c < 4; ++c)
        for (int k = 0; k < 4; ++k) result[r * 4 + c] += a[r * 4 + k] * b[k * 4 + c];
    std::memcpy(out, result, sizeof result);
}
inline bool perspective(const float* p, float& near_z, float& far_z, float& fov) {
    for (int i = 0; i < 16; ++i) if (!std::isfinite(p[i])) return false;
    if (p[0] <= 0 || p[5] <= 0 || std::abs(p[14] + 1) > 1e-5 || std::abs(p[15]) > 1e-5) return false;
    near_z = p[11] / (p[10] - 1);
    far_z = p[11] / (p[10] + 1);
    fov = 2 * std::atan(1 / p[5]);
    return std::isfinite(far_z) && near_z > 0 && far_z > near_z;
}
inline float halton(unsigned index, unsigned base) {
    float weight = 1, result = 0;
    while (index) { weight /= float(base); result += weight * (index % base); index /= base; }
    return result - 0.5f;
}
// Current unjittered clip -> previous unjittered clip. Pixel motion points current to previous.
inline bool motion(const float* reprojection, float x, float y, float depth, float w, float h,
                   float jitter_x, float jitter_y, bool minus_w, float& dx, float& dy) {
    float uvx = (x - jitter_x) / w, uvy = (y - jitter_y) / h;
    float v[4]{uvx * 2 - 1, 1 - uvy * 2, minus_w ? depth * 2 - 1 : depth, 1}, p[4]{};
    for (int r = 0; r < 4; ++r) for (int c = 0; c < 4; ++c) p[r] += reprojection[r * 4 + c] * v[c];
    if (!std::isfinite(p[3]) || p[3] <= 1e-6f) return false;
    dx = ((p[0] / p[3] * 0.5f + 0.5f) - uvx) * w;
    dy = ((0.5f - p[1] / p[3] * 0.5f) - uvy) * h;
    return std::isfinite(dx) && std::isfinite(dy);
}
}
