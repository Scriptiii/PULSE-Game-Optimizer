"""Manual RAM cleanup using Windows memory-manager APIs.

The selected operations correspond to Working Set, System File Cache and Standby
List in WinMemoryCleaner 3.0.8. This is an independent Python implementation:
https://github.com/IgorMundstein/WinMemoryCleaner/tree/3.0.8
No external executable, process termination or persistent settings are involved.
Native memory-list commands are not a stable public Win32 API; failures are
reported per operation rather than treated as a successful cleanup.
"""
from __future__ import annotations

import ctypes
import sys
import time
from contextlib import contextmanager

import psutil


OPERATIONS = (("working_set", "Рабочие наборы процессов"),
              ("system_cache", "Системный файловый кеш"),
              ("standby", "Список Standby"))
PRIVILEGES = ("SeProfileSingleProcessPrivilege", "SeIncreaseQuotaPrivilege")


class Luid(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_int32)]


class LuidAttributes(ctypes.Structure):
    _fields_ = [("luid", Luid), ("attributes", ctypes.c_uint32)]


class TokenPrivileges(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("privileges", LuidAttributes * 1)]


class WindowsMemoryAPI:
    def __init__(self):
        if sys.platform != "win32":
            raise OSError("Очистка ОЗУ доступна только в Windows.")
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        security = ctypes.WinDLL("advapi32", use_last_error=True)
        native = ctypes.WinDLL("ntdll", use_last_error=True)

        def bind(dll, name, arguments, result):
            function = getattr(dll, name)
            function.argtypes, function.restype = arguments, result
            return function

        handle, word, boolean = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int32
        self.is_admin = bind(shell, "IsUserAnAdmin", [], boolean)
        self.current_process = bind(kernel, "GetCurrentProcess", [], handle)
        self.close = bind(kernel, "CloseHandle", [handle], boolean)
        self.open_token = bind(security, "OpenProcessToken", [handle, word, ctypes.POINTER(handle)], boolean)
        self.lookup = bind(security, "LookupPrivilegeValueW", [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.POINTER(Luid)], boolean)
        self.adjust = bind(security, "AdjustTokenPrivileges", [handle, boolean, ctypes.POINTER(TokenPrivileges), word,
                                                              ctypes.POINTER(TokenPrivileges), ctypes.POINTER(word)], boolean)
        self.set_information = bind(native, "NtSetSystemInformation", [ctypes.c_int32, handle, word], ctypes.c_int32)
        self.set_cache_size = bind(kernel, "SetSystemFileCacheSize", [ctypes.c_size_t, ctypes.c_size_t, word], boolean)

    @contextmanager
    def privileges(self):
        """Enable only the needed privileges and restore their exact old states."""
        if not self.is_admin():
            raise PermissionError("Для очистки ОЗУ запусти PULSE от имени администратора.")
        token = ctypes.c_void_p()
        if not self.open_token(self.current_process(), 0x20 | 0x08, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        previous = []
        try:
            for name in PRIVILEGES:
                state, old, length = TokenPrivileges(), TokenPrivileges(), ctypes.c_uint32()
                state.count = 1
                state.privileges[0].attributes = 2  # SE_PRIVILEGE_ENABLED
                if not self.lookup(None, name, ctypes.byref(state.privileges[0].luid)):
                    raise ctypes.WinError(ctypes.get_last_error())
                ctypes.set_last_error(0)
                success = self.adjust(token, False, ctypes.byref(state), ctypes.sizeof(old),
                                      ctypes.byref(old), ctypes.byref(length))
                error = ctypes.get_last_error()
                if old.count:
                    previous.append(old)
                # AdjustTokenPrivileges can return TRUE with ERROR_NOT_ALL_ASSIGNED.
                if not success or error:
                    raise PermissionError(f"Windows не разрешила очистку ОЗУ: {name} (код {error}).")
            yield
        finally:
            errors = []
            try:
                for old in reversed(previous):
                    ctypes.set_last_error(0)
                    if not self.adjust(token, False, ctypes.byref(old), 0, None, None) or ctypes.get_last_error():
                        errors.append(str(ctypes.get_last_error()))
            finally:
                self.close(token)
            if errors:
                raise OSError("Не удалось вернуть исходные права процесса: " + ", ".join(errors))

    def _memory_list(self, command):
        value = ctypes.c_int32(command)
        status = self.set_information(80, ctypes.byref(value), ctypes.sizeof(value))
        if status < 0:
            raise OSError(f"Windows отклонила операцию: NTSTATUS 0x{status & 0xffffffff:08X}")

    def working_set(self):
        self._memory_list(2)  # MemoryEmptyWorkingSets

    def system_cache(self):
        flush = ctypes.c_size_t(-1).value
        if not self.set_cache_size(flush, flush, 0):
            raise ctypes.WinError(ctypes.get_last_error())

    def standby(self):
        self._memory_list(4)  # MemoryPurgeStandbyList


class MemoryCleaner:
    def __init__(self, demo=False):
        self.demo = demo

    def clean(self):
        if self.demo:
            # Fixed, explicitly labelled examples; never bind native cleanup APIs.
            return {"demo": True, "before": 4 * 1024**3, "after": 6 * 1024**3, "seconds": 0,
                    "steps": [{"id": key, "title": title, "ok": True, "error": ""} for key, title in OPERATIONS]}
        start = time.monotonic()
        api = WindowsMemoryAPI()
        steps = []
        with api.privileges():
            before = psutil.virtual_memory().available
            for key, title in OPERATIONS:
                try:
                    getattr(api, key)()
                    steps.append({"id": key, "title": title, "ok": True, "error": ""})
                except OSError as exc:
                    steps.append({"id": key, "title": title, "ok": False, "error": str(exc)})
            after = psutil.virtual_memory().available
        return {"demo": False, "before": before, "after": after,
                "steps": steps, "seconds": round(time.monotonic() - start, 2)}
