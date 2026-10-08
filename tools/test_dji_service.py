"""Tests für den DJI-Dienst (dji_daemon.py) und seine Anbindung in server.py (DjiService). Ohne Bluetooth, ohne Kamera.

Eine nachgestellte Kamera antwortet auf die Nachrichten des Dienstes; so läuft der ganze Ablauf (suchen, verbinden, koppeln,
vorbereiten, WLAN, Stabilisierung, Start, Stop) durch. Nicht getestet (nur mit echter Kamera und echtem BlueZ möglich): das
Verhalten von bleak/BlueZ selbst, Funkreichweite, das Verhalten der echten Kameras.
"""
import asyncio
import json
import os
import re
import socketserver
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.modules.setdefault("dbus", types.ModuleType("dbus"))
import dji  # noqa: E402
import dji_daemon as dd  # noqa: E402
import server  # noqa: E402

with open(os.path.join(HERE, "dji_golden.json")) as _f:
    GOLDEN = json.load(_f)
ADDR = "D0:D0:4B:00:00:01"
ADDR2 = "F0:4F:E2:00:00:02"


def arun(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- Protokoll

class Protocol(unittest.TestCase):
    URL = GOLDEN["url"]
    MODEL_KIND = {"osmoAction3": "action23", "osmoAction4": "action4", "osmoAction5Pro": "action5", "osmo360": "action5",
                  "osmoAction6": "action6", "osmoPocket3": "pocket3", "osmoPocket4": "pocket4"}
    STAB = {"off": "off", "rockSteady": "rocksteady", "rockSteadyPlus": "rocksteadyplus",
            "horizonBalancing": "horizonbalancing", "horizonSteady": "horizonsteady"}

    def test_start_messages_are_byte_identical_to_the_proven_encoding(self):
        n = 0
        for key, hexa in GOLDEN["start"].items():
            model, res, fps, kbps = key.split("|")
            got = dd.build_start_payload(self.MODEL_KIND[model], self.URL, res, int(kbps), int(fps))
            self.assertEqual(got.hex(), hexa, key)
            n += 1
        self.assertGreaterEqual(n, 49)

    def test_configure_messages_are_byte_identical(self):
        for key, hexa in GOLDEN["config"].items():
            model, stab = key.split("|")
            got = dd.build_configure_payload(self.MODEL_KIND[model], self.STAB[stab])
            self.assertEqual(got.hex(), hexa, key)

    def test_pair_and_wifi_messages_are_byte_identical(self):
        pair = dd.Message(dd.T_PAIR, dd.ID_PAIR, dd.TY_PAIR, dd.PAIR_PAYLOAD + dd.pack_string(dd.PAIR_PIN)).encode()
        self.assertEqual(pair.hex(), GOLDEN["pair_msg"])
        wifi = dd.Message(dd.T_WIFI, dd.ID_WIFI, dd.TY_WIFI, dd.pack_string("KameraNetz") + dd.pack_string("geheim123")).encode()
        self.assertEqual(wifi.hex(), GOLDEN["wifi_msg"])

    def test_crc_matches_an_independent_bitwise_implementation(self):
        def crc(data, init, rpoly, mask):
            c = init
            for b in data:
                c ^= b
                for _ in range(8):
                    c = (c >> 1) ^ rpoly if c & 1 else c >> 1
            return c & mask
        for data in (b"", b"\x55\x0d\x04", bytes(range(40)), b"hallo welt"):
            self.assertEqual(dd.crc8(data), crc(data, 0x77, 0x8C, 0xFF))
            self.assertEqual(dd.crc16(data), crc(data, 0x3692, 0x8408, 0xFFFF))

    def test_roundtrip_and_damaged_messages(self):
        m = dd.Message(0x0702, 0x8092, 0x450740, b"\x01\x02\x03")
        d = dd.Message.decode(m.encode())
        self.assertEqual((d.target, d.id, d.type, d.payload), (0x0702, 0x8092, 0x450740, b"\x01\x02\x03"))
        raw = bytearray(m.encode())
        raw[-1] ^= 0xFF
        with self.assertRaises(ValueError):
            dd.Message.decode(bytes(raw))
        with self.assertRaises(ValueError):
            dd.Message.decode(b"\x00" * 20)
        with self.assertRaises(ValueError):
            dd.Message.decode(m.encode()[:-1])

    def test_models_from_the_advertisement(self):
        self.assertEqual(dd.model_from_manufacturer_data({0x08AA: bytes([0x15, 0x00, 1])})[1:], ("Osmo Action 5 Pro", "action5"))
        self.assertEqual(dd.model_from_manufacturer_data({0xF7AA: bytes([0x21, 0x00])})[1:], ("Osmo Pocket 4", "pocket4"))
        self.assertEqual(dd.model_from_manufacturer_data({0x08AA: bytes([0x99, 0x00])})[2], "unknown")
        self.assertIsNone(dd.model_from_manufacturer_data({0x004C: b"\x10\x05"}))

    def test_frame_rate_is_only_25_or_30(self):
        self.assertEqual(sorted(dd.FPS), [25, 30])


# ---------------------------------------------------------------- Verbindungsliste

class Connections(unittest.TestCase):
    NM = {
        ("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"):
            "wlan0:wifi:connected:Handy\\:Hotspot\nwlan1:wifi:connected:BoxNetz\nwlan2:wifi:connected:Cafe\neth0:ethernet:connected:Kabel\n"
            "p2p-dev-wlan0:wifi-p2p:disconnected:",
        ("nmcli", "-g", "802-11-wireless.ssid", "connection", "show", "Handy:Hotspot"): "Handy-Hotspot",
        ("nmcli", "-g", "802-11-wireless.mode", "connection", "show", "Handy:Hotspot"): "infrastructure",
        ("nmcli", "-s", "-g", "802-11-wireless-security.psk", "connection", "show", "Handy:Hotspot"): "handypw123",
        ("nmcli", "-g", "802-11-wireless-security.key-mgmt", "connection", "show", "Handy:Hotspot"): "wpa-psk",
        ("nmcli", "-g", "IP4.ADDRESS", "dev", "show", "wlan0"): "10.1.1.20/24",
        ("nmcli", "-g", "802-11-wireless.ssid", "connection", "show", "BoxNetz"): "BoxNetz",
        ("nmcli", "-g", "802-11-wireless.mode", "connection", "show", "BoxNetz"): "ap",
        ("nmcli", "-s", "-g", "802-11-wireless-security.psk", "connection", "show", "BoxNetz"): "boxpw1234",
        ("nmcli", "-g", "802-11-wireless-security.key-mgmt", "connection", "show", "BoxNetz"): "wpa-psk",
        ("nmcli", "-g", "IP4.ADDRESS", "dev", "show", "wlan1"): "10.42.0.1/24",
        ("nmcli", "-g", "802-11-wireless.ssid", "connection", "show", "Cafe"): "Cafe",
        ("nmcli", "-g", "802-11-wireless.mode", "connection", "show", "Cafe"): "infrastructure",
        ("nmcli", "-s", "-g", "802-11-wireless-security.psk", "connection", "show", "Cafe"): "",     # Dienst darf das Passwort nicht lesen
        ("nmcli", "-g", "802-11-wireless-security.key-mgmt", "connection", "show", "Cafe"): "wpa-psk",
        ("nmcli", "-g", "IP4.ADDRESS", "dev", "show", "wlan2"): "10.9.9.2/24",
        ("ip", "-4", "-o", "addr", "show"):
            "1: lo    inet 127.0.0.1/8 scope host lo\n2: eth0    inet 192.168.1.20/24 brd 192.168.1.255 scope global eth0\n"
            "3: eth2    inet 192.168.80.5/24 brd 192.168.80.255 scope global eth2\n4: tailscale0    inet 100.64.0.9/32 scope global tailscale0\n"
            "5: wlan0    inet 10.1.1.20/24 scope global wlan0\n6: usb0    inet 169.254.3.4/16 scope link usb0\n7: docker0    inet 172.17.0.1/16 scope global docker0\n",
    }

    def opts(self):
        with mock.patch.object(dd, "run", lambda cmd: self.NM.get(tuple(cmd), "")):
            return {o["ifname"]: o for o in dd.nm_wifi_options()}

    def test_wifi_networks_and_other_connections(self):
        o = self.opts()
        self.assertEqual(o["wlan0"]["type"], "client")
        self.assertEqual((o["wlan0"]["ssid"], o["wlan0"]["password"], o["wlan0"]["ip"]), ("Handy-Hotspot", "handypw123", "10.1.1.20"))
        self.assertEqual(o["wlan1"]["type"], "hotspot")
        self.assertEqual(o["eth0"]["type"], "other")
        self.assertEqual(o["eth2"]["ip"], "192.168.80.5")
        for skipped in ("lo", "tailscale0", "usb0", "docker0", "p2p-dev-wlan0"):
            self.assertNotIn(skipped, o)

    def test_unreadable_wifi_password_is_flagged(self):
        o = self.opts()
        self.assertTrue(o["wlan2"]["secret_missing"])
        self.assertFalse(o["wlan0"]["secret_missing"])

    def test_browser_list_never_contains_passwords(self):
        for o in self.opts().values():
            pub = dd.public_option(o)
            self.assertNotIn("password", pub)
            self.assertNotIn("handypw123", json.dumps(pub))
            self.assertNotIn("boxpw1234", json.dumps(pub))

    def test_adapter_choice_prefers_a_stick_over_the_onboard_module(self):
        root = tempfile.mkdtemp()
        usb = os.path.join(root, "usb")
        cls = os.path.join(root, "bt")
        os.makedirs(cls)
        for hci, vid, pid in (("hci0", "13d3", "3572"), ("hci1", "0b05", "190e")):
            real = os.path.join(usb, hci, "bluetooth", hci)
            os.makedirs(real)
            for fn, val in (("idVendor", vid), ("idProduct", pid)):
                with open(os.path.join(usb, hci, fn), "w") as f:
                    f.write(val + "\n")
            os.symlink(real, os.path.join(cls, hci))
        with mock.patch.object(dji, "SYSFS_BT", cls):
            self.assertEqual(dd.preferred_adapter(), "hci1")
        with mock.patch.object(dji, "SYSFS_BT", os.path.join(root, "gibt-es-nicht")):
            self.assertIsNone(dd.preferred_adapter())

    def test_known_connection_errors_get_plain_texts(self):
        self.assertIn("nicht geantwortet", dd.friendly_error(asyncio.TimeoutError()))
        self.assertIn("nicht geantwortet", dd.friendly_error(TimeoutError()))
        self.assertIn("nicht mehr sichtbar", dd.friendly_error(Exception("device 'dev_E4_7A_2C_D0_D0_4B' not found")))
        self.assertIn("beim Einrichten", dd.friendly_error(Exception("failed to discover services, device disconnected")))
        self.assertIn("abgebrochen", dd.friendly_error(Exception("org.bluez.Error.Failed: le-connection-abort-by-local")))
        self.assertIsNone(dd.friendly_error(ValueError("etwas ganz anderes")))
        self.assertIsNone(dd.friendly_error(TimeoutError("mit Text")))

    def test_camera_key_has_no_clash_with_other_cameras(self):
        first = dd.camera_key("D0:D0:4B:00:00:01")
        self.assertEqual(first, "dji-000001")
        second = dd.camera_key("D0:D0:4B:00:00:01", {first})                 # gleiche letzte sechs Stellen: längerer Schlüssel
        self.assertTrue(second.startswith("dji-") and second != first and len(second) > len(first))
        self.assertRegex(second, r"^dji-[0-9a-f]+$")


# ---------------------------------------------------------------- Dienst: Befehle

class Commands(unittest.TestCase):
    def daemon(self):
        d = tempfile.mkdtemp()
        return d, dd.Daemon(d)

    async def add(self, dm, addr=ADDR, **kw):
        return await dm.handle(dict({"cmd": "add", "addr": addr, "name": "Kamera A", "model": "Osmo Action 5 Pro", "kind": "action5"}, **kw))

    def test_add_gives_each_camera_its_own_key_and_defaults(self):
        async def go():
            d, dm = self.daemon()
            r1 = await self.add(dm)
            r2 = await self.add(dm, addr=ADDR2, name="Kamera B")
            self.assertEqual((r1["key"], r2["key"]), ("dji-000001", "dji-000002"))
            c = dm.cameras[ADDR].cfg
            self.assertEqual((c["resolution"], c["fps"], c["bitrate"], c["stabilization"], c["autoconnect"]), ("1080p", 30, 6000, "off", False))
            self.assertEqual(dm.cameras[ADDR2].cfg["name"], "Kamera B")
        arun(go())

    def test_add_takes_role_defaults_but_validates_them(self):
        async def go():
            d, dm = self.daemon()
            await self.add(dm, settings={"resolution": "720p", "fps": 30, "bitrate": 4000})
            c = dm.cameras[ADDR].cfg
            self.assertEqual((c["resolution"], c["bitrate"]), ("720p", 4000))
            await self.add(dm, addr=ADDR2, settings={"resolution": "4k", "fps": 60, "bitrate": 999999, "stabilization": "x"})
            c = dm.cameras[ADDR2].cfg
            self.assertEqual((c["resolution"], c["fps"], c["bitrate"], c["stabilization"]), ("1080p", 30, 16000, "off"))
        arun(go())

    def test_add_rejects_a_bad_address_and_is_idempotent(self):
        async def go():
            d, dm = self.daemon()
            self.assertIn("error", await dm.handle({"cmd": "add", "addr": "kein-mac"}))
            await self.add(dm)
            await self.add(dm)
            self.assertEqual(len(dm.cameras), 1)
        arun(go())

    def test_update_limits_and_ignores_unknown_fields(self):
        async def go():
            d, dm = self.daemon()
            await self.add(dm)
            await dm.handle({"cmd": "update", "addr": ADDR, "bitrate": 100, "fps": 60, "resolution": "1080p", "stabilization": "rocksteady",
                             "rtmp_key": "live", "evil": 1, "name": "  Brust  "})
            c = dm.cameras[ADDR].cfg
            self.assertEqual((c["bitrate"], c["fps"], c["stabilization"], c["name"]), (500, 30, "rocksteady", "Brust"))
            self.assertEqual(c["rtmp_key"], "dji-000001")                  # der Schlüssel ist nicht änderbar
            self.assertNotIn("evil", c)
            await dm.handle({"cmd": "update", "addr": ADDR, "bitrate": True})
            self.assertEqual(dm.cameras[ADDR].cfg["bitrate"], 500)         # ein Wahrheitswert ist keine Zahl
        arun(go())

    def test_empty_password_keeps_the_saved_one(self):
        async def go():
            d, dm = self.daemon()
            await self.add(dm)
            await dm.handle({"cmd": "update", "addr": ADDR, "ssid": "Netz", "password": "geheim"})
            await dm.handle({"cmd": "update", "addr": ADDR, "password": ""})
            self.assertEqual(dm.cameras[ADDR].cfg["password"], "geheim")
        arun(go())

    def test_connection_can_be_changed_any_time_but_applies_next_time(self):
        async def go():
            d, dm = self.daemon()
            await self.add(dm)
            cam = dm.cameras[ADDR]
            for st in ("idle", "error", "searching", "connecting", "pairing", "preparing", "wifi", "streaming", "status", "stopping"):
                cam.state = st
                r = await dm.handle({"cmd": "update", "addr": ADDR, "wifi_ifname": "manual", "ssid": "Neu-" + st, "password": "pw-" + st})
                self.assertEqual(r, {"ok": True}, st)
                self.assertEqual(cam.cfg["ssid"], "Neu-" + st)
            for st, locked in (("idle", False), ("error", False), ("searching", False), ("connecting", False), ("pairing", True), ("streaming", True)):
                cam.state = st
                self.assertEqual(cam.public()["locked"], locked, st)          # nur noch ein Hinweis: "gilt ab der nächsten Verbindung"
            cam.state, cam.publishing = "idle", True                           # sendet noch ohne Bluetooth
            self.assertTrue(cam.public()["locked"])
            cam.publishing = False
            self.assertFalse(cam.public()["locked"])
            cam.state = "streaming"
            r = await dm.handle({"cmd": "update", "addr": ADDR, "bitrate": 5000})
            self.assertEqual(r, {"ok": True})
        arun(go())

    def test_saved_network_can_be_used_and_deleted_while_streaming(self):
        async def go():
            d, dm = self.daemon()
            await self.add(dm)
            cam = dm.cameras[ADDR]
            cam.cfg["saved"] = [{"ssid": "Alt", "password": "alt12345"}, {"ssid": "Neu", "password": "neu12345"}]
            cam.state = "streaming"
            self.assertEqual(await dm.handle({"cmd": "use_saved", "addr": ADDR, "ssid": "Neu"}), {"ok": True})
            self.assertEqual((cam.cfg["ssid"], cam.cfg["password"]), ("Neu", "neu12345"))
            self.assertEqual(await dm.handle({"cmd": "delete_saved", "addr": ADDR, "ssid": "Alt"}), {"ok": True})
            self.assertEqual([n["ssid"] for n in cam.cfg["saved"]], ["Neu"])
        arun(go())

    def test_mode_stays_locked_while_a_session_runs(self):
        async def go():
            d, dm = self.daemon()
            await self.add(dm)
            cam = dm.cameras[ADDR]
            for st in ("searching", "connecting", "pairing", "streaming"):
                cam.state = st
                r = await dm.handle({"cmd": "update", "addr": ADDR, "status_only": True})        # die Art hat die laufende Sitzung schon gelesen
                self.assertIn("error", r, st)
                self.assertTrue(cam.public()["mode_locked"], st)
            cam.state = "error"
            self.assertFalse(cam.public()["mode_locked"])
        arun(go())

    def test_saved_networks_use_and_delete(self):
        async def go():
            d, dm = self.daemon()
            await self.add(dm)
            cam = dm.cameras[ADDR]
            cam.cfg["saved"] = [{"ssid": "A", "password": "pa"}, {"ssid": "B", "password": "pb"}]
            await dm.handle({"cmd": "use_saved", "addr": ADDR, "ssid": "B"})
            self.assertEqual((cam.cfg["ssid"], cam.cfg["password"]), ("B", "pb"))
            pub = cam.public()
            self.assertEqual(pub["saved"], ["A", "B"])
            self.assertNotIn("password", pub)
            self.assertNotIn("pb", json.dumps(pub))
            await dm.handle({"cmd": "delete_saved", "addr": ADDR, "ssid": "A"})
            self.assertEqual([n["ssid"] for n in cam.cfg["saved"]], ["B"])
        arun(go())

    def test_same_connection_offers_the_wifi_already_entered_for_it(self):
        """Das WLAN einmal an einer Verbindung eingegeben: Wählt eine weitere Kamera dieselbe Verbindung, wird es angeboten."""
        async def go():
            d, dm = self.daemon()
            await self.add(dm)
            await self.add(dm, addr=ADDR2, name="Kamera B")
            await dm.handle({"cmd": "update", "addr": ADDR, "wifi_ifname": "eth2"})
            await dm.handle({"cmd": "update", "addr": ADDR, "ssid": "KameraNetz"})
            await dm.handle({"cmd": "update", "addr": ADDR, "password": "geheim123"})        # Name und Passwort kommen getrennt an
            b = dm.cameras[ADDR2]
            await dm.handle({"cmd": "update", "addr": ADDR2, "wifi_ifname": "eth2"})
            self.assertEqual((b.cfg["ssid"], b.cfg["password"]), ("KameraNetz", "geheim123"))
            self.assertEqual(b.public()["saved"], ["KameraNetz"])                           # in ihrer eigenen Liste
            self.assertNotIn("geheim123", json.dumps(await dm.handle({"cmd": "state"})))
            dm2 = dd.Daemon(d)                                                               # auch nach einem Neustart
            await self.add(dm2, addr="AA:BB:CC:00:00:03", name="Kamera C")
            await dm2.handle({"cmd": "update", "addr": "AA:BB:CC:00:00:03", "wifi_ifname": "eth2"})
            self.assertEqual(dm2.cameras["AA:BB:CC:00:00:03"].cfg["ssid"], "KameraNetz")
        arun(go())

    def test_cameras_stay_independent(self):
        async def go():
            d, dm = self.daemon()
            await self.add(dm)
            await self.add(dm, addr=ADDR2, name="Kamera B")
            await dm.handle({"cmd": "update", "addr": ADDR, "wifi_ifname": "eth2", "ssid": "Netz1", "password": "pw1"})
            await dm.handle({"cmd": "update", "addr": ADDR2, "wifi_ifname": "eth2"})
            a, b = dm.cameras[ADDR], dm.cameras[ADDR2]
            await dm.handle({"cmd": "update", "addr": ADDR, "ssid": "Netz2", "password": "pw2"})      # A ändert ihr WLAN
            self.assertEqual((b.cfg["ssid"], b.cfg["password"]), ("Netz1", "pw1"))                      # B bleibt wie sie war
            c_addr = "AA:BB:CC:00:00:03"
            await self.add(dm, addr=c_addr, name="Kamera C")
            await dm.handle({"cmd": "update", "addr": c_addr, "ssid": "Eigenes", "password": "pwc"})     # C hat schon ein eigenes WLAN
            await dm.handle({"cmd": "update", "addr": c_addr, "wifi_ifname": "eth2"})
            self.assertEqual(dm.cameras[c_addr].cfg["ssid"], "Eigenes")                                  # und behält es
            self.assertEqual(a.public()["saved"], [])                                                   # nichts wird in fremde Listen geschrieben
        arun(go())

    def test_another_connection_or_manual_gets_no_offer(self):
        async def go():
            d, dm = self.daemon()
            await self.add(dm)
            await self.add(dm, addr=ADDR2, name="Kamera B")
            await dm.handle({"cmd": "update", "addr": ADDR, "wifi_ifname": "eth2", "ssid": "Netz1", "password": "pw1"})
            await dm.handle({"cmd": "update", "addr": ADDR2, "wifi_ifname": "eth3"})
            self.assertFalse(dm.cameras[ADDR2].cfg.get("ssid"))
            await dm.handle({"cmd": "update", "addr": ADDR, "wifi_ifname": "manual", "ssid": "M", "password": "pm"})
            await dm.handle({"cmd": "update", "addr": ADDR2, "wifi_ifname": "manual"})
            self.assertFalse(dm.cameras[ADDR2].cfg.get("ssid"))                                          # "Manuell" ist keine bestimmte Verbindung
        arun(go())

    def test_wifi_accepted_by_a_camera_is_remembered_for_its_connection(self):
        async def go():
            d, dm = self.daemon()
            await self.add(dm)
            await self.add(dm, addr=ADDR2, name="Kamera B")
            cam = dm.cameras[ADDR]
            cam.cfg.update(wifi_ifname="eth2", ssid="", password="")
            dm.remember_network(cam, "KameraNetz", "geheim123")
            await dm.handle({"cmd": "update", "addr": ADDR2, "wifi_ifname": "eth2"})
            self.assertEqual(dm.cameras[ADDR2].cfg["ssid"], "KameraNetz")
        arun(go())

    def test_remove_and_unknown_things(self):
        async def go():
            d, dm = self.daemon()
            await self.add(dm)
            self.assertEqual(await dm.handle({"cmd": "remove", "addr": ADDR}), {"ok": True})
            self.assertEqual(dm.cameras, {})
            self.assertIn("error", await dm.handle({"cmd": "remove", "addr": ADDR}))
            self.assertIn("error", await dm.handle({"cmd": "gibtsnicht", "addr": ADDR}))
            self.assertIn("error", await dm.handle({"cmd": "gibtsnicht"}))
        arun(go())

    def test_config_file_is_private_and_survives_a_restart(self):
        async def go():
            d, dm = self.daemon()
            await self.add(dm)
            await dm.handle({"cmd": "update", "addr": ADDR, "ssid": "Netz", "password": "geheim", "autoconnect": False})
            path = os.path.join(d, "dji-cameras.json")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(os.path.join(d, "dji-token")).st_mode & 0o777, 0o600)
            dm2 = dd.Daemon(d)
            self.assertEqual(dm2.cameras[ADDR].cfg["password"], "geheim")
            self.assertEqual(dm2.token, dm.token)
        arun(go())

    def test_snapshot_never_contains_a_password(self):
        async def go():
            d, dm = self.daemon()
            await self.add(dm)
            await dm.handle({"cmd": "update", "addr": ADDR, "ssid": "Netz", "password": "supergeheim"})
            self.assertNotIn("supergeheim", json.dumps(await dm.handle({"cmd": "state"})))
        arun(go())

    def test_scan_without_bleak_says_so(self):
        async def go():
            d, dm = self.daemon()
            with mock.patch.object(dd, "BleakScanner", None):
                await dm.scan()
            self.assertIn("bleak", dm.scan_error)
            self.assertFalse(dm.snapshot()["scanning"])
        arun(go())

    def test_scan_lists_only_dji_cameras_sorted_by_signal(self):
        class Dev:
            def __init__(self, name):
                self.name = name

        class Adv:
            def __init__(self, md, rssi):
                self.manufacturer_data, self.rssi = md, rssi

        class Scanner:
            @staticmethod
            async def discover(timeout=5, return_adv=False, **kw):
                return {"AA:AA:AA:00:00:01": (Dev("Handy"), Adv({0x004C: b"\x10\x05"}, -40)),
                        ADDR: (Dev("OsmoAction5"), Adv({0x08AA: bytes([0x15, 0x00])}, -70)),
                        ADDR2: (Dev(None), Adv({0xF7AA: bytes([0x20, 0x00])}, -50))}

        async def go():
            d, dm = self.daemon()
            await self.add(dm)
            root = tempfile.mkdtemp()
            os.makedirs(os.path.join(root, "hci0"))
            with mock.patch.object(dd, "BleakScanner", Scanner), mock.patch.object(dji, "SYSFS_BT", root):
                await dm.scan(0.01)
            res = dm.scan_results
            self.assertEqual([r["addr"] for r in res], [ADDR2, ADDR])
            self.assertEqual(res[0]["model"], "Osmo Pocket 3")
            self.assertEqual([r["paired"] for r in res], [False, True])
        arun(go())


