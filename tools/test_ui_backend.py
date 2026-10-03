"""Tests für vier Kameras (drittes kleines Bild), Tonauswahl, Sendeweg-Verteilung und WLAN-Anfragen (server.py)."""
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

KEYS = ["cam-a", "cam-b", "cam-c", "cam-d"]
BASE = {"type": "pip", "main": "cam-a", "pip": "cam-b", "corner": 4, "size_pct": 25, "audio": "main",
        "pip2": "cam-c", "corner2": 2, "pip3": "cam-d", "corner3": 3}


def store():
    return server.PipelineStore(os.path.join(tempfile.mkdtemp(), "pipeline.json"))


class FourCameras(unittest.TestCase):
    def test_four_cameras_are_saved(self):
        s = store()
        s.set(dict(BASE, pip3_delay_ms=200), KEYS)
        self.assertEqual((s.cfg["pip3"], s.cfg["corner3"], s.cfg["pip3_delay_ms"]), ("cam-d", 3, 200))

    def test_third_picture_needs_second(self):
        s = store()
        s.set(dict(BASE, pip2=""), KEYS)
        self.assertEqual(s.cfg["pip3"], "")

    def test_duplicates_are_refused(self):
        for bad in (dict(BASE, pip3="cam-a"), dict(BASE, pip3="cam-c"), dict(BASE, pip3="nope"),
                    dict(BASE, corner3=4), dict(BASE, corner3=2)):
            with self.assertRaises(ValueError, msg=str(bad)):
                store().set(bad, KEYS)

    def test_build_has_one_mixer_and_three_pip_inputs(self):
        s = store()
        s.set(BASE, KEYS)
        t = s.build()
        self.assertEqual(t.count("pbpipmix"), 1)
        self.assertIn("slot2=1 corner2=2 slot3=2 corner3=3", t)
        self.assertEqual(t.count("pbpipsink"), 3)
        self.assertIn("pip3-queue=pip3q_v", t)

    def test_audio_from_each_camera_has_exactly_one_audio_path(self):
        for sel in ("main", "pip", "pip2", "pip3"):
            s = store()
            s.set(dict(BASE, audio=sel), KEYS)
            t = s.build()
            self.assertEqual(t.count("opusenc"), 1, sel)
            self.assertEqual(t.count("fakesink"), 3, sel)

    def test_audio_of_missing_picture_is_refused_or_falls_back(self):
        with self.assertRaises(ValueError):
            store().set(dict(BASE, pip3="", audio="pip3"), KEYS)
        t = store().build(dict(BASE, pip3="", audio="pip3"))   # kaputte Datei: Ton vom Hauptbild
        self.assertEqual(t.count("opusenc"), 1)


class FreePosition(unittest.TestCase):
    def test_free_positions_are_saved_and_built(self):
        s = store()
        s.set(dict(BASE, corner=5, x=100, y=900, corner2=5, x2=500, y2=0, corner3=3), KEYS)     # zwei freie Bilder dürfen sich überlappen
        self.assertEqual((s.cfg["corner"], s.cfg["x"], s.cfg["y"]), (5, 100, 900))
        t = s.build()
        self.assertIn("corner=5 x=100 y=900", t)
        self.assertIn("slot2=1 corner2=5 x2=500 y2=0", t)
        self.assertIn("slot3=2 corner3=3", t)
        self.assertNotIn("x3=", t)                          # Voreinstellung: keine freien Werte mitschicken

    def test_values_are_limited(self):
        s = store()
        s.set(dict(BASE, corner=5, x=-50, y=5000), KEYS)
        self.assertEqual((s.cfg["x"], s.cfg["y"]), (0, 1000))
        with self.assertRaises(ValueError):
            store().set(dict(BASE, corner=5, x="abc"), KEYS)

    def test_presets_still_must_differ(self):
        with self.assertRaises(ValueError):
            store().set(dict(BASE, corner=3, corner2=3), KEYS)

    def test_failover_keeps_positions_with_the_place(self):
        import pipbox_send as ps
        cfg = dict(BASE, corner=5, x=100, y=200, audio="main", main_delay_ms=0, pip_delay_ms=0, pip2_delay_ms=0, pip3_delay_ms=0)
        eff, used = ps.effective_cfg(cfg, {"cam-b", "cam-c", "cam-d"})      # Hauptbild fällt aus: Plätze bleiben
        self.assertEqual((eff["corner"], eff["x"], eff["y"]), (5, 100, 200))


