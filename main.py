"""PULSE entry point. Starting the app never applies Windows tweaks."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="PULSE — Windows Game Optimizer")
    parser.add_argument("--demo", action="store_true", help="Изолированный просмотр без изменения Windows и файлов")
    args = parser.parse_args()
    try:
        from PySide6.QtCore import QLockFile
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QApplication, QMessageBox
        from pulse.branding import application_icon, set_windows_app_id
        from pulse.cleanup import Cleaner
        from pulse.engine import Engine
        from pulse.ui import MainWindow
        from pulse.windows import DemoBackend, WindowsBackend
    except ImportError as exc:
        print(f"Не установлены зависимости: {exc}\nЗапустите run.bat или pip install -r requirements.txt", file=sys.stderr)
        return 1

    set_windows_app_id()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("PULSE")
    app.setOrganizationName("PulseOptimizer")
    app.setWindowIcon(application_icon())
    app.setFont(QFont("Segoe UI", 10))
    demo_directory = tempfile.TemporaryDirectory(prefix="pulse-demo-") if args.demo else None
    if demo_directory:
        data_root = Path(demo_directory.name)
    else:
        data_root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "PulseOptimizer"
    try:
        data_root.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(data_root / "instance.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(100):
            QMessageBox.information(None, "PULSE", "Приложение уже запущено или папка данных недоступна.")
            return 1
        handler = RotatingFileHandler(data_root / "pulse.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=[handler])
        backend = DemoBackend() if args.demo else WindowsBackend()
        engine = Engine(backend, data_root / "backups")
        window = MainWindow(engine, Cleaner(demo=args.demo), data_root)
    except Exception as exc:
        logging.exception("Startup failed")
        QMessageBox.critical(None, "PULSE · Запуск", str(exc))
        return 1

    def report_error(kind, value, traceback):
        logging.critical("Unhandled exception", exc_info=(kind, value, traceback))
        QMessageBox.critical(window, "PULSE · Ошибка", "Возникла ошибка интерфейса. Подробности сохранены в журнале.\n\n" + str(value))

    sys.excepthook = report_error
    screen = app.primaryScreen()
    if screen:
        available = screen.availableGeometry()
        window.resize(min(window.width(), available.width() - 60), min(window.height(), available.height() - 60))
    window.show()
    logging.info("PULSE started; demo=%s", args.demo)
    code = app.exec()
    window.pool.waitForDone()
    window.inventory_pool.waitForDone()
    window.component_pool.waitForDone()
    lock.unlock()
    logging.shutdown()
    if demo_directory:
        demo_directory.cleanup()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
