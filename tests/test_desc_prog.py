"""A program's descriptors are pushed again when that program returns.

Binding another program used to drop the set, so the next draw of the first one built it
again. python -I -B tests/test_desc_prog.py [--cxx g++]
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def ordered(text, early, late, what):
    if text.index(early) > text.index(late):
        raise SystemExit(what)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="g++")
    a = p.parse_args()
    replay = (ROOT / "gpu" / "vk" / "vk_replay.cpp").read_text(encoding="utf-8")
    draw = (ROOT / "gpu" / "vk" / "vk_draw.cpp").read_text(encoding="utf-8")
    state = (ROOT / "gpu" / "vk" / "vk_state.h").read_text(encoding="utf-8")
    if "uint64_t desc_gen" not in state:
        raise SystemExit("descriptors have no generation of their own")
    program = block(replay, "case NOP_BIND_PROGRAM:")
    if "s.bind_gen++" not in program or "s.desc_gen++" in program:
        raise SystemExit("a program change must leave that program's descriptors usable")
    bumps = [ln for ln in replay.splitlines() if "s.bind_gen++" in ln]
    if len(bumps) != 6 or sum("s.desc_gen++" not in ln for ln in bumps) != 1:
        raise SystemExit("every binding except the program must drop the kept descriptors")
    if "s->desc_gen = gen_base" not in block(replay, "void use_queue_state("):
        raise SystemExit("a queue switch keeps another queue's descriptors")
    if replay.count("desc_progs.clear(") + draw.count("desc_progs.clear(") != 1:
        raise SystemExit("the kept descriptors must be dropped exactly when a recording starts")
    if "desc_progs.clear()" not in block(draw, "void draw_begin_recording("):
        raise SystemExit("a new recording keeps descriptors whose stream was reset")
    if "desc_progs.clear()" in block(draw, "void clear_color_target("):
        raise SystemExit("a masked clear drops descriptors that only need pushing again")
    watch = draw.index("thread_local bool desc_watch_on")
    anon = draw.index("\nnamespace {\n", draw.index("namespace mkwvk"))
    if watch > anon or draw.count("thread_local bool desc_watch_on") != 1:
        raise SystemExit("the uniform watch is hidden from the replay")
    for name in ("bool ub_read(", "uint64_t ub_overlay("):
        fn = block(replay, name)
        ordered(fn, "desc_note_range(", "const size_t lim", f"{name} notes its range too late")
    body = block(draw, "void draw(")
    ordered(body, "desc_prog_use(", "desc_prog_build(", "a draw builds descriptors before looking for the program's set")
    disp = block(draw, "void dispatch(")
    ordered(disp, "desc_prog_use(", "desc_prog_build(", "a dispatch builds descriptors before looking for the program's set")
    built = block(draw, "void desc_prog_build(")
    if "build_descriptors(" not in built:
        raise SystemExit("a miss does not build the set")
    used = block(draw, "bool desc_prog_use(")
    if "pointer_table(" not in used or "1ull << 39" not in used:
        raise SystemExit("a kept set ignores a pointer table or a write past the page table")
    if "memory_stamp_gpu(" not in used or "texture_written(" not in used:
        raise SystemExit("reusing a set skips a storage write the build would have recorded")
    fn = block(replay, "bool range_lists_overlap(")
    fixture = f'''#include <cstdint>
#include <cstdio>
#include <cstdlib>
#define CHECK(x) do {{ if (!(x)) {{ std::fprintf(stderr, "FAIL:%d %s\\n", __LINE__, #x); std::exit(1); }} }} while (0)
{fn}
int main() {{
    const uint64_t a[] = {{0, 100}};
    const uint64_t an[] = {{10, 5}};
    const uint64_t b[] = {{20}};
    const uint64_t bn[] = {{5}};
    CHECK(!range_lists_overlap(a, an, 1, b, bn, 1));
    const uint64_t over[] = {{8}};
    CHECK(range_lists_overlap(a, an, 1, over, bn, 1));
    const uint64_t abut[] = {{10}};
    CHECK(!range_lists_overlap(a, an, 1, abut, bn, 1));
    CHECK(!range_lists_overlap(a, an, 0, b, bn, 1));
    CHECK(!range_lists_overlap(a, an, 1, b, bn, 0));
    const uint64_t zero[] = {{0}};
    CHECK(range_lists_overlap(a, zero, 1, b, bn, 1));
    const uint64_t wrap[] = {{~uint64_t{{0}} - 2}};
    const uint64_t wn[] = {{8}};
    CHECK(range_lists_overlap(wrap, wn, 1, b, bn, 1));
    const uint64_t inner[] = {{102}};
    const uint64_t inn[] = {{2}};
    CHECK(range_lists_overlap(a, an, 2, inner, inn, 1));
    const uint64_t beside[] = {{106}};
    CHECK(!range_lists_overlap(a, an, 2, beside, bn, 1));
    std::printf("PASS: a uniform update drops a kept set only when it overlaps\\n");
}}
'''
    with tempfile.TemporaryDirectory(prefix="desc-prog-") as tmp:
        cpp = Path(tmp) / "fixture.cpp"
        exe = Path(tmp) / "fixture"
        cpp.write_text(fixture, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
