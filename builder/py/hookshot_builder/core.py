"""What the WPF builder's Runner.cs, Storage.cs, Finder.cs and BuildWorkers.cs do, the same rules: the build tools, nx2/make_game.py as a
child process, what the builder keeps on the PC, the game files found on it, and the game's shortcuts."""
import ctypes
import json
import os
import shutil
import string
import subprocess
import sys
import threading
import time
from ctypes import wintypes

from . import paths

CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


# ---------------- the build tools (Runner.cs: Tools) ----------------

class Tools:
    """Where nx2/make_game.py and its Python are: the installed version's tools, or the source tree this app sits in"""

    def __init__(self, root, python, python_args=None):
        self.root, self.python, self.python_args = root, python, python_args or []

    @property
    def version(self):
        """Which release these tools are (tools/version.txt, written when the release is packed); None from the source tree"""
        return paths.read_small(os.path.join(self.root, "version.txt")) or None

    @staticmethod
    def find():
        from . import setup_parts
        env = os.environ.get("HOOKSHOT_TOOLS")   # (development: these tools)
        cands = [env] if env else []
        cur = setup_parts.current()
        if cur:
            cands.append(os.path.join(cur, "HookshotBuilder", "tools"))
        for c in cands:
            if c and os.path.isfile(os.path.join(c, "nx2", "make_game.py")):
                # (tools with a Python of their own run no other. With its python.exe gone, the first "python" Windows found
                # ran them instead: another version, without the modules they need, or not one the player chose to trust
                # with the game files. Its start now fails and says how to get it back: own_python_missing)
                py = os.path.join(c, "python", "python.exe")
                return Tools(c, py if os.path.isfile(py) or os.path.isdir(os.path.join(c, "python")) else "python")
        d = paths.APP_DIR
        while True:   # (a source checkout: builder/py/hookshot_builder in it)
            if os.path.isfile(os.path.join(d, "nx2", "make_game.py")):
                return Tools(d, sys.executable)
            up = os.path.dirname(d)
            if up == d:
                return None
            d = up


OWN_PYTHON_MISSING = ("The builder's own Python (tools/python/python.exe) is missing. Your antivirus probably removed it: restore "
                      "it from quarantine, then try again.")


def own_python_missing(tools):
    """The tools' own Python is gone: an antivirus took it, most often, after a false alarm on the download"""
    return not os.path.isfile(tools.python) and os.path.isdir(os.path.join(tools.root, "python"))


def build_env(tools, workers=None, ngx_sdk=None):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if workers:
        env["HOOKSHOT_JOBS"] = str(int(workers))
    # (written into the game folder's hookshot_version.txt: which builder made the folder)
    env["HOOKSHOT_BUILDER_EXE"] = paths.launcher_command()[0]
    # (NVIDIA DLSS: only when ticked. Without it, one set on the PC is not passed on either)
    if ngx_sdk:
        env["HOOKSHOT_NGX_SDK"] = ngx_sdk
    else:
        env.pop("HOOKSHOT_NGX_SDK", None)
    for name, folder in (("EMSDK", "emsdk"), ("RX_CPM", "cpm"), ("WGPU_NATIVE", "wgpu-native")):
        p = os.path.join(tools.root, folder)
        if os.path.isdir(p):
            env[name] = p
    return env


def build_command(tools, args, script="make_game.py"):
    return [tools.python, *tools.python_args, "-I", os.path.join(tools.root, "nx2", script), *args]


