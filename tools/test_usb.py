#!/usr/bin/env python3
"""Tests für die Meldung "USB-Gerät getrennt" (server.UsbWatch) mit Beispielzeilen aus dem Kernel-Protokoll."""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import server

DMESG = """2026-10-07T20:00:01,100000+00:00 usb 2-1: new high-speed USB device number 2 using xhci-hcd
2026-10-07T20:00:01,200000+00:00 usb 2-1: Product: 802.11ac NIC
2026-10-07T20:00:01,300000+00:00 usb 5-1.4: Product: ASUS USB-BT500
2026-10-07T21:04:48,500000+00:00 usb usb2-port1: disabled by hub (EMI?)
2026-10-07T21:04:48,900000+00:00 usb 2-1: USB disconnect, device number 2
2026-10-07T21:30:00,000000+00:00 usb 5-1.4: USB disconnect, device number 3
2026-10-07T21:31:00,000000+00:00 usb 9-9: USB disconnect, device number 4
2026-10-07T21:32:00,000000+00:00 something else entirely
kaputte Zeile ohne Zeit
"""


def watch(text=DMESG, now=None, path=None):
    t = [now if now is not None else time.mktime((2026, 10, 7, 23, 0, 0, 0, 0, 0))]
    w = server.UsbWatch(path or tempfile.mktemp(), runner=lambda: text, clock=lambda: t[0])
    return w, t


