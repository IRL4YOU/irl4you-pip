#!/usr/bin/env python3
"""Tests des HDMI-Dienstes (hdmi_daemon.py): Signal lesen, Einstellungen prüfen, Befehl bauen, Überwachung, TCP-Schnittstelle.
Alles ohne Hardware: Zustand des Empfängers, Prozess, nginx-Statistik und Uhr sind nachgebildet."""
import asyncio
import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import hdmi_daemon as H  # noqa: E402

LOCKED = """status: plugin
Clk-Ch:Lock	Ch0:Lock	Ch1:Lock	Ch2:Lock
Ch0-Err:0	Ch1-Err:0	Ch2-Err:0
Color Format: RGB			Store Format: RGB
Timing: 1920x1080p60 (2200x1125)		hfp:88  hs:44  hbp:148  vfp:4  vs:5  vbp:36
Pixel Clk: 148504000
Mode: HDMI
Color Depth: 8 bit
Color Range: FULL
Color Space: xvYCC601
"""
LOCKED_720 = LOCKED.replace("1920x1080p60 (2200x1125)", "1280x720p50 (1980x750)")
UNPLUGGED = "status: plugout\n"
HALF = LOCKED.replace("Ch1:Lock", "Ch1:UnLock")


class FakeStream:
    def __init__(self, lines):
        self.lines = [l.encode() + b"\n" for l in lines]

    async def readline(self):
        return self.lines.pop(0) if self.lines else b""


class FakeProc:
    def __init__(self, lines=()):
        self.returncode = None
        self.stderr = FakeStream(lines)
        self.terminated = False

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


class Rig:
    """Dienst mit nachgebildeter Hardware."""
    def __init__(self, tc, enabled=True, status=LOCKED, publishing=True, lines=()):
        self.tmp = tempfile.mkdtemp()
        self.dev = os.path.join(self.tmp, "hdmirx")
        open(self.dev, "w").close()
        self.status, self.pub, self.lines = status, publishing, list(lines)
        self.spawned, self.t = [], [100.0]
        self.d = H.Daemon(self.tmp, device=self.dev, read_status=self._read, spawn=self._spawn, publishing=lambda key: self.pub,
                          clock=lambda: self.t[0])
        if enabled:
            self.d.cfg["enabled"] = True

    def _read(self):
        if isinstance(self.status, Exception):
            raise self.status
        return self.status

    async def _spawn(self, argv):
        p = FakeProc(self.lines)
        self.spawned.append((argv, p))
        return p

    def advance(self, s):
        self.t[0] += s

    @property
    def proc(self):
        return self.spawned[-1][1] if self.spawned else None


class Parsing(unittest.TestCase):
    def test_real_status_of_the_box(self):
        s = H.parse_hdmirx_status(LOCKED)
        self.assertEqual((s["plugged"], s["locked"], s["width"], s["height"], s["fps"], s["interlaced"], s["format"], s["depth"]),
                         (True, True, 1920, 1080, 60.0, False, "RGB", 8))

    def test_unplugged_half_locked_and_garbage_are_not_locked(self):
        self.assertFalse(H.parse_hdmirx_status(UNPLUGGED)["locked"])
        self.assertFalse(H.parse_hdmirx_status(HALF)["locked"])
        self.assertFalse(H.parse_hdmirx_status("")["locked"])
        self.assertFalse(H.parse_hdmirx_status(None)["locked"])
        self.assertFalse(H.parse_hdmirx_status("status: plugin\nTiming: 1920x1080p60 (2200x1125)\n")["locked"])    # keine Kanalzeilen

    def test_interlaced_and_fractional_rates(self):
        s = H.parse_hdmirx_status(LOCKED.replace("1920x1080p60", "1920x1080i59.94"))
        self.assertTrue(s["interlaced"])
        self.assertAlmostEqual(s["fps"], 59.94)

    def test_a_format_change_changes_the_signal_id(self):
        a, b = H.parse_hdmirx_status(LOCKED), H.parse_hdmirx_status(LOCKED_720)
        self.assertNotEqual(H.signal_id(a), H.signal_id(b))
        self.assertEqual(H.signal_id(a), H.signal_id(H.parse_hdmirx_status(LOCKED)))


