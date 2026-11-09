"""CPU check of which bindings of read-back ranges go directly to guest memory.

python -I -B tests/test_rb_direct.py [--cxx clang++]
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    start = text.index(marker)
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cxx', default='g++')
    parser.add_argument('--source', type=Path, default=ROOT / 'gpu/vk/vk_memory.cpp')
    args = parser.parse_args()
    text = args.source.read_text(encoding='utf-8')
    bind = block(text, 'BufferRef gpu_buffer_bind(')
    direct = bind[bind.index('if (rb_direct_inside(g0, size))'):]
    direct = direct[:direct.index('return remember(')]
    assert 'if (written) wb_pages_note(g0, g0 + size, mem_rec_value);' in direct, 'a written direct binding must keep the read-back wait'
    assert 'size > kRbDirectMax' in block(text, 'void memory_rb_direct_note('), 'only small ranges go direct'
    assert 'cut_rb_direct' not in text, 'write-backs of mirror bindings must not be cut'
    assert 'rb_direct_forget_pool(old->lo, old->hi);' in block(text, 'void retire_pool(std::map<uint64_t, Pool>::iterator it) {'), 'a retired pool drops its ranges'
    fixture = r'''
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#define CHECK(ok) do { if (!(ok)) { std::fprintf(stderr, "failed line %d\n", __LINE__); std::exit(1); } } while (0)
''' + block(text, 'struct RbRange {') + r''';
RbRange rb_direct_ranges[512];
std::atomic<int> rb_direct_range_n{0};
''' + block(text, 'bool rb_direct_inside(') + block(text, 'bool rb_direct_range_add(') + block(text, 'void rb_direct_range_forget(') + r'''
int main() {
    CHECK(!rb_direct_inside(0x1000, 0x10));                                // nothing noted
    CHECK(rb_direct_range_add(0x1000, 0x3000));
    CHECK(rb_direct_inside(0x1000, 0x2000));                               // the whole range
    CHECK(rb_direct_inside(0x1800, 0x100));                                // inside it
    CHECK(!rb_direct_inside(0x800, 0x1000));                               // starts before it: stays mirrored
    CHECK(!rb_direct_inside(0x2800, 0x1000));                              // ends after it: stays mirrored
    CHECK(!rb_direct_inside(0x1000, 0));
    CHECK(rb_direct_range_add(0x1000, 0x2000) && rb_direct_range_n.load() == 1);   // already covered: no new slot
    CHECK(rb_direct_range_add(0x8000, 0x9000) && rb_direct_range_n.load() == 2);
    rb_direct_range_forget(0x8000, 0x10000);                               // only what lies in the pool goes
    CHECK(!rb_direct_inside(0x8000, 0x100) && rb_direct_inside(0x1000, 0x100));
    CHECK(rb_direct_range_add(0xA000, 0xB000) && rb_direct_range_n.load() == 2);   // the freed slot is used again
    CHECK(rb_direct_inside(0xA000, 0x100));
    for (uint64_t i = 2; i < 512; i++) CHECK(rb_direct_range_add(0x100000 + i * 0x2000, 0x100000 + i * 0x2000 + 0x1000));
    CHECK(rb_direct_range_n.load() == 512);
    CHECK(!rb_direct_range_add(0x9000000, 0x9001000));                     // full: not bound direct
    CHECK(!rb_direct_inside(0x9000000, 0x100));
    std::puts("rb_direct ok");
}
'''
    with tempfile.TemporaryDirectory() as d:
        src, exe = Path(d) / 't.cpp', Path(d) / 't'
        src.write_text(fixture, encoding='utf-8')
        subprocess.run([args.cxx, '-std=c++20', '-O1', '-o', str(exe), str(src)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    main()
