"""Bluetooth-Controller (Mini-Tastenfelder, Gamepads, Fernauslöser) der Box (Teil des Bluetooth-Dienstes pipbox-dji, dji_daemon.py).

Die Oberfläche kann in der Nähe suchen (Liste mit Name und Signalstärke), ein Gerät aus der Liste koppeln und gekoppelte Controller mit
Zustand (verbunden oder getrennt) und Akku zeigen. Die Tasten selbst legt Linux als Eingabegerät an (BlueZ, HID); Funktionen hinter den Tasten
folgen später. Kopplung, Suche und Zustand laufen über D-Bus (BlueZ, dbus-fast); gemerkt werden die Controller in <state>/controllers.json.
"""
import asyncio
import json
import logging
import os
import re
import time

import phone_battery as pb

log = logging.getLogger("pipbox-dji")

MAC_RE = pb.MAC_RE
BLUEZ = pb.BLUEZ
SCAN_SECONDS = 12
PAIR_TIMEOUT = 40
REFRESH = 5
HID_UUID = "00001812"        # HID over GATT; klassische Tastaturen und Gamepads melden dazu die Geräteklasse "Peripheral"


class ControllerError(Exception):
    """Fehler mit einem Text, der dem Nutzer angezeigt werden kann."""


def _prop(v):
    return getattr(v, "value", v)


def is_input_device(dev):
    """Sieht das Gerät nach einem Controller aus (Eingabegerät)? dev: Eigenschaften von org.bluez.Device1 (einfache Werte)."""
    if any(str(u).lower().startswith(HID_UUID) for u in (dev.get("UUIDs") or [])):
        return True
    if str(dev.get("Icon", "")).startswith("input-"):
        return True
    cls = dev.get("Class")
    if isinstance(cls, int) and (cls >> 8) & 0x1F == 5:           # Hauptklasse 5: Peripheral (Tastatur, Maus, Gamepad)
        return True
    app = dev.get("Appearance")
    return isinstance(app, int) and 0x03C0 <= app <= 0x03FF         # Appearance "HID" (Tastatur, Maus, Gamepad, Fernbedienung ...)


def plain(props):
    """Eigenschaften eines BlueZ-Objekts ohne dbus-Hülle."""
    return {k: _prop(v) for k, v in (props or {}).items()}


def scan_results(objects, known=(), only_inputs=True):
    """Aus dem BlueZ-Zustand die Liste der Geräte, die gerade gesehen werden (RSSI vorhanden), stärkstes Signal zuerst.
    objects: {Pfad: {Schnittstelle: Eigenschaften}}; known: Adressen, die nicht erscheinen sollen (Kameras, Handys, schon gekoppelte)."""
    out, skip = [], {str(a).upper() for a in known}
    for ifs in objects.values():
        d = ifs.get("org.bluez.Device1")
        if not d:
            continue
        d = plain(d)
        addr = str(d.get("Address", "")).upper()
        if not MAC_RE.match(addr) or addr in skip or d.get("RSSI") is None:
            continue
        inp = is_input_device(d)
        if only_inputs and not inp:
            continue
        name = str(d.get("Alias") or d.get("Name") or "").strip()
        if name.replace("-", ":").upper() == addr:
            name = ""                                   # BlueZ nennt namenlose Geräte nach ihrer Adresse
        out.append({"addr": addr, "name": name, "rssi": int(d["RSSI"]), "input": inp, "paired": bool(d.get("Paired", False))})
    out.sort(key=lambda r: (not r["input"], -r["rssi"]))
    return out


def live_state(objects, addrs):
    """{Adresse: {"connected": bool, "battery": int oder None}} der gekoppelten Controller nach dem Zustand von BlueZ."""
    want, res = {a.upper() for a in addrs}, {}
    for ifs in objects.values():
        d = ifs.get("org.bluez.Device1")
        if not d:
            continue
        d = plain(d)
        addr = str(d.get("Address", "")).upper()
        if addr in want:
            b = ifs.get("org.bluez.Battery1")
            pct = plain(b).get("Percentage") if b else None
            res[addr] = {"connected": bool(d.get("Connected", False)), "battery": int(pct) if isinstance(pct, int) else None}
    return res


