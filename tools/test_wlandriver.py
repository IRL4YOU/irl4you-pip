"""Tests für den WLAN-Treiber-Helfer (install/pipbox-wlandriver.py, AIC8800D80, zum Beispiel UGREEN AX900). Ohne Hardware, ohne Netz, ohne Bau."""
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("pbwlan", os.path.join(ROOT, "install", "pipbox-wlandriver.py"))
wl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wl)


def rd(path):
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


FILES = {
    "drivers/aic8800/Makefile": b"all:\n",
    "drivers/aic8800/Kconfig": b"# k\n",
    "drivers/aic8800/aic_load_fw/aic_load_fw.c": b"int a;\n",
    "drivers/aic8800/aic8800_fdrv/rwnx_main.c": b"int b;\n",
    "fw/aic8800D80/fmacfw_8800d80_u02.bin": b"\x00fw" * 100,
    "fw/aic8800D80/fw_patch_8800d80_u02.bin": b"\x01patch",
}
EXTRA = {"README.md": b"nicht noetig", "fw/aic8800/other.bin": b"anderer Chip", "tests/x.sh": b"echo"}


def make_tar(files, top="aic8800d80-" + "1" * 40, extra=None, links=()):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in {**files, **(extra or {})}.items():
            ti = tarfile.TarInfo(top + "/" + name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        for name, target in links:
            ti = tarfile.TarInfo(top + "/" + name)
            ti.type = tarfile.SYMTYPE
            ti.linkname = target
            tf.addfile(ti)
    return buf.getvalue()


def digest_of(files):
    d = tempfile.mkdtemp()
    for name, data in files.items():
        p = os.path.join(d, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
    out = wl.tree_digest(d)
    shutil.rmtree(d, ignore_errors=True)
    return out


class Download(unittest.TestCase):
    def test_pinned_values_are_well_formed(self):
        self.assertRegex(wl.COMMIT, r"^[0-9a-f]{40}$")
        self.assertRegex(wl.TREE_SHA256, r"^[0-9a-f]{64}$")
        self.assertIn(wl.COMMIT, wl.URL)
        self.assertTrue(wl.URL.startswith("https://codeload.github.com/shenmintao/aic8800d80/tar.gz/"))

    def test_only_the_needed_parts_are_unpacked(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        wl.extract_needed(make_tar(FILES, extra=EXTRA), d)
        got = sorted(os.path.relpath(os.path.join(dp, f), d).replace(os.sep, "/") for dp, _n, fn in os.walk(d) for f in fn)
        self.assertEqual(got, sorted(FILES))

    def test_links_and_paths_outside_are_refused(self):
        for bad in (dict(links=[("drivers/aic8800/aic_load_fw/evil", "/etc/passwd")]),):
            d = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, d, True)
            with self.assertRaises(RuntimeError):
                wl.extract_needed(make_tar(FILES, **bad), d)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with self.assertRaises(RuntimeError):
            wl.extract_needed(make_tar({"drivers/aic8800/../../../x": b"x"}), d)

    def test_size_limit_of_the_unpacked_files(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with mock.patch.object(wl, "MAX_UNPACKED", 50), self.assertRaises(RuntimeError):
            wl.extract_needed(make_tar(FILES), d)

    def test_digest_decides_and_detects_any_change(self):
        good = digest_of(FILES)
        with mock.patch.object(wl, "TREE_SHA256", good):
            d = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, d, True)
            opener = lambda url, timeout=0: io.BytesIO(make_tar(FILES, extra=EXTRA))        # noqa: E731
            self.assertEqual(wl.fetch_source(d, opener), d)
            changed = dict(FILES, **{"drivers/aic8800/aic_load_fw/aic_load_fw.c": b"int evil;\n"})
            d2 = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, d2, True)
            with self.assertRaises(RuntimeError) as e:
                wl.fetch_source(d2, lambda url, timeout=0: io.BytesIO(make_tar(changed)))
            self.assertIn("Prüfsumme", str(e.exception))
        self.assertNotEqual(good, digest_of(dict(FILES, **{"fw/aic8800D80/fw_patch_8800d80_u02.bin": b"x"})))

    def test_download_limit_and_network_errors(self):
        with mock.patch.object(wl, "MAX_DOWNLOAD", 10):
            with self.assertRaises(RuntimeError):
                wl.download(opener=lambda url, timeout=0: io.BytesIO(b"x" * 100))

        def down(url, timeout=0):
            raise OSError("Name or service not known")
        with self.assertRaises(OSError):
            wl.download(opener=down)

    def test_the_download_is_a_context_manager_response(self):
        seen = {}

        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        wl.download(opener=lambda url, timeout=0: seen.update(url=url, timeout=timeout) or R(b"abc"))
        self.assertEqual(seen["url"], wl.URL)
        self.assertGreater(seen["timeout"], 0)


class Detection(unittest.TestCase):
    def test_only_the_ugreen_modes_count(self):
        self.assertEqual(list(wl.wanted({"a69c:5723", "8087:0a2b"})), ["a69c:5723"])
        self.assertEqual(list(wl.wanted({"a69c:8d80"})), ["a69c:8d80"])
        self.assertEqual(wl.wanted({"a69c:8d81"}), {})                                   # läuft schon
        self.assertEqual(wl.wanted({"a69c:5721", "a69c:5724", "0bda:c811"}), {})        # andere AIC-Chips: nicht unser Treiber

    def test_kernel_support(self):
        self.assertTrue(wl.kernel_supported("5.10.160-belabox"))
        self.assertFalse(wl.kernel_supported("5.10.200-belabox"))
        self.assertFalse(wl.kernel_supported("6.1.0"))

    def test_netdev_is_found_by_usb_id_through_the_path(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        usb = os.path.join(root, "usb", "5-1.4")
        os.makedirs(os.path.join(usb, "if0"))
        for n, v in (("idVendor", "a69c"), ("idProduct", "8d81")):
            with open(os.path.join(usb, n), "w") as f:
                f.write(v + "\n")
        net = os.path.join(root, "net")
        for n in ("eth0", "wlan0", "wlan1"):
            os.makedirs(os.path.join(net, n))
        where = {"wlan1": os.path.join(usb, "if0")}
        res = lambda p: where.get(os.path.basename(os.path.dirname(p)), p)               # noqa: E731
        self.assertEqual(wl.netdev_for_usb("a69c:8d81", net, res), "wlan1")
        self.assertEqual(wl.netdev_for_usb("a69c:5723", net, res), "")
        self.assertEqual(wl.netdev_for_usb("a69c:8d81", os.path.join(root, "nix"), res), "")

    def test_waiting_for_the_interface(self):
        names = iter(["", "", "wlan1"])
        t = [0]
        self.assertEqual(wl.wait_for_wlan(10, find=lambda i: next(names), sleep=lambda s: t.__setitem__(0, t[0] + s), now=lambda: t[0]), "wlan1")
        t[0] = 0
        self.assertEqual(wl.wait_for_wlan(4, find=lambda i: "", sleep=lambda s: t.__setitem__(0, t[0] + s), now=lambda: t[0]), "")


class Build(unittest.TestCase):
    def test_only_the_two_modules_are_built_with_kbuild_taking_precedence(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "drivers", "aic8800"))
        seen = {}

        def fake_run(args, **kw):
            if args[0] != "make":
                return mock.Mock(returncode=0, stdout="5.10.160-belabox SMP\n", stderr="")
            seen["args"] = args
            seen["kbuild"] = rd(os.path.join(d, "drivers", "aic8800", "Kbuild"))
            for m in wl.MODULES:
                os.makedirs(os.path.join(d, "drivers", "aic8800", m), exist_ok=True)
                with open(os.path.join(d, "drivers", "aic8800", m, m + ".ko"), "wb") as f:
                    f.write(b"ELF")
            return mock.Mock(returncode=0, stdout="5.10.160-belabox SMP\n", stderr="")
        with mock.patch.object(wl, "run", fake_run):
            out = wl.build_modules(d, "5.10.160-belabox")
        self.assertEqual(sorted(out), sorted(wl.MODULES))
        self.assertEqual(seen["kbuild"], "obj-m += aic_load_fw/\nobj-m += aic8800_fdrv/\n")            # nicht aic_zlp_quirk: der Ordner fehlt im Download
        self.assertNotIn("aic_zlp_quirk", seen["kbuild"])
        self.assertTrue(any(a.startswith("M=") and a.replace(os.sep, "/").endswith("drivers/aic8800") for a in seen["args"]))
        self.assertIn("modules", seen["args"])

    def test_a_failed_build_names_the_cause(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "drivers", "aic8800"))
        with mock.patch.object(wl, "run", lambda args, **kw: mock.Mock(returncode=2, stdout="", stderr="make: *** Error 2")), self.assertRaises(RuntimeError) as e:
            wl.build_modules(d, "5.10.160-belabox")
        self.assertIn("Bau des Treibers", str(e.exception))


