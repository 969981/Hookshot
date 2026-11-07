"""Run the actual topology/restart cache fragment with command spies; no game, Vulkan or SDK required."""
import argparse
from pathlib import Path
import subprocess,tempfile

ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cxx',default='clang++');a=p.parse_args()
    text=(ROOT/'gpu/vk/vk_draw.cpp').read_text(encoding='utf-8')
    macro=next(l for l in text.splitlines() if l.startswith('#define DYN_CHANGED'))
    start=text.index('    if (!mesh) {',text.index('void dynamic_state('));end=text.index('\n    }',start)+6
    commands=text[start:end]
    mode='d.mesh = mesh;' if 'd.mesh = mesh;' in text else ''
    start=text.index('    last_dyn = d;',text.index('void dynamic_state('));end=text.index('\n',text.index('last_dyn_valid = true;',start))
    snapshot=text[start:end]
    fixture='''#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#define CHECK(x) do{if(!(x)){std::fprintf(stderr,"FAIL:%d %s\\n",__LINE__,#x);std::exit(1);}}while(0)
struct DynState { uint32_t topo,restart;bool mesh; } last_dyn{};
bool last_dyn_valid=false;int topology_calls=0,restart_calls=0;
uint32_t actual_topology=999,actual_restart=999;
void vkCmdSetPrimitiveTopology(int,uint32_t v){topology_calls++;actual_topology=v;}
void vkCmdSetPrimitiveRestartEnable(int,uint32_t v){restart_calls++;actual_restart=v;}
'''+macro+'''
void run(bool mesh,uint32_t topology,uint32_t restart) {
    int cmd=1;DynState d{};d.topo=topology;d.restart=restart;
'''+mode+'\n'+commands+'\n'+snapshot+'''
}
int main() {
    run(true,3,0);CHECK(topology_calls==0 && restart_calls==0);
    run(false,3,0);CHECK(topology_calls==1 && restart_calls==1 && actual_topology==3 && actual_restart==0);
    run(false,3,0);CHECK(topology_calls==1 && restart_calls==1);
    run(true,0,0);run(true,0,0);run(false,0,0);
    CHECK(topology_calls==2 && restart_calls==2 && actual_topology==0);
    run(false,0,1);CHECK(topology_calls==2 && restart_calls==3 && actual_restart==1);
    last_dyn_valid=false;run(false,0,1);CHECK(topology_calls==3 && restart_calls==4);
    puts("PASS: mesh/vertex transitions, repeated states, restart changes and command-buffer invalidation");
}
'''
    work=ROOT/'work';work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mesh-dynamic-',dir=work) as temp:
        directory=Path(temp).resolve();directory.relative_to(work.resolve());cpp=directory/'fixture.cpp';exe=directory/'fixture.exe';cpp.write_text(fixture,encoding='utf-8')
        subprocess.run([a.cxx,'-std=c++20','-O2',str(cpp),'-o',str(exe)],check=True);subprocess.run([str(exe)],check=True)
if __name__=='__main__':main()
