"""DJI-Kameras per Bluetooth-LE suchen, koppeln und den RTMP-Stream starten.

Ablauf und Nachrichtenformat folgen dem Projekt Moblin (MIT-Lizenz,
Copyright (c) 2023 Erik Moqvist), Ordner Moblin/Integrations/Dji/. Gegenprobe
mit dem Android-Nachbau dimadesu/dji-remote (MIT). Dies ist ein Nachbau von
Moblins Reverse-Engineering, nicht von DJI dokumentiert.

Funk läuft über BlueZ (D-Bus). Läuft nur auf Linux mit python3-dbus/python3-gi.
"""
import json
import queue
import threading
import time

# ---------------------------------------------------------------- Protokoll

PAIR_PIN = "mbln"
T_PAIR, T_STOP, T_PREPARE, T_WIFI, T_START, T_CONFIG = 0x8092, 0xEAC8, 0x8C12, 0x8C19, 0x8C2C, 0x8C2D
PAIR_PAYLOAD = bytes.fromhex(
    "20" + "".join("%02x" % c for c in b"284ae5b8d76b3375a04a6417ad71bea3"))
COMPANY_IDS = (0x08AA, 0xF7AA)  # Bytes AA 08 / AA F7 in den Herstellerdaten

MODELS = {  # Bytes 2-3 der Herstellerdaten (Moblin: djiModelFromManufacturerData)
    bytes([0x10, 0x00]): "osmoAction2", bytes([0x12, 0x00]): "osmoAction3",
    bytes([0x14, 0x00]): "osmoAction4", bytes([0x15, 0x00]): "osmoAction5Pro",
    bytes([0x17, 0x00]): "osmo360", bytes([0x18, 0x00]): "osmoAction6",
    bytes([0x20, 0x00]): "osmoPocket3", bytes([0x21, 0x00]): "osmoPocket4",
}
MODEL_NAMES = {
    "osmoAction2": "Osmo Action 2", "osmoAction3": "Osmo Action 3",
    "osmoAction4": "Osmo Action 4", "osmoAction5Pro": "Osmo Action 5 Pro",
    "osmoAction6": "Osmo Action 6", "osmo360": "Osmo 360",
    "osmoPocket3": "Osmo Pocket 3", "osmoPocket4": "Osmo Pocket 4", "unknown": "DJI-Gerät",
}
NEW_PROTOCOL = {"osmoAction5Pro", "osmoAction6", "osmoPocket4", "osmo360"}
HAS_STABILIZATION = {"osmoAction4", "osmoAction5Pro", "osmoAction6", "osmo360"}
RES = {"480p": 0x47, "720p": 0x04, "1080p": 0x0A}
FPS = {25: 2, 30: 3}
STAB = {"off": 0, "rockSteady": 1, "rockSteadyPlus": 3, "horizonBalancing": 4, "horizonSteady": 2}


def _crc(data, init, rpoly, mask):
    crc = init
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ rpoly if crc & 1 else crc >> 1
    return crc & mask


def crc8(data):   # init 0xEE, poly 0x31, spiegelt: Startwert 0x77, Polynom 0x8C
    return _crc(data, 0x77, 0x8C, 0xFF)


def crc16(data):  # init 0x496C, poly 0x1021, gespiegelt: 0x3692 / 0x8408
    return _crc(data, 0x3692, 0x8408, 0xFFFF)


def pack_string(s):
    b = s.encode()
    return bytes([len(b) & 0xFF]) + b


def pack_url(s):
    b = s.encode()
    return bytes([len(b) & 0xFF, 0]) + b


def encode(target, mid, mtype, payload):
    head = bytes([0x55, (13 + len(payload)) & 0xFF, 0x04])
    head += bytes([crc8(head)])
    body = head + target.to_bytes(2, "little") + mid.to_bytes(2, "little") \
        + mtype.to_bytes(3, "little") + payload
    return body + crc16(body).to_bytes(2, "little")