class Switch(unittest.TestCase):
    def test_success_when_the_stick_disappears_as_a_drive(self):
        seq = iter([{wl.MSC_ID}, {wl.MSC_ID}, set()])
        with mock.patch.object(wl, "run", lambda args, **kw: mock.Mock(returncode=0, stdout="Bye!", stderr="")):
            self.assertEqual(wl.switch_mode(present=lambda: next(seq), sleep=lambda s: None, now=lambda: 0), "")

    def test_reason_when_it_stays_a_drive(self):
        t = [0]
        out = mock.Mock(returncode=0, stdout="Access device 022 on bus 005\nError opening the device. Abort\n", stderr="")
        with mock.patch.object(wl, "run", lambda args, **kw: out):
            err = wl.switch_mode(present=lambda: {wl.MSC_ID}, sleep=lambda s: t.__setitem__(0, t[0] + s), now=lambda: t[0], timeout=3)
        self.assertEqual(err, "Error opening the device. Abort")

    def test_the_command_is_fixed(self):
        seen = []
        with mock.patch.object(wl, "run", lambda args, **kw: seen.append(args) or mock.Mock(returncode=0, stdout="", stderr="")):
            wl.switch_mode(present=lambda: set(), sleep=lambda s: None, now=lambda: 0)
        self.assertEqual(seen, [["usb_modeswitch", "-KW", "-v", "a69c", "-p", "5723"]])


