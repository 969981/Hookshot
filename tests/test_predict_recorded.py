"""Compile shaders on All (the default) runs the prediction at a start whose folder has no record of a finished one
(vk_main.cpp's `predict`, Longshot #713: a folder prepared on Most, then All). So every way predict_run ends, except a closed
window, must leave that record (predicted_keys_write), or the next start predicts again:
- no pipeline notes: the prediction, with its bar, would be tried at every start;
- given up after two starts that crashed in it: two starts in three would crash where the third played on for good;
- gone on past pipelines stuck in the driver: every start would stall on them again;
- out of its time budget (RX_VK_PREDICT_S): the rest is built in play, as before #713.
A closed window leaves no record and keeps its marker, so the next start finishes it; RX_VK_NO_PREDICT leaves none either, so a
start without the switch predicts.

This cuts predict_run's opening (up to its crash marker) and its ending (from the sample on) out of vk_draw.cpp, the record's
reader and writer, and the `predict` condition out of vk_main.cpp, compiles them with stubs, and runs each ending followed by
the next start.

    python -I -B tests/test_predict_recorded.py [--cxx clang++]
"""
import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]


def body(text, marker):
    """The function or block that starts at marker, braces included"""
    start = text.index(marker)
    at = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[at] == "{") - (text[at] == "}")
        at += 1
    return text[start:at]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cxx", default="clang++")
    args = ap.parse_args()
    draw = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    main_cpp = (ROOT / "gpu/vk/vk_main.cpp").read_text(encoding="utf-8")

    run = body(draw, "void predict_run(void (*progress)(size_t done, size_t total)) {")
    begin = "crash_markers::begin(predict_pending_path(), began + 1, began);"
    head = run[run.index("{") + 1:run.index(begin) + len(begin)]
    if head.count("return;") != 2:
        raise SystemExit("predict_run's opening no longer returns in two places (no notes, given up): this check needs a look")
    head = head.replace("return;", "return false;")   # (the opening's returns: it did not go on to build)
    tail_from = "    if (!prepare_stopped() && !out_of_time.load() && !gone_on) {"
    tail = run[run.index(tail_from):run.rindex("}")]
    rw = "\n".join(body(draw, m) for m in ("void predicted_keys_write(std::vector<uint64_t> keys) {",
                                            "bool predicted_keys_read(std::vector<uint64_t>& keys) {",
                                            "bool predicted_complete() {", "bool predict_pending() {"))
    m = re.search(r"const bool predict = ([^;]*);", body(main_cpp, "if (prepare_only()) {"))
    if not m or "predicted_complete()" not in m.group(1):
        raise SystemExit("vk_main.cpp: the preparation's `predict` condition is not where this check looks, or no longer asks "
                         "predicted_complete()")

    cpp = r"""
#include "crash_markers.h"
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <vector>
#include <algorithm>
#include <chrono>
using std::size_t;
struct PipeNote { int n = 0; };
std::string folder;
size_t notes_n = 0;
bool stopped = false, no_predict = false, all = true;
int sampled = 0, fails = 0;
std::string pipe_notes_path() { return folder + "/hookshot_pipelines.bin"; }
std::string predict_pending_path() { return pipe_notes_path() + ".predicting"; }
std::string predicted_keys_path() { return pipe_notes_path() + ".predicted_keys"; }
uint64_t program_cache_stamp() { return 42; }
std::vector<std::unique_ptr<PipeNote>> pipe_notes_read() {
    std::vector<std::unique_ptr<PipeNote>> v;
    for (size_t i = 0; i < notes_n; i++) v.push_back(std::make_unique<PipeNote>());
    return v;
}
bool env_on(const char* name) { return !std::strcmp(name, "RX_VK_NO_PREDICT") && no_predict; }
bool prepare_stopped() { return stopped; }
bool compile_all() { return all; }
void logf(const char*, ...) {}
void predicted_sample_write(const std::vector<std::unique_ptr<PipeNote>>&) { sampled++; }
void driver_env_note() {}
void driver_cache_log(const char*) {}
@RW@
// (predict_run's opening: false when it returned there, true when it went on to build)
bool predict_head() {
    @HEAD@
    (void)t0;
    return true;
}
// (and its ending, after its workers: gone on past stuck ones, out of its time budget, or finished)
struct Pre {
    std::vector<std::unique_ptr<PipeNote>> predicted;
    std::mutex keys_mu;
    std::vector<uint64_t> made_keys;
};
void predict_tail(bool gone_on, bool budget_out, std::vector<uint64_t> made) {
    auto pre = std::make_shared<Pre>();
    pre->made_keys = std::move(made);
    std::atomic<bool> out_of_time{budget_out};
@TAIL@
}
bool next_start_predicts(bool prepared) {
    const bool predict = @COND@;
    return predict;
}
void fresh(const char* name) {
    folder = std::string(std::getenv("CHECK_DIR")) + "/" + name;
    std::remove(predict_pending_path().c_str());
    std::remove(predicted_keys_path().c_str());
    notes_n = 5, stopped = false, no_predict = false, sampled = 0;
}
void expect(bool ok, const char* what) {
    if (!ok) std::printf("FAIL %s\n", what), fails++;
}
void marker(int began) {
    if (FILE* f = std::fopen(predict_pending_path().c_str(), "wb")) std::fprintf(f, "%d\n", began), std::fclose(f);
}
int main() {
    // (a folder prepared on Most, Compile shaders now All: no record, so this start predicts)
    fresh("most");
    expect(next_start_predicts(false), "a folder prepared on Most does not predict on All (#713)");

    fresh("no-notes");
    notes_n = 0;
    expect(next_start_predicts(false) && !predict_head(), "no notes: the prediction went on");
    expect(!next_start_predicts(false), "no notes: the next start predicts again");

    fresh("given-up");   // (two starts crashed in the prediction: the third gives it up)
    marker(2);
    expect(next_start_predicts(false) && !predict_head(), "after two crashed starts the prediction was not given up");
    expect(!next_start_predicts(false), "given up after two crashed starts: the next start predicts (and crashes) again");

    fresh("crashed-once");   // (one crashed start: the next one predicts again, from the marker)
    marker(1);
    expect(next_start_predicts(false) && predict_head(), "one crashed start: the next one does not predict again");

    fresh("stalled");
    expect(predict_head(), "the prediction did not start");
    predict_tail(true, false, {7, 3, 7});
    std::vector<uint64_t> keys;
    expect(predicted_keys_read(keys) && keys == std::vector<uint64_t>({3, 7}), "a stalled prediction's keys are not recorded");
    expect(sampled == 0, "a stalled prediction's sample was written");
    expect(!next_start_predicts(false), "gone on past stuck pipelines: the next start predicts (and stalls) again");

    fresh("budget");
    expect(predict_head(), "the prediction did not start");
    predict_tail(false, true, {1});
    expect(!next_start_predicts(false), "out of RX_VK_PREDICT_S: the next start predicts again");

    fresh("finished");
    expect(predict_head(), "the prediction did not start");
    predict_tail(false, false, {});
    expect(sampled == 1 && !next_start_predicts(false), "a finished prediction (no keys) is not recorded, or not sampled");

    fresh("closed");   // (the window closed in it: no record, the marker kept, the next start finishes it)
    expect(predict_head(), "the prediction did not start");
    stopped = true;
    predict_tail(false, false, {1, 2});
    stopped = false;
    expect(next_start_predicts(false) && predict_pending(), "a closed window's prediction is not taken up again");

    fresh("switched-off");
    no_predict = true;
    expect(!predict_head(), "RX_VK_NO_PREDICT: the prediction ran");
    no_predict = false;
    expect(next_start_predicts(false), "RX_VK_NO_PREDICT left a record: a start without it does not predict");

    fresh("prepared");   // (a new preparation predicts whatever the record says)
    predict_tail(false, false, {});
    expect(next_start_predicts(true), "a start that prepared does not predict");
    all = false;
    expect(!next_start_predicts(true), "Most predicts");
    return fails ? 1 : 0;
}
"""
    cpp = cpp.replace("@RW@", rw).replace("@HEAD@", head).replace("@TAIL@", tail).replace("@COND@", m.group(1))
    with tempfile.TemporaryDirectory(prefix="hookshot-predict-recorded-") as tmp:
        folder = Path(tmp).resolve()
        for name in ("most", "no-notes", "given-up", "crashed-once", "stalled", "budget", "finished", "closed", "switched-off",
                     "prepared"):
            (folder / name).mkdir()
        src = folder / "t.cpp"
        src.write_text(cpp, encoding="utf-8")
        exe = folder / "t.exe"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                        "-D_CRT_SECURE_NO_WARNINGS", "-I", str(ROOT / "gpu/vk"), str(src), "-o", str(exe)], check=True)
        ran = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60, env={**os.environ, "CHECK_DIR": str(folder)})
        if ran.returncode:
            raise SystemExit(ran.stdout + ran.stderr)
    print("every end of the prediction but a closed window is recorded, and the next start on All does not predict again: ok")


if __name__ == "__main__":
    main()
