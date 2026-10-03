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

STATE = "/var/lib/pipbox"
BELACODER = "/opt/pipbox/bin/belacoder"      # belacoder mit tolerantem Regler (belacoder/), sonst das Original aus dem Suchpfad
RUN = "/run/pipbox-send"
WORK = "/var/tmp/pipbox"
STATUS = f"{RUN}/status.json"
STATS = f"{RUN}/belacoder-stats.txt"     # die letzten Regelzeilen von belacoder (nur Zahlen), im RAM
STATS_KEEP = 3000
LISTEN_PORT = 9100
DELAY_LIVE = False        # hat die gestartete Pipeline den Steuerbaustein pbctl?
DELAY_LIVE_PIPS = False   # und kann er auch die kleinen Bilder verzögern?
PLUGIN_DIR = "/opt/pipbox/gst"
DOWN_S = 5        # so lange darf eine Kamera ausbleiben, bevor umgeschaltet wird
UP_S = 60         # so lange muss eine zurückgekehrte Kamera stabil senden, bevor sie wieder zugeschaltet wird
UP_FIRST_S = 5    # lief gar keine Kamera, genügt für die erste schon diese Zeit
NOTABLE = (
    (re.compile(r"Failed to establish an SRT connection"), "Verbindung zum SRT-Server fehlgeschlagen, neuer Versuch"),
    (re.compile(r"The SRT connection.*exiting"), "Verbindung zum Server kurz unterbrochen, wird automatisch neu aufgebaut"),
    (re.compile(r"Pipeline stall detected"), "Das Eingangsbild stockte, der Encoder wird neu gestartet"),
    (re.compile(r"Failed to establish any initial connections"), "Keine Verbindung zum SRTLA-Server, neuer Versuch"),
    (re.compile(r"no available connections"), "Alle Sendewege waren ausgefallen, Verbindung wird neu aufgebaut"),
    (re.compile(r"gstreamer error from (\w+)"), "Fehler im Bildaufbau (Kamera oder Encoder)"),
)


class Refuse(Exception):
    pass


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

    def step(self, now, live):
        """live: Menge der sendenden Kameras oder None (unbekannt). Gibt die neue Anordnung zurück, wenn sie sich ändert."""
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
                if k in live or now - self.down[k] < DOWN_S:
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
        vals = [max(0, min(3000, int(cfg.get(k, 0) or 0))) if cfg["type"] == "pip" else 0
                for k in ("main_delay_ms", "pip_delay_ms", "pip2_delay_ms", "pip3_delay_ms")]
        tmp = f"{STATE}/main-delay-ms.tmp"
        try:
            os.unlink(tmp)                  # der Ordner gehört dem Benutzer pipbox: kein Verweis darf als Ziel dienen
        except OSError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "w") as f:
            f.write(" ".join(map(str, vals)) + "\n")
        os.replace(tmp, f"{STATE}/main-delay-ms")
    except OSError:
        pass
    global DELAY_LIVE, DELAY_LIVE_PIPS
    DELAY_LIVE = "pbctl" in text
    DELAY_LIVE_PIPS = "pip-queue=" in text
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
    auto = bool(cfg.get("auto_failover", True)) and len(keys) > 1      # mit nur einer Kamera gibt es nichts umzuschalten
    live = live_keys() if auto else None
    if auto and live is not None:
        eff, used = effective_cfg(cfg, live)
        if eff is None:
            raise Refuse("Keine Kamera sendet gerade an die Box")
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
    return sv, lat, mn, mx, ips, {"cfg": cfg, "layout": used, "auto": auto,
                                  "spread": "all" if st.get("spread") == "all" else "best",
                                  "sig": server.srtla_signature(srt)}


