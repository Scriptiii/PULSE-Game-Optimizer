"""Native package uninstallation, never manual deletion of WindowsApps/WinSxS.

All-machine removal requires elevation. Inventory and demo are read-only.
The operation log is an audit trail, not a backup of removed applications.
"""
from __future__ import annotations

import base64
import copy
import json
import re
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .engine import atomic_json, now
from .windows import run_system, system_executable

# Optional consumer apps, not a recommendation to delete everything.
COMPONENTS = {
    "MicrosoftWindows.Client.WebExperience": ("Виджеты и лента новостей Windows 11", "Удаляется Windows Web Experience Pack, включая панель Win+W. Другие веб-функции этого пакета также исчезнут."),
    "Microsoft.BingNews": ("Microsoft News / Новости", "Отдельное приложение новостей. Это не встроенная лента «Новости и интересы» Windows 10."),
    "Microsoft.BingWeather": ("MSN Погода", "Удаляется приложение прогноза погоды."),
    "Microsoft.Copilot": ("Microsoft Copilot", "Удаляется отдельное приложение Copilot. Интеграции в Edge и Office этим не удаляются."),
    "Clipchamp.Clipchamp": ("Microsoft Clipchamp", "Удаляется видеоредактор; несохранённые локальные проекты могут быть потеряны."),
    "Microsoft.GetHelp": ("Техническая поддержка / Get Help", "Удаляется приложение поддержки. Некоторые ссылки диагностики Windows перестанут открывать его."),
    "Microsoft.Getstarted": ("Советы / Начало работы", "Удаляется приложение обучающих советов Windows."),
    "Microsoft.WindowsFeedbackHub": ("Центр отзывов", "Удаляется приложение отправки отзывов Microsoft."),
    "Microsoft.MicrosoftOfficeHub": ("Microsoft 365 / Office Hub", "Удаляется приложение-хаб Office, установленный настольный Office остаётся."),
    "Microsoft.MicrosoftSolitaireCollection": ("Microsoft Solitaire Collection", "Удаляется коллекция пасьянсов и её локальные данные."),
    "Microsoft.BingFinance": ("MSN Финансы", "Удаляется приложение финансовых новостей."),
    "Microsoft.BingSports": ("MSN Спорт", "Удаляется приложение спортивных новостей."),
    "Microsoft.WindowsMaps": ("Карты Windows", "Удаляется приложение карт. Загруженные отдельно карты не входят в проверку освобождения места."),
    "Microsoft.People": ("Люди / Контакты", "Удаляется приложение контактов и его локальные данные."),
    "Microsoft.YourPhone": ("Связь с телефоном", "Прекратит работать связь с телефоном через это приложение."),
    "Microsoft.OutlookForWindows": ("Новый Outlook для Windows", "Удаляется почтовое приложение и его локальные данные. Проверь сохранность писем и вложений."),
    "microsoft.windowscommunicationsapps": ("Почта и Календарь", "Удаляются старые Почта и Календарь, включая локальные данные."),
    "MicrosoftTeams": ("Microsoft Teams (личный)", "Удаляется приложение личных чатов и видеозвонков."),
    "MSTeams": ("Microsoft Teams", "Прекратят работать чаты и звонки через это приложение."),
    "Microsoft.SkypeApp": ("Skype", "Удаляется приложение Skype и его локальные данные."),
    "Microsoft.549981C3F5F10": ("Cortana", "Удаляется отдельное устаревшее приложение Cortana, если оно установлено."),
    "Microsoft.Windows.DevHome": ("Dev Home", "Удаляется панель для разработчиков; инструменты разработки отдельно не удаляются."),
    "Microsoft.MixedReality.Portal": ("Портал смешанной реальности", "Удаляется приложение портала; не выбирай, если используешь его с VR-гарнитурой."),
    "Microsoft.Microsoft3DViewer": ("3D Viewer", "Удаляется просмотрщик 3D-моделей."),
    "Microsoft.Print3D": ("Print 3D", "Удаляется приложение подготовки 3D-печати."),
    "Microsoft.MSPaint": ("Paint 3D (старый пакет)", "Удаляется старый Paint 3D. Современный Paint имеет другое имя пакета."),
}
CAPABILITIES = {
    "XPS.Viewer~~~~0.0.1.0": ("Средство просмотра XPS", "Удаляется дополнительный компонент чтения XPS-документов."),
    "MathRecognizer~~~~0.0.1.0": ("Распознавание математического ввода", "Исчезнет распознавание рукописных формул."),
    "App.StepsRecorder~~~~0.0.1.0": ("Средство записи действий", "Удаляется Steps Recorder для записи шагов со снимками экрана."),
    "Print.Fax.Scan~~~~0.0.1.0": ("Факсы и сканирование Windows", "Удаляется классическое приложение факсов и сканирования. Не выбирай, если пользуешься им."),
}
KNOWN_APPS = {
    "Microsoft.WindowsCalculator": "Калькулятор", "Microsoft.WindowsCamera": "Камера",
    "Microsoft.Windows.Photos": "Фотографии", "Microsoft.WindowsNotepad": "Блокнот",
    "Microsoft.Paint": "Paint", "Microsoft.WindowsSoundRecorder": "Звукозапись",
    "Microsoft.ZuneMusic": "Медиаплеер", "Microsoft.ZuneVideo": "Кино и ТВ",
    "Microsoft.WindowsAlarms": "Часы", "Microsoft.Todos": "Microsoft To Do",
    "Microsoft.ScreenSketch": "Ножницы", "SpotifyAB.SpotifyMusic": "Spotify",
}
PROTECTED = re.compile(
    r"^(?:Microsoft\.(?:WindowsStore|StorePurchaseApp|DesktopAppInstaller|Services\.Store|"
    r"VCLibs|NET|UI\.Xaml|WindowsAppRuntime|WinAppRuntime|DirectX|WidgetsPlatformRuntime|"
    r"Gaming|Xbox|GameInput|Winget|ApplicationCompatibility|LanguageExperiencePack|Ink\.|"
    r"AV1Video|AVCEncoder|HEIFImage|HEVCVideo|MPEG2Video|RawImage|VP9Video|WebMedia|WebpImage|"
    r"SecHealthUI|WindowsSecurity|AAD|AccountsControl|"
    r"BioEnrollment|CredDialogHost|AsyncTextService|LockApp|Win32WebViewHost|"
    r"Windows\.(?:Apprep|AssignedAccess|CapturePicker|CloudExperienceHost|ContentDeliveryManager|"
    r"Narrator|OOBE|ParentalControls|PeopleExperienceHost|Pinning|PrintDialog|SecureAssessment|"
    r"Shell|StartMenu|Search|XGpuEject|immersivecontrolpanel))|"
    r"MicrosoftCorporationII\.WinAppRuntime|NVIDIACorp\.|RealtekSemiconductorCorp\.|"
    r"AdvancedMicroDevices|AppUp\.Intel|MicrosoftWindows\.(?:Client\.(?!WebExperience$)|UndockedDevKit)|"
    r"windows\.(?:immersivecontrolpanel|printdialog)|Windows\.)", re.I)
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,150}\Z")
PUBLISHER_ID = re.compile(r"[a-z0-9]{13}\Z", re.I)