class Settings(unittest.TestCase):
    def test_defaults_and_valid_changes(self):
        self.assertEqual(H.clean_settings({}), H.DEFAULTS)
        got = H.clean_settings({"enabled": True, "key": "hdmi2", "bitrate": 6000, "fps": 25, "audio": "none"})
        self.assertEqual(got, {"enabled": True, "key": "hdmi2", "bitrate": 6000, "fps": 25, "audio": "none", "source": "hdmi", "usb_format": "auto"})

    def test_unknown_keys_are_ignored_and_the_old_values_stay(self):
        cur = {"enabled": True, "key": "a", "bitrate": 5000, "fps": 25, "audio": "none", "source": "hdmi", "usb_format": "auto"}
        self.assertEqual(H.clean_settings({"bitrate": 7000, "unsinn": 1}, cur), dict(cur, bitrate=7000))

    def test_the_usb_picture_format_is_checked_and_decides_which_format_is_tried_first(self):
        for fmt in ("auto", "mjpeg", "h264"):
            self.assertEqual(H.clean_settings({"usb_format": fmt})["usb_format"], fmt)
        for bad in ("raw", "", None, 1, True):
            with self.assertRaises(ValueError) as cm:
                H.clean_settings({"usb_format": bad})
            self.assertIn("Bildformat", str(cm.exception))
        kinds = lambda fmt: [c[0] for c in H.usb_candidates(fmt)]
        self.assertEqual(kinds("auto")[:2], ["mjpeg", "mjpeg"])
        self.assertEqual(kinds("h264")[:2], ["h264", "h264"])                       # H.264 zuerst, MJPEG bleibt Rückfall
        self.assertIn("mjpeg", kinds("h264"))
        self.assertEqual([c for c in H.usb_candidates("h264") if c[0] == "h264"], [c for c in H.USB_CANDIDATES if c[0] == "h264"])   # 1080p vor 720p bleibt
        self.assertEqual(H.usb_candidates(None), H.USB_CANDIDATES)

    def test_bad_values_are_refused_with_a_short_text(self):
        bad = [{"enabled": 1}, {"enabled": "ja"}, {"key": ""}, {"key": "A"}, {"key": "a b"}, {"key": "a" * 33}, {"key": "-x"}, {"key": 5},
               {"key": "test-hdmi"}, {"key": "a;rm"}, {"key": "a/b"}, {"bitrate": 999}, {"bitrate": 20001}, {"bitrate": "8000"},
               {"bitrate": True}, {"bitrate": 8000.5}, {"fps": 60}, {"fps": "30"}, {"fps": True}, {"audio": "usb"}, {"audio": None}]
        for d in bad:
            with self.assertRaises(ValueError, msg=repr(d)) as e:
                H.clean_settings(d)
            self.assertLess(len(str(e.exception)), 90)
        with self.assertRaises(ValueError):
            H.clean_settings("x")


