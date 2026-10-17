import numpy as np
import cv2

VEH, PER, MOTO = {2, 3, 5, 7}, {0}, 3


_GRIDS = {}


def _grids(size=640):
    if size in _GRIDS:
        return _GRIDS[size]
    g, s = [], []
    for st in (8, 16, 32):
        n = size // st
        yv, xv = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
        g.append(np.stack([xv, yv], -1).reshape(-1, 2))
        s.append(np.full((n * n, 1), st))
    _GRIDS[size] = (np.concatenate(g).astype(np.float32), np.concatenate(s).astype(np.float32))
    return _GRIDS[size]


def nms(items, thr=0.5, minside=False):
    items = sorted(items, key=lambda b: -b[4])
    keep = []
    for b in items:
        ok = True
        for k in keep:
            ix1, iy1, ix2, iy2 = max(b[0], k[0]), max(b[1], k[1]), min(b[2], k[2]), min(b[3], k[3])
            inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            a, c = (b[2] - b[0]) * (b[3] - b[1]), (k[2] - k[0]) * (k[3] - k[1])
            if inter / max(1.0, min(a, c) if minside else (a + c - inter)) > thr:
                ok = False
                break
        if ok:
            keep.append(b)
    return keep


def _letterbox(img, size):
    h, w = img.shape[:2]
    r = min(size / h, size / w)
    nh, nw = max(1, int(h * r)), max(1, int(w * r))
    pad = np.full((size, size, 3), 114, np.uint8)
    pad[:nh, :nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_CUBIC if r > 1 else cv2.INTER_AREA)
    return pad, r


def _decode_yolox(o, r, ox, oy, conf, size=640):
    GRID, STRIDE = _grids(size)
    xy = (o[:, :2] + GRID) * STRIDE
    wh = np.exp(o[:, 2:4]) * STRIDE
    sc = o[:, 4:5] * o[:, 5:]
    cls = sc.argmax(1)
    cs = sc.max(1)
    m = (cs > conf) & np.isin(cls, list(VEH | PER))
    out = []
    for (cx, cy), (bw, bh), c, s in zip(xy[m], wh[m], cls[m], cs[m]):
        out.append([(cx - bw / 2) / r + ox, (cy - bh / 2) / r + oy, (cx + bw / 2) / r + ox, (cy + bh / 2) / r + oy, float(s), int(c)])
    return out


def _decode_plate(o, r, ox, oy, conf):
    p = o.T
    sc = p[:, 4]
    m = sc > conf
    if not m.any():
        return []
    b, s = p[m, :4], sc[m]
    xyxy = np.stack([b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2, b[:, 0] + b[:, 2] / 2, b[:, 1] + b[:, 3] / 2], 1) / r
    return nms([[x[0] + ox, x[1] + oy, x[2] + ox, x[3] + oy, float(c)] for x, c in zip(xyxy, s)], 0.45)


def _inter_frac(a, b):
    ix1, iy1, ix2, iy2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return max(0, ix2 - ix1) * max(0, iy2 - iy1) / max(1.0, (a[2] - a[0]) * (a[3] - a[1]))


