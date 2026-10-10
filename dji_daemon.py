#!/usr/bin/env python3
"""Bluetooth-Dienst für DJI-Kameras (pipbox-dji.service).

Koppelt DJI-Osmo-Kameras per Bluetooth LE, sagt ihnen, in welches WLAN sie sich einbuchen sollen, und startet den
RTMP-Livestream zur Box (nginx-rtmp, Port 1935). Jede Kamera hat ihre eigene Verbindung: aus allen aktiven Verbindungen der
Box wird gewählt (WLAN-Hotspot und WLAN-Client-Netze mit Name und Passwort aus NetworkManager, alle anderen Verbindungen wie
Ethernet, USB-Router oder Modem mit einmal von Hand eingegebenem WLAN der Kamera).

Läuft getrennt von der Weboberfläche. Grund: Eine DJI-Kamera streamt nur, solange die Box die Bluetooth-Verbindung hält;
startet man die Oberfläche neu, würden sonst alle Kameras abbrechen. Der Dienst merkt sich die Kameras samt Einstellungen
(dji-cameras.json) und verbindet sie nach einem Ausfall selbst wieder.

Das BLE-Protokoll stammt von Moblin (https://github.com/eerimoq/moblin, MIT, Copyright (c) 2023 Erik Moqvist), geprüft gegen
datagutt/node-osmo (MIT). Ablauf der Sitzung, Verbindungsliste und Befehle folgen dem DJI-Dienst von Bittersweet1987
(Copyright 2026), hier an IRL4YOU BOX angepasst (siehe NOTICE.md).

Schnittstelle: nur 127.0.0.1, JSON-Zeilen über TCP. Jede Anfrage braucht das Token aus <state>/dji-token (nur der Benutzer
pipbox kann es lesen). Passwörter der Kamera-WLANs liegen nur in dji-cameras.json (Rechte 0600), niemals im Journal und
nie in einer Antwort an den Browser.
"""
import argparse
import asyncio
import hmac
import json
import logging
import os
import re
import secrets
import signal
import struct
import subprocess
import time
import urllib.request

try:
    from bleak import BleakClient, BleakScanner
except ImportError:  # Protokoll und Befehle lassen sich auch ohne bleak testen
    BleakClient = BleakScanner = None

import dji
import controllers
import phone_battery

LISTEN_HOST, LISTEN_PORT = "127.0.0.1", 9101
RTMP_PORT = 1935
RTMP_APP = "publish"
STAT_URL = "http://127.0.0.1:1936/"          # nginx-rtmp-Statistik
ONBOARD_USB_ID = "13d3:3572"                 # eingebautes Realtek-Modul der ROCK 5B+ (empfängt dort nichts): Stick zuerst

log = logging.getLogger("pipbox-dji")


def _bleak_major():
    try:
        from importlib.metadata import version
        return int(version("bleak").split(".")[0])
    except Exception:
        return 0


BLEAK_MAJOR = _bleak_major()      # ab bleak 2 steht der Adapter in "bluez", bei 1.x in "adapter" (die alte Angabe warnt in 3.x)

# ---------------------------------------------------------------- Protokoll ---

FIRST_BYTE = 0x55
VERSION = 0x04


def crc_generic(data, width, poly, init, ref_in=True, ref_out=True, xor=0):
    """CRC bitweise, wie im CRC-Katalog parametriert."""
    mask = (1 << width) - 1
    top = 1 << (width - 1)
    crc = init
    if ref_in:
        # gespiegelter Algorithmus: Polynom und Startwert werden einmal vorab gespiegelt
        rpoly = int("{:0{w}b}".format(poly, w=width)[::-1], 2)
        rinit = int("{:0{w}b}".format(init, w=width)[::-1], 2)
        crc = rinit
        for b in data:
            crc ^= b
            for _ in range(8):
                crc = (crc >> 1) ^ rpoly if crc & 1 else crc >> 1
        if not ref_out:
            crc = int("{:0{w}b}".format(crc, w=width)[::-1], 2)
        return (crc ^ xor) & mask
    for b in data:
        crc ^= b << (width - 8)
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & mask if crc & top else (crc << 1) & mask
    if ref_out:
        crc = int("{:0{w}b}".format(crc, w=width)[::-1], 2)
    return (crc ^ xor) & mask


def crc8(data):
    return crc_generic(data, 8, 0x31, 0xEE, True, True, 0x00)


def crc16(data):
    return crc_generic(data, 16, 0x1021, 0x496C, True, True, 0x0000)


def pack_string(value):
    b = value.encode("utf-8")
    return bytes([len(b) & 0xFF]) + b


def pack_url(url):
    b = url.encode("utf-8")
    return bytes([len(b) & 0xFF, 0]) + b


class Message:
    def __init__(self, target, mid, mtype, payload=b""):
        self.target = target
        self.id = mid
        self.type = mtype
        self.payload = bytes(payload)

    def encode(self):
        out = bytearray([FIRST_BYTE, (13 + len(self.payload)) & 0xFF, VERSION])
        out.append(crc8(bytes(out)))
        out += self.target.to_bytes(2, "little")
        out += self.id.to_bytes(2, "little")
        out += self.type.to_bytes(3, "little")
        out += self.payload
        out += crc16(bytes(out)).to_bytes(2, "little")
        return bytes(out)

    @staticmethod
    def decode(data):
        data = bytes(data)
        if len(data) < 13 or data[0] != FIRST_BYTE:
            raise ValueError("falsches erstes Byte oder zu kurz")
        if data[1] != len(data):
            raise ValueError("falsche Länge")
        if data[2] != VERSION:
            raise ValueError("falsche Version")
        if data[3] != crc8(data[0:3]):
            raise ValueError("Kopf-Prüfsumme falsch")
        if int.from_bytes(data[-2:], "little") != crc16(data[:-2]):
            raise ValueError("Prüfsumme falsch")
        return Message(int.from_bytes(data[4:6], "little"),
                       int.from_bytes(data[6:8], "little"),
                       int.from_bytes(data[8:11], "little"),
                       data[11:-2])

    def __repr__(self):
        return "Message(target=0x%04x id=0x%04x type=0x%06x payload=%s)" % (
            self.target, self.id, self.type, self.payload.hex())


# Transaktions-IDs (der Wert ist egal, er muss nur zur Antwort passen)
ID_PAIR, ID_STOP, ID_PREPARE, ID_WIFI, ID_START, ID_CONFIGURE = 0x8092, 0xEAC8, 0x8C12, 0x8C19, 0x8C2C, 0x8C2D
# Ziele
T_PAIR, T_STOP, T_PREPARE, T_WIFI, T_CONFIGURE, T_START = 0x0702, 0x0802, 0x0802, 0x0702, 0x0102, 0x0802
# Typen
TY_PAIR, TY_STOP, TY_PREPARE, TY_WIFI, TY_CONFIGURE, TY_START, TY_STATUS = \
    0x450740, 0x8E0240, 0xE10240, 0x470740, 0x8E0240, 0x780840, 0x020D00

PAIR_PAYLOAD = bytes([0x20]) + b"284ae5b8d76b3375a04a6417ad71bea3"
PAIR_PIN = "mbln"      # die bisherige PIN dieses Projekts: schon gekoppelte Kameras müssen nicht neu bestätigt werden
STOP_PAYLOAD = bytes([0x01, 0x01, 0x1A, 0x00, 0x01, 0x02])
CONFIRM_PAYLOAD = bytes([0x01, 0x01, 0x1A, 0x00, 0x01, 0x01])

# Modell-ID (die ersten zwei Bytes nach der Hersteller-ID in der Werbung) -> (Name, Art)
MODELS = {
    0x0010: ("Osmo Action 2", "action23"),
    0x0012: ("Osmo Action 3", "action23"),
    0x0014: ("Osmo Action 4", "action4"),
    0x0015: ("Osmo Action 5 Pro", "action5"),
    0x0017: ("Osmo 360", "action5"),
    0x0018: ("Osmo Action 6", "action6"),
    0x0020: ("Osmo Pocket 3", "pocket3"),
    0x0021: ("Osmo Pocket 4", "pocket4"),
}
NEW_PROTOCOL = ("action5", "action6", "pocket4")  # brauchen nach dem Start die Bestätigungsnachricht
CONFIGURE_KINDS = {"action4": 0x08, "action6": 0x08, "action5": 0x1A}
ACTION2_NAME = "Osmo Action 2"                     # Fernsteuerung klappt laut Moblin nicht
# Wo steht "am Ladekabel" in der Statusnachricht (Typ 0x020D00, 34 Byte)? Beobachtet an einer Osmo Action 4 (4. Okt 2026, Kabel mehrfach
# an- und abgesteckt): Byte 20 = Akku in Prozent; Bytes 1 bis 2 = Akkuspannung in mV (Little Endian; 4400 am Kabel bei vollem Akku, etwa
# 4250 bis 4300 im Batteriebetrieb); Bytes 5 bis 8 = Strom aus dem Akku in mA (vorzeichenbehaftet, Little Endian): etwa -650 bis -1100,
# solange die streamende Kamera aus dem Akku läuft, 0 bis -5, sobald das Kabel steckt (positiv wäre Laden). Maßgeblich ist der Strom, nicht
# ein einzelnes Byte (Byte 2 allein wechselt nur, wenn die Spannung eine Stufengrenze überquert).
# Osmo Action 5 Pro und Osmo Action 6 (6. Okt 2026, Kabel je einmal abgezogen und angesteckt, Journal der Box): dieselbe Stelle (Bytes 5 bis 8,
# int32 Little Endian), derselbe Verlauf: aus dem Akku etwa -600 bis -770 mA, beim Anstecken kurz um 0 (-54), am Kabel positiv (+700 bis +4400 mA; die Kamera
# lädt dann). Dieselbe Schwelle (-100 mA) trennt beides. Je Modell nur ein Versuch; beim Abziehen wechselten außerdem Byte 28 (0x60 -> 0x20) und Byte 32
# (2 -> 0, beim Anstecken 1, dann 2), das wird hier nicht benutzt.
# Osmo Pocket 3 (10. Okt 2026, im Modus "Nur Akkustand", die Kamera sendet selbst per RTMP; Kabel einmal abgezogen und wieder angesteckt, Journal der Box):
# dieselbe Stelle (Bytes 5 bis 8, int32 Little Endian): am Kabel bei vollem Akku 0 bis +1 mA, abgezogen -433 bis -552 mA (Bytes 1 bis 2 = Spannung, etwa 4120 bis 4150 mV).
# Gemessen nur bei 100 % Akku; echtes Laden (positiver Strom) bei niedrigerem Stand wurde bei dieser Kamera nicht beobachtet.
# Für andere Modelle (auch die Osmo 360, die ebenfalls "action5" heißt) ist es nicht bekannt: dort bleibt "lädt" unbekannt (None), nie geraten.
POWER_FROM_CURRENT = {"action4": (5, -100), "action5": (5, -100), "action6": (5, -100), "pocket3": (5, -100)}      # (Byte der Stromangabe, Schwelle in mA): darüber hängt die Kamera am Strom (lädt oder wird versorgt)
POWER_NOT_KNOWN_MODELS = ("360",)                # Namensteile von Modellen, für die die Stelle nicht gemessen wurde (teilen sich die Art mit einem bekannten)


