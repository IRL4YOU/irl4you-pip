"""Tests für vier Kameras (drittes kleines Bild), Tonauswahl, Sendeweg-Verteilung und WLAN-Anfragen (server.py)."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
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

    def connect(self, req, saved, rc=0, err=""):
        """Ruft do_connect mit nachgebautem nmcli auf; gibt die Liste der nmcli-Aufrufe zurück (Argumente, Eingabe) und das Ergebnis."""
        calls = []

        def nm(*args, stdin=None, timeout=60):
            calls.append((args, stdin))
            fail = rc if args[:2] in (("con", "up"), ("--ask", "dev")) else 0
            return mock.Mock(returncode=fail, stdout="", stderr=err if fail else "")
        with mock.patch.object(self.h, "nm", nm), mock.patch.object(self.h, "saved_wifi", lambda: saved), \
                mock.patch.object(self.h, "check_iface", lambda i: None), mock.patch.object(self.h, "check_not_camera_profile", lambda s: None), \
                mock.patch.object(self.h, "hotspot_active", lambda i: False):
            try:
                return calls, self.h.do_connect(req), None
            except RuntimeError as e:
                return calls, None, str(e)

    def test_saved_network_without_password_uses_the_saved_profile_and_is_never_deleted(self):
        """Issue #8: Ein gespeichertes Netz ohne neues Passwort wurde gelöscht und ohne Passwort neu versucht ("Password"), danach war es weg."""
        calls, msg, err = self.connect({"iface": "wlan1", "ssid": "Bittersweet_EXT", "password": ""}, ["Bittersweet_EXT", "Anderes"])
        self.assertIsNone(err)
        self.assertIn("gespeichert", msg)
        names = [c[0] for c in calls]
        self.assertNotIn(("con", "delete", "id", "Bittersweet_EXT"), names)                      # nichts gelöscht
        self.assertIn(("con", "modify", "id", "Bittersweet_EXT", "connection.interface-name", "wlan1"), names)
        self.assertIn(("con", "up", "id", "Bittersweet_EXT", "ifname", "wlan1"), names)
        self.assertFalse(any(a[0] == "--ask" for a in names))                                    # kein Versuch ohne Passwort
        self.assertTrue(all(c[1] is None for c in calls))                                         # nichts über stdin

    def test_failed_saved_connection_keeps_the_profile_and_says_what_to_do(self):
        calls, msg, err = self.connect({"iface": "wlan1", "ssid": "Heim", "password": ""}, ["Heim"], rc=4, err="Error: Secrets were required")
        self.assertIsNone(msg)
        self.assertIn("gespeicherten Netz", err)
        self.assertIn("Passwort", err)
        self.assertNotIn(("con", "delete", "id", "Heim"), [c[0] for c in calls])

    def test_new_password_replaces_the_saved_profile_as_before(self):
        calls, msg, err = self.connect({"iface": "wlan1", "ssid": "Heim", "password": "neues-Passwort1"}, ["Heim"])
        self.assertIsNone(err)
        names = [c[0] for c in calls]
        self.assertEqual(names[0], ("con", "delete", "id", "Heim"))
        self.assertEqual(names[1][:3], ("--ask", "dev", "wifi"))
        self.assertEqual(calls[1][1], "neues-Passwort1\n")                                         # Passwort nur über stdin, nie als Argument
        self.assertFalse(any("neues-Passwort1" in " ".join(n) for n in names))

    def test_unknown_network_is_connected_as_before(self):
        calls, msg, err = self.connect({"iface": "wlan1", "ssid": "Neu", "password": "abcdefgh"}, ["Heim"])
        self.assertIsNone(err)
        self.assertNotIn(("con", "delete", "id", "Neu"), [c[0] for c in calls])
        self.assertEqual(calls[0][0][:3], ("--ask", "dev", "wifi"))

    def test_hidden_saved_network_still_goes_through_the_full_connect(self):
        calls, msg, err = self.connect({"iface": "wlan1", "ssid": "Heim", "password": "", "hidden": True}, ["Heim"])
        self.assertEqual(calls[0][0], ("con", "delete", "id", "Heim"))

    def scan(self, rescan_rc=0, reads=(), all_reads=None, other_device="", minimum=3, rescan_every=10):
        """do_scan mit nachgebautem nmcli: reads = Antworten von "dev wifi list ifname wlan1" nacheinander (die letzte wiederholt sich). Die Liste ohne
        ifname (wie in der Original-Oberfläche) liefert dasselbe mit der Spalte DEVICE, oder all_reads, falls angegeben; other_device sind Zeilen einer anderen Karte."""
        calls, status, seq, writes = [], {}, list(reads), []
        cur = {"out": ""}

        def nm(*args, stdin=None, timeout=60):
            calls.append(args)
            if args[:3] == ("dev", "wifi", "rescan"):
                return mock.Mock(returncode=rescan_rc, stdout="", stderr="")
            if "list" in args and "wifi" in args:
                if "ifname" in args:
                    cur["out"] = seq.pop(0) if len(seq) > 1 else (seq[0] if seq else "")
                    return mock.Mock(returncode=0, stdout=cur["out"], stderr="")
                src = all_reads if all_reads is not None else cur["out"]
                rows = "".join(l + ":wlan1\n" for l in src.splitlines()) + "".join(l + ":wlan0\n" for l in other_device.splitlines())
                return mock.Mock(returncode=0, stdout=rows, stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        def ws(**kw):
            writes.append(dict(kw))
            status.update(kw)
        with mock.patch.object(self.h, "nm", nm), mock.patch.object(self.h, "check_iface", lambda i: None), \
                mock.patch.object(self.h, "hotspot_active", lambda i: False), \
                mock.patch.object(self.h.time, "sleep", lambda s: None), mock.patch.object(self.h, "write_status", ws), \
                mock.patch.object(self.h, "scan_diagnostics", lambda i, log=None: ["Karte: %s:wifi:disconnected:" % i] + ["Suchlauf angefordert: " + l for l in (log or [])[:1]]), \
                mock.patch.object(self.h, "SCAN_WAIT", 12), mock.patch.object(self.h, "SCAN_MIN", minimum), \
                mock.patch.object(self.h, "SCAN_RESCAN_EVERY", rescan_every):
            clock = iter(range(0, 1000))
            with mock.patch.object(self.h.time, "time", lambda: next(clock) * 3):
                msg = self.h.do_scan("wlan1")
        self.writes = writes
        return calls, status, msg

    NETS = " :Bittersweet 5G:80:WPA2\n*:Bittersweet_EXT:57:WPA2\n :Nighthawk:90:WPA2\n :Offen:30:\n"

    def test_scan_waits_for_the_results_when_the_list_is_empty_at_first(self):
        """Issue #8: Bei manchen Sticks (TP-Link) war die Liste direkt nach dem Suchlauf leer ("0 Netze gefunden"): Jetzt wird gewartet."""
        calls, status, msg = self.scan(reads=["", "", self.NETS])
        self.assertEqual(msg, "4 Netze gefunden")
        self.assertEqual([n["ssid"] for n in status["scan"]["nets"]], ["Bittersweet_EXT", "Nighthawk", "Bittersweet 5G", "Offen"])      # verbunden zuerst, dann nach Signal
        self.assertEqual(status["scan"]["iface"], "wlan1")
        self.assertIn(("dev", "wifi", "rescan", "ifname", "wlan1"), calls)                       # der Suchlauf wird ausdrücklich angestoßen
        self.assertTrue(all("--rescan" not in c or c[c.index("--rescan") + 1] == "no" for c in calls if "list" in c))

    def test_scan_reads_the_list_for_all_cards_like_the_original_and_keeps_only_this_card(self):
        """Issue #8 (Deep Dive): Die Original-Oberfläche liest "dev wifi list" ohne ifname. Manche Sticks melden ihre Netze nur auf diesem Weg."""
        calls, status, msg = self.scan(reads=[""], all_reads=self.NETS, other_device=" :Nachbarkarte:99:WPA2\n")
        self.assertEqual(msg, "4 Netze gefunden")
        self.assertNotIn("Nachbarkarte", [n["ssid"] for n in status["scan"]["nets"]])             # Netze einer anderen Karte gehören nicht in diese Liste
        self.assertTrue(any("list" in c and "ifname" not in c for c in calls))                    # ohne ifname gelesen
        self.assertTrue(any("list" in c and "ifname" in c for c in calls))                        # und mit ifname

    def test_scan_shows_networks_while_it_is_still_running(self):
        calls, status, msg = self.scan(reads=[" :Eins:50:WPA2\n", " :Eins:50:WPA2\n :Zwei:60:WPA2\n"])
        partial = [w["scan"] for w in self.writes if w["scan"].get("partial")]
        self.assertTrue(partial)
        self.assertEqual([n["ssid"] for n in partial[0]["nets"]], ["Eins"])                       # schon der erste Treffer wird angezeigt
        self.assertEqual([n["ssid"] for n in partial[-1]["nets"]], ["Zwei", "Eins"])
        self.assertFalse(status["scan"].get("partial"))                                             # das Endergebnis ist nicht mehr vorläufig

    def test_scan_does_not_stop_on_old_results_before_the_minimum_time(self):
        calls, status, msg = self.scan(reads=[" :Alt:50:WPA2\n"] * 3 + [" :Alt:50:WPA2\n :Neu:60:WPA2\n"], minimum=10)
        self.assertEqual(msg, "2 Netze gefunden")                                                  # das Netz, das erst der neue Suchlauf findet, ist dabei

    def test_scan_asks_for_a_new_rescan_while_the_list_stays_empty(self):
        calls, status, msg = self.scan(reads=[""], rescan_every=4)
        self.assertGreaterEqual(len([c for c in calls if c[:3] == ("dev", "wifi", "rescan")]), 2)

    def test_empty_scan_carries_technical_details(self):
        calls, status, msg = self.scan(reads=[""])
        self.assertEqual(status["scan"]["nets"], [])
        self.assertEqual(status["scan"]["debug"], ["Karte: wlan1:wifi:disconnected:", "Suchlauf angefordert: dev wifi rescan: ok"])
        self.assertIn("technische Angaben", msg)
        calls, status, msg = self.scan(reads=[self.NETS])
        self.assertNotIn("debug", status["scan"])                                                   # mit Treffern keine Angaben

    def test_scan_asks_like_the_original_for_all_cards_and_then_for_this_one_and_keeps_the_answers(self):
        calls, status, msg = self.scan(rescan_rc=1, reads=[""])
        first = [c for c in calls if c[:3] == ("dev", "wifi", "rescan")][:2]
        self.assertEqual(first, [("dev", "wifi", "rescan"), ("dev", "wifi", "rescan", "ifname", "wlan1")])
        self.assertTrue(status["scan"]["debug"][1].startswith("Suchlauf angefordert: dev wifi rescan: "))      # die Meldung von NetworkManager steht in den Angaben

    def test_diagnostics_shorten_mac_addresses_and_survive_missing_tools(self):
        out = self.h.scan_diagnostics("wlan9")                                                      # hier gibt es weder nmcli noch die Karte
        self.assertIsInstance(out, list)
        self.assertLessEqual(len(out), 60)
        self.assertEqual(self.h.MAC_RE.sub(lambda m: m.group(1) + ":xx:xx:xx", "GENERAL.HWADDR:00:11:22:33:44:55"), "GENERAL.HWADDR:00:11:22:xx:xx:xx")

    def test_scan_with_a_rescan_error_still_reads_the_list(self):
        calls, status, msg = self.scan(rescan_rc=1, reads=[self.NETS])                          # "Suchlauf gerade nicht erlaubt"
        self.assertEqual(msg, "4 Netze gefunden")

    def test_scan_that_stays_empty_says_what_to_do(self):
        calls, status, msg = self.scan(reads=[""])
        self.assertIn("Keine Netze gefunden", msg)
        self.assertIn("noch einmal", msg)
        self.assertEqual(status["scan"]["nets"], [])
        self.assertLess(len([c for c in calls if "list" in c]), 30)                            # es wird nicht endlos gefragt

    def test_scan_merges_networks_that_appear_a_moment_later(self):
        calls, status, msg = self.scan(reads=[" :Eins:50:WPA2\n", " :Eins:50:WPA2\n :Zwei:60:WPA2\n"])
        self.assertEqual(msg, "2 Netze gefunden")

    def test_terse_split_unescapes(self):
        self.assertEqual(self.h.split_terse(r"*:Mein\:WLAN:80:WPA2"), ["*", "Mein:WLAN", "80", "WPA2"])


class RootHelperHardening(unittest.TestCase):
    """Root-Helfer und Pipeline-Erzeugung: Eingaben aus dem Ordner des Benutzers pipbox dürfen nichts einschleusen."""

    def helper(self, name):
        import importlib.util
        spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(os.path.dirname(HERE), "install", name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_read_req_refuses_symlinks_and_limits_size(self):
        d = tempfile.mkdtemp()
        real, link = os.path.join(d, "real"), os.path.join(d, "req")
        with open(real, "w") as f:
            f.write("check\n" + "x" * 10000)
        os.symlink(real, link)
        for name in ("pipbox-update", "pipbox-remote", "pipbox-swupdate", "pipbox-wifi", "pipbox-power"):
            m = self.helper(name)
            with self.assertRaises(OSError, msg=name):
                m.read_req(link)
            self.assertEqual(len(m.read_req(real, 100)), 100, name)
            self.assertTrue(m.read_req(real).startswith("check"), name)

    def test_read_req_refuses_non_regular_files(self):
        m = self.helper("pipbox-power")
        with self.assertRaises(OSError):
            m.read_req("/dev/null")

    def test_pipeline_numbers_cannot_inject_text(self):
        cfg = dict(BASE, corner="3 ! filesink location=/etc/x", size_pct="25 ! fakesink", x="1 ! y", main_delay_ms="9;x")
        text = server.PipelineStore(os.devnull).build({**server.PipelineStore.DEFAULT, **cfg})
        self.assertTrue(text)
        for bad in ("filesink", "/etc/x", ";", "25 !", "1 ! y"):
            self.assertNotIn(bad, text)
        self.assertIn("width-pct=25 ", text)                 # ungültige Zahl fällt auf den erlaubten Bereich zurück

    def test_safe_cfg_clamps(self):
        c = server.PipelineStore._safe_cfg({"type": "pip", "corner": 99, "size_pct": 1000, "x": -5, "pip_delay_ms": 99999})
        self.assertEqual((c["corner"], c["size_pct"], c["x"], c["pip_delay_ms"]), (len(server.PIP_CORNERS) - 1, 100, 0, 3000))

    def test_work_dir_replaced_when_not_ours(self):
        sys.path.insert(0, os.path.dirname(HERE))
        import pipbox_send as ps
        d = tempfile.mkdtemp()
        target = os.path.join(d, "target")
        os.mkdir(target)
        work = os.path.join(d, "work")
        os.symlink(target, work)
        with mock.patch.object(ps, "WORK", work):
            ps.ensure_work()
            self.assertFalse(os.path.islink(work))
            self.assertEqual(stat.S_IMODE(os.stat(work).st_mode), 0o700)
        self.assertTrue(os.path.isdir(target))               # das Ziel des Verweises bleibt unangetastet


class BtDriverButton(unittest.TestCase):
    """Treiber von Hand einrichten: die Oberfläche legt nur das Stichwort "install" ab und liest den Stand des Root-Helfers."""

    def make(self, status=None, unit=True):
        d = tempfile.mkdtemp()
        bd = server.BtDriver(d, demo=False)
        bd.STATUS = os.path.join(d, "status.json")
        bd.UNIT = os.path.join(d, "unit") if unit else os.path.join(d, "fehlt")
        open(os.path.join(d, "unit"), "w").close()
        if status is not None:
            with open(bd.STATUS, "w") as f:
                json.dump(status, f)
        return bd, d

    def test_request_writes_the_keyword_only(self):
        bd, d = self.make({"state": "ok"})
        bd.request()
        self.assertEqual(open(os.path.join(d, "btdriver-request")).read(), "install\n")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, "btdriver-request")).st_mode), 0o600)

    def test_request_needs_the_helper_and_refuses_while_working(self):
        bd, d = self.make({"state": "ok"}, unit=False)
        with self.assertRaises(ValueError):
            bd.request()
        bd, d = self.make({"state": "working", "time": int(time.time())})
        with self.assertRaises(ValueError):
            bd.request()
        self.assertFalse(os.path.exists(os.path.join(d, "btdriver-request")))
        bd, d = self.make({"state": "working", "time": int(time.time()) - 7200})            # hängengebliebener Stand: neu anstoßen erlaubt
        bd.request()

    def test_status_passes_only_known_fields(self):
        bd, _ = self.make({"state": "working", "step": 3, "steps": 5, "step_text": "Treiber wird eingespielt", "message": "m", "manual": True, "time": 5, "geheim": "x"})
        st = bd.status()
        self.assertEqual((st["state"], st["step"], st["steps"], st["manual"], st["helper_installed"]), ("working", 3, 5, True, True))
        self.assertNotIn("geheim", st)

    def test_status_survives_missing_or_broken_file(self):
        bd, d = self.make()
        self.assertEqual(bd.status()["state"], "")
        with open(bd.STATUS, "w") as f:
            f.write("{kaputt")
        self.assertEqual(bd.status()["state"], "")
        with open(bd.STATUS, "w") as f:
            json.dump({"state": "boese", "step": "x"}, f)
        self.assertEqual(bd.status()["state"], "")

    def test_demo_runs(self):
        bd = server.BtDriver(tempfile.mkdtemp(), demo=True)
        bd.request()
        self.assertEqual(bd.status()["state"], "working")


