"""nvnCommandBufferBindProgram(cmd, program, stages) binds the stages it names and only those; a null program unbinds them.

gpu/render/rx_nvn.h's nvn_bind_stages is cut out of the source unmodified and run against a stand-in for the GPU memory
the stages' control sections are read from (the stage at +0x56):

  per stage   a vertex program bound for 0x1, a fragment program for 0x2, no geometry (NULL, 0x4), as Kirby Air Riders
              does before each draw: vertex and fragment are bound, under a serial of their own (the replays' caches
              go by serial), the same one each time the same pair is bound; the null bind changes nothing.
  whole       a program bound for every graphics stage (0x1f) is that program as it is (its own serial), whatever was
              bound; compute alone (0x20) as well: as before. So are 0xdf and 0xff (task and mesh, 0x40 and 0x80, as
              well), as Mario Kart World binds, over any program; and 0x3 over a vertex and fragment program when no
              other stage is bound.
  again       a bind that keeps another program's stage (0x3 over a vertex, geometry and fragment program) gives the
              same program and serial each time it is made, and another one for another pair.
  part        a vertex and fragment program bound for 0x1 only binds its vertex stage.
  pipelines   a vertex and fragment program bound for 0x3 (Mario Kart World does) over a mesh program (fragment, task,
              mesh) is that program as it is, its own serial: the mesh stages go, as no pipeline has both; and a mesh
              program bound for its task and mesh stages over a vertex one drops the vertex stage, keeps fragment.
  unbind      a vertex, geometry and fragment program, the geometry stage unbound: vertex and fragment stay, in order;
              both unbound: no stage is left.
  replays     both replays (gpu/vk/vk_replay.cpp, gpu/render/rx_nvn.cpp) take each bind through it.

Before, both took each bind for the whole program and a null one for none: every draw after Kirby's null bind, or with
only its fragment stage, was left out ("a draw without a program"), and Kirby drew nothing but black.

python -I -B tests/test_nvn_bind_stages.py [--cxx g++]
"""
import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ["--cxx", "{cxx}"]

