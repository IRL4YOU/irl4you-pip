#!/usr/bin/env python3
import argparse
import asyncio
import collections
import hmac
import json
import logging
import os
import pwd
import re
import secrets
import signal
import subprocess
import time
import urllib.request

LISTEN_HOST, LISTEN_PORT = "127.0.0.1", 9102
RTMP_PORT = 1935
RTMP_APP = "publish"
STAT_URL = "http://127.0.0.1:1936/"
HDMI_DEVICE = "/dev/hdmirx"
HDMI_STATUS = "/sys/kernel/debug/hdmirx/status"
HDMI_AUDIO = "hw:CARD=rockchiphdmiin"
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
DEFAULTS = {"enabled": False, "key": "hdmi", "bitrate": 8000, "fps": 30, "audio": "hdmi", "source": "hdmi", "usb_format": "auto"}
SOURCE_CHOICES = ("hdmi", "usb")
USB_FORMAT_CHOICES = ("auto", "mjpeg", "h264")
SYS_V4L = "/sys/class/video4linux"
PROC_ASOUND = "/proc/asound"
SYS_USB = "/sys/bus/usb/devices"
DJI_VENDOR = "2ca3"
POWER_WINDOW = 180.0
POWER_STAY = 10.0
USB_POWER_TEXT = "Die Kamera meldet sich nur kurz am USB und trennt sich wieder, meist fehlt ihr Strom. Bitte einen USB-Hub mit eigenem Netzteil verwenden."
PROBE_TIMEOUT = 8.0
USB_CANDIDATES = (("mjpeg", 1920, 1080, 30), ("mjpeg", 1280, 720, 30), ("h264", 1920, 1080, 30), ("h264", 1280, 720, 30), ("raw", 1280, 720, 30), ("raw", 640, 480, 30))
USB_BAD_AFTER = 3
PROBE_RETRY_PAUSE = 1.0
BITRATE_RANGE = (1000, 20000)
FPS_CHOICES = (25, 30)
AUDIO_CHOICES = ("hdmi", "none")
NICE = 10
GRACE = 15.0
STALL = 12.0
BACKOFF = (2.0, 4.0, 8.0, 15.0, 30.0)
STABLE = 30.0
TICK = 1.0

log = logging.getLogger("pipbox-hdmi")


def parse_hdmirx_status(text):
    out = {"plugged": False, "locked": False, "width": 0, "height": 0, "fps": 0.0, "interlaced": False, "format": "", "depth": 0}
    if not isinstance(text, str):
        return out
    m = re.search(r"^status:\s*(\S+)", text, re.M)
    out["plugged"] = bool(m and m.group(1).lower() == "plugin")
    lock = re.findall(r"(?:Clk-Ch|Ch\d)\s*:\s*(\w+)", text)
    lock = [x for x in lock if x.lower() in ("lock", "unlock", "nolock")]
    m = re.search(r"^Timing:\s*(\d+)x(\d+)([pi])(\d+(?:\.\d+)?)", text, re.M)
    if m:
        out["width"], out["height"] = int(m.group(1)), int(m.group(2))
        out["interlaced"] = m.group(3) == "i"
        out["fps"] = float(m.group(4))
    m = re.search(r"^Color Format:\s*(\S+)", text, re.M)
    out["format"] = m.group(1) if m else ""
    m = re.search(r"^Color Depth:\s*(\d+)", text, re.M)
    out["depth"] = int(m.group(1)) if m else 0
    out["locked"] = bool(out["plugged"] and lock and all(x.lower() == "lock" for x in lock) and out["width"] and out["height"])
    return out


def signal_id(sig):
    return (sig.get("width"), sig.get("height"), round(float(sig.get("fps") or 0)), bool(sig.get("interlaced")), sig.get("device"))


