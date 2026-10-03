"""Tests für die automatische Kamera-Umschaltung (pipbox_send.py). Ohne Kameras, ohne Dateien auf der Box, ohne Zeit."""
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import pipbox_send as ps  # noqa: E402
import server  # noqa: E402

CFG = {"type": "pip", "main": "cam-m", "pip": "cam-p", "pip2": "cam-q", "corner": 3, "corner2": 2, "size_pct": 25,
       "audio": "main", "main_delay_ms": 1500, "pip_delay_ms": 120, "pip2_delay_ms": 250, "auto_failover": True}
ALL = {"cam-m", "cam-p", "cam-q"}


def run(cfg, events, layout=None, end=None):
    """events: Liste (Sekunde, Menge sendender Kameras). Gibt die Umschaltungen als [(Sekunde, Anordnung)] zurück.
    Die Zeit läuft sekündlich bis `end` (Standard: 300 s nach dem letzten Ereignis)."""
    fo = ps.Failover(cfg, layout if layout is not None else ps.effective_cfg(cfg, ALL)[1])
    out = []
    end = end if end is not None else events[-1][0] + 300
    for t in range(0, end + 1):
        r = fo.step(t, live_at(events, t))
        if r is not None:
            out.append((t, r))
    return out


def live_at(events, t):
    cur = events[0][1]
    for sec, live in events:
        if sec <= t:
            cur = live
    return cur


class EffectiveCfg(unittest.TestCase):
    def test_all_live_equals_configuration(self):
        eff, used = ps.effective_cfg(CFG, ALL)
        self.assertEqual(used, ("cam-m", "cam-p", "cam-q"))
        for k in ("main", "pip", "pip2", "main_delay_ms", "pip_delay_ms", "pip2_delay_ms", "type", "audio"):
            self.assertEqual(eff[k], CFG[k], k)

    def test_four_cameras_with_third_small_picture(self):
        cfg = dict(CFG, pip3="cam-r", corner3=0, pip3_delay_ms=300)
        eff, used = ps.effective_cfg(cfg, {"cam-m", "cam-p", "cam-q", "cam-r"})
        self.assertEqual(used, ("cam-m", "cam-p", "cam-q", "cam-r"))
        self.assertEqual((eff["pip3"], eff["pip3_delay_ms"]), ("cam-r", 300))
        # Hauptbild fällt aus: die drei übrigen rücken auf, die Verzögerung folgt der Kamera
        eff, used = ps.effective_cfg(cfg, {"cam-p", "cam-q", "cam-r"})
        self.assertEqual((eff["main"], eff["pip"], eff["pip2"], eff["pip3"]), ("cam-p", "cam-q", "cam-r", ""))
        self.assertEqual((eff["pip_delay_ms"], eff["pip2_delay_ms"]), (250, 300))

    def test_main_missing_promotes_first_small(self):
        eff, used = ps.effective_cfg(CFG, {"cam-p", "cam-q"})
        self.assertEqual((eff["main"], eff["pip"], eff["pip2"], eff["type"]), ("cam-p", "cam-q", "", "pip"))
        # Verzögerung folgt der Kamera, nicht dem Platz
        self.assertEqual((eff["main_delay_ms"], eff["pip_delay_ms"], eff["pip2_delay_ms"]), (120, 250, 0))

    def test_only_one_live_is_single(self):
        eff, used = ps.effective_cfg(CFG, {"cam-q"})
        self.assertEqual((eff["type"], eff["main"], eff["pip"], used), ("single", "cam-q", "", ("cam-q",)))

    def test_nothing_live(self):
        self.assertEqual(ps.effective_cfg(CFG, set()), (None, ()))

    def test_audio_follows_camera(self):
        cfg = dict(CFG, audio="pip")                       # Ton kommt vom kleinen Bild (cam-p)
        self.assertEqual(ps.effective_cfg(cfg, ALL)[0]["audio"], "pip")
        self.assertEqual(ps.effective_cfg(cfg, {"cam-p", "cam-q"})[0]["audio"], "main")   # cam-p ist jetzt Hauptbild
        self.assertEqual(ps.effective_cfg(cfg, {"cam-m", "cam-q"})[0]["audio"], "main")   # cam-p fehlt: Ton vom Hauptbild

    def test_corners_stay_on_slots(self):
        eff, _ = ps.effective_cfg(CFG, {"cam-m", "cam-q"})
        self.assertEqual((eff["pip"], eff["corner"]), ("cam-q", 3))

    def test_pipelines_build_for_every_layout(self):
        store = server.PipelineStore(os.devnull)
        for live in (ALL, {"cam-p", "cam-q"}, {"cam-m", "cam-q"}, {"cam-m"}, {"cam-q"}):
            eff, used = ps.effective_cfg(CFG, live)
            text = store.build(eff)
            self.assertTrue(text, live)
            for k in used:
                self.assertIn(f"/publish/{k}", text)
            for k in ALL - set(used):
                self.assertNotIn(f"/publish/{k}", text)


