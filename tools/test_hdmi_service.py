#!/usr/bin/env python3
"""Tests der Anbindung des HDMI-Dienstes in der Weboberfläche: server.py (HdmiService, /api/hdmi, Kamera "HDMI") und web/index.html (Abschnitt
"HDMI- und USB-Kameras" in der Karte "Kameras"). Der echte Dienst (hdmi_daemon.Daemon) läuft mit nachgebildeter Hardware in einem Thread, die
Verbindung ist ein echter Socket: so stimmen Protokoll und Antworten beider Seiten überein."""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import hdmi_daemon  # noqa: E402
import server  # noqa: E402
from test_hdmi import LOCKED, UNPLUGGED, Rig  # noqa: E402

with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as _f:
    PAGE = _f.read()
JSC = next((p for p in (shutil.which("jsc"), "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc") if p and os.path.exists(p)), None)


class DaemonThread:
    """Der HDMI-Dienst mit nachgebildeter Hardware, erreichbar über einen echten Socket auf 127.0.0.1."""
    def __init__(self, rig):
        self.rig, self.loop, self.port, self.server = rig, asyncio.new_event_loop(), None, None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert self.ready.wait(5)

    def _run(self):
        asyncio.set_event_loop(self.loop)

        async def start():
            self.server = await asyncio.start_server(self.rig.d.client, "127.0.0.1", 0)
            self.port = self.server.sockets[0].getsockname()[1]
        self.loop.run_until_complete(start())
        self.ready.set()
        self.loop.run_forever()

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(5)

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.server.close()
        self.loop.close()


class Case(unittest.TestCase):
    """Dienst, Kameraliste und HdmiService zusammen."""
    def setUp(self):
        self.rig = Rig(None, enabled=False)
        self.dt = DaemonThread(self.rig)
        self.addCleanup(self.dt.stop)
        self.cams = server.CameraStore(os.path.join(self.rig.tmp, "cameras.json"), "publish", None, False)
        self.svc = server.HdmiService(self.rig.tmp, self.cams)
        self.svc.PORT = self.dt.port
        self.svc.TTL = 0                                    # kein Zwischenspeicher, wenn ein Test ihn nicht braucht