def clean_settings(d, current=None):
    cur = dict(DEFAULTS)
    cur.update(current or {})
    if not isinstance(d, dict):
        raise ValueError("Ungültige Anfrage")
    out = dict(cur)
    if "enabled" in d:
        if not isinstance(d["enabled"], bool):
            raise ValueError("Einschalten: ja oder nein")
        out["enabled"] = d["enabled"]
    if "key" in d:
        k = d["key"]
        if not isinstance(k, str) or not KEY_RE.match(k) or k.startswith("test-"):
            raise ValueError("Schlüssel: nur a-z, 0-9, - und _, höchstens 32 Zeichen, nicht mit test- beginnen")
        out["key"] = k
    if "bitrate" in d:
        b = d["bitrate"]
        if isinstance(b, bool) or not isinstance(b, int) or not BITRATE_RANGE[0] <= b <= BITRATE_RANGE[1]:
            raise ValueError("Bitrate: %d bis %d kbit/s" % BITRATE_RANGE)
        out["bitrate"] = b
    if "fps" in d:
        if isinstance(d["fps"], bool) or d["fps"] not in FPS_CHOICES:
            raise ValueError("Bildrate: 25 oder 30")
        out["fps"] = d["fps"]
    if "audio" in d:
        if d["audio"] not in AUDIO_CHOICES:
            raise ValueError("Ton: HDMI-Ton oder ohne Ton")
        out["audio"] = d["audio"]
    if "source" in d:
        if d["source"] not in SOURCE_CHOICES:
            raise ValueError("Quelle: HDMI-Eingang oder USB-Webcam")
        out["source"] = d["source"]
    if "usb_format" in d:
        if d["usb_format"] not in USB_FORMAT_CHOICES:
            raise ValueError("Bildformat: Automatisch, MJPEG oder H.264")
        out["usb_format"] = d["usb_format"]
    return out


ALSA_CLOCK = ["provide-clock=false", "slave-method=resample"]


def feeder_argv(cfg, rtmp_port=RTMP_PORT, rtmp_app=RTMP_APP, device=HDMI_DEVICE, audio_device=HDMI_AUDIO):
    cfg = clean_settings(cfg)
    if not (isinstance(device, str) and re.match(r"^/[A-Za-z0-9_./-]{1,120}$", device)) or "/../" in device + "/":
        raise ValueError("Gerät ungültig")
    if not (isinstance(audio_device, str) and re.match(r"^hw:CARD=[A-Za-z0-9_]{1,40}$", audio_device)):
        raise ValueError("Tongerät ungültig")
    if not (isinstance(rtmp_app, str) and re.match(r"^[a-z0-9_]{1,20}$", rtmp_app)) or not isinstance(rtmp_port, int):
        raise ValueError("RTMP-Ziel ungültig")
    v = ["v4l2src", "device=" + device, "!", "videorate", "!", "video/x-raw,framerate=%d/1" % cfg["fps"], "!",
         "mpph264enc", "bitrate=%d" % (cfg["bitrate"] * 1000), "gop=%d" % cfg["fps"], "!",
         "h264parse", "config-interval=-1", "!", "queue", "!", "mux."]
    if cfg["audio"] == "hdmi":
        a = ["alsasrc", "device=" + audio_device] + ALSA_CLOCK + ["!", "audioconvert", "!", "voaacenc", "bitrate=128000", "!", "aacparse", "!", "queue", "!", "mux."]
    else:
        a = ["audiotestsrc", "wave=silence", "is-live=true", "!", "audio/x-raw,rate=48000,channels=2", "!",
             "voaacenc", "bitrate=128000", "!", "aacparse", "!", "queue", "!", "mux."]
    sink = ["flvmux", "name=mux", "streamable=true", "!", "rtmpsink", "location=rtmp://127.0.0.1:%d/%s/%s" % (rtmp_port, rtmp_app, cfg["key"])]
    return ["gst-launch-1.0", "-q"] + v + a + sink


def _read(path, limit=200):
    try:
        with open(path, errors="replace") as f:
            return f.read(limit).strip()
    except OSError:
        return ""


def find_uvc_cameras(sysfs=SYS_V4L):
    out = []
    try:
        names = sorted(os.listdir(sysfs), key=lambda n: (len(n), n))
    except OSError:
        return out
    for n in names:
        if not re.match(r"^video\d{1,3}$", n):
            continue
        base = os.path.join(sysfs, n)
        if _read(os.path.join(base, "index")) not in ("", "0"):
            continue
        iface = os.path.realpath(os.path.join(base, "device"))
        if os.path.basename(os.path.realpath(os.path.join(iface, "driver"))) != "uvcvideo":
            continue
        usb = os.path.dirname(iface)
        out.append({"node": "/dev/" + n, "name": _read(os.path.join(base, "name"), 80) or "USB-Kamera", "bus": _read(os.path.join(usb, "busnum"), 8),
                    "dev": _read(os.path.join(usb, "devnum"), 8),
                    "id": (_read(os.path.join(usb, "idVendor"), 8) + ":" + _read(os.path.join(usb, "idProduct"), 8)).lower()})
    return out


