#!/usr/bin/env python3
"""Tests für die feste Zusatzadresse (Issue #65): Root-Helfer install/pipbox-netaddr.py (mit Attrappe für `ip` und /sys) und die Klasse ExtraAddress im Server."""
import importlib.util
import ipaddress
import json
import os
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import server  # noqa: E402

with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as _f:
    PAGE = _f.read()
with open(os.path.join(ROOT, "install", "install.sh"), encoding="utf-8") as _f:
    INSTALL = _f.read()
with open(os.path.join(ROOT, "server.py"), encoding="utf-8") as _f:
    SRC = _f.read()

MAC0, MAC1 = "aa:bb:cc:00:11:22", "aa:bb:cc:00:11:33"


def load_helper():
    spec = importlib.util.spec_from_file_location("pipbox_netaddr", os.path.join(ROOT, "install", "pipbox-netaddr.py"))
    h = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(h)
    return h


def fake_sys(cards):
    """/sys/class/net-Attrappe: cards = {Name: (MAC, Art)} mit Art "eth", "wlan", "bridge" oder "virt" (ohne device)."""
    root = tempfile.mkdtemp()
    for name, (mac, kind) in cards.items():
        d = os.path.join(root, name)
        os.makedirs(d)
        open(d + "/type", "w").write("1\n")
        open(d + "/address", "w").write(mac + "\n")
        if kind in ("eth", "wlan", "bridge"):
            os.makedirs(d + "/device")
        if kind == "wlan":
            os.makedirs(d + "/wireless")
        if kind == "bridge":
            os.makedirs(d + "/bridge")
    return root


class Setup:
    """Helfer mit Attrappen für /sys, `ip` und die Dateien."""

    def make(self, cards=None, addrs=None):
        h = load_helper()
        base = tempfile.mkdtemp()
        h.SYS_NET = fake_sys(cards or {"eth0": (MAC0, "eth"), "eth1": (MAC1, "eth"), "wlan0": ("aa:bb:cc:00:11:44", "wlan")})
        h.RUN, h.STATUS = base + "/run", base + "/run/status.json"
        h.CONF_DIR, h.CONF = base + "/etc", base + "/etc/extra-ip.json"
        h.HOOK_DIR, h.HOOK = base + "/ifup", base + "/ifup/pipbox-extra-ip"
        h.REQ, h.LOCK = base + "/req", base + "/lock"
        self.calls = []
        self.addrs = addrs if addrs is not None else [("eth0", "192.168.1.20/24"), ("eth1", "192.168.178.195/24")]
        h.ip = self._ip
        h.addresses = lambda: [(n, ipaddress.ip_address(a.split("/")[0]), ipaddress.ip_network(a, strict=False)) for n, a in self.addrs]
        return h

    def _ip(self, *args):
        self.calls.append(args)
        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()