class Authentication(unittest.TestCase):
    def test_requests_without_the_token_are_refused(self):
        async def go():
            d = tempfile.mkdtemp()
            dm = dd.Daemon(d)
            srv = await asyncio.start_server(dm.client, "127.0.0.1", 0)
            port = srv.sockets[0].getsockname()[1]

            async def ask(req):
                r, w = await asyncio.open_connection("127.0.0.1", port)
                w.write((json.dumps(req) + "\n").encode())
                await w.drain()
                line = await r.readline()
                w.close()
                return json.loads(line)
            bad = await ask({"cmd": "state", "id": 1})
            self.assertEqual(bad["error"], "kein Zugriff")
            self.assertEqual(bad["reply_to"], 1)
            self.assertEqual((await ask({"cmd": "state", "id": 2, "token": "falsch"}))["error"], "kein Zugriff")
            ok = await ask({"cmd": "state", "id": 3, "token": dm.token})
            self.assertEqual((ok["reply_to"], ok["cameras"]), (3, []))
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(b"kein json\n")
            await w.drain()
            self.assertIn("error", json.loads(await r.readline()))
            w.close()
            srv.close()
        arun(go())


# ---------------------------------------------------------------- Dienst: Sitzung

class FakeChar:
    def __init__(self, uuid, props):
        self.uuid, self.properties = uuid, props


