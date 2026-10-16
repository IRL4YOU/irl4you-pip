"""Akkustand von Handys per Bluetooth (Teil des Bluetooth-Dienstes pipbox-dji, dji_daemon.py).

Das Handy wird einmal gekoppelt (die Box ist dafür zwei Minuten sichtbar). Danach holt die Box alle 10 Minuten kurz den Akkustand und trennt
wieder: Sie verbindet sich als Freisprecheinrichtung (HFP) mit dem Handy, fragt die Anzeigen ab (AT+CIND, bei iPhones zusätzlich
AT+IPHONEACCEV) und schließt die Verbindung. Eine Dauerverbindung gibt es nicht. Android meldet den Akku in Fünfteln (0 bis 5, also
20-%-Schritte), ein iPhone in Zehnteln (0 bis 9).

Läuft im Dienst als Benutzer pipbox: Kopplung über D-Bus (BlueZ, dbus-fast), Abfrage über einen RFCOMM-Anschluss (Python-Socket), den Kanal
liefert sdptool. Die gekoppelten Handys stehen in <state>/phones.json (Name, Akkustand, Zeit, Fehler); die Kopplung selbst verwaltet BlueZ.
"""
import asyncio
import json
import logging
import os
import re
import socket
import subprocess
import threading
import time

try:
    from dbus_fast import BusType, Variant
    from dbus_fast.aio import MessageBus
    from dbus_fast.service import ServiceInterface, method
except ImportError:      # ohne dbus-fast (Tests, Vorschau) gibt es keine Kopplung, die Abfrage selbst geht trotzdem
    MessageBus = BusType = Variant = ServiceInterface = method = None

log = logging.getLogger("pipbox-dji")

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
INTERVAL = 600             # Sekunden zwischen zwei Abfragen eines Handys
RETRY = 120                # nach einem Fehler früher noch einmal
PAIR_SECONDS = 120         # so lange ist die Box beim Koppeln sichtbar
CONNECT_TIMEOUT = 15
IO_TIMEOUT = 8
SDP_TIMEOUT = 25
BLUEZ = "org.bluez"
AGENT_PATH = "/pipbox/phone_agent"
HFP_AG = "111f"            # Dienstkennung "Handsfree Audio Gateway": so nennt sich ein Handy, das Freisprechgeräte bedient


class PhoneError(Exception):
    """Fehler mit einem Text, der dem Nutzer angezeigt werden kann."""


# -- Abfrage ----------------------------------------------------------------------------------------------------------------------------

def parse_sdp_channel(text):
    """RFCOMM-Kanal des Freisprechdienstes aus der Ausgabe von `sdptool search HFAG`, sonst None. Ein Handy kann mehrere Einträge haben
    (Freisprechgerät und Audio Gateway); es zählt der mit der Klasse 0x111f."""
    for block in re.split(r"\n\s*\n|(?=Service Name:)", text):
        if re.search(r"\(0x%s\)" % HFP_AG, block, re.I):
            m = re.search(r"Channel:\s*(\d+)", block)
            if m:
                return int(m.group(1))
    return None


def find_channel(addr, hci=None, run=subprocess.run):
    """Fragt das Handy nach dem Kanal seines Freisprechdienstes."""
    cmd = ["sdptool"] + (["-i", hci] if hci else []) + ["search", "--bdaddr", addr, "HFAG"]
    try:
        out = run(cmd, capture_output=True, text=True, timeout=SDP_TIMEOUT)
    except FileNotFoundError:
        raise PhoneError("Das Programm sdptool fehlt auf der Box")
    except subprocess.TimeoutExpired:
        raise PhoneError("Das Handy antwortet nicht. Ist Bluetooth am Handy an und das Handy nah genug an der Box?")
    ch = parse_sdp_channel(out.stdout or "")
    if ch is None:
        err = (out.stderr or out.stdout or "").lower()
        if "host is down" in err or "timed out" in err or "no route" in err:
            raise PhoneError("Das Handy ist nicht erreichbar. Ist Bluetooth am Handy an und das Handy nah genug an der Box?")
        raise PhoneError("Das Handy bietet kein Freisprechprofil an, der Akkustand lässt sich so nicht lesen")
    return ch


