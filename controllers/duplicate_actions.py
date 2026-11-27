"""Explorer feature methods. Implemented on the shared Explorer instance.

These mixins do not construct UI independently; Explorer composes the controllers.
"""
from __future__ import annotations
from appcore import (
    Event, Path, QInputDialog, QLineEdit, QMessageBox, Qt, archive_matches, deque,
    duplicates, folder_sizes, normalized, recycle, sqlite3, zip_members, zipfile
)

class DuplicateActionsMixin:
    def cancel_duplicate_view(self):
        self.duplicate_scan_key = None
        if self.duplicate_job is not None:
            self.duplicate_job.set()
            self.duplicate_job = None


    def show_duplicates(self, view):
        i = self.views.index(view)
        selector = self.mode_selectors[i]
        if selector.currentData() == "duplicates":
            self.scan_duplicate_view()
        else:
            selector.setCurrentIndex(selector.findData("duplicates"))


    def scan_duplicate_view(self, force=True):
        scan_key = (normalized(self.current), self.show_hidden, tuple(sorted(
            (normalized(e.path), e.size if e.size is not None else -1,
             e.modified if e.modified is not None else -1, e.hidden)
            for e in self.model.entries if not e.folder)))
        if not force and self.duplicate_scan_key == scan_key:
            return
        self.cancel_duplicate_view()
        self.duplicate_scan_key = scan_key
        for proxy in self.proxies:
            proxy.duplicate_paths = set()
            if proxy.mode == "duplicates":
                proxy.invalidate()
        self.update_counts()
        if not self.current or self.duplicate_cleanup or not any(p.mode == "duplicates" for p in self.proxies):
            return
        path = self.current
        self.cancel_size_scan()
        stop = Event()
        self.duplicate_job = stop
        self.duplicate_progress = "Finding candidates..."
        self.duplicate_scan_errors = []
        self.update_counts()
        self.statusBar().showMessage("Checking matching file sizes and SHA-256...")

        def done(result, error):
            if stop.is_set() or self.duplicate_job is not stop:
                return
            self.duplicate_job = None
            self.duplicate_progress = None
            self.duplicate_scan_errors = [error] if error else result.errors
            if error:
                self.update_counts()
                self.statusBar().showMessage("Duplicate scan failed: " + error)
                self.refresh_folder_sizes()
                return
            paths = {normalized(item.path) for keep, copies in result.groups for item in [keep, *copies]}
            for proxy in self.proxies:
                proxy.duplicate_paths = paths
                if proxy.mode == "duplicates":
                    proxy.invalidate()
            self.update_counts()
            self.statusBar().showMessage(f"{len(result.groups)} duplicate groups; {len(paths)} matching files"
                                         + (f"; {len(result.errors)} skipped/errors" if result.errors else ""))
            self.refresh_folder_sizes()

        def progress(checked, total, new_groups):
            if self.duplicate_job is stop and not stop.is_set():
                if new_groups:
                    paths = {normalized(item.path) for keep, copies in new_groups for item in [keep, *copies]}
                    for proxy in self.proxies:
                        proxy.duplicate_paths.update(paths)
                        if proxy.mode == "duplicates":
                            proxy.invalidate()
                self.duplicate_progress = f"SHA: {checked:,}/{total:,}"
                self.update_counts()

        hidden = self.show_hidden
        self.submit(lambda emit: duplicates.find_duplicates(path, show_hidden=hidden,
                                                            cancelled=stop.is_set,
                                                            progress=lambda n, total: emit(n, total, None),
                                                            matches=lambda groups, n, total: emit(n, total, groups),
                                                            cache_db=self.size_db), done, progress)


    def cancel_archive_matches(self):
        self.archive_match_key = None
        if self.archive_match_job is not None:
            self.archive_match_job.set()
            self.archive_match_job = None


    def set_archive_containment(self, enabled):
        self.archive_contained = enabled
        if any(p.mode == "archive_matches" for p in self.proxies):
            self.scan_archive_matches()


    def show_archive_matches(self, view, with_password=False):
        selector = self.mode_selectors[self.views.index(view)]
        if with_password:
            password, ok = QInputDialog.getText(self, "Verify ZIP contents", "ZIP password:", QLineEdit.Password)
            if not ok:
                return
            selector.blockSignals(True)
            selector.setCurrentIndex(selector.findData("archive_matches"))
            selector.blockSignals(False)
            self.proxies[self.views.index(view)].set_mode("archive_matches")
            self.containment_button.show()
            view.sortByColumn(0, Qt.AscendingOrder)
            self.settings.setValue(f"pane/{self.views.index(view)}/mode", "archive_matches")
            self.scan_archive_matches(password_override=password.encode("utf-8"))
        elif selector.currentData() == "archive_matches":
            self.scan_archive_matches()
        else:
            selector.setCurrentIndex(selector.findData("archive_matches"))


    def scan_archive_matches(self, force=True, password_override=None):
        if self.duplicate_cleanup or not self.current or not any(p.mode == "archive_matches" for p in self.proxies):
            return
        scan_key = (normalized(self.current), self.show_hidden, self.archive_contained, tuple(sorted(
            (normalized(e.path), e.size if e.size is not None else -1,
             e.modified if e.modified is not None else -1)
            for e in self.model.entries)))
        if not force and self.archive_match_key == scan_key:
            return
        self.cancel_archive_matches()
        self.cancel_size_scan()
        self.archive_match_key = scan_key
        self.model.archive_notes = {}
        self.archive_pairs = []
        for proxy in self.proxies:
            proxy.archive_match_paths.clear()
            if proxy.mode == "archive_matches":
                proxy.invalidate()
        entries = [e for e in self.model.entries if self.show_hidden or not e.hidden]
        folders = [e.path for e in entries if e.folder]
        archives = [(e.path, e.size) for e in entries if
                    not e.folder and e.name.lower().endswith((".zip", ".iso")) and e.size is not None]
        sizes = dict(self.model.folder_sizes)
        allow_contained = self.archive_contained
        password = self.zip_password if password_override is None else password_override
        stop = Event()
        self.archive_match_job = stop
        self.archive_match_progress = "Screening file sizes (70%)..."
        self.archive_match_errors = []
        self.update_counts()

        def add(match):
            for item, other in ((match.archive, match.folder), (match.folder, match.archive)):
                key = normalized(item)
                note = f"{Path(other).name}: {match.status}"
                self.model.archive_notes[key] = (self.model.archive_notes.get(key, "") + "\n" + note).strip()
                for proxy in self.proxies:
                    proxy.archive_match_paths.add(key)
                    if proxy.mode == "archive_matches":
                        proxy.invalidate()

        def progress(n, total, match):
            if self.archive_match_job is not stop or stop.is_set():
                return
            if match:
                self.archive_pairs.append(match)
                add(match)
            self.archive_match_progress = f"Archive: {n}/{total}" if total else "Screening file sizes..."
            self.update_counts()

        def done(result, error):
            if self.archive_match_job is not stop or stop.is_set():
                return
            self.archive_match_job = None
            self.archive_match_errors = [error] if error else result[1]
            if not error:
                matches = result[0]
                self.archive_pairs = matches
                if password is not None and any(m.verified and m.encrypted for m in matches):
                    self.zip_password = password
                for path, size in result[2].items():
                    self.model.set_folder_size(path, size)
                self.archive_match_progress = f"{sum(m.verified for m in matches)} verified / {len(matches)} pairs"
            else:
                self.archive_match_progress = "Scan failed"
            self.update_counts()
            self.refresh_folder_sizes()

        folder_rows = [(e.path, e.modified) for e in entries if e.folder]

        def run(emit):
            matches, errors = archive_matches.find_matches(folders, archives, sizes, self.size_db,
                                                           zip_members, password, stop.is_set,
                                                           lambda n, total, m: emit(n, total, m),
                                                           allow_contained=allow_contained)
            try:
                updated = folder_sizes.lookup(self.size_db, folder_rows)
            except (OSError, sqlite3.Error):
                updated = {}
            return matches, errors, updated

        self.submit(run, done, progress)


    def cleanup_archive_pairs(self, preferred=None):
        if self.duplicate_cleanup or not self.current:
            return
        if self.archive_match_job is not None:
            self.statusBar().showMessage("Wait for the ZIP / folder scan to finish before cleanup.")
            return
        pairs = [m for m in self.archive_pairs if m.recyclable and (self.archive_contained or not m.contained)]
        if preferred:
            pairs = [m for m in pairs if normalized(preferred) in (normalized(m.archive), normalized(m.folder))]
        if not pairs:
            self.statusBar().showMessage("No ZIP / folder pairs to clean up.")
            return
        grouped = {}
        for match in pairs:
            grouped.setdefault(normalized(match.archive), []).append(match)
        queue = deque(grouped.values())
        self.cancel_duplicate_view()
        self.cancel_archive_matches()
        self.cancel_size_scan()
        self.duplicate_cleanup = True
        removed = 0
        issues = []
        total = len(queue)

        def finish():
            self.duplicate_cleanup = False
            self.refresh()
            self.refresh_tree_branch()
            message = f"Recycled {removed} matching archive(s); extracted folders kept."
            self.statusBar().showMessage(message)
            if issues:
                self.warn(message + "\n\n" + "\n".join(issues))

        def next_group():
            if not queue:
                finish()
                return
            group = queue.popleft()
            verify(group, self.zip_password)

        def verify(group, password):
            name = Path(group[0].archive).name
            self.statusBar().showMessage(f"Verifying {total - len(queue)}/{total}: {name}")

            def run():
                pending = None
                errors = []
                for match in group:
                    try:
                        before = duplicates.record(match.archive).signature
                        checked = archive_matches.compare(match.archive, match.folder, zip_members, password,
                                                          allow_contained=match.contained)
                        if checked and checked.verified and checked.recyclable:
                            if duplicates.record(match.archive).signature != before:
                                raise ValueError("Archive changed after verification")
                            failures = recycle([match.archive])
                            return (not failures, checked.encrypted, None, failures)
                        if checked and checked.password_required:
                            pending = checked
                        elif checked:
                            errors.append(name + ": " + checked.status)
                    except Exception as exc:
                        errors.append(name + ": " + str(exc))
                return (False, False, pending, errors)

            def done(result, error):
                nonlocal removed
                if error:
                    issues.append(name + ": " + error)
                else:
                    success, encrypted, pending, errors = result
                    if pending is not None:
                        replacement = self.ask_zip_password(pending.archive, password is not None)
                        if replacement is not None:
                            verify(group, replacement)
                            return
                        issues.append(name + ": skipped; password required")
                    elif success:
                        removed += 1
                        if encrypted:
                            self.zip_password = password
                    else:
                        issues.extend(errors or [name + ": no longer fully verified; retained"])
                next_group()

            self.submit(run, done)

        next_group()


    def cleanup_duplicates(self, recursive=False, preferred=None):
        if not self.current or self.duplicate_cleanup:
            return
        if recursive and QMessageBox.question(self, self.app_name,
                                              f"Recycle exact duplicates in this folder AND every subfolder?\n{self.current}\n"
                                              "One file per SHA-256 group will remain. Continue?",
                                              QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        path = self.current
        show_hidden = self.show_hidden
        self.cancel_duplicate_view()
        self.cancel_size_scan()
        self.duplicate_cleanup = True
        self.statusBar().showMessage("Checking sizes and SHA-256; recycling exact duplicates...")

        def run():
            result = duplicates.find_duplicates(path, recursive, preferred, show_hidden)
            return duplicates.remove_duplicates(result, recycle)

        def done(result, error):
            self.duplicate_cleanup = False
            if error:
                self.warn(error)
            elif result[1]:
                self.warn(f"Recycled {result[0]} duplicate(s). Skipped/errors:\n" + "\n".join(result[1]))
            self.refresh()
            self.refresh_tree_branch()
            if not error:
                self.statusBar().showMessage(f"Recycled {result[0]} duplicate(s)")

        self.submit(run, done)
