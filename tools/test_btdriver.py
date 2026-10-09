"""Tests für den Treiber-Helfer für Bluetooth-Sticks (Realtek und Barrot, install/pipbox-btdriver.py). Ohne Hardware, ohne Bau, ohne Laden von Modulen."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("pbbtdriver", os.path.join(ROOT, "install", "pipbox-btdriver.py"))
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

UB500 = "2357:0604"


def rd(path):
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


class Sources(unittest.TestCase):
    def test_bundled_sources_are_the_expected_kernel_files(self):
        bt.check_source(os.path.join(ROOT, "bluetooth-src"))                  # SHA-256 stimmen
        for name, want in bt.SRC_SHA256.items():
            with open(os.path.join(ROOT, "bluetooth-src", name), "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), want)
        for name in ("COPYING", "README.md"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "bluetooth-src", name)))

    def test_modified_source_is_refused(self):
        d = tempfile.mkdtemp()
        for name in bt.SRC_FILES:
            with open(os.path.join(ROOT, "bluetooth-src", name), "rb") as f, open(os.path.join(d, name), "wb") as g:
                g.write(f.read())
        bt.check_source(d)
        with open(os.path.join(d, "btusb.c"), "a") as f:
            f.write("/* eingeschleust */\n")
        with self.assertRaises(RuntimeError):
            bt.check_source(d)
        os.remove(os.path.join(d, "btrtl.h"))
        with self.assertRaises(RuntimeError):
            bt.check_source(d)


class Patch(unittest.TestCase):
    def setUp(self):
        self.text = rd(os.path.join(ROOT, "bluetooth-src", "btusb.c"))

    def test_adds_every_id_exactly_once_after_the_anchor(self):
        new = bt.patch_source(self.text)
        for i in bt.REALTEK_IDS:
            v, p = i.split(":")
            self.assertEqual(new.lower().count(f"usb_device(0x{v}, 0x{p})"), 1, i)
        self.assertLess(new.index(bt.ANCHOR), new.index("USB_DEVICE(0x2357, 0x0604)"))
        self.assertIn("BTUSB_REALTEK", new[new.index("0x2357, 0x0604"):new.index("0x2357, 0x0604") + 80])

    def test_only_adds_lines(self):
        new = bt.patch_source(self.text)
        old_lines, new_lines = self.text.splitlines(), new.splitlines()
        self.assertGreater(len(new_lines), len(old_lines))
        it = iter(new_lines)
        for line in old_lines:                                               # alle alten Zeilen stehen unverändert und in gleicher Reihenfolge da
            for cand in it:
                if cand == line:
                    break
            else:
                self.fail("Zeile verloren: " + line)
        added = [l for l in new_lines if l not in set(old_lines)]
        self.assertTrue(all(("USB_DEVICE" in l or "IRL4YOU" in l) for l in added), added)

    def test_is_idempotent_and_skips_existing_ids(self):
        once = bt.patch_source(self.text)
        self.assertEqual(bt.patch_source(once), once)
        self.assertEqual(bt.patch_source(self.text, ["0bda:b009"]), self.text)  # steht schon im Original

    def test_unexpected_layout_is_refused(self):
        with self.assertRaises(RuntimeError):
            bt.patch_source("nichts davon")
        with self.assertRaises(RuntimeError):
            bt.patch_source(self.text.replace(bt.ANCHOR, ""))

    def test_none_of_the_ids_is_in_the_original_except_what_the_helper_adds(self):
        for i in bt.ALL_IDS:
            v, p = i.split(":")
            self.assertNotIn(f"USB_DEVICE(0x{v}, 0x{p})", self.text.lower().replace("0x" + v.upper(), "0x" + v), i)


class Detection(unittest.TestCase):
    def sysfs(self, ids):
        root = tempfile.mkdtemp()
        for n, i in enumerate(ids):
            d = os.path.join(root, f"1-{n}")
            os.makedirs(d)
            v, p = i.split(":")
            with open(os.path.join(d, "idVendor"), "w") as f:
                f.write(v + "\n")
            with open(os.path.join(d, "idProduct"), "w") as f:
                f.write(p.upper() + "\n")
            os.makedirs(os.path.join(d + ":1.0"))                              # Schnittstellen haben keine idVendor-Dateien
        return root

    def test_only_candidates_count(self):
        root = self.sysfs([UB500, "0b05:190e", "1d6b:0002", "8087:0a2b"])
        present = bt.usb_ids_present(root)
        self.assertIn("0b05:190e", present)
        self.assertEqual(list(bt.wanted(present)), [UB500])                    # ASUS (läuft schon) und ein Intel-Stick lösen nichts aus

    def test_asus_alone_triggers_nothing(self):
        self.assertEqual(bt.wanted(bt.usb_ids_present(self.sysfs(["0b05:190e"]))), {})

    def test_missing_sysfs(self):
        self.assertEqual(bt.usb_ids_present("/nonexistent"), set())

    def test_kernel_support(self):
        self.assertTrue(bt.kernel_supported("5.10.160-belabox"))
        self.assertFalse(bt.kernel_supported("5.10.200-belabox"))
        self.assertFalse(bt.kernel_supported("6.1.0"))

    def test_firmware_error_lines(self):
        ok = ["Bluetooth: hci0: RTL: examining hci_ver=0a hci_rev=000b", "Bluetooth: hci0: RTL: loading rtl_bt/rtl8761bu_fw.bin",
              "Bluetooth: hci0: RTL: fw version 0xdfc6d922"]
        self.assertEqual(bt.firmware_errors("\n".join(ok)), [])
        bad = ok + ["bluetooth hci0: Direct firmware load for rtl_bt/rtl8761bu_fw.bin failed with error -2",
                    "Bluetooth: hci0: RTL: firmware file rtl_bt/rtl8761bu_fw.bin not found"]
        self.assertEqual(len(bt.firmware_errors("\n".join(bad))), 2)


class Flow(unittest.TestCase):
    """do_auto mit nachgestellten Bausteinen."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.status = {}
        self.calls = []
        self.dmesg = []
        self.loaded = "SRC123"
        self.modules = os.path.join(self.d, "modules")
        patches = {
            "LIB_MODULES": self.modules, "PERSIST": os.path.join(self.d, "persist"), "RUN": os.path.join(self.d, "run"),
            "STATUS": os.path.join(self.d, "run", "status.json"), "LOCK": os.path.join(self.d, "lock"),
        }
        for k, v in patches.items():
            p = mock.patch.object(bt, k, v)
            p.start()
            self.addCleanup(p.stop)
        self.build_dir = tempfile.mkdtemp(prefix="pipbox-btdriver-", dir="/var/tmp") if os.path.isdir("/var/tmp") else tempfile.mkdtemp()
        self.addCleanup(lambda: os.path.isdir(self.build_dir) and __import__("shutil").rmtree(self.build_dir, ignore_errors=True))
        self.ko = os.path.join(self.build_dir, "btusb.ko")
        with open(self.ko, "wb") as f:
            f.write(b"ELF")
        self.rel = "5.10.160-belabox"
        mp = [mock.patch.object(bt, "release", lambda: self.rel), mock.patch.object(bt, "status", lambda **kw: self.status.update(kw)),
              mock.patch.object(bt, "log", lambda m: None), mock.patch.object(bt, "wanted", lambda present=None: {UB500: bt.CANDIDATES[UB500]}),
              mock.patch.object(bt, "belacoder_running", lambda: False), mock.patch.object(bt, "tools_ok", lambda rel=None: []),
              mock.patch.object(bt, "build_module", self.fake_build), mock.patch.object(bt, "install_module", self.fake_install),
              mock.patch.object(bt, "reload_bluetooth", self.fake_reload), mock.patch.object(bt, "loaded_srcversion", lambda: self.loaded),
              mock.patch.object(bt, "dmesg_lines", lambda: list(self.dmesg)), mock.patch.object(bt, "run", self.fake_run),
              mock.patch.object(bt, "rollback", self.fake_rollback)]
        for p in mp:
            p.start()
            self.addCleanup(p.stop)
        self.reload_ok = True
        self.new_dmesg = ["Bluetooth: hci0: RTL: loading rtl_bt/rtl8761bu_fw.bin"]

    def fake_build(self, rel=None, ids=None):
        self.calls.append("build")
        return self.ko

    def fake_install(self, ko, rel=None):
        self.calls.append("install")
        os.makedirs(os.path.dirname(bt.module_path(self.rel)), exist_ok=True)
        with open(bt.module_path(self.rel), "wb") as f:
            f.write(b"ELF")
        return None

    def fake_reload(self):
        self.calls.append("reload")
        self.dmesg.extend(self.new_dmesg)
        return self.reload_ok

    def fake_rollback(self, backup, rel=None):
        self.calls.append("rollback")
        try:
            os.remove(bt.module_path(self.rel))
        except OSError:
            pass

    def fake_run(self, args, **kw):
        if args[:2] == ["modinfo", "-F"]:
            return mock.Mock(returncode=0, stdout="SRC123\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    def test_nothing_to_do_without_a_matching_stick(self):
        with mock.patch.object(bt, "wanted", lambda present=None: {}):
            bt.do_auto()
        self.assertEqual((self.calls, self.status), ([], {}))

    def test_other_kernel_is_reported_not_built(self):
        self.rel = "5.10.200-belabox"
        bt.do_auto()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.status["state"], "unsupported")

    def test_success_installs_and_remembers(self):
        bt.do_auto()
        self.assertEqual(self.calls, ["build", "install", "reload"])
        self.assertEqual(self.status["state"], "ok")
        with open(os.path.join(bt.PERSIST, "installed.json")) as rf:
            rec = json.load(rf)
        self.assertEqual(rec["kernel"], self.rel)
        self.assertIn(UB500, rec["ids"])
        self.assertFalse(os.path.exists(self.build_dir))                      # Temporärordner wurde aufgeräumt

    def test_second_run_does_nothing_when_installed(self):
        bt.do_auto()
        self.calls.clear()
        bt.do_auto()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.status["state"], "ok")

    def test_waits_while_sending(self):
        with mock.patch.object(bt, "belacoder_running", lambda: True):
            bt.do_auto()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.status["state"], "waiting")

    def test_missing_tools_fail_once_without_building(self):
        with mock.patch.object(bt, "tools_ok", lambda rel=None: ["make"]):
            bt.do_auto()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.status["state"], "failed")
        self.assertIn("make", self.status["message"])
        self.status.clear()
        bt.do_auto()                                                          # gleiche Kombination: kein neuer Versuch (keine Schleife)
        self.assertEqual(self.status, {})
        self.assertEqual(self.calls, [])

    def test_no_adapter_after_loading_rolls_back(self):
        self.reload_ok = False
        bt.do_auto()
        self.assertEqual(self.calls, ["build", "install", "reload", "rollback"])
        self.assertEqual(self.status["state"], "failed")
        self.assertIn("Standardtreiber", self.status["message"])
        self.assertFalse(os.path.exists(bt.module_path(self.rel)))
        self.assertFalse(os.path.exists(os.path.join(bt.PERSIST, "installed.json")))

    def test_firmware_error_rolls_back(self):
        self.new_dmesg = ["Bluetooth: hci0: RTL: firmware file rtl_bt/rtl8761bu_fw.bin not found"]
        bt.do_auto()
        self.assertIn("rollback", self.calls)
        self.assertEqual(self.status["state"], "failed")

    def test_no_firmware_message_at_all_rolls_back(self):
        self.new_dmesg = ["usb 1-1: new device"]
        bt.do_auto()
        self.assertIn("rollback", self.calls)
        self.assertIn("keine Firmware", self.status["message"])

    def test_old_module_still_loaded_rolls_back(self):
        self.loaded = "ALT999"
        bt.do_auto()
        self.assertIn("rollback", self.calls)
        self.assertIn("nicht geladen", self.status["message"])

    def test_build_failure_changes_nothing_and_is_not_retried(self):
        def boom(rel=None, ids=None):
            self.calls.append("build")
            raise RuntimeError("Der Bau des Treibers ist fehlgeschlagen: Fehler")
        with mock.patch.object(bt, "build_module", boom):
            bt.do_auto()
            self.assertEqual(self.calls, ["build"])                           # nichts eingespielt, nichts zurückzurollen
            self.assertEqual(self.status["state"], "failed")
            self.calls.clear()
            bt.do_auto()
            self.assertEqual(self.calls, [])

    def test_failure_marker_belongs_to_kernel_and_sticks(self):
        with mock.patch.object(bt, "build_module", mock.Mock(side_effect=RuntimeError("x"))):
            bt.do_auto()
        self.calls.clear()
        self.rel = "5.10.160-belabox2"                                        # anderer Kernelname: neuer Versuch
        bt.do_auto()
        self.assertIn("build", self.calls)


