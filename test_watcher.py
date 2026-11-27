import unittest
from unittest.mock import Mock, patch
from file_types import Entry
from controllers.navigation import NavigationMixin

class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.w=NavigationMixin()
        self.w.watcher_enabled=True
        self.w.current='C:/folder'
        self.w.refresh_timer=Mock()
        self.w.refresh_timer.isActive.return_value=False
        self.w.folder_load_token=None
        self.w.rename_editor=None
        self.w.rename_busy=False
        self.w.load_folder=Mock()
        self.w.refresh_tree_branch=Mock()

    def test_coalesces_and_ignores_other_folders(self):
        w=self.w
        w.queue_watcher_refresh('C:/other')
        w.refresh_timer.start.assert_not_called()
        w.queue_watcher_refresh('C:/folder')
        w.refresh_timer.start.assert_called_once()
        w.refresh_timer.isActive.return_value=True
        w.queue_watcher_refresh('C:/folder')
        w.refresh_timer.start.assert_called_once()

    def test_defers_during_scan_and_rename(self):
        w=self.w
        w.folder_load_token=3
        w.refresh_from_watcher()
        w.load_folder.assert_not_called()
        w.folder_load_token=None
        w.rename_busy=True
        w.refresh_from_watcher()
        w.load_folder.assert_not_called()
        w.rename_busy=False
        w.refresh_from_watcher()
        w.load_folder.assert_called_once_with(from_watcher=True)
        w.refresh_tree_branch.assert_not_called()

    def test_disabled_watcher(self):
        w=self.w
        w.watcher_enabled=False
        w.queue_watcher_refresh(w.current)
        w.refresh_timer.start.assert_not_called()
        w.refresh_from_watcher()
        w.load_folder.assert_not_called()

    def test_unchanged_notification_does_not_reset_model_or_read_cache(self):
        w=self.w
        w.load_folder=NavigationMixin.load_folder.__get__(w)
        w.generation=0
        w.views=[]
        w.watcher=Mock()
        w.watcher.directories.return_value=[w.current]
        w.statusBar=Mock()
        w.model=Mock()
        entry=Entry("C:/folder/a.txt","a.txt",False,size=1,modified=1)
        w.model.entries=[entry]
        w.submit=lambda fn,done: done(fn(),None)
        with patch("controllers.navigation.scan",return_value=[entry]), patch("controllers.navigation.folder_sizes.lookup") as lookup:
            w.refresh_from_watcher()
            lookup.assert_not_called()
        w.model.replace.assert_not_called()
        w.refresh_tree_branch.assert_not_called()
        w.statusBar.assert_not_called()
        self.assertIsNone(w.folder_load_token)

if __name__=='__main__':
    unittest.main()
