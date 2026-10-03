"""Prüft die Positionsfunktion des PiP-Bausteins (gst/gstpbpip.c) einzeln: übersetzt nur den Abschnitt PB_POS_BEGIN..END."""
import os
import re
import subprocess
import tempfile
import unittest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gst", "gstpbpip.c")


class CornerPos(unittest.TestCase):
    def run_c(self, mw, mh, pw, ph, cases):
        """cases: Liste (Ecke, fx, fy) -> Liste (x, y)."""
        text = open(SRC).read()
        m = re.search(r"/\* PB_POS_BEGIN.*?\*/(.*?)/\* PB_POS_END \*/", text, re.S)
        self.assertIsNotNone(m, "Markierungen PB_POS_BEGIN/END fehlen")
        calls = "".join(
            'pb_pos(%d, %d, %d, %d, %d, margin, %d, %d, &x, &y); x &= ~1; y &= ~1; printf("%%d %%d\\n", x, y);\n' % (c, mw, mh, pw, ph, fx, fy)
            for c, fx, fy in cases)
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "t.c")
            open(c, "w").write(
                "#include <stdio.h>\ntypedef unsigned int guint; typedef int gint; typedef long long gint64;\n" + m.group(1) +
                "int main(void){ gint x, y; const gint margin = (%d / 60) & ~1;\n%s return 0; }\n" % (mw, calls))
            exe = os.path.join(d, "t")
            subprocess.run(["cc", "-O0", "-Wall", "-Werror", c, "-o", exe], check=True)
            out = subprocess.run([exe], capture_output=True, text=True, check=True).stdout.split("\n")
        return [tuple(map(int, l.split())) for l in out if l]

    def positions(self, mw, mh, pw, ph):
        return self.run_c(mw, mh, pw, ph, [(c, 0, 0) for c in range(5)])

    def test_free_position_corners_and_clamp(self):
        pw, ph = 480, 270
        p = self.run_c(1920, 1080, pw, ph, [(5, 0, 0), (5, 1000, 1000), (5, 500, 500), (5, 2000, -5), (5, 250, 750)])
        self.assertEqual(p[0], (0, 0))                                   # ganz oben links, bündig
        self.assertEqual(p[1], (1920 - pw, 1080 - ph))                   # ganz unten rechts, bündig
        self.assertEqual(p[2], (((1920 - pw) // 2) & ~1, ((1080 - ph) // 2) & ~1))   # genau in der Mitte (gerade Werte)
        self.assertEqual(p[3], (1920 - pw, 0))                           # außerhalb wird begrenzt
        self.assertEqual(p[4], ((1920 - pw) // 4 & ~1, ((1080 - ph) * 3 // 4) & ~1))

    def test_free_position_never_leaves_the_frame(self):
        for pct in (15, 25, 40, 100):
            pw = (1920 * pct // 100) & ~1
            ph = (pw * 9 // 16) & ~1
            for x, y in self.run_c(1920, 1080, pw, ph, [(5, fx, fy) for fx in (0, 333, 1000) for fy in (0, 777, 1000)]):
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x + pw, 1920)
                self.assertLessEqual(y + ph, 1080)

    def test_1080p_25_percent(self):
        p = self.positions(1920, 1080, 480, 270)          # 25 % der Breite, 16:9
        margin = (1920 // 60) & ~1                        # 32
        self.assertEqual(p[0], (margin, margin))                                   # oben links
        self.assertEqual(p[1], (1920 - 480 - margin, margin))                      # oben rechts
        self.assertEqual(p[2], (margin, 1080 - 270 - margin))                      # unten links
        self.assertEqual(p[3], (1920 - 480 - margin, 1080 - 270 - margin))         # unten rechts
        self.assertEqual(p[4], ((1920 - 480) // 2, 1080 - 270 - margin))           # unten Mitte

    def test_bottom_center_is_centered_and_even(self):
        for pct in (15, 25, 40):
            pw = (1920 * pct // 100) & ~1
            ph = (pw * 9 // 16) & ~1
            x, y = self.positions(1920, 1080, pw, ph)[4]
            self.assertEqual(x % 2, 0)
            self.assertEqual(y % 2, 0)
            self.assertLessEqual(abs((x + pw / 2) - 960), 1)                       # Mitte des Bildes
            self.assertEqual(y + ph + (1920 // 60 & ~1), 1080)                     # gleicher Abstand wie die unteren Ecken
            self.assertGreaterEqual(x, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