def power_spec(cfg):
    """(Byte, Schwelle) für die Ladeerkennung dieser Kamera oder None (Modell, bei dem sie nicht gemessen wurde)."""
    if any(m in str(cfg.get("model", "")) for m in POWER_NOT_KNOWN_MODELS):
        return None
    return POWER_FROM_CURRENT.get(cfg_kind(cfg))

RESOLUTIONS = {"480p": 0x47, "720p": 0x04, "1080p": 0x0A}
FPS = {25: 2, 30: 3}
STABILIZATION = {"off": 0, "rocksteady": 1, "horizonsteady": 2, "rocksteadyplus": 3, "horizonbalancing": 4}

DJI_COMPANY_IDS = (0x08AA, 0xF7AA)  # Bytes AA 08 / AA F7 als Little-Endian-Zahl


def model_from_manufacturer_data(manufacturer_data):
    """bleak liefert {Hersteller-ID: Daten}; gibt (Modell-ID, Name, Art) zurück oder None, wenn es keine DJI-Kamera ist."""
    for cid, payload in manufacturer_data.items():
        if cid in DJI_COMPANY_IDS:
            mid = int.from_bytes(payload[0:2], "little") if len(payload) >= 2 else -1
            name, kind = MODELS.get(mid, ("DJI-Gerät (unbekanntes Modell)", "unknown"))
            return mid, name, kind
    return None


def build_start_payload(kind, rtmp_url, resolution, bitrate_kbps, fps):
    res = RESOLUTIONS[resolution]
    fpsb = FPS.get(fps, 3)
    if kind in ("action6", "pocket4"):
        header, middle = ((b"\x01\x9c\x00", b"\xfe\x00") if kind == "action6"
                          else (b"\x01\xb5\x00", b"\x02\x01"))
        # Byte-genau wie Swifts JSONEncoder bei Moblin (Feldreihenfolge, "\/" für "/"): so läuft es bei Moblin an den Kameras
        js = ('{"codec":"AVC","EnhancedRTMP":false,"supportStopLive":false,"watermark":0,'
              '"rtmpAddress":%s,"orientation":"landscape"}' % json.dumps(rtmp_url).replace("/", "\\/")).encode()
        return (header + bytes([res]) + (bitrate_kbps & 0xFFFF).to_bytes(2, "little") + middle +
                bytes([fpsb]) + b"\x00\x00\x00" + len(js).to_bytes(2, "little") + js)
    oa5 = 0x2A if kind in NEW_PROTOCOL else 0x2E
    return (b"\x00" + bytes([oa5]) + b"\x00" + bytes([res]) +
            (bitrate_kbps & 0xFFFF).to_bytes(2, "little") + b"\x02\x00" + bytes([fpsb]) +
            b"\x00\x00\x00" + pack_url(rtmp_url))


def build_configure_payload(kind, stabilization):
    return b"\x01\x01" + bytes([CONFIGURE_KINDS[kind]]) + b"\x00\x01" + bytes([STABILIZATION.get(stabilization, 0)])


# ------------------------------------------------------------------ System ---

def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


SYSFS_NET = "/sys/class/net"
IFNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,15}$")


def iface_mac(name, root=None):
    """Hardware-Adresse (MAC) einer Netzwerkschnittstelle oder "". Anders als der Name (eth0, eth1, eth2 ...) bleibt sie bei jedem Start gleich:
    Die Namen von zwei Netzwerkkarten können nach einem Neustart vertauscht sein."""
    if not isinstance(name, str) or not IFNAME_RE.match(name):
        return ""
    try:
        with open(os.path.join(root or SYSFS_NET, name, "address")) as f:
            mac = f.read().strip().lower()
    except OSError:
        return ""
    return mac if re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", mac) and mac != "00:00:00:00:00:00" else ""


def iface_by_mac(mac, root=None):
    """Name der Schnittstelle, die gerade diese Hardware-Adresse hat, oder None."""
    if not mac:
        return None
    try:
        names = sorted(os.listdir(root or SYSFS_NET))
    except OSError:
        return None
    for n in names:
        if iface_mac(n, root) == mac:
            return n
    return None


def other_connections(skip):
    """Die übrigen aktiven Verbindungen der Box (Ethernet, Modem, USB-Router …), dieselben wie in der Verbindungsliste der
    Oberfläche. Die Kamera braucht trotzdem ein WLAN: Name und Passwort werden von Hand eingegeben, nur die IP der Box kommt
    aus der gewählten Verbindung."""
    found = []
    for line in run(["ip", "-4", "-o", "addr", "show"]).splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        ifname, ip = parts[1], parts[3].split("/")[0]
        if ifname in skip or ifname == "lo" or re.match(r"(tailscale|docker|veth|br-|p2p-|virbr)", ifname):
            continue
        if ip.startswith("169.254."):
            continue
        found.append({"ifname": ifname, "ssid": "", "password": "", "ip": ip, "type": "other"})
    return found


HOTSPOT_FILE = "/var/lib/pipbox/hotspot.json"       # Daemon.__init__ setzt den Pfad im Zustandsordner


def hotspot_password(ifname, ssid):
    """Passwort des Hotspots dieser Box (hotspot.json, vom WLAN-Helfer geschrieben). NetworkManager gibt das Passwort an diesen Dienst nicht
    heraus; der eigene Hotspot ist aber zum Weitergeben an Kameras gedacht. Leer, wenn nichts passt."""
    try:
        with open(HOTSPOT_FILE) as f:
            h = json.load(f).get(ifname)
    except (OSError, ValueError, AttributeError):
        return ""
    if isinstance(h, dict) and h.get("ssid") == ssid and isinstance(h.get("password"), str):
        return h["password"]
    return ""