def script_output(tools, script, args):
    """One of the tools' scripts (nx2/<script>) run to its end (Runner.cs Output): its exit code, its lines on stdout and its last
    line on stderr (what it said when it stopped); -1 when it could not start"""
    try:
        p = subprocess.run(build_command(tools, args, script), cwd=tools.root, env=build_env(tools), capture_output=True,
                           stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    except OSError as e:
        return -1, [], OWN_PYTHON_MISSING if own_python_missing(tools) else str(e)
    errs = [l for l in p.stderr.decode("utf-8", "replace").splitlines() if l.strip()]
    return p.returncode, p.stdout.decode("utf-8", "replace").splitlines(), errs[-1].strip() if errs else None


def library(tools, base):
    """The game library under a base: nx2/game_folders.py --library <base> --json, run by the tools' Python as make_game.py is
    (Runner.cs Library), the one core the three Builders read. None when the tools could not say"""
    code, lines, _ = script_output(tools, "game_folders.py", ["--library", base, "--json"])
    if code != 0:
        return None
    try:
        games = json.loads("\n".join(lines))
    except ValueError:
        return None
    return games if isinstance(games, list) else None


class Runner:
    """nx2/make_game.py as a child process: its lines as they come (on_line, from a worker thread), and how it ended (on_finished:
    None, "cancelled", or why it stopped)"""

    def __init__(self, tools, args, workers=None, ngx_sdk=None, on_line=None, on_finished=None):
        if workers is not None and workers <= 0:
            raise ValueError("Build workers must be positive.")
        self.tools, self.args, self.workers, self.ngx_sdk = tools, args, workers, ngx_sdk
        self.on_line, self.on_finished = on_line or (lambda l: None), on_finished or (lambda f: None)
        self.process, self.error, self.cancelled = None, None, False

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            p = subprocess.Popen(build_command(self.tools, self.args), cwd=self.tools.root,
                                 env=build_env(self.tools, self.workers, self.ngx_sdk), stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                 creationflags=CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS)
        except OSError as e:
            self.on_finished(OWN_PYTHON_MISSING if own_python_missing(self.tools) else "The build tools could not start: %s" % e)
            return
        self.process = p

        def errs():
            for raw in p.stderr:
                self.on_line(raw.decode("utf-8", "replace").rstrip("\r\n"))
        threading.Thread(target=errs, daemon=True).start()
        for raw in p.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("@@error "):
                self.error = line[8:]
            self.on_line(line)
        p.wait()
        self.on_finished("cancelled" if self.cancelled else None if p.returncode == 0 else
                         self.error or "The build tools stopped without saying why. See the details.")

    def cancel(self):
        self.cancelled = True
        p = self.process
        if p and p.poll() is None:   # (the whole tree: the compilers it started too)
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, creationflags=CREATE_NO_WINDOW)


def check(tools, args):
    """Is this the supported game and version? (ok, the answer in plain words)"""
    try:
        p = subprocess.run(build_command(tools, args), cwd=tools.root, env=build_env(tools), capture_output=True,
                           stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    except OSError:
        if own_python_missing(tools):
            return False, OWN_PYTHON_MISSING
        return False, "Python 3 was not found. Install it from python.org (not the Microsoft Store build)."
    result, answered = (False, "The game files could not be read."), False
    for line in p.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("@@check ok "):
            result, answered = (True, line[11:]), True
        elif line.startswith("@@check bad "):
            result, answered = (False, line[12:]), True
        elif line.startswith("@@error "):
            result, answered = (False, line[8:]), True
    if not answered:
        errs = [l for l in p.stderr.decode("utf-8", "replace").splitlines() if l.strip()]
        last = errs[-1] if errs else None
        if last and "microsoft store" in last.lower():
            return False, ("Python 3 was not found: the 'python' command on this PC is the Microsoft Store shortcut, which only opens "
                           "the Store. Install Python 3 from python.org (not the Store version).")
        return False, last or "The game tools stopped without saying why (exit code %d)." % p.returncode
    return result


# ---------------- build workers (BuildWorkers.cs) ----------------

AUTOMATIC = "Automatic"


def worker_choices():
    return [AUTOMATIC] + list(range(1, max(1, os.cpu_count() or 1) + 1))


# ---------------- what the builder keeps on the PC (Storage.cs) ----------------

BUILD_FILES_BYTES = 4 << 30


def keep_build_files():
    v = os.environ.get("HOOKSHOT_KEEP_BUILD_FILES", "")
    return bool(v) and v != "0"


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)).rstrip("\\/") == os.path.normcase(os.path.abspath(b)).rstrip("\\/")


