# The FSR upscaler row

Both backends now sit behind one choice, the Insert menu's **FSR upscaler** row (`upscale::Choice` in
`gpu/vk/upscale_engine.h`, resolved by `engine_for`): Auto takes FSR 4.1 when AMD's library is present and has a model for
the card, else FSR 3.1, else the game's own upscale; FSR 4.1 forced shows why when it cannot run, and falls back to FSR 3.1 if
it fails in play; FSR 3.1 forced runs the SDK passes.

FSR 3.1 runs on the frame chain FSR 4.1 already uses (`dlss_dispatch` in `vk_draw.cpp`), from the same images: colour
tonemapped and square-root encoded (LDR, in [0, 1]), depth standard (0 near, 1 far), motion toward the previous frame in
render pixels, and the jitter. The passes are compiled for those inputs rather than converted to them
(`scripts/fsr3_shaders.py`: HDR input and inverted depth off, unit exposure bound as the SDK's zero default): undoing the
game's tonemap to feed an HDR input, and writing a reversed depth, would each be a pass that loses precision for nothing.
`tests/test_fsr3_shaders.py` pins those options. Near, far and the vertical field of view are read out of the same
view-projection matrix the motion is reprojected with (`camera_from_view_projection`), so FSR's disocclusion thresholds come
from the game's camera, not a guess.