class CameraSim:
    """Verhält sich wie eine DJI-Kamera: antwortet auf jede Nachricht des Dienstes."""

    def __init__(self, wifi_ok=True, battery=77, connect_delay=0.0, mv=4250, ma=-900):
        self.wifi_ok, self.battery, self.connect_delay = wifi_ok, battery, connect_delay
        self.mv, self.ma = mv, ma           # Akkuspannung (mV) und Strom aus dem Akku (mA): Werte wie an einer echten Action 4
        self.sent = []
        self.connecting = 0
        self.max_connecting = 0
        self.disconnect_cb = None
        self.client = None
        self.scan_on_during_connect = None
        self.found = True
        self.link_closed = 0               # wie oft die Bluetooth-Verbindung getrennt wurde
        self.on_reply = None               # Haken: wird mit jeder Nachricht des Dienstes aufgerufen, bevor die Kamera antwortet
        self.fail_connect = 0              # so viele Verbindungsversuche scheitern (Zeitüberschreitung, wie eine Kamera in der Wartezeit nach dem Trennen)

    def status_message(self, mv=None, ma=None, battery=None, extra=None):
        import struct
        pl = bytearray(34)
        struct.pack_into("<H", pl, 1, self.mv if mv is None else mv)
        struct.pack_into("<i", pl, 5, self.ma if ma is None else ma)
        pl[20] = self.battery if battery is None else battery
        for i, v in (extra or {}).items():
            pl[i] = v
        return dd.Message(0, 0, dd.TY_STATUS, bytes(pl)).encode()

    def drop_bluetooth(self):
        """Die Kamera (oder der Funkchip) beendet die Bluetooth-Verbindung."""
        self.client.is_connected = False
        self.disconnect_cb(self.client)

    def reply(self, msg):
        pl = b"\x00\x01" if msg.id == dd.ID_PAIR else (b"\x00\x00" if (msg.id != dd.ID_WIFI or self.wifi_ok) else b"\x00\x01")
        return dd.Message(msg.target, msg.id, msg.type, pl).encode()


class FakeClient:
    def __init__(self, sim, device, timeout=None, disconnected_callback=None):
        self.sim, self.cb = sim, None
        sim.disconnect_cb = disconnected_callback
        sim.client = self
        self.is_connected = False
        svc = types.SimpleNamespace(characteristics=[FakeChar("0000fff4-0000-1000-8000-00805f9b34fb", ["notify"]),
                                                     FakeChar("0000fff5-0000-1000-8000-00805f9b34fb", ["write-without-response"])])
        self.services = [svc]

    async def __aenter__(self):
        s = self.sim
        s.connecting += 1
        s.scan_on_during_connect = SHARED["scans_on"] > 0                 # BlueZ vergisst die Kamera, wenn die Suche vorher endet
        s.max_connecting = max(s.max_connecting, SHARED["active"] + 1)
        if s.fail_connect > 0:
            s.fail_connect -= 1
            s.connecting -= 1
            raise asyncio.TimeoutError()
        SHARED["active"] += 1
        await asyncio.sleep(s.connect_delay)
        SHARED["active"] -= 1
        s.connecting -= 1
        self.is_connected = True
        return self

    async def __aexit__(self, *a):
        self.is_connected = False
        self.sim.link_closed += 1

    async def disconnect(self):
        if self.is_connected:
            self.is_connected = False
            self.sim.link_closed += 1
            if self.sim.disconnect_cb:
                self.sim.disconnect_cb(self)

    async def start_notify(self, ch, cb):
        if ch.uuid.startswith("0000fff4"):
            self.cb = cb
            asyncio.get_event_loop().call_soon(cb, ch, self.sim.status_message())

    async def write_gatt_char(self, ch, data, response=False):
        msg = dd.Message.decode(data)
        self.sim.sent.append(msg)
        if self.sim.on_reply:
            self.sim.on_reply(msg)
        asyncio.get_event_loop().call_soon(self.cb, None, self.sim.reply(msg))


SHARED = {"active": 0, "scans_on": 0}


def fake_scanner(sims):
    class Scanner:
        def __init__(self, *a, **kw):
            self.kw = kw
            self.running = False

        async def start(self):
            self.running = True
            SHARED["scans_on"] += 1

        async def stop(self):
            if self.running:
                self.running = False
                SHARED["scans_on"] -= 1

        @property
        def discovered_devices_and_advertisement_data(self):
            return {a: (types.SimpleNamespace(address=a, sim=s), None) for a, s in sims.items() if s.found}

        @staticmethod
        async def discover(timeout=5, return_adv=False, **kw):
            return {}
    return Scanner