def decode(data):
    """Liefert (target, id, type, payload) oder None bei beschädigter Nachricht."""
    data = bytes(data)
    if len(data) < 13 or data[0] != 0x55 or data[1] != len(data) or data[2] != 0x04:
        return None
    if data[3] != crc8(data[:3]) or int.from_bytes(data[-2:], "little") != crc16(data[:-2]):
        return None
    return (int.from_bytes(data[4:6], "little"), int.from_bytes(data[6:8], "little"),
            int.from_bytes(data[8:11], "little"), data[11:-2])


def start_payload(model, url, res, fps, kbps, codec="HEVC"):
    r, f, kb = RES[res], FPS.get(fps, 0), (kbps & 0xFFFF).to_bytes(2, "little")
    if model in ("osmoPocket4", "osmoAction6"):
        header, middle = ((b"\x01\xb5\x00", b"\x02\x01") if model == "osmoPocket4"
                          else (b"\x01\x9c\x00", b"\xfe\x00"))
        # Byte-genau wie Swifts JSONEncoder (Feldreihenfolge, true/false, "\/" für "/"):
        js = ('{"codec":"%s","EnhancedRTMP":%s,"supportStopLive":false,"watermark":0,'
              '"rtmpAddress":%s,"orientation":"landscape"}' % (
                  codec, "true" if codec == "HEVC" else "false",
                  json.dumps(url).replace("/", "\\/"))).encode()
        return header + bytes([r]) + kb + middle + bytes([f]) + b"\x00\x00\x00" \
            + len(js).to_bytes(2, "little") + js
    oa5 = 0x2A if model in NEW_PROTOCOL else 0x2E
    return b"\x00" + bytes([oa5]) + b"\x00" + bytes([r]) + kb + b"\x02\x00" + bytes([f]) \
        + b"\x00\x00\x00" + pack_url(url)


def config_payload(model, stab):
    b1 = 0x1A if model in ("osmoAction5Pro", "osmo360") else 0x08
    return b"\x01\x01" + bytes([b1]) + b"\x00\x01" + bytes([STAB.get(stab, 0)])


STOP_PAYLOAD = bytes.fromhex("0101 1a00 0102".replace(" ", ""))
CONFIRM_PAYLOAD = bytes.fromhex("0101 1a00 0101".replace(" ", ""))


# ---------------------------------------------------------------- BlueZ

BLUEZ = "org.bluez"
OM = "org.freedesktop.DBus.ObjectManager"
PROPS = "org.freedesktop.DBus.Properties"
UUID_FFF4 = "0000fff4-0000-1000-8000-00805f9b34fb"
UUID_FFF5 = "0000fff5-0000-1000-8000-00805f9b34fb"