class PowerRequests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.p = server.Power(self.dir, False, mock.Mock(_active=lambda: False))
        pa = mock.patch.object(self.p, "status", lambda: {"helper_installed": True, "sending": False})
        pa.start()
        self.addCleanup(pa.stop)

    def test_poweroff_and_reboot_write_the_keyword_only(self):
        for act in ("poweroff", "reboot"):
            self.p.request(act, True)
            f = os.path.join(self.dir, "power-request")
            self.assertEqual(open(f).read(), act + "\n")
            self.assertEqual(stat.S_IMODE(os.stat(f).st_mode), 0o600)
            os.remove(f)

    def test_refusals(self):
        for act, conf in (("rm -rf /", True), ("poweroff", False), ("poweroff", None), (None, True), ("shutdown", True)):
            with self.assertRaises(ValueError, msg=str((act, conf))):
                self.p.request(act, conf)
            self.assertFalse(os.path.exists(os.path.join(self.dir, "power-request")))


class OldPlugin(unittest.TestCase):
    def test_old_plugin_gets_two_mixers_and_no_third_picture(self):
        s = store()
        s.set(BASE, KEYS)
        with mock.patch.object(server, "plugin_multi", lambda: False):
            t = s.build()
        self.assertEqual(t.count("pbpipmix"), 2)
        self.assertIn("pbpipmix name=pipmix2 slot=1 corner=2", t)
        self.assertNotIn("slot3", t)
        self.assertEqual(t.count("pbpipsink"), 2)
        self.assertEqual(t.count("opusenc"), 1)


class Spread(unittest.TestCase):
    def setUp(self):
        self.s = server.SrtlaStore(os.path.join(tempfile.mkdtemp(), "srtla.json"))
        self.req = {"min_kbps": 300, "max_kbps": 12000, "latency_ms": 4000, "uplinks": ["eth0", "wlan0"]}

    def test_default_is_best(self):
        self.s.set_settings(self.req, ["eth0", "wlan0"])
        self.assertEqual(self.s.data["settings"]["spread"], "best")

    def test_all_and_invalid(self):
        self.s.set_settings(dict(self.req, spread="all"), ["eth0", "wlan0"])
        self.assertEqual(self.s.data["settings"]["spread"], "all")
        with self.assertRaises(ValueError):
            self.s.set_settings(dict(self.req, spread="alles"), ["eth0", "wlan0"])


