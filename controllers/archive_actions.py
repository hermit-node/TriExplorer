"""Explorer feature methods. Implemented on the shared Explorer instance.

These mixins do not construct UI independently; Explorer composes the controllers.
"""
from __future__ import annotations
from appcore import (
    Path, QInputDialog, QLineEdit, extract_one
)

class ArchiveActionsMixin:
    def do_extract(self, paths, destination, remove_source=False):
        if not paths or not destination:
            return
        self.extract_queue.extend((path, destination, remove_source) for path in paths)
        if not self.extract_running:
            self.extract_running = True
            self.next_archive()


    def next_archive(self):
        if not self.extract_queue:
            self.extract_running = False
            errors, self.extract_errors = self.extract_errors, []
            self.refresh()
            self.refresh_tree_branch()
            if errors:
                self.warn("\n".join(errors))
            return
        self.try_archive(self.extract_queue.popleft(), self.zip_password)


    def ask_zip_password(self, path, rejected):
        message = ("The previous password did not work." if rejected else "This ZIP is encrypted.")
        text, ok = QInputDialog.getText(self, "ZIP password",
                                        f"{Path(path).name}\n{message}\nEnter password (Cancel skips this archive):",
                                        QLineEdit.Password)
        return text.encode("utf-8") if ok and text else None


    def try_archive(self, item, password):
        path, destination, remove_source = item
        self.statusBar().showMessage(f"Extracting {Path(path).name}...")

        def done(result, error):
            if error:
                self.extract_errors.append(f"{Path(path).name}: {error}")
            elif result.password_required:
                replacement = self.ask_zip_password(path, password is not None)
                if replacement is not None:
                    self.try_archive(item, replacement)
                    return
                # A skipped archive is not deleted. Keep the last successful password.
                self.extract_errors.append(f"{Path(path).name}: skipped (password required)")
            else:
                if result.encrypted:
                    self.zip_password = password
                if result.error:
                    self.extract_errors.append(result.error)
            self.next_archive()

        self.submit(lambda: extract_one(path, destination, remove_source, password), done)


    def extract_zip(self):
        entries = self.selected()
        if not entries or any(e.folder or not e.name.lower().endswith(".zip") for e in entries):
            self.warn("Select ZIP archives. Other formats open in the associated application.")
            return
        self.do_extract([e.path for e in entries], self.current)
