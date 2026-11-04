"""Small native Qt widgets and vector artwork used by the desktop interface."""

from __future__ import annotations

from collections import deque
from functools import lru_cache

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QAbstractButton, QCheckBox, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from .cleanup import format_bytes

ORANGE = "#ff9b52"
GREEN = "#65d9ac"
BLUE = "#7aa6ff"
MUTED = "#8d98ae"

PATHS = {
    "bolt": '<path d="M13 2 4 14h7l-1 8 10-13h-7l1-7Z"/>',
    "home": '<path d="m3 10 9-7 9 7v10H3Z"/><path d="M9 20v-7h6v7"/>',
    "sliders": '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="16" cy="17" r="3"/>',
    "broom": '<path d="m14 3-5 9m1-2 9 5-4 7-11-6 4-7Z"/><path d="m8 14-2 4m7-1-2 4"/>',
    "startup": '<path d="M12 3v9m-5-7a9 9 0 1 0 10 0"/>',
    "restore": '<path d="M3 10a9 9 0 1 1 1 8M3 4v6h6m3-3v6l4 2"/>',
    "settings": '<path d="m9 3-1 3-3 1-2 4 2 3v3l4 3 3-1 3 1 4-3v-3l2-3-2-4-3-1-1-3Z"/><circle cx="12" cy="12" r="3"/>',
    "gamepad": '<path d="M7 7h10c3 0 5 9 4 11s-4-1-6-3H9c-2 2-5 5-6 3S4 7 7 7Z"/><path d="M6 10v5m-2-2h5m7-2h.01m2 3h.01"/>',
    "video": '<rect x="3" y="5" width="13" height="14" rx="3"/><path d="m16 10 5-3v10l-5-3M3 3l18 18"/>',
    "mouse": '<rect x="6" y="2" width="12" height="20" rx="6"/><path d="M12 2v7"/>',
    "spark": '<path d="m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3Z"/>',
    "cpu": '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 1v5m6-5v5M9 18v5m6-5v5M1 9h5m-5 6h5m12-6h5m-5 6h5"/><rect x="9" y="9" width="6" height="6" rx="1"/>',
    "memory": '<rect x="2" y="5" width="20" height="13" rx="2"/><path d="M6 9v5m4-5v5m4-5v5m4-5v5M6 18v3m4-3v3m4-3v3m4-3v3"/>',
    "disk": '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="12" cy="11" r="4"/><path d="m12 11 5 6m-10 1h.01"/>',
    "shield": '<path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6Z"/><path d="m8 12 3 3 5-6"/>',
    "arrow": '<path d="M5 12h14m-6-6 6 6-6 6"/>',
    "external": '<path d="M13 3h8v8m0-8L10 14m-1-9H3v16h16v-6"/>',
    "refresh": '<path d="M20 8A9 9 0 0 0 4 6L2 9m0-6v6h6m-4 7a9 9 0 0 0 16 2l2-3m0 6v-6h-6"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>',
    "check": '<path d="m5 12 5 5L20 6"/>',
    "folder": '<path d="M3 6h7l2 3h9v11H3Z"/>',
    "search": '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
    "monitor": '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 22h8m-4-5v5"/>',
    "download": '<path d="M12 2v13m-5-5 5 5 5-5M3 16v6h18v-6"/>',
}


@lru_cache(maxsize=128)
def icon(name: str, color: str = MUTED, size: int = 22) -> QIcon:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
           f'fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
           + PATHS.get(name, PATHS["bolt"]) + '</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def label(text: str, size: int = 13, color: str = "#eaf0fb", bold: bool = False, wrap=False) -> QLabel:
    widget = QLabel(text)
    font = QFont("Segoe UI", size)
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    widget.setFont(font)
    widget.setStyleSheet(f"color: {color}; background: transparent; border: none;")
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def icon_label(name: str, color=ORANGE, size=22) -> QLabel:
    result = QLabel()
    result.setPixmap(icon(name, color, size).pixmap(size, size))
    result.setFixedSize(size + 2, size + 2)
    return result


