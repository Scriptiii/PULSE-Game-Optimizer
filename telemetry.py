"""Local, read-only system telemetry. No network requests or background service."""

from __future__ import annotations

import ctypes
import os
import platform
import sys
import time
from pathlib import Path

import psutil

from .cleanup import format_bytes


class Monitor:
    def __init__(self):
        psutil.cpu_percent(None)
        self.disk = Path(os.environ.get("SystemRoot", str(Path.home()))).anchor or "/"

    def sample(self) -> dict:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(self.disk)
        battery = psutil.sensors_battery()
        return {"cpu": psutil.cpu_percent(None), "memory": memory.percent,
                "memory_used": memory.used, "memory_total": memory.total,
                "disk": disk.percent, "disk_free": disk.free, "disk_total": disk.total,
                "uptime": max(0, time.time() - psutil.boot_time()),
                "battery": battery is not None and not battery.power_plugged}


def machine_info() -> dict:
    # On Windows platform.processor() can invoke WMI before we replace its result
    # with the registry value below. Avoid that redundant startup query.
    processor = (os.environ.get("PROCESSOR_IDENTIFIER", "") if sys.platform == "win32"
                 else platform.processor()) or "Процессор"
    gpu = "Нет данных"
    build = ""
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                processor = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError:
            pass
        try:
            from ctypes import wintypes

            class DisplayDevice(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("name", wintypes.WCHAR * 32),
                            ("description", wintypes.WCHAR * 128), ("flags", wintypes.DWORD),
                            ("device_id", wintypes.WCHAR * 128), ("key", wintypes.WCHAR * 128)]

            enum = ctypes.WinDLL("user32", use_last_error=True).EnumDisplayDevicesW
            enum.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DisplayDevice), wintypes.DWORD]
            enum.restype = wintypes.BOOL
            names = []
            for index in range(16):
                device = DisplayDevice()
                device.cb = ctypes.sizeof(device)
                if not enum(None, index, ctypes.byref(device), 0):
                    break
                if device.flags & 1 and device.description not in names:
                    names.append(device.description)
            gpu = " / ".join(names) or "Нет активного видеоадаптера"
        except (OSError, AttributeError):
            pass
        build = str(sys.getwindowsversion().build)
    total = psutil.virtual_memory().total
    name = "Windows 11" if build and int(build) >= 22000 else "Windows 10" if build else platform.system()
    return {"cpu": processor, "gpu": gpu, "ram": f"{format_bytes(total)} · {psutil.cpu_count(logical=True)} потоков CPU",
            "os": f"{name} · сборка {build}" if build else name}


