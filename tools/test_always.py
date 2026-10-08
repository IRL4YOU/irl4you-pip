"""Tests für "alle Kameras immer bereit" (Issue #19): Zubringer, Platzzuordnung, Wahl von Haupt- und kleinen Bildern bei Ausfall und Rückkehr,
Steuerung, Pipeline-Text, Einstellungen und Fußleiste. Die Sendekette selbst wurde mit Messskripten auf der Box geprüft (nicht mehr im Repository, siehe Git-Historie bis 0.9.144)."""
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import pipbox_always as A  # noqa: E402
import server  # noqa: E402

_tool = mock.patch.object(server, "feeder_tool", lambda: "/usr/bin/gst-launch-1.0")      # auf dem Entwicklungsrechner gibt es gst-launch-1.0 nicht


def setUpModule():
    _tool.start()


def tearDownModule():
    _tool.stop()


class FeederCommand(unittest.TestCase):
    def test_command_has_ports_and_no_shell(self):
        cmd = A.feeder_cmd("cam-a", 2)
        self.assertEqual(cmd[:2], ["gst-launch-1.0", "-q"])
        text = " ".join(cmd)
        self.assertIn("location=rtmp://127.0.0.1:1935/publish/cam-a", text)
        self.assertIn(f"port={A.FEED_PORT + 4}", text)          # Bild: Platz 2
        self.assertIn(f"port={A.FEED_PORT + 5}", text)          # Ton
        self.assertIn("rtph264pay config-interval=1", text)
        self.assertIn("avdec_aac", text)
        self.assertNotIn(";", text)
        self.assertNotIn("|", text)

    def test_ports_match_the_pipeline(self):
        self.assertEqual(A.FEED_PORT, server.FEED_PORT)

    def test_bad_key_or_slot_is_refused(self):
        for key in ("", "A", "a b", "a;b", "../x", "a" * 41, None):
            with self.assertRaises(ValueError):
                A.feeder_cmd(key, 0)
        for slot in (-1, 4, 9):
            with self.assertRaises(ValueError):
                A.feeder_cmd("cam", slot)


class BinderTests(unittest.TestCase):
    def test_new_keys_take_free_slots_and_keep_them(self):
        b = A.Binder()
        self.assertTrue(b.update(["a", "b", "c"]))
        self.assertEqual(b.slot_key, ["a", "b", "c", ""])
        self.assertFalse(b.update(["c", "a", "b"]))                     # nur die Reihenfolge der Einstellung ändert sich: keine neue Zuordnung
        self.assertEqual(b.slot_of("b"), 1)

    def test_removed_key_frees_its_slot_and_a_new_one_takes_it(self):
        b = A.Binder()
        b.update(["a", "b", "c"])
        self.assertTrue(b.update(["a", "c", "d"]))
        self.assertEqual(b.slot_key, ["a", "d", "c", ""])               # d übernimmt den Platz von b, a und c bleiben
        self.assertIsNone(b.slot_of("b"))

    def test_more_keys_than_slots_and_duplicates(self):
        b = A.Binder()
        b.update(["a", "a", "", "b", "c", "d", "e"])
        self.assertEqual(b.slot_key, ["a", "b", "c", "d"])


