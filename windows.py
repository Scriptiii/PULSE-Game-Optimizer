"""Allowlisted Windows I/O. Inspection never modifies preferences."""
from __future__ import annotations

import copy
import ctypes
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

from .rules import POWER, REGISTRY, SPI

HIGH_PERFORMANCE = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
GUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


class UnsupportedError(RuntimeError):
    pass


def system_executable(name):
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / name)


def run_system(args, timeout=15):
    result = subprocess.run(args, capture_output=True, timeout=timeout,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    def decode(data):
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("oem" if sys.platform == "win32" else "utf-8", errors="replace")
    if result.returncode:
        raise OSError(f"Команда завершилась с кодом {result.returncode}: {decode(result.stderr or result.stdout).strip()[:700]}")
    return decode(result.stdout)


class NativeGuid(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)

    def __str__(self):
        return str(uuid.UUID(bytes_le=bytes(self)))


class PowerAccess:
    """Documented PowrProf API with explicit scheme identity for recovery."""
    def __init__(self):
        self.dll = ctypes.WinDLL("powrprof", use_last_error=True)
        pointer = ctypes.POINTER(NativeGuid)
        self.dll.PowerGetActiveScheme.argtypes = [ctypes.c_void_p, ctypes.POINTER(pointer)]
        self.dll.PowerGetActiveScheme.restype = ctypes.c_uint32
        self.dll.PowerReadACValueIndex.argtypes = [ctypes.c_void_p, pointer, pointer, pointer, ctypes.POINTER(ctypes.c_uint32)]
        self.dll.PowerReadACValueIndex.restype = ctypes.c_uint32
        self.dll.PowerWriteACValueIndex.argtypes = [ctypes.c_void_p, pointer, pointer, pointer, ctypes.c_uint32]
        self.dll.PowerWriteACValueIndex.restype = ctypes.c_uint32
        self.dll.PowerSetActiveScheme.argtypes = [ctypes.c_void_p, pointer]
        self.dll.PowerSetActiveScheme.restype = ctypes.c_uint32
        self.free = ctypes.WinDLL("kernel32").LocalFree
        self.free.argtypes = [ctypes.c_void_p]
        self.free.restype = ctypes.c_void_p

    @staticmethod
    def check(code):
        if code:
            raise ctypes.WinError(code)

    def active(self):
        pointer = ctypes.POINTER(NativeGuid)()
        self.check(self.dll.PowerGetActiveScheme(None, ctypes.byref(pointer)))
        try:
            return str(pointer.contents)
        finally:
            self.free(pointer)

    def read(self, key, scheme):
        rule = POWER[key]
        guid, subgroup, setting = (NativeGuid.parse(v) for v in (scheme, rule.subgroup, rule.setting))
        value = ctypes.c_uint32()
        self.check(self.dll.PowerReadACValueIndex(None, ctypes.byref(guid), ctypes.byref(subgroup), ctypes.byref(setting), ctypes.byref(value)))
        return {"scheme": scheme, "ac": value.value}

    def write(self, key, value):
        rule = POWER[key]
        guid, subgroup, setting = (NativeGuid.parse(v) for v in (value["scheme"], rule.subgroup, rule.setting))
        self.check(self.dll.PowerWriteACValueIndex(None, ctypes.byref(guid), ctypes.byref(subgroup), ctypes.byref(setting), value["ac"]))
        # Restoring old values must never switch the user's current power plan.
        if self.active().lower() == value["scheme"].lower():
            self.check(self.dll.PowerSetActiveScheme(None, ctypes.byref(guid)))


class AnimationInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("enabled", ctypes.c_int)]


class HotkeyInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("flags", ctypes.c_uint32)]


class FilterInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                ("wait", ctypes.c_uint32), ("delay", ctypes.c_uint32),
                ("repeat", ctypes.c_uint32), ("bounce", ctypes.c_uint32)]


def spi_structure(kind):
    return AnimationInfo if kind == "animation" else FilterInfo if kind == "filter" else HotkeyInfo


