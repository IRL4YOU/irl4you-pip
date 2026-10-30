
import os
import shutil


PIP_CORNERS = ("oben links", "oben rechts", "unten links", "unten rechts", "unten Mitte", "frei (verschiebbar)")


FREE = 5


PLUGIN_SO = "/opt/pipbox/gst/libgstpbpip.so"


SEND_STATUS = "/run/pipbox-send/status.json"


VIEW_FILE = "main-view"


VIEW_STATE = "/run/pipbox-send/view-state"


SWAP_SELECT = "main-select"


SWAP_STATE = "/run/pipbox-send/swap-state"


_CENTER = {"mtime": None, "ok": True, "free": True}


def pip_corners():
    try:
        mt = os.stat(PLUGIN_SO).st_mtime
        if _CENTER["mtime"] != mt:
            with open(PLUGIN_SO, "rb") as f:
                data = f.read()
            _CENTER.update(mtime=mt, ok=b"4 unten Mitte" in data, free=b"5 frei" in data)
    except OSError:
        return PIP_CORNERS
    if not _CENTER["ok"]:
        return PIP_CORNERS[:4]
    return PIP_CORNERS if _CENTER["free"] else PIP_CORNERS[:5]


_MULTI = {"mtime": None, "ok": True}


def plugin_multi():
    try:
        mt = os.stat(PLUGIN_SO).st_mtime
        if _MULTI["mtime"] != mt:
            with open(PLUGIN_SO, "rb") as f:
                _MULTI.update(mtime=mt, ok=b"slot3" in f.read())
    except OSError:
        return True
    return _MULTI["ok"]


_SWAP = {"mtime": None, "ok": True}


def plugin_swap():
    try:
        mt = os.stat(PLUGIN_SO).st_mtime
        if _SWAP["mtime"] != mt:
            with open(PLUGIN_SO, "rb") as f:
                data = f.read()
            _SWAP.update(mtime=mt, ok=b"pbpipsel" in data and b"follow-tag" in data)
    except OSError:
        return True
    return _SWAP["ok"]


_FILL = {"mtime": None, "ok": True}


FEED_PORT = 9410


FEED_VIDEO_CAPS = "application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000"


FEED_AUDIO_CAPS = "audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved"


FEED_FILL_VIDEO = "video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1"


CAM_LIVE = "/run/pipbox-send/cam-live"


def plugin_fill():
    try:
        mt = os.stat(PLUGIN_SO).st_mtime
        if _FILL["mtime"] != mt:
            with open(PLUGIN_SO, "rb") as f:
                data = f.read()
            _FILL.update(mtime=mt, ok=b"fill-caps" in data and b"live-file" in data)
    except OSError:
        return True
    return _FILL["ok"]


def feeder_tool():
    return shutil.which("gst-launch-1.0")


_VIEW = {"mtime": None, "ok": True}


VIEW_ENABLED = True


def plugin_view():
    if not VIEW_ENABLED:
        return False
    try:
        mt = os.stat(PLUGIN_SO).st_mtime
        if _VIEW["mtime"] != mt:
            with open(PLUGIN_SO, "rb") as f:
                data = f.read()
            _VIEW.update(mtime=mt, ok=b"view-file" in data and b"view-state-file" in data)
    except OSError:
        return True
    return _VIEW["ok"]


_STYLE = {"mtime": None, "ok": True}


def plugin_style():
    try:
        mt = os.stat(PLUGIN_SO).st_mtime
        if _STYLE["mtime"] != mt:
            with open(PLUGIN_SO, "rb") as f:
                data = f.read()
            _STYLE.update(mtime=mt, ok=b"style1" in data and b"style3" in data)
    except OSError:
        return True
    return _STYLE["ok"]
