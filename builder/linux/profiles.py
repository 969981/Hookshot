"""The launcher's profiles: each one is a user of the game with saves of its own, save/<id> in the game's user folder.
The Windows launcher keeps them the same way: builder/HookshotBuilder/Profiles.cs.

The game plays the profile HOOKSHOT_PROFILE=<id> names (host/nn_applet.c); without it, the default profile, the one user
every build had before profiles, so its saves stay where they were. The list is profiles.txt in the user folder, one
"<id> <name>" a line. An id is 32 hex digits, the save folder's name (host/nn_fs.c).

A save from a console or an emulator is imported into a profile: the folder of the game's save data (account.sav,
option.sav, slot_00 ... slot_05) is copied over the profile's. The profile's saves before it are kept in
save-backups/<id>-<time> in the user folder. Removing a profile moves its saves there too: nothing is deleted.
"""
import os
import re
import shutil
import time

DEFAULT = "5a3c9e1f0b7d24680000000000000001"   # (the uid host/nn_applet.c always had)
PREFIX = DEFAULT[:16]
TITLE = "0400486024dec000"   # (the game's program id: an emulator keeps its saves in a folder of that name)
LIST = "profiles.txt"
NAME_MAX = 32
_ID = re.compile(r"[0-9a-f]{32}")
_SLOT = re.compile(r"slot_\d\d", re.I)


def user_dir(folder, environ=os.environ):
    """The folder the game keeps saves in when the launcher starts it, as host/hos_main.c's saves_dir chooses it:
    $XDG_DATA_HOME/hookshot or ~/.local/share/hookshot, unless a user folder the game could not move is still beside it."""
    beside = os.path.join(folder, "user")
    try:
        if os.listdir(beside):
            return beside
    except OSError:
        pass
    xdg, home = environ.get("XDG_DATA_HOME", ""), environ.get("HOME", "")
    if xdg.startswith("/"):
        return os.path.join(xdg, "hookshot")
    if home.startswith("/"):
        return os.path.join(home, ".local", "share", "hookshot")
    return beside


def shown_name(uid, name):
    """the name the profile list shows: the default profile says it is, whatever it is called, so that it is clear why it
    cannot be removed"""
    return "%s (default)" % name if uid == DEFAULT else name


def clean_name(name):
    return " ".join((name or "").split())[:NAME_MAX]


def load(user):
    """[(id, name)], the default profile first"""
    profiles = []
    try:
        with open(os.path.join(user, LIST), encoding="utf-8") as f:
            for line in f:
                uid, _, name = line.strip().partition(" ")
                uid = uid.lower()
                if _ID.fullmatch(uid) and uid != "0" * 32 and all(uid != p[0] for p in profiles):
                    profiles.append((uid, clean_name(name) or uid[-4:]))
    except OSError:
        pass
    if all(uid != DEFAULT for uid, _ in profiles):
        profiles.insert(0, (DEFAULT, "Player"))
    else:
        profiles.sort(key=lambda p: p[0] != DEFAULT)
    return profiles


def store(user, profiles):
    os.makedirs(user, exist_ok=True)
    path = os.path.join(user, LIST)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write("# Hookshot's profiles: the save folder (save/<id>) and the name the launcher shows\n")
        f.writelines("%s %s\n" % (uid, name) for uid, name in profiles)
    os.replace(path + ".tmp", path)


def add(user, name):
    """a new profile, with no saves yet: the game starts it at an empty file select. Returns its id."""
    name = clean_name(name)
    if not name:
        raise ValueError("Give the profile a name.")
    profiles = load(user)
    if any(n.casefold() == name.casefold() for _, n in profiles):
        raise ValueError('There is a profile called "%s" already.' % name)
    taken = set(p[0] for p in profiles)
    try:
        taken.update(n.lower() for n in os.listdir(os.path.join(user, "save")))   # (a removed profile's folder is not reused)
    except OSError:
        pass
    n = 2
    while "%s%016x" % (PREFIX, n) in taken:
        n += 1
    uid = "%s%016x" % (PREFIX, n)
    store(user, profiles + [(uid, name)])
    return uid


