import collections, json, mmap, multiprocessing as mp, os, queue, struct, subprocess, sys, threading, time
import cv2
import numpy as np
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
sys.setswitchinterval(0.001)
TAP = os.environ.get("PB_TAP", "/dev/shm/pbpixel-tap")
BOX = os.environ.get("PB_BOX", "/dev/shm/pbpixel-boxes")
NREC, MAXBOX, HDR = 320, 40, 16
TAP_FPS = int(os.environ.get("TAP_FPS", "10"))
REC_DT = np.dtype([("pts", "<i8"), ("n", "<u4"), ("pad", "<u4"),
                   ("box", [("id", "<i4"), ("cells", "<i4"), ("x0", "<f4"), ("y0", "<f4"), ("x1", "<f4"), ("y1", "<f4")], (MAXBOX,))])
assert REC_DT.itemsize == 976


def detector_proc(inq, outq, own_zones):
    from privacy.config import Config
    from privacy.backends import RknnBackend
    from privacy.detect import Detector
    det = Detector(RknnBackend(here + "/modelle", cores=3), Config(own_zones=own_zones, face_scan=False))
    outq.put(("ready",))
    while True:
        i, img = inq.get()
        t0 = time.time()
        r = det.run(img)
        res = {k: [[float(v) for v in b] for b in r[k]] for k in ("plate", "face", "head")}
        res["ctx"] = {"per": [[float(v) for v in b[:4]] for b in r["per"]], "moto": [[float(v) for v in b[:4]] for b in r["veh"] if int(b[5]) == 3]}
        outq.put(("pass", i, res, time.time() - t0))


def face_proc(fq, fout, own_zones):
    import cv2
    from privacy.config import Config
    from privacy.detect import Detector, nms
    cfg = Config(own_zones=own_zones)
    class _B:
        face_model = here + "/modelle/yunet.onnx"
    det = Detector(_B(), cfg)
    fout.put(("fready",))
    while True:
        i, img = fq.get()
        h, w = img.shape[:2]
        det.fd.setInputSize((w, h))
        _, fs = det.fd.detect(img)
        sc = 1920.0 / w
        out = []
        if fs is not None:
            for f in fs:
                if f[14] >= cfg.face_scan_conf and f[2] * sc >= cfg.min_face_px and f[3] * sc >= cfg.min_face_px:
                    b = [float(f[0] * sc), float(f[1] * sc), float((f[0] + f[2]) * sc), float((f[1] + f[3]) * sc), float(f[14])]
                    if not det._own(b, 1920, 1080):
                        out.append(b)
        fout.put(("faces", i, out))