def work():
    """Where builds keep their files: the folder the player chose (work.txt), else the builder's own"""
    chosen = paths.read_small(os.path.join(paths.base(), "work.txt"))
    return chosen if len(chosen) > 2 and os.path.isabs(chosen) else paths.base()


def work_chosen():
    return not _same(work(), paths.base())


def ngx_sdk():
    return os.path.join(paths.base(), "ngx-sdk")


WORK_PARTS = ("objcache", "sa", "exefs", "probe", "image", "system_headers.txt", "hot_funcs.txt", "hot_order.txt",
              "hookshot_pieces.prof", "hookshot.res")


def _delete(p):
    try:
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.isfile(p):
            os.remove(p)
    except OSError:
        shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else None


def move_work(picked):
    """The build files go to a folder of their own in the one the player picked (None: back to the builder's own); what the last
    builds left at the old place goes"""
    old = work()
    to = paths.base() if picked is None else os.path.join(picked, "Hookshot build files")
    if _same(old, to):
        return
    os.makedirs(to, exist_ok=True)
    os.makedirs(paths.base(), exist_ok=True)
    if picked is None:
        try:
            os.remove(os.path.join(paths.base(), "work.txt"))
        except OSError:
            pass
    else:
        paths.write_small(os.path.join(paths.base(), "work.txt"), to)
    for n in WORK_PARTS:
        _delete(os.path.join(old, n))
    if not _same(old, paths.base()):
        try:
            os.rmdir(old)
        except OSError:
            pass
    for key, _name, _bid in PROFILES:   # (each other game's, <old>-<key>, beside it: what the builds left there goes too)
        other = _rule_folder(old, key)
        if key == DEFAULT_KEY or not os.path.isdir(other) or _same(other, _rule_folder(to, key)):
            continue
        for n in WORK_PARTS:
            _delete(os.path.join(other, n))
        try:
            os.rmdir(other)
        except OSError:
            pass


def _kept_version(d):
    from . import setup_parts
    cur = setup_parts.current()
    return bool(cur) and _same(cur, d)


def kept_translation(sa):
    """make_game.py keep_translation left the lift's small files in sa (its .lift_key marked "kept"): the next update with the same
    tools skips the lift with them. Not let go after a build (Free up space still lets them go)"""
    try:
        with open(os.path.join(sa, "gen", ".lift_key"), encoding="utf-8") as f:
            return f.read().split()[2:3] == ["kept"]
    except (OSError, UnicodeDecodeError):
        return False


def game_work(key):
    """A game's own build files folder: work() for the default game, <work>-<key> for another (make_game.py's rule)"""
    return _rule_folder(work(), key)


def work_folders():
    """The build files folders: the default game's (work()), and each other game's beside it (<work>-<key>, as make_game.py
    names it for --base, --update and Update all), those that are there. Storage.cs WorkFolders"""
    yield work()
    for key, _name, _bid in PROFILES:
        if key != DEFAULT_KEY and os.path.isdir(work() + "-" + key):
            yield work() + "-" + key


def freeable(after_build=False):
    for w in work_folders():
        for d in (("sa", "exefs", "probe") if after_build else ("objcache", "sa", "exefs", "probe")):
            if not (after_build and d == "sa" and kept_translation(os.path.join(w, d))):
                yield os.path.join(w, d)
    for d in ("downloads", "update"):
        yield os.path.join(paths.base(), d)
    v = paths.versions()
    if os.path.isdir(v):
        for name in os.listdir(v):
            p = os.path.join(v, name)
            if os.path.isdir(p) and not _kept_version(p):
                yield p


def _size(d, seen):
    n = 0
    for root, _dirs, files in os.walk(d):
        for f in files:
            try:
                st = os.stat(os.path.join(root, f))
            except OSError:
                continue
            if not st.st_size:
                continue
            if st.st_nlink > 1:   # (an object hard-linked in both sa\build and objcache counted once)
                key = (st.st_dev, st.st_ino)
                if key in seen:
                    continue
                seen.add(key)
            n += st.st_size
    return n