class Controllers:
    def __init__(self, state_dir, taken=lambda: (), hci=lambda: None):
        self.path = os.path.join(state_dir, "controllers.json")
        self.taken, self.hci = taken, hci
        self.items = {}
        self.live = {}
        self.found = []
        self.scan = {"active": False, "until": 0.0, "message": ""}
        self.pairing = ""                # Adresse, die gerade gekoppelt wird
        self.message = ""
        self._scan_task = None
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        for addr, c in (d.get("controllers") or {}).items() if isinstance(d, dict) else []:
            if MAC_RE.match(addr) and isinstance(c, dict):
                self.items[addr.upper()] = {"name": str(c.get("name", ""))[:60]}

    def save(self):
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"controllers": self.items}, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    def snapshot(self):
        ctl = []
        for addr, c in sorted(self.items.items(), key=lambda kv: kv[1]["name"].lower()):
            lv = self.live.get(addr, {})
            ctl.append({"addr": addr, "name": c["name"] or addr, "connected": bool(lv.get("connected")), "battery": lv.get("battery"),
                        "pairing": addr == self.pairing})
        left = max(0, int(self.scan["until"] - time.monotonic())) if self.scan["active"] else 0
        found = [r for r in self.found if r["addr"] not in self.items]
        return {"controllers": ctl, "ctrl_found": found, "ctrl_scan": {"active": self.scan["active"], "left": left, "message": self.scan["message"]},
                "ctrl_message": self.message}

    # -- Zustand von BlueZ
    async def _objects(self, bus):
        intro = await bus.introspect(BLUEZ, "/")
        om = bus.get_proxy_object(BLUEZ, "/", intro).get_interface("org.freedesktop.DBus.ObjectManager")
        return await om.call_get_managed_objects()

    async def run(self):
        """Alle paar Sekunden den Zustand (verbunden, Akku) der gekoppelten Controller aus BlueZ lesen."""
        bus = None
        while True:
            await asyncio.sleep(REFRESH)
            if not self.items or pb.MessageBus is None:
                continue
            try:
                if bus is None:
                    bus = await pb.MessageBus(bus_type=pb.BusType.SYSTEM).connect()
                self.live = live_state(await self._objects(bus), self.items)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.info("Controller: Zustand nicht lesbar (%r)", e)
                try:
                    if bus is not None:
                        bus.disconnect()
                except Exception:
                    pass
                bus = None

    # -- Suche
    async def start_scan(self):
        if self.scan["active"]:
            return
        if pb.MessageBus is None:
            raise ControllerError("Die Bluetooth-Bibliothek (dbus-fast) fehlt. install.sh erneut ausführen (braucht Internet).")
        self.scan.update(active=True, until=time.monotonic() + SCAN_SECONDS, message="")
        self.message = ""
        self.found = []
        self._scan_task = asyncio.ensure_future(self._scanning())

    async def _scanning(self):
        bus = adapter = None
        try:
            bus = await pb.MessageBus(bus_type=pb.BusType.SYSTEM).connect()
            ad_path = "/org/bluez/" + (self.hci() or "hci0")
            intro = await bus.introspect(BLUEZ, ad_path)
            adapter = bus.get_proxy_object(BLUEZ, ad_path, intro).get_interface("org.bluez.Adapter1")
            await adapter.call_start_discovery()
            known = set(self.items) | {str(a).upper() for a in self.taken()}
            while time.monotonic() < self.scan["until"]:
                await asyncio.sleep(2)
                self.found = scan_results(await self._objects(bus), known, only_inputs=False)
            if not self.found:
                self.scan["message"] = "Kein Gerät gefunden. Controller einschalten, in den Kopplungsmodus bringen, nah an die Box legen und erneut suchen."
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("Controller-Suche: %r", e)
            self.scan["message"] = "Die Suche ließ sich nicht starten"
        finally:
            try:
                if adapter is not None:
                    await adapter.call_stop_discovery()
            except Exception:
                pass
            try:
                if bus is not None:
                    bus.disconnect()
            except Exception:
                pass
            self.scan["active"] = False

    # -- Koppeln
    async def pair(self, addr):
        """Koppelt ein Gerät aus der Suchliste, vertraut ihm und verbindet es. Löst ControllerError mit lesbarem Text aus."""
        addr = addr.upper()
        if pb.MessageBus is None:
            raise ControllerError("Die Bluetooth-Bibliothek (dbus-fast) fehlt. install.sh erneut ausführen (braucht Internet).")
        if self.pairing:
            raise ControllerError("Es wird gerade ein anderes Gerät gekoppelt")
        self.pairing, self.message = addr, ""
        name = next((r["name"] for r in self.found if r["addr"] == addr), "") or addr
        bus = None
        try:
            bus = await pb.MessageBus(bus_type=pb.BusType.SYSTEM).connect()
            bus.export(pb.AGENT_PATH, pb.PairAgent())
            intro = await bus.introspect(BLUEZ, "/org/bluez")
            mgr = bus.get_proxy_object(BLUEZ, "/org/bluez", intro).get_interface("org.bluez.AgentManager1")
            await mgr.call_register_agent(pb.AGENT_PATH, "NoInputNoOutput")
            try:
                await mgr.call_request_default_agent(pb.AGENT_PATH)
            except Exception as e:
                log.info("Controller: Standard-Agent nicht gesetzt (%s)", e)
            path = "/org/bluez/%s/dev_%s" % (self.hci() or "hci0", addr.replace(":", "_"))
            dintro = await bus.introspect(BLUEZ, path)
            obj = bus.get_proxy_object(BLUEZ, path, dintro)
            dev = obj.get_interface("org.bluez.Device1")
            try:
                await asyncio.wait_for(dev.call_pair(), PAIR_TIMEOUT)
            except Exception as e:
                if "AlreadyExists" not in str(e):
                    raise
            try:
                await obj.get_interface("org.freedesktop.DBus.Properties").call_set("org.bluez.Device1", "Trusted", pb.Variant("b", True))
            except Exception as e:
                log.info("Controller: Vertrauen nicht gesetzt (%s)", e)
            try:
                await asyncio.wait_for(dev.call_connect(), 15)
            except Exception as e:
                log.info("Controller: Verbinden nach dem Koppeln: %s", e)
            self.items[addr] = {"name": name[:60]}
            self.save()
            self.found = [r for r in self.found if r["addr"] != addr]
            log.info("Controller gekoppelt: %s (%s)", name, addr)
        except asyncio.CancelledError:
            raise
        except ControllerError:
            raise
        except Exception as e:
            log.warning("Controller koppeln: %r", e)
            raise ControllerError("Das Koppeln hat nicht geklappt. Controller in den Kopplungsmodus bringen, nah an die Box legen und erneut versuchen.")
        finally:
            self.pairing = ""
            try:
                if bus is not None:
                    try:
                        intro = await bus.introspect(BLUEZ, "/org/bluez")
                        await bus.get_proxy_object(BLUEZ, "/org/bluez", intro).get_interface("org.bluez.AgentManager1").call_unregister_agent(pb.AGENT_PATH)
                    except Exception:
                        pass
                    bus.disconnect()
            except Exception:
                pass

    def remove(self, addr):
        addr = addr.upper()
        self.items.pop(addr, None)
        self.live.pop(addr, None)
        self.save()
