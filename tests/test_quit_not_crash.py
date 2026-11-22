"""A quit is not a crash (gpu/vk/crash_markers.h). Four steps of a start leave a file that says they began and did not end,
so that a driver whose compiler crashes on one pipeline does not crash every start: the stage libraries
(hookshot_prepared.bin.warming, one such start and they are left out), the predicted pipelines (.predicting), the warm into
the pipeline cache (.built.warming) and the precompile (.running): two such starts and the step is let go, the precompile's
notes dropped. Closing the window, the Insert menu's Quit and the control socket's quit end the process at once
(ExitProcess: no destructor runs, no thread is joined), so a quit in a step used to count as a crash there: two quits in the
precompile dropped its notes. And the close itself takes time (the caches' saves, up to 10 s for the workers to stop) with
the window not answering: a player who quits again then ends the process (Force Quit, End task; the system at a log-out, a
runner's kill after its quit), and that end came after a quit too. So the close puts the files back before anything else.

The steps' marker code is taken from gpu/vk/vk_draw.cpp and the close from gpu/vk/vk_main.cpp, as they are, and run as
starts that each end one way: through, a crash, a kill with no quit asked for, or a quit (with no start-up screen; with it,
the workers stopping in time; with it, a compile that does not stop within the close's wait; and a quit whose process is
then ended in the close's first other part, or while the close waits for the workers). Every quit comes while the step
runs, before the start hands over to the game. Two quits must leave each step as it was, a quit must neither count nor clear
an earlier crash, a quit then a crash is one crash, and two crashes or kills (one for the libraries) must still let the step
go. Also the source: every close path calls the close first, the close puts the files back before anything else, and every
marker is written through crash_markers. No game or GPU.

python -I -B tests/test_quit_not_crash.py [--cxx g++] [--source <a checkout>]
"""
import argparse
import re
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

