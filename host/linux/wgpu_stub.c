/* Linux Vulkan-only builds do not link gpu/render.  rx_backend.c keeps the
   WebGPU API as a fallback, so provide a harmless fallback when wgpu-native
   is not installed.  These functions are not reached when Vulkan starts. */
#include "hos.h"
#include "../../gpu/render/rx_nvn.h"

int wgr_start(void *hwnd) { (void)hwnd; return 0; }
void wgr_resize(int width, int height) { (void)width; (void)height; }
const char *wgr_describe(void) { return "no WebGPU renderer"; }
void wgr_stats(int64_t *draws, int64_t *skipped, int64_t *pipelines) {
    if (draws) *draws = 0;
    if (skipped) *skipped = 0;
    if (pipelines) *pipelines = 0;
}
void wgr_nvn_submit(const uint64_t *handles, int n) { (void)handles; (void)n; }
uint64_t wgr_nvn_fence(void) { return 0; }
uint64_t wgr_nvn_pushed(void) { return 0; }
uint64_t wgr_nvn_replayed(void) { return 0; }
int wgr_nvn_wait(uint64_t fence, uint64_t timeout_ns) { (void)fence; (void)timeout_ns; return 1; }
void wgr_wait_writebacks_reading(const void *p, uint64_t n) { (void)p; (void)n; }
void wgr_nvn_finish(void) {}
void wgr_nvn_present(const nvn_tex *t, const int crop[4]) { (void)t; (void)crop; }
void wgr_nvn_register_texture(int id, const nvn_tex *t, const nvn_view *v) { (void)id; (void)t; (void)v; }
void wgr_nvn_register_sampler(int id, const nvn_samp_par *p) { (void)id; (void)p; }
void wgr_nvn_set_texture_pool_size(int n) { (void)n; }
void wgr_nvn_texture_released(const nvn_tex *t) { (void)t; }
void wgr_nvn_program_released(const nvn_prog *p) { (void)p; }
void wgr_nvn_read_texels(const nvn_tex *t, unsigned level, unsigned layer0, const NVNcopyRegion *r,
                         uint8_t *dst, uint64_t row, uint64_t img) {
    (void)t; (void)level; (void)layer0; (void)r; (void)dst; (void)row; (void)img;
}
void wgr_nvn_write_texels(const nvn_tex *t, unsigned level, unsigned layer0, const NVNcopyRegion *r,
                          const uint8_t *src, uint64_t row, uint64_t img) {
    (void)t; (void)level; (void)layer0; (void)r; (void)src; (void)row; (void)img;
}
void wgr_nvn_invalidate(uint64_t gpu, uint64_t size) { (void)gpu; (void)size; }