def nm_wifi_options():
    """Verbindungen, über die eine Kamera bedient werden kann: WLAN-Hotspots und -Client-Netze (Name und Passwort aus
    NetworkManager) und jede andere aktive Verbindung (Name und Passwort von Hand). Kann der Dienst das Passwort eines
    WLANs nicht lesen, steht "secret_missing" im Eintrag: Dann wie "von Hand" behandeln."""
    options = []
    for line in run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"]).splitlines():
        parts = line.replace("\\:", "\x00").split(":")
        if len(parts) < 4 or parts[1] != "wifi" or parts[2] != "connected":
            continue
        ifname, conn = parts[0], parts[3].replace("\x00", ":")
        ssid = run(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", conn])
        mode = run(["nmcli", "-g", "802-11-wireless.mode", "connection", "show", conn])
        psk = run(["nmcli", "-s", "-g", "802-11-wireless-security.psk", "connection", "show", conn])
        secured = bool(run(["nmcli", "-g", "802-11-wireless-security.key-mgmt", "connection", "show", conn]))
        if mode == "ap" and secured and not psk and conn == "pipbox-hotspot-" + ifname:
            psk = hotspot_password(ifname, ssid)
        ip = run(["nmcli", "-g", "IP4.ADDRESS", "dev", "show", ifname]).split("/")[0].split("\n")[0]
        if not ssid or not ip:
            continue
        options.append({"ifname": ifname, "ssid": ssid, "password": psk, "ip": ip,
                        "type": "hotspot" if mode == "ap" else "client",
                        "secret_missing": bool(secured and not psk)})
    options += other_connections({o["ifname"] for o in options})
    return options


def public_option(o):
    """Eintrag der Verbindungsliste für den Browser: ohne Passwort."""
    return {k: o[k] for k in ("ifname", "ssid", "ip", "type") if k in o} | {"secret_missing": bool(o.get("secret_missing"))}


def rtmp_publishing(key, stat_url=None):
    """True, wenn gerade jemand zu rtmp://<Box>/publish/<key> sendet (nginx-rtmp-Statistik). None, wenn sie nicht lesbar ist."""
    try:
        with urllib.request.urlopen(stat_url or STAT_URL, timeout=2) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    m = re.search(r"<stream>\s*<name>%s</name>[\s\S]*?</stream>" % re.escape(key), body)
    return bool(m and "<publishing/>" in m.group(0))


def preferred_adapter():
    """hciN des Adapters für Suche und Verbindung: ein Stick zuerst, das eingebaute Modul zuletzt. None = BlueZ-Standard."""
    try:
        names = [n for n in os.listdir(dji.SYSFS_BT) if re.fullmatch(r"hci\d+", n)]
    except OSError:
        return None
    if not names:
        return None
    names.sort(key=lambda n: (dji.usb_id_for_hci(n) == ONBOARD_USB_ID, int(n[3:])))
    return names[0]


def adapter_address(hci):
    """Bluetooth-Adresse des Adapters hciN (für den RFCOMM-Anschluss, damit die Abfrage über denselben Adapter läuft), sonst None."""
    if not hci:
        return None
    try:
        with open(os.path.join(dji.SYSFS_BT, hci, "address")) as f:
            a = f.read().strip().upper()
        return a if MAC_RE.match(a) else None
    except OSError:
        return None


async def bluez_cleanup(addr, remove=True):
    """Einen hängenden BlueZ-Eintrag verwerfen. Eine Kamera, die ohne ordentliches Trennen verschwand, kann sonst in BlueZ
    "verbunden" bleiben und sich nie wieder melden. Mit remove=False wird die Verbindung nur getrennt (beim Beenden des Dienstes)."""
    for args in ((["disconnect", addr], ["remove", addr]) if remove else (["disconnect", addr],)):
        try:
            p = await asyncio.create_subprocess_exec("bluetoothctl", *args,
                                                     stdout=asyncio.subprocess.DEVNULL,
                                                     stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(p.wait(), 8)
        except Exception:
            pass


def make_ble_device(state, addr):
    """Ein bleak-Gerät für eine Kamera, die BlueZ schon verbunden hält (ohne Suche; die Kamera wirbt dann nicht mehr)."""
    from bleak.backends.device import BLEDevice
    details = {"path": state["path"], "props": {"Address": addr, "Name": state.get("name", "")}}
    try:
        return BLEDevice(addr, state.get("name") or None, details)
    except TypeError:                                           # ältere bleak-Versionen verlangen den Empfangspegel
        return BLEDevice(addr, state.get("name") or None, details, -60)


def cfg_kind(cfg):
    return cfg.get("kind", "unknown")


def friendly_error(e):
    """Verständlicher Text für die bekannten Fehler beim Verbinden (None: unbekannt, dann mit Ablaufverfolgung ins Journal)."""
    text = str(e).lower()
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)) and not text:
        return "Die Kamera hat auf den Bluetooth-Verbindungsaufbau nicht geantwortet (Zeitüberschreitung)"
    if "not found" in text and ("device" in text or "dev_" in text):
        return "Die Kamera ist für Bluetooth nicht mehr sichtbar (aus, im Ruhemodus oder mit dem Handy verbunden?)"
    if "failed to discover services" in text or "device disconnected" in text:
        return "Die Kamera hat die Bluetooth-Verbindung beim Einrichten beendet"
    if "le-connection-abort-by-local" in text:
        return "Der Bluetooth-Verbindungsaufbau wurde abgebrochen (Funk gestört?)"
    return None


STATUS_ONLY_KEY = "hdmi"     # Schlüssel des HDMI-Eingangs der Box: Der Akkustand erscheint bei der HDMI-Kamera (Status, Twitch-Warnung)


def camera_key(addr, taken=()):
    """RTMP-Schlüssel einer DJI-Kamera: dji- plus die letzten sechs Stellen der Adresse (eindeutig, ohne Kollision mit anderen
    Kameras der Box, deren Schlüssel nicht mit dji- beginnen)."""
    hexa = re.sub(r"[^0-9a-f]", "", addr.lower())
    for n in (6, 8, 10, 12):
        key = "dji-" + hexa[-n:]
        if key not in taken:
            return key
    return "dji-" + hexa


# ----------------------------------------------------------- Kamerasitzung ---

class CameraError(Exception):
    pass


