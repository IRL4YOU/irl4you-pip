"""Tests für die Quelle "USB-Webcam" des HDMI-Dienstes (hdmi_daemon.py): Erkennen einer UVC-Kamera und ihres Tons in einem nachgebauten /sys und /proc, Bildformat-
Auswahl durch Probeläufe, Befehl der Einspeisung und das Verhalten des Dienstes (wartet ohne Kamera, startet mit Kamera, Fehlermeldungen). Alles ohne Hardware;
mit einer echten Kamera ist es nicht geprüft."""
import asyncio
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import hdmi_daemon as H  # noqa: E402
import server  # noqa: E402
import test_hdmi as T  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class Tree:
    """Nachbau von /sys/class/video4linux und /proc/asound mit einer USB-Kamera (Bus 1, Gerät 4) und einer Kamera ohne uvcvideo-Treiber."""
    def __init__(self):
        self.root = tempfile.mkdtemp()
        self.v4l = os.path.join(self.root, "class", "video4linux")
        self.asound = os.path.join(self.root, "asound")
        os.makedirs(self.v4l)
        os.makedirs(self.asound)
        os.makedirs(os.path.join(self.root, "drivers", "uvcvideo"))
        os.makedirs(os.path.join(self.root, "drivers", "rk_hdmirx"))

    def video(self, node, name, index, usb=None, driver="uvcvideo"):
        """usb: (busnum, devnum, vid, pid) oder None (kein USB-Gerät)."""
        dev = os.path.join(self.root, "devices", "usb%s" % (usb[0] if usb else "x"), "%s-1" % (usb[0] if usb else "x"))
        iface = os.path.join(dev, "%s-1:1.%d" % (usb[0] if usb else "x", index))
        os.makedirs(iface, exist_ok=True)
        if usb:
            write(os.path.join(dev, "busnum"), usb[0] + "\n")
            write(os.path.join(dev, "devnum"), usb[1] + "\n")
            write(os.path.join(dev, "idVendor"), usb[2] + "\n")
            write(os.path.join(dev, "idProduct"), usb[3] + "\n")
        link = os.path.join(iface, "driver")
        if not os.path.lexists(link):
            os.symlink(os.path.join(self.root, "drivers", driver), link)
        vdir = os.path.join(self.root, "devices", "v4l", node)
        os.makedirs(vdir, exist_ok=True)
        write(os.path.join(vdir, "name"), name + "\n")
        write(os.path.join(vdir, "index"), "%d\n" % index)
        for link, target in ((os.path.join(vdir, "device"), iface), (os.path.join(self.v4l, node), vdir)):
            if os.path.lexists(link):
                os.remove(link)
            os.symlink(target, link)

    def card(self, n, cid, usbbus):
        write(os.path.join(self.asound, "card%d" % n, "id"), cid + "\n")
        if usbbus:
            write(os.path.join(self.asound, "card%d" % n, "usbbus"), usbbus + "\n")

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


class Finding(unittest.TestCase):
    def setUp(self):
        self.t = Tree()
        self.addCleanup(self.t.close)

    def test_an_uvc_camera_is_found_without_its_metadata_node_and_without_other_video_devices(self):
        self.t.video("video0", "stream_hdmirx", 0, usb=None, driver="rk_hdmirx")
        self.t.video("video2", "Osmo Action 6: USB Camera", 0, usb=("1", "4", "2CA3", "0021"))
        self.t.video("video3", "Osmo Action 6: USB Camera", 1, usb=("1", "4", "2CA3", "0021"))
        cams = H.find_uvc_cameras(self.t.v4l)
        self.assertEqual(len(cams), 1)
        self.assertEqual((cams[0]["node"], cams[0]["bus"], cams[0]["dev"], cams[0]["id"]), ("/dev/video2", "1", "4", "2ca3:0021"))
        self.assertIn("Action 6", cams[0]["name"])

    def test_nothing_connected_or_no_sysfs(self):
        self.assertEqual(H.find_uvc_cameras(self.t.v4l), [])
        self.assertEqual(H.find_uvc_cameras(os.path.join(self.t.root, "gibt-es-nicht")), [])

    def test_the_audio_card_of_the_same_usb_device_is_found(self):
        self.t.video("video2", "Cam", 0, usb=("1", "4", "2CA3", "0021"))
        self.t.card(0, "rockchiphdmiin", None)
        self.t.card(3, "Action6", "001/004")
        self.t.card(4, "Anderes", "001/009")
        cam = H.find_uvc_cameras(self.t.v4l)[0]
        self.assertEqual(H.find_usb_audio(cam, self.t.asound), "plughw:CARD=Action6")

    def test_no_matching_card_or_odd_names_give_no_audio(self):
        self.t.video("video2", "Cam", 0, usb=("1", "4", "2CA3", "0021"))
        self.t.card(4, "Anderes", "001/009")
        cam = H.find_uvc_cameras(self.t.v4l)[0]
        self.assertIsNone(H.find_usb_audio(cam, self.t.asound))
        self.t.card(5, "Boese;Name", "001/004")                                          # ungültiger Kartenname wird nicht benutzt
        self.assertIsNone(H.find_usb_audio(cam, self.t.asound))
        self.assertIsNone(H.find_usb_audio(None, self.t.asound))
        self.assertIsNone(H.find_usb_audio({"bus": "x", "dev": "4"}, self.t.asound))


