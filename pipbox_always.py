"""Modus "alle Kameras immer bereit" (Issue #19, Anregung von Bittersweet1987): Zubringer und Auswahl der Kameras.

Die Sendekette hat in diesem Modus je Kamera-Platz (0 bis 3) zwei Eingänge, die nie ausfallen: udpsrc auf Loopback (Bild: H.264 über RTP, Ton: PCM). Ein
Zubringer-Prozess je Platz holt den Stream der Kamera aus dem RTMP-Server der Box und reicht ihn dorthin weiter. Sendet die Kamera nicht (noch nicht, nicht
mehr), endet der Zubringer sofort ("Failed to read any data from stream") und wird nach einer Sekunde neu gestartet. Die Kette bekommt davon nichts mit; der
Baustein pbpipsel hält den Ausgang mit schwarzen Bildern und Stille am Leben, bis wieder echte Daten kommen.

Dieses Modul enthält nur Logik ohne Zugriff auf die Sendekette: den Befehl des Zubringers, die Zuordnung der Kamera-Schlüssel zu den Plätzen, die Wahl
von Hauptbild und kleinen Bildern bei Ausfall und Rückkehr (Auswahl) und die Verwaltung der Prozesse (Zubringer). pipbox_send.py ruft es auf.
"""
import os
import re
import signal
import subprocess
import threading
import time

FEED_PORT = 9410            # wie server.FEED_PORT (Bild: FEED_PORT + 2*Platz, Ton: +1)
SLOTS = 4
RESTART_S = 1.0             # so lange wartet ein Zubringer nach seinem Ende, bevor er den Stream neu holt
BACK_S = 3.0                # so lange muss die eigentliche Hauptkamera wieder Bilder liefern, bevor sie die Ersatz-Hauptkamera ablöst
KEY_RE = re.compile(r"^[a-z0-9_-]{1,40}$")


def feeder_cmd(key, slot, rtmp_port=1935, rtmp_app="publish", gst="gst-launch-1.0"):
    """Befehl (Liste) eines Zubringers. Der Schlüssel muss geprüft sein (a-z, 0-9, -, _); er geht nie über eine Shell.

    Bild: Stream unverändert (kein Umkodieren), SPS/PPS jede Sekunde mitgeschickt, damit eine neu startende Kette sofort einsteigt.
    Ton: AAC wird hier zu PCM 48 kHz Stereo gewandelt, weil die AAC-Einstellungen je Kamera und Neuverbindung wechseln können; die RTP-Kopfdaten der Sendekette
    wären dann falsch ("not-negotiated", gemessen). Die Zeitstempel setzt die Kette selbst beim Eintreffen (udpsrc do-timestamp)."""
    if not KEY_RE.match(key or "") or not 0 <= slot < SLOTS:
        raise ValueError("Schlüssel oder Platz ungültig")
    vp, ap = FEED_PORT + 2 * slot, FEED_PORT + 2 * slot + 1
    chain = (f"rtmpsrc location=rtmp://127.0.0.1:{int(rtmp_port)}/{rtmp_app}/{key} do-timestamp=true ! flvdemux name=d "
             f"d.video ! queue max-size-time=2000000000 max-size-buffers=0 max-size-bytes=0 ! h264parse config-interval=-1 ! "
             f"rtph264pay config-interval=1 pt=96 ! udpsink host=127.0.0.1 port={vp} sync=false "
             f"d.audio ! queue max-size-time=2000000000 max-size-buffers=0 max-size-bytes=0 ! aacparse ! avdec_aac ! audioconvert ! audioresample ! "
             f"audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved ! audiobuffersplit output-buffer-duration=1024/48000 ! "
             f"udpsink host=127.0.0.1 port={ap} sync=false")
    return [gst, "-q"] + chain.split()


class Binder:
    """Ordnet Kamera-Schlüsseln Plätze zu. Ein Schlüssel behält seinen Platz, solange er gewünscht ist; neue Schlüssel bekommen einen freien Platz.
    So bleibt beim Tausch oder Entfernen einer Kamera der Zubringer der übrigen unverändert."""

    def __init__(self, n=SLOTS):
        self.slot_key = [""] * n

    def update(self, wanted):
        """wanted: gewünschte Schlüssel (ohne Leere). Gibt True zurück, wenn sich etwas geändert hat."""
        want = []
        for k in wanted:
            if k and k not in want:
                want.append(k)
        before = list(self.slot_key)
        for i, k in enumerate(self.slot_key):
            if k and k not in want:
                self.slot_key[i] = ""
        for k in want:
            if k not in self.slot_key:
                free = next((i for i, v in enumerate(self.slot_key) if not v), None)
                if free is None:
                    break                                    # mehr Kameras als Plätze: die übrigen bleiben ohne Platz
                self.slot_key[free] = k
        return before != self.slot_key

    def slot_of(self, key):
        return self.slot_key.index(key) if key in self.slot_key else None