class Camera:
    """Zustand und BLE-Sitzung einer eingerichteten Kamera."""

    AFTER_LINK_SECONDS = 90     # so lange nach dem Ende einer Verbindung gilt ein Fehlschlag beim Verbinden als "die Kamera erholt sich noch"
    RETRY_SCHEDULE = (8, 8, 15, 30)   # Sekunden bis zum nächsten Versuch nach dem 1., 2., 3. ... Fehlversuch (Suchen stört die stehenden Verbindungen)
    SEARCH_SECONDS = 15
    STREAM_CHECK_SECONDS = 5    # so oft wird geprüft, ob der Stream noch ankommt
    STREAM_LOST_SECONDS = 30    # so lange darf er fehlen, bevor die Sitzung neu beginnt
    CONNECT_SETTLE = 1.0     # so lange bleibt das Verbinden nach dem Aufbau noch gesperrt (dem Funkchip einen Moment geben)

    def __init__(self, daemon, addr, cfg):
        self.daemon = daemon
        self.addr = addr
        self.cfg = cfg
        self.state = "idle"
        self.detail = ""
        self.battery = None          # zuletzt gemeldeter Akkustand in Prozent; bleibt nach dem Verlust von Bluetooth stehen (mit Alter)
        self.battery_at = 0.0        # wann er gemeldet wurde
        self.charging = None         # am Ladekabel (lädt oder wird versorgt)? None = unbekannt (nur für Modelle in POWER_FROM_CURRENT bekannt)
        self._status_prev = None      # letzte Statusnachricht (zum Erkennen geänderter Bytes)
        self._status_flips = {}       # Byte -> wie oft es sich geändert hat
        self.task = None
        self.stop_requested = False
        self.manual_off = False      # "Trennen" wurde gedrückt: kein automatisches Wiederverbinden bis "Verbinden"
        self.last_seen = 0
        self.last_rx = 0
        self.fail_count = 0
        self.retry_at = 0
        self.wake = asyncio.Event()
        self.publishing = False      # die Kamera liefert ihren Stream (der Dienst prüft das), auch ohne Bluetooth
        self.force_cleanup = False   # "Verbinden" von Hand: erst einen hängenden BlueZ-Eintrag verwerfen
        self.typed_network = True
        self._scanner = None
        self._search_started = 0.0
        self._client = None           # die Bluetooth-Verbindung der laufenden Sitzung (zum ordentlichen Trennen beim Beenden des Dienstes)
        self.link_ended = 0.0         # wann zuletzt eine Bluetooth-Verbindung zu dieser Kamera endete (danach nimmt sie etwa eine Minute lang keine neue an)
        self._reusing = False         # diese Sitzung nutzt eine Verbindung weiter, die BlueZ schon hält
        self.reuse_failed = False     # das Weiterverwenden hat nicht geklappt: der nächste Versuch räumt auf und verbindet neu
        self.leaving = False          # der Dienst wird beendet: Sitzung ohne Stopp des Streams verlassen, Verbindung ordentlich trennen

    def locked(self):
        """Läuft eine Sitzung (Kopplung, Stream, Beenden)? Dann gelten Änderungen an Netz, Passwort und Adresse erst ab der nächsten Verbindung: Die Sitzung
        liest sie kurz vor der Übergabe an die Kamera (Schritt "preparing"). Sie lassen sich trotzdem jederzeit ändern (Kamera, die gerade streamt, bleibt
        verbunden). Die Oberfläche zeigt dann nur einen Hinweis; bei Suchen und Aufbau der Bluetooth-Verbindung gilt eine Änderung noch für diesen Versuch."""
        return self.state not in ("idle", "error", "searching", "connecting") or self.publishing

    def mode_locked(self):
        """Die Art (Stream oder nur Akku) lässt sich nur ändern, solange keine Sitzung läuft: die Sitzung hat sie beim Start gelesen."""
        return self.state not in ("idle", "error") or self.publishing

    def set_state(self, state, detail=""):
        if state != self.state or detail != self.detail:
            log.info("%s: %s %s", self.addr, state, detail)
            self.state, self.detail = state, detail

    def public(self):
        c = dict(self.cfg)
        c.pop("password", None)
        mac = c.pop("wifi_mac", "")
        if mac and iface_by_mac(mac):
            c["wifi_ifname"] = iface_by_mac(mac)           # die Anzeige folgt der gewählten Verbindung, auch wenn ihr Name wechselt
        c["saved"] = [n["ssid"] for n in self.cfg.get("saved", [])]   # nur die Namen, nie die Passwörter
        retry_in = max(0, int(self.retry_at - time.time())) if self.retry_at else 0
        c.update({"addr": self.addr, "state": self.state, "detail": self.detail,
                  "battery": self.battery, "charging": self.charging,
                  "battery_age": int(time.time() - self.battery_at) if self.battery_at else None,
                  "in_range": time.time() - self.last_seen < 30,
                  "retry_in": retry_in, "publishing": self.publishing, "locked": self.locked(), "mode_locked": self.mode_locked()})
        return c

    def resolve_target(self):
        """SSID, Passwort und RTMP-Adresse aus der gewählten Verbindung ermitteln."""
        cfg = self.cfg
        self.typed_network = True    # Name und Passwort von Hand eingegeben (lohnt zu merken), nicht aus NetworkManager
        ifname = cfg.get("wifi_ifname")
        mac = cfg.get("wifi_mac")
        if mac and ifname != "manual":
            # Die gewählte Verbindung ist über ihre Hardware-Adresse festgelegt (der Name kann nach einem Neustart ein anderer sein). Ist sie nicht da, wird
            # NICHT auf eine andere Verbindung ausgewichen (Heimnetz statt mobilem Router): lieber ein klarer Fehler.
            found = iface_by_mac(mac)
            if not found:
                raise CameraError("Die gewählte Verbindung (Router) ist nicht da. Router prüfen: eingeschaltet, per Kabel oder USB verbunden, "
                                  "im USB-Modus? Die Kamera bleibt bei dieser Verbindung und wechselt nicht ins Heimnetz.")
            if found != ifname:
                log.info("%s: die gewählte Verbindung heißt jetzt %s (vorher %s)", self.addr, found, ifname)
                cfg["wifi_ifname"] = ifname = found
        if ifname and ifname != "manual":
            for o in self.daemon.wifi_options():
                if o["ifname"] == ifname:
                    if o["type"] == "other" or o.get("secret_missing"):
                        if o["type"] != "other" and not cfg.get("password"):
                            raise CameraError("Das Passwort des WLANs %s lässt sich nicht aus NetworkManager lesen. "
                                              "Bitte WLAN-Name und Passwort der Kamera von Hand eintragen." % o["ssid"])
                        ssid, pw, ip = cfg.get("ssid") or o["ssid"], cfg.get("password", ""), o["ip"]
                    else:
                        self.typed_network = False
                        ssid, pw, ip = o["ssid"], o["password"], o["ip"]
                    break
            else:
                raise CameraError("Die Verbindung %s ist nicht aktiv. Router prüfen: eingeschaltet, per USB verbunden, "
                                  "im USB-Modus?" % ifname)
        else:
            ssid, pw, ip = cfg.get("ssid", ""), cfg.get("password", ""), cfg.get("ip", "")
        if not ssid or not ip:
            raise CameraError("Für die Kamera ist keine Verbindung gewählt (WLAN-Name oder Adresse der Box fehlt)")
        key = cfg.get("rtmp_key") or "cam1"
        return ssid, pw, "rtmp://%s:%d/%s/%s" % (ip, self.daemon.rtmp_port, self.daemon.rtmp_app, key)

    async def find_device(self):
        """Die Werbung der Kamera suchen und die Suche LAUFEN LASSEN, bis die Verbindung steht (stop_scan). BlueZ vergisst eine
        Kamera, sobald die Suche endet ("device not found"); gemessen auf der Box, so lief schon die frühere Version. Der Aufrufer
        hält das gemeinsame Schloss (nur eine Suche oder ein Verbindungsaufbau zur Zeit)."""
        # Hält BlueZ die Kamera noch verbunden (der Dienst wurde beendet oder ist abgestürzt, die Verbindung blieb), wird sie weiterverwendet: Ein Trennen
        # lässt die Kamera etwa eine Minute lang keine neue Verbindung annehmen (gemessen: Verbindungsaufbau gelingt auf Funkebene, die Kamera bricht ihn
        # nach 250 ms ab, "Connection Failed to be Established"), ein Weiterverwenden dauert Sekunden.
        self._reusing = False
        if not self.reuse_failed and not self.stop_requested:
            try:
                st = await asyncio.get_event_loop().run_in_executor(None, dji.bluez_device_state, self.addr)
            except Exception:
                st = None
            if st and st.get("connected"):
                log.info("%s: BlueZ hält die Kamera noch verbunden, die Verbindung wird weiterverwendet", self.addr)
                self._reusing = True
                self.force_cleanup = False
                self._search_started = time.time()
                return make_ble_device(st, self.addr)
        if self.fail_count >= 2 or self.force_cleanup:
            self.force_cleanup = False
            await bluez_cleanup(self.addr)
        self._search_started = time.time()
        scanner = BleakScanner(**self.daemon.scan_kwargs())
        try:
            await scanner.start()
        except Exception as e:
            raise CameraError("Die Bluetooth-Suche konnte nicht gestartet werden: %s" % e)
        self._scanner = scanner
        end = time.time() + self.SEARCH_SECONDS
        while time.time() < end and not self.stop_requested:
            try:
                for addr, (dev, _adv) in scanner.discovered_devices_and_advertisement_data.items():
                    if str(addr).upper() == self.addr.upper():
                        self.last_seen = time.time()
                        return dev
            except Exception as e:
                log.info("%s: Suchergebnis nicht lesbar: %s", self.addr, e)
            await asyncio.sleep(0.2)
        await self.stop_scan()
        return None

    async def stop_scan(self):
        s, self._scanner = self._scanner, None
        if s is not None:
            try:
                await s.stop()
            except Exception as e:
                log.info("%s: Suche beenden fehlgeschlagen: %s", self.addr, e)

    async def session(self):
        cfg = self.cfg
        model_kind = cfg.get("kind", "unknown")
        if BleakClient is None:
            raise CameraError("Die Bluetooth-Bibliothek (bleak) ist nicht installiert. install.sh erneut ausführen.")
        if cfg.get("model") == ACTION2_NAME:
            raise CameraError("Osmo Action 2: Die Fernsteuerung funktioniert laut Moblin nicht.")
        loop = asyncio.get_event_loop()
        if cfg.get("status_only"):
            ssid = password = rtmp_url = ""          # nur Akkustand lesen: kein WLAN, kein Stream
        else:
            ssid, password, rtmp_url = await loop.run_in_executor(None, self.resolve_target)
        key = cfg.get("rtmp_key") or "cam1"
        if self.daemon.adapter_missing():
            prob = dji.adapter_problems([], dji.usb_bluetooth_devices())
            raise CameraError(prob[0]["hint"] if prob else "Kein Bluetooth-Adapter gefunden")

        # Das Verbinden geschieht nacheinander: Ein Funkchip bricht gleichzeitige Verbindungsversuche gegenseitig ab
        # (le-connection-abort-by-local, gemessen mit mehreren Kameras an einem Stick). Nach dem Verbinden laufen alle parallel.
        if self.daemon.conn_lock.locked():
            self.set_state("connecting", "Wartet, bis eine andere Kamera fertig verbunden ist")
        await self.daemon.conn_lock.acquire()
        held = [True]
        loop2 = asyncio.get_event_loop()

        async def release():
            await self.stop_scan()                       # erst jetzt darf die Suche enden
            if held[0]:
                held[0] = False
                loop2.call_later(self.CONNECT_SETTLE, self.daemon.conn_lock.release)    # dem Funkchip einen Moment geben, bevor die nächste dran ist

        try:
            await self._connect_and_stream(loop, ssid, password, rtmp_url, key, release)
        finally:
            if self._client is not None:
                self.link_ended = time.time()
            self._client = None
            await release()

    async def _connect_and_stream(self, loop, ssid, password, rtmp_url, key, release):
        cfg = self.cfg
        model_kind = cfg.get("kind", "unknown")
        self.set_state("searching", "Kamera wird gesucht")
        device = await self.find_device()
        if device is None:
            raise CameraError("Kamera nicht gefunden. Ist sie an, Bluetooth aktiv und nicht mit dem Handy verbunden?")

        queue = asyncio.Queue()
        disconnected = asyncio.Event()

        def on_notify(_char, data):
            try:
                msg = Message.decode(data)
            except ValueError as e:
                log.debug("Nachricht verworfen %s: %s", bytes(data).hex(), e)
                return
            self.last_rx = time.time()
            if msg.type == TY_STATUS and len(msg.payload) >= 21:
                self.battery = msg.payload[20]
                self.battery_at = time.time()
                spec = power_spec(self.cfg)
                if spec and len(msg.payload) >= spec[0] + 4:
                    self.charging = struct.unpack_from("<i", bytes(msg.payload), spec[0])[0] > spec[1]
                # Zur Klärung bei anderen Modellen: Ändert sich ein Byte der Statusnachricht (außer dem Akkustand), das nicht ständig schwankt,
                # steht die Nachricht im Journal. Bytes, die schon oft gewechselt haben, gelten als schwankend (Temperatur o. ä.) und zählen nicht.
                cur = bytes(msg.payload)
                if self._status_prev is None:
                    log.info("%s: Statusnachricht (%d Byte, Akku %d %%): %s", self.addr, len(cur), self.battery, cur.hex())
                else:
                    changed = [i for i in range(min(len(cur), len(self._status_prev))) if i != 20 and cur[i] != self._status_prev[i]]
                    for i in changed:
                        self._status_flips[i] = self._status_flips.get(i, 0) + 1
                    if any(self._status_flips[i] <= 3 for i in changed):
                        log.info("%s: Statusnachricht (%d Byte, Akku %d %%): %s", self.addr, len(cur), self.battery, cur.hex())
                self._status_prev = cur
                return
            queue.put_nowait(msg)

        async def wait_for(mid, timeout=15):
            end = time.time() + timeout
            while True:
                left = end - time.time()
                if left <= 0:
                    raise CameraError("Keine Antwort der Kamera (0x%04x)" % mid)
                try:
                    msg = await asyncio.wait_for(queue.get(), left)
                except asyncio.TimeoutError:
                    raise CameraError("Keine Antwort der Kamera (0x%04x)" % mid)
                if msg.id == mid:
                    return msg

        self.set_state("connecting", "Verbinde per Bluetooth")
        self.last_rx = time.time()
        t_connect = time.time()
        async with BleakClient(device, timeout=20, disconnected_callback=lambda c: disconnected.set()) as client:
            self._client = client
            t_connected = time.time()
            write_char = None
            for service in client.services:
                for ch in service.characteristics:
                    if ch.uuid.startswith("0000fff5"):
                        write_char = ch
                    if "notify" in ch.properties or "indicate" in ch.properties:
                        try:
                            await client.start_notify(ch, on_notify)
                        except Exception as e:  # manche Kennungen lehnen ab, das ist in Ordnung
                            log.debug("Benachrichtigung auf %s fehlgeschlagen: %s", ch.uuid, e)
            if write_char is None:
                raise CameraError("Kein DJI-Gerät (Schreib-Kanal FFF5 fehlt)")
            log.info("%s: Zeiten: Verbinden und Dienste %.1f s, Benachrichtigungen %.1f s (Suche vorher %.1f s)", self.addr,
                     t_connected - t_connect, time.time() - t_connected, t_connect - self._search_started)
            await release()    # die Verbindung steht: die Suche endet, die nächste Kamera darf jetzt verbinden

            async def send(msg):
                await client.write_gatt_char(write_char, msg.encode(), response=False)

            # 1. Koppeln / Kopplung prüfen
            self.set_state("pairing", "Falls die Kamera fragt, bitte dort bestätigen")
            await send(Message(T_PAIR, ID_PAIR, TY_PAIR, PAIR_PAYLOAD + pack_string(PAIR_PIN)))
            resp = await wait_for(ID_PAIR)
            if resp.payload != bytes([0, 1]):
                # noch nicht gekoppelt: die Kamera fragt den Nutzer und antwortet nach der Bestätigung mit irgendeiner Nachricht
                try:
                    await asyncio.wait_for(queue.get(), 60)
                except asyncio.TimeoutError:
                    raise CameraError("Die Kopplung wurde an der Kamera nicht bestätigt")

            # "Trennen", "Neu verbinden" oder das Beenden des Dienstes während des Einrichtens: zwischen den Schritten ordentlich aufhören (die
            # Verbindung wird beim Verlassen getrennt). Ein Abbruch mitten in einem Schritt ließ die Kamera minutenlang nicht mehr antworten.
            if self.stop_requested:
                return

            # Nur Akkustand: Die Kamera sendet ihre Status (Akku) schon nach dem Koppeln. Weder WLAN noch Stream anfassen (die Kamera kann
            # zugleich per HDMI senden). Bleibt verbunden, bis gestoppt wird; bei Verbindungsverlust beginnt die Sitzung von vorn.
            if cfg.get("status_only"):
                await self._status_only_loop(client, disconnected)
                return

            # 2. alten Stream aufräumen, vorbereiten, WLAN einrichten
            self.set_state("preparing", "Stream wird vorbereitet")
            await send(Message(T_STOP, ID_STOP, TY_STOP, STOP_PAYLOAD))
            await wait_for(ID_STOP)
            await send(Message(T_PREPARE, ID_PREPARE, TY_PREPARE, b"\x1a"))
            await wait_for(ID_PREPARE)
            if self.stop_requested:
                return

            if not cfg.get("status_only"):
                # Netz, Passwort und Adresse jetzt noch einmal lesen: während des Suchens und Verbindens darf man sie ändern (siehe locked)
                ssid, password, rtmp_url = await loop.run_in_executor(None, self.resolve_target)
            self.set_state("wifi", ssid)
            await send(Message(T_WIFI, ID_WIFI, TY_WIFI, pack_string(ssid) + pack_string(password)))
            resp = await wait_for(ID_WIFI, 30)
            if resp.payload != bytes([0, 0]):
                raise CameraError('Die Kamera konnte dem WLAN "%s" nicht beitreten (Name oder Passwort?)' % ssid)
            if self.typed_network and ssid:
                self.daemon.remember_network(self, ssid, password)         # die Kamera hat das WLAN angenommen: merken
            if self.stop_requested:
                return

            # 3. Bildstabilisierung bei den Modellen, die sie brauchen
            if model_kind in CONFIGURE_KINDS:
                self.set_state("configuring", "Bildstabilisierung wird eingestellt")
                await send(Message(T_CONFIGURE, ID_CONFIGURE, TY_CONFIGURE,
                                   build_configure_payload(model_kind, cfg.get("stabilization", "off"))))
                await wait_for(ID_CONFIGURE)
            if self.stop_requested:
                return

            # 4. den RTMP-Stream starten
            self.set_state("starting", "Stream wird gestartet")
            payload = build_start_payload(model_kind, rtmp_url, cfg.get("resolution", "1080p"),
                                          int(cfg.get("bitrate", 6000)), int(cfg.get("fps", 30)))
            await send(Message(T_START, ID_START, TY_START, payload))
            if model_kind in NEW_PROTOCOL:
                await send(Message(T_STOP, ID_STOP, TY_STOP, CONFIRM_PAYLOAD))
            await wait_for(ID_START, 30)
            self.set_state("streaming", rtmp_url)
            self.fail_count = 0
            self.reuse_failed = False

            # 5. verbunden bleiben, bis gestoppt wird. Wächter: Der Stream muss weiter beim RTMP-Server ankommen. Geht nur die
            #    Bluetooth-Verbindung verloren, während der Stream noch ankommt, läuft er unverändert weiter (die frühere Version
            #    hat ebenfalls nur den Stream geprüft): Ein Neuaufbau würde ihn mit "Stopp, Vorbereiten, Start" unterbrechen.
            #    Endet der Stream, endet die Sitzung mit einem Fehler und beginnt von vorn (automatisch verbinden): Die
            #    Kamera kann das WLAN verlassen haben oder ausgeschaltet worden sein.
            last_ok = time.time()
            last_check = 0
            bt_lost = False
            while not self.stop_requested:
                if bt_lost:
                    await asyncio.sleep(1)          # das Signal "getrennt" ist schon gesetzt: darauf zu warten würde sofort zurückkehren (Dauerschleife)
                else:
                    try:
                        await asyncio.wait_for(disconnected.wait(), 1)
                    except asyncio.TimeoutError:
                        pass
                while not queue.empty():
                    queue.get_nowait()

                now = time.time()
                if disconnected.is_set() and not bt_lost:
                    bt_lost = True
                    log.info("%s: Bluetooth-Verbindung getrennt, prüfe den Stream", self.addr)
                if now - last_check >= self.STREAM_CHECK_SECONDS or (bt_lost and last_check == 0):
                    last_check = now
                    publishing = await loop.run_in_executor(None, rtmp_publishing, key, self.daemon.stat_url)
                    if publishing is None and bt_lost:
                        raise CameraError("Die Bluetooth-Verbindung ging verloren")     # ohne Statistik nicht zu beurteilen
                    if publishing is None or publishing:
                        last_ok = now
                        if bt_lost:
                            if time.time() - self.last_rx < 20:
                                self.set_state("streaming", "Der Stream läuft; die Steuerung per Bluetooth ist beendet, Statusmeldungen (Akku) kommen weiter")
                            else:
                                self.set_state("streaming", "Der Stream läuft, die Bluetooth-Verbindung ist getrennt")
                    elif now - last_ok > self.STREAM_LOST_SECONDS:
                        raise CameraError("Der Stream ist ausgefallen (Kamera außer Reichweite des WLANs?)")
                if not bt_lost and now - self.last_rx > 180 and not (await loop.run_in_executor(None, rtmp_publishing, key, self.daemon.stat_url)):
                    raise CameraError("Die Bluetooth-Verbindung ging verloren")

            if self.stop_requested and client.is_connected and not bt_lost and not self.leaving:
                self.set_state("stopping", "Stream wird beendet")
                try:
                    await send(Message(T_STOP, ID_STOP, TY_STOP, STOP_PAYLOAD))
                    await wait_for(ID_STOP, 10)
                except Exception:
                    pass

    STATUS_SILENCE_SECONDS = 180

    async def _status_only_loop(self, client, disconnected):
        """Verbunden bleiben und nur Statusmeldungen (Akku) mitlesen."""
        self.set_state("status", "Nur Akkustand per Bluetooth")
        self.fail_count = 0
        self.reuse_failed = False
        self.last_rx = time.time()
        while not self.stop_requested:
            try:
                await asyncio.wait_for(disconnected.wait(), 1)
            except asyncio.TimeoutError:
                pass
            if disconnected.is_set() or not client.is_connected:
                raise CameraError("Die Bluetooth-Verbindung ging verloren")
            if time.time() - self.last_rx > self.STATUS_SILENCE_SECONDS:
                raise CameraError("Die Kamera sendet keine Statusmeldungen mehr")

    async def run_loop(self):
        """Verbinden, streamen und von vorn beginnen, solange "automatisch verbinden" an ist."""
        while True:
            self.wake.clear()
            try:
                await self.session()
                self.fail_count = 0
                self.set_state("idle", "Getrennt")
            except asyncio.CancelledError:
                self.set_state("idle")
                raise
            except CameraError as e:
                self.fail_count += 1
                self._reuse_failed_now()
                self.set_state("error", str(e))
            except Exception as e:
                self.fail_count += 1
                self._reuse_failed_now()
                msg = friendly_error(e)
                if msg is None:
                    log.exception("Sitzung fehlgeschlagen")
                    msg = "%s: %s" % (type(e).__name__, e)
                else:
                    log.info("%s: %s (%s)", self.addr, msg, type(e).__name__)
                if self.recovering(e):
                    # Kurz nach dem Ende einer Verbindung meldet sich die Kamera erst nach etwa einer Minute wieder (gemessen): kein roter Fehler, der
                    # zu Tastendrücken verleitet (jedes Trennen beginnt die Wartezeit von vorn), sondern ein Hinweis; der Dienst versucht es weiter.
                    self.set_state("connecting", "Die Kamera meldet sich nach dem Trennen oft erst nach etwa einer Minute wieder. Der Dienst versucht es weiter.")
                else:
                    self.set_state("error", msg)
            if self.stop_requested or not self.cfg.get("autoconnect"):
                self.retry_at = 0
                return
            # vor dem nächsten Versuch warten (gestaffelt); "Verbinden" / "Neu verbinden" wecken sofort auf
            delay = self.retry_delay()
            self.retry_at = time.time() + delay
            try:
                await asyncio.wait_for(self.wake.wait(), delay)
            except asyncio.TimeoutError:
                pass
            self.retry_at = 0
            if self.stop_requested:
                return

    def recovering(self, exc):
        """Ist ein Fehlschlag beim Verbinden nur die Wartezeit der Kamera nach dem Ende einer Verbindung?"""
        connect_failure = isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "disconnected" in str(exc).lower() or "failed to discover" in str(exc).lower()
        return connect_failure and 0 < time.time() - self.link_ended < self.AFTER_LINK_SECONDS

    def _reuse_failed_now(self):
        """Ein Versuch mit der weiterverwendeten Verbindung ist gescheitert: Der nächste Versuch trennt, räumt BlueZ auf und verbindet neu."""
        if self._reusing:
            log.info("%s: Die weiterverwendete Verbindung hat nicht funktioniert, der nächste Versuch verbindet neu", self.addr)
            self._reusing = False
            self.reuse_failed = True
            self.force_cleanup = True

    def retry_delay(self):
        """Wartezeit bis zum nächsten Versuch: wächst mit den Fehlversuchen in Folge."""
        return self.RETRY_SCHEDULE[min(max(self.fail_count, 1) - 1, len(self.RETRY_SCHEDULE) - 1)]

    def running(self):
        return self.task is not None and not self.task.done()

    def start(self):
        """Die Verbindungsschleife starten oder eine wartende sofort noch einmal versuchen lassen."""
        if self.running():
            self.wake.set()
            return
        self.fail_count = 0
        self.stop_requested = False
        self.manual_off = False
        self.force_cleanup = True
        self.task = asyncio.ensure_future(self.run_loop())

    async def release_link(self):
        """Die Bluetooth-Verbindung zur Kamera jetzt ordentlich trennen (Beenden des Dienstes). Ohne ordentliches Trennen hält die Kamera die alte
        Verbindung noch eine ganze Weile für belegt und antwortet dem neu gestarteten Dienst erst nach Minuten."""
        client = self._client
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(), 5)
            except Exception as e:
                log.info("%s: Trennen beim Beenden fehlgeschlagen: %s", self.addr, e)

    async def stop_session(self):
        self.stop_requested = True
        self.wake.set()
        if self.running():
            try:
                await asyncio.wait_for(asyncio.shield(self.task), 15)
            except Exception:
                self.task.cancel()
        self.retry_at = 0

    async def stop(self):
        # "Trennen" beendet die Verbindung sofort. "Automatisch verbinden" ist ein eigener Schalter und bleibt, wie er ist;
        # die Kamera wird aber nicht von allein neu verbunden, bis "Verbinden" gedrückt wird (oder der Dienst neu startet).
        self.manual_off = True
        await self.stop_session()
        self.set_state("idle")

    async def restart(self):
        """Die laufende Sitzung (falls es eine gibt) verwerfen und sofort neu verbinden."""
        await self.stop_session()
        self.set_state("idle")
        self.start()