def button(text: str, callback=None, primary=False, glyph: str | None = None) -> QPushButton:
    result = QPushButton(text)
    result.setObjectName("Primary" if primary else "Secondary")
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    result.setMinimumHeight(40)
    if glyph:
        result.setIcon(icon(glyph, "#15191f" if primary else "#c1cbdd", 18))
    if callback:
        result.clicked.connect(callback)
    return result


class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")


class RemovalRow(Card):
    """An explicit selection for destructive uninstall, never an instant toggle."""
    def __init__(self, entry):
        super().__init__()
        self.entry = entry
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(16)
        layout.addWidget(icon_label("broom" if entry["group"] == "components" else "folder", ORANGE, 22))
        text = QVBoxLayout()
        text.setSpacing(5)
        self.title = label(entry["title"], 11, bold=True, wrap=True)
        self.caption = label(entry["description"], 9, MUTED, wrap=True)
        text.addWidget(self.title)
        text.addWidget(self.caption)
        state = ("Дополнительный компонент · DISM" if entry["kind"] == "capability" else
                 f"Пакетов установлено: {len(entry['packages'])} · подготовлено: {len(entry['provisioned'])}")
        self.detail = label(entry["reason"] or state, 9, MUTED, wrap=True)
        text.addWidget(self.detail)
        layout.addLayout(text, 1)
        self.selection = QCheckBox()
        self.selection.setEnabled(entry["available"])
        self.selection.setAccessibleName("Выбрать для удаления: " + entry["title"])
        self.selection.setToolTip(entry["reason"] or "Добавить в список удаления")
        layout.addWidget(self.selection)
        self.setToolTip(entry["name"])


class ProgramRow(Card):
    requested = Signal(str)

    def __init__(self, entry):
        super().__init__()
        self.entry = entry
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(16)
        layout.addWidget(icon_label("folder", BLUE, 22))
        text = QVBoxLayout()
        text.setSpacing(5)
        self.title = label(entry["title"], 11, bold=True, wrap=True)
        text.addWidget(self.title)
        scope = "Все пользователи" if entry["target"]["hive"] == "HKLM" else "Другой пользователь" if entry["target"]["hive"] == "HKU" else "Текущий пользователь"
        self.caption = label(" · ".join(v for v in (entry["version"], entry["publisher"], scope, f"{entry['target']['view']}-битная запись") if v), 9, MUTED, wrap=True)
        text.addWidget(self.caption)
        self.detail = label(entry["reason"] or "Штатное удаление с проверкой папки, вложенных файлов и каталогов", 9, MUTED, wrap=True)
        text.addWidget(self.detail)
        layout.addLayout(text, 1)
        size = entry.get("size_bytes")
        self.size = label(format_bytes(size) if size is not None else "—", 14, bold=True)
        self.size.setFixedWidth(105)
        self.size.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.size.setToolTip("Размер по данным установщика; может отличаться от фактического размера на диске."
                             if size is not None else "Установщик не указал размер программы.")
        layout.addWidget(self.size)
        self.remove = button("Удалить", lambda: self.requested.emit(entry["id"]), glyph="broom")
        self.remove.setEnabled(entry["available"])
        self.remove.setToolTip(entry["reason"] or "Открыть мастер удаления")
        layout.addWidget(self.remove)


class Pill(QLabel):
    def __init__(self, text: str, color=GREEN, parent=None):
        super().__init__(text, parent)
        self.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        self.setStyleSheet(f"color:{color};background:#1c2a2a;"
                          "border-radius:10px; padding:5px 10px; border: none;")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(28)


class StateSwitch(QAbstractButton):
    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setFixedSize(44, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Применить настройку или вернуть исходное состояние")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = self.isChecked()
        painter.setPen(Qt.PenStyle.NoPen)
        color = QColor(ORANGE if active else "#354155")
        if not self.isEnabled():
            color.setAlpha(130)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(0, 2, 44, 22), 11, 11)
        painter.setBrush(QColor("#fff4e8" if active else "#9ba7bc"))
        painter.drawEllipse(QRectF(25 if active else 3, 5, 16, 16))


