"""Tests für die Ansicht im Betrieb (Issue #19, Vorbereitung): kleine Bilder ein-/ausblenden, Tonquelle und stumm ohne Neustart der Sendung.
Prüft die Seite des Servers und des Senders; der Baustein selbst (gst/gstpbpip.c) wurde auf der Box mit Messskripten geprüft (nicht mehr im Repository, siehe Git-Historie bis 0.9.144)."""
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import pipbox_send as ps  # noqa: E402
import server  # noqa: E402

KEYS = ["cam-a", "cam-b", "cam-c", "cam-d"]
CFG = {"type": "pip", "main": "cam-a", "pip": "cam-b", "pip2": "cam-c", "pip3": "cam-d", "corner": 3, "corner2": 2, "corner3": 0,
       "size_pct": 25, "audio": "main", "swap_cams": 4}


def store(cfg=None):
    s = server.PipelineStore(os.path.join(tempfile.mkdtemp(), "pipeline.json"))
    s.set(dict(cfg or CFG), KEYS)
    return s


def hidden(styles_visible):
    st = server.clean_styles(None)
    for k, v in styles_visible.items():
        st[k]["visible"] = v
    return st


class PipelineText(unittest.TestCase):
    def build(self, cfg=None, view=True, **kw):
        c = dict(CFG, **(cfg or {}), **kw)
        with mock.patch.object(server, "plugin_view", lambda: view), mock.patch.object(server, "plugin_multi", lambda: True), \
                mock.patch.object(server, "plugin_swap", lambda: True), mock.patch.object(server, "plugin_style", lambda: True):
            return server.PipelineStore(os.devnull).build(c)

    def test_volume_is_in_the_audio_path_once_for_every_layout(self):
        for cfg in ({"swap_cams": 4}, {"swap_cams": 2}, {"swap_cams": 0}, {"swap_cams": 0, "audio": "pip"}, {"swap_cams": 4, "audio": "pip2"},
                    {"swap_cams": 2, "audio": "pip3"}):
            text = self.build(cfg)
            self.assertEqual(text.count("volume name=avol"), 1, str(cfg))
            self.assertRegex(text, r"volume name=avol ! opusenc", str(cfg))                   # direkt vor dem Opus-Encoder (dahinter ist es nicht mehr roh)

    def test_swap_with_audio_selector_puts_volume_behind_it(self):
        text = self.build({"swap_cams": 4})
        i = text.index("pbpipsel name=asel")
        self.assertIn("volume name=avol", text[i:i + 300])

    def test_not_for_single_camera_or_old_plugin(self):
        self.assertNotIn("avol", self.build(type="single", pip="", pip2="", pip3=""))
        self.assertNotIn("avol", self.build(view=False))
        self.assertNotIn("avol", self.build(view=False, swap_cams=0))

    def test_pbctl_keeps_working_with_the_defaults(self):
        for cfg in ({"swap_cams": 4}, {"swap_cams": 0}):
            self.assertIn("pbctl name=pbctl", self.build(cfg))


class ViewValues(unittest.TestCase):
    def test_hidden_bits_and_audio_position(self):
        cfg = dict(CFG, styles=hidden({"1": False, "3": False}), audio="pip2")
        self.assertEqual(server.PipelineStore.view_values(cfg), (0b101, 1))
        self.assertEqual(server.PipelineStore.view_line(cfg, 0), "5 1 0")
        self.assertEqual(server.PipelineStore.view_line(dict(CFG), 1), "0 -1 1")

    def test_all_audio_choices(self):
        for audio, pos in (("main", -1), ("pip", 0), ("pip2", 1), ("pip3", 2)):
            self.assertEqual(server.PipelineStore.view_values(dict(CFG, audio=audio))[1], pos, audio)

    def test_audio_of_missing_picture_falls_back_like_the_pipeline(self):
        self.assertEqual(server.PipelineStore.view_values(dict(CFG, pip3="", audio="pip3"))[1], -1)
        self.assertEqual(server.PipelineStore.view_values(dict(CFG, pip2="", pip3="", audio="pip2"))[1], -1)

    def test_garbage_does_not_reach_the_line(self):
        cfg = dict(CFG, styles={"1": {"visible": "böse; rm -rf"}, "2": 5}, audio={"x": 1})
        self.assertRegex(server.PipelineStore.view_line(cfg, "ja"), r"^[0-7] -?[0-2] [01]$")


class SetView(unittest.TestCase):
    def test_visibility_and_audio_are_saved(self):
        s = store()
        self.assertEqual(s.set_view({"2": False}, "pip"), (0b010, 0))
        self.assertFalse(s.cfg["styles"]["2"]["visible"])
        self.assertEqual(s.cfg["audio"], "pip")
        with open(s.path) as f:
            saved = json.load(f)
        self.assertFalse(saved["styles"]["2"]["visible"])
        self.assertEqual(s.set_view({"2": True}), (0, 0))

    def test_other_style_values_stay(self):
        s = store(dict(CFG, styles={"1": {"crop": {"l": 400, "r": 0, "t": 0, "b": 0}, "border": {"enabled": True, "width": 8, "color": "#ff0000", "opacity": 100}}}))
        s.set_view({"1": False})
        st = s.cfg["styles"]["1"]
        self.assertEqual((st["crop"]["l"], st["border"]["width"], st["border"]["color"]), (400, 8, "#ff0000"))

    def test_refusals(self):
        s = store()
        for kw in ({"visible": {"4": True}}, {"visible": {"1": "ja"}}, {"visible": {"1": 1}}, {"visible": [1]}, {"visible": "1"}, {"audio": "pip4"}, {"audio": "Haupt"},
                   {"audio": ["main"]}):
            with self.assertRaises(ValueError, msg=str(kw)):
                s.set_view(**kw)
        s2 = store(dict(CFG, pip3="", pip2="", swap_cams=0))
        for kw in ({"visible": {"3": False}}, {"visible": {"2": False}}, {"audio": "pip2"}, {"audio": "pip3"}):
            with self.assertRaises(ValueError, msg=str(kw)):
                s2.set_view(**kw)
        s3 = store(dict(CFG, type="single"))
        with self.assertRaises(ValueError):
            s3.set_view({"1": False})
        self.assertTrue(s.cfg["styles"]["1"]["visible"])                                    # nach Ablehnungen nichts verändert
        self.assertEqual(s.cfg["audio"], "main")


