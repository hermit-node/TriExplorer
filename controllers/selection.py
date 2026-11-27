"""Explorer feature methods. Implemented on the shared Explorer instance.

These mixins do not construct UI independently; Explorer composes the controllers.
"""
from __future__ import annotations
from appcore import (
    QApplication, QDesktopServices, QMenu, QMessageBox, QUrl, readable_size
)

class SelectionMixin:
    def set_active(self, view):
        self.active_view = view
        self.schedule_preview()


    def selected_view(self):
        focus = QApplication.focusWidget()
        for view in self.views:
            if focus is view or (focus and view.isAncestorOf(focus)):
                self.active_view = view
        return self.active_view


    def selection_changed(self, view):
        if view.hasFocus():
            self.active_view = view
        if view is self.active_view:
            self.schedule_preview()
            entries = self.selected(view)
            if entries:
                self.statusBar().showMessage(
                    f"{len(entries)} selected  |  {readable_size(sum(e.size or 0 for e in entries))}")


    def selected(self, view=None):
        view = view or self.selected_view()
        proxy = view.model()
        return [self.model.entry(proxy.mapToSource(i)) for i in view.selectionModel().selectedRows()]


    def select_all(self):
        if not self.editing():
            self.selected_view().selectAll()


    def open_entry(self, view, index):
        self.set_active(view)
        e = self.model.entry(view.model().mapToSource(index))
        if e:
            if e.folder:
                self.navigate(e.path)
            elif not QDesktopServices.openUrl(QUrl.fromLocalFile(e.path)):
                self.warn("Could not open " + e.path)


    def open_selected(self):
        if not self.editing():
            view = self.selected_view()
            rows = view.selectionModel().selectedRows()
            if rows:
                self.open_entry(view, rows[0])


    def warn(self, text):
        QMessageBox.warning(self, self.app_name, text)


    def context(self, view, pos):
        self.set_active(view)
        index = view.indexAt(pos)
        if index.isValid() and not view.selectionModel().isSelected(index):
            view.clearSelection()
            view.selectionModel().select(index, view.selectionModel().SelectionFlag.Select |
                                         view.selectionModel().SelectionFlag.Rows)
        menu = QMenu(self)
        actions = [("Open", self.open_selected), ("Copy", self.copy), ("Cut", self.cut),
                   ("Paste", self.paste), ("Rename", self.rename), ("Recycle", self.delete),
                   ("Copy path", self.copy_path), ("Create ZIP", self.create_zip),
                   ("Extract ZIP to folder", self.extract_zip), ("New folder", self.new_folder),
                   ("Open in Windows Explorer", self.open_location), ("Refresh", self.refresh)]
        for label, fn in actions:
            a = menu.addAction(label)
            a.triggered.connect(fn)
        entry = self.model.entry(view.model().mapToSource(index)) if index.isValid() else None
        if self.current:
            menu.addAction("Batch Rename + Regex", lambda: self.batch_rename(view, entry))
        if entry and entry.folder:
            menu.addSeparator()
            self.pin_action(menu, entry.path)
        elif not index.isValid() and self.current is not None:
            menu.addSeparator()
            self.pin_action(menu, self.current)
        if self.current:
            menu.addSeparator()
            menu.addAction("Recalculate folder sizes", lambda: self.refresh_folder_sizes(True))
            if entry and entry.folder:
                menu.addAction("Recalculate this folder size", lambda: self.refresh_folder_sizes(True, entry.path))
            if entry and not entry.folder:
                keep_action = menu.addAction("Keep this file, recycle SHA-256 duplicates",
                                             lambda: self.cleanup_duplicates(preferred=entry.path))
                keep_action.setEnabled(not self.duplicate_cleanup)
            dup = menu.addMenu("Duplicates (SHA-256)")
            dup.setEnabled(not self.duplicate_cleanup)
            dup.addAction("Find duplicates", lambda: self.show_duplicates(view))
            dup.addAction("Find ZIP / ISO / folder matches", lambda: self.show_archive_matches(view))
            dup.addAction("Verify ZIP / folder matches with password...", lambda: self.show_archive_matches(view, True))
            if self.archive_match_errors:
                dup.addAction("Show archive/folder scan errors",
                              lambda: self.warn("\n".join(self.archive_match_errors[:100])))
            if self.duplicate_scan_errors:
                dup.addAction("Show skipped files / scan errors",
                              lambda: self.warn("\n".join(self.duplicate_scan_errors[:100])))
            if view.model().mode == "archive_matches":
                cleanup = dup.addAction("Recycle matching archives - keep folders", lambda: self.cleanup_archive_pairs())
                cleanup.setEnabled(self.archive_match_job is None and bool(self.archive_pairs))
                if entry:
                    action = menu.addAction("Recycle matching archive(s) - keep folder",
                                            lambda: self.cleanup_archive_pairs(entry.path))
                    action.setEnabled(self.archive_match_job is None and not self.duplicate_cleanup)
                exact = dup.addAction("Recycle identical files - keep one", lambda: self.cleanup_duplicates())
            else:
                dup.addAction("Recycle duplicates - keep one", lambda: self.cleanup_duplicates())
            dup.addAction("Recycle duplicates recursively...", lambda: self.cleanup_duplicates(True))
            if entry and not entry.folder:
                dup.addAction("Keep this file, recycle matching copies",
                              lambda: self.cleanup_duplicates(preferred=entry.path))
        menu.exec(view.viewport().mapToGlobal(pos))