class Helper(Setup, unittest.TestCase):
    def test_valid_request_sets_the_address_the_hook_and_the_status(self):
        h = self.make()
        h.handle({"enable": True, "mac": MAC0, "addr": "192.168.1.50", "prefix": 24})
        self.assertIn(("addr", "replace", "192.168.1.50/24", "dev", "eth0", "label", "eth0:pb"), self.calls)
        conf = json.load(open(h.CONF))
        self.assertEqual(conf, {"mac": MAC0, "addr": "192.168.1.50", "prefix": 24})
        hook = open(h.HOOK).read()
        self.assertTrue(hook.startswith("#!/bin/sh"))
        self.assertIn('= "%s" ] || exit 0' % MAC0, hook)                                # gilt nur für diese MAC
        self.assertIn('ip addr replace 192.168.1.50/24 dev "$IFACE"', hook)
        self.assertEqual(os.stat(h.HOOK).st_mode & 0o777, 0o755)
        st = json.load(open(h.STATUS))
        self.assertTrue(st["ok"] and st["enabled"])
        self.assertEqual((st["addr"], st["iface"]), ("192.168.1.50", "eth0"))

    def test_address_follows_the_mac_not_the_name(self):
        h = self.make({"eth5": (MAC0, "eth"), "eth1": (MAC1, "eth")}, addrs=[("eth5", "192.168.1.20/24"), ("eth1", "192.168.178.195/24")])
        h.handle({"enable": True, "mac": MAC0, "addr": "192.168.1.50", "prefix": 24})
        self.assertIn(("addr", "replace", "192.168.1.50/24", "dev", "eth5", "label", "eth5:pb"), self.calls)

    def test_refusals(self):
        h = self.make()
        bad = [({"mac": MAC0, "addr": "8.8.8.8", "prefix": 24}, "privaten"),                           # öffentlich
               ({"mac": MAC0, "addr": "127.0.0.5", "prefix": 24}, "privaten"),
               ({"mac": MAC0, "addr": "169.254.1.5", "prefix": 24}, "privaten"),
               ({"mac": MAC0, "addr": "224.0.0.1", "prefix": 24}, "privaten"),
               ({"mac": MAC0, "addr": "192.168.1.50", "prefix": 31}, "zwischen 8 und 30"),
               ({"mac": MAC0, "addr": "192.168.1.50", "prefix": 7}, "zwischen 8 und 30"),
               ({"mac": MAC0, "addr": "192.168.1.0", "prefix": 24}, "Netz- oder Rundruf"),
               ({"mac": MAC0, "addr": "192.168.1.255", "prefix": 24}, "Netz- oder Rundruf"),
               ({"mac": MAC0, "addr": "nicht-ip", "prefix": 24}, "Ungültige IP"),
               ({"mac": "kaputt", "addr": "192.168.1.50", "prefix": 24}, "Ungültige MAC"),
               ({"mac": "aa:bb:cc:99:99:99", "addr": "192.168.1.50", "prefix": 24}, "nicht eingesteckt"),   # unbekannte Karte
               ({"mac": "aa:bb:cc:00:11:44", "addr": "192.168.1.50", "prefix": 24}, "nicht eingesteckt"),   # WLAN ist kein LAN-Kabel
               ({"mac": MAC0, "addr": "192.168.1.20", "prefix": 24}, "schon vergeben"),                  # die DHCP-Adresse selbst
               ({"mac": MAC0, "addr": "192.168.178.77", "prefix": 24}, "überschneidet"),                 # Netz einer anderen Karte
               ({"mac": MAC0, "addr": "192.168.0.50", "prefix": 16}, "überschneidet")]                   # größeres Netz verschluckt das andere
        for req, why in bad:
            with self.assertRaises(h.Refuse, msg=str(req)) as cm:
                h.handle(dict(req, enable=True))
            self.assertIn(why, str(cm.exception), str(req))
        self.assertEqual([c for c in self.calls if c[:2] == ("addr", "replace")], [])                    # nie etwas gesetzt
        self.assertFalse(os.path.exists(h.HOOK))

    def test_reapplying_its_own_address_is_fine(self):
        h = self.make()
        h.handle({"enable": True, "mac": MAC0, "addr": "192.168.1.50", "prefix": 24})
        self.addrs.append(("eth0", "192.168.1.50/24"))                                                 # jetzt steht sie auf der Karte
        h.handle({"enable": True, "mac": MAC0, "addr": "192.168.1.50", "prefix": 24})                  # erneut speichern: kein "schon vergeben"
        with self.assertRaises(h.Refuse):
            h.handle({"enable": True, "mac": MAC1, "addr": "192.168.1.50", "prefix": 24})              # auf einer anderen Karte schon

    def test_same_network_as_its_own_dhcp_address_is_allowed(self):
        h = self.make()
        h.handle({"enable": True, "mac": MAC0, "addr": "192.168.1.51", "prefix": 24})                  # der Normalfall: derselbe Router
        self.assertTrue(os.path.exists(h.HOOK))

    def test_non_ethernet_cards_are_never_offered(self):
        h = self.make({"eth0": (MAC0, "eth"), "wlan0": ("aa:bb:cc:00:11:44", "wlan"), "br0": ("aa:bb:cc:00:11:55", "bridge"), "veth1": ("aa:bb:cc:00:11:66", "virt"),
                       "tailscale0": ("aa:bb:cc:00:11:77", "eth"), "lo": ("00:00:00:00:00:00", "virt")})
        self.assertEqual(h.wired(), {"eth0": MAC0})

    def test_disable_removes_hook_config_and_address(self):
        h = self.make()
        h.handle({"enable": True, "mac": MAC0, "addr": "192.168.1.50", "prefix": 24})
        self.calls.clear()
        h.handle({"enable": False})
        self.assertIn(("addr", "del", "192.168.1.50/24", "dev", "eth0"), self.calls)
        self.assertFalse(os.path.exists(h.HOOK))
        self.assertFalse(os.path.exists(h.CONF))
        self.assertFalse(json.load(open(h.STATUS))["enabled"])

    def test_changing_the_address_removes_the_old_one_first(self):
        h = self.make()
        h.handle({"enable": True, "mac": MAC0, "addr": "192.168.1.50", "prefix": 24})
        self.calls.clear()
        h.handle({"enable": True, "mac": MAC0, "addr": "192.168.1.60", "prefix": 24})
        self.assertEqual(self.calls[0], ("addr", "del", "192.168.1.50/24", "dev", "eth0"))
        self.assertEqual(json.load(open(h.CONF))["addr"], "192.168.1.60")

    def test_long_interface_names_get_no_label(self):
        name = "enx0123456789ab"                                                                      # 15 Zeichen: kein Platz für ":pb"
        h = self.make({name: (MAC0, "eth")}, addrs=[(name, "192.168.1.20/24")])
        h.handle({"enable": True, "mac": MAC0, "addr": "192.168.1.50", "prefix": 24})
        self.assertIn(("addr", "replace", "192.168.1.50/24", "dev", name), self.calls)

    def test_request_file_is_read_once_and_removed_and_errors_land_in_the_status(self):
        h = self.make()
        open(h.REQ, "w").write(json.dumps({"enable": True, "mac": MAC0, "addr": "8.8.8.8", "prefix": 24}))
        self.assertEqual(h.main(["x"]), 0)
        self.assertFalse(os.path.exists(h.REQ))
        st = json.load(open(h.STATUS))
        self.assertFalse(st["ok"])
        self.assertIn("privaten", st["error"])
        open(h.REQ, "w").write("{kaputt")
        self.assertEqual(h.main(["x"]), 0)
        self.assertFalse(json.load(open(h.STATUS))["ok"])

    def test_symlink_as_request_is_not_followed(self):
        h = self.make()
        target = h.REQ + ".ziel"
        open(target, "w").write(json.dumps({"enable": True, "mac": MAC0, "addr": "192.168.1.50", "prefix": 24}))
        os.symlink(target, h.REQ)
        h.main(["x"])
        self.assertFalse(os.path.exists(h.HOOK))

    def test_ip_failure_is_reported_and_leaves_a_clear_message(self):
        h = self.make()
        class Bad:
            returncode, stdout, stderr = 2, "", "RTNETLINK answers: File exists"
        h.ip = lambda *a: Bad()
        with self.assertRaises(h.Refuse) as cm:
            h.handle({"enable": True, "mac": MAC0, "addr": "192.168.1.50", "prefix": 24})
        self.assertIn("konnte nicht gesetzt werden", str(cm.exception))

    def test_no_default_route_gateway_or_dns_is_touched(self):
        text = open(os.path.join(ROOT, "install", "pipbox-netaddr.py"), encoding="utf-8").read()
        code = text.split('"""', 2)[2]                                                                     # ohne die Beschreibung am Anfang
        for needle in ('"route"', "resolv", "dhclient", "ifdown", "ifup ", "nmcli", "systemctl"):
            self.assertNotIn(needle, code, needle)
        self.assertEqual(sorted(set(a for a in ("addr",) if '"addr", "' in code)), ["addr"])               # nur `ip addr ...`