def measure():
    """(bytes in the builder's folder and the build files folder, bytes Free lets go)"""
    seen = set()
    free = sum(_size(d, seen) for d in freeable())
    seen = set()
    total = _size(paths.base(), seen) + (_size(work(), seen) if work_chosen() else 0) + \
        sum(_size(w, seen) for w in list(work_folders())[1:])
    return max(total, free), free


def free(after_build=False):
    """Lets go of what measure counts as freeable; returns the bytes it could not delete (a file in use)"""
    for d in list(freeable(after_build)):
        _delete(d)
    if not after_build:
        for w in work_folders():
            _delete(os.path.join(w, "system_headers.txt"))
        return measure()[1]
    seen = set()
    return sum(_size(d, seen) for d in freeable(True))


def gb(n):
    return "%.1f GB" % (n / (1 << 30)) if n >= 1 << 30 else "%d MB" % (n >> 20)


def free_space(path):
    try:
        root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
        return root, shutil.disk_usage(root).free
    except OSError:
        return None, None


def spinning(path):
    """True on a hard drive, False on an SSD, None when the drive cannot be told (Disk.cs's IOCTL: the volume's seek penalty).
    A build reads and writes tens of gigabytes, and an SSD makes that much faster: a warning only, never a stop."""
    try:
        from ctypes import wintypes
        root = os.path.splitdrive(os.path.abspath(os.path.expanduser(path)))[0]
        if not root:
            return None

        class Query(ctypes.Structure):
            _fields_ = [("PropertyId", wintypes.DWORD), ("QueryType", wintypes.DWORD), ("Additional", wintypes.DWORD)]

        class SeekPenalty(ctypes.Structure):
            _fields_ = [("Version", wintypes.DWORD), ("Size", wintypes.DWORD), ("IncursSeekPenalty", ctypes.c_ubyte)]

        k = ctypes.windll.kernel32
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        k.CreateFileW.restype = wintypes.HANDLE
        k.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        k.DeviceIoControl.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k.CreateFileW("\\\\.\\" + root, 0, 0x1 | 0x2, None, 3, 0, None)   # (read + write share, OPEN_EXISTING)
        if not handle or handle == wintypes.HANDLE(-1).value:
            return None
        try:
            query, answer, returned = Query(7, 0, 0), SeekPenalty(), wintypes.DWORD()
            ok = k.DeviceIoControl(handle, 0x002D1400, ctypes.byref(query), ctypes.sizeof(query), ctypes.byref(answer),
                                   ctypes.sizeof(answer), ctypes.byref(returned), None)
            if not ok or returned.value < ctypes.sizeof(answer):
                return None
            return bool(answer.IncursSeekPenalty)
        finally:
            k.CloseHandle(handle)
    except (OSError, ValueError, AttributeError):
        return None


# ---------------- the player's game files (Finder.cs) ----------------

class Dump:
    def __init__(self, path, where, size, modified):
        self.path, self.where, self.size, self.modified = path, where, size, modified


def _known_folder(fid):
    buf = ctypes.c_wchar_p()
    guid = (ctypes.c_byte * 16).from_buffer_copy(fid)
    if ctypes.windll.shell32.SHGetKnownFolderPath(guid, 0, None, ctypes.byref(buf)) == 0:
        p = buf.value
        ctypes.windll.ole32.CoTaskMemFree(buf)
        return p
    return None


def _guid(s):
    import uuid
    return uuid.UUID(s).bytes_le


FOLDER_DESKTOP = _guid("B4BFCC3A-DB2C-424C-B029-7FE99A87C641")
FOLDER_DOCUMENTS = _guid("FDD39AD0-238F-46AF-ADB4-6C85480369C7")
FOLDER_DOWNLOADS = _guid("374DE290-123F-4565-9164-39C4925E467B")
FOLDER_PROGRAMS = _guid("A77F5D77-2E2B-44C3-A6A2-ABA601054A51")