class Flow(unittest.TestCase):
    """do_auto mit nachgestellten Bausteinen."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.status = {}
        self.calls = []
        self.rel = "5.10.160-belabox"
        self.present = {"a69c:5723"}
        self.netdev = "wlan1"
        self.net_error = None
        self.build_error = None
        self.switch_error = ""
        patches = {"LIB_MODULES": os.path.join(self.d, "modules"), "FW_DIR": os.path.join(self.d, "fw", "USB", "aic8800D80"),
                   "MODPROBE_CONF": os.path.join(self.d, "modprobe.d", "pipbox-aic8800.conf"), "PERSIST": os.path.join(self.d, "persist"),
                   "TMPROOT": self.d, "RUN": os.path.join(self.d, "run"), "STATUS": os.path.join(self.d, "run", "status.json"), "LOCK": os.path.join(self.d, "lock")}
        for k, v in patches.items():
            p = mock.patch.object(wl, k, v)
            p.start()
            self.addCleanup(p.stop)
        good = digest_of(FILES)
        mp = [mock.patch.object(wl, "TREE_SHA256", good), mock.patch.object(wl, "release", lambda: self.rel),
              mock.patch.object(wl, "status", lambda **kw: self.status.update(kw)), mock.patch.object(wl, "log", lambda m: None),
              mock.patch.object(wl, "usb_ids_present", lambda root=None: set(self.present)),
              mock.patch.object(wl, "belacoder_running", lambda: False), mock.patch.object(wl, "tools_ok", lambda rel=None: []),
              mock.patch.object(wl, "build_modules", self.fake_build), mock.patch.object(wl, "run", self.fake_run),
              mock.patch.object(wl, "switch_mode", lambda: self.calls.append("switch") or self.switch_error),
              mock.patch.object(wl, "netdev_for_usb", lambda ident, *a, **k: self.netdev if ident == wl.WORKING_ID and not self.working_hidden else ""),
              mock.patch.object(wl, "wait_for_wlan", lambda timeout=60, **k: self.netdev)]
        self.working_hidden = False
        for p in mp:
            p.start()
            self.addCleanup(p.stop)
        self.tar = make_tar(FILES, extra=EXTRA)

    def opener(self, url, timeout=0):
        self.calls.append("download")
        if self.net_error:
            raise self.net_error

        class R(io.BytesIO):
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False
        return R(self.tar)

    def fake_build(self, src, rel=None):
        self.calls.append("build")
        if self.build_error:
            raise RuntimeError(self.build_error)
        out = {}
        for m in wl.MODULES:
            p = os.path.join(src, m + ".ko")
            with open(p, "wb") as f:
                f.write(b"ELF")
            out[m] = p
        return out

    def fake_run(self, args, **kw):
        self.calls.append(" ".join(args[:2]))
        return mock.Mock(returncode=0, stdout="", stderr="")

    def go(self):
        return wl.do_auto(self.opener)

    def test_nothing_to_do_without_the_stick(self):
        self.present = {"0bda:c811"}
        self.go()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.status, {})

    def test_other_kernel_is_reported_not_built(self):
        self.rel = "6.1.0"
        self.go()
        self.assertEqual(self.status["state"], "unsupported")
        self.assertEqual(self.calls, [])

    def test_waits_while_sending(self):
        with mock.patch.object(wl, "belacoder_running", lambda: True):
            self.go()
        self.assertEqual((self.status["state"], self.calls), ("waiting", []))

    def test_missing_tools_fail_once_without_downloading(self):
        with mock.patch.object(wl, "tools_ok", lambda rel=None: ["make"]):
            self.go()
            self.assertEqual(self.status["state"], "failed")
            self.assertIn("make", self.status["message"])
            self.go()
        self.assertNotIn("download", self.calls)

    def test_first_run_downloads_builds_installs_switches_and_remembers(self):
        self.go()
        self.assertEqual([c for c in self.calls if c in ("download", "build", "switch")], ["download", "build", "switch"])
        self.assertEqual(self.status["state"], "ok")
        self.assertIn("wlan1", self.status["message"])
        for m in wl.MODULES:
            self.assertTrue(os.path.isfile(f"{wl.module_dir(self.rel)}/{m}.ko"))
        self.assertTrue(os.path.isfile(os.path.join(wl.FW_DIR, "fmacfw_8800d80_u02.bin")))
        self.assertIn(f"aic_fw_path={wl.FW_DIR}", rd(wl.MODPROBE_CONF))
        rec = json.load(open(os.path.join(wl.PERSIST, "installed.json")))
        self.assertEqual((rec["kernel"], rec["commit"]), (self.rel, wl.COMMIT))
        self.assertTrue(wl.installed_ok(self.rel))
        self.assertIn("depmod -a", self.calls)

    def test_second_plug_in_only_switches_the_mode(self):
        self.go()
        self.calls.clear()
        self.go()
        self.assertEqual([c for c in self.calls if c in ("download", "build", "switch")], ["switch"])

    def test_a_stick_in_loader_mode_gets_its_module_loaded(self):
        self.go()
        self.calls.clear()
        self.present = {"a69c:8d80"}
        self.go()
        self.assertIn("modprobe aic_load_fw", self.calls)
        self.assertNotIn("download", self.calls)

    def test_a_stick_that_cannot_be_switched_is_reported_with_the_reason_and_rolled_back(self):
        self.switch_error = "Error opening the device. Abort"
        self.go()
        self.assertEqual(self.status["state"], "failed")
        self.assertIn("Error opening the device", self.status["message"])
        self.assertIn("WLAN-Modus", self.status["message"])
        self.assertFalse(os.path.exists(wl.module_dir(self.rel)))

    def test_nothing_happens_when_the_stick_already_works(self):
        self.present = {"a69c:8d81", "a69c:5723"}
        self.go()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.status["state"], "ok")

    def test_no_internet_waits_and_retries_later_without_a_marker(self):
        self.net_error = OSError("Name or service not known")
        self.go()
        self.assertEqual(self.status["state"], "waiting")
        self.assertIn("Internet", self.status["message"])
        self.assertFalse(os.path.exists(os.path.join(wl.PERSIST, "failed.json")))
        self.net_error = None
        self.calls.clear()
        self.go()
        self.assertIn("build", self.calls)
        self.assertEqual(self.status["state"], "ok")

    def test_wrong_download_changes_nothing_and_is_not_retried(self):
        self.tar = make_tar(dict(FILES, **{"drivers/aic8800/Makefile": b"evil:\n"}))
        self.go()
        self.assertEqual(self.status["state"], "failed")
        self.assertNotIn("build", self.calls)
        self.assertFalse(os.path.exists(wl.module_dir(self.rel)))
        self.calls.clear()
        self.go()
        self.assertEqual(self.calls, [])

    def test_build_failure_changes_nothing(self):
        self.build_error = "Der Bau des Treibers ist fehlgeschlagen: x"
        self.go()
        self.assertEqual(self.status["state"], "failed")
        self.assertFalse(os.path.exists(os.path.join(wl.PERSIST, "installed.json")))

    def test_no_interface_after_switching_rolls_everything_back(self):
        self.netdev = ""
        self.go()
        self.assertEqual(self.status["state"], "failed")
        self.assertIn("WLAN-Schnittstelle", self.status["message"])
        self.assertFalse(os.path.exists(wl.module_dir(self.rel)))
        self.assertFalse(os.path.exists(wl.MODPROBE_CONF))
        self.assertFalse(os.path.exists(os.path.join(wl.PERSIST, "installed.json")))
        self.calls.clear()
        self.go()
        self.assertEqual(self.calls, [])                                                 # nicht in einer Schleife

    def test_a_fixed_helper_may_try_again_after_an_earlier_failure(self):
        self.netdev = ""
        self.go()
        self.assertEqual(self.status["state"], "failed")
        self.calls.clear()
        self.netdev = "wlan1"
        with mock.patch.object(wl, "HELPER_REV", wl.HELPER_REV + 1):
            self.go()
        self.assertIn("download", self.calls)
        self.assertEqual(self.status["state"], "ok")

    def test_failure_marker_belongs_to_kernel_and_commit(self):
        self.netdev = ""
        self.go()
        self.netdev = "wlan1"
        self.calls.clear()
        self.rel = "5.10.160-neu"
        self.go()
        self.assertIn("download", self.calls)                                            # anderer Kernel: neuer Versuch

    def test_uninstall_removes_only_our_files(self):
        self.go()
        other = os.path.join(wl.LIB_MODULES, self.rel, "updates", "btusb.ko")
        os.makedirs(os.path.dirname(other), exist_ok=True)
        open(other, "w").close()
        wl.do_uninstall()
        self.assertFalse(os.path.exists(wl.module_dir(self.rel)))
        self.assertFalse(os.path.exists(wl.MODPROBE_CONF))
        self.assertTrue(os.path.exists(other))
        self.assertFalse(os.path.exists(os.path.join(wl.PERSIST, "installed.json")))


class Wiring(unittest.TestCase):
    def test_udev_rules_match_the_candidates(self):
        rules = rd(os.path.join(ROOT, "install", "81-pipbox-wlandriver.rules"))
        got = set()
        for line in rules.splitlines():
            if line.startswith("ACTION"):
                self.assertNotIn(" #", line)
                self.assertIn("SYSTEMD_WANTS", line)
                m = re.search(r'idVendor\}=="(\w+)", ATTR\{idProduct\}=="(\w+)"', line)
                got.add(f"{m.group(1)}:{m.group(2)}")
        self.assertEqual(got, set(wl.CANDIDATES))

    def test_service_runs_with_root_rights_only_for_this_task(self):
        unit = rd(os.path.join(ROOT, "install", "pipbox-wlandriver.service"))
        got = dict(x.split("=", 1) for x in unit.splitlines() if "=" in x and not x.startswith("#"))
        for k, v in {"NoNewPrivileges": "yes", "ProtectSystem": "strict", "ProtectHome": "yes", "PrivateTmp": "yes", "RestrictSUIDSGID": "yes",
                     "ProtectKernelTunables": "yes", "RestrictNamespaces": "yes", "LockPersonality": "yes"}.items():
            self.assertEqual(got.get(k), v, k)
        self.assertEqual(set(got["CapabilityBoundingSet"].split()), {"CAP_SYS_MODULE", "CAP_SYSLOG", "CAP_DAC_OVERRIDE", "CAP_FOWNER", "CAP_CHOWN"})
        self.assertEqual(set(got["ReadWritePaths"].split()), {"/lib/modules", "/lib/firmware", "/etc/modprobe.d", "/run"})
        self.assertEqual(got["StateDirectory"], "pipbox-wlandriver")
        self.assertEqual(got["DeviceAllow"], "char-usb_device rw")                                # sonst sperrt ProtectClock den USB-Zugriff von usb_modeswitch
        self.assertEqual(set(got["RestrictAddressFamilies"].split()), {"AF_UNIX", "AF_NETLINK", "AF_INET", "AF_INET6"})        # Netz nur für den festen Download
        self.assertNotIn("User=", unit)
        self.assertEqual(got["ExecStart"].split()[-1], "auto")

    def test_no_comment_behind_a_value_in_the_unit_files(self):
        for name in ("pipbox-wlandriver.service", "pipbox-wlandriver.timer", "pipbox-btdriver.service", "pipbox-btdriver.timer"):
            for line in rd(os.path.join(ROOT, "install", name)).splitlines():
                if "=" in line and not line.lstrip().startswith(("#", ";")):
                    self.assertNotIn(" #", line, (name, line))                                   # systemd macht daraus einen Teil des Werts

    def test_installer_ships_and_removes_everything(self):
        s = rd(os.path.join(ROOT, "install", "install.sh"))
        for need in ('"$HERE/install/pipbox-wlandriver.py" /opt/pipbox/pipbox-wlandriver.py', "pipbox-wlandriver.service", "pipbox-wlandriver.timer",
                     "/etc/udev/rules.d/81-pipbox-wlandriver.rules", "pipbox-wlandriver.py uninstall"):
            self.assertIn(need, s)
        self.assertIn("pipbox-wlandriver.timer", s.split("systemctl enable --now")[1].split("\n")[0])
        self.assertIn("pipbox-wlandriver.service", s.split("systemctl start --no-block")[1].split("\n")[0])
        r = subprocess.run(["sh", "-n", os.path.join(ROOT, "install", "install.sh")], capture_output=True, text=True) if os.name == "posix" else None
        if r is not None:
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_timer_and_mode_words(self):
        self.assertIn("OnBootSec=", rd(os.path.join(ROOT, "install", "pipbox-wlandriver.timer")))
        with mock.patch.object(sys, "argv", ["x", "rm -rf /"]), mock.patch.object(wl, "log", lambda m: None), \
                mock.patch.object(wl, "do_auto", lambda: self.fail("darf nicht laufen")):
            self.assertEqual(wl.main(), 0)


class Surface(unittest.TestCase):
    """Die Meldung des Helfers erscheint in der Oberfläche (Karte Verbindungen, WLAN-Verbindungen)."""

    def test_wifi_status_carries_the_driver_message(self):
        import types
        sys.path.insert(0, ROOT)
        sys.modules.setdefault("dbus", types.ModuleType("dbus"))
        import server
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "status.json")
        w = server.Wifi(d, False, None)
        with mock.patch.object(server.Wifi, "DRIVER_STATUS", path):
            self.assertEqual(w.driver_status(), {})
            with open(path, "w") as f:
                json.dump({"state": "working", "message": "Treiber wird gebaut …", "ids": ["a69c:5723"], "extra": "x"}, f)
            self.assertEqual(w.driver_status(), {"state": "working", "message": "Treiber wird gebaut …"})
            with open(path, "w") as f:
                f.write("{kaputt")
            self.assertEqual(w.driver_status(), {})

    def test_page_shows_it_only_while_there_is_something_to_say(self):
        page = rd(os.path.join(ROOT, "web", "index.html"))
        self.assertIn('<div id="wifidriver" class="ph"', page)
        self.assertTrue(page.index('id="wifidriver"') < page.index('id="wifimsg"'))
        self.assertIn('["working","waiting","failed","unsupported"].includes(dr.state)', page)
        self.assertIn('d.driver||{}', page)
        self.assertIn('"driver": self.driver_status()', rd(os.path.join(ROOT, "server.py")))


class StatusNames(unittest.TestCase):
    """Im Status steht hinter der Schnittstelle der Name: "wlan0 (TP-Link Archer T2U)" (eigener Name, sonst der Name des Sticks)."""

    def setUp(self):
        import types
        sys.path.insert(0, ROOT)
        sys.modules.setdefault("dbus", types.ModuleType("dbus"))
        import server
        self.server = server
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        net = os.path.join(self.d, "net")
        for n, vid, pid, product, vendor in (("wlan0", "a69c", "8d81", "AIC 8800D80", "aicsemi"), ("wlan1", "2357", "011e", "802.11ac NIC", "Realtek")):
            os.makedirs(os.path.join(net, n, "wireless"))
            dev = os.path.join(net, n, "device")                     # (echtes Verzeichnis statt Verknüpfung: so geht es auch unter Windows)
            os.makedirs(dev)
            for f, v in (("idVendor", vid), ("idProduct", pid), ("product", product), ("manufacturer", vendor)):
                with open(os.path.join(dev, f), "w") as fh:
                    fh.write(v + "\n")
        os.makedirs(os.path.join(net, "eth0"))
        os.makedirs(os.path.join(net, "p2p-dev-wlan0", "wireless"))
        self.net = net
        server._TTL.pop("wifi_labels", None)
        self.addCleanup(server._TTL.pop, "wifi_labels", None)

    def wifi(self, names=None):
        w = self.server.Wifi(self.d, False, None, names)
        w.SYS_NET = self.net
        return w

    def test_default_names_come_from_the_stick(self):
        with mock.patch.object(self.server.dji, "hwdb_names", lambda uid: ("", "")):
            labels = self.wifi().labels()
        self.assertEqual(labels["wlan0"], "AIC 8800D80")
        self.assertEqual(labels["wlan1"], "Realtek 802.11ac NIC")                              # Standardbezeichnung: mit Hersteller
        self.assertEqual(sorted(labels), ["wlan0", "wlan1"])                                  # kein eth0, kein p2p-dev

    def test_own_names_of_the_card_win(self):
        class Names:
            def label(self, key, default):
                return "Mein Stick" if key == "usb:a69c:8d81" else default

            def conn_names(self):
                return {"eth0": "Router"}
        with mock.patch.object(self.server.dji, "hwdb_names", lambda uid: ("", "")):
            labels = self.wifi(Names()).labels()
        self.assertEqual(labels["wlan0"], "Mein Stick")
        self.assertEqual(labels["wlan1"], "Realtek 802.11ac NIC")

    def test_status_labels_merge_cards_and_own_names_of_the_connections(self):
        wifi = mock.Mock()
        wifi.labels.return_value = {"wlan0": "AIC 8800D80", "wlan1": "Realtek 802.11ac NIC"}
        names = mock.Mock()
        names.conn_names.return_value = {"eth0": "Router", "wlan1": "Handy-Hotspot"}
        h = object.__new__(self.server.Handler)
        h.wifi, h.names = wifi, names
        self.assertEqual(h.conn_labels(), {"wlan0": "AIC 8800D80", "wlan1": "Handy-Hotspot", "eth0": "Router"})     # eigener Name der Verbindung geht vor
        h.wifi = None
        self.assertEqual(h.conn_labels(), {"eth0": "Router", "wlan1": "Handy-Hotspot"})
        h.names = None
        self.assertEqual(h.conn_labels(), {})

    def test_both_status_routes_use_the_merged_names(self):
        with open(os.path.join(ROOT, "server.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(src.count("self.conn_labels()"), 2)
        self.assertNotIn('m["conn_names"] = self.names.conn_names()', src)

    def test_page_prints_name_behind_the_interface(self):
        self.assertIn("const ifd=n=>connNames[n]?`${n} (${connNames[n]})`:n;", rd(os.path.join(ROOT, "web", "index.html")))


if __name__ == "__main__":
    unittest.main()