class Session(unittest.TestCase):
    def setUp(self):
        SHARED["active"] = 0
        SHARED["scans_on"] = 0
        self.bt = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.bt, "hci0"))
        self.patches = [mock.patch.object(dji, "SYSFS_BT", self.bt),
                        mock.patch.object(dd, "rtmp_publishing", lambda key, url=None: True),
                        mock.patch.object(dd.Camera, "RETRY_SCHEDULE", (0.2,)),
                        mock.patch.object(dd.Camera, "SEARCH_SECONDS", 0.6),
                        mock.patch.object(dd.Camera, "STREAM_CHECK_SECONDS", 0.2),
                        mock.patch.object(dd.Camera, "STREAM_LOST_SECONDS", 0.6),
                        mock.patch.object(dd.Camera, "CONNECT_SETTLE", 0.0)]
        for p in self.patches:
            p.start()

        self.cleanups = []

        async def noop(addr, remove=True):
            self.cleanups.append((addr, remove))
        self.patches.append(mock.patch.object(dd, "bluez_cleanup", noop))
        self.patches[-1].start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def install(self, sims):
        by_addr = dict(sims)
        p1 = mock.patch.object(dd, "BleakScanner", fake_scanner(by_addr))
        p2 = mock.patch.object(dd, "BleakClient", lambda device, timeout=None, disconnected_callback=None:
                               FakeClient(device.sim, device, timeout, disconnected_callback))
        p1.start()
        p2.start()
        self.patches += [p1, p2]

    async def setup_cam(self, dm, addr=ADDR, kind="action5", model="Osmo Action 5 Pro", **cfg):
        await dm.handle({"cmd": "add", "addr": addr, "name": "Kamera " + addr[-2:], "model": model, "kind": kind})
        base = {"wifi_ifname": "manual", "ssid": "KameraNetz", "password": "geheim123", "ip": "192.168.1.10"}
        await dm.handle(dict({"cmd": "update", "addr": addr}, **dict(base, **cfg)))

    async def wait_state(self, cam, states, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end:
            if cam.state in states:
                return True
            await asyncio.sleep(0.02)
        return False

    def test_network_typed_during_the_search_is_used(self):
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            cam = dm.cameras[ADDR]
            orig = cam.resolve_target
            calls = []

            def changing():                                                  # zwischen dem ersten und dem zweiten Lesen tippt jemand ein anderes Netz ein
                calls.append(1)
                if len(calls) == 2:
                    cam.cfg["ssid"], cam.cfg["password"] = "Neues-Netz", "neu12345"
                return orig()
            cam.resolve_target = changing
            await dm.handle({"cmd": "connect", "addr": ADDR})
            self.assertTrue(await self.wait_state(cam, ("streaming",)), cam.detail)
            by_id = {m.id: m for m in sim.sent}
            self.assertEqual(len(calls), 2)
            self.assertEqual(by_id[dd.ID_WIFI].payload, dd.pack_string("Neues-Netz") + dd.pack_string("neu12345"))
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_full_session_streams_and_stops_cleanly(self):
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm, stabilization="rocksteady", bitrate=8000, resolution="720p")
            await dm.handle({"cmd": "connect", "addr": ADDR})
            cam = dm.cameras[ADDR]
            self.assertTrue(await self.wait_state(cam, ("streaming",)), cam.detail)
            types_sent = [(m.id, m.type) for m in sim.sent]
            self.assertEqual([i for i, _ in types_sent],
                             [dd.ID_PAIR, dd.ID_STOP, dd.ID_PREPARE, dd.ID_WIFI, dd.ID_CONFIGURE, dd.ID_START, dd.ID_STOP])   # neues Protokoll: Bestätigung am Ende
            by_id = {m.id: m for m in sim.sent}
            self.assertEqual(by_id[dd.ID_PAIR].payload, dd.PAIR_PAYLOAD + dd.pack_string("mbln"))
            self.assertEqual(by_id[dd.ID_WIFI].payload, dd.pack_string("KameraNetz") + dd.pack_string("geheim123"))
            self.assertEqual(by_id[dd.ID_CONFIGURE].payload, dd.build_configure_payload("action5", "rocksteady"))
            url = "rtmp://192.168.1.10:1935/publish/dji-000001"
            self.assertEqual(by_id[dd.ID_START].payload, dd.build_start_payload("action5", url, "720p", 8000, 30))
            self.assertEqual(cam.battery, 77)
            self.assertEqual(cam.public()["state"], "streaming")
            self.assertTrue(cam.public()["locked"])
            self.assertEqual(cam.cfg["saved"], [{"ssid": "KameraNetz", "password": "geheim123"}])    # das Netz hat funktioniert: merken
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
            self.assertEqual(cam.state, "idle")
            self.assertEqual(sim.sent[-1].id, dd.ID_STOP)                                         # Stream wird beendet
            self.assertEqual(sim.sent[-1].payload, dd.STOP_PAYLOAD)
        arun(go())

    def test_search_stays_on_until_the_connection_stands_and_then_stops(self):
        async def go():
            sim = CameraSim(connect_delay=0.05)
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            await dm.handle({"cmd": "connect", "addr": ADDR})
            self.assertTrue(await self.wait_state(dm.cameras[ADDR], ("streaming",)))
            self.assertTrue(sim.scan_on_during_connect)                           # sonst: "device not found"
            self.assertEqual(SHARED["scans_on"], 0)                               # nach dem Verbindungsaufbau ist die Suche aus
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_cable_is_read_from_the_battery_current_on_an_action_4(self):
        """Echte Werte einer Osmo Action 4: aus dem Akku etwa -650 bis -1100 mA, am Kabel 0 bis -5 mA (Spannung 4400 mV bei vollem Akku)."""
        async def go():
            sim = CameraSim(battery=100, mv=4400, ma=0)                           # Kabel steckt, Akku voll
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm, kind="action4", model="Osmo Action 4")
            await dm.handle({"cmd": "connect", "addr": ADDR})
            cam = dm.cameras[ADDR]
            self.assertTrue(await self.wait_state(cam, ("streaming",)))
            self.assertIs(cam.public()["charging"], True)
            sim.client.cb(None, sim.status_message(mv=4344, ma=-818, battery=99))  # Kabel abgezogen: die Kamera läuft aus dem Akku
            await asyncio.sleep(0.1)
            self.assertIs(cam.public()["charging"], False)
            sim.client.cb(None, sim.status_message(mv=4310, ma=-1, battery=94))    # wieder angesteckt, Akku nicht voll: Strom 0
            await asyncio.sleep(0.1)
            self.assertIs(cam.public()["charging"], True)
            sim.client.cb(None, sim.status_message(mv=4390, ma=350, battery=60))   # echtes Laden wäre positiv
            await asyncio.sleep(0.1)
            self.assertIs(cam.public()["charging"], True)
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_low_battery_voltage_is_no_cable(self):
        """Fehler von 0.9.54: Byte 2 war die Spannung, nicht "lädt". Unter 4096 mV (0x0FFF) hätte Bit 0 fälschlich "lädt" ergeben."""
        async def go():
            sim = CameraSim(battery=45, mv=3900, ma=-950)                         # niedriger Akku, Batteriebetrieb
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm, kind="action4", model="Osmo Action 4")
            await dm.handle({"cmd": "connect", "addr": ADDR})
            cam = dm.cameras[ADDR]
            self.assertTrue(await self.wait_state(cam, ("streaming",)))
            self.assertIs(cam.public()["charging"], False)
            for mv in (3900, 4000, 4095, 4096, 4300, 4351, 4352, 4400):           # keine Spannung allein ergibt "am Kabel"
                sim.client.cb(None, sim.status_message(mv=mv, ma=-900))
                await asyncio.sleep(0.03)
                self.assertIs(cam.public()["charging"], False, mv)
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_charging_stays_unknown_for_models_where_it_was_not_observed(self):
        async def go():
            for kind, model in (("pocket3", "Osmo Pocket 3"), ("action5", "Osmo 360")):        # die Osmo 360 teilt sich die Art "action5", gemessen ist nur die Action 5 Pro
                sim = CameraSim(battery=80, mv=4400, ma=0)
                self.install({ADDR: sim})
                dm = dd.Daemon(tempfile.mkdtemp())
                await self.setup_cam(dm, kind=kind, model=model)
                await dm.handle({"cmd": "connect", "addr": ADDR})
                cam = dm.cameras[ADDR]
                self.assertTrue(await self.wait_state(cam, ("streaming",)), model)
                self.assertIsNone(cam.public()["charging"], model)
                self.assertEqual(cam.public()["battery"], 80)
                await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_cable_on_an_action_5_pro_and_an_action_6_from_the_measurements_of_6_october(self):
        """Echte Werte aus dem Journal der Box (Kabel je einmal abgezogen und angesteckt): aus dem Akku -606 bis -765 mA, beim Anstecken kurz -54,
        am Kabel positiv (+696 bis +4464 mA)."""
        measured = {
            ("action5", "Osmo Action 5 Pro"): [(-638, False), (-688, False), (-606, False), (-54, True), (1052, True), (4464, True), (2167, True)],
            ("action6", "Osmo Action 6"): [(-763, False), (-765, False), (696, True), (2734, True), (3083, True)],
        }

        async def go():
            for (kind, model), series in measured.items():
                sim = CameraSim(battery=60, mv=4350, ma=series[-1][0])
                self.install({ADDR: sim})
                dm = dd.Daemon(tempfile.mkdtemp())
                await self.setup_cam(dm, kind=kind, model=model)
                await dm.handle({"cmd": "connect", "addr": ADDR})
                cam = dm.cameras[ADDR]
                self.assertTrue(await self.wait_state(cam, ("streaming",)), model)
                for ma, want in series:
                    sim.client.cb(None, sim.status_message(ma=ma))
                    await asyncio.sleep(0.03)
                    self.assertIs(cam.public()["charging"], want, f"{model} {ma} mA")
                await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_status_journal_only_lists_bytes_that_rarely_change(self):
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            await dm.handle({"cmd": "connect", "addr": ADDR})
            cam = dm.cameras[ADDR]
            self.assertTrue(await self.wait_state(cam, ("streaming",)))
            with self.assertLogs("pipbox-dji", level="INFO") as cm:
                for i in range(12):                                               # Byte 1 schwankt ständig (Temperatur o. ä.)
                    sim.client.cb(None, sim.status_message(extra={1: 47 + i % 2}))
                    await asyncio.sleep(0.01)
                sim.client.cb(None, sim.status_message(extra={26: 0x21}))            # ein Byte, das selten wechselt
                await asyncio.sleep(0.05)
            lines = [l for l in cm.output if "Statusnachricht" in l]
            self.assertLessEqual(len(lines), 6)                                   # nicht jede Schwankung
            self.assertIn("21", lines[-1])
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_battery_is_reported_with_age_and_unknown_charging(self):
        async def go():
            sim = CameraSim(battery=18)
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm, kind="pocket3", model="Osmo Pocket 3")      # ein Modell, bei dem das Laden nicht gemessen ist
            self.assertIsNone(dm.cameras[ADDR].public()["battery"])               # vor dem ersten Wert: unbekannt
            self.assertIsNone(dm.cameras[ADDR].public()["battery_age"])
            await dm.handle({"cmd": "connect", "addr": ADDR})
            cam = dm.cameras[ADDR]
            self.assertTrue(await self.wait_state(cam, ("streaming",)))
            pub = cam.public()
            self.assertEqual((pub["battery"], pub["charging"]), (18, None))      # Laden kennen wir noch nicht: nie geraten
            self.assertGreaterEqual(pub["battery_age"], 0)
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_search_is_off_after_a_camera_was_not_found(self):
        async def go():
            sim = CameraSim()
            sim.found = False
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            await dm.handle({"cmd": "connect", "addr": ADDR})
            self.assertTrue(await self.wait_state(dm.cameras[ADDR], ("error",), 4))
            self.assertEqual(SHARED["scans_on"], 0)
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_retry_delay_grows_with_failures(self):
        async def go():
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            cam = dm.cameras[ADDR]
            with mock.patch.object(dd.Camera, "RETRY_SCHEDULE", (8, 8, 15, 30)):
                got = []
                for fails in (0, 1, 2, 3, 4, 9):
                    cam.fail_count = fails
                    got.append(cam.retry_delay())
            self.assertEqual(got, [8, 8, 8, 15, 30, 30])
        arun(go())

    def test_bluetooth_loss_does_not_restart_a_stream_that_still_arrives(self):
        """Früher (und in der Vorlage) endete die Sitzung beim Verlust von Bluetooth: Der Neuaufbau unterbrach den Stream."""
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            await dm.handle({"cmd": "connect", "addr": ADDR})
            cam = dm.cameras[ADDR]
            self.assertTrue(await self.wait_state(cam, ("streaming",)))
            n = len(sim.sent)
            self.assertEqual(cam.public()["battery"], 77)
            sim.drop_bluetooth()
            await asyncio.sleep(1.2)
            self.assertEqual(cam.public()["battery"], 77)                         # der letzte Wert bleibt stehen ...
            self.assertLessEqual(cam.public()["battery_age"], 3)                   # ... mit seinem Alter
            self.assertEqual(cam.state, "streaming")                              # nicht "error", keine neue Sitzung
            self.assertIn("Bluetooth", cam.detail)
            self.assertEqual(len(sim.sent), n)                                    # nichts Neues an die Kamera gesendet
            self.assertTrue(cam.public()["locked"])
            await dm.handle({"cmd": "disconnect", "addr": ADDR})                  # ohne Bluetooth kein Stopp-Befehl möglich
            self.assertEqual(cam.state, "idle")
            self.assertEqual(len(sim.sent), n)
        arun(go())

    def test_lost_bluetooth_does_not_burn_cpu(self):
        """Fehler 0.9.50: Nach dem Bluetooth-Verlust drehte die Schleife ohne Pause (ein Kern voll, Last auf der Box)."""
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            await dm.handle({"cmd": "connect", "addr": ADDR})
            cam = dm.cameras[ADDR]
            self.assertTrue(await self.wait_state(cam, ("streaming",)))
            sim.drop_bluetooth()
            await asyncio.sleep(0.3)
            t0 = time.process_time()
            await asyncio.sleep(1.5)
            used = time.process_time() - t0
            self.assertLess(used, 0.4, "Schleife läuft ohne Pause: %.2f s CPU in 1,5 s" % used)
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_bluetooth_loss_and_a_vanished_stream_start_over(self):
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            await dm.handle({"cmd": "connect", "addr": ADDR})
            cam = dm.cameras[ADDR]
            self.assertTrue(await self.wait_state(cam, ("streaming",)))
            state = {"on": True}
            with mock.patch.object(dd, "rtmp_publishing", lambda key, url=None: state["on"]):
                sim.drop_bluetooth()
                await asyncio.sleep(0.5)
                self.assertEqual(cam.state, "streaming")
                state["on"] = False                                               # der Stream bleibt jetzt aus
                self.assertTrue(await self.wait_state(cam, ("error",), 4))
                self.assertIn("ausgefallen", cam.detail)
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_older_model_skips_the_confirmation_and_pocket3_the_stabilization(self):
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm, kind="pocket3", model="Osmo Pocket 3")
            await dm.handle({"cmd": "connect", "addr": ADDR})
            self.assertTrue(await self.wait_state(dm.cameras[ADDR], ("streaming",)))
            self.assertEqual([m.id for m in sim.sent], [dd.ID_PAIR, dd.ID_STOP, dd.ID_PREPARE, dd.ID_WIFI, dd.ID_START])
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_wifi_refusal_names_the_network(self):
        async def go():
            sim = CameraSim(wifi_ok=False)
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            await dm.handle({"cmd": "connect", "addr": ADDR})
            cam = dm.cameras[ADDR]
            self.assertTrue(await self.wait_state(cam, ("error",)))
            self.assertIn("KameraNetz", cam.detail)
            self.assertNotIn("geheim123", cam.detail)
            self.assertIsNone(cam.cfg.get("saved"))                              # ein Netz, das nicht klappt, wird nicht gemerkt
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_camera_not_found(self):
        async def go():
            sim = CameraSim()
            sim.found = False
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            await dm.handle({"cmd": "connect", "addr": ADDR})
            cam = dm.cameras[ADDR]
            self.assertTrue(await self.wait_state(cam, ("error",)))
            self.assertIn("nicht gefunden", cam.detail)
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_adapter_argument_depends_on_the_bleak_version(self):
        async def go():
            dm = dd.Daemon(tempfile.mkdtemp())
            with mock.patch.object(dd, "preferred_adapter", lambda: "hci1"):
                with mock.patch.object(dd, "BLEAK_MAJOR", 1):
                    self.assertEqual(dm.scan_kwargs(), {"adapter": "hci1"})
                with mock.patch.object(dd, "BLEAK_MAJOR", 3):
                    self.assertEqual(dm.scan_kwargs(), {"bluez": {"adapter": "hci1"}})
            with mock.patch.object(dd, "preferred_adapter", lambda: None):
                self.assertEqual(dm.scan_kwargs(), {})
        arun(go())

    def test_missing_connection_gives_a_router_hint_without_bluetooth_traffic(self):
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm, wifi_ifname="eth9")
            with mock.patch.object(dd, "nm_wifi_options", lambda: []):
                await dm.handle({"cmd": "connect", "addr": ADDR})
                cam = dm.cameras[ADDR]
                self.assertTrue(await self.wait_state(cam, ("error",)))
            self.assertIn("eth9", cam.detail)
            self.assertIn("Router", cam.detail)
            self.assertEqual(sim.sent, [])
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_hotspot_uses_name_and_password_from_networkmanager(self):
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm, wifi_ifname="wlan1", ssid="", password="", ip="")
            opts = [{"ifname": "wlan1", "ssid": "BoxNetz", "password": "boxpw1234", "ip": "10.42.0.1", "type": "hotspot", "secret_missing": False}]
            with mock.patch.object(dd, "nm_wifi_options", lambda: opts):
                await dm.handle({"cmd": "connect", "addr": ADDR})
                cam = dm.cameras[ADDR]
                self.assertTrue(await self.wait_state(cam, ("streaming",)))
            wifi = [m for m in sim.sent if m.id == dd.ID_WIFI][0]
            self.assertEqual(wifi.payload, dd.pack_string("BoxNetz") + dd.pack_string("boxpw1234"))
            start = [m for m in sim.sent if m.id == dd.ID_START][0]
            self.assertIn(b"10.42.0.1:1935/publish/dji-000001", start.payload)
            self.assertIsNone(cam.cfg.get("saved"))                             # aus NetworkManager: nicht noch einmal speichern
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_unreadable_wifi_password_asks_for_manual_entry(self):
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm, wifi_ifname="wlan2", ssid="", password="", ip="")
            opts = [{"ifname": "wlan2", "ssid": "Cafe", "password": "", "ip": "10.9.9.2", "type": "client", "secret_missing": True}]
            with mock.patch.object(dd, "nm_wifi_options", lambda: opts):
                await dm.handle({"cmd": "connect", "addr": ADDR})
                cam = dm.cameras[ADDR]
                self.assertTrue(await self.wait_state(cam, ("error",)))
            self.assertIn("von Hand", cam.detail)
            self.assertEqual(sim.sent, [])
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_cameras_connect_one_after_the_other(self):
        """Gleichzeitiges Verbinden bricht auf dem Funkchip ab (le-connection-abort-by-local): immer nur eine Kamera verbindet."""
        async def go():
            sims = {ADDR: CameraSim(connect_delay=0.15), ADDR2: CameraSim(connect_delay=0.15)}
            self.install(sims)
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm, ADDR)
            await self.setup_cam(dm, ADDR2, kind="action4", model="Osmo Action 4")
            await dm.handle({"cmd": "connect", "addr": ADDR})
            await dm.handle({"cmd": "connect", "addr": ADDR2})
            for a in (ADDR, ADDR2):
                self.assertTrue(await self.wait_state(dm.cameras[a], ("streaming",)), a)
            self.assertEqual(max(s.max_connecting for s in sims.values()), 1)
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
            await dm.handle({"cmd": "disconnect", "addr": ADDR2})
        arun(go())

    def test_autoconnect_retries_after_a_failure(self):
        async def go():
            sim = CameraSim()
            sim.found = False
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            await dm.handle({"cmd": "update", "addr": ADDR, "autoconnect": True})
            cam = dm.cameras[ADDR]
            self.assertTrue(await self.wait_state(cam, ("error",)))
            self.assertGreater(cam.public()["retry_in"] + 1, 0)
            sim.found = True                                                    # die Kamera ist jetzt da
            self.assertTrue(await self.wait_state(cam, ("streaming",), 8))
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
            await asyncio.sleep(0.5)
            self.assertEqual(cam.state, "idle")                                 # nach "Trennen" kein automatisches Wiederverbinden
        arun(go())

    def test_missing_bleak_and_action2_are_reported_plainly(self):
        async def go():
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            cam = dm.cameras[ADDR]
            with mock.patch.object(dd, "BleakClient", None):
                with self.assertRaises(dd.CameraError) as e:
                    await cam.session()
            self.assertIn("bleak", str(e.exception))
            cam.cfg["model"] = "Osmo Action 2"
            with mock.patch.object(dd, "BleakClient", object):
                with self.assertRaises(dd.CameraError) as e:
                    await cam.session()
            self.assertIn("Action 2", str(e.exception))
        arun(go())

    def test_no_adapter_gives_the_stick_hint(self):
        async def go():
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            cam = dm.cameras[ADDR]
            with mock.patch.object(dji, "SYSFS_BT", os.path.join(self.bt, "keiner")), mock.patch.object(dd, "BleakClient", object), \
                    mock.patch.object(dji, "usb_bluetooth_devices", lambda: [{"id": "33fa:0010", "name": "BARROT", "driver": ""}]):
                with self.assertRaises(dd.CameraError) as e:
                    await cam.session()
            self.assertIn("BARROT", str(e.exception))
        arun(go())