def find_dumps():
    """.nup files in Downloads, Desktop, Documents, the home folder and the top of each drive, one folder level down too, newest first"""
    home = os.path.expanduser("~")
    places = [(_known_folder(FOLDER_DOWNLOADS) or os.path.join(home, "Downloads"), "Downloads"),
              (_known_folder(FOLDER_DESKTOP) or os.path.join(home, "Desktop"), "Desktop"),
              (_known_folder(FOLDER_DOCUMENTS) or os.path.join(home, "Documents"), "Documents"), (home, "Home")]
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if mask >> i & 1:
            root = letter + ":\\"
            if ctypes.windll.kernel32.GetDriveTypeW(root) in (2, 3):   # (removable, fixed)
                places.append((root, letter + ":"))
    seen, found = set(), []
    for d, name in places:
        if not d or not os.path.isdir(d) or os.path.normcase(os.path.abspath(d)) in seen:
            continue
        seen.add(os.path.normcase(os.path.abspath(d)))
        dirs = [d]
        try:
            subs = [e.path for e in os.scandir(d) if e.is_dir() and not e.name.startswith((".", "$"))
                    and e.name.lower() != "windows" and not e.name.lower().startswith("program")]
            dirs += subs[:200]
        except OSError:
            pass
        for sub in dirs:
            try:
                for e in os.scandir(sub):
                    if e.is_file() and e.name.lower().endswith(".nup"):
                        full = os.path.abspath(e.path)
                        if os.path.normcase(full) not in seen:
                            seen.add(os.path.normcase(full))
                            st = e.stat()
                            found.append(Dump(full, name, st.st_size, st.st_mtime))
            except OSError:
                pass
    return sorted(found, key=lambda x: -x.modified)


def ago(t):
    import datetime
    now, then = datetime.datetime.now(), datetime.datetime.fromtimestamp(t)
    days = (now - then).total_seconds() / 86400
    if days < 1 and then.date() == now.date():
        return "modified today"
    if days < 2:
        return "modified yesterday"
    if days < 14:
        return "%d days ago" % int(days)
    if days < 60:
        return "%d weeks ago" % int(days / 7)
    return "%d %s" % (then.day, then.strftime("%b %Y"))


def size_text(n):
    return "%.1f GB" % (n / (1 << 30)) if n >= 1 << 30 else "%d MB" % (n >> 20)


# ---------------- the game folder ----------------

def _version_line(folder):
    for line in paths.read_small(os.path.join(folder, "hookshot_version.txt")).splitlines():
        if line.startswith("version="):
            return line[8:].strip() or None
    return None


def game_version(folder):
    """The release a game folder was built with, when it keeps its game files for updates"""
    if not os.path.isdir(os.path.join(folder, ".hookshot_exefs")) or not os.path.isdir(os.path.join(folder, "romfs")):
        return None
    return _version_line(folder)


def read_version(folder):
    """(a folder built before it kept its game files has a version too: only shown)"""
    return _version_line(folder)


def game_exe(folder):
    g = os.path.join(folder, "bin", "hookshot.exe")
    return g if os.path.isfile(g) else os.path.join(folder, "hookshot.exe")


def built_games(out):
    """"Your games": each game that builds, in its own folder by nx2/game_folders.py's rule from the game folder (out), when that
    folder holds a built game (image.bin and the game): the tools' game_folders.built_games, as the Linux builder's; the WPF
    builder's Games.cs keeps the same. Each a dict: key, name, folder, base. Nothing without the tools"""
    game_folders = tools_game_folders()
    if game_folders is None or not out:
        return []
    return game_folders.built_games(out, lambda f: game_exe(f) if os.path.isfile(game_exe(f)) else None)


def tools_game_folders():
    """The tools' nx2/game_folders.py (the folder rule, a folder's game, its shortcut's name), or None without the tools"""
    tools = Tools.find()
    if not tools:
        return None
    nx2 = os.path.join(tools.root, "nx2")
    if nx2 not in sys.path:
        sys.path.insert(0, nx2)
    try:
        import game_folders
    except ImportError:
        return None
    return game_folders


