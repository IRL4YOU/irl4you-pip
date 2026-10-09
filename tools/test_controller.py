"""Tests für die Bluetooth-Controller (controllers.py) und ihre Anbindung im Bluetooth-Dienst und in der Oberfläche.

Nicht getestet (nur mit echtem Controller und echtem BlueZ möglich): Suche und Kopplung über D-Bus und das Verhalten der Geräte selbst.
"""
import asyncio
import os
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.modules.setdefault("dbus", types.ModuleType("dbus"))
import controllers as ct  # noqa: E402
import dji_daemon as dd  # noqa: E402
import server  # noqa: E402

with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as _f:
    PAGE = _f.read()


class V:
    """Wie eine dbus-Variante: der Wert steckt in .value."""

    def __init__(self, v):
        self.value = v


def dev(addr, name="", rssi=None, uuids=(), icon="", cls=None, paired=False, connected=False, appearance=None):
    d = {"Address": V(addr), "Alias": V(name or addr.replace(":", "-")), "Paired": V(paired), "Connected": V(connected), "UUIDs": V(list(uuids))}
    if name:
        d["Name"] = V(name)
    if rssi is not None:
        d["RSSI"] = V(rssi)
    if icon:
        d["Icon"] = V(icon)
    if cls is not None:
        d["Class"] = V(cls)
    if appearance is not None:
        d["Appearance"] = V(appearance)
    return d


def objs(*devs, battery=None):
    out = {}
    for i, d in enumerate(devs):
        ifs = {"org.bluez.Device1": d}
        if battery and d["Address"].value in battery:
            ifs["org.bluez.Battery1"] = {"Percentage": V(battery[d["Address"].value])}
        out["/org/bluez/hci0/dev_%d" % i] = ifs
    return out


PAD = "E4:11:22:33:44:55"
LAMP = "AA:BB:CC:00:11:22"
PHONE = "7C:A1:77:00:00:09"


class Recognition(unittest.TestCase):
    def test_input_devices_are_recognised_by_service_icon_class_or_appearance(self):
        self.assertTrue(ct.is_input_device({"UUIDs": ["00001812-0000-1000-8000-00805f9b34fb"]}))
        self.assertTrue(ct.is_input_device({"Icon": "input-gaming"}))
        self.assertTrue(ct.is_input_device({"Class": 0x002508}))                  # Hauptklasse Peripheral, Gamepad
        self.assertTrue(ct.is_input_device({"Appearance": 0x03C4}))               # HID-Gamepad
        self.assertFalse(ct.is_input_device({"UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"], "Class": 0x240404}))   # Kopfhörer
        self.assertFalse(ct.is_input_device({}))

    def test_scan_list_has_only_devices_seen_right_now_and_puts_controllers_first(self):
        o = objs(dev(LAMP, "LED_BLE", rssi=-40), dev(PAD, "Mini Controller", rssi=-70, icon="input-keyboard"),
                 dev("11:22:33:44:55:66", "Alt", rssi=None, icon="input-keyboard"))      # ohne Signal: nur noch im Zwischenspeicher von BlueZ
        res = ct.scan_results(o, only_inputs=False)
        self.assertEqual([r["addr"] for r in res], [PAD, LAMP])                      # Controller zuerst, trotz schwächerem Signal
        self.assertEqual(res[0], {"addr": PAD, "name": "Mini Controller", "rssi": -70, "input": True, "paired": False})
        self.assertEqual([r["addr"] for r in ct.scan_results(o, only_inputs=True)], [PAD])

    def test_cameras_phones_and_known_devices_are_left_out(self):
        o = objs(dev(PAD, "Pad", rssi=-50, icon="input-gaming"), dev(PHONE, "Pixel", rssi=-40))
        self.assertEqual([r["addr"] for r in ct.scan_results(o, known=[PHONE.lower()], only_inputs=False)], [PAD])

    def test_unnamed_devices_get_an_empty_name_not_their_address(self):
        res = ct.scan_results(objs(dev(PAD, "", rssi=-50)), only_inputs=False)
        self.assertEqual(res[0]["name"], "")

    def test_live_state_reads_connection_and_battery(self):
        o = objs(dev(PAD, "Pad", connected=True, paired=True), dev(LAMP, "x", connected=True), battery={PAD: 87})
        self.assertEqual(ct.live_state(o, [PAD]), {PAD: {"connected": True, "battery": 87}})
        self.assertEqual(ct.live_state(objs(dev(PAD, "Pad", paired=True)), [PAD]), {PAD: {"connected": False, "battery": None}})


