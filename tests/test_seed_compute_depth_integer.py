"""seed_compute leaves out a variant that depth-compares an integer texture (invalid SPIR-V, which crashes RADV), game-free.

The check itself is compiled from gpu/vk/vk_shader.cpp and run against stand-in programs; the call is read from seed_compute.

    python -I -B tests/test_seed_compute_depth_integer.py [--cxx g++]
"""
import argparse
import os
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "gpu", "vk", "vk_shader.cpp")

FIXTURE = r"""
#include <cstdio>
#include <vector>
struct Descriptor { bool is_depth, is_integer; };
struct Info { std::vector<Descriptor> texture_descriptors; };
struct Stage { Info info; };
struct Program { std::vector<Stage> stages; };
%s
static int check(std::vector<Descriptor> textures, bool expected) {
    Program p;
    p.stages.push_back({});
    p.stages.push_back({Info{std::move(textures)}});
    if (compares_integer_texture(p) == expected) return 0;
    std::puts("wrong answer");
    return 1;
}
int main() {
    return check({}, false) + check({{false, false}}, false) + check({{true, false}}, false)
        + check({{false, true}}, false)                 // (a gather or a fetch of an integer texture is valid)
        + check({{false, true}, {true, true}}, true);
}
"""


def block(text, marker):
    start = text.index(marker)
    at = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[at] == "{") - (text[at] == "}")
        at += 1
    return text[start:at]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    with open(SOURCE, encoding="utf-8") as f:
        source = f.read()

    seed = block(source, "SeedVariants seed_compute(")
    translated = seed.index("translate_into(*t.p, *blank")
    returned = seed.index("return t;", translated)
    if "if (!t.p->failed && compares_integer_texture(*t.p)) t.p->failed = true;" not in seed[translated:returned]:
        raise SystemExit("seed_compute: a translated variant is not checked before it is returned")

    with tempfile.TemporaryDirectory(prefix="seed-depth-integer-") as tmp:
        unit, exe = os.path.join(tmp, "fixture.cpp"), os.path.join(tmp, "fixture")
        with open(unit, "w", encoding="utf-8") as f:
            f.write(FIXTURE % block(source, "bool compares_integer_texture("))
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", unit, "-o", exe], check=True, timeout=120)
        subprocess.run([exe], check=True, timeout=30)
    print("seed compute depth integer: PASS")


if __name__ == "__main__":
    main()
