"""Tasten der Bluetooth-Controller: lesen und mit Funktionen der Box verbinden (läuft in der Oberfläche, server.py).

Ein gekoppelter Controller (siehe controllers.py, Liste in controllers.json) legt unter Linux ein Eingabegerät an (/dev/input/eventN), dessen
`Uniq` seine Bluetooth-Adresse ist. Dieses Modul sucht die Geräte der gekoppelten Controller (/proc/bus/input/devices), liest die Tastendrücke und
führt die zugeordnete Funktion aus. Welche Tasten ein Controller kann, steht in den Fähigkeiten des Geräts; sie werden gemerkt, damit die Liste
auch erscheint, wenn der Controller gerade aus ist. Zuordnung und Tastenliste liegen in <state>/controller-keys.json.
"""
import json
import os
import re
import select
import struct
import threading
import time

EVENT = struct.Struct("llHHi")           # struct input_event (64 Bit): Zeit, Art, Code, Wert
EV_KEY = 1
EV_ABS = 3
VIRTUAL_MAX = 0x310                      # Codes ab 0x300 sind keine Tasten des Geräts, sondern Achsen, die wie Tasten gelten (Trigger, Steuerkreuz)
VK_LT, VK_RT, VK_UP, VK_DOWN, VK_LEFT, VK_RIGHT = 0x300, 0x301, 0x302, 0x303, 0x304, 0x305
# Xbox- und andere Gamepads melden LT/RT und das Steuerkreuz als Achsen: ABS_Z/ABS_BRAKE, ABS_RZ/ABS_GAS, ABS_HAT0X/ABS_HAT0Y
TRIGGERS = {2: VK_LT, 10: VK_LT, 5: VK_RT, 9: VK_RT}
HATS = {16: (VK_LEFT, VK_RIGHT), 17: (VK_UP, VK_DOWN)}
TRIGGER_AT = 0.4                         # ein Trigger gilt ab 40 % Weg als gedrückt
# übliche Belegung eines Gamepads (Xbox): gezeigt, solange der Controller noch nie verbunden war und seine Tasten unbekannt sind
GAMEPAD = (0x130, 0x131, 0x133, 0x134, 0x136, 0x137, VK_LT, VK_RT, 0x13A, 0x13B, 0x13C, 0x13D, 0x13E, VK_UP, VK_DOWN, VK_LEFT, VK_RIGHT)
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
DEMO_ADDR = "E4:11:22:33:44:55"
DEBOUNCE = 0.25                          # Sekunden: ein Druck zählt einmal, auch wenn das Gerät ihn doppelt meldet

# Funktionen, die sich an eine Taste legen lassen (Kennung -> Text der Oberfläche)
FUNCTIONS = (("main", "Hauptbild"), ("pip1", "Kleines Bild 1"), ("pip2", "Kleines Bild 2"), ("pip3", "Kleines Bild 3"),
             ("mute", "Aktuelle Tonquelle stumm schalten"), ("audio_next", "Zur nächsten Tonquelle wechseln"))
FUNCTION_IDS = tuple(k for k, _ in FUNCTIONS)

NAMES = {                                # Namen wie auf einer Tastatur oder einem Gamepad, in allen Sprachen gleich
    0x130: "A", 0x131: "B", 0x132: "C", 0x133: "X", 0x134: "Y", 0x135: "Z", 0x136: "LB", 0x137: "RB", 0x138: "LT", 0x139: "RT",
    0x13A: "Select", 0x13B: "Start", 0x13C: "Mode", 0x13D: "L3", 0x13E: "R3",
    0x220: "D-pad up", 0x221: "D-pad down", 0x222: "D-pad left", 0x223: "D-pad right",
    1: "Esc", 14: "Backspace", 15: "Tab", 28: "Enter", 29: "Ctrl left", 42: "Shift left", 54: "Shift right", 56: "Alt left", 57: "Space",
    58: "Caps Lock", 96: "Keypad Enter", 97: "Ctrl right", 100: "Alt right", 102: "Home", 103: "Up", 104: "Page up",
    105: "Left", 106: "Right", 107: "End", 108: "Down", 109: "Page down", 110: "Insert", 111: "Delete", 113: "Mute",
    114: "Volume down", 115: "Volume up", 116: "Power", 119: "Pause", 128: "Stop", 139: "Menu", 158: "Back", 159: "Forward", 163: "Next track",
    164: "Play/Pause", 165: "Previous track", 166: "Stop (media)", 167: "Record", 168: "Rewind", 172: "Home page", 173: "Refresh",
    207: "Play", 208: "Fast forward", 212: "Camera", 0x160: "OK", 0x161: "Select", 0x18E: "Red", 0x18F: "Green", 0x190: "Yellow", 0x191: "Blue",
    0x192: "Channel up", 0x193: "Channel down",
    VK_LT: "LT", VK_RT: "RT", VK_UP: "D-pad up", VK_DOWN: "D-pad down", VK_LEFT: "D-pad left", VK_RIGHT: "D-pad right",
}
for _i, _c in enumerate("1234567890"):
    NAMES[2 + _i] = _c
