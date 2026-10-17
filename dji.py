import json
import os
import re
import subprocess


SYSFS_USB = "/sys/bus/usb/devices"
SYSFS_BT = "/sys/class/bluetooth"
SYSFS_NET = "/sys/class/net"
BTDRIVER_STATUS = "/run/pipbox-btdriver/status.json"
BARROT_VENDOR = "33fa"
BARROT_HINT = ("Dieser Stick hat einen BARROT-Chip (zum Beispiel UGREEN Bluetooth 5.4 oder 6.0). Der Kernel dieser BELABOX (5.10) startet ihn nicht von allein. "
               "Die Box richtet dafür beim Einstecken selbst einen Treiber ein (dauert wenige Minuten, nie während einer Übertragung). "
               "Steht diese Meldung danach noch da, ließ sich der Treiber nicht einrichten (siehe die Meldung zum Treiber): "
               "Bitte einen Stick mit Realtek-Chip RTL8761B verwenden, zum Beispiel TP-Link UB500 oder ASUS USB-BT500.")


def _read1(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe63\uff0d"), "-")
_GENERIC_NAME = re.compile(r"^(802\.11|wlan\b|wireless|wi-?fi\b|bluetooth|usb\b|nic\b|network|adapter)", re.I)


def clean_devname(text, limit=60):
    s = str(text or "").translate(_DASHES)
    s = "".join(ch if ch.isprintable() else " " for ch in s)
    return re.sub(r"\s+", " ", s).strip()[:limit]


_HWDB = {}
_VENDOR_SUFFIX = re.compile(r"[,\s]+(semiconductor\s+)?(corp(oration)?\.?|inc\.?|co\.?,?\s*ltd\.?|ltd\.?|gmbh)$", re.I)


def hwdb_names(usb_id):
    if usb_id in _HWDB:
        return _HWDB[usb_id]
    vendor = model = ""
    m = re.match(r"^([0-9a-fA-F]{4}):([0-9a-fA-F]{4})$", str(usb_id or ""))
    if m:
        try:
            out = subprocess.run(["systemd-hwdb", "query", "usb:v%sp%s" % (m.group(1).upper(), m.group(2).upper())],
                                 capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        for line in out.splitlines():
            if line.startswith("ID_VENDOR_FROM_DATABASE="):
                vendor = _VENDOR_SUFFIX.sub("", clean_devname(line.split("=", 1)[1], 40))
            elif line.startswith("ID_MODEL_FROM_DATABASE="):
                model = clean_devname(line.split("=", 1)[1], 80)
        b = re.search(r"\[([^\]]+)\]", model)
        if b:
            model = b.group(1).strip()
    _HWDB[usb_id] = (vendor, model)
    return _HWDB[usb_id]


def device_label(name, vendor="", usb_id=""):
    name, vendor = clean_devname(name), clean_devname(vendor, 40)
    if not name:
        return vendor
    if _GENERIC_NAME.match(name) or len(name) < 8:
        db_vendor = db_model = ""
        if usb_id:
            db_vendor, db_model = hwdb_names(usb_id)
            if db_model and not _GENERIC_NAME.match(db_model):
                return db_model if db_vendor.lower() in db_model.lower() else f"{db_vendor} {db_model}".strip()
        brand = db_vendor or vendor
        if brand and brand.lower() not in name.lower():
            return f"{brand} {name}"
    return name


def usb_bluetooth_devices(root=None):
    root = root or SYSFS_USB
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for n in names:
        if ":" in n:
            continue
        base = os.path.join(root, n)
        vid, pid = _read1(f"{base}/idVendor").lower(), _read1(f"{base}/idProduct").lower()
        if not vid or not pid:
            continue
        bt, driver = False, ""
        try:
            for i in sorted(os.listdir(base)):
                ib = os.path.join(base, i)
                if ":" in i and (_read1(f"{ib}/bInterfaceClass").lower(), _read1(f"{ib}/bInterfaceSubClass"),
                                 _read1(f"{ib}/bInterfaceProtocol")) == ("e0", "01", "01"):
                    bt = True
                    drv = os.path.realpath(f"{ib}/driver") if os.path.islink(f"{ib}/driver") else ""
                    driver = driver or os.path.basename(drv)
        except OSError:
            pass
        if bt or vid == BARROT_VENDOR:
            out.append({"id": f"{vid}:{pid}", "name": _read1(f"{base}/product") or "Bluetooth-Stick", "driver": driver})
    return out


def usb_device_above(path):
    p = os.path.realpath(path)
    for _ in range(12):
        vid, pid = _read1(f"{p}/idVendor").lower(), _read1(f"{p}/idProduct").lower()
        if vid and pid:
            return {"usb_id": f"{vid}:{pid}", "name": clean_devname(_read1(f"{p}/product")), "vendor": clean_devname(_read1(f"{p}/manufacturer"), 40)}
        up = os.path.dirname(p)
        if up == p:
            break
        p = up
    return {}


def usb_info_for_hci(name, root=None):
    return usb_device_above(os.path.join(root or SYSFS_BT, name))


def usb_id_for_hci(name, root=None):
    return usb_info_for_hci(name, root).get("usb_id", "")


def netdev_info(name, root=None):
    base = os.path.join(root or SYSFS_NET, name, "device")
    out = dict(usb_device_above(base)) or {}
    out.setdefault("usb_id", "")
    out.setdefault("name", "")
    out.setdefault("vendor", "")
    try:
        out["driver"] = os.path.basename(os.path.realpath(os.path.join(base, "driver"))) if os.path.islink(os.path.join(base, "driver")) else ""
    except OSError:
        out["driver"] = ""
    return out


def usb_id_from_modalias(modalias):
    m = re.search(r"usb:v([0-9A-Fa-f]{4})p([0-9A-Fa-f]{4})", str(modalias or ""))
    return f"{m.group(1).lower()}:{m.group(2).lower()}" if m else ""


def adapter_problems(adapter_ids, usb_devs):
    have = set(adapter_ids)
    out = []
    for d in usb_devs:
        if d["id"] in have:
            continue
        if d["id"].startswith(BARROT_VENDOR + ":"):
            hint = BARROT_HINT
        else:
            hint = (f"Der Stick {d['name']} ({d['id']}) wurde erkannt, aber der Kernel hat keinen Bluetooth-Adapter daraus gemacht. "
                    "Möglicherweise fehlt die Firmware oder der Chip wird nicht unterstützt (Systemmeldungen: dmesg).")
        out.append({"id": d["id"], "name": d["name"], "hint": hint})
    return out


BLUEZ = "org.bluez"
OM = "org.freedesktop.DBus.ObjectManager"


def bluez_objects():
    try:
        import dbus
        bus = dbus.SystemBus()
        return dict(dbus.Interface(bus.get_object(BLUEZ, "/"), OM).GetManagedObjects())
    except Exception:
        return {}


def bluez_device_state(addr, objects=None):
    objs = bluez_objects() if objects is None else objects
    want = str(addr).upper()
    for path, ifs in objs.items():
        d = ifs.get("org.bluez.Device1")
        if d and str(d.get("Address", "")).upper() == want:
            return {"path": str(path), "connected": bool(d.get("Connected", False)), "resolved": bool(d.get("ServicesResolved", False)),
                    "name": str(d.get("Name", ""))}
    return None


def adapter_info(objects=None):
    objs = bluez_objects() if objects is None else objects
    adapters, ids = [], []
    for path, ifs in sorted(objs.items(), key=lambda kv: str(kv[0])):
        a = ifs.get("org.bluez.Adapter1")
        if not a:
            continue
        mid = usb_id_from_modalias(a.get("Modalias"))
        info = usb_info_for_hci(os.path.basename(str(path)))
        uid = info.get("usb_id") or ("" if mid.startswith("1d6b:") else mid)
        adapters.append({"usb_id": uid, "name": info.get("name", ""), "vendor": info.get("vendor", ""),
                         "address": str(a.get("Address", "")), "powered": bool(a.get("Powered", False))})
        if uid:
            ids.append(uid)
    drv = {}
    try:
        with open(BTDRIVER_STATUS) as f:
            raw = json.load(f)
        drv = {"state": str(raw.get("state", "")), "message": str(raw.get("message", ""))[:300]}
    except (OSError, ValueError, AttributeError):
        pass
    return {"adapters": adapters, "adapter_problems": adapter_problems(ids, usb_bluetooth_devices()), "driver": drv}
