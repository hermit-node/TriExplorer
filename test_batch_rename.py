import tempfile
import unittest
from pathlib import Path
from batch_rename.rules import transform
from batch_rename.preview import preview
from batch_rename.history import History

class BatchTests(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(transform("IMG_01.jpg",[("Replace text","IMG_","Photo_"),("Sequential numbering","3","2")]),"Photo_01_03.jpg")
    def test_swap_and_undo(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); a=p/"a.txt"; b=p/"b.txt"
            a.write_text("a"); b.write_text("b")
            h=History(p/"history.sqlite")
            h.apply(p,[(a,b),(b,a)])
            self.assertEqual(a.read_text(),"b")
            self.assertTrue(History(p/"history.sqlite").undo(p))
            self.assertEqual(a.read_text(),"a")
    def test_collision(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); a=p/"a.txt"; b=p/"b.txt"
            a.touch(); b.touch()
            pairs,errors=preview([a],[("Replace text","a","b")])
            self.assertTrue(errors)
    def test_modified_undo(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); a=p/"a.txt"; b=p/"b.txt"
            a.write_text("a"); h=History(p/"history.sqlite")
            h.apply(p,[(a,b)]); b.write_text("modified")
            with self.assertRaises(ValueError): h.undo(p)
            self.assertTrue(b.exists())
    def test_case_change(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); a=p/"a.txt"; b=p/"A.txt"
            a.write_text("a"); h=History(p/"history.sqlite")
            h.apply(p,[(a,b)])
            self.assertIn("A.txt",[x.name for x in p.iterdir()])
            self.assertTrue(h.undo(p))