class FailoverSteps(unittest.TestCase):
    def test_short_dropout_changes_nothing(self):
        self.assertEqual(run(CFG, [(0, ALL), (10, ALL - {"cam-m"}), (14, ALL)]), [])

    def test_dropout_over_5s_switches_and_returns_after_60s(self):
        sw = run(CFG, [(0, ALL), (10, ALL - {"cam-m"}), (200, ALL)])
        self.assertEqual(sw[0], (15, ("cam-p", "cam-q")))                    # 5 s nach dem Ausfall
        self.assertEqual(sw[1], (200 + 60, ("cam-m", "cam-p", "cam-q")))     # 60 s nach der Rückkehr
        self.assertEqual(len(sw), 2)

    def test_flapping_camera_does_not_come_back(self):
        ev = [(0, ALL), (10, ALL - {"cam-m"})]
        for t in range(30, 300, 20):                                         # alle 20 s kurz da, dann wieder weg
            ev += [(t, ALL), (t + 10, ALL - {"cam-m"})]
        sw = run(CFG, ev)
        self.assertEqual(len(sw), 1)

    def test_pip_drop_removes_only_that_one(self):
        sw = run(CFG, [(0, ALL), (10, ALL - {"cam-p"})])
        self.assertEqual(sw, [(15, ("cam-m", "cam-q"))])

    def test_all_dead_waits_then_starts_with_first_after_5s(self):
        sw = run(CFG, [(0, ALL), (10, set()), (100, {"cam-q"})])
        self.assertEqual(sw[0], (15, ()))
        self.assertEqual(sw[1], (105, ("cam-q",)))

    def test_unknown_statistics_change_nothing(self):
        fo = ps.Failover(CFG, ps.effective_cfg(CFG, ALL)[1])
        self.assertIsNone(fo.step(0, ALL))
        for t in range(1, 100):
            self.assertIsNone(fo.step(t, None))

    def test_single_camera_config_has_nothing_to_switch(self):
        cfg = dict(CFG, type="single", pip="", pip2="")
        self.assertEqual(ps.configured_keys(cfg), ["cam-m"])

    def test_late_newcomer_joins_after_60s(self):
        sw = run(CFG, [(0, {"cam-m"}), (30, {"cam-m", "cam-p"})], layout=("cam-m",))
        self.assertEqual(sw, [(90, ("cam-m", "cam-p"))])


class PrepareWithFailover(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state, self.work = os.path.join(self.tmp, "state"), os.path.join(self.tmp, "work")
        os.makedirs(self.state)
        import json
        json.dump({"servers": [{"id": "abcdef01", "name": "T", "host": "example.org", "port": 5000, "streamid": ""}],
                   "selected": "abcdef01", "settings": {}}, open(os.path.join(self.state, "srtla.json"), "w"))
        self.patches = [mock.patch.object(ps, "STATE", self.state), mock.patch.object(ps, "WORK", self.work),
                        mock.patch.object(ps, "PLUGIN_DIR", self.tmp),
                        mock.patch.object(server, "iface_ips", lambda: [{"iface": "eth0", "ip": "10.0.0.2"}])]
        open(os.path.join(self.tmp, "libgstpbpip.so"), "w").close()
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def write_cfg(self, **kw):
        import json
        json.dump(dict(CFG, **kw), open(os.path.join(self.state, "pipeline.json"), "w"))

    def test_start_with_main_missing_uses_the_others(self):
        self.write_cfg()
        with mock.patch.object(ps, "live_keys", lambda: {"cam-p", "cam-q"}):
            *_, plan = ps.prepare()
        self.assertEqual(plan["layout"], ("cam-p", "cam-q"))
        text = open(os.path.join(self.work, "pipeline")).read()
        self.assertIn("/publish/cam-p", text)
        self.assertNotIn("/publish/cam-m", text)
        self.assertEqual(open(os.path.join(self.state, "main-delay-ms")).read().split(), ["120", "250", "0", "0"])

    def test_start_with_nobody_is_refused(self):
        self.write_cfg()
        with mock.patch.object(ps, "live_keys", lambda: set()):
            with self.assertRaises(ps.Refuse):
                ps.prepare()

    def test_start_with_failover_off_needs_every_camera(self):
        self.write_cfg(auto_failover=False)
        with mock.patch.object(ps, "stream_live", lambda k: k != "cam-m"):
            with self.assertRaises(ps.Refuse):
                ps.prepare()

    def test_unreadable_statistics_use_configuration(self):
        self.write_cfg()
        with mock.patch.object(ps, "live_keys", lambda: None):
            *_, plan = ps.prepare()
        self.assertEqual(plan["layout"], ("cam-m", "cam-p", "cam-q"))


class SenderSwitch(unittest.TestCase):
    def test_switch_restarts_only_belacoder_and_waits_without_cameras(self):
        s = ps.Sender({"name": "T", "host": "h", "port": 1, "streamid": ""}, 2000, ["10.0.0.2"],
                      {"cfg": CFG, "layout": ("cam-m", "cam-p", "cam-q"), "auto": True})
        calls = []
        s.stop_proc = lambda n: calls.append(("stop", n)) or s.procs.__setitem__(n, None)
        s.spawn = lambda n, a, env=None: calls.append(("spawn", n))
        with mock.patch.object(ps, "write_pipeline", lambda cfg: "pipeline-text"):
            s.switch(("cam-p", "cam-q"), {})
            self.assertEqual(calls, [("stop", "belacoder"), ("spawn", "belacoder")])
            self.assertEqual((s.layout, s.waiting), (("cam-p", "cam-q"), False))
            calls.clear()
            s.switch((), {})
            self.assertEqual(calls, [("stop", "belacoder")])
            self.assertTrue(s.waiting)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ps, "RUN", d), \
                mock.patch.object(ps, "STATUS", os.path.join(d, "status.json")):
            s.write_status()
            import json
            st = json.load(open(os.path.join(d, "status.json")))
            self.assertTrue(st["failover"]["waiting"] and st["failover"]["degraded"])