class ChooserTests(unittest.TestCase):
    def setUp(self):
        self.t = [100.0]
        self.c = A.Chooser(clock=lambda: self.t[0])
        self.slots = {"a": 0, "b": 1, "c": 2}

    def line(self, alive, desired=("a", "b", "c", ""), inactive=()):
        return self.c.line(list(desired), self.slots, set(alive), inactive, now=self.t[0])

    def test_all_there_is_the_setting(self):
        self.assertEqual(self.line({"a", "b", "c"}), ("0 1 2 15", "a"))

    def test_small_pictures_stay_in_place_even_when_not_there_yet(self):
        self.assertEqual(self.line({"a"}), ("0 1 2 15", "a"))           # b und c erscheinen von selbst, sobald ihre Bilder kommen

    def test_main_missing_first_live_small_picture_takes_over(self):
        self.assertEqual(self.line({"b", "c"}), ("1 15 2 15", "b"))     # b wird Hauptbild, sein eigener Platz bleibt leer
        self.assertEqual(self.line({"c"}), ("2 1 15 15", "c"))

    def test_inactive_camera_does_not_take_over(self):
        self.assertEqual(self.line({"b", "c"}, inactive=("b",)), ("2 1 15 15", "c"))

    def test_nobody_there_main_stays_black(self):
        self.assertEqual(self.line(set()), ("0 1 2 15", "a"))
        self.assertEqual(self.line({"b"}, inactive=("b",)), ("0 1 2 15", "a"))      # nur eine deaktivierte Kamera sendet: sie springt nicht ein

    def test_original_main_returns_after_a_pause(self):
        self.line({"b", "c"})                                           # a fehlt, b ist Hauptbild
        self.t[0] += 1
        self.assertEqual(self.line({"a", "b", "c"})[1], "b")            # a ist gerade erst zurück
        self.t[0] += A.BACK_S - 1.5
        self.assertEqual(self.line({"a", "b", "c"})[1], "b")
        self.t[0] += 2
        self.assertEqual(self.line({"a", "b", "c"}), ("0 1 2 15", "a"))

    def test_original_main_back_and_substitute_gone_switches_at_once(self):
        self.line({"b"})
        self.t[0] += 1
        self.assertEqual(self.line({"a"})[1], "a")                      # die Ersatzkamera ist selbst weg: sofort wieder a

    def test_flapping_main_does_not_come_back_before_it_is_stable(self):
        self.line({"b"})
        for _ in range(4):                                              # a kommt und geht alle 2 s
            self.t[0] += 2
            self.assertEqual(self.line({"a", "b"})[1], "b")
            self.t[0] += 2
            self.assertEqual(self.line({"b"})[1], "b")

    def test_swapped_setting_is_followed(self):
        self.assertEqual(self.line({"a", "b", "c"}, desired=("b", "a", "c", "")), ("1 0 2 15", "b"))

    def test_empty_and_missing_slots(self):
        self.assertEqual(self.c.line(["a"], {"a": 0}, {"a"}, now=self.t[0]), ("0 15 15 15", "a"))
        self.assertEqual(self.c.line(["a", "x", "", ""], {"a": 0}, {"a"}, now=self.t[0]), ("0 15 15 15", "a"))      # Schlüssel ohne Platz


class AliveFile(unittest.TestCase):
    def write(self, text):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "live")
        with open(p, "w") as f:
            f.write(text)
        return p

    def test_reads_keys_by_slot(self):
        self.assertEqual(A.alive_from_file(self.write("1 0 1 0\n"), ["a", "b", "c", ""]), {"a", "c"})
        self.assertEqual(A.alive_from_file(self.write("0 0 0 1\n"), ["a", "b", "c", ""]), set())     # Platz ohne Schlüssel

    def test_bad_or_missing_file_is_none(self):
        self.assertIsNone(A.alive_from_file("/nonexistent/x", ["a"] * 4))
        for bad in ("", "1 0 1\n", "1 0 2 0\n", "a b c d\n"):
            self.assertIsNone(A.alive_from_file(self.write(bad), ["a"] * 4), bad)


class FakeProc:
    n = 0

    def __init__(self, cmd, **kw):
        FakeProc.n += 1
        self.pid = 4_000_000 + FakeProc.n             # keine echte Prozessnummer
        self.cmd = cmd
        self.rc = None

    def poll(self):
        return self.rc

    def terminate(self):
        self.rc = -15


