"""Tests für die Menüeinstellung (Optionen): Reihenfolge, Ausblenden und die Überschriften von Chat und Vorschau gelten je Gerät (Browser) und stehen nicht auf der Box."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

SRC = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
PAGE = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
_A = PAGE.index("// ---- Hauptmenüs: Reihenfolge")
BLOCK = PAGE[_A:PAGE.index("\n})();", _A) + 6]                                           # nur dieser Block


class PerDevice(unittest.TestCase):
    def test_page_keeps_the_setting_only_in_the_browser(self):
        self.assertIn('localStorage.setItem(KEY,JSON.stringify(st))', BLOCK)
        self.assertIn("je Gerät (Browser) gemerkt, nicht auf der Box", BLOCK)
        self.assertNotIn("srtlaCall", BLOCK)                                                  # nichts wird auf die Box geschickt oder von ihr geholt
        self.assertNotIn("/api/layout", PAGE)
        self.assertNotIn("function pull", BLOCK)
        self.assertNotIn("function push", BLOCK)

    def test_save_is_the_only_writer(self):
        self.assertIn("const save=()=>{ try{ localStorage.setItem(KEY,JSON.stringify(st)); }catch(e){} };", BLOCK)

    def test_applied_at_start_without_asking_the_box(self):
        self.assertIn("applyOrder(); applyHidden(); buildList();\n})();", BLOCK)

    def test_server_has_no_shared_layout_any_more(self):
        for needle in ("class UiLayout", "/api/layout", "Handler.layout", "ui-layout.json", "self.layout"):
            self.assertNotIn(needle, SRC, needle)

    def test_backup_does_not_carry_the_menu_setting(self):
        self.assertNotIn('("layout", "Optionen', SRC)
        self.assertNotIn('doc["layout"]', SRC)


if __name__ == "__main__":
    unittest.main()
