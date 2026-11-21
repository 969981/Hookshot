"""The build's own Python takes paths past 260 characters on Windows (#1115).

Windows' long-paths setting is off on most PCs, and then a plain path of MAX_PATH (260) characters or more can't be opened,
listed or written: FileNotFoundError, or False from os.path.exists and the like. A Builder unpacked in a deep folder (a
portable Launcher in Downloads) passes it in its own sources, its work folder (the objects are named after their sources,
up to ~90 characters) and the game folder. Python itself takes such a path in its extended-length form (\\\\?\\C:\\...), so
install() makes the standard file calls use that form for a path that needs it:

- open (builtins and io, and tarfile's copy of it), os.open, stat, lstat, listdir, scandir, mkdir, rmdir, remove, unlink,
  utime, chmod, truncate, access, rename, replace, link, symlink (a relative target as it is: it is the link's folder's); os.path.exists, lexists, isfile, isdir, islink, getsize,
  getmtime, getatime, getctime; shutil.copyfile, copy, copy2, move and copytree. What goes through these follows:
  os.walk, os.makedirs, shutil.rmtree, tempfile, pathlib, glob, zipfile.
- Only the path changes, and only one of 248 or more UTF-16 units once absolute (Windows counts a path in those: a
  character past U+FFFF, an emoji, is two; CreateDirectory's limit, 260 less 12): it goes as \\\\?\\ plus its absolute form (a share as \\\\?\\UNC\\...). Everything else about a call is as before (no
  argument, keywords by each call's own names, descriptors, os.supports_fd and the like), and so is every path that worked before. What comes back
  is in the caller's spelling: a scandir entry's path, os.walk's folders either way round, shutil's destinations.
- Paths already extended or a device's (\\\\?\\, \\\\.\\, //?/, //./), bytes and descriptors: as they are.
- Not possible: a current folder (os.chdir, subprocess's cwd=) past ~258 characters; Windows takes none in any form.
- Elsewhere (Linux, macOS) install() does nothing: their limit is far beyond these paths.

Each script the build runs calls install() as it loads, so the lift's spawned workers (which load the main script again)
have it too: make_game.py, lift.py, build.py and the steps make_game.py starts. nx2/version.py keeps
explicit long paths of its own, as other tools load it alone.

    import long_paths; long_paths.install()
"""
import builtins
import inspect
import io
import os
import shutil
import sys

LIMIT = 248   # (a directory's MAX_PATH: 260 less the 12 an 8.3 file name needs)
_SPECIAL = ("\\\\?\\", "\\\\.\\", "//?/", "//./")
_INSTALLED = []


def long_form(p):
    """The extended-length form of p when p needs it (Windows, a str path 248 UTF-16 units or more once absolute), else p
    as it is"""
    if os.name != "nt":
        return p
    try:
        s = os.fspath(p)
    except TypeError:   # (a file descriptor, None)
        return p
    if not isinstance(s, str) or s.startswith(_SPECIAL):
        return p
    a = os.path.abspath(s)
    if len(a) < LIMIT // 2 or _units(a) < LIMIT:   # (under LIMIT / 2 characters: under LIMIT units, whatever they are)
        return p
    return "\\\\?\\UNC\\" + a[2:] if a.startswith("\\\\") else "\\\\?\\" + a


def _units(s):
    """s's length as Windows counts it: in UTF-16 units (a lone surrogate, as Python keeps one from a file name, is one)"""
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


def _names(fn, defaults):
    """the names fn gives its first parameters (os.open's path, os.path.isdir's s or path by Python version, getsize's
    filename), for a path passed by keyword; defaults where fn tells none (os.utime)"""
    try:
        got = [k for k, v in inspect.signature(fn).parameters.items()
               if v.kind in (v.POSITIONAL_OR_KEYWORD, v.KEYWORD_ONLY)][:len(defaults)]
    except (TypeError, ValueError):
        return defaults
    return tuple(got) if len(got) == len(defaults) else defaults


def _plain(result, given, made):
    """what a call gave back for made (given's long form), in given's own spelling"""
    return given if made is not given and result == made else result


def _argument(args, kw, index, name):
    """(args, kw) with the path at args[index], or kw[name], in its long form; and (given, made) for _plain"""
    if len(args) > index:
        given = args[index]
        made = long_form(given)
        if made is not given:
            args = args[:index] + (made,) + args[index + 1:]
        return args, kw, given, made
    if name in kw:
        given = kw[name]
        made = long_form(given)
        if made is not given:
            kw = dict(kw, **{name: made})
        return args, kw, given, made
    return args, kw, None, None


def _wrap(fn, names):
    """fn with the paths at the positions named in names ((index, keyword), ...) in their long forms"""
    def call(*args, **kw):
        if kw.get("dir_fd") is None and kw.get("src_dir_fd") is None:   # (relative to a folder's descriptor: as it is)
            for index, name in names:
                args, kw = _argument(args, kw, index, name)[:2]
        return fn(*args, **kw)
    call.__wrapped__ = fn
    call.__name__ = getattr(fn, "__name__", "call")
    call.__qualname__ = getattr(fn, "__qualname__", call.__name__)
    call.__doc__ = getattr(fn, "__doc__", None)
    return call


class _Entry:
    """A scandir entry of a folder listed by its long form, with the path in the caller's spelling"""
    __slots__ = ("_entry", "path")

    def __init__(self, entry, path):
        self._entry, self.path = entry, path

    def __getattr__(self, name):
        return getattr(self._entry, name)

    def __fspath__(self):
        return self.path

    def __repr__(self):
        return "<DirEntry %r>" % self._entry.name