class FeedersTests(unittest.TestCase):
    def make(self):
        self.procs, self.killed = [], []

        def popen(cmd, **kw):
            p = FakeProc(cmd, **kw)
            self.procs.append(p)
            return p

        def killpg(pid, sig):
            self.killed.append(pid)
            for p in self.procs:
                if p.pid == pid:
                    p.rc = -15
        return A.Feeders(log=lambda m: None, popen=popen, killpg=killpg)

    def wait(self, cond, t=3.0):
        end = time.time() + t
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.02)
        return False

    def test_one_feeder_per_key_and_restart_after_exit(self):
        f = self.make()
        with mock.patch.object(A, "RESTART_S", 0.05):
            f.set_keys(["a", "", "c", ""])
            self.assertTrue(self.wait(lambda: len(self.procs) == 2))
            self.procs[0].rc = 1                                          # Stream weg: der Zubringer endet
            self.assertTrue(self.wait(lambda: len(self.procs) == 3), "der Zubringer wird neu gestartet")
            f.stop_all()
        self.assertTrue(self.wait(lambda: all(p.rc is not None for p in self.procs)), "alle beendet")

    def test_changed_key_restarts_only_that_slot(self):
        f = self.make()
        f.set_keys(["a", "b", "", ""])
        self.assertTrue(self.wait(lambda: len(self.procs) == 2))
        f.set_keys(["a", "x", "", ""])
        self.assertTrue(self.wait(lambda: len(self.procs) == 3))
        self.assertIn("rtmp://127.0.0.1:1935/publish/x", " ".join(self.procs[2].cmd))
        self.assertIsNotNone(self.procs[1].rc)                           # der alte von Platz 1 ist beendet
        self.assertIsNone(self.procs[0].rc)                              # Platz 0 blieb unberührt
        f.stop_all()

    def test_stop_all_kills_the_process_group_not_just_the_process(self):
        f = self.make()
        f.set_keys(["a", "", "", ""])
        self.assertTrue(self.wait(lambda: len(self.procs) == 1))
        f.stop_all()
        self.assertTrue(self.wait(lambda: self.killed))
        self.assertEqual(self.killed[0], self.procs[0].pid)

    def test_unstartable_feeder_does_not_kill_the_loop(self):
        calls = []

        def popen(cmd, **kw):
            calls.append(1)
            raise OSError("kein gst-launch")
        f = A.Feeders(log=lambda m: None, popen=popen, killpg=lambda *a: None)
        f.set_keys(["a", "", "", ""])
        self.assertTrue(self.wait(lambda: calls))
        f.stop_all()


class ControllerTests(unittest.TestCase):
    def make(self, cfg, live_text="1 1 1 0\n"):
        d = tempfile.mkdtemp()
        self.dir = d
        self.written = {}
        self.cfg = cfg
        with open(os.path.join(d, "live"), "w") as f:
            f.write(live_text)

        def put(name, text):
            self.written[name] = text
        feeders = mock.Mock()
        self.feeders = feeders
        t = [10.0]
        self.t = t
        return A.Controller(load_json=lambda name: dict(self.cfg), put_state_file=put, delay_values=server.PipelineStore.delay_values,
                            cam_live_path=os.path.join(d, "live"), select_name="sel", delay_name="delay", feeders=feeders, log=lambda m: None,
                            clock=lambda: t[0])

    CFG = {"type": "pip", "main": "a", "pip": "b", "pip2": "c", "main_delay_ms": 100, "pip_delay_ms": 200, "pip2_delay_ms": 300}

    def test_start_binds_slots_starts_feeders_and_writes_delays_in_slot_order(self):
        c = self.make(self.CFG)
        c.start()
        self.assertEqual(c.binder.slot_key, ["a", "b", "c", ""])
        self.feeders.set_keys.assert_called_with(["a", "b", "c", ""])
        self.assertEqual(self.written["delay"].split(), ["100", "200", "300", "0"])

    def test_tick_writes_the_line_only_on_change(self):
        c = self.make(self.CFG)
        c.start()
        st = c.tick()
        self.assertEqual(self.written["sel"], "0 1 2 15\n")
        self.assertEqual(st["alive"], ["a", "b", "c"])
        self.written.clear()
        c.tick()
        self.assertNotIn("sel", self.written)                             # unverändert: keine neue Datei

    def test_main_camera_loss_changes_the_line(self):
        c = self.make(self.CFG)
        c.start()
        c.tick()
        with open(os.path.join(self.dir, "live"), "w") as f:
            f.write("0 1 1 0\n")
        c.tick()
        self.assertEqual(self.written["sel"], "1 15 2 15\n")
        self.assertEqual(c.main_key, "b")

    def test_setting_change_moves_cameras_without_new_feeders_for_known_keys(self):
        c = self.make(self.CFG)
        c.start()
        c.tick()
        self.feeders.set_keys.reset_mock()
        self.cfg = dict(self.CFG, main="b", pip="a", main_delay_ms=200, pip_delay_ms=100)       # Tausch von Hauptbild und kleinem Bild
        c.tick()
        self.assertEqual(self.written["sel"], "1 0 2 15\n")
        self.feeders.set_keys.assert_not_called()                          # gleiche Schlüssel: keine Zubringer berührt
        self.assertEqual(self.written["delay"].split(), ["100", "200", "300", "0"])     # die Verzögerung bleibt bei der Kamera

    def test_new_camera_in_the_setting_gets_a_slot_and_a_feeder(self):
        c = self.make(dict(self.CFG, pip2=""))
        c.start()
        c.tick()
        self.feeders.set_keys.reset_mock()
        self.cfg = dict(self.CFG, pip2="d")
        c.tick()
        self.feeders.set_keys.assert_called_once_with(["a", "b", "d", ""])

    def test_missing_live_file_changes_nothing(self):
        c = self.make(self.CFG)
        c.start()
        os.remove(os.path.join(self.dir, "live"))
        st = c.tick()
        self.assertNotIn("sel", self.written)
        self.assertIsNone(st["line"])

    def test_garbage_keys_in_the_setting_are_ignored(self):
        c = self.make({"type": "pip", "main": "a", "pip": "B;rm", "pip2": "../x"})
        c.start()
        self.assertEqual(c.binder.slot_key, ["a", "", "", ""])