class WindowsBackend:
    demo = False

    def __init__(self):
        if sys.platform != "win32" or sys.getwindowsversion().build < 17763:
            raise UnsupportedError("Нужна Windows 10 1809 или новее. Для просмотра используйте --demo.")
        import winreg
        self.reg = winreg
        self.build = sys.getwindowsversion().build
        self.edition = ""
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
                self.edition = winreg.QueryValueEx(key, "EditionID")[0].lower()
        except OSError:
            pass
        shell32 = ctypes.WinDLL("shell32")
        shell32.IsUserAnAdmin.argtypes = []
        shell32.IsUserAnAdmin.restype = ctypes.c_int
        self.is_admin = bool(shell32.IsUserAnAdmin())
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.spi = self.user32.SystemParametersInfoW
        self.spi.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        self.spi.restype = ctypes.c_int
        self.powercfg = system_executable("powercfg.exe")
        self.power_access = PowerAccess()

    def _spi(self, action, value, persist=False, ui_param=0):
        if not self.spi(action, ui_param, value, 3 if persist else 0):
            raise ctypes.WinError(ctypes.get_last_error())

    def _hive(self, rule):
        return self.reg.HKEY_LOCAL_MACHINE if rule.scope == "HKLM" else self.reg.HKEY_CURRENT_USER

    def read(self, tweak_id):
        if tweak_id in REGISTRY:
            rule = REGISTRY[tweak_id]
            try:
                with self.reg.OpenKey(self._hive(rule), rule.path, 0, self.reg.KEY_READ) as key:
                    value, kind = self.reg.QueryValueEx(key, rule.name)
                if kind != self.reg.REG_DWORD or type(value) is not int:
                    raise UnsupportedError("Неожиданный тип параметра Windows. Изменение заблокировано.")
                return {"exists": True, "value": value, "type": kind}
            except FileNotFoundError:
                return {"exists": False}
        if tweak_id == "power":
            return {"guid": self.power_access.active()}
        if tweak_id in POWER:
            return self.read_power_value(tweak_id, self.power_access.active())
        if tweak_id == "mouse":
            data = (ctypes.c_int * 3)()
            self._spi(0x0003, ctypes.byref(data))
            return {"values": list(data)}
        if tweak_id in SPI:
            rule = SPI[tweak_id]
            if rule.kind in ("animation", "sticky", "toggle", "filter"):
                data = spi_structure(rule.kind)()
                data.cbSize = ctypes.sizeof(data)
                self._spi(rule.get, ctypes.byref(data), ui_param=data.cbSize)
                return {name: bool(getattr(data, name)) if name == "enabled" else getattr(data, name)
                        for name, _ in data._fields_ if name != "cbSize"}
            data = ctypes.c_uint32()
            self._spi(rule.get, ctypes.byref(data))
            return {"enabled": bool(data.value)} if rule.kind.startswith("bool") else {"value": data.value}
        raise ValueError("Неизвестная настройка")

    def read_power_value(self, key, scheme):
        return self.power_access.read(key, scheme)

    def matches(self, key, expected):
        actual = self.read_power_value(key, expected["scheme"]) if key in POWER else self.read(key)
        return actual == expected

    def target(self, tweak_id, before):
        if tweak_id in REGISTRY:
            return {"exists": True, "value": REGISTRY[tweak_id].target, "type": 4}
        if tweak_id == "power":
            return {"guid": HIGH_PERFORMANCE}
        if tweak_id in POWER:
            return {"scheme": before["scheme"], "ac": POWER[tweak_id].target}
        if tweak_id == "mouse":
            return {"values": [before["values"][0], before["values"][1], 0]}
        if tweak_id in SPI:
            rule = SPI[tweak_id]
            if rule.kind in ("sticky", "filter", "toggle"):
                # Disable shortcut and confirmation only, not the accessibility feature.
                return {**before, "flags": before["flags"] & ~0xC}
            if rule.kind.startswith("bool") or rule.kind == "animation":
                return {"enabled": bool(rule.target)}
            return {"value": rule.target}
        raise ValueError("Неизвестная настройка")

    def availability(self, tweak_id):
        try:
            if tweak_id in REGISTRY:
                rule = REGISTRY[tweak_id]
                if not rule.min_build <= self.build <= rule.max_build:
                    return False, "Этот параметр предназначен для другой версии Windows."
                pro = self.edition.startswith(("professional", "enterprise", "education", "iotenterprise"))
                enterprise = self.edition.startswith(("enterprise", "education", "iotenterprise"))
                if rule.edition == "pro" and not pro:
                    return False, "Политика поддерживается в Windows Pro, Enterprise и Education."
                if rule.edition == "enterprise" and not enterprise:
                    return False, "Политика поддерживается только в Enterprise / Education / IoT Enterprise."
                if rule.scope == "HKLM" and not self.is_admin:
                    return False, "Для изменения нужны права администратора. Запустите PULSE от имени администратора."
                if rule.app == "edge" and not self._edge_installed():
                    return False, "Microsoft Edge не найден на этом компьютере."
            if tweak_id == "power":
                plans = {m.lower() for m in GUID.findall(run_system([self.powercfg, "/list"]))}
                if HIGH_PERFORMANCE not in plans:
                    return False, "Этот план недоступен на устройстве. Используйте «Режим питания» в Windows."
            validate_state(tweak_id, self.read(tweak_id))
            return True, ""
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            return False, str(exc)

    @staticmethod
    def _edge_installed():
        return any((Path(os.environ[base]) / "Microsoft/Edge/Application/msedge.exe").is_file()
                   for base in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA") if os.environ.get(base))

    def write(self, tweak_id, value):
        validate_state(tweak_id, value)
        if tweak_id in REGISTRY:
            rule = REGISTRY[tweak_id]
            if value["exists"]:
                with self.reg.CreateKeyEx(self._hive(rule), rule.path, 0, self.reg.KEY_SET_VALUE) as key:
                    self.reg.SetValueEx(key, rule.name, 0, self.reg.REG_DWORD, value["value"])
            else:
                try:
                    with self.reg.OpenKey(self._hive(rule), rule.path, 0, self.reg.KEY_SET_VALUE) as key:
                        self.reg.DeleteValue(key, rule.name)
                except FileNotFoundError:
                    pass
        elif tweak_id == "power":
            run_system([self.powercfg, "/setactive", value["guid"]])
        elif tweak_id in POWER:
            self.power_access.write(tweak_id, value)
        elif tweak_id == "mouse":
            data = (ctypes.c_int * 3)(*value["values"])
            self._spi(0x0004, ctypes.byref(data), True)
        elif tweak_id in SPI:
            rule = SPI[tweak_id]
            if rule.kind in ("animation", "sticky", "toggle", "filter"):
                data = spi_structure(rule.kind)()
                data.cbSize = ctypes.sizeof(data)
                for name, field in value.items():
                    setattr(data, name, int(field))
                self._spi(rule.set, ctypes.byref(data), True, data.cbSize)
            elif rule.kind == "bool_pv":
                self._spi(rule.set, ctypes.c_void_p(int(value["enabled"])), True)
            else:
                number = int(value["enabled"]) if rule.kind == "bool_ui" else value["value"]
                self._spi(rule.set, None, True, number)


def validate_state(tweak_id, value):
    if not isinstance(value, dict):
        raise ValueError("Повреждён формат резервной копии")
    def uint(number):
        return type(number) is int and 0 <= number <= 0xFFFFFFFF
    if tweak_id in REGISTRY:
        if type(value.get("exists")) is not bool:
            raise ValueError("Повреждено поле exists")
        if value["exists"] and (value.get("type") != 4 or not uint(value.get("value"))):
            raise ValueError("Ожидалось значение DWORD")
    elif tweak_id == "power" or tweak_id in POWER:
        key = "guid" if tweak_id == "power" else "scheme"
        if not isinstance(value.get(key), str) or not GUID.fullmatch(value[key]):
            raise ValueError("Некорректный GUID плана")
        if tweak_id in POWER and not uint(value.get("ac")):
            raise ValueError("Некорректное значение питания от сети")
    elif tweak_id == "mouse":
        values = value.get("values")
        if not isinstance(values, list) or len(values) != 3 or any(type(v) is not int for v in values):
            raise ValueError("Некорректные параметры мыши")
        if any(not 0 <= v <= 2**31 - 1 for v in values) or values[2] not in (0, 1, 2):
            raise ValueError("Некорректные параметры ускорения")
    elif tweak_id in SPI:
        rule = SPI[tweak_id]
        if rule.kind.startswith("bool") or rule.kind == "animation":
            if set(value) != {"enabled"} or type(value.get("enabled")) is not bool:
                raise ValueError("Некорректный булев параметр")
        elif rule.kind == "int_ui":
            if set(value) != {"value"} or not uint(value.get("value")) or not rule.minimum <= value["value"] <= rule.maximum:
                raise ValueError("Некорректное числовое значение настройки")
        else:
            fields = {"flags", "wait", "delay", "repeat", "bounce"} if rule.kind == "filter" else {"flags"}
            if set(value) != fields or not all(uint(v) for v in value.values()):
                raise ValueError("Некорректные параметры специальной клавиатуры")
    else:
        raise ValueError("Неизвестная настройка в резервной копии")


class DemoBackend(WindowsBackend):
    demo = True

    def __init__(self):
        self.values = {key: {"exists": True, "value": 1 if rule.target == 0 else 0, "type": 4}
                       for key, rule in REGISTRY.items()}
        self.values["game_mode"] = {"exists": True, "value": 1, "type": 4}
        self.values.update(power={"guid": BALANCED}, mouse={"values": [6, 10, 1]})
        for key, rule in SPI.items():
            if rule.kind.startswith("bool") or rule.kind == "animation":
                self.values[key] = {"enabled": True}
            elif rule.kind == "int_ui":
                self.values[key] = {"value": rule.target + 1 if rule.target < rule.maximum else 0}
            else:
                self.values[key] = {"flags": 0x7E}
                if rule.kind == "filter":
                    self.values[key].update(wait=1000, delay=1000, repeat=500, bounce=0)
        defaults = dict(cpu_max=80, cpu_epp=50, cpu_boost=1, pcie_aspm=2,
                        disk_timeout=1200, wifi_power=2, sleep_timeout=1800, display_timeout=600)
        self.power_values = {scheme: defaults.copy()
                             for scheme in (BALANCED, HIGH_PERFORMANCE)}

    def read(self, tweak_id):
        if tweak_id in POWER:
            return self.read_power_value(tweak_id, self.values["power"]["guid"])
        return copy.deepcopy(self.values[tweak_id])

    def read_power_value(self, key, scheme):
        return {"scheme": scheme, "ac": self.power_values[scheme][key]}

    def availability(self, tweak_id):
        return True, ""

    def write(self, tweak_id, value):
        validate_state(tweak_id, value)
        if tweak_id in POWER:
            self.power_values[value["scheme"]][tweak_id] = value["ac"]
        else:
            self.values[tweak_id] = copy.deepcopy(value)
