"""Human-readable batch rename rule builder."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QComboBox,QLineEdit,QCheckBox,QTableWidget,QTableWidgetItem,QHeaderView,
    QMessageBox,QWidget,QScrollArea)
from .rules import OPTIONS
from .preview import preview
from .history import History

class RuleRow(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner=owner
        layout=QHBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        self.enabled=QCheckBox(); self.enabled.setChecked(True)
        self.kind=QComboBox(); self.kind.addItems(OPTIONS)
        self.a=QLineEdit(); self.b=QLineEdit()
        self.case=QComboBox(); self.case.addItems(["lower","upper","title","sentence","snake","kebab","camel"])
        for w in (self.enabled,self.kind,self.a,self.b,self.case): layout.addWidget(w)
        for text,fn in (("↑",lambda:owner.move_rule(self,-1)),("↓",lambda:owner.move_rule(self,1)),("×",lambda:owner.remove_rule(self))):
            btn=QPushButton(text); layout.addWidget(btn); btn.clicked.connect(fn)
        self.kind.currentTextChanged.connect(self.configure)
        self.enabled.toggled.connect(owner.update_preview)
        self.a.textChanged.connect(owner.update_preview)
        self.b.textChanged.connect(owner.update_preview)
        self.case.currentTextChanged.connect(owner.update_preview)
        self.configure()
    def configure(self,*_):
        kind=self.kind.currentText()
        a,b=OPTIONS[kind]
        self.a.setVisible(bool(a) and kind!="Change capitalization")
        self.b.setVisible(bool(b))
        self.case.setVisible(kind=="Change capitalization")
        self.a.setPlaceholderText(a); self.b.setPlaceholderText(b)
        self.owner.update_preview()
    def value(self):
        return self.kind.currentText(), self.case.currentText() if self.kind.currentText()=="Change capitalization" else self.a.text(), self.b.text()

class BatchRenameDialog(QDialog):
    def __init__(self,parent,directory,paths=None):
        super().__init__(parent)
        self.setWindowTitle("Batch Rename + Regex")
        self.resize(1050,650)
        self.directory=Path(directory)
        self.scope=None if paths is None else [Path(p) for p in paths]
        self.history=History(parent.size_db)
        self.rows=[]; self.excluded=set(); self.pairs=[]
        self.loading=False
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel(str(self.directory)))
        self.recursive=QCheckBox("Include subdirectories")
        self.recursive.setEnabled(paths is None)
        self.recursive.toggled.connect(self.reload)
        layout.addWidget(self.recursive)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setMaximumHeight(200)
        rulebox=QWidget(); self.rulelayout=QVBoxLayout(rulebox); self.rulelayout.setAlignment(Qt.AlignTop)
        scroll.setWidget(rulebox); layout.addWidget(scroll)
        add=QPushButton("Add rule"); add.clicked.connect(self.add_rule); layout.addWidget(add)
        self.table=QTableWidget(0,4)
        self.table.setHorizontalHeaderLabels(["Include","Original filename","Proposed filename","Status"])
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch)
        self.table.itemChanged.connect(self.toggle_file)
        layout.addWidget(self.table)
        self.status=QLabel(); layout.addWidget(self.status)
        buttons=QHBoxLayout(); layout.addLayout(buttons)
        self.undo=QPushButton("Undo last batch"); self.undo.clicked.connect(self.undo_batch)
        self.apply=QPushButton("Apply verified rename"); self.apply.clicked.connect(self.apply_batch)
        close=QPushButton("Close"); close.clicked.connect(self.accept)
        buttons.addWidget(self.undo); buttons.addStretch(); buttons.addWidget(self.apply); buttons.addWidget(close)
        self.paths=[]; self.add_rule(); self.reload()
    def add_rule(self):
        row=RuleRow(self); self.rows.append(row); self.rulelayout.addWidget(row); self.update_preview()
    def remove_rule(self,row):
        self.rows.remove(row); self.rulelayout.removeWidget(row); row.deleteLater(); self.update_preview()
    def move_rule(self,row,delta):
        i=self.rows.index(row); j=i+delta
        if 0<=j<len(self.rows):
            self.rows.pop(i); self.rows.insert(j,row)
            self.rulelayout.removeWidget(row); self.rulelayout.insertWidget(j,row); self.update_preview()
    def reload(self,*_):
        from services.recycle import NETWORK_TRASH
        candidates=self.scope if self.scope is not None else (self.directory.rglob("*") if self.recursive.isChecked() else self.directory.iterdir())
        self.paths=sorted((p for p in candidates if p.is_file() and not p.is_symlink() and p.resolve() not in {self.history.db.resolve(), Path(str(self.history.db)+chr(45)+chr(119)+chr(97)+chr(108)).resolve(), Path(str(self.history.db)+chr(45)+chr(115)+chr(104)+chr(109)).resolve()} and NETWORK_TRASH.casefold() not in [x.casefold() for x in p.parts] and not p.name.startswith(".triexplorer-rename-")),key=lambda p:str(p).casefold())
        self.update_preview()
    def toggle_file(self,item):
        if self.loading or item.column()!=0: return
        path=self.paths[item.row()]
        if item.checkState()==Qt.Checked: self.excluded.discard(path)
        else: self.excluded.add(path)
        self.update_preview()
    def update_preview(self,*_):
        if not hasattr(self,"table") or not hasattr(self,"paths"): return
        included=[p for p in self.paths if p not in self.excluded]
        rules=[r.value() for r in self.rows if r.enabled.isChecked()]
        self.pairs,errors=preview(included,rules)
        targets=dict(self.pairs)
        self.loading=True
        self.table.setRowCount(len(self.paths))
        for i,p in enumerate(self.paths):
            check=QTableWidgetItem(); check.setFlags(Qt.ItemIsEnabled|Qt.ItemIsUserCheckable); check.setCheckState(Qt.Unchecked if p in self.excluded else Qt.Checked)
            self.table.setItem(i,0,check)
            target=targets.get(p,p)
            status="Excluded" if p in self.excluded else errors.get(str(p),"Ready" if str(target)!=str(p) else "Unchanged")
            for col,value in enumerate((str(p.relative_to(self.directory)),str(target.relative_to(self.directory)),status),1):
                item=QTableWidgetItem(value); item.setFlags(Qt.ItemIsEnabled|Qt.ItemIsSelectable); self.table.setItem(i,col,item)
        self.loading=False
        count=sum(str(a)!=str(b) for a,b in self.pairs)
        self.status.setText(f"{count} changes · {len(errors)} validation errors")
        self.apply.setEnabled(bool(count) and not errors)
    def apply_batch(self):
        changes=[(a,b) for a,b in self.pairs if str(a)!=str(b)]
        if QMessageBox.question(self,"Confirm rename",f"Rename {len(changes)} files?")!=QMessageBox.Yes: return
        try:
            records=self.history.apply(self.directory,changes)
            if self.scope is not None:
                mapping={Path(a):Path(b) for a,b,_ in records}; self.scope=[mapping.get(p,p) for p in self.scope]
            self.excluded.clear()
            for row in list(self.rows): self.remove_rule(row)
            self.add_rule(); self.reload()
        except Exception as e: QMessageBox.warning(self,"Rename failed",str(e))
    def undo_batch(self):
        if QMessageBox.question(self,"Undo rename","Reverse the last completed batch in this directory?")!=QMessageBox.Yes: return
        try:
            if not self.history.undo(self.directory): QMessageBox.information(self,"Undo","No completed batch found.")
            self.scope=None; self.excluded.clear(); self.reload()
        except Exception as e: QMessageBox.warning(self,"Undo failed",str(e))
