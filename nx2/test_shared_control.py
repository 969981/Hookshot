"""Check the actual NVN2 compute metadata block and environment getter, without game files or a GPU.

python nx2/test_shared_control.py [--cxx clang++]
The ProgramHeader placeholder is unused by this metadata-only fixture; no shader translation/ABI is claimed.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]

def block(text,marker):
    start=text.index(marker);end=text.index('{',start)+1;depth=1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[start:end]

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--cxx',default='clang++');args=parser.parse_args()
    source=(ROOT/'gpu/vk/vk_shader.cpp').read_text(encoding='utf-8')
    code=block(source,'struct Code {')+';'
    getter=block(source,'u32 SharedMemorySize() const override').replace(' override','')
    start=source.index('c.c1.assign(area.begin() + c1o, area.end());')
    start=source.index('\n',start)+1;end=source.index('c.ok = true;',start)
    metadata=source[start:end]
    fixture='''#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>
using u32=uint32_t;
namespace Shader { struct ProgramHeader { uint8_t unused[80]; }; }
'''+code+'''
struct Environment { const Code& nvn;
'''+getter+'''
};
Code decode(const uint8_t* ctl,bool graphics) { Code c;
'''+metadata+'''
return c; }
int main() {
    unsigned cases=0;
    for(uint32_t bytes : {0u,3072u,32768u,49152u}) {
        uint8_t ctl[0xd8]{};const uint32_t local[3]={16,2,1};
        std::memcpy(ctl+0x58,local,12);std::memcpy(ctl+0x64,&bytes,4);
        const auto compute=decode(ctl,false);
        if(Environment{compute}.SharedMemorySize()!=bytes || std::memcmp(compute.local,local,12)) {
            std::fprintf(stderr,"compute shared size: expected %u, got %u\\n",bytes,Environment{compute}.SharedMemorySize());return 1;
        }
        const auto graphics=decode(ctl,true);
        if(Environment{graphics}.SharedMemorySize()!=0 || graphics.local[0] || graphics.local[1] || graphics.local[2])return 2;
        cases+=2;
    }
    std::printf("PASS: %u compute/graphics metadata cases, including zero and above-device-limit declarations\\n",cases);
}
'''
    work=ROOT/'work';work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='shared-control-',dir=work) as temporary:
        directory=Path(temporary).resolve();directory.relative_to(work.resolve())
        cpp=directory/'fixture.cpp';exe=directory/'fixture.exe';cpp.write_text(fixture,encoding='utf-8')
        subprocess.run([args.cxx,'-std=c++20','-O2',str(cpp),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)

if __name__=='__main__':main()