class _AtLink:
    """Zeilenweises Lesen und Schreiben der AT-Befehle über einen Socket."""

    def __init__(self, sock, timeout=IO_TIMEOUT):
        self.sock, self.timeout, self.buf = sock, timeout, b""

    def send(self, cmd):
        self.sock.sendall(cmd.encode("ascii") + b"\r")

    def lines(self, until_ok=True, wait=None):
        """Zeilen bis OK oder ERROR (until_ok) bzw. so lange, wie wait Sekunden vergehen. Gibt (Zeilen, Ergebnis) zurück,
        Ergebnis "OK", "ERROR" oder None."""
        out, end = [], time.monotonic() + (wait if wait is not None else self.timeout)
        while True:
            while True:
                m = re.search(rb"\r\n|\r|\n", self.buf)
                if not m:
                    break
                raw, self.buf = self.buf[:m.start()], self.buf[m.end():]
                s = raw.decode("ascii", "replace").strip()
                if not s:
                    continue
                if until_ok and (s == "OK" or s.startswith("ERROR") or s.startswith("+CME ERROR")):
                    return out, "OK" if s == "OK" else "ERROR"
                out.append(s)
            left = end - time.monotonic()
            if left <= 0:
                return out, None
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(1024)
            except socket.timeout:
                return out, None
            if not chunk:
                return out, None
            self.buf += chunk


def parse_cind_names(line):
    """('service',(0,1)),('battchg',(0,5)) ... aus der Antwort auf AT+CIND=? -> [(Name, größter Wert), ...] in der Reihenfolge der Anzeigen."""
    return [(n.lower(), int(hi)) for n, hi in re.findall(r'\("([^"]+)",\(\s*\d+\s*[-,]\s*(\d+)\s*\)\)', line)]


def parse_iphone_level(line):
    """+IPHONEACCEV: 2,1,7,2,0 -> 7 (Schlüssel 1 ist der Akkustand von 0 bis 9), sonst None."""
    m = re.match(r"\+IPHONEACCEV:\s*([\d,\s]+)", line)
    if not m:
        return None
    nums = [int(x) for x in re.split(r"\s*,\s*", m.group(1).strip()) if x != ""]
    if not nums:
        return None
    pairs = nums[1:1 + 2 * nums[0]]
    for i in range(0, len(pairs) - 1, 2):
        if pairs[i] == 1:
            return max(0, min(9, pairs[i + 1]))
    return None


def hfp_battery(sock, timeout=IO_TIMEOUT):
    """Der Ablauf einer Freisprechverbindung (SLC) bis zu den Anzeigen, dann der Akkustand in Prozent.
    Gibt {"percent": int, "steps": int} zurück (steps: Auflösung, 5 bei Android, 9 beim iPhone)."""
    link = _AtLink(sock, timeout)
    link.send("AT+BRSF=0")
    _, res = link.lines()
    if res != "OK":
        raise PhoneError("Das Handy hat die Freisprechverbindung nicht angenommen")
    link.send("AT+CIND=?")
    rows, res = link.lines()
    names = next((parse_cind_names(r) for r in rows if r.startswith("+CIND:")), [])
    if res != "OK" or not names:
        raise PhoneError("Das Handy meldet keine Anzeigen (Akku)")
    link.send("AT+CIND?")
    rows, res = link.lines()
    vals = next(([int(x) for x in re.findall(r"-?\d+", r.split(":", 1)[1])] for r in rows if r.startswith("+CIND:")), [])
    idx = next((i for i, (n, _) in enumerate(names) if n == "battchg"), None)
    if res != "OK" or idx is None or idx >= len(vals):
        raise PhoneError("Das Handy meldet seinen Akkustand nicht")
    hi = names[idx][1] or 5
    percent, steps = round(max(0, min(vals[idx], hi)) * 100 / hi), hi
    link.send("AT+CMER=3,0,0,1")                 # beendet den Aufbau; einige Handys melden Anzeigen erst danach
    extra, _ = link.lines(until_ok=False, wait=1.0)
    link.send("AT+XAPL=ABCD-1234-0100,2")        # nur iPhones antworten darauf, sie melden dann den Akku in Zehnteln
    more, _ = link.lines(until_ok=False, wait=1.2)
    for r in extra + more:
        lvl = parse_iphone_level(r)
        if lvl is not None:
            percent, steps = round(lvl * 100 / 9), 9
        m = re.match(r"\+CIEV:\s*(\d+)\s*,\s*(\d+)", r)
        if m and int(m.group(1)) == idx + 1 and steps != 9:
            percent = round(max(0, min(int(m.group(2)), hi)) * 100 / hi)
    try:
        link.send("AT+CMER=3,0,0,0")             # Anzeigen wieder abbestellen, dann wird getrennt
    except OSError:
        pass
    return {"percent": percent, "steps": steps}