class Talking(Case):
    def test_status_comes_from_the_real_service(self):
        self.dt.run(self.rig.d.tick())
        s = self.svc.status()
        self.assertTrue(s["service"])
        self.assertEqual((s["state"], s["available"]), ("off", True))
        self.assertEqual(s["settings"], {"enabled": False, "bitrate": 8000, "fps": 30, "audio": "hdmi", "source": "hdmi", "usb_format": "auto"})        # ohne den festen Schlüssel
        self.assertEqual((s["signal"]["width"], s["signal"]["height"], s["signal"]["locked"]), (1920, 1080, True))
        self.assertFalse(s["listed"])

    def test_switching_on_adds_the_camera_and_starts_the_service(self):
        out = self.svc.set({"enabled": True})
        self.assertTrue(out["settings"]["enabled"])
        self.assertTrue(out["listed"])
        self.assertEqual([(c["name"], c["key"]) for c in self.cams.cams], [("HDMI", "hdmi")])
        self.dt.run(self.rig.d.tick())
        self.assertEqual(self.svc.status()["state"], "starting")
        self.assertEqual(len(self.rig.spawned), 1)
        self.assertIn("location=rtmp://127.0.0.1:1935/publish/hdmi", self.rig.spawned[0][0])

    def test_settings_reach_the_service_in_its_units(self):
        self.svc.set({"bitrate": 6000, "fps": 25, "audio": "none"})
        self.assertEqual({k: self.rig.d.cfg[k] for k in ("bitrate", "fps", "audio")}, {"bitrate": 6000, "fps": 25, "audio": "none"})

    def test_the_key_cannot_be_changed_from_the_page(self):
        self.svc.set({"key": "anders", "enabled": True, "unsinn": 1})
        self.assertEqual(self.rig.d.cfg["key"], "hdmi")
        self.assertEqual([c["key"] for c in self.cams.cams], ["hdmi"])

    def test_bad_values_are_refused_before_they_reach_the_service(self):
        for bad in ({"bitrate": 1}, {"bitrate": "8000"}, {"fps": 60}, {"audio": "usb"}, {"enabled": "ja"}, "text", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.svc.set(bad)
        self.assertEqual(self.rig.d.cfg, hdmi_daemon.DEFAULTS)
        self.assertEqual(self.cams.cams, [])

    def test_the_status_is_cached_for_a_moment(self):
        self.svc.TTL = 60
        calls = []
        real = self.svc._call
        self.svc._call = lambda req, timeout=4: (calls.append(req), real(req, timeout))[1]
        self.svc.status()
        self.svc.status()
        self.assertEqual(len(calls), 1)
        self.svc.set({"fps": 25})                          # nach einer Änderung gilt wieder der frische Zustand
        self.assertEqual(self.svc.status()["settings"]["fps"], 25)

    def test_a_camera_that_is_already_listed_is_not_added_twice(self):
        self.svc.set({"enabled": True})
        self.svc.set({"enabled": True})
        self.svc.status()
        self.assertEqual(len(self.cams.cams), 1)

    def test_a_status_that_says_enabled_lists_the_camera(self):
        self.rig.d.cfg["enabled"] = True                    # z. B. nach einem Neustart der Oberfläche oder entfernter Kamera
        self.assertTrue(self.svc.status()["listed"])

    def test_removing_the_camera_switches_the_feed_off(self):
        self.svc.set({"enabled": True})
        self.svc.on_camera_removed()
        self.assertFalse(self.rig.d.cfg["enabled"])


class Naming(Case):
    def test_a_taken_name_gets_a_second_choice_and_roles_follow_the_free_slots(self):
        self.cams.add("HDMI", "andere", "main")
        self.svc.ensure_listed()
        self.assertEqual([(c["name"], c["key"], c["role"]) for c in self.cams.cams], [("HDMI", "andere", "main"), ("HDMI-Eingang", "hdmi", "pip")])

    def test_the_first_camera_of_an_empty_list_is_the_main_picture(self):
        self.svc.ensure_listed()
        self.assertEqual(self.cams.cams[0]["role"], "main")


class ServiceDown(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cams = server.CameraStore(os.path.join(self.tmp, "cameras.json"), "publish", None, False)
        self.svc = server.HdmiService(self.tmp, self.cams)

    def test_without_the_token_file_the_service_is_missing(self):
        s = self.svc.status()
        self.assertEqual((s["service"], s["state"], s["message"]), (False, "down", server.HdmiService.DOWN))
        self.assertEqual(s["settings"], {k: hdmi_daemon.DEFAULTS[k] for k in server.HdmiService.ALLOWED})
        with self.assertRaises(RuntimeError):
            self.svc.set({"enabled": True})
        self.assertEqual(self.cams.cams, [])

    def test_a_closed_port_or_a_wrong_token_is_the_same_message(self):
        with open(os.path.join(self.tmp, "hdmi-token"), "w") as f:
            f.write("x" * 40)
        self.svc.PORT = 1                                   # dort lauscht nichts
        self.assertFalse(self.svc.status()["service"])
        rig = Rig(None, enabled=False)
        dt = DaemonThread(rig)
        self.addCleanup(dt.stop)
        self.svc.PORT = dt.port                             # lauscht, kennt das Token aber nicht
        with self.assertRaises(RuntimeError) as e:
            self.svc.set({"enabled": True})
        self.assertEqual(str(e.exception), server.HdmiService.DOWN)

    def test_removing_the_camera_does_not_fail_when_the_service_is_down(self):
        self.svc.on_camera_removed()


class Demo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cams = server.CameraStore(os.path.join(self.tmp, "cameras.json"), "publish", None, True)
        self.svc = server.HdmiService(self.tmp, self.cams, demo=True)
        self.svc.TTL = 0

    def test_demo_has_a_signal_and_switches_on_and_off_without_a_service(self):
        s = self.svc.status()
        self.assertEqual((s["service"], s["state"], s["signal"]["locked"], s["signal"]["width"]), (True, "off", True, 1920))
        s = self.svc.set({"enabled": True, "bitrate": 5000})
        self.assertEqual((s["state"], s["settings"]["bitrate"], s["listed"]), ("streaming", 5000, True))
        self.assertEqual(self.svc.set({"enabled": False})["state"], "off")

    def test_demo_checks_the_values_like_the_real_service(self):
        with self.assertRaises(ValueError):
            self.svc.set({"bitrate": 5})
        self.assertEqual(self.svc.status()["settings"]["bitrate"], 8000)


class CameraList(unittest.TestCase):
    def test_the_hdmi_camera_has_no_address_and_no_connection_to_choose(self):
        tmp = tempfile.mkdtemp()
        cams = server.CameraStore(os.path.join(tmp, "cameras.json"), "publish", None, False)
        cams.ipfn = lambda: "192.168.1.5"
        cams.add("Handy", "handy", "main")
        cams.add("HDMI", server.HDMI_KEY, "pip")
        rows = {c["key"]: c for c in cams.listing("192.168.1.5")}
        self.assertEqual((rows["hdmi"]["via_src"], rows["hdmi"]["url"], rows["hdmi"]["via"]), ("hdmi", "", None))
        self.assertEqual((rows["handy"]["via_src"], rows["handy"]["url"]), ("main", "rtmp://192.168.1.5:1935/publish/handy"))


class Endpoints(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(None, enabled=False)
        self.dt = DaemonThread(self.rig)
        self.addCleanup(self.dt.stop)
        self.cams = server.CameraStore(os.path.join(self.rig.tmp, "cameras.json"), "publish", None, False)
        self.svc = server.HdmiService(self.rig.tmp, self.cams)
        self.svc.PORT, self.svc.TTL = self.dt.port, 0
        for name, val in (("hdmi", self.svc), ("cams", self.cams)):
            old = getattr(server.Handler, name)
            setattr(server.Handler, name, val)
            self.addCleanup(setattr, server.Handler, name, old)

    @staticmethod
    def handler(path, body=None, authed=True):
        raw = json.dumps({} if body is None else body).encode()
        h = server.Handler.__new__(server.Handler)
        h.path, h.sent, h.hdrs = path, [], {}
        h.authed = lambda: authed
        h.send_response = lambda code, *a: h.sent.append(code)
        h.send_header = lambda k, v: h.hdrs.__setitem__(k, v)
        h.end_headers = lambda: None

        class W:
            data = b""

            def write(self, b):
                W.data += b
        h.wfile, h.out = W(), W
        h.headers = {"Content-Length": str(len(raw))}
        h.rfile = type("R", (), {"read": lambda self, n: raw[:n]})()
        h.client_address = ("127.0.0.1", 1)
        return h

    def call(self, method, path, body=None, authed=True):
        h = self.handler(path, body, authed)
        getattr(h, "do_" + method)()
        return h.sent[0], json.loads(h.out.data.decode())

    def test_everything_needs_a_login(self):
        for method, path in (("GET", "/api/hdmi"), ("POST", "/api/hdmi")):
            self.assertEqual(self.call(method, path, {"enabled": True}, authed=False), (401, {"error": "nicht angemeldet"}))
        self.assertFalse(self.rig.d.cfg["enabled"])

    def test_get_and_post(self):
        code, out = self.call("GET", "/api/hdmi")
        self.assertEqual((code, out["service"], out["settings"]["enabled"]), (200, True, False))
        code, out = self.call("POST", "/api/hdmi", {"enabled": True, "fps": 25})
        self.assertEqual((code, out["settings"]["enabled"], out["settings"]["fps"], out["listed"]), (200, True, 25, True))

    def test_bad_input_is_a_400_and_a_missing_service_a_503(self):
        code, out = self.call("POST", "/api/hdmi", {"bitrate": 3})
        self.assertEqual(code, 400)
        self.assertIn("Bitrate", out["error"])
        self.svc.PORT = 1
        self.assertEqual(self.call("POST", "/api/hdmi", {"enabled": True})[0], 503)
        self.assertEqual(self.call("GET", "/api/hdmi")[1]["service"], False)

    def test_deleting_the_hdmi_camera_switches_the_feed_off_and_other_cameras_leave_it_alone(self):
        self.call("POST", "/api/hdmi", {"enabled": True})
        other = self.cams.add("Handy", "handy", "extra")
        self.assertEqual(self.call("DELETE", "/api/cameras/" + other["id"])[0], 200)
        self.assertTrue(self.rig.d.cfg["enabled"])
        hdmi = next(c for c in self.cams.cams if c["key"] == "hdmi")
        self.assertEqual(self.call("DELETE", "/api/cameras/" + hdmi["id"])[0], 200)
        self.assertFalse(self.rig.d.cfg["enabled"])
        self.assertEqual(self.cams.cams, [])


def func(name):
    m = re.search(r"^(?:async )?function %s\(.*?^}" % re.escape(name), PAGE, re.S | re.M)
    assert m, name
    return m.group(0)


class Markup(unittest.TestCase):
    def test_the_section_sits_at_the_bottom_of_the_cameras_card_and_is_not_a_new_card(self):
        card = PAGE[PAGE.index('id="c_cams"'):PAGE.index("</details>\n\n\n  <details class=\"card wide\" id=\"pipecard\"")]
        self.assertIn('<details class="subsec" id="hdmi_sec"><summary>HDMI- und USB-Kameras</summary>', card)
        self.assertGreater(card.index('id="hdmicard"'), card.index('id="djicard"'))                  # unter den DJI-Kameras (und deren Akku-Warnung)
        self.assertLess(card.index('id="hdmicard"'), card.index('id="tw_sec"'))                       # die Akku-Warnung steht seit dem Umbau der Karte darunter
        titles = re.findall(r'<details class="card[^>]*><summary><span class="sumh">([^<]*)</span>', PAGE)
        self.assertEqual([t for t in titles if "HDMI" in t or "USB" in t], [])                       # keine zusätzliche Karte, kein eigener Hauptpunkt
        self.assertIn('<span class="sumh">Kameras</span>', PAGE)                                     # die Karte heißt weiter "Kameras"
        self.assertIn('<details class="subsec" id="rtmp_sec"><summary>Aktive Kameras</summary>', PAGE)

    def test_the_fields_and_their_order(self):
        sec = PAGE[PAGE.index('id="hdmicard"'):PAGE.index('id="hdmi_err"')]
        ids = re.findall(r'id="(hdmi_\w+)"', sec)
        self.assertEqual(ids, ["hdmi_cam", "hdmi_name", "hdmi_state", "hdmi_source", "hdmi_signal", "hdmi_on", "hdmi_vsec", "hdmi_vsum", "hdmi_fps", "hdmi_br", "hdmi_audio", "hdmi_fmtrow", "hdmi_fmt", "hdmi_save"])
        self.assertIn('<select id="hdmi_source"><option value="hdmi">HDMI-Eingang</option><option value="usb">USB-Webcam</option></select>', sec)
        self.assertIn('<option value="hdmi">HDMI-Ton</option><option value="none">ohne Ton</option>', sec)
        self.assertIn('<option value="30">30 fps</option><option value="25">25 fps</option>', sec)

    def test_the_camera_row_has_no_address_for_hdmi(self):
        self.assertIn('if(c.via_src==="hdmi") return `<span class="camnet muted" title="Das Bild kommt vom HDMI-Eingang der Box">HDMI-Eingang</span>`;', PAGE)
        self.assertIn('${c.url?`<code>${esc(c.url)}</code>`:""}', PAGE)


class Script(unittest.TestCase):
    BLOCK = PAGE[PAGE.index("// ---- HDMI-Kamera (Karte"):PAGE.index("let netBusy=false;")]

    def test_every_call_goes_through_srtlaCall_and_texts_are_set_as_text(self):
        for needle in ('srtlaCall("GET","/api/hdmi")', 'srtlaCall("POST","/api/hdmi",body)'):
            self.assertIn(needle, self.BLOCK)
        self.assertNotIn("fetch(", self.BLOCK)
        self.assertNotIn("innerHTML", self.BLOCK)                                                    # Texte vom Server stehen nur als textContent
        for expr in re.findall(r"\$\{([^}]*)\}", self.BLOCK):
            ok = re.match(r"^(s\.\w+|Math\.round\(s\.fps\)|s\.fps|\(s\.bitrate/1000\)\.toFixed\(1\)\.replace\(.*\)|s\.audio===.*)$", expr) or expr == 'usb?"":s.fps+" fps · "'
            self.assertTrue(ok, expr)

    def test_the_switch_applies_at_once_and_the_button_saves_the_picture_and_sound_values(self):
        self.assertIn('$("hdmi_on").addEventListener("change",()=>hdmiPost({enabled:$("hdmi_on").checked}));', self.BLOCK)
        self.assertIn("hdmiPost({bitrate:Math.round(parseFloat($(\"hdmi_br\").value)*1000),fps:parseInt($(\"hdmi_fps\").value,10),audio:$(\"hdmi_audio\").value,usb_format:$(\"hdmi_fmt\").value},\"Gespeichert.\")", self.BLOCK)
        self.assertIn('due("hdmi","c_cams",20000)', self.BLOCK)

    def test_the_page_script_still_compiles(self):
        if not JSC:
            self.skipTest("keine JavaScript-Maschine (jsc)")
        scripts = "\n".join(re.findall(r"<script>(.*?)</script>", PAGE, re.S))
        self.assertEqual(self.run_js("try { new Function(%s); print('ok'); } catch (e) { print('FEHLER ' + e); }" % json.dumps(scripts)), "ok")

    @staticmethod
    def run_js(src):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(src)
        try:
            r = subprocess.run([JSC, f.name], capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(f.name)
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)
        return r.stdout.strip()

    def texts(self, d):
        if not JSC:
            self.skipTest("keine JavaScript-Maschine (jsc)")
        code = func("hdmiIsUsb") + "\n" + func("hdmiSignalText") + "\n" + func("hdmiStateText") + "\nvar d = %s; print(JSON.stringify([hdmiSignalText(d), hdmiStateText(d)]));" % json.dumps(d)
        return json.loads(self.run_js(code))

    def test_usb_webcam_texts(self):
        usb = {"service": True, "available": True, "signal_known": True, "signal": {}, "settings": {"source": "usb", "audio": "hdmi"}}
        none = dict(usb, state="waiting", usb={"present": False})
        self.assertEqual(self.texts(none), ["Keine USB-Kamera angeschlossen", "wartet auf Kamera"])
        on = dict(usb, state="streaming", usb={"present": True, "name": "Osmo Action 6", "format": "MJPEG 1280x720@30", "audio": "plughw:CARD=Action6"})
        self.assertEqual(self.texts(on), ["Osmo Action 6 · MJPEG 1280x720@30", "sendet"])
        mute = dict(on, usb=dict(on["usb"], audio=""))
        self.assertIn("kein Ton der Kamera gefunden", self.texts(mute)[0])
        off = dict(mute, settings={"source": "usb", "audio": "none"})
        self.assertNotIn("kein Ton", self.texts(off)[0])                                              # Ton ist ausgeschaltet: kein Hinweis
        hd = {"service": True, "available": True, "signal_known": True, "signal": {"locked": False}, "state": "waiting", "settings": {"source": "hdmi"}}
        self.assertEqual(self.texts(hd), ["Kein HDMI-Signal", "wartet auf Signal"])                    # HDMI bleibt, wie es war

    def test_signal_and_state_texts(self):
        sig = {"locked": True, "width": 1920, "height": 1080, "fps": 59.94}
        self.assertEqual(self.texts({"service": True, "available": True, "signal_known": True, "signal": sig, "state": "streaming"}), ["Signal 1920×1080 · 60 Hz", "sendet"])
        self.assertEqual(self.texts({"service": True, "available": True, "signal_known": True, "signal": {"locked": False}, "state": "waiting"}),
                         ["Kein HDMI-Signal", "wartet auf Signal"])
        self.assertEqual(self.texts({"service": True, "available": True, "signal_known": False, "signal": {}, "state": "starting"}), ["", "startet …"])
        self.assertEqual(self.texts({"service": True, "available": False, "state": "unavailable"}), ["Diese Box hat keinen HDMI-Eingang", "kein HDMI-Eingang"])
        self.assertEqual(self.texts({"service": False, "message": "Der HDMI-Dienst läuft nicht", "state": "down"}), ["Der HDMI-Dienst läuft nicht", "Dienst fehlt"])
        self.assertEqual(self.texts({"service": True, "available": True, "signal_known": True, "signal": sig, "state": "off"})[1], "aus")
        self.assertEqual(self.texts({"service": True, "available": True, "signal_known": True, "signal": sig, "state": "error"})[1], "Fehler")


class Translation(unittest.TestCase):
    def test_the_new_texts_are_keys_and_english_exists(self):
        import i18n_extract
        keys = set(i18n_extract.all_keys())
        with open(os.path.join(ROOT, "web", "i18n", "en.json"), encoding="utf-8") as f:
            en = json.load(f)["exact"]
        for k in ("HDMI- und USB-Kameras", "HDMI-Eingang", "Als Kamera senden", "Bild und Ton", "HDMI-Ton", "ohne Ton", "Signal {1}×{2} · {3} Hz", "Kein HDMI-Signal",
                  "Diese Box hat keinen HDMI-Eingang", "Das Bild kommt vom HDMI-Eingang der Box", "kein HDMI-Eingang", "Dienst fehlt", "startet …"):
            self.assertIn(k, keys, k)
            self.assertTrue(en.get(k), "Englisch fehlt: " + k)
        self.assertIn("Der HDMI-Dienst läuft nicht (Software-Update oder install.sh ausführen)", keys)        # Meldung des Servers


if __name__ == "__main__":
    unittest.main()