# ---------------------------------------------------------------- Übernahme der früheren Version

class GracefulRelease(Session):
    """Beim Beenden des Dienstes (Neustart, Update) und bei einem Abbruch während des Einrichtens bleibt die Kamera nicht hängen: Sie bekommt ein
    ordentliches Trennen. Vorher riss der Dienst die Verbindung einfach ab, und die Kamera antwortete danach erst nach ein bis vier Minuten."""

    def test_shutdown_leaves_a_streaming_camera_untouched(self):
        """Eine streamende Kamera bleibt beim Beenden des Dienstes unberührt: kein Stopp, kein Trennen. BlueZ hält die Verbindung, der nächste Dienst verwendet
        sie weiter. (Ein Trennen ließe die Kamera etwa eine Minute lang keine Verbindung annehmen: gemessen auf der Box.)"""
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            await dm.handle({"cmd": "connect", "addr": ADDR})
            cam = dm.cameras[ADDR]
            self.assertTrue(await self.wait_state(cam, ("streaming",)), cam.detail)
            n_sent, cleanups = len(sim.sent), list(self.cleanups)
            t0 = time.time()
            await dm.shutdown()
            self.assertLess(time.time() - t0, 1.0)                                        # kein Warten auf die Sitzung
            self.assertEqual(sim.link_closed, 0)                                          # Verbindung nicht getrennt
            self.assertTrue(sim.client.is_connected)
            self.assertFalse(cam.task.done())                                             # Sitzung läuft unverändert weiter
            self.assertEqual(len(sim.sent), n_sent)                                       # kein Stopp-Befehl: Die Kamera streamt weiter
            self.assertEqual(self.cleanups, cleanups)                                     # BlueZ wird nicht angefasst
            self.assertTrue(dm.closing)
            cam.task.cancel()
            await asyncio.gather(cam.task, return_exceptions=True)
        arun(go())

    def test_shutdown_during_setup_stops_between_steps_and_disconnects(self):
        """Eine Sitzung mitten im Einrichten bleibt nicht halb eingerichtet zurück: Sie hört nach dem laufenden Schritt auf und trennt."""
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            started = {"done": False}
            futures = []

            def hook(msg):
                if msg.id == dd.ID_PREPARE and not started["done"]:
                    started["done"] = True
                    futures.append(asyncio.ensure_future(dm.shutdown()))              # der Dienst wird beendet, während die Kamera eingerichtet wird
            sim.on_reply = hook
            await dm.handle({"cmd": "connect", "addr": ADDR})
            cam = dm.cameras[ADDR]
            for _ in range(300):
                if futures and futures[0].done():
                    break
                await asyncio.sleep(0.02)
            self.assertTrue(futures and futures[0].done())
            ids = [m.id for m in sim.sent]
            self.assertNotIn(dd.ID_WIFI, ids)
            self.assertNotIn(dd.ID_START, ids)
            self.assertGreaterEqual(sim.link_closed, 1)
            self.assertTrue(cam.task.done())
        arun(go())

    def test_shutdown_with_nothing_connected_is_quick_and_harmless(self):
        async def go():
            dm = dd.Daemon(tempfile.mkdtemp())
            t0 = time.time()
            await dm.shutdown()
            self.assertLess(time.time() - t0, 1.0)
        arun(go())

    def test_supervisor_does_not_start_anything_while_closing(self):
        src = open(dd.__file__, encoding="utf-8").read()
        self.assertIn("and not cam.manual_off and not self.closing", src)

    def reuse_env(self, sim, state_fn):
        """Nachbau: BlueZ meldet die Kamera als verbunden (state_fn) und make_ble_device liefert das Gerät der Nachbildung."""
        p1 = mock.patch.object(dji, "bluez_device_state", state_fn)
        p2 = mock.patch.object(dd, "make_ble_device", lambda st, addr: types.SimpleNamespace(address=addr, sim=sim))
        p1.start()
        p2.start()
        self.patches += [p1, p2]

    def test_a_link_that_bluez_still_holds_is_reused_without_search_or_cleanup(self):
        """Nach einem Neustart oder Absturz des Dienstes hält BlueZ die Kamera noch verbunden: Sie wird weiterverwendet. Kein Trennen (die Kamera
        nähme danach etwa eine Minute lang keine Verbindung an), keine Suche, keine Aufräumaktion."""
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            seen = []
            self.reuse_env(sim, lambda addr, objects=None: seen.append(addr) or {"path": "/org/bluez/hci0/dev_X", "connected": True, "resolved": True, "name": "Osmo"})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            await dm.handle({"cmd": "connect", "addr": ADDR})
            cam = dm.cameras[ADDR]
            self.assertTrue(await self.wait_state(cam, ("streaming",)), cam.detail)
            self.assertEqual(seen[0], ADDR)
            self.assertEqual(self.cleanups, [])                                    # nichts getrennt, nichts entfernt
            self.assertEqual(SHARED["scans_on"], 0)
            self.assertFalse(cam.reuse_failed)
            self.assertEqual([m.id for m in sim.sent][:2], [dd.ID_PAIR, dd.ID_STOP])
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_a_failed_reuse_falls_back_to_cleanup_and_a_fresh_connection(self):
        async def go():
            sim = CameraSim()
            sim.fail_connect = 1                                                   # die weiterverwendete Verbindung antwortet nicht
            self.install({ADDR: sim})
            self.reuse_env(sim, lambda addr, objects=None: {"path": "/org/bluez/hci0/dev_X", "connected": True, "resolved": True, "name": ""})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm, autoconnect=True)                            # damit nach dem Fehler ein zweiter Versuch folgt
            await dm.handle({"cmd": "connect", "addr": ADDR})
            cam = dm.cameras[ADDR]
            self.assertTrue(await self.wait_state(cam, ("streaming",), 10), cam.detail)
            self.assertEqual(self.cleanups, [(ADDR, True)])                        # der zweite Versuch räumt auf (trennen und entfernen) und sucht neu
            self.assertFalse(cam.reuse_failed)                                     # nach dem Erfolg wieder zurückgesetzt
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_no_reuse_when_bluez_does_not_hold_the_camera(self):
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            self.reuse_env(sim, lambda addr, objects=None: {"path": "/org/bluez/hci0/dev_X", "connected": False, "resolved": False, "name": ""})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            await dm.handle({"cmd": "connect", "addr": ADDR})
            self.assertTrue(await self.wait_state(dm.cameras[ADDR], ("streaming",)))
            self.assertEqual(self.cleanups, [(ADDR, True)])                        # wie bisher: aufräumen, suchen, verbinden
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_stop_during_setup_leaves_between_steps_and_disconnects(self):
        """"Trennen" oder "Neu verbinden", während die Kamera eingerichtet wird: Die Sitzung hört nach dem laufenden Schritt auf, startet den Stream
        nicht und trennt die Verbindung ordentlich (kein Abreißen mitten in einer Nachricht)."""
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            cam_holder = {}

            def hook(msg):
                if msg.id == dd.ID_PREPARE:                                  # mitten im Einrichten: der Nutzer drückt "Trennen"
                    cam_holder["cam"].stop_requested = True
            sim.on_reply = hook
            cam = cam_holder["cam"] = dm.cameras[ADDR]
            await dm.handle({"cmd": "connect", "addr": ADDR})
            for _ in range(200):
                if cam.task is not None and cam.task.done():
                    break
                await asyncio.sleep(0.02)
            self.assertTrue(cam.task.done())
            ids = [m.id for m in sim.sent]
            self.assertNotIn(dd.ID_WIFI, ids)                                  # nach dem Schritt "Vorbereiten" ist Schluss
            self.assertNotIn(dd.ID_START, ids)
            self.assertGreaterEqual(sim.link_closed, 1)                          # Verbindung getrennt
            self.assertFalse(sim.client.is_connected)
        arun(go())

    def test_restart_during_setup_reconnects_after_a_clean_leave(self):
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            first = {"done": False}
            cam = dm.cameras[ADDR]

            def hook(msg):
                if msg.id == dd.ID_PREPARE and not first["done"]:
                    first["done"] = True
                    asyncio.ensure_future(cam.restart())                    # "Neu verbinden" mitten im Einrichten
            sim.on_reply = hook
            await dm.handle({"cmd": "connect", "addr": ADDR})
            self.assertTrue(await self.wait_state(cam, ("streaming",), 10), cam.detail)    # zweiter Anlauf läuft durch
            self.assertGreaterEqual(sim.link_closed, 1)                                     # der erste wurde ordentlich getrennt
            self.assertEqual([m.id for m in sim.sent].count(dd.ID_START), 1)                # der Stream wurde genau einmal gestartet
        arun(go())

    def test_failure_right_after_a_link_ended_is_a_calm_hint_not_a_red_error(self):
        """Nach dem Ende einer Verbindung meldet sich die Kamera etwa eine Minute lang nicht (gemessen): Der Dienst zeigt dann einen Hinweis statt eines Fehlers."""
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm, autoconnect=True)
            cam = dm.cameras[ADDR]
            self.assertFalse(cam.recovering(asyncio.TimeoutError()))                       # noch nie verbunden gewesen: ein echter Fehler
            await dm.handle({"cmd": "connect", "addr": ADDR})
            self.assertTrue(await self.wait_state(cam, ("streaming",)))
            await dm.handle({"cmd": "disconnect", "addr": ADDR})                           # die Verbindung endet
            self.assertGreater(cam.link_ended, 0)
            self.assertTrue(cam.recovering(asyncio.TimeoutError()))                        # Zeitüberschreitung kurz danach: Wartezeit der Kamera
            self.assertTrue(cam.recovering(RuntimeError("failed to discover services, device disconnected")))
            self.assertFalse(cam.recovering(RuntimeError("etwas anderes")))
            cam.link_ended = time.time() - cam.AFTER_LINK_SECONDS - 5                       # lange her: wieder ein echter Fehler
            self.assertFalse(cam.recovering(asyncio.TimeoutError()))
        arun(go())

    def test_daemon_wires_signals_and_the_unit_waits_long_enough(self):
        src = open(dd.__file__, encoding="utf-8").read()
        self.assertIn("loop.add_signal_handler(sig, stop.set)", src)
        self.assertIn("signal.SIGTERM", src)
        unit = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "install", "pipbox-dji.service"), encoding="utf-8").read()
        self.assertRegex(unit, r"TimeoutStopSec=(\d+)")
        self.assertGreaterEqual(int(re.search(r"TimeoutStopSec=(\d+)", unit).group(1)), 20)   # länger als die Wartezeit des Beendens (20 s)
        self.assertIn("os._exit(0)", src)                                                      # kein Aufräumen durch asyncio.run: es würde die Verbindungen trennen
        install = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "install", "install.sh"), encoding="utf-8").read()
        self.assertNotIn("bluetoothctl disconnect", install)                                  # kein Trennen vor dem Neustart: BlueZ hält die Verbindung, der neue Dienst nutzt sie weiter


    # ---- Nur Akkustand (Action 5 sendet per HDMI, Bluetooth liefert nur den Akku)
    def test_status_only_reads_battery_without_wifi_and_stream(self):
        async def go():
            sim = CameraSim(battery=42)
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await dm.handle({"cmd": "add", "addr": ADDR, "name": "Action 5", "model": "Osmo Action 5 Pro", "kind": "action5", "status_only": True})
            cam = dm.cameras[ADDR]
            self.assertEqual(cam.cfg["rtmp_key"], "hdmi")                       # Akku erscheint bei der HDMI-Kamera
            await dm.handle({"cmd": "connect", "addr": ADDR})                   # ohne WLAN-Angaben: bei normalem Betrieb ein Fehler
            self.assertTrue(await self.wait_state(cam, ("status",)), cam.detail)
            self.assertEqual([m.id for m in sim.sent], [dd.ID_PAIR])            # nur koppeln, nichts an WLAN oder Stream
            self.assertEqual(cam.battery, 42)
            sim.client.cb(None, sim.status_message(battery=9))
            await asyncio.sleep(0.1)
            pub = cam.public()
            self.assertEqual((pub["battery"], pub["state"], pub["rtmp_key"], pub["status_only"]), (9, "status", "hdmi", True))
            self.assertTrue(pub["locked"])
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
            self.assertEqual(cam.state, "idle")
            self.assertEqual([m.id for m in sim.sent], [dd.ID_PAIR])            # beim Trennen kein Stopp-Befehl an die Kamera
            self.assertEqual(sim.link_closed, 1)
        arun(go())

    def test_status_only_reconnects_after_bluetooth_loss(self):
        async def go():
            sim = CameraSim(battery=55)
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await dm.handle({"cmd": "add", "addr": ADDR, "name": "Action 5", "model": "Osmo Action 5 Pro", "kind": "action5", "status_only": True})
            cam = dm.cameras[ADDR]
            await dm.handle({"cmd": "update", "addr": ADDR, "autoconnect": True})
            self.assertTrue(await self.wait_state(cam, ("status",)))
            first = sim.client
            sim.drop_bluetooth()
            end = time.time() + 6
            while time.time() < end and not (cam.state == "status" and sim.client is not first):
                await asyncio.sleep(0.02)
            self.assertEqual(cam.state, "status", cam.detail)
            self.assertIsNot(sim.client, first)                                 # neue Verbindung, wieder nur Status
            self.assertEqual([m.id for m in sim.sent], [dd.ID_PAIR, dd.ID_PAIR])
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
        arun(go())

    def test_status_only_cameras_connect_automatically_and_after_restart(self):
        async def go():
            sim = CameraSim(battery=33)
            self.install({ADDR: sim})
            d = tempfile.mkdtemp()
            dm = dd.Daemon(d)
            await dm.handle({"cmd": "add", "addr": ADDR, "name": "Action 5", "model": "Osmo Action 5 Pro", "kind": "action5"})
            self.assertFalse(dm.cameras[ADDR].cfg["autoconnect"])
            await dm.handle({"cmd": "update", "addr": ADDR, "status_only": True})
            cam = dm.cameras[ADDR]
            self.assertTrue(cam.cfg["autoconnect"])                             # sofort an
            self.assertTrue(await self.wait_state(cam, ("status",)), cam.detail)
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
            # alte Einstellung (vor dieser Änderung): status_only ohne autoconnect wird beim Laden nachgezogen
            cfg = json.load(open(os.path.join(d, "dji-cameras.json")))
            cfg["cameras"][ADDR]["autoconnect"] = False
            json.dump(cfg, open(os.path.join(d, "dji-cameras.json"), "w"))
            dm2 = dd.Daemon(d)
            self.assertTrue(dm2.cameras[ADDR].cfg["autoconnect"])
        arun(go())

    def test_status_only_fails_when_camera_goes_silent(self):
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await dm.handle({"cmd": "add", "addr": ADDR, "name": "Action 5", "model": "Osmo Action 5 Pro", "kind": "action5", "status_only": True})
            cam = dm.cameras[ADDR]
            with mock.patch.object(dd.Camera, "STATUS_SILENCE_SECONDS", 0.3):
                await dm.handle({"cmd": "connect", "addr": ADDR})
                self.assertTrue(await self.wait_state(cam, ("error",)))
            self.assertIn("Statusmeldungen", cam.detail)
        arun(go())

    def test_switching_the_mode_changes_the_key_and_is_locked_while_connected(self):
        async def go():
            sim = CameraSim()
            self.install({ADDR: sim})
            dm = dd.Daemon(tempfile.mkdtemp())
            await self.setup_cam(dm)
            cam = dm.cameras[ADDR]
            self.assertEqual(cam.cfg["rtmp_key"], "dji-000001")
            self.assertFalse(cam.public().get("status_only"))
            self.assertEqual((await dm.handle({"cmd": "update", "addr": ADDR, "status_only": True})).get("ok"), True)
            self.assertEqual(cam.cfg["rtmp_key"], "hdmi")
            await dm.handle({"cmd": "connect", "addr": ADDR})
            self.assertTrue(await self.wait_state(cam, ("status",)))
            self.assertIn("error", await dm.handle({"cmd": "update", "addr": ADDR, "status_only": False}))
            self.assertTrue(cam.cfg["status_only"])
            await dm.handle({"cmd": "disconnect", "addr": ADDR})
            await dm.handle({"cmd": "update", "addr": ADDR, "status_only": False})
            self.assertEqual(cam.cfg["rtmp_key"], "dji-000001")                 # eigener Schlüssel wie zuvor
        arun(go())


