"""Hookshot Launcher: the builder's source kept up to date from GitHub, and the game rebuilt with one button.

It runs on the Python the HookshotBuilder package ships (tools\\python\\python.exe), with nothing to install, and shows its window in the
web browser (127.0.0.1 only). Start it with "Hookshot Launcher.bat" beside HookshotBuilder.exe.

    Check       the newest commit of the chosen source (your fork, or NotRexed's official repository) against the one tools\\ was
                last updated to.
    Update      that commit's source as a zip from GitHub; the folders the build reads (gpu, host, nx2, scripts, third_party, and
                launcher, this file's own) written into tools\\. Only files whose bytes changed are written, so unchanged files keep
                their dates; every file changed or removed is kept first in launcher_backups\\ (the last three updates).
    Rebuild     nx2\\make_game.py with the settings in launcher.json, as HookshotBuilder.exe runs it.
    Play        hookshot.exe in the game folder.

Files beside HookshotBuilder.exe: launcher.json (settings), launcher_backups\\, launcher_download\\. In tools\\: .hookshot_source.json
(the commit tools\\ holds, and the files that update wrote, so a file a later commit removes is removed too; files it never wrote are
never touched).
"""
import collections
import datetime
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LAUNCHER_VERSION = 3
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.environ.get("HOOKSHOT_TOOLS") or os.path.dirname(HERE)
HOME = os.environ.get("HOOKSHOT_HOME") or os.path.dirname(TOOLS)
CONFIG = os.path.join(HOME, "launcher.json")
SOURCE_STATE = os.path.join(TOOLS, ".hookshot_source.json")
BUILT_STATE = os.path.join(HOME, "launcher_built.json")   # (the commit the game was last built from)
BACKUPS = os.path.join(HOME, "launcher_backups")
DOWNLOADS = os.path.join(HOME, "launcher_download")
SYNCED = ("gpu", "host", "nx2", "scripts", "third_party", "launcher")
PORT = int(os.environ.get("HOOKSHOT_LAUNCHER_PORT", "47390"))
WINDOWS = os.name == "nt"
API = os.environ.get("HOOKSHOT_GITHUB_API", "https://api.github.com")
CODELOAD = os.environ.get("HOOKSHOT_CODELOAD", "https://codeload.github.com")

DEFAULTS = {
    "source": "fork",
    "fork_repo": "xellow78-lgtm/Hookshot",
    "fork_branch": "main",
    "official_repo": "NotRexed/hookshot",
    "official_branch": "main",
    "nup": "",
    "romfs": "",
    "exefs": "",
    "out": "",
    "language": "en-US",
    "check_minutes": 15,
    # the builder's work folder (its generated code and compiled objects, about 4 GB). Empty: "work" beside
    # HookshotBuilder.exe, on the same drive as the tools, not %LOCALAPPDATA%\HookshotBuilder on C:, which filled a C: drive
    "work": "",
}
LANGUAGES = ["en-US", "en-GB", "fr", "fr-CA", "de", "it", "es", "es-419", "pt-BR", "nl", "pl", "ru", "ja", "ko", "zh-Hans", "zh-Hant"]
STAGES = [("download", "Downloading the new source"), ("sync", "Updating the build tools"), ("read", "Reading your game files"),
          ("lift", "Translating the game"), ("compile", "Compiling"), ("finish", "Finishing")]


# ---- settings and state ----

def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2)
    os.replace(tmp, path)


def load_config():
    cfg = dict(DEFAULTS)
    cfg.update(read_json(CONFIG, {}))
    return cfg


def source_of(cfg, which=None):
    which = which or cfg["source"]
    return (cfg["fork_repo"], cfg["fork_branch"]) if which == "fork" else (cfg["official_repo"], cfg["official_branch"])


def installed():
    s = read_json(SOURCE_STATE, {})
    return {k: s.get(k) for k in ("repo", "branch", "sha", "date", "message", "updated")} if s.get("sha") else None


def built():
    b = read_json(BUILT_STATE, {})
    return b if b.get("sha") else None