def connect_rfcomm(addr, channel, adapter_mac=None):
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    try:
        if adapter_mac:
            s.bind((adapter_mac, 0))
        s.settimeout(CONNECT_TIMEOUT)
        s.connect((addr, channel))
    except OSError:
        s.close()
        raise
    return s


def read_battery(addr, hci=None, adapter_mac=None, channel=None, connect=connect_rfcomm, find=find_channel):
    """Holt den Akkustand eines gekoppelten Handys. Gibt {"percent", "steps", "channel"} zurück oder löst PhoneError aus."""
    if channel is None:
        channel = find(addr, hci)
    try:
        s = connect(addr, channel, adapter_mac)
    except OSError as e:
        if e.errno in (111, 13, 1):                    # abgewiesen: nicht (mehr) gekoppelt oder der Dienst ist am Handy aus
            raise PhoneError("Das Handy hat die Verbindung abgewiesen. Eventuell Handy in der Box entfernen und neu koppeln")
        raise PhoneError("Das Handy ist nicht erreichbar. Ist Bluetooth am Handy an und das Handy nah genug an der Box?")
    try:
        res = hfp_battery(s)
    except socket.timeout:
        raise PhoneError("Das Handy hat nicht rechtzeitig geantwortet")
    except OSError:
        raise PhoneError("Die Verbindung zum Handy riss ab")
    finally:
        try:
            s.close()
        except OSError:
            pass
    res["channel"] = channel
    return res


# -- Kopplung (D-Bus) -------------------------------------------------------------------------------------------------------------------

if ServiceInterface is not None:
    class PairAgent(ServiceInterface):
        """Bestätigt jede Kopplung während der zwei Minuten, in denen die Box sichtbar ist (ohne Eingabe: Handy und Box bestätigen nur)."""

        def __init__(self):
            super().__init__("org.bluez.Agent1")

        @method()
        def Release(self):
            pass

        @method()
        def RequestPinCode(self, device: 'o') -> 's':
            return "0000"

        @method()
        def DisplayPinCode(self, device: 'o', pincode: 's'):
            pass

        @method()
        def RequestPasskey(self, device: 'o') -> 'u':
            return 0

        @method()
        def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q'):
            pass

        @method()
        def RequestConfirmation(self, device: 'o', passkey: 'u'):
            pass

        @method()
        def RequestAuthorization(self, device: 'o'):
            pass

        @method()
        def AuthorizeService(self, device: 'o', uuid: 's'):
            pass

        @method()
        def Cancel(self):
            pass


def _prop(v):
    """Wert einer dbus-fast-Eigenschaft (Variant) oder eines einfachen Werts."""
    return getattr(v, "value", v)