# ------------------------------------------------------------------ Dienst ---

SETTINGS_ALLOWED = {"name": str, "wifi_ifname": str, "ssid": str, "password": str, "ip": str,
                    "resolution": str, "fps": int, "bitrate": int, "stabilization": str, "autoconnect": bool, "status_only": bool}
NETWORK_FIELDS = ("wifi_ifname", "ssid", "password", "ip")
LEGACY_MODEL_IDS = {"osmoAction2": 0x0010, "osmoAction3": 0x0012, "osmoAction4": 0x0014, "osmoAction5Pro": 0x0015,
                    "osmo360": 0x0017, "osmoAction6": 0x0018, "osmoPocket3": 0x0020, "osmoPocket4": 0x0021}
LEGACY_STAB = {"rockSteady": "rocksteady", "rockSteadyPlus": "rocksteadyplus",
               "horizonBalancing": "horizonbalancing", "horizonSteady": "horizonsteady"}
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
DEFAULT_SETTINGS = {"resolution": "1080p", "fps": 30, "bitrate": 6000, "stabilization": "off"}


def _read_json(path):
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def iface_ip(name):
    """Aktuelle IPv4-Adresse einer Schnittstelle (nur Linux) oder None."""
    import socket
    import struct
    try:
        import fcntl
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            return socket.inet_ntoa(fcntl.ioctl(sk.fileno(), 0x8915,
                                                struct.pack("256s", name[:15].encode()))[20:24])
        finally:
            sk.close()
    except (ImportError, OSError):
        return None


