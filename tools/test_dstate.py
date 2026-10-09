"""Tests für die Meldungen "Kamera ausgefallen" (Issue #55: bewusst abgezogenes Handy lässt sich mit dem × schließen) und für die Namen hinter der Meldung
"n Prozess(e) blockiert (D-State)" (Issue #51: Zustandsprotokoll und Hilfetext in der Oberfläche)."""
import importlib.util
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import server  # noqa: E402

PAGE = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
SRC = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()


def load_health():
    spec = importlib.util.spec_from_file_location("pipbox_health", os.path.join(ROOT, "install", "pipbox_health.py"))
    h = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(h)
    return h


def put_thread(root, pid, tid, name, state, wchan=None, ticks=0, core=0):
    d = "%s/%s/task/%s" % (root, pid, tid)
    os.makedirs(d, exist_ok=True)
    fields = [state] + ["0"] * 10 + [str(ticks), "0"] + ["0"] * 23 + [str(core)]
    with open(d + "/stat", "w") as f:
        f.write("%s (%s) %s" % (tid, name, " ".join(fields)))
    if wchan is not None:
        with open(d + "/wchan", "w") as f:
            f.write(wchan)


class Outage(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.w = server.OutageWatch(clock=lambda: self.now[0])

    def cams(self, state):
        return [{"key": "cam-phone", "name": "Handy", "state": state}]

    def go_offline(self):
        self.w.alerts(self.cams("live"))
        self.now[0] += 1
        self.w.alerts(self.cams("offline"))
        self.now[0] += server.OutageWatch.DOWN + 1
        return self.w.alerts(self.cams("offline"))

    def test_alert_appears_after_the_wait(self):
        out = self.go_offline()
        self.assertEqual([a["kind"] for a in out], ["cam"])

    def test_closing_removes_it_and_it_stays_away(self):
        out = self.go_offline()
        self.assertTrue(self.w.dismiss("cam", out[0]["t"]))
        self.assertEqual(self.w.alerts(self.cams("offline")), [])
        self.now[0] += 600
        self.assertEqual(self.w.alerts(self.cams("offline")), [])

    def test_a_new_outage_shows_again(self):
        out = self.go_offline()
        self.w.dismiss("cam", out[0]["t"])
        self.now[0] += 5
        self.w.alerts(self.cams("live"))                       # die Kamera sendet wieder
        self.now[0] += 5
        out2 = self.go_offline()
        self.assertEqual(len(out2), 1)                          # ein neuer Ausfall ist ein neuer Fall
        self.assertNotEqual(out2[0]["t"], out[0]["t"])

    def test_closing_one_camera_does_not_close_others(self):
        self.w.alerts([{"key": "a", "name": "A", "state": "live"}, {"key": "b", "name": "B", "state": "live"}])
        self.now[0] += 1
        self.w.alerts([{"key": "a", "name": "A", "state": "offline"}, {"key": "b", "name": "B", "state": "live"}])
        self.now[0] += 5
        self.w.alerts([{"key": "a", "name": "A", "state": "offline"}, {"key": "b", "name": "B", "state": "offline"}])
        self.now[0] += server.OutageWatch.DOWN + 1
        both = self.w.alerts([{"key": "a", "name": "A", "state": "offline"}, {"key": "b", "name": "B", "state": "offline"}])
        self.assertEqual(len(both), 2)
        self.w.dismiss("cam", both[0]["t"])
        left = self.w.alerts([{"key": "a", "name": "A", "state": "offline"}, {"key": "b", "name": "B", "state": "offline"}])
        self.assertEqual(len(left), 1)

    def test_bad_requests_are_refused(self):
        for kind, t in (("usb", 1), ("cam", "x"), ("cam", None), ("cam", True), (None, 1)):
            with self.assertRaises(ValueError, msg=repr((kind, t))):
                self.w.dismiss(kind, t)

    def test_hdmi_alert_can_be_closed_too(self):
        bad = {"service": True, "available": True, "state": "error", "settings": {"enabled": True, "source": "hdmi"}}
        self.w.alerts([], bad)
        self.now[0] += server.OutageWatch.DOWN + 1
        out = self.w.alerts([], bad)
        self.assertEqual([a["kind"] for a in out], ["hdmi"])
        self.w.dismiss("hdmi", out[0]["t"])
        self.assertEqual(self.w.alerts([], bad), [])

    def test_old_closed_entries_are_dropped(self):
        self.w.dismiss("cam", 1000)
        self.now[0] += server.OutageWatch.MAX + 10
        self.w.alerts([])
        self.assertEqual(self.w.closed, set())

    def test_page_and_route(self):
        self.assertIn('(x.kind==="usb"||x.kind==="cam"||x.kind==="hdmi")', PAGE)               # × bei USB-, Kamera- und HDMI-Meldungen
        self.assertIn('data-akind="${x.kind}"', PAGE)
        self.assertIn('{kind:b.dataset.akind,t:parseInt(b.dataset.at,10)}', PAGE)
        self.assertIn('self.outages.dismiss(d.get("kind"), d.get("t"))', SRC)
        self.assertIn('self.usbwatch.dismiss(d.get("t"))', SRC)                                  # ohne Art wie bisher USB


class BlockedNames(unittest.TestCase):
    def test_sampler_names_the_waiting_threads(self):
        root = tempfile.mkdtemp()
        put_thread(root, 10, 10, "belacoder", "S")
        put_thread(root, 20, 20, "usb-storage", "D", "usb_sg_wait")
        put_thread(root, 30, 31, "kworker/1:2", "D", "0")
        put_thread(root, 40, 40, "mit Leerzeichen", "D", "io_schedule")
        self.assertEqual(server.Sampler.blocked_threads(root), ["usb-storage@usb_sg_wait", "kworker/1:2@?", "mit_Leerzeichen@io_schedule"])

    def test_limit_and_nothing_blocked(self):
        root = tempfile.mkdtemp()
        for i in range(10):
            put_thread(root, 100 + i, 100 + i, "t%d" % i, "D", "x")
        self.assertEqual(len(server.Sampler.blocked_threads(root, limit=4)), 4)
        self.assertEqual(server.Sampler.blocked_threads(tempfile.mkdtemp()), [])

    def test_alert_carries_the_names(self):
        s = server.Sampler(False)
        out = s.finish([10.0], [1800], 40.0, 8000000, 6000000, {}, 2, None, ["usb-storage@usb_sg_wait"])
        a = [x for x in out["alerts"] if x.get("kind") == "dstate"][0]
        self.assertEqual(a["text"], "2 Prozess(e) blockiert (D-State)")                       # Text unverändert (Übersetzungen)
        self.assertEqual(a["names"], ["usb-storage@usb_sg_wait"])
        self.assertFalse([x for x in s.finish([10.0], [1800], 40.0, 8000000, 6000000, {}, 0)["alerts"] if x.get("kind") == "dstate"])

    def test_page_shows_the_names_behind_the_info_button(self):
        self.assertIn('a.kind==="dstate"&&(a.names||[]).length', PAGE)
        self.assertIn("<span>Wartet im Kernel:</span>", PAGE)


class HealthLine(unittest.TestCase):
    def prepare(self, h, root, blocked):
        nl = chr(10)
        with open(root + "/stat", "w") as f:
            f.write("cpu  0 0 0 0 0 0 0 0" + nl + "cpu0 100 0 0 900 0 0 0 0" + nl + "procs_blocked %d" % blocked + nl)

    def test_names_and_wchan_in_the_state_line(self):
        h = load_health()
        root = tempfile.mkdtemp()
        self.prepare(h, root, 2)
        put_thread(root, 10, 10, "belacoder", "S")
        put_thread(root, 20, 20, "usb-storage", "D", "usb_sg_wait")
        put_thread(root, 30, 31, "kworker/1:2", "D", "0")
        with mock.patch.object(h, "PROC", root), mock.patch.object(h, "_prev", [None]):
            h.cpu_text()
            self.assertEqual(h.dstate_text(), "blocked=2 d=usb-storage@usb_sg_wait,kworker/1:2@?")

    def test_nothing_when_nothing_is_blocked(self):
        h = load_health()
        root = tempfile.mkdtemp()
        self.prepare(h, root, 0)
        put_thread(root, 10, 10, "belacoder", "S")
        with mock.patch.object(h, "PROC", root), mock.patch.object(h, "_prev", [None]):
            h.cpu_text()
            self.assertEqual(h.dstate_text(), "")

    def test_counter_without_names_is_still_logged(self):
        h = load_health()
        root = tempfile.mkdtemp()
        self.prepare(h, root, 1)                                      # zwischen Zählen und Suchen verschwunden
        put_thread(root, 10, 10, "belacoder", "S")
        with mock.patch.object(h, "PROC", root), mock.patch.object(h, "_prev", [None]):
            h.cpu_text()
            self.assertEqual(h.dstate_text(), "blocked=1 d=-")

    def test_cpu_text_is_unchanged(self):
        h = load_health()
        root = tempfile.mkdtemp()
        self.prepare(h, root, 0)
        put_thread(root, 10, 10, "belacoder", "S")
        with mock.patch.object(h, "PROC", root), mock.patch.object(h, "_prev", [None]):
            self.assertEqual(h.cpu_text(), "cores=? hot=?")

    def test_line_appends_the_text_only_when_needed(self):
        h = load_health()
        src = open(os.path.join(ROOT, "install", "pipbox_health.py"), encoding="utf-8").read()
        self.assertIn('{cpu}" + (" " + extra if extra else "")', src)
        self.assertIn("dstate_text()", src)


if __name__ == "__main__":
    unittest.main()
