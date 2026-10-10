"""Tests für IfaceMemory: Sendewege, Kameranetz und Kamera-Anschlüsse folgen der MAC-Adresse der Netzwerkkarte, wenn die Namen (eth0, eth1, usb0) wechseln.
Wunsch des Nutzers nach dem Neu-Flashen am 10. Oktober 2026: dort waren eth0 und eth1 gegenüber der alten Karte vertauscht."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import server  # noqa: E402

A, B, C, D = ("aa:bb:cc:00:00:0%d" % i for i in (1, 2, 3, 4))


def stores(d):
    srtla = server.SrtlaStore(os.path.join(d, "srtla.json"))
    srtla.data["settings"]["uplinks"] = ["eth0", "wlan0"]
    srtla.save()
    net = server.NetChoice(os.path.join(d, "camera-net.json"))
    net.iface = "eth0"
    cams = server.CameraStore(os.path.join(d, "cameras.json"), "publish", "", True)
    cams.ifaces = lambda: [{"iface": "eth0", "ip": "192.168.1.5"}, {"iface": "eth1", "ip": "192.168.80.5"}]
    cams.add("Handy", "cam-handy", "pip")
    cams.add("Drohne", "cam-drohne", "extra")
    cams.update(cams.cams[0]["id"], iface="eth1")
    return srtla, net, cams


def run(mem, srtla, net, cams, macs):
    with mock.patch.object(server.SettingsTransfer, "local_macs", staticmethod(lambda: macs)):
        return mem.reconcile(srtla, net, cams)


class Reconcile(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.s = stores(self.d)
        self.mem = server.IfaceMemory(os.path.join(self.d, "iface-macs.json"))

    def memory(self):
        return json.load(open(os.path.join(self.d, "iface-macs.json")))

    def test_first_run_only_remembers(self):
        self.assertEqual(run(self.mem, *self.s, {"eth0": A, "eth1": B}), {})
        self.assertEqual(self.memory(), {"eth0": A, "eth1": B})
        self.assertEqual(self.s[0].data["settings"]["uplinks"], ["eth0", "wlan0"])

    def test_swapped_names_move_every_reference(self):
        run(self.mem, *self.s, {"eth0": A, "eth1": B})                                 # früher: eth0 = A, eth1 = B
        m = run(self.mem, *self.s, {"eth0": B, "eth1": A})                             # jetzt vertauscht
        self.assertEqual(m, {"eth0": "eth1", "eth1": "eth0"})
        srtla, net, cams = self.s
        self.assertEqual(srtla.data["settings"]["uplinks"], ["eth1", "wlan0"])        # der Sendeweg gehört weiter zur Karte A
        self.assertEqual(json.load(open(srtla.path))["settings"]["uplinks"], ["eth1", "wlan0"])      # auch auf der Platte (der Sender liest die Datei)
        self.assertEqual(net.iface, "eth1")
        self.assertEqual(json.load(open(net.path)), {"iface": "eth1"})
        self.assertEqual(next(c for c in cams.cams if c["key"] == "cam-handy")["iface"], "eth0")
        self.assertNotIn("iface", next(c for c in cams.cams if c["key"] == "cam-drohne"))
        self.assertEqual(self.memory(), {"eth0": B, "eth1": A})

    def test_second_run_changes_nothing(self):
        run(self.mem, *self.s, {"eth0": A, "eth1": B})
        run(self.mem, *self.s, {"eth0": B, "eth1": A})
        self.assertEqual(run(self.mem, *self.s, {"eth0": B, "eth1": A}), {})
        self.assertEqual(self.s[0].data["settings"]["uplinks"], ["eth1", "wlan0"])

    def test_removed_card_changes_nothing(self):
        run(self.mem, *self.s, {"eth0": A, "eth1": B})
        self.assertEqual(run(self.mem, *self.s, {"eth0": A}), {})
        self.assertEqual(self.s[0].data["settings"]["uplinks"], ["eth0", "wlan0"])

    def test_a_different_card_under_the_old_name_is_not_followed(self):
        run(self.mem, *self.s, {"eth0": A, "usb0": C})
        self.assertEqual(run(self.mem, *self.s, {"eth0": A, "usb0": D}), {})            # C ist weg, D ist neu: kein Verweis wandert
        self.assertEqual(self.memory(), {"eth0": A, "usb0": D})

    def test_broken_memory_file_is_treated_as_empty(self):
        open(os.path.join(self.d, "iface-macs.json"), "w").write("kein json")
        self.assertEqual(run(self.mem, *self.s, {"eth0": A, "eth1": B}), {})
        self.assertEqual(self.memory(), {"eth0": A, "eth1": B})


class Wiring(unittest.TestCase):
    def test_started_in_main_but_not_in_demo(self):
        src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
        i = src.index("mem = IfaceMemory(os.path.join(args.state, \"iface-macs.json\"))")
        self.assertIn("if not args.demo:", src[i - 60:i])
        self.assertIn("threading.Thread(target=mem.run,", src[i:i + 600])


if __name__ == "__main__":
    unittest.main()
