"""(issue 890) A lift that lost functions is not reused: two lifts over one gen folder at once (the second one's split_big reads the
chunks the first had already cut, and removes their parts as stale) leave functions that functions.txt lists and other chunks call
but no code_*.c defines. The lift key used to vouch for that C, and every build after it failed at the link. A second build on the
same work folder is refused (POSIX).

python -I -B tests/test_lift_complete.py
"""
import collections
import contextlib
import glob
import importlib.util
import io
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
fake = types.ModuleType("a64emit")   # (the lifter without its compiled translator: the functions' C is made here)
fake.translate = lambda xs: ([], [])
fake.UNHANDLED, fake.TERMINATOR = 1, 2
sys.modules["a64emit"] = fake
import kit  # noqa: E402,F401  (a64-kit on the path)
import a64_lift  # noqa: E402
import chunks  # noqa: E402
import split_hot  # noqa: E402
SPLIT_BIG = chunks.split_big
spec = importlib.util.spec_from_file_location("make_game_under_test", ROOT / "nx2" / "make_game.py")
make_game = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_game)


class Lifter(a64_lift.Lifter):
    def __init__(self, sizes):
        self.funcs = {e: ({}, set()) for e in sizes}
        self.sizes, self.hooks = sizes, {}
        self.stats, self.unhandled, self.ijump = collections.Counter(), collections.Counter(), collections.Counter()

    def emit_function(self, e, texts):
        callee = e + 16 if e + 16 in self.funcs else min(self.funcs)
        self.refs = {"f_%08x" % callee}
        return ("void f_%08x(Ctx *__restrict A64_NOESC c) {\n" % e + "c->x[1] = c->x[2];\n" * (self.sizes[e] // 20) +
                "f_%08x(c);\n}\n" % callee), self.refs


def check(what, ok):
    if not ok:
        raise SystemExit("FAIL: " + what)


def lift(work, sizes, before_split=None):
    """lift.py's chunks.write and make_game's split_hot; before_split: run just before this lift's split_big"""
    gen = os.path.join(work, "sa", "gen")

    def split(gen_dir, files, budget=None):
        before_split()
        return SPLIT_BIG(gen_dir, files, budget)
    if before_split:
        chunks.split_big = split
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            chunks.write(Lifter(sizes), gen, os.path.join(work, "image"), jobs=1)
            if os.path.isfile(os.path.join(gen, "code_hot.c")):
                split_hot.split(gen, 8)
    finally:
        chunks.split_big = SPLIT_BIG
    return gen


def main():
    a64_lift.CHUNK_BYTES = 200000
    # (ordinary functions with a giant one now and then: the chunks holding those are cut into parts)
    sizes = {0x80000000 + 16 * i: 30000 if i % 29 == 7 else 120000 if i % 151 == 3 else 300 for i in range(3000)}
    with tempfile.TemporaryDirectory(prefix="hookshot-lift-complete-") as work:
        os.makedirs(os.path.join(work, "image"))
        os.makedirs(os.path.join(work, "sa", "image"))
        Path(work, "sa", "image", "image.bin").write_bytes(b"")
        hot = os.path.join(work, "hot_funcs.txt")
        Path(hot).write_text("".join("f_%08x\n" % e for e in sorted(sizes)[::97]))
        os.environ.update(A64_HOT_FUNCS=hot, A64_HOT_CHUNK="1")
        gen = lift(work, sizes)
        parts = sorted(f for f in os.listdir(gen) if chunks.PART.fullmatch(f))
        check("the giant functions' chunks are cut into parts", parts)
        check("one lift defines every function it lists", make_game.undefined_functions(gen) == [])
        make_game.write_lift_key(work, "k")
        check("one finished lift is reused", make_game.lifted_before(work, "k"))

        def other_lift_cuts():   # (the other lift wrote the same chunks too, and cuts them first)
            with contextlib.redirect_stdout(io.StringIO()):
                SPLIT_BIG(gen, sorted(glob.glob(os.path.join(gen, "code_[0-9][0-9][0-9].c"))))
        lift(work, sizes, before_split=other_lift_cuts)
        check("two lifts at once leave no part", not any(chunks.PART.fullmatch(f) for f in os.listdir(gen)))
        missing = make_game.undefined_functions(gen)
        check("the functions of the removed parts are found missing", missing)
        table = Path(gen, "a64_table.c").read_text()
        check("they are listed and in the table, as issue 890 saw", all(m in table for m in missing))
        make_game.write_lift_key(work, "k")
        check("a lift that lost functions is not reused, whatever its key", not make_game.lifted_before(work, "k"))
        lift(work, sizes)
        check("the next lift alone defines them again", make_game.undefined_functions(gen) == [] and
              sorted(f for f in os.listdir(gen) if chunks.PART.fullmatch(f)) == parts)

        if os.name != "nt":
            import fcntl   # (POSIX only: Windows has stop_stray_compilers)
            with open(os.path.join(work, ".build_lock"), "w") as held:   # (another build's lock)
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with contextlib.redirect_stdout(io.StringIO()) as said:
                    try:
                        make_game.lock_work(work)
                        refused = False
                    except SystemExit:
                        refused = True
                check("a second build on the same work folder is refused", refused and "Another Hookshot build" in said.getvalue())
            make_game.lock_work(work)
            make_game.lock_work(work)   # (the same process again: main run twice, as the tests do)
            check("the build holds the work folder", work in make_game._WORK_LOCKS)
            make_game._WORK_LOCKS.pop(work).close()
    print("ok: a lift that lost functions (%d here) is found and not reused; one build at a time on a work folder" % len(missing))


if __name__ == "__main__":
    main()