class Usb(unittest.TestCase):
    def test_events_are_found_merged_and_classified(self):
        w, _ = watch()
        ev = w._parse(DMESG)
        self.assertEqual(len(ev), 3)                                                   # Abschaltung und Trennung am selben Anschluss: ein Ereignis
        by = {e["port"]: e for e in ev}
        self.assertEqual(by["2-1"]["cat"], "wlan")
        self.assertEqual(by["2-1"]["why"], "emi")                                      # "EMI?" bleibt als Grund erhalten
        self.assertEqual(by["5-1.4"]["cat"], "bt")
        self.assertEqual(by["9-9"]["cat"], "other")                                    # unbekannter Anschluss
        self.assertTrue(abs(by["2-1"]["t"] - 1791407088.5) < 4000)                      # Zeit stimmt (Größenordnung, Ortszeit-unabhängig)

    def test_categories(self):
        c = server.UsbWatch.category
        self.assertEqual(c("802.11ac NIC"), "wlan")
        self.assertEqual(c("GL-iNet Mudi RNDIS"), "net")
        self.assertEqual(c("USB Camera"), "cam")
        self.assertEqual(c("Bluetooth Radio"), "bt")
        self.assertEqual(c("Irgendwas"), "other")
        self.assertEqual(c(""), "other")

    def test_alerts_show_only_the_last_24_hours_and_at_most_two(self):
        w, t = watch()
        t[0] = 1791410000.0
        for e in w._parse(DMESG):
            e["t"] = t[0] - 600                                                        # alle "vor 10 Minuten"
            w.events.append(e)
        w.last = t[0]                                                                  # nicht neu lesen
        al = w.alerts()
        self.assertEqual(len(al), 2)
        self.assertTrue(all(a["kind"] == "usb" and a["level"] == "warn" for a in al))
        t[0] += 25 * 3600
        w.last = t[0]
        self.assertEqual(w.alerts(), [])                                               # nach 24 Stunden ist die Meldung weg

    BACK = """2026-10-08T18:10:51,000000+00:00 usb 2-1: USB disconnect, device number 2
2026-10-08T18:10:52,500000+00:00 usb 2-1: new high-speed USB device number 3 using ehci-platform
2026-10-08T18:10:52,700000+00:00 usb 2-1: Product: 802.11ac NIC
"""

    def test_a_device_that_comes_back_is_marked_and_the_alert_goes_away_after_ten_minutes(self):
        w, t = watch(self.BACK)
        ev = w._parse(self.BACK)
        self.assertEqual(len(ev), 1)
        self.assertTrue(ev[0]["back"] > ev[0]["t"])                                    # am selben Anschluss neu erkannt
        t[0] = ev[0]["back"] + 60
        w.last = 0.0
        al = w.alerts()
        self.assertEqual(len(al), 1)
        self.assertTrue(al[0]["back"])                                                 # "wieder da" steht dabei
        t[0] = ev[0]["back"] + server.UsbWatch.BACK_SHOW + 5
        w.last = t[0]
        self.assertEqual(w.alerts(), [])                                               # nach zehn Minuten weg, ohne 24 Stunden zu warten

    def test_a_device_that_does_not_come_back_stays_until_dismissed_or_24_hours(self):
        text = "2026-10-08T18:10:51,000000+00:00 usb 2-1: USB disconnect, device number 2\n"
        w, t = watch(text)
        ev = w._parse(text)[0]
        self.assertNotIn("back", ev)
        t[0] = ev["t"] + 3 * 3600
        w.last = 0.0
        al = w.alerts()
        self.assertEqual(len(al), 1)
        self.assertFalse(al[0]["back"])
        self.assertTrue(w.dismiss(al[0]["t"]))                                         # der Nutzer schließt die Meldung
        w.last = t[0]
        self.assertEqual(w.alerts(), [])
        self.assertFalse(w.dismiss(al[0]["t"]))                                        # zweites Mal: nichts mehr zu schließen
        for bad in ("x", None, True):
            with self.assertRaises(ValueError):
                w.dismiss(bad)

    def test_dismissal_survives_a_restart(self):
        text = "2026-10-08T18:10:51,000000+00:00 usb 2-1: USB disconnect, device number 2\n"
        path = tempfile.mktemp()
        w, t = watch(text, path=path)
        w.scan()
        t[0] = w.events[0]["t"] + 60
        w.last = 0.0
        self.assertEqual(len(w.alerts()), 1)
        w.dismiss(w.events[0]["t"])
        w2 = server.UsbWatch(path, runner=lambda: text, clock=lambda: t[0])
        self.assertEqual(w2.alerts(), [])                                              # auch nach einem Neustart nicht wieder da

    def test_events_survive_a_restart_and_are_not_added_twice(self):
        path = tempfile.mktemp()
        w, t = watch(path=path)
        w.scan()
        n = len(w.events)
        self.assertEqual(n, 3)
        self.assertEqual(len(json.load(open(path))), 3)
        w2, _ = watch(path=path)
        self.assertEqual(len(w2.events), 3)                                            # nach dem Neustart noch da
        w2.scan()
        self.assertEqual(len(w2.events), 3)                                            # dieselben Zeilen kommen nicht noch einmal dazu
        os.unlink(path)

    def test_broken_input_never_raises(self):
        w = server.UsbWatch(tempfile.mktemp(), runner=lambda: (_ for _ in ()).throw(OSError("kein dmesg")))
        w.scan()
        self.assertEqual(w.alerts(), [])
        w2, _ = watch(text="")
        w2.scan()
        self.assertEqual(w2.events, [])

    def test_demo_shows_one_example(self):
        w = server.UsbWatch(tempfile.mktemp(), demo=True)
        a = w.alerts()
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["cat"], "wlan")


