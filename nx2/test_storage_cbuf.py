"""Check actual storage descriptor reads against the stage constant-bank reader.

python nx2/test_storage_cbuf.py [--cxx clang++]
No GPU or game files needed. Checks descriptor decoding, not Vulkan buffer contents.
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
    parser.add_argument('--cxx', default='clang++')
    args = parser.parse_args()
    draw = (ROOT / 'gpu/vk/vk_draw.cpp').read_text(encoding='utf-8')
    start = draw.index('const uint32_t off = d.cbuf_offset + k * 16;')
    reads = draw[start:draw.index('const uint64_t base = addr', start)]
    shader = (ROOT / 'gpu/vk/vk_shader.cpp').read_text(encoding='utf-8')
    reader = block(shader, 'uint32_t stage_cbuf_word(')
    fixture = r'''
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
constexpr int kNvnStages=8;
struct nvn_p_bind_buf { uint64_t addr=0,size=0; };
struct NvnState { nvn_p_bind_buf ubo[8][16]; };
struct Stage { int nvn_stage; std::vector<uint8_t> c1; };
uint32_t words[16]{};
constexpr uint64_t guest=0x1000120000ull;
bool ub_read(uint64_t addr,void* out,uint64_t n) {
    if(addr<guest || addr-guest>sizeof words || n>sizeof words-(addr-guest))return false;
    std::memcpy(out,(uint8_t*)words+(addr-guest),n);return true;
}
struct Host { bool gpu_read(uint64_t a,void* p,uint64_t n){return ub_read(a,p,n);} } instance;
Host* host=&instance;
uint32_t driver_bank_word(const NvnState&,int bank,int stage,uint32_t off) {
    return (bank==0 || bank==2) && stage>=0 && stage<8 && off<sizeof words && !(off&3) ? words[off/4] : 0;
}
''' + reader + r'''
struct Descriptor { uint32_t cbuf_index,cbuf_offset; };
std::array<uint64_t,2> decode(const NvnState& s,const Stage& st,const Descriptor& d,uint32_t k) {
    const int ns=st.nvn_stage;
''' + reads + r'''
    return {addr,size};
}
int main() {
    unsigned cases=0;
    for(int stage : {0,5,6,7}) {
        NvnState s{};Stage st{stage,{}};
        words[4]=0x76543210;words[5]=0x12;words[6]=0x4320;
        words[8]=0x12345678;words[9]=0x13;words[10]=0x1000;
        st.c1.resize(sizeof words);std::memcpy(st.c1.data(),words,sizeof words);
        for(auto& u:s.ubo[stage])u={guest,sizeof words};
        for(uint32_t bank : {0u,1u,2u,3u,18u}) {
            for(uint32_t k : {0u,1u}) {
                const auto got=decode(s,st,{bank,16},k);
                const uint32_t i=4+4*k;
                const std::array<uint64_t,2> want={words[i] | uint64_t(words[i+1])<<32,words[i+2]};
                if(got!=want){std::fprintf(stderr,"stage %d bank %u entry %u: got %llx/%llx, expected %llx/%llx\n",stage,bank,k,(unsigned long long)got[0],(unsigned long long)got[1],(unsigned long long)want[0],(unsigned long long)want[1]);return 1;}
                ++cases;
            }
        }
        st.c1.resize(16);
        if(decode(s,st,{1,16},0)!=std::array<uint64_t,2>{0,0})return 2;
        s.ubo[stage][0].size=16;
        if(decode(s,st,{3,16},0)!=std::array<uint64_t,2>{0,0})return 3;
        if(decode(s,st,{19,16},0)!=std::array<uint64_t,2>{0,0})return 4;
        cases+=3;
    }
    std::printf("PASS: %u stage/bank/array/bounds descriptor cases\n",cases);
}
'''
    work = ROOT / 'work'
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='storage-cbuf-', dir=work) as temporary:
        directory = Path(temporary).resolve()
        directory.relative_to(work.resolve())
        cpp, exe = directory / 'fixture.cpp', directory / 'fixture.exe'
        cpp.write_text(fixture, encoding='utf-8')
        subprocess.run([args.cxx, '-std=c++20', '-O2', str(cpp), '-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    main()