class Chooser:
    """Wählt aus der Einstellung (wer soll Hauptbild und kleine Bilder sein) und den Kameras mit Bild die Belegung der Sendekette.

    Ist die eingestellte Hauptkamera nicht da, wird die erste vorhandene Kamera der kleinen Bilder (in der Reihenfolge der Einstellung) Hauptbild; deaktivierte
    Kameras springen nicht ein (Issue #19). Deren eigener Platz bleibt dann leer, damit sie nicht doppelt zu sehen ist. Kommt die eingestellte Hauptkamera zurück,
    übernimmt sie nach BACK_S wieder. Kleine Bilder werden immer an ihrem Platz gezeigt: die Kamera erscheint, sobald ihre Bilder ankommen (der Mischer blendet
    ein Bild ohne frische Bilder aus), es braucht dafür keinen Befehl."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.alive_since = {}                # Schlüssel -> seit wann er ununterbrochen Bilder liefert
        self.main_key = None                 # wer zuletzt als Hauptbild gewählt war
        self.desired_main = None             # und wer es laut Einstellung sein sollte

    def line(self, desired, slot_of, alive, inactive=(), now=None):
        """desired: Schlüssel an Hauptbild, kleines Bild 1 bis 3 ("" = keiner). slot_of: Schlüssel -> Platz. alive: Schlüssel mit Bild.
        Gibt (Zeile "H K1 K2 K3" mit Plätzen, 15 = keine, Schlüssel des Hauptbildes) zurück."""
        now = self.clock() if now is None else now
        for k in list(self.alive_since):
            if k not in alive:
                del self.alive_since[k]
        for k in alive:
            self.alive_since.setdefault(k, now)
        want = list(desired) + [""] * (4 - len(desired))
        main = want[0]
        if main and main not in alive:
            sub = next((k for k in want[1:] if k and k in alive and k not in set(inactive)), None)
            main = sub or main
        elif (main and main == self.desired_main and self.main_key not in (None, main) and self.main_key in alive
              and now - self.alive_since.get(main, now) < BACK_S):
            main = self.main_key                          # die eigentliche Hauptkamera ist erst kurz zurück: die Ersatzkamera bleibt noch
        self.desired_main = want[0]                       # (wurde die Hauptkamera in der Einstellung gewechselt, gilt der Wunsch sofort)
        self.main_key = main
        vals = [slot_of.get(main, 0) if main in slot_of else 0]
        for k in want[1:]:
            vals.append(15 if (not k or k == main or k not in slot_of) else slot_of[k])
        return " ".join(str(v) for v in vals), main


def alive_from_file(path, slot_key):
    """Schlüssel der Kameras mit Bild aus der Datei von pbctl ("1 0 1 0", ein Wert je Platz). None, wenn die Datei fehlt oder nicht lesbar ist."""
    try:
        with open(path) as f:
            flags = f.read().split()
    except OSError:
        return None
    if len(flags) != SLOTS or any(x not in ("0", "1") for x in flags):
        return None
    return {slot_key[i] for i, x in enumerate(flags) if x == "1" and slot_key[i]}


class Feeders:
    """Ein Zubringer je belegtem Platz. Jeder läuft in einem eigenen Faden, startet bei Ende neu und wird mit dem Platz beendet."""

    def __init__(self, rtmp_port=1935, rtmp_app="publish", log=print, popen=subprocess.Popen, clock=time.monotonic, killpg=os.killpg):
        self.rtmp_port, self.rtmp_app, self.log, self.popen, self.clock, self.killpg = rtmp_port, rtmp_app, log, popen, clock, killpg
        self.lock = threading.Lock()
        self.running = {}                    # Platz -> {"key", "stop", "thread", "proc"}
        self.starts = {i: 0 for i in range(SLOTS)}

    def set_keys(self, slot_key):
        """Zubringer an die Belegung anpassen: neue Schlüssel starten, geänderte neu starten, nicht mehr belegte beenden."""
        with self.lock:
            for slot in range(SLOTS):
                key = slot_key[slot] if slot < len(slot_key) else ""
                cur = self.running.get(slot)
                if cur and cur["key"] == key:
                    continue
                if cur:
                    self._stop_locked(slot)
                if key:
                    self._start_locked(slot, key)

    def _start_locked(self, slot, key):
        st = {"key": key, "stop": threading.Event(), "proc": None}
        st["thread"] = threading.Thread(target=self._loop, args=(slot, st), daemon=True, name=f"feeder{slot}")
        self.running[slot] = st
        st["thread"].start()

    def _stop_locked(self, slot):
        st = self.running.pop(slot, None)
        if not st:
            return
        st["stop"].set()
        self._kill(st.get("proc"))

    def _kill(self, p):
        if p is None or p.poll() is not None:
            return
        try:
            self.killpg(p.pid, signal.SIGTERM)                  # die ganze Gruppe (der Zubringer startet eigene Fäden, kein Kindprozess bleibt zurück)
        except OSError:
            try:
                p.terminate()
            except OSError:
                pass

    def _loop(self, slot, st):
        key = st["key"]
        while not st["stop"].is_set():
            try:
                cmd = feeder_cmd(key, slot, self.rtmp_port, self.rtmp_app)
                p = self.popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            except (OSError, ValueError) as e:
                if st.get("warned") != type(e).__name__:          # einmal melden statt alle 5 s
                    st["warned"] = type(e).__name__
                    self.log(f"send: Zubringer {slot} nicht startbar ({type(e).__name__}"
                             + (": gst-launch-1.0 fehlt, Paket gstreamer1.0-tools)" if isinstance(e, FileNotFoundError) else ")"))
                if st["stop"].wait(5):
                    return
                continue
            st["proc"] = p
            self.starts[slot] += 1
            while p.poll() is None and not st["stop"].is_set():
                time.sleep(0.2)
            if st["stop"].is_set():
                self._kill(p)
                return
            st["stop"].wait(RESTART_S)                      # beendet (kein Stream da oder er ist zu Ende): in einer Sekunde neu holen

    def stop_all(self):
        with self.lock:
            for slot in list(self.running):
                self._stop_locked(slot)


class Controller:
    """Hält die Zubringer und die Belegung der Sendekette im Betrieb auf dem Stand der Einstellung und der Kameras mit Bild.

    tick() wird etwa alle 0,5 s gerufen: liest pipeline.json (bei Änderung), ordnet Kameras den Plätzen zu (Zubringer anpassen, Verzögerungsdatei schreiben),
    liest die Datei von pbctl (welche Plätze liefern Bilder), wählt die Belegung (Chooser) und schreibt bei Änderung die Umschaltzeile (select-file)."""

    def __init__(self, load_json, put_state_file, delay_values, cam_live_path, select_name, delay_name, feeders, log=print, clock=time.monotonic):
        self.load_json, self.put, self.delay_values = load_json, put_state_file, delay_values
        self.cam_live_path, self.select_name, self.delay_name = cam_live_path, select_name, delay_name
        self.feeders, self.log, self.clock = feeders, log, clock
        self.binder = Binder()
        self.chooser = Chooser(clock)
        self.cfg = {}
        self.desired = ["", "", "", ""]
        self.inactive = []
        self.last_line = None
        self.main_key = None
        self.alive = set()
        self.live_known = False
        self.last_cfg_sig = None

    @staticmethod
    def keys_of(cfg):
        out = []
        for k in ("main", "pip", "pip2", "pip3"):
            v = cfg.get(k, "") if cfg.get("type") == "pip" or k == "main" else ""
            out.append(v if isinstance(v, str) and KEY_RE.match(v) else "")
        return out

    def reload(self, cfg=None):
        """Einstellung lesen (oder übernehmen) und Plätze zuordnen. Gibt True zurück, wenn sich die Belegung der Plätze geändert hat."""
        cfg = cfg if cfg is not None else self.load_json("pipeline.json")
        sig = repr(sorted((k, repr(v)) for k, v in cfg.items()))
        if sig == self.last_cfg_sig:
            return False
        self.last_cfg_sig = sig
        self.cfg = cfg
        self.desired = self.keys_of(cfg)
        ina = cfg.get("inactive")
        self.inactive = [k for k in ina if isinstance(k, str)] if isinstance(ina, list) else []
        changed = self.binder.update([k for k in self.desired if k])
        if changed:
            self.feeders.set_keys(self.binder.slot_key)
        vals = self.delay_values(cfg, [k or None for k in self.binder.slot_key])
        try:
            self.put(self.delay_name, " ".join(map(str, vals)) + "\n")
        except OSError:
            pass
        return changed

    def start(self):
        self.reload()
        self.feeders.set_keys(self.binder.slot_key)

    def tick(self):
        """Ein Durchgang. Gibt den Zustand für die Anzeige zurück: {"slots", "alive", "main", "line"}."""
        self.reload()
        alive = alive_from_file(self.cam_live_path, self.binder.slot_key)
        if alive is not None:
            self.alive, self.live_known = alive, True
        if self.live_known:
            slot_of = {k: i for i, k in enumerate(self.binder.slot_key) if k}
            line, main = self.chooser.line(self.desired, slot_of, self.alive, self.inactive)
            if line != self.last_line:
                if main != self.main_key and self.main_key is not None:
                    self.log(f"send: Hauptbild jetzt {main} (eingestellt: {self.desired[0]})")
                self.main_key = main
                try:
                    self.put(self.select_name, line + "\n")
                    self.last_line = line
                except OSError:
                    pass
        return {"slots": list(self.binder.slot_key), "alive": sorted(self.alive), "main": self.main_key, "line": self.last_line}

    def stop(self):
        self.feeders.stop_all()
