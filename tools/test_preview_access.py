"""Tests für "Vorschau von außen" (server.client_origin, server.PreviewAccess und die Grenzen im Vorschau-Strom): Woher kommt die Anfrage (Heimnetz, WLAN der Box,
Tailscale, öffentlicher Link), von außen ist die Vorschau standardmäßig aus, mit Erlaubnis nur klein (320 px, 10 Bilder je Sekunde) und für einen Zuschauer, damit der
Upload für die Sendung bleibt. Nichts davon geht ins Netz; die Vorschau selbst ist ein Ersatz."""
import os
import shutil
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402


class Origin(unittest.TestCase):
    def test_home_network_hotspot_and_the_box_itself_are_local(self):
        for ip in ("192.168.178.20", "10.1.1.20", "172.16.5.9", "fe80::1", "fd12::5", "::ffff:192.168.1.5", "127.0.0.1", "::1"):
            self.assertEqual(server.client_origin(ip, False), "lokal", ip)
        self.assertEqual(server.client_origin("fe80::1%eth0", False), "lokal")               # Zonenangabe

    def test_tailscale_the_proxy_on_the_box_and_public_addresses_are_external(self):
        for ip in ("100.101.102.103", "100.64.0.1", "8.8.8.8", "2a00:1450:4001::1"):
            self.assertEqual(server.client_origin(ip, False), "extern", ip)
        self.assertEqual(server.client_origin("127.0.0.1", True), "extern")                  # Tailscale-Proxy auf der Box (Serve oder Funnel): X-Forwarded-For
        self.assertEqual(server.client_origin("::1", True), "extern")
        self.assertEqual(server.client_origin("kein-ip", False), "extern")                   # im Zweifel von außen
        self.assertEqual(server.client_origin("100.64.0.1", True), "extern")


