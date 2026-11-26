"""Shared visual roles for the native research workbench."""

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication


WINDOW = "#0B0D10"
PANEL = "#121519"
CARD = "#181B20"
INSET = "#0E1013"
BORDER = "#2A2E35"
TEXT = "#E6E8EB"
MUTED = "#8B919A"
ACCENT = "#3A9BDC"
ACCENT_SUBTLE = "#182C3A"
GOOD = "#0AAC23"
WARNING = "#FBB315"
SERIOUS = "#F18A59"
CRITICAL = "#DC3C3D"
SERIES_PRIMARY = "#338CED"
SERIES_COMPARISON = "#E76624"


STYLE = f"""
QWidget {{ background-color: {WINDOW}; color: {TEXT}; font-size: 13px; }}
QMainWindow, QDialog {{ background-color: {WINDOW}; }}
QLabel {{ background: transparent; }}
QLabel#title {{ font-size: 16px; font-weight: 600; }}
QLabel#section {{ font-size: 13px; font-weight: 600; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#error {{ color: {CRITICAL}; }}
QLabel#success {{ color: {GOOD}; }}
QLabel#warning {{ color: {WARNING}; }}
QToolBar, QFrame#toolbar {{
    background-color: {PANEL}; border: none; border-bottom: 1px solid {BORDER};
    padding: 5px 8px; spacing: 6px;
}}
QToolBar QLabel, QFrame#toolbar QLabel {{ background: transparent; }}
QDockWidget {{ color: {TEXT}; font-weight: 600; titlebar-close-icon: none; }}
QDockWidget::title {{
    background-color: {PANEL}; border-bottom: 1px solid {BORDER};
    padding: 8px 10px; text-align: left;
}}
QDockWidget > QWidget {{ background-color: {PANEL}; }}
QPushButton {{
    color: {TEXT}; background-color: {CARD}; border: 1px solid {BORDER};
    border-radius: 4px; min-height: 20px; padding: 3px 10px;
}}
QPushButton:hover {{ background-color: #222830; border-color: #3A424E; }}
QPushButton:pressed {{ background-color: #101419; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton#primary {{
    background-color: {ACCENT}; color: #08131B; border-color: {ACCENT}; font-weight: 600;
}}
QPushButton#primary:hover {{ background-color: #58A9DF; }}
QPushButton#primary:pressed {{ background-color: #2E83BA; }}
QPushButton:disabled, QPushButton#primary:disabled {{
    color: #626A75; background-color: #15191E; border-color: #252A31;
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget, QTableWidget, QPlainTextEdit {{
    background-color: {INSET}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 4px; padding: 4px 6px; selection-background-color: {ACCENT_SUBTLE};
    selection-color: {TEXT};
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ min-height: 20px; }}
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border-color: {ACCENT};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: #626A75; background-color: #12151A;
}}
QComboBox QAbstractItemView {{
    background-color: {CARD}; color: {TEXT}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT_SUBTLE};
}}
QListWidget {{ padding: 4px; }}
QListWidget::item {{ padding: 7px 6px; border-radius: 3px; border: 1px solid transparent; }}
QListWidget::item:hover {{ background-color: {CARD}; }}
QListWidget::item:selected {{
    background-color: {ACCENT_SUBTLE}; color: {TEXT}; border-color: {ACCENT};
}}
QTableWidget {{ gridline-color: #232830; alternate-background-color: #12161B; padding: 0; }}
QTableWidget::item {{ padding: 6px; border: none; }}
QTableWidget::item:selected {{ background-color: {ACCENT_SUBTLE}; color: {TEXT}; }}
QHeaderView::section {{
    background-color: {CARD}; color: {MUTED}; border: none;
    border-bottom: 1px solid {BORDER}; padding: 6px; font-weight: 500;
}}
QTabWidget::pane {{ border: 1px solid {BORDER}; background-color: {PANEL}; }}
QTabBar::tab {{
    background-color: {PANEL}; color: {MUTED}; padding: 8px 12px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{ color: {TEXT}; background-color: {CARD}; }}
QTabBar::tab:selected {{ color: {TEXT}; background-color: {CARD}; border-bottom-color: {ACCENT}; }}
QGraphicsView {{ background-color: {INSET}; border: 1px solid {BORDER}; }}
QSplitter::handle {{ background-color: {WINDOW}; width: 4px; height: 4px; }}
QSlider::groove:horizontal {{ height: 4px; background-color: #272D35; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background-color: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 12px; margin: -4px 0; border-radius: 6px; background-color: {TEXT};
}}
QSlider::handle:horizontal:disabled {{ background-color: #4C535D; }}
QProgressBar {{
    background-color: {INSET}; border: 1px solid {BORDER}; border-radius: 3px;
    min-height: 14px; max-height: 14px; text-align: center; color: {MUTED}; font-size: 11px;
}}
QProgressBar::chunk {{ background-color: {ACCENT}; }}
QStatusBar {{ background-color: {PANEL}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QStatusBar::item {{ border: none; }}
QToolTip {{ background-color: #222831; color: {TEXT}; border: 1px solid #3A424E; padding: 5px; }}
QScrollBar:vertical {{ background: {WINDOW}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #343B45; min-height: 28px; border-radius: 3px; margin: 2px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: {WINDOW}; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #343B45; min-width: 28px; border-radius: 3px; margin: 2px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
"""


def apply_theme(application: QApplication) -> None:
    """Apply the single neutral desktop theme without changing Qt behavior."""
    application.setFont(QFont("Microsoft YaHei UI", 9))
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)