def migrate_legacy(state_dir, wifi_options=None):
    """Einmalige Übernahme der Kameras der früheren Version (dji-known/-settings/-wifi/-active.json und camera-net.json) in
    dji-cameras.json: Einstellungen, Name, das gespeicherte WLAN und was laufen sollte (automatisch verbinden) bleiben erhalten.
    Die alten Dateien bleiben unangetastet liegen. Gibt das Wörterbuch {Adresse: Kamera} zurück (leer, wenn nichts da ist)."""
    known = _read_json(os.path.join(state_dir, "dji-known.json"))
    settings = _read_json(os.path.join(state_dir, "dji-settings.json"))
    wifi = _read_json(os.path.join(state_dir, "dji-wifi.json"))
    active = _read_json(os.path.join(state_dir, "dji-active.json"))
    net = _read_json(os.path.join(state_dir, "camera-net.json")).get("iface")
    names = {}                                  # selbst vergebene Namen aus der Kameraliste (cameras.json ist eine Liste)
    try:
        with open(os.path.join(state_dir, "cameras.json")) as f:
            cams = json.load(f)
    except (OSError, ValueError):
        cams = []
    for c in (cams if isinstance(cams, list) else []):
        if isinstance(c, dict) and c.get("key") and c.get("name"):
            names[c["key"]] = str(c["name"])
    out = {}
    for addr in sorted(set(k.upper() for k in known) | set(k.upper() for k in active)):
        if not MAC_RE.match(addr):
            continue
        info = known.get(addr) or next((v for k, v in known.items() if k.upper() == addr), {}) or {}
        act = active.get(addr) or next((v for k, v in active.items() if k.upper() == addr), {}) or {}
        legacy = info.get("model") or act.get("model") or "unknown"
        mid = LEGACY_MODEL_IDS.get(legacy)
        mname, kind = MODELS.get(mid, ("DJI-Gerät (unbekanntes Modell)", "unknown"))
        st = next((v for k, v in settings.items() if k.upper() == addr), {}) or {}
        key = camera_key(addr, {c["rtmp_key"] for c in out.values()})
        cfg = {"name": (names.get(camera_key(addr)) or info.get("model_name") or mname)[:40], "model": mname, "kind": kind,
               "wifi_ifname": "manual", "ssid": "", "password": "", "ip": "",
               "resolution": st.get("resolution", "1080p") if st.get("resolution") in RESOLUTIONS else "1080p",
               "fps": st.get("fps", 30) if st.get("fps") in FPS else 30,
               "bitrate": min(16000, max(500, int(st.get("bitrate_kbps", 6000) or 6000))),
               "stabilization": LEGACY_STAB.get(st.get("stabilization"), st.get("stabilization", "off")),
               "rtmp_key": key, "autoconnect": addr in {a.upper() for a in active}}
        if cfg["stabilization"] not in STABILIZATION:
            cfg["stabilization"] = "off"
        if wifi.get("ssid"):
            cfg["ssid"], cfg["password"] = str(wifi["ssid"]), str(wifi.get("password", ""))
            cfg["saved"] = [{"ssid": cfg["ssid"], "password": cfg["password"]}]
            if isinstance(net, str) and net:
                # War ein Kameranetz gewählt, wird dessen Verbindung weiter benutzt (WLAN-Name und Passwort bleiben von Hand);
                # ein WLAN, das NetworkManager führt, behält die feste Adresse, damit der gespeicherte Name gilt.
                opt = next((o for o in (wifi_options if wifi_options is not None else nm_wifi_options()) if o["ifname"] == net), None)
                if opt is None or opt["type"] == "other":
                    cfg["wifi_ifname"] = net
                else:
                    cfg["ip"] = opt["ip"]
        out[addr] = cfg
    return out


