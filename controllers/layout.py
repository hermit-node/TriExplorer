"""Explorer feature methods. Implemented on the shared Explorer instance.

These mixins do not construct UI independently; Explorer composes the controllers.
"""
from __future__ import annotations
from appcore import (
    PATH_ROLE, PreviewLabel, QAction, QApplication, QEasingCurve, QFrame, QHBoxLayout,
    QKeySequence, QLabel, QLineEdit, QPropertyAnimation, QSplitter, QToolButton,
    QTreeWidget, QVBoxLayout, QWidget, Qt, create_table_pane
)

class LayoutMixin:
    def tool(self, text, callback, icon=None, tip=None):
        b = QToolButton()
        b.setText(text)
        if icon:
            b.setIcon(self.icons[icon])
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        b.setToolTip(tip or text)
        b.clicked.connect(callback)
        return b


    def heading(self, text, note=None):
        box = QWidget()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(10, 7, 10, 4)
        label = QLabel(text, objectName="heading")
        layout.addWidget(label)
        layout.addStretch()
        if note is not None:
            layout.addWidget(note)
        return box


    def panel(self):
        frame = QFrame(objectName="pane")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(3)
        return frame, layout


    def build(self):
        shell = QWidget(objectName="shell")
        self.setCentralWidget(shell)
        root = QVBoxLayout(shell)
        root.setContentsMargins(10, 8, 10, 4)
        root.setSpacing(8)
        nav = QHBoxLayout()
        self.back_button = self.tool("Back", self.back, "back", "Alt+Left")
        self.forward_button = self.tool("Forward", self.forward, "forward", "Alt+Right")
        self.up_button = self.tool("Up", self.up, "up", "Alt+Up")
        self.home_button = self.tool("Home", self.go_home, "pc", "This PC (Alt+Home)")
        for b in (self.back_button, self.forward_button, self.up_button, self.home_button,
                  self.tool("", self.refresh, "refresh", "Refresh (F5)")):
            nav.addWidget(b)
        self.address = QLineEdit()
        self.address.setPlaceholderText("Enter a folder path")
        self.address.returnPressed.connect(lambda: self.navigate(self.address.text()))
        nav.addWidget(self.address, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter this folder...")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(235)
        self.search.textChanged.connect(self.filter_changed)
        nav.addWidget(self.search)
        root.addLayout(nav)
        actions = QHBoxLayout()
        for text, fn in (("New folder", self.new_folder), ("Copy", self.copy),
                         ("Cut", self.cut), ("Paste", self.paste), ("Rename", self.rename),
                         ("Recycle", self.delete), ("Restore", self.restore_recycled), ("Create ZIP", self.create_zip),
                         ("Pin folder", self.pin)):
            actions.addWidget(self.tool(text, fn))
        actions.addStretch()
        hidden = self.tool("Hidden items", self.toggle_hidden)
        hidden.setCheckable(True)
        hidden.setChecked(self.show_hidden)
        actions.addWidget(hidden)
        self.preview_button = QToolButton()
        self.preview_button.setText("Image preview")
        self.preview_button.setCheckable(True)
        self.preview_button.setToolTip("Show or hide image preview (Alt+P)")
        actions.addWidget(self.preview_button)
        root.addLayout(actions)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(7)
        root.addWidget(self.splitter, 1)
        side, side_layout = self.panel()
        side.setMinimumWidth(180)
        self.quick = QTreeWidget()
        self.quick.setHeaderHidden(True)
        self.quick.setRootIsDecorated(False)
        self.quick.setIndentation(0)
        self.quick.setContextMenuPolicy(Qt.CustomContextMenu)
        self.quick.customContextMenuRequested.connect(self.quick_menu)
        self.quick.itemClicked.connect(lambda item, _: self.navigate(item.data(0, PATH_ROLE)))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tree_menu)
        self.tree.setIndentation(17)
        self.tree.itemExpanded.connect(self.load_tree)
        self.tree.itemClicked.connect(lambda item, _: self.navigate(item.data(0, PATH_ROLE))
        if item.data(0, PATH_ROLE) is not None else None)
        self.side_splitter = QSplitter(Qt.Vertical)
        self.side_splitter.setChildrenCollapsible(False)
        self.side_splitter.setHandleWidth(7)
        quick_panel = QWidget()
        quick_layout = QVBoxLayout(quick_panel)
        quick_layout.setContentsMargins(0, 0, 0, 0)
        quick_layout.setSpacing(2)
        quick_layout.addWidget(self.heading("Quick access"))
        quick_layout.addWidget(self.quick, 1)
        self.quick.setMinimumHeight(42)
        tree_panel = QWidget()
        tree_layout = QVBoxLayout(tree_panel)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.setSpacing(2)
        tree_layout.addWidget(self.heading("Folders"))
        tree_layout.addWidget(self.tree, 1)
        self.tree.setMinimumHeight(70)
        self.side_splitter.addWidget(quick_panel)
        self.side_splitter.addWidget(tree_panel)
        self.side_splitter.setSizes([220, 500])
        self.side_splitter.setStretchFactor(0, 0)
        self.side_splitter.setStretchFactor(1, 1)
        side_layout.addWidget(self.side_splitter, 1)
        self.splitter.addWidget(side)
        self.views = []
        self.proxies = []
        self.counts = []
        self.mode_selectors = []
        for i, default_mode in enumerate(("default", "archives")):
            table = create_table_pane(
                i, default_mode, self.settings, self.model, self.show_hidden, self
            )
            pane, view, proxy, count, selector = (
                table.widget, table.view, table.proxy, table.count, table.selector
            )
            view.doubleClicked.connect(lambda idx, v=view: self.double_click_entry(v, idx))
            view.clicked.connect(lambda _, v=view: self.set_active(v))
            view.selectionModel().selectionChanged.connect(
                lambda *_args, v=view: self.selection_changed(v)
            )
            view.customContextMenuRequested.connect(lambda pos, v=view: self.context(v, pos))
            view.dropped.connect(
                lambda paths, target, move: self.do_transfer(paths, target or self.current, move)
            )
            view.archive_dropped.connect(
                lambda paths, target: self.do_extract(paths, target or self.current, remove_source=True)
            )
            self.splitter.addWidget(pane)
            self.views.append(view)
            self.proxies.append(proxy)
            self.counts.append(count)
            self.mode_selectors.append(selector)
            selector.currentIndexChanged.connect(lambda _, idx=i: self.change_pane_mode(idx))
        self.active_view = self.views[0]
        self.preview_panel, preview_layout = self.panel()
        self.preview_panel.setMinimumWidth(0)
        close_preview = self.tool("Hide", lambda: self.preview_button.setChecked(False))
        preview_layout.addWidget(self.heading("Image preview", close_preview))
        self.preview_label = PreviewLabel()
        preview_layout.addWidget(self.preview_label, 1)
        self.preview_details = QLabel("", objectName="muted")
        self.preview_details.setWordWrap(True)
        self.preview_details.setContentsMargins(9, 4, 9, 8)
        preview_layout.addWidget(self.preview_details)
        self.splitter.addWidget(self.preview_panel)
        self.splitter.setStretchFactor(3, 0)
        self.preview_panel.hide()
        self.preview_animation = QPropertyAnimation(self.preview_panel, b"maximumWidth", self)
        self.preview_animation.setDuration(150)
        self.preview_animation.setEasingCurve(QEasingCurve.OutCubic)
        self.preview_animation.finished.connect(self.preview_animation_finished)
        self.preview_button.toggled.connect(self.set_preview_open)
        self.splitter.setSizes([245, 680, 510, 0])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setStretchFactor(2, 2)
        self.build_quick()
        for key, fn in (("Alt+Left", self.back), ("Alt+Right", self.forward), ("Alt+Up", self.up),
                        ("F5", self.refresh), ("Alt+P", lambda: self.preview_button.toggle()),
                        ("Alt+Home", self.go_home), ("Ctrl+L", self.focus_address),
                        ("Ctrl+F", self.search.setFocus), ("Ctrl+Shift+N", self.new_folder),
                        ("F2", self.rename), ("Delete", self.delete), ("Ctrl+C", self.copy),
                        ("Ctrl+X", self.cut), ("Ctrl+V", self.paste), ("Return", self.open_selected),
                        ("Backspace", self.up), ("Ctrl+A", self.select_all)):
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            if key in ("Return", "Backspace", "Ctrl+A", "Ctrl+C", "Ctrl+X", "Ctrl+V", "Delete", "F2"):
                action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
                for view in self.views:
                    view.addAction(action)
            else:
                self.addAction(action)
            action.triggered.connect(fn)


    def editing(self):
        return isinstance(QApplication.focusWidget(), QLineEdit)


    def focus_address(self):
        self.address.setFocus()
        self.address.selectAll()


    def change_pane_mode(self, i):
        if self.rename_editor is not None:
            self.end_rename()
        mode = self.mode_selectors[i].currentData()
        proxy = self.proxies[i]
        proxy.set_mode(mode)
        if mode in ("alphabetical", "archive_matches"):
            self.views[i].sortByColumn(0, Qt.AscendingOrder)
        self.update_counts()
        self.settings.setValue(f"pane/{i}/mode", mode)
        if mode == "archive_matches":
            self.scan_archive_matches()
        elif not any(p.mode == "archive_matches" for p in self.proxies):
            self.cancel_archive_matches()
        if mode == "duplicates":
            self.scan_duplicate_view()
        elif not any(p.mode == "duplicates" for p in self.proxies):
            self.cancel_duplicate_view()
            if self.size_job is None:
                self.refresh_folder_sizes()
