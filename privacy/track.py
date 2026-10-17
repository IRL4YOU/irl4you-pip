

def _iou(a, b):
    ix1, iy1, ix2, iy2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    i = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    return i / max(1.0, (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i)


def _near(a, b):
    ca, cb = ((a[0] + a[2]) / 2, (a[1] + a[3]) / 2), ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    d = ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5
    return d < 1.2 * max(a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1])


def build(passes, kind, cfg, nframes, sure):
    tracks = []
    open_tr = []
    for fi, boxes in passes:
        used = set()
        nxt = []
        for tr in open_tr:
            best, bj = 0.0, None
            for j, b in enumerate(boxes):
                if j in used:
                    continue
                s = _iou(tr[-1][1], b)
                if s > best or (best == 0.0 and _near(tr[-1][1], b)):
                    best, bj = max(s, 0.01), j
            if bj is not None:
                used.add(bj)
                tr.append((fi, boxes[bj]))
                nxt.append(tr)
            else:
                nxt.append(tr)
        for j, b in enumerate(boxes):
            if j not in used:
                tr = [(fi, b)]
                tracks.append(tr)
                nxt.append(tr)
        open_tr = [t for t in nxt if t[-1][0] == fi]
    out = [[] for _ in range(nframes)]
    for tr in tracks:
        if len(tr) < cfg.min_hits and max(b[4] for _, b in tr) < sure:
            continue
        first, last = tr[0][0], tr[-1][0]
        for k in range(len(tr) - 1):
            (f0, b0), (f1, b1) = tr[k], tr[k + 1]
            for f in range(f0, f1 + 1):
                t = (f - f0) / max(1, f1 - f0)
                out[f].append([b0[i] + (b1[i] - b0[i]) * t for i in range(4)])
        b0 = tr[0][1]
        v0 = [0.0] * 4
        if cfg.back_extrap and len(tr) >= 2:
            (fa, ba), (fb, bb) = tr[0], tr[1]
            v0 = [(bb[i] - ba[i]) / max(1, fb - fa) for i in range(4)]
        for f in range(max(0, first - cfg.back_frames), first):
            k = first - f
            r = [b0[i] - v0[i] * k for i in range(4)]
            if r[2] - r[0] < 0.6 * (b0[2] - b0[0]) or r[3] - r[1] < 0.6 * (b0[3] - b0[1]):
                r = [b0[0] - v0[0] * k, b0[1] - v0[1] * k, b0[0] - v0[0] * k + 0.6 * (b0[2] - b0[0]), b0[1] - v0[1] * k + 0.6 * (b0[3] - b0[1])]
            out[f].append(r)
        b1 = tr[-1][1]
        v1 = [0.0] * 4
        if cfg.back_extrap and len(tr) >= 2:
            (fa, ba), (fb, bb) = tr[-2], tr[-1]
            v1 = [(bb[i] - ba[i]) / max(1, fb - fa) for i in range(4)]
        for f in range(last + 1, min(nframes, last + 1 + cfg.hold_frames)):
            k = f - last
            r = [b1[i] + v1[i] * k for i in range(4)]
            if r[2] - r[0] < 0.6 * (b1[2] - b1[0]) or r[3] - r[1] < 0.6 * (b1[3] - b1[1]):
                r = list(b1[:4])
            out[f].append(r)
        if len(tr) == 1:
            out[first].append(list(tr[0][1][:4]))
    return out