class Daemon:
    def __init__(self, state_dir, rtmp_port=RTMP_PORT, rtmp_app=RTMP_APP, stat_url=STAT_URL):
        global HOTSPOT_FILE
        self.state_dir = state_dir
        HOTSPOT_FILE = os.path.join(state_dir, "hotspot.json")
        self.config_file = os.path.join(state_dir, "dji-cameras.json")
        self.token_path = os.path.join(state_dir, "dji-token")
        self.rtmp_port, self.rtmp_app, self.stat_url = rtmp_port, rtmp_app, stat_url
        self.cameras = {}
        self.by_conn = {}                    # Verbindung -> zuletzt dort eingegebenes WLAN der Kamera {"ssid","password"} (nur zum Vorschlagen)
        self.scan_results = []
        self.scanning = False
        self.scan_error = ""
        self.ble_lock = self.conn_lock = asyncio.Lock()   # eine Suche oder ein Verbindungsaufbau zur Zeit (Suche und Verbinden stören sich)
        self.closing = False                              # der Dienst wird beendet: nichts mehr starten
        self.token = self._load_token()
        self._opts = (0.0, [])
        self._adapt = None
        self.phones = phone_battery.Phones(state_dir, hci=preferred_adapter, adapter_mac=adapter_address,
                                           busy=lambda: self.scanning, taken=lambda: list(self.cameras))
        self.ctrl = controllers.Controllers(state_dir, hci=preferred_adapter,
                                            taken=lambda: list(self.cameras) + list(self.phones.phones))
        self.load()

    # -- Dateien
    def _load_token(self):
        try:
            with open(self.token_path) as f:
                t = f.read().strip()
            if len(t) >= 32:
                return t
        except OSError:
            pass
        t = secrets.token_urlsafe(32)
        fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(t + "\n")
        return t

    def load(self):
        if os.path.exists(self.config_file):
            saved = _read_json(self.config_file)
        else:
            saved = {"cameras": migrate_legacy(self.state_dir)}
            if saved["cameras"]:
                log.info("%d Kamera(s) aus der früheren Version übernommen", len(saved["cameras"]))
        for ifname, n in (saved.get("by_connection") or {}).items():
            if isinstance(n, dict) and n.get("ssid"):
                self.by_conn[str(ifname)] = {"ssid": str(n["ssid"]), "password": str(n.get("password", ""))}
        migrated = not os.path.exists(self.config_file) and bool(saved.get("cameras"))
        for addr, cfg in (saved.get("cameras") or {}).items():
            if isinstance(cfg, dict):
                if cfg.get("status_only"):
                    cfg["autoconnect"] = True         # Nur-Akku-Kameras lesen immer mit, auch nach einem Neustart des Dienstes
                self.cameras[addr] = Camera(self, addr, cfg)
                if migrated:
                    self._note_connection(cfg)
        if migrated:
            self.save()

    def pin_connection(self, cam):
        """Die gewählte Verbindung über ihre Hardware-Adresse festhalten (ein Name wie eth0 kann nach einem Neustart eine andere Karte meinen)."""
        name = cam.cfg.get("wifi_ifname")
        cam.cfg["wifi_mac"] = iface_mac(name) if name and name != "manual" else ""

    def _note_connection(self, cfg):
        """Merkt das WLAN dieser Kamera für ihre Verbindung (nur Vorschlag für weitere Kameras an derselben Verbindung)."""
        ifname = cfg.get("wifi_ifname")
        if ifname and ifname != "manual" and cfg.get("ssid") and cfg.get("password"):
            self.by_conn[ifname] = {"ssid": cfg["ssid"], "password": cfg["password"]}

    def remember_network(self, cam, ssid, password):
        """Ein WLAN, das die Kamera angenommen hat: in ihrer eigenen Liste gespeichert und für ihre Verbindung gemerkt."""
        saved = [n for n in cam.cfg.get("saved", []) if n["ssid"] != ssid]
        saved.append({"ssid": ssid, "password": password})
        cam.cfg["saved"] = saved[-20:]
        self._note_connection(dict(cam.cfg, ssid=ssid, password=password))
        self.save()

    def offer_connection_network(self, cam):
        """Wurde für diese Kamera eine Verbindung gewählt, an der schon eine andere Kamera ihr WLAN eingegeben hat, wird dieses
        WLAN vorgeschlagen (Name und Passwort eingetragen und in ihrer Liste). Eine Kamera mit eigenem WLAN behält es; die anderen
        Kameras bleiben unberührt."""
        cfg = cam.cfg
        m = self.by_conn.get(cfg.get("wifi_ifname") or "")
        if not m or cfg.get("ssid"):
            return False
        cfg["ssid"], cfg["password"] = m["ssid"], m["password"]
        saved = [n for n in cfg.get("saved", []) if n["ssid"] != m["ssid"]]
        saved.append(dict(m))
        cfg["saved"] = saved[-20:]
        return True

    def save(self):
        data = {"cameras": {a: c.cfg for a, c in self.cameras.items()}, "by_connection": self.by_conn}
        tmp = self.config_file + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # enthält WLAN-Passwörter von Hand eingegebener Netze
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.config_file)

    # -- Hilfen
    def wifi_options(self, max_age=3.0):
        """Verbindungsliste (mit Passwörtern, nur für den Dienst selbst). Kurz zwischengespeichert."""
        now = time.monotonic()
        if now - self._opts[0] > max_age:
            self._opts = (now, nm_wifi_options())
        return self._opts[1]

    def adapter_missing(self):
        try:
            return not [n for n in os.listdir(dji.SYSFS_BT) if re.fullmatch(r"hci\d+", n)]
        except OSError:
            return True

    def scan_kwargs(self):
        """Adapter für Suche und Verbindung (die Angabe hängt von der bleak-Version ab)."""
        a = preferred_adapter()
        if not a:
            return {}
        return {"bluez": {"adapter": a}} if BLEAK_MAJOR >= 2 else {"adapter": a}

    def adapter_info(self):
        """Welche Bluetooth-Adapter laufen und welche Sticks stecken, ohne einen Adapter zu ergeben. Höchstens alle 10 s neu."""
        now = time.monotonic()
        if self._adapt and now - self._adapt[0] < 10:
            return self._adapt[1]
        info = dji.adapter_info()
        self._adapt = (now, info)
        return info

    def snapshot(self):
        return {"cameras": [c.public() for c in self.cameras.values()], "scan": self.scan_results,
                "scanning": self.scanning, "scan_error": self.scan_error, "bleak": BleakScanner is not None, **self.phones.snapshot(), **self.ctrl.snapshot()}

    async def scan(self, seconds=8):
        self.scan_error = ""
        if BleakScanner is None:
            self.scan_error = "Die Bluetooth-Bibliothek (bleak) ist nicht installiert. install.sh erneut ausführen."
            return
        if self.adapter_missing():
            prob = dji.adapter_problems([], dji.usb_bluetooth_devices())
            self.scan_error = prob[0]["hint"] if prob else "Kein Bluetooth-Adapter gefunden"
            return
        self.scanning = True
        try:
            async with self.ble_lock:
                found = await BleakScanner.discover(timeout=seconds, return_adv=True, **self.scan_kwargs())
        except Exception as e:
            self.scanning = False
            self.scan_error = "Suche fehlgeschlagen: %s" % e
            return
        self.scanning = False
        results = []
        for addr, (dev, adv) in found.items():
            m = model_from_manufacturer_data(adv.manufacturer_data)
            if m:
                results.append({"addr": addr, "name": dev.name or m[1], "model": m[1], "kind": m[2],
                                "rssi": adv.rssi, "paired": addr in self.cameras})
                if addr in self.cameras:
                    self.cameras[addr].last_seen = time.time()
        results.sort(key=lambda r: -(r["rssi"] or -999))
        self.scan_results = results
        if not found:
            self.scan_error = ("Es wurden gar keine Bluetooth-Geräte empfangen. Antennen am Funkmodul prüfen "
                               "oder einen USB-Bluetooth-Stick verwenden.")

    def _apply_status_only(self, cam):
        """Der Schlüssel folgt dem Modus: Nur-Akku-Kameras tragen den Schlüssel des HDMI-Eingangs, sonst einen eigenen dji-Schlüssel."""
        taken = {c.cfg.get("rtmp_key") for c in self.cameras.values() if c is not cam}
        if cam.cfg.get("status_only"):
            cam.cfg["rtmp_key"] = STATUS_ONLY_KEY
        elif cam.cfg.get("rtmp_key") == STATUS_ONLY_KEY:
            cam.cfg["rtmp_key"] = camera_key(cam.addr, taken)

    def sanitize(self, cam):
        cfg = cam.cfg
        if not str(cfg.get("name", "")).strip():
            cfg["name"] = cfg.get("model") or cam.addr
        cfg["name"] = cfg["name"].strip()[:40]
        if cfg.get("status_only"):
            cfg["autoconnect"] = True
        if cfg.get("resolution") not in RESOLUTIONS:
            cfg["resolution"] = "1080p"
        if cfg.get("fps") not in FPS:
            cfg["fps"] = 30
        try:
            cfg["bitrate"] = min(16000, max(500, int(cfg.get("bitrate", 6000))))
        except (TypeError, ValueError):
            cfg["bitrate"] = 6000
        if cfg.get("stabilization") not in STABILIZATION:
            cfg["stabilization"] = "off"
        if not re.match(r"^[A-Za-z0-9_-]{1,32}$", cfg.get("rtmp_key", "")):
            cfg["rtmp_key"] = camera_key(cam.addr, {c.cfg.get("rtmp_key") for c in self.cameras.values() if c is not cam})

    async def handle(self, req):
        cmd = req.get("cmd")
        if cmd == "state":
            return self.snapshot()
        if cmd == "wifi_options":
            opts = await asyncio.get_event_loop().run_in_executor(None, self.wifi_options)
            return {"wifi_options": [public_option(o) for o in opts]}
        if cmd == "adapters":
            return await asyncio.get_event_loop().run_in_executor(None, self.adapter_info)
        if cmd == "scan":
            asyncio.ensure_future(self.scan())
            return {"ok": True}
        if cmd == "add":
            addr = str(req.get("addr", ""))
            if not MAC_RE.match(addr):
                return {"error": "Ungültige Geräteadresse"}
            addr = addr.upper()
            if addr in self.cameras:
                return {"ok": True}
            kind = req.get("kind", "unknown")
            # jede Kamera sendet auf ihren eigenen RTMP-Schlüssel, damit mehrere gleichzeitig streamen können
            key = camera_key(addr, {c.cfg.get("rtmp_key") for c in self.cameras.values()})
            cfg = {"name": str(req.get("name") or req.get("model") or addr)[:40], "model": str(req.get("model", "")), "kind": kind,
                   "wifi_ifname": str(req.get("wifi_ifname", "")), "rtmp_key": key, "autoconnect": False,
                   **DEFAULT_SETTINGS}
            prof = req.get("settings") if isinstance(req.get("settings"), dict) else {}
            for k in ("resolution", "fps", "bitrate", "stabilization"):
                if k in prof:
                    cfg[k] = prof[k]
            if req.get("status_only") is True:
                cfg["status_only"], cfg["rtmp_key"] = True, STATUS_ONLY_KEY
            cam = Camera(self, addr, cfg)
            self.cameras[addr] = cam
            self.sanitize(cam)
            self.pin_connection(cam)
            self.save()
            return {"ok": True, "key": cfg["rtmp_key"]}
        if cmd in ("phone_pair", "phone_pair_stop", "phone_read", "phone_remove"):
            return await self.handle_phone(cmd, req)
        if cmd in ("ctrl_scan", "ctrl_pair", "ctrl_remove"):
            return await self.handle_controller(cmd, req)
        addr = str(req.get("addr", "")).upper()
        cam = self.cameras.get(addr)
        if cam is None:
            return {"error": "Unbekannte Kamera"}
        if cmd == "update":
            if cam.mode_locked() and isinstance(req.get("status_only"), bool) and req["status_only"] != bool(cam.cfg.get("status_only")):
                return {"error": "Der Modus lässt sich nicht ändern, solange die Kamera verbunden ist. Zuerst trennen."}
            for k, t in SETTINGS_ALLOWED.items():
                if k in req and isinstance(req[k], t) and not (t is int and isinstance(req[k], bool)):
                    if k == "password" and req[k] == "":
                        continue                         # leer lassen = gespeichertes Passwort behalten
                    cam.cfg[k] = req[k]
            if "status_only" in req and isinstance(req["status_only"], bool):
                self._apply_status_only(cam)
            if "wifi_ifname" in req:
                self.pin_connection(cam)
                self.offer_connection_network(cam)
            if "ssid" in req or "password" in req:
                self._note_connection(cam.cfg)
            self.sanitize(cam)
            if req.get("autoconnect") is True or req.get("status_only") is True:
                cam.manual_off = False
                if not cam.running():
                    cam.start()
            self.save()
            return {"ok": True}
        if cmd in ("use_saved", "delete_saved"):
            ssid = str(req.get("ssid", ""))
            if cmd == "delete_saved":
                cam.cfg["saved"] = [n for n in cam.cfg.get("saved", []) if n["ssid"] != ssid]
            else:
                for n in cam.cfg.get("saved", []):
                    if n["ssid"] == ssid:
                        cam.cfg["ssid"], cam.cfg["password"] = n["ssid"], n["password"]
                        self._note_connection(cam.cfg)
            self.save()
            return {"ok": True}
        if cmd == "remove":
            await cam.stop()
            del self.cameras[addr]
            self.save()
            return {"ok": True}
        if cmd == "connect":
            cam.start()                 # eine Verbindung jetzt; der Schalter "automatisch verbinden" bleibt unberührt
            return {"ok": True}
        if cmd == "disconnect":
            await cam.stop()
            self.save()
            return {"ok": True}
        if cmd == "reconnect":
            asyncio.ensure_future(cam.restart())    # sofort neu verbinden, ohne den Schalter anzufassen
            return {"ok": True}
        return {"error": "Unbekannter Befehl"}

    async def handle_phone(self, cmd, req):
        """Handys: koppeln (die Box ist zwei Minuten sichtbar), Akkustand jetzt lesen, Handy entfernen."""
        if cmd == "phone_pair":
            try:
                await self.phones.start_pairing()
            except phone_battery.PhoneError as e:
                return {"error": str(e)}
            return {"ok": True}
        if cmd == "phone_pair_stop":
            await self.phones.stop_pairing()
            return {"ok": True}
        addr = str(req.get("addr", ""))
        if not MAC_RE.match(addr) or addr.upper() not in self.phones.phones:
            return {"error": "Unbekanntes Handy"}
        addr = addr.upper()
        if cmd == "phone_read":
            asyncio.ensure_future(self.phones.read(addr))
            return {"ok": True}
        self.phones.remove(addr)                  # phone_remove: aus der Liste nehmen und die Kopplung in BlueZ aufheben
        await bluez_cleanup(addr, remove=True)
        return {"ok": True}

    async def handle_controller(self, cmd, req):
        """Controller: in der Nähe suchen, ein Gerät aus der Liste koppeln, einen gekoppelten Controller entfernen."""
        if cmd == "ctrl_scan":
            try:
                await self.ctrl.start_scan()
            except controllers.ControllerError as e:
                return {"error": str(e)}
            return {"ok": True}
        addr = str(req.get("addr", ""))
        if not MAC_RE.match(addr):
            return {"error": "Ungültige Geräteadresse"}
        addr = addr.upper()
        if cmd == "ctrl_pair":
            if self.ctrl.pairing:
                return {"error": "Es wird gerade ein anderes Gerät gekoppelt"}
            asyncio.ensure_future(self._ctrl_pair(addr))      # dauert bis zu 40 s: im Hintergrund, die Oberfläche fragt den Zustand ab
            return {"ok": True}
        if addr not in self.ctrl.items:
            return {"error": "Unbekannter Controller"}
        self.ctrl.remove(addr)                                  # ctrl_remove: aus der Liste nehmen und die Kopplung in BlueZ aufheben
        await bluez_cleanup(addr, remove=True)
        return {"ok": True}

    async def _ctrl_pair(self, addr):
        try:
            await self.ctrl.pair(addr)
        except controllers.ControllerError as e:
            self.ctrl.message = str(e)

    async def client(self, reader, writer):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                req = None
                try:
                    req = json.loads(line)
                    if not isinstance(req, dict):
                        raise ValueError("Ungültige Anfrage")
                    if not hmac.compare_digest(str(req.get("token", "")), self.token):
                        resp = {"error": "kein Zugriff"}
                    else:
                        resp = await self.handle(req)
                except Exception as e:
                    resp = {"error": str(e) or "Ungültige Anfrage"}
                resp["reply_to"] = req.get("id") if isinstance(req, dict) else None
                writer.write((json.dumps(resp) + "\n").encode())
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    async def supervise(self):
        """Netz: Eine Kamera mit "automatisch verbinden" hat immer eine laufende Verbindungsschleife."""
        loop = asyncio.get_event_loop()
        while True:
            await asyncio.sleep(5)
            for cam in list(self.cameras.values()):
                key = cam.cfg.get("rtmp_key") or "cam1"
                cam.publishing = bool(await loop.run_in_executor(None, rtmp_publishing, key, self.stat_url))
                if cam.publishing and cam.state == "streaming" and not cam.cfg.get("wifi_mac"):
                    name = cam.cfg.get("wifi_ifname")
                    if name and name != "manual" and iface_mac(name):
                        # Der Stream kommt an: Diese Verbindung ist die richtige. Ab jetzt zählt ihre Hardware-Adresse, nicht der Name.
                        self.pin_connection(cam)
                        self.save()
                        log.info("%s: Verbindung %s festgehalten", cam.addr, name)
                if cam.cfg.get("autoconnect") and not cam.running() and not cam.manual_off and not self.closing:
                    log.warning("%s: Verbindungsschleife lief nicht, wird neu gestartet", cam.addr)
                    cam.start()

    SETTLED_STATES = ("streaming",)       # Sitzungen in diesem Zustand bleiben beim Beenden des Dienstes unberührt

    async def shutdown(self, wait=8):
        """Beim Beenden des Dienstes (Neustart, Software-Update) die Kameras so zurücklassen, dass der nächste Dienst sofort weitermachen kann.
        Eine Kamera, die gerade streamt, bleibt unberührt: Weder Stream noch Bluetooth-Verbindung werden beendet. BlueZ hält die Verbindung
        weiter, und der neue Dienst verwendet sie weiter (Sekunden). Ein Trennen ließe die Kamera etwa eine Minute lang keine Verbindung annehmen
        (gemessen). Eine Sitzung mitten im Einrichten hört dagegen nach dem laufenden Schritt auf und trennt, damit sie nicht halb eingerichtet
        zurückbleibt. Alles ist zeitlich begrenzt."""
        self.closing = True
        cams = list(self.cameras.values())
        busy = [c for c in cams if c.task is not None and not c.task.done() and c.state not in self.SETTLED_STATES]
        for cam in busy:
            cam.leaving = True
            cam.stop_requested = True
            cam.wake.set()
        tasks = [c.task for c in busy]
        if tasks:
            await asyncio.wait(tasks, timeout=wait)              # die Sitzungen verlassen sich selbst und trennen dabei
        for cam in busy:
            if cam.task is not None and not cam.task.done():
                await cam.release_link()
                cam.task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=3)
        log.info("Kameras zurückgelassen: %d streamen weiter (Verbindung bleibt), %d Sitzungen beendet", len(cams) - len(busy), len(busy))

    async def main(self, host=LISTEN_HOST, port=LISTEN_PORT):
        server = await asyncio.start_server(self.client, host, port)
        log.info("bereit auf %s:%d, Kameras: %d", host, port, len(self.cameras))
        await asyncio.sleep(5)           # BlueZ nach dem Start kurz Zeit geben
        for cam in self.cameras.values():
            if cam.cfg.get("autoconnect"):
                cam.start()
        asyncio.ensure_future(self.supervise())
        asyncio.ensure_future(self.phones.run())
        asyncio.ensure_future(self.ctrl.run())
        async with server:
            await server.serve_forever()


