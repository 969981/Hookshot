"""The game starts while the startup precompile's last pipelines still build.

At a start the precompile builds every pipeline noted in earlier sessions, on all the CPUs, before the game. AMD's optimizer
took about 30 s each on a few of them (RX 6650 XT): the console sat at 99%, players closed it as hung, and two closed starts
in a row dropped the notes, so everything was compiled in play again. Once every pipeline has begun, the start now waits
RX_VK_PRECOMPILE_TAIL_MS (3 s by default) and lets the game start with the rest still building:

- each note's PipeEntry is made before the workers start, marked begun, so a draw that needs one meanwhile does not start a
  second full build (it draws with a quick one, or waits, as pipeline_build_waited says) and the map is not touched later;
- the workers keep what they use alive themselves (a shared Run) and are let go, not joined, when the game starts;
- compute builds take the program's compute_state as a dispatch's worker build does, so a dispatch meanwhile is left out;
- only the start before the game hands over: the builder's prepare-only run has no game and still waits for all.

Reads gpu/vk/vk_draw.cpp, vk_main.cpp and vk_internal.h.
python -I -B tests/test_precompile_hand_over.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VK = ROOT / "gpu" / "vk"


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def need(cond, what):
    if not cond:
        raise SystemExit(what)


def main():
    draw = (VK / "vk_draw.cpp").read_text(encoding="utf-8")
    main_cpp = (VK / "vk_main.cpp").read_text(encoding="utf-8")
    header = (VK / "vk_internal.h").read_text(encoding="utf-8")
    run = block(draw, "void precompile_run(void (*progress)(size_t done, size_t total), bool hand_over)")

    need("bool hand_over = false" in header, "hand_over must default to false: a caller that says nothing waits for all")
    prepare_only = block(main_cpp, "if (prepare_only()) {")
    need("precompile_run(progress);" in prepare_only and "precompile_run(progress, true)" not in prepare_only,
         "the builder's prepare-only run has no game to start and must wait for every pipeline")
    need("precompile_run(progress, true);" in main_cpp, "the start before the game must let it start with the last ones building")

    need("std::make_shared<Run>()" in run and "[run, total]" in run, "the workers must keep their own state alive, by value")
    entries = run.index("run->entries.assign(notes.size(), nullptr);")
    need(entries < run.index("threads.emplace_back(worker);"), "the entries must be made before the workers start")
    reg = run[entries:run.index("const size_t total", entries)]
    need("e->started.store(true" in reg and "pipelines.emplace(n.key, std::move(e));" in reg,
         "each note's entry must be in the map, marked begun, before any draw can come for it")
    need("pipelines.count(n.key)" in reg and "program_find(n.prog, n.variant)" in reg,
         "a note whose program is gone, or whose key is there already, must get no entry")
    worker = block(run, "const auto worker = [run, total] {")
    need("pipelines." not in worker and "note_mu" not in worker, "a worker must touch only its own entry, never the map")
    need("if (const VkPipeline made = build_graphics(" in worker and "e->pipeline = made;" in worker,
         "a failed build must not replace a quick pipeline a draw made meanwhile")
    need(worker.index("e->ready.store(true") < worker.index("jobs_notify();"), "a draw waiting for the entry must be woken")
    need("p.compute_state.compare_exchange_strong(none, 1)" in worker, "a compute build must claim the program as the dispatch's does")
    need("p.compute_state.store(2" in worker and "p.compute_state.store(0" in worker,
         "a compute build must say it is done, or give the program back to the dispatch when it failed")
    need('getenv("RX_VK_PRECOMPILE_TAIL_MS")' in run and ": 3000;" in run, "the wait for the last ones is RX_VK_PRECOMPILE_TAIL_MS, 3 s")
    loop = block(run, "while (run->done.load() < total) {")
    need("hand_over && tail_ms > 0 && run->next.load() >= total" in loop,
         "the game may start only once every pipeline has begun, and only when asked to")
    handed = block(run, "if (handed) {")
    need("t.detach();" in handed and "return;" in handed, "threads still building must be let go, not joined")
    need("t.join();" in run[run.index("if (handed) {") + len(handed):], "when all are done the threads are joined as before")
    print("precompile hand-over ok")


if __name__ == "__main__":
    main()