class PipelineText(unittest.TestCase):
    CFG = {"type": "pip", "main": "cam-a", "pip": "cam-b", "pip2": "cam-c", "corner": 3, "corner2": 2, "always_ready": True}

    def build(self, cfg=None, fill=True):
        with mock.patch.object(server, "plugin_fill", lambda: fill):
            return server.PipelineStore(os.devnull).build(cfg or self.CFG)

    def test_inputs_are_local_udp_not_rtmp(self):
        t = self.build()
        for slot in range(3):
            self.assertIn(f"udpsrc port={server.FEED_PORT + 2 * slot} address=127.0.0.1", t)
            self.assertIn(f"udpsrc port={server.FEED_PORT + 2 * slot + 1} address=127.0.0.1", t)
        self.assertNotIn("rtmpsrc", t)
        self.assertNotIn("flvdemux", t)
        self.assertNotIn("cam-a", t)                      # kein Schlüssel im Pipeline-Text: ein Kamerawechsel braucht keinen Neustart

    def test_selectors_fill_and_pbctl_reports_live(self):
        t = self.build()
        self.assertIn('pbpipsel name=vsel tag-offset=true force-key=true state=', t)
        self.assertIn(f'fill-caps="{server.FEED_FILL_VIDEO}"', t)
        self.assertIn(f'fill-caps="{server.FEED_AUDIO_CAPS}"', t)
        self.assertIn(f"live-file={server.CAM_LIVE}", t)
        self.assertIn("pbpipsel name=asel", t)

    def test_all_cameras_are_in_the_swap_group_with_audio_selector(self):
        with mock.patch.object(server, "plugin_fill", lambda: True):
            plan = server.PipelineStore.always_plan(self.CFG)
        self.assertEqual(plan["cams"], ["cam-a", "cam-b", "cam-c"])
        self.assertEqual(plan["group"], 3)
        self.assertTrue(plan["asel"])
        self.assertEqual(plan["line"], "0 1 2 15")

    def test_without_the_switch_or_without_plugin_support_nothing_changes(self):
        off = dict(self.CFG, always_ready=False)
        with mock.patch.object(server, "plugin_fill", lambda: True):
            self.assertIsNone(server.PipelineStore.always_plan(off))
        self.assertNotIn("udpsrc", self.build(off))
        self.assertIsNone(self.cfg_plan(fill=False))
        self.assertIn("rtmpsrc", self.build(fill=False))      # alter Baustein: wie bisher mit rtmpsrc

    def cfg_plan(self, fill):
        with mock.patch.object(server, "plugin_fill", lambda: fill):
            return server.PipelineStore.always_plan(self.CFG)

    def test_single_picture_or_duplicate_cameras_are_not_always_mode(self):
        with mock.patch.object(server, "plugin_fill", lambda: True):
            self.assertIsNone(server.PipelineStore.always_plan({"type": "single", "main": "cam-a", "always_ready": True}))
            self.assertIsNone(server.PipelineStore.always_plan(dict(self.CFG, pip="cam-a", pip2="")))

    def test_every_pipeline_text_still_parses_as_before_for_old_settings(self):
        t = self.build({"type": "pip", "main": "cam-a", "pip": "cam-b", "corner": 3})
        self.assertIn("rtmpsrc location=rtmp://127.0.0.1:1935/publish/cam-a", t)