class List(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.c = ct.Controllers(self.dir.name)

    def test_snapshot_merges_the_live_state_and_hides_paired_devices_from_the_search_list(self):
        self.c.items[PAD] = {"name": "Mini Controller"}
        self.c.live = {PAD: {"connected": True, "battery": 55}}
        self.c.found = [{"addr": PAD, "name": "Mini Controller", "rssi": -40, "input": True, "paired": True},
                        {"addr": LAMP, "name": "Lampe", "rssi": -60, "input": False, "paired": False}]
        s = self.c.snapshot()
        self.assertEqual(s["controllers"], [{"addr": PAD, "name": "Mini Controller", "connected": True, "battery": 55, "pairing": False}])
        self.assertEqual([r["addr"] for r in s["ctrl_found"]], [LAMP])
        self.assertEqual(s["ctrl_scan"], {"active": False, "left": 0, "message": ""})

    def test_list_survives_a_restart_and_a_remove(self):
        self.c.items[PAD] = {"name": "Mini Controller"}
        self.c.save()
        again = ct.Controllers(self.dir.name)
        self.assertEqual(again.items, {PAD: {"name": "Mini Controller"}})
        if os.name == "posix":
            self.assertEqual(os.stat(self.c.path).st_mode & 0o777, 0o600)
        again.remove(PAD.lower())
        self.assertEqual(ct.Controllers(self.dir.name).items, {})

    def test_corrupt_file_is_ignored(self):
        with open(self.c.path, "w") as f:
            f.write("{kaputt")
        self.assertEqual(ct.Controllers(self.dir.name).items, {})

    def test_without_the_dbus_library_search_and_pairing_say_so(self):
        old = ct.pb.MessageBus
        ct.pb.MessageBus = None
        try:
            for call in (self.c.start_scan(), self.c.pair(PAD)):
                with self.assertRaises(ct.ControllerError) as e:
                    asyncio.run(call)
                self.assertIn("dbus-fast", str(e.exception))
        finally:
            ct.pb.MessageBus = old
        self.assertFalse(self.c.scan["active"])
        self.assertEqual(self.c.pairing, "")


class Service(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.d = dd.Daemon(self.dir.name)

    def cmd(self, **req):
        return asyncio.run(self.d.handle(req))

    def test_state_carries_the_controllers(self):
        s = self.cmd(cmd="state")
        self.assertEqual((s["controllers"], s["ctrl_found"], s["ctrl_message"]), ([], [], ""))
        self.assertFalse(s["ctrl_scan"]["active"])

    def test_bad_or_unknown_addresses_are_refused(self):
        self.assertIn("error", self.cmd(cmd="ctrl_pair", addr="nix"))
        self.assertIn("error", self.cmd(cmd="ctrl_remove", addr="nix"))
        self.assertIn("error", self.cmd(cmd="ctrl_remove", addr=PAD))

    def test_remove_cleans_bluez_and_the_list(self):
        self.d.ctrl.items[PAD] = {"name": "Pad"}
        gone = []

        async def fake(addr, remove=True):
            gone.append((addr, remove))
        old = dd.bluez_cleanup
        dd.bluez_cleanup = fake
        try:
            self.assertEqual(self.cmd(cmd="ctrl_remove", addr=PAD.lower()), {"ok": True})
        finally:
            dd.bluez_cleanup = old
        self.assertEqual((gone, self.d.ctrl.items), ([(PAD, True)], {}))

    def test_pairing_runs_in_the_background_and_a_failure_shows_up_as_a_message(self):
        async def go():
            async def boom(addr):
                raise ct.ControllerError("Das Koppeln hat nicht geklappt.")
            self.d.ctrl.pair = boom
            r = await self.d.handle({"cmd": "ctrl_pair", "addr": PAD})
            await asyncio.sleep(0.05)
            return r, self.d.ctrl.snapshot()["ctrl_message"]
        r, msg = asyncio.run(go())
        self.assertEqual(r, {"ok": True})
        self.assertEqual(msg, "Das Koppeln hat nicht geklappt.")

    def test_second_pairing_while_one_runs_is_refused(self):
        self.d.ctrl.pairing = PAD
        self.assertIn("error", self.cmd(cmd="ctrl_pair", addr=LAMP))

    def test_cameras_and_phones_never_show_up_as_controllers(self):
        self.d.cameras["D0:D0:4B:00:00:01"] = object()
        self.d.phones.phones[PHONE] = {"name": "Pixel"}
        self.assertEqual(sorted(self.d.ctrl.taken()), sorted(["D0:D0:4B:00:00:01", PHONE]))

    def test_the_web_layer_and_the_loop(self):
        for c in ("ctrl_scan", "ctrl_pair", "ctrl_remove"):
            self.assertIn(c, server.DJI_COMMANDS)
        with open(os.path.join(ROOT, "dji_daemon.py"), encoding="utf-8") as f:
            self.assertIn("asyncio.ensure_future(self.ctrl.run())", f.read())


class Page(unittest.TestCase):
    def test_heading_comes_after_the_phones_and_bluetooth_has_its_plain_name(self):
        bt = PAGE[PAGE.index('id="net_bt"'):PAGE.index('id="net_ua"')]
        self.assertIn('<summary>Bluetooth</summary>', bt)
        self.assertNotIn("für DJI-Kameras", bt[:80])
        self.assertTrue(bt.index("Handyverbindungen") < bt.index(">Controller<") < bt.index('id="ctrlcard"'))
        for need in ('id="ct_scan"', 'id="ct_all"', 'id="ctrlfound"', "Nach Controllern suchen", "Alle Geräte zeigen"):
            self.assertIn(need, bt)

    def test_page_searches_lists_pairs_and_removes(self):
        for need in ("function ctrlRender(d)", 'cmd:"ctrl_scan"', 'cmd:"ctrl_pair"', 'cmd:"ctrl_remove"', "ctrlRender(d);", "all||r.input"):
            self.assertIn(need, PAGE)

    def test_the_fast_poll_runs_while_searching_or_pairing(self):
        self.assertIn("(djiData.ctrl_scan||{}).active||(djiData.controllers||[]).some(c=>c.pairing)", PAGE)

    def test_installation_ships_the_module(self):
        with open(os.path.join(ROOT, "install", "install.sh"), encoding="utf-8") as f:
            inst = f.read()
        with open(os.path.join(ROOT, "install", "pipbox-swupdate.py"), encoding="utf-8") as f:
            up = f.read()
        self.assertIn('install -m 644 "$HERE/controllers.py" /opt/pipbox/controllers.py', inst)
        self.assertIn("controllers.py", inst[inst.index("dji_changed=0"):inst.index("hdmi_changed=0")])
        self.assertGreaterEqual(up.count("controllers.py"), 2)


if __name__ == "__main__":
    unittest.main()
