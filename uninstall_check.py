"""Read-only verification of a desktop uninstall; never deletes residuals.

An absent registry entry alone cannot prove removal. Inspect the registered
installation directory, without following reparse points or guessing AppData
ownership. Tests use synthetic installations under pytest's temporary root.
"""
from __future__ import annotations

import os
import stat
import time
from pathlib import Path


def link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def installation_scope(row, others=()):
    raw = row.get("location", "").strip().strip('"')
    if not raw:
        return {"root": None, "reason": "Установщик не указал папку программы; полноту удаления файлов проверить нельзя."}
    path = Path(os.path.abspath(os.path.expandvars(raw)))
    if not Path(os.path.expandvars(raw)).is_absolute() or path == Path(path.anchor):
        return {"root": None, "reason": "В записи установки указан некорректный или общий путь."}
    protected = [Path(os.path.abspath(value)) for key in (
        "SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "CommonProgramFiles",
        "CommonProgramFiles(x86)", "ProgramData", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP")
                 if (value := os.environ.get(key))]
    user = Path.home()
    protected.extend(user / name for name in ("Desktop", "Documents", "Downloads", "Music", "Pictures", "Videos", "AppData"))
    if path in protected or any(path == p or path in p.parents for p in protected):
        return {"root": None, "reason": "Путь указывает на общую папку. Она не считается папкой одной программы."}
    windows = os.environ.get("SystemRoot")
    if windows and Path(os.path.abspath(windows)) in path.parents:
        return {"root": None, "reason": "Путь относится к системным файлам Windows; проверка дерева отключена."}
    for other in others:
        if other.get("id") == row.get("id") or not other.get("location"):
            continue
        other_path = Path(os.path.abspath(os.path.expandvars(other["location"].strip().strip('"'))))
        if path == other_path or path in other_path.parents:
            return {"root": None, "reason": "В этой папке зарегистрирована другая программа. Общие файлы не считаются остатками одной установки."}
    try:
        for ancestor in (path, *path.parents):
            if link(ancestor.lstat()):
                return {"root": None, "reason": "Папка или её родитель является ссылкой / junction. Проверка дерева отключена."}
        if not path.is_dir():
            return {"root": None, "reason": "До удаления папка установки не была доступна как каталог."}
    except OSError as exc:
        return {"root": None, "reason": "До удаления папку установки не удалось проверить: " + str(exc)}
    return {"root": str(path), "reason": ""}


def inspect_remaining(scope, max_entries=20_000, seconds=3):
    root_value = scope.get("root")
    result = {"root": root_value, "absent": False, "files": 0, "folders": 0, "bytes": 0,
              "sample": [], "errors": [], "truncated": False}
    if not root_value:
        result["errors"].append(scope.get("reason") or "Неизвестна папка установки.")
        return result
    root = Path(root_value)
    # A removed parent is expected; a substituted symlink must never be followed.
    try:
        for ancestor in (root, *root.parents):
            try:
                info = ancestor.lstat()
            except FileNotFoundError:
                continue
            if link(info):
                result["errors"].append("После удаления путь заменён ссылкой / junction; дерево не проверялось.")
                return result
        root.lstat()
    except FileNotFoundError:
        result["absent"] = True
        return result
    except OSError as exc:
        result["errors"].append(str(exc))
        return result
    end = time.monotonic() + seconds
    pending = [root]
    entries = 0
    while pending:
        path = pending.pop()
        try:
            # Recheck each directory; traversal is read-only, never cleanup.
            if link(path.lstat()):
                result["errors"].append("Ссылка пропущена: " + str(path))
                continue
            with os.scandir(path) as children:
                for child in children:
                    entries += 1
                    if entries > max_entries or time.monotonic() >= end:
                        result["truncated"] = True
                        return result
                    try:
                        info = child.stat(follow_symlinks=False)
                        if link(info):
                            result["errors"].append("Ссылка пропущена: " + child.path)
                            continue
                        if len(result["sample"]) < 60:
                            result["sample"].append(str(Path(child.path).relative_to(root)))
                        if stat.S_ISDIR(info.st_mode):
                            result["folders"] += 1
                            pending.append(Path(child.path))
                        else:
                            result["files"] += 1
                            result["bytes"] += info.st_size
                    except OSError as exc:
                        result["errors"].append(str(exc))
        except OSError as exc:
            result["errors"].append(str(exc))
    return result


def verify_uninstall(row, scope, inventory, process):
    if process.get("running"):
        return {"status": "running", "message": "Мастер удаления ещё работает. Заверши или отмени его; проверка запустится после завершения."}
    leftovers = inspect_remaining(scope)
    registered = any(r["id"] == row["id"] or (
        r["title"].casefold() == row["title"].casefold() and r["version"] == row["version"]
        and r["target"]["hive"] == row["target"]["hive"] and r.get("location", "") == row.get("location", ""))
                     for r in inventory["rows"])
    code = process.get("exit_code")
    if code in (1602, 1223):
        status, text = "cancelled", "Удаление отменено в штатном мастере. Остатки автоматически не удаляются."
    elif process.get("error") or inventory["errors"]:
        status, text = "unknown", "Не удалось полностью проверить результат удаления."
    elif code not in (None, 0, 3010, 1641):
        status, text = "failed", f"Мастер завершился с кодом {code}. Успешное удаление не подтверждено."
    elif registered:
        status, text = "registered", "Программа всё ещё зарегистрирована в Windows. Удаление не подтверждено; мастер мог открыть дочерний процесс."
    elif leftovers["errors"] or leftovers["truncated"]:
        status, text = "unknown", "Запись установки исчезла, но проверка файлов неполная. Полное удаление не подтверждено."
    elif leftovers["absent"]:
        status, text = "verified", "Запись установки и проверенная папка программы со всем её содержимым отсутствуют."
    else:
        status, text = "leftovers", f"Запись установки удалена, но папка программы осталась: файлов {leftovers['files']}, вложенных папок {leftovers['folders']}."
    lines = [row["title"], text]
    if leftovers["root"]:
        lines.append("Проверенная папка: " + leftovers["root"])
    if leftovers["sample"]:
        lines += ["Оставшиеся файлы и папки (первые 60):", *leftovers["sample"]]
    lines += leftovers["errors"] + inventory["errors"]
    if process.get("error"):
        lines.append(process["error"])
    if leftovers["truncated"]:
        lines.append("Достигнут лимит проверки; список остатков неполный.")
    if code in (3010, 1641):
        lines.append("Windows сообщает о необходимости перезагрузки.")
    lines.append("Личные данные вне папки установки, например AppData и Документы, не проверялись и автоматически не удаляются.")
    return {"status": status, "message": "\n".join(lines), "registered": registered,
            "leftovers": leftovers, "exit_code": code}