class SettingTests(unittest.TestCase):
    def store(self):
        return server.PipelineStore(os.path.join(tempfile.mkdtemp(), "pipeline.json"))

    def test_always_ready_is_saved_for_pip_only(self):
        s = self.store()
        s.set({"type": "pip", "main": "a", "pip": "b", "corner": 3, "always_ready": True}, ["a", "b"])
        self.assertTrue(s.cfg["always_ready"])
        s.set({"type": "single", "main": "a", "always_ready": True}, ["a", "b"])
        self.assertFalse(s.cfg["always_ready"])
        s.set({"type": "pip", "main": "a", "pip": "b", "corner": 3}, ["a", "b"])
        self.assertFalse(s.cfg["always_ready"])                           # Standard: aus

    def test_junk_value_is_not_true(self):
        for junk in ("true", 1, "ja", None):
            s = self.store()
            s.set({"type": "pip", "main": "a", "pip": "b", "corner": 3, "always_ready": junk}, ["a", "b"])
            self.assertFalse(s.cfg["always_ready"], junk)

    def test_always_compatible_changes(self):
        base = {"type": "pip", "main": "a", "pip": "b", "pip2": "c", "corner": 3, "corner2": 2, "always_ready": True, "main_delay_ms": 0}
        c = server.always_compatible
        self.assertTrue(c(base, dict(base, main="b", pip="a")))                        # Tausch
        self.assertTrue(c(base, dict(base, pip2="d")))                                 # andere Kamera am Platz
        self.assertTrue(c(base, dict(base, main_delay_ms=500, audio="pip")))           # Verzögerung, Ton
        self.assertTrue(c(base, dict(base, inactive=["b"])))
        self.assertFalse(c(base, dict(base, pip2="")))                                 # ein kleines Bild weniger: anderer Aufbau
        self.assertFalse(c(base, dict(base, corner=1)))                                # Ecke
        self.assertFalse(c(base, dict(base, size_pct=40)))                             # Größe
        self.assertFalse(c(base, dict(base, always_ready=False)))                      # der Schalter selbst
        self.assertFalse(c(dict(base, always_ready=False), dict(base, always_ready=False)))


