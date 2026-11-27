"""Explorer feature methods. Implemented on the shared Explorer instance.

These mixins do not construct UI independently; Explorer composes the controllers.
"""
from __future__ import annotations
from appcore import (
    LOADED_ROLE, PATH_ROLE, PC, QFileDialog, QMenu, QStandardPaths, QTreeWidgetItem, Qt,
    normalized, os, scan
)

RECYCLE_ROLE = int(PATH_ROLE) + 20

class SidebarMixin:
    def quick_activated(self, item, _column=0):
        if item.data(0, RECYCLE_ROLE):
            self.open_recycle_bin()
        else:
            self.navigate(item.data(0, PATH_ROLE))

    def open_recycle_bin(self):
        if self.rename_busy:
            return
        from recycle_dialog import RecycleDialog
        RecycleDialog(self).exec()

    def build_quick(self):
        self.quick.clear()
        defaults = [("This PC", PC)]
        for name, location in (("Home", QStandardPaths.HomeLocation),
                               ("Desktop", QStandardPaths.DesktopLocation),
                               ("Documents", QStandardPaths.DocumentsLocation),
                               ("Downloads", QStandardPaths.DownloadLocation),
                               ("Pictures", QStandardPaths.PicturesLocation)):
            path = QStandardPaths.writableLocation(location)
            if path:
                defaults.append((name, path))
        self.default_pins = {normalized(path): name for name, path in defaults}
        hidden = set(self.hidden_default_pins)
        locations = [(name, path) for name, path in defaults if normalized(path) not in hidden]
        seen = {normalized(path) for _, path in locations}
        for path in self.pins:
            key = normalized(path)
            if key not in seen and key not in hidden:
                locations.append((self.default_pins.get(key, os.path.basename(path.rstrip("\\/")) or path), path))
                seen.add(key)
        self.visible_pins = seen
        for name, path in locations:
            item = QTreeWidgetItem([name])
            item.setIcon(0, self.icons["pc" if not path else "folder"])
            item.setData(0, PATH_ROLE, path)
            item.setToolTip(0, path or "This PC")
            self.quick.addTopLevelItem(item)
        recycle_item = QTreeWidgetItem(["Recycle Bin"])
        recycle_item.setData(0, RECYCLE_ROLE, True)
        recycle_item.setIcon(0, self.style().standardIcon(self.style().StandardPixmap.SP_TrashIcon))
        recycle_item.setToolTip(0, "Recover or permanently delete recycled files")
        index = 1 if locations and locations[0][1] == PC else 0
        self.quick.insertTopLevelItem(index, recycle_item)


    def pin_action(self, menu, path):
        if path is None:
            return
        if normalized(path) in self.visible_pins:
            menu.addAction("Unpin from Quick Access", lambda: self.unpin(path))
        else:
            menu.addAction("Pin to Quick Access", lambda: self.add_pin(path))


    def quick_menu(self, pos):
        item = self.quick.itemAt(pos)
        menu = QMenu(self)
        if item and item.data(0, RECYCLE_ROLE):
            menu.addAction("Open Recycle Bin", self.open_recycle_bin)
            menu.exec(self.quick.viewport().mapToGlobal(pos))
            return
        if item and item.data(0, PATH_ROLE) is not None:
            self.pin_action(menu, item.data(0, PATH_ROLE))
            menu.addSeparator()
        if self.current is not None and normalized(self.current) not in self.visible_pins:
            menu.addAction("Pin current folder", self.pin)
        menu.addAction("Add pinned folder...", self.browse_pin)
        if self.hidden_default_pins:
            menu.addAction("Restore default shortcuts", self.restore_default_pins)
        menu.exec(self.quick.viewport().mapToGlobal(pos))


    def tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item:
            menu = QMenu(self)
            self.pin_action(menu, item.data(0, PATH_ROLE))
            if not menu.isEmpty():
                menu.exec(self.tree.viewport().mapToGlobal(pos))


    def save_pins(self):
        self.settings.setValue("pins", self.pins)
        self.settings.setValue("hidden_default_pins", self.hidden_default_pins)
        if self.settings.sync() is False:
            self.warn("Settings could not be saved: " + self.settings.last_error)
        self.build_quick()


    def add_pin(self, path):
        if path is None:
            return
        path = os.path.abspath(path) if path else PC
        key = normalized(path)
        if key in self.visible_pins:
            return
        self.hidden_default_pins = [p for p in self.hidden_default_pins if p != key]
        if key not in self.default_pins and not any(normalized(p) == key for p in self.pins):
            self.pins.append(path)
        self.save_pins()


    def browse_pin(self):
        path = QFileDialog.getExistingDirectory(self, "Add pinned folder", self.current or "")
        if path:
            self.add_pin(path)


    def unpin(self, path):
        key = normalized(path)
        self.pins = [p for p in self.pins if normalized(p) != key]
        if key in self.default_pins and key not in self.hidden_default_pins:
            self.hidden_default_pins.append(key)
        self.save_pins()


    def restore_default_pins(self):
        self.hidden_default_pins = []
        self.save_pins()


    def pin(self):
        self.add_pin(self.current)


    def rebuild_tree(self):
        self.tree_epoch += 1
        self.tree.clear()
        self.pc_item = QTreeWidgetItem(["This PC"])
        self.pc_item.setData(0, PATH_ROLE, PC)
        self.pc_item.setIcon(0, self.icons["pc"])
        self.pc_item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        self.tree.addTopLevelItem(self.pc_item)
        self.pc_item.setExpanded(True)
        self.load_tree(self.pc_item)


    def load_tree(self, item):
        if item.data(0, LOADED_ROLE):
            return
        path = item.data(0, PATH_ROLE)
        if path is None:
            return
        item.setData(0, LOADED_ROLE, "loading")
        epoch = self.tree_epoch

        def done(entries, error):
            if epoch != self.tree_epoch:
                return
            # A removed item can be invalidated while a worker is running.
            try:
                if item.treeWidget() is not self.tree:
                    return
            except RuntimeError:
                return
            item.takeChildren()
            if error:
                item.setData(0, LOADED_ROLE, None)
                item.setToolTip(0, error)
                return
            for e in sorted((e for e in entries if e.folder and (self.show_hidden or not e.hidden)),
                            key=lambda e: e.name.casefold()):
                child = QTreeWidgetItem([e.name])
                child.setData(0, PATH_ROLE, e.path)
                child.setIcon(0, self.icons["drive" if path == PC else "folder"])
                child.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
                child.setToolTip(0, e.path)
                item.addChild(child)
            item.setData(0, LOADED_ROLE, "loaded")
            if not item.childCount():
                item.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
            self.sync_tree()

        self.submit(lambda: scan(path), done)


    def sync_tree(self):
        target = normalized(self.current)
        item = self.pc_item
        while item is not None:
            path = normalized(item.data(0, PATH_ROLE))
            if path == target:
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                return
            if item.data(0, LOADED_ROLE) != "loaded":
                item.setExpanded(True)
                self.load_tree(item)
                return
            match = None
            for i in range(item.childCount()):
                child = item.child(i)
                p = normalized(child.data(0, PATH_ROLE))
                try:
                    if os.path.commonpath([p, target]) == p:
                        match = child
                        break
                except ValueError:
                    pass
            if match is None:
                return
            item.setExpanded(True)
            item = match
