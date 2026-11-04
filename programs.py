"""Read registered desktop applications without querying Win32_Product.

Both registry views and loaded user hives are read. Launching an uninstaller
uses its exact executable/arguments, never cmd.exe, PowerShell or shell=True.
Portable software without an uninstall registration cannot be inventoried here.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import ntpath
import os
import re
import uuid

from .windows import system_executable
from .uninstall_check import installation_scope

UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
FIELDS = ("DisplayName", "DisplayVersion", "Publisher", "UninstallString", "InstallLocation",
          "SystemComponent", "WindowsInstaller", "ParentKeyName", "ReleaseType", "NoRemove", "EstimatedSize")
RUNTIMES = re.compile(r"visual c\+\+|\.net(?:\s|$)|windows (?:desktop runtime|app runtime|sdk|driver)|"
                      r"webview2|directx|defender|security update|servicing stack|"
                      r"\bdrivers?\b|драйвер|^(?:nvidia|amd software|intel\(r\)|realtek)", re.I)
WRAPPERS = {"cmd.exe", "powershell.exe", "pwsh.exe", "wscript.exe", "cscript.exe", "rundll32.exe",
            "regsvr32.exe", "mshta.exe", "wmic.exe", "reg.exe"}


def current_identity():
    """Read elevation and the process user's SID without starting PowerShell."""
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.IsUserAnAdmin.argtypes = []
    shell.IsUserAnAdmin.restype = ctypes.c_int
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    security.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                           ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    security.GetTokenInformation.restype = ctypes.c_int
    security.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    security.ConvertSidToStringSidW.restype = ctypes.c_int
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # GetCurrentProcessToken's documented pseudo-handle, supported since Win 8.
    token = ctypes.c_void_p(-4)
    size = ctypes.c_uint32()
    security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
    if not 0 < size.value <= 1_048_576:
        raise OSError("Не удалось прочитать идентификатор текущего пользователя")
    buffer = ctypes.create_string_buffer(size.value)
    if not security.GetTokenInformation(token, 1, buffer, size.value, ctypes.byref(size)):
        raise OSError("Windows не вернула сведения о текущем пользователе")
    sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents.value
    string_sid = ctypes.c_void_p()
    if not security.ConvertSidToStringSidW(sid, ctypes.byref(string_sid)):
        raise OSError("Не удалось преобразовать SID пользователя")
    try:
        return bool(shell.IsUserAnAdmin()), ctypes.wstring_at(string_sid.value)
    finally:
        kernel.LocalFree(string_sid)


def product_code(value):
    if not re.fullmatch(r"\{[0-9a-fA-F-]{36}\}", value):
        raise ValueError("Некорректный код MSI-продукта")
    return "{" + str(uuid.UUID(value[1:-1])).upper() + "}"


def uninstall_command(entry, exists=os.path.isfile):
    """Return executable + unchanged parameters, or a useful read-only reason."""
    target = entry["target"]
    if entry.get("msi"):
        return system_executable("msiexec.exe"), "/x " + product_code(target["key"]) + " /norestart"
    command = os.path.expandvars(entry.get("command", "").strip())
    if not command or "\x00" in command or "\n" in command or "\r" in command:
        raise ValueError("Не зарегистрирован корректный мастер удаления")
    if command.startswith('"'):
        match = re.fullmatch(r'"([^"\r\n]+)"\s*(.*)', command)
    else:
        match = re.fullmatch(r"(.+?\.exe)(?:\s+(.*))?", command, re.I)
    if not match:
        raise ValueError("Команда удаления нестандартная; используй «Установленные приложения» Windows")
    executable, arguments = match.group(1), match.group(2) or ""
    basename = ntpath.basename(executable).casefold()
    if basename == "msiexec.exe":
        match = re.fullmatch(r"/[ix]\s*(\{[0-9a-fA-F-]{36}\})(?:\s+/norestart)?", arguments.strip(), re.I)
        if not match:
            raise ValueError("Нестандартная команда MSI; используй мастер Windows")
        return system_executable("msiexec.exe"), "/x " + product_code(match.group(1)) + " /norestart"
    if (basename in WRAPPERS or not ntpath.isabs(executable) or executable.startswith("\\\\")
            or not executable.lower().endswith(".exe") or not exists(executable)):
        raise ValueError("Мастер удаления недоступен или использует системный интерпретатор команд")
    return executable, arguments


