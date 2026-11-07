"""Exercise the injected scene-scale invalidation without game data or a GPU.

python -I -B tests/test_resolution_scale.py [--cxx g++] [--source nx2/oot_patches.py]
These checks validate state transitions, not rendered image quality.
"""
import argparse
import importlib.util
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--cxx', default=os.environ.get('CXX', 'g++'))
parser.add_argument('--source', type=Path, default=ROOT / 'nx2/oot_patches.py')
args = parser.parse_args()
spec = importlib.util.spec_from_file_location('resolution_patch_under_test', args.source)
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)
original_cap = 'if (((c->x[9] >> 19) & 1)) { goto L_831f3ff0; }'
fixture = '#include "a64.h"\n' + patch.SCENE_ENTRY + '\n' + original_cap + '\nL_831f3ff0:;\n}\n'
updated = patch.apply_scene_scale_fix(fixture)
assert patch.apply_scene_scale_fix(updated) == updated
assert updated.count(patch.SCENE_SCALE_FIX) == 1
assert updated.count(original_cap) == 1
for bad in (fixture.replace(patch.SCENE_ENTRY, 'unknown'), fixture.replace('#include "a64.h"', 'unknown')):
    try:
        patch.apply_scene_scale_fix(bad)
    except ValueError:
        pass
    else:
        raise AssertionError('Unexpected guest layout accepted')

code=r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <cstdio>
#include <limits>
struct Ctx { uint64_t x[32]{}; };
unsigned char memory[4096]{};
volatile int hos_fixed_resolution=1;
volatile uint32_t hos_fixed_scale_bits=0x3f800000u;
uint32_t recomp_load32(Ctx*,uint64_t at) { assert(at+4<=sizeof memory); uint32_t v; memcpy(&v,memory+at,4); return v; }
void recomp_store32(Ctx*,uint64_t at,uint32_t v) { assert(at+4<=sizeof memory); memcpy(memory+at,&v,4); }
void set_float(int offset,float v) { memcpy(memory+offset,&v,4); }
void scale(float v) { uint32_t u; memcpy(&u,&v,4); hos_fixed_scale_bits=u; }
void apply(Ctx* c) {
'''+patch.SCENE_SCALE_FIX+r'''
}
bool bypass_cap(Ctx* c) {
'''+original_cap+r'''
return false;
L_831f3ff0: return true;
}
int main() {
 Ctx c; const bool enabled=getenv("HOS_FIX_RES") && atoi(getenv("HOS_FIX_RES"));
 set_float(3664,1152); set_float(3672,1152); set_float(3676,.75f);
 recomp_store32(&c,3628,1); apply(&c);
 assert(recomp_load32(&c,3628)==(enabled?9u:1u)); // existing resource-dirty bit survives
 // Model the actual consumer: it updates the cached scale only when bit 3 is set.
 if (recomp_load32(&c,3628)&8) set_float(3676,1.f);
 float cached; memcpy(&cached,memory+3676,4); assert(cached==(enabled?1.f:.75f));
 set_float(3676,1.f); recomp_store32(&c,3628,0); apply(&c);
 assert(recomp_load32(&c,3628)==0); // no perpetual invalidation
 scale(.67f); apply(&c); assert(recomp_load32(&c,3628)==(enabled?8u:0u));
 recomp_store32(&c,3628,0); hos_fixed_resolution=0; scale(1.25f); apply(&c);
 assert(recomp_load32(&c,3628)==0 && !bypass_cap(&c)); // Auto is unchanged
 hos_fixed_resolution=1; assert(!bypass_cap(&c)); // final fix leaves engine cap intact
 scale(1.f); assert(!bypass_cap(&c)); // native and lower retain original cap
 scale(2.01f); apply(&c); assert(!bypass_cap(&c) && recomp_load32(&c,3628)==0);
 scale(std::numeric_limits<float>::quiet_NaN()); apply(&c); assert(recomp_load32(&c,3628)==0 && !bypass_cap(&c));
 scale(-1.f); apply(&c); assert(recomp_load32(&c,3628)==0 && !bypass_cap(&c));
 scale(1.f); set_float(3664,0); apply(&c); assert(recomp_load32(&c,3628)==0);
 set_float(3664,1152); set_float(3672,0); apply(&c); assert(recomp_load32(&c,3628)==0);
 set_float(3664,1440); set_float(3672,1152); set_float(3676,.8f); apply(&c);
 assert(recomp_load32(&c,3628)==0); // compare in the scene's normalized units
 c.x[9]=1ull<<19; assert(bypass_cap(&c)); // guest's original opt-out remains valid
 puts("PASS resolution invalidation and cap cases");
}
'''
with tempfile.TemporaryDirectory(prefix='resolution-scale-') as tmp:
    cpp = Path(tmp) / 'fixture.cpp'
    exe = Path(tmp) / ('fixture.exe' if os.name == 'nt' else 'fixture')
    cpp.write_text(code, encoding='utf-8')
    subprocess.run([args.cxx, '-std=c++17', '-D_CRT_SECURE_NO_WARNINGS', '-Wall', '-Wextra', '-Werror',
                    str(cpp), '-o', str(exe)], check=True)
    for mode in (None, '0', '1'):
        env = dict(os.environ)
        if mode is None:
            env.pop('HOS_FIX_RES', None)
        else:
            env['HOS_FIX_RES'] = mode
        subprocess.run([str(exe)], env=env, check=True)
print('PASS: patch idempotence, layout guards, and compiled scale invalidation with opt-in absent/0/1')
