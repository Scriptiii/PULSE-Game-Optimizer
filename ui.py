"""Russian desktop UI. System writes only follow an explicit preview/confirmation."""

from __future__ import annotations

import logging
import os
import sys
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, QThreadPool, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLineEdit, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QStackedWidget, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from . import __version__
from .branding import application_icon
from .catalog import BY_ID, TWEAKS
from .rules import POWER
from .cleanup import Cleaner, Scan, format_bytes
from .debloat import DebloatManager, DemoDebloat, WindowsDebloat
from .engine import Engine, atomic_json, now
from .memory import MemoryCleaner
from .programs import sort_program_entries
from .telemetry import Monitor, machine_info
from .startup import DemoStartup, StartupManager, WindowsStartup
from .theme import STYLE
from .widgets import (BLUE, GREEN, MUTED, ORANGE, Card, Hero, MetricCard, Pill,
                      ProgramRow, RemovalRow, Ring, Sparkline, StartupRow, TweakRow, TweakSection, TweakTile,
                      button, icon, icon_label, label)
from .windows import system_executable

log = logging.getLogger(__name__)


class JobSignals(QObject):
    finished = Signal(object)


class Job(QRunnable):
    def __init__(self, operation, callback, refresh_on_error=False):
        super().__init__()
        self.signals = JobSignals()
        self.operation = operation
        self.callback = callback
        self.refresh_on_error = refresh_on_error

    def run(self):
        try:
            result = self.operation()
            outcome = (self, result, None)
        except Exception as exc:
            log.exception("Background operation failed")
            outcome = (self, None, str(exc))
        self.signals.finished.emit(outcome)


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


@contextmanager
def layout_batch(*layouts):
    """Defer repeated geometry work while changing a group of rows."""
    enabled = [layout for layout in layouts if layout.isEnabled()]
    for layout in enabled:
        layout.setEnabled(False)
    try:
        yield
    finally:
        for layout in reversed(enabled):
            layout.setEnabled(True)


def table(headers: list[str]) -> QTableWidget:
    result = QTableWidget(0, len(headers))
    result.setHorizontalHeaderLabels(headers)
    result.verticalHeader().hide()
    result.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    result.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    result.setAlternatingRowColors(True)
    result.setShowGrid(False)
    result.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    result.setMinimumHeight(260)
    return result


def fill_table(widget: QTableWidget, rows: list[list[str]]):
    widget.setRowCount(len(rows))
    for index, row in enumerate(rows):
        widget.setRowHeight(index, 43)
        for column, text in enumerate(row):
            item = QTableWidgetItem(str(text))
            item.setToolTip(str(text))
            widget.setItem(index, column, item)


class ConfirmDialog(QDialog):
    def __init__(self, parent, title: str, text: str, action="Применить"):
        super().__init__(parent)
        self.setWindowTitle("PULSE · Подтверждение")
        self.setMinimumWidth(560)
        self.setMaximumWidth(700)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(27, 25, 27, 25)
        layout.setSpacing(18)
        layout.addWidget(label(title, 19, bold=True, wrap=True))
        # Large profiles must keep the confirmation buttons on screen.
        description = QTextEdit()
        description.setReadOnly(True)
        description.setPlainText(text)
        description.setStyleSheet("QTextEdit {font-family:'Segoe UI'; font-size:14px; color:#b6c1d3; background:#171d28; border:1px solid #344057; border-radius:8px; padding:12px;}")
        layout.addWidget(description, 1)
        self.resize(650, 520)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button("Отмена", self.reject))
        yes = button(action, self.accept, True)
        yes.setDefault(False)
        row.addWidget(yes)
        layout.addLayout(row)


