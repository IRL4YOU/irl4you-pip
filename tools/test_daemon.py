"""Tests für den Wächter des Kamera-Dienstes (dji_daemon.py) und die Verbindungssperre (dji.py). Ohne Bluetooth."""
import json
import os
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

PARAMS = {"model": "osmoAction4", "ssid": "x", "password": "y", "url": "rtmp://10.0.0.1:1935/publish/dji-aaaaaa",
          "res": "1080p", "fps": 30, "kbps": 10000, "codec": "AVC", "stab": "off"}


class FakeDji:
    def __init__(self):
        self.sessions, self.started, self.stopped = {}, [], []

    def status(self):
        return {"sessions": {k: {"state": v} for k, v in self.sessions.items()}, "devices": []}

    def start(self, addr, *a):
        self.started.append(addr)
        self.sessions[addr] = "connecting"

    def stop(self, addr=None):
        self.stopped.append(addr)


class Supervisor(unittest.TestCase):
    def make(self, cams=("AA:AA", "BB:BB"), iface_up=True):
        self.tmp = tempfile.mkdtemp()
        json.dump({"iface": "eth9"}, open(os.path.join(self.tmp, "camera-net.json"), "w"))
        self.fake = FakeDji()
        dm = dd.Daemon(self.tmp, self.fake)
        dm.desired = {c: dict(PARAMS, url=f"rtmp://10.0.0.1:1935/publish/dji-{c[:2].lower()}") for c in cams}
        dm.ipfn = lambda: "10.0.0.1"
        self.up = [iface_up]
        p1 = mock.patch.object(dd, "iface_ip", lambda name: "10.0.0.1" if self.up[0] else None)
        p2 = mock.patch.object(dd, "publishing_keys", lambda: set())
        p3 = mock.patch.object(dd.time, "sleep", lambda s: None)
        for p in (p1, p2, p3):
            p.start()
            self.addCleanup(p.stop)
        for c in cams:
            self.fake.sessions[c] = "failed"
        return dm

    def test_one_new_connection_per_round(self):
        dm = self.make()
        dm.supervise_once(100)
        self.assertEqual(self.fake.started, ["AA:AA"])            # nur eine Kamera
        self.fake.sessions["AA:AA"] = "connecting"
        dm.supervise_once(105)
        self.assertEqual(self.fake.started, ["AA:AA", "BB:BB"])   # die nächste im folgenden Durchlauf

    def test_no_attempts_while_network_is_down(self):
        dm = self.make(iface_up=False)
        for t in range(100, 400, 5):
            dm.supervise_once(t)
        self.assertEqual(self.fake.started, [])
        self.assertEqual(dm.tries, {})                             # Wartezeiten wurden nicht verbraucht

    def test_network_return_resets_backoff_and_waits(self):
        dm = self.make(cams=("AA:AA",))
        dm.tries["AA:AA"] = (5, 10 ** 9)                           # lange Wartezeit aufgelaufen
        self.up[0] = False
        dm.supervise_once(100)                                     # Router weg
        self.up[0] = True
        dm.supervise_once(110)                                     # Router wieder da: zurücksetzen, noch warten
        self.assertEqual(self.fake.started, [])
        dm.supervise_once(110 + dd.NET_SETTLE - 1)
        self.assertEqual(self.fake.started, [])
        dm.supervise_once(110 + dd.NET_SETTLE + 1)
        self.assertEqual(self.fake.started, ["AA:AA"])

    def test_normal_backoff_still_grows(self):
        dm = self.make(cams=("AA:AA",))
        dm.supervise_once(100)
        self.fake.sessions["AA:AA"] = "failed"
        dm.supervise_once(105)                                     # noch in der Wartezeit (10 s)
        self.assertEqual(len(self.fake.started), 1)
        dm.supervise_once(111)
        self.assertEqual(len(self.fake.started), 2)
        self.assertEqual(dm.tries["AA:AA"][0], 2)


class ConnectionLock(unittest.TestCase):
    def make(self):
        d = dji.Dji.__new__(dji.Dji)
        d._conn_lock = threading.Lock()
        d._set = lambda *a, **k: None
        return d

    def test_second_camera_waits_for_the_first(self):
        d = self.make()
        order = []
        stop = threading.Event()
        with mock.patch.object(dji.time, "sleep", lambda s: None):
            d._acquire_conn("A", stop)
            order.append("A holt")
            t = threading.Thread(target=lambda: (d._acquire_conn("B", threading.Event()), order.append("B holt")))
            t.start()
            time.sleep(0.8)
            self.assertEqual(order, ["A holt"])                    # B wartet noch
            d._release_conn()
            t.join(3)
        self.assertEqual(order, ["A holt", "B holt"])

    def test_waiting_camera_can_be_stopped(self):
        d = self.make()
        d._conn_lock.acquire()
        stop = threading.Event()
        stop.set()
        with self.assertRaises(RuntimeError):
            d._acquire_conn("B", stop)


if __name__ == "__main__":
    unittest.main(verbosity=2)