class LogModeSwitch(unittest.TestCase):
    """Protokoll-Modus: Oberfläche legt nur ein festes Stichwort ab, der Root-Helfer stellt um."""

    def helper(self, name):
        import importlib.util
        spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(os.path.dirname(HERE), "install", name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_request_writes_keyword_only(self):
        d = tempfile.mkdtemp()
        lm = server.LogMode(d, demo=False)
        with mock.patch.object(lm, "status", return_value={"mode": "ausfuehrlich", "helper_installed": True}):
            lm.request("sparsam")
            self.assertEqual(open(os.path.join(d, "logmode-request")).read(), "sparsam\n")
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, "logmode-request")).st_mode), 0o600)
            for bad in ("", "aus", "sparsam; reboot", None, 5, "../x"):
                with self.assertRaises(ValueError, msg=str(bad)):
                    lm.request(bad)

    def test_request_needs_installed_helper(self):
        lm = server.LogMode(tempfile.mkdtemp(), demo=False)
        with mock.patch.object(lm, "status", return_value={"mode": "ausfuehrlich", "helper_installed": False}):
            with self.assertRaises(ValueError):
                lm.request("sparsam")

    def test_status_defaults_to_verbose_without_file(self):
        lm = server.LogMode(tempfile.mkdtemp(), demo=False)
        with mock.patch.object(server, "read", return_value=None):
            self.assertEqual(lm.status()["mode"], "ausfuehrlich")        # ältere Installation schreibt wie bisher dauerhaft
        with mock.patch.object(server, "read", return_value="sparsam\n"):
            self.assertEqual(lm.status()["mode"], "sparsam")
        with mock.patch.object(server, "read", return_value="irgendwas"):
            self.assertEqual(lm.status()["mode"], "ausfuehrlich")

    def test_demo_switches(self):
        lm = server.LogMode(tempfile.mkdtemp(), demo=True)
        lm.request("sparsam")
        self.assertEqual(lm.status()["mode"], "sparsam")

    def test_helper_apply_writes_config_and_restarts_journald(self):
        m = self.helper("pipbox-logmode")
        d = tempfile.mkdtemp()
        calls = []
        with mock.patch.multiple(m, CONF_DIR=d + "/etc", MODE_FILE=d + "/etc/logmode", JOURNAL_DIR=d + "/j",
                                 JOURNAL_CONF=d + "/j/pipbox-journal.conf", OLD_JOURNAL_CONF=d + "/j/pipbox-persistent.conf",
                                 RUN=d + "/run", JOURNAL_LOG_DIR=d + "/varlog"), \
                mock.patch.object(m.subprocess, "run", side_effect=lambda *a, **k: calls.append(a[0])):
            os.makedirs(d + "/j")
            open(d + "/j/pipbox-persistent.conf", "w").write("alt")
            m.apply("sparsam")
            self.assertEqual(open(d + "/etc/logmode").read(), "sparsam\n")
            self.assertIn("Storage=volatile", open(d + "/j/pipbox-journal.conf").read())
            self.assertFalse(os.path.exists(d + "/j/pipbox-persistent.conf"))
            self.assertIn(["systemctl", "restart", "systemd-journald"], calls)
            m.JOURNAL_LOG_DIR = d + "/varlog"
            m.apply("ausfuehrlich")
            self.assertEqual(open(d + "/etc/logmode").read(), "ausfuehrlich\n")
            self.assertIn("Storage=persistent", open(d + "/j/pipbox-journal.conf").read())
            self.assertIn("SystemMaxUse=30M", open(d + "/j/pipbox-journal.conf").read())
            self.assertTrue(os.path.isdir(d + "/varlog"))
            self.assertIn(["journalctl", "--flush"], calls)
            with self.assertRaises(ValueError):
                m.apply("alles")

    def test_helper_ignores_unknown_keyword(self):
        m = self.helper("pipbox-logmode")
        d = tempfile.mkdtemp()
        req = os.path.join(d, "logmode-request")
        open(req, "w").write("poweroff\n")
        with mock.patch.multiple(m, REQ=req, LOCK=d + "/lock"), mock.patch.object(m, "apply") as ap:
            self.assertEqual(m.main(["x"]), 1)
            ap.assert_not_called()
        self.assertFalse(os.path.exists(req))                 # Anforderung wird trotzdem gelöscht

    def test_health_log_follows_mode(self):
        h = self.helper("pipbox_health")
        with mock.patch.object(h, "rd", return_value="sparsam"):
            self.assertEqual(h.mode(), "sparsam")
        with mock.patch.object(h, "rd", return_value=""):
            self.assertEqual(h.mode(), "ausfuehrlich")         # ohne Datei wie bisher auf der Karte


class HealthCpuText(unittest.TestCase):
    """Zeile des Zustandsprotokolls: Last je Kern und der Thread mit der größten Last seit der vorigen Zeile."""

    def put(self, d, pid, tid, name, ticks, core):
        os.makedirs("%s/%s/task/%s" % (d, pid, tid), exist_ok=True)
        fields = ["S"] + ["0"] * 10 + [str(ticks), "0"] + ["0"] * 23 + [str(core)]
        with open("%s/%s/task/%s/stat" % (d, pid, tid), "w") as f:
            f.write("%s (%s) %s" % (tid, name, " ".join(fields)))

    def test_busiest_thread_and_core_load_between_two_lines(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("pipbox_health", os.path.join(os.path.dirname(HERE), "install", "pipbox_health.py"))
        h = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(h)
        nl = chr(10)
        d = tempfile.mkdtemp()
        with open(d + "/stat", "w") as f:
            f.write("cpu  0 0 0 0 0 0 0 0" + nl + "cpu0 100 0 0 900 0 0 0 0" + nl + "cpu1 100 0 0 900 0 0 0 0" + nl)
        self.put(d, 10, 10, "sbf3_lq:src", 1000, 1)
        self.put(d, 10, 11, "mux:src", 100, 0)
        with mock.patch.object(h, "PROC", d), mock.patch.object(h, "_prev", [None]):
            self.assertEqual(h.cpu_text(), "cores=? hot=?")                                        # die erste Zeile hat keinen Vergleich
            with open(d + "/stat", "w") as f:
                f.write("cpu  0 0 0 0 0 0 0 0" + nl + "cpu0 150 0 0 950 0 0 0 0" + nl + "cpu1 1100 0 0 900 0 0 0 0" + nl)
            self.put(d, 10, 10, "sbf3_lq:src", 1500, 1)
            self.put(d, 10, 11, "mux:src", 101, 0)
            with mock.patch.object(h.time, "monotonic", return_value=h._prev[0][0] + 5.0):
                out = h.cpu_text()
        self.assertEqual(out, "cores=50/100 hot=sbf3_lq:src:100%@1")                           # 500 Takte in 5 s = ein ganzer Kern


class AutoStartTests(unittest.TestCase):
    """Automatisch live gehen nach dem Start der Box: einmal pro Start, nur mit sendender Kamera, abbrechbar."""

    class FakeSend:
        def __init__(self, can_start=True, reasons=None):
            self.can_start, self.reasons, self.active, self.calls = can_start, reasons or [], False, []

        def status(self):
            return {"active": self.active, "can_start": self.can_start and not self.active, "reasons": [] if self.can_start else self.reasons}

        def request(self, action, confirm):
            self.calls.append((action, confirm))
            self.active = True

    def make(self, send, enabled=True, boot_done=None, wait_s=1.0):
        d = tempfile.mkdtemp()
        if enabled or boot_done:
            with open(os.path.join(d, "autostart.json"), "w") as f:
                json.dump({"enabled": enabled, "boot": boot_done or ""}, f)
        a = server.AutoStart(d, send, wait_s=wait_s, poll_s=0.02)
        return a, d

    def run_it(self, a):
        with mock.patch.object(server.AutoStart, "boot_id", staticmethod(lambda: "boot1")):
            a.run()

    def test_starts_once_when_camera_is_there(self):
        s = self.FakeSend()
        a, d = self.make(s)
        self.run_it(a)
        self.assertEqual(s.calls, [("start", True)])
        self.assertEqual(a.status()["phase"], "gestartet")
        self.assertEqual(json.load(open(os.path.join(d, "autostart.json")))["boot"], "boot1")

    def test_waits_for_camera_then_starts(self):
        s = self.FakeSend(can_start=False, reasons=["Keine Kamera sendet gerade"])
        a, _ = self.make(s)
        threading.Timer(0.15, lambda: setattr(s, "can_start", True)).start()
        self.run_it(a)
        self.assertEqual(len(s.calls), 1)

    def test_gives_up_and_names_reason(self):
        s = self.FakeSend(can_start=False, reasons=["Keine Kamera sendet gerade"])
        a, _ = self.make(s, wait_s=0.2)
        self.run_it(a)
        self.assertEqual(s.calls, [])
        self.assertEqual(a.status()["phase"], "aufgegeben")
        self.assertIn("Keine Kamera", a.status()["message"])

    def test_not_twice_in_same_boot(self):
        s = self.FakeSend()
        a, _ = self.make(s, boot_done="boot1")
        self.run_it(a)
        self.assertEqual(s.calls, [])

    def test_new_boot_starts_again(self):
        s = self.FakeSend()
        a, _ = self.make(s, boot_done="boot0")
        self.run_it(a)
        self.assertEqual(len(s.calls), 1)

    def test_off_by_default_and_when_disabled(self):
        s = self.FakeSend()
        a, _ = self.make(s, enabled=False)
        self.run_it(a)
        self.assertEqual(s.calls, [])
        self.assertFalse(a.enabled())

    def test_manual_stop_cancels_waiting(self):
        s = self.FakeSend(can_start=False, reasons=["x"])
        a, d = self.make(s, wait_s=2.0)
        th = threading.Thread(target=self.run_it, args=(a,))
        th.start()
        time.sleep(0.15)
        with mock.patch.object(server.AutoStart, "boot_id", staticmethod(lambda: "boot1")):
            a.cancel("von Hand beendet")
        th.join(3)
        self.assertFalse(th.is_alive())
        self.assertEqual(s.calls, [])
        self.assertEqual(a.status()["phase"], "abgebrochen")
        self.assertEqual(json.load(open(os.path.join(d, "autostart.json")))["boot"], "boot1")

    def test_cancel_when_not_waiting_does_nothing(self):
        a, d = self.make(self.FakeSend(), enabled=False)
        a.cancel("egal")
        self.assertEqual(a.status()["phase"], "aus")
        self.assertFalse(os.path.exists(os.path.join(d, "autostart.json")))

    def test_set_enabled_validates_and_persists(self):
        a, d = self.make(self.FakeSend(), enabled=False)
        for bad in ("true", 1, None, "ja"):
            with self.assertRaises(ValueError, msg=str(bad)):
                a.set_enabled(bad)
        a.set_enabled(True)
        self.assertTrue(json.load(open(os.path.join(d, "autostart.json")))["enabled"])
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, "autostart.json")).st_mode), 0o600)
        self.assertTrue(server.AutoStart(d, self.FakeSend()).enabled())


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

    def test_picture_state_per_camera(self):
        d = tempfile.mkdtemp()
        srt = server.SrtlaStore(os.path.join(d, "srtla.json"))
        sc = server.SendControl(d, srt, store(), mock.Mock(), demo=False)
        fo = {"layout": ["a", "c"], "configured": ["a", "b", "c", "d"], "wait": {"b": 40}}
        with mock.patch.object(sc, "_active", return_value=True), mock.patch.object(sc, "_detail", return_value={"failover": fo}):
            self.assertEqual(sc.picture(), {"a": ("an", 0), "b": ("wartet", 40), "c": ("an", 0), "d": ("aus", 0)})
        with mock.patch.object(sc, "_active", return_value=False):
            self.assertIsNone(sc.picture())
        with mock.patch.object(sc, "_active", return_value=True), mock.patch.object(sc, "_detail", return_value={"state": "running"}):
            self.assertIsNone(sc.picture())              # keine Automatik: keine Aussage


class UplinkLights(unittest.TestCase):
    LINES = ("10:00:01 links: 10.0.0.2 srtt=45ms var=3 peak=60 guete=70 genutzt in_flight=4 pkts_5s=800\n"
             "10:00:01 links: 10.0.1.2 srtt=250ms var=200 peak=800 guete=1000 reserve in_flight=0 pkts_5s=0\n"
             "10:00:06 links: 10.0.0.2 srtt=40ms var=3 peak=60 guete=70 genutzt in_flight=4 pkts_5s=900\n"
             "10:00:06 links: 10.0.1.2 srtt=-1ms var=200 peak=800 guete=-1 reserve in_flight=0 pkts_5s=0\n")

    def run_states(self, selected, age=0, lines=None):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "srtla-links.txt")
        with open(path, "w") as f:
            f.write(self.LINES if lines is None else lines)
        t = time.time() - age
        os.utime(path, (t, t))
        ifs = [{"iface": "eth2", "ip": "10.0.0.2"}, {"iface": "wlan0", "ip": "10.0.1.2"}]
        with mock.patch.object(server, "LINKS_FILE", path), mock.patch.object(server, "iface_ips", lambda: ifs):
            return server.uplink_states(selected)

    def test_used_reserve_and_missing(self):
        r = self.run_states(["eth2", "wlan0", "eth0"])
        self.assertEqual(r["eth2"], {"state": "an", "srtt": 40})
        self.assertEqual(r["wlan0"], {"state": "reserve", "srtt": None})      # -1 ms = keine frische Messung
        self.assertEqual(r["eth0"], {"state": "aus", "srtt": None})          # ausgewählt, aber nicht verbunden

    def test_stale_file_means_no_lights(self):
        self.assertEqual(self.run_states(["eth2"], age=60), {})

    def test_missing_file_means_no_lights(self):
        with mock.patch.object(server, "LINKS_FILE", "/nonexistent/links.txt"):
            self.assertEqual(server.uplink_states(["eth2"]), {})


class SwapMainPip(unittest.TestCase):
    def store(self, cfg):
        d = tempfile.mkdtemp()
        st = server.PipelineStore(os.path.join(d, "pipeline.json"))
        st.cfg.update(cfg)
        return st

    def test_swaps_cameras_and_their_delays_but_not_the_places(self):
        st = self.store({"type": "pip", "main": "cam-a", "pip": "cam-b", "pip2": "cam-c", "corner": 3, "size_pct": 25,
                         "audio": "main", "main_delay_ms": 1500, "pip_delay_ms": 120, "pip2_delay_ms": 250})
        st.swap_main_pip()
        c = st.cfg
        self.assertEqual((c["main"], c["pip"], c["pip2"]), ("cam-b", "cam-a", "cam-c"))
        self.assertEqual((c["main_delay_ms"], c["pip_delay_ms"], c["pip2_delay_ms"]), (120, 1500, 250))
        self.assertEqual((c["corner"], c["size_pct"], c["audio"]), (3, 25, "main"))
        with open(st.path) as f:                     # gespeichert
            self.assertEqual(json.load(f)["main"], "cam-b")
        st.swap_main_pip()                           # zweimal tauschen = wie vorher
        self.assertEqual((st.cfg["main"], st.cfg["pip"], st.cfg["main_delay_ms"]), ("cam-a", "cam-b", 1500))

    def test_swap_with_a_chosen_small_picture(self):
        st = self.store({"type": "pip", "main": "cam-a", "pip": "cam-b", "pip2": "cam-c", "pip3": "cam-d", "corner2": 2,
                         "main_delay_ms": 1500, "pip_delay_ms": 120, "pip2_delay_ms": 250, "pip3_delay_ms": 300})
        st.swap_main_pip("cam-c")
        c = st.cfg
        self.assertEqual((c["main"], c["pip"], c["pip2"], c["pip3"]), ("cam-c", "cam-b", "cam-a", "cam-d"))
        self.assertEqual((c["main_delay_ms"], c["pip_delay_ms"], c["pip2_delay_ms"], c["pip3_delay_ms"]), (250, 120, 1500, 300))
        self.assertEqual(c["corner2"], 2)                                 # Ecke bleibt am Platz
        st.swap_main_pip("cam-d")
        self.assertEqual((st.cfg["main"], st.cfg["pip3"]), ("cam-d", "cam-c"))
        with self.assertRaises(ValueError):                               # Kamera, die nicht als kleines Bild im Bild ist
            st.swap_main_pip("cam-x")
        with self.assertRaises(ValueError):                               # die Hauptkamera selbst ist kein kleines Bild
            st.swap_main_pip(st.cfg["main"])

    def test_refuses_without_small_picture(self):
        for cfg in ({"type": "single", "main": "cam-a", "pip": ""}, {"type": "pip", "main": "cam-a", "pip": ""}):
            st = self.store(cfg)
            with self.assertRaises(ValueError):
                st.swap_main_pip()
            self.assertEqual(st.cfg["main"], "cam-a")


