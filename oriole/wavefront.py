"""Wavefront IV as a program (Windows): started from its install folder, in script mode, found among the processes.

An install is a copy of Wavefront's zip in a folder of its own (E:\\wfdIV_<date>): its ``wfdIV.exe`` is a launcher
that runs the program itself, ``wfdIVCore.dll`` (also the process name). Its licence expires now and then: the core
then fails to start, or is left empty, and Wavefront is installed again in a new folder (the fix is in "Wavefront IV
Errorcode 7536725.pdf" on the desktop).

Every install shares the settings in %APPDATA%\\wfdIV\\wfdIV.ini (a QSettings ini file), read when Wavefront starts
and written when it starts and exits. As tried on the rig, ``[scriptEngine] autoload`` there is the script mode
(Tools → Script engine → auto load (script folder)) Wavefront starts in, and ``[mainSettings] rootPath`` the folder it
saves the masks in: both are set before Oriole starts it. Closing Wavefront asks to confirm: Oriole never does.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LAUNCHER = "wfdIV.exe"
CORE = "wfdIVCore.dll"
WINDOW_TITLE = "Wavefront Designer IV"      # its main window, open once it is ready for scripts
INI_PATH = Path(os.environ.get("APPDATA", Path.home())) / "wfdIV" / "wfdIV.ini"
INSTALL_ZIP = Path.home() / "Documents" / "wfdIV.ifta2D-181220.00.zip"         # what an install is made from
LICENCE_PDF = Path.home() / "Desktop" / "Wavefront IV Errorcode 7536725.pdf"   # the lab's reinstall procedure
LICENCE_HELP = 'the licence expired? Its fix is in "Wavefront IV Errorcode 7536725.pdf" on the desktop'
FOLDERS = ("script", "GeneratedPhasemasks")     # made by hand in a new install: the zip has neither
SUPPORTED = sys.platform == "win32"


def install_problem(folder) -> str | None:
    """What keeps Wavefront from starting from ``folder``, if anything."""
    folder = Path(folder)
    if not (folder / LAUNCHER).is_file():
        return f"no {LAUNCHER} in {folder}"
    core = folder / CORE
    if not core.is_file() or core.stat().st_size == 0:
        return f"{CORE} is missing or empty in {folder}: {LICENCE_HELP}"
    missing = [name for name in FOLDERS if not (folder / name).is_dir()]
    if missing:
        return f"no {' or '.join(missing)} folder in {folder}: make {'it' if len(missing) == 1 else 'them'}"
    return None


def other_installs(folder) -> list[Path]:
    """The other Wavefront installs next to ``folder`` (wfdIV* folders) that can start."""
    folder = Path(folder)
    if not folder.parent.is_dir():
        return []
    return sorted(p for p in folder.parent.glob("wfdIV*")
                  if p.is_dir() and p.resolve() != folder.resolve() and install_problem(p) is None)


def start(folder):
    """Start Wavefront from its install folder, detached: it outlives Oriole."""
    folder = Path(folder)
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if SUPPORTED else 0
    subprocess.Popen([str(folder / LAUNCHER)], cwd=folder, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ----------------------------------------------------------------------------- settings (QSettings ini)
def _ini_string(value: str) -> str:
    """A string as QSettings writes it in an ini file (backslashes doubled; quoted when it holds , ; or =)."""
    text = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"' if any(c in value for c in ",;=") or value != value.strip() else text


def _ini_value(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1]
    return text.replace('\\"', '"').replace("\\\\", "\\")


def read_ini(section: str, key: str, path=None) -> str | None:
    try:
        lines = Path(path or INI_PATH).read_bytes().decode("latin-1").splitlines()
    except OSError:
        return None
    current = None
    for line in lines:
        if line.startswith("["):
            current = line.strip()[1:-1]
        elif current == section and line.split("=", 1)[0] == key:
            return _ini_value(line.split("=", 1)[1])
    return None


def write_ini(values: dict[tuple[str, str], str], path=None) -> list[str]:
    """Set ``{(section, key): value}`` in the ini file (made if missing), every other line left as it is; the
    ``section/key`` changed."""
    path = Path(path or INI_PATH)
    try:
        text = path.read_bytes().decode("latin-1")
    except FileNotFoundError:
        text = ""
    nl = "\n" if text and "\r\n" not in text else "\r\n"
    lines = text.split(nl) if text else []
    changed = []
    for (section, key), value in values.items():
        line = f"{key}={_ini_string(value)}"
        start = next((i for i, l in enumerate(lines) if l.strip() == f"[{section}]"), None)
        if start is None:                                       # a new section at the end
            while lines and lines[-1] == "":
                lines.pop()
            lines += ([""] if lines else []) + [f"[{section}]", line, ""]
            changed.append(f"{section}/{key}")
            continue
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
        k = next((i for i in range(start + 1, end) if lines[i].split("=", 1)[0] == key), None)
        if k is None:
            last = max((i for i in range(start + 1, end) if lines[i].strip()), default=start)
            lines.insert(last + 1, line)
        elif lines[k] == line:
            continue
        else:
            lines[k] = line
        changed.append(f"{section}/{key}")
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(nl.join(lines).encode("latin-1"))
    return changed


def prepare(folder, path=None) -> list[str]:
    """Before starting Wavefront from ``folder``: script mode on, masks saved in its GeneratedPhasemasks folder."""
    return write_ini({("scriptEngine", "autoload"): "true",
                      ("mainSettings", "rootPath"): str(Path(folder) / "GeneratedPhasemasks")}, path)


# ----------------------------------------------------------------------------- running processes
def running() -> dict[str, tuple[int, Path]]:
    """The running Wavefront processes, {"launcher" / "core": (pid, install folder)}."""
    if not SUPPORTED:
        return {}
    import ctypes
    from ctypes import wintypes as wt

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                    ("szExeFile", wt.WCHAR * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    k32.OpenProcess.restype = wt.HANDLE
    names = {LAUNCHER.lower(): "launcher", CORE.lower(): "core"}
    found = {}
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)                 # TH32CS_SNAPPROCESS
    try:
        entry = Entry(dwSize=ctypes.sizeof(Entry))
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            role = names.get(entry.szExeFile.lower())
            if role:
                found[role] = (entry.th32ProcessID, _process_folder(k32, entry.th32ProcessID))
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return found


def _process_folder(k32, pid: int) -> Path | None:
    import ctypes
    from ctypes import wintypes as wt
    handle = k32.OpenProcess(0x1000, False, pid)                # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        buf, size = ctypes.create_unicode_buffer(1024), wt.DWORD(1024)
        return Path(buf.value).parent if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else None
    finally:
        k32.CloseHandle(handle)


def window_open(pid: int) -> bool:
    """The process ``pid`` shows Wavefront's main window: started, ready for scripts."""
    if not SUPPORTED:
        return False
    import ctypes
    from ctypes import wintypes as wt
    user32 = ctypes.WinDLL("user32")
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def visit(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            if title.value.startswith(WINDOW_TITLE):
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(visit, 0)
    return bool(found)