class ViewOnlyChange(unittest.TestCase):
    def test_only_visibility_and_audio_count(self):
        a = dict(store().cfg)
        b = dict(a, audio="pip", styles=hidden({"1": False, "2": False}))
        self.assertTrue(server.view_only_change(a, b))
        self.assertTrue(server.view_only_change(a, dict(a)))

    def test_everything_else_needs_a_restart(self):
        a = dict(store().cfg)
        for change in ({"corner": 0}, {"size_pct": 30}, {"pip": "cam-d", "pip3": "cam-b"}, {"main_delay_ms": 10}, {"swap_cams": 2}, {"type": "single"}, {"x": 1}):
            self.assertFalse(server.view_only_change(a, dict(a, **change)), str(change))
        st = server.clean_styles(None)
        st["1"]["radius"] = 20
        self.assertFalse(server.view_only_change(a, dict(a, styles=st)))                    # Rundung ändert den Pipeline-Text
        st = server.clean_styles(None)
        st["1"]["visible"] = False
        st["1"]["crop"]["l"] = 100
        self.assertFalse(server.view_only_change(a, dict(a, styles=st)))                    # Sichtbarkeit plus Beschnitt: Neustart
        self.assertFalse(server.view_only_change(dict(a, type="single"), dict(a, type="single")))


class FakeBaustein:
    """Antwortet wie pbctl: schreibt den Zustand zurück, den die Datei vorgibt (oder nicht)."""

    def __init__(self, state_path, view_path, answer=True, delay=0.15):
        self.state_path, self.view_path, self.answer, self.delay = state_path, view_path, answer, delay
        self.stop = False
        self.t = threading.Thread(target=self.run, daemon=True)
        self.t.start()

    def run(self):
        seen = None
        while not self.stop:
            try:
                mt = os.stat(self.view_path).st_mtime_ns
                if mt != seen and self.answer:
                    seen = mt
                    time.sleep(self.delay)
                    txt = open(self.view_path).read()
                    with open(self.state_path, "w") as f:
                        f.write(txt)
            except OSError:
                pass
            time.sleep(0.02)


