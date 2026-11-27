"""Explorer feature methods. Implemented on the shared Explorer instance.

These mixins do not construct UI independently; Explorer composes the controllers.
"""
from __future__ import annotations
from appcore import (
    Event, LOADED_ROLE, PATH_ROLE, PC, QApplication, QSortFilterProxyModel,
    QTreeWidgetItem, folder_sizes, normalized, os, scan, sqlite3
)

class NavigationMixin:
    def navigate(self, path, record=True):
        if self.rename_busy:
            return
        if self.rename_editor is not None:
            self.end_rename(refresh=False)
        if path is None:
            return
        path = os.path.expandvars(os.path.expanduser(path.strip().strip('"')))
        if path.casefold() in ("this pc", "my pc", "computer"):
            path = PC
        if path:
            path = os.path.abspath(path)
        if record and normalized(path) == normalized(self.current) and self.current is not None:
            return
        self.current = path
        if record:
            del self.history[self.history_index + 1:]
            self.history.append(path)
            self.history_index = len(self.history) - 1
        self.address.setText(path or "This PC")
        self.search.clear()
        self.back_button.setEnabled(self.history_index > 0)
        self.forward_button.setEnabled(self.history_index < len(self.history) - 1)
        self.up_button.setEnabled(bool(path))
        self.sync_tree()
        self.load_folder(clear=True)


    def go_home(self):
        self.navigate(PC)


    def back(self):
        if self.rename_busy:
            return
        if self.history_index > 0:
            self.history_index -= 1
            self.navigate(self.history[self.history_index], False)


    def forward(self):
        if self.rename_busy:
            return
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            self.navigate(self.history[self.history_index], False)


    def up(self):
        if self.editing() and QApplication.focusWidget() is self.search:
            return
        if self.current:
            parent = os.path.dirname(self.current)
            self.navigate(parent if parent != self.current else PC)


    def refresh_tree_branch(self):
        item = self.tree.currentItem()
        if item and normalized(item.data(0, PATH_ROLE)) == normalized(self.current):
            item.setData(0, LOADED_ROLE, None)
            item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            if item.isExpanded():
                self.load_tree(item)


    def queue_watcher_refresh(self, path):
        # Coalesce event bursts without perpetually postponing refresh on busy shares.
        if self.watcher_enabled and normalized(path) == normalized(self.current) and self.current:
            if not self.refresh_timer.isActive():
                self.refresh_timer.start()

    def refresh_from_watcher(self):
        # Keep at most one automatic directory reload in flight; never interrupt renaming.
        if self.folder_load_token is not None or self.rename_editor is not None or self.rename_busy:
            self.refresh_timer.start()
            return
        if self.watcher_enabled:
            self.load_folder(from_watcher=True)


    def refresh(self):
        self.cancel_duplicate_view()
        self.cancel_archive_matches()
        self.load_folder()


    def load_folder(self, clear=False, from_watcher=False):
        if self.rename_editor is not None or self.rename_busy:
            return
        path = self.current
        if path is None:
            return
        if clear:
            self.refresh_timer.stop()
            self.cancel_duplicate_view()
            self.cancel_archive_matches()
            self.model.archive_notes = {}
            self.archive_pairs = []
            self.cancel_size_scan()
            self.size_attempted.clear()
        self.generation += 1
        token = self.generation
        self.folder_load_token = token
        selected = [[e.path for e in self.selected(v)] for v in self.views] if not clear else [[], []]
        if clear:
            self.model.replace([])
            self.update_counts()
        if not from_watcher:
            self.statusBar().showMessage("Loading " + (path or "This PC") + "...")
        obsolete = [p for p in self.watcher.directories() if normalized(p) != normalized(path)]
        if obsolete:
            self.watcher.removePaths(obsolete)

        def done(result, error):
            if self.folder_load_token == token:
                self.folder_load_token = None
            if token != self.generation:
                return
            if error:
                if from_watcher:
                    self.statusBar().showMessage("Automatic refresh failed: " + error)
                    return
                self.model.replace([])
                self.update_counts()
                self.statusBar().showMessage("Cannot read folder: " + error)
                return
            entries, sizes = result
            if from_watcher and set(entries) == set(self.model.entries):
                # SMB can emit notifications without a visible listing change.
                # Do not reset/sort both models or initiate further scans.
                return
            self.model.replace(entries, sizes)
            for view, paths in zip(self.views, selected):
                if paths:
                    wanted = set(paths)
                    proxy = view.model()
                    for row in range(proxy.rowCount()):
                        i = proxy.index(row, 0)
                        if i.data(PATH_ROLE) in wanted:
                            view.selectionModel().select(i, view.selectionModel().SelectionFlag.Select |
                                                         view.selectionModel().SelectionFlag.Rows)
            if self.watcher_enabled and path and not any(normalized(p) == normalized(path) for p in self.watcher.directories()):
                self.watcher.addPath(path)
            self.update_counts()
            self.schedule_preview()
            if from_watcher:
                self.refresh_tree_branch()
            self.statusBar().showMessage(path or "This PC")
            if any(p.mode == "duplicates" for p in self.proxies):
                self.scan_duplicate_view(force=False)
            if any(p.mode == "archive_matches" for p in self.proxies):
                self.scan_archive_matches(force=False)
            if self.size_job is None:
                self.refresh_folder_sizes()

        previous_entries = frozenset(self.model.entries) if from_watcher else None

        def list_with_sizes():
            entries = scan(path)
            if previous_entries is not None and frozenset(entries) == previous_entries:
                return entries, {}
            try:
                sizes = folder_sizes.lookup(self.size_db,
                                            [(e.path, e.modified) for e in entries if e.folder]) if path else {}
            except (OSError, ValueError, sqlite3.Error):
                sizes = {}
            return entries, sizes

        self.submit(list_with_sizes, done)


    def cancel_size_scan(self):
        if self.size_job is not None:
            self.size_job.set()
            self.size_job = None


    def refresh_folder_sizes(self, force=False, only=None):
        self.cancel_size_scan()
        if not self.current or self.duplicate_job is not None or self.duplicate_cleanup or self.archive_match_job is not None:
            return  # Never compete with a duplicate scan or recursively walk This PC.
        paths = []
        for e in self.model.entries:
            if not e.folder or (only and normalized(e.path) != normalized(only)):
                continue
            if e.hidden and not self.show_hidden and not force:
                continue
            k = normalized(e.path)
            cached = self.model.folder_sizes.get(k)
            if force or ((not cached or cached.stale) and k not in self.size_attempted):
                paths.append(e.path)
        if not paths:
            return
        stop = Event()
        self.size_job = stop
        self.statusBar().showMessage("Calculating uncached/stale folder sizes...")

        def progress(path, value, error):
            if self.size_job is not stop or stop.is_set():
                return
            if value is not None:
                self.model.set_folder_size(path, value)
                self.size_attempted.discard(path)
            else:
                self.size_attempted.add(path)

        def done(result, error):
            if self.size_job is not stop or stop.is_set():
                return
            self.size_job = None
            if error:
                self.statusBar().showMessage("Folder size cache: " + error)
            elif result:
                self.statusBar().showMessage(f"Folder sizes: {len(result)} incomplete; right-click to retry")
            else:
                self.statusBar().showMessage("Folder sizes up to date")

        self.submit(lambda emit: folder_sizes.refresh(self.size_db, paths, stop.is_set, emit), done, progress)


    def filter_changed(self, text):
        if self.rename_editor is not None and not self.rename_busy:
            self.end_rename()
        for proxy in self.proxies:
            if hasattr(proxy, "endFilterChange"):
                proxy.beginFilterChange()
                proxy.query = text.casefold()
                proxy.endFilterChange(QSortFilterProxyModel.Direction.Rows)
            else:
                proxy.query = text.casefold()
                proxy.invalidateFilter()
        self.update_counts()


    def toggle_hidden(self, checked):
        self.show_hidden = checked
        self.settings.setValue("hidden", checked)
        for proxy in self.proxies:
            if hasattr(proxy, "endFilterChange"):
                proxy.beginFilterChange()
                proxy.hidden = checked
                proxy.endFilterChange(QSortFilterProxyModel.Direction.Rows)
            else:
                proxy.hidden = checked
                proxy.invalidateFilter()
        self.rebuild_tree()
        self.update_counts()
        if any(p.mode == "duplicates" for p in self.proxies):
            self.scan_duplicate_view()
        if any(p.mode == "archive_matches" for p in self.proxies):
            self.scan_archive_matches()


    def update_counts(self):
        for proxy, label in zip(self.proxies, self.counts):
            if proxy.mode == "archive_matches":
                label.setText(self.archive_match_progress or "ZIP / folder matches")
                label.setToolTip(
                    "\n".join(self.archive_match_errors[:30]) or "Hover matched rows for verification details")
            elif proxy.mode == "duplicates" and self.duplicate_job is not None:
                label.setText(f"{proxy.rowCount():,} matches | {self.duplicate_progress or 'Scanning...'}")
                label.setToolTip("Checking exact SHA-256 matches; results appear as groups finish")
            else:
                text = f"{proxy.rowCount():,} items"
                errors = self.duplicate_scan_errors if proxy.mode == "duplicates" else []
                if errors:
                    text += f" ({len(errors)} skipped)"
                label.setText(text)
                label.setToolTip("\n".join(errors[:30]))
