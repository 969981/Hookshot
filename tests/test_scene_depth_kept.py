"""DLSS and FSR 3 keep the scene depth they took while no depth image went (gpu/vk/vk_texture.cpp's depth_destroy_gen),
not while no texture at all went: texture_destroy_generation also moves for every sampled texture the budget lets go and
every ASTC upload left for a later frame, and taken as "the scene depth may be gone" it handed the frame back to the game's
upscale 1214 times in 48 minutes of one Windows log. Checks that both cameras use the depth counter, that every way a depth
image goes or moves bumps it (destroy_later for a depth texture, a compaction pass), and that the two sweeps (the eviction's and the dead
targets') are the only places a Texture is freed (a camera's depth pointer is good while the counter has not moved). That destroy_later
moves it for a depth texture and not for a colour one is run in tests/test_target_image_reuse.py. No game or GPU required.

python -I -B tests/test_scene_depth_kept.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VK = ROOT / "gpu" / "vk"


def block(source, start):
    at = source.find(start)
    if at < 0:
        raise SystemExit("%s is missing" % start.rstrip(" {("))
    body = source[at:]
    return body[:body.index("\n}\n")]


def main():
    tex = (VK / "vk_texture.cpp").read_text(encoding="utf-8")
    later = block(tex, "void destroy_later(Texture& tex) {")
    if "if (tex.aspect & (VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT)) depth_destroy_gen++;" not in later:
        raise SystemExit("destroy_later: a depth texture can go without the depth counter moving (DLSS would read it)")
    compaction = tex[tex.index('full_barrier(cmd, "compaction: after the copies");'):]
    compaction = compaction[:compaction.index("\n}\n")]
    if "depth_destroy_gen++;" not in compaction:
        raise SystemExit("a compaction pass moves images without the depth counter moving")
    # (three places free a Texture: the eviction sweep, between its passes the dead targets' sweep (dead_targets.h, Longshot
    # #1255), and the ASTC images that served a new texture's blocks (astc_reuse.h, Longshot #1222); each through
    # destroy_later, which moves the depth counter for a depth image)
    if (tex.count("textures.erase(") != 3 or "destroy_later(*tex);" not in block(tex, "void texture_frame(uint64_t completed)")
            or "destroy_later(*tex);" not in block(tex, "void dead_targets_sweep(uint64_t completed)")
            or "destroy_later(*t);" not in block(tex, "void reuse_retire_frame() {")):
        raise SystemExit("a Texture is freed somewhere other than the three sweeps, or without destroy_later: check that it bumps the depth counter")
    if tex.count("depth_destroy_gen++") != 2:
        raise SystemExit("vk_texture.cpp bumps the depth counter somewhere new: say why here")
    for name in ("vk_temporal.cpp", "vk_fsr3_inputs.cpp"):
        src = (VK / name).read_text(encoding="utf-8")
        if "texture_destroy_generation()" in src:
            raise SystemExit("%s still drops its scene depth when any texture goes" % name)
        for part, why in (("k.depth_gen = texture_depth_destroy_generation()", "the camera does not note the depth counter"),
                          ("if (cam.depth_gen != texture_depth_destroy_generation()) return fail(\"the scene depth may be gone\");",
                           "a depth image gone since the camera was taken may be read"),
                          ("const uint64_t gen = texture_depth_destroy_generation();",
                           "its depth views are not let go when a depth image goes")):
            if part not in src:
                raise SystemExit("%s: %s" % (name, why))
    print("DLSS and FSR 3 keep the scene depth while no depth image went")


if __name__ == "__main__":
    main()