class Phones:
    """Die Liste der Handys, die Kopplung und der 10-Minuten-Takt. snapshot() liefert das, was die Oberfläche zeigt."""

    def __init__(self, state_dir, hci=lambda: None, adapter_mac=lambda hci: None, busy=lambda: False, taken=lambda: (), reader=read_battery):
        self.path = os.path.join(state_dir, "phones.json")
        self.hci, self.adapter_mac, self.busy, self.taken, self.reader = hci, adapter_mac, busy, taken, reader
        self.phones = {}
        self.due = {}                 # Adresse -> Zeitpunkt (time.monotonic) der nächsten Abfrage
        self.reading = set()
        self.pair = {"active": False, "until": 0.0, "message": "", "found": ""}
        self._pair_task = None
        self.lock = threading.Lock()
        self.load()

    # Dateien
    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        for addr, p in (d.get("phones") or {}).items() if isinstance(d, dict) else []:
            if MAC_RE.match(addr) and isinstance(p, dict):
                self.phones[addr.upper()] = {"name": str(p.get("name", ""))[:60], "percent": p.get("percent") if isinstance(p.get("percent"), int) else None,
                                             "steps": p.get("steps") if p.get("steps") in (5, 9) else None, "ts": float(p.get("ts") or 0),
                                             "error": str(p.get("error", ""))[:200], "channel": p.get("channel") if isinstance(p.get("channel"), int) else None}
        for addr in self.phones:
            self.due[addr] = time.monotonic() + 15      # kurz nach dem Start einmal, gestaffelt

    def save(self):
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"phones": self.phones}, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    # Anzeige
    def snapshot(self):
        now = time.time()
        phones = []
        for addr, p in sorted(self.phones.items(), key=lambda kv: kv[1].get("name", "").lower()):
            phones.append({"addr": addr, "name": p["name"] or addr, "percent": p["percent"], "steps": p["steps"],
                           "age": int(now - p["ts"]) if p["ts"] else None, "error": p["error"], "reading": addr in self.reading})
        left = max(0, int(self.pair["until"] - time.monotonic())) if self.pair["active"] else 0
        return {"phones": phones, "phone_pairing": {"active": self.pair["active"], "left": left, "message": self.pair["message"]}}

    # Abfrage
    async def read(self, addr):
        """Holt den Akkustand eines Handys jetzt und merkt sich das Ergebnis (oder den Fehler)."""
        addr = addr.upper()
        p = self.phones.get(addr)
        if p is None or addr in self.reading:
            return
        self.reading.add(addr)
        loop = asyncio.get_event_loop()
        try:
            hci = self.hci()
            res = await loop.run_in_executor(None, lambda: self.reader(addr, hci, self.adapter_mac(hci), p.get("channel")))
            p.update(percent=res["percent"], steps=res["steps"], ts=time.time(), error="", channel=res.get("channel"))
            self.due[addr] = time.monotonic() + INTERVAL
            log.info("Handy %s: Akku %d %%", addr, res["percent"])
        except PhoneError as e:
            p.update(error=str(e))
            p["channel"] = None                  # beim nächsten Mal den Kanal neu erfragen
            self.due[addr] = time.monotonic() + RETRY
            log.info("Handy %s: %s", addr, e)
        except Exception as e:                   # nie den Dienst gefährden
            p.update(error="Unerwarteter Fehler beim Lesen des Akkustands")
            self.due[addr] = time.monotonic() + RETRY
            log.warning("Handy %s: %r", addr, e)
        finally:
            self.reading.discard(addr)
            try:
                self.save()
            except OSError as e:
                log.warning("phones.json: %s", e)

    async def run(self):
        """Alle 10 Minuten ein Handy nach dem anderen. Während einer Kamerasuche oder Kopplung wird nichts abgefragt."""
        while True:
            await asyncio.sleep(5)
            if self.busy() or self.pair["active"]:
                continue
            now = time.monotonic()
            for addr in sorted(self.phones, key=lambda a: self.due.get(a, 0)):
                if self.due.get(addr, 0) <= now:
                    await self.read(addr)
                    break

    def remove(self, addr):
        addr = addr.upper()
        self.phones.pop(addr, None)
        self.due.pop(addr, None)
        self.save()

    # Kopplung
    async def start_pairing(self):
        if self.pair["active"]:
            return
        if MessageBus is None:
            raise PhoneError("Die Bluetooth-Bibliothek (dbus-fast) fehlt. install.sh erneut ausführen (braucht Internet).")
        self.pair.update(active=True, until=time.monotonic() + PAIR_SECONDS, message="", found="")
        self._pair_task = asyncio.ensure_future(self._pairing())

    async def stop_pairing(self):
        t = self._pair_task
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self.pair["active"] = False

    async def _pairing(self):
        bus = None
        adapter = None
        try:
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            hci = self.hci() or "hci0"
            ad_path = "/org/bluez/" + hci
            agent = PairAgent()
            bus.export(AGENT_PATH, agent)
            intro = await bus.introspect(BLUEZ, "/org/bluez")
            mgr = bus.get_proxy_object(BLUEZ, "/org/bluez", intro).get_interface("org.bluez.AgentManager1")
            await mgr.call_register_agent(AGENT_PATH, "NoInputNoOutput")
            try:
                await mgr.call_request_default_agent(AGENT_PATH)
            except Exception as e:
                log.info("Kopplung: Standard-Agent nicht gesetzt (%s)", e)
            intro = await bus.introspect(BLUEZ, ad_path)
            adapter = bus.get_proxy_object(BLUEZ, ad_path, intro).get_interface("org.bluez.Adapter1")
            await adapter.set_pairable(True)
            await adapter.set_discoverable_timeout(PAIR_SECONDS)
            await adapter.set_discoverable(True)
            known = {a for a in self.phones} | {str(a).upper() for a in self.taken()}
            root_intro = await bus.introspect(BLUEZ, "/")
            om = bus.get_proxy_object(BLUEZ, "/", root_intro).get_interface("org.freedesktop.DBus.ObjectManager")
            before = set()
            for path, ifs in (await om.call_get_managed_objects()).items():
                d = ifs.get("org.bluez.Device1")
                if d and _prop(d.get("Paired", False)):
                    before.add(str(_prop(d["Address"])).upper())
            while time.monotonic() < self.pair["until"]:
                await asyncio.sleep(2)
                for path, ifs in (await om.call_get_managed_objects()).items():
                    d = ifs.get("org.bluez.Device1")
                    if not d or not _prop(d.get("Paired", False)):
                        continue
                    addr = str(_prop(d["Address"])).upper()
                    if addr in known or addr in before:
                        continue
                    name = str(_prop(d.get("Alias", "")) or _prop(d.get("Name", "")) or addr)
                    self.phones[addr] = {"name": name[:60], "percent": None, "steps": None, "ts": 0.0, "error": "", "channel": None}
                    self.due[addr] = time.monotonic() + 5
                    self.save()
                    try:
                        dev_intro = await bus.introspect(BLUEZ, str(path))
                        props = bus.get_proxy_object(BLUEZ, str(path), dev_intro).get_interface("org.freedesktop.DBus.Properties")
                        await props.call_set("org.bluez.Device1", "Trusted", Variant("b", True))
                    except Exception as e:
                        log.info("Kopplung: Vertrauen nicht gesetzt (%s)", e)
                    self.pair.update(found=name, message="")
                    log.info("Handy gekoppelt: %s (%s)", name, addr)
                    return
            self.pair["message"] = "Kein Handy gekoppelt. Auf dem Handy in den Bluetooth-Einstellungen nach neuen Geräten suchen und die Box auswählen."
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("Kopplung: %r", e)
            self.pair["message"] = "Die Kopplung ließ sich nicht starten"
        finally:
            try:
                if adapter is not None:
                    await adapter.set_discoverable(False)
                    await adapter.set_pairable(False)
            except Exception:
                pass
            try:
                if bus is not None:
                    try:
                        intro = await bus.introspect(BLUEZ, "/org/bluez")
                        await bus.get_proxy_object(BLUEZ, "/org/bluez", intro).get_interface("org.bluez.AgentManager1").call_unregister_agent(AGENT_PATH)
                    except Exception:
                        pass
                    bus.disconnect()
            except Exception:
                pass
            self.pair["active"] = False