class MainWindow(QMainWindow):
    PAGE_NAMES = ("Обзор", "Оптимизации", "Очистка", "Автозагрузка", "Восстановление", "О приложении")
    PAGE_ICONS = ("home", "sliders", "broom", "startup", "restore", "settings")

    def __init__(self, engine: Engine, cleaner: Cleaner, data_root: Path):
        super().__init__()
        self.engine = engine
        self.cleaner = cleaner
        self.data_root = data_root
        self.demo = engine.backend.demo
        self.memory_cleaner = MemoryCleaner(demo=self.demo)
        self.last_memory_clean = None
        self.startup_manager = StartupManager(DemoStartup() if self.demo else WindowsStartup(), data_root / "startup")
        self.startup_data = {}
        self.startup_rows = {}
        self.startup_loaded = False
        self.startup_refresh_pending = False
        self.debloat_manager = DebloatManager(DemoDebloat() if self.demo else WindowsDebloat(), data_root / "uninstall")
        self.debloat_data = {}
        self.debloat_rows = {}
        self.program_data = {}
        self.program_rows = {}
        self.program_render_queue = deque()
        self.program_render_timer = QTimer(self)
        self.program_render_timer.setInterval(10)
        self.program_render_timer.timeout.connect(self._render_program_batch)
        self.program_check_key = None
        self.program_check_auto = False
        self.cleanup_refresh_pending = False
        self.cleanup_loaded = False
        self.cleanup_scan_in_progress = False
        self.cleanup_generation = 0
        self.cleanup_parts = set()
        self.inventory_jobs = set()
        self.cleanup_snapshot = {"admin": False, "rows": [], "desktop": [], "hidden": 0, "errors": [], "desktop_errors": []}
        self.states = {}
        self.jobs = set()
        self.busy = False
        self.scan_result: Scan | None = None
        self.last_clean = None
        self.completion_message = ""
        self.monitor = Monitor()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.inventory_pool = QThreadPool(self)
        self.inventory_pool.setMaxThreadCount(1)
        self.component_pool = QThreadPool(self)
        self.component_pool.setMaxThreadCount(1)
        self.setWindowTitle("PULSE · Game Optimizer" + (" · ДЕМО" if self.demo else ""))
        self.setWindowIcon(application_icon())
        self.resize(1420, 970)
        self.setMinimumSize(1120, 700)
        self.setStyleSheet(STYLE)
        self.rows = {}
        self.checkboxes = {}
        self._build()
        self._load_cleanup_summary()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_telemetry)
        self.timer.start(1000)
        self.uninstall_timer = QTimer(self)
        self.uninstall_timer.timeout.connect(self.poll_program_uninstall)
        self.uninstall_timer.start(1500)
        QTimer.singleShot(0, self.refresh)
        QTimer.singleShot(50, self.update_telemetry)
        self._dark_titlebar()

    def _dark_titlebar(self):
        if sys.platform == "win32":
            import ctypes
            try:
                from ctypes import wintypes
                fn = ctypes.WinDLL("dwmapi").DwmSetWindowAttribute
                fn.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
                fn.restype = ctypes.c_long
                flag = ctypes.c_int(1)
                fn(int(self.winId()), 20, ctypes.byref(flag), ctypes.sizeof(flag))
            except (OSError, AttributeError):
                pass

    def _build(self):
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(root)
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(218)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(17, 27, 17, 22)
        side.setSpacing(8)
        brand = QHBoxLayout()
        brand.addWidget(icon_label("bolt", ORANGE, 32))
        brand.addWidget(label("PULSE", 24, bold=True))
        brand.addStretch()
        side.addLayout(brand)
        side.addWidget(label("GAME OPTIMIZER", 8, MUTED, True))
        side.addSpacing(28)
        side.addWidget(label("  ПРОСТРАНСТВО", 8, "#65728a", True))
        side.addSpacing(6)
        self.navigation = QButtonGroup(self)
        self.navigation.setExclusive(True)
        for index, (name, glyph) in enumerate(zip(self.PAGE_NAMES, self.PAGE_ICONS)):
            nav = QPushButton("  " + name)
            nav.setObjectName("Nav")
            nav.setIcon(icon(glyph, ORANGE if index == 0 else MUTED, 20))
            nav.setIconSize(QSize(20, 20))
            nav.setCheckable(True)
            nav.setCursor(Qt.CursorShape.PointingHandCursor)
            self.navigation.addButton(nav, index)
            side.addWidget(nav)
            if index == 4:
                side.addSpacing(20)
        self.navigation.button(0).setChecked(True)
        self.navigation.idClicked.connect(self.navigate)
        side.addStretch()
        safe = Card()
        safe_layout = QVBoxLayout(safe)
        safe_layout.setContentsMargins(14, 15, 14, 15)
        safe_layout.addWidget(icon_label("shield", GREEN, 22))
        safe_layout.addWidget(label("Всё под контролем", 10, bold=True))
        safe_layout.addWidget(label("Исходные настройки сохраняются до изменений.", 9, MUTED, wrap=True))
        side.addWidget(safe)
        side.addSpacing(13)
        side.addWidget(label("  LOCAL FIRST   /   v" + __version__, 8, "#637189", True))
        layout.addWidget(sidebar)

        content = QWidget()
        content.setObjectName("Content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 22, 28, 18)
        content_layout.setSpacing(14)
        top = QHBoxLayout()
        self.breadcrumb = label("РАБОЧЕЕ ПРОСТРАНСТВО   /   ОБЗОР", 8, MUTED, True)
        top.addWidget(self.breadcrumb)
        top.addStretch()
        self.connection = label("●  ДЕМО · настройки изолированы" if self.demo else "●  Локальное подключение", 9, ORANGE if self.demo else GREEN)
        top.addWidget(self.connection)
        content_layout.addLayout(top)
        if self.demo:
            banner = label("ДЕМОРЕЖИМ  ·  Изменения Windows и удаление файлов отключены. Мониторинг показывает этот ПК.", 9, ORANGE, wrap=True)
            content_layout.addWidget(banner)
        self.stack = QStackedWidget()
        for builder in (self._dashboard, self._optimizations, self._cleanup, self._startup, self._recovery, self._about):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 2, 7, 10)
            page_layout.setSpacing(18)
            builder(page_layout)
            page_layout.addStretch()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            self.stack.addWidget(scroll)
        content_layout.addWidget(self.stack, 1)
        content_layout.addWidget(self.optimization_actions)
        self.optimization_actions.hide()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        content_layout.addWidget(self.progress)
        bottom = QHBoxLayout()
        self.status = label("Подключение к Windows…", 9, MUTED)
        self.status.setWordWrap(True)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(label("Никаких обещаний FPS. Только твои настройки.", 8, "#68768e"))
        content_layout.addLayout(bottom)
        layout.addWidget(content, 1)

    def heading(self, layout, title, subtitle, action=None):
        row = QHBoxLayout()
        text = QVBoxLayout()
        text.setSpacing(7)
        text.addWidget(label(title, 26, bold=True))
        text.addWidget(label(subtitle, 10, MUTED, wrap=True))
        row.addLayout(text, 1)
        if action:
            row.addWidget(action, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(row)

    def _dashboard(self, layout):
        self.heading(layout, "Твой ПК. На максимуме.", "Настройки для игр, порядок в системе и всё важное на одном экране.",
                     button("Проверить", self.refresh, glyph="refresh"))
        columns = QHBoxLayout()
        columns.setSpacing(18)
        left = QVBoxLayout()
        left.setSpacing(15)
        left.addWidget(Hero(lambda: self.navigate(1)))
        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        self.applied_metric = MetricCard("Настроено", f"— / {len(TWEAKS)}", "проверка параметров", "sliders")
        self.cleaned_metric = MetricCard("Очищено", "—", "очистка ещё не запускалась", "broom", GREEN)
        self.backup_metric = MetricCard("Копии", "0", "для отката настроек", "restore", BLUE)
        for metric in (self.applied_metric, self.cleaned_metric, self.backup_metric):
            metrics.addWidget(metric, 1)
        left.addLayout(metrics)
        row = QHBoxLayout()
        row.addWidget(label("Твои оптимизации", 14, bold=True))
        row.addStretch()
        all_button = button("Все настройки", lambda: self.navigate(1))
        all_button.setMinimumHeight(31)
        row.addWidget(all_button)
        left.addLayout(row)
        for tweak in TWEAKS[:3]:
            row = TweakRow(tweak)
            row.requested.connect(self.toggle_tweak)
            row.details.connect(self.tweak_details)
            self.rows.setdefault(tweak.id, []).append(row)
            left.addWidget(row)
        left.addStretch()
        columns.addLayout(left, 7)
        right = QVBoxLayout()
        right.setSpacing(15)
        monitor = Card()
        monitor.setMinimumWidth(270)
        m = QVBoxLayout(monitor)
        m.setContentsMargins(20, 20, 20, 20)
        m.setSpacing(9)
        top = QHBoxLayout()
        top.addWidget(label("Нагрузка системы", 12, bold=True))
        top.addStretch()
        top.addWidget(label("● LIVE", 8, GREEN, True))
        m.addLayout(top)
        m.addSpacing(5)
        cpu_row = QHBoxLayout()
        cpu_row.addWidget(icon_label("cpu", MUTED, 17))
        cpu_row.addWidget(label("CPU", 10, MUTED, True))
        cpu_row.addStretch()
        self.cpu_value = label("—", 23, bold=True)
        cpu_row.addWidget(self.cpu_value)
        m.addLayout(cpu_row)
        self.sparkline = Sparkline()
        m.addWidget(self.sparkline)
        time_row = QHBoxLayout()
        time_row.addWidget(label("45 секунд", 8, "#65738d"))
        time_row.addStretch()
        time_row.addWidget(label("сейчас", 8, "#65738d"))
        m.addLayout(time_row)
        self.ram_ring = Ring(GREEN)
        self.disk_ring = Ring(BLUE)
        for ring, title, attr in ((self.ram_ring, "Оперативная память", "ram_info"),
                                  (self.disk_ring, "Системный диск", "disk_info")):
            row = QHBoxLayout()
            row.setSpacing(13)
            row.addWidget(ring)
            text = QVBoxLayout()
            text.setSpacing(4)
            text.addWidget(label(title, 10, bold=True))
            value = label("Ожидание данных", 9, MUTED, wrap=True)
            setattr(self, attr, value)
            text.addWidget(value)
            row.addLayout(text, 1)
            m.addLayout(row)
            if attr == "ram_info":
                self.memory_clean_button = button("Очистить ОЗУ", self.clean_memory, True, "broom")
                self.memory_clean_button.setToolTip("Рабочие наборы процессов, системный файловый кеш и Standby — как в WinMemoryCleaner. Нужны права администратора.")
                m.addWidget(self.memory_clean_button)
                self.memory_clean_note = label("Лучше перед запуском игры: нужные данные затем загрузятся в память снова.", 9, MUTED, wrap=True)
                m.addWidget(self.memory_clean_note)
        right.addWidget(monitor)
        hardware = Card()
        hardware_layout = QVBoxLayout(hardware)
        hardware_layout.setContentsMargins(20, 17, 20, 17)
        hardware_layout.setSpacing(8)
        hardware_layout.addWidget(label("ТВОЯ КОНФИГУРАЦИЯ", 8, MUTED, True))
        self.hardware_cpu = label("Определяем процессор…", 10, bold=True, wrap=True)
        self.hardware_gpu = label("Определяем видеокарту…", 9, MUTED, wrap=True)
        self.hardware_os = label("Windows", 9, MUTED, wrap=True)
        hardware_layout.addWidget(self.hardware_cpu)
        hardware_layout.addWidget(self.hardware_gpu)
        hardware_layout.addWidget(self.hardware_os)
        right.addWidget(hardware)
        shortcut = button("Автозагрузка приложений", lambda: self.navigate(3), glyph="startup")
        right.addWidget(shortcut)
        shortcut = button("Копии и восстановление", lambda: self.navigate(4), glyph="restore")
        right.addWidget(shortcut)
        right.addStretch()
        columns.addLayout(right, 3)
        layout.addLayout(columns)

    def clean_memory(self):
        if self.busy:
            return
        self.memory_clean_note.setText("Очищаем рабочие наборы, системный кеш и Standby…")
        self.memory_clean_note.setToolTip("")
        self.submit("Очищаем ОЗУ…", self.memory_cleaner.clean, self._memory_cleaned,
                    self._memory_clean_failed, invalidate_cleanup=False)

    def _memory_cleaned(self, result):
        self.last_memory_clean = result
        done = sum(step["ok"] for step in result["steps"])
        total = len(result["steps"])
        title = "Очистка завершена" if done == total else "Очистка выполнена частично" if done else "Очистить ОЗУ не удалось"
        prefix = "ДЕМО · пример. " if result["demo"] else ""
        text = f"{prefix}{title} · {done}/{total}.\nДоступно: {format_bytes(result['before'])} → {format_bytes(result['after'])}."
        self.memory_clean_note.setText(text)
        details = "\n".join(step["title"] + ": " + ("выполнено" if step["ok"] else step["error"]) for step in result["steps"])
        self.memory_clean_note.setToolTip(text + "\n\n" + details)
        self.status.setText(prefix + title + ". Подробности — наведи курсор на результат под кнопкой.")
        log.info("RAM cleanup: demo=%s; available %s -> %s; %s", result["demo"], result["before"], result["after"], details)
        self.update_telemetry()

    def _memory_clean_failed(self):
        self.memory_clean_note.setText("Очистка ОЗУ не завершена. Причина указана в сообщении об ошибке.")

    def _optimizations(self, layout):
        self.heading(layout, "Настройки производительности", f"{len(TWEAKS)} настроек · выбирай целые разделы или отдельные плитки.")
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Найти настройку…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_tweaks)
        bar.addWidget(self.search, 1)
        layout.addLayout(bar)
        actions = Card()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(17, 12, 17, 12)
        self.selection_label = label("Выбрано: 0", 11, bold=True)
        actions_layout.addWidget(self.selection_label)
        self.available_label = label("Проверка доступности…", 9, MUTED)
        actions_layout.addWidget(self.available_label)
        actions_layout.addStretch()
        actions_layout.addWidget(button("Наверх", self.scroll_tweaks_to_top))
        actions_layout.addWidget(button("Снять выбор", self.clear_selection))
        self.apply_button = button("Применить выбранное", self.apply_selected, True, "bolt")
        actions_layout.addWidget(self.apply_button)
        self.optimization_actions = actions
        self.filter_count = label("", 9, MUTED)
        layout.addWidget(self.filter_count)
        self.selection_cards = {}
        self.tweak_sections = []
        appearance_first = ("transparency", "window_animation", "animations")
        appearance = sorted((t for t in TWEAKS if t.category == "Интерфейс"),
                            key=lambda t: appearance_first.index(t.id) if t.id in appearance_first else len(appearance_first))
        groups = (
            ("power", "Электропитание", "План питания, процессор, PCIe, Wi-Fi и таймеры от сети.", "bolt", ORANGE,
             [t for t in TWEAKS if t.category == "Питание"]),
            ("gaming", "Игровые функции", "Игровой режим, запись моментов и вызов панели с контроллера.", "gamepad", GREEN,
             [t for t in TWEAKS if t.category == "Игры"]),
            ("background", "Фоновые функции Windows", "Виджеты, поиск, уведомления и системные рекомендации.", "sliders", BLUE,
             [t for t in TWEAKS if t.category == "Фон" and not t.id.startswith("edge_")]),
            ("browser", "Браузер Microsoft Edge", "Предзагрузка и работа браузера после закрытия окна.", "startup", GREEN,
             [t for t in TWEAKS if t.id.startswith("edge_")]),
            ("explorer", "Проводник Windows", "Миниатюры, предпросмотр, подсказки и история оболочки.", "folder", BLUE,
             [t for t in TWEAKS if t.category == "Проводник"]),
            ("appearance", "Внешний вид Windows", "Прозрачность, анимации, тени и отзывчивость стандартных окон.", "spark", BLUE, appearance),
            ("input", "Мышь и клавиатура", "Поведение указателя, повтор клавиш и сочетания специальных функций.", "mouse", ORANGE,
             [t for t in TWEAKS if t.category == "Управление"]),
        )
        for key, title, description, glyph, color, tweaks in groups:
            tiles = []
            for tweak in tweaks:
                tile = TweakTile(tweak, color)
                tile.requested.connect(self.toggle_tweak)
                tile.details.connect(self.tweak_details)
                tile.selection.toggled.connect(self.update_selection)
                self.checkboxes[tweak.id] = tile.selection
                self.rows.setdefault(tweak.id, []).append(tile)
                self.selection_cards[tweak.id] = tile
                tiles.append(tile)
            section = TweakSection(key, title, description, glyph, color, tiles)
            section.selection_requested.connect(self.select_section)
            self.tweak_sections.append(section)
            layout.addWidget(section)
        assert len(self.selection_cards) == len(TWEAKS)
        self.no_results = label("Подходящих настроек нет. Измените поисковый запрос.", 12, MUTED, wrap=True)
        self.no_results.hide()
        layout.addWidget(self.no_results)
        layout.addWidget(label("Выбор раздела отмечает доступные параметры. Проверь список перед применением; эффект настроек зависит от игры и оборудования.", 9, MUTED, wrap=True))
        self.filter_tweaks()

    def _cleanup(self, layout):
        self.heading(layout, "Больше места для важного", "Временные файлы, компоненты Windows и установленные программы EXE / MSI.",
                     button("Обновить список приложений", self.scan_packages, glyph="search"))
        shortcuts = QHBoxLayout()
        for key, title in (("components", "Компоненты Windows ↓"), ("desktop", "Программы EXE / MSI ↓")):
            shortcuts.addWidget(button(title, lambda checked=False, group=key: self.scroll_cleanup(group)))
        shortcuts.addStretch()
        layout.addLayout(shortcuts)
        layout.addWidget(label("Временные файлы", 18, bold=True))
        info = Card()
        info_layout = QHBoxLayout(info)
        info_layout.setContentsMargins(25, 22, 25, 22)
        info_layout.addWidget(icon_label("broom", ORANGE, 42))
        text = QVBoxLayout()
        self.clean_size = label("Готов к анализу", 25, bold=True)
        self.clean_count = label("Только временные файлы старше 48 часов", 10, MUTED, wrap=True)
        text.addWidget(self.clean_size)
        text.addWidget(self.clean_count)
        info_layout.addLayout(text, 1)
        info_layout.addWidget(button("Анализировать", self.scan_temp, True, "search"))
        layout.addWidget(info)
        self.temp_user = QCheckBox("Временные файлы текущего пользователя")
        self.temp_user.setChecked(True)
        self.temp_system = QCheckBox("Временные файлы Windows · защищённые файлы будут пропущены")
        for check in (self.temp_user, self.temp_system):
            check.toggled.connect(self.invalidate_scan)
            layout.addWidget(check)
        self.cleanup_note = label("Загрузки, корзина, браузеры и кеш шейдеров не входят в очистку.", 10, MUTED, wrap=True)
        layout.addWidget(self.cleanup_note)
        self.files_table = table(["Временный файл", "Размер"])
        self.files_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.files_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.files_table)
        row = QHBoxLayout()
        self.clean_report = label("Список появится после анализа.", 9, MUTED, wrap=True)
        row.addWidget(self.clean_report, 1)
        self.clean_button = button("Очистить найденное", self.clean_temp, True, "broom")
        self.clean_button.setEnabled(False)
        row.addWidget(self.clean_button)
        layout.addLayout(row)
        layout.addWidget(button("Открыть раздел «Память» в Windows", lambda: self.open_settings("ms-settings:storagesense"), glyph="external"), 0, Qt.AlignmentFlag.AlignLeft)
        self.debloat_note = label("Программы EXE / MSI и необязательные компоненты Windows загружаются при первом открытии раздела. Для повторного поиска нажми «Обновить».", 10, MUTED, wrap=True)
        layout.addWidget(self.debloat_note)
        self.debloat_search = QLineEdit()
        self.debloat_search.setPlaceholderText("Найти приложение, программу или компонент…")
        self.debloat_search.setClearButtonEnabled(True)
        self.debloat_search.textChanged.connect(self.filter_packages)
        layout.addWidget(self.debloat_search)
        self.debloat_sections = {}
        for key, title, description in (
            ("components", "Компоненты Windows", "Widgets, новости, Copilot и другие необязательные приложения, а также дополнительные компоненты. Выбирай только то, чем не пользуешься."),
        ):
            group = QWidget()
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(0, 16, 0, 0)
            group_layout.setSpacing(10)
            header = QHBoxLayout()
            header.addWidget(label(title, 18, bold=True), 1)
            select = button("Выбрать раздел", lambda checked=False, section=key: self.select_packages(section))
            select.setEnabled(False)
            header.addWidget(select)
            group_layout.addLayout(header)
            group_layout.addWidget(label(description, 10, MUTED, wrap=True))
            rows = QVBoxLayout()
            rows.setSpacing(8)
            group_layout.addLayout(rows)
            empty = label("Список появится после проверки Windows.", 10, MUTED, wrap=True)
            group_layout.addWidget(empty)
            footer = QHBoxLayout()
            count = label("Выбрано: 0", 10, MUTED)
            footer.addWidget(count, 1)
            remove = button("Удалить выбранное", lambda checked=False, section=key: self.remove_packages(section), True, "broom")
            remove.setEnabled(False)
            footer.addWidget(remove)
            group_layout.addLayout(footer)
            layout.addWidget(group)
            self.debloat_sections[key] = {"widget": group, "layout": rows, "select": select, "remove": remove, "count": count, "empty": empty}
        self.program_section = QWidget()
        programs = QVBoxLayout(self.program_section)
        programs.setContentsMargins(0, 16, 0, 0)
        programs.setSpacing(10)
        header = QHBoxLayout()
        header.addWidget(label("Установленные программы · EXE / MSI", 18, bold=True, wrap=True), 1)
        self.program_sort = QComboBox()
        self.program_sort.setAccessibleName("Сортировка установленных программ")
        self.program_sort.addItem("Название: А–Я", "name")
        self.program_sort.addItem("Размер: по убыванию", "size_desc")
        self.program_sort.addItem("Размер: по возрастанию", "size_asc")
        self.program_sort.setMaximumWidth(230)
        self.program_sort.setToolTip("Сортировка по названию или размеру. Программы без указанного размера — в конце списка.")
        self.program_sort.currentIndexChanged.connect(self.sort_program_list)
        header.addWidget(self.program_sort)
        self.program_count = Pill("0", BLUE)
        header.addWidget(self.program_count)
        header.addWidget(button("Обновить", self.scan_packages, glyph="refresh"))
        programs.addLayout(header)
        programs.addWidget(label("Системные и пользовательские установки, 32 и 64 бит. Штатный мастер удаляет программу; после его завершения PULSE проверяет запись установки и всю указанную папку программы, включая вложенные файлы и каталоги.", 10, MUTED, wrap=True))
        self.program_layout = QVBoxLayout()
        self.program_layout.setSpacing(8)
        programs.addLayout(self.program_layout)
        self.program_empty = label("Список появится после проверки Windows.", 10, MUTED, wrap=True)
        programs.addWidget(self.program_empty)
        layout.addWidget(self.program_section)
        layout.addWidget(label("Программы ищутся по регистрации установки Windows, включая загруженные профили других пользователей. Portable-приложения без регистрации и программы в незагруженных профилях могут отсутствовать. Остатки после EXE / MSI зависят от мастера удаления; PULSE не стирает произвольные папки программы.", 9, MUTED, wrap=True))
        layout.addWidget(label("Удаление выполняется штатными средствами с проверкой результата. Занятые или общие файлы могут оставаться до перезагрузки. Удалённые программы и их данные не восстанавливаются через раздел «Восстановление»; повторная установка выполняется из официального источника программы.", 9, MUTED, wrap=True))
        layout.addWidget(label("В Windows 10 встроенная лента «Новости и интересы» не является отдельным удаляемым Appx-пакетом. Её отключение доступно в «Оптимизациях»; здесь оно не выдаётся за удаление.", 9, MUTED, wrap=True))
        self.debloat_report = QTextEdit()
        self.debloat_report.setReadOnly(True)
        self.debloat_report.setMinimumHeight(150)
        self.debloat_report.setMaximumHeight(260)
        self.debloat_report.setPlaceholderText("Здесь появится отчёт об удалении каждого пункта.")
        self.debloat_report.hide()
        layout.addWidget(self.debloat_report)
        self.program_check_button = button("Проверить удаление ещё раз", self.check_program_uninstall, glyph="search")
        self.program_check_button.hide()
        layout.addWidget(self.program_check_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(button("Журналы удаления", lambda: self.open_folder(self.data_root / "uninstall"), glyph="folder"), 0, Qt.AlignmentFlag.AlignLeft)

    def scroll_cleanup(self, key):
        widget = self.program_section if key == "desktop" else self.debloat_sections[key]["widget"]
        self.stack.widget(2).verticalScrollBar().setValue(widget.y() - 8)

    def scan_packages(self):
        if self.busy:
            if not self.cleanup_scan_in_progress and self.stack.currentIndex() == 2:
                self.cleanup_refresh_pending = True
            return
        if self.cleanup_scan_in_progress:
            return
        self.cleanup_refresh_pending = False
        self.cleanup_scan_in_progress = True
        self.cleanup_generation += 1
        self.cleanup_parts = {"desktop", "components"}
        for row in self.debloat_rows.values():
            row.selection.setEnabled(False)
        self.update_package_selection()
        self._update_cleanup_note()
        self.progress.show()
        self.status.setText("Читаем программы EXE / MSI; компоненты Windows проверяются параллельно…")
        for kind, operation in (("desktop", self.debloat_manager.inspect_desktop),
                                ("components", self.debloat_manager.inspect_components)):
            def read_if_current(operation=operation, generation=self.cleanup_generation):
                return operation() if generation == self.cleanup_generation else None
            job = Job(read_if_current, None)
            job.inventory_kind = kind
            job.inventory_generation = self.cleanup_generation
            job.signals.finished.connect(self._inventory_finished)
            self.inventory_jobs.add(job)
            (self.component_pool if kind == "components" else self.inventory_pool).start(job)

    @Slot(object)
    def _inventory_finished(self, outcome):
        job, result, error = outcome
        self.inventory_jobs.discard(job)
        if job.inventory_generation != self.cleanup_generation:
            return  # A read started before a mutation cannot overwrite newer data.
        kind = job.inventory_kind
        self.cleanup_parts.discard(kind)
        if kind == "desktop":
            result = result if not error else {"desktop": [], "desktop_errors": [error]}
            self.cleanup_snapshot.update({k: result[k] for k in ("desktop", "desktop_errors", "admin") if k in result})
            self._set_programs(result["desktop"])
        else:
            result = result if not error else {"rows": [], "errors": [error], "hidden": 0}
            self.cleanup_snapshot.update({k: result[k] for k in ("rows", "errors", "hidden", "admin") if k in result})
            self._set_components(result["rows"])
        self.cleanup_scan_in_progress = bool(self.cleanup_parts)
        if not self.cleanup_scan_in_progress:
            self.cleanup_loaded = True
        self.filter_packages()
        self._update_cleanup_note()
        self.progress.setVisible(self.busy or self.cleanup_scan_in_progress)
        if not self.busy:
            self.status.setText("Список EXE / MSI готов · компоненты Windows ещё проверяются…" if self.cleanup_parts == {"components"} else
                                "Проверка списка завершена · подробности в разделе «Очистка»" if not self.cleanup_parts else
                                "Читаем список программ EXE / MSI…")

    def _invalidate_cleanup_inventory(self):
        if self.cleanup_scan_in_progress:
            self.cleanup_generation += 1
            self.cleanup_parts.clear()
            self.cleanup_scan_in_progress = False
            self.cleanup_loaded = False
            self.cleanup_refresh_pending = self.stack.currentIndex() == 2

    def refresh_cleanup_when_idle(self):
        if self.cleanup_refresh_pending and not self.busy and self.stack.currentIndex() == 2:
            self.scan_packages()

    def _packages_scanned(self, result):
        self.cleanup_refresh_pending = False
        self.cleanup_scan_in_progress = False
        self.cleanup_loaded = True
        self.cleanup_parts.clear()
        self.cleanup_snapshot = dict(result)
        self._set_programs(result["desktop"])
        self._set_components(result["rows"])
        self._update_cleanup_note()
        self.filter_packages()

    def _set_programs(self, entries):
        self.program_render_timer.stop()
        self.program_render_queue.clear()
        self.program_data = {r["id"]: r for r in entries}
        self.debloat_manager.programs = dict(self.program_data)
        with self._cleanup_layout_batch():
            for key, row in list(self.program_rows.items()):
                if self.program_data.get(key) != row.entry:
                    self.program_layout.removeWidget(row)
                    row.hide()
                    row.deleteLater()
                    del self.program_rows[key]
        self.sort_program_list()

    def sort_program_list(self, *_):
        self.program_render_timer.stop()
        self.program_render_queue.clear()
        entries = sort_program_entries(self.program_data.values(), self.program_sort.currentData())
        self.program_render_queue.extend(enumerate(entries))
        if self.program_render_queue:
            self.program_render_timer.start()

    def _cleanup_layout_batch(self):
        layouts = [self.stack.widget(2).widget().layout(), self.program_section.layout(), self.program_layout]
        for section in self.debloat_sections.values():
            layouts.extend((section["widget"].layout(), section["layout"]))
        return layout_batch(*layouts)

    def _render_program_batch(self):
        # Keep painting, navigation and search responsive even with hundreds of apps.
        deadline = time.perf_counter() + 0.016
        query = self.debloat_search.text().strip().casefold()
        with self._cleanup_layout_batch():
            while self.program_render_queue:
                index, entry = self.program_render_queue.popleft()
                row = self.program_rows.get(entry["id"])
                if row is None:
                    row = ProgramRow(entry)
                    row.requested.connect(self.remove_program)
                    self.program_rows[entry["id"]] = row
                row.remove.setEnabled(entry["available"] and not self.program_check_auto)
                item = self.program_layout.itemAt(index)
                if item is None or item.widget() is not row:
                    self.program_layout.insertWidget(index, row)
                row.setVisible(self._program_matches(entry, query))
                if time.perf_counter() >= deadline:
                    break
        if not self.program_render_queue:
            self.program_render_timer.stop()

    @staticmethod
    def _program_matches(entry, query):
        return not query or query in (entry["title"] + " " + entry["publisher"]).casefold()

    def _set_components(self, entries):
        self.debloat_data = {r["id"]: r for r in entries}
        self.debloat_manager.rows = dict(self.debloat_data)
        previous = self.debloat_rows
        self.debloat_rows = {}
        with self._cleanup_layout_batch():
            for key, row in previous.items():
                if self.debloat_data.get(key) != row.entry:
                    self.debloat_sections[row.entry["group"]]["layout"].removeWidget(row)
                    row.hide()
                    row.deleteLater()
            positions = dict.fromkeys(self.debloat_sections, 0)
            for entry in entries:
                row = previous.get(entry["id"])
                if row is None or row.entry != entry:
                    row = RemovalRow(entry)
                    row.selection.toggled.connect(self.update_package_selection)
                row.selection.setEnabled(entry["available"])
                self.debloat_rows[entry["id"]] = row
                layout = self.debloat_sections[entry["group"]]["layout"]
                index = positions[entry["group"]]
                item = layout.itemAt(index)
                if item is None or item.widget() is not row:
                    layout.insertWidget(index, row)
                positions[entry["group"]] += 1

    def _update_cleanup_note(self):
        result = self.cleanup_snapshot
        note = ("Все пользователи ПК · установленные и подготовленные пакеты." if result["admin"] else
                "Показаны приложения текущего пользователя. Для удаления со всего ПК нужны права администратора.")
        if self.demo:
            note = "ДЕМО · список вымышленный; удаление меняет только данные в памяти."
        note += f" Найдено: {len(result['rows'])} необязательных компонентов, {len(result['desktop'])} программ EXE / MSI."
        if "desktop" in self.cleanup_parts:
            note += " Обновляем программы EXE / MSI…"
        if "components" in self.cleanup_parts:
            note += " Компоненты Windows проверяются в фоне; готовым списком программ уже можно пользоваться."
        if result["hidden"]:
            note += f" Нераспознанных записей пакетов: {result['hidden']}."
        if result["errors"]:
            note += " Ошибки чтения: " + "; ".join(result["errors"])
        if result["desktop_errors"]:
            note += " Ошибки чтения программ: " + "; ".join(result["desktop_errors"])
        self.debloat_note.setText(note)

    def filter_packages(self):
        text = self.debloat_search.text().strip().casefold()
        with self._cleanup_layout_batch():
            for row in self.debloat_rows.values():
                row.setVisible(not text or text in (row.entry["title"] + " " + row.entry["name"]).casefold())
            for row in self.program_rows.values():
                row.setVisible(self._program_matches(row.entry, text))
        shown_programs = sum(self._program_matches(entry, text) for entry in self.program_data.values())
        self.program_count.setText(f"{shown_programs} / {len(self.program_data)}")
        self.program_empty.setVisible(not shown_programs)
        self.program_empty.setText("Программы не найдены. Измени поисковый запрос или обнови список.")
        for key, section in self.debloat_sections.items():
            shown = sum(r.entry["group"] == key and not r.isHidden() for r in self.debloat_rows.values())
            section["empty"].setVisible(not shown)
            section["empty"].setText("Ничего не найдено. Список содержит только обнаруженные в Windows пункты.")
        self.update_package_selection()

    def update_package_selection(self):
        for key, section in self.debloat_sections.items():
            eligible = [r for r in self.debloat_rows.values() if r.entry["group"] == key and not r.isHidden() and r.selection.isEnabled()]
            selected = sum(r.selection.isChecked() for r in eligible)
            total_selected = sum(r.selection.isChecked() for r in self.debloat_rows.values() if r.entry["group"] == key)
            total = sum(r.entry["group"] == key for r in self.debloat_rows.values())
            section["count"].setText(f"Найдено: {total} · Выбрано: {total_selected}")
            section["select"].setEnabled(bool(eligible))
            section["select"].setText("Снять выбор" if eligible and selected == len(eligible) else "Выбрать найденные" if self.debloat_search.text().strip() else "Выбрать раздел")
            section["remove"].setEnabled(bool(total_selected) and "components" not in self.cleanup_parts)

    def select_packages(self, key):
        eligible = [r for r in self.debloat_rows.values() if r.entry["group"] == key and not r.isHidden() and r.selection.isEnabled()]
        checked = not all(r.selection.isChecked() for r in eligible)
        for row in eligible:
            row.selection.setChecked(checked)

    def remove_packages(self, key):
        ids = [k for k, row in self.debloat_rows.items() if row.entry["group"] == key and row.selection.isChecked()]
        if not ids:
            return
        text = "Демонстрация, Windows не изменяется.\n\n" if self.demo else "Удаление для ВСЕХ пользователей компьютера.\n\n"
        text += "Будут удалены выбранные приложения и их локальные данные, а также подготовленные пакеты для новых учётных записей. Дополнительные компоненты удаляются через DISM.\n\n"
        for item in ids:
            entry = self.debloat_data[item]
            text += f"• {entry['title']}\n{entry['name']}\n{entry['description']}\n"
            if entry["kind"] == "appx":
                text += "Пакеты: " + ", ".join(p["full"] for p in entry["packages"]) + "\nПодготовленные: " + ", ".join(entry["provisioned"]) + "\n"
            text += "\n"
        text += "Проверь сохранность файлов, писем и игровых сохранений. Автоматического отката и резервной копии приложений нет. Для возврата потребуется повторная установка из Store или «Дополнительных компонентов» Windows; локальные данные это не вернёт.\n\nЗанятые/общие файлы может освободить сама Windows после перезагрузки. PULSE не удаляет вручную системные каталоги и не обещает безвозвратное стирание всех следов."
        if ConfirmDialog(self, "Удалить выбранное с компьютера?", text, "Удалить" if not self.demo else "Проверить в демо").exec() != QDialog.DialogCode.Accepted:
            return
        self.submit("Удаляем выбранные пакеты и проверяем состояние. Дождись завершения…",
                    lambda: self.debloat_manager.remove(ids), self._packages_removed, refresh_on_error=self.scan_packages)

    def remove_program(self, key):
        entry = self.program_data.get(key)
        if entry is None:
            self.status.setText("Список программ изменился. Выбери программу ещё раз.")
            return
        text = ("ДЕМО · Windows не изменяется.\n\n" if self.demo else "")
        text += f"{entry['title']}\nВерсия: {entry['version']}\nИздатель: {entry['publisher']}\n\n"
        text += "Откроется штатный мастер удаления выбранной программы. После его завершения PULSE проверит запись установки и зарегистрированную папку программы, включая вложенные файлы и каталоги.\n\n"
        text += "Папка из записи установки: " + (entry.get("location") or "не указана установщиком; полнота удаления файлов не может быть подтверждена") + "\n\n"
        text += "Проверь нужные файлы и сохранения. Резервные копии настроек PULSE не возвращают удалённые программы и их данные. Общие библиотеки и личные данные вне папки установки автоматически не стираются. Если папка останется, она появится в отчёте как остаток."
        if ConfirmDialog(self, "Открыть мастер удаления?", text, "Открыть мастер" if not self.demo else "Проверить в демо").exec() != QDialog.DialogCode.Accepted:
            return
        if self.program_data.get(key) != entry:
            self.status.setText("Сведения о программе обновились. Проверь их и повтори удаление.")
            return
        self.submit("Открываем штатный мастер удаления…", lambda: self.debloat_manager.launch_program(key), self._program_uninstaller_started)

    def _program_uninstaller_started(self, result):
        self.program_check_key = result["id"]
        self.program_check_auto = True
        self.program_check_button.show()
        for row in self.program_rows.values():
            row.remove.setEnabled(False)
        self.debloat_report.setPlainText(result["message"])
        self.debloat_report.show()
        self.status.setText(result["message"])
        self.poll_program_uninstall()

    def poll_program_uninstall(self):
        if not self.program_check_auto or self.busy or not self.program_check_key:
            return
        if not self.debloat_manager.program_running(self.program_check_key):
            self.check_program_uninstall()

    def check_program_uninstall(self):
        if not self.program_check_key or self.busy:
            return
        self.program_check_auto = False  # An error dialog must not trigger repeated automatic checks.
        self.submit("Проверяем завершение удаления, регистрацию и папку программы…",
                    lambda: self.debloat_manager.check_program(self.program_check_key), self._program_uninstall_checked)

    def _program_uninstall_checked(self, result):
        self.debloat_report.setPlainText(result["message"])
        self.debloat_report.show()
        self.status.setText("Мастер удаления ещё работает." if result["status"] == "running" else "Проверка удаления завершена. Результат и остатки — в отчёте.")
        if result["status"] == "running":
            self.program_check_auto = True
        else:
            self.program_check_auto = False
            self.scan_packages()

    def _packages_removed(self, result):
        if result["inventory"] is not None:
            self._packages_scanned(result["inventory"])
        else:
            self.debloat_data.clear()
            self.debloat_rows.clear()
            for section in self.debloat_sections.values():
                clear_layout(section["layout"])
            self.debloat_note.setText("Результат не удалось прочитать. Обнови список приложений для повторной проверки.")
            self.filter_packages()
        lines = ["ДЕМО · Windows не изменялась." if self.demo else "Результат удаления:"]
        for entry in result["record"]["entries"]:
            lines += ["", entry["title"], entry["message"]]
            if entry.get("error"):
                lines.append(entry["error"])
            lines += entry.get("native", {}).get("errors", [])
        lines += ["", "Журнал: " + result["path"]]
        self.debloat_report.setPlainText("\n".join(lines))
        self.debloat_report.show()
        self.stack.widget(2).ensureWidgetVisible(self.debloat_report, 0, 12)

    def _startup(self, layout):
        self.heading(layout, "Автозагрузка", "Приложения и службы при включении компьютера. Управляй запуском тумблером справа.",
                     button("Обновить", self.refresh_startup, glyph="refresh"))
        row = QHBoxLayout()
        self.startup_search = QLineEdit()
        self.startup_search.setPlaceholderText("Найти приложение или службу…")
        self.startup_search.setClearButtonEnabled(True)
        self.startup_search.textChanged.connect(self.filter_startup)
        row.addWidget(self.startup_search, 1)
        self.startup_restore_button = button("Отменить мои изменения", self.restore_startup, glyph="restore")
        self.startup_restore_button.setEnabled(False)
        row.addWidget(self.startup_restore_button)
        layout.addLayout(row)
        layout.addWidget(label("Тумблер меняет автозапуск. Работающие приложения и службы продолжат текущую работу до закрытия или перезагрузки.", 10, MUTED, wrap=True))
        self.startup_note = label("Открой раздел, чтобы прочитать автозагрузку.", 9, MUTED, wrap=True)
        layout.addWidget(self.startup_note)
        self.startup_sections = {}
        for kind, title in (("app", "Приложения"), ("service", "Службы")):
            group = QWidget()
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(0, 8, 0, 0)
            group_layout.setSpacing(9)
            heading = QHBoxLayout()
            heading.addWidget(label(title, 18, bold=True))
            heading.addStretch()
            count = Pill("0", BLUE if kind == "app" else ORANGE)
            heading.addWidget(count)
            group_layout.addLayout(heading)
            rows = QVBoxLayout()
            rows.setSpacing(8)
            group_layout.addLayout(rows)
            layout.addWidget(group)
            self.startup_sections[kind] = {"widget": group, "layout": rows, "count": count}
        self.startup_empty = label("По запросу ничего не найдено.", 12, MUTED)
        self.startup_empty.hide()
        layout.addWidget(self.startup_empty)

    def _recovery(self, layout):
        self.heading(layout, "Всегда можно вернуться", "Для каждой настройки сохранено её точное исходное значение, включая отсутствие параметра.",
                     button("Проверить", self.refresh, glyph="refresh"))
        layout.addWidget(label("Копии хранят только настройки PULSE. Они не заменяют точку восстановления Windows и не возвращают удалённые файлы, приложения и компоненты.", 10, MUTED, wrap=True))
        self.history_layout = QVBoxLayout()
        self.history_layout.setSpacing(13)
        layout.addLayout(self.history_layout)
        row = QHBoxLayout()
        row.addWidget(button("Открыть папку копий", lambda: self.open_local(self.engine.directory), glyph="folder"))
        row.addWidget(button("Защита системы Windows", self.system_protection, glyph="shield"))
        row.addStretch()
        layout.addLayout(row)

    def _about(self, layout):
        self.heading(layout, "Прозрачная оптимизация", "PULSE / Game Optimizer · Python + Qt · версия " + __version__)
        card = Card()
        inner = QVBoxLayout(card)
        inner.setContentsMargins(26, 24, 26, 24)
        inner.setSpacing(14)
        inner.addWidget(label("Твой компьютер. Твои правила.", 20, bold=True))
        inner.addWidget(label("PULSE работает локально: без аккаунта, подписки, телеметрии и фонового сервиса. "
                              "Мониторинг использует данные этого компьютера. Программа не измеряет FPS в играх.", 11, "#bac4d6", wrap=True))
        inner.addWidget(label("Что делает приложение", 13, bold=True))
        inner.addWidget(label(f"•  {len(TWEAKS)} настроек: игры, питание, фон, интерфейс, управление и Проводник.\n"
                              "•  Выбор целых разделов, поиск, проверка версии Windows и прав.\n"
                              "•  Анализ временных файлов старше 48 часов с предварительным списком.\n"
                              "•  Удаление программ EXE / MSI с проверкой папки и остатков.\n"
                              "•  Проверка результата и восстановление сохранённых настроек.", 11, MUTED, wrap=True))
        inner.addWidget(label("Результат зависит от игры, драйверов и оборудования. "
                              "Настройки мыши меняют управление, очистка освобождает место, а повышенная производительность может увеличить нагрев. "
                              "Сравнивайте FPS и время кадра в одной и той же сцене игры до и после изменений.", 11, MUTED, wrap=True))
        row = QHBoxLayout()
        row.addWidget(button("Рекомендации Microsoft", lambda: self.open_url("https://support.microsoft.com/en-us/windows/experience/performance-optimization/tips-to-improve-pc-performance-in-windows"), glyph="external"))
        row.addWidget(button("Журнал приложения", self.show_log, glyph="info"))
        row.addStretch()
        inner.addLayout(row)
        layout.addWidget(card)
        layout.addWidget(label("Данные приложения: " + str(self.data_root), 10, MUTED, wrap=True))
        layout.addWidget(label("Независимый проект, вдохновлённый интерфейсом Hone. Не связан с Hone и Microsoft.", 9, MUTED, wrap=True))

    def navigate(self, index):
        self.stack.setCurrentIndex(index)
        self.optimization_actions.setVisible(index == 1)
        self.navigation.button(index).setChecked(True)
        self.breadcrumb.setText("РАБОЧЕЕ ПРОСТРАНСТВО   /   " + self.PAGE_NAMES[index].upper())
        for button_ in self.navigation.buttons():
            current = self.navigation.id(button_)
            button_.setIcon(icon(self.PAGE_ICONS[current], ORANGE if current == index else MUTED, 20))
        if index == 2:
            if not self.cleanup_loaded and not self.cleanup_scan_in_progress:
                self.cleanup_refresh_pending = True
                self.refresh_cleanup_when_idle()
        else:
            self.cleanup_refresh_pending = False
        if index == 3:
            if not self.startup_loaded:
                self.startup_refresh_pending = True
                self.refresh_startup_when_idle()
        else:
            self.startup_refresh_pending = False

    def submit(self, title, operation, callback, refresh_on_error=False, *, invalidate_cleanup=True):
        if self.busy:
            self.status.setText("Подождите завершения текущей операции.")
            return
        if invalidate_cleanup:
            self._invalidate_cleanup_inventory()
        self.busy = True
        self.status.setText(title)
        self.progress.show()
        self.stack.setEnabled(False)
        self.optimization_actions.setEnabled(False)
        job = Job(operation, callback, refresh_on_error)
        job.signals.finished.connect(self._finished)
        self.jobs.add(job)
        self.pool.start(job)

    @Slot(object)
    def _finished(self, outcome):
        job, result, error = outcome
        self.jobs.discard(job)
        self.busy = False
        self.progress.hide()
        self.stack.setEnabled(True)
        self.optimization_actions.setEnabled(True)
        if error:
            self.status.setText("Операция не завершена. Подробности в журнале.")
            QMessageBox.warning(self, "PULSE · Не удалось завершить", error)
            if callable(job.refresh_on_error):
                job.refresh_on_error()
            elif job.refresh_on_error:
                self.refresh()
        else:
            self.status.setText("Готово · все данные остаются на этом компьютере")
            job.callback(result)
        self.refresh_cleanup_when_idle()
        self.refresh_startup_when_idle()

    def refresh(self):
        self.submit("Проверяем настройки и резервные копии…",
                    lambda: (self.engine.inspect(), machine_info()), self._inspected, invalidate_cleanup=False)

    def _inspected(self, result):
        inspection, hardware = result
        self.states = inspection["states"]
        for tweak_id, rows in self.rows.items():
            state = self.states[tweak_id]
            for row in rows:
                row.set_state(state)
            check = self.checkboxes[tweak_id]
            check.setEnabled(state["available"] and not state["active"] and not state["managed"])
            check.setToolTip(state["reason"])
            if not check.isEnabled():
                check.setChecked(False)
        self.update_selection()
        self.filter_tweaks()
        self.available_label.setText(f"Доступно: {sum(s['available'] for s in self.states.values())} / {len(TWEAKS)}")
        active = sum(s["active"] for s in self.states.values())
        managed = sum(s["managed"] for s in self.states.values())
        self.applied_metric.value.setText(f"{active} / {len(TWEAKS)}")
        self.applied_metric.footer.setText(f"с копией для отката: {managed}")
        recoverable = sum(bool(r["recoverable"]) for r in inspection["history"])
        startup_copies = self.startup_manager.history()
        self.backup_metric.value.setText(str(recoverable + len(startup_copies)))
        self.hardware_cpu.setText(hardware["cpu"])
        self.hardware_gpu.setText(hardware["gpu"])
        self.hardware_os.setText(hardware["os"] + "\n" + hardware["ram"])
        self._render_history(inspection["history"])
        errors = [r["error"] for r in inspection["history"] if r.get("error") and not r["entries"]]
        interrupted = any(e["status"] in ("applying", "restore_failed")
                          for r in inspection["history"] for e in r["entries"])
        if errors:
            self.status.setText("Есть повреждённые копии. Откройте «Восстановление».")
        elif interrupted:
            self.status.setText("Найдены незавершённые изменения. Откройте «Восстановление» для повторного отката.")
        elif self.completion_message:
            self.status.setText(self.completion_message)
            self.completion_message = ""

    def update_selection(self):
        count = sum(c.isChecked() and c.isEnabled() for c in self.checkboxes.values())
        if hasattr(self, "selection_label"):
            self.selection_label.setText(f"Выбрано: {count}")
            self.apply_button.setEnabled(count > 0)
        for section in self.tweak_sections:
            section.update_selection()

    def select_section(self, key):
        if self.busy:
            return
        section = next(section for section in self.tweak_sections if section.key == key)
        tiles = section.selectable_tiles()
        if not tiles:
            return
        select = not all(tile.selection.isChecked() for tile in tiles)
        for tile in tiles:
            tile.selection.setChecked(select)
        self.update_selection()
        self.status.setText(f"«{section.title}»: выбрано {len(tiles)} доступных настроек. Нажмите «Применить выбранное»."
                            if select else f"Выбор в разделе «{section.title}» снят.")

    def clear_selection(self):
        for check in self.checkboxes.values():
            check.setChecked(False)

    def filter_tweaks(self, *_):
        query = self.search.text().casefold().strip()
        matching = []
        for tweak in TWEAKS:
            visible = query in (tweak.title + " " + tweak.description + " " + tweak.effect).casefold()
            if visible:
                matching.append(tweak.id)
        self.filtered_ids = matching
        for section in self.tweak_sections:
            section.show_matching(set(matching))
        self.filter_count.setText(f"Показано: {len(matching)} из {len(TWEAKS)} · кнопка раздела выбирает все его доступные настройки.")
        self.no_results.setVisible(not matching)

    def scroll_tweaks_to_top(self):
        self.stack.widget(1).verticalScrollBar().setValue(0)

    def apply_selected(self):
        self.apply_tweaks([key for key, check in self.checkboxes.items() if check.isChecked() and check.isEnabled()])

    def apply_tweaks(self, ids):
        if not ids or self.busy:
            return
        text = "Будут применены:\n\n" + "\n".join("•  " + BY_ID[i].title for i in ids)
        cautions = list(dict.fromkeys(BY_ID[i].caution for i in ids if BY_ID[i].caution))
        if cautions:
            text += "\n\n" + "\n\n".join(cautions)
        if "power" in ids or any(i in POWER for i in ids):
            try:
                if self.monitor.sample()["battery"]:
                    text += "\n\nСейчас компьютер работает от батареи. Рекомендуется подключить питание."
            except OSError:
                pass
        if any(i in POWER for i in ids):
            text += "\n\nПараметры питания относятся только к режиму от сети " + ("выбранного плана «Высокая производительность»." if "power" in ids else "текущего плана Windows.")
        restarts = list(dict.fromkeys(BY_ID[i].restart for i in ids))
        text += "\n\nВступление в силу:\n" + "\n".join("•  " + message for message in restarts)
        text += "\n\nИсходные значения будут сохранены перед первым изменением."
        if self.demo:
            text += "\n\nДЕМО: изменения выполняются только в памяти приложения."
        if ConfirmDialog(self, "Применить выбранные настройки?", text).exec() == QDialog.DialogCode.Accepted:
            self.submit("Сохраняем исходные значения и применяем настройки…", lambda: self.engine.apply(ids), self._changed, True)

    def toggle_tweak(self, tweak_id):
        if self.busy:
            return
        state = self.states.get(tweak_id, {})
        if state.get("managed"):
            text = f"«{BY_ID[tweak_id].title}» вернётся к значению до изменения в PULSE. "
            text += "Если вы меняли параметр позже в Windows, он тоже будет заменён сохранённым значением."
            if ConfirmDialog(self, "Вернуть исходную настройку?", text, "Восстановить").exec() == QDialog.DialogCode.Accepted:
                self.submit("Восстанавливаем исходное значение…", lambda: self.engine.restore_tweak(tweak_id), self._changed, True)
        elif state.get("available") and not state.get("active"):
            self.apply_tweaks([tweak_id])

    def _changed(self, result):
        self.status.setText(result["message"])
        self.completion_message = result["message"]
        log.info(result["message"])
        self.refresh()

    def tweak_details(self, tweak_id):
        tweak = BY_ID[tweak_id]
        text = tweak.detail + "\n\nНазначение: " + tweak.effect + ".\n" + tweak.restart
        if tweak.caution:
            text += "\n\n" + tweak.caution
        reason = self.states.get(tweak_id, {}).get("reason")
        if reason:
            text += "\n\nНа этом ПК: " + reason
        dialog = ConfirmDialog(self, tweak.title, text, "Открыть источник")
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.open_url(tweak.source)

    def invalidate_scan(self, *_):
        self.scan_result = None
        if hasattr(self, "clean_button"):
            self.clean_button.setEnabled(False)
            self.clean_report.setText("Параметры изменились. Выполните анализ.")

    def scan_temp(self):
        categories = (["user"] if self.temp_user.isChecked() else []) + (["system"] if self.temp_system.isChecked() else [])
        if not categories:
            self.status.setText("Выберите хотя бы одну категорию очистки.")
            return
        self.scan_result = None
        self.clean_button.setEnabled(False)
        self.submit("Анализируем временные файлы старше 48 часов…", lambda: self.cleaner.scan(categories), self._scanned)

    def _scanned(self, result: Scan):
        self.scan_result = result
        self.clean_size.setText(format_bytes(result.size))
        self.clean_count.setText(f"Найдено файлов: {len(result.candidates)} · старше 48 часов")
        fill_table(self.files_table, [[str(c.path), format_bytes(c.size)] for c in result.candidates[:500]])
        if result.errors:
            self.clean_report.setText("\n".join(result.errors))
        elif self.demo:
            self.clean_report.setText("ДЕМО: реальная файловая система не сканируется и не изменяется.")
        else:
            self.clean_report.setText(f"Показаны первые {min(500, len(result.candidates))} из {len(result.candidates)} файлов. "
                                      f"Пропущено при анализе: {result.skipped}." +
                                      (" Анализ ограничен по времени/количеству; можно повторить после очистки." if result.truncated else ""))
        self.clean_button.setEnabled(bool(result.candidates) and not self.demo)

    def clean_temp(self):
        scan = self.scan_result
        if not scan or not scan.candidates or self.busy:
            return
        text = (f"Будут безвозвратно удалены временные файлы: {len(scan.candidates)}.\n"
                f"Суммарный размер: {format_bytes(scan.size)}.\n\n"
                "Все кандидаты старше 48 часов и находятся в выбранных временных папках. "
                "Занятые или изменившиеся файлы будут пропущены.\n\n"
                "Файлы не попадут в корзину. Резервные копии настроек не возвращают удалённые файлы.")
        if ConfirmDialog(self, "Удалить найденные временные файлы?", text, "Удалить файлы").exec() == QDialog.DialogCode.Accepted:
            self.scan_result = None
            self.clean_button.setEnabled(False)
            self.submit("Очищаем проверенные временные файлы…", lambda: self.cleaner.clean(scan), self._cleaned)

    def _cleaned(self, result):
        self.last_clean = {**result, "created": now()}
        try:
            atomic_json(self.data_root / "last_cleanup.json", self.last_clean)
        except OSError:
            log.exception("Could not save cleanup summary")
        self.cleaned_metric.value.setText(format_bytes(result["bytes"]))
        self.cleaned_metric.footer.setText("за последнюю очистку")
        self.clean_report.setText(result["message"])
        self.clean_size.setText("Очистка завершена")
        self.clean_count.setText("Освобождено: " + format_bytes(result["bytes"]))
        self.files_table.setRowCount(0)
        self.status.setText(result["message"])
        log.info(result["message"])

    def _load_cleanup_summary(self):
        import json
        try:
            path = self.data_root / "last_cleanup.json"
            if path.stat().st_size > 10_000:
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            if type(data.get("bytes")) is int and data["bytes"] >= 0:
                self.last_clean = data
                self.cleaned_metric.value.setText(format_bytes(data["bytes"]))
                self.cleaned_metric.footer.setText("за последнюю очистку")
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _render_history(self, records):
        clear_layout(self.history_layout)
        startup_copies = self.startup_manager.history()
        if startup_copies:
            card = Card()
            inner = QVBoxLayout(card)
            inner.setContentsMargins(22, 20, 22, 20)
            inner.addWidget(label(f"Автозагрузка · сохранено записей: {len(startup_copies)}", 14, bold=True))
            inner.addWidget(label("Исходный запуск приложений и служб можно вернуть в разделе автозагрузки.", 10, MUTED, wrap=True))
            inner.addWidget(button("Открыть автозагрузку", lambda: self.navigate(3), glyph="restore"), 0, Qt.AlignmentFlag.AlignLeft)
            self.history_layout.addWidget(card)
        if not records and not startup_copies:
            card = Card()
            inner = QVBoxLayout(card)
            inner.setContentsMargins(28, 28, 28, 28)
            inner.addWidget(icon_label("restore", BLUE, 35))
            inner.addWidget(label("Пока всё как было", 18, bold=True))
            inner.addWidget(label("После первого изменения здесь появится копия для отката. "
                                  "Настройки, которые уже включены в Windows, не создают лишних копий.", 11, MUTED, wrap=True))
            self.history_layout.addWidget(card)
            return
        for record in records:
            card = Card()
            inner = QVBoxLayout(card)
            inner.setContentsMargins(22, 20, 22, 20)
            row = QHBoxLayout()
            try:
                date = datetime.fromisoformat(record["created"]).astimezone().strftime("%d.%m.%Y · %H:%M")
            except (ValueError, TypeError):
                date = record["id"]
            row.addWidget(label(date, 12, bold=True))
            row.addStretch()
            pending = any(e["status"] in ("applying", "restore_failed") for e in record["entries"])
            row.addWidget(Pill("Нужен откат" if pending else "Есть копия" if record["recoverable"] else "Нет активных изменений", ORANGE if pending else GREEN))
            inner.addLayout(row)
            if record["entries"]:
                inner.addWidget(label(" · ".join(BY_ID[e["id"]].title for e in record["entries"]), 10, MUTED, wrap=True))
            if record.get("error"):
                inner.addWidget(label(record["error"], 10, ORANGE, wrap=True))
            if record["recoverable"]:
                inner.addWidget(button("Вернуть исходные настройки", lambda checked=False, key=record["id"]: self.restore_record(key), glyph="restore"), 0, Qt.AlignmentFlag.AlignLeft)
            self.history_layout.addWidget(card)

    def restore_record(self, record_id):
        if self.busy:
            return
        text = "Все ещё не восстановленные настройки этой копии вернутся к значениям до изменения в PULSE. "
        text += "Более поздние ручные изменения этих же параметров Windows будут заменены. Перезапустите игру после отката."
        if ConfirmDialog(self, "Восстановить настройки из копии?", text, "Восстановить").exec() == QDialog.DialogCode.Accepted:
            self.submit("Восстанавливаем сохранённые значения…", lambda: self.engine.restore(record_id), self._changed, True)

    def refresh_startup(self):
        if self.busy:
            if self.stack.currentIndex() == 3:
                self.startup_refresh_pending = True
            return
        self.startup_refresh_pending = False
        self.submit("Читаем приложения и службы автозагрузки…", self.startup_manager.inspect, self._startup_loaded,
                    invalidate_cleanup=False)

    def refresh_startup_when_idle(self):
        if self.startup_refresh_pending and not self.busy and self.stack.currentIndex() == 3:
            self.refresh_startup()

    def _startup_loaded(self, result):
        self.startup_loaded = True
        self.startup_refresh_pending = False
        for section in self.startup_sections.values():
            clear_layout(section["layout"])
        self.startup_data = {entry["id"]: entry for entry in result["rows"]}
        self.startup_rows = {}
        for entry in result["rows"]:
            row = StartupRow(entry)
            row.requested.connect(self.toggle_startup)
            self.startup_sections[entry["kind"]]["layout"].addWidget(row)
            self.startup_rows[entry["id"]] = row
        copies = result["history"]
        self.startup_restore_button.setEnabled(bool(copies))
        applications = sum(r["kind"] == "app" for r in result["rows"])
        services = len(result["rows"]) - applications
        note = f"Приложений: {applications} · служб: {services} · сохранённых изменений: {len(copies)}."
        if any(r["status"] in ("pending", "restore_failed") for r in copies):
            note += " Есть незавершённые изменения — используйте «Отменить мои изменения»."
        if result["errors"]:
            note += f" Не удалось прочитать записей: {len(result['errors'])}. Подробности в журнале."
            for error in result["errors"]:
                log.warning("Startup: %s", error)
        self.startup_note.setText(note)
        self.filter_startup()
        core_history = self.engine.history()
        self.backup_metric.value.setText(str(sum(bool(r["recoverable"]) for r in core_history) + len(copies)))
        self._render_history(core_history)
        if self.completion_message:
            self.status.setText(self.completion_message)
            self.completion_message = ""

    def filter_startup(self, *_):
        query = self.startup_search.text().casefold().strip()
        counts = {"app": 0, "service": 0}
        for key, row in self.startup_rows.items():
            entry = row.entry
            visible = query in (entry["name"] + " " + entry["target"]["name"]).casefold()
            row.setVisible(visible)
            if visible:
                counts[entry["kind"]] += 1
        for kind, section in self.startup_sections.items():
            section["widget"].setVisible(counts[kind] > 0)
            section["count"].setText(str(counts[kind]))
        self.startup_empty.setVisible(not any(counts.values()))

    def toggle_startup(self, key, desired):
        if self.busy:
            return
        entry = self.startup_data.get(key)
        if not entry or not entry["can_change"]:
            return
        if entry["kind"] == "service":
            text = f"Служба: {entry['name']}\n\n" + (entry.get("description") or "Изменится тип запуска этой службы.")
            text += "\n\n" + ("Служба будет запускаться автоматически при загрузке ПК." if desired else
                                   "Автоматический запуск будет отключён. Связанные функции приложения могут стать недоступны после перезагрузки.")
            text += "\n\nСейчас служба не запускается и не останавливается. Исходный режим запуска сохраняется для отката."
            if ConfirmDialog(self, "Изменить автозапуск службы?", text, "Включить" if desired else "Отключить").exec() != QDialog.DialogCode.Accepted:
                return
        self.submit("Сохраняем и меняем автозапуск…", lambda: self.startup_manager.set_enabled(key, desired),
                    self._startup_changed, self.refresh_startup)

    def _startup_changed(self, result):
        log.info(result["message"])
        self.completion_message = result["message"]
        self.refresh_startup()

    def restore_startup(self):
        if self.busy:
            return
        copies = self.startup_manager.history()
        if not copies:
            return
        text = "Будут возвращены исходные настройки автозапуска:\n\n" + "\n".join("•  " + str(r.get("name", r["id"])) for r in copies)
        text += "\n\nБолее поздние изменения тех же записей будут заменены сохранёнными значениями. Работающие процессы и службы не останавливаются."
        if ConfirmDialog(self, "Восстановить автозагрузку?", text, "Восстановить").exec() == QDialog.DialogCode.Accepted:
            self.submit("Возвращаем исходную автозагрузку…", self.startup_manager.restore_all,
                        self._startup_changed, self.refresh_startup)

    def update_telemetry(self):
        try:
            sample = self.monitor.sample()
            self.cpu_value.setText(f"{sample['cpu']:.0f}%")
            self.sparkline.push(sample["cpu"])
            self.ram_ring.set_value(sample["memory"])
            self.disk_ring.set_value(sample["disk"])
            self.ram_info.setText(f"{format_bytes(sample['memory_used'])} из {format_bytes(sample['memory_total'])}")
            self.disk_info.setText("Свободно " + format_bytes(sample["disk_free"]))
        except (OSError, RuntimeError) as exc:
            self.cpu_value.setText("—")
            self.connection.setText("●  Мониторинг недоступен")
            self.connection.setToolTip(str(exc))

    def open_url(self, url):
        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(self, "PULSE", "Не удалось открыть ссылку в системе.")

    def open_settings(self, uri):
        if self.demo:
            self.status.setText("ДЕМО: открытие системных настроек отключено.")
        else:
            self.open_url(uri)

    def open_local(self, path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def system_protection(self):
        self._launch_tool("SystemPropertiesProtection.exe")

    def _launch_tool(self, name):
        if self.demo:
            self.status.setText("ДЕМО: системные инструменты не запускаются.")
            return
        import subprocess
        try:
            subprocess.Popen([system_executable(name)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            QMessageBox.warning(self, "PULSE", str(exc))

    def show_log(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("PULSE · Журнал")
        dialog.resize(800, 500)
        layout = QVBoxLayout(dialog)
        log_view = QTextEdit()
        log_view.setReadOnly(True)
        path = self.data_root / "pulse.log"
        try:
            text = path.read_text(encoding="utf-8")[-60_000:]
        except OSError:
            text = "Журнал пока пуст."
        log_view.setPlainText(text)
        layout.addWidget(log_view)
        layout.addWidget(button("Закрыть", dialog.accept))
        dialog.exec()

    def closeEvent(self, event):
        if self.busy or self.program_check_auto or self.inventory_jobs:
            self.status.setText("Дождитесь окончания операции, затем закройте приложение.")
            event.ignore()
        else:
            self.timer.stop()
            self.uninstall_timer.stop()
            self.program_render_timer.stop()
            event.accept()
