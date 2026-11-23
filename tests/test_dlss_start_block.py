"""The start-up log's DLSS block (gpu/vk/vk_dlss.cpp dlss_status_log): its four lines (the renderer and GPU, super
resolution, frame generation, DLSS 5 neural rendering, each on or off and why), logged once from the upscalers' background
questions (vk_menu.cpp probe_upscalers_soon), which the start-up screen asks and the game's first frame asks too: a start
with no start-up screen (RX_VK_PRECOMPILE=0) logged no block at all. No game, GPU or NVIDIA SDK required.

python -I -B tests/test_dlss_start_block.py
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
    dlss = (VK / "vk_dlss.cpp").read_text(encoding="utf-8")
    log = block(dlss, "void dlss_status_log() {")
    for line in ("vk: DLSS at start: ", "vk: DLSS   super resolution (DLSS, DLAA): ", "vk: DLSS   frame generation: ",
                 "vk: DLSS   neural rendering (DLSS 5): "):
        if line not in log:
            raise SystemExit("dlss_status_log: the line %r is gone" % line)
    # (DLSS 5 off on Windows: the files a player needs, by name, readme.md's table; each reason one sentence)
    at = log.rindex("#ifdef _WIN32")   # (the DLSS 5 line's: the last in the function)
    windows = log[at:log.index("#else", at)]
    for name in ("sl.interposer.dll", "sl.common.dll", "sl.pcl.dll", "sl.dlss_nr.dll", "nvngx_dlssnr.dll", "ngx or streamline"):
        if name not in windows:
            raise SystemExit("dlss_status_log: DLSS 5's off line on Windows does not name %s" % name)
    if "auto sentence = " not in log or "s.back() == '.'" not in log:
        raise SystemExit("dlss_status_log: a reason's own full stop gets another after it")
    menu = (VK / "vk_menu.cpp").read_text(encoding="utf-8")
    probe = block(menu, "static void probe_upscalers_soon() {")
    if "std::call_once(once," not in probe or probe.index("dlss_probe();") > probe.index("dlss_status_log();"):
        raise SystemExit("probe_upscalers_soon: the block is not logged once, after NGX was asked")
    if "probe_upscalers_soon();" not in block(menu, "void startup_screen(const StartupView* view) {"):
        raise SystemExit("the start-up screen no longer asks the upscalers in the background")
    if "if (dev.device) probe_upscalers_soon();" not in block(menu, "void menu_tick() {"):
        raise SystemExit("menu_tick: a start with no start-up screen logs no DLSS block")
    print("the DLSS block is logged once at every start, with or without the start-up screen")


if __name__ == "__main__":
    main()