PS_HEADER = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
function Read-Packages {
    $argsMap = @{PackageTypeFilter = @('Main','Bundle','Framework'); ErrorAction = 'Stop'}
    if ($admin) { $argsMap.AllUsers = $true }
    @(Get-AppxPackage @argsMap)
}
"""
PS_INVENTORY = PS_HEADER + r"""
$errorsList = @()
$packages = @(Read-Packages | ForEach-Object {
    @{name=$_.Name; publisher=$_.PublisherId; full=$_.PackageFullName;
      family=$_.PackageFamilyName; location=$_.InstallLocation;
      framework=[bool]$_.IsFramework; resource=[bool]$_.IsResourcePackage;
      protected=[bool]$_.NonRemovable; development=[bool]$_.IsDevelopmentMode;
      bundle=[bool]$_.IsBundle;
      dependencies=@($_.Dependencies | ForEach-Object { $_.PackageFullName })}
})
$provisioned = @(); $capabilities = @(); $provisionOk=$false; $capabilityOk=$false
if ($admin) {
    try {
        $provisioned = @(Get-AppxProvisionedPackage -Online | ForEach-Object { @{name=$_.DisplayName; full=$_.PackageName} })
        $provisionOk=$true
    } catch { $errorsList += ('Подготовленные приложения: ' + $_.Exception.Message) }
    try {
        $capabilities = @(Get-WindowsCapability -Online | ForEach-Object { @{name=$_.Name; state=[string]$_.State} })
        $capabilityOk=$true
    } catch { $errorsList += ('Дополнительные компоненты: ' + $_.Exception.Message) }
}
@{admin=$admin; sid=[string][Security.Principal.WindowsIdentity]::GetCurrent().User.Value; packages=@($packages); provisioned=@($provisioned); capabilities=@($capabilities);
  provision_ok=$provisionOk; capability_ok=$capabilityOk; errors=@($errorsList)} | ConvertTo-Json -Depth 7 -Compress