# (the profiles that build, as Games.cs Profiles: key, game, main's build id, in nx2/game_profile.py's order, and the default
# game's key. For what is to be said without the tools' answer: the library's fallback, the launcher page's game, the other
# games' build files folders. tests/test_builder_games.py holds them to nx2/game_profile.py)
PROFILES = (
    ("oot", "The Legend of Zelda: Ocarina of Time", "e0f3ccfaf50065ae4ea01eb5aeb13b1a45da3348"),
    ("dkb", "Donkey Kong Bananza", "e25c90c1c136638885659fd55bf8a03b3403489d"),
    ("mkw", "Mario Kart World", "9e244438c3d8ef1c703a6a85ef99c4f000000000"),
)
DEFAULT_KEY = "oot"


def _rule_folder(base, key):
    b = base.rstrip("\\/") or base
    return b if key == DEFAULT_KEY else b + "-" + key


def _base_of(folder):
    f = folder.rstrip("\\/") or folder
    for key, _name, _bid in PROFILES:
        s = "-" + key
        if key != DEFAULT_KEY and len(f) > len(s) and f.endswith(s) and f[-len(s) - 1] not in "\\/":
            return f[:-len(s)]
    return f


def _held_key(folder):
    """The profile key of the copy a folder keeps (.hookshot_exefs/main's build id: Games.cs FolderGameKey), or None"""
    try:
        with open(os.path.join(folder, ".hookshot_exefs", "main"), "rb") as f:
            head = f.read(0x60)
    except OSError:
        return None
    if len(head) < 0x60 or head[:4] != b"NSO0":
        return None
    bid = head[0x40:0x60].hex()
    return next((k for k, _n, b in PROFILES if b.startswith(bid) or bid.startswith(b)), None)


def _version_in(folder):
    for line in paths.read_small(os.path.join(folder, "hookshot_version.txt")).splitlines():
        if line.startswith("version="):
            return line[8:].strip() or None
    return None


def library_fallback(base):
    """The library when the tools could not answer (Games.cs Fallback): every game, the ones built in their folders by the rule
    found, each folder's kept game files and version read here, and no update flag (None: "Installed", with Update). Never
    "built before updates came in" because the answer failed: only for a folder that keeps no game files"""
    if not base:
        return []
    b = _base_of(base)
    games = []
    for key, name, _bid in PROFILES:
        f = _rule_folder(b, key)
        built = os.path.isfile(os.path.join(f, "image.bin")) and os.path.isfile(game_exe(f))
        kept = built and os.path.isdir(os.path.join(f, ".hookshot_exefs")) and os.path.isdir(os.path.join(f, "romfs"))
        games.append({"key": key, "name": name, "folder": f, "base": b, "built": built, "other_folders": [], "own_folder": f,
                      "own_folder_holds": None, "version": _version_in(f) if built else None, "kept": kept,
                      "update": None if kept else False})
    return games


def folder_key(folder):
    """The game a folder is (game_folders.folder_key): its kept copy's, else its folder by the rule"""
    game_folders = tools_game_folders()
    if game_folders:
        return game_folders.folder_key(folder)
    held = _held_key(folder)
    if held:
        return held
    f = folder.rstrip("\\/") or folder
    return next((k for k, _n, _b in PROFILES if _rule_folder(_base_of(f), k) == f), DEFAULT_KEY)


def game_name(key):
    """A profile's game, or "Hookshot" for none"""
    return next((n for k, n, _b in PROFILES if k == key), "Hookshot")


def title_parts(name):
    """The launcher page's two lines for a game (game_folders.title_parts): its series in capitals, and its title"""
    head, sep, rest = name.partition(": ")
    return (head.upper(), rest) if sep and head and rest else ("", name)


def shortcut_folder(folder):
    """The folder a game's shortcuts start (game_folders.shortcut_folder): its own folder when that holds the game"""
    game_folders = tools_game_folders()
    return game_folders.shortcut_folder(folder) if game_folders else folder


def older(have, new):
    """have is a known version older than new (the tools' nx2/version.py release_key, the updaters' order); False when either
    names no version or the tools are not there"""
    if tools_game_folders() is None:
        return False
    import version
    a, b = version.release_key(have), version.release_key(new)
    return bool(a and b and a < b)


