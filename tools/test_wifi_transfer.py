"""Tests für die gespeicherten WLAN-Netze beim Sichern und Einspielen (Issue #20): Root-Helfer pipbox-wifi.py (export_wifi, import_wifi) mit nachgebautem nmcli.
Alle Netznamen und Passwörter sind erfunden."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(ROOT, "install", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = load("pipbox-wifi")
PW = "Testnetz-Passwort-1"


def esc(v):
    return v.replace("\\", "\\\\").replace(":", "\\:")


class FakeNm:
    """nmcli nachgebaut: Profile mit Eigenschaften, merkt sich die Aufrufe."""

    def __init__(self, profiles=None, fail_add=()):
        self.profiles = profiles or {}
        self.calls = []
        self.fail_add = fail_add

    def __call__(self, *args, stdin=None, timeout=60):
        self.calls.append(args)
        a = list(args)
        secrets = False
        if a[:1] == ["-s"]:
            secrets, a = True, a[1:]
        if a[:2] == ["-t", "-f"] and a[3:6] == ["con", "show", "id"]:
            name, fields = a[6], a[2].split(",")
            prof = self.profiles.get(name)
            if prof is None:
                return mock.Mock(returncode=10, stdout="", stderr="Error: unknown connection")
            lines = []
            for f in fields:
                if f in prof and (secrets or not f.endswith(".psk")):
                    lines.append("%s:%s" % (f, esc(prof[f])))
                elif f.endswith(".psk") and not secrets:
                    lines.append("%s:<hidden>" % f)
            return mock.Mock(returncode=0, stdout="\n".join(lines) + "\n", stderr="")
        if a[:2] == ["con", "add"]:
            ssid = a[a.index("con-name") + 1]
            if ssid in self.fail_add:
                return mock.Mock(returncode=1, stdout="", stderr="Error: nicht möglich mit %s" % (a[a.index("wifi-sec.psk") + 1] if "wifi-sec.psk" in a else ""))
            self.profiles[ssid] = {"802-11-wireless.ssid": ssid}
            return mock.Mock(returncode=0, stdout="", stderr="")
        if a[:2] == ["con", "delete"]:
            self.profiles.pop(a[3], None)
            return mock.Mock(returncode=0, stdout="", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    def find(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == prefix]


def prof(ssid, km="wpa-psk", psk=PW, hidden="no", mode="infrastructure", flags="0 (none)"):
    p = {"802-11-wireless.ssid": ssid, "802-11-wireless.mode": mode, "802-11-wireless.hidden": hidden, "802-11-wireless-security.key-mgmt": km}
    if km:
        p["802-11-wireless-security.psk-flags"] = flags
    if psk is not None:
        p["802-11-wireless-security.psk"] = psk
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        patches = [mock.patch.object(H, "STATE", self.d), mock.patch.object(H, "EXPORT_FILE", os.path.join(self.d, "wifi-export.json")),
                   mock.patch.object(H, "REQ", os.path.join(self.d, "wifi-request")), mock.patch.object(H, "RUN", os.path.join(self.d, "run")),
                   mock.patch.object(H, "STATUS", os.path.join(self.d, "run", "status.json")), mock.patch.object(H, "HOTSPOT_FILE", os.path.join(self.d, "hotspot.json"))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.nm = FakeNm()
        p = mock.patch.object(H, "nm", self.nm)
        p.start()
        self.addCleanup(p.stop)
        self.names = []
        for p in (mock.patch.object(H, "connection_names", lambda: [[n, "802-11-wireless"] for n in self.nm.profiles]),
                  mock.patch.object(H, "wifi_devices", lambda: []), mock.patch.object(H, "camera_iface", lambda: "")):
            p.start()
            self.addCleanup(p.stop)


class Helpers(unittest.TestCase):
    def test_unescape(self):
        self.assertEqual(H.unescape_terse("a\\:b\\\\c"), "a:b\\c")
        self.assertEqual(H.unescape_terse("ohne"), "ohne")
        self.assertEqual(H.unescape_terse("ende\\"), "ende")

    def test_new_actions_are_known_and_the_request_may_be_larger(self):
        self.assertIn("export_wifi", H.ACTIONS)
        self.assertIn("import_wifi", H.ACTIONS)
        self.assertIn("read_req(REQ, 65536)", open(os.path.join(ROOT, "install", "pipbox-wifi.py"), encoding="utf-8").read())


class Export(Base):
    def test_reads_every_kind_of_profile(self):
        self.nm.profiles.update({"Heimnetz": prof("Heimnetz"), "Gast": prof("Gast", km="", psk=None), "Versteckt": prof("Versteckt", hidden="yes"),
                                 "Neu3": prof("Neu3", km="sae", psk="sae-passwort-1")})
        msg = H.do_export_wifi({"secrets": True})
        d = json.load(open(H.EXPORT_FILE))
        by = {n["ssid"]: n for n in d["networks"]}
        self.assertEqual(by["Heimnetz"], {"ssid": "Heimnetz", "hidden": False, "open": False, "security": "wpa-psk", "password": PW})
        self.assertEqual(by["Gast"], {"ssid": "Gast", "hidden": False, "open": True, "security": "none", "password": ""})
        self.assertTrue(by["Versteckt"]["hidden"])
        self.assertEqual((by["Neu3"]["security"], by["Neu3"]["password"]), ("sae", "sae-passwort-1"))
        self.assertEqual(msg, "4 WLAN-Netze gelesen")
        self.assertNotIn(PW, msg)

    def test_without_passwords_none_is_read_or_written(self):
        self.nm.profiles["Heimnetz"] = prof("Heimnetz")
        H.do_export_wifi({"secrets": False})
        self.assertNotIn(PW, open(H.EXPORT_FILE).read())
        self.assertEqual(json.load(open(H.EXPORT_FILE))["networks"][0]["password"], "")
        self.assertFalse([c for c in self.nm.calls if c[:1] == ("-s",)], "ohne Wunsch werden keine Geheimnisse gelesen")

    def test_not_transferable_profiles_are_reported_not_dropped_silently(self):
        self.nm.profiles.update({"Firma": prof("Firma", km="wpa-eap", psk=None), "Altgerät": prof("Altgerät", km="none", psk=None), "Offline": prof("Offline", psk=None),
                                 "Zugang": prof("Zugang", mode="ap"), "Gut": prof("Gut"), "Fragt": prof("Fragt", flags="2 (not-saved)", psk=None),
                                 "Agent": prof("Agent", flags="1 (agent-owned)", psk=None)})
        for secrets in (True, False):
            H.do_export_wifi({"secrets": secrets})
            d = json.load(open(H.EXPORT_FILE))
            self.assertIn("Gut", [n["ssid"] for n in d["networks"]])
            why = {s["ssid"]: s["why"] for s in d["skipped"]}
            self.assertEqual(why["Firma"], "Unternehmens-WLAN oder WEP")
            self.assertEqual(why["Fragt"], "Passwort nicht gespeichert")
            self.assertEqual(why["Agent"], "Passwort nicht gespeichert")
            self.assertEqual(why["Zugang"], "kein Client-Profil")
        H.do_export_wifi({"secrets": True})
        d = json.load(open(H.EXPORT_FILE))
        self.assertEqual([n["ssid"] for n in d["networks"]], ["Gut"])
        self.assertEqual({s["ssid"]: s["why"] for s in d["skipped"]}["Offline"], "Passwort nicht gespeichert")

    def test_hotspot_profiles_of_this_box_are_not_part_of_it(self):
        self.nm.profiles.update({"pipbox-hotspot-wlan1": prof("pipbox-hotspot-wlan1"), "Heimnetz": prof("Heimnetz")})
        with mock.patch.object(H, "connection_names", lambda: [[n, "802-11-wireless"] for n in self.nm.profiles]):
            H.do_export_wifi({"secrets": True})
        self.assertEqual([n["ssid"] for n in json.load(open(H.EXPORT_FILE))["networks"]], ["Heimnetz"])

    def test_profile_name_may_differ_from_the_network_name_and_duplicates_collapse(self):
        self.nm.profiles.update({"Arbeit (alt)": prof("Büro:Netz"), "Büro:Netz": prof("Büro:Netz")})
        H.do_export_wifi({"secrets": True})
        nets = json.load(open(H.EXPORT_FILE))["networks"]
        self.assertEqual([n["ssid"] for n in nets], ["Büro:Netz"])

    def test_password_with_colon_and_backslash_survives_the_terse_format(self):
        self.nm.profiles["Heimnetz"] = prof("Heimnetz", psk="pw:mit\\zeichen1")
        H.do_export_wifi({"secrets": True})
        self.assertEqual(json.load(open(H.EXPORT_FILE))["networks"][0]["password"], "pw:mit\\zeichen1")

    def test_invalid_password_in_a_profile_is_skipped(self):
        self.nm.profiles["Kurz"] = prof("Kurz", psk="kurz")
        H.do_export_wifi({"secrets": True})
        self.assertEqual(json.load(open(H.EXPORT_FILE))["skipped"][0]["why"], "Passwort nicht übertragbar")

    def test_file_is_private_and_a_planted_link_is_not_followed(self):
        target = os.path.join(self.d, "fremd")
        open(target, "w").write("unberührt")
        os.symlink(target, H.EXPORT_FILE)
        os.symlink(target, H.EXPORT_FILE + ".tmp")
        self.nm.profiles["Heimnetz"] = prof("Heimnetz")
        H.do_export_wifi({"secrets": True})
        self.assertEqual(open(target).read(), "unberührt")
        self.assertFalse(os.path.islink(H.EXPORT_FILE))
        self.assertEqual(os.stat(H.EXPORT_FILE).st_mode & 0o777, 0o600)

    def test_nothing_saved(self):
        self.assertEqual(H.do_export_wifi({}), "0 WLAN-Netze gelesen")
        self.assertEqual(json.load(open(H.EXPORT_FILE)), {"networks": [], "skipped": []})


class Import(Base):
    def net(self, ssid="Heimnetz", **kw):
        return dict({"ssid": ssid, "password": PW, "hidden": False, "open": False, "security": "wpa-psk"}, **kw)

    def test_creates_a_profile_for_every_network(self):
        msg = H.do_import_wifi({"networks": [self.net(), self.net("Gast", password="", open=True, security="none"), self.net("Versteckt", hidden=True),
                                             self.net("Neu3", security="sae")]})
        self.assertEqual(msg, "4 WLAN-Netze eingespielt")
        adds = {c[c.index("con-name") + 1]: c for c in self.nm.find("con", "add")}
        self.assertEqual(set(adds), {"Heimnetz", "Gast", "Versteckt", "Neu3"})
        a = adds["Heimnetz"]
        self.assertEqual(a[:4], ("con", "add", "type", "wifi"))
        self.assertIn("connection.autoconnect", a)
        self.assertEqual(a[a.index("wifi-sec.key-mgmt") + 1], "wpa-psk")
        self.assertEqual(a[a.index("wifi-sec.psk") + 1], PW)
        self.assertEqual(a[a.index("ifname") + 1], "*")
        self.assertNotIn("wifi-sec.psk", adds["Gast"])
        self.assertIn("802-11-wireless.hidden", adds["Versteckt"])
        self.assertEqual(adds["Neu3"][adds["Neu3"].index("wifi-sec.key-mgmt") + 1], "sae")
        self.assertNotIn(PW, msg)

    def test_a_network_of_the_same_name_is_replaced(self):
        self.nm.profiles["Heimnetz"] = prof("Heimnetz", psk="altes-passwort")
        H.do_import_wifi({"networks": [self.net()]})
        self.assertEqual([c[3] for c in self.nm.find("con", "delete")], ["Heimnetz"])
        self.assertLess(self.nm.calls.index(self.nm.find("con", "delete")[0]), self.nm.calls.index(self.nm.find("con", "add")[0]))

    def test_the_profile_of_the_camera_network_is_left_alone(self):
        self.nm.profiles["Kameranetz"] = prof("Kameranetz")
        with mock.patch.object(H, "camera_iface", lambda: "wlan0"), mock.patch.object(H, "wifi_devices", lambda: [{"iface": "wlan0", "connection": "Kameranetz", "state": "connected"}]):
            msg = H.do_import_wifi({"networks": [self.net("Kameranetz"), self.net("Heimnetz")]})
        self.assertIn("1 WLAN-Netze eingespielt", msg)
        self.assertIn("Kameranetz", msg)
        self.assertEqual(self.nm.find("con", "delete"), [])

    def test_bad_requests_change_nothing(self):
        bad = ([], "x", None, [self.net()] * 51, [5], [self.net("")], [self.net("x" * 33)], [self.net("a\nb")], [self.net("pipbox-hotspot-wlan1")],
               [self.net(password="kurz")], [self.net(password="ä" * 10)], [self.net(security="wep")], [self.net(password="", open=False)],
               [self.net(password=PW, open=True, security="none")], [self.net(ssid=5)])
        for nets in bad:
            with self.assertRaises(ValueError, msg=str(nets)[:60]):
                H.do_import_wifi({"networks": nets})
        self.assertEqual(self.nm.calls, [])

    def test_failures_are_reported_without_the_password(self):
        self.nm.fail_add = ("Heimnetz",)
        msg = H.do_import_wifi({"networks": [self.net(), self.net("Gast", password="", open=True, security="none")]})
        self.assertIn("1 WLAN-Netze eingespielt", msg)
        self.assertIn("Heimnetz", msg)
        self.assertNotIn(PW, msg)
        self.nm.fail_add = ("Heimnetz", "Gast")
        with self.assertRaises(RuntimeError) as cm:
            H.do_import_wifi({"networks": [self.net(), self.net("Gast", password="", open=True, security="none")]})
        self.assertNotIn(PW, str(cm.exception))


class Main(Base):
    def run_main(self, req, raw=None):
        with open(H.REQ, "w") as f:
            f.write(raw if raw is not None else json.dumps(req))
        H.main()
        return json.load(open(H.STATUS))

    def test_mark_is_echoed_and_the_request_file_is_gone(self):
        self.nm.profiles["Heimnetz"] = prof("Heimnetz")
        st = self.run_main({"action": "export_wifi", "secrets": True, "mark": "0123456789abcdef"})
        self.assertEqual((st["state"], st["mark"]), ("done", "0123456789abcdef"))
        self.assertFalse(os.path.exists(H.REQ))
        self.assertNotIn(PW, json.dumps(st))

    def test_invalid_mark_is_dropped(self):
        for mark in ("x", "0123456789ABCDEF", 5, "0123456789abcdef0"):
            st = self.run_main({"action": "export_wifi", "mark": mark})
            self.assertEqual(st["mark"], "", str(mark))

    def test_import_through_main_with_a_request_over_8_kb(self):
        nets = [{"ssid": "Netz%02d" % i, "password": PW + str(i), "hidden": False, "open": False, "security": "wpa-psk", "pad": "x" * 200} for i in range(50)]
        raw = json.dumps({"action": "import_wifi", "networks": nets, "mark": "0123456789abcdef"})
        self.assertGreater(len(raw), 8192)
        st = self.run_main(None, raw)
        self.assertEqual(st["state"], "done", st)
        self.assertEqual(len(self.nm.find("con", "add")), 50)
        self.assertFalse(os.path.exists(H.REQ))                                              # Passwörter sofort von der Platte

    def test_errors_become_a_status_not_a_crash(self):
        st = self.run_main({"action": "import_wifi", "networks": [], "mark": "0123456789abcdef"})
        self.assertEqual(st["state"], "error")
        st = self.run_main({"action": "gibt-es-nicht"})
        self.assertEqual(st["state"], "error")



class RequestsDoNotOverwriteEachOther(unittest.TestCase):
    """Meldung des Nutzers beim Einspielen: "Der WLAN-Helfer hat nicht rechtzeitig geantwortet", obwohl die Netze angelegt waren. Eine Suche nach WLANs
    schrieb in die Auslösedatei, solange das Einspielen noch nicht gelesen war. Darum läuft immer nur eine Anfrage."""

    def make(self):
        sys.path.insert(0, ROOT)
        import server
        d = tempfile.mkdtemp()
        w = server.Wifi(d, False, None, None, None)
        w.status = lambda: {"helper_installed": True, "state": "idle", "cards": [{"iface": "wlan0", "camera_net": False}]}
        return server, w

    def test_scan_is_refused_while_an_import_waits_for_the_helper(self):
        server, w = self.make()
        w.STATUS = os.path.join(tempfile.mkdtemp(), "status.json")                    # der Helfer antwortet nie: helper_call wartet
        import threading
        t = threading.Thread(target=lambda: self.assertRaises(RuntimeError, w.helper_call, {"action": "import_wifi", "networks": []}, 1.2))
        t.start()
        import time
        time.sleep(0.4)
        with self.assertRaises(ValueError) as cm:
            w.request({"action": "scan", "iface": "wlan0"})
        self.assertIn("schon eine Aktion", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(w.req), "wifi-request")) and "scan" in open(w.req).read())     # die Anfrage des Einspielens bleibt unberührt
        t.join()

    def test_lock_is_released_after_the_call(self):
        server, w = self.make()
        w.STATUS = os.path.join(tempfile.mkdtemp(), "status.json")
        with self.assertRaises(RuntimeError):
            w.helper_call({"action": "import_wifi", "networks": []}, 0.5)
        w.request({"action": "scan", "iface": "wlan0"})                                  # danach geht eine Suche wieder
        self.assertIn("scan", open(w.req).read())

if __name__ == "__main__":
    unittest.main()