class Migration(unittest.TestCase):
    def legacy(self, d, net="eth2", wifi=True):
        def w(name, obj):
            with open(os.path.join(d, name), "w") as f:
                json.dump(obj, f)
        w("dji-known.json", {ADDR: {"model": "osmoAction5Pro", "model_name": "Osmo Action 5 Pro"},
                             ADDR2: {"model": "osmoAction4", "model_name": "Osmo Action 4"}})
        w("dji-settings.json", {ADDR: {"resolution": "720p", "fps": 25, "bitrate_kbps": 4000, "stabilization": "rockSteadyPlus"}})
        if wifi:
            w("dji-wifi.json", {"ssid": "KameraNetz", "password": "geheim123"})
        w("dji-active.json", {ADDR: {"model": "osmoAction5Pro", "ssid": "KameraNetz", "password": "geheim123",
                                     "url": "rtmp://192.168.1.10:1935/publish/dji-000001", "res": "720p", "fps": 25, "kbps": 4000,
                                     "codec": "AVC", "stab": "rockSteadyPlus"}})
        if net:
            w("camera-net.json", {"iface": net})
        w("cameras.json", [{"id": "a1", "name": "Hauptkamera", "key": "dji-000001", "role": "main"}])      # so speichert CameraStore: eine Liste

    OTHER = [{"ifname": "eth2", "type": "other", "ip": "192.168.80.5", "ssid": "", "password": ""}]

    def test_old_cameras_settings_and_network_are_taken_over(self):
        d = tempfile.mkdtemp()
        self.legacy(d)
        out = dd.migrate_legacy(d, self.OTHER)
        self.assertEqual(sorted(out), [ADDR, ADDR2])
        c = out[ADDR]
        self.assertEqual((c["name"], c["kind"], c["model"]), ("Hauptkamera", "action5", "Osmo Action 5 Pro"))      # selbst vergebener Name bleibt
        self.assertEqual((c["resolution"], c["fps"], c["bitrate"], c["stabilization"]), ("720p", 25, 4000, "rocksteadyplus"))
        self.assertEqual((c["wifi_ifname"], c["ssid"], c["password"]), ("eth2", "KameraNetz", "geheim123"))
        self.assertEqual(c["rtmp_key"], "dji-000001")
        self.assertTrue(c["autoconnect"])                                            # lief vorher: soll weiter laufen
        self.assertFalse(out[ADDR2]["autoconnect"])
        self.assertEqual(out[ADDR2]["name"], "Osmo Action 4")
        self.assertEqual(c["saved"], [{"ssid": "KameraNetz", "password": "geheim123"}])

    def test_wifi_managed_by_networkmanager_keeps_a_fixed_address(self):
        d = tempfile.mkdtemp()
        self.legacy(d, net="wlan0")
        out = dd.migrate_legacy(d, [{"ifname": "wlan0", "type": "client", "ip": "10.1.1.20", "ssid": "Handy", "password": "x"}])
        c = out[ADDR]
        self.assertEqual((c["wifi_ifname"], c["ip"], c["ssid"]), ("manual", "10.1.1.20", "KameraNetz"))

    def test_without_old_files_nothing_is_created(self):
        self.assertEqual(dd.migrate_legacy(tempfile.mkdtemp(), []), {})

    def test_daemon_migrates_once_and_leaves_the_old_files(self):
        d = tempfile.mkdtemp()
        self.legacy(d)
        async def go():
            with mock.patch.object(dd, "nm_wifi_options", lambda: self.OTHER):
                dm = dd.Daemon(d)
            self.assertEqual(sorted(dm.cameras), [ADDR, ADDR2])
            self.assertTrue(os.path.exists(os.path.join(d, "dji-cameras.json")))
            self.assertTrue(os.path.exists(os.path.join(d, "dji-known.json")))
            os.remove(os.path.join(d, "dji-known.json"))
            os.remove(os.path.join(d, "dji-active.json"))
            dm2 = dd.Daemon(d)                                                       # zweiter Start: nur noch die neue Datei zählt
            self.assertEqual(sorted(dm2.cameras), [ADDR, ADDR2])
        arun(go())

    def test_broken_old_files_do_not_crash(self):
        d = tempfile.mkdtemp()
        for n in ("dji-known.json", "dji-settings.json", "dji-active.json", "dji-wifi.json"):
            with open(os.path.join(d, n), "w") as f:
                f.write("{kaputt")
        self.assertEqual(dd.migrate_legacy(d, []), {})