def find_dji_devices(sysusb=SYS_USB):
    out = []
    try:
        names = sorted(os.listdir(sysusb))
    except OSError:
        return out
    for n in names:
        base = os.path.join(sysusb, n)
        if _read(os.path.join(base, "idVendor"), 8).lower() != DJI_VENDOR:
            continue
        out.append({"dev": n, "num": _read(os.path.join(base, "devnum"), 8), "id": (DJI_VENDOR + ":" + _read(os.path.join(base, "idProduct"), 8)).lower()})
    return out


def find_usb_audio(cam, asound=PROC_ASOUND):
    if not (cam and cam.get("bus") and cam.get("dev")):
        return None
    try:
        cards = sorted(os.listdir(asound))
    except OSError:
        return None
    want = "%03d/%03d" % (int(cam["bus"]), int(cam["dev"])) if str(cam["bus"]).isdigit() and str(cam["dev"]).isdigit() else None
    for c in cards:
        if not re.match(r"^card\d{1,2}$", c):
            continue
        if want and _read(os.path.join(asound, c, "usbbus"), 16) == want:
            cid = _read(os.path.join(asound, c, "id"), 40)
            if re.match(r"^[A-Za-z0-9_]{1,40}$", cid):
                return "plughw:CARD=" + cid
    return None


def usb_candidates(fmt):
    if fmt in ("mjpeg", "h264"):
        return tuple(sorted(USB_CANDIDATES, key=lambda c: c[0] != fmt))
    return USB_CANDIDATES


def usb_caps(cand):
    kind, w, h, f = cand
    media = {"mjpeg": "image/jpeg", "h264": "video/x-h264", "raw": "video/x-raw"}[kind]
    label = {"mjpeg": "MJPEG", "h264": "H.264", "raw": "RAW"}[kind]
    return "%s %dx%d@%d" % (label, w, h, f), "%s,width=%d,height=%d,framerate=%d/1" % (media, w, h, f)


PADDED_H264 = ("action6",)


def usb_h264_padded(cand, name=""):
    n = (name or "").lower().replace(" ", "").replace("_", "")
    return cand[0] == "h264" and cand[2] % 16 != 0 and any(k in n for k in PADDED_H264)


def _usb_decode(kind):
    if kind == "mjpeg":
        return ["jpegparse", "!", "mppjpegdec"]
    if kind == "h264":
        return ["h264parse", "config-interval=-1"]
    return ["videoconvert"]


def usb_probe_argv(device, cand):
    if not re.match(r"^/dev/video\d{1,3}$", device or ""):
        raise ValueError("Gerät ungültig")
    return ["gst-launch-1.0", "-q", "v4l2src", "device=" + device, "num-buffers=3", "!", usb_caps(cand)[1], "!"] + _usb_decode(cand[0]) + ["!", "fakesink"]


def usb_feeder_argv(cfg, device, cand, audio_device=None, rtmp_port=RTMP_PORT, rtmp_app=RTMP_APP, name=""):
    cfg = clean_settings(cfg)
    if not re.match(r"^/dev/video\d{1,3}$", device or ""):
        raise ValueError("Gerät ungültig")
    if audio_device is not None and not re.match(r"^plughw:CARD=[A-Za-z0-9_]{1,40}$", audio_device):
        raise ValueError("Tongerät ungültig")
    if not (isinstance(rtmp_app, str) and re.match(r"^[a-z0-9_]{1,20}$", rtmp_app)) or not isinstance(rtmp_port, int):
        raise ValueError("RTMP-Ziel ungültig")
    kind = cand[0]
    fps = cand[3]
    v = ["v4l2src", "device=" + device, "!", usb_caps(cand)[1], "!"] + _usb_decode(kind) + ["!"]
    if kind == "h264" and usb_h264_padded(cand, name):
        v += ["queue", "!", "mppvideodec", "crop-rectangle=<0,0,%d,%d>" % (cand[1], cand[2]), "height=%d" % cand[2], "!", "queue", "!",
              "mpph264enc", "bitrate=%d" % (cfg["bitrate"] * 1000), "gop=%d" % fps, "!", "h264parse", "config-interval=-1", "!", "queue", "!", "mux."]
    elif kind == "h264":
        v += ["queue", "!", "mux."]
    else:
        v += ["queue", "!", "mpph264enc", "bitrate=%d" % (cfg["bitrate"] * 1000), "gop=%d" % fps, "!", "h264parse", "config-interval=-1", "!", "queue", "!", "mux."]
    if cfg["audio"] == "hdmi" and audio_device:
        a = ["alsasrc", "device=" + audio_device] + ALSA_CLOCK + ["!", "audioconvert", "!", "audioresample", "!", "audio/x-raw,rate=48000,channels=2", "!",
             "voaacenc", "bitrate=128000", "!", "aacparse", "!", "queue", "!", "mux."]
    else:
        a = ["audiotestsrc", "wave=silence", "is-live=true", "!", "audio/x-raw,rate=48000,channels=2", "!",
             "voaacenc", "bitrate=128000", "!", "aacparse", "!", "queue", "!", "mux."]
    sink = ["flvmux", "name=mux", "streamable=true", "!", "rtmpsink", "location=rtmp://127.0.0.1:%d/%s/%s" % (rtmp_port, rtmp_app, cfg["key"])]
    return ["gst-launch-1.0", "-q"] + v + a + sink