class Access(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "preview-access.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_external_is_off_by_default_and_local_is_always_allowed(self):
        pa = server.PreviewAccess(self.path)
        self.assertEqual((pa.allowed("lokal"), pa.allowed("extern")), (True, False))
        self.assertTrue(pa.acquire("lokal"))
        self.assertFalse(pa.acquire("extern"))

    def test_with_permission_exactly_one_external_viewer(self):
        pa = server.PreviewAccess(self.path)
        pa.set(True)
        self.assertTrue(pa.allowed("extern"))
        self.assertTrue(pa.acquire("extern"))
        self.assertFalse(pa.acquire("extern"))                                              # der zweite von außen wird abgewiesen
        self.assertTrue(pa.acquire("lokal"))                                                # lokal bleibt unberührt
        self.assertEqual(pa.status()["ext_viewers"], 1)
        pa.release("extern")
        self.assertTrue(pa.acquire("extern"))
        pa.release("extern")
        pa.release("extern")                                                                # nie unter null
        self.assertEqual(pa.status()["ext_viewers"], 0)

    def test_switching_off_stops_new_external_viewers(self):
        pa = server.PreviewAccess(self.path)
        pa.set(True)
        self.assertTrue(pa.acquire("extern"))
        pa.set(False)
        self.assertFalse(pa.allowed("extern"))
        self.assertFalse(pa.acquire("extern"))

    def test_the_choice_is_kept_after_a_restart_and_the_file_is_private(self):
        server.PreviewAccess(self.path).set(True)
        self.assertTrue(server.PreviewAccess(self.path).external)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        with self.assertRaises(ValueError):
            server.PreviewAccess(self.path).set("ja")
        open(self.path, "w").write("kaputt")
        self.assertFalse(server.PreviewAccess(self.path).external)                           # kaputte Datei: aus


class ChangeFromOutside(unittest.TestCase):
    """0.9.231 (Wunsch des Nutzers): Wer angemeldet ist, darf die Vorschau von außen auch über Tailscale einschalten (ein Streamer unterwegs)."""
    def test_the_post_route_no_longer_refuses_external_requests(self):
        src = open(os.path.join(os.path.dirname(HERE), "server.py"), encoding="utf-8").read()
        i = src.index('if path == "/api/previewaccess":                                   # auch über Tailscale')
        block = src[i:i + 500]
        self.assertNotIn("403", block)
        self.assertIn('origin=self.origin()', block)
        self.assertNotIn("nur im Heimnetz oder im WLAN der Box ändern", src)

    def test_the_setting_itself_works_for_any_origin(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        pa = server.PreviewAccess(os.path.join(d, "pa.json"), demo=True)
        self.assertFalse(pa.allowed("extern"))
        pa.set(True)
        self.assertTrue(pa.allowed("extern"))


class FakeSend:
    def _active(self):
        return True


class FakePreview:
    def __init__(self):
        self.calls = []

    def open(self, fps, width, long, fmt):
        self.calls.append((fps, width, long, fmt))
        return None, {"error": "capture"}


class Stream(unittest.TestCase):
    """Der Vorschau-Strom mit nachgebauter Anfrage: Entscheidung, Grenzen und Freigabe des Platzes."""
    def make(self, peer, forwarded=False, allowed=False, query="fmt=mp4&fps=30&w=640&long=1"):
        h = server.Handler.__new__(server.Handler)
        h.client_address = (peer, 5555)
        h.headers = {"X-Forwarded-For": "203.0.113.9"} if forwarded else {}
        h.path = "/api/preview/stream?" + query
        h.out = []
        h.reply = lambda code, obj, **kw: h.out.append((code, obj))
        h.send = FakeSend()
        h.preview = FakePreview()
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        h.previewaccess = server.PreviewAccess(os.path.join(d, "pa.json"), demo=True)
        if allowed:
            h.previewaccess.set(True)
        return h

    def test_external_without_permission_is_refused_before_anything_starts(self):
        h = self.make("100.101.102.103")
        h.preview_stream()
        self.assertEqual(h.out, [(403, {"error": "extern"})])
        self.assertEqual(h.preview.calls, [])
        h = self.make("127.0.0.1", forwarded=True)                                          # Tailscale-Proxy: auch von außen
        h.preview_stream()
        self.assertEqual(h.out[0][0], 403)

    def test_external_with_permission_is_small_and_short(self):
        h = self.make("100.101.102.103", allowed=True)
        h.preview_stream()
        self.assertEqual(h.preview.calls, [(10, 320, False, "mp4")])                         # 640 und 30 und "long" werden gekürzt
        h = self.make("100.101.102.103", allowed=True, query="fmt=mjpeg&fps=abc&w=xyz")
        h.preview_stream()
        self.assertEqual(h.preview.calls, [(10, 320, False, "mjpeg")])
        h = self.make("100.101.102.103", allowed=True, query="fmt=mp4&fps=5&w=200")
        h.preview_stream()
        self.assertEqual(h.preview.calls, [(5, 200, False, "mp4")])                          # kleinere Wünsche bleiben

    def test_local_keeps_full_quality(self):
        h = self.make("192.168.178.20")
        h.preview_stream()
        self.assertEqual(h.preview.calls, [("30", "640", True, "mp4")])                      # unverändert (die Vorschau selbst begrenzt)

    def test_the_external_slot_is_given_back_even_after_a_failure(self):
        h = self.make("100.101.102.103", allowed=True)
        for _ in range(3):
            h.preview_stream()                                                              # open() liefert "capture": der Platz muss jedes Mal frei werden
        self.assertEqual(h.previewaccess.status()["ext_viewers"], 0)
        self.assertEqual(len(h.preview.calls), 3)

    def test_second_external_viewer_gets_busy(self):
        h = self.make("100.101.102.103", allowed=True)
        self.assertTrue(h.previewaccess.acquire("extern"))                                  # ein anderer von außen schaut schon
        h.preview_stream()
        self.assertEqual(h.out, [(429, {"error": "busy"})])
        self.assertEqual(h.preview.calls, [])


if __name__ == "__main__":
    unittest.main()