FIXTURE = r'''// tests/test_quit_not_crash.py: a start's steps that count the starts which crashed in them, as gpu/vk/vk_draw.cpp has
// them, and the close as gpu/vk/vk_main.cpp has it. The work in between ends as the run is told
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <stdio.h>
#include <string>
#include <thread>
#include <vector>
#include "warm_bounded.h"
#if __has_include("crash_markers.h")
#include "crash_markers.h"   // (a checkout from before it builds too: its quits are then counted as crashes)
#endif

namespace mkwvk {
std::string dir, fate;
std::atomic<bool> halt{false}, prep_running{false}, working{false}, ending{false};
struct Event {
    std::mutex mu;
    std::condition_variable cv;
    bool set = false;
};
using HANDLE = Event*;
constexpr unsigned WAIT_OBJECT_0 = 0, WAIT_TIMEOUT = 258;
HANDLE prep_ended = new Event;
void SetEvent(HANDLE e) {
    std::lock_guard lk(e->mu);
    e->set = true;
    e->cv.notify_all();
}
/// A quit whose process is ended from outside while the close is at this point (quit-killed-early: the close's first part other
/// than the files; quit-killed-waiting: its wait for the workers): the close stays here until then
[[noreturn]] void ended_here() {
    ending.store(true);
    for (;;) std::this_thread::sleep_for(std::chrono::milliseconds(1));
}
unsigned WaitForSingleObject(HANDLE e, unsigned ms) {   // (the close's 10 s, a hundredth of it here)
    if (fate == "quit-killed-waiting") ended_here();
    std::unique_lock lk(e->mu);
    return e->cv.wait_for(lk, std::chrono::milliseconds(ms / 100), [e] { return e->set; }) ? WAIT_OBJECT_0 : WAIT_TIMEOUT;
}
/// One of the close's other parts, where vk_main.cpp has it (the caches' saves, FSR 4's and DLSS's close): each can take time
void slow(const char* /*what*/) {
    if (fate == "quit-killed-early") ended_here();
}
void logf(const char* fmt, ...) {
    va_list a;
    va_start(a, fmt);
    std::vprintf(fmt, a);
    va_end(a);
    std::printf("\n");
    std::fflush(stdout);
}
void prepare_stop() { halt.store(true); }
bool prepare_stopped() { return halt.load(); }
std::string pipe_notes_path() { return dir + "/hookshot_pipelines.bin"; }
std::string precompile_built_path() { return pipe_notes_path() + ".built"; }
std::string predict_pending_path() { return pipe_notes_path() + ".predicting"; }
std::string prepared_path() { return dir + "/hookshot_prepared.bin"; }
void precompile_built_write(size_t n) {
    if (FILE* f = fopen(precompile_built_path().c_str(), "wb")) fprintf(f, "%u\n", (unsigned)n), fclose(f);
}
void pcache_init() {}
void driver_env_note() {}   // (vk_draw.cpp: a completed build records its driver environment; nothing to record here)
void predicted_keys_write(std::vector<uint64_t>) {}   // (vk_draw.cpp: a given-up prediction is recorded as done; tests/test_predict_recorded.py)
void pipeline_cache_save(bool) {}
std::atomic<bool> prepare_saving_now{false};   // (vk_draw.cpp, main's #721: precompile_warm says its pipeline cache save is under way)
struct NoteRef {};
std::vector<NoteRef> pipe_notes_refs() { return std::vector<NoteRef>(3); }

/// The step's work, begun. through: done at once. crash: the process ends there, nothing run after it. quit-screen: it goes on
/// until the close stops it; the other quits and kill: it never stops (no start-up screen, or a compile longer than the close
/// waits)
void work() {
    std::printf("work\n");
    std::fflush(stdout);
    working.store(true);
    if (fate == "crash") std::_Exit(3);
    if (fate == "through") return;
    while (fate != "quit-screen" || !prepare_stopped()) std::this_thread::sleep_for(std::chrono::milliseconds(1));
}

void precompile() {
@PRECOMPILE@
    work();
}

void warm() {
@WARM_HEAD@
    work();
    const bool all = !prepare_stopped();
    static const bool prepare_only = false;
    const size_t total = refs.size();
@WARM_TAIL@
}

void predict() {
@PREDICT_HEAD@
    work();
@PREDICT_TAIL@
}

bool warm_libraries(void (*)(size_t done, size_t total)) {
    work();
    return !prepare_stopped();
}
bool libraries_alone(void (*progress)(size_t done, size_t total)) {   // (prepare_run's driver's part alone)
    const std::string warming = prepared_path() + ".warming";
    const bool libraries = true;
@LIBRARIES@
    return true;
}
}   // namespace mkwvk

extern "C" void mkwvk_close_asked(void) {   // (as vk_main.cpp has it: the stop and the files as they are, the rest stand-ins)
    using namespace mkwvk;
@CLOSE@
}

int main(int argc, char** argv) {
    if (argc != 4) return 1;
    mkwvk::dir = argv[1];
    const std::string step = argv[2];
    mkwvk::fate = argv[3];
    static std::atomic<bool> returned{false};
    const auto run = [step] {
        if (step == "precompile") mkwvk::precompile();
        else if (step == "warm") mkwvk::warm();
        else if (step == "predict") mkwvk::predict();
        else if (step == "libraries") mkwvk::libraries_alone(nullptr);
        else std::_Exit(1);
        returned.store(true);
        mkwvk::SetEvent(mkwvk::prep_ended);   // (prepare_with_screen's worker, once the steps return)
    };
    if (mkwvk::fate == "through" || mkwvk::fate == "crash") {
        run();
        return 0;
    }
    if (mkwvk::fate == "kill") {   // (ended from outside while the step works, no quit asked: End task, kill -9, power cut)
        std::thread(run).detach();
        while (!mkwvk::working.load() && !returned.load()) std::this_thread::sleep_for(std::chrono::milliseconds(1));
        std::_Exit(9);
    }
    // A quit: the step at work on its thread and the close on another (the window's, the control socket's), then the
    // process ends at once, as ExitProcess does (host/posix/win32_posix.c: fflush(NULL), _exit)
    mkwvk::prep_running.store(mkwvk::fate != "quit");   // (the start-up screen's: the close stops the workers and waits)
    std::thread(run).detach();
    while (!mkwvk::working.load() && !returned.load()) std::this_thread::sleep_for(std::chrono::milliseconds(1));
    if (mkwvk::fate.rfind("quit-killed", 0) == 0) {
        // (the close on the window's thread, which does not answer meanwhile: the player quits again, Force Quit or End task,
        // and the process ends there, from outside: nothing more of the close runs)
        std::thread(mkwvk_close_asked).detach();
        while (!mkwvk::ending.load()) std::this_thread::sleep_for(std::chrono::milliseconds(1));
        std::_Exit(9);
    }
    mkwvk_close_asked();
    std::fflush(nullptr);
    std::_Exit(0);
}
'''

QUITS = ("quit", "quit-screen", "quit-slow", "quit-killed-early", "quit-killed-waiting")
# (ended from outside: the starts that exit 9)
KILLED = ("kill", "quit-killed-early", "quit-killed-waiting")
# (how many starts that ended inside a step let it go)
STRIKES = {"precompile": 2, "warm": 2, "predict": 2, "libraries": 1}


def block(source, marker):
    """The statement that starts at marker, through the brace that closes its first one."""
    start = source.index(marker)
    at = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[at] == "{") - (source[at] == "}")
        at += 1
    return source[start:at]


def between(text, start, end):
    at = text.index(start)
    return text[at:text.index(end, at)]


