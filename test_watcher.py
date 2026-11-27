import unittest
from unittest.mock import Mock
from controllers.navigation import NavigationMixin

class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.w=NavigationMixin()
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
        w.load_folder.assert_called_once()
        w.refresh_tree_branch.assert_called_once()

if __name__=='__main__':
    unittest.main()