class ChangeView(unittest.TestCase):
    def make(self, active=True, view_live=True, audio_live=True, cfg=None):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "run"))
        self.d = d
        self.state_path = os.path.join(d, "run", "view-state")
        pipeline = server.PipelineStore(os.path.join(d, "pipeline.json"))
        pipeline.set(dict(cfg or CFG), KEYS)
        send = server.SendControl(d, None, pipeline, None, demo=False)
        send.SWAP_WAIT = 1.0
        detail = {"view_live": view_live, "audio_live": audio_live}
        patches = [mock.patch.object(send, "_active", lambda: active), mock.patch.object(send, "_detail", lambda: detail),
                   mock.patch.object(server, "VIEW_STATE", self.state_path)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.restarts = []
        send.restart_if_live = lambda: (self.restarts.append(1) or True, "Neustart")
        return send

    def baustein(self, send, **kw):
        b = FakeBaustein(self.state_path, os.path.join(self.d, server.VIEW_FILE), **kw)
        self.addCleanup(lambda: setattr(b, "stop", True))
        return b

    def test_hide_picture_live_without_restart(self):
        send = self.make()
        self.baustein(send)
        r = send.change_view(visible={"1": False})
        self.assertEqual((r["live"], r["restarted"]), (True, False))
        self.assertEqual(self.restarts, [])
        self.assertEqual(open(os.path.join(self.d, server.VIEW_FILE)).read(), "1 -1 0\n")
        self.assertFalse(send.pipeline.cfg["styles"]["1"]["visible"])                     # und gespeichert
        self.assertEqual(r["view"], {"hide": 1, "audio": -1, "mute": False})

    def test_mute_is_live_only_and_not_saved(self):
        send = self.make()
        self.baustein(send)
        r = send.change_view(mute=True)
        self.assertTrue(r["live"])
        self.assertEqual(open(os.path.join(self.d, server.VIEW_FILE)).read(), "0 -1 1\n")
        with open(send.pipeline.path) as f:
            self.assertNotIn("mute", f.read())
        r = send.change_view(visible={"2": False})                                         # Stumm bleibt beim Ändern der Sichtbarkeit
        self.assertEqual(open(os.path.join(self.d, server.VIEW_FILE)).read(), "2 -1 1\n")
        r = send.change_view(mute=False)
        self.assertEqual(open(os.path.join(self.d, server.VIEW_FILE)).read(), "2 -1 0\n")

    def test_audio_source_live(self):
        send = self.make()
        self.baustein(send)
        r = send.change_view(audio="pip")
        self.assertTrue(r["live"])
        self.assertEqual(open(os.path.join(self.d, server.VIEW_FILE)).read(), "0 0 0\n")
        self.assertEqual(send.pipeline.cfg["audio"], "pip")

    def test_audio_without_selector_falls_back_to_restart(self):
        send = self.make(audio_live=False)
        self.baustein(send)
        r = send.change_view(audio="pip")
        self.assertEqual((r["live"], r["restarted"]), (False, True))
        self.assertEqual(send.pipeline.cfg["audio"], "pip")                               # gespeichert, der Neustart übernimmt es
        r = send.change_view(visible={"1": False})                                         # Sichtbarkeit geht trotzdem live
        self.assertTrue(r["live"])

    def test_old_chain_without_view_support_restarts(self):
        send = self.make(view_live=False)
        r = send.change_view(visible={"1": False})
        self.assertEqual((r["live"], r["restarted"]), (False, True))
        self.assertFalse(send.pipeline.cfg["styles"]["1"]["visible"])

    def test_no_answer_from_the_plugin_restarts(self):
        send = self.make()
        self.baustein(send, answer=False)
        r = send.change_view(visible={"1": False})
        self.assertEqual((r["live"], r["restarted"]), (False, True))

    def test_wrong_answer_from_the_plugin_is_not_taken_for_success(self):
        send = self.make()
        self.baustein(send)
        with open(self.state_path, "w") as f:
            f.write("0 -1 0\n")                                                            # alter Stand
        r = send.change_view(visible={"1": False})
        self.assertTrue(r["live"])
        self.assertEqual(open(self.state_path).read().split(), ["1", "-1", "0"])
        b2 = self.make()
        FakeBaustein(self.state_path, os.path.join(self.d, server.VIEW_FILE), answer=False)
        with open(self.state_path, "w") as f:
            f.write("0 -1 0\n")
        self.assertFalse(b2.apply_view(1, -1, False))                                      # der Baustein hat 1 nicht übernommen

    def test_mute_that_cannot_be_applied_is_an_error_not_a_restart(self):
        send = self.make()
        self.baustein(send, answer=False)
        with self.assertRaises(ValueError):
            send.change_view(mute=True)
        self.assertEqual(self.restarts, [])

    def test_without_sending_visibility_is_saved_and_mute_is_refused(self):
        send = self.make(active=False)
        r = send.change_view(visible={"1": False}, audio="pip")
        self.assertEqual((r["live"], r["restarted"]), (False, False))
        self.assertIn("nächsten Start", r["note"])
        self.assertFalse(send.pipeline.cfg["styles"]["1"]["visible"])
        self.assertFalse(os.path.exists(os.path.join(self.d, server.VIEW_FILE)))
        with self.assertRaises(ValueError):
            send.change_view(mute=True)

    def test_bad_input(self):
        send = self.make()
        for kw in ({}, {"mute": "ja"}, {"mute": 1}, {"visible": {"9": True}}, {"audio": "x"}):
            with self.assertRaises(ValueError, msg=str(kw)):
                send.change_view(**kw)

    def test_failover_chain_is_not_live(self):
        send = self.make()
        with mock.patch.object(send, "_detail", lambda: {"view_live": True, "audio_live": True, "failover": {"degraded": True}}):
            self.assertFalse(send.view_live())
            self.assertFalse(send.audio_live())

    def test_degraded_chain_saves_without_restart(self):
        send = self.make(active=True)
        send.restart_if_live = mock.Mock(return_value=(True, "neu"))
        with mock.patch.object(send, "_detail", lambda: {"view_live": True, "audio_live": True, "failover": {"degraded": True}}):
            r = send.change_view(audio="pip")
            self.assertFalse(r["restarted"])
            self.assertIn("Notbetrieb", r["note"])
            self.assertEqual(send.pipeline.cfg["audio"], "pip")                    # gespeichert
            with self.assertRaises(ValueError):
                send.change_view(mute=True)
        send.restart_if_live.assert_not_called()

    def test_start_resets_mute_in_the_file(self):
        send = self.make(active=False)
        with open(os.path.join(self.d, server.VIEW_FILE), "w") as f:
            f.write("2 0 1\n")
        send.srtla = mock.Mock(public=lambda: {"selected": "x", "servers": [{"id": "x", "name": "n", "host": "h", "port": 1}]}, data={})
        with mock.patch.object(send, "status", lambda: {"active": False, "can_start": True, "reasons": []}):
            send.request("start", True)
        self.assertEqual(open(os.path.join(self.d, server.VIEW_FILE)).read(), "0 -1 0\n")   # aus der Einstellung, ohne Stumm
        self.assertEqual(open(os.path.join(self.d, "send-request")).read(), "start\n")

    def test_restart_keeps_the_file_untouched(self):
        send = self.make(active=True)
        with open(os.path.join(self.d, server.VIEW_FILE), "w") as f:
            f.write("0 -1 1\n")
        send.reset_mute = mock.Mock()
        with mock.patch.object(send, "status", lambda: {"active": True, "can_start": False, "reasons": []}), mock.patch.object(send, "reasons", lambda: []):
            try:
                send.request("restart", True)
            except ValueError:
                pass
        send.reset_mute.assert_not_called()

    def test_status_carries_the_view(self):
        send = self.make()
        with open(self.state_path, "w") as f:
            f.write("5 1 1\n")
        self.assertEqual(send.view_state(), {"hide": 5, "audio": 1, "mute": True})
        with open(self.state_path, "w") as f:
            f.write("kaputt\n")
        self.assertIsNone(send.view_state())
        os.remove(self.state_path)
        self.assertIsNone(send.view_state())


class SenderWritesTheView(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state, self.work, self.run = (os.path.join(self.tmp, n) for n in ("state", "work", "run"))
        os.makedirs(self.state)
        os.makedirs(self.run)
        patches = [mock.patch.object(ps, "STATE", self.state), mock.patch.object(ps, "WORK", self.work), mock.patch.object(ps, "RUN", self.run),
                   mock.patch.object(server, "VIEW_STATE", os.path.join(self.run, "view-state")),
                   mock.patch.object(server, "plugin_view", lambda: True), mock.patch.object(server, "plugin_multi", lambda: True),
                   mock.patch.object(server, "plugin_swap", lambda: True), mock.patch.object(server, "plugin_style", lambda: True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_initial_view_is_written_from_the_configuration_and_flags_are_set(self):
        cfg = dict(server.PipelineStore.DEFAULT, **dict(CFG, styles=hidden({"2": False}), audio="pip"))
        text = ps.write_pipeline(cfg)
        self.assertIn("volume name=avol", text)
        self.assertEqual(open(os.path.join(self.state, "main-view")).read(), "2 0 0\n")
        self.assertTrue(ps.VIEW_LIVE)
        self.assertTrue(ps.AUDIO_LIVE)

    def test_stale_state_file_of_the_last_chain_is_removed(self):
        stale = os.path.join(self.run, "view-state")
        open(stale, "w").write("7 2 1\n")
        ps.write_pipeline(dict(server.PipelineStore.DEFAULT, **CFG))
        self.assertFalse(os.path.exists(stale))

    def test_mute_survives_a_restart_of_the_chain(self):
        open(os.path.join(self.state, "main-view"), "w").write("0 -1 1\n")
        ps.write_pipeline(dict(server.PipelineStore.DEFAULT, **CFG))
        self.assertEqual(open(os.path.join(self.state, "main-view")).read(), "0 -1 1\n")
        open(os.path.join(self.state, "main-view"), "w").write("garbage")
        ps.write_pipeline(dict(server.PipelineStore.DEFAULT, **CFG))
        self.assertEqual(open(os.path.join(self.state, "main-view")).read(), "0 -1 0\n")

    def test_no_view_file_and_flags_off_without_support(self):
        with mock.patch.object(server, "plugin_view", lambda: False):
            text = ps.write_pipeline(dict(server.PipelineStore.DEFAULT, **CFG))
        self.assertNotIn("avol", text)
        self.assertFalse(ps.VIEW_LIVE)
        self.assertFalse(os.path.exists(os.path.join(self.state, "main-view")))

    def test_audio_is_not_live_without_audio_selector(self):
        ps.write_pipeline(dict(server.PipelineStore.DEFAULT, **dict(CFG, swap_cams=0)))
        self.assertTrue(ps.VIEW_LIVE)
        self.assertFalse(ps.AUDIO_LIVE)

    def test_status_publishes_the_flags(self):
        src = open(os.path.join(ROOT, "pipbox_send.py"), encoding="utf-8").read()
        self.assertIn('"view_live": VIEW_LIVE, "audio_live": AUDIO_LIVE', src)
        self.assertIn("server.VIEW_STATE", src)                                            # beim Beenden aufgeräumt


class Endpoint(unittest.TestCase):
    def handler(self, path, body):
        h = server.Handler.__new__(server.Handler)
        h.path, h.sent, h.hdrs = path, [], {}
        h.authed = lambda: True
        h.send_response = lambda code, *a: h.sent.append(code)
        h.send_header = lambda k, v: h.hdrs.__setitem__(k, v)
        h.end_headers = lambda: None

        class W:
            data = b""

            def write(self, b):
                W.data += b
        h.wfile, h.out = W(), W
        raw = json.dumps(body).encode()
        h.headers = {"Content-Length": str(len(raw))}
        h.rfile = type("R", (), {"read": lambda self, n: raw})()
        h.client_address = ("127.0.0.1", 1)
        return h

    def setUp(self):
        d = tempfile.mkdtemp()
        pipeline = server.PipelineStore(os.path.join(d, "pipeline.json"))
        pipeline.set(dict(CFG), KEYS)
        self.send = server.SendControl(d, None, pipeline, None, demo=True)
        server.Handler.send = self.send
        server.Handler.pipeline = pipeline
        server.Handler.cams = mock.Mock(cams=[{"key": k} for k in KEYS], listing=lambda host: cam_listing())
        self.pipeline = pipeline

    def test_view_endpoint_demo_flow(self):
        h = self.handler("/api/pipeline/view", {"visible": {"1": False}, "audio": "pip"})
        h.do_POST()
        self.assertEqual(h.sent, [200])
        out = json.loads(h.out.data)
        self.assertTrue(out["live"] and out["ok"])
        self.assertEqual(out["view"], {"hide": 1, "audio": 0, "mute": False})
        h = self.handler("/api/pipeline/view", {"mute": True})
        h.do_POST()
        self.assertEqual(json.loads(h.out.data)["view"]["mute"], True)
        self.assertEqual(self.send.view_state(), {"hide": 1, "audio": 0, "mute": True})

    def test_view_endpoint_refuses_bad_requests(self):
        for body in ({}, {"visible": {"9": True}}, {"audio": "x"}, {"mute": "ja"}, {"visible": {"1": "ja"}}):
            h = self.handler("/api/pipeline/view", body)
            h.do_POST()
            self.assertEqual(h.sent, [400], str(body))

    def test_saving_only_the_view_in_the_form_does_not_restart_when_live_is_possible(self):
        applied = []
        with mock.patch.object(self.send, "view_live", lambda: True), mock.patch.object(self.send, "audio_live", lambda: True), \
                mock.patch.object(self.send, "apply_view", lambda h, a, m: applied.append((h, a, m)) or True), \
                mock.patch.object(self.send, "restart_if_live", lambda: (True, "Neustart")) as _:
            req = dict(self.pipeline.cfg, styles={"1": {"visible": False}}, audio="pip2")
            h = self.handler("/api/pipeline", req)
            h.do_POST()
        self.assertEqual(h.sent, [200])
        out = json.loads(h.out.data)
        self.assertFalse(out["restarted"])
        self.assertIn("live", out["note"])
        self.assertEqual(applied, [(1, 1, False)])

    def test_form_changes_beyond_the_view_still_restart(self):
        with mock.patch.object(self.send, "view_live", lambda: True), mock.patch.object(self.send, "audio_live", lambda: True), \
                mock.patch.object(self.send, "apply_view", lambda h, a, m: True), \
                mock.patch.object(self.send, "restart_if_live", lambda: (True, "Neustart")):
            req = dict(self.pipeline.cfg, styles={"1": {"visible": False}}, corner=1)
            h = self.handler("/api/pipeline", req)
            h.do_POST()
        self.assertTrue(json.loads(h.out.data)["restarted"])

    def test_form_falls_back_to_restart_when_live_is_not_confirmed(self):
        with mock.patch.object(self.send, "view_live", lambda: True), mock.patch.object(self.send, "audio_live", lambda: True), \
                mock.patch.object(self.send, "apply_view", lambda h, a, m: False), \
                mock.patch.object(self.send, "restart_if_live", lambda: (True, "Neustart")):
            h = self.handler("/api/pipeline", dict(self.pipeline.cfg, styles={"1": {"visible": False}}))
            h.do_POST()
        self.assertTrue(json.loads(h.out.data)["restarted"])


NAMES = {"cam-a": "Osmo Action 4", "cam-b": "iPhone", "cam-c": "Action 5 Pro", "cam-d": "Webcam hinten"}


def cam_listing(states=None, keys=KEYS):
    states = states or {}
    return [{"key": k, "name": NAMES[k], "state": states.get(k, "live")} for k in keys]


class FooterData(unittest.TestCase):
    """Was die Fußleiste am Handy braucht (Issue #19): Kameras im Bild, Hauptbild, ausgeblendet, Tonquelle."""
    def make(self, cfg=None, listing=None, active=False):
        d = tempfile.mkdtemp()
        pipeline = server.PipelineStore(os.path.join(d, "pipeline.json"))
        pipeline.set(dict(CFG), KEYS)
        pipeline.cfg.update(cfg or {})                                                      # auch Zustände, die das Formular nicht erlaubt (alte Dateien)
        cams = mock.Mock()
        cams.listing = lambda host: listing if listing is not None else cam_listing()
        srtla = mock.Mock(data={})
        srtla.public = lambda: {"servers": [], "selected": None}
        send = server.SendControl(d, srtla, pipeline, cams, demo=True)
        patches = [mock.patch.object(server, "plugin_multi", lambda: True), mock.patch.object(server, "plugin_swap", lambda: True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return send

    def test_none_unless_picture_in_picture(self):
        self.assertIsNone(self.make(dict(CFG, type="single", pip="", pip2="", pip3="")).footer())
        self.assertIsNone(self.make(dict(CFG, pip="", pip2="", pip3="")).footer())

    def test_footer_follows_a_changed_camera_list_and_leaves_the_pictures_alone(self):
        order = ["cam-d", "cam-b", "cam-a", "cam-c"]                                        # Reihenfolge der Liste geändert (Issue #32)
        send = self.make(listing=cam_listing(keys=order))
        f = send.footer()
        self.assertEqual([c["key"] for c in f["cams"]], order)
        self.assertEqual({c["key"]: (c["slot"], c["main"]) for c in f["cams"]},
                         {c["key"]: (c["slot"], c["main"]) for c in self.make().footer()["cams"]})   # Hauptbild und kleine Bilder unverändert

    def test_all_cameras_in_the_order_of_the_camera_list_with_the_main_one_marked(self):
        f = self.make().footer()
        self.assertEqual([c["key"] for c in f["cams"]], KEYS)
        self.assertEqual([c["main"] for c in f["cams"]], [True, False, False, False])
        self.assertEqual([c["slot"] for c in f["cams"]], [0, 1, 2, 3])
        self.assertEqual([c["name"] for c in f["cams"]], [NAMES[k] for k in KEYS])         # das erste Wort bildet die Oberfläche
        self.assertEqual([c["hidden"] for c in f["cams"]], [False] * 4)

    def test_only_cameras_that_are_in_the_picture(self):
        f = self.make(dict(CFG, pip3="", swap_cams=2)).footer()
        self.assertEqual([c["key"] for c in f["cams"]], ["cam-a", "cam-b", "cam-c"])

    def test_hidden_comes_from_the_stored_visibility_without_a_chain(self):
        f = self.make(dict(CFG, styles=hidden({"1": False, "3": False}))).footer()
        self.assertEqual({c["key"]: c["hidden"] for c in f["cams"]}, {"cam-a": False, "cam-b": True, "cam-c": False, "cam-d": True})

    def test_main_picture_is_never_reported_hidden(self):
        f = self.make(dict(CFG, styles=hidden({"1": False, "2": False, "3": False}))).footer()
        self.assertFalse(f["cams"][0]["hidden"])

    def test_while_sending_what_the_plugin_reports_counts(self):
        send = self.make(dict(CFG, styles=hidden({"1": False})))
        send._fake_view = {"hide": 4, "audio": 1, "mute": True}                             # laut Baustein: Klein 3 aus, Ton von Klein 2, stumm
        f = send.footer()
        self.assertEqual({c["key"]: c["hidden"] for c in f["cams"]}, {"cam-a": False, "cam-b": False, "cam-c": False, "cam-d": True})
        self.assertEqual((f["audio"]["src"], f["audio"]["key"], f["audio"]["mute"]), ("pip2", "cam-c", True))

    def test_audio_follows_the_stored_choice_and_is_not_muted_without_a_chain(self):
        f = self.make(dict(CFG, audio="pip3")).footer()
        self.assertEqual((f["audio"]["src"], f["audio"]["key"], f["audio"]["name"], f["audio"]["mute"]), ("pip3", "cam-d", "Webcam hinten", False))

    def test_audio_cycles_through_the_existing_pictures_only(self):
        for cfg, start, order in ((CFG, "main", ["pip", "pip2", "pip3", "main"]), (dict(CFG, pip3="", swap_cams=2), "main", ["pip", "pip2", "main"]),
                                  (dict(CFG, pip2="", pip3="", swap_cams=2), "main", ["pip", "main"])):
            send = self.make(dict(cfg, audio=start))
            seen = []
            for _ in order:
                f = send.footer()
                seen.append(f["audio"]["next"])
                send.pipeline.set_view(audio=f["audio"]["next"])
            self.assertEqual(seen, order, str(cfg))

    def test_audio_of_a_missing_picture_falls_back_to_the_main_picture(self):
        send = self.make(dict(CFG, audio="pip3", pip3="", swap_cams=2))
        self.assertEqual(send.footer()["audio"]["src"], "main")

    def test_camera_that_the_list_does_not_know_keeps_its_key_as_name(self):
        f = self.make(listing=cam_listing(keys=["cam-a", "cam-b", "cam-c"])).footer()
        d = [c for c in f["cams"] if c["key"] == "cam-d"][0]
        self.assertEqual((d["name"], d["state"]), ("cam-d", "unknown"))

    def test_camera_state_is_handed_on(self):
        f = self.make(listing=cam_listing({"cam-b": "offline"})).footer()
        self.assertEqual({c["key"]: c["state"] for c in f["cams"]}["cam-b"], "offline")

    def test_status_carries_the_footer(self):
        send = self.make()
        with mock.patch.object(send, "reasons", lambda: []):
            st = send.status()
        self.assertEqual([c["key"] for c in st["footer"]["cams"]], KEYS)

    def test_swap_moves_the_main_mark_but_not_the_buttons(self):
        send = self.make()
        send.pipeline.swap_main_pip("cam-c")
        f = send.footer()
        self.assertEqual([c["key"] for c in f["cams"]], KEYS)                              # die Reihenfolge bleibt
        self.assertEqual([c["key"] for c in f["cams"] if c["main"]], ["cam-c"])
        self.assertEqual({c["key"]: c["slot"] for c in f["cams"]}, {"cam-a": 2, "cam-b": 1, "cam-c": 0, "cam-d": 3})

    def test_swap_shows_the_picture_of_the_former_main_camera(self):
        send = self.make(dict(CFG, styles=hidden({"2": False})))
        self.assertTrue(send.pipeline.swap_main_pip("cam-c"))                              # Klein 2 war ausgeblendet: jetzt ist dort cam-a
        self.assertTrue(send.pipeline.cfg["styles"]["2"]["visible"])
        self.assertFalse(send.pipeline.swap_main_pip("cam-a"))                             # zurücktauschen: nichts mehr einzublenden
        self.assertTrue(self.make(dict(CFG, styles=hidden({"1": False}))).pipeline.swap_main_pip())

    def test_swap_keeps_other_hidden_pictures_hidden(self):
        send = self.make(dict(CFG, styles=hidden({"1": False, "3": False})))
        send.pipeline.swap_main_pip("cam-d")
        v = {k: send.pipeline.cfg["styles"][k]["visible"] for k in "123"}
        self.assertEqual(v, {"1": False, "2": True, "3": True})


class FooterSwapEndpoint(Endpoint):
    def test_swap_endpoint_applies_the_view_when_a_hidden_picture_comes_back(self):
        self.pipeline.cfg["styles"] = hidden({"1": False})
        applied = []
        with mock.patch.object(self.send, "swap_live", lambda: True), mock.patch.object(self.send, "view_live", lambda: True), \
                mock.patch.object(self.send, "apply_view", lambda h, a, m: applied.append((h, a, m)) or True):
            h = self.handler("/api/pipeline/swap", {"with": "cam-b"})
            h.do_POST()
        self.assertEqual(h.sent, [200])
        out = json.loads(h.out.data)
        self.assertEqual(out["note"], "Getauscht, ohne Unterbrechung.")
        self.assertEqual(applied, [(0, -1, False)])                                        # Klein 1 ist jetzt sichtbar

    def test_swap_to_a_camera_that_is_not_connected_is_refused(self):
        server.Handler.cams = mock.Mock(cams=[{"key": k} for k in KEYS], listing=lambda host: cam_listing({"cam-b": "offline"}))
        before = dict(self.pipeline.cfg)
        with mock.patch.object(self.send, "swap_live", lambda: True), mock.patch.object(self.send, "apply_view", side_effect=AssertionError("nichts zu tun")):
            h = self.handler("/api/pipeline/swap", {"with": "cam-b"})
            h.do_POST()
        self.assertEqual(h.sent, [400])
        self.assertIn("nicht verbunden", json.loads(h.out.data)["error"])
        self.assertEqual(self.pipeline.cfg["main"], before["main"])                         # nichts getauscht

    def test_swap_without_a_name_checks_the_first_small_picture(self):
        server.Handler.cams = mock.Mock(cams=[{"key": k} for k in KEYS], listing=lambda host: cam_listing({self.pipeline.cfg["pip"]: "offline"}))
        h = self.handler("/api/pipeline/swap", {})
        h.do_POST()
        self.assertEqual(h.sent, [400])

    def test_swap_endpoint_says_so_when_the_view_cannot_be_applied(self):
        self.pipeline.cfg["styles"] = hidden({"1": False})
        with mock.patch.object(self.send, "swap_live", lambda: True), mock.patch.object(self.send, "view_live", lambda: False):
            h = self.handler("/api/pipeline/swap", {"with": "cam-b"})
            h.do_POST()
        self.assertIn("nächsten Start", json.loads(h.out.data)["note"])

    def test_swap_endpoint_leaves_the_view_alone_when_nothing_was_hidden(self):
        with mock.patch.object(self.send, "swap_live", lambda: True), mock.patch.object(self.send, "apply_view", side_effect=AssertionError("nicht nötig")):
            h = self.handler("/api/pipeline/swap", {"with": "cam-b"})
            h.do_POST()
        self.assertEqual(h.sent, [200])
        self.assertEqual(json.loads(h.out.data)["note"], "Getauscht, ohne Unterbrechung.")


class Deactivate(unittest.TestCase):
    """Kamera deaktivieren (Issue #19): springt bei Ausfall des Hauptbildes nicht als Ersatz ein."""
    def make(self, cfg=None):
        d = tempfile.mkdtemp()
        pipeline = server.PipelineStore(os.path.join(d, "pipeline.json"))
        pipeline.set(dict(CFG), KEYS)
        pipeline.cfg.update(cfg or {})
        cams = mock.Mock()
        cams.listing = lambda host: cam_listing()
        srtla = mock.Mock(data={})
        srtla.public = lambda: {"servers": [], "selected": None}
        self.send = server.SendControl(d, srtla, pipeline, cams, demo=True)
        self.pipeline = pipeline
        for p in (mock.patch.object(server, "plugin_multi", lambda: True), mock.patch.object(server, "plugin_swap", lambda: True)):
            p.start()
            self.addCleanup(p.stop)
        return pipeline

    def req(self, pl, **kw):
        return dict(pl.cfg, **kw)

    def test_set_and_clear(self):
        pl = self.make()
        pl.set_active("cam-b", False)
        pl.set_active("cam-d", False)
        self.assertEqual(pl.cfg["inactive"], ["cam-b", "cam-d"])
        self.assertEqual(json.load(open(pl.path))["inactive"], ["cam-b", "cam-d"])
        pl.set_active("cam-b", False)                                                      # zweimal ist nicht doppelt
        self.assertEqual(pl.cfg["inactive"], ["cam-d", "cam-b"])
        pl.set_active("cam-d", True)
        pl.set_active("cam-b", True)
        self.assertNotIn("inactive", pl.cfg)                                               # leer: der Schlüssel fehlt
        self.assertNotIn("inactive", json.load(open(pl.path)))

    def test_refuses_what_makes_no_sense(self):
        pl = self.make()
        for key, active, msg in (("cam-a", False, "Hauptbild"), ("cam-x", False, "nicht im Bild"), ("../x", False, "ungültig"), (5, False, "ungültig"),
                                 ("cam-b", "ja", "ungültig"), ("cam-b", None, "ungültig")):
            with self.assertRaisesRegex(ValueError, msg):
                pl.set_active(key, active)
        with self.assertRaisesRegex(ValueError, "Bild-in-Bild"):
            self.make(dict(type="single")).set_active("cam-b", False)

    def test_the_form_keeps_the_flag_and_does_not_count_as_a_change(self):
        pl = self.make()
        pl.set_active("cam-c", False)
        before = dict(pl.cfg)
        pl.set(self.req(pl, size_pct=25), KEYS)                                            # das Formular schickt "inactive" nicht mit
        self.assertEqual(pl.cfg["inactive"], ["cam-c"])
        self.assertEqual({k: v for k, v in pl.cfg.items() if k != "styles"}, {k: v for k, v in before.items() if k != "styles"})

    def test_a_camera_that_leaves_the_picture_loses_the_flag(self):
        pl = self.make()
        pl.set_active("cam-c", False)
        req = self.req(pl, pip2="", pip3="", swap_cams=2)
        req.pop("inactive")
        pl.set(req, KEYS)
        self.assertNotIn("inactive", pl.cfg)

    def test_the_form_may_set_it_and_junk_is_dropped(self):
        pl = self.make()
        pl.set(self.req(pl, inactive=["cam-b", "cam-a", "cam-x", 5, "cam-b"]), KEYS)
        self.assertEqual(pl.cfg["inactive"], ["cam-b"])                                    # Doppelte fallen weg, nur kleine Bilder zählen
        self.assertNotIn("cam-a", pl.cfg["inactive"])                                      # das Hauptbild nie
        pl.set(self.req(pl, inactive="cam-b"), KEYS)
        self.assertNotIn("inactive", pl.cfg)

    def test_a_swap_makes_the_new_main_camera_active(self):
        pl = self.make()
        pl.set_active("cam-b", False)
        pl.set_active("cam-c", False)
        pl.swap_main_pip("cam-b")
        self.assertEqual((pl.cfg["main"], pl.cfg["inactive"]), ("cam-b", ["cam-c"]))
        pl.swap_main_pip("cam-c")
        self.assertNotIn("inactive", pl.cfg)

    def test_safe_cfg_cleans_the_list(self):
        for junk, want in ((["cam-b", "x y", 5, "cam-c", "cam-d", "cam-e"], ["cam-b", "cam-c", "cam-d"]), ("cam-b", []), (None, []), ({"cam-b": 1}, [])):
            self.assertEqual(server.PipelineStore._safe_cfg(dict(CFG, inactive=junk))["inactive"], want, str(junk))
        self.assertNotIn("inactive", server.PipelineStore._safe_cfg(dict(CFG)))

    def test_footer_reports_it_for_small_pictures_only(self):
        pl = self.make()
        pl.set_active("cam-c", False)
        f = self.send.footer()
        self.assertEqual({c["key"]: c["inactive"] for c in f["cams"]}, {"cam-a": False, "cam-b": False, "cam-c": True, "cam-d": False})

    def test_start_check_counts_only_cameras_that_may_step_in(self):
        pl = self.make()
        self.send.srtla.public = lambda: {"servers": [{"id": "x"}], "selected": "x"}
        pl.set_active("cam-b", False)
        pl.set_active("cam-c", False)
        pl.set_active("cam-d", False)
        self.send.cams.listing = lambda host: cam_listing({"cam-a": "offline", "cam-b": "live", "cam-c": "offline", "cam-d": "live"})
        with mock.patch.object(server.os.path, "exists", lambda p: True):
            self.assertEqual(self.send.reasons(), ["Es sendet nur eine deaktivierte Kamera"])
            self.send.cams.listing = lambda host: cam_listing({"cam-a": "live", "cam-b": "offline", "cam-c": "offline", "cam-d": "offline"})
            self.assertEqual(self.send.reasons(), [])
            self.send.cams.listing = lambda host: cam_listing({k: "offline" for k in KEYS})
            self.assertEqual(self.send.reasons(), ["Keine Kamera sendet gerade"])
            pl.set_active("cam-d", True)
            self.send.cams.listing = lambda host: cam_listing({"cam-a": "offline", "cam-d": "live", "cam-b": "offline", "cam-c": "offline"})
            self.assertEqual(self.send.reasons(), [])

    def test_export_and_import_carry_it(self):
        pl = self.make()
        pl.set_active("cam-c", False)
        self.assertEqual(pl.cfg["inactive"], ["cam-c"])
        other = server.PipelineStore(os.path.join(tempfile.mkdtemp(), "p.json"))
        other.set(dict(pl.cfg), KEYS)
        self.assertEqual(other.cfg["inactive"], ["cam-c"])


class DeactivateEndpoint(Endpoint):
    def test_toggle_over_http(self):
        h = self.handler("/api/pipeline/active", {"key": "cam-b", "active": False})
        h.do_POST()
        self.assertEqual(h.sent, [200])
        self.assertEqual(self.pipeline.cfg["inactive"], ["cam-b"])
        h = self.handler("/api/pipeline/active", {"key": "cam-b", "active": True})
        h.do_POST()
        self.assertNotIn("inactive", self.pipeline.cfg)

    def test_errors_are_400(self):
        for body in ({"key": "cam-a", "active": False}, {"key": "cam-b"}, {"active": False}, {"key": "cam-b", "active": "nein"}, {"key": ["x"], "active": True}):
            h = self.handler("/api/pipeline/active", body)
            h.do_POST()
            self.assertEqual(h.sent, [400], str(body))


class PluginSource(unittest.TestCase):
    SRC = open(os.path.join(ROOT, "gst", "gstpbpip.c"), encoding="utf-8").read()

    def test_properties_and_protocol_exist(self):
        for needle in ('"view-file"', '"view-state-file"', '"mixer"', '"volume"', "pb_ctl_poll_view", "pb_style_strip_op", "/var/lib/pipbox/main-view",
                       "/run/pipbox-send/view-state"):
            self.assertIn(needle, self.SRC)

    def test_python_and_plugin_agree_on_the_names(self):
        self.assertIn('name=pipmix', open(os.path.join(ROOT, "server.py"), encoding="utf-8").read())      # Vorgabe des Bausteins: mixer=pipmix
        self.assertIn('name=avol', open(os.path.join(ROOT, "server.py"), encoding="utf-8").read())         # Vorgabe des Bausteins: volume=avol
        self.assertEqual(server.VIEW_FILE, "main-view")
        self.assertEqual(server.VIEW_STATE, "/run/pipbox-send/view-state")

    def test_plugin_detection_looks_for_the_new_properties(self):
        d = tempfile.mkdtemp()
        so = os.path.join(d, "x.so")
        self.assertTrue(server.VIEW_ENABLED)                                               # die Fußleiste am Handy braucht es (Issue #19)
        with mock.patch.object(server, "VIEW_ENABLED", False):
            self.assertFalse(server.plugin_view())                                         # abschaltbar, ohne dass der Baustein befragt wird
        with mock.patch.object(server, "PLUGIN_SO", so):
            server._VIEW.update(mtime=None, ok=True)
            open(so, "wb").write(b"... style1 style3 slot3 ...")
            self.assertFalse(server.plugin_view())
            time.sleep(0.01)
            open(so, "wb").write(b"... view-file ... view-state-file ...")
            os.utime(so, (time.time() + 5, time.time() + 5))
            self.assertTrue(server.plugin_view())
        server._VIEW.update(mtime=None, ok=True)


if __name__ == "__main__":
    unittest.main()