class StartupRow(Card):
    requested = Signal(str, bool)

    def __init__(self, entry):
        super().__init__()
        self.entry = entry
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(14)
        layout.addWidget(icon_label("startup" if entry["kind"] == "app" else "settings",
                                    BLUE if entry["kind"] == "app" else ORANGE, 24))
        text = QVBoxLayout()
        text.setSpacing(5)
        text.addWidget(label(entry["name"], 11, bold=True, wrap=True))
        if entry["kind"] == "app":
            state = "Включено при входе" if entry["enabled"] is True else "Выключено" if entry["enabled"] is False else "Состояние неизвестно"
            detail = entry["scope"] + " · " + state
        else:
            state = ("Отложенный автозапуск" if entry["state"]["delayed"] else "Автоматически") if entry["enabled"] else (
                "Отключена" if entry["state"]["start"] == 4 else "По требованию")
            detail = state + " · " + ("Работает сейчас" if entry["running"] else "Остановлена")
        if entry["reason"]:
            detail += " · " + entry["reason"]
        self.detail = label(detail, 9, MUTED, wrap=True)
        text.addWidget(self.detail)
        layout.addLayout(text, 1)
        self.switch = StateSwitch()
        self.switch.setAccessibleName("Автозапуск: " + entry["name"])
        self.switch.setChecked(entry["enabled"] is True)
        self.switch.setEnabled(entry["can_change"])
        self.switch.setToolTip(entry["reason"] or ("Отключить автозапуск" if entry["enabled"] else "Включить автозапуск"))
        self.switch.clicked.connect(self._request)
        layout.addWidget(self.switch)
        self.setToolTip(entry.get("description", "") or detail)

    def _request(self):
        desired = self.switch.isChecked()
        self.switch.setChecked(self.entry["enabled"] is True)
        self.requested.emit(self.entry["id"], desired)