def play_folder(base, key):
    """The folder `--play <base> --game <key>` starts (builder/setup/setup.c play_target): base for the default game, base-<key>
    for another (nx2/game_folders.py folder)"""
    import game_folders
    return game_folders.folder(base, key)


def start_game(folder):
    """(the launcher already checked for updates: the game does not ask again)"""
    env = dict(os.environ, HOOKSHOT_NO_UPDATE_CHECK="1")
    subprocess.Popen([game_exe(folder)], cwd=folder, env=env)


def open_folder(folder):
    subprocess.Popen(["explorer.exe", folder])


# ---------------- shortcuts (Finder.cs: IShellLinkW + IPersistFile, Windows' own shell object writes the .lnk) ----------------

def shortcut_name(key):
    """A game's shortcut name (game_folders.shortcut_name, Games.cs ShortcutName): Hookshot for the default game, the name its
    shortcut always had (and for None), "<game> (Hookshot)" for another"""
    if key in (None, DEFAULT_KEY) or key not in [k for k, _n, _b in PROFILES]:
        return "Hookshot"
    return "".join(c for c in game_name(key) if c not in '\\/:*?"<>|').strip() + " (Hookshot)"


def desktop_link(key=None):
    return os.path.join(_known_folder(FOLDER_DESKTOP) or os.path.join(os.path.expanduser("~"), "Desktop"), shortcut_name(key) + ".lnk")


def start_link(key=None):
    return os.path.join(_known_folder(FOLDER_PROGRAMS) or "", shortcut_name(key) + ".lnk")


def folder_link(game_folder):
    """The game folder's own launcher: Play, and Update left of it when a newer release is out (the setup exe's launch mode)"""
    return os.path.join(game_folder, "Hookshot.lnk")


shortcut_error = None


def _com_call(obj, index, restype, *args):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *[type(a) for a in args])(vtbl[index])
    return fn(obj, *args)


def make_link(link, target, args, workdir, icon, description):
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    clsid = (ctypes.c_byte * 16).from_buffer_copy(_guid("00021401-0000-0000-C000-000000000046"))
    iid_link = (ctypes.c_byte * 16).from_buffer_copy(_guid("000214F9-0000-0000-C000-000000000046"))
    iid_file = (ctypes.c_byte * 16).from_buffer_copy(_guid("0000010B-0000-0000-C000-000000000046"))
    sl = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(clsid, None, 1, iid_link, ctypes.byref(sl))
    if hr != 0:
        raise OSError("CoCreateInstance(ShellLink) failed: 0x%08x" % (hr & 0xFFFFFFFF))
    try:
        W = ctypes.c_wchar_p
        _com_call(sl, 20, ctypes.HRESULT, W(target))            # SetPath
        _com_call(sl, 11, ctypes.HRESULT, W(args))              # SetArguments
        _com_call(sl, 9, ctypes.HRESULT, W(workdir))            # SetWorkingDirectory
        _com_call(sl, 17, ctypes.HRESULT, W(icon), ctypes.c_int(0))   # SetIconLocation
        _com_call(sl, 7, ctypes.HRESULT, W(description))        # SetDescription
        pf = ctypes.c_void_p()
        _com_call(sl, 0, ctypes.HRESULT, ctypes.cast(iid_file, ctypes.c_void_p), ctypes.byref(pf))   # QueryInterface
        try:
            os.makedirs(os.path.dirname(link), exist_ok=True)
            _com_call(pf, 6, ctypes.HRESULT, W(link), wintypes.BOOL(True))   # IPersistFile::Save
        finally:
            _com_call(pf, 2, ctypes.c_ulong)
    finally:
        _com_call(sl, 2, ctypes.c_ulong)