def make_entry(values, target, admin):
    title = str(values.get("DisplayName", "")).strip()
    if not title:
        return None
    identity = json.dumps(target, sort_keys=True).encode("utf-8")
    estimated = values.get("EstimatedSize")
    size_bytes = estimated * 1024 if type(estimated) is int and 0 < estimated <= 0xffffffff else None
    row = {"id": "desktop:" + hashlib.sha256(identity).hexdigest()[:24], "title": title,
           "name": title, "version": str(values.get("DisplayVersion", "")),
           "publisher": str(values.get("Publisher", "")), "target": target,
           "command": str(values.get("UninstallString", "")), "msi": values.get("WindowsInstaller") == 1,
           "location": str(values.get("InstallLocation", "")), "kind": "desktop", "size_bytes": size_bytes}
    reason = ""
    if values.get("NoRemove") == 1 or values.get("SystemComponent") == 1 or values.get("ParentKeyName") or values.get("ReleaseType"):
        reason = "Служебный компонент или обновление Windows: показан только для просмотра"
    elif RUNTIMES.search(title):
        reason = "Общая среда выполнения, драйвер или защита: удаление через PULSE заблокировано"
    elif target["hive"] == "HKU":
        reason = "Программа другой учётной записи: для удаления войди в неё"
    elif target["hive"] == "HKLM" and not admin:
        reason = "Для удаления системной установки запусти run.bat от имени администратора"
    else:
        try:
            uninstall_command(row)
        except ValueError as exc:
            reason = str(exc)
    row.update(reason=reason, available=not reason)
    return row


def deduplicate(rows):
    """Merge mirrored 32/64-bit registry entries, not merely equal app names."""
    result = []
    seen = {}
    for row in rows:
        target = row["target"]
        fingerprint = (target["hive"], target.get("sid", ""), target["key"].casefold(),
                       row["title"].casefold(), row["version"], row["publisher"].casefold(),
                       row["command"].casefold(), row["location"].casefold())
        if fingerprint not in seen:
            seen[fingerprint] = len(result)
            result.append(row)
        elif result[seen[fingerprint]].get("size_bytes") is None and row.get("size_bytes") is not None:
            # A mirrored registration can supply an estimate missing in the preferred view.
            index = seen[fingerprint]
            result[index] = {**result[index], "size_bytes": row["size_bytes"]}
    return sorted(result, key=lambda r: (r["title"].casefold(), r["version"], r["id"]))


def sort_program_entries(entries, order="name"):
    """Sort cached metadata numerically; unknown sizes stay last in both directions."""
    def key(row):
        name = (row["title"].casefold(), row.get("version", "").casefold(), row["id"])
        if order not in ("size_desc", "size_asc"):
            return name
        size = row.get("size_bytes")
        known = type(size) is int and size >= 0
        numeric = size if known else 0
        return (not known, -numeric if order == "size_desc" else numeric, name)
    return sorted(entries, key=key)


def inspect_programs(admin, current_sid=None, registry=None):
    if registry is None:
        import winreg as registry
    reg = registry
    roots = [("HKLM", reg.HKEY_LOCAL_MACHINE, ""), ("HKCU", reg.HKEY_CURRENT_USER, "")]
    errors, rows = [], []
    try:
        with reg.OpenKey(reg.HKEY_USERS, "", 0, reg.KEY_READ) as users:
            for index in range(reg.QueryInfoKey(users)[0]):
                sid = reg.EnumKey(users, index)
                if sid != current_sid and re.fullmatch(r"S-1-(?:5-21|12-1)(?:-\d+)+", sid):
                    roots.append(("HKU", reg.HKEY_USERS, sid))
    except OSError as exc:
        errors.append("Профили пользователей: " + str(exc))
    for hive_name, hive, sid in roots:
        path = (sid + "\\" if sid else "") + UNINSTALL
        for view in (64, 32):
            access = reg.KEY_READ | (reg.KEY_WOW64_64KEY if view == 64 else reg.KEY_WOW64_32KEY)
            try:
                with reg.OpenKey(hive, path, 0, access) as root:
                    for index in range(reg.QueryInfoKey(root)[0]):
                        key_name = reg.EnumKey(root, index)
                        try:
                            with reg.OpenKey(root, key_name) as key:
                                values = {}
                                for name in FIELDS:
                                    try:
                                        value, _ = reg.QueryValueEx(key, name)
                                        if isinstance(value, (str, int)):
                                            values[name] = value
                                    except FileNotFoundError:
                                        pass
                            target = {"hive": hive_name, "sid": sid, "view": view, "key": key_name}
                            row = make_entry(values, target, admin)
                            if row:
                                rows.append(row)
                        except OSError as exc:
                            errors.append(f"{hive_name} {view} · {key_name}: {exc}")
            except FileNotFoundError:
                pass
            except OSError as exc:
                errors.append(f"{hive_name} {view}: {exc}")
    return {"rows": deduplicate(rows), "errors": errors}


class ShellExecuteInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("fMask", ctypes.c_uint32), ("hwnd", ctypes.c_void_p),
                ("lpVerb", ctypes.c_wchar_p), ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p),
                ("lpDirectory", ctypes.c_wchar_p), ("nShow", ctypes.c_int), ("hInstApp", ctypes.c_void_p),
                ("lpIDList", ctypes.c_void_p), ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
                ("dwHotKey", ctypes.c_uint32), ("hIcon", ctypes.c_void_p), ("hProcess", ctypes.c_void_p)]


class UninstallProcess:
    """Own a process handle, polling without terminating the uninstaller."""
    def __init__(self, handle):
        self.handle = handle
        self.completed = None
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.kernel.WaitForSingleObject.restype = ctypes.c_uint32
        self.kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        self.kernel.GetExitCodeProcess.restype = ctypes.c_int
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        self.kernel.CloseHandle.restype = ctypes.c_int

    def poll(self):
        if self.completed is not None:
            return dict(self.completed)
        if not self.handle:
            return {"running": False, "exit_code": None, "error": "Windows не вернула дескриптор мастера; завершение процесса не подтверждено."}
        wait = self.kernel.WaitForSingleObject(self.handle, 0)
        if wait == 0x102:
            return {"running": True}
        if wait != 0:
            return {"running": False, "error": "Windows не смогла проверить процесс удаления."}
        code = ctypes.c_uint32()
        if not self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            return {"running": False, "error": "Не удалось получить код завершения мастера удаления."}
        self.completed = {"running": False, "exit_code": code.value}
        self.close()
        return dict(self.completed)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __del__(self):
        self.close()


def start_uninstaller(executable, arguments):
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = ctypes.c_int
    ole = ctypes.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole.CoInitializeEx.restype = ctypes.c_int32
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    initialized = ole.CoInitializeEx(None, 2) in (0, 1)
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40 | 0x100  # NOCLOSEPROCESS | NOASYNC
    info.lpVerb, info.lpFile, info.lpParameters = "open", executable, arguments
    info.lpDirectory, info.nShow = ntpath.dirname(executable), 1
    try:
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            raise OSError(f"Windows не запустила мастер удаления (код {ctypes.get_last_error()}).")
        return UninstallProcess(info.hProcess)
    finally:
        if initialized:
            ole.CoUninitialize()


def launch_uninstaller(row, admin, current_sid=None):
    fresh = inspect_programs(admin, current_sid)
    current = next((r for r in fresh["rows"] if r["id"] == row["id"]), None)
    if not current or not current["available"]:
        raise ValueError("Программа больше не доступна для удаления. Обнови список.")
    if any(current[k] != row[k] for k in ("command", "msi", "title", "version", "location")):
        raise ValueError("Команда удаления изменилась. Обнови список и проверь выбор.")
    if fresh["errors"]:
        raise ValueError("Список установленных программ прочитан не полностью. Обнови список перед удалением.")
    executable, arguments = uninstall_command(current)
    scope = installation_scope(current, fresh["rows"])
    process = start_uninstaller(executable, arguments)
    return {"message": "Открыт штатный мастер удаления. После завершения PULSE проверит запись установки и папку программы со вложенными файлами и каталогами.",
            "process": process, "scope": scope}