def rtmp_publishing(key, stat_url=None):
    try:
        with urllib.request.urlopen(stat_url or STAT_URL, timeout=2) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    m = re.search(r"<stream>\s*<name>\s*%s\s*</name>[\s\S]*?</stream>" % re.escape(key), body)
    return bool(m and "<publishing/>" in m.group(0))


def friendly_error(lines, usb=False):
    text = " ".join(lines)[-600:]
    low = text.lower()
    if usb and ("no such device" in low or "cannot identify device" in low or "could not open" in low or "device busy" in low or "streamon" in low):
        return "Die USB-Kamera ist nicht erreichbar oder wird schon benutzt"
    if "busy" in low and ("alsa" in low or "audio" in low or "snd" in low):
        return "Das Tongerät ist belegt"
    if "no such device" in low or "cannot identify device" in low or "could not open" in low and "hdmirx" in low:
        return "HDMI-Gerät nicht gefunden"
    if "timings invalid" in low or "not lock" in low or "streamon" in low:
        return "Kein HDMI-Signal"
    if "rtmp" in low and ("connect" in low or "could not" in low or "failed" in low):
        return "Keine Verbindung zum RTMP-Eingang der Box"
    if "mpp" in low and ("fail" in low or "error" in low):
        return "Der Hardware-Kodierer meldet einen Fehler"
    return "Die Einspeisung wurde beendet" + (": " + re.sub(r"\s+", " ", lines[-1])[:120] if lines else "")