class Server(unittest.TestCase):
    def make(self, cards=None):
        ea = server.ExtraAddress(tempfile.mkdtemp(), clock=time.time, sleep=time.sleep)
        ea.SYS_NET = fake_sys(cards or {"eth0": (MAC0, "eth"), "wlan0": ("aa:bb:cc:00:11:44", "wlan")})
        base = tempfile.mkdtemp()
        ea.CONF, ea.STATUS, ea.UNIT = base + "/conf.json", base + "/status.json", base + "/unit.path"
        return ea

    def test_check_rules_match_the_helper(self):
        c = server.ExtraAddress.check
        self.assertEqual(c(MAC0, "192.168.1.50", 24), "192.168.1.50")
        for args in ((MAC0, "8.8.8.8", 24), (MAC0, "192.168.1.50", 31), (MAC0, "192.168.1.0", 24), (MAC0, "x", 24), ("kaputt", "192.168.1.50", 24),
                     (MAC0, "192.168.1.50", True), (MAC0, 5, 24), (None, "192.168.1.50", 24)):
            with self.assertRaises(ValueError, msg=str(args)):
                c(*args)

    def test_status_lists_only_wired_cards_and_marks_a_present_address(self):
        ea = self.make()
        st = ea.status()
        self.assertEqual([c["name"] for c in st["cards"]], ["eth0"])
        self.assertFalse(st["installed"])
        self.assertFalse(st["enabled"])
        open(ea.CONF, "w").write(json.dumps({"mac": MAC0, "addr": "192.168.1.50", "prefix": 24}))
        st = ea.status()
        self.assertTrue(st["enabled"])
        self.assertEqual((st["mac"], st["addr"], st["prefix"]), (MAC0, "192.168.1.50", 24))

    def test_set_writes_the_request_and_waits_for_the_helper(self):
        ea = self.make()
        open(ea.UNIT, "w").write("x")
        seen = {}

        def helper():
            for _ in range(100):
                if os.path.exists(ea.req):
                    seen["req"] = json.load(open(ea.req))
                    os.remove(ea.req)
                    open(ea.CONF, "w").write(json.dumps({"mac": MAC0, "addr": "192.168.1.50", "prefix": 24}))
                    json.dump({"ok": True, "enabled": True, "time": int(time.time())}, open(ea.STATUS, "w"))
                    return
                time.sleep(0.05)
        t = threading.Thread(target=helper)
        t.start()
        st = ea.set(True, MAC0, "192.168.1.50", 24)
        t.join()
        self.assertEqual(seen["req"], {"enable": True, "mac": MAC0, "addr": "192.168.1.50", "prefix": 24})
        self.assertTrue(st["enabled"])

    def test_error_from_the_helper_is_passed_on(self):
        ea = self.make()
        open(ea.UNIT, "w").write("x")

        def helper():
            for _ in range(100):
                if os.path.exists(ea.req):
                    os.remove(ea.req)
                    json.dump({"ok": False, "error": "Das Netz überschneidet sich mit dem Netz von eth1.", "time": int(time.time())}, open(ea.STATUS, "w"))
                    return
                time.sleep(0.05)
        t = threading.Thread(target=helper)
        t.start()
        with self.assertRaises(ValueError) as cm:
            ea.set(True, MAC0, "192.168.1.50", 24)
        t.join()
        self.assertIn("überschneidet", str(cm.exception))

    def test_without_helper_and_without_answer(self):
        ea = self.make()
        with self.assertRaises(ValueError) as cm:
            ea.set(True, MAC0, "192.168.1.50", 24)
        self.assertIn("nächsten Software-Update", str(cm.exception))
        open(ea.UNIT, "w").write("x")
        ea.WAIT = 0.3
        with self.assertRaises(ValueError) as cm:
            ea.set(False)
        self.assertIn("Keine Antwort", str(cm.exception))

    def test_invalid_input_is_refused_before_anything_is_written(self):
        ea = self.make()
        open(ea.UNIT, "w").write("x")
        for args in ((True, MAC0, "8.8.8.8", 24), ("ja", None, None, None)):
            with self.assertRaises(ValueError):
                ea.set(*args)
        self.assertFalse(os.path.exists(ea.req))

    def test_demo_mode(self):
        ea = server.ExtraAddress(tempfile.mkdtemp(), demo=True)
        st = ea.set(True, "aa:bb:cc:00:11:22", "192.168.1.50", 24)
        self.assertTrue(st["enabled"] and st["installed"])
        self.assertFalse(ea.set(False)["enabled"])


