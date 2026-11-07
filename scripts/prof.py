"""Offline profile of a thread sampled with HOS_SAMPLE + HOS_SAMPLE_FILE (host/mkw_glue.c, mkw-recomp work/prof.py): each line is a stack of
module+offset frames, leaf first. Frames in the game module, the runtime and the renderer (hookshot_game.dll /
libhookshot_game.so, hookshot_rt[.exe], hookshot_vk.dll / libhookshot_vk.so; hookshot.exe before the split) are named
from the linker maps beside them (--maps <the folder the game was built into>; work/sa/build otherwise).

    python -I scripts/prof.py [--maps <dir>] work/runs/<run>/stacks.txt [top] [skip] [only]
    (skip: the first fraction of the samples, e.g. 0.5; only: samples with that function in their stack, e.g.
    bench_replay)

Prints the functions by inclusive share (in the stack at all) and by self share (the leaf), and the leaf modules.
"""
import bisect, collections, os, re, sys

BUILD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "work", "sa", "build")


def load_map(path, base):
    syms = []
    pat = re.compile(r"^\s*0001:[0-9a-f]+\s+(\S+)\s+([0-9a-f]{16})\s", re.I)
    for line in open(path, encoding="utf-8", errors="replace"):
        m = pat.match(line)
        if m:
            syms.append((int(m.group(2), 16) - base, m.group(1)))
    syms.sort()
    return [a for a, _ in syms], [n for _, n in syms]


# (hookshot) the split build: the lifted code in the game module, the host in the runtime, the renderer beside them;
# Windows names (lld-link's /map, based at the preferred load address) and Linux ones (ld.lld's -Map, based at 0)
GAME_MODULES = ("hookshot_game.dll", "libhookshot_game.so", "hookshot.exe")
ELF_SYM = re.compile(r"^\s*([0-9a-f]+)\s+[0-9a-f]+\s+[0-9a-f]+\s+\d+\s+([A-Za-z_$][\w$.@]*)$")


def map_of(mod, dirs):
    """the linker map beside a module, in the first of dirs that has it: <stem>.map"""
    stem = mod[:-4] if mod.endswith((".dll", ".exe")) else mod[:-3] if mod.endswith(".so") else mod
    return next((os.path.join(d, stem + ".map") for d in dirs if os.path.exists(os.path.join(d, stem + ".map"))), None)


def load_any_map(path):
    """either kind of map, as offsets from the module's load base"""
    with open(path, encoding="utf-8", errors="replace") as f:
        head = f.read(4096)
    m = re.search(r"Preferred load address is ([0-9a-f]+)", head, re.I)
    if m:
        return load_map(path, int(m.group(1), 16))
    syms = []
    for line in open(path, encoding="utf-8", errors="replace"):
        e = ELF_SYM.match(line)
        if e and not e.group(2).startswith("."):
            syms.append((int(e.group(1), 16), e.group(2)))
    syms.sort()
    return [a for a, _ in syms], [n for _, n in syms]


def map_dirs(argv):
    """--maps <dir> (repeatable) taken out of argv: where the modules' maps are (the folder the game was built into);
    work/sa/build last"""
    dirs, rest, i = [], [], 0
    while i < len(argv):
        if argv[i] == "--maps" and i + 1 < len(argv):
            dirs.append(argv[i + 1])
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    argv[:] = rest
    return dirs + [BUILD]


def demangle(n):
    # ?name@ns@@... -> ns::name (enough to read)
    m = re.match(r"\?([^@]+)@([^@]*)@", n)
    if m and not n.startswith("??"):
        return "%s::%s" % (m.group(2), m.group(1)) if m.group(2) else m.group(1)
    m = re.match(r"\?\?\$?([^@]+)@", n)
    return m.group(1) if m else n


def main():
    dirs = map_dirs(sys.argv)
    path = sys.argv[1]
    top = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    skip = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    only = sys.argv[4] if len(sys.argv) > 4 else None
    maps = {}
    for mod in GAME_MODULES + ("hookshot_vk.dll", "libhookshot_vk.so", "hookshot_rt.exe", "hookshot_rt"):
        mp = map_of(mod, dirs)
        if mp:
            maps[mod] = load_any_map(mp)

    def name(frame):
        mod, _, off = frame.rpartition("+")
        if mod in maps:
            addrs, names = maps[mod]
            i = bisect.bisect_right(addrs, int(off, 16)) - 1
            if i >= 0:
                return mod.split(".")[0] + ":" + demangle(names[i])
        return mod

    incl, self_, leafmod = collections.Counter(), collections.Counter(), collections.Counter()
    n = 0
    lines = open(path, encoding="utf-8", errors="replace").readlines()
    lines = lines[int(len(lines) * skip):]
    for line in lines:
        frames = line.strip().split(";")
        if not frames or not frames[0]:
            continue
        named = [name(f) for f in frames]
        if only and not any(only in f for f in named):
            continue
        n += 1
        self_[named[0]] += 1
        leafmod[frames[0].rpartition("+")[0]] += 1
        for f in set(named):
            incl[f] += 1
    print("%d samples" % n)
    print("-- leaf modules")
    for k, v in leafmod.most_common(10):
        print("%6.1f%%  %s" % (100.0 * v / n, k))
    print("-- inclusive")
    for k, v in incl.most_common(top):
        print("%6.1f%%  %s" % (100.0 * v / n, k))
    print("-- self")
    for k, v in self_.most_common(top):
        print("%6.1f%%  %s" % (100.0 * v / n, k))


if __name__ == "__main__":
    main()
