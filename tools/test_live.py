"""Tests für die Engine "alle Kameras immer bereit" mit Compositor (pipbox_live.py): Geometrie wie im Baustein pbpipmix, Pipeline-Text (feste Form,
alle Plätze, feste Ausgabe), Befehle für den Steuerkanal (nur Änderungen, oberstes Bild zuletzt), Regler (Zweige starten/stoppen, Wahl von Haupt- und
kleinen Bildern, anderer Schlüssel ohne Neustart, Ansicht) und die Anbindung an server.py. Die echte Kette wurde mit Messskripten auf der Box geprüft (nicht mehr im Repository, siehe Git-Historie bis 0.9.144)."""
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
_env = mock.patch.dict(os.environ, {"PIPBOX_LIVE": "1"})       # auf dem Entwicklungsrechner gibt es den belacoder sb10 nicht; nur für dieses Modul (sonst verändert es die Tests der anderen Dateien)


def setUpModule():
    _env.start()


def tearDownModule():
    _env.stop()
if not hasattr(os, "killpg"):                   # Windows (nur Entwicklungsrechner): die Module der Box brauchen Linux-Teile beim Import
    os.killpg = lambda *a, **k: None
    for _m in ("pwd", "grp", "fcntl", "termios"):
        sys.modules.setdefault(_m, mock.MagicMock())
import pipbox_live as L  # noqa: E402
import server  # noqa: E402

CFG = {"type": "pip", "main": "dji-a", "pip": "cam2", "pip2": "cam3", "pip3": "cam4", "corner": 5, "corner2": 2, "corner3": 0,
       "x": 994, "y": 0, "size_pct": 99, "size_pct2": 40, "size_pct3": 25, "audio": "main", "always_ready": True,
       "main_delay_ms": 450, "pip_delay_ms": 0, "pip2_delay_ms": 0, "pip3_delay_ms": 0,
       "styles": {"1": {"visible": True, "opacity": 100, "crop": {"l": 600, "r": 600, "t": 0, "b": 0}, "radius": 0,
                        "border": {"enabled": True, "width": 6, "color": "#ffffff", "opacity": 100}}}}


def safe(cfg=None):
    return server.PipelineStore._safe_cfg(dict(CFG, **(cfg or {})))