class Detector:
    def __init__(self, backend, cfg):
        self.b, self.cfg = backend, cfg
        self.ysz, self.psz = getattr(backend, "sizes", (640, 608))
        self.fd = cv2.FaceDetectorYN.create(backend.face_model, "", (320, 320), cfg.face_conf, 0.3, 50)

    def _own(self, box, W, H):
        cx, cy = (box[0] + box[2]) / 2 / W, (box[1] + box[3]) / 2 / H
        return any(z[0] <= cx <= z[2] and z[1] <= cy <= z[3] for z in self.cfg.own_zones)

    def objects(self, img):
        cfg = self.cfg
        H, W = img.shape[:2]
        jobs = []
        pad, r = _letterbox(img, self.ysz)
        jobs.append((pad, r, 0, 0))
        if cfg.obj_tiles:
            t, s = self.ysz, int(self.ysz * 0.75)
            xs = list(range(0, max(1, W - t + 1), s)); ys = list(range(0, max(1, H - t + 1), s))
            if xs[-1] != W - t: xs.append(W - t)
            if ys[-1] != H - t: ys.append(H - t)
            ys = [y for y in ys if y + t > cfg.obj_tile_top * H] or ys[-1:]
            for y in ys:
                for x in xs:
                    jobs.append((np.ascontiguousarray(img[y:y + t, x:x + t]), 1.0, x, y))
        raws = self.b.yolox([j[0] for j in jobs])
        objs = []
        for (tile, r, ox, oy), raw in zip(jobs, raws):
            objs += _decode_yolox(raw, r, ox, oy, cfg.veh_conf, self.ysz)
        veh = nms([o for o in objs if o[5] in VEH], 0.6, True)
        per = nms([o for o in objs if o[5] in PER], 0.6, True)
        veh = [v for v in veh if not self._own(v, W, H)]
        per = [p for p in per if not self._own(p, W, H)]
        veh = sorted(veh, key=lambda v: -(v[2] - v[0]) * (v[3] - v[1]))[:cfg.max_vehicles]
        per = sorted(per, key=lambda p: -(p[2] - p[0]) * (p[3] - p[1]))[:cfg.max_persons]
        return veh, per

    def plates(self, img, veh):
        cfg = self.cfg
        H, W = img.shape[:2]
        crops, meta = [], []
        for v in veh:
            bw, bh = v[2] - v[0], v[3] - v[1]
            S = int(min(max(cfg.plate_window_min, 2.2 * max(bw, bh)), cfg.plate_window_max, W, H))
            if max(bw, bh) <= 0.9 * S:
                spots = [((v[0] + v[2]) / 2, (v[1] + v[3]) / 2)]
            else:
                step = 0.7 * S
                xs = np.arange(max(v[0], 0) + S / 2 - 0.15 * S, max(v[2], 0) + 0.15 * S, step)
                xs = [min(max(x, S / 2), W - S / 2) for x in xs] or [(v[0] + v[2]) / 2]
                ys = [min(max(v[3] - 0.45 * S, S / 2), H - S / 2)]
                spots = [(x, y) for x in dict.fromkeys(xs) for y in ys][:4]
            for cx, cy in spots:
                x1 = int(min(max(0, cx - S / 2), W - S))
                y1 = int(min(max(0, cy - S / 2), H - S))
                pad, r = _letterbox(img[y1:y1 + S, x1:x1 + S], self.psz)
                crops.append(cv2.cvtColor(pad, cv2.COLOR_BGR2RGB))
                meta.append((r, x1, y1, v))
        res = []
        for (r, ox, oy, v), raw in zip(meta, self.b.plates(crops)):
            bw, bh = v[2] - v[0], v[3] - v[1]
            for p in _decode_plate(raw, r, ox, oy, cfg.plate_conf):
                cx, cy = (p[0] + p[2]) / 2, (p[1] + p[3]) / 2
                inside = v[0] - 0.25 * bw <= cx <= v[2] + 0.25 * bw and v[1] - 0.10 * bh <= cy <= v[3] + 0.20 * bh
                if inside and (p[2] - p[0]) >= cfg.min_plate_w and (p[3] - p[1]) >= cfg.min_plate_h and not self._own(p, W, H):
                    res.append(p)
        return nms(res, 0.3, True)

    def scan(self, img, veh=()):
        cfg = self.cfg
        H0, W0 = img.shape[:2]
        S, st = self.psz, int(self.psz * 0.75)
        crops, meta = [], []
        for sc, top, bot in cfg.scan_levels:
            g = img if sc == 1.0 else cv2.resize(img, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA if sc < 1.0 else cv2.INTER_CUBIC)
            H, W = g.shape[:2]
            y_lo, y_hi = int(top * H), int(bot * H)
            ys = list(range(max(0, min(y_lo, H - S)), max(1, min(y_hi, H) - S + 1), st))
            if not ys or ys[-1] + S < min(y_hi, H):
                ys.append(min(max(0, y_hi - S), H - S))
            xs = list(range(0, W - S + 1, st))
            if xs[-1] != W - S:
                xs.append(W - S)
            for y in dict.fromkeys(ys):
                for x in xs:
                    pad, r = _letterbox(g[y:y + S, x:x + S], self.psz)
                    crops.append(cv2.cvtColor(pad, cv2.COLOR_BGR2RGB))
                    meta.append((r, x, y, sc))
        res = []
        for (r, ox, oy, sc), raw in zip(meta, self.b.plates(crops)):
            for p in _decode_plate(raw, r, ox, oy, cfg.scan_conf):
                q = [p[0] / sc, p[1] / sc, p[2] / sc, p[3] / sc, p[4]]
                if (q[2] - q[0]) >= cfg.min_plate_w and (q[3] - q[1]) >= cfg.min_plate_h and not self._own(q, W0, H0):
                    cx, cy = (q[0] + q[2]) / 2, (q[1] + q[3]) / 2
                    near = any(v[0] - 0.3 * (v[2] - v[0]) <= cx <= v[2] + 0.3 * (v[2] - v[0]) and v[1] - 0.3 * (v[3] - v[1]) <= cy <= v[3] + 0.3 * (v[3] - v[1]) for v in veh)
                    if near or q[4] >= cfg.scan_free_conf:
                        res.append(q)
        return nms(res, 0.3, True)

    def faces(self, img, per, veh):
        cfg = self.cfg
        H, W = img.shape[:2]
        motos = [v for v in veh if v[5] == MOTO]
        faces, heads = [], []
        for p in per:
            pw, ph = p[2] - p[0], p[3] - p[1]
            rider = any(_inter_frac(p, m) > 0.3 and (p[1] + p[3]) / 2 < (m[1] + m[3]) / 2 for m in motos)
            hx1, hy1, hx2, hy2 = int(max(0, p[0] - .1 * pw)), int(max(0, p[1] - .05 * ph)), int(min(W, p[2] + .1 * pw)), int(min(H, p[1] + .38 * ph))
            if hx2 - hx1 < 6 or hy2 - hy1 < 6:
                continue
            crop = img[hy1:hy2, hx1:hx2]
            sc = min(960 / max(crop.shape[:2]), max(1.0, 320 / max(crop.shape[:2])))
            if sc != 1.0:
                crop = cv2.resize(crop, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
            self.fd.setInputSize((crop.shape[1], crop.shape[0]))
            _, fs = self.fd.detect(crop)
            found = False
            need = cfg.rider_face_conf if rider else cfg.face_conf
            if fs is not None:
                for f in fs:
                    if f[14] >= need:
                        faces.append([f[0] / sc + hx1, f[1] / sc + hy1, (f[0] + f[2]) / sc + hx1, (f[1] + f[3]) / sc + hy1, float(f[14])])
                        found = True
            if not found and not rider and 0.2 * ph >= cfg.min_head_px:
                heads.append([p[0] + .2 * pw, p[1], p[2] - .2 * pw, p[1] + .2 * ph, float(p[4])])
        return nms(faces, 0.4, True), heads

    def scan_faces(self, img, per=(), veh=()):
        cfg = self.cfg
        H, W = img.shape[:2]
        motos = [v for v in veh if v[5] == MOTO]
        self.fd.setInputSize((W, H))
        _, fs = self.fd.detect(img)
        out = []
        if fs is not None:
            for f in fs:
                if f[14] >= cfg.face_scan_conf and f[2] >= cfg.min_face_px and f[3] >= cfg.min_face_px:
                    b = [float(f[0]), float(f[1]), float(f[0] + f[2]), float(f[1] + f[3]), float(f[14])]
                    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                    on_rider = any(p[0] <= cx <= p[2] and p[1] <= cy <= p[3] and any(_inter_frac(p, m) > 0.3 and (p[1] + p[3]) / 2 < (m[1] + m[3]) / 2 for m in motos) for p in per)
                    if on_rider and b[4] < cfg.rider_face_conf:
                        continue
                    if not self._own(b, W, H):
                        out.append(b)
        return out

    def run(self, img):
        veh, per = self.objects(img)
        plates = nms(self.plates(img, [v for v in veh]) + self.scan(img, veh), 0.3, True)
        faces, heads = self.faces(img, per, veh)
        if self.cfg.face_scan:
            faces = nms(faces + self.scan_faces(img, per, veh), 0.4, True)
        if not self.cfg.head_fallback:
            heads = []
        return {"veh": veh, "per": per, "plate": plates, "face": faces, "head": heads}