class Build(unittest.TestCase):
    def test_build_patches_a_copy_and_checks_the_module_version(self):
        made = {}
        def run(args, **kw):
            if args[0] == "make":
                tmp = [a for a in args if a.startswith("M=")][0][2:]
                made["tmp"] = tmp
                with open(os.path.join(tmp, "Kbuild")) as kf:
                    made["kbuild"] = kf.read()
                made["btusb"] = rd(os.path.join(tmp, "btusb.c"))
                with open(os.path.join(tmp, "btusb.ko"), "wb") as f:
                    f.write(b"ELF")
                return mock.Mock(returncode=0, stdout="", stderr="")
            if args[:3] == ["modinfo", "-F", "vermagic"]:
                return mock.Mock(returncode=0, stdout=made["vermagic"] + "\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(bt, "SRC", os.path.join(ROOT, "bluetooth-src")), mock.patch.object(bt, "run", run):
            made["vermagic"] = "5.10.160-belabox SMP mod_unload modversions aarch64"
            ko = bt.build_module("5.10.160-belabox")
            self.addCleanup(lambda: __import__("shutil").rmtree(os.path.dirname(ko), ignore_errors=True))
            self.assertEqual(made["kbuild"], "obj-m := btusb.o\n")
            self.assertIn("USB_DEVICE(0x2357, 0x0604)", made["btusb"])
            self.assertTrue(os.path.basename(os.path.dirname(ko)).startswith("pipbox-btdriver-"))
            # die Quellen im Paket bleiben unverändert
            self.assertNotIn("0x2357, 0x0604", rd(os.path.join(ROOT, "bluetooth-src", "btusb.c")))
            made["vermagic"] = "5.15.0 SMP"
            with self.assertRaises(RuntimeError) as e:
                bt.build_module("5.10.160-belabox")
            self.assertIn("passt nicht", str(e.exception))
            self.assertFalse(os.path.exists(made["tmp"]))                       # nach einem Fehler bleibt nichts liegen

    def test_failed_make_is_reported(self):
        with mock.patch.object(bt, "SRC", os.path.join(ROOT, "bluetooth-src")), \
                mock.patch.object(bt, "run", lambda a, **k: mock.Mock(returncode=2, stdout="", stderr="error: kaputt")):
            with self.assertRaises(RuntimeError) as e:
                bt.build_module("5.10.160-belabox")
        self.assertIn("fehlgeschlagen", str(e.exception))

    def test_tools_check(self):
        d = tempfile.mkdtemp()
        with mock.patch.object(bt, "LIB_MODULES", d), mock.patch.object(bt.shutil, "which", lambda t: "/usr/bin/" + t):
            self.assertIn("Kernel-Header (Makefile)", bt.tools_ok("5.10.160-belabox"))
            build = os.path.join(d, "5.10.160-belabox", "build")
            os.makedirs(os.path.join(build, "scripts", "mod"))
            for f in ("Makefile", "Module.symvers", ".config", "scripts/mod/modpost"):
                open(os.path.join(build, f), "w").close()
            self.assertEqual(bt.tools_ok("5.10.160-belabox"), [])
        with mock.patch.object(bt.shutil, "which", lambda t: None):
            self.assertEqual(sorted(x for x in bt.tools_ok("5.10.160-belabox") if x in ("make", "gcc")), ["gcc", "make"])


class Uninstall(unittest.TestCase):
    def test_uninstall_removes_only_our_module(self):
        d = tempfile.mkdtemp()
        with mock.patch.object(bt, "LIB_MODULES", d), mock.patch.object(bt, "PERSIST", os.path.join(d, "p")), \
                mock.patch.object(bt, "release", lambda: "5.10.160-belabox"), mock.patch.object(bt, "run", lambda *a, **k: mock.Mock(returncode=0, stdout="")), \
                mock.patch.object(bt, "log", lambda m: None):
            path = bt.module_path()
            os.makedirs(os.path.dirname(path))
            std = os.path.join(d, "5.10.160-belabox", "kernel", "drivers", "bluetooth", "btusb.ko")
            os.makedirs(os.path.dirname(std))
            for p in (path, std):
                open(p, "w").close()
            bt.write_json(bt.marker("installed.json"), {"kernel": "x"})
            bt.do_uninstall()
            self.assertFalse(os.path.exists(path))
            self.assertTrue(os.path.exists(std))                              # das Standardmodul des Kernels bleibt
            self.assertFalse(os.path.exists(bt.marker("installed.json")))


class Barrot(unittest.TestCase):
    """UGREEN BT6.0 (Barrot 33fa:0012): ein Byte zu viel in einer Antwort verschiebt alle folgenden; der Kernel-Fix 7722d6fb54 kommt in das neue Modul."""
    UGREEN = "33fa:0012"

    def setUp(self):
        self.text = rd(os.path.join(ROOT, "bluetooth-src", "btusb.c"))

    def test_the_sticks_are_candidates_and_trigger_the_build(self):
        self.assertIn(self.UGREEN, bt.CANDIDATES)
        self.assertIn("33fa:0010", bt.CANDIDATES)
        self.assertEqual(bt.wanted({self.UGREEN, "8087:0a2b"}), {self.UGREEN: bt.CANDIDATES[self.UGREEN]})

    def test_barrot_sticks_never_get_the_realtek_table_entry(self):
        new = bt.patch_recv_intr(bt.patch_source(self.text))
        self.assertNotIn("usb_device(0x33fa", new.lower())                       # sie laufen über die Geräteklasse am Standardtreiber
        self.assertNotIn(self.UGREEN, bt.REALTEK_IDS)
        self.assertEqual(set(bt.REALTEK_IDS) | set(bt.BARROT), set(bt.ALL_IDS))

    def test_guard_is_inserted_once_before_the_frame_is_handed_over(self):
        new = bt.patch_recv_intr(self.text)
        self.assertEqual(new.count("Unexpected continuation"), 1)
        i = new.index("Unexpected continuation")
        self.assertLess(new.index("if (!hci_skb_expect(skb)) {", i - 900), i)
        self.assertLess(i, new.index("data->recv_event(data->hdev, skb);", i))             # vor der Übergabe des Ereignisses
        self.assertIn("count && count < HCI_EVENT_HDR_SIZE", new)
        self.assertIn("count = 0;", new[i:i + 160])

    def test_a_single_byte_packet_at_the_start_of_an_event_is_dropped(self):
        """Am USB-Mitschnitt des UGREEN: nach der 16-Byte-Antwort kommt ein einzelnes Byte (0x0c) als eigenes Paket, dann die echte Antwort."""
        new = bt.patch_recv_intr(self.text)
        self.assertEqual(new.count("Unexpected stray byte"), 1)
        i = new.index("Unexpected stray byte")
        self.assertLess(new.index("if (!skb) {", i - 700), i)
        self.assertLess(i, new.index("skb = bt_skb_alloc(HCI_MAX_EVENT_SIZE, GFP_ATOMIC);", i))              # vor dem Anlegen des Ereignisses
        self.assertIn("count < HCI_EVENT_HDR_SIZE", new[i - 200:i])
        self.assertIn("break;", new[i:i + 120])
        self.assertEqual(new.count("static int btusb_recv_intr"), 1)
        self.assertGreater(new.index("static int btusb_recv_bulk"), i)                                       # nur in btusb_recv_intr, nicht im Bulk-Zweig

    def test_only_lines_are_added_to_the_original(self):
        new = bt.patch_recv_intr(self.text)
        a, b = self.text.splitlines(), new.splitlines()
        self.assertEqual([x for x in a if x not in set(b)], [])                            # nichts entfernt oder geändert
        self.assertEqual(len(b) - len(a), 15)

    def test_is_idempotent_and_refuses_an_unknown_layout(self):
        once = bt.patch_recv_intr(self.text)
        self.assertEqual(bt.patch_recv_intr(once), once)
        with self.assertRaises(RuntimeError):
            bt.patch_recv_intr("nichts davon")
        with self.assertRaises(RuntimeError):
            bt.patch_recv_intr(self.text.replace(bt.RECV_ANCHOR, "x"))

    def test_the_build_applies_both_patches(self):
        src = os.path.join(ROOT, "install", "pipbox-btdriver.py")
        code = rd(src)
        self.assertIn("patch_recv_intr(patch_source(text, ids))", code)
        self.assertIn("build_module(rel, list(REALTEK_IDS))", code)

    def test_adapter_is_found_by_usb_id_through_the_sysfs_path(self):
        root = tempfile.mkdtemp()
        usb = os.path.join(root, "usb", "5-1.3")
        os.makedirs(os.path.join(usb, "5-1.3_1.0", "bluetooth", "hci1"))
        with open(os.path.join(usb, "idVendor"), "w") as f:
            f.write("33fa\n")
        with open(os.path.join(usb, "idProduct"), "w") as f:
            f.write("0012\n")
        other = os.path.join(root, "usb", "4-1")
        os.makedirs(os.path.join(other, "bluetooth", "hci0"))
        with open(os.path.join(other, "idVendor"), "w") as f:
            f.write("2357\n")
        with open(os.path.join(other, "idProduct"), "w") as f:
            f.write("0604\n")
        bt_root = os.path.join(root, "bt")
        for n in ("hci0", "hci1", "hci0_16"):                                                # (hci0:16 ist kein Adapter: der Name passt nicht)
            os.makedirs(os.path.join(bt_root, n))
        where = {"hci1": os.path.join(usb, "5-1.3_1.0", "bluetooth", "hci1"), "hci0": os.path.join(other, "bluetooth", "hci0")}
        res = lambda path: where.get(os.path.basename(path), path)
        self.assertEqual(bt.hci_for_usb(self.UGREEN, bt_root, res), "hci1")
        self.assertEqual(bt.hci_for_usb("2357:0604", bt_root, res), "hci0")
        self.assertEqual(bt.hci_for_usb("0bda:8771", bt_root, res), "")
        self.assertEqual(bt.hci_for_usb(self.UGREEN, os.path.join(root, "gibtsnicht"), res), "")

    def test_check_after_loading_the_driver(self):
        sleeps = []
        ok = bt.barrot_errors([self.UGREEN], hci=lambda i: "hci1", sleep=sleeps.append, lines=lambda: ["Bluetooth: hci0: irgendwas"])
        self.assertEqual(ok, [])
        bad = bt.barrot_errors([self.UGREEN], hci=lambda i: "hci1", sleep=sleeps.append,
                               lines=lambda: ["Bluetooth: hci1: command 0x0c01 tx timeout", "Bluetooth: hci0: command 0x0c01 tx timeout"])
        self.assertEqual(len(bad), 1)
        self.assertIn("hci1", bad[0])
        self.assertEqual(bt.barrot_errors([self.UGREEN], timeout=0, hci=lambda i: "", sleep=sleeps.append, lines=lambda: []),
                         [self.UGREEN + ": es gibt keinen Adapter"])
        self.assertIn(6, sleeps)                                                             # der Start des Adapters bekommt Zeit

    def test_udev_rule_exists_for_both_ids(self):
        rules = rd(os.path.join(ROOT, "install", "80-pipbox-btdriver.rules"))
        for pid in ("0012", "0010"):
            self.assertIn(f'ATTR{{idVendor}}=="33fa", ATTR{{idProduct}}=="{pid}"', rules)


class BarrotFlow(Flow):
    """Ablauf mit einem Barrot-Stick: keine Realtek-Firmware nötig, dafür muss der Adapter nach dem Laden laufen."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(bt, "wanted", lambda present=None: {"33fa:0012": bt.CANDIDATES["33fa:0012"]})
        p.start()
        self.addCleanup(p.stop)
        self.new_dmesg = []                                                                   # keine Realtek-Meldung
        self.barrot = []
        p2 = mock.patch.object(bt, "barrot_errors", lambda ids, **kw: self.barrot)
        p2.start()
        self.addCleanup(p2.stop)

    def test_success_needs_no_realtek_firmware_message(self):
        bt.do_auto()
        self.assertEqual(self.calls, ["build", "install", "reload"])
        self.assertEqual(self.status["state"], "ok")
        with open(os.path.join(bt.PERSIST, "installed.json")) as rf:
            self.assertIn("33fa:0012", json.load(rf)["ids"])

    def test_a_stick_that_still_fails_rolls_back_and_is_not_retried(self):
        self.barrot = ["33fa:0012: Bluetooth: hci1: command 0x0c01 tx timeout"]
        bt.do_auto()
        self.assertEqual(self.calls[-1], "rollback")
        self.assertEqual(self.status["state"], "failed")
        self.assertIn("Barrot", self.status["message"])
        self.calls.clear()
        bt.do_auto()
        self.assertEqual(self.calls, [])

    def test_the_old_realtek_installation_is_rebuilt_once_the_new_ids_are_known(self):
        os.makedirs(bt.PERSIST, exist_ok=True)
        os.makedirs(os.path.dirname(bt.module_path(self.rel)), exist_ok=True)
        with open(bt.module_path(self.rel), "wb") as f:
            f.write(b"ELF")
        with open(os.path.join(bt.PERSIST, "installed.json"), "w") as f:
            json.dump({"kernel": self.rel, "ids": ["2357:0604", "0b05:190e"]}, f)
        bt.do_auto()
        self.assertEqual(self.calls[:2], ["build", "install"])


for _n in [m for m in dir(Flow) if m.startswith("test_") and m not in BarrotFlow.__dict__]:
    setattr(BarrotFlow, _n, None)                                                         # die Tests für den Realtek-Ablauf laufen nur in Flow


class Wiring(unittest.TestCase):
    def test_udev_rules_match_the_candidates(self):
        rules = rd(os.path.join(ROOT, "install", "80-pipbox-btdriver.rules"))
        got = set()
        for line in rules.splitlines():
            if line.startswith("ACTION"):
                self.assertNotIn(" #", line)                                  # udev kennt keine Kommentare am Zeilenende
                self.assertIn("SYSTEMD_WANTS", line)
                import re
                m = re.search(r'idVendor\}=="(\w+)", ATTR\{idProduct\}=="(\w+)"', line)
                got.add(f"{m.group(1)}:{m.group(2)}")
        self.assertEqual(got, set(bt.CANDIDATES))
        self.assertNotIn("0b05", rules)                                       # ASUS läuft ohne und löst nichts aus

    def test_units_and_installer(self):
        s = rd(os.path.join(ROOT, "install", "install.sh"))
        for needle in ('"$HERE/install/pipbox-btdriver.py" /opt/pipbox/pipbox-btdriver.py', "pipbox-btdriver.service", "pipbox-btdriver.timer",
                       "/etc/udev/rules.d/80-pipbox-btdriver.rules", "bluetooth-src", "btusb.c btintel.h btbcm.h btrtl.h COPYING README.md"):
            self.assertIn(needle, s)
        self.assertIn("pipbox-btdriver.py uninstall", s.split("uninstall)")[1])
        self.assertIn("pipbox-btdriver.timer", s.split("uninstall)")[1])
        self.assertIn("pipbox-btdriver.py auto", rd(os.path.join(ROOT, "install", "pipbox-btdriver.service")))
        self.assertIn("OnBootSec=", rd(os.path.join(ROOT, "install", "pipbox-btdriver.timer")))
        r = subprocess.run(["sh", "-n", os.path.join(ROOT, "install", "install.sh")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_service_runs_with_root_rights_only_for_this_task(self):
        """Der Helfer ist die einzige Stelle mit Root-Rechten für die Treiber: eingeschränkt auf Module laden und Kernelmeldungen lesen, Schreiben nur in
        wenigen Ordnern, keine neuen Rechte, keine Eingaben von außen."""
        unit = rd(os.path.join(ROOT, "install", "pipbox-btdriver.service"))
        want = {"NoNewPrivileges": "yes", "ProtectSystem": "strict", "ProtectHome": "yes", "PrivateTmp": "yes", "RestrictSUIDSGID": "yes",
                "ProtectKernelTunables": "yes", "RestrictNamespaces": "yes", "LockPersonality": "yes"}
        got = dict(x.split("=", 1) for x in unit.splitlines() if "=" in x and not x.startswith("#"))
        for k, v in want.items():
            self.assertEqual(got.get(k), v, k)
        self.assertEqual(set(got["CapabilityBoundingSet"].split()), {"CAP_SYS_MODULE", "CAP_SYSLOG", "CAP_DAC_OVERRIDE", "CAP_FOWNER", "CAP_CHOWN"})
        self.assertEqual(got["ReadWritePaths"].split(), ["/lib/modules", "/run"])
        self.assertEqual(got["StateDirectory"], "pipbox-btdriver")                         # legt /var/lib/pipbox-btdriver an (Merkzettel des Helfers)
        self.assertNotIn("User=", unit)                                                    # Root bleibt nötig (Module laden), aber nur mit diesen Einschränkungen
        self.assertEqual(got["ExecStart"].split()[-1], "auto")                             # fester Aufruf, keine Eingabe

    def test_mode_words_are_fixed(self):
        with mock.patch.object(sys, "argv", ["x", "rm -rf /"]), mock.patch.object(bt, "log", lambda m: None), \
                mock.patch.object(bt, "do_auto", lambda: self.fail("darf nicht laufen")):
            self.assertEqual(bt.main(), 0)


if __name__ == "__main__":
    unittest.main()
