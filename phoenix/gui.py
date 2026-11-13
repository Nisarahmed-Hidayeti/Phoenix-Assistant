"""Native desktop entry point."""
import os
import sqlite3
import time
import unicodedata
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPushButton, QPlainTextEdit, QVBoxLayout, QWidget,
)

from .desktop import append_kate
from .engine import Engine
from .files import Files, Grant
from .model import Llama
from .storage import Storage


class Events(QObject):
    result = Signal(str)
    status = Signal(str)
    activity = Signal(str, str)
    approval = Signal(str, str, object)


class Window(QWidget):
    def __init__(self, model, storage, approval=None):
        super().__init__()
        self.model, self.storage = model, storage
        self.approval = approval or self.request_approval
        self.grants = {}
        self.stop = None
        self.close_requested = False
        self.events = Events()
        self.events.result.connect(self.finished)
        self.events.status.connect(self.set_status)
        self.events.activity.connect(self.record)
        self.events.approval.connect(self.show_approval)
        self.setWindowTitle("Phoenix Assistant")
        self.resize(740, 680)
        self.status = QLabel("Ready")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.grant_label = QLabel("No folders granted")
        self.grant_label.setWordWrap(True)
        self.grant_label.setTextFormat(Qt.TextFormat.PlainText)
        self.chat = QPlainTextEdit(readOnly=True)
        self.chat.setAccessibleName("Conversation")
        self.chat.document().setMaximumBlockCount(1000)
        self.prompt = QLineEdit(placeholderText="Ask Phoenix to do something")
        self.prompt.setMaxLength(2000)
        self.prompt.returnPressed.connect(self.send)
        self.history = QListWidget()
        self.history.setAccessibleName("Action history")
        for stamp, action, outcome in reversed(self.storage.history()):
            self.history.addItem(f"{stamp} UTC  {action}  {outcome}")
        self.send_button = self.button("Send", self.send)
        self.stop_button = self.button("■ Stop", self.cancel)
        self.stop_button.setStyleSheet("font-weight: bold; padding: 7px 18px;")
        self.stop_button.setEnabled(False)
        self.controls = [
            self.button("Grant folder", self.grant),
            self.button("Revoke folders", self.revoke),
            self.button("Kate append", self.kate),
            self.button("Memories", self.memories),
            self.send_button,
        ]
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        for control in self.controls[:-1]:
            bar.addWidget(control)
        bar.addStretch()
        bar.addWidget(self.stop_button)
        layout.addLayout(bar)
        layout.addWidget(self.status)
        layout.addWidget(self.grant_label)
        layout.addWidget(self.chat, 3)
        compose = QHBoxLayout()
        compose.addWidget(self.prompt)
        compose.addWidget(self.send_button)
        layout.addLayout(compose)
        layout.addWidget(QLabel("Action history"))
        layout.addWidget(self.history, 1)

    def button(self, text, callback):
        button = QPushButton(text)
        button.clicked.connect(callback)
        return button

    def set_status(self, status):
        if self.stop is not None and not self.stop.is_set():
            self.status.setText(status)

    def record(self, action, outcome):
        self.storage.log(action, outcome)
        self.history.addItem(f"{action}  {outcome}")
        if self.history.count() > 100:
            self.history.takeItem(0)
        self.history.scrollToBottom()

    def grant(self):
        selected = QFileDialog.getExistingDirectory(self, "Grant access to a folder")
        if not selected:
            return
        alias, ok = QInputDialog.getText(self, "Folder alias", "Use this name in chat",
                                        text=Path(selected).name)
        alias = alias.strip()
        if ok and alias and len(alias) <= 60 and alias.replace("-", "").replace("_", "").isalnum():
            try:
                grant = Grant(selected)
            except OSError as exc:
                QMessageBox.warning(self, "Folder grant", str(exc))
                return
            previous = self.grants.get(alias)
            if isinstance(previous, Grant):
                previous.close()
            self.grants[alias] = grant
            self.show_grants()

    def show_grants(self):
        self.grant_label.setText("\n".join(f"{key} → {self.grant_path(path)}" for key, path in self.grants.items())
                                 or "No folders granted")

    @staticmethod
    def grant_path(grant):
        return grant.path if isinstance(grant, Grant) else Path(grant)

    def revoke(self):
        for grant in self.grants.values():
            if isinstance(grant, Grant):
                grant.close()
        self.grants.clear()
        self.show_grants()

    def start(self, task):
        if self.stop is not None:
            return
        stop = self.stop = threading.Event()
        grants = self.grants.copy()
        for control in self.controls:
            control.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status.setText("Working")
        def worker():
            try:
                files = Files(grants)
                try:
                    result = task(files, stop)
                finally:
                    files.close()
            except Exception as exc:
                result = str(exc) or type(exc).__name__
            self.events.result.emit(result)
        threading.Thread(target=worker, daemon=True).start()

    def send(self):
        if self.stop is not None:
            return
        request = self.prompt.text().strip()
        if not request:
            return
        self.prompt.clear()
        if request.lower() == "remember this" or request.lower().startswith(("remember this:", "remember this ")):
            preference = request[len("remember this"):].lstrip(" :").strip()
            if preference:
                self.storage.remember(preference)
                self.record("memory", "saved")
                self.chat.appendPlainText("Phoenix\nPreference saved.")
            else:
                self.chat.appendPlainText("Phoenix\nWhat would you like me to remember?")
            return
        self.chat.appendPlainText("You\n" + request)
        preferences = [value for _, value in self.storage.memories()[:10]]
        self.start(lambda files, stop: Engine(self.model, files, stop, self.events.activity.emit,
                                              approve=self.approval).run(request, preferences))

    def request_approval(self, action, details):
        done = threading.Event()
        response = {"event": done, "approved": False, "deadline": time.monotonic() + 55}
        self.events.approval.emit(action, details, response)
        while not done.wait(.05):
            if self.stop.is_set() or time.monotonic() >= response["deadline"]:
                return False
        return response["approved"]

    def show_approval(self, action, details, response):
        from PySide6.QtCore import QTimer
        if self.stop is None or self.stop.is_set() or time.monotonic() >= response["deadline"]:
            response["event"].set()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Confirm Phoenix action")
        dialog.resize(590, 330)
        layout = QVBoxLayout(dialog)
        heading = QLabel(action)
        heading.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(heading)
        visible = "".join(
            f"\\u{ord(char):04x}" if char != "\n" and unicodedata.category(char) in {"Cc", "Cf"} else char
            for char in details
        )
        preview = QPlainTextEdit(visible, readOnly=True)
        layout.addWidget(preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        QTimer.singleShot(max(0, int((response["deadline"] - time.monotonic()) * 1000)), dialog, dialog.reject)
        response["approved"] = dialog.exec() == QDialog.DialogCode.Accepted
        response["event"].set()

    def finished(self, result):
        stopped = self.stop.is_set()
        self.stop = None
        self.chat.appendPlainText("Phoenix\n" + result)
        self.status.setText("Stopped" if stopped else "Ready")
        self.stop_button.setEnabled(False)
        for control in self.controls:
            control.setEnabled(True)
        if self.close_requested:
            self.close()

    def cancel(self):
        if self.stop:
            self.stop.set()
            self.status.setText("Stopping")
            self.stop_button.setEnabled(False)

    def closeEvent(self, event):
        if self.stop:
            self.close_requested = True
            self.cancel()
            event.ignore()
        else:
            self.revoke()
            self.storage.db.close()
            event.accept()

    def memories(self):
        rows = self.storage.memories()
        if not rows:
            QMessageBox.information(self, "Memories", "Type “remember this:” followed by a preference to save it.")
            return
        labels = [f"{key}  {value}" for key, value in rows]
        label, ok = QInputDialog.getItem(self, "Memories", "Select a preference", labels, editable=False)
        if not ok:
            return
        key, old = rows[labels.index(label)]
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Saved preference")
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(old)
        edit = dialog.addButton("Edit", QMessageBox.ButtonRole.ActionRole)
        delete = dialog.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()
        if dialog.clickedButton() == edit:
            value, accepted = QInputDialog.getText(self, "Edit memory", "Preference", text=old)
            if accepted and value.strip():
                self.storage.remember(value.strip()[:2000], key)
                self.record("memory", "edited")
        elif dialog.clickedButton() == delete:
            self.storage.forget(key)
            self.record("memory", "deleted")

    def kate(self):
        if not self.grants:
            QMessageBox.information(self, "Kate append", "Grant the folder containing the text file first.")
            return
        grant, ok = QInputDialog.getItem(self, "Kate append", "Authorized folder", list(self.grants), editable=False)
        if not ok:
            return
        selected, _ = QFileDialog.getOpenFileName(self, "Choose a text document", str(self.grant_path(self.grants[grant])))
        if not selected:
            return
        target = Path(selected)
        root = self.grant_path(self.grants[grant])
        if not target.is_relative_to(root):
            QMessageBox.warning(self, "Kate append", "Choose a file inside the authorized folder.")
            return
        line, ok = QInputDialog.getText(self, "Kate append", "Line to append (lowercase letters and spaces)")
        if not ok:
            return
        confirm = QMessageBox(self)
        confirm.setWindowTitle("Confirm Kate edit")
        confirm.setTextFormat(Qt.TextFormat.PlainText)
        confirm.setText(f"Append and save this line in Kate?\n\n{target}\n\n{line}")
        confirm.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return
        def edit(files, stop):
            self.events.activity.emit("kate_append", "running")
            try:
                result = append_kate(files, grant, str(target.relative_to(root)), line, stop,
                                     self.events.status.emit)
            except Exception:
                self.events.activity.emit("kate_append", "interrupted" if stop.is_set() else "failed")
                raise
            self.events.activity.emit("kate_append", "completed")
            return result
        self.start(edit)


def main():
    app = QApplication([])
    app.setApplicationName("Phoenix Assistant")
    state = Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local/state")) / "phoenix"
    try:
        model = Llama()
        storage = Storage(state / "phoenix.sqlite3")
    except (ValueError, OSError, sqlite3.Error) as exc:
        QMessageBox.critical(None, "Phoenix Assistant", str(exc))
        return 1
    window = Window(model, storage)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