class FooterTests(unittest.TestCase):
    """Fußleiste im Modus: nur Kameras mit Bild, Hauptbild ist, wer es jetzt wirklich ist."""

    def footer(self, live, main, active=True):
        pipeline = server.PipelineStore(os.devnull)
        pipeline.cfg = dict(server.PipelineStore.DEFAULT, type="pip", main="a", pip="b", pip2="c", corner=3, corner2=2, always_ready=True)
        sc = server.SendControl.__new__(server.SendControl)
        sc.pipeline, sc.demo = pipeline, False
        sc.cams = mock.Mock()
        sc.cams.listing = lambda host: [{"key": k, "name": k.upper(), "state": "live"} for k in "abc"]
        detail = {"always": True, "failover": {"live": live, "main": main}}
        sc._detail = lambda: detail
        sc._active = lambda: active
        sc.view_live = lambda: False
        return sc.footer()

    def test_only_live_cameras_are_listed(self):
        f = self.footer(["a", "b"], "a")
        self.assertEqual([c["key"] for c in f["cams"]], ["a", "b"])
        self.assertEqual(f["audio"]["next"], "pip")

    def test_main_is_the_effective_one(self):
        f = self.footer(["b", "c"], "b")
        self.assertEqual([(c["key"], c["main"]) for c in f["cams"]], [("b", True), ("c", False)])

    def test_audio_of_a_missing_camera_falls_back_to_a_live_one(self):
        pipeline = server.PipelineStore(os.devnull)
        pipeline.cfg = dict(server.PipelineStore.DEFAULT, type="pip", main="a", pip="b", pip2="c", corner=3, corner2=2, always_ready=True, audio="pip2")
        sc = server.SendControl.__new__(server.SendControl)
        sc.pipeline, sc.demo, sc.cams = pipeline, False, mock.Mock()
        sc.cams.listing = lambda host: [{"key": k, "name": k.upper(), "state": "live"} for k in "abc"]
        sc._detail = lambda: {"always": True, "failover": {"live": ["a", "b"], "main": "a"}}
        sc._active, sc.view_live = (lambda: True), (lambda: False)
        f = sc.footer()
        self.assertEqual(f["audio"]["src"], "main")                                    # c fehlt: Ton der ersten vorhandenen Kamera

    def test_nobody_live_does_not_crash(self):
        f = self.footer([], None)
        self.assertEqual(f["cams"], [])
        self.assertIn(f["audio"]["next"], ("main", "pip", "pip2"))


class SenderAlways(unittest.TestCase):
    """Sende-Dienst im Modus "immer bereit": Zubringer starten vor dem Encoder, Auswahl tickt, Status trägt die Belegung, Ende räumt auf."""

    def setUp(self):
        import json
        import pipbox_send as ps
        self.ps = ps
        self.tmp = tempfile.mkdtemp()
        self.state, self.work, self.run_dir = (os.path.join(self.tmp, d) for d in ("state", "work", "run"))
        for d in (self.state, self.work, self.run_dir):
            os.makedirs(d)
        self.cam_live = os.path.join(self.tmp, "cam-live")
        cfg = dict(server.PipelineStore.DEFAULT, type="pip", main="cam-a", pip="cam-b", corner=3, always_ready=True, audio="main")
        json.dump(cfg, open(os.path.join(self.state, "pipeline.json"), "w"))
        open(self.cam_live, "w").write("1 1 0 0\n")
        for p in (mock.patch.object(ps, "STATE", self.state), mock.patch.object(ps, "WORK", self.work), mock.patch.object(ps, "RUN", self.run_dir),
                  mock.patch.object(ps, "STATUS", os.path.join(self.run_dir, "status.json")), mock.patch.object(server, "CAM_LIVE", self.cam_live),
                  mock.patch.object(ps, "SWAP_BASE", {"cams": ["cam-a", "cam-b"], "group": 2})):
            p.start()
            self.addCleanup(p.stop)
        self.plan = {"cfg": cfg, "layout": ("cam-a", "cam-b"), "auto": False, "always": True}
        self.events = []

    def sender(self):
        ps = self.ps
        s = ps.Sender({"name": "T", "host": "h", "port": 1, "streamid": ""}, 2000, ["10.0.0.2"], self.plan)
        self.assertIsNotNone(s.always)
        fake = mock.Mock()
        fake.set_keys = lambda keys: self.events.append(("keys", list(keys)))
        fake.stop_all = lambda: self.events.append(("feeders-stopped",))
        s.always.feeders = fake
        real_stop = s.always.stop

        def stop():
            try:                                                           # Zustand vor dem Aufräumen merken (shutdown löscht die Datei)
                self.status = open(self.ps.STATUS).read()
            except OSError:
                self.status = None
            real_stop()
        s.always.stop = stop
        orig_tick = s.always.tick

        def tick():
            self.events.append(("tick",))
            r = orig_tick()
            if sum(1 for e in self.events if e == ("tick",)) >= 3:
                s.stop_ev.set()
            return r
        s.always.tick = tick

        def spawn(name, args, env=None):
            self.events.append(("spawn", name))
            p = mock.Mock()
            p.poll.side_effect = lambda: 0 if p.terminate.called else None
            s.procs[name] = p
        s.spawn = spawn
        s.wait_links_ready = lambda timeout=20: None
        s.flush_stats = lambda: None
        s.sync_cfg = lambda: False
        s.refresh_uplinks = lambda: None
        return s

    def test_feeders_start_before_the_encoder_and_stop_at_the_end(self):
        s = self.sender()
        s.run()
        names = [e[0] if e[0] != "spawn" else e[1] for e in self.events]
        self.assertLess(names.index("keys"), names.index("belacoder"))
        self.assertLess(names.index("srtla_send"), names.index("belacoder"))
        self.assertGreaterEqual(names.count("tick"), 3)
        self.assertEqual(names.count("belacoder"), 1)                     # nie ein Neustart wegen Kamerawechsel
        self.assertIn("feeders-stopped", names)

    def test_status_carries_slots_live_and_main(self):
        import json
        s = self.sender()
        s.run()
        st = json.loads(self.status)
        self.assertTrue(st["always"])
        fo = st["failover"]
        self.assertTrue(fo["always"])
        self.assertFalse(fo["degraded"])
        self.assertFalse(fo["waiting"])
        self.assertEqual(fo["configured"], ["cam-a", "cam-b"])
        self.assertEqual(fo["main"], "cam-a")
        self.assertEqual(sorted(fo["live"]), ["cam-a", "cam-b"])

    def test_nobody_live_at_start_is_refused(self):
        ps = self.ps
        with mock.patch.object(ps, "live_keys", lambda: set()), mock.patch.object(ps, "PLUGIN_DIR", self.tmp), \
                mock.patch.object(server, "plugin_fill", lambda: True):
            open(os.path.join(self.tmp, "libgstpbpip.so"), "w").close()
            with self.assertRaises(ps.Refuse):
                ps.prepare()