def rename(user, uid, name):
    name = clean_name(name)
    if not name:
        raise ValueError("Give the profile a name.")
    profiles = load(user)
    if any(n.casefold() == name.casefold() and u != uid for u, n in profiles):
        raise ValueError('There is a profile called "%s" already.' % name)
    store(user, [(u, name if u == uid else n) for u, n in profiles])


def folder_to_show(user, uid):
    """The folder "Open save folder" shows: the profile's saves, or the nearest folder above them that is there. None is
    made: an empty save folder would read to the game as a save that exists."""
    for path in (os.path.join(user, "save", uid), os.path.join(user, "save"), user):
        if os.path.isdir(path):
            return path
    return None


def _backup(user, uid):
    """moves save/<id> to save-backups/<id>-<time>; returns where, or None when there were no saves"""
    saves = os.path.join(user, "save", uid)
    try:
        if not os.listdir(saves):
            os.rmdir(saves)
            return None
    except OSError:
        return None
    root = os.path.join(user, "save-backups")
    os.makedirs(root, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    dest = os.path.join(root, "%s-%s" % (uid, stamp))
    k = 2
    while os.path.exists(dest):
        dest = os.path.join(root, "%s-%s-%d" % (uid, stamp, k))
        k += 1
    os.replace(saves, dest)
    return dest


def remove(user, uid):
    """takes the profile off the list; its saves go to save-backups. Returns where they went, or None."""
    if uid == DEFAULT:
        raise ValueError("The default profile cannot be removed: the game plays it when it is started without the launcher.")
    moved = _backup(user, uid)
    store(user, [p for p in load(user) if p[0] != uid])
    return moved


def is_save(path):
    """a folder of the game's save data: account.sav, option.sav or slot_NN in it"""
    try:
        names = os.listdir(path)
    except OSError:
        return False
    return any(n.lower() in ("account.sav", "option.sav") or (_SLOT.fullmatch(n) and os.path.isdir(os.path.join(path, n)))
               for n in names)


def find_saves(path, depth=6):
    """The game's save folders in what the player chose: the save folder itself, or an emulator's save folder
    (save/0000000000000000/<user>/0400486024DEC000), one for each user it has. The newest first."""
    if is_save(path):
        return [path]
    found = []
    top = path.rstrip(os.sep).count(os.sep)
    for here, dirs, _files in os.walk(path):
        if here.count(os.sep) - top >= depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(here, d))]
        for d in list(dirs):
            if d.lower() == TITLE and is_save(os.path.join(here, d)):
                found.append(os.path.join(here, d))
                dirs.remove(d)
    return sorted(found, key=lambda p: -last_saved(p))


def last_saved(path):
    newest = 0.0
    for here, _dirs, files in os.walk(path):
        for f in files:
            try:
                newest = max(newest, os.path.getmtime(os.path.join(here, f)))
            except OSError:
                pass
    return newest


def describe(path, chosen):
    """one line for a save folder: where it is under the folder the player chose, how many files, when last saved"""
    where = os.path.relpath(path, chosen)
    if where == ".":
        where = os.path.basename(path.rstrip(os.sep)) or path
    try:
        slots = sum(1 for n in os.listdir(path) if _SLOT.fullmatch(n) and os.listdir(os.path.join(path, n)))
    except OSError:
        slots = 0
    saved = last_saved(path)
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(saved)) if saved else "never"
    return "%s  (%d save slot%s, last saved %s)" % (where, slots, "" if slots == 1 else "s", when)


def import_save(src, user, uid):
    """Copies the save folder src over the profile's saves. The profile's saves before it go to save-backups: returns
    where, or None when it had none. A failed copy leaves the profile's saves as they were."""
    if not is_save(src):
        raise ValueError("%s has none of the game's save files (account.sav, option.sav, slot_00 ...)." % src)
    saves = os.path.join(user, "save", uid)
    real_src, real_saves = os.path.realpath(src), os.path.realpath(saves)
    if real_src == real_saves or real_src.startswith(real_saves + os.sep) or real_saves.startswith(real_src + os.sep):
        raise ValueError("These are the profile's own saves.")
    os.makedirs(os.path.dirname(saves), exist_ok=True)
    staging = saves + ".importing"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        shutil.copytree(src, staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    moved = _backup(user, uid)
    os.replace(staging, saves)
    return moved
