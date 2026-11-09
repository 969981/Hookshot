"""Exercise the actual microphone ring buffer without a capture device."""
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'host/nn_audio.c').read_text()
start = source.index('static void in_write_bytes(')
end = source.index('\nstatic float in_gain(', start)
function = source[start:end]
harness = r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
typedef struct { int channels, bps; uint8_t *ring; int cap, rpos, wpos, filled; } InStream;
''' + function + r'''
int main(void) {
    uint8_t ring[8]={0}, data[]={1,2,3,4,5,6,7,8,9,10};
    InStream s={1,2,ring,8,0,0,0};
    in_write_bytes(&s,data,6);
    assert(s.filled==3 && s.wpos==6 && s.rpos==0);
    in_write_bytes(&s,data+6,4);
    assert(s.filled==4 && s.wpos==2 && s.rpos==2);
    assert(ring[2]==3 && ring[4]==5 && ring[6]==7 && ring[0]==9);
    in_write_bytes(&s,data,1);
    assert(s.filled==4 && s.wpos==2 && s.rpos==2);
    s.channels=0; in_write_bytes(&s,data,4); assert(s.filled==4);
    return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    src = Path(tmp)/'ring.c'
    exe = Path(tmp)/'ring'
    src.write_text(harness)
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',str(src),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
print('microphone ring buffer checks passed')