class Sparkline(QWidget):
    def __init__(self, color=ORANGE):
        super().__init__()
        self.color = color
        self.values = deque(maxlen=45)
        self.setMinimumHeight(95)

    def push(self, value):
        self.values.append(max(0, min(100, value)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(1, 7, self.width() - 3, self.height() - 16)
        painter.setPen(QPen(QColor("#263041"), 1))
        for fraction in (0, .5, 1):
            y = area.top() + area.height() * fraction
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        if len(self.values) < 2:
            return
        values = list(self.values)
        path = QPainterPath()
        # Keep the time axis fixed at 45 seconds as new samples arrive.
        offset = self.values.maxlen - len(values)
        points = [QPointF(area.left() + (i + offset) * area.width() / (self.values.maxlen - 1),
                         area.bottom() - value / 100 * area.height()) for i, value in enumerate(values)]
        path.moveTo(points[0])
        for point in points[1:]:
            path.lineTo(point)
        fill = QPainterPath(path)
        fill.lineTo(points[-1].x(), area.bottom())
        fill.lineTo(points[0].x(), area.bottom())
        fill.closeSubpath()
        gradient = QLinearGradient(0, area.top(), 0, area.bottom())
        gradient.setColorAt(0, QColor(255, 155, 82, 65))
        gradient.setColorAt(1, QColor(255, 155, 82, 0))
        painter.fillPath(fill, gradient)
        painter.setPen(QPen(QColor(self.color), 2.0))
        painter.drawPath(path)
        painter.setBrush(QColor(self.color))
        painter.drawEllipse(points[-1], 3.5, 3.5)


class Ring(QWidget):
    def __init__(self, color=GREEN):
        super().__init__()
        self.color = color
        self.value = 0.0
        self.ready = False
        self.setFixedSize(75, 75)

    def set_value(self, value):
        self.value = value
        self.ready = True
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(7, 7, 61, 61)
        pen = QPen(QColor("#2c3546"), 5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, 90 * 16, -360 * 16)
        pen.setColor(QColor(self.color))
        painter.setPen(pen)
        painter.drawArc(rect, 90 * 16, -int(max(0, min(100, self.value)) * 3.6 * 16))
        painter.setPen(QColor("#edf2fb"))
        painter.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"{self.value:.0f}%" if self.ready else "—")


class Hero(QFrame):
    def __init__(self, callback):
        super().__init__()
        self.setMinimumHeight(203)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(27, 23, 180, 23)
        layout.setSpacing(9)
        layout.addWidget(label("МЕНЬШЕ ФОНА. БОЛЬШЕ ИГРЫ.", 9, ORANGE, True))
        layout.addWidget(label("Подготовь систему\nк следующей победе.", 24, bold=True))
        layout.addWidget(label("Выбери настройки. Сохрани контроль.", 11, "#b0b8c8"))
        row = QHBoxLayout()
        self.action = button("Открыть настройки", callback, True, "bolt")
        row.addWidget(self.action)
        row.addStretch()
        layout.addLayout(row)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 15, 15)
        painter.setClipPath(clip)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor("#222a39"))
        gradient.setColorAt(.55, QColor("#222735"))
        gradient.setColorAt(1, QColor("#473329"))
        painter.fillPath(clip, gradient)
        center = QPointF(self.width() - 124, self.height() / 2)
        for radius, alpha in ((128, 15), (94, 30), (65, 65)):
            painter.setPen(QPen(QColor(255, 155, 82, alpha), 1))
            painter.drawEllipse(center, radius, radius)
        painter.setPen(QPen(QColor(255, 155, 82, 85), 3))
        painter.drawArc(QRectF(center.x() - 94, center.y() - 94, 188, 188), 12 * 16, 80 * 16)
        painter.drawArc(QRectF(center.x() - 65, center.y() - 65, 130, 130), 185 * 16, 90 * 16)
        painter.setPen(QPen(QColor(255, 155, 82, 28), 1))
        painter.drawLine(QPointF(center.x() - 155, center.y()), QPointF(center.x() + 155, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - 155), QPointF(center.x(), center.y() + 155))
        painter.setBrush(QColor(255, 155, 82, 18))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, 49, 49)
        painter.drawPixmap(int(center.x() - 28), int(center.y() - 28), icon("bolt", ORANGE, 56).pixmap(56, 56))
        painter.setClipping(False)
        painter.setPen(QPen(QColor("#4a3d37"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(clip)


class MetricCard(Card):
    def __init__(self, title, value, footer, glyph, color=ORANGE):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(5)
        row = QHBoxLayout()
        row.addWidget(label(title, 10, MUTED))
        row.addStretch()
        row.addWidget(icon_label(glyph, color, 18))
        layout.addLayout(row)
        self.value = label(value, 25, bold=True)
        layout.addWidget(self.value)
        self.footer = label(footer, 9, MUTED, wrap=True)
        layout.addWidget(self.footer)


class TweakRow(Card):
    requested = Signal(str)
    details = Signal(str)

    def __init__(self, tweak):
        super().__init__()
        self.tweak = tweak
        self.state = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)
        glyph = icon_label(tweak.icon, ORANGE, 22)
        layout.addWidget(glyph)
        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(label(tweak.title, 11, bold=True))
        self.caption = label(tweak.description, 9, MUTED, wrap=True)
        text.addWidget(self.caption)
        self.status = label("Проверка…", 8, MUTED, wrap=True)
        text.addWidget(self.status)
        layout.addLayout(text, 1)
        details = QPushButton()
        details.setObjectName("IconButton")
        details.setIcon(icon("info", MUTED, 17))
        details.setFixedSize(28, 28)
        details.setToolTip("Описание настройки и источник")
        details.setAccessibleName("Описание: " + tweak.title)
        details.clicked.connect(lambda: self.details.emit(tweak.id))
        layout.addWidget(details)
        self.switch = StateSwitch()
        self.switch.setEnabled(False)
        self.switch.clicked.connect(self._request)
        layout.addWidget(self.switch)

    def _request(self):
        self.switch.setChecked(self.state.get("active", False))
        self.requested.emit(self.tweak.id)

    def set_state(self, state):
        self.state = state
        self.switch.setChecked(state["active"])
        self.switch.setEnabled(state["managed"] or (state["available"] and not state["active"]))
        self.switch.setToolTip("Вернуть исходное состояние" if state["managed"] else
                               "Уже настроено в Windows" if state["active"] else state["reason"] or "Применить с сохранением исходного значения")
        text = ("Есть копия для отката" if state["managed"] else "Уже настроено в Windows" if state["active"] else
                state["reason"] or "Недоступно" if not state["available"] else "Системный выбор" if state["unknown"] else "Можно применить")
        text += " · " + self.tweak.effect
        self.status.setText(text)
        self.status.setStyleSheet(f"color:{GREEN if state['active'] else MUTED};background:transparent;border:none;")


class TweakTile(QFrame):
    """Square catalog card; selection and immediate apply remain separate actions."""
    requested = Signal(str)
    details = Signal(str)

    def __init__(self, tweak, color=ORANGE):
        super().__init__()
        self.tweak = tweak
        self.state = {}
        self.setObjectName("TweakTile")
        self.setProperty("selected", False)
        self.setProperty("available", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(tweak.title)
        self.setToolTip("Нажмите на плитку, чтобы выбрать настройку")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(6)
        badge = QFrame()
        badge.setFixedSize(36, 36)
        badge.setStyleSheet(f"QFrame {{background:{QColor(color).darker(450).name()};border:none;border-radius:10px;}}")
        badge_layout = QHBoxLayout(badge)
        badge_layout.setContentsMargins(6, 6, 6, 6)
        badge_layout.addWidget(icon_label(tweak.icon, color, 22))
        top.addWidget(badge)
        top.addStretch()
        self.info_button = QPushButton()
        self.info_button.setObjectName("IconButton")
        self.info_button.setIcon(icon("info", MUTED, 17))
        self.info_button.setFixedSize(26, 26)
        self.info_button.setToolTip("Описание настройки и источник")
        self.info_button.setAccessibleName("Описание: " + tweak.title)
        self.info_button.clicked.connect(lambda: self.details.emit(tweak.id))
        top.addWidget(self.info_button)
        self.selection = QCheckBox()
        self.selection.setObjectName("TileSelection")
        self.selection.setAccessibleName("Выбрать: " + tweak.title)
        self.selection.setToolTip("Включить в список выбранных настроек")
        self.selection.toggled.connect(self._selection_changed)
        top.addWidget(self.selection)
        layout.addLayout(top)
        self.title = label(tweak.title, 11, bold=True, wrap=True)
        self.title.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.title.setFixedHeight(42)
        layout.addWidget(self.title)
        self.caption = label(tweak.description, 9, MUTED, wrap=True)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.caption.setFixedHeight(48)
        layout.addWidget(self.caption)
        layout.addStretch(1)
        bottom = QHBoxLayout()
        bottom.setSpacing(9)
        self.status = label("Проверка…", 8, MUTED, wrap=True)
        self.status.setFixedHeight(40)
        self.status.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        bottom.addWidget(self.status, 1)
        self.switch = StateSwitch()
        self.switch.setEnabled(False)
        self.switch.clicked.connect(self._request)
        bottom.addWidget(self.switch)
        layout.addLayout(bottom)
        self._selection_changed()

    def _selection_changed(self, *_):
        self.setProperty("selected", self.selection.isChecked())
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def _request(self):
        self.switch.setChecked(self.state.get("active", False))
        self.requested.emit(self.tweak.id)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            if self.selection.isEnabled():
                self.selection.toggle()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def set_state(self, state):
        self.state = state
        self.switch.setChecked(state["active"])
        self.switch.setEnabled(state["managed"] or (state["available"] and not state["active"]))
        self.switch.setToolTip("Вернуть исходное состояние" if state["managed"] else
                               "Уже настроено в Windows" if state["active"] else state["reason"] or "Применить эту настройку")
        if state["managed"]:
            text = "Есть копия для отката"
        elif not state["available"]:
            reason = state["reason"]
            text = ("Нужны права администратора" if "администратора" in reason else
                    "Только Enterprise / Education" if "только в Enterprise" in reason else
                    "Недоступно на этом ПК")
        elif state["active"]:
            text = "Уже настроено в Windows"
        elif state["unknown"]:
            text = "Системный выбор"
        else:
            text = "Можно применить"
        self.status.setText(text)
        self.status.setToolTip(state["reason"] or text)
        self.status.setStyleSheet(f"color:{GREEN if state['active'] else MUTED};background:transparent;border:none;")
        self.setProperty("available", state["available"])
        self._selection_changed()


class TileGrid(QWidget):
    """One responsive grid in the parent scroll area; each item stays square."""
    minimum_tile_width = 235
    gap = 14

    def __init__(self, tiles):
        super().__init__()
        self.tiles = list(tiles)
        self.shown_tiles = list(tiles)
        self.columns = 1
        self.tile_edge = 0
        self._laying_out = False
        self.setMinimumWidth(self.minimum_tile_width)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for tile in self.tiles:
            tile.setParent(self)
        self._relayout()

    def minimumSizeHint(self):
        return QSize(self.minimum_tile_width, self.minimumHeight())

    def sizeHint(self):
        return QSize(self.minimum_tile_width * 3 + self.gap * 2, self.minimumHeight())

    def set_shown(self, tiles):
        self.shown_tiles = list(tiles)
        shown = set(self.shown_tiles)
        for tile in self.tiles:
            tile.setVisible(tile in shown)
        self._relayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self):
        if self._laying_out:
            return
        self._laying_out = True
        try:
            width = max(self.minimum_tile_width, self.width())
            self.columns = max(1, (width + self.gap) // (self.minimum_tile_width + self.gap))
            self.tile_edge = (width - (self.columns - 1) * self.gap) // self.columns
            rows = (len(self.shown_tiles) + self.columns - 1) // self.columns
            height = rows * self.tile_edge + max(0, rows - 1) * self.gap
            if self.height() != height or self.minimumHeight() != height:
                self.setFixedHeight(height)
            for index, tile in enumerate(self.shown_tiles):
                column, row = index % self.columns, index // self.columns
                tile.setGeometry(column * (self.tile_edge + self.gap), row * (self.tile_edge + self.gap),
                                 self.tile_edge, self.tile_edge)
        finally:
            self._laying_out = False


class TweakSection(QWidget):
    selection_requested = Signal(str)

    def __init__(self, key, title, description, glyph, color, tiles):
        super().__init__()
        self.key = key
        self.title = title
        self.tiles = list(tiles)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 9, 0, 10)
        layout.setSpacing(16)
        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(icon_label(glyph, color, 25))
        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(label(title, 17, bold=True))
        text.addWidget(label(description, 9, MUTED, wrap=True))
        header.addLayout(text, 1)
        self.count = Pill("Настроек: " + str(len(self.tiles)), color)
        header.addWidget(self.count)
        self.selection_button = button("Выбрать раздел", lambda: self.selection_requested.emit(key), glyph="check")
        self.selection_button.setMinimumWidth(190)
        self.selection_button.setMinimumHeight(36)
        self.selection_button.setAccessibleName("Выбрать доступные настройки раздела «" + title + "»")
        header.addWidget(self.selection_button)
        layout.addLayout(header)
        self.grid = TileGrid(self.tiles)
        layout.addWidget(self.grid)

    def show_matching(self, ids):
        shown = [tile for tile in self.tiles if tile.tweak.id in ids]
        self.grid.set_shown(shown)
        self.setVisible(bool(shown))
        self.update_selection()

    def selectable_tiles(self):
        return [tile for tile in self.grid.shown_tiles if tile.selection.isEnabled()]

    def update_selection(self):
        selectable = self.selectable_tiles()
        selected = sum(tile.selection.isChecked() for tile in selectable)
        complete = bool(selectable) and selected == len(selectable)
        narrowed = len(self.grid.shown_tiles) < len(self.tiles)
        self.count.setText(f"Выбрано: {selected}")
        self.count.setToolTip(f"Настроек показано: {len(self.grid.shown_tiles)}. Можно выбрать: {len(selectable)}. Выбрано: {selected}.")
        self.selection_button.setText("Снять выбор" if complete else "Выбрать найденные" if narrowed else "Выбрать раздел")
        self.selection_button.setAccessibleName(("Снять выбор в разделе «" if complete else "Выбрать доступные настройки раздела «") + self.title + "»")
        self.selection_button.setEnabled(bool(selectable))
        self.selection_button.setToolTip("Снять выбор доступных настроек этого раздела" if complete else
                                         "Выбрать доступные ненастроенные плитки; при поиске — только найденные" if selectable else
                                         "Все показанные настройки уже настроены или недоступны")
