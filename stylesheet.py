from PySide6.QtGui import QPalette, QColor


def import_palette():
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#f3f5f8"))
    palette.setColor(QPalette.WindowText, QColor("#202a39"))
    palette.setColor(QPalette.Base, QColor("#ffffff"))
    palette.setColor(QPalette.Text, QColor("#202a39"))
    palette.setColor(QPalette.Button, QColor("#f3f5f8"))
    palette.setColor(QPalette.ButtonText, QColor("#202a39"))
    palette.setColor(QPalette.Highlight, QColor("#dceaff"))
    palette.setColor(QPalette.HighlightedText, QColor("#152d50"))
    return palette

STYLE = """
QWidget { font-family: 'Segoe UI'; font-size: 10pt; }
QMainWindow, QWidget#shell { background: #f3f5f8; color: #202a39; }
QFrame#pane { background: #ffffff; border: 1px solid #dbe1e9; border-radius: 7px; }
QLabel#heading { font-size: 11pt; font-weight: 600; color: #23354b; }
QLabel#muted { color: #657388; font-size: 9pt; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 5px; padding: 5px 9px; }
QToolButton:hover { background: #e6edf7; border-color: #d4e0ee; }
QToolButton:pressed, QToolButton:checked { background: #d5e5fa; }
QToolButton:disabled { color: #a0aab8; }
QLineEdit { background: white; border: 1px solid #cdd6e1; border-radius: 5px; padding: 6px 9px; selection-background-color: #3478c9; }
QLineEdit:focus { border-color: #3478c9; }
QTreeView, QTreeWidget { border: none; background: #ffffff; alternate-background-color: #f7f9fc; outline: 0; selection-background-color: #dceaff; selection-color: #152d50; }
QTreeView::item, QTreeWidget::item { min-height: 27px; padding: 2px; }
QTreeView::item:hover, QTreeWidget::item:hover { background: #edf3fb; }
QTreeView::item:selected, QTreeWidget::item:selected { background: #dceaff; color: #152d50; }
QHeaderView::section { background: #f4f6f9; color: #526174; border: none; border-bottom: 1px solid #dce2eb; padding: 6px; font-size: 9pt; }
QSplitter::handle { background: transparent; }
QSplitter::handle:hover { background: #b9cde7; }
QMenu { background: white; border: 1px solid #d5dce7; padding: 4px; }
QMenu::item { padding: 6px 24px 6px 10px; }
QMenu::item:selected { background: #dceaff; color: #152d50; }
QStatusBar { color: #536174; background: #f3f5f8; }
"""