class SeamlessSwap(unittest.TestCase):
    """Tausch ohne Neustart: Pipeline-Text, Umschaltzeile, Verzögerungsreihenfolge und Übergabe an die laufende Sendekette."""
    CFG = dict(server.PipelineStore.DEFAULT, type="pip", main="cam-a", pip="cam-b", pip2="cam-c", pip3="cam-d", corner=4, corner2=2, corner3=3,
               main_delay_ms=100, pip_delay_ms=300, pip2_delay_ms=50, pip3_delay_ms=0, swap_cams=2)

    def build(self, **kw):
        return server.PipelineStore(os.devnull).build(dict(self.CFG, **kw))

    def test_off_by_default_and_with_old_plugin(self):
        self.assertNotIn("pbpipsel", self.build(swap_cams=0))
        with mock.patch.object(server, "plugin_swap", lambda: False):
            self.assertNotIn("pbpipsel", self.build())
        self.assertNotIn("pbpipsel", self.build(type="single"))

    def test_two_swappable_cameras_decode_six_times(self):
        t = self.build()
        self.assertEqual(t.count("mppvideodec"), 6)          # 2 groß + 4 klein
        self.assertEqual(t.count("tee name="), 2)
        self.assertEqual(t.count("pbpipsel"), 2)             # Bild und Ton
        self.assertEqual(t.count("opusenc"), 1)
        self.assertEqual(t.count("pbpipsink"), 4)
        for ring in range(4):
            self.assertEqual(t.count(f"pbpipsink slot={ring}"), 1)
        self.assertIn("vsel.sink_0", t)
        self.assertIn("vsel.sink_1", t)
        self.assertNotIn("vsel.sink_2", t)
        self.assertIn("follow-tag=true", t)
        self.assertIn("pbctl name=pbctl selector=vsel audio-selector=asel audio-pos=-1 cam0=vfq0:v,vsq0:s,aq0:a cam1=vfq1:v,vsq1:s,aq1:a cam2=vsq2:s cam3=vsq3:s", t)

    def test_four_swappable_cameras_decode_eight_times(self):
        t = self.build(swap_cams=4)
        self.assertEqual(t.count("mppvideodec"), 8)
        for i in range(4):
            self.assertIn(f"vsel.sink_{i}", t)
            self.assertIn(f"asel.sink_{i}", t)

    def test_group_is_limited_by_the_cameras_present(self):
        t = self.build(swap_cams=4, pip3="")
        self.assertEqual(t.count("tee name="), 3)
        self.assertEqual(self.build(swap_cams=4, pip2="", pip3="").count("tee name="), 2)

    def test_every_queue_named_for_the_control_exists(self):
        t = self.build(swap_cams=4)
        ctl = next(l for l in t.splitlines() if l.startswith("pbctl"))
        for part in ctl.split():
            if part.startswith("cam"):
                for item in part.split("=", 1)[1].split(","):
                    self.assertIn(f"name={item.split(':')[0]} ", t + " ")
        self.assertEqual(t.count("name=a_delay"), 1)
        self.assertEqual(t.count("name=v_delay"), 1)

    def test_delay_belongs_to_the_camera_in_all_its_queues(self):
        t = self.build()
        self.assertIn("name=vfq0 min-threshold-time=133000000", t)     # 100 ms + ein Bild
        self.assertIn("name=aq0 min-threshold-time=100000000", t)
        self.assertIn("name=vfq1 min-threshold-time=333000000", t)
        self.assertIn("queue name=vsq1 max-size-time=833000000 max-size-buffers=0 leaky=downstream min-threshold-time=333000000", t)

    def test_audio_follows_the_picture_only_for_swappable_cameras(self):
        t = self.build(audio="pip")                         # Ton vom ersten kleinen Bild: läuft über den Umschalter
        self.assertIn("audio-pos=0", t)
        self.assertIn("pbpipsel name=asel state=1", t)
        t = self.build(audio="pip2")                        # Kamera 3 ist nicht in der Gruppe: fester Ton, kein Umschalter
        self.assertNotIn("asel", t)
        self.assertEqual(t.count("opusenc"), 1)
        self.assertEqual(t.count("fakesink"), 3)
        t = self.build(audio="pip2", swap_cams=4)
        self.assertIn("audio-pos=1", t)
        self.assertIn("pbpipsel name=asel state=2", t)

    def test_plan_and_initial_state(self):
        plan = server.PipelineStore.swap_plan(self.CFG)
        self.assertEqual((plan["cams"], plan["group"], plan["line"]), (KEYS, 2, "0 1 2 3"))
        self.assertEqual(plan["state"], 0 | 1 << 4 | 2 << 8 | 3 << 12)
        plan = server.PipelineStore.swap_plan(dict(self.CFG, pip3="", swap_cams=4))
        self.assertEqual((plan["group"], plan["line"]), (3, "0 1 2 15"))
        self.assertIsNone(server.PipelineStore.swap_plan(dict(self.CFG, swap_cams=0)))
        self.assertIsNone(server.PipelineStore.swap_plan(dict(self.CFG, pip="")))

    def test_swap_line(self):
        line = server.PipelineStore.swap_line
        self.assertEqual(line(["cam-a", "cam-b", "cam-c", "cam-d"], KEYS, 2), "0 1 2 3")
        self.assertEqual(line(["cam-b", "cam-a", "cam-c", "cam-d"], KEYS, 2), "1 0 2 3")
        self.assertIsNone(line(["cam-c", "cam-b", "cam-a", "cam-d"], KEYS, 2))          # Kamera 3 ist nicht in der Gruppe
        self.assertEqual(line(["cam-c", "cam-b", "cam-a", "cam-d"], KEYS, 4), "2 1 0 3")
        self.assertEqual(line(["cam-b", "cam-a", "cam-c", ""], KEYS[:3], 2), "1 0 2 15")
        self.assertIsNone(line(["cam-b", "cam-a", "cam-c", "cam-d"], KEYS[:3], 2))      # andere Kameras als beim Aufbau
        self.assertIsNone(line(["cam-x", "cam-b", "cam-c", "cam-d"], KEYS, 2))
        self.assertIsNone(line(["cam-a", "cam-b"], ["cam-a"], 1))

    def test_delay_file_order_stays_with_the_cameras(self):
        cfg = dict(self.CFG)
        self.assertEqual(server.PipelineStore.delay_values(cfg), [100, 300, 50, 0])
        st = server.PipelineStore(os.path.join(tempfile.mkdtemp(), "p.json"))
        st.cfg.update(cfg)
        st.swap_main_pip()                                   # cfg: b ist Hauptbild und trägt seine 300 ms mit
        self.assertEqual((st.cfg["main"], st.cfg["main_delay_ms"], st.cfg["pip_delay_ms"]), ("cam-b", 300, 100))
        self.assertEqual(server.PipelineStore.delay_values(st.cfg, KEYS), [100, 300, 50, 0])   # Reihenfolge des Aufbaus
        self.assertEqual(server.PipelineStore.delay_values(st.cfg), [300, 100, 50, 0])         # ohne Aufbau: Reihenfolge der Einstellung
        self.assertEqual(server.PipelineStore.delay_values(dict(cfg, type="single")), [0, 0, 0, 0])

    def test_setting_is_validated(self):
        s = store()
        s.set(dict(BASE, swap_cams=4), KEYS)
        self.assertEqual(s.cfg["swap_cams"], 4)
        for bad in (3, 1, "x", -2):
            with self.assertRaises(ValueError):
                store().set(dict(BASE, swap_cams=bad), KEYS)
        s.set(dict(BASE), KEYS)
        self.assertEqual(s.cfg["swap_cams"], 0)
        self.assertEqual(server.PipelineStore._safe_cfg(dict(BASE, swap_cams=3))["swap_cams"], 0)

    def control(self, cams_state=None, swap=None):
        d = tempfile.mkdtemp()
        st = server.PipelineStore(os.path.join(d, "pipeline.json"))
        st.cfg.update(self.CFG)
        cams = mock.Mock()
        cams.listing.return_value = [{"key": k, "state": (cams_state or {}).get(k, "live")} for k in KEYS]
        sc = server.SendControl(d, mock.Mock(), st, cams)
        sc.SWAP_WAIT = 1.0
        sc._active = lambda: True
        sc._detail = lambda: {"swap": swap if swap is not None else {"cams": KEYS, "group": 2}, "failover": {"degraded": False}}
        return d, st, sc

    def answer(self, d, state_path, delay=0.2):
        """Spielt den Baustein: liest die Umschaltdatei und meldet den Zustand zurück."""
        def run():
            time.sleep(delay)
            with open(os.path.join(d, server.SWAP_SELECT)) as f:
                txt = f.read()
            with open(state_path, "w") as f:
                f.write(txt)
        th = threading.Thread(target=run)
        th.start()
        return th

    def test_live_swap_writes_the_line_and_waits_for_the_report(self):
        d, st, sc = self.control()
        state = os.path.join(d, "swap-state")
        with mock.patch.object(server, "SWAP_STATE", state):
            st.swap_main_pip()
            th = self.answer(d, state)
            self.assertTrue(sc.swap_live())
            th.join()
        with open(os.path.join(d, server.SWAP_SELECT)) as f:
            self.assertEqual(f.read().strip(), "1 0 2 3")

    def test_live_swap_without_report_falls_back_to_restart(self):
        d, st, sc = self.control()
        with mock.patch.object(server, "SWAP_STATE", os.path.join(d, "never")):
            st.swap_main_pip()
            self.assertFalse(sc.swap_live())

    def test_stale_report_is_not_taken_for_an_answer(self):
        d, st, sc = self.control()
        state = os.path.join(d, "swap-state")
        with open(state, "w") as f:
            f.write("1 0 2 3\n")
        os.utime(state, (time.time() - 60, time.time() - 60))
        with mock.patch.object(server, "SWAP_STATE", state):
            st.swap_main_pip()
            self.assertFalse(sc.swap_live())

    def test_live_swap_refused_when_it_cannot_work(self):
        for kw in ({"swap": {"cams": KEYS, "group": 2}, "cams_state": {"cam-b": "off"}},   # neue Hauptkamera sendet nicht
                   {"swap": {"cams": KEYS[:3], "group": 2}},                                # Anordnung weicht vom Aufbau ab
                   {"swap": None}):
            d, st, sc = self.control(**kw)
            if kw.get("swap") is None:
                sc._detail = lambda: {}
            st.swap_main_pip()
            self.assertFalse(sc.swap_live(), kw)
            self.assertFalse(os.path.exists(os.path.join(d, server.SWAP_SELECT)), kw)
        d, st, sc = self.control()
        st.swap_main_pip("cam-c")                                                            # Kamera 3 gehört nicht zur Gruppe
        self.assertFalse(sc.swap_live())


class AuthModes(unittest.TestCase):
    """Anmeldung: auf einer BELABOX gilt deren Passwort; ohne Passwort dort wartet die Oberfläche (kein Setup-Code)."""

    def bela(self, config=None):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "belaUI"))
        path = os.path.join(d, "belaUI", "config.json")
        if config is not None:
            with open(path, "w") as f:
                json.dump(config, f)
        return d, path

    def test_fresh_belabox_waits_for_its_password_and_has_no_setup_code(self):
        d, cfg = self.bela({"asrc": "x"})                               # belaUI ist da, aber ohne Passwort
        a = server.Auth(os.path.join(d, "state"), cfg)
        self.assertEqual((a.mode, a.configured, a.setup_code), ("belabox-wartet", False, None))
        self.assertFalse(os.path.exists(os.path.join(d, "state", "setup-code")))
        with self.assertRaises(ValueError) as e:
            a.login("irgendwas", "127.0.0.1")
        self.assertIn("BELABOX", str(e.exception))
        with self.assertRaises(ValueError) as e:
            a.set_password("abc", "ein-langes-passwort", "127.0.0.1")
        self.assertIn("BELABOX", str(e.exception))
        self.assertFalse(os.path.exists(os.path.join(d, "state", "auth.json")))     # nichts Eigenes angelegt

    def test_missing_config_file_of_an_installed_belaui_also_waits(self):
        d, cfg = self.bela(None)
        a = server.Auth(os.path.join(d, "state"), cfg)
        self.assertEqual((a.mode, a.setup_code), ("belabox-wartet", None))

    def test_page_switches_when_the_belabox_password_appears(self):
        d, cfg = self.bela({"asrc": "x"})
        a = server.Auth(os.path.join(d, "state"), cfg)
        self.assertEqual(a.mode, "belabox-wartet")
        with open(cfg, "w") as f:
            json.dump({"asrc": "x", "password_hash": "$2b$10$abcdefghijklmnopqrstuuabcdefghijklmnopqrstuvwxyz01234"}, f)
        self.assertEqual((a.mode, a.configured), ("belabox", True))

    def test_belabox_password_is_used_when_present(self):
        d, cfg = self.bela({"password_hash": "$2b$10$abcdefghijklmnopqrstuuabcdefghijklmnopqrstuvwxyz01234"})
        a = server.Auth(os.path.join(d, "state"), cfg)
        self.assertEqual((a.mode, a.configured, a.setup_code), ("belabox", True, None))
        with mock.patch.object(a, "bela_ok", lambda pw, h: pw == "richtig"):
            self.assertTrue(a.login("richtig", "127.0.0.1"))
            with self.assertRaises(ValueError):
                a.login("falsch", "127.0.0.1")

    def test_without_belaui_the_own_password_with_setup_code_still_works(self):
        d = tempfile.mkdtemp()
        a = server.Auth(os.path.join(d, "state"), os.path.join(d, "keine-belaui", "config.json"))
        self.assertEqual(a.mode, "own")
        self.assertTrue(a.setup_code)
        code = a.setup_code
        with self.assertRaises(ValueError):
            a.set_password("falsch", "ein-langes-passwort", "127.0.0.1")
        a.set_password(code, "ein-langes-passwort", "127.0.0.1")
        self.assertTrue(a.configured)
        self.assertFalse(os.path.exists(os.path.join(d, "state", "setup-code")))
        self.assertTrue(a.login("ein-langes-passwort", "127.0.0.1"))

    def test_own_password_from_an_earlier_version_stays_valid_on_a_belabox_without_password(self):
        d = tempfile.mkdtemp()
        a0 = server.Auth(os.path.join(d, "state"), None)                 # früher: eigenes Passwort gesetzt
        a0.set_password(a0.setup_code, "ein-langes-passwort", "127.0.0.1")
        bd, cfg = self.bela({"asrc": "x"})
        a = server.Auth(os.path.join(d, "state"), cfg)
        self.assertEqual((a.mode, a.configured), ("own", True))
        self.assertTrue(a.login("ein-langes-passwort", "127.0.0.1"))

    def test_stale_setup_code_file_is_removed_on_a_belabox(self):
        d, cfg = self.bela({"asrc": "x"})
        os.makedirs(os.path.join(d, "state"))
        with open(os.path.join(d, "state", "setup-code"), "w") as f:
            f.write("alt\n")
        server.Auth(os.path.join(d, "state"), cfg)
        self.assertFalse(os.path.exists(os.path.join(d, "state", "setup-code")))

    def test_demo_has_the_password_from_the_start_and_no_setup_code(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "state"))
        with open(os.path.join(d, "state", "setup-code"), "w") as f:
            f.write("alt\n")
        a = server.Auth(os.path.join(d, "state"), None, demo=True)
        self.assertEqual((a.mode, a.configured, a.setup_code), ("demo", True, None))
        self.assertFalse(os.path.exists(os.path.join(d, "state", "setup-code")))
        self.assertFalse(os.path.exists(os.path.join(d, "state", "auth.json")))      # nichts auf der Platte
        self.assertTrue(a.login(server.Auth.DEMO_PASSWORD, "127.0.0.1"))
        with self.assertRaises(ValueError):
            a.login("falsch", "127.0.0.1")
        with self.assertRaises(ValueError):
            a.set_password("x", "ein-langes-passwort", "127.0.0.1")

    def test_demo_never_replaces_the_belabox_password(self):
        d, cfg = self.bela({"password_hash": "$2b$10$abcdefghijklmnopqrstuuabcdefghijklmnopqrstuvwxyz01234"})
        a = server.Auth(os.path.join(d, "state"), cfg, demo=True)
        self.assertEqual(a.mode, "belabox")
        with mock.patch.object(a, "bela_ok", lambda pw, h: pw == "richtig"):
            with self.assertRaises(ValueError):
                a.login(server.Auth.DEMO_PASSWORD, "127.0.0.1")

    def test_without_demo_there_is_no_preset_password(self):
        d = tempfile.mkdtemp()
        a = server.Auth(os.path.join(d, "state"), None)
        self.assertEqual(a.mode, "own")
        with self.assertRaises(ValueError):
            a.login(server.Auth.DEMO_PASSWORD, "127.0.0.1")

    def test_remembered_session_survives_a_restart_but_a_normal_one_does_not(self):
        d = tempfile.mkdtemp()
        st = os.path.join(d, "state")
        a = server.Auth(st, None, demo=True)
        t_rem = a.login(server.Auth.DEMO_PASSWORD, "127.0.0.1", True)
        t_norm = a.login(server.Auth.DEMO_PASSWORD, "127.0.0.1")
        self.assertTrue(a.valid(t_rem) and a.valid(t_norm))
        b = server.Auth(st, None, demo=True)                       # Neustart der Oberfläche (z. B. nach einem Update)
        self.assertTrue(b.valid(t_rem))
        self.assertFalse(b.valid(t_norm))
        self.assertFalse(b.valid("") or b.valid("irgendwas"))

    def test_remembered_sessions_are_stored_hashed_and_private(self):
        d = tempfile.mkdtemp()
        st = os.path.join(d, "state")
        a = server.Auth(st, None, demo=True)
        tok = a.login(server.Auth.DEMO_PASSWORD, "127.0.0.1", True)
        path = os.path.join(st, "sessions.json")
        with open(path) as f:
            raw = f.read()
        self.assertNotIn(tok, raw)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        a.login(server.Auth.DEMO_PASSWORD, "127.0.0.1")
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_logout_ends_a_remembered_session(self):
        d = tempfile.mkdtemp()
        st = os.path.join(d, "state")
        a = server.Auth(st, None, demo=True)
        tok = a.login(server.Auth.DEMO_PASSWORD, "127.0.0.1", True)
        a.logout(tok)
        self.assertFalse(a.valid(tok))
        self.assertFalse(server.Auth(st, None, demo=True).valid(tok))

    def test_remembered_session_expires_after_30_days(self):
        d = tempfile.mkdtemp()
        st = os.path.join(d, "state")
        a = server.Auth(st, None, demo=True)
        now = time.time()
        with mock.patch.object(server.time, "time", lambda: now):
            tok = a.login(server.Auth.DEMO_PASSWORD, "127.0.0.1", True)
        b = server.Auth(st, None, demo=True)
        with mock.patch.object(server.time, "time", lambda: now + server.REMEMBER_SECONDS - 60):
            self.assertTrue(b.valid(tok))
        with mock.patch.object(server.time, "time", lambda: now + server.REMEMBER_SECONDS + 60):
            self.assertFalse(b.valid(tok))

    def test_remembered_sessions_end_when_the_belabox_password_changes(self):
        d, cfg = self.bela({"password_hash": "$2b$10$abcdefghijklmnopqrstuuabcdefghijklmnopqrstuvwxyz01234"})
        a = server.Auth(os.path.join(d, "state"), cfg)
        with mock.patch.object(a, "bela_ok", lambda pw, h: True):
            tok = a.login("egal", "127.0.0.1", True)
        self.assertTrue(server.Auth(os.path.join(d, "state"), cfg).valid(tok))
        with open(cfg, "w") as f:
            json.dump({"password_hash": "$2b$10$zzzzzzzzzzzzzzzzzzzzzzabcdefghijklmnopqrstuvwxyz01234"}, f)
        self.assertFalse(server.Auth(os.path.join(d, "state"), cfg).valid(tok))

    def test_remember_flag_must_be_exactly_true_in_the_request(self):
        import inspect
        src = inspect.getsource(server.Handler.do_POST)
        self.assertIn('d.get("remember") is True', src)