# ---------------------------------------------------------------- server.py: DjiService

class FakeDaemonHandler(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            req = json.loads(line)
            self.server.seen.append(req)
            if req.get("token") != self.server.token:
                resp = {"error": "kein Zugriff"}
            elif req.get("cmd") == "boom":
                resp = {"error": "kaputt"}
            elif req.get("cmd") == "add":
                resp = {"ok": True, "key": "dji-" + req["addr"].replace(":", "").lower()[-6:]}
            elif req.get("cmd") == "state":
                resp = {"cameras": self.server.cameras, "scan": [], "scanning": False, "scan_error": "", "bleak": True}
            elif req.get("cmd") == "wifi_options":
                resp = {"wifi_options": [{"ifname": "eth0", "ssid": "", "ip": "192.168.1.20", "type": "other", "secret_missing": False}]}
            elif req.get("cmd") == "adapters":
                resp = {"adapters": [], "adapter_problems": [], "driver": {}}
            else:
                resp = {"ok": True}
            resp["reply_to"] = req.get("id") + (1000 if self.server.wrong_id else 0)
            self.wfile.write((json.dumps(resp) + "\n").encode())


class DjiServiceTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FakeDaemonHandler)
        self.srv.daemon_threads = True
        self.srv.seen, self.srv.token, self.srv.cameras, self.srv.wrong_id = [], "t" * 40, [], False
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        with open(os.path.join(self.state, "dji-token"), "w") as f:
            f.write(self.srv.token + "\n")
        self.cams = server.CameraStore(os.path.join(self.state, "cameras.json"), "publish", "", False)
        self.svc = server.DjiService(self.state, self.cams, "publish", 1935)
        self.svc.PORT = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_commands_carry_the_token_and_get_the_matching_answer(self):
        r = self.svc.command({"cmd": "scan"})
        self.assertEqual(r, {"ok": True})
        self.assertEqual(self.srv.seen[-1]["token"], self.srv.token)

    def test_only_known_commands_and_fields_pass(self):
        with self.assertRaises(ValueError):
            self.svc.command({"cmd": "boom"})
        with self.assertRaises(ValueError):
            self.svc.command({"cmd": "shell"})
        self.svc.command({"cmd": "update", "addr": ADDR, "bitrate": 5000, "evil": "x", "token": "mein-eigenes", "id": 99})
        seen = self.srv.seen[-1]
        self.assertNotIn("evil", seen)
        self.assertEqual(seen["token"], self.srv.token)                         # ein Token vom Browser zählt nie
        self.assertNotEqual(seen["id"], 99)

    def test_status_only_field_passes_and_does_not_list_or_rename_the_hdmi_camera(self):
        self.svc.command({"cmd": "update", "addr": ADDR, "status_only": True})
        self.assertIs(self.srv.seen[-1]["status_only"], True)
        self.svc._ensure_listed([{"rtmp_key": "hdmi", "name": "Action 5"}])        # der HDMI-Dienst legt seine Kamera selbst an
        self.assertEqual([c["key"] for c in self.cams.cams], [])
        self.cams.add("HDMI", "hdmi", "main")
        with mock.patch.object(self.svc, "_config", lambda: {ADDR: {"rtmp_key": "hdmi", "status_only": True}}):
            self.svc.command({"cmd": "update", "addr": ADDR, "name": "Neuer Name"})
        self.assertEqual(self.cams.cams[0]["name"], "HDMI")

    def test_switching_to_status_only_drops_the_cameras_own_list_entry(self):
        self.cams.add("Osmo Action 5 Pro", "dji-d0d04b", "extra")
        self.cams.add("HDMI", "hdmi", "extra")
        cfg = {ADDR: {"rtmp_key": "dji-d0d04b"}}
        with mock.patch.object(self.svc, "_config", lambda: cfg):
            self.svc.command({"cmd": "update", "addr": ADDR, "status_only": True})
        self.assertEqual([c["key"] for c in self.cams.cams], ["hdmi"])
        self.cams.add("Andere", "dji-111111", "extra")
        with mock.patch.object(self.svc, "_config", lambda: cfg):
            self.svc.command({"cmd": "update", "addr": ADDR, "status_only": False})    # zurück: nichts wird gelöscht
        self.assertEqual(len(self.cams.cams), 2)

    def test_removing_a_dji_camera_in_its_card_removes_it_from_the_camera_list_too_and_it_does_not_come_back(self):
        self.cams.add("Osmo Action 4", "dji-f04fe2", "extra")
        cfg = {ADDR: {"rtmp_key": "dji-f04fe2"}}
        with mock.patch.object(self.svc, "_config", lambda: cfg):
            self.svc.command({"cmd": "remove", "addr": ADDR})
        self.assertEqual(self.cams.cams, [])
        self.assertEqual(self.srv.seen[-1]["cmd"], "remove")
        self.assertIn("dji-f04fe2", self.cams.forgotten)
        with mock.patch.object(self.cams, "live_streams", lambda: {"dji-f04fe2": {}}):
            self.assertEqual(self.cams.auto_add(), [])                           # die Kamera sendet noch, kommt aber nicht von selbst zurück
        self.assertEqual(self.cams.cams, [])

    def test_removing_a_dji_camera_in_the_camera_list_removes_it_from_the_dji_service(self):
        with mock.patch.object(self.svc, "_config", lambda: {ADDR: {"rtmp_key": "dji-f04fe2"}}):
            self.assertEqual(self.svc.addr_for_key("dji-f04fe2"), ADDR)
            self.assertTrue(self.svc.remove_for_key("dji-f04fe2"))
        self.assertEqual((self.srv.seen[-1]["cmd"], self.srv.seen[-1]["addr"]), ("remove", ADDR))
        n = len(self.srv.seen)
        with mock.patch.object(self.svc, "_config", lambda: {ADDR: {"rtmp_key": "hdmi", "status_only": True}}):
            self.assertFalse(self.svc.remove_for_key("hdmi"))                    # Nur-Akku-Kamera und fremde Schlüssel bleiben unberührt
            self.assertFalse(self.svc.remove_for_key("cam-123456"))
            self.assertIsNone(self.svc.addr_for_key("dji-ffffff"))
        self.assertEqual(len(self.srv.seen), n)

    def test_a_removed_key_can_be_added_again_by_hand_or_by_setting_the_camera_up_again(self):
        self.cams.forget("dji-f04fe2")
        self.cams.forget("cam-abc")
        self.assertEqual(server.CameraStore(self.cams.path, "publish", "", False).forgotten, {"dji-f04fe2", "cam-abc"})   # übersteht den Neustart
        self.svc._ensure_listed([{"rtmp_key": "dji-f04fe2", "name": "Osmo Action 4"}])
        self.assertEqual([c["key"] for c in self.cams.cams], ["dji-f04fe2"])
        self.assertNotIn("dji-f04fe2", self.cams.forgotten)
        self.cams.add("Von Hand", "cam-abc", "extra")
        self.assertNotIn("cam-abc", self.cams.forgotten)

    def test_status_only_is_part_of_the_settings_backup(self):
        t = server.SettingsTransfer.__new__(server.SettingsTransfer)
        clean_dji = t._clean_dji
        out, _ = clean_dji([{"addr": ADDR, "name": "A5", "status_only": True}])
        self.assertIs(out[0]["status_only"], True)
        with self.assertRaises(ValueError):
            clean_dji([{"addr": ADDR, "name": "A5", "status_only": "ja"}])

    def test_bad_address_is_refused_before_it_reaches_the_service(self):
        n = len(self.srv.seen)
        with self.assertRaises(ValueError):
            self.svc.command({"cmd": "connect", "addr": "../../etc"})
        self.assertEqual(len(self.srv.seen), n)

    def test_service_errors_become_value_errors(self):
        with mock.patch.object(self.svc, "_config", lambda: {}):
            with self.assertRaises(ValueError) as e:
                self.svc._call({"cmd": "boom"})
        self.assertIn("kaputt", str(e.exception))

    def test_wrong_token_is_rejected_by_the_service(self):
        with open(os.path.join(self.state, "dji-token"), "w") as f:
            f.write("falsch\n")
        with self.assertRaises(ValueError):
            self.svc.command({"cmd": "scan"})

    def test_missing_token_file_and_dead_service(self):
        os.remove(os.path.join(self.state, "dji-token"))
        with self.assertRaises(RuntimeError) as e:
            self.svc.command({"cmd": "scan"})
        self.assertIn("nicht bereit", str(e.exception))
        with open(os.path.join(self.state, "dji-token"), "w") as f:
            f.write(self.srv.token + "\n")
        self.svc.PORT = 1                                                        # dort lauscht niemand
        with self.assertRaises(RuntimeError) as e:
            self.svc.command({"cmd": "scan"})
        self.assertIn("läuft nicht", str(e.exception))

    def test_status_when_the_service_is_down_says_why(self):
        self.svc.PORT = 1
        st = self.svc.status()
        self.assertFalse(st["available"])
        self.assertIn("läuft nicht", st["reason"])
        self.assertEqual(st["cameras"], [])

    def test_status_merges_state_connections_and_adapters(self):
        self.srv.cameras = [{"addr": ADDR, "name": "Kamera A", "model": "Osmo Action 5 Pro", "rtmp_key": "dji-000001", "fps": 25}]
        st = self.svc.status()
        self.assertTrue(st["available"])
        self.assertEqual(st["wifi_options"][0]["ifname"], "eth0")
        self.assertEqual(st["adapters"], [])
        self.assertEqual([c["key"] for c in self.cams.cams], ["dji-000001"])      # jede Kamera steht auch in der Kameraliste
        self.assertEqual(self.cams.cams[0]["name"], "Kamera A")
        self.assertEqual(self.cams.cams[0]["role"], "main")
        self.svc.status()                                                          # nicht doppelt eintragen
        self.assertEqual(len(self.cams.cams), 1)

    def test_add_uses_the_profile_of_the_free_role(self):
        self.svc.command({"cmd": "add", "addr": ADDR, "name": "Vorn", "model": "Osmo Action 5 Pro", "kind": "action5"})
        self.assertEqual(self.srv.seen[-1]["settings"], {"resolution": "1080p", "fps": 30, "bitrate": 8000})     # erste Kamera: Hauptbild
        self.svc.command({"cmd": "add", "addr": ADDR2, "name": "Hinten", "model": "Osmo Pocket 3", "kind": "pocket3"})
        self.assertEqual(self.srv.seen[-1]["settings"], {"resolution": "720p", "fps": 30, "bitrate": 4000})      # zweite: Bild-in-Bild
        self.assertEqual([(c["key"], c["role"]) for c in self.cams.cams], [("dji-000001", "main"), ("dji-000002", "pip")])
        self.svc.command({"cmd": "add", "addr": "AA:BB:CC:00:00:03", "name": "Dritte", "model": "x", "kind": "action4"})
        self.assertEqual(self.srv.seen[-1]["settings"], {})                                                       # weitere: Standard des Dienstes

    def test_renaming_also_renames_the_camera_in_the_list(self):
        self.svc.command({"cmd": "add", "addr": ADDR, "name": "Vorn", "model": "m", "kind": "action5"})
        with open(os.path.join(self.state, "dji-cameras.json"), "w") as f:
            json.dump({"cameras": {ADDR: {"rtmp_key": "dji-000001", "fps": 30}}}, f)
        self.svc.command({"cmd": "update", "addr": ADDR, "name": "Brust"})
        self.assertEqual(self.cams.cams[0]["name"], "Brust")
        self.svc.command({"cmd": "add", "addr": ADDR2, "name": "Hinten", "model": "m", "kind": "action5"})
        with open(os.path.join(self.state, "dji-cameras.json"), "w") as f:
            json.dump({"cameras": {ADDR: {"rtmp_key": "dji-000001"}, ADDR2: {"rtmp_key": "dji-000002"}}}, f)
        with self.assertRaises(ValueError):                                       # ein doppelter Name wird abgelehnt
            self.svc.command({"cmd": "update", "addr": ADDR2, "name": "brust"})

    def test_fps_for_key_reads_the_service_config(self):
        with open(os.path.join(self.state, "dji-cameras.json"), "w") as f:
            json.dump({"cameras": {ADDR: {"rtmp_key": "dji-000001", "fps": 25}, ADDR2: {"rtmp_key": "dji-000002", "fps": 60}}}, f)
        self.assertEqual(self.svc.fps_for_key("dji-000001"), 25)
        self.assertEqual(self.svc.fps_for_key("dji-000002"), 30)                  # ungültiger Wert: Standard
        self.assertIsNone(self.svc.fps_for_key("cam-abc"))
        self.assertIsNone(self.svc.fps_for_key("dji-ffffff"))

    def test_battery_of_dji_cameras_comes_from_the_service_state(self):
        self.srv.cameras = [{"addr": ADDR, "rtmp_key": "dji-000001", "battery": 82, "battery_age": 4, "charging": True},
                            {"addr": ADDR2, "rtmp_key": "dji-000002", "battery": None},
                            {"addr": "AA:BB:CC:00:00:03", "rtmp_key": "dji-000003", "battery": 20, "battery_age": 900, "charging": None}]
        got = self.svc.camera_extras()
        self.assertEqual(got["dji-000001"], {"battery": 82, "battery_age": 4, "charging": True})
        self.assertNotIn("dji-000002", got)                                       # unbekannter Akkustand: nichts anzeigen
        self.assertEqual(got["dji-000003"]["battery_age"], 900)
        self.srv.cameras = []
        self.assertIn("dji-000001", self.svc.camera_extras())                     # kurz zwischengespeichert (die Statusseite fragt oft)
        self.svc._extras = None
        self.assertEqual(self.svc.camera_extras(), {})
        self.svc.PORT = 1                                                         # Dienst weg: leer, kein Fehler
        self.svc._extras = None
        self.assertEqual(self.svc.camera_extras(), {})

    def test_demo_mode_works_without_any_service(self):
        svc = server.DjiService(self.state, self.cams, "publish", 1935, demo=True)
        st = svc.status()
        self.assertTrue(st["available"])
        self.assertEqual(len(st["cameras"]), 2)
        svc.command({"cmd": "disconnect", "addr": st["cameras"][0]["addr"]})
        self.assertEqual(svc.status()["cameras"][0]["state"], "idle")
        self.assertNotIn("password", json.dumps(st))