class StaleUplinks(unittest.TestCase):
    def setUp(self):
        self.s = server.SrtlaStore(os.path.join(tempfile.mkdtemp(), "srtla.json"))      # Standard: eth0 + eth1
        self.req = {"min_kbps": 300, "max_kbps": 12000, "latency_ms": 4000}

    def test_can_change_while_an_old_network_is_missing(self):
        # eth1 steht gespeichert, die Karte gibt es nicht mehr (anderes Gerät); eth2 und wlan0 sollen dazu
        self.s.set_settings(dict(self.req, uplinks=["eth0", "eth1", "eth2", "wlan0"]), ["eth0", "eth2", "wlan0"])
        self.assertEqual(self.s.data["settings"]["uplinks"], ["eth0", "eth1", "eth2", "wlan0"])

    def test_stale_network_can_be_removed(self):
        self.s.set_settings(dict(self.req, uplinks=["eth0", "eth2"]), ["eth0", "eth2"])
        self.assertEqual(self.s.data["settings"]["uplinks"], ["eth0", "eth2"])

    def test_new_unknown_network_is_refused(self):
        with self.assertRaises(ValueError):
            self.s.set_settings(dict(self.req, uplinks=["eth0", "eth7"]), ["eth0"])

    def test_at_least_one_existing_network(self):
        with self.assertRaises(ValueError):
            self.s.set_settings(dict(self.req, uplinks=["eth1"]), ["eth0"])          # nur ein fehlendes Netz
        with self.assertRaises(ValueError):
            self.s.set_settings(dict(self.req, uplinks=[]), ["eth0"])

    def test_only_uplinks_sent_keeps_other_settings(self):
        # Ein veralteter Stand der Seite darf Mindestbitrate usw. nicht zurücksetzen: beim Anhaken wird nur die Netzliste geschickt
        self.s.set_settings(dict(self.req, min_kbps=4000, uplinks=["eth0", "eth2"], spread="all"), ["eth0", "eth2", "wlan0"])
        self.s.set_settings({"uplinks": ["eth2", "wlan0"]}, ["eth0", "eth2", "wlan0"])
        st = self.s.data["settings"]
        self.assertEqual((st["min_kbps"], st["max_kbps"], st["latency_ms"], st["spread"]), (4000, 12000, 4000, "all"))
        self.assertEqual(st["uplinks"], ["eth2", "wlan0"])

    def test_names_are_checked(self):
        for bad in ("eth0; rm -rf /", "", "x" * 16, "ü"):
            with self.assertRaises(ValueError, msg=bad):
                self.s.set_settings(dict(self.req, uplinks=["eth0", bad]), ["eth0", bad])


