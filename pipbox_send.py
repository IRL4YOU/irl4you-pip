#!/usr/bin/env python3
"""Sende-Dienst (pipbox-send.service, läuft als root).

Startet die Sendekette selbst, unabhängig von belaUI:
    srtla_send <Port> <SRTLA-Server> <Port> <IP-Datei>      (Bonding über die gewählten Netze)
    belacoder <Pipeline> 127.0.0.1 <Port> -d 0 -b <Bitrate-Datei> -l <Latenz> [-s <Stream-ID>]

Alle Werte kommen aus Dateien, die der Benutzer pipbox schreibt (srtla.json, pipeline.json).
Weil dieser Dienst Root-Rechte hat, wird **alles noch einmal streng geprüft** und nie über
eine Shell ausgeführt. Läuft schon ein belacoder (z. B. von belaUI), wird nicht gesendet.
"""
import collections
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time

sys.path.insert(0, "/opt/pipbox")
import server  # noqa: E402  (nur Prüf- und Erzeugungsfunktionen, startet nichts)
import pipbox_always  # noqa: E402  (Zubringer und Auswahl im Modus "alle Kameras immer bereit")
import pipbox_live  # noqa: E402  (Engine "immer bereit" mit Compositor und dynamischen Zweigen, Technik von streamingbox)
try:
    import pipbox_watch  # noqa: E402  (Wächter: erkennt Hänger von belacoder, sichert ein Diagnosepaket, startet neu)
except ImportError:        # ein Update, das die Datei nicht mitbringt, darf die Sendung nie verhindern: dann gibt es nur keinen Wächter
    pipbox_watch = None

STATE = "/var/lib/pipbox"
BELACODER = "/opt/pipbox/bin/belacoder"      # belacoder mit tolerantem Regler (belacoder/), sonst das Original aus dem Suchpfad
RUN = "/run/pipbox-send"
WORK = "/var/tmp/pipbox"
STATUS = f"{RUN}/status.json"
BC_STATS = f"{RUN}/belacoder-stats.json"       # JSON von belacoder, einmal je Sekunde (Bitrate, RTT, Sendepuffer, Neuübertragungen, Verlust, Encoder-Bilder), im RAM
EVENTS = f"{RUN}/belacoder-events.txt"     # Ereignisse von belacoder (Zweige starten, Zeitausrichtung), für die Protokolle, im RAM
EVENT_RX = re.compile(r"^(Aligned |Re-aligned |Aligning |Feed sbf[0-9]|Dynamic feed |Controller parts|Bitrate controller|control: no element)")
STATS = f"{RUN}/belacoder-stats.txt"     # die letzten Regelzeilen von belacoder (nur Zahlen), im RAM
STATS_KEEP = 3000
LISTEN_PORT = 9100
VIEW_LIVE = False         # kann die gestartete Pipeline kleine Bilder ein-/ausblenden und stumm schalten (Ansicht im Betrieb)?
AUDIO_LIVE = False        # und die Tonquelle wechseln (Ton-Umschalter)?
DELAY_LIVE = False        # hat die gestartete Pipeline den Steuerbaustein pbctl?
DELAY_LIVE_PIPS = False   # und kann er auch die kleinen Bilder verzögern?
SWAP_BASE = None          # Tausch ohne Neustart: {"cams": [...], "group": n} der gestarteten Pipeline, sonst None
PLUGIN_DIR = "/opt/pipbox/gst"
DOWN_S = 5        # so lange darf eine Kamera ausbleiben, bevor umgeschaltet wird
UP_S = 60         # so lange muss eine zurückgekehrte Kamera stabil senden, bevor sie wieder zugeschaltet wird
UP_FIRST_S = 5    # lief gar keine Kamera, genügt für die erste schon diese Zeit
NOTABLE = (
    (re.compile(r"Failed to establish an SRT connection"), "Verbindung zum SRT-Server fehlgeschlagen, neuer Versuch"),
    (re.compile(r"The SRT connection.*exiting"), "Verbindung zum Server kurz unterbrochen, wird automatisch neu aufgebaut"),
    (re.compile(r"Pipeline stall detected \(output\)"), "Der Ausgang stockte (der Encoder lieferte nichts mehr), er wird neu gestartet"),
    (re.compile(r"Pipeline stall detected"), "Das Eingangsbild stockte, der Encoder wird neu gestartet"),
    (re.compile(r"Failed to establish any initial connections"), "Keine Verbindung zum SRTLA-Server, neuer Versuch"),
    (re.compile(r"no available connections"), "Alle Sendewege waren ausgefallen, Verbindung wird neu aufgebaut"),
    (re.compile(r"gstreamer error from (\w+)"), "Fehler im Bildaufbau (Kamera oder Encoder)"),
)


class Refuse(Exception):
    pass


def exit_text(rc):
    """Rückgabewert eines beendeten Programms für das Protokoll: 'Code 0' oder, wenn ein Signal es beendet hat (negativer
    Wert), 'Code -13, Signal SIGPIPE'. So ist im Journal sichtbar, ob der Encoder sich selbst beendet hat oder abgeschossen wurde."""
    if rc is not None and rc < 0:
        try:
            return f"Code {rc}, Signal {signal.Signals(-rc).name}"
        except ValueError:
            pass
    return f"Code {rc}"