class Dji:
    """Eine Suche und höchstens eine Kopplungs-/Streamsitzung gleichzeitig."""

    def __init__(self):
        import dbus
        import dbus.mainloop.glib
        from gi.repository import GLib
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.dbus = dbus
        self.bus = dbus.SystemBus()
        self.loop = GLib.MainLoop()
        threading.Thread(target=self.loop.run, daemon=True).start()
        self.lock = threading.Lock()
        self.devices = []
        self.scanning = False
        self._fails = {}               # Adresse -> Fehlversuche in Folge
        self._conn_lock = threading.Lock()   # nur eine Kamera gleichzeitig verbinden (ein Funk-Chip schafft das nicht parallel)
        self._disc_lock = threading.Lock()
        self._disc_users = 0
        self._disc_ads = []
        self.scan_error = ""
        self.sessions = {}      # Adresse -> {"state", "message", "name"}
        self._stops = {}        # Adresse -> Event
        self._boxes = {}        # Geräte-Pfad -> Queue (Antworten der Kamera)
        self.bus.add_signal_receiver(self._on_props, dbus_interface=PROPS,
                                     signal_name="PropertiesChanged", path_keyword="path")

    # -- Hilfen
    ONBOARD = "v13D3p3572"   # eingebautes Realtek-Modul der ROCK 5B+ (empfängt dort nichts)

    def adapter_paths(self):
        """Alle Bluetooth-Adapter; externe (Stick) zuerst, der eingebaute zuletzt."""
        found = []
        for path, ifs in self.objects().items():
            a = ifs.get("org.bluez.Adapter1")
            if a:
                onboard = self.ONBOARD.lower() in str(a.get("Modalias", "")).lower()
                found.append((onboard, str(path)))
        return [p for _, p in sorted(found)]

    def adapter_path(self):
        paths = self.adapter_paths()
        return paths[0] if paths else None

    def cleanup_stale(self, exclude=()):
        """Getrennt werden DJI-Geräte, die BlueZ als verbunden führt, obwohl wir keine
        Sitzung dafür haben. Solche Geräte senden keine Werbesignale mehr und sind für
        die Suche unsichtbar (z. B. nach einem Neustart unseres Dienstes)."""
        ex = {a.upper() for a in exclude}
        n = 0
        for path, ifs in self.objects().items():
            dv = ifs.get("org.bluez.Device1")
            if not dv or not dv.get("Connected"):
                continue
            if not any(int(c) in COMPANY_IDS for c in (dv.get("ManufacturerData") or {})):
                continue
            if str(dv.get("Address")).upper() in ex:
                continue
            try:
                self.dbus.Interface(self.bus.get_object(BLUEZ, str(path)), "org.bluez.Device1").Disconnect()
                n += 1
            except Exception:
                pass
        if n:
            print(f"dji: {n} hängende Verbindung(en) ohne Sitzung getrennt", flush=True)
        return n

    def active_addresses(self):
        with self.lock:
            return [a for a, s in self.sessions.items() if s.get("state") not in ("idle", "failed")]

    def objects(self):
        return self.dbus.Interface(self.bus.get_object(BLUEZ, "/"), OM).GetManagedObjects()

    def status(self):
        with self.lock:
            return {"scanning": self.scanning, "scan_error": self.scan_error,
                    "devices": list(self.devices),
                    "sessions": {k: dict(v) for k, v in self.sessions.items()}}

    def _set(self, address, state, message=""):
        if state == "streaming":
            self._fails[address] = 0
        with self.lock:
            self.sessions.setdefault(address, {}).update(state=state, message=message)
        print(f"dji: {address[-8:]} {state} {message}", flush=True)   # ins Journal (nie WLAN-Daten)

    def _collect_dji(self, seen):
        """Trägt alle gerade bekannten DJI-Geräte in seen ein (Adresse -> Eintrag); gibt die Zahl aller BlueZ-Geräte zurück."""
        total = 0
        for path, ifs in self.objects().items():
            d = ifs.get("org.bluez.Device1")
            if not d:
                continue
            total += 1
            for cid, val in (d.get("ManufacturerData") or {}).items():
                if int(cid) in COMPANY_IDS:
                    model = MODELS.get(bytes(bytearray(int(x) for x in val))[:2], "unknown")
                    addr = str(d.get("Address"))
                    old = seen.get(addr)
                    rssi = int(d["RSSI"]) if "RSSI" in d else (old or {}).get("rssi")
                    seen[addr] = {"address": addr, "name": str(d.get("Alias") or d.get("Name") or ""),
                                  "model": model, "model_name": MODEL_NAMES[model], "rssi": rssi}
        return total

    def scan(self, seconds=30):
        with self.lock:
            if self.scanning:
                return
            self.scanning = True
            self.scan_error = ""
        print(f"dji: Suche gestartet ({seconds} s)", flush=True)
        threading.Thread(target=self._scan, args=(seconds,), daemon=True).start()

    def _scan(self, seconds):
        try:
            aps = self.adapter_paths()
            if not aps:
                raise RuntimeError("Kein Bluetooth-Adapter gefunden")
            self.cleanup_stale(exclude=self.active_addresses())
            if not self._disc_acquire():
                self._disc_release()
                raise RuntimeError("Kein Bluetooth-Adapter ließ sich starten")
            # Während der Suche mitlesen und alles merken: BlueZ vergisst nicht verbundene Geräte schnell
            # wieder, und manche Kameras (z. B. Action 5 Pro) melden sich erst nach über 10 s.
            seen, total = {}, 0
            end = time.time() + seconds
            while True:
                total = max(total, self._collect_dji(seen))
                if time.time() >= end:
                    break
                time.sleep(1)
            self._disc_release()
            total = max(total, self._collect_dji(seen))
            found = sorted(seen.values(), key=lambda x: -(x["rssi"] if x["rssi"] is not None else -999))
            with self.lock:
                self.devices = found
                self.scan_error = "" if total else (
                    "Es wurden gar keine Bluetooth-Geräte empfangen. Antennen am Funkmodul prüfen "
                    "oder einen USB-Bluetooth-Stick verwenden.")
        except Exception as e:
            with self.lock:
                self.scan_error = f"Suche fehlgeschlagen: {e}"
        finally:
            with self.lock:
                self.scanning = False
                print(f"dji: Suche beendet, {len(self.devices)} DJI-Gerät(e), Fehler: {self.scan_error or '-'}", flush=True)

    # -- Sitzung
    def _on_props(self, interface, changed, invalidated, path=None):
        if interface == "org.bluez.GattCharacteristic1" and "Value" in changed:
            for dev_path, q in list(self._boxes.items()):
                if str(path).startswith(dev_path + "/"):
                    q.put(bytes(bytearray(int(x) for x in changed["Value"])))

    def start(self, address, model, ssid, password, url, res="1080p", fps=30,
              kbps=6000, codec="HEVC", stab="off"):
        key = address.upper()
        with self.lock:
            cur = self.sessions.get(key)
            if cur and cur.get("state") not in ("idle", "failed"):
                raise ValueError("Diese Kamera wird schon verbunden oder streamt")
            self.sessions[key] = {"state": "connecting", "message": ""}
            self._stops[key] = threading.Event()
        threading.Thread(
            target=self._run, daemon=True,
            args=(key, model, ssid, password, url, res, fps, kbps, codec, stab)).start()

    def stop(self, address=None):
        """Eine Kamera (Adresse) oder alle beenden."""
        for key, ev in list(self._stops.items()):
            if address is None or key == address.upper():
                ev.set()

    @staticmethod
    def _expect(q, stop, mid, timeout, any_message=False):
        end = time.time() + timeout
        while time.time() < end:
            if stop.is_set():
                raise RuntimeError("Abgebrochen")
            try:
                raw = q.get(timeout=0.5)
            except queue.Empty:
                continue
            m = decode(raw)
            if m is None:
                print(f"dji: unlesbare Nachricht ({len(raw)} Bytes)", flush=True)
                continue
            print(f"dji: Antwort id={m[1]:#06x} typ={m[2]:#08x} ({len(m[3])} Bytes)", flush=True)
            if any_message or m[1] == mid:
                return m
        raise TimeoutError("Keine Antwort der Kamera")

    def _trust(self, path):
        """Merkt die Kamera dauerhaft in BlueZ. Sonst vergisst BlueZ nicht verbundene Geräte schon nach Sekunden
        und eine direkte Verbindung per Adresse (wie bei Moblin und dji-remote) ist nicht mehr möglich."""
        try:
            self.dbus.Interface(self.bus.get_object(BLUEZ, path), PROPS).Set(
                "org.bluez.Device1", "Trusted", self.dbus.Boolean(True))
        except self.dbus.DBusException as e:
            print(f"dji: Kamera konnte nicht gemerkt werden: {str(e)[:60]}", flush=True)

    def forget(self, address, even_trusted=False):
        """Entfernt einen hängenden BlueZ-Eintrag dieser Kamera (nur wenn nicht verbunden). Gemerkte (vertraute)
        Kameras bleiben, außer es wird ausdrücklich verlangt."""
        n = 0
        for path, ifs in self.objects().items():
            d = ifs.get("org.bluez.Device1")
            if not d or str(d.get("Address")).upper() != address.upper() or d.get("Connected"):
                continue
            if d.get("Trusted") and not even_trusted:
                continue
            try:
                self.dbus.Interface(self.bus.get_object(BLUEZ, str(d.get("Adapter"))),
                                    "org.bluez.Adapter1").RemoveDevice(path)
                n += 1
            except self.dbus.DBusException:
                pass
        if n:
            print(f"dji: {address[-8:]} alter BlueZ-Eintrag verworfen", flush=True)
        return n

    def _find_path(self, address, live=False):
        """BlueZ-Pfad der Kamera (bevorzugt der Adapter, der sie gerade hört), sonst None.
        live=True: nur, wenn die Kamera gerade funkt (RSSI), sonst ist der Eintrag ein Rest, den BlueZ gleich löscht."""
        order = {a: i for i, a in enumerate(self.adapter_paths())}
        best = None
        for p, ifs in self.objects().items():
            d = ifs.get("org.bluez.Device1")
            if d and str(d.get("Address")).upper() == address.upper():
                if live and "RSSI" not in d and not d.get("Connected"):
                    continue
                rank = (0 if "RSSI" in d else 1, order.get(str(d.get("Adapter")), 99))
                if best is None or rank < best[0]:
                    best = (rank, str(p))
        return best[1] if best else None

    def _discover_for(self, address, stop, seconds):
        """Sucht gezielt, bis genau diese Kamera auftaucht (BlueZ vergisst Geräte, die länger nichts senden).
        Nutzt die gemeinsame Suche mit: läuft schon eine, wird sie weder neu gestartet noch beendet."""
        if not self._disc_acquire():
            self._disc_release()
            return None
        try:
            end = time.time() + seconds
            while time.time() < end and not stop.is_set():
                path = self._find_path(address, live=True)
                if path:
                    return path
                time.sleep(1)
            return None
        finally:
            self._disc_release()

    def _disc_acquire(self):
        """Gemeinsame Suche: nur der erste Nutzer startet sie. Gibt die Zahl der laufenden Adapter-Suchen zurück."""
        with self._disc_lock:
            if self._disc_users == 0:
                self._disc_ads = self._start_discovery()
            self._disc_users += 1
            return len(self._disc_ads)

    def _disc_release(self):
        """Nur der letzte Nutzer beendet die Suche."""
        with self._disc_lock:
            self._disc_users = max(0, self._disc_users - 1)
            if self._disc_users == 0:
                for ad in self._disc_ads:
                    try:
                        ad.StopDiscovery(timeout=8)
                    except self.dbus.DBusException:
                        pass
                self._disc_ads = []

    def _reset_adapter(self, ap):
        """Selbstheilung: Antwortet ein Controller nicht mehr auf "Suche starten" (kommt bei den Realtek-Chips vor),
        wird er einmal aus- und eingeschaltet. Nur wenn keine Kamera daran hängt, sonst würde sie getrennt."""
        ap = str(ap)
        for path, ifs in self.objects().items():
            d = ifs.get("org.bluez.Device1")
            if d and str(d.get("Adapter")) == ap and d.get("Connected"):
                print(f"dji: {ap.rsplit('/', 1)[-1]} antwortet nicht, hat aber verbundene Kameras: kein Zurücksetzen", flush=True)
                return False
        try:
            props = self.dbus.Interface(self.bus.get_object(BLUEZ, ap), PROPS)
            props.Set("org.bluez.Adapter1", "Powered", self.dbus.Boolean(False))
            time.sleep(1)
            props.Set("org.bluez.Adapter1", "Powered", self.dbus.Boolean(True))
            time.sleep(2)
            print(f"dji: {ap.rsplit('/', 1)[-1]} antwortete nicht und wurde zurückgesetzt", flush=True)
            return True
        except self.dbus.DBusException as e:
            print(f"dji: Zurücksetzen von {ap.rsplit('/', 1)[-1]} fehlgeschlagen: {str(e)[:60]}", flush=True)
            return False

    def _start_discovery(self):
        started = []
        for ap in self.adapter_paths():
            try:
                props = self.dbus.Interface(self.bus.get_object(BLUEZ, ap), PROPS)
                if not props.Get("org.bluez.Adapter1", "Powered"):
                    props.Set("org.bluez.Adapter1", "Powered", self.dbus.Boolean(True))
                ad = self.dbus.Interface(self.bus.get_object(BLUEZ, ap), "org.bluez.Adapter1")
                try:
                    ad.SetDiscoveryFilter(self.dbus.Dictionary(
                        {"Transport": self.dbus.String("le"),
                         "DuplicateData": self.dbus.Boolean(False)}, signature="sv"), timeout=8)
                except Exception:   # Filter ist nur eine Optimierung
                    pass
                try:
                    ad.StartDiscovery(timeout=8)
                except self.dbus.DBusException as e:
                    if "InProgress" in str(e):
                        pass
                    elif "NoReply" in str(e) and self._reset_adapter(ap):
                        ad.StartDiscovery(timeout=8)       # nach dem Zurücksetzen noch einmal
                    else:
                        raise
                started.append(ad)
            except self.dbus.DBusException as e:
                print(f"dji: Suche auf {str(ap).rsplit('/', 1)[-1]} nicht gestartet: {str(e)[:60]}", flush=True)
        return started

    def _acquire_conn(self, address, stop):
        """Wartet, bis keine andere Kamera gerade verbindet. Mehrere gleichzeitige Verbindungsversuche brechen sich auf
        dem Funk-Chip gegenseitig ab ("le-connection-abort-by-local", "Dienste nicht aufgelöst")."""
        waited = False
        while not self._conn_lock.acquire(timeout=0.5):
            if stop.is_set():
                raise RuntimeError("Abgebrochen")
            if not waited:
                waited = True
                self._set(address, "connecting", "Wartet, bis eine andere Kamera fertig verbunden ist")
        return True

    def _release_conn(self):
        time.sleep(1)                  # dem Funk-Chip einen Moment geben, bevor die nächste Kamera dran ist
        self._conn_lock.release()

    def _run(self, address, model, ssid, password, url, res, fps, kbps, codec, stab):
        dev = None
        held = False
        disc = False
        q = queue.Queue()
        stop = self._stops[address]
        expect = lambda mid, timeout, any_message=False: self._expect(q, stop, mid, timeout, any_message)
        path = None
        try:
            if self._fails.get(address, 0) >= 2:
                self.forget(address, self._fails.get(address, 0) >= 4)   # hängenden BlueZ-Eintrag früh verwerfen
            # Die Suche bleibt an, bis Connect fertig ist: BlueZ vergisst Kameras, die noch nicht dauerhaft gekoppelt sind,
            # sobald die Suche endet ("Kamera nicht mehr sichtbar"). Gemessen: Verbinden klappte nur, solange eine Suche lief.
            self._disc_acquire()
            disc = True
            path = self._find_path(address, live=True)
            if not path:
                self._set(address, "connecting", "Kamera wird gesucht")
                path = self._discover_for(address, stop, 20)
            if not path:
                raise RuntimeError("Kamera nicht gefunden. Ist sie an, Bluetooth aktiv und nicht mit dem Handy verbunden?")
            held = self._acquire_conn(address, stop)
            self._boxes[path] = q
            dev = self.dbus.Interface(self.bus.get_object(BLUEZ, path), "org.bluez.Device1")
            dprops = self.dbus.Interface(self.bus.get_object(BLUEZ, path), PROPS)
            self._set(address, "connecting", "Verbinde per Bluetooth")
            try:
                dev.Connect(timeout=40)
            except self.dbus.DBusException as e:
                if "UnknownObject" in str(e):
                    raise RuntimeError("Die Kamera ist für Bluetooth nicht mehr sichtbar. Ist sie an, Bluetooth aktiv "
                                       "(bei neuen Kameras im Kopplungsmodus) und nicht mit dem Handy verbunden?")
                raise
            if disc:
                disc = False
                self._disc_release()                # verbunden: die Suche wird nicht mehr gebraucht
            for _ in range(60):
                if dprops.Get("org.bluez.Device1", "ServicesResolved"):
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("Dienste der Kamera nicht aufgelöst")
            self._trust(path)                 # BlueZ behält die Kamera, auch wenn sie gerade nicht funkt
            write_char = None
            for p, ifs in self.objects().items():
                c = ifs.get("org.bluez.GattCharacteristic1")
                if not c or not str(p).startswith(path):
                    continue
                if str(c.get("UUID")).lower() == UUID_FFF5:
                    write_char = self.dbus.Interface(self.bus.get_object(BLUEZ, p),
                                                     "org.bluez.GattCharacteristic1")
                flags = [str(f) for f in c.get("Flags", [])]
                if "notify" in flags or "indicate" in flags:
                    try:
                        self.dbus.Interface(self.bus.get_object(BLUEZ, p),
                                            "org.bluez.GattCharacteristic1").StartNotify()
                    except self.dbus.DBusException:
                        pass
            if write_char is None:
                raise RuntimeError("Schreib-Kanal FFF5 nicht gefunden (kein DJI-Gerät?)")
            held = False
            self._release_conn()           # Verbindung steht: die nächste Kamera darf jetzt verbinden

            def send(target, mid, mtype, payload):
                data = encode(target, mid, mtype, payload)
                write_char.WriteValue([self.dbus.Byte(b) for b in data],
                                      {"type": self.dbus.String("command")})

            self._set(address, "pairing", "Koppeln. Falls die Kamera fragt, bitte dort bestätigen.")
            send(0x0702, T_PAIR, 0x450740, PAIR_PAYLOAD + pack_string(PAIR_PIN))
            m = expect(T_PAIR, 30)
            if m[3] != b"\x00\x01":          # noch nicht gekoppelt: auf Bestätigung warten
                expect(0, 60, any_message=True)
            self._set(address, "preparing", "Stream wird vorbereitet")
            send(0x0802, T_STOP, 0x8E0240, STOP_PAYLOAD)
            expect(T_STOP, 15)
            send(0x0802, T_PREPARE, 0xE10240, b"\x1a")
            expect(T_PREPARE, 15)
            self._set(address, "wifi", "WLAN-Daten werden an die Kamera übergeben")
            send(0x0702, T_WIFI, 0x470740, pack_string(ssid) + pack_string(password))
            m = expect(T_WIFI, 40)
            if m[3] != b"\x00\x00":
                raise RuntimeError("Die Kamera konnte dem WLAN nicht beitreten (Name/Passwort?)")
            if model in ("osmoAction4", "osmoAction6", "osmoAction5Pro", "osmo360"):
                send(0x0102, T_CONFIG, 0x8E0240, config_payload(model, stab))
                expect(T_CONFIG, 15)
            self._set(address, "starting", "Stream wird gestartet")
            send(0x0802, T_START, 0x780840, start_payload(model, url, res, fps, kbps, codec))
            if model in NEW_PROTOCOL:
                send(0x0802, T_STOP, 0x8E0240, CONFIRM_PAYLOAD)
            expect(T_START, 30)
            self._set(address, "streaming", "Die Kamera streamt. Sie erscheint in der Kameraliste.")
            while not stop.is_set():
                time.sleep(1)
            self._set(address, "stopping", "Stream wird beendet")
            send(0x0802, T_STOP, 0x8E0240, STOP_PAYLOAD)
            try:
                expect(T_STOP, 10)
            except TimeoutError:
                pass
            self._set(address, "idle", "Beendet")
        except Exception as e:
            import traceback
            print("dji: Ausnahme im Ablauf:\n" + traceback.format_exc(), flush=True)
            self._fails[address] = self._fails.get(address, 0) + 1
            self._set(address, "failed", str(e) or e.__class__.__name__)
        finally:
            if disc:
                disc = False
                self._disc_release()
            if held:
                held = False
                self._release_conn()
            if path:
                self._boxes.pop(path, None)
            self._stops.pop(address, None)
            try:
                if dev:
                    dev.Disconnect()
            except Exception:
                pass
