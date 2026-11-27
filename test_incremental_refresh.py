import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import unittest
from dataclasses import replace
from PySide6.QtCore import Qt, QItemSelectionModel, QPersistentModelIndex
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication
from panels import Files, PaneFilter, FileView, PATH_ROLE
from file_types import Entry
from folder_sizes import FolderSize
app=QApplication.instance() or QApplication([])

class IncrementalRefreshTests(unittest.TestCase):
    def test_both_panes_preserve_current_and_selection(self):
        files=Files({})
        rows=[Entry(f"C:/folder/{n:05}.txt",f"{n:05}.txt",False,size=10,modified=1) for n in range(3000)]
        files.replace(rows)
        views=[]
        for mode in ("default","all"):
            proxy=PaneFilter(mode)
            proxy.setSourceModel(files)
            view=FileView()
            view.setModel(proxy)
            view.sortByColumn(0,Qt.AscendingOrder)
            selected=proxy.mapFromSource(files.index(1500,0))
            view.selectionModel().setCurrentIndex(selected,QItemSelectionModel.ClearAndSelect|QItemSelectionModel.Rows)
            views.append((view,proxy,QPersistentModelIndex(selected)))
        reset=QSignalSpy(files.modelReset)
        inserted=QSignalSpy(files.rowsInserted)
        removed=QSignalSpy(files.rowsRemoved)
        changed=QSignalSpy(files.dataChanged)
        updated=replace(rows[1500],size=123)
        incoming=[e for i,e in enumerate(rows) if i not in (5,6,1500)] + [updated,Entry("C:/folder/00001-new.txt","00001-new.txt",False,size=12,modified=1)]
        files.reconcile(list(reversed(incoming)),{})
        self.assertEqual(reset.count(),0)
        self.assertEqual(inserted.count(),1)
        self.assertEqual(removed.count(),1)
        self.assertEqual(changed.count(),1)
        self.assertEqual(len(files.entries),2999)
        for view,proxy,persistent in views:
            self.assertTrue(persistent.isValid())
            self.assertEqual(persistent.data(PATH_ROLE),updated.path)
            self.assertEqual(view.currentIndex().data(PATH_ROLE),updated.path)
            self.assertEqual([i.data(PATH_ROLE) for i in view.selectionModel().selectedRows()],[updated.path])

    def test_fresher_folder_size_is_not_overwritten(self):
        files=Files({})
        e=Entry("C:/folder/sub","sub",True,modified=1)
        from file_types import normalized
        key=normalized(e.path)
        fresh=FolderSize(200,20,1)
        stale=FolderSize(100,10,1)
        files.replace([e],{key:fresh})
        files.reconcile([replace(e,modified=2)],{key:stale})
        self.assertEqual(files.folder_sizes[key].size,200)
        self.assertTrue(files.folder_sizes[key].stale)
        files.reconcile([],{})
        self.assertEqual(files.folder_sizes,{})