class Outages(unittest.TestCase):
    def cams(self, **states):
        return [{"key": k, "name": k.upper(), "state": v} for k, v in states.items()]

    def test_camera_that_sent_and_stopped_is_reported_after_20_seconds_and_cleared_when_back(self):
        t = [1000.0]
        w = server.OutageWatch(clock=lambda: t[0])
        self.assertEqual(w.alerts(self.cams(**{"dji-aa": "offline"})), [])                    # nie gesendet: keine Meldung (nur noch nicht verbunden)
        self.assertEqual(w.alerts(self.cams(**{"dji-aa": "live"})), [])
        t[0] += 5
        self.assertEqual(w.alerts(self.cams(**{"dji-aa": "offline"})), [])                    # gerade erst weg
        t[0] += 25
        al = w.alerts(self.cams(**{"dji-aa": "offline"}))
        self.assertEqual(len(al), 1)
        self.assertEqual((al[0]["kind"], al[0]["cam"], al[0]["name"]), ("cam", "dji", "DJI-AA"))
        t[0] += 5
        self.assertEqual(w.alerts(self.cams(**{"dji-aa": "live"})), [])                       # wieder da: Meldung weg
        self.assertEqual(w.alerts(self.cams(**{"dji-aa": "unknown"})), [])                    # Zustand unbekannt ändert nichts

    def test_other_camera_and_removed_camera(self):
        t = [0.0]
        w = server.OutageWatch(clock=lambda: t[0])
        w.alerts(self.cams(abc="live"))
        t[0] += 30
        al = w.alerts(self.cams(abc="offline"))
        t[0] += 30
        al = w.alerts(self.cams(abc="offline"))
        self.assertEqual(al[0]["cam"], "cam")
        self.assertEqual(w.alerts([]), [])                                                      # Kamera aus der Liste entfernt: keine Meldung
        t[0] += 7 * 3600
        w.alerts(self.cams(abc="live")); w.alerts(self.cams(abc="offline"))
        t[0] += 7 * 3600
        self.assertEqual(w.alerts(self.cams(abc="offline")), [])                                # nach sechs Stunden gilt sie nicht mehr als frischer Ausfall

    def test_hdmi_without_signal_or_with_error(self):
        t = [0.0]
        w = server.OutageWatch(clock=lambda: t[0])
        ok = {"service": True, "available": True, "state": "streaming", "settings": {"enabled": True}, "signal_known": True, "signal": {"plugged": True}}
        self.assertEqual(w.alerts([], ok), [])
        bad = dict(ok, signal={"plugged": False}, state="starting")
        self.assertEqual(w.alerts([], bad), [])                                                  # gerade erst
        t[0] += 25
        al = w.alerts([], bad)
        self.assertEqual([a["kind"] for a in al], ["hdmi"])
        self.assertEqual(w.alerts([], ok), [])                                                   # Signal wieder da
        off = dict(bad, settings={"enabled": False})
        t[0] += 100
        self.assertEqual(w.alerts([], off), [])                                                  # HDMI ausgeschaltet: keine Meldung
        self.assertEqual(w.alerts([], {"service": False}), [])                                   # Dienst fehlt: keine Meldung
        err = dict(ok, state="error")
        w.alerts([], err); t[0] += 30
        self.assertEqual([a["kind"] for a in w.alerts([], err)], ["hdmi"])


BOOT_LOG = """2026-10-09T21:21:12,000000+00:00 usb 5-1.4: new high-speed USB device number 4 using xhci-hcd
2026-10-09T21:21:12,500000+00:00 usb 5-1.4: New USB device found, idVendor=a69c, idProduct=5721, bcdDevice= 1.00
2026-10-09T21:21:37,043013+00:00 usb 5-1.4: USB disconnect, device number 4
2026-10-09T21:21:38,300000+00:00 usb 5-1.4: new high-speed USB device number 5 using xhci-hcd
2026-10-09T21:21:38,410582+00:00 usb 5-1.4: New USB device found, idVendor=a69c, idProduct=8d80, bcdDevice= 1.00
2026-10-09T21:21:38,410618+00:00 usb 5-1.4: Product: AIC Wlan
2026-10-09T21:21:40,883493+00:00 usb 5-1.4: USB disconnect, device number 5
2026-10-09T21:21:41,612494+00:00 usb 5-1.4: new high-speed USB device number 6 using xhci-hcd
2026-10-09T21:21:41,726855+00:00 usb 5-1.4: New USB device found, idVendor=a69c, idProduct=8d81, bcdDevice= 1.00
2026-10-09T21:21:41,726882+00:00 usb 5-1.4: Product: AIC 8800D80
2026-10-09T21:30:00,000000+00:00 usb 5-1.4: USB disconnect, device number 6
2026-10-09T21:30:04,000000+00:00 usb 5-1.4: new high-speed USB device number 7 using xhci-hcd
"""


def boot_watch(text, now, up, path=None):
    """Uhr und Betriebszeit von Hand: Die Box ist um 21:21:12 UTC gestartet (Issue #58, Protokoll von Bittersweet1987)."""
    t = [now]
    w = server.UsbWatch(path or tempfile.mktemp(), runner=lambda: text, clock=lambda: t[0], uptime=lambda: up[0])
    return w, t