HARNESS = r'''
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); std::exit(1); } } while (0)
@PROG@
@HELPER@
static uint16_t stage_at[16];   // (control section i at 0x1000 * (i + 1): its stage)
static bool read(uint64_t at, void* to, uint64_t n) {
    const uint64_t i = at / 0x1000 - 1;
    if (at % 0x1000 != 0x56 || n != 2 || i >= 16) return false;
    std::memcpy(to, &stage_at[i], 2);
    return true;
}
static int next_control = 1;
static nvn_prog program(uint64_t serial, int n, const int* stages) {
    nvn_prog p{};
    p.magic = 0x474f5250, p.nstage = (uint32_t)n, p.serial = serial;
    for (int i = 0; i < n; i++, next_control++) {
        p.sd[i].data = 0x100000 * next_control;
        p.sd[i].control = 0x1000 * next_control;
        stage_at[next_control - 1] = (uint16_t)stages[i];
    }
    return p;
}
static bool same(const nvn_prog& a, const nvn_prog& b) { return std::memcmp(&a, &b, sizeof a) == 0; }
int main() {
    const int v[] = {0}, f[] = {1}, vf[] = {0, 1}, vgf[] = {0, 2, 1}, cs[] = {5}, ftm[] = {1, 6, 7};
    const nvn_prog A = program(1, 1, v), B = program(2, 1, f), C = program(3, 2, vf), G = program(4, 3, vgf), D = program(5, 1, cs),
                   M = program(6, 3, ftm);
    {   // per stage
        nvn_prog cur{};
        uint64_t serials[8]{};
        bool has = false;
        has = nvn_bind_stages(cur, has, &A, 0x1, serials, read);
        CHECK(has && same(cur, A));
        has = nvn_bind_stages(cur, has, &B, 0x2, serials, read);
        CHECK(has && cur.nstage == 2 && cur.sd[0].data == A.sd[0].data && cur.sd[1].data == B.sd[0].data && cur.serial >> 63);
        const nvn_prog pair = cur;
        has = nvn_bind_stages(cur, has, nullptr, 0x4, serials, read);
        CHECK(has && same(cur, pair));
        has = nvn_bind_stages(cur, has, &A, 0x1, serials, read);   // (the next draw's binds: the same pair, the same program)
        has = nvn_bind_stages(cur, has, &B, 0x2, serials, read);
        has = nvn_bind_stages(cur, has, nullptr, 0x4, serials, read);
        CHECK(has && same(cur, pair));
        static const uint32_t whole[] = {0xdf, 0xff};   // (whole, as MKW binds)
        for (const uint32_t mask : whole) {
            has = nvn_bind_stages(cur, has, &B, 0x2, serials, read);
            has = nvn_bind_stages(cur, has, &C, mask, serials, read);
            CHECK(has && same(cur, C));
        }
        has = nvn_bind_stages(cur, has, &C, 0x3, serials, read);
        CHECK(has && same(cur, C));
        // whole
        has = nvn_bind_stages(cur, has, &C, 0x1f, serials, read);
        CHECK(has && same(cur, C));
        has = nvn_bind_stages(cur, has, &D, 0x20, serials, read);
        CHECK(has && same(cur, D));
        // part
        has = nvn_bind_stages(cur, has, &C, 0x1, serials, read);
        CHECK(has && cur.nstage == 1 && cur.sd[0].data == C.sd[0].data && cur.serial >> 63);
    }
    {   // pipelines
        nvn_prog cur{};
        uint64_t serials[8]{};
        bool has = nvn_bind_stages(cur, false, &M, 0xff, serials, read);
        CHECK(has && same(cur, M));
        has = nvn_bind_stages(cur, has, &C, 0x3, serials, read);
        CHECK(has && same(cur, C));
        has = nvn_bind_stages(cur, has, &M, 0xc0, serials, read);
        CHECK(has && cur.nstage == 3 && cur.sd[0].data == C.sd[1].data && cur.sd[1].data == M.sd[1].data
              && cur.sd[2].data == M.sd[2].data && cur.serial >> 63);
    }
    {   // again
        nvn_prog cur{}, first{};
        uint64_t serials[8]{};
        bool has = false;
        for (int round = 0; round < 3; round++) {
            has = nvn_bind_stages(cur, has, &G, 0x1f, serials, read);
            has = nvn_bind_stages(cur, has, &C, 0x3, serials, read);
            CHECK(has && cur.nstage == 3 && cur.sd[0].data == C.sd[0].data && cur.sd[1].data == C.sd[1].data
                  && cur.sd[2].data == G.sd[1].data && cur.serial >> 63);
            if (!round) first = cur;
            CHECK(same(cur, first));
        }
        has = nvn_bind_stages(cur, has, &G, 0x1f, serials, read);
        has = nvn_bind_stages(cur, has, &A, 0x3, serials, read);
        CHECK(has && cur.serial >> 63 && cur.serial != first.serial);
    }
    {   // unbind
        nvn_prog cur = G;
        uint64_t serials[8] = {4, 4, 4, 4, 4, 4, 4, 4};
        bool has = nvn_bind_stages(cur, true, nullptr, 0x4, serials, read);
        CHECK(has && cur.nstage == 2 && cur.sd[0].data == G.sd[0].data && cur.sd[1].data == G.sd[2].data && cur.sd[2].data == 0);
        has = nvn_bind_stages(cur, has, nullptr, 0x3, serials, read);
        CHECK(!has);
    }
    std::printf("PASS: BindProgram binds and unbinds only the stages it names\n");
    return 0;
}
'''


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + what)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--cxx", default=os.environ.get("CXX", "g++"))
    a = p.parse_args()
    h = (ROOT / "gpu" / "render" / "rx_nvn.h").read_text(encoding="utf-8")
    prog = "\n".join(re.search(r"^typedef struct \{ uint64_t data, control; \} nvn_stage_data;$", h, re.M).group(0).splitlines()
                     + [re.search(r"^typedef struct \{ uint32_t magic, nstage; nvn_stage_data sd\[6\]; uint64_t serial; \} nvn_prog;$",
                                  h, re.M).group(0)])
    helper = block(h, "template <class Read> inline uint32_t nvn_stage_type(") + "\n" + \
             block(h, "template <class Read>\ninline bool nvn_bind_stages(")
    for rel in ("gpu/vk/vk_replay.cpp", "gpu/render/rx_nvn.cpp"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        case = src[src.index("case NOP_BIND_PROGRAM:"):src.index("case NOP_BIND_STATE:")]
        check("nvn_bind_stages(" in case and "*b.prog" not in case, rel + ": a BindProgram does not go through nvn_bind_stages")
    with tempfile.TemporaryDirectory(prefix="bind-stages-") as tmp:
        src, exe = Path(tmp, "t.cpp"), Path(tmp, "t")
        src.write_text(HARNESS.replace("@PROG@", prog).replace("@HELPER@", helper), encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++17", "-Wall", "-Werror", "-o", str(exe), str(src)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
