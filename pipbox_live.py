import json
import os
import re
import subprocess
import threading
import time
import urllib.request

import pipbox_always as pa

CANVAS_W, CANVAS_H, FPS = 1920, 1080, 30
SLOTS = 4
RINGS = 3
PLACEHOLDER = "pipbox_unused_%d"
RTMP_BASE = "rtmp://127.0.0.1:1935/publish"
RUN = "/run/pipbox-send"
CTL_FIFO = f"{RUN}/belacoder.ctl"
LIVE_STATS = f"{RUN}/belacoder-live.txt"
BIN = "/opt/pipbox/bin/belacoder"
BUFFER_NEED_MIN_MS = 600
BUFFER_MIN_COPY_MS = 1350
BUFFER_MIN_MS = 1050
FEED_COOLDOWN_S = 3.0
STATS_MAX_AGE_S = 4.0
Z_BIG = 1
COLOR_CAPS = "video/x-raw,format=NV12,colorimetry=bt709"
AUDIO_CAPS = "audio/x-raw,format=S16LE,rate=48000,channels=2"


_SUP = {"mtime": None, "ok": False}


def supported():
    force = os.environ.get("PIPBOX_LIVE")
    if force in ("0", "1"):
        return force == "1"
    try:
        mt = os.stat(BIN).st_mtime
        if _SUP["mtime"] != mt:
            out = subprocess.run([BIN, "-v"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"-sb(\d+)", out)
            _SUP.update(mtime=mt, ok=bool(m and int(m.group(1)) >= 10))
        return _SUP["ok"]
    except (OSError, subprocess.SubprocessError):
        return False


PLUGIN_SO = os.environ.get("PIPBOX_PLUGIN", "/opt/pipbox/gst/libgstpbpip.so")
_FRM = {"mtime": None, "ok": False}
FRAME_BUILT = False


def frame_ok():
    force = os.environ.get("PIPBOX_FRAME")
    if force in ("0", "1"):
        return force == "1"
    try:
        mt = os.stat(PLUGIN_SO).st_mtime
        if _FRM["mtime"] != mt:
            with open(PLUGIN_SO, "rb") as f:
                data = f.read()
            _FRM.update(mtime=mt, ok=b"pbround" in data and b"pbtap" in data)
        return _FRM["ok"]
    except OSError:
        return False


_ENG = {"mtime": None, "level": 0, "shift": False}
COPY_LEVEL = 13


def engine_level():
    force = os.environ.get("PIPBOX_ENGINE")
    if force is not None:
        try:
            return int(force)
        except ValueError:
            return 0
    try:
        mt = os.stat(BIN).st_mtime
        if _ENG["mtime"] != mt:
            out = subprocess.run([BIN, "-v"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"-sb(\d+)", out)
            _ENG.update(mtime=mt, level=int(m.group(1)) if m else 0, shift=bool(re.search(r"-sb\d+(-[a-z0-9]+)*-sh\b", out)))
        return _ENG["level"]
    except (OSError, subprocess.SubprocessError):
        return 0


def shift_ok():
    force = os.environ.get("PIPBOX_SHIFT")
    if force in ("0", "1"):
        return force == "1"
    engine_level()
    return bool(_ENG["shift"])


def enabled(cfg):
    return bool(cfg.get("type") == "pip" and cfg.get("always_ready") is True and cfg.get("main") and supported())


def pip_size(pct):
    w = max(16, int(round(CANVAS_W * pct / 100.0 / 16.0)) * 16)
    h = int(round(w * 9 / 16.0 / 2.0)) * 2
    return w, h


def crop_px(st, sw, sh):
    c = st["crop"]
    cl, cr, ct, cb = c["l"], c["r"], c["t"], c["b"]
    if cl <= 0 and cr <= 0 and ct <= 0 and cb <= 0:
        return 0, 0, 0, 0
    l = ((cl * sw + 960) // 1920) & ~1
    r = ((cr * sw + 960) // 1920) & ~1
    t = ((ct * sh + 540) // 1080) & ~1
    b = ((cb * sh + 540) // 1080) & ~1
    if l == r == t == b == 0:
        return 0, 0, 0, 0
    w, h = (sw - l - r) & ~1, (sh - t - b) & ~1
    if w < 16 or h < 16:
        return 0, 0, 0, 0
    return l, r, t, b


def place(corner, mw, mh, pw, ph, margin, fx, fy):
    if corner == 5:
        fx, fy = max(0, min(1000, fx)), max(0, min(1000, fy))
        x, y = (mw - pw) * fx // 1000, (mh - ph) * fy // 1000
        return max(0, x), max(0, y)
    if corner == 4:
        x, y = (mw - pw) // 2, mh - ph - margin
    else:
        x = mw - pw - margin if corner & 1 else margin
        y = mh - ph - margin if corner & 2 else margin
    return max(0, min(x, mw - pw)), max(0, min(y, mh - ph))


def small_geometry(c, k):
    sfx = "" if k == 1 else str(k)
    pw, ph = pip_size(c["size_pct" + sfx])
    st = c["styles"][str(k)]
    l, r, t, b = crop_px(st, 1920, 1080)
    cw, ch = ((1920 - l - r) * pw // 1920) & ~1, ((1080 - t - b) * ph // 1080) & ~1
    margin = (CANVAS_W // 60) & ~1
    x, y = place(c["corner" + sfx], CANVAS_W, CANVAS_H, cw, ch, margin, c["x" + sfx], c["y" + sfx])
    x, y = x & ~1, y & ~1
    br = st["border"]
    bw = 0
    if br["enabled"] and br["width"] > 0:
        bw = max(1, min(int(br["width"]), min(cw, ch) // 2))
    return {"sw": pw, "sh": ph, "crop": (l, r, t, b), "w": cw, "h": ch, "x": x, "y": y,
            "opacity": int(st["opacity"]) if st["visible"] else 0, "bw": bw, "radius": int(st.get("radius", 0)),
            "bcolor": int(br["color"][1:], 16), "bopacity": int(br["opacity"])}


def portrait_geometry(c, k, g, dims):
    sfx = "" if k == 1 else str(k)
    h = g["sh"]
    w = (h * dims[0] // dims[1]) & ~1
    margin = (CANVAS_W // 60) & ~1
    x, y = place(c["corner" + sfx], CANVAS_W, CANVAS_H, w, h, margin, c["x" + sfx], c["y" + sfx])
    g2 = dict(g)
    g2.update(crop=(0, 0, 0, 0), w=w, h=h, x=x & ~1, y=y & ~1)
    if g["bw"]:
        g2["bw"] = max(1, min(g["bw"], min(w, h) // 2))
    return g2


def ring_rects(g):
    x, y, w, h, bw = g["x"], g["y"], g["w"], g["h"], g["bw"]
    return [(x, y, w, bw), (x, y + h - bw, w, bw), (x, y + bw, bw, h - 2 * bw), (x + w - bw, y + bw, bw, h - 2 * bw)]


def fit_big(dims):
    if not dims or not dims[0] or not dims[1]:
        return 0, 0, CANVAS_W, CANVAS_H
    aspect = dims[1] / float(dims[0])
    cav = CANVAS_H / float(CANVAS_W)
    if abs(aspect - cav) <= 0.01:
        return 0, 0, CANVAS_W, CANVAS_H
    if aspect > cav:
        w = int(CANVAS_H / aspect) & ~1
        return ((CANVAS_W - w) // 2) & ~1, 0, w, CANVAS_H
    h = int(CANVAS_W * aspect) & ~1
    return 0, ((CANVAS_H - h) // 2) & ~1, CANVAS_W, h


def slot_keys(c):
    b = pa.Binder()
    b.update([k for k in (c.get("main", ""), c.get("pip", ""), c.get("pip2", ""), c.get("pip3", "")) if k])
    return list(b.slot_key)


MAX_DELAY_MS = 750
SHIFT_MAX_MS = 3000
LAG_LATE_MS = 1500
LAG_EARLY_MS = -2500
LAG_SUSTAIN_S = 6.0
LAG_GRACE_S = 8.0
LAG_HOLD_S = 15.0
LAG_MAX_TRIES = 3
LAG_WINDOW_S = 180.0
HOLD_FILE = "feed-hold.json"


def delays_by_key(c):
    out = {}
    for pos, dk in (("main", "main_delay_ms"), ("pip", "pip_delay_ms"), ("pip2", "pip2_delay_ms"), ("pip3", "pip3_delay_ms")):
        if c.get(pos):
            out[c[pos]] = max(0, min(SHIFT_MAX_MS if shift_ok() else MAX_DELAY_MS, int(c.get(dk, 0) or 0)))
    return out


def buffer_ms(c):
    return max(BUFFER_MIN_COPY_MS if engine_level() >= COPY_LEVEL else BUFFER_MIN_MS, buffer_need_ms(c))


def buffer_need_ms(c):
    if shift_ok():
        return BUFFER_NEED_MIN_MS
    return max(BUFFER_NEED_MIN_MS, max(delays_by_key(c).values() or [0]) + 300)


LEAD_MAX_MS = 1000
LEAD_MAX_BIG_MS = 600
HOLD_UNIFORM = True
BRANCH_QUEUE_BUFFERS = 3
SWAP_SETTLE_S = 0.2
WAIT_MARK = "#wait"
BUFFER_LATE_MS = 150
BUFFER_EARLY_MS = 800


def buffer_mismatch(running, need):
    try:
        running, need = int(running), int(need)
    except (TypeError, ValueError):
        return False
    return need > running + BUFFER_LATE_MS or running > need + BUFFER_EARLY_MS


def hold_delays(delays, buffer, big_key=None, big_lead=None, uniform=False, shifted=False):
    floor = max(0, int(buffer) - LEAD_MAX_MS)
    floor_big = max(0, int(buffer) - (LEAD_MAX_BIG_MS if big_lead is None else int(big_lead)))
    if shifted:
        return {k: max(0, int(v)) + (floor_big if (uniform or k == big_key) else floor) for k, v in delays.items()}
    if uniform and delays:
        lo, hi = min(int(v) for v in delays.values()), max(int(v) for v in delays.values())
        shift = max(0, min(floor_big - lo, int(buffer) - 300 - hi))
        return {k: max(int(v) + shift, floor_big) for k, v in delays.items()}
    return {k: max(int(v), floor_big if k == big_key else floor) for k, v in delays.items()}


def build(c, base=RTMP_BASE, frame=None):
    global FRAME_BUILT
    frame = frame_ok() if frame is None else bool(frame)
    FRAME_BUILT = frame
    q = "queue max-size-time=10000000000 max-size-buffers=1000 max-size-bytes=41943040"
    keys = slot_keys(c)
    abuf = max(2000, buffer_ms(c) + 1000 + (max(delays_by_key(c).values() or [0]) if shift_ok() else 0)) * 1000000
    out = []
    pads = []
    for s in range(SLOTS):
        pads.append(f"sink_{s}::alpha=0 sink_{s}::zorder={Z_BIG}")
    n = SLOTS
    for k in range(RINGS):
        for j in range(4):
            pads.append(f"sink_{n}::xpos=0 sink_{n}::ypos=0 sink_{n}::width=2 sink_{n}::height=2 sink_{n}::alpha=0 sink_{n}::zorder={3 + 2 * k}")
            n += 1
    out.append("compositor name=pipcomp background=black " + " ".join(pads) + " !\n"
               f"video/x-raw,format=NV12,width={CANVAS_W},height={CANVAS_H},framerate={FPS}/1,colorimetry=bt709 !\n"
               + ("pbround name=rnd !\n" if frame else "")
               + "queue max-size-time=500000000 max-size-buffers=4 leaky=downstream !\n"
               "mpph265enc zero-copy-pkt=0 qp-max=51 gop=60 name=venc_bps !\n"
               f"h265parse config-interval=-1 ! {q} ! mux.\n")
    out.append("input-selector name=asel ! identity name=a_delay signal-handoffs=TRUE ! volume name=avol ! "
               f"opusenc bitrate=128000 ! opusparse ! {q} ! mux.\n")
    out.append(f"audiotestsrc is-live=true wave=silence ! capsfilter caps=\"{AUDIO_CAPS}\" ! queue ! asel.sink_0\n")
    n = SLOTS
    for k in range(RINGS):
        for j in range(4):
            i = k * 4 + j
            out.append(f"videotestsrc name=sbb{i} is-live=true pattern=solid-color foreground-color=4294967295 !\n"
                       f"capsfilter name=sbb{i}s caps=\"video/x-raw,format=NV12,width=2,height=2,framerate={FPS}/1,colorimetry=bt709\" !\n"
                       f"queue max-size-buffers=2 leaky=downstream ! pipcomp.sink_{n}\n")
            n += 1
    for s in range(SLOTS):
        key = keys[s] or PLACEHOLDER % s
        nm = f"sbf{s}"
        out.append(
            f"rtmpsrc name={nm}_src location={base}/{key} ! flvdemux name={nm}\n"
            f"{nm}.video ! queue name={nm}_vq max-size-time=4000000000 max-size-buffers=400 ! h264parse name={nm}_parse ! "
            f"mppvideodec name={nm}_dec ! "
            f"queue name={nm}_lq max-size-buffers=45 max-size-bytes=0 max-size-time=0 leaky=downstream ! "
            f"videorate name={nm}_vr max-duplication-time=100000000 ! capsfilter name={nm}_c1 caps=\"video/x-raw,framerate={FPS}/1\" ! "
            f"videocrop name={nm}_crop ! "
            f"videoscale name={nm}_sc method=nearest-neighbour ! "
            f"capsfilter name={nm}_scc caps=\"video/x-raw,format=NV12,width={CANVAS_W},height={CANVAS_H}\" ! "
            f"capssetter name={nm}_c2 join=true replace=false caps=\"video/x-raw,colorimetry=bt709\" ! "
            + (f"pbtap name={nm}_tap ! " if frame else "") +
            f"queue name={nm}_q max-size-buffers={BRANCH_QUEUE_BUFFERS} max-size-bytes=0 max-size-time=0 ! pipcomp.sink_{s}\n"
            f"{nm}.audio ! queue name={nm}_aq max-size-time={abuf} max-size-buffers=0 max-size-bytes=0 ! aacparse name={nm}_ap ! avdec_aac name={nm}_ad ! "
            f"audioconvert name={nm}_ac1 ! audioresample name={nm}_ar ! capsfilter name={nm}_ac2 caps=\"{AUDIO_CAPS}\" ! "
            f"queue name={nm}_aq2 max-size-time={abuf} max-size-buffers=0 max-size-bytes=0 ! asel.sink_{s + 1}\n")
    out.append("mpegtsmux name=mux !\nappsink name=appsink\n")
    out.append("\n#sb-absent: " + " ".join(f"sbf{s}" for s in range(SLOTS)) + "\n")
    return "\n".join(out)


def scale_caps(w, h):
    return f"video/x-raw,format=NV12,width={w},height={h}"


def strip_caps(w, h):
    return f"video/x-raw,format=NV12,width={w},height={h},framerate={FPS}/1,colorimetry=bt709"


def argb(rgb):
    return (0xff000000 | rgb) & 0xffffffff


_CAPS_RE = re.compile(r"width=(\d+),height=(\d+)")


def _area(line):
    m = _CAPS_RE.search(line or "")
    return int(m.group(1)) * int(m.group(2)) if m else None


def order_for_swap(lines, old_caps, alpha):
    caps = {}
    for i, l in enumerate(lines):
        m = re.match(r"set sbf(\d+)_scc caps ", l)
        if m:
            s = int(m.group(1))
            old, new = _area(old_caps.get(s)), _area(l)
            if old and new:
                if new * 5 < old * 4:
                    caps[s] = "shrink"
                elif new * 4 > old * 5:
                    caps[s] = "grow"
    if not caps:
        return lines
    out = list(lines)
    for s, kind in caps.items():
        if kind == "grow":
            i = next(j for j, l in enumerate(out) if l.startswith(f"set sbf{s}_scc caps "))
            cl = out.pop(i)
            last = max((j for j, l in enumerate(out) if l.startswith(f"pad pipcomp sink_{s} ")), default=i - 1)
            out.insert(last + 1, cl)
    later = []
    for s, kind in caps.items():
        if kind != "shrink":
            continue
        mine = [l for l in out if l.startswith(f"pad pipcomp sink_{s} ")]
        geo = [l for l in mine if " alpha " not in l]
        out = [l for l in out if l not in mine]
        i = next(j for j, l in enumerate(out) if l.startswith(f"set sbf{s}_scc caps "))
        out.insert(i + 1, f"pad pipcomp sink_{s} alpha 0")
        later += geo + [alpha.get(s) or f"pad pipcomp sink_{s} alpha 1.00"]
    return out + [WAIT_MARK] + later if later else out


class Layout:

    def __init__(self):
        self.sent = {}

    def reset(self):
        self.sent = {}

    def commands(self, c, slot_key, roles, dims, audio_ok, hide, audio_pos, mute, delay_ms, shifts=None):
        want = []
        geoms = {k: small_geometry(c, k) for k in (1, 2, 3)}
        by_pos = {v: s for s, v in roles.items()}
        for k in (1, 2, 3):
            d = dims.get(by_pos.get(k)) if by_pos.get(k) is not None else None
            if d and d[0] and d[1] and d[1] > d[0]:
                geoms[k] = portrait_geometry(c, k, geoms[k], d)

        def add(key, line, z):
            want.append((key, line, z))

        for s in range(SLOTS):
            nm = f"sbf{s}"
            pos = roles.get(s)
            if pos is None:
                add(("a", s), f"pad pipcomp sink_{s} alpha 0", -1)
                continue
            if pos == 0:
                x, y, w, h = fit_big(dims.get(s))
                crop, sw, sh, alpha, z = (0, 0, 0, 0), w, h, 1.0, Z_BIG
            else:
                g = geoms[pos]
                x, y, w, h = g["x"], g["y"], g["w"], g["h"]
                sw0, sh0 = dims.get(s) or (1920, 1080)
                sw0, sh0 = sw0 or 1920, sh0 or 1080
                crop = tuple(int(v * k // 1920) & ~1 for v, k in zip(g["crop"], (sw0, sw0, sw0, sw0)))
                crop = (crop[0], crop[1], int(g["crop"][2] * sh0 // 1080) & ~1, int(g["crop"][3] * sh0 // 1080) & ~1)
                sw, sh = g["w"], g["h"]
                op = 0 if (hide >> (pos - 1)) & 1 else g["opacity"]
                alpha, z = op / 100.0, 2 * pos
            for name, v in zip(("left", "right", "top", "bottom"), crop):
                add(("crop", s, name), f"set {nm}_crop {name} {v}", z)
            add(("caps", s), f"set {nm}_scc caps {scale_caps(sw, sh)}", z)
            add(("x", s), f"pad pipcomp sink_{s} xpos {x}", z)
            add(("y", s), f"pad pipcomp sink_{s} ypos {y}", z)
            add(("w", s), f"pad pipcomp sink_{s} width {w}", z)
            add(("h", s), f"pad pipcomp sink_{s} height {h}", z)
            add(("z", s), f"pad pipcomp sink_{s} zorder {z}", z)
            add(("a", s), f"pad pipcomp sink_{s} alpha {alpha:.2f}", z)
        rounded = {}
        if FRAME_BUILT:
            for s in range(SLOTS):
                add(("tap", s), f"set sbf{s}_tap active {'true' if roles.get(s) == 0 else 'false'}", 100)
            for k in (1, 2, 3):
                g = geoms[k]
                s = by_pos.get(k)
                shown = s is not None and g["opacity"] > 0 and not (hide >> (k - 1)) & 1
                on = shown and g["radius"] > 0
                rounded[k] = on
                val = (f"{g['x']},{g['y']},{g['w']},{g['h']},{g['bw']},{g['bcolor']:06x},{g['bopacity']},{g['radius']},{g['opacity']}") if on else "off"
                add(("rnd", k), f"set rnd pip{k} {val}", 2 * k + 1)
        for k in (1, 2, 3):
            g = geoms[k]
            s = by_pos.get(k)
            shown = s is not None and g["bw"] > 0 and g["opacity"] > 0 and not (hide >> (k - 1)) & 1 and not rounded.get(k)
            rects = ring_rects(g) if shown else [(0, 0, 2, 2)] * 4
            alpha = (g["bopacity"] / 100.0) * (g["opacity"] / 100.0) if shown else 0.0
            for j in range(4):
                i = (k - 1) * 4 + j
                n = SLOTS + i
                rx, ry, rw, rh = rects[j]
                rw, rh = max(2, rw), max(2, rh)
                z = 2 * k + 1
                add(("rc", i), f"set sbb{i} foreground-color {argb(g['bcolor'])}", z)
                add(("rcaps", i), f"set sbb{i}s caps {strip_caps(rw, rh)}", z)
                add(("rx", i), f"pad pipcomp sink_{n} xpos {rx}", z)
                add(("ry", i), f"pad pipcomp sink_{n} ypos {ry}", z)
                add(("rw", i), f"pad pipcomp sink_{n} width {rw}", z)
                add(("rh", i), f"pad pipcomp sink_{n} height {rh}", z)
                add(("ra", i), f"pad pipcomp sink_{n} alpha {alpha:.2f}", z)
        asel = 0
        src = by_pos.get(0) if audio_pos < 0 else by_pos.get(audio_pos + 1)
        if src is None and audio_pos >= 0:
            src = by_pos.get(0)
        if src is not None and src in audio_ok:
            asel = src + 1
        add(("asel",), f"select asel sink_{asel}", 100)
        add(("mute",), f"set avol mute {'true' if mute else 'false'}", 100)
        for s in range(SLOTS):
            d = delay_ms.get(slot_key[s] or "", 0)
            add(("delay", s), f"set sbf{s}_vq min-threshold-time {(d + 33) * 1000000 if d else 0}", 100)
            if shifts is not None:
                add(("shift", s), f"shift sbf{s} {int(shifts.get(slot_key[s] or '', 0))}", 100)
        want.sort(key=lambda t: t[2])
        lines, old_caps, alpha = [], {}, {}
        for key, line, _ in want:
            if key[0] == "caps":
                old_caps[key[1]] = self.sent.get(key)
            elif key[0] == "a" and isinstance(key[1], int):
                alpha[key[1]] = line
            if self.sent.get(key) != line:
                lines.append(line)
                self.sent[key] = line
        return order_for_swap(lines, old_caps, alpha)


    def pre_commands(self, c, pre_roles, dims):
        geoms = {k: small_geometry(c, k) for k in (1, 2, 3)}
        want = []
        for s, pos in pre_roles.items():
            nm = f"sbf{s}"
            if pos == 0:
                x, y, w, h = fit_big(dims.get(s))
                crop, sw, sh = (0, 0, 0, 0), w, h
            else:
                g = geoms[pos]
                sw0, sh0 = dims.get(s) or (1920, 1080)
                sw0, sh0 = sw0 or 1920, sh0 or 1080
                crop = tuple(int(v * k // 1920) & ~1 for v, k in zip(g["crop"], (sw0, sw0, sw0, sw0)))
                crop = (crop[0], crop[1], int(g["crop"][2] * sh0 // 1080) & ~1, int(g["crop"][3] * sh0 // 1080) & ~1)
                sw, sh = g["w"], g["h"]
            for name, v in zip(("left", "right", "top", "bottom"), crop):
                want.append((("crop", s, name), f"set {nm}_crop {name} {v}"))
            want.append((("caps", s), f"set {nm}_scc caps {scale_caps(sw, sh)}"))
        lines = []
        for key, line in want:
            if self.sent.get(key) != line:
                lines.append(line)
                self.sent[key] = line
        return lines


def read_stats(path=LIVE_STATS, now=None):
    try:
        if (now if now is not None else time.time()) - os.stat(path).st_mtime > STATS_MAX_AGE_S:
            return {}
        with open(path) as f:
            text = f.readline()
    except OSError:
        return {}
    return {int(m.group(1)): int(m.group(2)) for m in re.finditer(r" feed_sbf(\d)=(\d)", text)}


def read_lags(path=LIVE_STATS, now=None):
    try:
        if (now if now is not None else time.time()) - os.stat(path).st_mtime > STATS_MAX_AGE_S:
            return {}
        with open(path) as f:
            text = f.readline()
    except OSError:
        return {}
    return {int(m.group(1)): int(m.group(3)) for m in re.finditer(r" in_f(\d)_lag=(-?\d+)/(-?\d+)/(-?\d+)", text)}


def read_fps(path=LIVE_STATS, now=None):
    try:
        if (now if now is not None else time.time()) - os.stat(path).st_mtime > STATS_MAX_AGE_S:
            return {}
        with open(path) as f:
            text = f.readline()
    except OSError:
        return {}
    out = {}
    for m in re.finditer(r" in_f(\d)=(\d+(?:\.\d+)?)/", text):
        v = float(m.group(2))
        if v > 0:
            out[int(m.group(1))] = round(v, 1)
    return out


def parse_rtmp_stats(xml):
    out = {}
    for m in re.finditer(r"<stream>.*?</stream>", xml, re.S):
        s = m.group(0)
        n = re.search(r"<name>([^<]+)</name>", s)
        if not n or "<publishing/>" not in s:
            continue
        w, h = re.search(r"<width>(\d+)</width>", s), re.search(r"<height>(\d+)</height>", s)
        out[n.group(1).strip()] = {"w": int(w.group(1)) if w else 0, "h": int(h.group(1)) if h else 0, "audio": "<audio>" in s}
    return out


def fetch_publishing(url="http://127.0.0.1:1936/"):
    try:
        return parse_rtmp_stats(urllib.request.urlopen(url, timeout=2).read().decode(errors="replace"))
    except (OSError, ValueError):
        return None


def send_control(lines, path=CTL_FIFO):
    if not lines:
        return True
    fd = None
    try:
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        data = ("\n".join(lines) + "\n").encode()
        for i in range(0, len(data), 3000):
            os.write(fd, data[i:i + 3000])
        return True
    except OSError:
        return False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def big_cpus(cpu_dir="/sys/devices/system/cpu"):
    cap = {}
    try:
        for d in os.listdir(cpu_dir):
            m = re.fullmatch(r"cpu(\d+)", d)
            if m:
                try:
                    cap[int(m.group(1))] = int(open(f"{cpu_dir}/{d}/cpu_capacity").read().strip())
                except (OSError, ValueError):
                    pass
    except OSError:
        return []
    if not cap or max(cap.values()) == min(cap.values()):
        return []
    top = max(cap.values())
    return sorted(c for c, v in cap.items() if v >= top * 0.9)


class CpuBoost:

    def __init__(self, cpu_dir="/sys/devices/system/cpu", state=f"{RUN}/cpugov.json", log=print):
        self.cpu_dir, self.state, self.log = cpu_dir, state, log
        self.saved = {}

    def _policy_dirs(self):
        seen, out = set(), []
        for c in big_cpus(self.cpu_dir):
            p = f"{self.cpu_dir}/cpu{c}/cpufreq"
            try:
                rel = tuple(sorted(int(x) for x in open(p + "/related_cpus").read().split()))
            except (OSError, ValueError):
                rel = (c,)
            if rel not in seen:
                seen.add(rel)
                out.append(p)
        return out

    def on(self):
        for p in self._policy_dirs():
            try:
                cur = open(p + "/scaling_governor").read().strip()
                if cur != "performance" and "performance" in open(p + "/scaling_available_governors").read().split():
                    with open(p + "/scaling_governor", "w") as f:
                        f.write("performance")
                    self.saved[p] = cur
            except OSError as e:
                self.log(f"send: Takt der schnellen Kerne nicht umstellbar ({e.__class__.__name__})")
        if self.saved:
            try:
                os.makedirs(os.path.dirname(self.state), exist_ok=True)
                with open(self.state, "w") as f:
                    json.dump(self.saved, f)
            except OSError:
                pass
            self.log("send: schnelle Kerne auf vollem Takt (performance), solange gemischt wird")

    def off(self):
        saved = self.saved
        if not saved:
            try:
                saved = json.load(open(self.state))
            except (OSError, ValueError):
                saved = {}
        for p, g in saved.items():
            try:
                with open(p + "/scaling_governor", "w") as f:
                    f.write(str(g))
            except OSError:
                pass
        self.saved = {}
        try:
            os.unlink(self.state)
        except OSError:
            pass


class LiveController:

    def __init__(self, load_json, put_state_file, state_dir, safe_cfg, view_values, log=print, clock=time.monotonic,
                 fetch=fetch_publishing, stats=read_stats, control=send_control, rtmp_base=RTMP_BASE, start_cfg=None, wall=time.time, lags=read_lags):
        self.load_json, self.put, self.state_dir = load_json, put_state_file, state_dir
        self.wall = wall
        self.held = set()
        self.lags_fn = lags
        self.lag_hold = {}
        self.lag_since, self.up_at, self.lag_tries = {}, {}, {}
        self.safe_cfg, self.view_values = safe_cfg, view_values
        sc = safe_cfg(start_cfg) if start_cfg else None
        self.running_buffer = buffer_ms(sc) if sc else None
        self.big_lead = LEAD_MAX_MS if engine_level() >= COPY_LEVEL else LEAD_MAX_BIG_MS
        self.start_delays = delays_by_key(sc) if sc else {}
        self.delays_frozen = False
        self.log, self.clock, self.fetch, self.stats, self.control, self.base = log, clock, fetch, stats, control, rtmp_base
        self.sleep = time.sleep
        self.binder = pa.Binder()
        self.chooser = pa.Chooser(clock)
        self.layout = Layout()
        self.cfg = {}
        self.desired = ["", "", "", ""]
        self.inactive = []
        self.cfg_sig = None
        self.cmd_at = {}
        self.pub = {}
        self.pub_known = False
        self.pub_t = -10.0
        self.view_sig = object()
        self.view = (0, -1, 0)
        self.main_key = None
        self.last_line = None
        self.states = {}
        self.alive = set()
        self.reassign_lines = []
        self.started = False
        self._view_pending = False

    def reload(self):
        cfg = self.load_json("pipeline.json")
        sig = repr(sorted((k, repr(v)) for k, v in cfg.items()))
        if sig == self.cfg_sig:
            return False
        self.cfg_sig = sig
        c = self.safe_cfg({**{"type": "single", "main": ""}, **cfg})
        self.cfg = c
        self.desired = [c.get(k, "") if isinstance(c.get(k, ""), str) and pa.KEY_RE.match(c.get(k, "") or "") else "" for k in ("main", "pip", "pip2", "pip3")]
        ina = c.get("inactive")
        self.inactive = [k for k in ina if isinstance(k, str)] if isinstance(ina, list) else []
        before = list(self.binder.slot_key)
        self.binder.update([k for k in self.desired if k])
        for s in range(SLOTS):
            if self.started and before[s] != self.binder.slot_key[s]:
                key = self.binder.slot_key[s] or PLACEHOLDER % s
                self.reassign_lines += [f"feed sbf{s} off", f"set sbf{s}_src location {self.base}/{key}"]
                self.cmd_at[s] = 0.0
                self.log(f"send: Platz {s}: Kamera {before[s] or '-'} -> {self.binder.slot_key[s] or '-'} (ohne Neustart)")
        return True

    def effective_delays(self, big_key=None):
        want = delays_by_key(self.cfg)
        frozen = self.running_buffer is not None and buffer_mismatch(self.running_buffer, buffer_need_ms(self.cfg))
        if frozen != self.delays_frozen:
            self.delays_frozen = frozen
            self.log("send: Verzögerung passt nicht zum laufenden Puffer, bleibt bis zum Neustart bei den Werten vom Start" if frozen
                     else "send: Verzögerung passt wieder zum laufenden Puffer, gilt jetzt")
        if frozen:
            cap = max(0, self.running_buffer - 300)
            want = {k: (self.start_delays[k] if k in self.start_delays else min(v, cap)) for k, v in want.items()}
        self.shift_ms = dict(want) if shift_ok() else None
        return hold_delays(want, self.running_buffer if self.running_buffer is not None else buffer_ms(self.cfg), big_key, self.big_lead, uniform=HOLD_UNIFORM, shifted=shift_ok())

    def read_view(self):
        path = os.path.join(self.state_dir, "main-view")
        try:
            mt = os.stat(path).st_mtime_ns
        except OSError:
            mt = None
        if mt != self.view_sig:
            self.view_sig = mt
            try:
                with open(path) as f:
                    hide, audio, mute = (int(x) for x in f.read().split()[:3])
            except (OSError, ValueError):
                hide, audio = self.view_values(self.cfg) if self.cfg else (0, -1)
                mute = 0
            self.view = (hide, audio, 1 if mute else 0)
            return True
        return False

    def start(self):
        self.reload()
        self.read_view()
        self.started = True
        self.layout.reset()

    def stop(self):
        pass

    def on_restart(self):
        self.layout.reset()
        self.cmd_at.clear()
        self.view_sig = object()
        self.states = {}
        self._view_pending = True
        self.reassign_lines = [f"set sbf{s}_src location {self.base}/{self.binder.slot_key[s] or PLACEHOLDER % s}" for s in range(SLOTS)]

    def read_hold(self):
        d = self.load_json(HOLD_FILE)
        keys = d.get("keys") if isinstance(d, dict) else None
        now = self.wall()
        self.held = {k for k, t in keys.items() if isinstance(k, str) and isinstance(t, (int, float)) and not isinstance(t, bool) and t > now} if isinstance(keys, dict) else set()
        mono = self.clock()
        self.lag_hold = {k: t for k, t in self.lag_hold.items() if t > mono}
        self.held |= set(self.lag_hold)

    def check_lag(self, now, slot_key):
        lags = self.lags_fn() or {}
        for s in range(SLOTS):
            key = slot_key[s]
            if not key or self.states.get(s) != 3 or key in self.held:
                self.lag_since.pop(s, None)
                self.up_at.pop(s, None)
                continue
            up = self.up_at.setdefault(s, now)
            v = lags.get(s)
            if v is None or now - up < LAG_GRACE_S or not (v > LAG_LATE_MS or v < LAG_EARLY_MS):
                self.lag_since.pop(s, None)
                continue
            since = self.lag_since.setdefault(s, now)
            if now - since < LAG_SUSTAIN_S:
                continue
            self.lag_since.pop(s, None)
            tries = [t for t in self.lag_tries.get(key, []) if now - t < LAG_WINDOW_S]
            self.lag_tries[key] = tries
            if len(tries) >= LAG_MAX_TRIES:
                continue
            tries.append(now)
            self.lag_hold[key] = now + LAG_HOLD_S
            self.held.add(key)
            self.up_at.pop(s, None)
            self.log(f"send: Kamera {key} (Platz {s}): Bild {abs(v) / 1000:.1f} s {'zu spät' if v > 0 else 'zu früh'}, der Zweig wird {int(LAG_HOLD_S)} s ausgesetzt und neu ausgerichtet")

    def _cooldown_ok(self, s, now):
        return now - self.cmd_at.get(s, -100.0) > FEED_COOLDOWN_S

    def tick(self):
        now = self.clock()
        self.reload()
        self.read_hold()
        view_changed = self.read_view()
        reassign = self.reassign_lines
        self.reassign_lines = []
        lines = list(reassign)
        if now - self.pub_t >= 1.0:
            got = self.fetch()
            self.pub_t = now
            if got is not None:
                self.pub, self.pub_known = got, True
        self.states = self.stats()
        slot_key = self.binder.slot_key
        self.check_lag(now, slot_key)
        pre = {}
        for pos in range(4):
            k = self.desired[pos]
            s0 = slot_key.index(k) if k and k in slot_key else None
            if s0 is not None and self.states.get(s0) in (1, 2) and k in self.pub and k not in self.held:
                pre[s0] = pos
        if pre and self.cfg and self.pub_known:
            dims0 = {s: (self.pub.get(slot_key[s], {}).get("w", 0), self.pub.get(slot_key[s], {}).get("h", 0)) for s in pre}
            lines += self.layout.pre_commands(self.cfg, pre, dims0)
        for s in range(SLOTS):
            key, st = slot_key[s], self.states.get(s)
            if st is None or not self.pub_known:
                continue
            want_on = bool(key) and key in self.pub and key not in self.held and not (key in self.inactive and key != self.desired[0])
            cmd = None
            if want_on and st == 1:
                cmd = "on"
            elif not want_on and st >= 2:
                cmd = "off"
            if cmd and self._cooldown_ok(s, now):
                self.cmd_at[s] = now
                lines.append(f"feed sbf{s} {cmd}")
                self.log(f"send: Kamera {key or '-'} (Platz {s}): {cmd}")
        self.alive = {slot_key[s] for s in range(SLOTS) if slot_key[s] and self.states.get(s) == 3 and slot_key[s] in self.pub
                      and slot_key[s] not in self.held and not (slot_key[s] in self.inactive and slot_key[s] != self.desired[0])}
        slot_of = {k: i for i, k in enumerate(slot_key) if k}
        line, main = self.chooser.line(self.desired, slot_of, self.alive, self.inactive)
        if line != self.last_line:
            if main != self.main_key and self.main_key is not None:
                self.log(f"send: Hauptbild jetzt {main} (eingestellt: {self.desired[0]})")
            self.main_key, self.last_line = main, line
        nums = [int(x) for x in line.split()]
        roles = {}
        if main in slot_of and main in self.alive:
            roles[slot_of[main]] = 0
        for k in (1, 2, 3):
            if nums[k] != 15 and slot_key[nums[k]] in self.alive and nums[k] not in roles:
                roles[nums[k]] = k
        dims = {s: (self.pub.get(slot_key[s], {}).get("w", 0), self.pub.get(slot_key[s], {}).get("h", 0)) for s in range(SLOTS) if slot_key[s]}
        audio_ok = {s for s in range(SLOTS) if slot_key[s] and self.pub.get(slot_key[s], {}).get("audio")}
        hide, apos, mute = self.view
        if apos >= 0 and self.desired[apos + 1] in self.inactive:
            apos = -1
        if self.cfg:
            cl = self.layout.commands(self.cfg, slot_key, roles, dims, audio_ok, hide, apos, mute, self.effective_delays(main), getattr(self, "shift_ms", None))
            sel = [x for x in cl if x.startswith("select asel ")]
            lines = sel + lines + [x for x in cl if not x.startswith("select asel ")]
        eff_changed = apos != getattr(self, "_eff_apos", None)
        self._eff_apos = apos
        if lines and os.environ.get("PIPBOX_LIVE_DEBUG"):
            self.log("live: " + " | ".join(lines)[:1500])
        ok = self.send_lines(lines)
        if not ok:
            self.layout.reset()
            self.reassign_lines = reassign + self.reassign_lines
            self._view_pending = self._view_pending or view_changed
        else:
            self._confirm_view(view_changed or self._view_pending or eff_changed)
            self._view_pending = False
        shown = set(self.alive) | {k for k in slot_key if k and k in self.pub}
        return {"slots": list(slot_key), "alive": sorted(shown), "main": self.main_key, "line": self.last_line}

    def send_lines(self, lines):
        parts = [[]]
        for l in lines:
            if l == WAIT_MARK:
                parts.append([])
            else:
                parts[-1].append(l)
        ok = True
        for i, p in enumerate(parts):
            if i:
                self.sleep(SWAP_SETTLE_S)
            if p:
                ok = bool(self.control(p)) and ok
        return ok

    def _confirm_view(self, force):
        hide, apos, mute = self.view
        apos = getattr(self, "_eff_apos", apos)
        text = f"{hide} {apos} {mute}\n"
        if not force and getattr(self, "_conf_text", None) == text:
            return
        try:
            tmp = f"{RUN}/view-state.tmp"
            with open(tmp, "w") as f:
                f.write(text)
            os.chmod(tmp, 0o644)
            os.replace(tmp, f"{RUN}/view-state")
            self._conf_text = text
        except OSError:
            pass