if __name__ == "__main__":
    from privacy.config import Config
    from privacy import mosaic
    from privacy.flowtrack import FlowTracker
    key = sys.argv[1]
    DELAY = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0
    PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 8790
    RTMP_OUT = os.environ.get("RTMP_OUT", "pixeltest")
    RTMP_KBPS = int(os.environ.get("RTMP_KBPS", "6000"))
    TS_PORT = int(os.environ.get("TS_PORT", "9001"))
    NO_GST = os.environ.get("PB_NO_GST") == "1"
    cv2.setNumThreads(2)
    cfg = Config(own_zones=json.loads(os.environ.get("OWN_ZONES", "[]")))
    for _n, _t in (("margin_face", float), ("margin_plate", float), ("margin_px", int)):
        if os.environ.get(_n.upper()):
            setattr(cfg, _n, _t(os.environ[_n.upper()]))
    FACE_EVERY = int(os.environ.get("FACE_EVERY", "5"))
    TEMP_TARGET = float(os.environ.get("TEMP_TARGET", "64"))
    KINDS = (("plate", 0, cfg.margin_plate, 3, cfg.sure_plate, 0.3), ("face", 1_000_000, cfg.margin_face, 4, cfg.sure_face, 0.0), ("head", 2_000_000, cfg.margin_face, 4, 0.6, 0.0))
    S = {"pass_s": 0.0, "passes": 0, "tap_frames": 0, "boxes": 0, "paused": False, "warn": "", "cpu_gross": 0.0, "cpu_klein": 0.0, "temp": 0.0, "trk_backlog": 0,
         "recs": 0, "pixel": True, "delay_s": DELAY, "last_pass": -1, "slow": 0.0}
    st = {"stop": False, "busy": False, "pass_end": 0.0, "face_last": 0.0}
    grays, pts_of = {}, {}
    gl = threading.Lock()
    evq = queue.Queue()
    info = {"w": 1920, "h": 1080}

    size = HDR + NREC * REC_DT.itemsize
    try:
        fdb = os.open(BOX, os.O_RDWR)
    except FileNotFoundError:
        fdb = os.open(BOX, os.O_RDWR | os.O_CREAT, 0o666)
    os.fchmod(fdb, 0o666)
    os.ftruncate(fdb, size)
    bm = mmap.mmap(fdb, size)
    struct.pack_into("<IIII", bm, 0, 0x50424258, 0, 0, 0)

    def publish(recs):
        recs = recs[-NREC:]
        arr = np.zeros(len(recs), REC_DT)
        for k, (pts, bl) in enumerate(recs):
            arr[k]["pts"] = pts
            n = min(len(bl), MAXBOX)
            arr[k]["n"] = n
            for j in range(n):
                arr[k]["box"][j] = bl[j]
        seq = struct.unpack_from("<I", bm, 4)[0]
        struct.pack_into("<I", bm, 4, seq + 1)
        bm[HDR:HDR + arr.nbytes] = arr.tobytes()
        struct.pack_into("<I", bm, 8, len(recs))
        struct.pack_into("<I", bm, 4, seq + 2)

    def set_pixel(on):
        S["pixel"] = on
        struct.pack_into("<I", bm, 12, 0 if on else 2)

    inq, outq = mp.Queue(maxsize=1), mp.Queue()
    fq, fout = mp.Queue(maxsize=1), mp.Queue()
    mp.Process(target=detector_proc, args=(inq, outq, cfg.own_zones), daemon=True).start()
    mp.Process(target=face_proc, args=(fq, fout, cfg.own_zones), daemon=True).start()
    ctx = {"per": [], "moto": []}
    TRK = {k: FlowTracker(0.5, min_hits=1, sure=sure, min_new=mn, hold=int(2.5 * TAP_FPS), back=int(5 * TAP_FPS), grey=lambda f: grays.get(f),
                          keep=int((DELAY + 8) * TAP_FPS)) for k, off, mg, cells, sure, mn in KINDS}
    recs = collections.OrderedDict()

    SPEED_GAIN = float(os.environ.get("SPEED_GAIN", "1.5"))
    def make_rec(idx):
        W, H = info["w"], info["h"]
        out = []
        for k, off, mg, cells, sure, mn in KINDS:
            idmap = {t["id"]: t for t in TRK[k].tracks} if SPEED_GAIN > 0 else {}
            for tid, b in TRK[k].boxes_with_id(idx):
                extra = 0
                if SPEED_GAIN > 0:
                    tr = idmap.get(tid)
                    prevb = tr["hist"].get(idx - 1) if tr else None
                    if prevb is not None:
                        sp = (((b[0] + b[2]) - (prevb[0] + prevb[2])) ** 2 + ((b[1] + b[3]) - (prevb[1] + prevb[3])) ** 2) ** 0.5 / 2
                        extra = min(60.0, SPEED_GAIN * sp)
                g = mosaic.grow(list(b[:4]), mg, cfg.margin_px + extra, W, H)
                out.append((off + tid, cells, g[0], g[1], g[2], g[3]))
        return out

    def tracker_worker():
        while not st["stop"]:
            ev = evq.get()
            S["trk_backlog"] = evq.qsize()
            if ev[0] == "f":
                _, idx, prev, cur = ev
                for t in TRK.values():
                    t.advance(idx, prev, cur)
                if S["paused"]:
                    for t in TRK.values():
                        for tr in t.tracks:
                            tr["last_det"] = idx
                recs[idx] = (pts_of[idx], make_rec(idx))
            elif ev[0] == "pf":
                _, idx, faces = ev
                TRK["face"].on_pass(idx, faces)
                for j in list(recs):
                    if j >= idx - 60:
                        recs[j] = (recs[j][0], make_rec(j))
            else:
                _, idx, dets = ev
                for k, t in TRK.items():
                    t.on_pass(idx, dets[k])
                for j in list(recs):
                    recs[j] = (recs[j][0], make_rec(j))
            while len(recs) > NREC:
                recs.popitem(last=False)
            lst = sorted(recs.values(), key=lambda x: x[0])
            publish(lst)
            S["recs"] = len(lst)
            S["boxes"] = len(lst[-1][1]) if lst else 0

    def results_thread():
        ema = 0.0
        while not st["stop"]:
            m = outq.get()
            if m[0] != "pass":
                continue
            _, i, r, dt = m
            st["busy"] = False
            st["pass_end"] = time.time()
            ema = dt if ema == 0 else 0.8 * ema + 0.2 * dt
            S["pass_s"] = round(ema, 2)
            S["passes"] += 1
            S["last_pass"] = i
            ctx.update(r.pop("ctx", {}))
            evq.put(("p", i, r))

    def faces_thread():
        while not st["stop"]:
            m = fout.get()
            if m[0] != "faces":
                continue
            st["fbusy"] = False
            _, i, faces = m
            keep = []
            for b in faces:
                cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                on_rider = any(p[0] <= cx <= p[2] and p[1] <= cy <= p[3] and any(
                    max(0, min(p[2], m2[2]) - max(p[0], m2[0])) * max(0, min(p[3], m2[3]) - max(p[1], m2[1])) > 0.3 * (p[2] - p[0]) * (p[3] - p[1]) and (p[1] + p[3]) / 2 < (m2[1] + m2[3]) / 2
                    for m2 in ctx["moto"]) for p in ctx["per"])
                if on_rider and b[4] < cfg.rider_face_conf:
                    continue
                keep.append(b)
            S["face_scans"] = S.get("face_scans", 0) + 1
            evq.put(("pf", i, keep))

    def cpu_temps():
        def rd():
            out = {}
            for l in open("/proc/stat"):
                if l.startswith("cpu") and l[3].isdigit():
                    f = list(map(int, l.split()[1:]))
                    out[int(l[3])] = (sum(f), f[3] + f[4])
            return out
        p = rd()
        while not st["stop"]:
            time.sleep(2)
            c = rd()
            for name, ids in (("cpu_klein", range(0, 4)), ("cpu_gross", range(4, 8))):
                tot = sum(c[i][0] - p[i][0] for i in ids)
                idle = sum(c[i][1] - p[i][1] for i in ids)
                S[name] = round(100 * (1 - idle / max(1, tot)), 1)
            p = c
            try:
                S["temp"] = max(int(open("/sys/class/thermal/thermal_zone%d/temp" % z).read()) / 1000 for z in range(7))
            except Exception:
                pass
            S["paused"] = S["cpu_gross"] > 85 or S["temp"] > 78
            err = S["temp"] - TEMP_TARGET
            S["slow"] = round(max(0.0, min(1.0, S["slow"] + (0.06 * err if err > 0 else 0.03 * err))), 2)
            S["warn"] = "Erkennung pausiert (Last/Temperatur): Rechtecke werden gehalten" if S["paused"] else ""

    def tap_reader():
        mm = None
        last_seq = -1
        idx = 0
        prev_gray = None
        while not st["stop"]:
            try:
                if mm is None:
                    fd = os.open(TAP, os.O_RDONLY)
                    sz = os.fstat(fd).st_size
                    if sz < 128:
                        os.close(fd)
                        time.sleep(0.2)
                        continue
                    mm = mmap.mmap(fd, sz, prot=mmap.PROT_READ)
                    os.close(fd)
                magic, seq, w, h, slot_size, last_slot = struct.unpack_from("<IIIIII", mm, 0)
                if magic != 0x50425458 or w == 0:
                    time.sleep(0.05)
                    continue
                if seq == last_seq:
                    time.sleep(0.01)
                    continue
                epoch = struct.unpack_from("<I", mm, 48)[0]
                if epoch != st.get("epoch", epoch):
                    with gl:
                        grays.clear(); pts_of.clear()
                    for t in TRK.values():
                        t.tracks.clear()
                    recs.clear()
                    prev_gray = None
                st["epoch"] = epoch
                if seq < last_seq:
                    with gl:
                        grays.clear(); pts_of.clear()
                    for t in TRK.values():
                        t.tracks.clear()
                    recs.clear()
                    prev_gray = None
                s1 = struct.unpack_from("<I", mm, 40 + 4 * last_slot)[0]
                pts = struct.unpack_from("<q", mm, 24 + 8 * last_slot)[0]
                off = 128 + last_slot * slot_size
                nv = np.frombuffer(mm, np.uint8, slot_size, off).copy()
                s2 = struct.unpack_from("<I", mm, 40 + 4 * last_slot)[0]
                if (s1 & 1) or s1 != s2:
                    continue
                last_seq = seq
                info["w"], info["h"] = w, h
                Y = nv[:w * h].reshape(h, w)
                gray = cv2.resize(Y, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
                with gl:
                    grays[idx] = gray
                    pts_of[idx] = pts
                    for k in [k for k in grays if k < idx - int((DELAY + 10) * TAP_FPS)]:
                        del grays[k]
                        pts_of.pop(k, None)
                if prev_gray is not None:
                    evq.put(("f", idx, prev_gray, gray))
                prev_gray = gray
                if not st["busy"] and not S["paused"] and time.time() - st["pass_end"] >= 4.0 * S["slow"]:
                    try:
                        bgr = cv2.cvtColorTwoPlane(Y, nv[w * h:].reshape(h // 2, w // 2, 2), cv2.COLOR_YUV2BGR_NV12)
                        inq.put_nowait((idx, bgr))
                        st["busy"] = True
                    except Exception:
                        pass
                if idx % FACE_EVERY == 0 and not st.get("fbusy") and not S["paused"] and time.time() - st["face_last"] >= 0.5 + 3.0 * S["slow"] - 0.05:
                    try:
                        bgr = cv2.cvtColorTwoPlane(Y, nv[w * h:].reshape(h // 2, w // 2, 2), cv2.COLOR_YUV2BGR_NV12)
                        fq.put_nowait((idx, cv2.resize(bgr, (1280, 720), interpolation=cv2.INTER_AREA)))
                        st["fbusy"] = True
                        st["face_last"] = time.time()
                    except Exception:
                        pass
                S["tap_frames"] = idx + 1
                idx += 1
            except FileNotFoundError:
                time.sleep(0.3)
            except Exception as e:
                S["warn"] = "Tap: %s" % e
                time.sleep(0.3)

    def src_elements():
        if key.startswith("/"):
            return ["filesrc", "location=" + key, "!", "tsdemux" if key.endswith(".ts") else "qtdemux", "!"]
        return ["rtmpsrc", "location=rtmp://127.0.0.1:1935/publish/" + key, "!", "flvdemux", "name=d", "d.video", "!"]

    def gst_cmd():
        qmax = str(int((DELAY + 5) * 1e9))
        c = ["gst-launch-1.0", "-q"] + src_elements() + ["h264parse", "!", "tee", "name=t",
             "t.", "!", "queue", "max-size-buffers=0", "max-size-bytes=0", "max-size-time=3000000000", "!", "mppvideodec", "!",
             "pbpixeltap", "shm=" + TAP, "interval-ms=%d" % (1000 // TAP_FPS), "!", "fakesink", "sync=false", "async=false",
             "t.", "!", "queue", "max-size-buffers=0", "max-size-bytes=0", "max-size-time=" + qmax, "min-threshold-time=%d" % int(DELAY * 1e9), "!", "mppvideodec", "!",
             "pbpixelmask", "boxes=" + BOX, "!", "mpph264enc", "bitrate=%d" % (RTMP_KBPS * 1000), "gop=60", "qos=false", "!", "tee", "name=o",
             "o.", "!", "queue", "!", "h264parse", "config-interval=-1", "!", "flvmux", "name=m", "streamable=true", "!",
             "rtmpsink", "location=rtmp://127.0.0.1:1935/publish/%s live=1" % RTMP_OUT, "sync=false",
             "audiotestsrc", "is-live=true", "wave=silence", "!", "audio/x-raw,rate=48000,channels=2", "!", "voaacenc", "bitrate=128000", "!", "aacparse", "!", "m.",
             ]
        if TS_PORT:
            c += ["o.", "!", "queue", "leaky=downstream", "max-size-buffers=90", "!", "h264parse", "config-interval=-1", "!", "video/x-h264,stream-format=byte-stream", "!",
                  "mpegtsmux", "!", "tcpserversink", "host=0.0.0.0", "port=%d" % TS_PORT, "sync=false"]
        return c

    def gst_runner():
        env = dict(os.environ)
        env.setdefault("GST_PLUGIN_PATH", os.path.expanduser("~/pb-privacy-test/gst"))
        while not st["stop"]:
            p = subprocess.Popen(gst_cmd(), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=open("/tmp/pbpixel_gst.log", "ab"))
            S["gst_pid"] = p.pid
            p.wait()
            S["warn"] = "Bildweg beendet, starte neu"
            time.sleep(2)

    def watchdog():
        last, since = -1, time.time()
        while not st["stop"]:
            time.sleep(3)
            if S["tap_frames"] != last:
                last, since = S["tap_frames"], time.time()
            elif time.time() - since > 12 and S.get("gst_pid"):
                S["warn"] = "Kein Bild vom Eingang seit 12 s: Bildweg wird neu gestartet"
                try:
                    os.kill(S["gst_pid"], 15)
                except Exception:
                    pass
                since = time.time()

    class Web(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/pixel"):
                set_pixel(not self.path.endswith("0"))
                body = ("Verpixelung " + ("an" if S["pixel"] else "aus")).encode()
                ct = "text/plain"
            else:
                body = json.dumps(S).encode()
                ct = "application/json"
            self.send_response(200); self.send_header("Content-Type", ct); self.end_headers(); self.wfile.write(body)

    ready = 0
    while ready < 2:
        if outq.empty() and fout.empty():
            time.sleep(0.1)
            continue
        if not outq.empty() and outq.get()[0] == "ready":
            ready += 1
        if not fout.empty() and fout.get()[0] == "fready":
            ready += 1
    for fn in ((results_thread, faces_thread, tracker_worker, cpu_temps, tap_reader) if NO_GST else (results_thread, faces_thread, tracker_worker, cpu_temps, tap_reader, gst_runner, watchdog)):
        threading.Thread(target=fn, daemon=True).start()
    print("pbpixel-Dienst läuft: Eingang %s, Verzögerung %.1f s, RTMP %s, TS tcp://<Box>:%d, Werte http://<Box>:%d/" % (key, DELAY, RTMP_OUT, TS_PORT, PORT), flush=True)
    try:
        ThreadingHTTPServer((os.environ.get("PB_BIND", "127.0.0.1"), PORT), Web).serve_forever()
    finally:
        st["stop"] = True