class InstallFiles(unittest.TestCase):
    def test_installer_gets_bleak_and_stops_when_it_is_missing(self):
        sh = open(os.path.join(os.path.dirname(HERE), "install", "install.sh"), encoding="utf-8").read()
        self.assertIn('import bleak', sh)
        self.assertIn('pip3 install --disable-pip-version-check "bleak>=0.22"', sh)
        i = sh.index("FEHLER: Die Bluetooth-Bibliothek bleak")
        self.assertIn("exit 1", sh[i:i + 500])
        self.assertLess(sh.index('import bleak'), sh.index("systemctl stop pipbox.service"))   # bevor etwas verändert wird

    def test_service_keeps_its_hardening(self):
        unit = open(os.path.join(os.path.dirname(HERE), "install", "pipbox-dji.service"), encoding="utf-8").read()
        for line in ("User=pipbox", "NoNewPrivileges=yes", "ProtectSystem=strict", "SupplementaryGroups=bluetooth"):
            self.assertIn(line, unit)

    def test_old_endpoints_are_gone_and_the_command_endpoint_is_whitelisted(self):
        src = open(os.path.join(os.path.dirname(HERE), "server.py"), encoding="utf-8").read()
        for gone in ('"/api/dji/start"', '"/api/dji/stop"', '"/api/dji/wifi"', '"/api/dji/settings"'):
            self.assertNotIn(gone, src)
        self.assertIn('"/api/dji/cmd"', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