class Commands(unittest.TestCase):
    CFG = dict(H.DEFAULTS, source="usb", bitrate=6000)

    def test_mjpeg_is_decoded_by_hardware_and_encoded_again(self):
        a = H.usb_feeder_argv(self.CFG, "/dev/video2", ("mjpeg", 1920, 1080, 30), "plughw:CARD=Action6")
        s = " ".join(a)
        self.assertIn("v4l2src device=/dev/video2 ! image/jpeg,width=1920,height=1080,framerate=30/1 ! jpegparse ! mppjpegdec ! queue ! mpph264enc bitrate=6000000 gop=30", s)
        self.assertIn("alsasrc device=plughw:CARD=Action6 ! audioconvert ! audioresample ! audio/x-raw,rate=48000,channels=2 ! voaacenc", s)
        self.assertIn("rtmpsink location=rtmp://127.0.0.1:1935/publish/hdmi", s)
        self.assertEqual(a[:2], ["gst-launch-1.0", "-q"])

    def test_h264_is_passed_through_without_a_new_encode(self):
        s = " ".join(H.usb_feeder_argv(self.CFG, "/dev/video2", ("h264", 1920, 1080, 30), None))
        self.assertIn("video/x-h264,width=1920,height=1080,framerate=30/1 ! h264parse ! queue ! mux.", s)
        self.assertNotIn("mpph264enc", s)

    def test_raw_uses_videoconvert_and_the_hardware_encoder(self):
        s = " ".join(H.usb_feeder_argv(self.CFG, "/dev/video4", ("raw", 1280, 720, 30), None))
        self.assertIn("video/x-raw,width=1280,height=720,framerate=30/1 ! videoconvert ! queue ! mpph264enc", s)

    def test_without_a_sound_card_or_with_sound_off_the_sound_is_silence(self):
        for cfg, audio in ((self.CFG, None), (dict(self.CFG, audio="none"), "plughw:CARD=Action6")):
            s = " ".join(H.usb_feeder_argv(cfg, "/dev/video2", ("mjpeg", 1280, 720, 30), audio))
            self.assertIn("audiotestsrc wave=silence", s)
            self.assertNotIn("alsasrc", s)

    def test_the_probe_stops_before_the_encoder(self):
        s = " ".join(H.usb_probe_argv("/dev/video2", ("mjpeg", 1280, 720, 30)))
        self.assertIn("num-buffers=3", s)
        self.assertTrue(s.endswith("! fakesink"))
        self.assertNotIn("mpph264enc", s)

    def test_bad_values_are_refused(self):
        for dev in ("/dev/video2;rm", "video2", "/dev/hdmirx", "/dev/video", "", None, "/dev/video2 ! fakesink"):
            with self.assertRaises(ValueError):
                H.usb_feeder_argv(self.CFG, dev, ("mjpeg", 1280, 720, 30), None)
            with self.assertRaises(ValueError):
                H.usb_probe_argv(dev, ("mjpeg", 1280, 720, 30))
        for audio in ("hw:CARD=x", "plughw:CARD=a b", "plughw:CARD=x;y", "alsa"):
            with self.assertRaises(ValueError):
                H.usb_feeder_argv(self.CFG, "/dev/video2", ("mjpeg", 1280, 720, 30), audio)
        with self.assertRaises(ValueError):
            H.usb_feeder_argv(self.CFG, "/dev/video2", ("mjpeg", 1280, 720, 30), None, rtmp_app="a b")

    def test_every_argument_is_a_plain_word(self):
        for cand in H.USB_CANDIDATES:
            for part in H.usb_feeder_argv(self.CFG, "/dev/video2", cand, "plughw:CARD=Action6"):
                self.assertNotRegex(part, r"[;&|$`\n\"']")

    def test_the_source_setting_is_checked(self):
        self.assertEqual(H.clean_settings({"source": "usb"})["source"], "usb")
        self.assertEqual(H.clean_settings({})["source"], "hdmi")
        for bad in ("usb2", "", None, 5, ["usb"]):
            with self.assertRaises(ValueError):
                H.clean_settings({"source": bad})

    def test_labels(self):
        self.assertEqual(H.usb_caps(("mjpeg", 1920, 1080, 30))[0], "MJPEG 1920x1080@30")
        self.assertEqual(H.usb_caps(("h264", 1920, 1080, 30))[0], "H.264 1920x1080@30")
        self.assertEqual(H.usb_caps(("raw", 640, 480, 30))[0], "RAW 640x480@30")


