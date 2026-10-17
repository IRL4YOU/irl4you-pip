import collections
import os
import re
import time

STALE_S = 15.0
START_GRACE_S = 30.0
DSTATE_S = 12.0
FEED_DEAD_S = 25.0
SOFT_WINDOW_S = 300.0
COOLDOWN_S = 90.0
MAX_PER_HOUR = 4
REPORT_KEEP = 6
REPORT_MARK = "===== HÄNGER "
REPORT_FILE = "hang-diagnose.txt"

FPS = re.compile(r"\b(in|dmx|dec|vr)_f(\d)=([0-9.]+)/")
FEED = re.compile(r"\bfeed_sbf(\d)=(\d)")


def parse_live(text):
    out = {"in": {}, "dmx": {}, "dec": {}, "vr": {}, "feed": {}}
    for m in FPS.finditer(text or ""):
        out[m.group(1)][int(m.group(2))] = float(m.group(3))
    for m in FEED.finditer(text or ""):
        out["feed"][int(m.group(1))] = int(m.group(2))
    return out


class HangWatch:

    def __init__(self, clock=time.monotonic, wall=time.time):
        self.clock, self.wall = clock, wall
        self.started = None
        self.dead = {}
        self.dstate_since = None
        self.acted = collections.deque()
        self.soft = {}

    def started_now(self):
        self.started = self.clock()
        self.soft = {}
        self.dead = {}
        self.dstate_since = None

    def check(self, stats_mtime, line, expected, d_threads=0):
        now = self.clock()
        if self.started is None or now - self.started < START_GRACE_S:
            return None
        if d_threads > 0:
            self.dstate_since = self.dstate_since if self.dstate_since is not None else now
            if now - self.dstate_since >= DSTATE_S:
                return {"kind": "dstate", "slot": None, "text": "%d Thread(s) von belacoder hängen seit %d s im Kernel (Zustand D)" % (d_threads, int(now - self.dstate_since))}
        else:
            self.dstate_since = None
        if stats_mtime is None or self.wall() - stats_mtime > STALE_S:
            self.dead = {}
            age = "fehlt" if stats_mtime is None else "%d s alt" % int(self.wall() - stats_mtime)
            return {"kind": "stats", "slot": None, "text": "Statistik von belacoder steht still (%s)" % age}
        v = parse_live(line)
        for slot in list(self.dead):
            if slot not in expected:
                del self.dead[slot]
        for slot in expected:
            fps, dmx = v["in"].get(slot), v["dmx"].get(slot)
            if fps is not None and fps < 1.0 and (dmx is None or dmx < 1.0):
                self.dead.setdefault(slot, now)
            else:
                self.dead.pop(slot, None)
        for slot, since in sorted(self.dead.items()):
            if now - since >= FEED_DEAD_S:
                return {"kind": "feed", "slot": slot, "text": "Platz %d: Kamera sendet, aber seit %d s kommen keine Bilder im Mischer an" % (slot, int(now - since))}
        return None

    def soft_ok(self, hit):
        if hit.get("kind") != "feed" or hit.get("slot") is None or set(self.dead) != {hit["slot"]}:
            return False
        t = self.soft.get(hit["slot"])
        return t is None or self.clock() - t > SOFT_WINDOW_S

    def soft_done(self, slot):
        self.soft[slot] = self.clock()
        self.dead.pop(slot, None)

    def allow(self):
        now = self.clock()
        while self.acted and now - self.acted[0] > 3600:
            self.acted.popleft()
        if self.acted and now - self.acted[-1] < COOLDOWN_S:
            return False
        return len(self.acted) < MAX_PER_HOUR

    def acted_now(self):
        self.acted.append(self.clock())
        self.dead = {}
        self.dstate_since = None


def _read(path, limit=400):
    try:
        with open(path, errors="replace") as f:
            return f.read(limit).strip()
    except OSError:
        return ""


def _thread_sample(pid, proc):
    out = {}
    base = f"{proc}/{pid}/task"
    try:
        tids = sorted(os.listdir(base), key=int)
    except (OSError, ValueError):
        return out
    for tid in tids:
        stat = _read(f"{base}/{tid}/stat", 1000)
        m = re.match(r"^\d+ \((.*)\) (\S) (.*)$", stat)
        if not m:
            continue
        f = m.group(3).split()
        try:
            cpu = int(f[11]) + int(f[12])
        except (IndexError, ValueError):
            cpu = 0
        out[tid] = {"name": m.group(1), "state": m.group(2), "cpu": cpu, "wchan": _read(f"{base}/{tid}/wchan", 80) or "-"}
    return out