class Geometry(unittest.TestCase):
    def test_size_and_crop_like_the_plugin(self):
        self.assertEqual(L.pip_size(99), server.pip_size(99))
        self.assertEqual(L.pip_size(25), server.pip_size(25))
        g = L.small_geometry(safe(), 1)
        self.assertEqual((g["sw"], g["sh"]), (1904, 1072))
        l, r, t, b = g["crop"]
        self.assertEqual((l, r, t, b), (600, 600, 0, 0))               # Beschnitt in Bildpunkten der Quelle (Bezug 1920x1080), vor dem Skalieren
        self.assertEqual((g["w"], g["h"]), (714, 1072))                  # sichtbar: (1920-1200) * 1904 / 1920

    def test_crop_is_ignored_when_too_little_is_left(self):
        st = {"crop": {"l": 1000, "r": 1000, "t": 0, "b": 0}}
        self.assertEqual(L.crop_px(st, 1904, 1072), (0, 0, 0, 0))

    def test_corners_and_free_position(self):
        m = (1920 // 60) & ~1
        self.assertEqual(L.place(0, 1920, 1080, 480, 270, m, 0, 0), (m, m))
        self.assertEqual(L.place(3, 1920, 1080, 480, 270, m, 0, 0), (1920 - 480 - m, 1080 - 270 - m))
        self.assertEqual(L.place(4, 1920, 1080, 480, 270, m, 0, 0), ((1920 - 480) // 2, 1080 - 270 - m))
        self.assertEqual(L.place(5, 1920, 1080, 480, 270, m, 1000, 1000), (1440, 810))
        self.assertEqual(L.place(5, 1920, 1080, 480, 270, m, -5, 5000), (0, 810))

    def test_big_picture_keeps_its_aspect(self):
        self.assertEqual(L.fit_big((1920, 1080)), (0, 0, 1920, 1080))
        self.assertEqual(L.fit_big(None), (0, 0, 1920, 1080))
        x, y, w, h = L.fit_big((1080, 1920))                           # Hochformat: schwarze Balken links und rechts
        self.assertEqual((y, h), (0, 1080))
        self.assertLess(w, 1920)
        self.assertLessEqual(abs(x * 2 + w - 1920), 2)

    def test_ring_is_inside_the_picture(self):
        g = L.small_geometry(safe(), 1)
        top, bottom, left, right = L.ring_rects(g)
        self.assertEqual(top, (g["x"], g["y"], g["w"], g["bw"]))
        self.assertEqual(bottom[1], g["y"] + g["h"] - g["bw"])
        self.assertEqual(right[0], g["x"] + g["w"] - g["bw"])

    def test_buffer_covers_the_delay(self):
        self.assertEqual(L.buffer_ms(safe()), 750)                      # größte Verzögerung 450 ms + 300 ms
        self.assertEqual(L.buffer_ms(safe({"main_delay_ms": 0})), 600)


class PipelineText(unittest.TestCase):
    def setUp(self):
        self.text = L.build(safe())

    def test_all_places_always_exist(self):
        for s in range(4):
            for name in ("src", "vq", "dec", "scc", "crop", "q", "aq2"):
                self.assertIn(f"name=sbf{s}_{name} ", self.text + " ")
            self.assertIn(f"pipcomp.sink_{s}\n", self.text)
            self.assertIn(f"asel.sink_{s + 1}\n", self.text)
        for i in range(12):
            self.assertIn(f"name=sbb{i} ", self.text)
            self.assertIn(f"pipcomp.sink_{4 + i}\n", self.text)

    def test_output_has_a_fixed_format_and_no_old_elements(self):
        self.assertIn("video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1", self.text)
        for old in ("pbpipsel", "pbpipmix", "pbpipsink", "pbctl", "udpsrc", "rtph264depay"):
            self.assertNotIn(old, self.text)
        self.assertIn("name=venc_bps", self.text)
        self.assertIn("appsink name=appsink", self.text)
        self.assertIn("audiotestsrc is-live=true wave=silence", self.text)

    def test_all_branches_start_stopped(self):
        self.assertEqual(self.text.strip().splitlines()[-1], "#sb-absent: sbf0 sbf1 sbf2 sbf3")

    def test_slots_follow_the_binder_and_empty_slots_have_a_dummy_key(self):
        t = L.build(safe({"pip3": "", "pip2": ""}))
        self.assertIn("name=sbf0_src location=rtmp://127.0.0.1:1935/publish/dji-a ", t)
        self.assertIn("name=sbf1_src location=rtmp://127.0.0.1:1935/publish/cam2 ", t)
        self.assertIn("publish/pipbox_unused_2 ", t)
        self.assertIn("publish/pipbox_unused_3 ", t)

    def test_text_does_not_depend_on_who_is_big(self):
        swapped = safe({"main": "cam2", "pip": "dji-a"})
        # nach einem Tausch in der Einstellung bleibt die Reihenfolge der Plätze beim ersten Aufbau (Binder), der Text ist sonst gleich gebaut
        self.assertEqual(self.text.count("rtmpsrc"), L.build(swapped).count("rtmpsrc"))


class Commands(unittest.TestCase):
    def cmds(self, lay, roles, **kw):
        c = safe()
        args = dict(c=c, slot_key=["dji-a", "cam2", "cam3", "cam4"], roles=roles, dims={}, audio_ok={0, 1, 2}, hide=0, audio_pos=-1, mute=False,
                    delay_ms={"dji-a": 450})
        args.update(kw)
        return lay.commands(**args)

    def test_first_send_is_complete_then_only_changes(self):
        lay = L.Layout()
        first = self.cmds(lay, {0: 0, 1: 1, 2: 2})
        self.assertIn("pad pipcomp sink_0 alpha 1.00", first)
        self.assertIn("pad pipcomp sink_3 alpha 0", first)             # Platz 3 ohne Kamera: ausgeblendet
        self.assertIn("select asel sink_1", first)
        self.assertIn("set avol mute false", first)
        self.assertEqual(self.cmds(lay, {0: 0, 1: 1, 2: 2}), [])       # nichts geändert: nichts senden

    def test_swap_moves_only_layout_never_the_branch(self):
        lay = L.Layout()
        self.cmds(lay, {0: 0, 1: 1})
        swapped = self.cmds(lay, {1: 0, 0: 1})
        self.assertTrue(swapped)
        self.assertTrue(all(l.startswith(("set sbf", "pad pipcomp", "select asel", "set sbb")) for l in swapped))
        self.assertFalse(any(l.startswith("feed ") for l in swapped))
        # das Bild, das oben landet (jetzt das kleine auf Platz 0, Reihenfolge 2), wird zuletzt bewegt: erst das große (Platz 1), dann das kleine
        self.assertGreater(swapped.index("pad pipcomp sink_0 zorder 2"), swapped.index("pad pipcomp sink_1 zorder 1"))
        self.assertGreater(swapped.index(next(l for l in swapped if l.startswith("pad pipcomp sink_0 width"))), swapped.index("pad pipcomp sink_1 width 1920"))

    def test_scaling_caps_come_before_pad_size(self):
        lay = L.Layout()
        lines = self.cmds(lay, {0: 0, 1: 1})
        i = lines.index(next(l for l in lines if l.startswith("set sbf1_scc caps")))
        j = lines.index(next(l for l in lines if l.startswith("pad pipcomp sink_1 width")))
        self.assertLess(i, j)

    def test_hidden_position_and_audio_follow_the_view(self):
        lay = L.Layout()
        lines = self.cmds(lay, {0: 0, 1: 1}, hide=1, audio_pos=0)
        self.assertIn("pad pipcomp sink_1 alpha 0.00", lines)           # Stelle 1 ausgeblendet
        self.assertIn("select asel sink_2", lines)                     # Ton des kleinen Bildes an Stelle 1 (Platz 1)
        self.assertTrue(any(l.startswith("pad pipcomp sink_4 alpha 0.00") for l in lines))   # Rahmen mit ausgeblendet

    def test_audio_falls_back_to_silence_when_the_camera_has_no_audio(self):
        lay = L.Layout()
        lines = self.cmds(lay, {0: 0, 1: 1}, audio_ok={1})
        self.assertIn("select asel sink_0", lines)

    def test_delay_goes_to_the_compressed_queue_of_the_camera(self):
        lines = self.cmds(L.Layout(), {0: 0})
        self.assertIn(f"set sbf0_vq min-threshold-time {(450 + 33) * 1000000}", lines)
        self.assertIn("set sbf1_vq min-threshold-time 0", lines)


class Fakes:
    """Alles, was der Regler von außen braucht: Einstellung, nginx, Statistik von belacoder, Steuerkanal."""

    def __init__(self, cfg=None):
        self.cfg = dict(CFG, **(cfg or {}))
        self.pub = {}
        self.states = {}
        self.sent = []
        self.ok = True
        self.t = 100.0

    def load(self, name):
        return dict(self.cfg)

    def control(self, lines):
        if not self.ok:
            return False
        self.sent += lines
        return True

    def controller(self, tmp):
        c = L.LiveController(load_json=self.load, put_state_file=lambda *a: None, state_dir=tmp, safe_cfg=server.PipelineStore._safe_cfg,
                             view_values=server.PipelineStore.view_values, log=lambda m: None, clock=lambda: self.t,
                             fetch=lambda: dict(self.pub), stats=lambda: dict(self.states), control=self.control)
        return c

    def take(self):
        out, self.sent = self.sent, []
        return out


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.f = Fakes()
        self.c = self.f.controller(self.tmp)
        self.c.start()
        self.run_dir = mock.patch.object(L, "RUN", self.tmp)
        self.run_dir.start()

    def tearDown(self):
        self.run_dir.stop()

    def tick(self, dt=1.1):
        self.f.t += dt
        return self.c.tick()

    def test_starts_empty_and_lays_cameras_on_when_they_come(self):
        self.f.states = {0: 1, 1: 1, 2: 1, 3: 1}
        st = self.tick()
        self.assertEqual(st["alive"], [])
        self.assertFalse(any(l.startswith("feed ") for l in self.f.take()))      # niemand sendet: nichts zu starten
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}}
        self.tick()
        self.assertIn("feed sbf0 on", self.f.take())
        self.f.states[0] = 2                                              # läuft, noch kein Bild: noch nicht sichtbar
        self.tick()
        self.assertFalse(any("sink_0 alpha 1" in l for l in self.f.take()))
        self.f.states[0] = 3
        st = self.tick()
        self.assertEqual(st["alive"], ["dji-a"])
        self.assertIn("pad pipcomp sink_0 alpha 1.00", self.f.take())

    def test_small_picture_appears_without_touching_the_big_one(self):
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1920, "h": 1080, "audio": True}}
        self.f.states = {0: 3, 1: 1, 2: 1, 3: 1}
        self.tick()
        self.f.take()
        self.f.states[1] = 3
        self.tick()
        lines = self.f.take()
        self.assertFalse(any(l.startswith("pad pipcomp sink_0") for l in lines))   # das große Bild bleibt, wie es ist
        self.assertTrue(any(l.startswith("pad pipcomp sink_1 alpha 1.00") for l in lines))

    def test_portrait_small_picture_keeps_its_aspect_ratio(self):
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1080, "h": 1920, "audio": True}}
        self.f.states = {0: 3, 1: 3, 2: 1, 3: 1}
        self.tick()
        lines = self.f.take()
        w = [int(l.split()[-1]) for l in lines if l.startswith("pad pipcomp sink_1 width")][-1]
        h = [int(l.split()[-1]) for l in lines if l.startswith("pad pipcomp sink_1 height")][-1]
        self.assertLess(w, h)                                                   # Rahmen hochkant
        self.assertLessEqual(abs(w * 1920 - h * 1080), 2 * 1920)               # Seitenverhaeltnis der Quelle (bis auf Rundung auf gerade Zahlen)
        caps = [l for l in lines if l.startswith("set sbf1_scc caps")][-1]
        self.assertIn(f"width={w},height={h}", caps)
        self.assertIn("set sbf1_crop left 0", lines)                          # kein Beschnitt

    def test_feed_commands_wait_for_the_cooldown(self):
        self.f.states = {0: 1, 1: 1, 2: 1, 3: 1}
        self.f.pub = {"dji-a": {"w": 0, "h": 0, "audio": False}}
        self.tick()
        self.assertEqual([l for l in self.f.take() if l.startswith("feed")], ["feed sbf0 on"])
        self.tick(0.6)
        self.assertEqual([l for l in self.f.take() if l.startswith("feed")], [])      # noch Abkühlzeit
        self.tick(3.0)
        self.assertEqual([l for l in self.f.take() if l.startswith("feed")], ["feed sbf0 on"])

    def test_camera_gone_stops_the_branch(self):
        self.f.pub = {"dji-a": {"w": 0, "h": 0, "audio": False}}
        self.f.states = {0: 3, 1: 1, 2: 1, 3: 1}
        self.tick()
        self.f.take()
        self.f.pub = {}
        self.tick(4.0)
        lines = self.f.take()
        self.assertIn("feed sbf0 off", lines)
        self.assertIn("pad pipcomp sink_0 alpha 0", lines)

    def test_main_camera_gone_the_next_one_becomes_big_and_comes_back(self):
        pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1920, "h": 1080, "audio": True}}
        self.f.pub = dict(pub)
        self.f.states = {0: 3, 1: 3, 2: 1, 3: 1}
        self.tick(20)
        self.f.take()
        self.f.pub = {"cam2": pub["cam2"]}
        self.f.states[0] = 1
        st = self.tick(4.0)
        self.assertEqual(st["main"], "cam2")
        lines = self.f.take()
        self.assertIn("pad pipcomp sink_1 width 1920", lines)             # cam2 jetzt groß
        self.f.pub = dict(pub)
        self.f.states[0] = 3
        self.tick(1.0)
        self.assertEqual(self.tick(1.0)["main"], "cam2")                  # erst nach BACK_S (3 s) stabil wieder die eigentliche Hauptkamera
        self.tick(3.5)
        self.assertEqual(self.tick(1.0)["main"], "dji-a")

    def test_swap_in_the_settings_changes_the_layout_not_the_slots(self):
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1920, "h": 1080, "audio": True}}
        self.f.states = {0: 3, 1: 3, 2: 1, 3: 1}
        self.tick(20)
        self.f.take()
        self.f.cfg.update(main="cam2", pip="dji-a")
        self.tick()
        lines = self.f.take()
        self.assertEqual(self.c.binder.slot_key[:2], ["dji-a", "cam2"])    # Zweige bleiben, wo sie sind
        self.assertFalse(any(l.startswith("feed ") or "location" in l for l in lines))
        self.assertIn("pad pipcomp sink_1 width 1920", lines)

    def test_new_camera_in_a_free_place_needs_no_restart(self):
        self.f.cfg.update(pip3="")
        c = self.f.controller(self.tmp)
        c.start()
        self.f.states = {0: 3, 1: 3, 2: 3, 3: 1}
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam4": {"w": 1920, "h": 1080, "audio": True}}
        self.f.t += 20
        c.tick()
        self.f.take()
        self.f.cfg.update(pip3="cam4")
        self.f.t += 1
        c.tick()
        lines = self.f.take()
        # Zweig stoppen, Adresse setzen, Zweig starten: im selben Durchgang, in dieser Reihenfolge (belacoder arbeitet die Zeilen nacheinander ab)
        self.assertEqual(lines[:3], ["feed sbf3 off", "set sbf3_src location rtmp://127.0.0.1:1935/publish/cam4", "feed sbf3 on"])

    def test_other_key_in_a_place_is_set_while_the_branch_is_stopped(self):
        self.f.states = {0: 1, 1: 1, 2: 1, 3: 1}
        self.tick()
        self.f.take()
        self.f.cfg.update(pip="cam9")
        self.tick()
        lines = self.f.take()
        self.assertEqual(lines[:2], ["feed sbf1 off", "set sbf1_src location rtmp://127.0.0.1:1935/publish/cam9"])

    def test_commands_are_repeated_until_belacoder_listens(self):
        self.f.ok = False
        self.f.states = {0: 1, 1: 1, 2: 1, 3: 1}
        self.tick()
        self.assertEqual(self.f.sent, [])
        self.f.ok = True
        self.tick()
        self.assertTrue(any(l.startswith("pad pipcomp") for l in self.f.take()))      # alles kommt nach, nichts ging verloren

    def test_after_a_belacoder_restart_everything_is_sent_again(self):
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}}
        self.f.states = {0: 3, 1: 1, 2: 1, 3: 1}
        self.tick(20)
        self.f.take()
        self.tick()
        self.assertEqual(self.f.take(), [])                                   # alles gesendet, nichts mehr zu tun
        self.c.on_restart()                                                    # der neue belacoder beginnt wieder bei null
        self.f.states = {0: 1, 1: 1, 2: 1, 3: 1}
        self.tick()
        lines = self.f.take()
        self.assertIn("set sbf0_src location rtmp://127.0.0.1:1935/publish/dji-a", lines)
        self.assertIn("feed sbf0 on", lines)
        self.f.states = {0: 3, 1: 1, 2: 1, 3: 1}
        self.tick()
        self.assertIn("pad pipcomp sink_0 alpha 1.00", self.f.take())         # das Bild kommt wieder

    def test_deactivated_camera_is_not_in_the_stream(self):
        self.f.cfg.update(inactive=["cam2"])
        c = self.f.controller(self.tmp)
        c.start()
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1920, "h": 1080, "audio": True}}
        self.f.states = {0: 3, 1: 1, 2: 1, 3: 1}
        self.f.t += 20
        c.tick()
        lines = self.f.take()
        self.assertNotIn("feed sbf1 on", lines)                              # der Zweig der deaktivierten Kamera wird nicht gestartet
        self.f.states[1] = 3                                                 # lief er trotzdem (eben deaktiviert): nie im Bild
        self.f.t += 20
        c.tick()
        self.assertNotIn("cam2", c.alive)
        st = c.tick()
        self.assertIn("cam2", st["alive"])                                   # der Knopf in der Fußleiste bleibt (Anzeige), im Bild ist sie nicht
        self.assertNotIn("cam2", c.alive)
        self.assertIn("feed sbf1 off", self.f.take())

    def test_view_file_hides_pictures_and_sets_mute_and_confirms(self):
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1920, "h": 1080, "audio": True}}
        self.f.states = {0: 3, 1: 3, 2: 1, 3: 1}
        self.tick(20)
        self.f.take()
        with open(os.path.join(self.tmp, "main-view"), "w") as f:
            f.write("1 0 1\n")                                              # Stelle 1 ausgeblendet, Ton von Stelle 1, stumm
        self.tick()
        lines = self.f.take()
        self.assertIn("pad pipcomp sink_1 alpha 0.00", lines)
        self.assertIn("select asel sink_2", lines)
        self.assertIn("set avol mute true", lines)
        with open(os.path.join(self.tmp, "view-state")) as f:
            self.assertEqual(f.read().split(), ["1", "0", "1"])