def shortcuts(game_folder, desktop, start, folder=True):
    """Made or removed as the ticks say: each starts this app's launcher for the folder (Play, and Update when a newer release is
    out), with the game's own icon, named and described by the game the folder holds (folder_key)"""
    global shortcut_error
    own = shortcut_folder(game_folder)   # (first: a game in two folders has its shortcuts, --launch and icon, on its own one)
    key = folder_key(own)
    for link, want, f in ((desktop_link(key), desktop, own), (start_link(key), start, own),
                          (folder_link(game_folder), folder, game_folder)):   # (the folder's own launcher: that folder's)
        try:
            if not want:
                if os.path.isfile(link):
                    os.remove(link)
                continue
            game_link(link, f, key)
        except OSError as e:
            shortcut_error = "%s: %s" % (os.path.basename(link), e)


def game_link(link, game_folder, key):
    """One shortcut onto a game folder: this app's launcher for it (--launch), with the game's own icon, described by the game.
    None made when the folder has no game"""
    exe, args = paths.launcher_command()
    icon = os.path.join(game_folder, "bin", "hookshot.exe")
    if not os.path.isfile(icon):
        icon = os.path.join(game_folder, "hookshot.exe")
    if os.path.isfile(icon):
        make_link(link, exe, subprocess.list2cmdline(args + ["--launch", game_folder]), game_folder, icon,
                  "%s, built by Hookshot" % game_name(key))


def link_workdir(link):
    """The working folder a .lnk starts in (IShellLinkW GetWorkingDirectory, after IPersistFile Load), or None"""
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    clsid = (ctypes.c_byte * 16).from_buffer_copy(_guid("00021401-0000-0000-C000-000000000046"))
    iid_link = (ctypes.c_byte * 16).from_buffer_copy(_guid("000214F9-0000-0000-C000-000000000046"))
    iid_file = (ctypes.c_byte * 16).from_buffer_copy(_guid("0000010B-0000-0000-C000-000000000046"))
    sl = ctypes.c_void_p()
    if ole32.CoCreateInstance(clsid, None, 1, iid_link, ctypes.byref(sl)) != 0:
        return None
    try:
        pf = ctypes.c_void_p()
        _com_call(sl, 0, ctypes.HRESULT, ctypes.cast(iid_file, ctypes.c_void_p), ctypes.byref(pf))   # QueryInterface
        try:
            _com_call(pf, 5, ctypes.HRESULT, ctypes.c_wchar_p(link), wintypes.DWORD(0))   # IPersistFile::Load
        finally:
            _com_call(pf, 2, ctypes.c_ulong)
        buf = ctypes.create_unicode_buffer(1024)
        _com_call(sl, 8, ctypes.HRESULT, buf, ctypes.c_int(1024))   # GetWorkingDirectory
        return buf.value or None
    finally:
        _com_call(sl, 2, ctypes.c_ulong)


def repoint_default(default_folder):
    """A Hookshot.lnk (the default game's name) that an older Builder made for the folder it built, holding another game: made
    again onto the default game's folder when that game is installed (default_folder), else left as it is, and nothing said.
    The other game first gets its own named shortcut in the same place, so it keeps one. Finder.cs RepointDefault"""
    if not default_folder:
        return
    for link in (desktop_link(), start_link()):
        try:
            if not os.path.isfile(link):
                continue
            was = link_workdir(link)
            if not was or folder_key(was) == DEFAULT_KEY:
                continue
            key = folder_key(was)   # (that game keeps a shortcut: its own named one, in the same place, first)
            own = os.path.join(os.path.dirname(link), shortcut_name(key) + ".lnk")
            if not os.path.isfile(own):
                game_link(own, shortcut_folder(was), key)
            game_link(link, default_folder, DEFAULT_KEY)
        except (OSError, AttributeError, ValueError):
            pass


def refresh_shortcuts(game_folder):
    key = folder_key(game_folder)
    shortcuts(game_folder, os.path.isfile(desktop_link(key)), os.path.isfile(start_link(key)))


# ---------------- what the player chose last time (builder-settings.json, the WPF builder's) ----------------

def settings_path():
    return os.path.join(paths.base(), "builder-settings.json")


def load_settings():
    try:
        with open(settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(s):
    try:
        os.makedirs(paths.base(), exist_ok=True)
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(s, f)
    except OSError:
        pass