# ---- GitHub ----

class GitHubError(Exception):
    pass


def github(path):
    req = urllib.request.Request(API + path, headers={"User-Agent": "HookshotLauncher", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (403, 429) and e.headers.get("X-RateLimit-Remaining") == "0":
            reset = int(e.headers.get("X-RateLimit-Reset", "0") or 0)
            at = datetime.datetime.fromtimestamp(reset).strftime("%H:%M") if reset else "later"
            raise GitHubError("GitHub allows 60 checks an hour without signing in, and they are used up. Try again after %s." % at)
        if e.code == 404:
            raise GitHubError("GitHub has no %s (a wrong repository or branch name in the settings?)" % path.split("/repos/")[-1])
        raise GitHubError("GitHub answered %d for %s" % (e.code, path))
    except (urllib.error.URLError, OSError) as e:
        raise GitHubError("GitHub could not be reached: %s" % getattr(e, "reason", e))


def latest_commit(repo, branch):
    c = github("/repos/%s/commits/%s" % (repo, urllib.request.quote(branch, safe="")))
    return {"repo": repo, "branch": branch, "sha": c["sha"], "date": c["commit"]["committer"]["date"],
            "message": c["commit"]["message"].splitlines()[0][:200]}


def official_ahead(cfg):
    """How many commits NotRexed's branch has that the fork's branch does not (None when unknown)."""
    owner = cfg["official_repo"].split("/")[0]
    try:
        c = github("/repos/%s/compare/%s...%s:%s" % (cfg["fork_repo"], cfg["fork_branch"], owner, cfg["official_branch"]))
        return int(c.get("ahead_by", 0))
    except (GitHubError, KeyError, ValueError):
        return None


def download(repo, sha, dest, progress):
    url = "%s/%s/zip/%s" % (CODELOAD, repo, sha)
    req = urllib.request.Request(url, headers={"User-Agent": "HookshotLauncher"})
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    got = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(dest + ".part", "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            progress(got / total if total else None, got)
    os.replace(dest + ".part", dest)
    return got


# ---- the update of tools\ ----

def sync(zip_path, commit, log):
    """The synced folders of the commit written into tools\\: changed files only, what they replace kept in a backup. Returns counts."""
    with zipfile.ZipFile(zip_path) as z:
        entries = [i for i in z.infolist() if not i.is_dir()]
        if not entries:
            raise RuntimeError("The download was empty.")
        prefix = entries[0].filename.split("/")[0] + "/"
        new = {}
        for i in entries:
            if not i.filename.startswith(prefix):
                continue
            rel = i.filename[len(prefix):]
            if "/" in rel and rel.split("/")[0] in SYNCED and ".." not in rel.split("/"):
                new[rel] = i
        if "nx2/make_game.py" not in new:
            raise RuntimeError("The download has no nx2/make_game.py: it is not a Hookshot source.")
        old = read_json(SOURCE_STATE, {})
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "_" + (old.get("sha") or "unknown")[:7]
        backup = os.path.join(BACKUPS, stamp)
        counts = {"changed": 0, "added": 0, "removed": 0, "same": 0}
        touched = []
        launcher_changed = False

        def keep(rel, dst, move):
            b = os.path.join(backup, *rel.split("/"))
            os.makedirs(os.path.dirname(b), exist_ok=True)
            (shutil.move if move else shutil.copy2)(dst, b)

        for rel, info in sorted(new.items()):
            dst = os.path.join(TOOLS, *rel.split("/"))
            data = z.read(info)
            if os.path.isfile(dst):
                with open(dst, "rb") as f:
                    if f.read() == data:
                        counts["same"] += 1
                        continue
                keep(rel, dst, move=False)
                counts["changed"] += 1
                log("  changed  " + rel)
            else:
                counts["added"] += 1
                log("  added    " + rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst + ".new", "wb") as f:
                f.write(data)
            os.replace(dst + ".new", dst)
            launcher_changed |= rel.startswith("launcher/")
            touched.append(rel)
        for rel in sorted(set(old.get("files", [])) - set(new)):
            dst = os.path.join(TOOLS, *rel.split("/"))
            if os.path.isfile(dst):
                keep(rel, dst, move=True)
                touched.append(rel)
                counts["removed"] += 1
                log("  removed  " + rel)
    write_json(SOURCE_STATE, dict(commit, updated=datetime.datetime.now().isoformat(timespec="seconds"), files=sorted(new)))
    if os.path.isdir(BACKUPS):
        for d in sorted(os.listdir(BACKUPS))[:-3]:
            shutil.rmtree(os.path.join(BACKUPS, d), ignore_errors=True)
    counts["launcher"] = launcher_changed
    counts["only_launcher"] = bool(touched) and all(r.startswith("launcher/") for r in touched)
    counts["nothing"] = not touched
    return counts


# ---- the build ----

def game_running():
    if not WINDOWS:
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq hookshot.exe", "/NH"], capture_output=True, text=True,
                             creationflags=0x08000000).stdout
        return "hookshot.exe" in out.lower()
    except OSError:
        return False


def build_command(cfg):
    py = os.path.join(TOOLS, "python", "python.exe")
    if not os.path.isfile(py):
        py = sys.executable
    cmd = [py, "-I", os.path.join(TOOLS, "nx2", "make_game.py")]
    if cfg["nup"]:
        cmd += ["--nup", cfg["nup"]]
    else:
        cmd += ["--romfs", cfg["romfs"], "--exefs", cfg["exefs"]]
    return cmd + ["--out", cfg["out"], "--language", cfg["language"], "--work", work_folder(cfg)]


def work_folder(cfg):
    return cfg.get("work") or os.path.join(HOME, "work")


def settings_problem(cfg):
    if cfg["nup"]:
        if not os.path.isfile(cfg["nup"]):
            return "The game file (.nup) in the settings was not found: " + cfg["nup"]
    elif not (os.path.isdir(cfg["romfs"]) and os.path.isfile(os.path.join(cfg["exefs"], "main"))):
        return "Set your game file (.nup), or the romfs and exefs folders, in the settings."
    if len(cfg["out"]) < 3:
        return "Set the folder the game is built into in the settings."
    if cfg["language"] not in LANGUAGES:
        return "The language in the settings is not one the game has."
    if not os.path.isfile(os.path.join(TOOLS, "nx2", "make_game.py")):
        return "The build tools were not found in " + TOOLS
    return None


class Job:
    """One update and/or build at a time, in a thread; its state read by the page."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset("idle")

    def reset(self, state):
        self.state = state          # idle, running, done, failed, cancelled
        self.kind = ""
        self.stages = {k: "waiting" for k, _ in STAGES}
        self.current = None
        self.progress = None
        self.log = collections.deque(maxlen=4000)
        self.message = ""
        self.started = self.ended = None
        self.process = None
        self.cancelled = False
        self.relaunch = False

    def view(self):
        with self.lock:
            return {"state": self.state, "kind": self.kind, "stages": [{"id": k, "text": t, "state": self.stages[k]} for k, t in STAGES],
                    "current": self.current, "progress": self.progress, "message": self.message, "started": self.started,
                    "ended": self.ended, "log": list(self.log)[-400:], "relaunch": self.relaunch}

    def say(self, line):
        with self.lock:
            self.log.append(line)

    def stage(self, sid):
        with self.lock:
            if self.current and self.stages.get(self.current) == "running":
                self.stages[self.current] = "done"
            self.current = sid
            self.progress = None
            if sid in self.stages:
                self.stages[sid] = "running"

    def start(self, update, cfg, commit=None):
        with self.lock:
            if self.state == "running":
                return "Something is already running."
        problem = settings_problem(cfg)
        if problem:
            return problem
        if game_running():
            return "The game is running: close it first (the build replaces hookshot.exe)."
        self.reset("running")
        self.kind = "update" if update else "rebuild"
        self.started = time.time()
        if not update:
            for k in ("download", "sync"):
                self.stages[k] = "skipped"
        threading.Thread(target=self.run, args=(update, cfg, commit), daemon=True).start()
        return None

    def finish(self, state, message):
        with self.lock:
            if self.current and self.stages.get(self.current) == "running":
                self.stages[self.current] = "done" if state == "done" else ("cancelled" if state == "cancelled" else "failed")
            self.state = state
            self.message = message
            self.ended = time.time()
            self.process = None

    def run(self, update, cfg, commit):
        try:
            self.previous_sha = (installed() or {}).get("sha")
            if update:
                self.stage("download")
                repo, branch = source_of(cfg)
                commit = commit or latest_commit(repo, branch)
                self.say("Downloading %s %s (%s)" % (repo, commit["sha"][:7], commit["message"]))
                zpath = os.path.join(DOWNLOADS, "source.zip")

                def got(f, n):
                    with self.lock:
                        self.progress = f
                        self.message = "%.1f MB" % (n / 1048576)
                download(repo, commit["sha"], zpath, got)
                if self.cancelled:
                    return self.finish("cancelled", "Cancelled before the build tools were changed.")
                self.stage("sync")
                self.say("Writing the new source into " + TOOLS)
                c = sync(zpath, commit, self.say)
                self.say("%d files changed, %d added, %d removed, %d the same" % (c["changed"], c["added"], c["removed"], c["same"]))
                threading.Thread(target=checker.check, daemon=True).start()   # (the page shows the new commit as installed)
                with self.lock:
                    self.relaunch = c["launcher"]
                    self.message = ""
                b = built()
                if c["only_launcher"] and b and b.get("sha") == self.previous_sha:
                    write_json(BUILT_STATE, dict(b, sha=commit["sha"], date=commit["date"], message=commit["message"]))
                    for k in ("read", "lift", "compile", "finish"):
                        self.stages[k] = "skipped"
                    return self.finish("done", "Only the launcher changed: no rebuild needed. Restart the launcher to use it.")
            if self.cancelled:
                return self.finish("cancelled", "Cancelled.")
            self.build(cfg)
        except GitHubError as e:
            self.finish("failed", str(e))
        except Exception as e:  # (said on the page rather than lost in the console)
            self.say("error: %r" % e)
            self.finish("failed", "Stopped: %s" % e)

    def build(self, cfg):
        cmd = build_command(cfg)
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        for name, folder in (("EMSDK", "emsdk"), ("RX_CPM", "cpm"), ("WGPU_NATIVE", "wgpu-native")):
            if os.path.isdir(os.path.join(TOOLS, folder)):
                env[name] = os.path.join(TOOLS, folder)
        self.say("> " + subprocess.list2cmdline(cmd))
        flags = 0x00004000 | 0x08000000 if WINDOWS else 0   # (below normal priority, no console window: as HookshotBuilder.exe)
        p = subprocess.Popen(cmd, cwd=TOOLS, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                             errors="replace", bufsize=1, creationflags=flags)
        with self.lock:
            self.process = p
        done, error = None, None
        for line in p.stdout:
            line = line.rstrip("\r\n")
            if line.startswith("@@stage "):
                self.stage(line.split(" ")[1])
            elif line.startswith("@@progress "):
                try:
                    with self.lock:
                        self.progress = float(line[11:])
                except ValueError:
                    pass
                continue
            elif line.startswith("@@done "):
                done = line[7:]
            elif line.startswith("@@error "):
                error = line[8:]
            self.say(line)
        p.wait()
        if self.cancelled:
            self.finish("cancelled", "Cancelled.")
        elif p.returncode == 0 and done:
            inst = installed()
            if inst:
                write_json(BUILT_STATE, dict(inst, built=datetime.datetime.now().isoformat(timespec="seconds")))
            self.finish("done", "The game is ready in " + done)
        else:
            self.finish("failed", error or "The build stopped (exit code %s). See the details." % p.returncode)

    def cancel(self):
        with self.lock:
            if self.state != "running":
                return
            self.cancelled = True
            p = self.process
        if p:
            if WINDOWS:
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, creationflags=0x08000000)
            else:
                p.kill()


# ---- checks ----

class Checker:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest = None
        self.ahead = None
        self.error = ""
        self.checked = None
        self.busy = False

    def check(self):
        with self.lock:
            if self.busy:
                return
            self.busy = True
        try:
            cfg = load_config()
            latest = latest_commit(*source_of(cfg))
            ahead = official_ahead(cfg) if cfg["source"] == "fork" else None
            with self.lock:
                self.latest, self.ahead, self.error = latest, ahead, ""
        except GitHubError as e:
            with self.lock:
                self.error = str(e)
        finally:
            with self.lock:
                self.busy = False
                self.checked = time.time()

    def view(self):
        with self.lock:
            return {"latest": self.latest, "ahead": self.ahead, "error": self.error, "checked": self.checked, "busy": self.busy}

    def loop(self):
        while True:
            self.check()
            time.sleep(max(5, int(load_config().get("check_minutes") or 15)) * 60)


# ---- the page ----

TOKEN = secrets.token_hex(16)
job = Job()
checker = Checker()


def state():
    cfg = load_config()
    inst = installed()
    chk = checker.view()
    latest = chk["latest"]
    repo, branch = source_of(cfg)
    update = bool(latest and (not inst or inst["sha"] != latest["sha"]))
    out = cfg["out"]
    return {"version": LAUNCHER_VERSION, "settings": cfg, "languages": LANGUAGES, "installed": inst, "built": built(), "check": chk, "update": update,
            "source": {"repo": repo, "branch": branch, "url": "https://github.com/%s/tree/%s" % (repo, branch)},
            "fork_url": "https://github.com/" + cfg["fork_repo"], "tools": TOOLS, "problem": settings_problem(cfg),
            "playable": bool(out) and os.path.isfile(os.path.join(out, "hookshot.exe")), "job": job.view()}


def apply_settings(body):
    cfg = load_config()
    for k in ("nup", "romfs", "exefs", "out"):
        if isinstance(body.get(k), str):
            cfg[k] = body[k].strip().strip('"')
    if body.get("source") in ("fork", "official"):
        cfg["source"] = body["source"]
    if body.get("language") in LANGUAGES:
        cfg["language"] = body["language"]
    write_json(CONFIG, cfg)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def local(self):   # (only this PC's own page: another site's page in the browser cannot set the header, nor name this host)
        return self.headers.get("Host", "") in ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT)

    def do_GET(self):
        if not self.local():
            return self.send(403, {"error": "not here"})
        if self.path == "/":
            return self.send(200, PAGE.replace("__TOKEN__", TOKEN).encode(), "text/html; charset=utf-8")
        if self.path == "/api/state":
            return self.send(200, state())
        self.send(404, {"error": "no such page"})

    def do_POST(self):
        if not self.local() or self.headers.get("X-Token") != TOKEN:
            return self.send(403, {"error": "not allowed"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            body = {}
        action = self.path.rsplit("/", 1)[-1]
        error = None
        if action == "check":
            threading.Thread(target=checker.check, daemon=True).start()
        elif action == "update":
            # (the newest commit is asked for again when the update starts: the last check's answer could be minutes old, and a
            # commit pushed since was missed. v1 used it, and built the commit before the one that was wanted)
            error = job.start(True, load_config())
        elif action == "rebuild":
            error = job.start(False, load_config())
        elif action == "cancel":
            job.cancel()
        elif action == "play":
            out = load_config()["out"]
            # the game itself: bin\hookshot.exe since NotRexed's launcher came (hookshot.exe in the folder is that launcher,
            # whose Update installs the official release over this build); hookshot.exe in a folder built before
            exe = os.path.join(out, "bin", "hookshot.exe")
            if not os.path.isfile(exe):
                exe = os.path.join(out, "hookshot.exe")
            if job.view()["state"] == "running":
                error = "Wait for the build to finish."
            elif not os.path.isfile(exe):
                error = "hookshot.exe is not in " + out
            else:
                subprocess.Popen([exe], cwd=out, creationflags=0x00000008 if WINDOWS else 0)
        elif action == "settings":
            apply_settings(body)
            threading.Thread(target=checker.check, daemon=True).start()
        elif action == "quit":
            self.send(200, {"ok": True})
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
            return
        else:
            return self.send(404, {"error": "no such action"})
        self.send(200, {"ok": error is None, "error": error})


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hookshot Launcher</title>
<style>
:root{--bg:#f4f1ea;--card:#fffdf8;--ink:#1d1b16;--soft:#5d574b;--faint:#8f8879;--line:#e2dccf;--accent:#2f7d5b;--accent-ink:#fff;--warn:#a2620b;--bad:#b3261e;--bar:#e9e3d6}
@media (prefers-color-scheme:dark){:root{--bg:#14130f;--card:#1d1b16;--ink:#efe9dc;--soft:#b9b1a1;--faint:#857e70;--line:#353128;--accent:#5fbf8f;--accent-ink:#0d1f16;--warn:#e0a24a;--bad:#f2867c;--bar:#2b2820}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Segoe UI",system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:28px 16px 60px}
h1{font-size:22px;margin:0 0 2px;letter-spacing:-.01em}.sub{color:var(--soft);margin:0 0 22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-bottom:16px}
.row{display:grid;grid-template-columns:110px 1fr;gap:6px 14px;align-items:baseline}.k{color:var(--faint);font-size:13px}
.v code{font:13px ui-monospace,Consolas,monospace;background:var(--bar);padding:1px 6px;border-radius:5px}
.msg{color:var(--soft)}.pill{display:inline-block;font-size:12px;font-weight:600;padding:2px 10px;border-radius:99px;margin-left:8px;vertical-align:2px}
.pill.new{background:var(--accent);color:var(--accent-ink)}.pill.ok{background:var(--bar);color:var(--soft)}
.buttons{display:flex;flex-wrap:wrap;gap:10px;margin-top:16px}
button{font:inherit;font-weight:600;border-radius:9px;padding:9px 18px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}button:disabled{opacity:.45;cursor:default}
button.danger{color:var(--bad)}
.note{font-size:13px;color:var(--soft);margin-top:12px}.note.warn{color:var(--warn)}.note.bad{color:var(--bad)}
a{color:var(--accent)}
.stages{list-style:none;margin:0;padding:0}.stages li{display:grid;grid-template-columns:16px 1fr auto;gap:10px;align-items:center;padding:5px 0;color:var(--faint)}
.dot{width:9px;height:9px;border-radius:50%;background:var(--line)}
li.running{color:var(--ink);font-weight:600}li.running .dot{background:var(--warn)}li.done .dot{background:var(--accent)}li.done{color:var(--soft)}
li.failed .dot{background:var(--bad)}li.failed{color:var(--bad)}li.skipped{opacity:.45}
.bar{height:4px;background:var(--bar);border-radius:4px;overflow:hidden;margin:2px 0 4px 26px}.bar>div{height:100%;background:var(--accent);width:0}
pre{max-height:320px;overflow:auto;background:var(--bar);border-radius:8px;padding:10px 12px;font:12px/1.45 ui-monospace,Consolas,monospace;white-space:pre-wrap;word-break:break-all;margin:12px 0 0}
details summary{cursor:pointer;font-weight:600}label{display:block;font-size:13px;color:var(--soft);margin:12px 0 4px}
input,select{width:100%;font:inherit;padding:8px 10px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--ink)}
.radios label{display:flex;gap:8px;align-items:center;color:var(--ink);font-size:14px;margin:6px 0}.radios input{width:auto}
.status{font-weight:600}.status.done{color:var(--accent)}.status.failed{color:var(--bad)}
@media (max-width:520px){.row{grid-template-columns:1fr}.k{margin-top:6px}}
</style></head><body><main>
<h1>Hookshot Launcher</h1><p class="sub">Update the build tools from GitHub and rebuild the game in one go.</p>

<section class="card"><div class="row">
<div class="k">Installed</div><div class="v" id="inst">…</div>
<div class="k">Game built from</div><div class="v" id="built">…</div>
<div class="k">Newest</div><div class="v" id="latest">…</div>
<div class="k">Source</div><div class="v" id="src">…</div></div>
<div id="notes"></div>
<div class="buttons">
<button class="primary" id="go" onclick="act('update')">Update &amp; rebuild</button>
<button id="rebuild" onclick="act('rebuild')">Rebuild only</button>
<button id="play" onclick="act('play')">Play</button>
<button id="check" onclick="act('check')">Check now</button>
<button class="danger" id="cancel" onclick="act('cancel')" hidden>Cancel</button></div>
<div class="note bad" id="err" hidden></div></section>

<section class="card" id="jobcard" hidden><div class="status" id="jobstatus"></div>
<ul class="stages" id="stages"></ul>
<details id="logbox"><summary>Details</summary><pre id="log"></pre></details></section>

<section class="card"><details id="settings"><summary>Settings</summary>
<div class="radios"><label><input type="radio" name="source" value="fork"> Your fork (NotRexed's work plus your fixes)</label>
<label><input type="radio" name="source" value="official"> Official only (NotRexed)</label></div>
<label for="nup">Game file (.nup)</label><input id="nup" spellcheck="false">
<label for="out">Game folder (where the game is built)</label><input id="out" spellcheck="false">
<label for="language">Language</label><select id="language"></select>
<div class="buttons"><button onclick="save()">Save</button><button onclick="act('quit')">Close the launcher</button></div>
<p class="note" id="toolsnote"></p></details></section>
</main>
<script>
const TOKEN="__TOKEN__";let S=null,filled=false;
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[c]);
const when=d=>d?new Date(d).toLocaleString([], {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}):"";
const commit=c=>c?`<code>${esc(c.sha.slice(0,7))}</code> · ${esc(when(c.date))}<div class="msg">${esc(c.message)}</div>`:"";
async function post(a,body){const r=await fetch("/api/"+a,{method:"POST",headers:{"X-Token":TOKEN,"Content-Type":"application/json"},body:JSON.stringify(body||{})});return r.json()}
async function act(a){const e=document.getElementById("err");e.hidden=true;
 if(a==="quit"&&!confirm("Close the launcher?"))return;
 if(a==="cancel"&&!confirm("Stop the build?"))return;
 const r=await post(a);if(r&&r.error){e.textContent=r.error;e.hidden=false}
 if(a==="quit"){document.body.innerHTML='<main><h1>Launcher closed</h1><p class="sub">Start it again with Hookshot Launcher.bat.</p></main>';return}
 setTimeout(tick,300)}
async function save(){const src=document.querySelector('input[name=source]:checked');
 await post("settings",{source:src?src.value:"fork",nup:val("nup"),out:val("out"),language:val("language")});filled=false;tick()}
const val=id=>document.getElementById(id).value;
function render(){const s=S,j=s.job,c=s.check,run=j.state==="running";
 document.getElementById("inst").innerHTML=s.installed?commit(s.installed)+(s.installed.repo!==s.source.repo?`<div class="msg">from ${esc(s.installed.repo)}</div>`:""):'<span class="msg">Not recorded yet: the source came with the builder package.</span>';
 document.getElementById("built").innerHTML=s.built?commit(s.built):'<span class="msg">Not recorded yet (the first build with this launcher records it)</span>';
 document.getElementById("latest").innerHTML=c.latest?commit(c.latest)+(run?'<span class="pill ok">Installing…</span>':s.update?'<span class="pill new">Update available</span>':'<span class="pill ok">Up to date</span>'):(c.busy?"Checking…":'<span class="msg">Not checked yet</span>');
 document.getElementById("src").innerHTML=`<a href="${esc(s.source.url)}" target="_blank" rel="noopener">${esc(s.source.repo)} · ${esc(s.source.branch)}</a>`+(c.checked?` <span class="msg">checked ${esc(when(c.checked*1000))}</span>`:"");
 let n="";if(c.error)n+=`<p class="note bad">${esc(c.error)}</p>`;
 if(s.problem)n+=`<p class="note warn">${esc(s.problem)}</p>`;
 if(s.settings.source==="fork"&&c.ahead>0)n+=`<p class="note warn">NotRexed has ${c.ahead} commit${c.ahead>1?"s":""} your fork doesn't have yet. To bring them in, open <a href="${esc(s.fork_url)}" target="_blank" rel="noopener">your fork</a> and press <b>Sync fork</b>, or switch Settings to <b>Official only</b>.</p>`;
 if(!run&&s.built&&s.installed&&s.built.sha!==s.installed.sha)n+=`<p class="note warn">The game was built from <code>${esc(s.built.sha.slice(0,7))}</code>, older than the build tools (<code>${esc(s.installed.sha.slice(0,7))}</code>): press <b>Rebuild only</b> to bring it up to date.</p>`;
 if(j.relaunch)n+='<p class="note warn">The launcher itself was updated: close it and start it again to use the new version.</p>';
 document.getElementById("notes").innerHTML=n;
 const go=document.getElementById("go");go.disabled=run||!!s.problem;go.textContent="Update & rebuild";
 document.getElementById("rebuild").disabled=run||!!s.problem;document.getElementById("play").disabled=run||!s.playable;
 document.getElementById("check").disabled=c.busy;document.getElementById("cancel").hidden=!run;
 const card=document.getElementById("jobcard");card.hidden=j.state==="idle";
 if(j.state!=="idle"){const secs=Math.round(((j.ended||Date.now()/1000)-j.started));const t=secs>=60?Math.floor(secs/60)+" min "+(secs%60)+" s":secs+" s";
  const st=document.getElementById("jobstatus");st.className="status "+j.state;
  st.textContent=(run?(j.kind==="update"?"Updating and rebuilding":"Rebuilding")+" · "+t:j.message+" · "+t);
  document.getElementById("stages").innerHTML=j.stages.map(g=>`<li class="${g.state}"><span class="dot"></span><span>${esc(g.text)}</span><span class="msg">${g.state==="running"&&j.progress!=null?Math.round(j.progress*100)+"%":g.state==="running"&&j.message?esc(j.message):""}</span></li>`+(g.state==="running"&&j.progress!=null?`<div class="bar"><div style="width:${j.progress*100}%"></div></div>`:"")).join("");
  const pre=document.getElementById("log"),end=pre.scrollTop+pre.clientHeight>=pre.scrollHeight-8;pre.textContent=j.log.join("\n");if(end)pre.scrollTop=pre.scrollHeight}
 if(!filled){filled=true;document.querySelectorAll("input[name=source]").forEach(r=>r.checked=r.value===s.settings.source);
  document.getElementById("nup").value=s.settings.nup;document.getElementById("out").value=s.settings.out;
  document.getElementById("language").innerHTML=s.languages.map(l=>`<option${l===s.settings.language?" selected":""}>${l}</option>`).join("");
  document.getElementById("toolsnote").textContent="Build tools: "+s.tools+" · launcher v"+s.version;if(s.problem)document.getElementById("settings").open=true}}
async function tick(){try{const r=await fetch("/api/state",{cache:"no-store"});S=await r.json();render()}catch(e){}}
tick();setInterval(tick,1000);
</script></body></html>"""


def main():
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:   # (one is running already: its page is opened)
        webbrowser.open("http://127.0.0.1:%d/" % PORT)
        print("The launcher is already running: its page was opened in your browser.")
        return
    if not os.path.exists(CONFIG):
        write_json(CONFIG, load_config())
    threading.Thread(target=checker.loop, daemon=True).start()
    url = "http://127.0.0.1:%d/" % PORT
    print("Hookshot Launcher is running at %s\nIts page is open in your browser. Close this window to stop the launcher." % url, flush=True)
    if not os.environ.get("HOOKSHOT_NO_BROWSER"):
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        job.cancel()


if __name__ == "__main__":
    main()