class UsbRig:
    """Dienst mit Quelle "usb" und nachgebauter Hardware: /sys, /proc und Probeläufe."""
    def __init__(self, with_camera=True, probe_ok=("mjpeg", 1280, 720, 30), card=True):
        self.tree = Tree()
        self.tmp = tempfile.mkdtemp()
        if with_camera:
            self.plug(card)
        self.probes, self.spawned, self.t, self.pub = [], [], [100.0], True
        self.probe_ok = probe_ok
        self.d = H.Daemon(self.tmp, device=os.path.join(self.tmp, "keine-hdmi"), spawn=self._spawn, publishing=lambda key: self.pub, clock=lambda: self.t[0],
                          sysfs=self.tree.v4l, asound=self.tree.asound, probe=self._probe)
        self.d.cfg.update(enabled=True, source="usb")

    def plug(self, card=True):
        self.tree.video("video2", "Osmo Action 6: USB Camera", 0, usb=("1", "4", "2CA3", "0021"))
        self.tree.video("video3", "Osmo Action 6: USB Camera", 1, usb=("1", "4", "2CA3", "0021"))
        if card:
            self.tree.card(3, "Action6", "001/004")

    def unplug(self):
        shutil.rmtree(self.tree.v4l)
        os.makedirs(self.tree.v4l)

    def _probe(self, argv):
        self.probes.append(argv)
        caps = next(p for p in argv if p.startswith(("image/jpeg", "video/x-h264", "video/x-raw")))
        for cand in ([self.probe_ok] if self.probe_ok else []):
            if H.usb_caps(cand)[1] == caps:
                return True
        return False

    async def _spawn(self, argv):
        p = T.FakeProc([])
        self.spawned.append((argv, p))
        return p

    def advance(self, s):
        self.t[0] += s