"""
PS_REMOVE = PS_HEADER + r"""
if (-not $admin) { throw 'Для удаления со всего ПК нужны права администратора.' }
$issues = @(); $restart=$false; $paths=@(); $unknownPaths=@()
if ($request.kind -eq 'capability') {
    $before = Get-WindowsCapability -Online -Name $request.name
    if ([string]$before.State -ne 'Installed') { throw 'Состояние компонента изменилось. Обнови список.' }
    $answer = Remove-WindowsCapability -Online -Name $request.name
    $restart = [bool]$answer.RestartNeeded
} else {
    $all = @(Read-Packages)
    $packages = @($all | Where-Object { $_.Name -eq $request.name -and $_.PublisherId -eq $request.publisher })
    $provisioned = @(Get-AppxProvisionedPackage -Online | Where-Object { $_.DisplayName -eq $request.name -and $_.PackageName.EndsWith('_' + $request.publisher) })
    $expected = @($request.packages | Sort-Object); $actual = @($packages.PackageFullName | Sort-Object)
    $ep = @($request.provisioned | Sort-Object); $ap = @($provisioned.PackageName | Sort-Object)
    if (($expected -join '|') -cne ($actual -join '|') -or ($ep -join '|') -cne ($ap -join '|')) {
        throw 'Пакеты изменились после просмотра. Обнови список.'
    }
    foreach ($package in $packages) {
        if ($package.NonRemovable -or $package.IsFramework -or $package.IsResourcePackage -or $package.IsDevelopmentMode -or $package.InstallLocation -match '\\(?:SystemApps|Windows)\\') {
            throw 'Пакет защищён или относится к системным компонентам.'
        }
        foreach ($other in $all) {
            if ($other.PackageFamilyName -ne $package.PackageFamilyName -and $other.Dependencies.PackageFullName -contains $package.PackageFullName) {
                throw 'От пакета зависит другое приложение. Удаление заблокировано.'
            }
        }
    }
    # Bundles are their parent package type, as required by Remove-AppxPackage -AllUsers.
    foreach ($package in @($packages | Sort-Object -Property IsBundle -Descending)) {
        try {
            if (@(Read-Packages | Where-Object { $_.PackageFullName -ceq $package.PackageFullName }).Count) {
                Remove-AppxPackage -Package $package.PackageFullName -AllUsers -Confirm:$false
            }
        } catch { $issues += $_.Exception.Message }
    }
    foreach ($package in $provisioned) {
        try {
            Remove-AppxProvisionedPackage -Online -AllUsers -PackageName $package.PackageName | Out-Null
        } catch { $issues += $_.Exception.Message }
    }
    foreach ($location in @($packages.InstallLocation | Where-Object { $_ } | Select-Object -Unique)) {
        try { if (Test-Path -LiteralPath $location -ErrorAction Stop) { $paths += $location } }
        catch { $unknownPaths += $location }
    }
}
@{errors=@($issues); restart=$restart; remaining_paths=@($paths); unknown_paths=@($unknownPaths)} | ConvertTo-Json -Depth 4 -Compress
"""


def powershell(script, request=None, timeout=90):
    if request is not None:
        payload = base64.b64encode(json.dumps(request).encode("utf-8")).decode("ascii")
        script = "$request = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + payload + "')) | ConvertFrom-Json\n" + script
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    output = run_system([system_executable(r"WindowsPowerShell\v1.0\powershell.exe"),
                         "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], timeout=timeout)
    result = json.loads(output.lstrip("\ufeff").strip())
    if not isinstance(result, dict):
        raise ValueError("Windows вернула некорректный ответ о пакетах")
    return result


def restriction(name, packages, referenced):
    if PROTECTED.match(name):
        return "Компонент Windows, магазина, среды выполнения или игровой инфраструктуры"
    for package in packages:
        if any(package.get(key) for key in ("protected", "framework", "resource", "development")):
            return "Windows защищает этот пакет, либо он содержит общие библиотеки"
        if re.search(r"\\(?:SystemApps|Windows)\\", package.get("location", ""), re.I):
            return "Системный пакет Windows"
        if package["full"] in referenced:
            return "От пакета зависит другое приложение"
    return ""


def build_inventory(raw):
    groups = {}
    referenced = set()
    for package in raw.get("packages", []):
        for dependency in package.get("dependencies", []):
            if not dependency.startswith(package["name"] + "_"):
                referenced.add(dependency)
        key = (package["name"], package["publisher"])
        groups.setdefault(key, {"packages": [], "provisioned": []})["packages"].append(package)
    for package in raw.get("provisioned", []):
        key = (package["name"], package["full"].rsplit("_", 1)[-1])
        groups.setdefault(key, {"packages": [], "provisioned": []})["provisioned"].append(package["full"])
    rows = []
    hidden = 0
    for (name, publisher), group in groups.items():
        if not IDENTITY.fullmatch(name) or not PUBLISHER_ID.fullmatch(publisher):
            hidden += 1
            continue
        reason = restriction(name, group["packages"], referenced)
        curated = COMPONENTS.get(name)
        if not curated:
            continue  # No general UWP/MSIX application removal section.
        if not reason and not raw.get("admin"):
            reason = "Запусти run.bat от имени администратора для удаления со всего ПК"
        elif not reason and not raw.get("provision_ok"):
            reason = "Не удалось проверить подготовленные пакеты. Обнови список"
        title, description = curated or (KNOWN_APPS.get(name, name), "Удаляется приложение и его локальные данные у всех пользователей. Проверь нужные файлы и сохранения.")
        rows.append({"id": f"appx:{name}_{publisher}", "kind": "appx", "group": "components" if curated else "uwp",
                     "name": name, "publisher": publisher, "title": title, "description": description,
                     "packages": group["packages"], "provisioned": sorted(group["provisioned"]),
                     "reason": reason, "available": not reason})
    if raw.get("capability_ok"):
        for capability in raw.get("capabilities", []):
            name = capability["name"]
            if name in CAPABILITIES and capability["state"] != "NotPresent":
                title, description = CAPABILITIES[name]
                reason = "" if capability["state"] == "Installed" and raw.get("admin") else "Изменение состояния ожидает завершения или перезагрузки"
                rows.append({"id": "cap:" + name, "kind": "capability", "group": "components", "name": name,
                             "title": title, "description": description, "state": capability["state"],
                             "reason": reason, "available": not reason})
    return {"rows": sorted(rows, key=lambda r: (r["group"], r["title"].casefold())),
            "admin": bool(raw.get("admin")), "errors": raw.get("errors", []), "hidden": hidden,
            "desktop": raw.get("desktop", []), "desktop_errors": raw.get("desktop_errors", []),
            "complete": bool(raw.get("admin") and raw.get("provision_ok") and raw.get("capability_ok"))}


def signature(row):
    return (row["kind"], row["name"], row.get("state"), row.get("publisher"),
            tuple(sorted(p["full"] for p in row.get("packages", []))), tuple(sorted(row.get("provisioned", []))))


class WindowsDebloat:
    demo = False

    def desktop_inventory(self):
        from .programs import current_identity, inspect_programs
        self.admin, self.sid = current_identity()
        desktop = inspect_programs(self.admin, self.sid)
        return {"admin": self.admin, "sid": self.sid, "desktop": desktop["rows"], "desktop_errors": desktop["errors"]}

    def component_inventory(self):
        return powershell(PS_INVENTORY)

    def inventory(self):
        # Complete, uncached inspection remains mandatory before package writes.
        with ThreadPoolExecutor(max_workers=1) as reader:
            components = reader.submit(self.component_inventory)
            desktop = self.desktop_inventory()
            try:
                raw = components.result()
            except Exception as exc:
                raw = {"admin": desktop["admin"], "errors": ["Компоненты Windows: " + str(exc)[:1000]]}
        raw.update(desktop)
        return raw

    def launch_program(self, row):
        from .programs import launch_uninstaller
        return launch_uninstaller(row, self.admin, self.sid)

    def check_program(self, row, scope, process):
        from .programs import inspect_programs
        from .uninstall_check import verify_uninstall
        state = process.poll()
        if state.get("running"):
            return {"status": "running", "message": "Мастер удаления ещё работает."}
        return verify_uninstall(row, scope, inspect_programs(self.admin, self.sid), state)

    def remove(self, row):
        if not row["available"]:
            raise ValueError(row["reason"])
        request = {"kind": row["kind"], "name": row["name"]}
        if row["kind"] == "capability":
            if row["name"] not in CAPABILITIES:
                raise ValueError("Неизвестный дополнительный компонент")
        else:
            if (row["name"] not in COMPONENTS or not IDENTITY.fullmatch(row["name"]) or not PUBLISHER_ID.fullmatch(row["publisher"])
                    or restriction(row["name"], row["packages"], set())):
                raise ValueError("Этот пакет нельзя удалить через PULSE")
            request.update(publisher=row["publisher"], packages=[p["full"] for p in row["packages"]], provisioned=row["provisioned"])
        return powershell(PS_REMOVE, request, timeout=240)


class DemoDebloat:
    demo = True

    def __init__(self):
        names = ["MicrosoftWindows.Client.WebExperience", "Microsoft.BingNews", "Microsoft.Copilot", "Clipchamp.Clipchamp",
                 "Microsoft.Getstarted", "Microsoft.WindowsFeedbackHub", "Microsoft.MicrosoftSolitaireCollection",
                 "Microsoft.YourPhone", "Microsoft.OutlookForWindows", "Microsoft.WindowsCalculator", "SpotifyAB.SpotifyMusic",
                 "Microsoft.WindowsStore", "Microsoft.VCLibs.140.00"]
        from .programs import make_entry
        desktop = [make_entry({"DisplayName": title, "DisplayVersion": "1.0", "Publisher": publisher,
                               "UninstallString": f'"C:\\Apps\\{title}\\uninstall.exe"', "WindowsInstaller": 1,
                               "EstimatedSize": (1700 * 1024, 680 * 1024, 340 * 1024, 8 * 1024)[index]},
                              {"hive": "HKLM", "sid": "", "view": view, "key": "{" + str(uuid.UUID(int=index + 1)) + "}"}, True)
                   for index, (title, publisher, view) in enumerate((("Steam", "Valve", 64), ("Discord", "Discord", 64),
                                                                    ("OBS Studio", "OBS Project", 64), ("7-Zip", "Igor Pavlov", 32)))]
        self.raw = {"admin": True, "provision_ok": True, "capability_ok": True, "errors": [], "packages": [], "provisioned": [], "desktop": desktop,
                    "capabilities": [{"name": n, "state": "Installed"} for n in CAPABILITIES]}
        for name in names:
            full = name + "_1.0.0.0_x64__8wekyb3d8bbwe"
            self.raw["packages"].append({"name": name, "publisher": "8wekyb3d8bbwe", "full": full,
                                         "family": name + "_8wekyb3d8bbwe", "location": "", "dependencies": []})
            self.raw["provisioned"].append({"name": name, "full": full})

    def inventory(self):
        return copy.deepcopy(self.raw)

    def desktop_inventory(self):
        return {"admin": self.raw["admin"], "desktop": copy.deepcopy(self.raw["desktop"]), "desktop_errors": []}

    def component_inventory(self):
        return {k: copy.deepcopy(v) for k, v in self.raw.items() if k != "desktop"}

    def launch_program(self, row):
        self.raw["desktop"] = [r for r in self.raw["desktop"] if r["id"] != row["id"]]
        return {"message": "ДЕМО · удаление программы показано только в памяти. Windows не изменялась."}

    def check_program(self, row, scope, process):
        return {"status": "verified", "message": "ДЕМО · показана проверка удаления программы. Реальные программы и файлы Windows не изменялись."}

    def remove(self, row):
        if row["kind"] == "capability":
            self.raw["capabilities"] = [c for c in self.raw["capabilities"] if c["name"] != row["name"]]
        else:
            self.raw["packages"] = [p for p in self.raw["packages"] if p["name"] != row["name"]]
            self.raw["provisioned"] = [p for p in self.raw["provisioned"] if p["name"] != row["name"]]
        return {"errors": [], "restart": False, "remaining_paths": [], "unknown_paths": []}


class DebloatManager:
    def __init__(self, backend, directory: Path):
        self.backend = backend
        self.directory = Path(directory)
        self.rows = {}
        self.programs = {}
        self.pending_programs = {}

    def inspect(self):
        result = build_inventory(self.backend.inventory())
        self.rows = {r["id"]: r for r in result["rows"]}
        self.programs = {r["id"]: r for r in result["desktop"]}
        return result

    def inspect_desktop(self):
        return self.backend.desktop_inventory()

    def inspect_components(self):
        return build_inventory(self.backend.component_inventory())

    def launch_program(self, key):
        if any(p.get("running") for p in self.pending_programs.values()):
            raise ValueError("Дождись завершения и проверки предыдущего удаления.")
        row = copy.deepcopy(self.programs[key])
        if not row["available"]:
            raise ValueError(row["reason"])
        record = {"id": uuid.uuid4().hex, "created": now(), "kind": "desktop", "title": row["title"],
                  "target": row["target"], "demo": self.backend.demo, "status": "launch_requested"}
        path = self.directory / (record["id"] + ".json")
        atomic_json(path, record)
        try:
            result = self.backend.launch_program(row)
            record.update(status="uninstaller_launched", message=result["message"], scope=result.get("scope"))
            self.pending_programs[key] = {"row": row, "scope": result.get("scope", {}), "process": result.get("process"),
                                          "record": record, "path": path, "running": True}
        except Exception as exc:
            record.update(status="launch_failed", error=str(exc))
            raise
        finally:
            atomic_json(path, record)
        return {"message": result["message"], "id": key}

    def program_running(self, key):
        pending = self.pending_programs[key]
        process = pending["process"]
        return bool(process and process.poll().get("running"))

    def check_program(self, key):
        pending = self.pending_programs[key]
        answer = self.backend.check_program(pending["row"], pending["scope"], pending["process"])
        pending["running"] = answer["status"] == "running"
        pending["record"].update(status=answer["status"], verification=answer, checked=now())
        atomic_json(pending["path"], pending["record"])
        return answer

    def remove(self, ids):
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("Выбери приложения без повторов")
        planned = [copy.deepcopy(self.rows[key]) for key in ids]
        fresh = self.inspect()
        for row in planned:
            current = self.rows.get(row["id"])
            if not current or not current["available"] or signature(current) != signature(row):
                raise ValueError("Список изменился или пакет недоступен. Обнови список и проверь выбор.")
        record = {"id": uuid.uuid4().hex, "created": now(), "demo": self.backend.demo, "status": "running", "entries": []}
        path = self.directory / (record["id"] + ".json")
        # Write the requested identities before the first system change.
        record["entries"] = [{"id": r["id"], "title": r["title"], "before": signature(r), "status": "planned"} for r in planned]
        atomic_json(path, record)
        for row, entry in zip(planned, record["entries"]):
            entry["status"] = "removing"
            atomic_json(path, record)
            try:
                entry["native"] = self.backend.remove(row)
            except subprocess.TimeoutExpired:
                entry["error"] = "Windows не ответила вовремя. Операция могла продолжиться; состояние будет проверено заново."
                entry["status"] = "unknown"
                atomic_json(path, record)
                break  # Do not start more servicing work while the timed-out operation may still run.
            except Exception as exc:
                entry["error"] = str(exc)[:2000]
            entry["status"] = "checking"
            atomic_json(path, record)
        try:
            fresh = self.inspect()
            for row, entry in zip(planned, record["entries"]):
                current = self.rows.get(row["id"])
                native = entry.get("native", {})
                # Failed provisioning/capability queries cannot prove absence.
                complete = fresh["complete"] and not fresh["errors"] and current is None
                entry["status"] = "removed" if complete else "incomplete"
                if not complete:
                    entry["message"] = "Остались пакеты или Windows не смогла подтвердить удаление. Обнови список после перезагрузки."
                elif native.get("remaining_paths") or native.get("restart"):
                    entry["message"] = "Пакеты удалены; остались файлы или требуется перезагрузка. Освобождение всего места ещё не подтверждено."
                elif native.get("unknown_paths"):
                    entry["message"] = "Пакеты удалены; проверка каталогов на диске недоступна."
                elif row["kind"] == "capability":
                    entry["message"] = "Дополнительный компонент удалён через DISM. Общее хранилище компонентов обслуживается Windows."
                else:
                    entry["message"] = "Установленные и подготовленные пакеты отсутствуют; известных оставшихся каталогов пакета не обнаружено."
        except Exception as exc:
            fresh = None
            for entry in record["entries"]:
                entry.update(status="unknown", message="Не удалось проверить результат: " + str(exc)[:1000])
        record["status"] = "finished"
        record["finished"] = now()
        atomic_json(path, record)
        return {"record": record, "inventory": fresh, "path": str(path)}
