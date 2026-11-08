"""A compare-exchange loop ends after a ceiling of attempts, not only on success.

Under robust buffer access an atomic on a word out of bounds need not return what it was given, so a loop that only
leaves on success can spin for ever. On an Intel Arc B390 that hung the frame after the intro movie (issue 17), with no
device loss. Every failed attempt on a real word means another invocation's succeeded, so the ceiling is far above what
contention needs. Every loop (CasLoop, the shared-memory, storage-buffer and global byte and halfword stores) counts its
attempts. python -I -B tests/test_cas_loop_ceiling.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "gpu" / "vk" / "eden" / "shader_recompiler" / "backend" / "spirv" / "spirv_emit_context.cpp"


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def main():
    source = SOURCE.read_text(encoding="utf-8")
    ceiling = re.search(r"constexpr u32 CAS_LOOP_MAX_TRIES = (\d+);", source)
    if not ceiling:
        raise SystemExit("no CAS_LOOP_MAX_TRIES")
    if not 64 <= int(ceiling.group(1)) <= 1 << 16:
        raise SystemExit("CAS_LOOP_MAX_TRIES is %s: too few for contention or too many to end soon" % ceiling.group(1))
    done = block(source, "Id CasLoopDone(")
    if "OpLogicalOr" not in done or "OpUGreaterThanEqual" not in done or "CAS_LOOP_MAX_TRIES" not in done:
        raise SystemExit("CasLoopDone does not leave on success or the ceiling")
    tries = block(source, "Id CasTriesVariable(")
    if "OpStore(tries_var, ctx.u32_zero_value)" not in tries:
        raise SystemExit("the attempt count does not start at zero")
    loops = (
        ("CasLoop", block(source, "Id CasLoop(EmitContext& ctx"), "CasTriesVariable(ctx)", "CasLoopDone(ctx, tries_var, success)"),
        ("the shared-memory stores", block(source, "const auto make_function{[&](u32 mask, u32 size)"),
         "CasTriesVariable(*this)", "CasLoopDone(*this, tries_var, success)"),
        ("the storage-buffer stores", block(source, "void EmitContext::DefineWriteStorageCasLoopFunction("),
         "CasTriesVariable(*this)", "CasLoopDone(*this, tries_var, store_successful)"),
    )
    for name, body, start, leave in loops:
        if start not in body:
            raise SystemExit("%s does not count its attempts" % name)
        if leave not in body:
            raise SystemExit("%s leaves only on success" % name)
        if body.index(start) > body.index("OpLoopMerge"):
            raise SystemExit("%s sets its count inside the loop" % name)
    # (the global narrow stores: one loop body used twice, so the count is declared in the entry block and reset by each)
    marker = "// (hookshot) 8- and 16-bit global stores:"
    if marker in source:
        glob = block(source, marker)
        loop = block(glob, "const auto cas_loop{[&](Id pointer)")
        declared = glob.find("const Id tries_var{AddLocalVariable(")
        if declared < 0 or declared > glob.index("const auto cas_loop{"):
            raise SystemExit("the global stores declare no attempt count before their loop")
        if "OpStore(tries_var, u32_zero_value);" not in loop or loop.index("OpStore(tries_var") > loop.index("OpLoopMerge"):
            raise SystemExit("the global stores do not reset their count before the loop")
        if "CasLoopDone(*this, tries_var, success)" not in loop:
            raise SystemExit("the global stores leave only on success")
    # (the global f32 add's fallback: the same shape as the global stores)
    marker = "// (hookshot) RED/ATOM.E.ADD.F32"
    if marker in source:
        add = source[source.index(marker):]
        add = add[:add.index("atomic_global_add_f32_func = func;")]
        loop = block(add, "const auto cas_loop{[&](Id pointer)")
        declared = add.find("const Id tries_var{AddLocalVariable(")
        if declared < 0 or declared > add.index("const auto cas_loop{"):
            raise SystemExit("the global f32 add declares no attempt count before its loop")
        if "OpStore(tries_var, u32_zero_value);" not in loop or loop.index("OpStore(tries_var") > loop.index("OpLoopMerge"):
            raise SystemExit("the global f32 add does not reset its count before the loop")
        if "CasLoopDone(*this, tries_var, success)" not in loop:
            raise SystemExit("the global f32 add leaves only on success")
    # (ATOMG.CAS is one compare-exchange and no loop: the guest's own loop decides whether to try again)
    single = block(source, "const auto cas{[&](Id pointer)").count("OpAtomicCompareExchange(") if "const auto cas{[&](Id pointer)" in source else 0
    exchanges = source.count("OpAtomicCompareExchange(") - single
    if exchanges != source.count("CasLoopDone(") - 1:
        raise SystemExit("a compare-exchange loop without the ceiling (%d exchanges, %d ceilings)"
                         % (exchanges, source.count("CasLoopDone(") - 1))
    print("compare-exchange loops end at the ceiling ok")


if __name__ == "__main__":
    main()