class _Listing:
    """os.scandir's iterator over a long form, its entries' paths joined to the folder as given"""

    def __init__(self, it, given):
        self._it, self._given = it, given

    def __iter__(self):
        return self

    def __next__(self):
        e = next(self._it)
        return _Entry(e, os.path.join(self._given, e.name))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self._it.close()


def _scandir(fn):
    def scandir(*args, **kw):
        if not args and "path" not in kw:   # (no argument: the current folder, as before)
            return fn()
        args, kw, given, made = _argument(args, kw, 0, "path")
        it = fn(*args, **kw)
        return it if made is given else _Listing(it, os.fspath(given))
    scandir.__wrapped__ = fn
    scandir.__doc__ = fn.__doc__
    return scandir


def _destination(fn):
    """shutil's copies and move: both paths long, and the destination they return in the caller's spelling"""
    def call(*args, **kw):
        args, kw = _argument(args, kw, 0, "src")[:2]
        args, kw, given, made = _argument(args, kw, 1, "dst")
        return _plain(fn(*args, **kw), given, made)
    call.__wrapped__ = fn
    call.__name__ = getattr(fn, "__name__", "call")
    call.__doc__ = getattr(fn, "__doc__", None)
    return call


def _absolute(s):
    """s names a drive's or a share's root: C:\\..., \\\\server\\share\\... (not C:x or \\x, the current drive's or folder's)"""
    drive, rest = os.path.splitdrive(s)
    return bool(drive) and rest[:1] in ("\\", "/")


def _symlink(fn, keys=("src", "dst")):
    """os.symlink: the link in its long form when it needs it, and its target too only when absolute. A relative target is the link's folder's, not the
    current one's, and is kept as written"""
    def symlink(*args, **kw):
        if kw.get("dir_fd") is None:
            target = args[0] if args else kw.get(keys[0])
            try:
                text = os.fspath(target)
            except TypeError:
                text = None
            if isinstance(text, str) and _absolute(text):
                args, kw = _argument(args, kw, 0, keys[0])[:2]
            args, kw = _argument(args, kw, 1, keys[1])[:2]
        return fn(*args, **kw)
    symlink.__wrapped__ = fn
    symlink.__doc__ = fn.__doc__
    return symlink


ONE = {os: ("stat", "lstat", "listdir", "mkdir", "rmdir", "remove", "unlink", "utime", "chmod", "truncate", "access",
            "open"),
       os.path: ("exists", "lexists", "isfile", "isdir", "islink", "getsize", "getmtime", "getatime", "getctime")}
TWO = {os: ("rename", "replace", "link")}
DESTINATION = ("copyfile", "copy", "copy2", "move")



def install():
    """On Windows: the calls above take paths past 260 characters, from now on and in this process. Once is enough; again
    does nothing. Elsewhere: nothing"""
    if os.name != "nt" or _INSTALLED:
        return
    replaced = {}

    def put(mod, name, new):
        old = getattr(mod, name)
        replaced[old] = new
        setattr(mod, name, new)

    put(builtins, "open", _wrap(builtins.open, ((0, "file"),)))
    io.open = builtins.open
    for mod, names in ONE.items():
        for name in names:
            if hasattr(mod, name):
                fn = getattr(mod, name)
                put(mod, name, _wrap(fn, ((0, _names(fn, ("path",))[0]),)))
    for name in TWO[os]:
        if hasattr(os, name):
            fn = getattr(os, name)
            src, dst = _names(fn, ("src", "dst"))
            put(os, name, _wrap(fn, ((0, src), (1, dst))))
    if hasattr(os, "symlink"):
        put(os, "symlink", _symlink(os.symlink, _names(os.symlink, ("src", "dst"))))
    put(os, "scandir", _scandir(os.scandir))
    for name in DESTINATION:
        put(shutil, name, _destination(getattr(shutil, name)))
    copytree = shutil.copytree

    def copytree_long(src, dst, *args, **kw):   # (its copy_function default is the copy2 it was defined with)
        if len(args) < 3:
            kw.setdefault("copy_function", shutil.copy2)
        return copytree(src, dst, *args, **kw)
    copytree_long.__wrapped__ = copytree
    copytree_long.__doc__ = copytree.__doc__
    put(shutil, "copytree", copytree_long)
    # (what os says each call supports: the replacements are in the same sets as the calls they replace)
    for group in ("supports_fd", "supports_dir_fd", "supports_follow_symlinks", "supports_effective_ids"):
        held = getattr(os, group, None)
        if isinstance(held, set):
            held.update(new for old, new in replaced.items() if old in held)
    # (the standard library's modules loaded before install() that kept a call as they loaded: tarfile's bltn_open, bz2's
    # _builtin_open; modules loaded after it take the replacement. Only the standard library's: code of ours keeps what it
    # holds)
    stdlib = os.path.normcase(os.path.dirname(os.__file__)) + os.sep
    for mod in list(sys.modules.values()):
        if mod is None or mod in (builtins, io, os, os.path, shutil):
            continue
        where = getattr(mod, "__file__", None)
        if not where or not os.path.normcase(os.path.abspath(where)).startswith(stdlib) or \
                os.sep + "site-packages" + os.sep in os.path.normcase(where):
            continue
        try:
            names = [(k, v) for k, v in vars(mod).items() if callable(v)]
        except TypeError:
            continue
        for k, v in names:
            try:
                if v in replaced:
                    setattr(mod, k, replaced[v])
            except TypeError:   # (an unhashable callable)
                continue
    _INSTALLED.append(True)
