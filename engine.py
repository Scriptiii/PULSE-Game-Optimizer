"""Write-ahead recovery journal, exact-state verification and transactional changes."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from .catalog import BY_ID, TWEAKS
from .rules import POWER
from .windows import validate_state

RECOVERABLE = {"applying", "applied", "restore_failed"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Engine:
    def __init__(self, backend, directory: Path):
        self.backend = backend
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def _load(self, path: Path) -> dict:
        if path.stat().st_size > 256_000:
            raise ValueError("Слишком большая резервная копия")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Некорректный формат резервной копии")
        if data.get("version") != 1 or data.get("id") != path.stem:
            raise ValueError("Неподдерживаемый формат резервной копии")
        entries = data.get("entries")
        if not isinstance(entries, list) or not 1 <= len(entries) <= len(TWEAKS):
            raise ValueError("Некорректный список настроек")
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("id") not in BY_ID or entry["id"] in seen:
                raise ValueError("Неизвестная или повторная настройка")
            seen.add(entry["id"])
            validate_state(entry["id"], entry.get("before"))
            validate_state(entry["id"], entry.get("after"))
            if entry.get("status") not in RECOVERABLE | {"planned", "restored", "rolled_back"}:
                raise ValueError("Неизвестное состояние операции")
        return data

    def history(self) -> list[dict]:
        with self.lock:
            result = []
            for path in sorted(self.directory.glob("*.json"), reverse=True):
                try:
                    data = self._load(path)
                    data["recoverable"] = sum(e["status"] in RECOVERABLE for e in data["entries"])
                    result.append(data)
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    result.append({"id": path.stem, "created": "", "entries": [], "recoverable": 0,
                                   "error": f"Повреждена резервная копия {path.name}: {exc}"})
            return result

    def active_entry(self, tweak_id: str):
        for record in self.history():
            for entry in record["entries"]:
                if entry["id"] == tweak_id and entry["status"] in RECOVERABLE:
                    return record, entry
        return None

    def inspect(self) -> dict:
        with self.lock:
            history = self.history()
            managed = {e["id"] for r in history for e in r["entries"] if e["status"] in RECOVERABLE}
            states = {}
            for tweak in TWEAKS:
                available, reason = self.backend.availability(tweak.id)
                state = {"available": available, "reason": reason, "active": False,
                         "unknown": False, "managed": tweak.id in managed}
                if available:
                    try:
                        current = self.backend.read(tweak.id)
                        state["active"] = current == self.backend.target(tweak.id, current)
                        state["unknown"] = current.get("exists") is False
                    except Exception as exc:
                        state.update(available=False, reason=str(exc))
                states[tweak.id] = state
            return {"states": states, "history": history}

    def _save(self, record: dict):
        atomic_json(self.directory / f"{record['id']}.json", record)

    def apply(self, tweak_ids: list[str]) -> dict:
        with self.lock:
            if not tweak_ids or any(i not in BY_ID for i in tweak_ids):
                raise ValueError("Выберите известные настройки")
            if any(r.get("error") and not r["entries"] for r in self.history()):
                raise RuntimeError("Есть повреждённая резервная копия. Проверьте раздел «Восстановление» перед новыми изменениями.")
            entries = []
            ids = list(dict.fromkeys(tweak_ids))
            projected_scheme = None
            if "power" in ids:
                available, reason = self.backend.availability("power")
                if not available:
                    raise RuntimeError(f"{BY_ID['power'].title}: {reason}")
                projected_scheme = self.backend.target("power", self.backend.read("power"))["guid"]
                ids.remove("power")
                ids.insert(0, "power")
            for tweak_id in ids:
                available, reason = self.backend.availability(tweak_id)
                if not available:
                    raise RuntimeError(f"{BY_ID[tweak_id].title}: {reason}")
                before = (self.backend.read_power_value(tweak_id, projected_scheme)
                          if tweak_id in POWER and projected_scheme else self.backend.read(tweak_id))
                after = self.backend.target(tweak_id, before)
                validate_state(tweak_id, before)
                validate_state(tweak_id, after)
                if before == after:
                    continue
                if self.active_entry(tweak_id):
                    raise RuntimeError(f"{BY_ID[tweak_id].title}: сначала восстановите сохранённое состояние.")
                entries.append({"id": tweak_id, "before": before, "after": after, "status": "planned"})
            if not entries:
                return {"changed": 0, "message": "Выбранные параметры уже настроены."}
            record = {"version": 1, "id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + "-" + uuid.uuid4().hex[:8],
                      "created": now(), "demo": self.backend.demo, "entries": entries}
            # The entire before-state reaches disk before the first Windows write.
            self._save(record)
            try:
                for entry in entries:
                    entry["status"] = "applying"
                    self._save(record)
                    self.backend.write(entry["id"], entry["after"])
                    if not self.backend.matches(entry["id"], entry["after"]):
                        raise RuntimeError("Windows не подтвердила новое значение")
                    entry["status"] = "applied"
                    self._save(record)
            except Exception as exc:
                failures = []
                for entry in reversed(entries):
                    if entry["status"] not in RECOVERABLE:
                        continue
                    try:
                        self.backend.write(entry["id"], entry["before"])
                        if not self.backend.matches(entry["id"], entry["before"]):
                            raise RuntimeError("Проверка отката не пройдена")
                        entry["status"] = "rolled_back"
                    except Exception as restore_exc:
                        entry["status"] = "restore_failed"
                        entry["error"] = str(restore_exc)
                        failures.append(BY_ID[entry["id"]].title)
                record["error"] = str(exc)
                self._save(record)
                suffix = (" Нужен ручной повтор отката в «Восстановлении»: " + ", ".join(failures)) if failures else " Изменения отменены."
                raise RuntimeError(str(exc) + suffix) from exc
            return {"changed": len(entries), "message": f"Настроек применено: {len(entries)}. Исходные значения сохранены."}

    def restore(self, record_id: str, tweak_id: str | None = None) -> dict:
        with self.lock:
            # IDs are generated internally; never accept a path from the journal/UI.
            if not isinstance(record_id, str) or not all(c.isalnum() or c == "-" for c in record_id):
                raise ValueError("Некорректный идентификатор резервной копии")
            record = self._load(self.directory / f"{record_id}.json")
            if record.get("demo") != self.backend.demo:
                raise ValueError("Демонстрационные копии нельзя применять к Windows")
            entries = [e for e in record["entries"] if e["status"] in RECOVERABLE
                       and (tweak_id is None or e["id"] == tweak_id)]
            for entry in entries:
                active = self.active_entry(entry["id"])
                if active and active[0]["id"] != record_id:
                    raise RuntimeError("Сначала восстановите более новую копию этой настройки")
            failed = []
            for entry in reversed(entries):
                try:
                    self.backend.write(entry["id"], entry["before"])
                    if not self.backend.matches(entry["id"], entry["before"]):
                        raise RuntimeError("Windows не подтвердила восстановление")
                    entry["status"] = "restored"
                    entry.pop("error", None)
                except Exception as exc:
                    entry["status"] = "restore_failed"
                    entry["error"] = str(exc)
                    failed.append(f"{BY_ID[entry['id']].title}: {exc}")
                self._save(record)
            if failed:
                raise RuntimeError("Не все настройки восстановлены. Можно повторить:\n" + "\n".join(failed))
            return {"changed": len(entries), "message": f"Настроек восстановлено: {len(entries)}."}

    def restore_tweak(self, tweak_id: str) -> dict:
        with self.lock:
            active = self.active_entry(tweak_id)
            if not active:
                return {"changed": 0, "message": "Сохранённого исходного состояния нет."}
            return self.restore(active[0]["id"], tweak_id)