for _i, _c in enumerate("QWERTYUIOP"):
    NAMES[16 + _i] = _c
for _i, _c in enumerate("ASDFGHJKL"):
    NAMES[30 + _i] = _c
for _i, _c in enumerate("ZXCVBNM"):
    NAMES[44 + _i] = _c
for _i in range(10):
    NAMES[59 + _i] = "F%d" % (_i + 1)
NAMES[87], NAMES[88] = "F11", "F12"
for _i in range(1, 17):
    NAMES[0x2C0 + _i - 1] = "Button %d" % _i                  # BTN_TRIGGER_HAPPY1 ...
for _i in range(10):
    NAMES[0x100 + _i] = "Button %d" % _i                      # BTN_0 ... BTN_9


def trigger_ranges(fd):
    """Größter Wert der Trigger-Achsen eines Eingabegeräts (EVIOCGABS), {} wenn das nicht geht (kein Linux, keine Achse)."""
    out = {}
    try:
        import fcntl
    except ImportError:
        return out
    for code in TRIGGERS:
        try:
            buf = bytearray(24)
            fcntl.ioctl(fd, 0x80000000 | (24 << 16) | (ord("E") << 8) | (0x40 + code), buf)
            top = struct.unpack_from("iiiiii", buf)[2]
            if top > 0:
                out[code] = top
        except OSError:
            pass
    return out


def virtual_keys(abs_codes):
    """Achsen eines Geräts, die wie Tasten behandelt werden: LT/RT aus den Trigger-Achsen, Steuerkreuz aus den Hat-Achsen."""
    out = {v for a, v in TRIGGERS.items() if a in abs_codes}
    for a, (lo, hi) in HATS.items():
        if a in abs_codes:
            out |= {lo, hi}
    return out


def key_name(code):
    return NAMES.get(code) or "Key 0x%x" % code


def parse_devices(text):
    """/proc/bus/input/devices -> [{"name", "uniq", "event": "eventN" oder "", "keys": {Codes}}] (eine Zeile je Eingabegerät)."""
    out = []
    for block in re.split(r"\n\s*\n", text):
        d = {"name": "", "uniq": "", "event": "", "keys": set(), "abs": set()}
        for ln in block.splitlines():
            if ln.startswith("N: Name="):
                d["name"] = ln.split("=", 1)[1].strip().strip('"')
            elif ln.startswith("U: Uniq="):
                d["uniq"] = ln.split("=", 1)[1].strip().upper()
            elif ln.startswith("H: Handlers="):
                m = re.search(r"\b(event\d+)\b", ln)
                d["event"] = m.group(1) if m else ""
            elif ln.startswith("B: KEY="):
                d["keys"] = parse_bitmask(ln.split("=", 1)[1])
            elif ln.startswith("B: ABS="):
                d["abs"] = parse_bitmask(ln.split("=", 1)[1])
        if d["event"]:
            out.append(d)
    return out


def parse_bitmask(s):
    """Hex-Wörter (64 Bit, das höchste zuerst, durch Leerzeichen getrennt) -> Menge der gesetzten Bits."""
    bits = set()
    words = s.split()
    for i, w in enumerate(reversed(words)):
        try:
            v = int(w, 16)
        except ValueError:
            continue
        for b in range(64):
            if v >> b & 1:
                bits.add(i * 64 + b)
    return bits


