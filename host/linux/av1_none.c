/* (hookshot) Linux: no Media Foundation (host/mf_av1.c). FFmpeg decodes AV1 itself (the bundled LGPL build's dav1d,
   which host/nn_video.c asks for by name); these are its fallback's entry points, answering "none". */
#include <stddef.h>
#include <stdint.h>

typedef void (*mf_av1_picture_fn)(void *user, const uint8_t *y, const uint8_t *uv, int w, int h, int stride, int64_t pts);
void *mf_av1_open(int w, int h) { (void)w, (void)h; return NULL; }
void mf_av1_decode(void *h, const uint8_t *data, size_t n, int64_t pts, mf_av1_picture_fn fn, void *user) {
    (void)h, (void)data, (void)n, (void)pts, (void)fn, (void)user;
}
void mf_av1_flush(void *h) { (void)h; }
void mf_av1_close(void *h) { (void)h; }