def statements(body):
    """A function body's statements at its top level, in order, comments dropped (an if with its block, and its else)."""
    out, now, depth, at = [], [], 0, 0
    while at < len(body):
        if body.startswith("//", at):
            line_end = body.find("\n", at)
            at = len(body) if line_end < 0 else line_end
            continue
        if body.startswith("/*", at):
            at = body.index("*/", at) + 2
            continue
        c = body[at]
        if c in "\"'":   # (a literal, whole: what is in it is not code)
            end = at + 1
            while body[end] != c:
                end += 2 if body[end] == "\\" else 1
            now.append(body[at:end + 1])
            at = end + 1
            continue
        now.append(c)
        depth += (c in "({") - (c in ")}")
        if depth == 0 and (c == ";" or (c == "}" and not body[at + 1:].lstrip().startswith("else"))):
            out.append(" ".join("".join(now).split()))
            now = []
        at += 1
    return [s for s in out if s and s != ";"]


def close_parts(close):
    """mkwvk_close_asked's statements, its using-directive left out"""
    return [s for s in statements(close[close.index("{") + 1:close.rindex("}")]) if not s.startswith("using ")]


def fixture_source(root):
    draw = (root / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    main_cpp = (root / "gpu/vk/vk_main.cpp").read_text(encoding="utf-8")
    run = block(draw, "void precompile_run(void (*progress)(size_t done, size_t total)) {")
    head = run[run.index('const std::string running = pipe_notes_path() + ".running";'):]
    head = head[:head.index("\n", head.index("done_marker{running};"))]
    warm = block(draw, "void precompile_warm(void (*progress)(size_t done, size_t total)) {")
    predict = block(draw, "void predict_run(void (*progress)(size_t done, size_t total)) {")
    prepare = block(draw, "bool prepare_run(")
    close = block(main_cpp, "void mkwvk_close_asked(void) {")
    # (the close in its order: the preparation's stop and the files put back as they are, each other part (the caches' saves,
    # FSR 4's and DLSS's close) a stand-in that can take time, where a quit's process can be ended)
    stop = block(close, "if (prep_running.load() && !prepare_stopped()) {")
    parts = [stop if s.startswith("if (prep_running.load()") else s if "crash_markers::" in s
             else 'slow("%s");' % re.match(r"[\w:]*", s).group(0) for s in close_parts(close)]
    return (FIXTURE.replace("@PRECOMPILE@", head)
            .replace("@WARM_HEAD@", between(warm, 'const std::string warming = precompile_built_path() + ".warming";',
                                            "// (by program, in the notes' order"))
            .replace("@WARM_TAIL@", block(warm, "if (all && !prepare_stopped()) {"))
            .replace("@PREDICT_HEAD@", between(predict, "int began = 0;", "size_t programs = 0;"))
            .replace("@PREDICT_TAIL@", between(predict, "// (gone on: what is stuck is left to play", 'driver_cache_log("after'))
            .replace("@LIBRARIES@", block(prepare, "if (libraries) {"))
            .replace("@CLOSE@", "\n".join(parts)))


def start(exe, folder, step, fate):
    """One start of the game that ends as told; True when it got to the step's work, False when it went without the step"""
    got = subprocess.run([str(exe), str(folder), step, fate], capture_output=True, text=True, timeout=60)
    worked = "work" in got.stdout.split("\n")
    if got.returncode != (3 if worked and fate == "crash" else 9 if fate in KILLED else 0):
        raise SystemExit("the %s start (%s) ended with %d:\n%s%s" % (fate, step, got.returncode, got.stdout, got.stderr))
    return worked


def starts(exe, tmp, step, fates):
    """A new game folder and its starts in turn, then one more that gets through: whether each got to the step's work"""
    folder = Path(tempfile.mkdtemp(prefix="starts-", dir=tmp))
    (folder / "hookshot_pipelines.bin").write_bytes(b"the notes")
    return [start(exe, folder, step, fate) for fate in list(fates) + ["through"]], folder


def check_behaviour(exe, tmp):
    problems = []

    def expect(step, fates, want, why):
        got, folder = starts(exe, tmp, step, fates)
        if got != want:
            problems.append("%s, starts ended by %s then one through: %s (got to the step's work: %s, wanted %s)"
                            % (step, ", ".join(fates), why, got, want))
        return folder

    for step, strikes in STRIKES.items():
        for end in ("crash", "kill"):   # (a kill with no quit asked for counts as a crash does)
            expect(step, [end] * strikes, [True] * strikes + [False],
                   "a %s in the step at every start no longer lets it go" % end)
        for quit in QUITS:
            expect(step, [quit, quit], [True, True, True], "two quits counted as crashes")
            expect(step, [quit, "crash"], [True, True, strikes > 1], "a quit then a crash: wanted one crash counted")
            if strikes == 2:
                expect(step, ["crash", quit], [True, True, True], "a quit after a crash counted as a second crash")
                expect(step, ["crash", quit, "crash"], [True, True, True, False], "a quit cleared the crash before it")
            folder = Path(tempfile.mkdtemp(prefix="one-quit-", dir=tmp))
            start(exe, folder, step, quit)
            left = sorted(p.name for p in folder.iterdir())
            pending = folder / "hookshot_pipelines.bin.predicting"
            if step == "predict":   # (a prediction that did not finish is begun again at the next start, not counted)
                if left != [pending.name] or pending.read_text().split() != ["0"]:
                    problems.append("predict: a quit (%s) left %s, wanted the prediction pending, with 0 starts that "
                                    "crashed in it" % (quit, left))
            elif left:
                problems.append("%s: a quit (%s) in a new folder left %s" % (step, quit, left))
        if strikes == 2:
            expect(step, ["crash", "through", "crash"], [True, True, True, True],
                   "a start that got through no longer clears the crash before it")
    return problems


def check_source(root):
    problems = []
    if not (root / "gpu/vk/crash_markers.h").is_file():
        problems.append("gpu/vk/crash_markers.h is missing")
    host = {name: (root / name).read_text(encoding="utf-8") for name in
            ("host/control.c", "host/display.c", "host/posix/display_sdl.c", "host/rx_backend.c", "host/hos_relaunch.c")}
    quit_ = block(host["host/control.c"], "static void game_quit(void) {")
    if "rx_close_asked();" not in quit_ or quit_.index("rx_close_asked();") > quit_.index("ExitProcess(0);"):
        problems.append("host/control.c game_quit ends the process without the renderer's close first")
    for name in ("host/display.c", "host/posix/display_sdl.c"):
        lines = [line.strip() for line in host[name].splitlines() if line.strip()]
        calls = [i for i, line in enumerate(lines) if line.startswith("hos_window_closed_exit();")]
        if not calls or any(not lines[i - 1].startswith("rx_close_asked();") for i in calls):
            problems.append(name + ": a close ends the process (hos_window_closed_exit) without rx_close_asked first")
    if "ExitProcess(0);" not in block(host["host/hos_relaunch.c"], "void hos_window_closed_exit(void) {"):
        problems.append("host/hos_relaunch.c: hos_window_closed_exit no longer ends the process: read the close again")
    if '"mkwvk_close_asked"' not in block(host["host/rx_backend.c"], "void rx_close_asked(void) {"):
        problems.append("host/rx_backend.c rx_close_asked no longer calls the renderer's mkwvk_close_asked")
    main_cpp = (root / "gpu/vk/vk_main.cpp").read_text(encoding="utf-8")
    close = block(main_cpp, "void mkwvk_close_asked(void) {")
    if "crash_markers::close();" not in close:
        problems.append("mkwvk_close_asked does not put the steps' files back (crash_markers::close)")
    elif close_parts(close)[0] != "crash_markers::close();":
        problems.append("mkwvk_close_asked does %s before it puts the steps' files back (crash_markers::close): a player who "
                        "ends the game while that takes time is counted as a crash" % close_parts(close)[0].split("(")[0])
    draw = (root / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    for written in ('fopen(running.c_str(), "wb")', 'fopen(warming.c_str(), "wb")', 'fopen(predict_pending_path().c_str(), "wb")'):
        if written in draw:
            problems.append("vk_draw.cpp writes a step's file itself (%s): a close cannot put it back" % written)
    for signature, begins in (("bool prepare_run(", 2), ("void precompile_warm(", 1), ("void precompile_run(", 1),
                              ("void predict_run(", 1)):
        if block(draw, signature).count("crash_markers::begin(") != begins:
            problems.append("%s: wanted %d crash_markers::begin" % (signature, begins))
    prepare = block(draw, "bool prepare_run(")
    stopped = block(prepare, "if (prepare_stopped()) {")
    if "warming" in stopped.replace("crash_markers::close", ""):
        problems.append("prepare_run: a stopped preparation settles the stage libraries' file itself (the close's to do)")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.source.resolve()
    with tempfile.TemporaryDirectory(prefix="quit-not-crash-") as tmp:
        cpp, exe = Path(tmp) / "fixture.cpp", Path(tmp) / "fixture.exe"
        cpp.write_text(fixture_source(root), encoding="utf-8")
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-D_CRT_SECURE_NO_WARNINGS", "-pthread",
                        "-I", str(root / "gpu/vk"), str(cpp), "-o", str(exe)], check=True)
        problems = check_behaviour(exe, tmp) + check_source(root)
    if problems:
        raise SystemExit("FAIL:\n" + "\n".join("- " + p for p in problems))
    print("PASS: a quit in a step leaves its file as the start found it, also when its process is ended while it closes; "
          "crashes and kills with no quit still let the step go")


if __name__ == "__main__":
    main()