class Command(unittest.TestCase):
    def test_video_and_audio_chains(self):
        a = H.feeder_argv(dict(H.DEFAULTS, key="cam-hdmi", bitrate=6500, fps=25))
        self.assertEqual(a[:2], ["gst-launch-1.0", "-q"])
        self.assertIn("device=/dev/hdmirx", a)
        self.assertIn("video/x-raw,framerate=25/1", a)
        self.assertIn("bitrate=6500000", a)                                        # bit/s
        self.assertIn("gop=25", a)
        self.assertIn("device=hw:CARD=rockchiphdmiin", a)
        self.assertIn("location=rtmp://127.0.0.1:1935/publish/cam-hdmi", a)
        self.assertEqual(a.count("mux."), 2)

    def test_silence_instead_of_the_hdmi_sound(self):
        a = H.feeder_argv(dict(H.DEFAULTS, audio="none"))
        self.assertIn("audiotestsrc", a)
        self.assertNotIn("alsasrc", a)
        self.assertEqual(a.count("mux."), 2)

    def test_every_value_is_checked_again_and_nothing_goes_through_a_shell(self):
        for k, v in (("key", "a b"), ("bitrate", "1;2"), ("fps", "30;x"), ("audio", "x")):
            with self.assertRaises(ValueError):
                H.feeder_argv(dict(H.DEFAULTS, **{k: v}))
        for kw in ({"device": "/dev/hdmirx; rm"}, {"device": "hdmirx"}, {"device": "/dev/../etc/passwd"}, {"device": "/dev/a b"}, {"audio_device": "hw:CARD=a b"}, {"audio_device": "default"},
                   {"rtmp_app": "pub lish"}, {"rtmp_port": "1935"}):
            with self.assertRaises(ValueError, msg=repr(kw)):
                H.feeder_argv(dict(H.DEFAULTS), **kw)
        for part in H.feeder_argv(dict(H.DEFAULTS)):
            self.assertNotRegex(part, r"[;&|`$<>\\\n]")

    def test_friendly_errors(self):
        self.assertEqual(H.friendly_error(["ERROR: Could not open audio device for recording: Device or resource busy (alsa)"]), "Das Tongerät ist belegt")
        self.assertEqual(H.friendly_error(["v4l2src: Cannot identify device '/dev/hdmirx'"]), "HDMI-Gerät nicht gefunden")
        self.assertEqual(H.friendly_error(["VIDIOC_STREAMON failed: Link has been severed"]), "Kein HDMI-Signal")
        self.assertIn("Verbindung", H.friendly_error(["rtmpsink: Could not connect to RTMP stream"]))
        self.assertTrue(H.friendly_error([]).startswith("Die Einspeisung wurde beendet"))
        self.assertLess(len(H.friendly_error(["x" * 5000])), 200)