class Supervision(unittest.IsolatedAsyncioTestCase):
    async def test_without_a_camera_it_waits_and_says_so(self):
        r = UsbRig(with_camera=False)
        await r.d.tick()
        self.assertEqual((r.d.state, r.d.message, r.spawned), ("waiting", "Keine USB-Kamera angeschlossen", []))
        self.assertTrue(r.d.available)                                                 # nicht "diese Box hat keinen HDMI-Eingang"
        self.assertFalse(r.d.status()["usb"]["present"])

    async def test_with_a_camera_it_probes_the_formats_in_order_and_starts_the_first_that_works(self):
        r = UsbRig(probe_ok=("mjpeg", 1280, 720, 30))
        await r.d.tick()
        self.assertEqual(len(r.probes), 2)                                             # MJPEG 1080p scheiterte, MJPEG 720p ging
        self.assertEqual(r.d.state, "starting")
        argv, _ = r.spawned[0]
        self.assertIn("image/jpeg,width=1280,height=720,framerate=30/1", argv)
        self.assertIn("device=plughw:CARD=Action6", argv)
        st = r.d.status()
        self.assertEqual((st["usb"]["present"], st["usb"]["format"], st["usb"]["audio"]), (True, "MJPEG 1280x720@30", "plughw:CARD=Action6"))
        self.assertEqual(st["settings"]["source"], "usb")

    async def test_the_probe_result_is_remembered_for_the_same_device(self):
        r = UsbRig()
        await r.d.tick()
        r.proc = r.spawned[-1][1]
        n = len(r.probes)
        r.spawned[-1][1].returncode = 1
        r.advance(1)
        await r.d.tick()
        r.advance(5)
        await r.d.tick()                                                               # neuer Start nach dem Fehler
        self.assertEqual(len(r.spawned), 2)
        self.assertEqual(len(r.probes), n)                                             # nicht noch einmal alles durchprobiert

    async def test_a_camera_without_any_usable_format_gives_a_clear_error(self):
        r = UsbRig(probe_ok=None)
        await r.d.tick()
        self.assertEqual(r.d.state, "error")
        self.assertIn("kein Bild", r.d.message)
        self.assertIn("Webcam-Modus", r.d.message)
        self.assertEqual(len(r.probes), len(H.USB_CANDIDATES))
        self.assertEqual(r.spawned, [])

    async def test_no_sound_card_means_silence_but_the_picture_runs(self):
        r = UsbRig(card=False)
        await r.d.tick()
        self.assertIn("audiotestsrc", r.spawned[0][0])
        self.assertEqual(r.d.status()["usb"]["audio"], "")

    async def test_unplugging_stops_the_feeder_and_plugging_in_again_restarts_it(self):
        r = UsbRig()
        await r.d.tick()
        r.unplug()
        r.advance(1)
        await r.d.tick()
        self.assertEqual((r.d.state, r.d.message), ("waiting", "Keine USB-Kamera angeschlossen"))
        self.assertTrue(r.spawned[0][1].terminated)
        r.plug()
        r.advance(1)
        await r.d.tick()
        self.assertEqual((r.d.state, len(r.spawned)), ("starting", 2))

    async def test_a_stream_that_shows_up_is_streaming(self):
        r = UsbRig()
        await r.d.tick()
        r.advance(6)
        await r.d.tick()
        self.assertEqual((r.d.state, r.d.published), ("streaming", True))

    async def test_switching_back_to_hdmi_uses_the_hdmi_path(self):
        r = UsbRig()
        await r.d.tick()
        r.d.cfg["source"] = "hdmi"
        r.advance(1)
        await r.d.tick()
        self.assertEqual(r.d.state, "unavailable")                                     # hier gibt es keinen HDMI-Eingang (Gerätedatei fehlt)
        self.assertTrue(r.spawned[0][1].terminated)

    def test_friendly_errors_for_usb(self):
        self.assertIn("USB-Kamera", H.friendly_error(["Could not open device /dev/video2: Device or resource busy"], usb=True))
        self.assertEqual(H.friendly_error(["VIDIOC_STREAMON failed"]), "Kein HDMI-Signal")                  # HDMI bleibt, wie es war


class ServerSide(unittest.TestCase):
    def service(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        cams = server.CameraStore(os.path.join(d, "cameras.json"), "publish", "", True)
        return server.HdmiService(d, cams, demo=True), cams

    def test_the_page_may_change_the_source_and_the_camera_gets_a_usb_name(self):
        svc, cams = self.service()
        self.assertEqual(svc.status()["settings"]["source"], "hdmi")
        st = svc.set({"source": "usb", "enabled": True})
        self.assertEqual(st["settings"]["source"], "usb")
        self.assertTrue(st["usb"]["present"])
        self.assertEqual([c["name"] for c in cams.cams], ["USB-Kamera"])
        self.assertEqual(cams.cams[0]["key"], "hdmi")                                  # der Schlüssel bleibt, alles andere der Box kennt ihn

    def test_hdmi_keeps_its_old_name(self):
        svc, cams = self.service()
        svc.set({"enabled": True})
        self.assertEqual([c["name"] for c in cams.cams], ["HDMI"])

    def test_a_bad_source_is_refused_before_it_reaches_the_service(self):
        svc, _ = self.service()
        for bad in ("usb;x", "", None, 3):
            with self.assertRaises(ValueError):
                svc.set({"source": bad})

    def test_the_source_travels_with_the_settings_backup(self):
        self.assertIn("source", server.HdmiService.ALLOWED)

    def test_a_missing_usb_camera_is_reported_as_such_and_not_as_hdmi_without_signal(self):
        w = server.OutageWatch(clock=lambda: 1000.0)
        hdmi = {"service": True, "available": True, "state": "waiting", "signal_known": True, "signal": {"plugged": False}, "settings": {"enabled": True, "source": "usb"}}
        w.alerts([], hdmi)
        w.clock = lambda: 1000.0 + server.OutageWatch.DOWN + 1
        al = w.alerts([], hdmi)
        self.assertEqual([(a["kind"], a["src"]) for a in al], [("hdmi", "usb")])
        w2 = server.OutageWatch(clock=lambda: 1000.0)
        w2.alerts([], dict(hdmi, settings={"enabled": True, "source": "hdmi"}))
        w2.clock = lambda: 1000.0 + server.OutageWatch.DOWN + 1
        self.assertEqual(w2.alerts([], dict(hdmi, settings={"enabled": True, "source": "hdmi"}))[0]["src"], "hdmi")


if __name__ == "__main__":
    unittest.main()