class MissingTool(unittest.TestCase):
    """Fehlt gst-launch-1.0 (frisches BELABOX-Image, Meldung von Bittersweet1987), läuft die Sendung im normalen Modus weiter, mit Hinweis."""
    CFG = dict(server.PipelineStore.DEFAULT, type="pip", main="cam-a", pip="cam-b", corner=3, always_ready=True)

    def test_without_the_tool_there_is_no_always_plan_and_a_reason(self):
        with mock.patch.object(server, "feeder_tool", lambda: None):
            self.assertIsNone(server.PipelineStore.always_plan(self.CFG))
            self.assertIn("gst-launch-1.0", server.PipelineStore.always_blocker(self.CFG))
            text = server.PipelineStore(os.devnull).build(self.CFG)
        self.assertNotIn("udpsrc port=9410", text)                     # normaler Aufbau mit RTMP-Quellen
        self.assertIn("rtmpsrc", text)

    def test_with_the_tool_there_is_no_reason(self):
        self.assertIsNotNone(server.PipelineStore.always_plan(self.CFG))
        self.assertIsNone(server.PipelineStore.always_blocker(self.CFG))

    def test_switch_off_has_no_reason(self):
        with mock.patch.object(server, "feeder_tool", lambda: None):
            self.assertIsNone(server.PipelineStore.always_blocker(dict(self.CFG, always_ready=False)))

    def test_feeder_start_failure_is_logged_once(self):
        logs = []

        def popen(*a, **k):
            raise FileNotFoundError("gst-launch-1.0")
        f = A.Feeders(log=logs.append, popen=popen)
        f.set_keys(["cam-a", "", "", ""])
        time.sleep(0.4)
        f.stop_all()
        self.assertEqual(len(logs), 1)
        self.assertIn("gstreamer1.0-tools", logs[0])


if __name__ == "__main__":
    unittest.main()
