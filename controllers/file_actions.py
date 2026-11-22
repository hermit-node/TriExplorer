"""Explorer feature methods. Implemented on the shared Explorer instance.

These mixins do not construct UI independently; Explorer composes the controllers.
"""
from __future__ import annotations
from appcore import (
    PATH_ROLE, Path, QApplication, QDesktopServices, QInputDialog, QMessageBox,
    QMimeData, QUrl, RenameEdit, archive_name, deque, make_zip, network_root,
    normalized, os, recycle, replace, restore_share_item, share_trash_entries, transfer,
    valid_name
)

class FileActionsMixin:
    def copy_path(self):
        paths = [e.path for e in self.selected()]
        if paths:
            QApplication.clipboard().setText("\n".join(paths))


    def open_location(self):
        if self.current:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.current))


    def clipboard(self, cut=False):
        if self.editing():
            return
        paths = [e.path for e in self.selected() if e.path != os.path.abspath(os.path.splitdrive(e.path)[0] + os.sep)]
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        # Explorer's Preferred DropEffect clipboard format.
        mime.setData('application/x-qt-windows-mime;value="Preferred DropEffect"',
                     bytes([2 if cut else 1, 0, 0, 0]))
        QApplication.clipboard().setMimeData(mime)
        self.cut_paths = paths if cut else []
        self.statusBar().showMessage(f"{len(paths)} items {'cut' if cut else 'copied'}")


    def copy(self):
        self.clipboard()


    def cut(self):
        self.clipboard(True)


    def paste(self):
        if self.editing() or not self.current:
            return
        mime = QApplication.clipboard().mimeData()
        if mime and mime.hasUrls():
            paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
            effect = bytes(mime.data('application/x-qt-windows-mime;value="Preferred DropEffect"'))
            move = paths == self.cut_paths or (len(effect) >= 4 and int.from_bytes(effect[:4], "little") == 2)
            self.do_transfer(paths, self.current, move)


    def operation(self, label, fn, after=None):
        self.statusBar().showMessage(label + "...")

        def done(result, error):
            if error or result:
                self.warn(error or "\n".join(result))
            elif after:
                after()
            self.refresh()
            self.refresh_tree_branch()

        self.submit(fn, done)


    def do_transfer(self, paths, target, move):
        if not target or not paths:
            return
        if move and not QMessageBox.question(self, self.app_name, f"Move {len(paths)} item(s) to:\n{target}") == QMessageBox.Yes:
            return
        self.operation("Moving" if move else "Copying", lambda: transfer(paths, target, move),
                       lambda: self.cut_paths.clear() if move else None)


    def new_folder(self):
        if not self.current:
            return
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if ok:
            if not valid_name(name):
                self.warn("Invalid folder name")
                return
            path = os.path.join(self.current, name)
            self.operation("Creating folder", lambda: os.mkdir(path))


    def double_click_entry(self, view, index):
        entry = self.model.entry(view.model().mapToSource(index))
        if entry and entry.folder:
            self.open_entry(view, index)
        elif entry:
            self.begin_rename(view, index)


    def begin_rename(self, view, index):
        if not self.current or self.rename_busy or self.duplicate_cleanup:
            return
        if self.rename_editor is not None:
            self.end_rename(refresh=False)
        proxy = view.model()
        entry = self.model.entry(proxy.mapToSource(index))
        if entry is None or entry.folder:
            return
        self.generation += 1  # discard any listing prepared before editing
        self.cancel_duplicate_view()
        self.cancel_archive_matches()
        self.cancel_size_scan()
        self.rename_view = view
        self.active_view = view
        # Follow the original visible order even if a new name sorts elsewhere.
        self.rename_queue = deque(
            e.path for row in range(index.row(), proxy.rowCount())
            if (e := self.model.entry(proxy.mapToSource(proxy.index(row, 0)))) and not e.folder)
        self.next_rename()


    def next_rename(self):
        view = self.rename_view
        if view is None:
            return
        proxy = view.model()
        while self.rename_queue:
            path = self.rename_queue.popleft()
            for row in range(proxy.rowCount()):
                index = proxy.index(row, 0)
                if normalized(index.data(PATH_ROLE)) != normalized(path):
                    continue
                entry = self.model.entry(proxy.mapToSource(index))
                if entry is None or entry.folder:
                    continue
                self.rename_path = entry.path
                view.setCurrentIndex(index)
                view.selectionModel().select(index, view.selectionModel().SelectionFlag.ClearAndSelect |
                                             view.selectionModel().SelectionFlag.Rows)
                view.scrollTo(index)
                editor = RenameEdit(view.viewport())
                editor.setObjectName("inlineRename")
                editor.setStyleSheet("QLineEdit {padding: 1px 3px; border-radius: 2px;}")
                editor.setText(entry.name)
                rect = view.visualRect(index)
                editor.setGeometry(rect.adjusted(19, 0, 0, 0) if rect.width() > 55 else rect)
                self.rename_editor = editor
                editor.commit_name.connect(lambda ed=editor: self.commit_rename(ed))
                editor.cancel_name.connect(lambda ed=editor: self.cancel_rename_editor(ed))
                editor.show()
                editor.setFocus()
                stem = Path(entry.name).stem
                editor.setSelection(0, len(stem))
                return
        self.end_rename()


    def cancel_rename_editor(self, editor):
        if self.rename_editor is editor and not self.rename_busy:
            self.end_rename()


    def remove_rename_editor(self):
        editor, self.rename_editor = self.rename_editor, None
        if editor is not None:
            editor.hide()
            editor.deleteLater()


    def end_rename(self, refresh=True):
        if self.rename_busy:
            return
        self.remove_rename_editor()
        view, self.rename_view = self.rename_view, None
        self.rename_path = None
        self.rename_queue.clear()
        if refresh and view is not None:
            self.refresh()


    def commit_rename(self, editor):
        if self.rename_editor is not editor or self.rename_busy:
            return
        path = self.rename_path
        name = editor.text()
        if not valid_name(name) or getattr(os.path, "isreserved", lambda n: False)(name):
            editor.setStyleSheet("QLineEdit {border: 1px solid #c33; padding: 1px 3px;}")
            self.statusBar().showMessage("Invalid filename; correct it or press Escape.")
            return
        if name == os.path.basename(path):
            self.remove_rename_editor()
            self.next_rename()
            return
        dest = os.path.join(os.path.dirname(path), name)
        self.rename_busy = True
        editor.setReadOnly(True)
        self.statusBar().showMessage("Renaming " + os.path.basename(path) + "...")

        def work():
            if os.path.lexists(dest) and normalized(path) != normalized(dest):
                raise FileExistsError("Already exists: " + name)
            os.rename(path, dest)
            return dest

        def done(result, error):
            self.rename_busy = False
            if error:
                if self.rename_editor is editor:
                    editor.setReadOnly(False)
                    editor.setStyleSheet("QLineEdit {border: 1px solid #c33; padding: 1px 3px;}")
                    editor.setFocus()
                self.statusBar().showMessage("Rename failed: " + error)
                return
            self.remove_rename_editor()
            for row, entry in enumerate(self.model.entries):
                if normalized(entry.path) == normalized(path):
                    self.model.entries[row] = replace(entry, path=dest, name=name,
                                                      archive=archive_name(name),
                                                      hidden=entry.hidden or name.startswith("."))
                    old_key, new_key = normalized(path), normalized(dest)
                    for proxy in self.proxies:
                        for values in (proxy.duplicate_paths, proxy.archive_match_paths):
                            if old_key in values:
                                values.discard(old_key)
                                values.add(new_key)
                    self.model.dataChanged.emit(self.model.index(row, 0), self.model.index(row, 3), [])
                    break
            self.next_rename()

        self.submit(work, done)


    def rename(self):
        if self.editing() or not self.current:
            return
        entries = self.selected()
        if len(entries) != 1:
            return
        e = entries[0]
        if not e.folder:
            view = self.selected_view()
            self.begin_rename(view, view.selectionModel().selectedRows()[0])
            return
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=e.name)
        if not ok or name == e.name:
            return
        if not valid_name(name):
            self.warn("Invalid name")
            return
        dest = os.path.join(self.current, name)

        def work():
            if os.path.lexists(dest) and normalized(dest) != normalized(e.path):
                raise FileExistsError(dest)
            os.rename(e.path, dest)

        self.operation("Renaming", work)


    def delete(self):
        if self.editing() or not self.current:
            return
        paths = [e.path for e in self.selected()]
        if paths and QMessageBox.question(self, self.app_name,
                                          f"Move {len(paths)} item(s) to the Recycle Bin?") == QMessageBox.Yes:
            self.operation("Recycling", lambda: recycle(paths))


    def restore_recycled(self):
        root = network_root(self.current) if self.current else None
        if root is None:
            if os.name == "nt":
                os.startfile("shell:RecycleBinFolder")
            return

        def done(records, error):
            if error:
                self.warn(error)
                return
            if not records:
                self.statusBar().showMessage("No recoverable items on this share.")
                return
            labels = [f"{date}  |  {relative}  [{key[:8]}]" for date, relative, key in records]
            label, ok = QInputDialog.getItem(self, "Restore from network recycle",
                                             "Select an item to restore to its original location:", labels, 0, False)
            if ok:
                key = records[labels.index(label)][2]
                self.operation("Restoring", lambda: restore_share_item(root, key))

        self.submit(lambda: share_trash_entries(root), done)


    def create_zip(self):
        if not self.current:
            return
        paths = [e.path for e in self.selected()]
        if not paths:
            return
        dest, ok = QInputDialog.getText(self, "Create ZIP", "Archive name:",
                                        text=Path(paths[0]).stem + ".zip")
        if ok:
            if not valid_name(dest):
                self.warn("Invalid archive name")
                return
            path = os.path.join(self.current, dest if dest.lower().endswith(".zip") else dest + ".zip")
            self.operation("Compressing", lambda: make_zip(paths, path))