def belacoder_running():
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/comm") as f:
                    if f.read().strip() == "belacoder":
                        return True
            except OSError:
                pass
    return False


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def stream_live(key):
    """Sendet die Kamera gerade an unseren RTMP-Server? (nginx-Statistik)"""
    import urllib.request
    try:
        x = urllib.request.urlopen("http://127.0.0.1:1936/", timeout=3).read().decode()
    except OSError:
        return None
    for m in re.finditer(r"<stream>.*?</stream>", x, re.S):
        if re.search(rf"<name>{re.escape(key)}</name>", m.group(0)) and "<publishing/>" in m.group(0):
            return True
    return False


def live_keys():
    """Schlüssel der Kameras, die gerade an unseren RTMP-Server senden (nginx-Statistik). None, wenn nicht lesbar."""
    import urllib.request
    try:
        x = urllib.request.urlopen("http://127.0.0.1:1936/", timeout=3).read().decode()
    except OSError:
        return None
    out = set()
    for m in re.finditer(r"<stream>.*?</stream>", x, re.S):
        n = re.search(r"<name>([^<]+)</name>", m.group(0))
        if n and "<publishing/>" in m.group(0):
            out.add(n.group(1))
    return out


def configured_keys(cfg):
    """Die eingestellten Kameras in der Reihenfolge Hauptbild, kleines Bild, zweites kleines Bild."""
    ks = [cfg.get("main", "")]
    if cfg.get("type") == "pip":
        ks += [cfg.get("pip", ""), cfg.get("pip2", ""), cfg.get("pip3", "")]
    out = []
    for k in ks:
        if k and k not in out:
            out.append(k)
    return out


def effective_cfg(cfg, use):
    """Pipeline-Einstellung für die Kameras in `use` (Menge). Es gilt die Reihenfolge der Einstellung; die erste
    vorhandene Kamera wird Hauptbild. Verzögerung und Ton folgen der Kamera (jede Kamera hat ihre eigene Laufzeit),
    die Ecken folgen dem Platz. Gibt (Einstellung, genutzte Kameras) zurück, ohne Kamera (None, ())."""
    ks = [k for k in configured_keys(cfg) if k in use]
    if not ks:
        return None, ()
    # Eine deaktivierte Kamera springt nicht als Ersatz fürs Hauptbild ein (Issue #19): Hauptbild ist die eingestellte Hauptkamera, wenn sie da ist,
    # sonst die erste Kamera, die nicht deaktiviert ist. Deaktivierte Kameras bleiben als kleine Bilder, solange sie senden.
    ina = cfg.get("inactive")
    off = {k for k in ina if isinstance(k, str)} if isinstance(ina, list) else set()
    main = next((k for k in ks if k == cfg.get("main") or k not in off), None)
    if main is None:
        return None, ()
    ks = [main] + [k for k in ks if k != main]
    delay = {}
    for key, name in (("main", "main_delay_ms"), ("pip", "pip_delay_ms"), ("pip2", "pip2_delay_ms"), ("pip3", "pip3_delay_ms")):
        if cfg.get(key):
            delay[cfg[key]] = cfg.get(name, 0)
    main, smalls = ks[0], ks[1:4]
    out = dict(cfg)
    out.update(main=main, pip=smalls[0] if smalls else "", pip2=smalls[1] if len(smalls) > 1 else "", pip3=smalls[2] if len(smalls) > 2 else "",
               type="pip" if smalls else "single", main_delay_ms=delay.get(main, 0),
               pip_delay_ms=delay.get(smalls[0], 0) if smalls else 0,
               pip2_delay_ms=delay.get(smalls[1], 0) if len(smalls) > 1 else 0,
               pip3_delay_ms=delay.get(smalls[2], 0) if len(smalls) > 2 else 0)
    if smalls:
        audio_key = cfg.get(cfg["audio"]) if cfg.get("audio") in ("pip", "pip2", "pip3") else cfg.get("main")
        names = ("pip", "pip2", "pip3")
        out["audio"] = next((names[i] for i, k in enumerate(smalls) if k == audio_key), "main")
    return out, tuple(ks[:4])


class Failover:
    """Entscheidet, welche Kameras die Sendekette nutzen soll. Reine Rechnung ohne Zeit- und Dateizugriff (testbar).

    Fällt eine genutzte Kamera länger als DOWN_S aus, wird ohne sie weitergesendet. Eine zurückgekehrte Kamera wird erst
    nach UP_S stabilem Signal wieder zugeschaltet (sonst pendelt die Übertragung). Läuft keine Kamera, wird gewartet."""

    def __init__(self, cfg, layout):
        self.cfg, self.keys = cfg, configured_keys(cfg)
        self.layout = tuple(layout)
        self.up, self.down = {}, {}

    def wait_left(self, now):
        """Sekunden, die eine sendende, aber noch nicht zugeschaltete Kamera noch warten muss: {Schlüssel: Sekunden}."""
        need = UP_S if self.layout else UP_FIRST_S
        return {k: max(0, int(need - (now - t) + 0.999)) for k, t in self.up.items() if k not in self.layout}

    def step(self, now, live, gone=False):
        """live: Menge der sendenden Kameras oder None (unbekannt). Gibt die neue Anordnung zurück, wenn sie sich ändert.
        gone=True: der Encoder ist gerade beendet worden; eine genutzte Kamera, die nicht sendet, fällt dann sofort weg
        (ohne die Wartezeit DOWN_S), denn mit unveränderter Anordnung liefe der Encoder ins Leere."""
        if live is None:
            return None
        for k in self.keys:
            if k in live:
                self.up.setdefault(k, now)
                self.down.pop(k, None)
            else:
                self.down.setdefault(k, now)
                self.up.pop(k, None)
        need = UP_S if self.layout else UP_FIRST_S
        use = set()
        for k in self.keys:
            if k in self.layout:
                if k in live or (not gone and now - self.down[k] < DOWN_S):
                    use.add(k)
            elif k in live and now - self.up[k] >= need:
                use.add(k)
        new = effective_cfg(self.cfg, use)[1]
        if new != self.layout:
            self.layout = new
            return new
        return None


