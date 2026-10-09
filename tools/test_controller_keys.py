"""Tests für die Tasten der Controller (controller_keys.py): Tastenliste aus /proc, Lesen der Ereignisse, Zuordnung und die Funktionen
(Hauptbild tauschen, Ton), dazu die Schnittstelle und das Menü in der Oberfläche.

Nicht getestet (nur mit echtem Controller möglich): was ein bestimmter Controller als Tasten meldet.
"""
import json
import os
import struct
import sys
import tempfile
import threading
import time
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.modules.setdefault("dbus", types.ModuleType("dbus"))
import controller_keys as ck  # noqa: E402
import server  # noqa: E402

with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as _f:
    PAGE = _f.read()
PAD = "E4:11:22:33:44:55"
OTHER = "AA:BB:CC:00:11:22"

# 16 Gamepad-Tasten 0x130 bis 0x13f: Bits 48 bis 63 im Wort für die Codes 256 bis 319 (das höchste Wort steht zuerst)
PROC = """I: Bus=0003 Vendor=046d Product=c52b Version=0111
N: Name="Logitech USB Receiver"
U: Uniq=
H: Handlers=sysrq kbd event3
B: KEY=1000000000007 ff9f207ac14057ff

I: Bus=0005 Vendor=057e Product=2009 Version=0001
N: Name="Mini Controller"
P: Phys=aa:bb:cc:dd:ee:ff
U: Uniq=e4:11:22:33:44:55
H: Handlers=event7 js0
B: PROP=0
B: EV=1b
B: KEY=ffff000000000000 0 0 0 0
"""


class Parsing(unittest.TestCase):
    def test_bitmask_words_are_most_significant_first(self):
        self.assertEqual(ck.parse_bitmask("0 1"), {0})
        self.assertEqual(ck.parse_bitmask("1 0"), {64})
        self.assertEqual(ck.parse_bitmask("3 0 0 0 0"), {256, 257})

    def test_devices_and_their_declared_keys(self):
        devs = ck.parse_devices(PROC)
        self.assertEqual([d["event"] for d in devs], ["event3", "event7"])
        pad = devs[1]
        self.assertEqual((pad["name"], pad["uniq"]), ("Mini Controller", PAD))
        self.assertEqual(sorted(pad["keys"]), list(range(0x130, 0x140)))

    def test_names_are_the_usual_gamepad_and_keyboard_names(self):
        self.assertEqual([ck.key_name(c) for c in (0x133, 0x139, 0x136)], ["X", "RT", "LB"])
        self.assertEqual((ck.key_name(30), ck.key_name(2), ck.key_name(115)), ("A", "1", "Volume up"))
        self.assertEqual(ck.key_name(0x2FF), "Key 0x2ff")


