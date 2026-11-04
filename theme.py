STYLE = """
QWidget { color: #eaf0fb; }
QMainWindow, QDialog { background: #10141d; }
QWidget#Sidebar { background: #141a25; border-right: 1px solid #242c3b; }
QWidget#Content, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget { background: #10141d; }
QFrame#Card { background: #191f2c; border: 1px solid #2a3140; border-radius: 13px; }
QFrame#TweakTile { background: #191f2c; border: 1px solid #2a3140; border-radius: 14px; }
QFrame#TweakTile:hover { background: #1e2634; border-color: #526079; }
QFrame#TweakTile[available="false"] { background: #171d28; }
QFrame#TweakTile[selected="true"] { background: #29241f; border: 1px solid #d48c53; }
QFrame#TweakTile[selected="true"]:hover { background: #30281f; border-color: #ffad6b; }
QFrame#Inset { background: #141a25; border: 1px solid #2a3140; border-radius: 10px; }
QPushButton { border: none; border-radius: 8px; padding: 9px 15px; font-weight: 600; }
QPushButton#Primary { background: #ff9b52; color: #18191e; }
QPushButton#Primary:hover { background: #ffb477; }
QPushButton#Primary:pressed { background: #df813d; }
QPushButton#Secondary { background: #252e40; color: #d9e1ef; border: 1px solid #344057; }
QPushButton#Secondary:hover { background: #303c52; border-color: #596780; }
QPushButton#Nav { text-align: left; padding: 13px 15px; color: #95a1b8; background: transparent; border-radius: 8px; }
QPushButton#Nav:hover { color: #eaf0fb; background: #1e2737; }
QPushButton#Nav:checked { background: #362c28; color: #ffac6b; border: 1px solid #59402e; }
QPushButton#IconButton { padding: 3px; background: transparent; border-radius: 6px; }
QPushButton#IconButton:hover { background: #303c52; }
QPushButton:disabled, QPushButton#Primary:disabled, QPushButton#Secondary:disabled { background: #222937; color: #788398; border: 1px solid #30384a; }
QPushButton:focus, QLineEdit:focus { border: 1px solid #ff9b52; }
QLineEdit { background: #191f2c; border: 1px solid #344057; border-radius: 8px; padding: 10px 13px; }
QCheckBox { spacing: 10px; background: transparent; padding: 4px 0; }
QCheckBox::indicator { width: 18px; height: 18px; border: 1px solid #58667d; border-radius: 5px; background: #171d28; }
QCheckBox::indicator:checked { background: #ff9b52; border: 4px solid #ffca9f; }
QCheckBox::indicator:disabled { background: #30394b; border-color: #445168; }
QCheckBox#TileSelection { spacing: 0; padding: 0; }
QComboBox { background: #252e40; border: 1px solid #344057; border-radius: 7px; padding: 9px 14px; min-width: 100px; }
QComboBox QAbstractItemView { background: #252e40; selection-background-color: #49382c; border: 1px solid #344057; }
QScrollArea { border: none; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 3px 0; }
QScrollBar::handle:vertical { background: #364156; border-radius: 3px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QToolTip { background: #283246; color: #edf2fb; border: 1px solid #526079; padding: 7px; border-radius: 5px; }
QProgressBar { background: #222c3d; border: none; border-radius: 3px; height: 5px; }
QProgressBar::chunk { background: #ff9b52; border-radius: 3px; }
QTableWidget { background: #191f2c; alternate-background-color: #1c2331; border: 1px solid #2a3140; border-radius: 10px; gridline-color: #2a3140; }
QTableWidget::item { padding: 9px; border-bottom: 1px solid #242e3e; }
QTableWidget::item:selected { background: #303d54; }
QHeaderView::section { background: #202838; color: #9eacc3; border: none; padding: 12px 10px; text-align: left; }
QTableCornerButton::section { background: #202838; border: none; }
QTextEdit { background: #171d28; border: 1px solid #344057; border-radius: 8px; padding: 10px; font-family: 'Consolas'; }
QMessageBox { background: #191f2c; }
QMessageBox QLabel { min-width: 360px; }
"""