class Daemon:
    def __init__(self, state_dir, rtmp_port=RTMP_PORT, rtmp_app=RTMP_APP, stat_url=STAT_URL, device=HDMI_DEVICE,
                 status_file=HDMI_STATUS, audio_device=HDMI_AUDIO, read_status=None, spawn=None, publishing=None, clock=time.monotonic,
                 sysfs=SYS_V4L, asound=PROC_ASOUND, probe=None, sysusb=SYS_USB):
        self.state_dir = state_dir
        self.config_file = os.path.join(state_dir, "hdmi.json")
        self.token_path = os.path.join(state_dir, "hdmi-token")
        self.rtmp_port, self.rtmp_app, self.stat_url = rtmp_port, rtmp_app, stat_url
        self.device, self.status_file, self.audio_device = device, status_file, audio_device
        self._read_status = read_status or self._read_status_file
        self._spawn = spawn or self._spawn_process
        self._publishing = publishing or (lambda key: rtmp_publishing(key, self.stat_url))
        self.clock = clock
        self.sysfs, self.asound, self.sysusb = sysfs, asound, sysusb
        self.dji_seen = {}
        self._probe_run = probe or self._probe_process
        self.usb = {"present": False, "name": "", "node": "", "format": "", "audio": "", "power": False}
        self.usb_choice = None
        self.usb_bad = set()
        self.usb_cur = None
        self.usb_quick_fails = 0
        self.token = self._load_token()
        self.cfg = self.load()
        self.state, self.message = "off", ""
        self.signal = parse_hdmirx_status("")
        self.signal_known = False
        self.proc = None
        self.tail = collections.deque(maxlen=12)
        self.started = 0.0
        self.sig_at_start = None
        self.fails = 0
        self.next_try = 0.0
        self.published = None
        self.missing_since = None
        self.restarts = 0
        self.closing = False
        self._reader = None

    def _load_token(self):
        try:
            with open(self.token_path) as f:
                t = f.read().strip()
            if len(t) >= 32:
                return t
        except OSError:
            pass
        t = secrets.token_urlsafe(32)
        fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(t + "\n")
        try:
            pw = pwd.getpwnam("pipbox")
            os.chown(self.token_path, pw.pw_uid, pw.pw_gid)
        except (KeyError, PermissionError):
            pass
        return t

    def load(self):
        try:
            with open(self.config_file) as f:
                saved = json.load(f)
            return clean_settings(saved)
        except (OSError, ValueError):
            return dict(DEFAULTS)

    def save(self):
        tmp = self.config_file + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.cfg, f, indent=1)
        os.replace(tmp, self.config_file)

    @property
    def available(self):
        if self.cfg.get("source") == "usb":
            return True
        return os.path.exists(self.device)

    @staticmethod
    def _probe_process(argv):
        try:
            return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=PROBE_TIMEOUT).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _usb_pick(self):
        cams = find_uvc_cameras(self.sysfs)
        return cams[0] if cams else None

    def _usb_power_problem(self, cam):
        now = self.clock()
        if cam:
            self.dji_seen.clear()
            return False
        here = set()
        for d in find_dji_devices(self.sysusb):
            here.add(d["num"])
            first, _ = self.dji_seen.get(d["num"], (now, now))
            self.dji_seen[d["num"]] = (first, now)
        for num in [n for n, (_, last) in self.dji_seen.items() if now - last > POWER_WINDOW]:
            del self.dji_seen[num]
        stays = any(now - first >= POWER_STAY for num, (first, _) in self.dji_seen.items() if num in here)
        return len(self.dji_seen) >= 2 or stays

    def _usb_waiting_message(self):
        return USB_POWER_TEXT if self.usb.get("power") else "Keine USB-Kamera angeschlossen"

    def _usb_choose_format(self, cam):
        if self.usb_choice and self.usb_choice[0] == cam["node"]:
            return self.usb_choice[1]
        for cand in usb_candidates(self.cfg.get("usb_format")):
            if (cam["node"], cand) in self.usb_bad:
                continue
            argv = usb_probe_argv(cam["node"], cand)
            if self._probe_run(argv) or (self._pause(PROBE_RETRY_PAUSE) or self._probe_run(argv)):
                self.usb_choice = (cam["node"], cand)
                return cand
        self.usb_choice = None
        return None

    @staticmethod
    def _pause(seconds):
        time.sleep(seconds)

    def _usb_note_failure(self):
        if self.cfg.get("source") != "usb" or not self.usb_cur or self.clock() - self.started >= STABLE:
            self.usb_quick_fails = 0
            return
        self.usb_quick_fails += 1
        if self.usb_quick_fails >= USB_BAD_AFTER:
            self.usb_bad.add(self.usb_cur)
            if self.usb_choice == self.usb_cur:
                self.usb_choice = None
            self.usb_quick_fails = 0
            log.warning("Das Bildformat %s läuft nicht stabil, die Box nimmt das nächste", usb_caps(self.usb_cur[1])[0])

    def _read_status_file(self):
        with open(self.status_file) as f:
            return f.read(4096)

    async def read_signal(self):
        if self.cfg.get("source") == "usb":
            cam = self._usb_pick()
            self.signal_known = True
            sig = parse_hdmirx_status("")
            if cam:
                f = self.usb_choice[1] if self.usb_choice and self.usb_choice[0] == cam["node"] else None
                sig.update(plugged=True, locked=True, width=f[1] if f else 0, height=f[2] if f else 0, fps=float(f[3]) if f else 0.0, device=cam["node"])
            self.usb = dict(self.usb, power=self._usb_power_problem(cam), present=bool(cam), name=cam["name"] if cam else "", node=cam["node"] if cam else "",
                            format=usb_caps(self.usb_choice[1])[0] if cam and self.usb_choice and self.usb_choice[0] == cam["node"] else "")
            return sig
        loop = asyncio.get_event_loop()
        try:
            text = await loop.run_in_executor(None, self._read_status)
        except Exception:
            self.signal_known = False
            return parse_hdmirx_status("")
        self.signal_known = True
        return parse_hdmirx_status(text)

    async def _spawn_process(self, argv):
        return await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            preexec_fn=lambda: os.nice(NICE))

    async def _drain(self, proc):
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                self.tail.append(line.decode("utf-8", "replace").strip())
        except Exception:
            pass

    async def _usb_argv(self):
        cam = self._usb_pick()
        if cam is None:
            return None, None
        loop = asyncio.get_event_loop()
        cand = await loop.run_in_executor(None, self._usb_choose_format, cam)
        if cand is None:
            self._failed("Die Kamera liefert kein Bild. Sie muss eingeschaltet und im Webcam-Modus sein (an der Kamera: USB-Modus „Webcam“)")
            return None, None
        audio = find_usb_audio(cam, self.asound) if self.cfg["audio"] == "hdmi" else None
        self.usb = dict(self.usb, present=True, name=cam["name"], node=cam["node"], format=usb_caps(cand)[0], audio=(audio or ""))
        return usb_feeder_argv(self.cfg, cam["node"], cand, audio, self.rtmp_port, self.rtmp_app, cam.get("name", "")), cand

    async def _start(self, sig):
        if self.cfg.get("source") == "usb":
            argv, cand = await self._usb_argv()
            if argv is None:
                return
            self.usb_cur = (self.usb["node"], cand)
            sig = dict(sig, width=cand[1], height=cand[2], fps=float(cand[3]), device=self.usb["node"])
        else:
            argv = feeder_argv(self.cfg, self.rtmp_port, self.rtmp_app, self.device, self.audio_device)
        self.tail.clear()
        try:
            self.proc = await self._spawn(argv)
        except Exception as e:
            self.proc = None
            self._failed("Start nicht möglich: %s" % (str(e) or e.__class__.__name__))
            return
        self.started = self.clock()
        self.sig_at_start = signal_id(sig)
        self.published, self.missing_since = None, None
        self.state, self.message = "starting", ""
        if getattr(self.proc, "stderr", None) is not None:
            self._reader = asyncio.ensure_future(self._drain(self.proc))
        log.info("Einspeisung gestartet (%s, %dx%d@%s, %d kbit/s, Schlüssel %s)", self.cfg.get("source", "hdmi"), sig.get("width") or 0, sig.get("height") or 0,
                 sig.get("fps") or "?", self.cfg["bitrate"], self.cfg["key"])

    async def _stop(self):
        proc, self.proc = self.proc, None
        if proc is None:
            return
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), 3)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), 3)
                except Exception:
                    pass
            except ProcessLookupError:
                pass
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
        log.info("Einspeisung beendet")

    def _failed(self, message):
        self.fails += 1
        wait = BACKOFF[min(self.fails - 1, len(BACKOFF) - 1)]
        self.next_try = self.clock() + wait
        self.state, self.message = "error", message
        log.warning("%s (nächster Versuch in %d s)", message, wait)

    async def tick(self):
        if self.closing:
            return
        if not self.available:
            await self._stop()
            self.state, self.message = "unavailable", "Diese Box hat keinen HDMI-Eingang"
            return
        sig = await self.read_signal()
        self.signal = sig
        running = self.proc is not None and self.proc.returncode is None
        if self.proc is not None and not running:
            rc = self.proc.returncode
            if self._reader is not None:
                try:
                    await asyncio.wait_for(self._reader, 1)
                except Exception:
                    pass
            self.proc, self._reader = None, None
            if self.clock() - self.started >= STABLE:
                self.fails = 0
            self._usb_note_failure()
            self._failed(friendly_error(list(self.tail), self.cfg.get("source") == "usb") if rc else "Die Einspeisung wurde beendet")
            self.restarts += 1
            return
        if not self.cfg["enabled"]:
            if running:
                await self._stop()
            self.state, self.message, self.fails = "off", "", 0
            return
        if self.signal_known and not sig["locked"]:
            if running:
                await self._stop()
            self.state, self.message, self.fails = "waiting", (self._usb_waiting_message() if self.cfg.get("source") == "usb" else "Kein HDMI-Signal"), 0
            return
        if running:
            if self.signal_known and signal_id(sig) != self.sig_at_start:
                log.info("Bildformat hat gewechselt, die Einspeisung startet neu")
                await self._stop()
                self.next_try = self.clock()
                self.state = "starting"
                return
            await self._watch_stream()
            return
        if self.clock() < self.next_try:
            return
        await self._start(sig)

    async def _watch_stream(self):
        now = self.clock()
        loop = asyncio.get_event_loop()
        pub = await loop.run_in_executor(None, self._publishing, self.cfg["key"])
        self.published = pub
        age = now - self.started
        if pub is None:
            if age >= 5:
                self.state, self.message = "streaming", ""
            return
        if pub:
            self.missing_since = None
            self.state, self.message = "streaming", ""
            if age >= STABLE:
                self.fails = 0
            return
        if age < GRACE:
            self.state = "starting"
            return
        if self.missing_since is None:
            self.missing_since = now
        if now - self.missing_since >= STALL:
            log.warning("Der Stream erscheint nicht im RTMP-Eingang, die Einspeisung startet neu")
            await self._stop()
            self.restarts += 1
            self._failed("Der Stream kommt nicht in der Box an")

    def status(self):
        s = {"ok": True, "available": self.available, "state": self.state, "message": self.message, "settings": dict(self.cfg), "usb": dict(self.usb),
             "signal": {k: self.signal[k] for k in ("plugged", "locked", "width", "height", "fps", "interlaced", "format", "depth")},
             "signal_known": self.signal_known, "publishing": self.published, "restarts": self.restarts}
        return s

    async def handle(self, req):
        cmd = req.get("cmd")
        if cmd == "status":
            return self.status()
        if cmd == "set":
            new = clean_settings(req.get("settings"), self.cfg)
            changed = new != self.cfg
            if new.get("usb_format") != self.cfg.get("usb_format"):
                self.usb_choice, self.usb_bad, self.usb_quick_fails = None, set(), 0
            self.cfg = new
            self.save()
            if changed and self.proc is not None:
                await self._stop()
                self.next_try = self.clock()
            if changed:
                self.fails = 0
            return self.status()
        if cmd == "restart":
            await self._stop()
            self.fails, self.next_try = 0, self.clock()
            return self.status()
        return {"error": "Unbekannter Befehl"}

    async def client(self, reader, writer):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                req = None
                try:
                    req = json.loads(line)
                    if not isinstance(req, dict):
                        raise ValueError("Ungültige Anfrage")
                    if not hmac.compare_digest(str(req.get("token", "")), self.token):
                        resp = {"error": "kein Zugriff"}
                    else:
                        resp = await self.handle(req)
                except Exception as e:
                    resp = {"error": str(e) or "Ungültige Anfrage"}
                resp["reply_to"] = req.get("id") if isinstance(req, dict) else None
                writer.write((json.dumps(resp) + "\n").encode())
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    async def supervise(self):
        while not self.closing:
            try:
                await self.tick()
            except Exception:
                log.exception("Fehler in der Überwachung")
            await asyncio.sleep(TICK)

    async def shutdown(self):
        self.closing = True
        await self._stop()

    async def main(self, host=LISTEN_HOST, port=LISTEN_PORT):
        server = await asyncio.start_server(self.client, host, port)
        log.info("bereit auf %s:%d, Quelle: %s, HDMI-Eingang: %s", host, port, self.cfg.get("source", "hdmi"), "vorhanden" if os.path.exists(self.device) else "fehlt")
        asyncio.ensure_future(self.supervise())
        async with server:
            await server.serve_forever()