class InactiveAudio(unittest.TestCase):
    def test_deactivated_camera_is_never_the_audio_source(self):
        tmp = tempfile.mkdtemp()
        f = Fakes({"inactive": ["cam2"]})
        c = f.controller(tmp)
        c.start()
        with open(os.path.join(tmp, "main-view"), "w") as fh:
            fh.write("0 0 0"+chr(10))                                          # Ton von Stelle 1 (cam2), die deaktiviert ist
        f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1920, "h": 1080, "audio": True}}
        f.states = {0: 3, 1: 3, 2: 1, 3: 1}
        f.t += 20
        with mock.patch.object(L, "RUN", tmp):
            c.tick()
        self.assertIn("select asel sink_1", f.take())                    # Ton des Hauptbildes, nicht sink_2 (cam2)

    def test_server_refuses_it(self):
        st = server.PipelineStore(os.devnull)
        st.cfg = dict(server.PipelineStore.DEFAULT, **CFG, inactive=["cam2"])
        with self.assertRaises(ValueError):
            st.set_view(audio="pip")
        with mock.patch.object(st, "save"):
            st.set_view(audio="pip2")                                     # eine aktive Kamera geht


class ServerIntegration(unittest.TestCase):
    def test_always_plan_is_the_live_engine_and_build_gives_its_text(self):
        plan = server.PipelineStore.always_plan(CFG)
        self.assertTrue(plan and plan["always"] and plan["live"])
        self.assertIn("name=sbf0_src", server.PipelineStore(os.devnull).build(dict(server.PipelineStore.DEFAULT, **CFG)))
        self.assertIsNone(server.PipelineStore.always_blocker(CFG))

    def test_everything_is_live_in_this_engine(self):
        a, b = dict(CFG), dict(CFG, size_pct=50, corner=1, pip="cam9", main_delay_ms=900)
        self.assertTrue(server.always_compatible(a, b))
        self.assertFalse(server.always_compatible(a, dict(b, always_ready=False)))

    def test_without_the_switch_the_old_modes_are_untouched(self):
        cfg = dict(CFG, always_ready=False)
        text = server.PipelineStore(os.devnull).build(dict(server.PipelineStore.DEFAULT, **cfg))
        self.assertNotIn("sbf0_src", text)

    def test_without_the_new_belacoder_the_old_engine_is_used(self):
        with mock.patch.dict(os.environ, {"PIPBOX_LIVE": "0"}):
            self.assertFalse(L.enabled(safe()))


class SendControl(unittest.TestCase):
    def test_stats_and_rtmp_parsing(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("t=1 br=1 feed_sbf0=3 feed_sbf2=1\nwarn x\n")
        self.assertEqual(L.read_stats(f.name), {0: 3, 2: 1})
        os.utime(f.name, (1, 1))
        self.assertEqual(L.read_stats(f.name), {})                       # alte Datei: belacoder läuft nicht
        os.unlink(f.name)
        xml = ("<stream><name>a</name><publishing/><video><width>1920</width><height>1080</height></video><audio><codec>AAC</codec></audio></stream>"
               "<stream><name>b</name></stream>")
        self.assertEqual(L.parse_rtmp_stats(xml), {"a": {"w": 1920, "h": 1080, "audio": True}})

    @unittest.skipUnless(hasattr(os, "O_NONBLOCK"), "nur Linux")
    def test_control_without_belacoder_fails_at_once(self):
        self.assertFalse(L.send_control(["feed sbf0 on"], "/nonexistent/fifo"))
        self.assertTrue(L.send_control([]))


if __name__ == "__main__":
    unittest.main()
