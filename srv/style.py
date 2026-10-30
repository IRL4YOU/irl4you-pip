
import re


STYLE_SLOTS = ("1", "2", "3")


CROP_REF_W, CROP_REF_H, CROP_KEEP = 1920, 1080, 32


STYLE_DEFAULT = {"visible": True, "opacity": 100, "crop": {"l": 0, "r": 0, "t": 0, "b": 0}, "radius": 0,
                 "border": {"enabled": False, "width": 6, "color": "#ffffff", "opacity": 100}}


STYLE_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _style_copy(st):
    out = {"visible": st["visible"], "opacity": st["opacity"], "crop": dict(st["crop"]), "radius": st.get("radius", 0), "border": dict(st["border"])}
    if "radius" not in st:
        out["radius"] = int(out["border"].get("radius", 0)) if out["border"].get("enabled") else 0
    out["border"].pop("radius", None)
    return out


def clean_style(src, old=None, strict=False):
    st = _style_copy(old if isinstance(old, dict) and "crop" in old and "border" in old else STYLE_DEFAULT)
    if src is None:
        src = {}
    if not isinstance(src, dict):
        if strict:
            raise ValueError("Aussehen des kleinen Bildes: ungültige Angabe")
        src = {}

    def num(v, lo, hi, cur, what):
        if isinstance(v, bool) or not isinstance(v, (int, float)) and not (isinstance(v, str) and v.strip().lstrip("-").isdigit()):
            if strict:
                raise ValueError(f"{what} muss eine Zahl sein")
            return cur
        n = int(float(v))
        if strict and not lo <= n <= hi:
            raise ValueError(f"{what}: {lo} bis {hi}")
        return max(lo, min(hi, n))
    if "visible" in src:
        if isinstance(src["visible"], bool):
            st["visible"] = src["visible"]
        elif strict:
            raise ValueError("Kleines Bild zeigen: ja oder nein")
    if "opacity" in src:
        st["opacity"] = num(src["opacity"], 0, 100, st["opacity"], "Deckkraft")
    crop = src.get("crop")
    if isinstance(crop, dict):
        for k, lbl in (("l", "links"), ("r", "rechts"), ("t", "oben"), ("b", "unten")):
            if k in crop:
                st["crop"][k] = num(crop[k], 0, 1900, st["crop"][k], f"Beschnitt {lbl}") // 2 * 2
    elif crop is not None and strict:
        raise ValueError("Beschnitt: ungültige Angabe")
    c = st["crop"]
    for a, b, total in (("l", "r", CROP_REF_W), ("t", "b", CROP_REF_H)):
        room = total - CROP_KEEP
        if c[a] + c[b] > room:
            if strict:
                raise ValueError(f"Beschnitt: {'links plus rechts' if a == 'l' else 'oben plus unten'} höchstens {room} Pixel "
                                 f"(es bleiben mindestens {CROP_KEEP} Pixel stehen)")
            k = room / float(c[a] + c[b])
            c[a], c[b] = int(c[a] * k) // 2 * 2, int(c[b] * k) // 2 * 2
    if "radius" in src:
        st["radius"] = num(src["radius"], 0, 60, st["radius"], "Eckenrundung")
    border = src.get("border")
    if isinstance(border, dict):
        b = st["border"]
        if "enabled" in border:
            if isinstance(border["enabled"], bool):
                b["enabled"] = border["enabled"]
            elif strict:
                raise ValueError("Rahmen: ein oder aus")
        if "width" in border:
            b["width"] = num(border["width"], 1, 40, b["width"], "Rahmendicke")
        if "color" in border:
            if isinstance(border["color"], str) and STYLE_COLOR_RE.match(border["color"]):
                b["color"] = border["color"].lower()
            elif strict:
                raise ValueError("Rahmenfarbe: Farbe wie #ffffff")
        if "opacity" in border:
            b["opacity"] = num(border["opacity"], 10, 100, b["opacity"], "Rahmendeckkraft")
        if "radius" in border:
            legacy = num(border["radius"], 0, 60, st["radius"], "Eckenrundung")
            if "radius" not in src and b["enabled"]:
                st["radius"] = legacy
    elif border is not None and strict:
        raise ValueError("Rahmen: ungültige Angabe")
    return st


def clean_styles(src, old=None, strict=False):
    if src is not None and not isinstance(src, dict):
        if strict:
            raise ValueError("Aussehen der kleinen Bilder: ungültige Angabe")
        src = None
    old = old if isinstance(old, dict) else {}
    return {k: clean_style((src or {}).get(k), old.get(k), strict) for k in STYLE_SLOTS}


def style_text(st):
    parts = []
    op = st["opacity"] if st["visible"] else 0
    if op != 100:
        parts.append(f"op={int(op)}")
    for key, name in (("l", "cl"), ("r", "cr"), ("t", "ct"), ("b", "cb")):
        if st["crop"][key]:
            parts.append(f"{name}={int(st['crop'][key])}")
    b = st["border"]
    if b["enabled"]:
        parts.append(f"bw={int(b['width'])}")
        parts.append(f"bc={b['color'][1:]}")
        if b["opacity"] != 100:
            parts.append(f"bo={int(b['opacity'])}")
    if st["radius"]:
        parts.append(f"br={int(st['radius'])}")
    return ",".join(parts)


SIZE_MIN, SIZE_MAX = 1, 100


HW_MIN_W, HW_MIN_H = 128, 72


def pip_size(pct):
    w = max(16, int(round(1920 * pct / 100.0 / 16.0)) * 16)
    h = int(round(w * 9 / 16.0 / 2.0)) * 2
    return w, h


def small_decode(w, h):
    if w >= HW_MIN_W:
        return f"mppvideodec width={w} height={h} !\nvideo/x-raw,format=NV12 !\n"
    return (f"mppvideodec width={HW_MIN_W} height={HW_MIN_H} !\nvideoscale !\nvideo/x-raw,format=NV12,width={w},height={h} !\n")