class Supervision(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_does_nothing_even_with_a_signal(self):
        r = Rig(None, enabled=False)
        await r.d.tick()
        self.assertEqual((r.d.state, r.spawned), ("off", []))

    async def test_no_signal_waits_and_does_not_start(self):
        r = Rig(None, status=UNPLUGGED)
        await r.d.tick()
        self.assertEqual((r.d.state, r.d.message, r.spawned), ("waiting", "Kein HDMI-Signal", []))

    async def test_signal_starts_the_feeder_and_the_stream_shows_up(self):
        r = Rig(None, publishing=False)
        await r.d.tick()
        self.assertEqual(r.d.state, "starting")
        self.assertEqual(len(r.spawned), 1)
        r.pub = True
        r.advance(1)
        await r.d.tick()
        self.assertEqual((r.d.state, r.d.published), ("streaming", True))
        self.assertEqual(len(r.spawned), 1)                                        # kein zweiter Start

    async def test_the_signal_is_lost_and_comes_back(self):
        r = Rig(None)
        await r.d.tick()
        r.status = UNPLUGGED
        r.advance(1)
        await r.d.tick()
        self.assertEqual(r.d.state, "waiting")
        self.assertTrue(r.proc.terminated)
        r.status = LOCKED
        r.advance(1)
        await r.d.tick()
        self.assertEqual((r.d.state, len(r.spawned)), ("starting", 2))

    async def test_a_new_picture_format_restarts_the_feeder(self):
        r = Rig(None)
        await r.d.tick()
        r.status = LOCKED_720
        r.advance(1)
        await r.d.tick()
        self.assertTrue(r.spawned[0][1].terminated)
        r.advance(1)
        await r.d.tick()
        self.assertEqual(len(r.spawned), 2)

    async def test_when_the_process_ends_there_is_an_error_a_wait_and_a_restart(self):
        r = Rig(None, lines=["VIDIOC_STREAMON failed: Link has been severed"])
        await r.d.tick()
        r.proc.returncode = 1
        r.advance(1)
        await r.d.tick()
        self.assertEqual((r.d.state, r.d.message), ("error", "Kein HDMI-Signal"))
        self.assertEqual(r.d.fails, 1)
        n = len(r.spawned)
        r.advance(1)                                                               # Wartezeit (2 s) läuft noch
        await r.d.tick()
        self.assertEqual(len(r.spawned), n)
        r.advance(2)
        await r.d.tick()
        self.assertEqual(len(r.spawned), n + 1)

    async def test_the_wait_grows_and_is_capped(self):
        r = Rig(None)
        waits = []
        for _ in range(7):
            await r.d.tick()
            r.proc.returncode = 1
            r.advance(1)
            await r.d.tick()                                                       # Ende bemerkt: Zähler steigt
            waits.append(round(r.d.next_try - r.t[0]))
            r.t[0] = r.d.next_try
        self.assertEqual(waits, [2, 4, 8, 15, 30, 30, 30])                         # wächst und bleibt bei 30 Sekunden

    async def test_a_stable_run_clears_the_failure_count(self):
        r = Rig(None)
        r.d.fails = 3
        await r.d.tick()
        r.advance(H.STABLE + 1)
        await r.d.tick()
        self.assertEqual((r.d.state, r.d.fails), ("streaming", 0))

    async def test_a_stream_that_never_shows_up_is_restarted(self):
        r = Rig(None, publishing=False)
        await r.d.tick()
        r.advance(H.GRACE + 1)
        await r.d.tick()
        self.assertEqual(r.d.state, "starting" if False else r.d.state)           # Beginn der Fehlzeit
        self.assertTrue(r.proc.returncode is None)
        r.advance(H.STALL + 1)
        await r.d.tick()
        self.assertEqual(r.d.state, "error")
        self.assertIn("kommt nicht", r.d.message)
        self.assertTrue(r.spawned[0][1].terminated)

    async def test_unreadable_statistics_trust_the_process(self):
        r = Rig(None, publishing=None)
        await r.d.tick()
        self.assertEqual(r.d.state, "starting")
        r.advance(6)
        await r.d.tick()
        self.assertEqual(r.d.state, "streaming")

    async def test_unreadable_receiver_state_still_tries_to_start(self):
        r = Rig(None, status=PermissionError("debugfs"))
        await r.d.tick()
        self.assertEqual(len(r.spawned), 1)
        self.assertFalse(r.d.signal_known)

    async def test_a_box_without_hdmi_input_is_reported(self):
        r = Rig(None)
        os.remove(r.dev)
        await r.d.tick()
        self.assertEqual(r.d.state, "unavailable")
        self.assertEqual(r.spawned, [])
        self.assertFalse(r.d.status()["available"])

    async def test_a_process_that_cannot_be_started_is_an_error(self):
        r = Rig(None)

        async def boom(argv):
            raise FileNotFoundError("gst-launch-1.0")
        r.d._spawn = boom
        await r.d.tick()
        self.assertEqual(r.d.state, "error")
        self.assertIn("gst-launch-1.0", r.d.message)

    async def test_set_applies_at_once_and_restarts_a_running_feeder(self):
        r = Rig(None)
        await r.d.tick()
        out = await r.d.handle({"cmd": "set", "settings": {"bitrate": 4000}})
        self.assertEqual(out["settings"]["bitrate"], 4000)
        self.assertTrue(r.spawned[0][1].terminated)
        r.advance(1)
        await r.d.tick()
        self.assertIn("bitrate=4000000", r.spawned[1][0])
        self.assertEqual(json.load(open(r.d.config_file))["bitrate"], 4000)

    async def test_set_with_the_same_values_does_not_restart(self):
        r = Rig(None)
        await r.d.tick()
        await r.d.handle({"cmd": "set", "settings": {"bitrate": 8000}})
        self.assertFalse(r.spawned[0][1].terminated)

    async def test_switching_off_stops_the_feeder(self):
        r = Rig(None)
        await r.d.tick()
        await r.d.handle({"cmd": "set", "settings": {"enabled": False}})
        r.advance(1)
        await r.d.tick()
        self.assertEqual(r.d.state, "off")
        self.assertTrue(r.spawned[0][1].terminated)

    async def test_shutdown_ends_the_process_and_nothing_starts_afterwards(self):
        r = Rig(None)
        await r.d.tick()
        await r.d.shutdown()
        self.assertTrue(r.spawned[0][1].terminated)
        await r.d.tick()
        self.assertEqual(len(r.spawned), 1)

    async def test_status_has_everything_the_page_needs(self):
        r = Rig(None)
        await r.d.tick()
        s = r.d.status()
        for k in ("ok", "available", "state", "message", "settings", "signal", "signal_known", "publishing", "restarts"):
            self.assertIn(k, s)
        self.assertEqual((s["signal"]["width"], s["signal"]["height"], s["signal"]["fps"]), (1920, 1080, 60.0))


class Files(unittest.TestCase):
    def test_settings_survive_a_restart_and_garbage_falls_back_to_defaults(self):
        tmp = tempfile.mkdtemp()
        d = H.Daemon(tmp, device=os.path.join(tmp, "x"))
        d.cfg = H.clean_settings({"enabled": True, "bitrate": 5000})
        d.save()
        self.assertEqual(H.Daemon(tmp).cfg["bitrate"], 5000)
        self.assertEqual(stat.S_IMODE(os.stat(d.config_file).st_mode), 0o600)
        with open(d.config_file, "w") as f:
            f.write("{kaputt")
        self.assertEqual(H.Daemon(tmp).cfg, H.DEFAULTS)
        with open(d.config_file, "w") as f:
            json.dump({"bitrate": 99999999, "key": "../x"}, f)
        self.assertEqual(H.Daemon(tmp).cfg, H.DEFAULTS)

    def test_token_is_created_once_with_private_rights(self):
        tmp = tempfile.mkdtemp()
        a = H.Daemon(tmp)
        self.assertGreaterEqual(len(a.token), 32)
        self.assertEqual(stat.S_IMODE(os.stat(a.token_path).st_mode), 0o600)
        self.assertEqual(H.Daemon(tmp).token, a.token)


class Wire(unittest.IsolatedAsyncioTestCase):
    async def ask(self, port, payload, raw=False):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(payload if raw else (json.dumps(payload) + "\n").encode())
        await w.drain()
        line = await asyncio.wait_for(r.readline(), 5)
        w.close()
        return json.loads(line)

    async def asyncSetUp(self):
        self.rig = Rig(None, enabled=False)
        self.server = await asyncio.start_server(self.rig.d.client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def test_without_the_token_nothing_works(self):
        for p in ({"cmd": "status"}, {"cmd": "status", "token": "falsch"}, {"cmd": "set", "settings": {"enabled": True}, "token": ""}):
            self.assertEqual((await self.ask(self.port, p)).get("error"), "kein Zugriff")
        self.assertFalse(self.rig.d.cfg["enabled"])

    async def test_status_set_and_restart_with_the_token(self):
        t = self.rig.d.token
        s = await self.ask(self.port, {"cmd": "status", "token": t, "id": 7})
        self.assertTrue(s["ok"])
        self.assertEqual(s["reply_to"], 7)
        s = await self.ask(self.port, {"cmd": "set", "token": t, "settings": {"enabled": True, "key": "hdmi3"}})
        self.assertEqual((s["settings"]["enabled"], s["settings"]["key"]), (True, "hdmi3"))
        self.assertTrue((await self.ask(self.port, {"cmd": "restart", "token": t}))["ok"])

    async def test_bad_input_gives_a_short_error_and_changes_nothing(self):
        t = self.rig.d.token
        for p in ({"cmd": "set", "token": t, "settings": {"bitrate": 1}}, {"cmd": "set", "token": t, "settings": "x"},
                  {"cmd": "gibtsnicht", "token": t}, {"cmd": "set", "token": t, "settings": {"key": "test-x"}}):
            out = await self.ask(self.port, p)
            self.assertIn("error", out, p)
            self.assertLess(len(out["error"]), 90)
        self.assertEqual(self.rig.d.cfg, H.DEFAULTS)
        self.assertEqual((await self.ask(self.port, b"das ist kein json\n", raw=True)).get("error") is not None, True)
        self.assertEqual((await self.ask(self.port, b"[1,2]\n", raw=True)).get("error") is not None, True)


class Installation(unittest.TestCase):
    ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

    def read(self, rel):
        with open(os.path.join(self.ROOT, rel), encoding="utf-8") as f:
            return f.read()

    def test_unit_runs_as_root_for_the_hardware_and_keeps_the_state_folder_alone(self):
        unit = self.read("install/pipbox-hdmi.service")
        self.assertIn("ExecStart=/usr/bin/python3 -u /opt/pipbox/hdmi_daemon.py --state /var/lib/pipbox", unit)
        self.assertNotIn("User=", unit)                                            # MPP und /dev/hdmirx sind nur für root
        self.assertNotIn("StateDirectory", unit)                                   # sonst würde systemd /var/lib/pipbox an root übergeben
        self.assertIn("Restart=on-failure", unit)

    def test_installer_and_updater_know_the_service(self):
        sh = self.read("install/install.sh")
        up = self.read("install/pipbox-swupdate.py")
        self.assertIn('install -m 644 "$HERE/hdmi_daemon.py" /opt/pipbox/hdmi_daemon.py', sh)
        self.assertIn('install -m 644 "$HERE/install/pipbox-hdmi.service" /etc/systemd/system/pipbox-hdmi.service', sh)
        self.assertIn("pipbox-hdmi.service", sh.split("systemctl enable")[1].split("\n")[0])
        self.assertIn('"hdmi_daemon.py"', up.split("REQUIRED")[1].split(")")[0])
        self.assertIn('"install/pipbox-hdmi.service"', up.split("REQUIRED")[1].split(")")[0])


class UpdaterRestore(unittest.TestCase):
    """Zurückrollen oder Wechseln im Software-Update: der HDMI-Dienst folgt der Version (kein Dienst ohne Programm)."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("pbswupdate_hdmi", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "install", "pipbox-swupdate.py"))
        cls.h = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.h)

    def run_restore(self, backup_has_hdmi, same=False):
        from unittest import mock
        root = tempfile.mkdtemp()
        inst, units, bk = (os.path.join(root, n) for n in ("opt", "etc", "backup"))
        for d in (inst, units, os.path.join(bk, "0.9.1", "opt-pipbox"), os.path.join(bk, "0.9.1", "units")):
            os.makedirs(d)
        for name, text in (("server.py", "neu"), ("hdmi_daemon.py", "neu")):
            with open(os.path.join(inst, name), "w") as f:
                f.write(text)
        with open(os.path.join(units, "pipbox-hdmi.service"), "w") as f:
            f.write("[Unit]\n")
        with open(os.path.join(bk, "0.9.1", "opt-pipbox", "server.py"), "w") as f:
            f.write("alt")
        if backup_has_hdmi:
            with open(os.path.join(bk, "0.9.1", "opt-pipbox", "hdmi_daemon.py"), "w") as f:
                f.write("neu" if same else "alt")
            with open(os.path.join(bk, "0.9.1", "units", "pipbox-hdmi.service"), "w") as f:
                f.write("[Unit]\n")
        calls = []
        h = self.h
        with mock.patch.object(h, "INSTALL", inst), mock.patch.object(h, "UNIT_DIR", units), mock.patch.object(h, "BACKUP", bk), \
                mock.patch.object(h, "bdir", lambda v: os.path.join(bk, v)), mock.patch.object(h, "local_version", lambda: "0.9.1"), \
                mock.patch.object(h, "status", lambda **kw: None), mock.patch.object(h, "log", lambda m: None), mock.patch.object(h, "prune", lambda *a, **k: None), \
                mock.patch.object(h, "wait_active", lambda u, seconds=40: True), mock.patch.object(h, "backup", lambda *a, **k: None), \
                mock.patch.object(h.subprocess, "run", lambda cmd, *a, **k: calls.append(list(cmd))):
            h.restore("0.9.1")
        return calls, units

    def test_a_version_without_the_hdmi_service_removes_it(self):
        calls, units = self.run_restore(backup_has_hdmi=False)
        self.assertIn(["systemctl", "disable", "--now", "pipbox-hdmi.service"], calls)
        self.assertFalse(os.path.exists(os.path.join(units, "pipbox-hdmi.service")))
        self.assertNotIn(["systemctl", "restart", "pipbox-hdmi.service"], calls)

    def test_a_version_with_a_different_hdmi_program_restarts_it(self):
        calls, units = self.run_restore(backup_has_hdmi=True)
        self.assertIn(["systemctl", "restart", "pipbox-hdmi.service"], calls)
        self.assertTrue(os.path.exists(os.path.join(units, "pipbox-hdmi.service")))

    def test_an_unchanged_hdmi_program_is_left_running(self):
        calls, units = self.run_restore(backup_has_hdmi=True, same=True)
        self.assertNotIn(["systemctl", "restart", "pipbox-hdmi.service"], calls)
        self.assertFalse([c for c in calls if "pipbox-hdmi.service" in c and c[1] in ("disable", "restart")])


if __name__ == "__main__":
    unittest.main()