class Wiring(unittest.TestCase):
    def test_routes(self):
        self.assertIn('if path == "/api/netaddr":', SRC)
        self.assertIn('self.netaddr.set(d.get("enable"), d.get("mac"), d.get("addr"), d.get("prefix"))', SRC)
        self.assertIn("Handler.netaddr = ExtraAddress(", SRC)

    def test_install_and_uninstall_know_the_helper(self):
        for n in ("install/pipbox-netaddr.py", "install/pipbox-netaddr.service", "install/pipbox-netaddr.path"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, n)), n)
        self.assertIn('install -m 755 "$HERE/install/pipbox-netaddr.py" /opt/pipbox/pipbox-netaddr.py', INSTALL)
        self.assertIn("pipbox-logmode.path pipbox-netaddr.path pipbox-logs.path", INSTALL)               # wird aktiviert
        self.assertIn("python3 /opt/pipbox/pipbox-netaddr.py --remove", INSTALL)                          # beim Entfernen: Adresse und Skript weg
        self.assertIn("/etc/systemd/system/pipbox-netaddr.path", INSTALL.split("uninstall)")[1])

    def test_units_follow_the_pattern(self):
        p = open(os.path.join(ROOT, "install", "pipbox-netaddr.path"), encoding="utf-8").read()
        self.assertIn("PathExists=/var/lib/pipbox/netaddr-request", p)
        s = open(os.path.join(ROOT, "install", "pipbox-netaddr.service"), encoding="utf-8").read()
        self.assertIn("Type=oneshot", s)
        self.assertIn("ExecStart=/usr/bin/python3 /opt/pipbox/pipbox-netaddr.py", s)
        self.assertNotRegex(s + p, r"(?m)^[A-Za-z]+=.*\s#")                                               # kein Kommentar hinter einem Wert (systemd)

    def test_section_is_under_options_and_bound_to_the_mac(self):
        a = PAGE.index('id="c_layout"')
        i = PAGE.index('id="opt_netaddr"')
        self.assertGreater(i, a)
        self.assertLess(i, PAGE.index('id="c_report"'))                                                  # innerhalb der Karte Optionen
        self.assertIn("{enable:true,mac:card.value,addr:addr.value.trim(),prefix:parseInt(prefix.value,10)}", PAGE)
        self.assertIn("MAC-Adresse", PAGE[i:i + 1200])
        self.assertIn("getrennter Adressraum", PAGE[i:i + 1200])


if __name__ == "__main__":
    unittest.main()
