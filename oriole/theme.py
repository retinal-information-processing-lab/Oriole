"""Colours and styles: Plerion's dark theme, the colour of each part and of what is drawn on the image."""
from __future__ import annotations

from PyQt6 import QtGui, QtWidgets

BG, CARD, TEXT, DIM, BTN, ACCENT = "#121212", "#1E1E1E", "#E0E0E0", "#555555", "#2A2A2A", "#2D6A9F"
PANEL_BG = "#181818"                                    # the three parts are cards on the window background
VISUAL_COLOUR, IMAGE_COLOUR, HOLO_COLOUR = "#2D6A9F", "#3A3A3A", "#7A3E9D"
WARN = "#FFD700"
SPOT = (255, 40, 40)
SLM_RING = "#C77DFF"                                    # the spot whose mask is on the SLM (holo purple, lighter)
NO_GO = "#FF4040"                                       # outside Wavefront's field: red line, hatched area
OUTER_CIRCLE, INNER_CIRCLE, CROSS = "#FF9A3C", "#FFE14D", "#C8C8C8"    # Wavefront's 20 %, 90 % circles, centre
LOG_COLOURS = {"info": "#00FF00", "warn": WARN, "error": "#FF4444", "alarm": "#FF4444"}   # alarm: big, bold
ACTION_STYLE = """
    QPushButton { background: #1A4A1A; color: #00FF00; border: none; border-radius: 4px;
                  font: bold 11pt 'Consolas'; padding: 10px; }
    QPushButton:hover { background: #2A6A2A; }
    QPushButton:disabled { background: #3A3A3A; color: #666666; }
"""
BLOCKED_STYLE = ACTION_STYLE.replace("#1A4A1A", "#4A4A1A").replace("#00FF00", WARN).replace("#2A6A2A", "#6A6A2A")
STOP_STYLE = ACTION_STYLE.replace("#1A4A1A", "#5A1414").replace("#00FF00", "#FF6060").replace("#2A6A2A", "#7A2020")


def apply_dark_theme(app: QtWidgets.QApplication):
    """Plerion's dark palette and style sheet."""
    R = QtGui.QPalette.ColorRole
    palette = QtGui.QPalette()
    for role, colour in ((R.Window, BG), (R.WindowText, TEXT), (R.Base, CARD), (R.AlternateBase, BG),
                         (R.ToolTipBase, CARD), (R.ToolTipText, TEXT), (R.Text, TEXT), (R.Button, BTN),
                         (R.ButtonText, TEXT), (R.BrightText, "#FF4444"), (R.Link, ACCENT),
                         (R.Highlight, ACCENT), (R.HighlightedText, "#FFFFFF")):
        palette.setColor(role, QtGui.QColor(colour))
    for role in (R.Text, R.ButtonText):
        palette.setColor(QtGui.QPalette.ColorGroup.Disabled, role, QtGui.QColor(DIM))
    app.setPalette(palette)
    app.setFont(QtGui.QFont("Segoe UI", 9))
    app.setStyleSheet("""
        QGroupBox { border: 1px solid #333; border-radius: 4px; margin-top: 10px;
                    padding: 12px 6px 6px 6px; font: bold 9pt 'Segoe UI'; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #E0E0E0; }
        QPushButton { border: none; border-radius: 3px; padding: 6px 14px; background: #2A2A2A; color: #E0E0E0; }
        QPushButton:hover { background: #3A3A3A; }
        QPushButton:pressed { background: #2D6A9F; }
        QPushButton:disabled { color: #555555; }
        QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
            border: 1px solid #333; border-radius: 3px; padding: 4px; background: #1E1E1E; color: #E0E0E0; }
        QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { width: 0; border: none; }
        QComboBox QAbstractItemView { background: #1E1E1E; color: #E0E0E0;
                                      selection-background-color: #2D6A9F; selection-color: #FFFFFF; }
        QToolTip { background: #1E1E1E; color: #E0E0E0; border: 1px solid #333; }
    """)