class LiveUplinks(unittest.TestCase):
    def setUp(self):
        import json
        self.d = tempfile.mkdtemp()
        self.json = json
        self.ifs = [{"iface": "eth0", "ip": "192.0.2.10"}, {"iface": "eth2", "ip": "192.0.2.20"}, {"iface": "wlan0", "ip": "192.0.2.30"}]
        for name, val in (("STATE", self.d), ("WORK", self.d)):
            p = mock.patch.object(ps, name, val); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(server, "iface_ips", lambda: self.ifs); p.start(); self.addCleanup(p.stop)
        self.s = ps.Sender({"name": "x"}, 4000, ["192.0.2.10", "192.0.2.20", "192.0.2.30"])
        self.proc = mock.Mock(); self.proc.poll.return_value = None
        self.s.procs["srtla_send"] = self.proc

    def save(self, ups):
        self.json.dump({"settings": {"uplinks": ups}}, open(os.path.join(self.d, "srtla.json"), "w"))

    def test_change_writes_file_and_sends_sighup(self):
        import signal
        self.save(["eth2", "wlan0"])
        self.assertTrue(self.s.refresh_uplinks())
        self.assertEqual(open(os.path.join(self.d, "ips")).read().split(), ["192.0.2.20", "192.0.2.30"])
        self.proc.send_signal.assert_called_once_with(signal.SIGHUP)
        self.assertFalse(self.s.refresh_uplinks())                 # nichts Neues: kein zweites Signal
        self.assertEqual(self.proc.send_signal.call_count, 1)

    def test_no_usable_network_changes_nothing(self):
        self.save(["eth7"])
        self.assertFalse(self.s.refresh_uplinks())
        self.assertFalse(os.path.exists(os.path.join(self.d, "ips")))
        self.proc.send_signal.assert_not_called()

    def test_missing_networks_are_skipped(self):
        self.save(["eth0", "eth1", "eth2"])                        # eth1 gibt es nicht
        self.assertTrue(self.s.refresh_uplinks())
        self.assertEqual(open(os.path.join(self.d, "ips")).read().split(), ["192.0.2.10", "192.0.2.20"])


class CornerGate(unittest.TestCase):
    def test_positions_only_when_plugin_knows_them(self):
        with tempfile.TemporaryDirectory() as d:
            so = os.path.join(d, "p.so")
            with mock.patch.object(server, "PLUGIN_SO", so), mock.patch.dict(server._CENTER, {"mtime": None, "ok": True, "free": True}):
                self.assertEqual(len(server.pip_corners()), 6)                      # kein Baustein: alles anzeigen
                open(so, "wb").write(b"\0alt: 3 unten rechts\0")
                self.assertEqual(len(server.pip_corners()), 4)                      # alter Baustein
                os.utime(so, (1, 2))
                open(so, "wb").write(b"\0mittel: 4 unten Mitte\0")
                os.utime(so, (1, 5))
                self.assertEqual(len(server.pip_corners()), 5)                      # Baustein mit "unten Mitte"
                open(so, "wb").write(b"\0neu: 4 unten Mitte, 5 frei (x/y)\0")
                os.utime(so, (1, 9))
                self.assertEqual(len(server.pip_corners()), 6)                      # Baustein mit freier Position


if __name__ == "__main__":
    unittest.main(verbosity=2)
