"""Bounded, session-local history of the Explorer status bar."""
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QToolButton, QVBoxLayout,
)


class StatusHistory(QFrame):
    MAX_LINES = 500

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pane")
        self.setMinimumHeight(90)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(4)
        header = QHBoxLayout()
        header.addWidget(QLabel("Status history", objectName="heading"))
        header.addStretch()
        self.copy_button = QToolButton(text="Copy all")
        self.clear_button = QToolButton(text="Clear")
        header.addWidget(self.copy_button)
        header.addWidget(self.clear_button)
        layout.addLayout(header)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setUndoRedoEnabled(False)
        self.output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.output.setMaximumBlockCount(self.MAX_LINES)
        self.output.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.output.setStyleSheet(
            "QPlainTextEdit { background: #202832; color: #e0e7ef; "
            "border: 1px solid #384553; border-radius: 4px; padding: 4px; }"
        )
        layout.addWidget(self.output, 1)
        self.copy_button.clicked.connect(self.copy_all)
        self.clear_button.clicked.connect(self.output.clear)

    def record(self, message):
        if not message or not message.strip():
            return
        bar = self.output.verticalScrollBar()
        follow = bar.value() >= bar.maximum()
        # Keep the selection and reading position while the user reviews old output.
        old_position = bar.value()
        old_count = self.output.document().blockCount()
        text = " ".join(message.splitlines())
        line = f"[{datetime.now():%H:%M:%S}] {text}"
        cursor = QTextCursor(self.output.document())
        cursor.movePosition(QTextCursor.End)
        if not self.output.document().isEmpty():
            cursor.insertBlock()
        cursor.insertText(line)
        if follow and not self.output.textCursor().hasSelection():
            bar.setValue(bar.maximum())
        else:
            dropped = max(0, old_count + 1 - self.MAX_LINES)
            bar.setValue(max(0, old_position - dropped))

    def copy_all(self):
        QApplication.clipboard().setText(self.output.toPlainText())
