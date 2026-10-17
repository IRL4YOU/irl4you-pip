import cv2
import numpy as np


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    i = ix * iy
    return i / max(1.0, (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i)


def _near(a, b):
    ca, cb = ((a[0] + a[2]) / 2, (a[1] + a[3]) / 2), ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    return ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5 < 0.8 * max(a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1])


LK = dict(winSize=(15, 15), maxLevel=2, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03))


def step(prev, cur, box, scale, vel, pts=None, age=0):
    H, W = prev.shape[:2]
    bw, bh = box[2] - box[0], box[3] - box[1]
    pad = int(0.6 * max(bw, bh) * scale) + 24
    X0, Y0 = max(0, int(box[0] * scale) - pad), max(0, int(box[1] * scale) - pad)
    X1, Y1 = min(W, int(box[2] * scale) + pad), min(H, int(box[3] * scale) + pad)
    nb = None
    newpts = None
    if X1 - X0 >= 16 and Y1 - Y0 >= 16:
        pc, cc = prev[Y0:Y1, X0:X1], cur[Y0:Y1, X0:X1]
        off = np.array([[X0, Y0]], np.float32)
        p0 = None if pts is None else (pts - off)
        if p0 is None or len(p0) < 6 or age % 8 == 0:
            m = 0.25
            x0, y0 = int(max(0, (box[0] - m * bw) * scale - X0)), int(max(0, (box[1] - m * bh) * scale - Y0))
            x1, y1 = int(min(X1 - X0, (box[2] + m * bw) * scale - X0)), int(min(Y1 - Y0, (box[3] + m * bh) * scale - Y0))
            p0 = None
            if x1 - x0 >= 6 and y1 - y0 >= 6:
                q = cv2.goodFeaturesToTrack(pc[y0:y1, x0:x1], maxCorners=20, qualityLevel=0.01, minDistance=2, blockSize=3)
                if q is not None and len(q) >= 4:
                    p0 = (q + np.array([[x0, y0]], np.float32)).reshape(-1, 1, 2)
        if p0 is not None:
            p1, st, _ = cv2.calcOpticalFlowPyrLK(pc, cc, p0, None, **LK)
            ok = st.ravel() == 1
            if ok.sum() >= 4:
                a, b = p0[ok].reshape(-1, 2), p1[ok].reshape(-1, 2)
                d = b - a
                med = np.median(d, axis=0)
                keep = np.linalg.norm(d - med, axis=1) < max(2.0, 0.15 * max(bw, bh) * scale)
                if keep.sum() >= 4:
                    a, b = a[keep], b[keep]
                    d = np.median(b - a, axis=0)
                    sa = np.std(a, axis=0).mean()
                    sb = np.std(b, axis=0).mean()
                    s = float(np.clip(sb / sa, 0.92, 1.08)) if sa > 1.0 else 1.0
                    cx, cy = (box[0] + box[2]) / 2 + d[0] / scale, (box[1] + box[3]) / 2 + d[1] / scale
                    nw, nh = bw * s, bh * s
                    nb = [cx - nw / 2, cy - nh / 2, cx + nw / 2, cy + nh / 2]
                    vel = (0.7 * vel[0] + 0.3 * d[0] / scale, 0.7 * vel[1] + 0.3 * d[1] / scale)
                    newpts = (b + off).reshape(-1, 1, 2).astype(np.float32)
    if nb is None:
        vx, vy = vel
        nb = [box[0] + vx, box[1] + vy, box[2] + vx, box[3] + vy]
        vel = (0.9 * vx, 0.9 * vy)
    return nb, vel, newpts


class FlowTracker:
    def __init__(self, scale, min_hits=2, sure=0.35, hold=90, back=150, grey=None, keep=450, min_new=0.0):
        self.scale, self.min_hits, self.sure, self.hold, self.back = scale, min_hits, sure, hold, back
        self.grey = grey
        self.tracks = []
        self._id = 0
        self.latest = -1
        self.min_new = min_new
        self.keep = keep

    def _next_id(self):
        self._id += 1
        return self._id

    def advance(self, f, prev, cur):
        self.latest = f
        alive = []
        for t in self.tracks:
            if f - t["last_det"] > self.hold + self.keep:
                continue
            if f - 1 in t["hist"] and f - t["last_det"] <= self.hold:
                nb, t["vel"], t["pts"] = step(prev, cur, t["hist"][f - 1], self.scale, t["vel"], t["pts"], f)
                t["hist"][f] = nb
            alive.append(t)
        self.tracks = alive

    def _walk(self, t, f0, direction, stop):
        f = f0
        box, vel, pts = t["hist"][f0], (0.0, 0.0), None
        while f != stop:
            g0, g1 = self.grey(f), self.grey(f + direction)
            if g0 is None or g1 is None:
                break
            box, vel, pts = step(g0, g1, box, self.scale, vel, pts, f)
            f += direction
            t["hist"][f] = box

    def on_pass(self, p, dets):
        used = set()
        cand = []
        for t in self.tracks:
            tb = t["hist"].get(p)
            if tb is None:
                continue
            for j, d in enumerate(dets):
                s = _iou(tb, d)
                if s > 0.1 or _near(tb, d):
                    cand.append((s, id(t), j, t))
        cand.sort(key=lambda c: -c[0])
        matched = set()
        for s, tid, j, t in cand:
            if j in used or tid in matched:
                continue
            used.add(j)
            matched.add(tid)
            d = dets[j]
            tb = t["hist"][p]
            delta = [d[i] - tb[i] for i in range(4)]
            for f in [k for k in t["hist"] if k >= p]:
                t["hist"][f] = [t["hist"][f][i] + delta[i] for i in range(4)]
            t["hits"] += 1
            t["score"] = max(t["score"], d[4])
            t["last_det"] = p
        for j, d in enumerate(dets):
            if j in used or d[4] < self.min_new:
                continue
            t = {"hist": {p: list(d[:4])}, "hits": 1, "score": d[4], "last_det": p, "vel": (0.0, 0.0), "pts": None, "first": p, "id": self._next_id()}
            self.tracks.append(t)
            self._walk(t, p, -1, p - self.back)
            self._walk(t, p, +1, self.latest)
            t["first"] = min(t["hist"])
            t["pts"] = None

    def boxes_at(self, f):
        out = []
        for t in self.tracks:
            if t["hits"] < self.min_hits and t["score"] < self.sure:
                continue
            b = t["hist"].get(f)
            if b is not None and f <= t["last_det"] + self.hold:
                out.append(b)
        return out

    def boxes_with_id(self, f):
        out = []
        for t in self.tracks:
            if t["hits"] < self.min_hits and t["score"] < self.sure:
                continue
            b = t["hist"].get(f)
            if b is not None and f <= t["last_det"] + self.hold:
                out.append((t["id"], b))
        return out