async def amain(args):
    daemon = Daemon(args.state, args.rtmp_port, args.rtmp_app, args.stat_url, args.device, args.status_file, args.audio_device)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError, ValueError):
            pass
    main_task = asyncio.ensure_future(daemon.main(LISTEN_HOST, args.port))
    stopper = asyncio.ensure_future(stop.wait())
    await asyncio.wait({main_task, stopper}, return_when=asyncio.FIRST_COMPLETED)
    if stop.is_set():
        log.info("wird beendet")
        try:
            await asyncio.wait_for(daemon.shutdown(), 10)
        except Exception as e:
            log.info("Beenden unvollständig: %s", e)
        logging.shutdown()
        os._exit(0)
    stopper.cancel()
    main_task.cancel()
    if not main_task.cancelled() and main_task.done() and main_task.exception():
        raise main_task.exception()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="/var/lib/pipbox")
    ap.add_argument("--port", type=int, default=LISTEN_PORT)
    ap.add_argument("--rtmp-port", type=int, default=RTMP_PORT)
    ap.add_argument("--rtmp-app", default=RTMP_APP)
    ap.add_argument("--stat-url", default=STAT_URL)
    ap.add_argument("--device", default=HDMI_DEVICE)
    ap.add_argument("--status-file", default=HDMI_STATUS)
    ap.add_argument("--audio-device", default=HDMI_AUDIO)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
