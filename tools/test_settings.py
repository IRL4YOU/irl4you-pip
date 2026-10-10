"""Tests für Sichern und Einspielen der Einstellungen (Issue #20): Verschlüsselung (Vault), Export, strenge Prüfung beim Import, Rundlauf,
Schutz während der Sendung, Rückgängig. Alle Namen, Adressen und Passwörter sind erfunden."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import server  # noqa: E402

V = server.Vault
PW = "Geheim-Test-2026"
SID = "live_test_0001"
WIFI_PW = "Testnetz-Passwort-1"
HS_PW = "hotspot-test-pw"
DJI_PW = "kamera-wlan-pw"


class VaultTests(unittest.TestCase):
    def test_aes256_matches_fips_197(self):
        key = bytes(range(32))
        out = V.aes256_block(V._round_keys(key), bytes.fromhex("00112233445566778899aabbccddeeff"))
        self.assertEqual(out.hex(), "8ea2b7ca516745bfeafc49904b496089")                      # FIPS 197, Anhang C.3

    def test_ctr_matches_nist_sp800_38a(self):
        key = bytes.fromhex("603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4")
        ctr = int("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff", 16)
        plain = bytes.fromhex("6bc1bee22e409f96e93d7e117393172aae2d8a571e03ac9c9eb76fac45af8e51")
        want = "601ec313775789a5b7a7f504bbf3d228f443e3ca4d62b59aca84e990cacaf5c5"
        self.assertEqual(V.ctr(key, ctr, plain).hex(), want)                                  # SP 800-38A, F.5.5 CTR-AES256, Blöcke 1 und 2
        self.assertEqual(V.ctr(key, ctr, V.ctr(key, ctr, plain)), plain)                      # Ver- und Entschlüsseln sind dasselbe

    def test_ctr_matches_openssl_for_odd_lengths(self):
        try:
            subprocess.run(["openssl", "version"], capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("kein openssl")
        key, nonce = os.urandom(32), os.urandom(12)
        for n in (0, 1, 15, 16, 17, 1237):
            data = os.urandom(n)
            r = subprocess.run(["openssl", "enc", "-aes-256-ctr", "-K", key.hex(), "-iv", (nonce + b"\0" * 4).hex()], input=data, capture_output=True)
            if r.returncode:
                self.skipTest("openssl kennt aes-256-ctr nicht")
            self.assertEqual(V.ctr(key, int.from_bytes(nonce + b"\0" * 4, "big"), data), r.stdout, n)

    def test_round_trip_and_nothing_in_the_clear(self):
        plain = ("Kamera vorn, " + SID + " ").encode() * 50
        doc = V.seal(plain, PW, {"format": "x"}, iterations=V.MIN_ITER)
        self.assertEqual(V.open(doc, PW), plain)
        self.assertTrue(doc["encrypted"])
        self.assertNotIn(SID, json.dumps(doc))
        self.assertNotIn("Kamera", json.dumps(doc))

    def test_every_seal_is_different(self):
        a = V.seal(b"gleich", PW, {}, iterations=V.MIN_ITER)
        b = V.seal(b"gleich", PW, {}, iterations=V.MIN_ITER)
        self.assertNotEqual((a["salt"], a["nonce"], a["data"]), (b["salt"], b["nonce"], b["data"]))

    def test_wrong_password_and_every_tampered_field_are_refused(self):
        doc = V.seal(b"geheim" * 10, PW, {"format": "x"}, iterations=V.MIN_ITER)
        with self.assertRaisesRegex(ValueError, "falsch oder .* verändert"):
            V.open(doc, "falsches-Passwort")
        for field in ("salt", "nonce", "data", "mac"):
            bad = dict(doc)
            raw = bytearray(__import__("base64").b64decode(bad[field]))
            raw[0] ^= 1
            bad[field] = __import__("base64").b64encode(bytes(raw)).decode()
            with self.assertRaises(ValueError, msg=field):
                V.open(bad, PW)
        bad = dict(doc, iterations=doc["iterations"] + 1)
        with self.assertRaises(ValueError):
            V.open(bad, PW)                                                                   # auch die Rundenzahl ist durch die Prüfsumme geschützt

    def test_files_cannot_demand_absurd_work(self):
        doc = V.seal(b"x", PW, {}, iterations=V.MIN_ITER)
        for it in (1, 99999, V.MAX_ITER + 1, 10 ** 9, True, "600000", None):
            with self.assertRaises(ValueError, msg=str(it)):
                V.open(dict(doc, iterations=it), PW)

    def test_malformed_files_are_refused_cleanly(self):
        doc = V.seal(b"x", PW, {}, iterations=V.MIN_ITER)
        for patch in ({"salt": "###"}, {"salt": ""}, {"nonce": "AAAA"}, {"mac": "AAAA"}, {"cipher": "AES-128-CBC"}, {"kdf": "MD5"}, {"data": 5}):
            with self.assertRaises(ValueError, msg=str(patch)):
                V.open(dict(doc, **patch), PW)

    def test_password_rules(self):
        for bad in ("", "kurz", None, 12345678, "x" * 129):
            with self.assertRaises(ValueError, msg=str(bad)):
                V.seal(b"x", bad, {}, iterations=V.MIN_ITER)
        V.seal(b"x", "12345678", {}, iterations=V.MIN_ITER)


def make_box(state=None, sending=False):
    """Eine Box aus den echten Speichern in einem Zustandsordner, mit erfundenen Daten für Netz und Bluetooth."""
    d = state or tempfile.mkdtemp()
    os.makedirs(d, exist_ok=True)
    box = type("Box", (), {})()
    box.dir = d
    box.cams = server.CameraStore(os.path.join(d, "cameras.json"), "publish", "", True)
    box.cams.ifaces = lambda: [{"iface": "eth0", "ip": "192.168.1.5"}, {"iface": "usb0", "ip": "192.168.20.2"}]
    box.pipeline = server.PipelineStore(os.path.join(d, "pipeline.json"))
    box.srtla = server.SrtlaStore(os.path.join(d, "srtla.json"))
    box.names = server.DeviceNames(d)
    box.send = server.SendControl(d, box.srtla, box.pipeline, box.cams, demo=True)
    box.send._active = lambda: sending
    box.autostart = server.AutoStart(d, box.send, True)
    box.wifi = server.Wifi(d, False, None, box.names, box.srtla)
    box.wifi.helper_call = mock.Mock(side_effect=AssertionError("kein Helfer im Test"))
    box.dji = server.DjiService(d, box.cams, "publish", 1935, True)
    box.t = server.SettingsTransfer(d, box.cams, box.pipeline, box.srtla, box.autostart, box.names, box.dji, box.wifi, box.send, True)
    return box


def fill(box):
    """Eine eingerichtete Box: drei Kameras, Bild in Bild, zwei SRTLA-Server, Namen, Hotspot, eine DJI-Kamera."""
    box.cams.add("Kamera vorn", "cam-vorn", "main")
    box.cams.add("Handy Test", "cam-handy", "pip")
    box.cams.add("Drohne Test", "cam-drohne", "extra")
    box.cams.add("Action Test", "dji-001122", "extra")                                       # eine DJI-Kamera steht auch in der Kameraliste
    box.cams.update(box.cams.cams[1]["id"], iface="usb0")
    box.pipeline.set({"type": "pip", "main": "cam-vorn", "pip": "cam-handy", "pip2": "cam-drohne", "corner": 3, "corner2": 2, "size_pct": 30,
                      "swap_cams": 2, "main_delay_ms": 700, "audio": "pip",
                      "styles": {"1": {"border": {"enabled": True, "width": 4, "color": "#ff0000", "opacity": 100}}}},
                     [c["key"] for c in box.cams.cams])
    box.srtla.add({"name": "Relais Nord", "host": "relais-nord.example.org", "port": 5000, "streamid": SID})
    box.srtla.add({"name": "Relais Süd", "host": "relais-sued.example.org", "port": 5001, "streamid": ""})
    box.srtla.select(box.srtla.data["servers"][1]["id"])
    with mock.patch.object(server, "iface_ips", lambda: [{"iface": "eth0", "ip": "192.168.1.5"}, {"iface": "usb0", "ip": "192.168.20.2"}]):
        box.srtla.set_settings({"min_kbps": 500, "max_kbps": 9000, "latency_ms": 3000, "uplinks": ["eth0", "usb0"], "spread": "all"}, ["eth0", "usb0"])
    box.autostart.set_enabled(True)
    box.names.set("usb:0bda:c811", "Mein WLAN-Stick")
    with open(os.path.join(box.dir, "hotspot.json"), "w") as f:
        json.dump({"wlan1": {"ssid": "Box-Test", "password": HS_PW, "band": "a", "channel": 36}}, f)
    with open(os.path.join(box.dir, "dji-cameras.json"), "w") as f:
        json.dump({"cameras": {"AA:BB:CC:00:11:22": {"name": "Action Test", "model": "Osmo Action 4", "kind": "action4", "wifi_ifname": "eth0", "rtmp_key": "dji-001122",
                                                      "autoconnect": True, "resolution": "1080p", "fps": 30, "bitrate": 8000, "stabilization": "off",
                                                      "ssid": "KameraNetz", "password": DJI_PW, "saved": [{"ssid": "KameraNetz", "password": DJI_PW}]}}}, f)
    return box


WIFI = {"networks": [{"ssid": "Heimnetz", "hidden": False, "open": False, "password": WIFI_PW}, {"ssid": "Gast", "hidden": False, "open": True, "password": ""}],
        "skipped": [{"ssid": "Firma", "why": "Unternehmens-WLAN"}]}


def export_with_wifi(box, secrets_on, password=None):
    box.wifi.export_saved = lambda s: json.loads(json.dumps(WIFI))
    return box.t.export(secrets_on, password)


class Export(unittest.TestCase):
    def setUp(self):
        self.box = fill(make_box())

    def test_passwords_only_with_encryption(self):
        with self.assertRaisesRegex(ValueError, "immer verschlüsselt"):
            self.box.t.export(True, None)
        with self.assertRaises(ValueError):
            self.box.t.export(True, "kurz")

    def test_plain_export_without_passwords_has_none(self):
        out = export_with_wifi(self.box, False)
        text = json.dumps(out["document"], ensure_ascii=False)
        self.assertFalse(out["encrypted"])
        for secret in (SID, WIFI_PW, HS_PW, DJI_PW):
            self.assertNotIn(secret, text)
        doc = out["document"]
        self.assertFalse(doc["secrets"])
        self.assertEqual([s["name"] for s in doc["srtla"]["servers"]], ["Relais Nord", "Relais Süd"])
        self.assertNotIn("streamid", doc["srtla"]["servers"][0])
        self.assertTrue(all("password" not in n for n in doc["wifi"]["networks"]))

    def test_encrypted_export_hides_everything(self):
        out = export_with_wifi(self.box, True, PW)
        text = json.dumps(out["document"], ensure_ascii=False)
        self.assertTrue(out["encrypted"])
        for secret in (SID, WIFI_PW, HS_PW, DJI_PW, "Relais", "Kamera vorn", "Heimnetz"):
            self.assertNotIn(secret, text)
        self.assertTrue(out["document"]["secrets"])
        self.assertEqual(set(out["document"]), {"format", "version", "created", "box_version", "secrets", "encrypted", "cipher", "kdf", "iterations", "salt", "nonce", "data", "mac"})

    def test_what_is_in_and_what_is_never_in_it(self):
        doc = export_with_wifi(self.box, False)["document"]
        self.assertEqual({"cameras", "pipeline", "srtla", "autostart", "names", "dji", "hotspots", "wifi"}, set(doc) - {"format", "version", "created", "box_version", "secrets"})
        text = json.dumps(doc).lower()
        for never in ("sessions", "ssh", "token", "login", "pass_hash", "bela"):
            self.assertNotIn('"%s' % never, text)
        self.assertEqual([c["key"] for c in doc["cameras"]], ["cam-vorn", "cam-handy", "cam-drohne", "dji-001122"])
        self.assertEqual(doc["cameras"][1]["iface"], "usb0")
        self.assertEqual(doc["srtla"]["selected"], 1)
        self.assertEqual(doc["srtla"]["settings"]["spread"], "all")
        self.assertEqual(doc["names"], {"usb:0bda:c811": "Mein WLAN-Stick"})
        self.assertEqual(doc["dji"][0]["addr"], "AA:BB:CC:00:11:22")
        self.assertEqual(doc["pipeline"]["swap_cams"], 2)

    def test_wifi_failure_is_a_note_not_an_error(self):
        self.box.wifi.export_saved = mock.Mock(side_effect=RuntimeError("Der WLAN-Helfer ist nicht installiert"))
        out = self.box.t.export(False, None)
        self.assertNotIn("wifi", out["document"])
        self.assertIn("nicht installiert", " ".join(out["notes"]))

    def test_skipped_networks_are_reported(self):
        out = export_with_wifi(self.box, False)
        self.assertIn("Firma", " ".join(out["notes"]))


class RoundTrip(unittest.TestCase):
    def setUp(self):
        self.a = fill(make_box())
        self.out = export_with_wifi(self.a, True, PW)["document"]

    def fresh(self, **kw):
        b = make_box(**kw)
        b.wifi.import_saved = mock.Mock(return_value="2 WLAN-Netze eingespielt")
        return b

    def test_progress_is_reported_part_by_part_and_ends_done(self):
        """Fortschrittsanzeige beim Einspielen: Der Server meldet, welcher Teil dran ist (Wunsch des Nutzers: nicht denken, dass nichts passiert)."""
        b = self.fresh()
        self.assertEqual(b.t.progress, {"state": "idle"})
        seen = []
        orig = b.t._apply_names
        b.t._apply_names = lambda x: (seen.append(dict(b.t.progress)), orig(x))[1]
        res = b.t.apply(self.out, PW, ["names", "autostart", "hotspots"])
        self.assertEqual(len(res["results"]), 3)
        self.assertEqual(seen[0]["state"], "running")
        self.assertEqual((seen[0]["total"], seen[0]["label"]), (3, "Namen (Verbindungen, WLAN- und Bluetooth-Sticks)"))
        self.assertEqual(b.t.progress, {"state": "done", "done": 3, "total": 3, "label": ""})

    def test_progress_ends_even_if_a_part_blows_up(self):
        b = self.fresh()
        b.t._apply_names = mock.Mock(side_effect=RuntimeError("kaputt"))
        res = b.t.apply(self.out, PW, ["names"])
        self.assertFalse(res["results"][0]["ok"])
        self.assertEqual(b.t.progress["state"], "done")

    def test_all_parts_arrive_identically(self):
        b = self.fresh()
        res = b.t.apply(self.out, PW)
        self.assertTrue(all(r["ok"] for r in res["results"]), res)
        self.assertEqual([c["name"] for c in b.cams.cams], ["Kamera vorn", "Handy Test", "Drohne Test", "Action Test"])
        self.assertEqual([(c["key"], c["role"], c.get("iface")) for c in b.cams.cams],
                         [("cam-vorn", "main", None), ("cam-handy", "pip", "usb0"), ("cam-drohne", "extra", None), ("dji-001122", "extra", None)])
        for k in ("type", "main", "pip", "pip2", "corner", "size_pct", "swap_cams", "main_delay_ms", "audio"):
            self.assertEqual(b.pipeline.cfg[k], self.a.pipeline.cfg[k], k)
        self.assertEqual(b.pipeline.cfg["styles"]["1"]["border"]["color"], "#ff0000")
        self.assertEqual([(s["name"], s["host"], s["port"], s["streamid"]) for s in b.srtla.data["servers"]],
                         [(s["name"], s["host"], s["port"], s["streamid"]) for s in self.a.srtla.data["servers"]])
        self.assertEqual(b.srtla.data["selected"], b.srtla.data["servers"][1]["id"])
        self.assertEqual(b.srtla.data["settings"]["spread"], "all")
        self.assertTrue(b.autostart.enabled())
        self.assertEqual(b.names._all(), {"usb:0bda:c811": "Mein WLAN-Stick"})
        self.assertEqual(b.wifi.hotspots()["wlan1"], {"ssid": "Box-Test", "password": HS_PW, "band": "a", "channel": 36})
        cam = b.dji.fake["cameras"]["AA:BB:CC:00:11:22"]
        self.assertEqual((cam["name"], cam["ssid"], cam["password"], cam["resolution"], cam["bitrate"]), ("Action Test", "KameraNetz", DJI_PW, "1080p", 8000))
        b.wifi.import_saved.assert_called_once()
        self.assertEqual([n["ssid"] for n in b.wifi.import_saved.call_args[0][0]], ["Heimnetz", "Gast"])

    def test_hotspot_file_is_private(self):
        b = self.fresh()
        b.t.apply(self.out, PW, ["hotspots"])
        self.assertEqual(os.stat(os.path.join(b.dir, "hotspot.json")).st_mode & 0o777, 0o600)

    def test_only_the_chosen_parts_are_applied(self):
        b = self.fresh()
        res = b.t.apply(self.out, PW, ["names", "autostart"])
        self.assertEqual([r["id"] for r in res["results"]], ["autostart", "names"])
        self.assertEqual(b.cams.cams, [])
        self.assertEqual(b.srtla.data["servers"], [])
        with self.assertRaisesRegex(ValueError, "Nichts zum Einspielen"):
            b.t.apply(self.out, PW, ["gibt-es-nicht"])

    def test_wrong_password_applies_nothing(self):
        b = self.fresh()
        with self.assertRaisesRegex(ValueError, "falsch oder"):
            b.t.apply(self.out, "ein-anderes-Passwort")
        self.assertEqual(b.cams.cams, [])
        self.assertFalse(os.path.exists(b.t.backup_path))

    def test_refused_while_sending(self):
        b = self.fresh(sending=True)
        with self.assertRaisesRegex(ValueError, "gesendet"):
            b.t.apply(self.out, PW)
        with self.assertRaisesRegex(ValueError, "gesendet"):
            b.t.restore()
        self.assertEqual(b.cams.cams, [])

    def test_state_before_is_saved_and_can_be_restored(self):
        b = fill(make_box())
        b.wifi.import_saved = mock.Mock(return_value="")
        other = fill(make_box())
        other.cams.cams[0]["name"] = "Andere Kamera"
        other.srtla.data["servers"][0]["name"] = "Anderer Server"
        doc = export_with_wifi(other, True, PW)["document"]
        b.t.apply(doc, PW, ["cameras", "srtla"])
        self.assertEqual(b.cams.cams[0]["name"], "Andere Kamera")
        self.assertEqual(os.stat(b.t.backup_path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(b.t.backup_dir).st_mode & 0o777, 0o700)
        res = b.t.restore()
        self.assertTrue(all(r["ok"] for r in res["results"]), res)
        self.assertEqual(b.cams.cams[0]["name"], "Kamera vorn")
        self.assertEqual(b.srtla.data["servers"][0]["name"], "Relais Nord")
        self.assertNotIn("wifi", [r["id"] for r in res["results"]])                         # die WLAN-Netze führt der Helfer, sie stehen nicht im Stand davor

    def test_restore_without_backup(self):
        with self.assertRaisesRegex(ValueError, "keinen gesicherten Stand"):
            make_box().t.restore()

    def test_stream_id_is_kept_when_the_file_has_none(self):
        plain = export_with_wifi(self.a, False)["document"]
        b = fill(make_box())
        b.wifi.import_saved = mock.Mock(return_value="")
        b.t.apply(plain, None, ["srtla"])
        self.assertEqual(b.srtla.data["servers"][0]["streamid"], SID)                       # gleicher Name, Adresse und Port: die Kennung bleibt
        fresh = self.fresh()
        fresh.t.apply(plain, None, ["srtla"])
        self.assertEqual(fresh.srtla.data["servers"][0]["streamid"], "")

    def test_hotspot_without_password_in_file_keeps_the_stored_one_or_is_skipped(self):
        plain = export_with_wifi(self.a, False)["document"]
        b = fill(make_box())
        b.t.apply(plain, None, ["hotspots"])
        self.assertEqual(b.wifi.hotspots()["wlan1"]["password"], HS_PW)
        fresh = self.fresh()
        res = fresh.t.apply(plain, None, ["hotspots"])
        self.assertEqual(fresh.wifi.hotspots(), {})
        self.assertIn("ohne Passwort ausgelassen", res["results"][0]["message"])

    def test_networks_that_do_not_exist_here_are_left_out(self):
        doc = export_with_wifi(self.a, True, PW)
        b = self.fresh()
        with mock.patch.object(server, "iface_ips", lambda: [{"iface": "eth0", "ip": "192.168.1.5"}]):
            b.t.apply(doc["document"], PW, ["srtla"])
        self.assertEqual(b.srtla.data["settings"]["uplinks"], ["eth0"])

    def test_camera_connection_that_does_not_exist_here_is_dropped(self):
        b = self.fresh()
        b.cams.ifaces = lambda: [{"iface": "eth0", "ip": "192.168.1.5"}]
        b.t.apply(self.out, PW, ["cameras"])
        self.assertNotIn("iface", b.cams.cams[1])

    def test_existing_ids_are_kept_and_missing_cameras_are_reported(self):
        b = fill(make_box())
        before = {c["key"]: c["id"] for c in b.cams.cams}
        b.cams.add("Überzählig", "cam-extra", "extra")
        b.wifi.import_saved = mock.Mock(return_value="")
        res = b.t.apply(self.out, PW, ["cameras"])
        self.assertEqual({c["key"]: c["id"] for c in b.cams.cams}, before)
        self.assertIn("1 nicht in der Sicherung entfernt", res["results"][0]["message"])

    def test_one_failing_part_does_not_stop_the_others(self):
        b = self.fresh()
        b.djisvc = None
        b.dji.command = mock.Mock(side_effect=RuntimeError("Der Bluetooth-Dienst (pipbox-dji) läuft nicht"))
        res = b.t.apply(self.out, PW)
        by = {r["id"]: r for r in res["results"]}
        self.assertFalse(by["dji"]["ok"])
        self.assertIn("Bluetooth-Dienst", by["dji"]["message"])
        self.assertTrue(by["cameras"]["ok"] and by["srtla"]["ok"] and by["wifi"]["ok"])


class UiParts(unittest.TestCase):
    """Einstellungen der Oberfläche in der Sicherung: HDMI-Eingang, Akku-Warnung im Twitch-Chat (ohne Token), Netzwerk der Kameras."""
    IFACES = [{"iface": "eth0", "ip": "192.168.1.5"}, {"iface": "usb0", "ip": "192.168.20.2"}]
    TOKEN = "geheimer-token-" + "x" * 12

    def attach(self, box):
        box.twitch = server.TwitchStore(os.path.join(box.dir, "twitch.json"))
        box.netchoice = server.NetChoice(os.path.join(box.dir, "camera-net.json"))
        box.hdmi = server.HdmiService(box.dir, box.cams, True)
        box.t.twitch, box.t.netchoice, box.t.hdmi = box.twitch, box.netchoice, box.hdmi
        return box

    def setUp(self):
        p = mock.patch.object(server, "iface_ips", lambda: self.IFACES)
        p.start()
        self.addCleanup(p.stop)
        self.box = self.attach(fill(make_box()))
        self.box.hdmi.set({"enabled": True, "bitrate": 6000, "fps": 25, "audio": "none"})
        self.box.twitch.set({"channel": "MeinKanal", "login": "MeinBot", "token": self.TOKEN, "threshold": 15, "message": "Akku leer: {Kamera}", "only_live": False, "enabled": True})
        self.box.netchoice.select("usb0")
        self.out = export_with_wifi(self.box, False)["document"]

    def fresh(self):
        return self.attach(make_box())

    def test_export_has_the_parts_and_never_the_twitch_token(self):
        d = self.out
        self.assertNotIn("layout", d)                                                    # Reihenfolge und Ausblenden gelten je Gerät und stehen nicht auf der Box
        self.assertEqual(d["hdmi"], {"enabled": True, "bitrate": 6000, "fps": 25, "audio": "none", "source": "hdmi", "usb_format": "auto"})
        self.assertEqual(d["camnet"], {"iface": "usb0"})
        self.assertEqual(d["twitch"], {"enabled": True, "channel": "meinkanal", "login": "meinbot", "threshold": 15, "message": "Akku leer: {Kamera}", "only_live": False})
        self.assertNotIn(self.TOKEN, json.dumps(d))
        self.assertNotIn("token", json.dumps(d["twitch"]))

    def test_nothing_is_exported_that_the_box_does_not_have(self):
        box = make_box()                                                                 # ohne Netzwerkwahl: kein leerer Teil
        box.netchoice = server.NetChoice(os.path.join(box.dir, "camera-net.json"))
        box.t.netchoice = box.netchoice
        d = box.t.make_document(False)
        self.assertNotIn("camnet", d)

    def test_import_restores_all_parts(self):
        b = self.fresh()
        b.twitch.set({"login": "meinbot", "token": self.TOKEN})                         # auf dieser Box gibt es ein Token (Anmeldung bleibt dort)
        res = b.t.apply(self.out, None, ["hdmi", "twitch", "camnet"])
        self.assertTrue(all(r["ok"] for r in res["results"]), res)
        self.assertEqual({k: b.hdmi.status()["settings"][k] for k in ("enabled", "bitrate", "fps", "audio")}, {"enabled": True, "bitrate": 6000, "fps": 25, "audio": "none"})
        t = b.twitch.data
        self.assertEqual((t["enabled"], t["channel"], t["threshold"], t["message"], t["only_live"], t["token"]), (True, "meinkanal", 15, "Akku leer: {Kamera}", False, self.TOKEN))
        self.assertEqual(b.netchoice.iface, "usb0")

    def test_battery_warning_stays_off_without_token_or_login(self):
        b = self.fresh()
        res = b.t.apply(self.out, None, ["twitch"])
        self.assertTrue(res["results"][0]["ok"])
        self.assertIn("bleibt aber ausgeschaltet", res["results"][0]["message"])
        self.assertFalse(b.twitch.data["enabled"])
        self.assertEqual(b.twitch.data["channel"], "meinkanal")                          # die übrigen Werte sind trotzdem da
        self.assertEqual(b.twitch.data["token"], "")

    def test_unknown_network_for_cameras_is_skipped(self):
        b = self.fresh()
        doc = dict(self.out, camnet={"iface": "wlan9"})
        res = b.t.apply(doc, None, ["camnet"])
        self.assertTrue(res["results"][0]["ok"])
        self.assertIn("gibt es hier nicht", res["results"][0]["message"])
        self.assertIsNone(b.netchoice.iface)

    def test_bad_values_are_refused_per_part(self):
        b = self.fresh()
        bad = dict(self.out, hdmi={"enabled": True, "bitrate": 1, "fps": 99, "audio": "none"},
                   twitch=dict(self.out["twitch"], threshold=99), camnet={"iface": "../etc"})
        p = {s["id"]: s for s in b.t.preview(bad, None)["sections"]}
        for sid in ("hdmi", "twitch", "camnet"):
            self.assertFalse(p[sid]["ok"], sid)
        with self.assertRaisesRegex(ValueError, "Nichts zum Einspielen"):                   # nichts Gültiges übrig: es wird nichts verändert
            b.t.apply(bad, None, ["hdmi", "twitch", "camnet"])
        self.assertEqual(b.twitch.data["threshold"], 10)

    def test_old_files_without_these_parts_still_work(self):
        b = self.fresh()
        old = {k: v for k, v in self.out.items() if k not in ("hdmi", "twitch", "camnet")}
        ids = [s["id"] for s in b.t.preview(old, None)["sections"]]
        self.assertTrue({"cameras", "pipeline", "srtla"} <= set(ids))
        self.assertFalse({"hdmi", "twitch", "camnet"} & set(ids))

    def test_old_files_with_the_menu_options_ignore_them(self):
        """Frühere Sicherungen enthielten die Menüeinstellung der Oberfläche; sie gilt jetzt je Gerät und wird nicht mehr eingespielt (und nicht angezeigt)."""
        b = self.fresh()
        old = dict(self.out, layout={"order": ["c_chat"], "hidden": ["rcard"]})
        ids = [s["id"] for s in b.t.preview(old, None)["sections"]]
        self.assertNotIn("layout", ids)
        res = b.t.apply(old, None, None)
        self.assertNotIn("layout", [r["id"] for r in res["results"]])
        self.assertNotIn("layout", dict(server.SETTINGS_SECTIONS))


class RenamedThings(unittest.TestCase):
    """Selbst vergebene Namen gehen mit: Namen der Verbindungen (Netzwerke) und umbenannte DJI-Kameras."""
    def setUp(self):
        self.box = fill(make_box())
        self.box.names.set("net:eth0", "Kabel zuhause")
        self.box.names.set("net:usb0", "Handy per USB")
        cam = next(c for c in self.box.cams.cams if c["key"] == "dji-001122")
        self.box.cams.update(cam["id"], name="Mein Osmo")                                   # in der Kameraliste umbenannt
        self.doc = export_with_wifi(self.box, False)["document"]

    def fresh(self):
        return make_box()

    def test_connection_names_are_in_the_file(self):
        self.assertEqual(self.doc["names"]["net:eth0"], "Kabel zuhause")
        self.assertEqual(self.doc["names"]["net:usb0"], "Handy per USB")

    def test_connection_names_come_back(self):
        b = self.fresh()
        res = b.t.apply(self.doc, None, ["names"])
        self.assertTrue(res["results"][0]["ok"])
        self.assertEqual(b.names.conn_names(), {"eth0": "Kabel zuhause", "usb0": "Handy per USB"})

    def test_renamed_dji_camera_keeps_its_name_after_the_import(self):
        b = self.fresh()
        orig = b.dji.command

        def like_the_dji_service(req):                                                      # der DJI-Dienst benennt die Kamera in der Liste nach seinem eigenen Namen
            if req.get("cmd") == "update" and req.get("name"):
                for c in b.cams.cams:
                    if c["key"] == "dji-001122":
                        c["name"] = req["name"]
            return orig(req)
        b.dji.command = like_the_dji_service
        self.assertEqual(self.doc["dji"][0]["name"], "Action Test")                         # im DJI-Teil steht noch der alte Name
        res = b.t.apply(self.doc, None, ["cameras", "dji"])
        self.assertTrue(all(r["ok"] for r in res["results"]), res)
        self.assertEqual({c["key"]: c["name"] for c in b.cams.cams}["dji-001122"], "Mein Osmo")   # es gilt der Name der Kameraliste

    def test_names_of_the_camera_list_are_all_restored(self):
        b = self.fresh()
        b.t.apply(self.doc, None, ["cameras", "dji"])
        self.assertEqual({c["key"]: c["name"] for c in b.cams.cams}, {c["key"]: c["name"] for c in self.box.cams.cams})


class Validation(unittest.TestCase):
    def setUp(self):
        self.box = fill(make_box())
        self.doc = export_with_wifi(self.box, False)["document"]

    def preview(self, doc):
        return self.box.t.preview(doc, None)

    def section(self, doc, sid):
        return {s["id"]: s for s in self.preview(doc)["sections"]}[sid]

    def test_good_file_shows_every_part(self):
        p = self.preview(self.doc)
        self.assertEqual([(s["id"], s["ok"]) for s in p["sections"]], [(i, True) for i in ("cameras", "pipeline", "srtla", "autostart", "names", "dji", "hotspots", "wifi")])
        self.assertEqual(p["sections"][0]["count"], 4)
        self.assertFalse(p["encrypted"])
        self.assertFalse(p["has_backup"])

    def test_not_one_of_ours(self):
        for raw in (None, [], "text", {}, {"format": "anderes"}, {"format": server.SETTINGS_FORMAT}, {"format": server.SETTINGS_FORMAT, "version": "1"},
                    {"format": server.SETTINGS_FORMAT, "version": True}, {"format": server.SETTINGS_FORMAT, "version": 0}):
            with self.assertRaises(ValueError, msg=str(raw)):
                self.box.t.preview(raw, None)

    def test_newer_version_is_refused_with_a_hint(self):
        with self.assertRaisesRegex(ValueError, "neueren Version"):
            self.box.t.preview(dict(self.doc, version=2), None)

    def test_secrets_flag_without_encryption_is_refused(self):
        with self.assertRaisesRegex(ValueError, "verschlüsselt sein"):
            self.box.t.preview(dict(self.doc, secrets=True), None)

    def test_encrypted_needs_the_password(self):
        enc = export_with_wifi(self.box, True, PW)["document"]
        with self.assertRaisesRegex(ValueError, "Passwort eingeben"):
            self.box.t.preview(enc, None)
        self.assertTrue(self.box.t.preview(enc, PW)["encrypted"])
        self.assertTrue(self.box.t.preview(enc, PW)["secrets"])

    def test_inner_document_must_match_the_envelope(self):
        inner = dict(self.doc, format="anderes")
        env = V.seal(json.dumps(inner).encode(), PW, {"format": server.SETTINGS_FORMAT, "version": 1}, iterations=V.MIN_ITER)
        with self.assertRaisesRegex(ValueError, "passt nicht"):
            self.box.t.preview(env, PW)
        env = V.seal(b"kein json", PW, {"format": server.SETTINGS_FORMAT, "version": 1}, iterations=V.MIN_ITER)
        with self.assertRaisesRegex(ValueError, "beschädigt"):
            self.box.t.preview(env, PW)

    def test_huge_file_is_refused(self):
        with self.assertRaisesRegex(ValueError, "zu groß"):
            self.box.t.preview(dict(self.doc, notizen="x" * 300000), None)

    def test_cameras(self):
        bad = ([{"name": "x", "key": "../etc", "role": "main"}], [{"name": "x", "key": "A B", "role": "main"}], [{"name": "", "key": "a", "role": "main"}],
               [{"name": "x" * 41, "key": "a", "role": "main"}], [{"name": "x", "key": "a"}, {"name": "y", "key": "a"}], "keine liste", [1],
               [{"name": "x", "key": "k%d" % i, "role": "extra"} for i in range(41)], [{"name": "a\nb", "key": "a", "role": "extra"}])
        for cams in bad:
            self.assertFalse(self.section(dict(self.doc, cameras=cams), "cameras")["ok"], str(cams)[:60])

    def test_duplicate_roles_and_names_are_fixed_not_refused(self):
        cams = [{"name": "Gleich", "key": "cam-a", "role": "main"}, {"name": "gleich", "key": "cam-b", "role": "main"}]
        s = self.section(dict(self.doc, cameras=cams), "cameras")
        self.assertTrue(s["ok"])
        self.assertIn("Rolle", s["note"])
        b = make_box()
        b.t.apply(dict(self.doc, cameras=cams), None, ["cameras"])
        self.assertEqual([c["role"] for c in b.cams.cams], ["main", "extra"])
        self.assertNotEqual(b.cams.cams[0]["name"].casefold(), b.cams.cams[1]["name"].casefold())

    def test_unknown_role_becomes_extra(self):
        b = make_box()
        b.t.apply(dict(self.doc, cameras=[{"name": "A", "key": "cam-a", "role": "chef"}]), None, ["cameras"])
        self.assertEqual(b.cams.cams[0]["role"], "extra")

    def test_pipeline_needs_cameras_that_exist(self):
        doc = dict(self.doc, pipeline=dict(self.doc["pipeline"], main="gibt-es-nicht"))
        self.assertFalse(self.section(doc, "pipeline")["ok"])
        doc = dict(self.doc, pipeline=dict(self.doc["pipeline"], size_pct=9999))
        self.assertFalse(self.section(doc, "pipeline")["ok"])
        self.assertFalse(self.section(dict(self.doc, pipeline="text"), "pipeline")["ok"])
        self.assertFalse(self.section(dict(self.doc, pipeline=dict(self.doc["pipeline"], styles="x")), "pipeline")["ok"])

    def test_pipeline_is_checked_against_the_imported_cameras(self):
        doc = dict(self.doc)
        doc["cameras"] = [c for c in doc["cameras"] if c["key"] != "cam-handy"]
        self.assertFalse(self.section(doc, "pipeline")["ok"])                                # das kleine Bild fehlt in der Sicherung
        doc.pop("cameras")
        self.assertTrue(self.section(doc, "pipeline")["ok"])                                 # ohne Kamerateil zählen die Kameras dieser Box

    def test_failed_part_is_reported_and_the_rest_goes_through(self):
        b = make_box()
        b.wifi.import_saved = mock.Mock(return_value="")
        doc = dict(self.doc, srtla={"servers": [{"name": "x", "host": "böse host", "port": 1}]})
        res = b.t.apply(doc, None)
        by = {r["id"]: r for r in res["results"]}
        self.assertFalse(by["srtla"]["ok"])
        self.assertIn("Nicht eingespielt", by["srtla"]["message"])
        self.assertTrue(by["cameras"]["ok"])
        self.assertEqual(b.srtla.data["servers"], [])

    def test_srtla(self):
        s = lambda **kw: self.section(dict(self.doc, srtla=dict(self.doc["srtla"], **kw)), "srtla")["ok"]
        self.assertTrue(s())
        self.assertFalse(s(servers=[{"name": "x", "host": "rtmp://x", "port": 5000}]))
        self.assertFalse(s(servers=[{"name": "x", "host": "a.example", "port": 0}]))
        self.assertFalse(s(servers=[{"name": "x", "host": "a.example", "port": "x"}]))
        self.assertFalse(s(servers=[{"name": "", "host": "a.example", "port": 1}]))
        self.assertFalse(s(servers=[{"name": "x", "host": "a.example", "port": 1, "streamid": "ä" * 3}]))
        self.assertFalse(s(servers=[1]))
        self.assertFalse(s(servers=[{"name": "s", "host": "a.example", "port": 1}] * 31))
        self.assertFalse(s(settings={"min_kbps": 5000, "max_kbps": 1000}))
        self.assertFalse(s(settings={"min_kbps": "x"}))
        self.assertFalse(s(settings={"latency_ms": 5}))
        self.assertFalse(s(settings={"spread": "alles"}))
        self.assertFalse(s(settings={"uplinks": ["eth0; rm -rf /"]}))
        self.assertFalse(s(settings={"uplinks": "eth0"}))
        self.assertFalse(s(servers="x"))

    def test_srtla_selection_out_of_range_falls_back_to_the_first(self):
        b = make_box()
        for sel in (99, -1, True, "1"):
            b.t.apply(dict(self.doc, srtla=dict(self.doc["srtla"], selected=sel)), None, ["srtla"])
            self.assertEqual(b.srtla.data["selected"], b.srtla.data["servers"][0]["id"], str(sel))

    def test_names(self):
        for names in ({"usb:zzzz:0000": "x"}, {"usb:0bda:c811": 5}, {"usb:0bda:c811": "x" * 41}, {"usb:0bda:c811": "a\x07b"}, [], {"../x": "y"}):
            self.assertFalse(self.section(dict(self.doc, names=names), "names")["ok"], str(names))

    def test_autostart(self):
        for a in ({"enabled": "ja"}, {"enabled": 1}, {}, "an"):
            self.assertFalse(self.section(dict(self.doc, autostart=a), "autostart")["ok"], str(a))

    def test_dji(self):
        base = self.doc["dji"][0]
        bad = (dict(base, addr="kaputt"), dict(base, wifi_ifname="eth0; x"), dict(base, ip="999.1.1.1"), dict(base, ssid="x" * 33), dict(base, password="a\nb"),
               dict(base, fps=True), dict(base, fps=9999), dict(base, bitrate="viel"), dict(base, resolution="$(x)"), dict(base, autoconnect="ja"), dict(base, name="a\x00b"))
        for c in bad:
            self.assertFalse(self.section(dict(self.doc, dji=[c]), "dji")["ok"], str(c)[:80])
        self.assertFalse(self.section(dict(self.doc, dji=[base, base]), "dji")["ok"])
        self.assertTrue(self.section(dict(self.doc, dji=[base]), "dji")["ok"])
        self.assertIn("Bluetooth-Kopplung", self.section(dict(self.doc, dji=[base]), "dji")["note"])

    def test_dji_is_applied_through_the_service_commands_only(self):
        b = make_box()
        calls = []
        real = b.dji.command
        b.dji.command = lambda d: calls.append(dict(d)) or real(d)
        b.t.apply(dict(self.doc), None, ["dji"])
        self.assertEqual([c["cmd"] for c in calls], ["add", "update"])
        self.assertTrue(all(set(c) <= set(server.DJI_FIELDS) | {"cmd"} for c in calls), calls)

    def test_hotspots(self):
        base = {"ssid": "Box-Test", "password": HS_PW, "band": "bg", "channel": 6}
        for h in (dict(base, ssid=""), dict(base, ssid="x" * 33), dict(base, ssid="pipbox-hotspot-wlan1"), dict(base, password="kurz"), dict(base, password="ä" * 10),
                  dict(base, band="x"), dict(base, channel=14), dict(base, channel=True), dict(base, band="a", channel=100), dict(base, ssid=" x ")):
            self.assertFalse(self.section(dict(self.doc, hotspots={"wlan1": h}), "hotspots")["ok"], str(h))
        for iface in ("WLAN1", "../x", "w", "a" * 20):
            self.assertFalse(self.section(dict(self.doc, hotspots={iface: base}), "hotspots")["ok"], iface)
        self.assertFalse(self.section(dict(self.doc, hotspots={"wlan%d" % i: base for i in range(9)}), "hotspots")["ok"])
        self.assertTrue(self.section(dict(self.doc, hotspots={"wlan1": base}), "hotspots")["ok"])

    def test_wifi(self):
        w = lambda nets: self.section(dict(self.doc, wifi={"networks": nets}), "wifi")
        good = {"ssid": "Heimnetz", "password": WIFI_PW, "hidden": False, "open": False}
        self.assertTrue(w([good])["ok"])
        for bad in (dict(good, ssid=""), dict(good, ssid="x" * 33), dict(good, ssid="a\nb"), dict(good, password="kurz"), dict(good, password="ä" * 10), dict(good, ssid=5), 5):
            self.assertFalse(w([bad])["ok"], str(bad))
        self.assertFalse(self.section(dict(self.doc, wifi={"networks": [good] * 51}), "wifi")["ok"])
        self.assertFalse(self.section(dict(self.doc, wifi="x"), "wifi")["ok"])
        self.assertTrue(w([dict(good, password="g" * 64)])["ok"] is False)                    # 64 Zeichen nur als Hex
        self.assertTrue(w([dict(good, password="ab" * 32)])["ok"])

    def test_wifi_without_password_for_a_secured_network_is_skipped_with_a_note(self):
        s = self.section(dict(self.doc, wifi={"networks": [{"ssid": "Heimnetz", "password": "", "open": False}, {"ssid": "Gast", "password": "", "open": True}]}), "wifi")
        self.assertEqual(s["count"], 1)
        self.assertIn("bitte neu verbinden", s["note"])

    def test_hotspot_names_of_this_box_are_never_imported_as_wifi(self):
        s = self.section(dict(self.doc, wifi={"networks": [{"ssid": "pipbox-hotspot-wlan1", "password": WIFI_PW}]}), "wifi")
        self.assertEqual(s["count"], 0)

    def test_empty_file(self):
        with self.assertRaisesRegex(ValueError, "nichts"):
            self.box.t.preview({"format": server.SETTINGS_FORMAT, "version": 1}, None)


class WifiBridge(unittest.TestCase):
    """Die Brücke zum Root-Helfer in server.Wifi (der Helfer selbst: tools/test_wifi_transfer.py)."""
    def make(self):
        d = tempfile.mkdtemp()
        w = server.Wifi(d, False, None, None, None)
        return w, d

    def test_export_reads_the_list_and_removes_the_file(self):
        w, d = self.make()
        path = os.path.join(d, "wifi-export.json")

        def call(req, wait=40):
            self.assertEqual(req, {"action": "export_wifi", "secrets": True})
            with open(path, "w") as f:
                json.dump(WIFI, f)
            return {"state": "done", "message": "ok"}
        w.helper_call = call
        out = w.export_saved(True)
        self.assertEqual(out["networks"][0]["ssid"], "Heimnetz")
        self.assertEqual(out["skipped"][0]["ssid"], "Firma")
        self.assertFalse(os.path.exists(path))

    def test_export_error_of_the_helper_and_missing_file(self):
        w, d = self.make()
        w.helper_call = lambda req, wait=40: {"state": "error", "message": "nmcli fehlt"}
        with self.assertRaisesRegex(RuntimeError, "nmcli fehlt"):
            w.export_saved(False)
        w.helper_call = lambda req, wait=40: {"state": "done", "message": ""}
        with self.assertRaises(RuntimeError):
            w.export_saved(False)

    def test_export_refuses_a_symlink_and_garbage(self):
        w, d = self.make()
        path = os.path.join(d, "wifi-export.json")
        target = os.path.join(d, "ziel")
        open(target, "w").write("{}")
        os.symlink(target, path)
        w.helper_call = lambda req, wait=40: {"state": "done", "message": ""}
        with self.assertRaises(RuntimeError):
            w.export_saved(False)
        os.unlink(path)
        for junk in ("kein json", "[]", '{"networks": 5}'):
            with open(path, "w") as f:
                f.write(junk)
            with self.assertRaises(RuntimeError, msg=junk):
                w.export_saved(False)
            self.assertFalse(os.path.exists(path), junk)

    def test_import_goes_through_the_helper(self):
        w, d = self.make()
        got = []
        w.helper_call = lambda req, wait=40: got.append(req) or {"state": "done", "message": "2 WLAN-Netze angelegt"}
        self.assertEqual(w.import_saved([{"ssid": "A", "password": "", "hidden": False, "open": True}]), "2 WLAN-Netze angelegt")
        self.assertEqual(got[0]["action"], "import_wifi")
        w.helper_call = lambda req, wait=40: {"state": "error", "message": "Fehler X"}
        with self.assertRaisesRegex(RuntimeError, "Fehler X"):
            w.import_saved([{"ssid": "A"}])
        self.assertEqual(w.import_saved([]), "Keine WLAN-Netze zum Einspielen")

    def test_helper_call_waits_for_its_own_mark(self):
        w, d = self.make()
        w.STATUS = os.path.join(d, "status.json")
        w.status = lambda: {"helper_installed": True, "state": "idle"}
        seen = {}

        def fake_sleep(_):
            req = json.load(open(w.req))
            seen["req"] = req
            json.dump({"mark": "fremde-marke", "state": "done"} if "n" not in seen else {"mark": req["mark"], "state": "done", "message": "fertig"}, open(w.STATUS, "w"))
            seen["n"] = seen.get("n", 0) + 1
        with mock.patch.object(server.time, "sleep", fake_sleep):
            h = w.helper_call({"action": "export_wifi"})
        self.assertEqual(h["message"], "fertig")
        self.assertEqual(seen["n"], 2)                                                        # der fremde Zustand wurde nicht für die eigene Antwort gehalten
        self.assertEqual(os.stat(w.req).st_mode & 0o777, 0o600)

    def test_helper_call_refuses_without_helper_or_while_busy(self):
        w, d = self.make()
        w.status = lambda: {"helper_installed": False, "state": "idle"}
        with self.assertRaisesRegex(RuntimeError, "nicht installiert"):
            w.helper_call({"action": "export_wifi"})
        w.status = lambda: {"helper_installed": True, "state": "working", "time": server.time.time()}
        with self.assertRaisesRegex(ValueError, "schon eine"):
            w.helper_call({"action": "export_wifi"})

    def test_helper_call_times_out(self):
        w, d = self.make()
        w.STATUS = os.path.join(d, "gibt-es-nicht.json")
        w.status = lambda: {"helper_installed": True, "state": "idle"}
        with mock.patch.object(server.time, "sleep", lambda _: None):
            with self.assertRaisesRegex(RuntimeError, "nicht rechtzeitig"):
                w.helper_call({"action": "export_wifi"}, wait=0.01)


class Endpoints(unittest.TestCase):
    def handler(self, path, body, authed=True, length=None):
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
        raw = json.dumps(body).encode()
        h.headers = {"Content-Length": str(len(raw) if length is None else length)}
        h.rfile = type("R", (), {"read": lambda self, n: raw[:n]})()
        h.client_address = ("127.0.0.1", 1)
        return h

    def setUp(self):
        self.box = fill(make_box())
        self.box.wifi.export_saved = lambda s: json.loads(json.dumps(WIFI))
        server.Handler.transfer = self.box.t
        server.Handler.send = self.box.send

    def call(self, path, body, **kw):
        h = self.handler(path, body, **kw)
        h.do_POST()
        return h.sent[0], json.loads(h.out.data)

    def test_export_preview_import_over_http(self):
        code, out = self.call("/api/settings/export", {"secrets": True, "password": PW})
        self.assertEqual((code, out["ok"], out["encrypted"]), (200, True, True))
        code, p = self.call("/api/settings/preview", {"document": out["document"], "password": PW})
        self.assertEqual(code, 200)
        self.assertEqual(len(p["sections"]), 8)
        b = make_box()
        b.wifi.import_saved = lambda n: "ok"
        server.Handler.transfer = b.t
        code, res = self.call("/api/settings/import", {"document": out["document"], "password": PW, "sections": ["cameras", "names"]})
        self.assertEqual(code, 200)
        self.assertEqual([r["id"] for r in res["results"]], ["cameras", "names"])
        self.assertEqual(len(b.cams.cams), 4)

    def test_errors_are_400_with_a_message(self):
        code, out = self.call("/api/settings/export", {"secrets": True})
        self.assertEqual(code, 400)
        self.assertIn("verschlüsselt", out["error"])
        code, out = self.call("/api/settings/preview", {"document": {"format": "x"}})
        self.assertEqual(code, 400)
        code, out = self.call("/api/settings/import", {"document": {}, "sections": "alle"})
        self.assertEqual(code, 400)
        code, out = self.call("/api/settings/export", {"secrets": False, "password": 12345678})
        self.assertEqual(code, 400)
        code, out = self.call("/api/settings/unbekannt", {})
        self.assertEqual(code, 404)

    def test_needs_login(self):
        for path in ("/api/settings/export", "/api/settings/preview", "/api/settings/import", "/api/settings/restore"):
            code, _ = self.call(path, {"secrets": False}, authed=False)
            self.assertEqual(code, 401, path)

    def test_import_body_may_be_large_but_not_huge_and_other_endpoints_stay_small(self):
        big = {"document": {"x": "y" * 100000}}
        code, out = self.call("/api/settings/preview", big)
        self.assertEqual(code, 400)
        self.assertNotIn("zu groß", out["error"])                                             # die Anfrage wurde gelesen, die Datei ist keine Sicherung
        code, out = self.call("/api/settings/preview", {"document": "x"}, length=server.SettingsTransfer.MAX_BODY + 1)
        self.assertEqual((code, out["error"]), (400, "Die Anfrage ist zu groß"))
        code, out = self.call("/api/send", {"action": "stop", "pad": "x" * 5000})
        self.assertEqual((code, out["error"]), (400, "Die Anfrage ist zu groß"))

    def test_get_tells_about_backup_and_sending(self):
        h = self.handler("/api/settings", {})
        h.do_GET()
        self.assertEqual(json.loads(h.out.data), {"has_backup": False, "sending": False})


class Source(unittest.TestCase):
    def test_no_private_values_in_the_tests_or_page(self):
        src = open(os.path.join(HERE, "test_settings.py"), encoding="utf-8").read()
        for needle in ("192.168." + "178", "irl4you_" + "box", "Bitter" + "sweet", "aki" + "nos"):
            self.assertNotIn(needle, src)


if __name__ == "__main__":
    unittest.main()