def d_state_threads(pid, proc="/proc"):
    return sum(1 for t in _thread_sample(pid, proc).values() if t["state"] == "D")


def blocked_tasks(proc="/proc", limit=12):
    rows = []
    try:
        pids = [d for d in os.listdir(proc) if d.isdigit()]
    except OSError:
        pids = []
    for pid in pids:
        base = f"{proc}/{pid}/task"
        try:
            tids = os.listdir(base)
        except OSError:
            continue
        for tid in tids:
            stat = _read(f"{base}/{tid}/stat", 1000)
            m = re.match(r"^\d+ \((.*)\) (\S) ", stat)
            if not m or m.group(2) != "D":
                continue
            stack = [re.sub(r"^\[<[0-9a-f]+>\]\s*", "", x.strip()) for x in _read(f"{base}/{tid}/stack", 2000).splitlines()[:6]]
            rows.append("  %s (%s): %s%s" % (m.group(1), tid, _read(f"{base}/{tid}/wchan", 80) or "-",
                                          ("  Stapel: " + " < ".join(x for x in stack if x)) if any(stack) else ""))
            if len(rows) >= limit:
                break
    blocked = re.search(r"^procs_blocked (\d+)", _read(f"{proc}/stat", 4000) + "\n", re.M)
    head = "procs_blocked laut /proc/stat: %s" % (blocked.group(1) if blocked else "?")
    return head + ("\n" + "\n".join(rows) if rows else "\n  (kein Thread im Zustand D)")


def thread_table(pid, proc="/proc", sleep=time.sleep, interval=1.0):
    a = _thread_sample(pid, proc)
    sleep(interval)
    b = _thread_sample(pid, proc)
    rows = ["TID      Zustand  CPU-Ticks  Wartestelle                      Name"]
    for tid, t in sorted(b.items(), key=lambda kv: (kv[1]["name"], int(kv[0]))):
        d = t["cpu"] - a.get(tid, {}).get("cpu", t["cpu"])
        rows.append("%-8s %-8s %-10d %-32s %s" % (tid, t["state"], d, t["wchan"][:32], t["name"]))
    return "\n".join(rows)


def build_report(hit, pid, uptime_s, live_text, events, slots, proc="/proc", sleep=time.sleep, wall=time.time):
    parts = [
        "%s%s UTC · %s" % (REPORT_MARK, time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(wall())), hit["text"]),
        "Art: %s | belacoder läuft seit %d s" % (hit["kind"], int(uptime_s)),
        "",
        "Status von belacoder (%s):" % proc.rstrip("/"),
    ]
    for line in _read(f"{proc}/{pid}/status", 4000).splitlines():
        if re.match(r"^(Name|State|Threads|voluntary_ctxt_switches|nonvoluntary_ctxt_switches|VmRSS):", line):
            parts.append("  " + line)
    parts += ["", "Threads (Messung über 1 s):", thread_table(pid, proc, sleep), "", "Threads im Zustand D (ganzes System):", blocked_tasks(proc), "", "Plätze (was nginx meldet und was der Regler glaubt):"]
    for slot, pub, state in slots:
        parts.append("  Platz %s: %s | Zweig-Zustand %s (1 gestoppt, 2 läuft ohne Bild, 3 Bilder kommen)" % (slot, pub, state))
    parts += ["", "Statistik von belacoder (erste Zeilen):", live_text.strip()[:6000], "", "Letzte Ereignisse:"]
    parts += ["  " + e for e in events[-25:]]
    return "\n".join(parts) + "\n"


def write_report(directory, text, keep=REPORT_KEEP, name=REPORT_FILE, limit=60000):
    path = os.path.join(directory, name)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            old = f.read()
    except OSError:
        old = ""
    blocks = [REPORT_MARK + b for b in old.split(REPORT_MARK) if b.strip()]
    blocks.append(text)
    blocks = blocks[-keep:]
    data = "".join(b if b.endswith("\n") else b + "\n" for b in blocks)
    if len(data) > limit:
        data = data[-limit:]
    try:
        os.makedirs(directory, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        return True
    except OSError:
        return False
