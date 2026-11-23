"""The sparse mirrors' spare buffers (RX_VK_SPARSE_SPARE_MB; gpu/vk/sparse_spare.h, no game/GPU required).

A sparse VRAM mirror's buffer holds address space and no memory, but NVIDIA's Windows driver takes about 0.06 ms a MB to
create one and 0.07 ms a MB to destroy one, and an area change lets go of hundreds of mirrors and makes hundreds again:
Castle Town to Hyrule Field destroyed 41 to 56 of them at the start of one submission, four times, 52 to 62 ms each. So a
freed mirror's buffer is kept, with what was bound to it unbound, and the next mirror of exactly its size takes it.

The header's list is compiled and run (what a size takes, the cap, which spares go for a new one), and vk_memory.cpp is
read for the rules a kept buffer needs: nothing is destroyed before the GPU is past it and its unbinding, the blocks'
memory goes only once the unbinding is done, a mirror takes a buffer by the size it was created with, and with
RX_VK_SPARSE_SPARE_MB=0 every buffer is destroyed at once, as before.

python -I -B tests/test_sparse_spare.py [--cxx g++]
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def need(text, needles, where):
    for n in needles:
        if n not in text:
            raise SystemExit(f"vk_memory.cpp: {where} no longer has: {n}")


def ordered(text, needles, where):
    at = -1
    for n in needles:
        nxt = text.find(n, at + 1)
        if nxt < 0:
            raise SystemExit(f"vk_memory.cpp: {where} no longer has, in this order: {n}")
        at = nxt


def check_source():
    mem = (ROOT / "gpu/vk/vk_memory.cpp").read_text(encoding="utf-8")
    # (the cap: RX_VK_SPARSE_SPARE_MB, the header's default without it; 0 is no spares)
    need(block(mem, "uint64_t sparse_spare_cap() {"), ['getenv("RX_VK_SPARSE_SPARE_MB")', "sparse_spare::kDefaultMb", "<< 20",
                                                      "std::min<uint64_t>(asked, dev.props.limits.sparseAddressSpaceSize / 4)"], "sparse_spare_cap()")
    # (a mirror's buffer: the spare of its size first, with the spare's requirements; a new one counts as made)
    take = block(mem, "bool sparse_buffer_for(")
    ordered(take, ["sparse_spare_cap() && sparse_spares.take(n, &spare)", "after = spare.after;", "req.size = spare.size, req.alignment = spare.block, req.memoryTypeBits = spare.types;",
                   "return true;", "create_sparse_buffer(n, usage, buffer, req)"], "sparse_buffer_for()")
    # (given up: never destroyed while a bind of it may be pending; at once only with no spares, as before)
    doom = block(mem, "void sparse_buffer_doom(")
    ordered(doom, ["if (!sparse_spare_cap()) {", "destroy_sparse_buffer(buffer);", "return;", "spare_doomed.push_back({after, buffer});"], "sparse_buffer_doom()")
    unused = block(mem, "void sparse_buffer_unused(")
    need(unused, ["sparse_spares.put({buffer, n, req.size, req.alignment, req.memoryTypeBits, after}, sparse_spare_cap(), &gone)",
                  "sparse_buffer_doom(buffer, after);", "for (const auto& g : gone) sparse_buffer_doom(g.buffer, g.after);"], "sparse_buffer_unused()")
    if "destroy_sparse_buffer(" in unused:
        raise SystemExit("vk_memory.cpp: sparse_buffer_unused() destroys a buffer itself: a spare's unbinding may still be on the queue")
    # (mirror_make: through those, and the size it made the buffer with recorded once the mirror has it)
    make = block(mem, "bool mirror_make(Pool& pool) {")
    need(make, ["sparse_buffer_for(n, kUsage, sparse_buf, sparse_req, spare_after)", "sparse_buffer_unused(sparse_buf, n, sparse_req, spare_after);",
                "sparse_buffer_doom(sparse_buf, spare_after);", "if (sparse_buf) p->sparse_made = n;"], "mirror_make()")
    if make.count("sparse_buffer_unused(sparse_buf, n, sparse_req, spare_after);") != 2:
        raise SystemExit("vk_memory.cpp: mirror_make() gives a buffer it bound nothing to back in two places (no live block, over the budget)")
    if "destroy_sparse_buffer(" in make or "create_sparse_buffer(" in make:
        raise SystemExit("vk_memory.cpp: mirror_make() makes or destroys a sparse buffer past the spares")
    ordered(make, ["sparse_fill(*p, sparse_buf, sparse_req, live)", "sparse_buffer_doom(sparse_buf, spare_after);", "if (sparse_buf) p->sparse_made = n;"],
            "mirror_make()'s fill")
    # (mirror_free: what is bound, a block's memory or a released block's still bound, noted before the lists are emptied)
    free = block(mem, "void mirror_free(Pool& p) {")
    ordered(free, ["dead_sparse.push_back(", "if (sparse_spare_cap() && p.sparse_made) {",
                   "d.spare = {p.mirror, p.sparse_made, p.sparse_size, p.sparse_block_bytes, p.sparse_types, 0};",
                   "return p.blocks[i] || (i < p.released.size() && p.released[i]);", "d.unbind.push_back({at, end - at, VK_NULL_HANDLE, 0, 0});",
                   "for (VmaAllocation& a : p.blocks)", "for (VmaAllocation& a : p.released)", "p.sparse_made = 0;"], "mirror_free()")
    # (sparse_reap: a doomed buffer waits for the binds' timeline; a freed mirror for the GPU and its binds, as before; kept
    # buffers are unbound in one bind, destroyed with their memory if that fails, and their memory waits for the unbinding)
    reap = block(mem, "void sparse_reap() {")
    ordered(reap, ["for (auto it = spare_doomed.begin(); it != spare_doomed.end();) {", "if (it->first > done) {", "destroy_sparse_buffer(it->second);"],
            "sparse_reap()'s doomed buffers")
    ordered(reap, ["dead_sparse.front().value <= mem_completed && dead_sparse.front().binds <= done", "sparse_live.erase(d.buffer);",
                   "if (d.spare.made && sparse_spare_cap()) {", "keep.push_back(std::move(d));", "continue;", "destroy_sparse_buffer(d.buffer);",
                   "for (VmaAllocation a : d.blocks) free_sparse_block(a);", "if (keep.empty()) return;",
                   "if (!d.unbind.empty()) infos.push_back({d.buffer, (uint32_t)d.unbind.size(), d.unbind.data()});",
                   "if (!sparse_bind_infos(infos, &after)) {", "destroy_sparse_buffer(d.buffer);", "for (VmaAllocation a : d.blocks) free_sparse_block(a);",
                   "return;", "if (!after) after = sparse_tl_value.load(std::memory_order_relaxed);", "d.spare.after = after;",
                   "sparse_spares.put(d.spare, sparse_spare_cap(), &gone)", "for (const auto& g : gone) sparse_buffer_doom(g.buffer, g.after);",
                   "sparse_frees.push_back({after, std::move(blocks)});"], "sparse_reap()'s freed mirrors")
    kept = reap[reap.index("if (!after) after = sparse_tl_value.load(std::memory_order_relaxed);"):]
    if "free_sparse_block(" in kept or "destroy_sparse_buffer(" in kept:
        raise SystemExit("vk_memory.cpp: sparse_reap() frees a kept buffer's memory, or destroys one, before its unbinding is done on the GPU")
    # (the log says what the spares did, every 1200 submissions with the sparse mirrors' line)
    need(block(mem, "void memory_begin_submission() {"), ["vk: sparse mirrors' buffers: %lld taken from the spares, %lld made new; %lld kept as spares, %lld destroyed"],
         "memory_begin_submission()'s log")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="sparse-spare-") as tmp:
        exe = Path(tmp) / "sparse-spare"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", str(ROOT / "tests/test_sparse_spare.cpp"), "-o", str(exe)],
                       check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
