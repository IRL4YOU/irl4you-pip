"""Tests für den Akkustand von Handys (phone_battery.py) und seine Anbindung im Bluetooth-Dienst und in der Oberfläche.

Ein nachgebautes Handy (Freisprechdienst über ein Socket-Paar) antwortet wie ein Android- oder iPhone-Handy. Nicht getestet (nur mit echtem Handy
und echtem BlueZ möglich): die Kopplung über D-Bus, sdptool und die Funkverbindung selbst.
"""
import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.modules.setdefault("dbus", types.ModuleType("dbus"))
import phone_battery as pb  # noqa: E402
import dji_daemon as dd  # noqa: E402
import server  # noqa: E402

PAGE = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
ADDR = "7C:A1:77:00:00:09"

SDP = """Inquiring ...
Searching for HFAG on 7C:A1:77:00:00:09 ...
Service Name: Handsfree Gateway
Service RecHandle: 0x10003
Service Class ID List:
  "Handsfree Audio Gateway" (0x111f)
  "Generic Audio" (0x1203)
Protocol Descriptor List:
  "L2CAP" (0x0100)
  "RFCOMM" (0x0003)
    Channel: 13
Profile Descriptor List:
  "Handsfree" (0x111e)
    Version: 0x0107

Service Name: Headset Gateway
Service Class ID List:
  "Headset Audio Gateway" (0x1112)
Protocol Descriptor List:
  "RFCOMM" (0x0003)
    Channel: 12
"""


def fake_phone(sock, cind_names, cind_vals, iphone=None, drop_after=None):
    """Ein Handy am anderen Ende des Socket-Paars: beantwortet die Befehle der Box wie ein Freisprech-Audio-Gateway."""
    def run():
        buf = b""
        n = 0
        while True:
            try:
                data = sock.recv(1024)
            except OSError:
                return
            if not data:
                return
            buf += data
            while b"\r" in buf:
                cmd, buf = buf.split(b"\r", 1)
                cmd = cmd.decode()
                n += 1
                if drop_after is not None and n > drop_after:
                    sock.close()
                    return
                if cmd.startswith("AT+BRSF"):
                    sock.sendall(b"\r\n+BRSF: 20\r\n\r\nOK\r\n")
                elif cmd == "AT+CIND=?":
                    sock.sendall(("\r\n+CIND: " + ",".join('("%s",(0-%d))' % x for x in cind_names) + "\r\n\r\nOK\r\n").encode())
                elif cmd == "AT+CIND?":
                    sock.sendall(("\r\n+CIND: " + ",".join(str(v) for v in cind_vals) + "\r\n\r\nOK\r\n").encode())
                elif cmd.startswith("AT+CMER"):
                    sock.sendall(b"\r\nOK\r\n")
                elif cmd.startswith("AT+XAPL"):
                    if iphone is None:
                        sock.sendall(b"\r\nERROR\r\n")
                    else:
                        sock.sendall(b"\r\n+XAPL=iPhone,6\r\n\r\nOK\r\n")
                        sock.sendall(("\r\n+IPHONEACCEV: 2,1,%d,2,0\r\n" % iphone).encode())
                else:
                    sock.sendall(b"\r\nERROR\r\n")
    def safe():
        try:
            run()
        except OSError:
            pass                                                                      # das Test-Socket wurde beim Aufräumen geschlossen
    t = threading.Thread(target=safe, daemon=True)
    t.start()
    return t


NAMES = [("service", 1), ("call", 1), ("callsetup", 3), ("callheld", 2), ("signal", 5), ("roam", 1), ("battchg", 5)]