class CameraOrder(unittest.TestCase):
    """Reihenfolge der Kameras in der Liste, der die Kamera-Knöpfe der Fußleiste folgen (Issue #32)."""

    def store(self):
        d = tempfile.mkdtemp()
        c = server.CameraStore(os.path.join(d, "cameras.json"), "publish", "", False)
        c.live_streams = lambda: {}
        return d, c

    def keys(self, c):
        return [x["key"] for x in c.listing("")]

    def test_swap_changes_the_order_and_survives_a_restart(self):
        d, c = self.store()
        a, b, x = c.add("A", "ka", "extra"), c.add("B", "kb", "extra"), c.add("C", "kc", "extra")
        c.swap(b["id"], a["id"])
        self.assertEqual(self.keys(c), ["kb", "ka", "kc"])
        c.swap(a["id"], x["id"])
        self.assertEqual(self.keys(c), ["kb", "kc", "ka"])
        again = server.CameraStore(os.path.join(d, "cameras.json"), "publish", "", False)
        self.assertEqual([k["key"] for k in again.cams], ["kb", "kc", "ka"])

    def test_swap_keeps_names_roles_and_keys_of_the_cameras(self):
        d, c = self.store()
        a, b = c.add("Haupt", "ka", "main"), c.add("Klein", "kb", "pip")
        c.swap(a["id"], b["id"])
        by = {x["key"]: (x["name"], x["role"]) for x in c.cams}
        self.assertEqual(by, {"ka": ("Haupt", "main"), "kb": ("Klein", "pip")})

    def test_swap_with_itself_changes_nothing_and_unknown_ids_fail(self):
        d, c = self.store()
        a = c.add("A", "ka", "extra")
        c.add("B", "kb", "extra")
        c.swap(a["id"], a["id"])
        self.assertEqual(self.keys(c), ["ka", "kb"])
        with self.assertRaises(KeyError):
            c.swap(a["id"], "00000000")
        with self.assertRaises(KeyError):
            c.swap("00000000", a["id"])
        self.assertEqual(self.keys(c), ["ka", "kb"])


class CameraConnection(unittest.TestCase):
    """Die Adresse jeder Kamera in der Liste gilt für ihre Verbindung (DJI-Karte, eigene Wahl oder Hauptverbindung)."""
    IFACES = [{"iface": "eth0", "ip": "192.168.1.20", "cam_ip": "192.168.1.20", "label": "LAN"},
              {"iface": "eth1", "ip": "192.168.5.9", "cam_ip": "192.168.80.50", "label": "Kameranetz"},
              {"iface": "eth2", "ip": "192.168.80.5", "cam_ip": "192.168.80.5", "label": "Router"}]

    def store(self):
        d = tempfile.mkdtemp()
        c = server.CameraStore(os.path.join(d, "cameras.json"), "publish", "", False)
        c.ipfn = lambda: "192.168.1.20"                       # Hauptverbindung
        c.ifaces = lambda: self.IFACES
        c.live_streams = lambda: {}
        return d, c

    def test_default_is_the_main_connection(self):
        d, c = self.store()
        c.add("Handy", "handy", "extra")
        row = c.listing("")[0]
        self.assertEqual((row["url"], row["via_src"], row["via"]), ("rtmp://192.168.1.20:1935/publish/handy", "main", None))

    def test_own_connection_changes_the_shown_address(self):
        d, c = self.store()
        cam = c.add("Handy", "handy", "extra")
        c.update(cam["id"], iface="eth2")
        row = c.listing("")[0]
        self.assertEqual((row["url"], row["via_src"], row["via"]), ("rtmp://192.168.80.5:1935/publish/handy", "own", "eth2"))
        c.update(cam["id"], iface="eth1")                                     # feste Zweitadresse zählt, wie bei der Hauptverbindung
        self.assertIn("192.168.80.50", c.listing("")[0]["url"])
        c.update(cam["id"], iface="")                                         # zurück zur Hauptverbindung
        self.assertEqual(c.listing("")[0]["via_src"], "main")
        self.assertNotIn("iface", c.cams[0])

    def test_unknown_connection_is_refused_and_a_vanished_one_falls_back(self):
        d, c = self.store()
        cam = c.add("Handy", "handy", "extra")
        with self.assertRaises(ValueError):
            c.update(cam["id"], iface="eth9")
        with self.assertRaises(ValueError):
            c.update(cam["id"], iface=["eth0"])
        c.update(cam["id"], iface="eth2")
        c.ifaces = lambda: self.IFACES[:2]                                    # der Router ist weg
        row = c.listing("")[0]
        self.assertEqual((row["via_src"], row["url"]), ("main", "rtmp://192.168.1.20:1935/publish/handy"))
        self.assertEqual(c.cams[0]["iface"], "eth2")                         # gewählt bleibt gewählt, bis der Router wiederkommt

    def test_dji_camera_uses_the_connection_of_its_card(self):
        d, c = self.store()
        c.add("Action 4", "dji-f04fe2", "extra")
        c.add("Handy", "handy", "extra")
        svc = server.DjiService(d, c, "publish", 1935)
        with open(os.path.join(d, "dji-cameras.json"), "w") as f:
            json.dump({"cameras": {"58:B8:58:F0:4F:E2": {"rtmp_key": "dji-f04fe2", "wifi_ifname": "eth2"}}}, f)
        with mock.patch.object(server, "iface_ips", lambda: self.IFACES):
            rows = {r["key"]: r for r in c.listing("", svc.host_for_key)}
        self.assertEqual((rows["dji-f04fe2"]["url"], rows["dji-f04fe2"]["via_src"], rows["dji-f04fe2"]["via"]),
                         ("rtmp://192.168.80.5:1935/publish/dji-f04fe2", "dji", "eth2"))
        self.assertEqual(rows["handy"]["via_src"], "main")                    # andere Kameras bleiben unberührt

    def test_dji_manual_connection_and_none_chosen(self):
        d, c = self.store()
        svc = server.DjiService(d, c, "publish", 1935)
        with open(os.path.join(d, "dji-cameras.json"), "w") as f:
            json.dump({"cameras": {"A": {"rtmp_key": "dji-000001", "wifi_ifname": "manual", "ip": "10.7.7.7"},
                                   "B": {"rtmp_key": "dji-000002", "wifi_ifname": ""},
                                   "C": {"rtmp_key": "dji-000003", "wifi_ifname": "eth9"}}}, f)
        with mock.patch.object(server, "iface_ips", lambda: self.IFACES):
            self.assertEqual(svc.host_for_key("dji-000001"), ("10.7.7.7", "manual"))
            self.assertIsNone(svc.host_for_key("dji-000002"))                 # nichts gewählt: Hauptverbindung
            self.assertIsNone(svc.host_for_key("dji-000003"))                 # Verbindung gerade nicht da
            self.assertIsNone(svc.host_for_key("handy"))
            self.assertIsNone(svc.host_for_key("dji-ffffff"))

    def test_the_connection_of_a_dji_camera_is_not_set_in_the_list(self):
        src = open(os.path.join(server.os.path.dirname(os.path.abspath(server.__file__)), "server.py"), encoding="utf-8").read()
        self.assertIn("wird in ihrer DJI-Karte gewählt", src)


class HeaderControls(unittest.TestCase):
    """Kopfleiste: Live-Knopf (Zustand der Sendung, Start und Beenden mit Rückfrage); Abmelden steht in der Karte "Box ausschalten und abmelden"."""

    @classmethod
    def setUpClass(cls):
        cls.html = open(os.path.join(server.WEB_DIR, "index.html"), encoding="utf-8").read()
        cls.header = cls.html[cls.html.index("<header>"):cls.html.index("</header>")]

    def test_live_button_is_in_the_header_and_uses_the_existing_send_api(self):
        self.assertIn('id="hdr_live"', self.header)
        self.assertIn("function updHdrLive", self.html)
        self.assertIn("updHdrLive(d);", self.html)                                # wird bei jeder Abfrage des Sendezustands nachgeführt
        self.assertNotIn("Die Sendung jetzt beenden?", self.html)                 # Beenden ohne Rückfrage (Issue #19, Antwort des Melders: Ja)
        self.assertIn("if(sendData&&sendData.active) await doLiveStop(); else await doLiveStart();", self.html)
        self.assertEqual(self.html.count('{action:"start",confirm:true}'), 1)     # ein gemeinsamer Weg zum Starten (Live-Karte und Kopfleiste)
        self.assertEqual(self.html.count('"/api/send",{action:"stop"}'), 1)       # ein gemeinsamer Weg zum Beenden

    def test_update_hints_are_buttons_in_the_right_group_of_the_header(self):
        """Die gelben Update-Hinweise (Oberfläche, System) stehen als gleichartige Knöpfe in der Gruppe rechts, nicht mehr als Punkte am Titel."""
        group = self.header[self.header.index('class="hdr"'):]
        for i in ("hdr_upd", "hdr_sys"):
            self.assertIn('<button type="button" id="%s" class="updbtn" hidden' % i, group)
            self.assertNotIn('id="%s"' % i, self.header[:self.header.index('class="hdr"')])
        self.assertNotIn("updlink", self.html)

    def test_status_cameras_show_the_battery_in_its_own_centered_column_with_a_heading(self):
        # "Akku" steht in derselben Zeile und Schrift wie "Kameras" (beides class="sech"), mittig über seiner Spalte
        self.assertIn("""'<div class="hrow"><div class="sech">Kameras</div>'+(any&&list.length?'<div class="sech mid">Akku</div><div class="sech">&nbsp;</div>':"")""", self.html)
        self.assertIn('<div id="camlights"><div class="sech">Kameras</div></div>', self.html)    # die Überschrift steht im Raster, nicht davor
        self.assertIn("#camlights .hrow .mid{text-align:center", self.html)
        self.assertIn("#camlights .battcell{text-align:center", self.html)
        # feste Breiten der beiden rechten Spalten: Akku und Zahlen rutschen nicht hin und her, wenn sich Werte ändern
        self.assertIn("grid-template-columns:minmax(0,1fr) 4.3em 5.4em", self.html)
        self.assertIn("font-variant-numeric:tabular-nums", self.html[self.html.index("#camlights.hasbatt .row>span:last-child"):][:200])
        self.assertIn("text-overflow:ellipsis", self.html[self.html.index("#camlights .row .nm"):][:200])    # langer Name wird gekürzt
        self.assertIn("const any=list.some(c=>c.battery!=null)", self.html)               # ohne Akkustand keine Spalte

    def test_saved_wlan_networks_can_be_picked_and_connected_without_a_password(self):
        """Issue #8: Gespeicherte Netze sind anklickbar, in der Netzliste markiert, das Passwortfeld sagt, dass das gespeicherte gilt."""
        h = self.html
        self.assertIn('<a href="#" data-ssid="${esc(s)}">${esc(s)}</a> <a href="#" data-forget="${esc(s)}"', h)
        self.assertIn('(d.saved||[]).includes(n.ssid)?" · gespeichert":""', h)
        self.assertIn("leer = gespeichertes Passwort verwenden", h)
        self.assertIn('$("w_ssid").addEventListener("input",wifiPwHint)', h)
        self.assertIn("[6000,14000].forEach(ms=>setTimeout(wifiLoad,ms))", h)           # Anzeige nach dem Verbinden noch zweimal auffrischen

    def test_camera_row_has_the_connection_as_a_column_before_the_signal(self):
        """Issue #10: Name | Verbindung | Signal | Entfernen in einer Zeile (spart die Zeile darunter)."""
        h = self.html
        row = h[h.index('`<div class="cam" data-cid'):][:1400]
        order = [row.index(x) for x in ('class="cl"', '<span class="cn">${camNetHtml(c)}</span>', 'class="muted cr"><span class="dyn">')]
        self.assertEqual(order, sorted(order))
        self.assertLess(row.index("camNetHtml(c)"), row.index('<span class="dyn">'))           # Verbindung vor dem Signal
        self.assertNotIn("</div><code>${esc(c.url)}</code>${camNetHtml(c)}</div>", h)          # nicht mehr als eigene Zeile unter der Adresse
        self.assertIn("min-width:9.6em", h[h.index(".cam .top .dyn{"):][:200])               # Signal: feste Breite, gleich breite Ziffern
        self.assertIn("tabular-nums", h[h.index(".cam .top .dyn{"):][:200])

    def test_devices_can_be_renamed_in_the_wlan_and_bluetooth_lists(self):
        """Issue #11: Der Stick kennt seinen Handelsnamen oft nicht: man kann ihn selbst vergeben ("umbenennen"), er gilt überall."""
        h = self.html
        self.assertIn("function devRename(", h)
        self.assertIn('"/api/devname"', h)
        self.assertEqual(h.count("${dnLink("), 4)                       # WLAN-Übersicht (devInfo: dreimal) und Bluetooth (Adapter und Problem-Sticks) tragen den Link
        self.assertIn("a.label||a.name", h)                              # Bluetooth-Adapter: eigener Name vor dem gemeldeten

    def test_bildaufbau_preview_is_square_and_shows_what_is_sent(self):
        """Issue #7: Hauptbild und kleine Bilder in der Vorschau nicht abgerundet, ohne eigenen Rahmen und Schatten."""
        h = self.html
        pvbox = h[h.index(".pvbox{"):][:400].split("}")[0]
        pvpip = h[h.index(".pvpip{"):][:400].split("}")[0]
        self.assertNotIn("border-radius", pvbox)
        for bad in ("border-radius", "border:", "box-shadow"):
            self.assertNotIn(bad, pvpip)
        # Rundung und Rahmen kommen nur aus der Einstellung des Bildes (Rahmen innen, Rundung begrenzt)
        self.assertIn("border-radius:${rad}px", h)
        self.assertIn("class=\"pvrim\"", h)

    def test_each_small_picture_has_an_appearance_block_with_visibility_crop_corners_and_border(self):
        h = self.html
        for k in (1, 2, 3):
            self.assertIn('<details class="pvs" data-k="%d"></details>' % k, h)
        block = h[h.index("function styleBlockHtml"):][:2800]
        for f in ('data-f="vis"', '"rd"', '"cl"', '"cr"', '"ct"', '"cb"', 'data-f="be"', '"bw"', 'data-f="bc"', '"bo"'):
            self.assertIn(f, block)
        # Issue #7: kein Deckkraft-Regler für das Bild (sichtbar oder ausgeblendet); die Rundung ist ein eigenes Feld, nicht Teil des Rahmens
        self.assertNotIn('data-f="op"', block)
        self.assertNotIn('"br"', block)
        self.assertIn("pipStyles[k].opacity=100", h)                           # ältere gespeicherte Deckkraft wird nicht mehr angewendet
        self.assertIn("rad=st.radius?", h)                                      # Vorschau: Rundung auch ohne Rahmen
        self.assertIn("styles:pipStyles", h)                                   # wird mit dem Bildaufbau gespeichert
        self.assertIn("CROP_KEEP=32", h)                                       # wie auf dem Server: mindestens 32 Pixel bleiben
        self.assertIn("Math.floor(clampN(el.value,0,total-CROP_KEEP,0)/2)*2", h)   # gerade Werte
        self.assertIn('document.querySelectorAll(".pvs").forEach(e=>e.hidden=d.plugin_present&&d.plugin_style===false)', h)   # mit altem Baustein bleiben die Felder weg
        self.assertNotIn("pvstylenote", h)

    def test_each_small_picture_has_its_own_size_field(self):
        """Issue #7: Die Größe steht in jedem Block "Kleines Bild 1 bis 3"; das gemeinsame Feld oben entfällt."""
        h = self.html
        self.assertNotIn('id="p_size"', h)
        for k in (1, 2, 3):
            self.assertIn('id="p_size%d" type="number" min="1" max="100"' % k, h)                      # Skalierung 1 bis 100 Prozent
            self.assertIn("Skalierung (%)", h)
        self.assertIn("size_pct2:+$(\"p_size2\").value,size_pct3:+$(\"p_size3\").value", h.replace("\\", ""))
        self.assertIn('const size=Math.max(1,Math.min(100,+$("p_size"+k).value||25))', h)             # Vorschau: Größe je Bild
        self.assertNotIn("(% der Breite)", h[h.index('id="p_size1"') - 300:h.index('id="p_size1"') + 100])    # Text "Skalierung", nicht "Breite"

    def test_saving_reports_when_the_restarted_transmission_runs_again(self):
        """Issue #7: Nach "Die Übertragung wird jetzt kurz neu gestartet" meldet die Oberfläche, wann sie wieder läuft."""
        h = self.html
        self.assertIn("async function pipWatchRestart(base)", h)
        self.assertIn("if(r&&r.restarted) pipWatchRestart(", h)
        self.assertIn("Die Übertragung läuft wieder (nach", h)
        self.assertIn('d.state==="refused"||d.state==="stopped"', h[h.index("async function pipWatchRestart"):][:1400])        # ein Fehlschlag wird gemeldet

    def test_wlan_cards_show_the_name_of_the_stick(self):
        """Issue #6: Die Namen der WLAN-Sticks (z. B. 802.11ac NIC) stehen in der Übersicht und in der Auswahl der WLAN-Karte."""
        self.assertIn("const head=`<b>${esc(c.iface)}</b>${devInfo(c)}`", self.html)          # Name des Sticks steht in jeder Zeile (Zeilen der Karten: devRow)
        self.assertIn("${head} · Kameranetz", self.html)
        self.assertIn("${head} · ${c.ip?", self.html)
        self.assertIn('${(c.label||c.name)?" · "+esc(c.label||c.name):(c.ip?" · "+esc(c.ip):"")}', self.html)

    def test_wifi_cards_carry_name_vendor_usb_id_and_driver(self):
        info = {"usb_id": "0bda:c811", "name": "802.11ac NIC", "vendor": "Realtek", "driver": "rtl8821cu"}
        w = server.Wifi(tempfile.mkdtemp(), False, mock.Mock(iface="eth1"))
        with mock.patch("os.listdir", lambda p: ["lo", "wlan0"]), mock.patch("os.path.isdir", lambda p: p.endswith("wlan0/wireless")), \
                mock.patch.object(server, "iface_ips", lambda: []), mock.patch.object(server, "read", lambda p, d="": "up"), \
                mock.patch.object(server.dji, "netdev_info", lambda n: dict(info)):
            cards = w.cards()
        self.assertEqual(len(cards), 1)
        self.assertEqual({k: cards[0][k] for k in ("iface", "name", "vendor", "usb_id", "driver")},
                         {"iface": "wlan0", "name": "802.11ac NIC", "vendor": "Realtek", "usb_id": "0bda:c811", "driver": "rtl8821cu"})

    def test_main_connection_has_its_own_heading_not_under_the_dji_one(self):
        """Issue #5: Die Hauptverbindung gilt für alle RTMP-Kameras (Handy, Drohne ...); sie stand fälschlich unter "DJI-Kameras (Bluetooth)"."""
        h = self.html
        main, dji = h.index('<div class="sech">Hauptverbindung</div>'), h.index('<summary>DJI-Kameras (Bluetooth)</summary>')
        self.assertLess(main, dji)
        self.assertLess(h.index('id="netsel"'), dji)            # Auswahl und Adresszeile gehören zur Hauptverbindung
        self.assertLess(h.index('id="netinfo"'), dji)
        self.assertGreater(h.index('id="djicams"'), dji)        # der DJI-Teil (Adapter, Kameras, Suche) bleibt unter seiner Überschrift
        self.assertNotIn("Bereich „DJI-Kameras“ gewählt", h)    # der Hinweis oben verweist auf "Hauptverbindung"

    def test_header_has_no_connection_badge(self):
        self.assertNotIn('id="mode"', self.html)                # "verbunden" sagte nichts, was die Live-Karte nicht schon zeigt
        self.assertNotIn('$("mode")', self.html)

    def test_logout_moved_from_the_header_to_the_power_card(self):
        self.assertNotIn('id="logout"', self.header)
        card = self.html[self.html.index('id="c_power"'):self.html.index("</details>", self.html.index('id="c_power"'))]
        self.assertIn('id="logout"', card)
        self.assertIn("Box ausschalten und abmelden", card)
        self.assertEqual(self.html.count('id="logout"'), 1)
        self.assertIn('$("logout").addEventListener("click"', self.html)


