"""Preview-first cleanup of old files inside explicitly allowlisted temp roots."""

from __future__ import annotations

import ctypes
import os
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

MIN_AGE_SECONDS = 48 * 3600
MAX_FILES = 100_000


def format_bytes(size: int | float) -> str:
    value = float(size)
    for suffix in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if value < 1024 or suffix == "ТБ":
            return f"{value:.1f} {suffix}" if suffix != "Б" else f"{int(value)} Б"
        value /= 1024
    return "0 Б"


def is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def safe_root(path: Path) -> bool:
    """Reject drive roots, symlinks, junctions and any reparse-point ancestor."""
    if not path.is_absolute() or path == Path(path.anchor):
        return False
    try:
        return path.is_dir() and all(not is_link(p.lstat()) for p in (path, *path.parents))
    except OSError:
        return False


@dataclass(frozen=True)
class Candidate:
    category: str
    path: Path
    root: Path
    size: int
    modified_ns: int
    device: int
    inode: int

    def matches(self, info: os.stat_result, cutoff: float) -> bool:
        return (stat.S_ISREG(info.st_mode) and not is_link(info) and info.st_nlink == 1
                and info.st_size == self.size and info.st_mtime_ns == self.modified_ns
                and info.st_dev == self.device and info.st_ino == self.inode and info.st_mtime <= cutoff)


@dataclass
class Scan:
    candidates: list[Candidate] = field(default_factory=list)
    skipped: int = 0
    truncated: bool = False
    created: float = field(default_factory=time.time)
    errors: list[str] = field(default_factory=list)

    @property
    def size(self):
        return sum(c.size for c in self.candidates)


def default_roots() -> dict[str, Path]:
    # Deliberately ignore TEMP/TMP: they can point to arbitrary user folders.
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return {"user": local / "Temp", "system": windows / "Temp"}


class Cleaner:
    def __init__(self, roots: dict[str, Path] | None = None, demo: bool = False):
        self.roots = {key: Path(value).absolute() for key, value in (roots if roots is not None else default_roots()).items()}
        self.demo = demo

    def scan(self, categories: list[str], seconds: float = 45) -> Scan:
        result = Scan()
        if self.demo:
            return result
        cutoff = time.time() - MIN_AGE_SECONDS
        deadline = time.monotonic() + seconds
        seen = set()
        for category in dict.fromkeys(categories):
            if category not in self.roots:
                raise ValueError("Неизвестная категория очистки")
            root = self.roots[category]
            if not safe_root(root):
                result.errors.append(f"Папка недоступна или является ссылкой: {root}")
                continue

            def walk_error(_):
                result.skipped += 1

            for parent, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
                if time.monotonic() > deadline:
                    result.truncated = True
                    return result
                safe_dirs = []
                for name in dirs:
                    try:
                        if not is_link((Path(parent) / name).lstat()):
                            safe_dirs.append(name)
                    except OSError:
                        result.skipped += 1
                dirs[:] = safe_dirs
                for name in files:
                    if time.monotonic() > deadline or len(result.candidates) >= MAX_FILES:
                        result.truncated = True
                        return result
                    path = Path(parent) / name
                    try:
                        info = path.lstat()
                        identity = (info.st_dev, info.st_ino)
                        if (is_link(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                                or info.st_mtime > cutoff or identity in seen):
                            result.skipped += 1
                            continue
                        # A parent may have changed during traversal. Resolve and check it again.
                        if not safe_root(path.parent) or not path.resolve().is_relative_to(root):
                            result.skipped += 1
                            continue
                        seen.add(identity)
                        result.candidates.append(Candidate(category, path, root, info.st_size,
                                                           info.st_mtime_ns, info.st_dev, info.st_ino))
                    except OSError:
                        result.skipped += 1
        return result

    def clean(self, scan: Scan) -> dict:
        if self.demo:
            return {"deleted": 0, "bytes": 0, "skipped": 0, "message": "Демо: файлы не удаляются."}
        if time.time() - scan.created > 15 * 60:
            raise RuntimeError("Результат сканирования устарел. Выполните новый анализ.")
        deleted = freed = skipped = 0
        cutoff = time.time() - MIN_AGE_SECONDS
        for candidate in scan.candidates:
            try:
                root = self.roots.get(candidate.category)
                if (root is None or root != candidate.root or not safe_root(root)
                        or not candidate.path.is_relative_to(root) or not safe_root(candidate.path.parent)
                        or not candidate.path.resolve().is_relative_to(root)):
                    skipped += 1
                    continue
                if sys.platform == "win32":
                    _delete_windows(candidate, cutoff)
                else:
                    if not candidate.matches(candidate.path.lstat(), cutoff):
                        raise OSError("Файл изменился после анализа")
                    candidate.path.unlink()
                deleted += 1
                freed += candidate.size
            except (OSError, ValueError):
                # Busy, changed, protected or moved files are never retried with force.
                skipped += 1
        return {"deleted": deleted, "bytes": freed, "skipped": skipped,
                "message": f"Удалено файлов: {deleted}. Объём: {format_bytes(freed)}. Пропущено: {skipped}."}


def _delete_windows(candidate: Candidate, cutoff: float):
    """Validate and delete the SAME handle; never recursively delete directories.

    OPEN_REPARSE_POINT prevents following a substituted file symlink. Checking the
    final handle path also catches junctions substituted into parent directories.
    """
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    final_path = kernel.GetFinalPathNameByHandleW
    final_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    final_path.restype = wintypes.DWORD
    dispose = kernel.SetFileInformationByHandle
    dispose.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    dispose.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(candidate.path), 0x00010080, 0x7, None, 3, 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = None
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        info = os.fstat(descriptor)
        if not candidate.matches(info, cutoff):
            raise OSError("Файл изменился после анализа")
        buffer = ctypes.create_unicode_buffer(32768)
        length = final_path(handle, buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise OSError("Не удалось проверить окончательный путь")
        raw = buffer.value
        if raw.startswith("\\\\?\\UNC\\"):
            raw = "\\\\" + raw[8:]
        elif raw.startswith("\\\\?\\"):
            raw = raw[4:]
        actual = Path(raw)
        if not actual.is_relative_to(candidate.root) or actual != candidate.path:
            raise OSError("Путь файла изменился")
        delete = ctypes.c_ubyte(1)  # FILE_DISPOSITION_INFO.DeleteFile is a BOOLEAN.
        if not dispose(handle, 4, ctypes.byref(delete), ctypes.sizeof(delete)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if descriptor is not None:
            os.close(descriptor)
        else:
            close(handle)