class Reading(unittest.TestCase):
    def pair(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        return a, b

    def test_android_battery_comes_in_fifths(self):
        a, b = self.pair()
        fake_phone(b, NAMES, [1, 0, 0, 0, 4, 0, 3])
        r = pb.hfp_battery(a, timeout=3)
        self.assertEqual(r, {"percent": 60, "steps": 5})

    def test_full_and_empty(self):
        for v, want in ((5, 100), (0, 0)):
            a, b = self.pair()
            fake_phone(b, NAMES, [1, 0, 0, 0, 4, 0, v])
            self.assertEqual(pb.hfp_battery(a, timeout=3)["percent"], want)

    def test_iphone_battery_comes_in_ninths(self):
        a, b = self.pair()
        fake_phone(b, NAMES, [1, 0, 0, 0, 4, 0, 3], iphone=7)
        r = pb.hfp_battery(a, timeout=3)
        self.assertEqual(r, {"percent": 78, "steps": 9})                       # 7 von 9, genauer als die Fünftel

    def test_phone_without_battery_indicator_is_reported(self):
        a, b = self.pair()
        fake_phone(b, [("service", 1), ("call", 1)], [1, 0])
        with self.assertRaises(pb.PhoneError):
            pb.hfp_battery(a, timeout=3)

    def test_silent_phone_times_out_as_an_error_not_a_hang(self):
        a, b = self.pair()
        with self.assertRaises(pb.PhoneError):
            pb.hfp_battery(a, timeout=0.5)

    def test_link_dropping_midway_is_an_error(self):
        a, b = self.pair()
        fake_phone(b, NAMES, [1, 0, 0, 0, 4, 0, 3], drop_after=2)
        with self.assertRaises((pb.PhoneError, OSError)):
            pb.hfp_battery(a, timeout=1)

    def test_parsers(self):
        self.assertEqual(pb.parse_cind_names('+CIND: ("service",(0-1)),("battchg",(0-5))'), [("service", 1), ("battchg", 5)])
        self.assertEqual(pb.parse_cind_names('+CIND: ("call",(0,1)),("battchg",(0-5))'), [("call", 1), ("battchg", 5)])
        self.assertEqual(pb.parse_iphone_level("+IPHONEACCEV: 2,1,7,2,0"), 7)
        self.assertEqual(pb.parse_iphone_level("+IPHONEACCEV: 1,2,0"), None)         # nur "Dock", kein Akku
        self.assertEqual(pb.parse_iphone_level("+IPHONEACCEV: 1,1,12"), 9)
        self.assertIsNone(pb.parse_iphone_level("+CIND: 1,0"))

    def test_channel_is_taken_from_the_handsfree_record_not_the_headset(self):
        self.assertEqual(pb.parse_sdp_channel(SDP), 13)
        self.assertIsNone(pb.parse_sdp_channel("Searching for HFAG on 7C:A1:77:00:00:09 ...\n"))

    def test_read_battery_maps_connection_errors_to_messages(self):
        def refuse(addr, ch, mac):
            raise ConnectionRefusedError(111, "refused")

        def down(addr, ch, mac):
            raise OSError(112, "host down")
        with self.assertRaises(pb.PhoneError) as e:
            pb.read_battery(ADDR, channel=3, connect=refuse)
        self.assertIn("abgewiesen", str(e.exception))
        with self.assertRaises(pb.PhoneError) as e:
            pb.read_battery(ADDR, channel=3, connect=down)
        self.assertIn("nicht erreichbar", str(e.exception))

    def test_read_battery_closes_the_link_and_remembers_the_channel(self):
        a, b = socket.socketpair()
        fake_phone(b, NAMES, [1, 0, 0, 0, 4, 0, 5])
        asked = []
        r = pb.read_battery(ADDR, "hci0", "AA:BB:CC:00:00:01", connect=lambda ad, ch, mac: a, find=lambda ad, hci: asked.append(ad) or 7)
        self.assertEqual((r["percent"], r["channel"], asked), (100, 7, [ADDR]))
        self.assertEqual(a.fileno(), -1)                                              # Verbindung geschlossen: keine Dauerverbindung


class PhoneList(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.calls = []

    def make(self, reader):
        return pb.Phones(self.dir.name, reader=reader, hci=lambda: "hci0", adapter_mac=lambda h: "AA:BB:CC:00:00:01")

    def add(self, ph, addr=ADDR, name="Pixel"):
        ph.phones[addr] = {"name": name, "percent": None, "steps": None, "ts": 0.0, "error": "", "channel": None}

    def test_reading_stores_value_time_and_survives_a_restart(self):
        ph = self.make(lambda a, h, m, ch: {"percent": 60, "steps": 5, "channel": 13})
        self.add(ph)
        asyncio.run(ph.read(ADDR))
        s = ph.snapshot()["phones"][0]
        self.assertEqual((s["name"], s["percent"], s["steps"], s["error"]), ("Pixel", 60, 5, ""))
        self.assertLess(s["age"], 5)
        again = self.make(None)
        self.assertEqual(again.phones[ADDR]["percent"], 60)
        self.assertEqual(again.phones[ADDR]["channel"], 13)
        mode = os.stat(os.path.join(self.dir.name, "phones.json")).st_mode & 0o777
        if os.name == "posix":
            self.assertEqual(mode, 0o600)

    def test_next_reading_is_in_ten_minutes_and_a_failure_retries_sooner(self):
        import time
        ph = self.make(lambda a, h, m, ch: {"percent": 40, "steps": 5, "channel": 13})
        self.add(ph)
        asyncio.run(ph.read(ADDR))
        self.assertAlmostEqual(ph.due[ADDR] - time.monotonic(), pb.INTERVAL, delta=5)
        self.assertEqual(pb.INTERVAL, 600)

        def bad(a, h, m, ch):
            raise pb.PhoneError("Das Handy ist nicht erreichbar.")
        ph.reader = bad
        asyncio.run(ph.read(ADDR))
        self.assertAlmostEqual(ph.due[ADDR] - time.monotonic(), pb.RETRY, delta=5)
        s = ph.snapshot()["phones"][0]
        self.assertEqual((s["percent"], s["error"]), (40, "Das Handy ist nicht erreichbar."))   # der letzte Wert bleibt stehen, mit Fehlermeldung

    def test_unexpected_errors_never_escape(self):
        def boom(a, h, m, ch):
            raise RuntimeError("x")
        ph = self.make(boom)
        self.add(ph)
        asyncio.run(ph.read(ADDR))
        self.assertTrue(ph.snapshot()["phones"][0]["error"])

    def test_remove_forgets_the_phone(self):
        ph = self.make(None)
        self.add(ph)
        ph.save()
        ph.remove(ADDR)
        self.assertEqual(self.make(None).phones, {})

    def test_loop_skips_while_scanning_or_pairing_and_reads_due_phones_one_at_a_time(self):
        seen = []
        ph = self.make(lambda a, h, m, ch: seen.append(a) or {"percent": 50, "steps": 5, "channel": 1})
        self.add(ph)
        self.add(ph, "7C:A1:77:00:00:0A", "Zweites")
        ph.due = {ADDR: 0, "7C:A1:77:00:00:0A": 0}
        ph.busy = lambda: True

        async def go():
            real = asyncio.sleep

            async def fast(_):
                await real(0)
            asyncio.sleep = fast
            try:
                t = asyncio.ensure_future(ph.run())
                for _ in range(5):
                    await real(0.01)
                self.assertEqual(seen, [])                                           # beschäftigt: nichts abgefragt
                ph.busy = lambda: False
                for _ in range(20):
                    await real(0.02)
                t.cancel()
            finally:
                asyncio.sleep = real
        asyncio.run(go())
        self.assertEqual(sorted(seen), sorted([ADDR, "7C:A1:77:00:00:0A"]))

    def test_corrupt_file_is_ignored(self):
        with open(os.path.join(self.dir.name, "phones.json"), "w") as f:
            f.write("{kaputt")
        self.assertEqual(self.make(None).phones, {})


class Service(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.d = dd.Daemon(self.dir.name)

    def run_cmd(self, **req):
        return asyncio.run(self.d.handle(req))

    def test_state_carries_the_phones_and_the_pairing(self):
        s = self.run_cmd(cmd="state")
        self.assertEqual(s["phones"], [])
        self.assertEqual(s["phone_pairing"], {"active": False, "left": 0, "message": ""})

    def test_unknown_phone_is_refused_and_bad_address_too(self):
        for cmd in ("phone_read", "phone_remove"):
            self.assertIn("error", self.run_cmd(cmd=cmd, addr=ADDR))
            self.assertIn("error", self.run_cmd(cmd=cmd, addr="kein-mac"))

    def test_remove_cleans_bluez_and_the_list(self):
        self.d.phones.phones[ADDR] = {"name": "Pixel", "percent": 50, "steps": 5, "ts": 1.0, "error": "", "channel": 1}
        gone = []

        async def fake_cleanup(addr, remove=True):
            gone.append((addr, remove))
        old = dd.bluez_cleanup
        dd.bluez_cleanup = fake_cleanup
        try:
            self.assertEqual(self.run_cmd(cmd="phone_remove", addr=ADDR.lower()), {"ok": True})
        finally:
            dd.bluez_cleanup = old
        self.assertEqual(gone, [(ADDR, True)])
        self.assertEqual(self.d.phones.phones, {})

    def test_pairing_without_the_dbus_library_says_so(self):
        old = pb.MessageBus
        pb.MessageBus = None
        try:
            r = self.run_cmd(cmd="phone_pair")
        finally:
            pb.MessageBus = old
        self.assertIn("dbus-fast", r["error"])
        self.assertFalse(self.run_cmd(cmd="state")["phone_pairing"]["active"])

    def test_the_web_layer_lets_the_four_commands_through(self):
        for c in ("phone_pair", "phone_pair_stop", "phone_read", "phone_remove"):
            self.assertIn(c, server.DJI_COMMANDS)

    def test_the_loop_is_started_with_the_service(self):
        with open(os.path.join(ROOT, "dji_daemon.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("asyncio.ensure_future(self.phones.run())", src)

    def test_a_camera_is_never_taken_as_a_new_phone(self):
        self.d.cameras["D0:D0:4B:00:00:01"] = object()
        self.assertEqual(self.d.phones.taken(), ["D0:D0:4B:00:00:01"])


class Page(unittest.TestCase):
    def test_section_sits_under_connections_bluetooth(self):
        bt = PAGE[PAGE.index('id="net_bt"'):PAGE.index('id="net_ua"')]
        for need in ("Handyverbindungen", 'id="phonecard"', 'id="ph_pair"', "alle 10 Minuten", "keine Dauerverbindung"):
            self.assertIn(need, bt)

    def test_page_renders_and_commands_the_service(self):
        for need in ("function phoneRender(d)", '"phone_pair_stop":"phone_pair"', 'cmd:"phone_read"', 'cmd:"phone_remove"', "phoneRender(d);"):
            self.assertIn(need, PAGE)

    def test_the_fast_poll_runs_while_pairing_or_reading(self):
        self.assertIn("(djiData.phone_pairing||{}).active||(djiData.phones||[]).some(p=>p.reading)", PAGE)

    def test_installation_ships_the_module(self):
        inst = open(os.path.join(ROOT, "install", "install.sh"), encoding="utf-8").read()
        self.assertIn('install -m 644 "$HERE/phone_battery.py" /opt/pipbox/phone_battery.py', inst)
        self.assertIn("phone_battery.py", inst[inst.index("dji_changed=0"):inst.index("hdmi_changed=0")])    # Änderung -> Bluetooth-Dienst neu starten
        up = open(os.path.join(ROOT, "install", "pipbox-swupdate.py"), encoding="utf-8").read()
        self.assertGreaterEqual(up.count("phone_battery.py"), 2)


if __name__ == "__main__":
    unittest.main()