async def amain(args):
    daemon = Daemon(args.state, args.rtmp_port, args.rtmp_app, args.stat_url)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):          # systemctl restart/stop: erst die Kameras freigeben, dann beenden
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError, ValueError):
            pass
    main_task = asyncio.ensure_future(daemon.main(LISTEN_HOST, args.port))
    stopper = asyncio.ensure_future(stop.wait())
    await asyncio.wait({main_task, stopper}, return_when=asyncio.FIRST_COMPLETED)
    if stop.is_set():
        log.info("wird beendet")
        try:
            await asyncio.wait_for(daemon.shutdown(), 20)
        except Exception as e:
            log.info("Beenden der Sitzungen unvollständig: %s", e)
        # Sofort und ohne weiteres Aufräumen beenden: asyncio.run würde die Sitzungen sonst abbrechen, und jede abgebrochene Sitzung trennt ihre
        # Bluetooth-Verbindung (das wollen wir hier gerade nicht, siehe shutdown).
        logging.shutdown()
        os._exit(0)
    stopper.cancel()
    main_task.cancel()
    if not main_task.cancelled() and main_task.done() and main_task.exception():
        raise main_task.exception()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="/var/lib/pipbox")
    ap.add_argument("--port", type=int, default=LISTEN_PORT)
    ap.add_argument("--rtmp-port", type=int, default=RTMP_PORT)
    ap.add_argument("--rtmp-app", default=RTMP_APP)
    ap.add_argument("--stat-url", default=STAT_URL)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