class ControllerKeys:
    def __init__(self, state_dir, act, controllers_path=None, proc="/proc/bus/input/devices", dev_dir="/dev/input", demo=False):
        self.path = os.path.join(state_dir, "controller-keys.json")
        self.controllers_path = controllers_path or os.path.join(state_dir, "controllers.json")
        self.act, self.proc, self.dev_dir, self.demo = act, proc, dev_dir, demo
        self.lock = threading.Lock()
        self.data = {}                     # Adresse -> {"keys": [Codes], "map": {"Code": Funktion}}
        self.last = {"addr": "", "code": 0, "ts": 0.0}
        self.fds = {}                      # Pfad -> (Datei, Adresse)
        self.present = {}                  # Adresse -> True, wenn ein Eingabegerät da ist
        self.errors = []                   # letzte Meldungen (Rechte, Fehler)
        self._last_act = {}
        self._down = {}                    # (Adresse, virtuelle Taste) -> gerade gedrückt (Trigger und Steuerkreuz sind Achsen)
        self.absmax = {}                   # Adresse -> {Achse: größter Wert} der Trigger-Achsen
        self._stop = threading.Event()
        self.load()
        if demo:                           # Vorschau: ein Beispiel-Controller mit den Tasten eines Gamepads
            self.data.setdefault(DEMO_ADDR, {"keys": [0x130, 0x131, 0x133, 0x134, 0x136, 0x137, 0x138, 0x139, 0x13A, 0x13B], "map": {}})

    # -- Dateien
    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        for addr, v in (d.get("controllers") or {}).items() if isinstance(d, dict) else []:
            if not (MAC_RE.match(addr) and isinstance(v, dict)):
                continue
            keys = [k for k in v.get("keys", []) if isinstance(k, int) and 0 <= k < VIRTUAL_MAX]
            mp = {str(k): f for k, f in (v.get("map") or {}).items() if str(k).isdigit() and f in FUNCTION_IDS}
            self.data[addr.upper()] = {"keys": sorted(set(keys)), "map": mp}

    def save(self):
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"controllers": self.data}, f)
        os.replace(tmp, self.path)

    def paired(self):
        """{Adresse: Name} der gekoppelten Controller (Liste des Bluetooth-Dienstes)."""
        if self.demo:
            return {DEMO_ADDR: "Mini Controller"}
        try:
            with open(self.controllers_path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return {}
        return {a.upper(): str((c or {}).get("name", "")) or a for a, c in (d.get("controllers") or {}).items() if MAC_RE.match(a)} if isinstance(d, dict) else {}

    # -- Zuordnung
    def set_map(self, addr, code, fn):
        addr = str(addr).upper()
        if not MAC_RE.match(addr) or addr not in self.paired():
            raise ValueError("Unbekannter Controller")
        if not isinstance(code, int) or isinstance(code, bool) or not 0 <= code < VIRTUAL_MAX:
            raise ValueError("Unbekannte Taste")
        if fn not in (None, "") and fn not in FUNCTION_IDS:
            raise ValueError("Unbekannte Funktion")
        with self.lock:
            entry = self.data.setdefault(addr, {"keys": [], "map": {}})
            if fn:
                entry["map"][str(code)] = fn
            else:
                entry["map"].pop(str(code), None)
            self.save()

    def forget(self, addrs):
        """Zuordnungen von Controllern entfernen, die nicht mehr gekoppelt sind."""
        with self.lock:
            gone = [a for a in self.data if a not in addrs]
            for a in gone:
                del self.data[a]
            if gone:
                self.save()

    # -- Anzeige
    def snapshot(self):
        names = self.paired()
        if not self.demo and os.path.exists(self.controllers_path):
            self.forget(set(names))                                # entfernte Controller: Zuordnung vergessen (nur, wenn die Liste lesbar ist)
        out = []
        for addr, name in sorted(names.items(), key=lambda kv: kv[1].lower()):
            e = self.data.get(addr, {"keys": [], "map": {}})
            assumed = not e["keys"]                                  # noch nie verbunden: die übliche Gamepad-Belegung zeigen
            keys = GAMEPAD if assumed else e["keys"]
            out.append({"addr": addr, "name": name, "present": self.demo or bool(self.present.get(addr)), "assumed": assumed,
                        "keys": [{"code": c, "name": key_name(c)} for c in keys], "map": dict(e["map"])})
        last = dict(self.last)
        last["age"] = round(time.time() - last["ts"], 1) if last["ts"] else None
        return {"controllers": out, "functions": [{"id": k, "name": v} for k, v in FUNCTIONS], "last": last, "error": self.errors[-1] if self.errors else ""}

    # -- Lesen
    def _scan(self):
        """Eingabegeräte der gekoppelten Controller suchen, neue öffnen, verschwundene schließen, Tastenlisten merken."""
        names = self.paired()
        try:
            with open(self.proc, encoding="utf-8", errors="replace") as f:
                devs = parse_devices(f.read())
        except OSError:
            devs = []
        found, present = {}, {}
        for d in devs:
            if d["uniq"] in names:
                found[os.path.join(self.dev_dir, d["event"])] = d["uniq"]
                present[d["uniq"]] = True
                keys = sorted({c for c in d["keys"] if c < 0x300} | virtual_keys(d["abs"]))
                with self.lock:
                    e = self.data.setdefault(d["uniq"], {"keys": [], "map": {}})
                    merged = sorted(set(e["keys"]) | set(keys))
                    if merged != e["keys"]:
                        e["keys"] = merged
                        self.save()
        self.present = present
        for p in list(self.fds):
            if p not in found:
                try:
                    self.fds.pop(p)[0].close()
                except OSError:
                    pass
        for p, addr in found.items():
            if p in self.fds:
                continue
            try:
                f = open(p, "rb", buffering=0)
                os.set_blocking(f.fileno(), False)
                self.fds[p] = (f, addr)
                self.absmax.setdefault(addr, {}).update(trigger_ranges(f.fileno()))
                self.errors = []
            except PermissionError:
                self.errors = ["Kein Zugriff auf die Eingabegeräte (Gruppe input). Die Oberfläche muss einmal neu gestartet werden."]
            except OSError:
                pass

    def dispatch(self, addr, etype, code, value):
        if etype == EV_ABS:
            self.handle_abs(addr, code, value)
        else:
            self.handle_event(addr, etype, code, value)

    def _edge(self, addr, vk, down):
        was = self._down.get((addr, vk), False)
        if down != was:
            self._down[(addr, vk)] = down
            if down:
                self.handle_event(addr, EV_KEY, vk, 1)

    def handle_abs(self, addr, code, value):
        """Trigger (ab 40 % Weg gedrückt, erst unter 20 % wieder losgelassen) und Steuerkreuz als Tasten behandeln."""
        if code in TRIGGERS:
            vk = TRIGGERS[code]
            top = self.absmax.get(addr, {}).get(code) or 255
            down = value >= top * (TRIGGER_AT / 2 if self._down.get((addr, vk)) else TRIGGER_AT)
            self._edge(addr, vk, down)
        elif code in HATS:
            lo, hi = HATS[code]
            self._edge(addr, lo, value < 0)
            self._edge(addr, hi, value > 0)

    def handle_event(self, addr, etype, code, value):
        """Ein Ereignis eines Controllers; nur das Drücken einer Taste zählt (nicht Loslassen und nicht Wiederholen)."""
        if etype != EV_KEY or value != 1:
            return None
        now = time.time()
        self.last = {"addr": addr, "code": code, "ts": now}
        fn = (self.data.get(addr) or {}).get("map", {}).get(str(code))
        if not fn:
            return None
        key = (addr, code)
        if now - self._last_act.get(key, 0) < DEBOUNCE:
            return None
        self._last_act[key] = now
        threading.Thread(target=self._run_act, args=(fn,), daemon=True).start()
        return fn

    def _run_act(self, fn):
        try:
            self.act(fn)
        except Exception as e:
            print("Controller-Taste %s: %s" % (fn, e), flush=True)

    def run(self):
        last_scan = 0.0
        while not self._stop.is_set():
            if time.monotonic() - last_scan > 2:
                last_scan = time.monotonic()
                try:
                    self._scan()
                except Exception as e:
                    print("Controller-Tasten: %r" % (e,), flush=True)
            files = {f: addr for f, addr in self.fds.values()}
            if not files:
                self._stop.wait(1)
                continue
            try:
                ready, _, _ = select.select(list(files), [], [], 1)
            except (OSError, ValueError):
                self._scan()
                continue
            for f in ready:
                try:
                    data = f.read(EVENT.size * 32)
                except BlockingIOError:
                    continue
                except OSError:                                       # Gerät verschwunden (Controller aus)
                    for p, (g, _) in list(self.fds.items()):
                        if g is f:
                            self.fds.pop(p)
                    try:
                        f.close()
                    except OSError:
                        pass
                    continue
                if not data:
                    continue
                for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    _, _, etype, code, value = EVENT.unpack_from(data, i)
                    self.dispatch(files[f], etype, code, value)

    def start(self):
        if self.demo:
            return
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        self._stop.set()