class HiddenAttribute(unittest.TestCase):

    """Ein Element mit hidden muss auch wirklich verschwinden: eine Klasse mit display (z. B. .row, .updlink) überstimmt sonst das Attribut.
    Fehler 0.9.47: die gelben Punkte in der Kopfleiste blieben trotz hidden dauerhaft sichtbar."""

    def test_page_has_a_global_hidden_rule(self):
        html = open(os.path.join(server.WEB_DIR, "index.html"), encoding="utf-8").read()
        self.assertRegex(html, r"\[hidden\]\s*\{\s*display\s*:\s*none\s*!important")

    def test_header_dots_are_hidden_in_the_markup(self):
        html = open(os.path.join(server.WEB_DIR, "index.html"), encoding="utf-8").read()
        for i in ("hdr_upd", "hdr_sys"):
            self.assertRegex(html, r'id="%s"[^>]*\shidden[\s>]' % i)


class UpdateHelperRepair(unittest.TestCase):
    """System-Updates: ein unterbrochener Paketlauf (dpkg was interrupted) wird erkannt und vor dem Update abgeschlossen."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("pbupdate_repair", os.path.join(os.path.dirname(HERE), "install", "pipbox-update.py"))
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.upd = os.path.join(self.d, "updates")
        os.makedirs(self.upd)
        self.status = {}
        patches = [mock.patch.object(self.m, "DPKG_UPDATES", self.upd), mock.patch.object(self.m, "log", lambda line: None),
                   mock.patch.object(self.m, "save_status", lambda **kw: self.status.update(kw)),
                   mock.patch.object(self.m, "belacoder_running", lambda: False),
                   mock.patch.object(self.m, "DPKG_LOCKS", (os.path.join(self.d, "lock"),))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def audit(self, out="", rc=0):
        return mock.patch.object(self.m.subprocess, "run", lambda *a, **k: mock.Mock(returncode=rc, stdout=out, stderr=""))

    def test_leftovers_in_updates_mean_interrupted(self):
        with self.audit(""):
            self.assertFalse(self.m.dpkg_interrupted())
            open(os.path.join(self.upd, "0001"), "w").close()
            self.assertTrue(self.m.dpkg_interrupted())

    def test_half_configured_packages_mean_interrupted(self):
        with self.audit("The following packages are only half configured, probably due to problems\n tailscale"):
            self.assertTrue(self.m.dpkg_interrupted())

    def test_busy_lock_is_detected(self):
        import fcntl
        self.assertFalse(self.m.dpkg_busy())
        fd = os.open(self.m.DPKG_LOCKS[0], os.O_RDWR | os.O_CREAT, 0o640)
        try:
            # eine Sperre eines anderen Prozesses: im selben Prozess würde lockf sie nur erneuern, darum in einem Kindprozess prüfen
            code = ("import fcntl,os,sys\nfd=os.open(sys.argv[1],os.O_RDWR)\n"
                    "try:\n fcntl.lockf(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)\n print('frei')\nexcept OSError:\n print('belegt')\n")
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            r = subprocess.run([sys.executable, "-c", code, self.m.DPKG_LOCKS[0]], capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), "belegt")
        finally:
            os.close(fd)

    def test_repair_runs_configure_then_fix_install(self):
        calls = []
        def fake_run(args, **kw):
            calls.append(args)
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(self.m.subprocess, "run", fake_run), mock.patch.object(self.m, "run_apt", lambda a, p=None: (calls.append(["apt-get"] + a) or (0, ""))):
            self.assertTrue(self.m.repair_dpkg())
        self.assertEqual(calls[0][:3], ["dpkg", "--configure", "-a"])
        self.assertEqual(calls[1][:3], ["apt-get", "-f", "install"])

    def test_repair_fails_cleanly(self):
        with mock.patch.object(self.m.subprocess, "run", lambda *a, **k: mock.Mock(returncode=1, stdout="Fehler", stderr="")):
            self.assertFalse(self.m.repair_dpkg())
        with mock.patch.object(self.m, "dpkg_busy", lambda: True):
            self.assertFalse(self.m.repair_dpkg())                     # anderer Paketvorgang: nichts anfassen

    def test_friendly_messages(self):
        f = self.m.friendly_error
        self.assertIn("sudo dpkg --configure -a", f("E: dpkg was interrupted, you must manually run 'dpkg --configure -a'"))
        self.assertIn("anderer Paketvorgang", f("E: Could not get lock /var/lib/dpkg/lock-frontend"))
        self.assertEqual(f("E: irgendwas anderes"), "Update fehlgeschlagen: E: irgendwas anderes")

    def test_run_repairs_before_updating_and_reports_failure(self):
        open(os.path.join(self.upd, "0001"), "w").close()
        order = []
        with mock.patch.object(self.m.shutil, "disk_usage", lambda p: mock.Mock(free=10 * 2**30)), \
                mock.patch.object(self.m, "dpkg_interrupted", lambda: True), mock.patch.object(self.m, "dpkg_busy", lambda: False), \
                mock.patch.object(self.m, "repair_dpkg", lambda: order.append("repair") or False), \
                mock.patch.object(self.m, "run_apt", lambda a, p=None: order.append("apt") or (0, "")):
            self.m.do_run()
        self.assertEqual(order, ["repair"])                             # bei Misserfolg kein apt
        self.assertEqual(self.status["state"], "failed")
        self.assertIn("sudo dpkg --configure -a", self.status["message"])

    def test_run_does_not_touch_a_running_package_process(self):
        with mock.patch.object(self.m.shutil, "disk_usage", lambda p: mock.Mock(free=10 * 2**30)), \
                mock.patch.object(self.m, "dpkg_interrupted", lambda: True), mock.patch.object(self.m, "dpkg_busy", lambda: True), \
                mock.patch.object(self.m, "repair_dpkg", lambda: self.fail("darf nicht reparieren")):
            self.m.do_run()
        self.assertEqual(self.status["state"], "failed")
        self.assertIn("anderer Paketvorgang", self.status["message"])


class FunnelRemote(unittest.TestCase):
    """Öffentliche Freigabe (Funnel): nur auf ausdrückliche Anforderung, ohne Zeitgrenze (bleibt bis zum Beenden)."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("pbremote_funnel", os.path.join(os.path.dirname(HERE), "install", "pipbox-remote.py"))
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.status = {}
        self.calls = []
        self.cfg = {}
        self.patches = [mock.patch.object(self.m, "RUN", self.d), mock.patch.object(self.m, "LOCK", os.path.join(self.d, "lock")), mock.patch.object(self.m, "log", lambda msg: None),
                        mock.patch.object(self.m, "status", lambda **kw: self.status.update(kw)),
                        mock.patch.object(self.m, "installed", lambda: True),
                        mock.patch.object(self.m, "serve_config", lambda: self.cfg)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def on(self, host="box.example.ts.net:443", proxy="http://127.0.0.1:8780"):
        return {"TCP": {"443": {"HTTPS": True}}, "Web": {host: {"Handlers": {"/": {"Proxy": proxy}}}}, "AllowFunnel": {host: True}}

    def fake_run(self, rc=0, out="", become=None):
        def run(args, **kw):
            self.calls.append(list(args))
            if become is not None and args[:2] == ["tailscale", "funnel"] and "--bg" in args:
                self.cfg = become
            return mock.Mock(returncode=rc, stdout=out, stderr="")
        return run

    def test_funnel_active_only_for_the_ui_target(self):
        self.assertTrue(self.m.funnel_active(self.on()))
        self.assertFalse(self.m.funnel_active(self.on(proxy="http://127.0.0.1:3000")))     # fremde Freigabe zählt nicht
        self.assertFalse(self.m.funnel_active({"Web": self.on()["Web"], "AllowFunnel": {"box.example.ts.net:443": False}}))
        self.assertFalse(self.m.funnel_active({}))

    def test_funnel_on_has_no_time_limit(self):
        """Issue 2: Die Freigabe bleibt bis zum Beenden an, auch nach einem Neustart (Tailscale behält sie)."""
        with mock.patch.object(self.m.subprocess, "run", self.fake_run(become=self.on())):
            self.m.do_funnel_on()
        self.assertEqual(self.calls[0][:3], ["tailscale", "funnel", "--bg"])
        self.assertIn("8780", self.calls[0])
        self.assertEqual(self.status["state"], "idle")
        self.assertNotIn("funnel_until", self.status)
        self.assertNotIn("Stunden", self.status["message"])
        self.assertIn("bis sie beendet wird", self.status["message"])
        for gone in ("guard", "funnel_until", "set_funnel_until", "FUNNEL_HOURS", "FUNNEL_UNTIL"):
            self.assertFalse(hasattr(self.m, gone), gone)

    def test_funnel_not_allowed_in_the_tailnet_shows_the_link(self):
        out = "Funnel not available; HTTPS must be enabled. Visit https://login.tailscale.com/f/funnel?node=abc123 to enable"
        with mock.patch.object(self.m.subprocess, "run", self.fake_run(rc=1, out=out)):
            self.m.do_funnel_on()
        self.assertEqual(self.status["state"], "needs_funnel")
        self.assertTrue(self.status["hint_url"].startswith("https://login.tailscale.com/f/funnel"))

    def test_funnel_failure_is_reported(self):
        with mock.patch.object(self.m.subprocess, "run", self.fake_run(rc=1, out="irgendein anderer Fehler")):
            with self.assertRaises(RuntimeError):
                self.m.do_funnel_on()

    def test_funnel_off_resets_and_restores_only_the_private_share(self):
        self.cfg = self.on()
        def run(args, **kw):
            self.calls.append(list(args))
            if args[:3] == ["tailscale", "serve", "--bg"]:
                self.cfg = {"Web": self.on()["Web"]}               # nur Serve, kein Funnel
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(self.m.subprocess, "run", run), mock.patch.object(self.m, "ts", lambda *a, **k: self.calls.append(["tailscale", *a])):
            self.m.do_funnel_off()
        self.assertEqual(self.calls[0], ["tailscale", "funnel", "reset"])
        self.assertEqual(self.calls[1][:3], ["tailscale", "serve", "--bg"])
        self.assertEqual(self.status["state"], "idle")

    def test_funnel_off_complains_if_it_stays_on(self):
        self.cfg = self.on()
        with mock.patch.object(self.m.subprocess, "run", lambda *a, **k: mock.Mock(returncode=0, stdout="", stderr="")), \
                mock.patch.object(self.m, "ts", lambda *a, **k: None):
            with self.assertRaises(RuntimeError) as e:
                self.m.do_funnel_off()
        self.assertIn("sudo tailscale funnel reset", str(e.exception))

    def test_a_leftover_guard_timer_of_an_earlier_version_does_nothing(self):
        """Der Zeitgeber früherer Versionen ruft den Helfer mit "guard" auf: Er beendet nichts mehr (kein Aufräumen nach 8 Stunden)."""
        offs = []
        self.cfg = self.on()
        with mock.patch.object(self.m, "do_funnel_off", lambda msg="": offs.append(msg)), \
                mock.patch.object(self.m.sys, "argv", ["pipbox-remote.py", "guard"]):
            self.assertEqual(self.m.main(), 0)
        self.assertEqual(offs, [])
        self.assertEqual(self.calls, [])

    def test_mode_list_has_both_funnel_words(self):
        self.assertIn("funnel_on", self.m.MODES)
        self.assertIn("funnel_off", self.m.MODES)


class FunnelRequests(unittest.TestCase):
    """Server: funnel_on nur mit ausdrücklicher Bestätigung, Absenderadresse hinter dem Tailscale-Proxy."""

    def remote(self, **fake):
        d = tempfile.mkdtemp()
        r = server.Remote(d, demo=True)
        r.fake = fake
        return r

    def test_funnel_needs_the_explicit_public_flag(self):
        r = self.remote(serve=True)
        with self.assertRaises(ValueError):
            r.request("funnel_on", True)
        with self.assertRaises(ValueError):
            r.request("funnel_on", True, public=False)
        with self.assertRaises(ValueError):
            r.request("funnel_on", False, public=True)
        r.request("funnel_on", True, public=True)
        st = r.status()
        self.assertTrue(st["funnel"])
        self.assertNotIn("funnel_until", st)
        self.assertNotIn("peers", st)                                            # Issue 2: die Geräteliste "Weitere Geräte in Ihrem Netz" ist weg

    def test_funnel_off_only_when_it_is_on(self):
        r = self.remote(serve=True)
        with self.assertRaises(ValueError):
            r.request("funnel_off", True)
        r.request("funnel_on", True, public=True)
        r.request("funnel_off", True)
        self.assertFalse(r.status()["funnel"])
        self.assertTrue(r.status()["serve"])

    def test_actions_list_is_fixed(self):
        self.assertEqual(server.Remote.ACTIONS, ("install", "login", "down", "serve_on", "serve_off", "funnel_on", "funnel_off", "logout"))

    def handler(self, peer, xff=None):
        h = server.Handler.__new__(server.Handler)
        h.client_address = (peer, 12345)
        h.headers = {"X-Forwarded-For": xff} if xff is not None else {}
        return h

    def test_address_behind_the_local_proxy_is_the_last_forwarded_one(self):
        self.assertEqual(self.handler("127.0.0.1", "203.0.113.9").ip(), "203.0.113.9")
        self.assertEqual(self.handler("127.0.0.1", "1.2.3.4, 203.0.113.9").ip(), "203.0.113.9")     # eine vom Absender mitgeschickte Adresse zählt nicht
        self.assertEqual(self.handler("::1", "2001:db8::1").ip(), "2001:db8::1")
        self.assertEqual(self.handler("127.0.0.1").ip(), "127.0.0.1")
        self.assertEqual(self.handler("127.0.0.1", "kein-ip").ip(), "127.0.0.1")

    def test_address_from_other_peers_ignores_the_header(self):
        self.assertEqual(self.handler("192.168.1.20", "203.0.113.9").ip(), "192.168.1.20")


class AutoSystemCheck(unittest.TestCase):
    """Stille Suche nach Systemupdates alle 6 Stunden: wann sie fällig ist, was der Helfer dabei tut (und nicht tut)."""
    U = server.Updates
    DAY = 6 * 3600

    def due(self, st=None, now=10 * 24 * 3600, uptime=3600, streaming=False, pending=False, last_try=0, helper=True):
        return self.U.auto_check_due(st if st is not None else {"state": "done", "last_check": now - self.DAY - 5}, now, uptime, streaming, pending, last_try, helper)

    def test_interval_is_six_hours_and_retry_one_hour(self):
        self.assertEqual((self.U.AUTO_EVERY, self.U.AUTO_RETRY, self.U.AUTO_AFTER_BOOT, self.U.AUTO_RETRY_EARLY), (6 * 3600, 3600, 120, 300))
        self.assertEqual((server.SwUpdate.CHECK_EVERY, server.SwUpdate.RETRY_AFTER_ERROR), (6 * 3600, 1800))

    def test_due_after_the_interval_and_never_checked(self):
        self.assertTrue(self.due())
        self.assertTrue(self.due(st={"state": "never"}))
        self.assertTrue(self.due(st={}))

    def test_not_due_when_checked_recently(self):
        now = 10 * self.DAY
        up = 9 * self.DAY                                                           # die Box läuft schon lange
        self.assertFalse(self.due(st={"state": "done", "last_check": now - 3600}, now=now, uptime=up))
        self.assertFalse(self.due(st={"state": "done", "last_check": now - self.DAY + 60}, now=now, uptime=up))

    def test_once_after_every_start_even_if_checked_recently(self):
        """Nach dem Start der Box wird immer einmal gesucht, auch wenn die letzte Suche (z. B. vor dem Neustart) noch keine 6 Stunden her ist."""
        now = 10 * self.DAY
        st = {"state": "done", "last_check": now - 1800}                            # vor 30 Minuten
        self.assertTrue(self.due(st=st, now=now, uptime=300))                       # die Box ist erst seit 5 Minuten an: davor war der Start
        self.assertFalse(self.due(st=st, now=now, uptime=3600))                     # an seit einer Stunde, Suche war danach: nicht wieder
        self.assertTrue(self.due(st={"state": "never"}, now=now, uptime=130))

    def test_failed_search_is_repeated_sooner_in_the_first_half_hour(self):
        now = 10 * self.DAY
        st = {"state": "never"}
        self.assertFalse(self.due(st=st, now=now, uptime=600, last_try=now - 200))
        self.assertTrue(self.due(st=st, now=now, uptime=600, last_try=now - 400))   # Router und Mobilfunk brauchen oft einige Minuten
        self.assertFalse(self.due(st=st, now=now, uptime=4000, last_try=now - 400)) # später wieder nur stündlich
        self.assertTrue(self.due(st=st, now=now, uptime=4000, last_try=now - 3700))

    def test_never_while_sending_or_busy_or_pending(self):
        self.assertFalse(self.due(streaming=True))
        self.assertFalse(self.due(pending=True))
        self.assertFalse(self.due(st={"state": "running", "last_check": 0}))
        self.assertFalse(self.due(st={"state": "rebooting"}))
        self.assertFalse(self.due(helper=False))

    def test_not_right_after_boot_and_with_retry_pause(self):
        self.assertFalse(self.due(uptime=60))                                        # Dienste und Netz kommen noch hoch
        self.assertTrue(self.due(uptime=120))                                        # zwei Minuten nach dem Start: suchen
        now = 10 * self.DAY
        self.assertFalse(self.due(now=now, last_try=now - 1800))                     # vor kurzem versucht (z. B. kein Internet)
        self.assertTrue(self.due(now=now, last_try=now - 2 * 3600))

    def make(self):
        d = tempfile.mkdtemp()
        u = server.Updates(d, demo=False)
        return d, u

    def test_github_check_runs_every_six_hours_and_retries_sooner_after_an_error(self):
        class Idle:
            def _active(self):
                return False
        sw = server.SwUpdate(tempfile.mkdtemp(), False, Idle())
        sw.started = 1000.0 - 3 * 3600                          # läuft schon lange (die erste halbe Stunde nach dem Start gilt unten)
        calls = []

        def fake_get(name, limit):
            calls.append(name)
            if fail[0]:
                raise OSError("kein Netz")
            return "9.9.9" if name == "VERSION" else "## 9.9.9\n- x"
        fail = [True]
        t = [1000.0]
        with mock.patch.object(sw, "_get", fake_get), mock.patch.object(server.time, "time", lambda: t[0]):
            self.assertIn("error", sw.check())
            n = len(calls)
            t[0] += 600
            sw.check()
            self.assertEqual(len(calls), n)                       # nach 10 Minuten noch nicht wieder
            t[0] += 1500                                          # 35 Minuten nach dem Fehlversuch
            fail[0] = False
            self.assertEqual(sw.check().get("latest"), "9.9.9")
            n = len(calls)
            t[0] += 5 * 3600
            sw.check()
            self.assertEqual(len(calls), n)                       # nach 5 Stunden noch aus dem Zwischenspeicher
            t[0] += 2 * 3600
            sw.check()
            self.assertGreater(len(calls), n)                     # nach über 6 Stunden neu

    def test_github_check_retries_after_three_minutes_in_the_first_half_hour(self):
        class Idle:
            def _active(self):
                return False
        sw = server.SwUpdate(tempfile.mkdtemp(), False, Idle())
        calls = []

        def fake_get(name, limit):
            calls.append(name)
            raise OSError("kein Netz")
        t = [5000.0]
        sw.started = t[0]
        with mock.patch.object(sw, "_get", fake_get), mock.patch.object(server.time, "time", lambda: t[0]):
            sw.check()
            n = len(calls)
            t[0] += 120
            sw.check()
            self.assertEqual(len(calls), n)                       # nach 2 Minuten noch nicht
            t[0] += 90                                            # 3,5 Minuten nach dem Start des Dienstes
            sw.check()
            self.assertGreater(len(calls), n)
            n = len(calls)
            t[0] += 3600                                          # später: nur noch nach 30 Minuten
            sw.check()
            m = len(calls)
            t[0] += 600
            sw.check()
            self.assertEqual(len(calls), m)

    def test_github_auto_check_does_not_ask_while_sending(self):
        class Busy:
            def _active(self):
                return True
        sw = server.SwUpdate(tempfile.mkdtemp(), False, Busy())
        with mock.patch.object(sw, "_get", lambda n, l: (_ for _ in ()).throw(AssertionError("gefragt"))):
            self.assertEqual(sw.check(), {"checked_at": None})

    def test_request_file_gets_only_the_fixed_word(self):
        d, u = self.make()
        with mock.patch.object(u, "status", lambda: {"state": "done", "last_check": 0, "streaming": False, "helper_installed": True}), \
                mock.patch.object(server, "read", lambda p, d=None: "9999\n" if "uptime" in p else (d or "")):
            self.assertTrue(u.auto_check())
            self.assertEqual(open(os.path.join(d, "update-request")).read(), "autocheck\n")
            self.assertFalse(u.auto_check())                                          # Anforderung liegt noch: nicht noch einmal

    def test_opening_the_page_after_a_start_searches_right_away(self):
        """Issue 1: Wer sich kurz nach dem Start anmeldet, soll gleich wissen, ob es Updates gibt, ohne die zwei Minuten abzuwarten."""
        d, u = self.make()
        st = {"state": "never", "last_check": 0, "streaming": False, "helper_installed": True}
        with mock.patch.object(u, "status", lambda: st), \
                mock.patch.object(server, "read", lambda p, d=None: "30\n" if "uptime" in p else (d or "")):
            self.assertFalse(u.auto_check())                                          # die Schleife wartet noch
            self.assertTrue(u.auto_check(present=True))                               # die Anmeldung nicht
            self.assertEqual(open(os.path.join(d, "update-request")).read(), "autocheck\n")
            os.remove(os.path.join(d, "update-request"))
            self.assertFalse(u.auto_check(present=True))                              # nicht bei jedem Seitenaufruf wieder (Pause nach dem Versuch)
        streaming = dict(st, streaming=True)
        with mock.patch.object(u, "status", lambda: streaming), \
                mock.patch.object(server, "read", lambda p, d=None: "9999\n" if "uptime" in p else (d or "")):
            u.last_try = 0.0
            self.assertFalse(u.auto_check(present=True))                              # nie während der Übertragung

    def test_demo_and_api_do_not_trigger_it(self):
        d, u = self.make()
        u.demo = True
        self.assertFalse(u.auto_check())
        u.demo = False
        with self.assertRaises(ValueError):
            u.request("autocheck", True)                                              # über die Schnittstelle nicht erreichbar

    def helper(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("pbupdate_auto", os.path.join(os.path.dirname(HERE), "install", "pipbox-update.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_helper_knows_the_word(self):
        self.assertIn("autocheck", self.helper().MODES)

    def test_helper_failure_leaves_the_old_state_untouched(self):
        m = self.helper()
        saved = {}
        prev = {"state": "failed", "mode": "run", "message": "Update fehlgeschlagen: x", "finished": 5, "started": 4}
        with mock.patch.object(m, "belacoder_running", lambda: False), mock.patch.object(m, "log", lambda l: None), \
                mock.patch.object(m, "run_apt", lambda a, p=None: (100, "kein Internet")), \
                mock.patch.object(m, "save_status", lambda **kw: saved.update(kw)):
            m.do_autocheck(prev)
        self.assertEqual(saved, prev)                                                 # nichts Neues, alter Zustand zurückgesetzt

    def test_helper_does_nothing_while_sending(self):
        m = self.helper()
        calls = []
        with mock.patch.object(m, "belacoder_running", lambda: True), mock.patch.object(m, "run_apt", lambda a, p=None: calls.append(a) or (0, "")), \
                mock.patch.object(m, "save_status", lambda **kw: calls.append(kw)):
            m.do_autocheck({})
        self.assertEqual(calls, [])

    def test_helper_success_records_the_result_like_a_check(self):
        m = self.helper()
        saved = {}
        plan = {"rc": 0, "count": 7, "download": "40 MB", "packages": ["belaui"], "belabox": ["belaui"], "held": [], "install_held": None}
        with mock.patch.object(m, "belacoder_running", lambda: False), mock.patch.object(m, "log", lambda l: None), \
                mock.patch.object(m, "run_apt", lambda a, p=None: (0, "")), mock.patch.object(m, "plan", lambda: plan), \
                mock.patch.object(m, "save_status", lambda **kw: saved.update(kw)):
            m.do_autocheck({"state": "never"})
        self.assertEqual((saved["state"], saved["available"], saved["mode"]), ("done", 7, "check"))
        self.assertIn("last_check", saved)


class UpdateFreshCheck(unittest.TestCase):
    """Eine neue Version war bis zu sechs Stunden unsichtbar, wenn die Box kurz davor nachgefragt hatte (Meldung: "Er findet auf der Box die 75 nicht")."""

    def sw(self, answers, sending=False):
        send = mock.Mock(_active=lambda: sending)
        sw = server.SwUpdate(tempfile.mkdtemp(), False, send)
        sw.version = "0.9.74"
        sw.started -= 3 * 3600                                      # nicht in der ersten halben Stunde nach dem Start
        calls = []

        def get(name, limit):
            calls.append(name)
            return (answers.pop(0) if len(answers) > 1 else answers[0]) if name == "VERSION" else "## 0.9.75 (Beta)\n- x\n"
        return sw, calls, mock.patch.object(sw, "_get", get)

    def test_background_check_still_waits_six_hours(self):
        sw, calls, patch = self.sw(["0.9.74\n", "0.9.75\n"])
        with patch:
            sw.check()
            sw.cache_t -= 3600
            self.assertEqual(sw.status()["latest"], "0.9.74")        # ohne fresh: der Zwischenspeicher gilt (kein Mobilfunk-Verkehr ohne Grund)
        self.assertEqual(calls.count("VERSION"), 1)

    def test_opening_the_page_renews_an_answer_older_than_five_minutes(self):
        sw, calls, patch = self.sw(["0.9.74\n", "0.9.75\n"])
        with patch:
            sw.check()
            sw.cache_t -= 6 * 60
            st = sw.status(fresh=True)
        self.assertEqual((st["latest"], st["newer"]), ("0.9.75", True))
        self.assertEqual(calls.count("VERSION"), 2)

    def test_a_recent_answer_is_not_asked_again(self):
        sw, calls, patch = self.sw(["0.9.74\n", "0.9.75\n"])
        with patch:
            sw.check()
            sw.cache_t -= 60
            self.assertEqual(sw.status(fresh=True)["latest"], "0.9.74")
        self.assertEqual(calls.count("VERSION"), 1)

    def test_nothing_is_asked_while_sending(self):
        sw, calls, patch = self.sw(["0.9.74\n", "0.9.75\n"], sending=True)
        with patch:
            sw.cache, sw.cache_t = {"latest": "0.9.74", "checked_at": 1}, time.time() - 3600
            self.assertEqual(sw.status(fresh=True)["latest"], "0.9.74")
        self.assertEqual(calls, [])

    def test_endpoint_and_page_use_it(self):
        src = open(os.path.join(os.path.dirname(HERE), "server.py"), encoding="utf-8").read()
        self.assertIn('fresh="fresh=1" in q', src)
        page = open(os.path.join(os.path.dirname(HERE), "web", "index.html"), encoding="utf-8").read()
        self.assertIn('swLoad(false,true);', page)                                   # beim Öffnen der Seite
        self.assertIn('$("swcard").addEventListener("toggle"', page)                 # und beim Aufklappen der Karte
        self.assertIn('?fresh=1', page)


class UpdateVersionCheck(unittest.TestCase):
    """Die Karte "Software-Update" zeigte nach einem Update "installiert 0.9.64" und "neueste auf GitHub 0.9.63": Die Antwort kam aus einem veralteten
    Zwischenspeicher (raw.githubusercontent.com hält eine neue Version bis zu fünf Minuten zurück)."""

    def sw(self, version, answers):
        send = mock.Mock(_active=lambda: False)
        sw = server.SwUpdate(tempfile.mkdtemp(), False, send)
        sw.version = version
        calls = []

        def get(name, limit):
            calls.append(name)
            if name == "VERSION":
                return answers.pop(0) if len(answers) > 1 else answers[0]
            return "## 0.9.64 (Beta)\n- x\n"
        return sw, calls, mock.patch.object(sw, "_get", get)

    def test_latest_is_never_older_than_the_installed_version(self):
        sw, calls, patch = self.sw("0.9.64", ["0.9.63\n"])
        with patch:
            st = sw.check(force=True)
        self.assertEqual(st["latest"], "0.9.64")                 # nicht "0.9.63" neben "installiert 0.9.64"
        self.assertTrue(st.get("stale"))
        with patch:
            out = sw.status()
        self.assertEqual((out["current"], out["latest"], out["newer"]), ("0.9.64", "0.9.64", False))

    def test_a_stale_answer_is_asked_again_soon_and_the_real_latest_shows_up(self):
        sw, calls, patch = self.sw("0.9.64", ["0.9.63\n", "0.9.65\n"])
        with patch:
            sw.check(force=True)
            n = len(calls)
            sw.check()                                             # gerade erst gefragt: noch der gespeicherte Wert
            self.assertEqual(len(calls), n)
            sw.cache_t -= sw.RETRY_EARLY + 5                       # nach der kurzen Wartezeit (nicht erst nach sechs Stunden)
            st = sw.check()
        self.assertEqual(st["latest"], "0.9.65")
        self.assertFalse(st.get("stale"))
        self.assertTrue(server.vkey(st["latest"]) > server.vkey(sw.version))

    def test_a_normal_answer_is_kept_for_hours(self):
        sw, calls, patch = self.sw("0.9.60", ["0.9.64\n"])
        with patch:
            st = sw.check(force=True)
            self.assertEqual((st["latest"], st.get("stale")), ("0.9.64", None))
            sw.cache_t -= 3600
            n = len(calls)
            sw.check()
        self.assertEqual(len(calls), n)

    def test_files_come_from_the_github_api_first_and_from_raw_only_as_a_fallback(self):
        sw = server.SwUpdate(tempfile.mkdtemp(), False, mock.Mock(_active=lambda: False))
        urls = []

        class Resp:
            def __init__(self, body):
                self.body = body

            def read(self, n):
                return self.body[:n]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(req, timeout=0):
            urls.append((req.full_url, req.headers.get("Accept")))
            if "api.github.com" in req.full_url and opener.fail_api:
                raise urllib.error.HTTPError(req.full_url, 403, "rate limit", {}, None)
            return Resp(b"0.9.64\n")
        opener.fail_api = False
        with mock.patch.object(server.urllib.request, "urlopen", opener):
            self.assertEqual(sw._get("VERSION", 64).strip(), "0.9.64")
            self.assertEqual(urls[0][0], "https://api.github.com/repos/IRL4YOU/irl4you-pip/contents/VERSION?ref=main")
            self.assertEqual(urls[0][1], "application/vnd.github.raw+json")
            self.assertEqual(len(urls), 1)
            opener.fail_api = True                                   # Anfragegrenze erreicht: dann über raw
            urls.clear()
            self.assertEqual(sw._get("VERSION", 64).strip(), "0.9.64")
            self.assertEqual([u for u, _ in urls], ["https://api.github.com/repos/IRL4YOU/irl4you-pip/contents/VERSION?ref=main",
                                                     "https://raw.githubusercontent.com/IRL4YOU/irl4you-pip/main/VERSION"])
            def fail_all(req, timeout=0):
                raise urllib.error.URLError("kein Netz")
            with mock.patch.object(server.urllib.request, "urlopen", fail_all):
                with self.assertRaises(OSError):
                    sw._get("VERSION", 64)


class UpdateNotes(unittest.TestCase):
    """Issue #9: Das Update zeigt die Änderungen aller Versionen, die neuer sind als die installierte."""
    LOG = ("# Änderungen\n\n## 0.9.58 (Beta)\n- **Neu:** Eins.\n  Fortsetzung.\n\n## 0.9.57 (Beta)\n- Zwei.\n\n## 0.9.56 (Beta)\n- Drei.\n\n"
           "## 0.9.55 (Beta)\n- Vier.\n")

    def test_everything_since_the_installed_version_newest_first(self):
        n = server.SwUpdate._sections_since(self.LOG, "0.9.56")
        self.assertEqual([l for l in n.splitlines() if l.startswith("## ")], ["## 0.9.58 (Beta)", "## 0.9.57 (Beta)"])
        self.assertNotIn("Drei", n)
        self.assertIn("Fortsetzung.", n)

    def test_one_new_version_and_nothing_new(self):
        self.assertEqual(server.SwUpdate._sections_since(self.LOG, "0.9.57").splitlines()[0], "## 0.9.58 (Beta)")
        self.assertEqual(server.SwUpdate._sections_since(self.LOG, "0.9.58").splitlines()[0], "## 0.9.58 (Beta)")     # nichts neuer: erster Abschnitt wie bisher
        self.assertEqual(server.SwUpdate._sections_since(self.LOG, "unbekannt").splitlines()[0], "## 0.9.58 (Beta)")

    def test_number_of_versions_is_limited(self):
        log = "\n".join("## 0.9.%d (Beta)\n- x\n" % i for i in range(60, 20, -1))
        n = server.SwUpdate._sections_since(log, "0.9.1", max_sections=3)
        self.assertEqual(len([l for l in n.splitlines() if l.startswith("## ")]), 3)

    def test_all_sixteen_versions_of_a_long_jump_are_shown(self):
        """Issue #57: Von 0.9.169 auf 0.9.183 zeigte die Box nur die neuesten 6 Versionen."""
        log = "# Änderungen\n\n" + "\n".join("## 0.9.%d (Beta)\n- **Neu:** Punkt %d.\n" % (i, i) for i in range(183, 160, -1))
        n = server.SwUpdate._sections_since(log, "0.9.169")
        heads = [l for l in n.splitlines() if l.startswith("## ")]
        self.assertEqual(len(heads), 14)
        self.assertEqual((heads[0], heads[-1]), ("## 0.9.183 (Beta)", "## 0.9.170 (Beta)"))

    def test_limits_are_generous_but_finite(self):
        log = "\n".join("## 0.9.%d (Beta)\n- x\n" % i for i in range(500, 0, -1))
        n = server.SwUpdate._sections_since(log, "0.0.1")
        self.assertEqual(len([l for l in n.splitlines() if l.startswith("## ")]), 100)
        big = "## 1.0.0\n" + ("- " + "y" * 900 + "\n") * 1000
        self.assertLessEqual(len(server.SwUpdate._sections_since(big, "0.9.0")), 201_000)

    def test_whole_changelog_file_is_read(self):
        self.assertGreaterEqual(server.SwUpdate.CHANGELOG_MAX, 400_000)                 # die Datei hat heute rund 130 KB; 60 KB schnitten ältere Versionen ab


class UpdateHistory(unittest.TestCase):
    """Issue #57: der ganze Änderungsverlauf mit Suche."""

    def sw(self, sending=False, demo=False):
        sw = server.SwUpdate(tempfile.mkdtemp(), demo, mock.Mock(_active=lambda: sending))
        return sw

    def test_loads_the_whole_file_once_and_keeps_it(self):
        sw = self.sw()
        calls = []

        def get(name, limit):
            calls.append((name, limit))
            return "## 0.9.2\n- b\n\n## 0.9.1\n- a\n" if name == "CHANGELOG.md" else "# Archiv\n\n## 0.8.0\n- z\n"
        with mock.patch.object(sw, "_get", get):
            a = sw.history()
            b = sw.history()
        self.assertEqual(a, {"text": "## 0.9.2\n- b\n\n## 0.9.1\n- a\n\n# Archiv\n\n## 0.8.0\n- z\n", "error": ""})   # Liste und Archiv zusammen
        self.assertEqual(b, a)
        self.assertEqual(calls, [("CHANGELOG.md", server.SwUpdate.CHANGELOG_MAX), ("CHANGELOG-Archiv.md", server.SwUpdate.CHANGELOG_MAX)])        # genau eine Abfrage je Datei

    def test_archive_is_loaded_for_notes_only_when_the_installed_version_is_older_than_the_list(self):
        """Die Änderungsliste hält nur die letzten Versionen, der Rest steht im Archiv: dessen Einträge kommen nur dazu, wenn die installierte Version älter ist als die Liste."""
        sw = self.sw()
        calls = []

        def get(name, limit):
            calls.append(name)
            return "# Änderungen\n\n## 0.9.202\n- c\n\n## 0.9.201\n- b\n\n## 0.9.200\n- a\n" if name == "CHANGELOG.md" else "# Archiv\n\n## 0.9.199\n- z\n\n## 0.9.198\n- y\n"
        with mock.patch.object(sw, "_get", get):
            t = sw._changelog("0.9.200")                                                   # neuer als alles im Archiv: nur die Liste
            self.assertEqual(calls, ["CHANGELOG.md"])
            self.assertNotIn("0.9.199", t)
            calls.clear()
            t = sw._changelog("0.9.150")                                                   # älter als die Liste: Archiv dazu
            self.assertEqual(calls, ["CHANGELOG.md", "CHANGELOG-Archiv.md"])
            self.assertIn("## 0.9.199", t)
            n = server.SwUpdate._sections_since(t, "0.9.198")
            self.assertEqual([l for l in n.splitlines() if l.startswith("## ")], ["## 0.9.202", "## 0.9.201", "## 0.9.200", "## 0.9.199"])
        self.assertEqual(server.SwUpdate._oldest("## 0.9.5\n\n## 0.9.11\n## x\n"), (0, 9, 5))
        self.assertIsNone(server.SwUpdate._oldest("kein Abschnitt"))

    def test_nothing_is_loaded_while_sending(self):
        sw = self.sw(sending=True)
        with mock.patch.object(sw, "_get", side_effect=AssertionError("kein Zugriff")):
            r = sw.history()
        self.assertEqual(r["text"], "")
        self.assertIn("Übertragung", r["error"])

    def test_error_is_readable_and_not_cached(self):
        sw = self.sw()
        with mock.patch.object(sw, "_get", side_effect=OSError("kein Netz")):
            r = sw.history()
        self.assertEqual((r["text"], r["error"]), ("", "GitHub ist nicht erreichbar."))
        with mock.patch.object(sw, "_get", side_effect=lambda n, l: "## 0.9.1\n- a\n" if n == "CHANGELOG.md" else (_ for _ in ()).throw(OSError("kein Archiv"))):
            self.assertEqual(sw.history()["error"], "")                                    # der nächste Versuch klappt (auch ohne Archiv)

    def test_stale_copy_is_kept_when_github_fails_later(self):
        sw = self.sw()
        with mock.patch.object(sw, "_get", side_effect=lambda n, l: "## 0.9.1\n- a\n" if n == "CHANGELOG.md" else (_ for _ in ()).throw(OSError("kein Archiv"))):
            sw.history()
        sw.hist_t -= 3600
        with mock.patch.object(sw, "_get", side_effect=OSError("weg")):
            self.assertEqual(sw.history()["text"], "## 0.9.1\n- a\n")

    def test_page_search_filters_words_marks_hits_and_never_uses_html(self):
        h = open(os.path.join(os.path.dirname(HERE), "web", "index.html"), encoding="utf-8").read()
        self.assertIn('fetch("/api/swupdate/history")', h)
        self.assertIn("toks.every(w=>t.includes(w))", h)                                    # alle Wörter müssen vorkommen
        self.assertIn("const k=document.createElement(\"mark\"); k.textContent=m[0];", h)     # Treffer als Textknoten, nie als HTML
        self.assertIn("res.innerHTML=fmtNotes(out)", h)                                    # fmtNotes maskiert den Text
        self.assertIn('id="sw_histbtn"', h)

    def test_route_exists(self):
        src = open(os.path.join(os.path.dirname(HERE), "server.py"), encoding="utf-8").read()
        self.assertIn('if path == "/api/swupdate/history":', src)

    def test_update_card_formats_the_notes_and_reloads_by_itself(self):
        h = open(os.path.join(os.path.dirname(HERE), "web", "index.html"), encoding="utf-8").read()
        self.assertIn("function fmtNotes(", h)
        self.assertIn("setHtml(n,fmtNotes(d.notes))", h)
        self.assertNotIn('n.textContent=d.newer?d.notes:""', h)                       # nicht mehr als roher Text
        self.assertIn("swPending", h)                                                  # Sperre und Neuladen hängen an einem Zustand, der nicht von selbst zurückgesetzt wird
        self.assertIn("location.reload()", h[h.index("function swFinish"):][:700])          # am Ende des Updates einmal neu laden
        self.assertIn("swFinish(", h[h.index("async function swLoad"):][:2600])
        self.assertNotIn("swWasInstalling", h)

    def test_page_keeps_showing_progress_while_the_interface_restarts(self):
        h = open(os.path.join(os.path.dirname(HERE), "web", "index.html"), encoding="utf-8").read()
        self.assertIn("!pbUpdating", h[:h.index("</script>", h.index("var pbUpdating"))])        # beim Update kein Neuladen beim ersten 401
        self.assertIn("/api/swprogress", h[h.index("async function swPublic"):][:400])
        for card in ("swprog", "updprog"):                                                   # beide Update-Karten haben dieselbe Anzeige
            self.assertIn('id="%s" class="prog"' % card, h)
        self.assertIn("function progSet(", h)


class UpdateProgress(unittest.TestCase):
    """Fortschritt des Software-Updates: Prozent im Zustand, öffentlicher Abruf ohne Version und Adressen."""

    def make(self, st):
        d = tempfile.mkdtemp()
        sw = server.SwUpdate(d, False, None)
        sw.STATUS = os.path.join(d, "status.json")
        if st is not None:
            with open(sw.STATUS, "w") as f:
                json.dump(st, f)
        return sw

    def test_public_view_has_only_state_step_and_percent(self):
        sw = self.make({"state": "installing", "step": "Kopiere Programme und Oberfläche", "progress": 58, "time": int(time.time()),
                        "frm": "0.9.1", "to": "0.9.2", "version": "0.9.2", "message": "intern"})
        self.assertEqual(sw.progress_public(), {"state": "installing", "step": "Kopiere Programme und Oberfläche", "progress": 58})

    def test_public_view_finished_old_and_garbage(self):
        now = int(time.time())
        self.assertEqual(self.make({"state": "done", "time": now, "progress": 100, "step": "x"}).progress_public(), {"state": "done", "step": "", "progress": 100})
        self.assertEqual(self.make({"state": "done", "time": now - 7 * 3600, "progress": 100}).progress_public()["state"], "idle")    # alte Meldung
        self.assertEqual(self.make(None).progress_public(), {"state": "idle", "step": "", "progress": 0})
        for bad in (-5, 101, "50", True, None):
            self.assertEqual(self.make({"state": "installing", "progress": bad, "time": now}).progress_public()["progress"], 0)
        self.assertEqual(self.make({"state": "bogus", "time": now}).progress_public()["state"], "idle")

    def test_progress_endpoint_works_without_login(self):
        src = open(os.path.join(os.path.dirname(HERE), "server.py"), encoding="utf-8").read()
        i = src.index('path == "/api/swprogress"')
        self.assertLess(i, src.index('if not self.authed():', i - 200) + 400)                  # vor der Anmeldeprüfung
        self.assertLess(src.index('path == "/api/swprogress"'), src.index('if path == "/api/metrics"'))


class DeviceNaming(unittest.TestCase):
    """Issues #11 und #12: Gerätenamen von WLAN- und Bluetooth-Sticks: Standardname mit Hersteller, bereinigte Zeichen, eigene Namen."""

    def test_generic_names_get_the_vendor_in_front(self):
        import dji
        self.assertEqual(dji.device_label("802.11ac NIC", "Realtek"), "Realtek 802.11ac NIC")      # Standardbezeichnung statt Gerätename
        self.assertEqual(dji.device_label("Bluetooth Radio", "Realtek"), "Realtek Bluetooth Radio")
        self.assertEqual(dji.device_label("WLAN", "Ralink"), "Ralink WLAN")
        self.assertEqual(dji.device_label("ASUS USB-BT500", "Realtek"), "ASUS USB-BT500")          # echter Name: unverändert
        self.assertEqual(dji.device_label("TP-Link UB500 Adapter", "Realtek"), "TP-Link UB500 Adapter")
        self.assertEqual(dji.device_label("802.11ac NIC", "Realtek 802"), "Realtek 802 802.11ac NIC")
        self.assertEqual(dji.device_label("Realtek 802.11ac NIC", "Realtek"), "Realtek 802.11ac NIC")   # Hersteller steht schon drin
        self.assertEqual(dji.device_label("", "Realtek"), "Realtek")
        self.assertEqual(dji.device_label("", ""), "")

    HWDB_TPLINK = ("ID_VENDOR_FROM_DATABASE=TP-Link\nID_MODEL_FROM_DATABASE=AC600 wireless Realtek RTL8811AU [Archer T2U Nano]\n")

    def hwdb(self, outputs):
        """systemd-hwdb nachgebaut: outputs = {"usb:v2357p011E": Ausgabe}; gibt die Aufrufe zurück."""
        import dji
        dji._HWDB.clear()
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            if cmd[0] != "systemd-hwdb":
                raise FileNotFoundError(cmd[0])
            return mock.Mock(stdout=outputs.get(cmd[2], ""))
        return calls, mock.patch.object(dji.subprocess, "run", run)

    def test_generic_names_use_the_name_from_the_system_hardware_database(self):
        """Issue #11: Die Original-Oberfläche der BELABOX zeigt "TP-Link Archer T2U Nano", die Box zeigte "802.11ac NIC": Jetzt gilt der Name aus der Datenbank."""
        import dji
        calls, patch = self.hwdb({"usb:v2357p011E": self.HWDB_TPLINK})
        with patch:
            self.assertEqual(dji.hwdb_names("2357:011e"), ("TP-Link", "Archer T2U Nano"))            # Handelsname aus den eckigen Klammern
            self.assertEqual(dji.device_label("802.11ac NIC", "Realtek", "2357:011e"), "TP-Link Archer T2U Nano")
            self.assertEqual(dji.device_label("802.11ac NIC", "Realtek", "2357:011e"), "TP-Link Archer T2U Nano")
            self.assertEqual(calls, [["systemd-hwdb", "query", "usb:v2357p011E"]])                      # Kennung groß geschrieben, nur einmal gefragt
        self.assertEqual(server.DeviceNames.key("2357:011e"), "usb:2357:011e")

    def test_database_name_only_replaces_generic_names_and_falls_back_cleanly(self):
        import dji
        calls, patch = self.hwdb({"usb:v2357p0604": "ID_VENDOR_FROM_DATABASE=TP-Link\nID_MODEL_FROM_DATABASE=UB500 Adapter\n",
                                   "usb:v0BDApC811": "ID_VENDOR_FROM_DATABASE=Realtek Semiconductor Corp.\n"})        # Datenbank kennt nur den Hersteller
        with patch:
            self.assertEqual(dji.device_label("TP-Link UB500 Adapter", "Realtek", "2357:0604"), "TP-Link UB500 Adapter")   # echter Name bleibt
            self.assertEqual(dji.device_label("Bluetooth Radio", "Realtek", "2357:0604"), "TP-Link UB500 Adapter")        # Standardname: Datenbank
            self.assertEqual(dji.device_label("WLAN", "Ralink", "0bda:c811"), "Realtek WLAN")                              # Marke zur USB-Kennung vor dem Chiphersteller
            self.assertEqual(dji.device_label("802.11ac NIC", "Realtek", "0bda:c811"), "Realtek 802.11ac NIC")             # kein Modell in der Datenbank
            self.assertEqual(dji.hwdb_names("0bda:c811"), ("Realtek", ""))                                                  # Firmenzusatz abgeschnitten
            self.assertEqual(dji.device_label("802.11ac NIC", "Realtek", ""), "Realtek 802.11ac NIC")                       # ohne Kennung (eingebaute Karte)
            self.assertEqual(dji.hwdb_names("kaputt"), ("", ""))
        dji._HWDB.clear()
        with mock.patch.object(dji.subprocess, "run", side_effect=FileNotFoundError("systemd-hwdb")):                        # ohne Datenbank
            self.assertEqual(dji.device_label("802.11ac NIC", "Realtek", "2357:011e"), "Realtek 802.11ac NIC")
        dji._HWDB.clear()

    def test_typographic_dashes_and_control_characters_are_cleaned(self):
        import dji
        for dash in "\u2010\u2011\u2012\u2013\u2014\u2212\uff0d":
            self.assertEqual(dji.clean_devname("TP%sLink UB500 Adapter" % dash), "TP-Link UB500 Adapter")
        self.assertEqual(dji.clean_devname("  Mein \x00Stick\n  v2 "), "Mein Stick v2")
        self.assertEqual(dji.clean_devname(None), "")
        self.assertEqual(len(dji.clean_devname("x" * 200)), 60)

    def test_unreadable_bytes_in_the_usb_name_do_not_break_reading(self):
        import dji
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "idVendor"), "w") as f:
            f.write("2357\n")
        with open(os.path.join(d, "idProduct"), "w") as f:
            f.write("0604\n")
        with open(os.path.join(d, "product"), "wb") as f:
            f.write(b"TP\x96Link UB500 Adapter\n")                      # kein UTF-8
        info = dji.usb_device_above(d)
        self.assertEqual(info["usb_id"], "2357:0604")
        self.assertIn("Link UB500 Adapter", info["name"])
        self.assertNotIn("%", info["name"])

    def test_own_names_are_checked_stored_and_removed(self):
        n = server.DeviceNames(tempfile.mkdtemp())
        n.set("usb:0bda:c811", "  Logilink   WL0237 ")
        self.assertEqual(n.label("usb:0bda:c811", "Standard"), "Logilink WL0237")                  # Leerzeichen zusammengefasst
        self.assertEqual(n.label("usb:2357:0604", "Standard"), "Standard")
        n.set("bt:AA:BB:CC:DD:EE:FF", "Mein Stick")
        n.set("if:wlan1", "Hotspot-Stick")
        n.set("usb:0bda:c811", "")                                                                  # leer = Standardname
        self.assertEqual(n.label("usb:0bda:c811", "Standard"), "Standard")
        self.assertEqual(n.label("if:wlan1", "x"), "Hotspot-Stick")
        for bad_key in ("", None, "wlan0", "usb:xyz", "usb:0BDA:c811", "if:../../etc", "bt:AA", "../x"):
            with self.assertRaises(ValueError, msg=str(bad_key)):
                n.set(bad_key, "Name")
        for bad_name in ("x" * 41, "a\x00b", "a\nb\x07", 5, None):
            with self.assertRaises(ValueError, msg=str(bad_name)):
                n.set("usb:0bda:c811", bad_name)

    def test_connection_names_add_to_the_interface_and_stay_apart_from_device_names(self):
        d = tempfile.mkdtemp()
        n = server.DeviceNames(d)
        n.set("net:eth1", "  Router   Keller ")
        n.set("net:usb0", "Handy Tethering")
        n.set("if:wlan0", "Interner Stick")                                                        # Name einer Karte, keine Verbindung
        self.assertEqual(n.conn_names(), {"eth1": "Router Keller", "usb0": "Handy Tethering"})
        self.assertEqual(n.label("if:eth1", "Standard"), "Standard")                               # die Karten-Namen bleiben unberührt
        self.assertEqual(n.label("if:wlan0", "x"), "Interner Stick")
        self.assertEqual(server.DeviceNames(d).conn_names()["eth1"], "Router Keller")             # bleibt nach einem Neustart
        n.set("net:eth1", "")                                                                       # leer = kein Name
        self.assertEqual(n.conn_names(), {"usb0": "Handy Tethering"})
        for bad_key in ("net:", "net:ETH1", "net:../x", "net:eth1;rm", "net:a"):
            with self.assertRaises(ValueError, msg=bad_key):
                n.set(bad_key, "Name")
        self.assertIn("net:usb0", n._all())                                                         # damit auch in der Sicherung der Einstellungen

    def test_wifi_cards_carry_key_and_label_with_the_own_name_first(self):
        names = server.DeviceNames(tempfile.mkdtemp())
        w = server.Wifi(tempfile.mkdtemp(), False, mock.Mock(iface="eth1"), names)
        info = {"usb_id": "0bda:c811", "name": "802.11ac NIC", "vendor": "Realtek", "driver": "rtl8821cu"}

        def cards():
            with mock.patch("os.listdir", lambda p: ["wlan0"]), mock.patch("os.path.isdir", lambda p: p.endswith("wlan0/wireless")), \
                    mock.patch.object(server, "iface_ips", lambda: []), mock.patch.object(server, "read", lambda p, d="": "up"), \
                    mock.patch.object(server.dji, "netdev_info", lambda n: dict(info)):
                return w.cards()
        c = cards()[0]
        self.assertEqual((c["key"], c["label"], c["custom"]), ("usb:0bda:c811", "Realtek 802.11ac NIC", False))
        names.set("usb:0bda:c811", "Logilink WL0237")
        c = cards()[0]
        self.assertEqual((c["label"], c["custom"]), ("Logilink WL0237", True))
        info.update(usb_id="", name="", vendor="")                                                   # eingebaute Karte: Schlüssel ist die Schnittstelle
        self.assertEqual(cards()[0]["key"], "if:wlan0")

    def test_bluetooth_adapters_get_labels_in_the_api(self):
        names = server.DeviceNames(tempfile.mkdtemp())
        names.set("usb:2357:0604", "Mein UB500")
        st = {"adapters": [{"usb_id": "2357:0604", "name": "TP\u2011Link UB500 Adapter", "vendor": "Realtek", "address": "AA:BB:CC:DD:EE:FF", "powered": True},
                           {"usb_id": "0b05:190e", "name": "ASUS USB-BT500", "vendor": "Realtek", "address": "AA:BB:CC:DD:EE:00", "powered": True},
                           {"usb_id": "0bda:8771", "name": "Bluetooth Radio", "vendor": "Realtek", "address": "AA:BB:CC:DD:EE:01", "powered": True},
                           {"usb_id": "", "name": "", "vendor": "", "address": "11:22:33:44:55:66", "powered": True}],
              "adapter_problems": [{"id": "33fa:0010", "name": "BARROT Bluetooth 5.4 Adapter", "hint": "x"}]}
        out = server.label_bluetooth(st, names)["adapters"]
        self.assertEqual([(a["key"], a["label"], a["custom"]) for a in out],
                         [("usb:2357:0604", "Mein UB500", True), ("usb:0b05:190e", "ASUS USB-BT500", False),
                          ("usb:0bda:8771", "Realtek Bluetooth Radio", False), ("bt:11:22:33:44:55:66", "Eingebauter Bluetooth-Adapter", False)])
        self.assertEqual(st["adapter_problems"][0]["label"], "BARROT Bluetooth 5.4 Adapter")
        # der gemeldete Name wird nur bereinigt, wenn der eigene fehlt: der typografische Strich wird zum Bindestrich
        names.set("usb:2357:0604", "")
        self.assertEqual(server.label_bluetooth(st, names)["adapters"][0]["label"], "TP-Link UB500 Adapter")
        self.assertEqual(server.label_bluetooth({"adapters": [], "adapter_problems": []}, None)["adapters"], [])        # ohne Namensspeicher

    def test_handler_uses_the_labelling_and_has_a_checked_name_endpoint(self):
        src = open(server.__file__, encoding="utf-8").read()
        self.assertIn("label_bluetooth(self.djisvc.status(), self.names)", src)
        self.assertIn('if path == "/api/devname":', src)


class PictureStyle(unittest.TestCase):
    """Issue #7: Deckkraft, Beschnitt und Rahmen je kleinem Bild (Stellen 1 bis 3): prüfen, speichern, in den Pipeline-Text bringen."""
    STY = {"1": {"visible": True, "opacity": 50, "crop": {"l": 400, "r": 0, "t": 0, "b": 0},
                 "border": {"enabled": True, "width": 6, "color": "#FF8800", "opacity": 70, "radius": 24}},
           "2": {"visible": False},
           "3": {"border": {"enabled": True, "width": 3}}}

    def build(self, styles=None, **kw):
        cfg = dict(server.PipelineStore.DEFAULT, **BASE, **kw)
        if styles is not None:
            cfg["styles"] = styles
        return server.PipelineStore(os.devnull).build(cfg)

    def test_default_look_changes_nothing_in_the_pipeline_text(self):
        self.assertNotIn("style", self.build())
        self.assertNotIn("style", self.build(styles=server.clean_styles(None)))

    def test_pipeline_text_carries_the_style_of_each_position(self):
        t = self.build(server.clean_styles(self.STY, strict=True))
        mix = [l for l in t.split("\n") if l.startswith("pbpipmix")][0]
        self.assertIn('width-pct=25 style1="op=50,cl=400,bw=6,bc=ff8800,bo=70,br=24"', mix)
        self.assertIn('slot2=1 corner2=2 style2="op=0"', mix)                         # nicht sichtbar: Deckkraft 0
        self.assertIn('slot3=2 corner3=3 style3="bw=3,bc=ffffff"', mix)
        self.assertEqual(t.count("style1="), 1)

    def test_old_plugin_never_gets_the_property(self):
        with mock.patch.object(server, "plugin_style", lambda: False):
            self.assertNotIn("style", self.build(server.clean_styles(self.STY, strict=True)))

    def test_old_two_mixer_form_gives_the_second_picture_its_own_style_as_style1(self):
        with mock.patch.object(server, "plugin_multi", lambda: False):
            t = self.build(server.clean_styles(self.STY, strict=True))
        self.assertIn('pbpipmix name=pipmix2 slot=1 corner=2 width-pct=25 style1="op=0"', t)

    def test_swap_pipeline_has_the_styles_on_the_following_mixer(self):
        t = self.build(server.clean_styles(self.STY, strict=True), swap_cams=2)
        mix = [l for l in t.split("\n") if l.startswith("pbpipmix")][0]
        self.assertIn("follow-tag=true", mix)
        self.assertIn('style1="op=50,cl=400,bw=6,bc=ff8800,bo=70,br=24"', mix)
        self.assertIn('style2="op=0"', mix)

    def test_rounding_belongs_to_the_picture_not_to_the_border(self):
        """Issue #7: Die Rundung gilt für das kleine Bild selbst, mit oder ohne Rahmen. Ältere Einstellungen (Rundung im Rahmen) werden übernommen."""
        self.assertEqual(server.style_text(server.clean_style({"radius": 20}, strict=True)), "br=20")                       # ohne Rahmen
        self.assertEqual(server.style_text(server.clean_style({"radius": 20, "border": {"enabled": True, "width": 4}}, strict=True)),
                         "bw=4,bc=ffffff,br=20")
        self.assertEqual(server.style_text(server.clean_style(None)), "")                                                  # Standard: eckig
        self.assertEqual(server.clean_style(None)["radius"], 0)
        # ältere Anfrage und ältere Datei: Die Rundung stand im Rahmen und wirkte nur mit ihm
        self.assertEqual(server.clean_style({"border": {"enabled": True, "radius": 30}}, strict=True)["radius"], 30)
        self.assertEqual(server.clean_style({"border": {"enabled": False, "radius": 30}}, strict=True)["radius"], 0)
        old = {"visible": True, "opacity": 100, "crop": {"l": 0, "r": 0, "t": 0, "b": 0},
               "border": {"enabled": False, "width": 6, "color": "#ffffff", "opacity": 100, "radius": 12}}              # so lagen die Standardwerte in 0.9.59 und 0.9.60 auf der Platte
        st = server.clean_style(None, old)
        self.assertEqual(st["radius"], 0)                                                                                 # nicht plötzlich rund
        self.assertNotIn("radius", st["border"])
        self.assertEqual(server.clean_style({"radius": 60}, strict=True)["radius"], 60)
        with self.assertRaises(ValueError):
            server.clean_style({"radius": 61}, strict=True)

    def test_scaling_from_1_to_100_percent_with_a_software_stage_for_tiny_pictures(self):
        """Issue #7: Die Skalierung ist von 1 bis 100 % frei. Der Hardware-Decoder schafft auf der Box nur Breiten ab 120 Pixeln (gemessen: darunter
        "No valid frames decoded"): Darunter verkleinert er auf 128 x 72 und videoscale macht den Rest."""
        self.assertEqual((server.SIZE_MIN, server.SIZE_MAX), (1, 100))
        self.assertEqual(server.pip_size(100), (1920, 1080))
        self.assertEqual(server.pip_size(1), (16, 8))
        for pct in range(1, 101):
            w, h = server.pip_size(pct)
            self.assertTrue(w % 16 == 0 and h % 2 == 0 and 16 <= w <= 1920, pct)
        big = server.small_decode(*server.pip_size(50))
        self.assertEqual(big, "mppvideodec width=960 height=540 !\nvideo/x-raw,format=NV12 !\n")                  # Hardware allein
        self.assertNotIn("videoscale", server.small_decode(*server.pip_size(7)))                                     # 7 % = 144 Pixel: noch Hardware
        tiny = server.small_decode(*server.pip_size(3))                                                              # 3 % = 64 Pixel: Software-Stufe
        self.assertEqual(tiny, "mppvideodec width=128 height=72 !\nvideoscale !\nvideo/x-raw,format=NV12,width=64,height=36 !\n")
        text = server.PipelineStore(os.devnull).build(dict(server.PipelineStore.DEFAULT, **dict(BASE, size_pct=2, size_pct2=50, size_pct3=100)))
        self.assertIn("mppvideodec width=128 height=72 !\nvideoscale !\nvideo/x-raw,format=NV12,width=32,height=18", text)
        self.assertIn("mppvideodec width=960 height=540 !", text)
        self.assertIn("mppvideodec width=1920 height=1080 !", text)
        s = store()
        s.set(dict(BASE, size_pct=1, size_pct2=100, size_pct3=60), KEYS)
        self.assertEqual((s.cfg["size_pct"], s.cfg["size_pct2"], s.cfg["size_pct3"]), (1, 100, 60))

    def test_every_small_picture_has_its_own_size(self):
        """Issue #7: Die Größe wird je kleinem Bild eingestellt (vorher galt eine Größe für alle)."""
        cfg = dict(server.PipelineStore.DEFAULT, **dict(BASE, size_pct=20, size_pct2=30, size_pct3=40))
        text = server.PipelineStore(os.devnull).build(cfg)
        w1, h1 = server.pip_size(20)
        w2, h2 = server.pip_size(30)
        w3, h3 = server.pip_size(40)
        self.assertEqual(len({(w1, h1), (w2, h2), (w3, h3)}), 3)
        self.assertIn("rtmp://127.0.0.1:1935/publish/cam-b", text)
        for pdemux, (w, h) in (("pdemux", (w1, h1)), ("p2demux", (w2, h2)), ("p3demux", (w3, h3))):
            chain = text[text.index(pdemux + ".video"):].split("pbpipsink")[0]
            self.assertIn("mppvideodec width=%d height=%d" % (w, h), chain, pdemux)
        # Tausch ohne Unterbrechung: je Kamera die Größe der Stelle, an der sie beim Aufbau steht
        dual = server.PipelineStore(os.devnull).build(dict(cfg, swap_cams=4))
        for slot, (w, h) in ((0, (w1, h1)), (1, (w1, h1)), (2, (w2, h2)), (3, (w3, h3))):            # Kamera 0 (Hauptbild) bekommt die Größe von Stelle 1
            self.assertIn("mppvideodec width=%d height=%d !" % (w, h), dual)
        # Speichern: jeder Wert 1 bis 100, ältere Anfragen ohne Bild 2 und 3 geben allen die Größe von Bild 1
        s = store()
        s.set(dict(BASE, size_pct=22, size_pct2=33, size_pct3=38), KEYS)
        self.assertEqual((s.cfg["size_pct"], s.cfg["size_pct2"], s.cfg["size_pct3"]), (22, 33, 38))
        s.set(dict(BASE, size_pct=27), KEYS)
        self.assertEqual((s.cfg["size_pct"], s.cfg["size_pct2"], s.cfg["size_pct3"]), (27, 27, 27))
        for bad in (dict(BASE, size_pct2=0), dict(BASE, size_pct3=101), dict(BASE, size_pct2="gross")):
            with self.assertRaises(ValueError, msg=str(bad)):
                s.set(bad, KEYS)
        self.assertEqual(s.cfg["size_pct2"], 27)                                                       # abgelehnt: nichts geändert
        old = server.PipelineStore._safe_cfg({"type": "pip", "size_pct": 31})                           # ältere Datei ohne die neuen Felder
        self.assertEqual((old["size_pct2"], old["size_pct3"]), (31, 31))
        self.assertEqual(server.PipelineStore._safe_cfg({"type": "pip", "size_pct": 20, "size_pct3": 9999})["size_pct3"], 100)

    def test_requests_are_checked(self):
        bad = ({"opacity": 101}, {"opacity": -1}, {"opacity": "viel"}, {"visible": "ja"}, {"crop": {"l": 2000}}, {"crop": {"l": 1000, "r": 900}},
               {"crop": {"t": 600, "b": 500}}, {"crop": "links"}, {"border": {"enabled": "ein"}}, {"border": {"width": 0}},
               {"border": {"width": 41}}, {"border": {"color": "rot"}}, {"border": {"color": "#12345"}}, {"border": {"opacity": 5}},
               {"border": {"radius": 61}}, {"border": 7})
        for b in bad:
            with self.assertRaises(ValueError, msg=str(b)):
                server.clean_style(b, strict=True)
        with self.assertRaises(ValueError):
            server.clean_styles({"1": "x"}, strict=True)
        with self.assertRaises(ValueError):
            server.clean_styles(["x"], strict=True)

    def test_crop_is_made_even_and_leaves_at_least_32_pixels(self):
        st = server.clean_style({"crop": {"l": 401, "r": 3, "t": 5, "b": 1043}}, strict=True)
        self.assertEqual(st["crop"], {"l": 400, "r": 2, "t": 4, "b": 1042})
        st = server.clean_style({"crop": {"l": 1888, "r": 0}}, strict=True)                  # genau am Rand erlaubt: 32 Pixel bleiben
        self.assertEqual(st["crop"]["l"], 1888)

    def test_saved_files_are_forced_into_ranges_and_never_reach_the_text(self):
        evil = {"1": {"opacity": "100 ! fakesink", "visible": "x", "crop": {"l": "5; rm", "r": 9999, "t": -5, "b": None},
                      "border": {"enabled": True, "color": "#fff; rm -rf", "width": "9;", "opacity": 1000, "radius": [1]}},
                "2": {"opacity": 1e9, "border": {"enabled": True, "width": 99999, "color": "#00ff00"}}, "3": "kaputt"}
        t = self.build(evil)
        self.assertNotIn("100 ! fakesink", t)
        self.assertNotIn("rm -rf", t)
        for m in __import__("re").findall(r'style\d="([^"]*)"', t):
            self.assertRegex(m, r"^[a-z0-9=,]*$")
        st = server.clean_styles(evil)
        self.assertTrue(0 <= st["1"]["opacity"] <= 100 and 1 <= st["2"]["border"]["width"] <= 40)
        self.assertEqual(st["2"]["border"]["color"], "#00ff00")
        self.assertEqual(st["3"], server.clean_style(None))

    def test_style_is_saved_kept_and_reported(self):
        s = store()
        s.set(dict(BASE, styles=self.STY), KEYS)
        self.assertEqual(s.cfg["styles"]["1"]["border"]["color"], "#ff8800")
        self.assertFalse(s.cfg["styles"]["2"]["visible"])
        s.set(dict(BASE), KEYS)                                            # eine Anfrage ohne Stile (z. B. ein anderer Client) lässt sie stehen
        self.assertEqual(s.cfg["styles"]["1"]["crop"]["l"], 400)
        s.set(dict(BASE, type="single", pip="", pip2="", pip3=""), KEYS)  # auch beim Wechsel auf eine Kamera
        self.assertEqual(s.cfg["styles"]["1"]["opacity"], 50)
        st = s.status([{"key": k, "name": k} for k in KEYS])
        self.assertEqual(st["config"]["styles"]["1"]["opacity"], 50)
        self.assertIn("plugin_style", st)
        with self.assertRaises(ValueError):
            s.set(dict(BASE, styles={"1": {"opacity": 500}}), KEYS)
        self.assertEqual(s.cfg["styles"]["1"]["opacity"], 50)             # ein abgelehnter Versuch ändert nichts

    def test_older_saved_pipeline_without_styles_gets_the_defaults(self):
        s = store()
        self.assertEqual(s.status([])["config"]["styles"], server.clean_styles(None))


class UpdateArchiveCheck(unittest.TestCase):
    """Das Archiv eines Software-Updates wird geprüft, bevor etwas eingespielt wird: eine Fehlerseite statt der Oberfläche darf nie durchkommen."""

    ERROR_PAGE = ('<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN" "http://www.w3.org/TR/html4/strict.dtd"><html><head><title>Error response</title></head>'
                  '<body><h1>Error response</h1><p>Error code: 404</p></body></html>')

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("pbswupdate", os.path.join(os.path.dirname(HERE), "install", "pipbox-swupdate.py"))
        cls.h = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.h)

    def tree(self, **pages):
        d = tempfile.mkdtemp()
        for f in self.h.REQUIRED:
            p = os.path.join(d, f)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write("")
        with open(os.path.join(d, "VERSION"), "w") as fh:
            fh.write("0.9.99\n")
        with open(os.path.join(d, "install", "install.sh"), "w") as fh:
            fh.write("echo ok\n")
        good = "<!doctype html>\n<html><head><title>IRL4YOU BOX</title></head><body>Seite</body></html>\n"
        for name in ("index", "login"):
            with open(os.path.join(d, "web", name + ".html"), "w") as fh:
                fh.write(pages.get(name, good))
        return d

    def test_good_pages_pass(self):
        self.assertEqual(self.h.validate(self.tree(), "0.9.1"), "0.9.99")

    def test_error_page_instead_of_the_ui_is_refused(self):
        for name in ("index", "login"):
            with self.assertRaises(self.h.Refuse) as e:
                self.h.validate(self.tree(**{name: self.ERROR_PAGE}), "0.9.1")
            self.assertIn(name + ".html", str(e.exception))

    def test_empty_or_foreign_pages_are_refused(self):
        for bad in ("", "kein html", "<!doctype html><html><body>etwas anderes</body></html>", "<!doctype html><html><body>IRL4YOU, abgeschnitten"):
            with self.assertRaises(self.h.Refuse, msg=bad):
                self.h.validate(self.tree(index=bad), "0.9.1")

    def test_real_pages_of_this_version_pass(self):
        d = self.tree()
        for name in ("index", "login"):
            shutil_copy = open(os.path.join(os.path.dirname(HERE), "web", name + ".html"), encoding="utf-8").read()
            with open(os.path.join(d, "web", name + ".html"), "w", encoding="utf-8") as fh:
                fh.write(shutil_copy)
        self.assertEqual(self.h.validate(d, "0.9.1"), "0.9.99")


if __name__ == "__main__":
    unittest.main()