class Keys(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.calls = []
        self.paired = {PAD: {"name": "Mini Controller"}}
        self.write_paired()
        self.proc = os.path.join(self.dir.name, "devices")
        with open(self.proc, "w") as f:
            f.write(PROC)
        self.k = ck.ControllerKeys(self.dir.name, self.calls.append, proc=self.proc, dev_dir=self.dir.name)
        self.addCleanup(self.k.stop)

    def write_paired(self):
        with open(os.path.join(self.dir.name, "controllers.json"), "w") as f:
            json.dump({"controllers": self.paired}, f)

    def test_scan_remembers_the_keys_and_marks_the_controller_present(self):
        self.k._scan()
        s = self.k.snapshot()
        c = s["controllers"][0]
        self.assertEqual((c["addr"], c["name"], c["present"]), (PAD, "Mini Controller", True))
        self.assertEqual([k["name"] for k in c["keys"]][:4], ["A", "B", "C", "X"])
        self.assertEqual(len(c["keys"]), 16)
        self.assertEqual([f["id"] for f in s["functions"]], ["main", "pip1", "pip2", "pip3", "mute", "audio_next"])
        again = ck.ControllerKeys(self.dir.name, self.calls.append, proc=self.proc)          # die Tastenliste bleibt auch ohne Controller
        os.unlink(self.proc)
        c = again.snapshot()["controllers"][0]
        self.assertEqual((c["present"], len(c["keys"])), (False, 16))

    def test_assignment_is_saved_validated_and_private(self):
        self.k.set_map(PAD, 0x139, "pip1")
        self.k.set_map(PAD, 0x133, "main")
        self.assertEqual(ck.ControllerKeys(self.dir.name, print).data[PAD]["map"], {str(0x139): "pip1", str(0x133): "main"})
        self.k.set_map(PAD, 0x139, "")
        self.assertEqual(self.k.snapshot()["controllers"][0]["map"], {str(0x133): "main"})
        for bad in ((OTHER, 0x133, "main"), (PAD, 99999, "main"), (PAD, True, "main"), (PAD, "x", "main"), (PAD, 0x133, "wurst")):
            with self.assertRaises(ValueError):
                self.k.set_map(*bad)
        if os.name == "posix":
            self.assertEqual(os.stat(self.k.path).st_mode & 0o777, 0o600)

    def test_a_removed_controller_loses_its_assignments(self):
        self.k.set_map(PAD, 0x133, "main")
        self.paired.clear()
        self.write_paired()
        self.assertEqual(self.k.snapshot()["controllers"], [])
        self.assertNotIn(PAD, ck.ControllerKeys(self.dir.name, print).data)

    def test_only_the_press_counts_and_only_when_assigned(self):
        self.k.set_map(PAD, 0x139, "pip1")
        self.assertIsNone(self.k.handle_event(PAD, ck.EV_KEY, 0x139, 0))      # Loslassen
        self.assertIsNone(self.k.handle_event(PAD, ck.EV_KEY, 0x139, 2))      # Wiederholen
        self.assertIsNone(self.k.handle_event(PAD, 3, 0x139, 1))              # andere Art von Ereignis
        self.assertIsNone(self.k.handle_event(PAD, ck.EV_KEY, 0x133, 1))      # nicht zugeordnet
        self.assertEqual(self.k.last["code"], 0x133)                          # aber für die Markierung in der Liste gemerkt
        self.assertEqual(self.k.handle_event(PAD, ck.EV_KEY, 0x139, 1), "pip1")
        time.sleep(0.1)
        self.assertEqual(self.calls, ["pip1"])

    def test_a_doubled_press_is_one_press(self):
        self.k.set_map(PAD, 0x139, "mute")
        self.k.handle_event(PAD, ck.EV_KEY, 0x139, 1)
        self.k.handle_event(PAD, ck.EV_KEY, 0x139, 1)
        time.sleep(0.1)
        self.assertEqual(self.calls, ["mute"])

    def test_a_failing_function_never_kills_the_reader(self):
        def boom(fn):
            raise RuntimeError("x")
        self.k.act = boom
        self.k.set_map(PAD, 0x139, "mute")
        self.k.handle_event(PAD, ck.EV_KEY, 0x139, 1)
        time.sleep(0.1)

    @unittest.skipUnless(os.name == "posix", "select() auf Dateien gibt es nur unter Linux")
    def test_events_are_read_from_the_device_file(self):
        node = os.path.join(self.dir.name, "event7")
        with open(node, "wb") as f:
            f.write(struct.pack("llHHi", 0, 0, ck.EV_KEY, 0x139, 1) + struct.pack("llHHi", 0, 0, ck.EV_KEY, 0x139, 0))
        self.k.set_map(PAD, 0x139, "audio_next")
        t = threading.Thread(target=self.k.run, daemon=True)
        t.start()
        for _ in range(60):
            if self.calls:
                break
            time.sleep(0.05)
        self.assertEqual(self.calls, ["audio_next"])
        self.assertEqual(self.k.snapshot()["last"]["code"], 0x139)


class Gamepad(unittest.TestCase):
    """Xbox-Controller: LT/RT und das Steuerkreuz kommen als Achsen, nicht als Tasten."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.calls = []
        with open(os.path.join(self.dir.name, "controllers.json"), "w") as f:
            json.dump({"controllers": {PAD: {"name": "Xbox Wireless Controller"}}}, f)
        self.k = ck.ControllerKeys(self.dir.name, self.calls.append, controllers_path=os.path.join(self.dir.name, "controllers.json"),
                                   proc=os.path.join(self.dir.name, "nix"))
        self.k.absmax[PAD] = {2: 1023, 5: 1023}

    def test_a_controller_never_seen_shows_the_usual_gamepad_layout_and_keeps_choices(self):
        c = self.k.snapshot()["controllers"][0]
        self.assertTrue(c["assumed"])
        self.assertEqual([k["name"] for k in c["keys"]], ["A", "B", "X", "Y", "LB", "RB", "LT", "RT", "Select", "Start", "Mode", "L3", "R3",
                                                          "D-pad up", "D-pad down", "D-pad left", "D-pad right"])
        self.k.set_map(PAD, ck.VK_RT, "pip1")                                   # schon vor der ersten Verbindung zuordnen
        self.k.data[PAD]["keys"] = [0x130, ck.VK_RT]                            # später meldet das Gerät seine echten Tasten
        c = self.k.snapshot()["controllers"][0]
        self.assertFalse(c["assumed"])
        self.assertEqual(c["map"], {str(ck.VK_RT): "pip1"})

    def test_axes_of_the_device_become_virtual_keys(self):
        self.assertEqual(ck.virtual_keys({0, 1, 2, 5, 16, 17}), {ck.VK_LT, ck.VK_RT, ck.VK_UP, ck.VK_DOWN, ck.VK_LEFT, ck.VK_RIGHT})
        self.assertEqual(ck.virtual_keys({9, 10}), {ck.VK_LT, ck.VK_RT})                    # ABS_GAS / ABS_BRAKE
        self.assertEqual(ck.virtual_keys({0, 1}), set())
        text = "\n".join(['N: Name="Xbox"', "U: Uniq=e4:11:22:33:44:55", "H: Handlers=event9", "B: ABS=3003f"]) + "\n"
        devs = ck.parse_devices(text)
        self.assertEqual(devs[0]["abs"], {0, 1, 2, 3, 4, 5, 16, 17})

    def test_trigger_counts_once_when_pushed_past_40_percent_and_again_only_after_release(self):
        self.k.set_map(PAD, ck.VK_RT, "pip1")
        for v in (0, 100, 300, 410, 600, 1023, 900, 600, 300):                       # drücken, halten, ein Stück zurück
            self.k.dispatch(PAD, ck.EV_ABS, 5, v)
        time.sleep(0.1)
        self.assertEqual(self.calls, ["pip1"])
        self.k._last_act.clear()                                                    # (die Entprellung gilt nur für schnelle Doppelmeldungen)
        for v in (100, 0, 500, 1000):                                               # ganz losgelassen, dann wieder gedrückt
            self.k.dispatch(PAD, ck.EV_ABS, 5, v)
        time.sleep(0.1)
        self.assertEqual(self.calls, ["pip1", "pip1"])

    def test_left_trigger_and_dpad(self):
        for code, fn in ((ck.VK_LT, "mute"), (ck.VK_LEFT, "main"), (ck.VK_DOWN, "pip2")):
            self.k.set_map(PAD, code, fn)
        self.k.dispatch(PAD, ck.EV_ABS, 2, 1023)
        self.k.dispatch(PAD, ck.EV_ABS, 16, -1)
        self.k.dispatch(PAD, ck.EV_ABS, 17, 1)
        self.k.dispatch(PAD, ck.EV_ABS, 16, 0)
        self.k.dispatch(PAD, ck.EV_ABS, 17, 0)
        time.sleep(0.15)
        self.assertEqual(sorted(self.calls), ["main", "mute", "pip2"])
        self.assertEqual(self.k.last["code"], ck.VK_DOWN)

    def test_a_default_range_is_assumed_when_the_device_does_not_tell(self):
        self.k.absmax.clear()
        self.k.set_map(PAD, ck.VK_RT, "mute")
        self.k.dispatch(PAD, ck.EV_ABS, 5, 50)
        time.sleep(0.05)
        self.assertEqual(self.calls, [])
        self.k.dispatch(PAD, ck.EV_ABS, 5, 200)
        time.sleep(0.05)
        self.assertEqual(self.calls, ["mute"])

    def test_the_gamepad_list_survives_a_restart_with_the_real_keys(self):
        self.k.data[PAD] = {"keys": [0x130, 0x133, ck.VK_RT], "map": {}}
        self.k.save()
        again = ck.ControllerKeys(self.dir.name, print, controllers_path=self.k.controllers_path)
        self.assertEqual(again.data[PAD]["keys"], [0x130, 0x133, ck.VK_RT])


class Cams:
    def __init__(self, roles):
        self.cams = [{"key": k, "role": r} for k, r in roles.items()]

    def listing(self, host):
        return [{"key": c["key"], "state": "live"} for c in self.cams]


class Pipe:
    def __init__(self, cfg):
        self.cfg = cfg
        self.swaps = []

    def swap_main_pip(self, with_key=None):
        self.swaps.append(with_key)
        slot = next(k for k in ("pip", "pip2", "pip3") if self.cfg.get(k) == with_key)
        self.cfg["main"], self.cfg[slot] = self.cfg[slot], self.cfg["main"]
        return False


class Send:
    def __init__(self, mute=False, nxt="pip"):
        self.views = []
        self.mute, self.nxt = mute, nxt

    def always_live(self):
        return True

    def swap_live(self):
        return True

    def footer(self):
        return {"audio": {"mute": self.mute, "next": self.nxt}}

    def change_view(self, **kw):
        self.views.append(kw)


class Actions(unittest.TestCase):
    def setUp(self):
        self.cams = Cams({"cam1": "main", "cam2": "pip", "cam3": "extra", "cam4": "extra"})
        self.pipe = Pipe({"type": "pip", "main": "cam1", "pip": "cam2", "pip2": "cam3", "pip3": "cam4"})
        self.send = Send()

    def act(self, fn):
        server.controller_action(fn, self.pipe, self.cams, self.send)

    def test_a_small_picture_key_makes_that_camera_the_main_picture_and_a_second_press_swaps_back(self):
        self.act("pip1")
        self.assertEqual((self.pipe.cfg["main"], self.pipe.cfg["pip"]), ("cam2", "cam1"))
        self.act("pip1")
        self.assertEqual((self.pipe.cfg["main"], self.pipe.cfg["pip"]), ("cam1", "cam2"))
        self.act("pip3")
        self.assertEqual((self.pipe.cfg["main"], self.pipe.cfg["pip3"]), ("cam4", "cam1"))

    def test_main_key_brings_the_main_role_camera_back_and_does_nothing_if_it_is_already_main(self):
        self.act("main")
        self.assertEqual(self.pipe.swaps, [])
        self.act("pip2")                                                         # cam3 ist jetzt Hauptbild
        self.act("main")
        self.assertEqual(self.pipe.cfg["main"], "cam1")

    def test_empty_slots_and_single_picture_do_nothing(self):
        self.pipe.cfg["pip2"] = ""
        self.act("pip2")
        self.pipe.cfg["type"] = "single"
        self.act("pip1")
        self.act("main")
        self.assertEqual(self.pipe.swaps, [])

    def test_offline_camera_is_not_swapped_in(self):
        self.cams.listing = lambda host: [{"key": "cam2", "state": "offline"}]
        with self.assertRaises(ValueError):
            self.act("pip1")
        self.assertEqual(self.pipe.cfg["main"], "cam1")

    def test_mute_toggles_and_next_source_follows_the_footer(self):
        self.act("mute")
        self.send.mute = True
        self.act("mute")
        self.act("audio_next")
        self.assertEqual(self.send.views, [{"mute": True}, {"mute": False}, {"audio": "pip"}])


class Surface(unittest.TestCase):
    def test_the_api_serves_and_sets_the_assignment_in_the_demo(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        k = ck.ControllerKeys(d.name, lambda fn: None, demo=True)
        s = k.snapshot()
        self.assertEqual(s["controllers"][0]["name"], "Mini Controller")
        self.assertTrue(s["controllers"][0]["present"])
        k.set_map(ck.DEMO_ADDR, 0x136, "pip2")
        self.assertEqual(k.snapshot()["controllers"][0]["map"], {str(0x136): "pip2"})

    def test_routes_exist(self):
        with open(os.path.join(ROOT, "server.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(src.count('"/api/controller-keys"'), 2)
        self.assertIn("scene_swap(self.pipeline, self.cams, self.send, d.get(\"with\"))", src)
        self.assertIn("Handler.ckeys.start()", src)

    def test_menu_card_is_hidden_until_a_controller_exists_and_joins_the_menu_system(self):
        card = PAGE[PAGE.index('id="c_ctl"'):PAGE.index('id="rcard"')]
        self.assertIn("hidden", PAGE[PAGE.index('<details class="card wide" id="c_ctl"'):][:80])
        self.assertIn('id="ctl_list"', card)
        self.assertIn("details.card[hidden]{display:none!important}", PAGE)
        self.assertIn("!el.hidden", PAGE)                                              # versteckte Karte steht nicht in den Menülisten
        self.assertIn('pb-menus-changed', PAGE)
        self.assertTrue(PAGE.index('id="pipecard"') < PAGE.index('id="c_ctl"') < PAGE.index('id="rcard"'))

    def test_page_polls_lists_keys_and_saves_the_choice(self):
        for need in ('"/api/controller-keys"', "ctlrow", "l.age<2", "fn:sel.value", "card.hidden=!show"):
            self.assertIn(need, PAGE)

    def test_installation_ships_module_and_input_group(self):
        with open(os.path.join(ROOT, "install", "install.sh"), encoding="utf-8") as f:
            inst = f.read()
        with open(os.path.join(ROOT, "install", "pipbox.service"), encoding="utf-8") as f:
            unit = f.read()
        with open(os.path.join(ROOT, "install", "pipbox-swupdate.py"), encoding="utf-8") as f:
            up = f.read()
        self.assertIn('install -m 644 "$HERE/controller_keys.py" /opt/pipbox/controller_keys.py', inst)
        self.assertIn("SupplementaryGroups=bluetooth input", unit)                      # Lesezugriff auf /dev/input
        self.assertGreaterEqual(up.count("controller_keys.py"), 1)


if __name__ == "__main__":
    unittest.main()