def ensure_work():
    """Arbeitsordner der Sendekette: gehört dem laufenden Benutzer (root) und ist kein Verweis (/var/tmp darf jeder Benutzer beschreiben)."""
    try:
        st = os.lstat(WORK)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
            if stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode):
                shutil.rmtree(WORK)
            else:
                os.unlink(WORK)
    except FileNotFoundError:
        pass
    os.makedirs(WORK, mode=0o700, exist_ok=True)
    os.chmod(WORK, 0o700)


def put_state_file(name, text):
    """Steuerdatei im Zustandsordner atomar schreiben. Der Ordner gehört dem Benutzer pipbox: kein Verweis darf als Ziel dienen."""
    tmp = f"{STATE}/{name}.tmp"
    try:
        os.unlink(tmp)
    except OSError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, f"{STATE}/{name}")


def write_pipeline(cfg):
    """Pipeline-Text und Verzögerungsdatei für diese Einstellung schreiben. Gibt den Text zurück ('' bei Fehler)."""
    text = server.PipelineStore(os.devnull).build(cfg)
    if not text:
        return ""
    ensure_work()
    with open(f"{WORK}/pipeline", "w") as f:
        f.write(text)
    # Verzögerung des Hauptbildes: Steuerdatei für den laufenden Baustein (pbctl) passend zur Pipeline setzen
    try:
        vals = server.PipelineStore.delay_values(cfg)
        put_state_file("main-delay-ms", " ".join(map(str, vals)) + "\n")
    except OSError:
        pass
    global DELAY_LIVE, DELAY_LIVE_PIPS, SWAP_BASE, VIEW_LIVE, AUDIO_LIVE
    DELAY_LIVE = "pbctl" in text
    DELAY_LIVE_PIPS = "pip-queue=" in text or "cam1=" in text
    plan = (server.PipelineStore.always_plan(cfg) or server.PipelineStore.swap_plan(cfg)) if "pbpipsel" in text else None
    SWAP_BASE = {"cams": plan["cams"], "group": plan["group"]} if plan else None
    try:
        os.unlink(server.SWAP_STATE)              # Rückmeldung der letzten Sendekette ist veraltet
    except OSError:
        pass
    try:
        if plan:                                  # Anfangszustand des Umschalters: wie gebaut (Hauptbild = erste Kamera)
            put_state_file(server.SWAP_SELECT, plan["line"] + "\n")
    except OSError:
        pass
    # Ansicht im Betrieb (kleine Bilder ein-/ausblenden, Tonquelle, stumm): Anfangszustand wie gebaut; stumm bleibt bei einem Neustart der
    # Sendekette erhalten (die Oberfläche setzt es beim Start einer neuen Sendung zurück)
    VIEW_LIVE = "name=avol" in text and "pbctl" in text
    AUDIO_LIVE = VIEW_LIVE and "pbpipsel name=asel" in text
    if "name=sbf0_src" in text:             # Engine Compositor: Verzögerung, Ansicht, Ton und Stumm gehen im Betrieb über den Steuerkanal
        DELAY_LIVE = DELAY_LIVE_PIPS = VIEW_LIVE = AUDIO_LIVE = True
        SWAP_BASE = None
    try:
        os.unlink(server.VIEW_STATE)
    except OSError:
        pass
    try:
        if VIEW_LIVE:
            mute = 0
            try:
                parts = open(f"{STATE}/{server.VIEW_FILE}").read().split()
                mute = 1 if len(parts) >= 3 and parts[2] == "1" else 0
            except OSError:
                pass
            put_state_file(server.VIEW_FILE, server.PipelineStore.view_line(cfg, mute) + "\n")
    except OSError:
        pass
    return text


