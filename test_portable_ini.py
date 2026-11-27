import tempfile
import unittest
from pathlib import Path
from PySide6.QtCore import QSettings, QByteArray
from portable_ini import PortableIni, write_guard

class PortableIniTests(unittest.TestCase):
    def test_legacy_lock_never_blocks_and_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.ini"
            path.write_text("[General]\nlocation=example\n",encoding="utf-8")
            old = path.read_bytes()
            lock = path.with_suffix(".ini.lock")
            lock.write_text("123\npython\nremote-host\n")
            child = lock.with_name(lock.name + ".rmlock")
            child.write_text("fixture")
            s = PortableIni(path)
            self.assertEqual(s.value("location"),"example")
            s.setValue("location","changed")
            with self.assertLogs("portable_ini","WARNING"):
                self.assertFalse(s.sync())
            self.assertEqual(path.read_bytes(),old)
            self.assertTrue(lock.exists())
            self.assertTrue(child.exists())
            self.assertEqual(s.value("location"),"changed")
            lock.unlink()
            child.unlink()
            self.assertTrue(s.sync())
            self.assertEqual(QSettings(str(path), QSettings.IniFormat).value("location"), "changed")

    def test_merge_and_qt_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.ini"
            a,b = PortableIni(path),PortableIni(path)
            a.setValue("pins",["C:/one","D:/two"])
            b.setValue("geometry",QByteArray(b"fixture"))
            self.assertTrue(a.sync())
            self.assertTrue(b.sync())
            c=PortableIni(path)
            self.assertEqual(c.value("pins",type=list),["C:/one","D:/two"])
            self.assertEqual(c.value("geometry"),QByteArray(b"fixture"))
            self.assertFalse(path.with_suffix(".ini.lock").exists())

    def test_guard_contention_preserves_previous_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"settings.ini"
            s=PortableIni(path)
            s.setValue("value",1)
            self.assertTrue(s.sync())
            old=path.read_bytes()
            s.setValue("value",2)
            guard=path.with_name(".settings.ini.writeguard")
            with write_guard(guard):
                with self.assertLogs("portable_ini","WARNING"):
                    self.assertFalse(s.sync())
                self.assertEqual(path.read_bytes(),old)
            self.assertTrue(s.sync())
            self.assertEqual(PortableIni(path).value("value",type=int),2)