class WifiRequests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.net = mock.Mock(iface="eth1")
        self.w = server.Wifi(self.dir, False, self.net)
        cards = [{"iface": "wlan0", "ip": "", "camera_net": False, "up": False, "ssid": "", "signal": None},
                 {"iface": "eth1", "ip": "", "camera_net": True, "up": True, "ssid": "", "signal": None}]
        self.st = {"helper_installed": True, "cards": cards, "state": "idle", "message": "", "scan": {}, "saved": []}
        p = mock.patch.object(self.w, "status", lambda: dict(self.st))
        p.start()
        self.addCleanup(p.stop)

    def req(self):
        return os.path.join(self.dir, "wifi-request")

    def test_connect_writes_private_request_file(self):
        self.w.request({"action": "connect", "iface": "wlan0", "ssid": "Hotspot", "password": "geheim1234"})
        self.assertEqual(stat.S_IMODE(os.stat(self.req()).st_mode), 0o600)
        d = json.load(open(self.req()))
        self.assertEqual((d["action"], d["iface"], d["ssid"]), ("connect", "wlan0", "Hotspot"))

    def test_refusals(self):
        for bad in ({"action": "rm -rf"}, {"action": "scan", "iface": "wlan9"}, {"action": "scan", "iface": "eth1"},
                    {"action": "connect", "iface": "wlan0", "ssid": ""}, {"action": "connect", "iface": "wlan0", "ssid": "x" * 33},
                    {"action": "connect", "iface": "wlan0", "ssid": "x", "password": "p" * 65},
                    {"action": "forget"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.w.request(bad)
            self.assertFalse(os.path.exists(self.req()))

    def test_no_helper_and_busy(self):
        self.st["helper_installed"] = False
        with self.assertRaises(ValueError):
            self.w.request({"action": "scan", "iface": "wlan0"})
        self.st.update(helper_installed=True, state="working", time=server.time.time())
        with self.assertRaises(ValueError):
            self.w.request({"action": "scan", "iface": "wlan0"})


class HelperChecks(unittest.TestCase):
    """Die Prüfungen des Root-Helfers (ohne nmcli)."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("pbwifi", os.path.join(os.path.dirname(HERE), "install", "pipbox-wifi.py"))
        cls.h = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.h)

    def test_password_rules(self):
        self.h.check_password("")                       # offenes Netz
        self.h.check_password("abcdefgh")
        self.h.check_password("a" * 63)
        self.h.check_password("0123456789abcdef" * 4)   # 64 Hex-Zeichen (PSK)
        for bad in ("short", "g" * 64, "ümlaut-passwort", "tab\there1234"):
            with self.assertRaises(ValueError, msg=bad):
                self.h.check_password(bad)

    def test_ssid_rules(self):
        self.h.check_ssid("Mein Handy")
        self.h.check_ssid("ä" * 16)                     # 32 Byte
        for bad in ("", "x" * 33, "ä" * 17, "a\nb", None):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.h.check_ssid(bad)

    def test_terse_split_unescapes(self):
        self.assertEqual(self.h.split_terse(r"*:Mein\:WLAN:80:WPA2"), ["*", "Mein:WLAN", "80", "WPA2"])


class PendingSettings(unittest.TestCase):
    DATA = {"servers": [{"id": "a1", "name": "X", "host": "h.example", "port": 5000, "streamid": "geheim"}], "selected": "a1",
            "settings": {"min_kbps": 4000, "max_kbps": 12000, "latency_ms": 4000, "spread": "all", "uplinks": ["eth2"]}}

    def sig(self, **chg):
        d = json.loads(json.dumps(self.DATA))
        d["settings"].update(chg.get("settings", {}))
        if "server" in chg:
            d["servers"][0].update(chg["server"])
        return server.srtla_signature(d)

    def test_same_data_same_signature(self):
        self.assertEqual(self.sig(), self.sig())

    def test_uplinks_do_not_need_restart(self):
        self.assertEqual(self.sig(), self.sig(settings={"uplinks": ["eth2", "wlan0"]}))

    def test_each_restart_setting_changes_its_group_only(self):
        base = self.sig()
        for chg, grp in ((dict(settings={"min_kbps": 300}), "bitrate"), (dict(settings={"max_kbps": 9000}), "bitrate"),
                         (dict(settings={"latency_ms": 2000}), "latency"), (dict(settings={"spread": "best"}), "spread"),
                         (dict(server={"host": "x.example"}), "server"), (dict(server={"streamid": "anders"}), "server")):
            new = self.sig(**chg)
            self.assertEqual([k for k in base if base[k] != new[k]], [grp], str(chg))

    def test_signature_hides_streamid(self):
        self.assertNotIn("geheim", json.dumps(self.sig()))

    def test_status_reports_pending(self):
        d = tempfile.mkdtemp()
        srt = server.SrtlaStore(os.path.join(d, "srtla.json"))
        srt.data.update(json.loads(json.dumps(self.DATA)))
        sc = server.SendControl(d, srt, store(), mock.Mock(), demo=False)
        applied = self.sig()
        with mock.patch.object(sc, "_active", return_value=True), \
                mock.patch.object(sc, "_detail", return_value={"state": "running", "applied": applied}), \
                mock.patch.object(sc, "reasons", return_value=[]), mock.patch.object(server, "belacoder_running", return_value=False):
            self.assertEqual(sc.status()["pending"], [])
            srt.data["settings"]["min_kbps"] = 300
            srt.data["settings"]["spread"] = "best"
            self.assertEqual(sc.status()["pending"], ["Bitrate", "Verteilung"])
        with mock.patch.object(sc, "_active", return_value=True), \
                mock.patch.object(sc, "_detail", return_value={"state": "running"}), \
                mock.patch.object(sc, "reasons", return_value=[]), mock.patch.object(server, "belacoder_running", return_value=False):
            self.assertEqual(sc.status()["pending"], [])         # ältere Sendekette ohne Merkwerte: keine Aussage


if __name__ == "__main__":
    unittest.main()
