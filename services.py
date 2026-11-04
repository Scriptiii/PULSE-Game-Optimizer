"""Read and change Win32 service startup configuration through the SCM API."""
from __future__ import annotations

import ctypes
import os
from contextlib import contextmanager
from pathlib import Path

import psutil


class ServiceConfig(ctypes.Structure):
    _fields_ = [("service_type", ctypes.c_uint32), ("start", ctypes.c_uint32),
                ("error_control", ctypes.c_uint32), ("binary", ctypes.c_wchar_p),
                ("group", ctypes.c_wchar_p), ("tag", ctypes.c_uint32),
                ("dependencies", ctypes.c_void_p), ("account", ctypes.c_wchar_p),
                ("display", ctypes.c_wchar_p)]


OPTIONAL_WINDOWS = frozenset(name.lower() for name in (
    "SysMain", "Spooler", "WSearch", "MapsBroker", "Fax", "WMPNetworkSvc", "TrkWks",
    "XblAuthManager", "XblGameSave", "XboxNetApiSvc", "XboxGipSvc",
))
CRITICAL = frozenset(name.lower() for name in (
    "RpcSs", "RpcEptMapper", "DcomLaunch", "PlugPlay", "Power", "EventLog", "SamSs",
    "LSM", "Winmgmt", "WinDefend", "WdNisSvc", "SecurityHealthService", "Sense", "wscsvc",
    "BFE", "MpsSvc", "Dhcp", "Dnscache", "NlaSvc", "nsi", "WlanSvc", "Netlogon",
    "LanmanWorkstation", "ProfSvc", "Schedule", "gpsvc", "UserManager", "Appinfo",
    "BrokerInfrastructure", "SystemEventsBroker", "StateRepository", "CryptSvc",
    "TrustedInstaller", "UsoSvc", "WaaSMedicSvc", "wuauserv", "BITS", "DoSvc",
))


def valid_service_name(name):
    return isinstance(name, str) and 0 < len(name) <= 256 and not any(c in name for c in ("\\", "/", "\0"))


def service_restriction(row, is_admin):
    name = row["name"].lower()
    if row["service_type"] & 0xB or row["start"] in (0, 1):
        return "Драйвер или компонент загрузки Windows"
    if name in CRITICAL or row.get("protected", 0):
        return "Защищённая системная служба"
    binary = os.path.expandvars(row.get("binary", "")).strip().strip('"').lower()
    windows = str(Path(os.environ.get("SystemRoot", r"C:\Windows"))).lower()
    windows_binary = (binary.startswith(windows + "\\") or binary.startswith("\\systemroot\\")
                      or "\\svchost.exe" in binary or "\\lsass.exe" in binary)
    if windows_binary and name not in OPTIONAL_WINDOWS:
        return "Системная служба Windows — изменение защищено"
    if not is_admin:
        return "Нужен запуск от имени администратора"
    return ""


class Services:
    def __init__(self):
        self.api = ctypes.WinDLL("advapi32", use_last_error=True)
        handle = ctypes.c_void_p
        text = ctypes.c_wchar_p
        uint = ctypes.c_uint32
        self.api.OpenSCManagerW.argtypes = [text, text, uint]
        self.api.OpenSCManagerW.restype = handle
        self.api.OpenServiceW.argtypes = [handle, text, uint]
        self.api.OpenServiceW.restype = handle
        self.api.CloseServiceHandle.argtypes = [handle]
        self.api.CloseServiceHandle.restype = ctypes.c_int
        self.api.QueryServiceConfigW.argtypes = [handle, ctypes.c_void_p, uint, ctypes.POINTER(uint)]
        self.api.QueryServiceConfigW.restype = ctypes.c_int
        self.api.QueryServiceConfig2W.argtypes = [handle, uint, ctypes.c_void_p, uint, ctypes.POINTER(uint)]
        self.api.QueryServiceConfig2W.restype = ctypes.c_int
        self.api.ChangeServiceConfigW.argtypes = [handle, uint, uint, uint, text, text,
                                                 ctypes.c_void_p, text, text, text, text]
        self.api.ChangeServiceConfigW.restype = ctypes.c_int
        self.api.ChangeServiceConfig2W.argtypes = [handle, uint, ctypes.c_void_p]
        self.api.ChangeServiceConfig2W.restype = ctypes.c_int

    @contextmanager
    def open(self, name, change=False):
        if not valid_service_name(name):
            raise ValueError("Некорректное имя службы")
        scm = self.api.OpenSCManagerW(None, None, 1)
        if not scm:
            raise ctypes.WinError(ctypes.get_last_error())
        service = None
        try:
            service = self.api.OpenServiceW(scm, name, 1 | (2 if change else 0))
            if not service:
                raise ctypes.WinError(ctypes.get_last_error())
            yield service
        finally:
            if service:
                self.api.CloseServiceHandle(service)
            self.api.CloseServiceHandle(scm)

    def _extra_uint(self, service, level, optional=False):
        value, needed = ctypes.c_uint32(), ctypes.c_uint32()
        if not self.api.QueryServiceConfig2W(service, level, ctypes.byref(value), 4, ctypes.byref(needed)):
            code = ctypes.get_last_error()
            if optional and code == 124:  # Unsupported configuration level.
                return 0
            raise ctypes.WinError(code)
        return value.value

    def read(self, name):
        with self.open(name) as service:
            needed = ctypes.c_uint32()
            self.api.QueryServiceConfigW(service, None, 0, ctypes.byref(needed))
            if not 0 < needed.value <= 1_000_000:
                raise ctypes.WinError(ctypes.get_last_error())
            buffer = ctypes.create_string_buffer(needed.value)
            if not self.api.QueryServiceConfigW(service, buffer, len(buffer), ctypes.byref(needed)):
                raise ctypes.WinError(ctypes.get_last_error())
            data = ctypes.cast(buffer, ctypes.POINTER(ServiceConfig)).contents
            dependencies = []
            address = data.dependencies
            end = ctypes.addressof(buffer) + len(buffer)
            while address and address < end - ctypes.sizeof(ctypes.c_wchar):
                word = ctypes.wstring_at(address)
                if not word:
                    break
                dependencies.append(word)
                address += (len(word) + 1) * ctypes.sizeof(ctypes.c_wchar)
            return {"name": name, "display": data.display or name, "binary": data.binary or "",
                    "service_type": data.service_type, "start": data.start,
                    "delayed": bool(self._extra_uint(service, 3)),
                    "protected": self._extra_uint(service, 12, optional=True),
                    "dependencies": dependencies}

    def collect(self):
        rows, errors = [], []
        for service in psutil.win_service_iter():
            try:
                row = self.read(service.name())
                row["running"] = service.status() == "running"
                rows.append(row)
            except (OSError, psutil.Error) as exc:
                errors.append(f"Служба {service.name()}: {exc}")
        return rows, errors

    def write(self, name, state):
        if type(state.get("start")) is not int or state["start"] not in (2, 3, 4) or type(state.get("delayed")) is not bool:
            raise ValueError("Некорректная конфигурация запуска службы")
        with self.open(name, change=True) as service:
            if not self.api.ChangeServiceConfigW(service, 0xFFFFFFFF, state["start"], 0xFFFFFFFF,
                                                None, None, None, None, None, None, None):
                raise ctypes.WinError(ctypes.get_last_error())
            if state["start"] == 2:
                delayed = ctypes.c_int(int(state["delayed"]))
                if not self.api.ChangeServiceConfig2W(service, 3, ctypes.byref(delayed)):
                    raise ctypes.WinError(ctypes.get_last_error())
