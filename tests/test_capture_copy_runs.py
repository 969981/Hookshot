"""Captured mirror regions preserve bytes when adjacent runs are combined.

python -I -B tests/test_capture_copy_runs.py --cxx <compiler>
The fixture is retained in the ignored work directory.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, marker):
    start = source.index(marker)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    source = (ROOT / "gpu/vk/vk_memory.cpp").read_text(encoding="utf-8")
    helper = function(source, "void append_capture_copy(")
    sync = function(source, "void mirror_sync(Pool& p)")
    call = sync.index("append_capture_copy(cap_runs[")
    start = sync.rindex("const uint64_t ga =", 0, call)
    captured_copy = sync[start:sync.index(";", call) + 1]
    adapter = r'''
// Execute mirror_sync's real clipping and append expression with controlled capture inputs.
static void capture_page(std::map<uint64_t, std::vector<VkBufferCopy>>& cap_runs,
                         uint64_t buffer, uint64_t slot, uint64_t page) {
    constexpr unsigned kPageShift = 12;
    const struct { uint64_t lo, hi; } p{0x1004, 0x400a};
    const struct { uint64_t slot; } e{slot};
    struct Buffer { uint64_t buffer; };
    const std::map<uint64_t, Buffer> buffers{{0, {buffer}}};
    auto b = buffers.begin();
''' + captured_copy + r'''
}
'''
    fixture = r'''
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <vector>
struct VkBufferCopy { uint64_t srcOffset, dstOffset, size; };
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"FAIL:%d %s\n",__LINE__,#x); std::exit(1); } } while (0)
''' + helper + adapter + r'''
static void expect_separate(VkBufferCopy a, VkBufferCopy b) {
    std::vector<VkBufferCopy> v; append_capture_copy(v,a); append_capture_copy(v,b); CHECK(v.size()==2);
}
int main() {
    std::vector<VkBufferCopy> v;
    append_capture_copy(v,{16,32,4}); append_capture_copy(v,{20,36,8});
    CHECK(v.size()==1 && v[0].srcOffset==16 && v[0].dstOffset==32 && v[0].size==12);
    expect_separate({0,0,4},{5,4,4}); expect_separate({0,0,4},{4,5,4});
    expect_separate({0,0,4},{2,2,4}); expect_separate({4,4,4},{0,0,4});
    expect_separate({0,0,0},{0,0,4}); expect_separate({0,0,4},{4,4,0});
    expect_separate({UINT64_MAX-1,0,2},{0,2,4});
    expect_separate({0,UINT64_MAX-1,2},{2,0,4});
    expect_separate({0,0,8},{8,8,UINT64_MAX-7});
    expect_separate({UINT64_MAX-10,0,5},{UINT64_MAX-5,5,10});
    expect_separate({0,UINT64_MAX-10,5},{5,UINT64_MAX-5,10});

    // Two independent capture buffers with partial edges and gaps. Interleave the
    // input and include real adjacent runs, with disjoint destination ranges.
    std::vector<uint8_t> src[2]={std::vector<uint8_t>(65536),std::vector<uint8_t>(65536)};
    for (unsigned b=0;b<2;b++) for (unsigned i=0;i<src[b].size();i++) src[b][i]=(uint8_t)(i*31+b*53);
    std::vector<uint8_t> original(65536,0x7d), combined=original;
    std::map<int,std::vector<VkBufferCopy>> baseline, optimized;
    const std::vector<std::pair<int,VkBufferCopy>> input={
        {0,{3,101,7}}, {1,{100,1000,5}}, {0,{10,108,9}},
        {1,{105,1005,11}}, {0,{30,150,4}}, {1,{200,1100,3}},
        {0,{34,154,2}}, {1,{203,1103,1}}, {0,{60,180,5}}
    };
    for (const auto& [buffer,r]:input) {
        baseline[buffer].push_back(r); append_capture_copy(optimized[buffer],r);
    }
    CHECK(baseline[0].size()==5 && optimized[0].size()==3);
    CHECK(baseline[1].size()==4 && optimized[1].size()==2);
    for (const auto& [b,rs]:baseline) for(const auto& r:rs)
        std::copy_n(src[b].begin()+r.srcOffset,(size_t)r.size,original.begin()+r.dstOffset);
    for (const auto& [b,rs]:optimized) for(const auto& r:rs)
        std::copy_n(src[b].begin()+r.srcOffset,(size_t)r.size,combined.begin()+r.dstOffset);
    CHECK(original==combined);

    // A long contiguous sequence and its fragmented counterpart.
    std::vector<VkBufferCopy> pages;
    for(uint64_t i=0;i<1024;i++) pages.push_back({(i+i/256)*4096,(i+i/256)*4096,4096});
    std::vector<VkBufferCopy> compressed;
    for(const auto& r:pages) append_capture_copy(compressed,r);
    CHECK(compressed.size()==4);
    compressed.clear();
    for(uint64_t i=0;i<pages.size();i++) append_capture_copy(compressed,{(i*2)*4096,(i*2)*4096,4096});
    CHECK(compressed.size()==pages.size());

    // Pool [0x1004, 0x400a) clips both edge pages. Interleaved buffers keep separate lists.
    std::map<uint64_t, std::vector<VkBufferCopy>> captures;
    capture_page(captures,17,7,1); capture_page(captures,29,3,2);
    capture_page(captures,17,9,3); capture_page(captures,17,10,4);
    CHECK(captures.size()==2 && captures.count(17)==1 && captures.count(29)==1);
    const auto& a=captures.at(17); const auto& b=captures.at(29);
    CHECK(a.size()==2 && b.size()==1);
    CHECK(a[0].srcOffset==0x7004 && a[0].dstOffset==0 && a[0].size==0xffc);
    CHECK(a[1].srcOffset==0x9000 && a[1].dstOffset==0x1ffc && a[1].size==0x100a);
    CHECK(b[0].srcOffset==0x3000 && b[0].dstOffset==0xffc && b[0].size==0x1000);
    captures.clear();
    for(uint64_t page=1;page<=4;page++) capture_page(captures,17,page+6,page);
    CHECK(captures.size()==1 && captures.count(17)==1 && captures.at(17).size()==1);
    const auto& joined=captures.at(17)[0];
    CHECK(joined.srcOffset==0x7004 && joined.dstOffset==0 && joined.size==0x3006);
    std::printf("PASS merged exact bytes, gaps, defensive overlaps, overflow and buffer grouping\n");
}
'''
    work = ROOT / "work"
    work.mkdir(exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="capture-copy-runs-", dir=work))
    cpp, exe = out / "fixture.cpp", out / "fixture.exe"
    cpp.write_text(fixture, encoding="utf-8")
    subprocess.run([args.cxx, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror", str(cpp), "-o", str(exe)], check=True)
    env = dict(os.environ)
    compiler = Path(args.cxx)
    if compiler.is_file():
        env["PATH"] = str(compiler.resolve().parent) + os.pathsep + env.get("PATH", "")
    subprocess.run([str(exe)], check=True, env=env)
    print("Retained:", out)


if __name__ == "__main__":
    main()