class Sender:
    def __init__(self, sv, lat, ips, plan=None):
        self.sv, self.lat, self.ips = sv, lat, ips
        plan = plan or {"cfg": {}, "layout": (), "auto": False}
        self.plan = plan
        self.fo = Failover(plan["cfg"], plan["layout"]) if plan["auto"] else None
        self.layout = tuple(plan["layout"])
        self.waiting = False
        self.senv = None
        self._ups_t = 0
        self.stats = collections.deque(maxlen=STATS_KEEP)    # (Zeit, Zeile)
        self.links = collections.deque(maxlen=300)           # Zustandszeilen der Wegewahl (srtla_send "links: ...")
        self.stop_ev = threading.Event()
        self.procs = {}
        self.lock = threading.Lock()
        self.restarts = {"srtla_send": 0, "belacoder": 0}
        self.last = ""
        self.last_at = 0
        self.since = int(time.time())
        self.state = "starting"

    def note(self, line):
        for rx, msg in NOTABLE:
            if rx.search(line):
                with self.lock:
                    self.last = msg
                    self.last_at = time.time()
                return

    def spawn(self, name, args, env=None):
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             errors="replace", env=env)
        with self.lock:
            self.procs[name] = p
        threading.Thread(target=self.pump, args=(name, p), daemon=True).start()
        return p

    def pump(self, name, p):
        for line in p.stdout:
            self.note(line)      # nur erkannte Meldungen, nie Stream-ID oder Adressen ins Journal
            if name == "srtla_send" and line.startswith("links:"):
                # Zustand der laufzeitbewussten Wegewahl (nur Zahlen und eigene Adressen), im RAM
                self.links.append((time.strftime("%H:%M:%S", time.gmtime()), line.strip()[:200]))
                continue
            if name == "belacoder":
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
            for path, rows in ((STATS, self.stats), (f"{RUN}/srtla-links.txt", self.links)):
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
        a = ["stdbuf", "-oL", "-eL", BELACODER if os.access(BELACODER, os.X_OK) else "belacoder", f"{WORK}/pipeline", "127.0.0.1", str(LISTEN_PORT), "-d", "0",
             "-b", f"{WORK}/bitrate", "-l", str(self.lat)]
        if sv.get("streamid"):
            a += ["-s", sv["streamid"]]
        return a

    def write_status(self, extra=None):
        os.makedirs(RUN, exist_ok=True)
        with self.lock:
            data = {"state": self.state, "since": self.since, "server": self.sv["name"],
                    "restarts": dict(self.restarts), "last": self.last, "last_age": int(time.time() - self.last_at) if self.last_at else None, "time": int(time.time()),
                    "delay_live": DELAY_LIVE, "delay_live_pips": DELAY_LIVE_PIPS,
                    "applied": self.plan.get("sig")}
        if self.fo is not None:
            keys = configured_keys(self.plan["cfg"])
            data["failover"] = {"auto": True, "layout": list(self.layout), "configured": keys,
                                "waiting": self.waiting, "degraded": self.waiting or set(self.layout) != set(keys)}
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

    def run(self):
        env = dict(os.environ, GST_PLUGIN_PATH=PLUGIN_DIR)
        senv = None
        if self.plan.get("spread") == "all":
            # Alle Wege gleichzeitig: auch Leitungen mit höherer Laufzeit mitnutzen (bis 300 ms schlechter als die beste)
            senv = dict(os.environ, SRTLA_LAT_MARGIN_MS="300")
        self.senv = senv
        self.spawn("srtla_send", self.args("srtla_send"), env=senv)
        self.write_status()          # Zustand "startet" sofort sichtbar machen
        self.wait_links_ready()
        self.spawn("belacoder", self.args("belacoder"), env=env)
        with self.lock:
            self.state = "running"
        print("send: Sendekette gestartet", flush=True)
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
                    print(f"send: {name} beendet (Code {p.returncode}), Neustart {n}", flush=True)
                    if self.stop_ev.wait(2):
                        break
                    self.spawn(name, self.args(name), env=env if name == "belacoder" else self.senv)
                    with self.lock:
                        self.state = "running"
            try:
                if time.monotonic() - self._ups_t >= 6:     # alle 6 s genügen: srtla_send merkt tote Wege selbst
                    self._ups_t = time.monotonic()
                    self.refresh_uplinks()
            except Exception as e:           # eine kaputte Einstellung darf die Übertragung nie beenden
                print(f"send: Netze konnten nicht neu gelesen werden ({type(e).__name__})", flush=True)
            self.write_status()
            self.flush_stats()
            self.stop_ev.wait(2)
        self.shutdown()

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
        for name in ("belacoder", "srtla_send"):
            p = self.procs.get(name)
            if p and p.poll() is None:
                p.terminate()
        deadline = time.time() + 8
        for p in self.procs.values():
            while p.poll() is None and time.time() < deadline:
                time.sleep(0.2)
            if p.poll() is None:
                p.kill()
        for f in (STATUS, STATS, f"{RUN}/srtla-links.txt"):
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