def prepare():
    """Konfiguration prüfen und Dateien für die Programme schreiben. Wirft Refuse mit klarer Meldung."""
    srt = load_json(f"{STATE}/srtla.json")
    pipe = load_json(f"{STATE}/pipeline.json")
    servers = srt.get("servers", [])
    cur = next((s for s in servers if s.get("id") == srt.get("selected")), None)
    if not cur:
        raise Refuse("Kein SRTLA-Server ausgewählt")
    try:
        sv = server.SrtlaStore.check(cur)
    except ValueError as e:
        raise Refuse(f"SRTLA-Server ungültig: {e}")
    st = {**server.SrtlaStore.DEFAULT_SETTINGS, **srt.get("settings", {})}
    try:
        mn, mx, lat = int(st["min_kbps"]), int(st["max_kbps"]), int(st["latency_ms"])
    except (TypeError, ValueError):
        raise Refuse("Sendeeinstellungen ungültig")
    if not (100 <= mn < mx <= 20000 and 100 <= lat <= 10000):
        raise Refuse("Sendeeinstellungen außerhalb des erlaubten Bereichs")
    if not pipe.get("main"):
        raise Refuse("Kein Bildaufbau gespeichert (Hauptkamera fehlt)")
    cfg = {**server.PipelineStore.DEFAULT, **pipe}
    if cfg["type"] == "pip" and not os.path.exists(f"{PLUGIN_DIR}/libgstpbpip.so"):
        raise Refuse("Bild-in-Bild braucht den Überlagerungs-Baustein, der noch nicht installiert ist")
    if not server.PipelineStore(os.devnull).build(cfg):
        raise Refuse("Der Bildaufbau konnte nicht erzeugt werden (Kamera-Schlüssel ungültig)")
    keys = configured_keys(cfg)
    always = bool(server.PipelineStore.always_plan(cfg))               # "alle Kameras immer bereit": kein Notbetrieb, keine Neustarts bei Kamerawechsel
    note = server.PipelineStore.always_blocker(cfg)                    # Schalter an, aber nicht möglich: normaler Modus, mit Hinweis
    if note:
        print(f"send: {note}", flush=True)
    auto = bool(cfg.get("auto_failover", True)) and len(keys) > 1 and not always      # mit nur einer Kamera gibt es nichts umzuschalten
    live = live_keys() if (auto or always) else None
    if always:
        if live is not None and not (set(keys) & live) and not pipbox_live.enabled(server.PipelineStore._safe_cfg(cfg)):
            raise Refuse("Keine Kamera sendet gerade an die Box")      # nur die Zubringer-Engine; die Engine mit Compositor startet auch leer
        auto = False
    if always:
        eff, used = cfg, tuple(keys)
    elif auto and live is not None:
        eff, used = effective_cfg(cfg, live)
        if eff is None:
            raise Refuse("Es sendet nur eine deaktivierte Kamera an die Box" if live else "Keine Kamera sendet gerade an die Box")
    else:
        eff, used = cfg, tuple(keys)
        for key in keys:
            if not auto and stream_live(key) is False:
                raise Refuse(f"Die Kamera {key} sendet gerade nicht an die Box")
    have = {o["iface"]: o["ip"] for o in server.iface_ips()}
    ips = [have[u] for u in st.get("uplinks", []) if u in have]
    if not ips:
        raise Refuse("Keiner der gewählten Sendewege hat eine IP-Adresse")
    ensure_work()
    if not write_pipeline(eff):
        raise Refuse("Der Bildaufbau konnte nicht erzeugt werden (Kamera-Schlüssel ungültig)")
    with open(f"{WORK}/bitrate", "w") as f:
        f.write(f"{mn * 1000}\n{mx * 1000}")
    with open(f"{WORK}/ips", "w") as f:
        f.write("\n".join(ips) + "\n")
    return sv, lat, mn, mx, ips, {"cfg": cfg, "layout": used, "auto": auto, "always": always, "always_note": note,
                                  "spread": "all" if st.get("spread") == "all" else "best",
                                  "sig": server.srtla_signature(srt)}


