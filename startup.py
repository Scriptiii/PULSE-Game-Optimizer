"""Startup apps/services, reversible changes, and a separate write-ahead journal."""
from __future__ import annotations

import copy
import ctypes
import hashlib
import json
import os
import re
import struct
import sys
import time
from pathlib import Path
from threading import RLock

from .engine import atomic_json, now
from .services import Services, service_restriction, valid_service_name

APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
ACTIVE = {"pending", "managed", "restore_failed"}


def identity(target):
    return hashlib.sha256(json.dumps(target, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def validate_target(target):
    if not isinstance(target, dict):
        raise ValueError("Повреждена запись автозагрузки")
    name = target.get("name")
    if target.get("kind") == "app":
        if (set(target) != {"kind", "hive", "bucket", "name"}
                or target["hive"] not in ("HKCU", "HKLM")
                or target["bucket"] not in ("Run", "Run32", "StartupFolder")
                or not isinstance(name, str) or not 0 < len(name) <= 1024 or "\0" in name):
            raise ValueError("Некорректный источник приложения")
    elif target.get("kind") == "service":
        if set(target) != {"kind", "name"} or not valid_service_name(name):
            raise ValueError("Некорректное имя службы")
    else:
        raise ValueError("Неизвестный вид автозагрузки")


def validate_state(kind, state):
    if not isinstance(state, dict):
        raise ValueError("Некорректное исходное состояние")
    if kind == "app":
        if type(state.get("exists")) is not bool:
            raise ValueError("Некорректное состояние приложения")
        if state["exists"]:
            if not isinstance(state.get("data"), str) or not re.fullmatch(r"[0-9a-fA-F]{24}", state["data"]):
                raise ValueError("Неизвестный формат StartupApproved")
            if struct.unpack("<I", bytes.fromhex(state["data"])[:4])[0] not in (2, 3, 6, 7):
                raise ValueError("Неизвестный флаг StartupApproved")
    elif kind == "service":
        if type(state.get("start")) is not int or state["start"] not in (2, 3, 4) or type(state.get("delayed")) is not bool:
            raise ValueError("Некорректный режим службы")
    else:
        raise ValueError("Неизвестный вид автозагрузки")


def enabled(kind, state):
    validate_state(kind, state)
    if kind == "service":
        return state["start"] == 2
    return not state["exists"] or struct.unpack("<I", bytes.fromhex(state["data"])[:4])[0] in (2, 6)


def desired_state(kind, current, desired):
    validate_state(kind, current)
    if kind == "service":
        return {**current, "start": 2 if desired else 4}
    if not current["exists"] and desired:
        return current.copy()
    family = 2
    if current["exists"] and struct.unpack("<I", bytes.fromhex(current["data"])[:4])[0] in (6, 7):
        family = 6
    stamp = 0 if desired else int((time.time() + 11644473600) * 10_000_000)
    return {"exists": True, "data": struct.pack("<IQ", family if desired else family + 1, stamp).hex()}


class WindowsStartup:
    demo = False

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("Управление автозагрузкой доступно в Windows")
        import winreg
        self.reg = winreg
        shell = ctypes.WinDLL("shell32")
        shell.IsUserAnAdmin.argtypes = []
        shell.IsUserAnAdmin.restype = ctypes.c_int
        self.is_admin = bool(shell.IsUserAnAdmin())
        self.services = Services()
        self.service_rows = []

    def _hive(self, target):
        return self.reg.HKEY_CURRENT_USER if target["hive"] == "HKCU" else self.reg.HKEY_LOCAL_MACHINE

    def read(self, target):
        validate_target(target)
        if target["kind"] == "service":
            data = self.services.read(target["name"])
            state = {"start": data["start"], "delayed": data["delayed"]}
        else:
            try:
                with self.reg.OpenKey(self._hive(target), APPROVED + "\\" + target["bucket"], 0,
                                      self.reg.KEY_READ | self.reg.KEY_WOW64_64KEY) as key:
                    raw, kind = self.reg.QueryValueEx(key, target["name"])
                if kind != self.reg.REG_BINARY or not isinstance(raw, bytes):
                    raise ValueError("Неизвестный тип StartupApproved")
                state = {"exists": True, "data": raw.hex()}
            except FileNotFoundError:
                state = {"exists": False}
        validate_state(target["kind"], state)
        return state

    def write(self, target, state):
        validate_target(target)
        validate_state(target["kind"], state)
        if target["kind"] == "service":
            self.services.write(target["name"], state)
        elif state["exists"]:
            with self.reg.CreateKeyEx(self._hive(target), APPROVED + "\\" + target["bucket"], 0,
                                      self.reg.KEY_SET_VALUE | self.reg.KEY_WOW64_64KEY) as key:
                self.reg.SetValueEx(key, target["name"], 0, self.reg.REG_BINARY, bytes.fromhex(state["data"]))
        else:
            try:
                with self.reg.OpenKey(self._hive(target), APPROVED + "\\" + target["bucket"], 0,
                                      self.reg.KEY_SET_VALUE | self.reg.KEY_WOW64_64KEY) as key:
                    self.reg.DeleteValue(key, target["name"])
            except FileNotFoundError:
                pass

    def _app(self, target, name, command):
        reason = ""
        try:
            state = self.read(target)
            on = enabled("app", state)
        except (OSError, ValueError) as exc:
            state, on, reason = None, None, str(exc)
        if any(word in (name + " " + command).lower() for word in ("securityhealth", "windows defender", "msmpeng")):
            reason = "Компонент безопасности Windows"
        elif target["hive"] == "HKLM" and not self.is_admin:
            reason = "Нужен запуск от имени администратора"
        return {"id": identity(target), "target": target, "kind": "app", "name": name,
                "scope": "Текущий пользователь" if target["hive"] == "HKCU" else "Все пользователи",
                "description": command, "enabled": on, "state": state, "reason": reason,
                "can_change": not reason}

    def _apps(self):
        rows, errors = [], []
        for hive, tag, view, bucket in (
            (self.reg.HKEY_CURRENT_USER, "HKCU", self.reg.KEY_WOW64_64KEY, "Run"),
            (self.reg.HKEY_LOCAL_MACHINE, "HKLM", self.reg.KEY_WOW64_64KEY, "Run"),
            (self.reg.HKEY_LOCAL_MACHINE, "HKLM", self.reg.KEY_WOW64_32KEY, "Run32"),
        ):
            try:
                with self.reg.OpenKey(hive, RUN, 0, self.reg.KEY_READ | view) as key:
                    for i in range(self.reg.QueryInfoKey(key)[1]):
                        name, command, kind = self.reg.EnumValue(key, i)
                        if kind in (self.reg.REG_SZ, self.reg.REG_EXPAND_SZ) and isinstance(command, str):
                            rows.append(self._app({"kind": "app", "hive": tag, "bucket": bucket, "name": name}, name, command))
            except FileNotFoundError:
                pass
            except OSError as exc:
                errors.append(f"{tag} Run: {exc}")
        for variable, tag in (("APPDATA", "HKCU"), ("ProgramData", "HKLM")):
            base = os.environ.get(variable)
            if not base:
                continue
            folder = Path(base) / "Microsoft/Windows/Start Menu/Programs/Startup"
            try:
                for file in folder.iterdir():
                    if file.is_file() and file.name.lower() != "desktop.ini":
                        rows.append(self._app({"kind": "app", "hive": tag, "bucket": "StartupFolder", "name": file.name}, file.stem, str(file)))
            except FileNotFoundError:
                pass
            except OSError as exc:
                errors.append(f"Startup: {exc}")
        return rows, errors

    def guard(self, row, turn_on):
        if row["kind"] == "app":
            return row["reason"]
        config = next((s for s in self.service_rows if s["name"] == row["target"]["name"]), None)
        if not config:
            return "Служба больше не найдена"
        reason = service_restriction(config, self.is_admin)
        if reason:
            return reason
        name = config["name"].lower()
        if not turn_on:
            dependents = [s["display"] for s in self.service_rows if (s["start"] == 2 or (s["start"] != 4 and s.get("running")))
                          and name in {d.lower() for d in s["dependencies"]}]
            if dependents:
                return "Используется службами: " + ", ".join(dependents[:3])
        else:
            disabled = {s["name"].lower() for s in self.service_rows if s["start"] == 4}
            if any(d.lower() in disabled for d in config["dependencies"]):
                return "Для запуска сначала включите зависимые службы"
        return ""

    def collect(self):
        rows, errors = self._apps()
        self.service_rows, service_errors = self.services.collect()
        errors.extend(service_errors)
        for config in self.service_rows:
            if not config["service_type"] & 0x30 or config["start"] not in (2, 3, 4):
                continue
            target = {"kind": "service", "name": config["name"]}
            state = {"start": config["start"], "delayed": config["delayed"]}
            row = {"id": identity(target), "target": target, "kind": "service", "name": config["display"],
                   "enabled": enabled("service", state), "state": state, "running": config.get("running", False),
                   "description": config.get("description", ""), "scope": "Служба Windows"}
            row["reason"] = self.guard(row, not row["enabled"])
            row["can_change"] = not row["reason"]
            rows.append(row)
        unique = {row["id"]: row for row in rows}
        return list(unique.values()), errors


class DemoStartup(WindowsStartup):
    demo = True

    def __init__(self):
        self.is_admin = True
        self.service_rows = []
        self.values = {}
        self.app_rows = []
        for name, on in (("Discord", True), ("Steam", False), ("Epic Games Launcher", True)):
            target = {"kind": "app", "hive": "HKCU", "bucket": "Run", "name": name}
            self.values[identity(target)] = {"exists": False} if on else {"exists": True, "data": struct.pack("<IQ", 3, 123456).hex()}
            self.app_rows.append((target, name))
        for name, display, start, delayed, binary in (
            ("LauncherHelper", "Служба игрового лаунчера", 2, False, r"C:\Program Files\GameLauncher\helper.exe"),
            ("LauncherUpdater", "Обновление игрового лаунчера", 2, True, r"C:\Program Files\GameLauncher\update.exe"),
            ("OptionalTools", "Фоновые инструменты", 4, False, r"C:\Program Files\Tools\helper.exe"),
            ("SysMain", "SysMain", 2, False, r"C:\Windows\System32\svchost.exe"),
            ("RpcSs", "Удалённый вызов процедур (RPC)", 2, False, r"C:\Windows\System32\svchost.exe"),
        ):
            self.service_rows.append({"name": name, "display": display, "start": start, "delayed": delayed,
                                      "binary": binary, "service_type": 16, "protected": 0,
                                      "dependencies": [], "running": start == 2, "description": "Демонстрационная служба"})

    def _apps(self):
        return [self._app(target, name, "Демонстрационное приложение") for target, name in self.app_rows], []

    def read(self, target):
        if target["kind"] == "app":
            return copy.deepcopy(self.values[identity(target)])
        row = next(s for s in self.service_rows if s["name"] == target["name"])
        return {"start": row["start"], "delayed": row["delayed"]}

    def write(self, target, state):
        validate_target(target)
        validate_state(target["kind"], state)
        if target["kind"] == "app":
            self.values[identity(target)] = copy.deepcopy(state)
        else:
            row = next(s for s in self.service_rows if s["name"] == target["name"])
            row.update(state)

    def collect(self):
        # Reuse policy and row assembly without touching the real SCM.
        class Data:
            def collect(inner):
                return self.service_rows, []
        self.services = Data()
        return super().collect()


class StartupManager:
    def __init__(self, backend, directory):
        self.backend = backend
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def _load(self, path):
        if path.stat().st_size > 32_000:
            raise ValueError("Слишком большая копия автозагрузки")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or data.get("demo") != self.backend.demo:
            raise ValueError("Несовместимая копия автозагрузки")
        validate_target(data.get("target"))
        if identity(data["target"]) != path.stem or data.get("id") != path.stem:
            raise ValueError("Некорректный идентификатор копии")
        if data.get("status") not in ACTIVE | {"restored"}:
            raise ValueError("Некорректное состояние копии")
        for key in ("original", "previous", "after"):
            validate_state(data["target"]["kind"], data.get(key))
        return data

    def history(self):
        with self.lock:
            result = []
            for path in self.directory.glob("*.json"):
                try:
                    data = self._load(path)
                    if data["status"] in ACTIVE:
                        result.append(data)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    result.append({"id": path.stem, "error": str(exc), "status": "invalid"})
            return result

    def inspect(self):
        with self.lock:
            history = self.history()
            records = {r["id"]: r for r in history}
            corrupt = any(r["status"] == "invalid" for r in history)
            rows, errors = self.backend.collect()
            shown = []
            for row in rows:
                row["managed"] = row["id"] in records
                if row["kind"] == "service" and row["state"]["start"] == 3 and not row["managed"]:
                    continue
                if corrupt:
                    row.update(can_change=False, reason="Есть повреждённая копия автозагрузки; проверьте журнал")
                shown.append(row)
            shown.sort(key=lambda r: (r["kind"] != "app", r["name"].casefold(), r["id"]))
            errors += [r["error"] for r in history if r["status"] == "invalid"]
            return {"rows": shown, "history": history, "errors": errors}

    def _current(self, key):
        if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
            raise ValueError("Некорректный идентификатор автозагрузки")
        rows, _ = self.backend.collect()
        row = next((r for r in rows if r["id"] == key), None)
        if row is None:
            raise RuntimeError("Запись больше не найдена. Обновите список.")
        return row

    def _save(self, record):
        atomic_json(self.directory / (record["id"] + ".json"), record)

    def _change(self, row, target, original, previous_status="restored"):
        reason = self.backend.guard(row, enabled(row["kind"], target))
        if reason:
            raise RuntimeError(reason)
        current = self.backend.read(row["target"])
        validate_state(row["kind"], current)
        if current == target:
            return {"message": "Автозагрузка уже настроена.", "changed": False}
        record = {"version": 1, "id": row["id"], "target": row["target"], "name": row["name"],
                  "demo": self.backend.demo, "original": original, "previous": current,
                  "after": target, "status": "pending", "updated": now()}
        self._save(record)  # reaches disk BEFORE registry / SCM writes
        try:
            self.backend.write(row["target"], target)
            if self.backend.read(row["target"]) != target:
                raise RuntimeError("Windows не подтвердила изменение автозагрузки")
            record["status"] = "restored" if target == original else "managed"
            self._save(record)
        except Exception as exc:
            try:
                if self.backend.read(row["target"]) != current:
                    self.backend.write(row["target"], current)
                if self.backend.read(row["target"]) != current:
                    raise RuntimeError("Не подтверждён откат")
                record["status"] = previous_status
            except Exception as restore_exc:
                record["status"] = "restore_failed"
                record["restore_error"] = str(restore_exc)
            record["error"] = str(exc)
            self._save(record)
            raise RuntimeError(str(exc) + (" Изменение отменено." if record["status"] != "restore_failed"
                                          else " Повторите восстановление автозагрузки.")) from exc
        return {"changed": True, "message": f"«{row['name']}»: автозапуск {'включён' if enabled(row['kind'], target) else 'отключён'}."}

    def set_enabled(self, key, desired):
        with self.lock:
            if type(desired) is not bool:
                raise ValueError("Ожидался переключатель включено/выключено")
            history = self.history()
            if any(r["status"] == "invalid" for r in history):
                raise RuntimeError("Есть повреждённая копия автозагрузки")
            row = self._current(key)
            current = self.backend.read(row["target"])
            record = next((r for r in history if r["id"] == key), None)
            original = record["original"] if record else current
            target = original if enabled(row["kind"], original) == desired else desired_state(row["kind"], current, desired)
            return self._change(row, target, original, record["status"] if record else "restored")

    def restore(self, key):
        with self.lock:
            if not re.fullmatch(r"[a-f0-9]{64}", key):
                raise ValueError("Некорректный идентификатор")
            record = self._load(self.directory / (key + ".json"))
            row = self._current(key)
            result = self._change(row, record["original"], record["original"], record["status"])
            # Also close a crash journal when the original state is already present.
            if not result["changed"]:
                record["status"] = "restored"
                self._save(record)
            return result

    def restore_all(self):
        with self.lock:
            records = self.history()
            if any(r["status"] == "invalid" for r in records):
                raise RuntimeError("Повреждена копия автозагрузки. Проверьте журнал.")
            pending = {r["id"]: r for r in records}
            failures = {}
            while pending:
                progress = False
                for key in list(pending):
                    try:
                        self.restore(key)
                        pending.pop(key)
                        failures.pop(key, None)
                        progress = True
                    except Exception as exc:
                        failures[key] = str(exc)
                if not progress:
                    break
            if pending:
                raise RuntimeError("Не все записи восстановлены:\n" + "\n".join(f"{pending[k].get('name', k)}: {failures[k]}" for k in pending))
            return {"message": f"Исходная автозагрузка восстановлена: {len(records)} записей."}