class Issue58(unittest.TestCase):
    BOOT = 1791580872.0                                   # 2026-10-09 21:21:12 UTC

    def test_reconnects_right_after_the_start_are_not_failures(self):
        up = [180.0]
        w, _ = boot_watch(BOOT_LOG, self.BOOT + 180.0, up)
        ev = w._parse(BOOT_LOG)
        self.assertEqual([round(e["t"] - self.BOOT) for e in ev], [528])                 # nur die Trennung um 21:30; 25 s und 29 s nach dem Start zählen nicht

    def test_grace_ends_after_two_minutes(self):
        up = [300.0]
        text = "2026-10-09T21:23:00,000000+00:00 usb 5-1.4: USB disconnect, device number 6\n"       # 108 s nach dem Start: noch in der Schonzeit
        w, _ = boot_watch(text, self.BOOT + 300.0, up)
        self.assertEqual(w._parse(text), [])
        text2 = "2026-10-09T21:23:20,000000+00:00 usb 5-1.4: USB disconnect, device number 6\n"      # 128 s: zählt
        self.assertEqual(len(w._parse(text2)), 1)

    def test_alert_names_the_device_and_the_port(self):
        w, t = boot_watch(BOOT_LOG, self.BOOT + 700.0, [700.0])
        al = w.alerts()
        self.assertEqual(len(al), 1)
        self.assertEqual((al[0]["name"], al[0]["port"], al[0]["ids"]), ("AIC 8800D80", "5-1.4", "a69c:8d81"))
        self.assertTrue(al[0]["back"])                                                    # um 21:30:04 wieder erkannt

    def test_alert_without_a_known_name_is_still_valid(self):
        text = "2026-10-09T21:40:00,000000+00:00 usb 9-9: USB disconnect, device number 4\n"
        w, _ = boot_watch(text, self.BOOT + 2000.0, [2000.0])
        al = w.alerts()
        self.assertEqual((al[0]["name"], al[0]["ids"], al[0]["port"]), ("", "", "9-9"))

    def test_alert_goes_away_five_minutes_after_the_device_is_back(self):
        w, t = boot_watch(BOOT_LOG, self.BOOT + 700.0, [700.0])
        self.assertEqual(server.UsbWatch.BACK_SHOW, 300.0)
        self.assertEqual(len(w.alerts()), 1)
        back = w.events[0]["back"]
        t[0] = back + 299
        w.last = t[0]
        self.assertEqual(len(w.alerts()), 1)
        t[0] = back + 301
        w.last = t[0]
        self.assertEqual(w.alerts(), [])

    def test_events_from_before_the_restart_do_not_come_back(self):
        path = tempfile.mktemp()
        old = [{"t": self.BOOT - 40, "port": "5-1.4", "cat": "other", "why": "gone", "name": "AIC 8800D80"}]
        with open(path, "w") as f:
            json.dump(old, f)
        w, _ = boot_watch("", self.BOOT + 60.0, [60.0], path)
        self.assertEqual(w.events, [])                                                    # sofort nach dem Start keine Meldung von vorher
        self.assertEqual(w.alerts(), [])

    def test_service_restart_without_reboot_keeps_the_events(self):
        path = tempfile.mktemp()
        ev = [{"t": self.BOOT + 500, "port": "5-1.4", "cat": "wlan", "why": "gone"}]
        with open(path, "w") as f:
            json.dump(ev, f)
        w, _ = boot_watch("", self.BOOT + 900.0, [900.0], path)
        self.assertEqual(len(w.events), 1)

    def test_page_shows_name_and_port(self):
        page = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "index.html"), encoding="utf-8").read()
        self.assertIn('const nm=a.name?` <b translate="no">${esc(a.name)}</b>`:"";', page)
        self.assertIn('"USB "+a.port', page)

    def test_without_own_clock_the_real_uptime_is_used(self):
        w = server.UsbWatch(tempfile.mktemp(), runner=lambda: "")
        self.assertEqual(w.uptime, server.UsbWatch._uptime)
        w2 = server.UsbWatch(tempfile.mktemp(), runner=lambda: "", clock=lambda: 1.0)
        self.assertIsNone(w2.uptime())                                                    # Tests mit eigener Uhr: keine Schonzeit


if __name__ == "__main__":
    unittest.main()