class Sender:
    def __init__(self, sv, lat, ips, plan=None):
        self.sv, self.lat, self.ips = sv, lat, ips
        plan = plan or {"cfg": {}, "layout": (), "auto": False}
        self.plan = plan
        self.fo = Failover(plan["cfg"], plan["layout"]) if plan["auto"] else None
        self.layout = tuple(plan["layout"])
        self.always = None
        self.live = bool(plan.get("always") and plan["cfg"] and pipbox_live.enabled(server.PipelineStore._safe_cfg(plan["cfg"])))
        self.boost = pipbox_live.CpuBoost(log=lambda m: print(m, flush=True))
        if self.live:
            self.always = pipbox_live.LiveController(
                load_json=lambda name: load_json(f"{STATE}/{name}"), put_state_file=put_state_file, state_dir=STATE,
                safe_cfg=server.PipelineStore._safe_cfg, view_values=server.PipelineStore.view_values, log=lambda m: print(m, flush=True))
        elif plan.get("always"):
            self.always = pipbox_always.Controller(
                load_json=lambda name: load_json(f"{STATE}/{name}"), put_state_file=put_state_file,
                delay_values=server.PipelineStore.delay_values, cam_live_path=server.CAM_LIVE,
                select_name=server.SWAP_SELECT, delay_name="main-delay-ms",
                feeders=pipbox_always.Feeders(log=lambda m: print(m, flush=True)), log=lambda m: print(m, flush=True))
        self.always_state = {}
        self.waiting = False
        self.senv = None
        self._ups_t = 0
        self._cfg_mt = None
        self._inact_mt = None
        self.stats = collections.deque(maxlen=STATS_KEEP)    # (Zeit, Zeile)
        self.events = collections.deque(maxlen=120)          # Ereigniszeilen von belacoder (nur feste Meldungen ohne Adressen und Schlüssel)
        self.links = collections.deque(maxlen=300)           # Zustandszeilen der Wegewahl (srtla_send "links: ...")
        self.watch = pipbox_watch.HangWatch() if pipbox_watch else None
        self.hang_n = 0
        self._hang_t = 0.0
        self.stop_ev = threading.Event()
        self.procs = {}
        self.lock = threading.Lock()
        self.restarts = {"srtla_send": 0, "belacoder": 0}
        self.last = ""
        self.last_src = ""
        self.last_at = 0
        self.since = int(time.time())
        self.state = "starting"

    def note(self, line):
        for rx, msg in NOTABLE:
            m = rx.search(line)
            if m:
                with self.lock:
                    fresh = msg != self.last or time.time() - self.last_at > 10     # nicht jede Wiederholung ins Journal
                    self.last = msg
                    self.last_at = time.time()
                    self.last_src = m.group(1) if m.groups() else ""     # nur der Elementname (rtmpsrc1), nie die Zeile selbst
                if fresh:
                    print(f"send: {msg}", flush=True)      # Grund des Encoder-Endes im Journal (nur dieser feste Text)
                return

    def spawn(self, name, args, env=None):
        # Python ignoriert SIGPIPE und setzt es im Kindprozess standardmäßig zurück (restore_signals=True). Gemessen: Jedes Code -13
        # des Encoders folgte 1 s auf einen Kamera-Rauswurf durch nginx, danach lehnte der Empfänger den schnellen Wiederanlauf ab
        # (ca. 4 s länger Ausfall); nach einem Code 0 nie. Vermutlich schreibt librtmp beim Schließen noch einmal in den toten Socket.
        # Bleibt SIGPIPE ignoriert (wie in diesem Dienst), endet belacoder über den normalen Fehlerweg (Code 0, SRT-Abmeldung).
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             errors="replace", env=env, restore_signals=(name != "belacoder"))
        with self.lock:
            self.procs[name] = p
        if name == "belacoder" and self.watch is not None:
            self.watch.started_now()
        if name == "belacoder" and self.live and self.always is not None:
            self.always.on_restart()                  # der neue belacoder kennt die Live-Befehle des alten nicht
        threading.Thread(target=self.pump, args=(name, p), daemon=True).start()
        return p

    def pump(self, name, p):
        dbg = open(os.environ["PIPBOX_BC_LOG"], "a") if os.environ.get("PIPBOX_BC_LOG") and name == "belacoder" else None     # nur für Tests auf der Box: alle Zeilen mit Uhrzeit
        for line in p.stdout:
            if dbg and not line.startswith("b:"):
                dbg.write(time.strftime("%H:%M:%S ") + line[:300].rstrip() + "\n")
                dbg.flush()
            self.note(line)      # nur erkannte Meldungen, nie Stream-ID oder Adressen ins Journal
            if name == "srtla_send" and line.startswith("links:"):
                # Zustand der laufzeitbewussten Wegewahl (nur Zahlen und eigene Adressen), im RAM
                self.links.append((time.strftime("%H:%M:%S", time.gmtime()), line.strip()[:200]))
                continue
            if name == "belacoder":
                ev = line.strip()
                if EVENT_RX.match(ev):                         # Ereignisse (Zweig gestartet, Zeitausrichtung): nur feste Meldungen, für die Protokolle
                    self.events.append((time.strftime("%H:%M:%S", time.gmtime()), ev[:200]))
                # Regelgrößen der Bitratenanpassung (nur Zahlen). Das installierte belacoder gibt sie nicht aus; eine mit
                # DEBUG gebaute Fassung (zur Fehlersuche) schreibt "bs: ..." und "set bitrate ..." (jede 5. Zeile behalten).
                t = line.strip()
                if t.startswith("bs:"):
                    self._dbg_n = (getattr(self, "_dbg_n", 0) + 1) % 5
                    if self._dbg_n:
                        continue
                elif not (t.startswith("set bitrate") or re.match(r"^b:\s*-?\d+/", t)):
                    continue
                self.stats.append((time.strftime("%H:%M:%S", time.gmtime()), t[:200]))

    def flush_stats(self):
        try:
            os.makedirs(RUN, exist_ok=True)
            for path, rows in ((STATS, self.stats), (f"{RUN}/srtla-links.txt", self.links), (EVENTS, self.events)):
                tmp = path + ".tmp"
                with open(tmp, "w") as f:
                    f.write("".join(f"{t} {l}\n" for t, l in list(rows)))
                os.chmod(tmp, 0o644)
                os.replace(tmp, path)
        except OSError:
            pass

    def args(self, name):
        sv = self.sv
        if name == "srtla_send":
            return ["srtla_send", str(LISTEN_PORT), sv["host"], str(sv["port"]), f"{WORK}/ips"]
        pre = []
        if self.live:                               # Engine Compositor: auf die großen Kerne (dort läuft der Compositor schnell genug)
            big = pipbox_live.big_cpus()
            if big and shutil.which("taskset"):
                pre = ["taskset", "-c", ",".join(map(str, big))]
        a = pre + ["stdbuf", "-oL", "-eL", BELACODER if os.access(BELACODER, os.X_OK) else "belacoder", f"{WORK}/pipeline", "127.0.0.1", str(LISTEN_PORT), "-d", "0",
                   "-b", f"{WORK}/bitrate", "-l", str(self.lat)]
        if self.live:
            a += ["-C", pipbox_live.CTL_FIFO, "-S", pipbox_live.LIVE_STATS, "-A", str(pipbox_live.buffer_ms(server.PipelineStore._safe_cfg(self.plan["cfg"])))]
        if sv.get("streamid"):
            a += ["-s", sv["streamid"]]
        return a

    def write_status(self, extra=None):
        os.makedirs(RUN, exist_ok=True)
        with self.lock:
            data = {"state": self.state, "since": self.since, "server": self.sv["name"],
                    "restarts": dict(self.restarts), "last": self.last, "last_age": int(time.time() - self.last_at) if self.last_at else None, "time": int(time.time()),
                    "delay_live": DELAY_LIVE, "delay_live_pips": DELAY_LIVE_PIPS, "swap": SWAP_BASE,
                    "view_live": VIEW_LIVE, "audio_live": AUDIO_LIVE,
                    "applied": self.plan.get("sig"), "always_note": self.plan.get("always_note"), "hang_restarts": self.hang_n}
        if self.always is not None:
            keys = configured_keys(self.plan["cfg"])
            st = self.always_state or {}
            data["failover"] = {"auto": True, "always": True, "layout": [k for k in st.get("slots", []) if k], "configured": keys,
                                "inactive": list(self.plan["cfg"].get("inactive") or []), "waiting": False, "degraded": False, "wait": {},
                                "live": st.get("alive", []), "main": st.get("main")}
            data["always"] = True
        if self.fo is not None:
            keys = configured_keys(self.plan["cfg"])
            data["failover"] = {"auto": True, "layout": list(self.layout), "configured": keys, "inactive": list(self.plan["cfg"].get("inactive") or []),
                                "waiting": self.waiting, "degraded": self.waiting or set(self.layout) != set(keys),
                                "wait": self.fo.wait_left(time.time())}
        if extra:
            data.update(extra)
        tmp = STATUS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, STATUS)

    def stop_proc(self, name):
        p = self.procs.get(name)
        if p is not None and p.poll() is None:
            p.terminate()
            deadline = time.time() + 5
            while p.poll() is None and time.time() < deadline:
                time.sleep(0.1)
            if p.poll() is None:
                p.kill()
        self.procs[name] = None

    def switch(self, layout, env):
        """Auf eine andere Kamera-Anordnung umschalten: nur belacoder neu starten (srtla_send hält die Verbindung)."""
        eff, used = effective_cfg(self.plan["cfg"], set(layout))
        if eff is None:
            self.stop_proc("belacoder")
            self.waiting, self.layout = True, ()
            print("send: keine Kamera sendet, warte auf ein Signal", flush=True)
            return
        if not write_pipeline(eff):
            print("send: Umschaltung abgebrochen (Pipeline nicht erzeugbar)", flush=True)
            return
        self.stop_proc("belacoder")
        self.waiting, self.layout = False, used
        self.spawn("belacoder", self.args("belacoder"), env=env)
        with self.lock:
            self.state = "running"
        print(f"send: umgeschaltet auf {len(used)} Kamera(s): {', '.join(used)}", flush=True)

    def encoder_died(self, env):
        """belacoder ist beendet. Fehlt eine genutzte Kamera (der RTMP-Server hat sie entfernt), geht es gleich ohne sie
        weiter, statt mit der alten Anordnung noch zweimal ins Leere zu starten. True, wenn umgeschaltet wurde."""
        if self.fo is None:
            return False
        try:
            live = live_keys()
            if live is not None:
                print(f"send: Kameras laut RTMP-Server: {len(live & set(self.fo.keys))} von {len(self.fo.keys)} senden", flush=True)
            new = self.fo.step(time.time(), live, gone=True)
            if new is None:
                return False
            self.switch(new, env)
        except Exception as e:           # die Umschaltung darf die Übertragung nie beenden
            print(f"send: Sofort-Umschaltung nicht möglich ({type(e).__name__})", flush=True)
            return False
        with self.lock:
            self.state = "running"
        return True

    def run(self):
        env = dict(os.environ, GST_PLUGIN_PATH=PLUGIN_DIR)
        env["BELACODER_STATS_FILE"] = BC_STATS            # Kennzahlen für "Details" im Status (belacoder-stats.patch), sonst nichts
        if self.live:
            env["GST_MPP_NO_RGA"] = "1"                   # der Encoder bekommt nur Bilder aus dem Compositor (Systemspeicher): Software-Kopie statt RGA über virtuelle Adresse
            self.boost.off()                              # Rest eines abgestürzten Laufs zurückstellen, dann für diese Sendung hochnehmen
            self.boost.on()
        senv = None
        if self.plan.get("spread") == "all":
            # Alle Wege gleichzeitig: auch Leitungen mit höherer Laufzeit mitnutzen (bis 300 ms schlechter als die beste); jeder geeignete Weg bekommt mindestens 10 Prozent der Pakete
            senv = dict(os.environ, SRTLA_LAT_MARGIN_MS="300", SRTLA_MIN_SHARE_PCT="10")
        self.senv = senv
        if os.environ.get("PIPBOX_NO_SRTLA") == "1":           # nur für Tests auf der Box: belacoder sendet direkt an einen lokalen SRT-Empfänger (Port LISTEN_PORT)
            self.write_status()
        else:
            self.spawn("srtla_send", self.args("srtla_send"), env=senv)
            self.write_status()          # Zustand "startet" sofort sichtbar machen
            self.wait_links_ready()
        if self.always is not None:
            self.always.start()                     # Zubringer zuerst: die Eingänge der Kette (udpsrc) bekommen dann gleich Daten
            self.sync_swap_base()
        self.spawn("belacoder", self.args("belacoder"), env=env)
        with self.lock:
            self.state = "running"
        print("send: Sendekette gestartet" + (" (alle Kameras immer bereit)" if self.always is not None else ""), flush=True)
        while not self.stop_ev.is_set():
            if self.fo is not None:
                try:
                    new = self.fo.step(time.time(), live_keys())
                    if new is not None:
                        self.switch(new, env)
                except Exception as e:      # die Umschaltung darf die Übertragung nie beenden
                    print(f"send: automatische Umschaltung abgeschaltet ({type(e).__name__})", flush=True)
                    self.fo = None
                    if self.procs.get("belacoder") is None and not self.waiting:
                        try:
                            self.spawn("belacoder", self.args("belacoder"), env=env)
                        except OSError:
                            pass
            for name in ("srtla_send", "belacoder"):
                p = self.procs.get(name)
                if p is not None and p.poll() is not None:
                    with self.lock:
                        self.restarts[name] += 1
                        n = self.restarts[name]
                        self.state = "restarting"
                    why = ""
                    with self.lock:
                        if name == "belacoder" and self.last_at and time.time() - self.last_at < 15:
                            why = f", zuletzt: {self.last}" + (f" ({self.last_src})" if self.last_src else "")
                    print(f"send: {name} beendet ({exit_text(p.returncode)}), Neustart {n}{why}", flush=True)
                    if name == "belacoder" and self.encoder_died(env):
                        continue
                    if self.stop_ev.wait(2):
                        break
                    self.spawn(name, self.args(name), env=env if name == "belacoder" else self.senv)
                    with self.lock:
                        self.state = "running"
            try:
                self.sync_cfg()
                self.refresh_inactive()
            except Exception as e:           # eine kaputte Datei darf die Übertragung nie beenden
                print(f"send: Einstellung konnte nicht nachgeführt werden ({type(e).__name__})", flush=True)
            try:
                if time.monotonic() - self._ups_t >= 6:     # alle 6 s genügen: srtla_send merkt tote Wege selbst
                    self._ups_t = time.monotonic()
                    self.refresh_uplinks()
            except Exception as e:           # eine kaputte Einstellung darf die Übertragung nie beenden
                print(f"send: Netze konnten nicht neu gelesen werden ({type(e).__name__})", flush=True)
            if self.always is None:
                self.write_status()
                self.flush_stats()
                self.stop_ev.wait(2)
            else:
                # "alle Kameras immer bereit": Belegung der Kameras alle 0,5 s nachführen (Ausfall der Hauptkamera: Wechsel innerhalb von etwa 2 s),
                # Anzeige und Statistik wie bisher alle 2 s
                try:
                    self.always_state = self.always.tick()
                    self.sync_swap_base()
                except Exception as e:                  # die Auswahl darf die Übertragung nie beenden
                    print(f"send: Kameraauswahl: Fehler {type(e).__name__}", flush=True)
                try:
                    self.hang_check(env)
                except Exception as e:                  # der Wächter darf die Übertragung nie beenden
                    print(f"send: Wächter: Fehler {type(e).__name__}", flush=True)
                if time.monotonic() - getattr(self, "_slow_t", 0) >= 2:
                    self._slow_t = time.monotonic()
                    self.write_status()
                    self.flush_stats()
                self.stop_ev.wait(0.5)
        self.shutdown()

    def hang_check(self, env):
        """Wächter (Issue #51): steht belacoder still oder liefert eine sendende Kamera keine Bilder mehr, wird ein Diagnosepaket gesichert und belacoder neu gestartet.
        Höchstens einmal je Sekunde geprüft, nur im Modus "alle Kameras immer bereit" mit Compositor (nur dort gibt es die Statistikdatei)."""
        if self.watch is None or not (self.live and self.always is not None) or time.monotonic() - self._hang_t < 1.0:
            return
        self._hang_t = time.monotonic()
        p = self.procs.get("belacoder")
        if p is None or p.poll() is not None:
            return
        try:
            mt = os.stat(pipbox_live.LIVE_STATS).st_mtime
        except OSError:
            mt = None
        try:
            with open(pipbox_live.LIVE_STATS, errors="replace") as f:
                text = f.read(12000)
        except OSError:
            text = ""
        a = self.always
        expected = {}
        for slot, key in enumerate(a.binder.slot_key):
            info = a.pub.get(key) if key else None
            if info and info.get("w", 0) > 0 and a.states.get(slot) == 3 and not (key in a.inactive and key != a.desired[0]):
                expected[slot] = True
        hit = self.watch.check(mt, text.split("\n", 1)[0], expected, pipbox_watch.d_state_threads(p.pid))
        if hit is None or not self.watch.allow():
            return
        slots = []
        for slot, key in enumerate(a.binder.slot_key):
            info = a.pub.get(key) if key else None
            slots.append((slot, ("Bild %dx%d, Ton %s" % (info.get("w", 0), info.get("h", 0), "ja" if info.get("audio") else "nein")) if info else "sendet nicht", a.states.get(slot, "?")))
        report = pipbox_watch.build_report(hit, p.pid, time.monotonic() - (self.watch.started or time.monotonic()), text,
                                           [f"{t} {e}" for t, e in list(self.events)], slots)
        saved = pipbox_watch.write_report(STATE, report)
        self.watch.acted_now()
        self.hang_n += 1
        print(f"send: Hänger erkannt ({hit['text']}); Diagnosepaket {'gesichert (hang-diagnose.txt)' if saved else 'nicht speicherbar'}, belacoder wird neu gestartet", flush=True)
        try:
            if hit["kind"] != "feed":
                p.kill()                       # steht belacoder, nimmt er SIGTERM nicht mehr an
            self.stop_proc("belacoder")
            self.spawn("belacoder", self.args("belacoder"), env=env)
            with self.lock:
                self.state = "running"
        except OSError as e:
            print(f"send: Neustart nach Hänger nicht möglich ({type(e).__name__})", flush=True)

    def sync_swap_base(self):
        """Die Kameras je Platz (für die Verzögerungsdatei der Oberfläche) im Zustand der Sendekette führen: sie können sich im Betrieb ändern."""
        global SWAP_BASE
        if self.always is not None and SWAP_BASE is not None:
            SWAP_BASE = {"cams": list(self.always.binder.slot_key), "group": SWAP_BASE["group"]}      # ein Schlüssel je Platz, "" = frei

    def refresh_inactive(self):
        """Wurde in der Oberfläche eine Kamera deaktiviert oder wieder aktiviert, gilt das sofort für die automatische Umschaltung, ohne Neustart der Sendung."""
        if self.fo is None:
            return False
        try:
            mt = os.stat(f"{STATE}/pipeline.json").st_mtime_ns
        except OSError:
            return False
        if mt == self._inact_mt:
            return False
        self._inact_mt = mt
        new = [k for k in (load_json(f"{STATE}/pipeline.json").get("inactive") or []) if isinstance(k, str) and server.KEY_RE.match(k)][:3]
        if sorted(new) == sorted(self.plan["cfg"].get("inactive") or []):
            return False
        if new:
            self.plan["cfg"]["inactive"] = new
        else:
            self.plan["cfg"].pop("inactive", None)
        self.fo.cfg = self.plan["cfg"]
        print("send: deaktivierte Kameras geändert: " + (", ".join(new) or "keine"), flush=True)
        return True

    def sync_cfg(self):
        """Nach einem Tausch ohne Neustart hat sich pipeline.json geändert, die Kameras sind dieselben: die Einstellung der automatischen
        Umschaltung nachführen, sonst setzte ein späterer Kameraausfall das Hauptbild auf den alten Stand zurück."""
        if self.fo is None or SWAP_BASE is None:
            return False
        try:
            mt = os.stat(f"{STATE}/pipeline.json").st_mtime_ns
        except OSError:
            return False
        if mt == self._cfg_mt:
            return False
        self._cfg_mt = mt
        new = {**server.PipelineStore.DEFAULT, **load_json(f"{STATE}/pipeline.json")}
        if new.get("type") != "pip" or set(configured_keys(new)) != set(self.fo.keys):
            return False                          # andere Kameras: das regelt der normale Neustart
        self.plan["cfg"] = new
        self.fo.cfg, self.fo.keys = new, configured_keys(new)
        # Die Reihenfolge der genutzten Kameras (Anordnung) folgt der neuen Einstellung. Sonst hielte die automatische Umschaltung die
        # geänderte Reihenfolge beim nächsten Durchlauf für eine neue Anordnung und startete den Encoder neu (gemessen: 3 s nach dem Tausch).
        def ordered(layout):
            return tuple(k for k in self.fo.keys if k in set(layout))[:4]
        self.fo.layout = ordered(self.fo.layout)
        self.layout = ordered(self.layout)
        return True

    def wait_links_ready(self, timeout=20):
        """Der Encoder startet erst, wenn srtla_send mindestens einen Weg zum Server aufgebaut hat (erste Zustandszeile mit
        gemessener Laufzeit). Sonst gibt der Encoder nach wenigen Sekunden auf und muss neu starten (Fehlalarm beim Start)."""
        if not (shutil.which("srtla_send") or "").startswith("/usr/local/"):
            time.sleep(1)               # Original-Sender ohne Zustandszeilen: nicht umsonst warten
            return False
        end = time.time() + timeout
        while time.time() < end and not self.stop_ev.is_set():
            if any("srtt=" in l and "srtt=-1ms" not in l for _, l in list(self.links)):
                return True
            p = self.procs.get("srtla_send")
            if p is not None and p.poll() is not None:
                return False
            self.stop_ev.wait(0.3)
        return False

    def refresh_uplinks(self):
        """Wurden die Netze zum Senden in der Oberfläche geändert, liest der laufende srtla_send die Liste neu (Signal SIGHUP),
        ohne dass die Übertragung neu startet. Hat keines der gewählten Netze eine Adresse, bleibt alles wie es ist."""
        st = load_json(f"{STATE}/srtla.json").get("settings", {})
        ups = [u for u in st.get("uplinks", []) if isinstance(u, str)]
        have = {o["iface"]: o["ip"] for o in server.iface_ips()}
        ips = [have[u] for u in ups if u in have]
        if not ips or ips == self.ips:
            return False
        self.ips = ips
        with open(f"{WORK}/ips", "w") as f:
            f.write("\n".join(ips) + "\n")
        p = self.procs.get("srtla_send")
        if p is not None and p.poll() is None:
            p.send_signal(signal.SIGHUP)
        print(f"send: Netze zum Senden geändert, {len(ips)} Netz(e) neu gelesen", flush=True)
        return True

    def shutdown(self):
        with self.lock:
            self.state = "stopping"
        self.write_status()
        if self.always is not None:
            self.always.stop()
        for name in ("belacoder", "srtla_send"):
            p = self.procs.get(name)
            if p and p.poll() is None:
                p.terminate()
        deadline = time.time() + 8
        for p in self.procs.values():
            if p is None:
                continue
            while p.poll() is None and time.time() < deadline:
                time.sleep(0.2)
            if p.poll() is None:
                p.kill()
        if self.live:
            self.boost.off()
        for f in (STATUS, STATS, BC_STATS, f"{RUN}/srtla-links.txt", server.VIEW_STATE, server.CAM_LIVE):
            try:
                os.remove(f)
            except OSError:
                pass
        print("send: Sendekette beendet", flush=True)


def main():
    try:
        if belacoder_running():
            raise Refuse("Es läuft schon ein belacoder (z. B. über die BELABOX-Oberfläche). Bitte dort zuerst beenden.")
        sv, lat, mn, mx, ips, plan = prepare()
    except Refuse as e:
        os.makedirs(RUN, exist_ok=True)
        with open(STATUS, "w") as f:
            json.dump({"state": "refused", "message": str(e), "time": int(time.time())}, f)
        os.chmod(STATUS, 0o644)
        print(f"send: abgelehnt: {e}", flush=True)
        return 1
    s = Sender(sv, lat, ips, plan)
    signal.signal(signal.SIGTERM, lambda *_: s.stop_ev.set())
    signal.signal(signal.SIGINT, lambda *_: s.stop_ev.set())
    print(f"send: Server '{sv['name']}', Bitrate {mn}-{mx} kbit/s, Latenz {lat} ms, Wege {len(ips)}", flush=True)
    s.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
