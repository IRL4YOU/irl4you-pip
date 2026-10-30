
import json
import os
import threading
import pipbox_live

from srv.cameras import KEY_RE
from srv.datenschutz import PBPIXEL_BOXES, PBPIXEL_TAP, datenschutz_aktiv
from srv.plugin import CAM_LIVE, FEED_AUDIO_CAPS, FEED_FILL_VIDEO, FEED_PORT, FEED_VIDEO_CAPS, FREE, PIP_CORNERS, SEND_STATUS, feeder_tool, pip_corners, plugin_fill, plugin_multi, plugin_style, plugin_swap, plugin_view
from srv.style import SIZE_MAX, SIZE_MIN, STYLE_SLOTS, clean_styles, pip_size, small_decode, style_text


DEFAULT_MAIN_DELAY_MS = 450


DEFAULT_PIP_DELAY_MS = 0


DELAY_KEYS = ("main_delay_ms", "pip_delay_ms", "pip2_delay_ms", "pip3_delay_ms")


FRAME_MS = 33


class PipelineStore:
    DEFAULT = {"type": "single", "main": "", "pip": "", "corner": 3, "size_pct": 25, "size_pct2": 25, "size_pct3": 25, "audio": "main",
               "pip2": "", "corner2": 2, "pip3": "", "corner3": 0, "x": 500, "y": 500, "x2": 500, "y2": 500, "x3": 500, "y3": 500, "main_delay_ms": DEFAULT_MAIN_DELAY_MS,
               "pip_delay_ms": DEFAULT_PIP_DELAY_MS, "pip2_delay_ms": DEFAULT_PIP_DELAY_MS,
               "pip3_delay_ms": DEFAULT_PIP_DELAY_MS, "auto_failover": True, "swap_cams": 0, "always_ready": False, "facecam": ""}
    SLOTS = ("main", "pip", "pip2", "pip3")
    Q = "queue max-size-time=10000000000 max-size-buffers=1000 max-size-bytes=41943040"
    DATENSCHUTZ_PFAD = None
    DATENSCHUTZ_FEST = "datei"

    @classmethod
    def datenschutz(cls):
        if cls.DATENSCHUTZ_FEST != "datei":
            return cls.DATENSCHUTZ_FEST
        return datenschutz_aktiv(cls.DATENSCHUTZ_PFAD)

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.cfg = dict(self.DEFAULT)
        try:
            with open(path) as f:
                self.cfg.update(json.load(f))
        except FileNotFoundError:
            self.cfg["always_ready"] = True
        except (OSError, ValueError):
            pass
        self.cfg["styles"] = clean_styles(self.cfg.get("styles"))

    def save(self):
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w") as f:
            json.dump(self.cfg, f, indent=1)
        os.replace(tmp, self.path)
        self.write_delay_file()

    def write_delay_file(self):
        if self.path == os.devnull:
            return
        d = os.path.join(os.path.dirname(os.path.abspath(self.path)), "main-delay-ms")
        try:
            base = None
            try:
                with open(SEND_STATUS) as f:
                    base = (json.load(f).get("swap") or {}).get("cams")
            except (OSError, ValueError, AttributeError):
                pass
            vals = self.delay_values(self.cfg, base if isinstance(base, list) else None)
            with open(d + ".tmp", "w") as f:
                f.write(" ".join(map(str, vals)) + "\n")
            os.chmod(d + ".tmp", 0o644)
            os.replace(d + ".tmp", d)
        except OSError:
            pass

    SLOT_DELAY = {"pip": "pip_delay_ms", "pip2": "pip2_delay_ms", "pip3": "pip3_delay_ms"}

    @classmethod
    def facecam_slot(cls, c):
        k = c.get("facecam")
        if not k or c.get("type") != "pip":
            return None
        return next((s for s in cls.SLOTS if c.get(s) == k), None)

    @classmethod
    def _facecam_keeps_the_sound(cls, c):
        if not c.get("facecam"):
            return
        slot = cls.facecam_slot(c)
        if slot is None:
            c["facecam"] = ""
        elif slot != "main" and c.get(slot) in (c.get("inactive") or []):
            c["audio"] = "main"
        else:
            c["audio"] = slot

    def set_facecam(self, slot):
        if slot not in ("", "main", "pip", "pip2", "pip3"):
            raise ValueError("Facecam: eines der Bilder oder keine")
        with self.lock:
            c = self.cfg
            if c.get("type") != "pip":
                raise ValueError("Das gibt es nur bei der Art Bild-in-Bild")
            if slot:
                key = c.get(slot)
                if not key:
                    raise ValueError("Dieses Bild ist nicht belegt")
                if slot != "main" and key in (c.get("inactive") or []):
                    raise ValueError("Diese Kamera ist deaktiviert und kann nicht die Facecam sein")
                c["facecam"] = key
            else:
                c["facecam"] = ""
            self.save()
            return slot or "main"

    def _carry_facecam(self, new):
        old = self.cfg.get("facecam")
        if not old or new.get("type") != "pip":
            return ""
        slot = self.facecam_slot(self.cfg)
        here = {new.get(s) for s in self.SLOTS} - {""}
        if slot in ("pip", "pip2", "pip3"):
            return new.get(slot) or (old if old in here else "")
        return old if slot == "main" and old in here else ""

    def swap_main_pip(self, with_key=None):
        with self.lock:
            c = self.cfg
            if c.get("type") != "pip" or not c.get("pip") or not c.get("main"):
                raise ValueError("Zum Tauschen braucht es ein Hauptbild und ein kleines Bild")
            slot = "pip"
            if with_key:
                slot = next((k for k in self.SLOT_DELAY if c.get(k) == with_key), None)
                if slot is None:
                    raise ValueError("Diese Kamera ist gerade nicht als kleines Bild im Bild")
            dk = self.SLOT_DELAY[slot]
            c["main"], c[slot] = c[slot], c["main"]
            c["main_delay_ms"], c[dk] = c.get(dk, 0), c.get("main_delay_ms", 0)
            if c.get("inactive"):
                rest = [k for k in c["inactive"] if k != c["main"]]
                if rest:
                    c["inactive"] = rest
                else:
                    c.pop("inactive")
            self._facecam_keeps_the_sound(c)
            styles = clean_styles(c.get("styles"))
            idx = STYLE_SLOTS[list(self.SLOT_DELAY).index(slot)]
            shown = not styles[idx]["visible"]
            if shown:
                styles[idx]["visible"] = True
                c["styles"] = styles
            self.save()
            return shown

    def set(self, req, camera_keys):
        t = req.get("type")
        if t not in ("single", "pip"):
            raise ValueError("Art des Bildaufbaus unbekannt")
        main = str(req.get("main", ""))
        if main not in camera_keys:
            raise ValueError("Hauptkamera: bitte eine vorhandene Kamera wählen")
        cfg = {"type": t, "main": main, "pip": "", "corner": 3, "size_pct": 25, "size_pct2": 25, "size_pct3": 25, "audio": "main",
               "pip2": "", "corner2": 2, "pip3": "", "corner3": 0, "x": 500, "y": 500, "x2": 500, "y2": 500, "x3": 500, "y3": 500, "main_delay_ms": DEFAULT_MAIN_DELAY_MS,
               "pip_delay_ms": DEFAULT_PIP_DELAY_MS, "pip2_delay_ms": DEFAULT_PIP_DELAY_MS,
               "pip3_delay_ms": DEFAULT_PIP_DELAY_MS, "auto_failover": req.get("auto_failover", True) is not False, "swap_cams": 0,
               "always_ready": req.get("always_ready") is True}
        cfg["styles"] = clean_styles(req.get("styles"), self.cfg.get("styles"), strict=True)
        if t == "pip":
            try:
                swap = int(req.get("swap_cams", 0) or 0)
            except (TypeError, ValueError):
                raise ValueError("Tausch: aus, 2 oder 4 Kameras")
            if swap not in (0, 2, 4):
                raise ValueError("Tausch: aus, 2 oder 4 Kameras")
            cfg["swap_cams"] = swap
            pipk = str(req.get("pip", ""))
            if pipk not in camera_keys or pipk == main:
                raise ValueError("Kleines Bild: eine andere vorhandene Kamera wählen")
            try:
                corner, pct = int(req.get("corner", 3)), int(req.get("size_pct", 25))
                pct2, pct3 = int(req.get("size_pct2", pct)), int(req.get("size_pct3", pct))
            except (TypeError, ValueError):
                raise ValueError("Ecke und Größe müssen Zahlen sein")
            if corner not in range(len(pip_corners())) or not all(SIZE_MIN <= v <= SIZE_MAX for v in (pct, pct2, pct3)):
                raise ValueError("Position aus der Liste wählen, Skalierung jedes kleinen Bildes %d bis %d Prozent" % (SIZE_MIN, SIZE_MAX))
            audio = req.get("audio", "main")
            if audio not in ("main", "pip", "pip2", "pip3"):
                raise ValueError("Ton: Hauptbild oder eines der kleinen Bilder")
            cfg.update(pip=pipk, corner=corner, size_pct=pct, size_pct2=pct2, size_pct3=pct3, audio=audio)
            for key in ("x", "y", "x2", "y2", "x3", "y3"):
                try:
                    val = int(req.get(key, 500))
                except (TypeError, ValueError):
                    raise ValueError("Position muss eine Zahl sein")
                cfg[key] = max(0, min(1000, val))
            for key, dflt in (("main_delay_ms", DEFAULT_MAIN_DELAY_MS), ("pip_delay_ms", DEFAULT_PIP_DELAY_MS),
                              ("pip2_delay_ms", DEFAULT_PIP_DELAY_MS), ("pip3_delay_ms", DEFAULT_PIP_DELAY_MS)):
                try:
                    delay = int(req.get(key, dflt)) if req.get(key) != "" else 0
                except (TypeError, ValueError):
                    raise ValueError("Verzögerung muss eine Zahl sein (Millisekunden)")
                if not 0 <= delay <= 3000:
                    raise ValueError("Verzögerung: 0 bis 3000 ms")
                cfg[key] = delay
            pip2 = str(req.get("pip2", "") or "")
            if pip2:
                try:
                    corner2 = int(req.get("corner2", 2))
                except (TypeError, ValueError):
                    raise ValueError("Ecke muss eine Zahl sein")
                if pip2 not in camera_keys or pip2 in (main, pipk):
                    raise ValueError("Zweites kleines Bild: eine weitere vorhandene Kamera wählen")
                if corner2 not in range(len(pip_corners())) or (corner2 == corner and corner != FREE):
                    raise ValueError("Zweites kleines Bild: eine andere Position als das erste wählen")
                cfg.update(pip2=pip2, corner2=corner2)
            else:
                cfg["pip2_delay_ms"] = 0
            pip3 = str(req.get("pip3", "") or "")
            if pip3 and pip2:
                try:
                    corner3 = int(req.get("corner3", 0))
                except (TypeError, ValueError):
                    raise ValueError("Ecke muss eine Zahl sein")
                if pip3 not in camera_keys or pip3 in (main, pipk, pip2):
                    raise ValueError("Drittes kleines Bild: eine weitere vorhandene Kamera wählen")
                if corner3 not in range(len(pip_corners())) or (corner3 != FREE and corner3 in (corner, cfg["corner2"])):
                    raise ValueError("Drittes kleines Bild: eine andere Position als die ersten beiden wählen")
                cfg.update(pip3=pip3, corner3=corner3)
            else:
                cfg["pip3_delay_ms"] = 0
            if cfg["audio"] == "pip2" and not cfg["pip2"] or cfg["audio"] == "pip3" and not cfg["pip3"]:
                raise ValueError("Ton: dieses kleine Bild ist nicht gewählt")
        pips = {cfg.get(k) for k in ("pip", "pip2", "pip3")} - {""}
        old = req.get("inactive") if "inactive" in req else self.cfg.get("inactive", [])
        inactive = list(dict.fromkeys(k for k in (old if isinstance(old, list) else []) if isinstance(k, str) and k in pips))[:3]
        if t == "pip" and inactive:
            cfg["inactive"] = inactive
        fc = self._carry_facecam(cfg)
        if fc:
            cfg["facecam"] = fc
        with self.lock:
            self.cfg = cfg
            self.save()

    def set_active(self, key, active):
        if not isinstance(active, bool) or not isinstance(key, str) or not KEY_RE.match(key):
            raise ValueError("Kamera oder Zustand ungültig")
        with self.lock:
            c = self.cfg
            if c.get("type") != "pip":
                raise ValueError("Das gibt es nur bei der Art Bild-in-Bild")
            if key == c.get("main"):
                raise ValueError("Das Hauptbild lässt sich nicht deaktivieren")
            if key not in (c.get("pip"), c.get("pip2"), c.get("pip3")):
                raise ValueError("Diese Kamera ist nicht im Bild")
            cur = [k for k in (c.get("inactive") or []) if k != key]
            if not active:
                cur.append(key)
            if cur:
                c["inactive"] = cur
            else:
                c.pop("inactive", None)
            self.save()

    @classmethod
    def _safe_cfg(cls, c):
        out = dict(c)

        def num(k, lo, hi):
            try:
                v = int(out.get(k, cls.DEFAULT[k]))
            except (TypeError, ValueError):
                v = cls.DEFAULT[k]
            out[k] = max(lo, min(hi, v))
        for k in ("corner", "corner2", "corner3"):
            num(k, 0, len(PIP_CORNERS) - 1)
        num("size_pct", SIZE_MIN, SIZE_MAX)
        for k in ("size_pct2", "size_pct3"):
            if k not in out:
                out[k] = out["size_pct"]
            num(k, SIZE_MIN, SIZE_MAX)
        for k in ("x", "y", "x2", "y2", "x3", "y3"):
            num(k, 0, 1000)
        for k in ("main_delay_ms", "pip_delay_ms", "pip2_delay_ms", "pip3_delay_ms"):
            num(k, 0, 3000)
        num("swap_cams", 0, 4)
        if out["swap_cams"] not in (2, 4):
            out["swap_cams"] = 0
        out["always_ready"] = out.get("always_ready") is True
        out["type"] = "pip" if out.get("type") == "pip" else "single"
        fc = out.get("facecam")
        out["facecam"] = fc if (out["type"] == "pip" and isinstance(fc, str) and KEY_RE.match(fc) and fc in [out.get(s) for s in cls.SLOTS]) else ""
        out["styles"] = clean_styles(out.get("styles"))
        if "inactive" in out:
            ina = out["inactive"]
            out["inactive"] = [k for k in ina if isinstance(k, str) and KEY_RE.match(k)][:3] if isinstance(ina, list) else []
        return out

    @classmethod
    def _layout(cls, c):
        pip = c["type"] == "pip" and bool(KEY_RE.match(c.get("pip", "")))
        pip2 = bool(pip and c.get("pip2") and KEY_RE.match(c["pip2"]) and c.get("corner2") in range(len(PIP_CORNERS))
                    and (c["corner2"] != c["corner"] or c["corner"] == FREE))
        multi = plugin_multi()
        pip3 = bool(multi and pip2 and c.get("pip3") and KEY_RE.match(c["pip3"])
                    and c.get("corner3") in range(len(PIP_CORNERS)) and (c["corner3"] == FREE or c["corner3"] not in (c["corner"], c["corner2"])))
        return pip, pip2, pip3, multi

    @classmethod
    def swap_plan(cls, cfg):
        c = cls._safe_cfg(cfg)
        if c["type"] != "pip" or c["swap_cams"] not in (2, 4) or not KEY_RE.match(c.get("main", "")) or not plugin_swap():
            return None
        pip, pip2, pip3, multi = cls._layout(c)
        if not pip or not multi:
            return None
        cams = [c["main"], c["pip"]] + ([c["pip2"]] if pip2 else []) + ([c["pip3"]] if pip3 else [])
        if len(set(cams)) != len(cams):
            return None
        group = min(c["swap_cams"], len(cams))
        audio = c.get("audio", "main")
        if audio == "pip2" and not pip2 or audio == "pip3" and not pip3 or audio not in ("main", "pip", "pip2", "pip3"):
            audio = "main"
        pos = {"main": -1, "pip": 0, "pip2": 1, "pip3": 2}[audio]
        nums = [0, 1, 2 if pip2 else 15, 3 if pip3 else 15]
        return {"cams": cams, "group": group, "audio_pos": pos, "asel": pos < 0 or pos + 1 < group,
                "state": nums[0] | nums[1] << 4 | nums[2] << 8 | nums[3] << 12, "line": " ".join(map(str, nums))}

    @classmethod
    def always_plan(cls, cfg):
        c = cls._safe_cfg(cfg)
        if pipbox_live.enabled(c) and KEY_RE.match(c.get("main", "")):
            cams = [k for k in (c["main"], c.get("pip", ""), c.get("pip2", ""), c.get("pip3", "")) if k]
            if len(set(cams)) != len(cams):
                return None
            audio = c.get("audio", "main")
            pos = {"main": -1, "pip": 0, "pip2": 1, "pip3": 2}.get(audio, -1)
            return {"cams": cams, "group": len(cams), "audio_pos": pos, "asel": True, "always": True, "live": True, "state": 0x3210, "line": "0 1 2 3"}
        if c["type"] != "pip" or c.get("always_ready") is not True or not KEY_RE.match(c.get("main", "")) or not plugin_swap() or not plugin_fill() or not feeder_tool():
            return None
        pip, pip2, pip3, multi = cls._layout(c)
        if not pip:
            return None
        cams = [c["main"], c["pip"]] + ([c["pip2"]] if pip2 else []) + ([c["pip3"]] if pip3 else [])
        if len(set(cams)) != len(cams):
            return None
        audio = c.get("audio", "main")
        if audio == "pip2" and not pip2 or audio == "pip3" and not pip3 or audio not in ("main", "pip", "pip2", "pip3"):
            audio = "main"
        pos = {"main": -1, "pip": 0, "pip2": 1, "pip3": 2}[audio]
        nums = [0, 1, 2 if pip2 else 15, 3 if pip3 else 15]
        return {"cams": cams, "group": len(cams), "audio_pos": pos, "asel": True, "always": True,
                "state": nums[0] | nums[1] << 4 | nums[2] << 8 | nums[3] << 12, "line": " ".join(map(str, nums))}

    @classmethod
    def always_blocker(cls, cfg):
        c = cls._safe_cfg(cfg)
        if c.get("always_ready") is not True or cls.always_plan(cfg):
            return None
        if c["type"] != "pip":
            return None if pipbox_live.supported() else "Alle Kameras immer bereit ist nicht aktiv: Der Sende-Baustein ist noch nicht auf dem neuen Stand. Die Sendung läuft im normalen Modus. Ein Software-Update baut ihn neu."
        if not feeder_tool():
            return "Alle Kameras immer bereit ist nicht aktiv: Das Programm gst-launch-1.0 fehlt (Paket gstreamer1.0-tools). Die Sendung läuft im normalen Modus. Ein Software-Update installiert das Paket."
        if not plugin_swap() or not plugin_fill():
            return "Alle Kameras immer bereit ist nicht aktiv: Der Überlagerungs-Baustein ist noch nicht auf dem neuen Stand. Die Sendung läuft im normalen Modus. Ein Software-Update baut ihn neu."
        return "Alle Kameras immer bereit ist nicht aktiv: Die Kameras des Bildaufbaus passen nicht dazu (jede Kamera nur einmal, mindestens ein kleines Bild). Die Sendung läuft im normalen Modus."

    AUDIO_POS = {"main": -1, "pip": 0, "pip2": 1, "pip3": 2}

    @classmethod
    def view_values(cls, cfg):
        c = cls._safe_cfg(cfg)
        hide = 0
        for i, k in enumerate(STYLE_SLOTS):
            if not c["styles"][k]["visible"]:
                hide |= 1 << i
        plan = cls.swap_plan(c)
        audio = plan["audio_pos"] if plan else cls.AUDIO_POS.get(c.get("audio", "main"), -1)
        return hide, audio

    @classmethod
    def view_line(cls, cfg, mute=0):
        hide, audio = cls.view_values(cfg)
        return f"{hide} {audio} {1 if mute else 0}"

    def set_view(self, visible=None, audio=None):
        with self.lock:
            c = self.cfg
            if c.get("type") != "pip":
                raise ValueError("Das gibt es nur bei der Art Bild-in-Bild")
            if visible is not None:
                if not isinstance(visible, dict) or any(k not in STYLE_SLOTS or not isinstance(v, bool) for k, v in visible.items()):
                    raise ValueError("Bild einblenden: Stelle 1 bis 3, ja oder nein")
                for k, v in visible.items():
                    if k == "2" and not c.get("pip2") or k == "3" and not c.get("pip3") or k == "1" and not c.get("pip"):
                        raise ValueError("Dieses kleine Bild gibt es nicht")
            if audio is not None:
                if not isinstance(audio, str) or audio not in self.AUDIO_POS:
                    raise ValueError("Ton: Hauptbild oder eines der kleinen Bilder")
                if audio == "pip" and not c.get("pip") or audio == "pip2" and not c.get("pip2") or audio == "pip3" and not c.get("pip3"):
                    raise ValueError("Ton: dieses kleine Bild gibt es nicht")
                if audio != "main" and c.get(audio) in (c.get("inactive") or []):
                    raise ValueError("Ton: eine deaktivierte Kamera kann nicht die Tonquelle sein")
            styles = clean_styles(c.get("styles"))
            for k, v in (visible or {}).items():
                styles[k]["visible"] = v
            c["styles"] = styles
            if audio is not None:
                c["audio"] = audio
            self.save()
            return self.view_values(c)

    @staticmethod
    def swap_line(slots, cams, group):
        slots = [(s or "") for s in slots] + [""] * (4 - len(slots))
        if len(cams) < 2 or group < 2 or group > len(cams) or slots[len(cams):] != [""] * (4 - len(cams)):
            return None
        if sorted(slots[:group]) != sorted(cams[:group]):
            return None
        if any(slots[i] != cams[i] for i in range(group, len(cams))):
            return None
        return " ".join([str(cams.index(slots[0]))] + [str(cams.index(s)) if s else "15" for s in slots[1:]])

    @staticmethod
    def delay_values(cfg, base=None):
        if cfg.get("type") != "pip":
            return [0, 0, 0, 0]
        slots = ("main", "pip", "pip2", "pip3")
        by_key = {cfg[s]: cfg.get(d, 0) for s, d in zip(slots, DELAY_KEYS) if cfg.get(s)}
        order = list(base) if base else [cfg.get(s) for s in slots]
        ds = PipelineStore.datenschutz() if not pipbox_live.enabled(PipelineStore._safe_cfg(cfg)) else None
        pd = int(ds["delay_ms"]) if ds else 0
        vals = []
        for k in (order + [None] * 4)[:4]:
            try:
                vals.append((max(0, min(3000, int(by_key.get(k, 0) or 0))) if k else 0) + pd)
            except (TypeError, ValueError):
                vals.append(pd)
        return vals

    def build(self, cfg=None, rtmp_port=1935, rtmp_app="publish"):
        c = self._safe_cfg(cfg or self.cfg)
        if not KEY_RE.match(c.get("main", "")):
            return ""
        q = self.Q
        base = f"rtmp://127.0.0.1:{rtmp_port}/{rtmp_app}"
        pip, pip2, pip3, multi = self._layout(c)
        if pipbox_live.enabled(c):
            return pipbox_live.build(c, base, ds=self.datenschutz(), ds_files=(PBPIXEL_TAP, PBPIXEL_BOXES))

        def xy(cfg_, kx, ky, corner_):
            if corner_ != FREE:
                return ""
            return f" {kx}={int(cfg_.get(kx, 500))} {ky}={int(cfg_.get(ky, 500))}"
        sty = self._style_props(c)
        out = []
        delay = 0
        if c["type"] == "pip":
            try:
                delay = max(0, min(3000, int(c.get("main_delay_ms", DEFAULT_MAIN_DELAY_MS))))
            except (TypeError, ValueError):
                delay = 0
        ds = self.datenschutz()
        pd = int(ds["delay_ms"]) if ds else 0
        delay += pd
        qa = q + (f" min-threshold-time={delay * 1000000}" if delay else "")
        qv = q + (f" min-threshold-time={(delay + FRAME_MS) * 1000000}" if delay else "")
        if pip:
            qv, qa = qv + " name=mainq_v", qa + " name=mainq_a"
        self._pd = pd

        def small(name, key):
            d = 0
            try:
                d = max(0, min(3000, int(c.get(key, DEFAULT_PIP_DELAY_MS) or 0)))
            except (TypeError, ValueError):
                d = 0
            d += pd
            th = d + FRAME_MS if d else 0
            return (f"queue name={name} max-size-time={(th + 500) * 1000000} max-size-buffers={0 if d else 30} "
                    f"leaky=downstream" + (f" min-threshold-time={th * 1000000}" if d else ""))
        plan = (self.always_plan(c) or self.swap_plan(c)) if pip else None
        if plan:
            return self._build_dual(c, plan, base, small, xy, pip2, pip3, sty)
        out.append(f"rtmpsrc location={base}/{c['main']} do-timestamp=true !\nflvdemux name=demux\n")
        vsrc = "demux.video !\n"
        mask = ""
        if ds:
            out.append("demux.video !\ntee name=ptee\n"
                       "ptee. !\nqueue max-size-time=3000000000 max-size-buffers=0 max-size-bytes=0 leaky=downstream !\nh264parse ! mppvideodec !\n"
                       f"pbpixeltap shm={PBPIXEL_TAP} interval-ms=100 !\nfakesink sync=false async=false\n")
            vsrc = "ptee. !\n"
            mask = f"pbpixelmask boxes={PBPIXEL_BOXES} !\n"
        if pip:
            v = (f"{vsrc}{qv} !\nidentity name=v_delay signal-handoffs=TRUE ! h264parse ! mppvideodec !\n"
                 f"{mask}video/x-raw,format=NV12 !\n"
                 f"pbpipmix name=pipmix corner={c['corner']}{xy(c, 'x', 'y', c['corner'])} width-pct={c['size_pct']}{sty(0, 1)}"
                 + (f" slot2=1 corner2={c['corner2']}{xy(c, 'x2', 'y2', c['corner2'])}{sty(1, 2)}" if pip2 and multi else "")
                 + (f" slot3=2 corner3={c['corner3']}{xy(c, 'x3', 'y3', c['corner3'])}{sty(2, 3)}" if pip3 else "") + " !\n"
                 + (f"pbpipmix name=pipmix2 slot=1 corner={c['corner2']}{xy(c, 'x', 'y', c['corner2'])} width-pct={c['size_pct']}{sty(1, 1)} !\n" if pip2 and not multi else "") +
                 "queue max-size-time=500000000 max-size-buffers=4 leaky=downstream !\n")
        else:
            v = (f"{vsrc}{qv if ds else q} !\nidentity name=v_delay signal-handoffs=TRUE ! h264parse ! mppvideodec !\n"
                 f"{mask}videorate ! video/x-raw,framerate=30/1,format=NV12 ! queue !\n")
        v += ("mpph265enc zero-copy-pkt=0 qp-max=51 gop=60 name=venc_bps !\n"
              f"h265parse config-interval=-1 ! {q} ! mux.\n")
        out.append(v)
        vol = "volume name=avol ! " if pip and plugin_view() else ""
        opus = f"audioconvert ! audioresample quality=10 sinc-filter-mode=1 ! {vol}opusenc bitrate=128000 ! opusparse"
        audio_sel = c.get("audio", "main") if pip else "main"
        if audio_sel == "pip2" and not pip2 or audio_sel == "pip3" and not pip3 or audio_sel not in ("main", "pip", "pip2", "pip3"):
            audio_sel = "main"
        main_audio = audio_sel == "main"
        mute = "queue max-size-buffers=4 leaky=downstream ! fakesink sync=false async=false\n"
        def small_audio(demux, sel):
            if audio_sel == sel:
                return (f"{demux}.audio !\n{q} !\naacparse ! avdec_aac ! identity name=a_delay signal-handoffs=TRUE !\n"
                        f"{opus} ! {q} ! mux.\n")
            return f"{demux}.audio !\n{mute}"
        if main_audio:
            out.append(f"demux.audio !\n{qa} !\naacparse ! avdec_aac ! identity name=a_delay signal-handoffs=TRUE !\n"
                       f"{opus} ! {q} ! mux.\n")
        else:
            out.append("demux.audio !\nqueue max-size-buffers=4 leaky=downstream ! fakesink sync=false async=false\n")
        if pip:
            (w, h), (w2, h2), (w3, h3) = pip_size(c["size_pct"]), pip_size(c["size_pct2"]), pip_size(c["size_pct3"])
            out.append(f"rtmpsrc location={base}/{c['pip']} do-timestamp=true !\nflvdemux name=pdemux\n")
            out.append(f"pdemux.video !\n{small('pipq_v', 'pip_delay_ms')} !\n"
                       f"h264parse ! {small_decode(w, h)}"
                       "queue max-size-time=300000000 max-size-buffers=2 leaky=downstream ! pbpipsink\n")
            if pip2:
                out.append(f"rtmpsrc location={base}/{c['pip2']} do-timestamp=true !\nflvdemux name=p2demux\n")
                out.append(f"p2demux.video !\n{small('pip2q_v', 'pip2_delay_ms')} !\n"
                           f"h264parse ! {small_decode(w2, h2)}"
                           "queue max-size-time=300000000 max-size-buffers=2 leaky=downstream ! pbpipsink slot=1\n")
                out.append(small_audio("p2demux", "pip2"))
            if pip3:
                out.append(f"rtmpsrc location={base}/{c['pip3']} do-timestamp=true !\nflvdemux name=p3demux\n")
                out.append(f"p3demux.video !\n{small('pip3q_v', 'pip3_delay_ms')} !\n"
                           f"h264parse ! {small_decode(w3, h3)}"
                           "queue max-size-time=300000000 max-size-buffers=2 leaky=downstream ! pbpipsink slot=2\n")
                out.append(small_audio("p3demux", "pip3"))
            out.append(small_audio("pdemux", "pip"))
        if pip:
            out.append("pbctl name=pbctl video-queue=mainq_v audio-queue=mainq_a pip-queue=pipq_v"
                       + (" pip2-queue=pip2q_v" if pip2 else "") + (" pip3-queue=pip3q_v" if pip3 else "") + "\n")
        out.append("mpegtsmux name=mux !\nappsink name=appsink\n")
        return "\n".join(out)

    @staticmethod
    def _style_props(c):
        texts = [style_text(c["styles"][k]) for k in STYLE_SLOTS] if plugin_style() else ["", "", ""]

        def sty(pos, n):
            return f' style{n}="{texts[pos]}"' if texts[pos] else ""
        return sty

    def _build_dual(self, c, plan, base, small, xy, pip2, pip3, sty):
        q = self.Q
        cams, group, ap = plan["cams"], plan["group"], plan["audio_pos"]
        always = bool(plan.get("always"))
        sizes = [pip_size(c["size_pct"]), pip_size(c["size_pct2"]), pip_size(c["size_pct3"])]
        out = []
        fillv = f' fill-caps="{FEED_FILL_VIDEO}"' if always else ""
        filla = f' fill-caps="{FEED_AUDIO_CAPS}"' if always else ""
        out.append(f"pbpipsel name=vsel tag-offset=true force-key=true state={plan['state']}{fillv} !\n"
                   "identity name=v_delay signal-handoffs=TRUE !\nvideo/x-raw,format=NV12 !\n"
                   f"pbpipmix name=pipmix follow-tag=true corner={c['corner']}{xy(c, 'x', 'y', c['corner'])} width-pct={c['size_pct']}{sty(0, 1)}"
                   + (f" slot2=1 corner2={c['corner2']}{xy(c, 'x2', 'y2', c['corner2'])}{sty(1, 2)}" if pip2 else "")
                   + (f" slot3=2 corner3={c['corner3']}{xy(c, 'x3', 'y3', c['corner3'])}{sty(2, 3)}" if pip3 else "") + " !\n"
                   "queue max-size-time=500000000 max-size-buffers=4 leaky=downstream !\n"
                   "mpph265enc zero-copy-pkt=0 qp-max=51 gop=60 name=venc_bps !\n"
                   f"h265parse config-interval=-1 ! {q} ! mux.\n")
        vol = "volume name=avol ! " if plugin_view() else ""
        opus = f"audioconvert ! audioresample quality=10 sinc-filter-mode=1 ! {vol}opusenc bitrate=128000 ! opusparse"
        mute = "queue max-size-buffers=4 leaky=downstream ! fakesink sync=false async=false\n"
        queues = []
        pd = getattr(self, "_pd", 0)
        for i, key in enumerate(cams):
            d = int(c.get(DELAY_KEYS[i], 0) or 0) + pd
            items = [f"vsq{i}:s"]
            vport, aport = FEED_PORT + 2 * i, FEED_PORT + 2 * i + 1
            if always:
                vsrc = (f'udpsrc port={vport} address=127.0.0.1 buffer-size=8388608 do-timestamp=true caps="{FEED_VIDEO_CAPS}" !\n'
                        "rtph264depay ! h264parse config-interval=-1 !\n")
            else:
                out.append(f"rtmpsrc location={base}/{key} do-timestamp=true !\nflvdemux name=dm{i}\n")
                vsrc = f"dm{i}.video !\n"
            w, h = sizes[max(i - 1, 0)]
            small_chain = (f"{small(f'vsq{i}', DELAY_KEYS[i])} !\nh264parse ! {small_decode(w, h)}"
                           f"queue max-size-time=300000000 max-size-buffers=2 leaky=downstream ! pbpipsink slot={(i + 3) % 4}\n")
            if i < group:
                qv = f"{q} name=vfq{i}" + (f" min-threshold-time={(d + FRAME_MS) * 1000000}" if d else "")
                out.append(f"{vsrc}tee name=vt{i}\n")
                mask = ""
                if pd and i == 0:
                    out.append(f"vt{i}. !\nqueue max-size-time=3000000000 max-size-buffers=0 max-size-bytes=0 leaky=downstream !\nh264parse ! mppvideodec !\n"
                               f"pbpixeltap shm={PBPIXEL_TAP} interval-ms=100 !\nfakesink sync=false async=false\n")
                    mask = f"pbpixelmask boxes={PBPIXEL_BOXES} !\n"
                out.append(f"vt{i}. !\n{qv} !\nh264parse ! mppvideodec !\n{mask}video/x-raw,format=NV12 !\nvsel.sink_{i}\n")
                out.append(f"vt{i}. !\n{small_chain}")
                items.insert(0, f"vfq{i}:v")
            else:
                out.append(f"{vsrc}{small_chain}")
            qa = f"{q} name=aq{i}" + (f" min-threshold-time={d * 1000000}" if d else "")
            if plan["asel"] and i < group:
                if always:
                    out.append(f'udpsrc port={aport} address=127.0.0.1 buffer-size=1048576 do-timestamp=true caps="{FEED_AUDIO_CAPS}" !\n{qa} !\nasel.sink_{i}\n')
                else:
                    out.append(f"dm{i}.audio !\n{qa} !\naacparse ! avdec_aac ! audioconvert ! audioresample quality=10 sinc-filter-mode=1 !\n"
                               f"audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved ! asel.sink_{i}\n")
                items.append(f"aq{i}:a")
            elif not plan["asel"] and i == ap + 1:
                out.append(f"dm{i}.audio !\n{qa} !\naacparse ! avdec_aac ! identity name=a_delay signal-handoffs=TRUE !\n{opus} ! {q} ! mux.\n")
                items.append(f"aq{i}:a")
            else:
                out.append(f"dm{i}.audio !\n{mute}")
            queues.append(f" cam{i}={','.join(items)}")
        if plan["asel"]:
            out.append(f"pbpipsel name=asel state={0 if ap < 0 else ap + 1}{filla} !\nidentity name=a_delay signal-handoffs=TRUE !\n"
                       f"{vol}opusenc bitrate=128000 ! opusparse ! {q} ! mux.\n")
        out.append("pbctl name=pbctl selector=vsel" + (" audio-selector=asel" if plan["asel"] else "") + f" audio-pos={ap}" + "".join(queues)
                   + (f" live-file={CAM_LIVE}" if always else "") + "\n")
        out.append("mpegtsmux name=mux !\nappsink name=appsink\n")
        return "\n".join(out)

    def status(self, cams):
        keys = [c["key"] for c in cams]
        return {"config": dict(self.cfg, styles=clean_styles(self.cfg.get("styles"))), "plugin_style": plugin_style(), "cameras": [{"key": c["key"], "name": c["name"], "state": c.get("state", "unknown"), "w": c.get("w", 0), "h": c.get("h", 0)} for c in cams],
                "corners": list(pip_corners()), "preview": self.build() if self.cfg.get("main") in keys else "",
                "needs_plugin": self.cfg.get("type") == "pip", "plugin_present": os.path.exists("/opt/pipbox/gst/libgstpbpip.so")}


def view_only_change(a, b):
    def norm(c):
        c = dict(c)
        c.pop("facecam", None)
        c["audio"] = "main"
        c["styles"] = clean_styles(c.get("styles"))
        for k in STYLE_SLOTS:
            c["styles"][k] = dict(c["styles"][k], visible=True)
        return c
    return a.get("type") == "pip" and norm(a) == norm(b)


def always_compatible(a, b):
    def norm(c):
        c = dict(c)
        for k in ("main", "pip", "pip2", "pip3"):
            c[k] = bool(c.get(k))
        for k in DELAY_KEYS:
            c.pop(k, None)
        for k in ("audio", "inactive", "auto_failover", "swap_cams", "facecam"):
            c.pop(k, None)
        c["styles"] = clean_styles(c.get("styles"))
        for k in STYLE_SLOTS:
            c["styles"][k] = dict(c["styles"][k], visible=True)
        return c
    both = a.get("type") in ("pip", "single") and b.get("type") in ("pip", "single") and a.get("always_ready") is True and b.get("always_ready") is True
    if pipbox_live.supported() and both:
        return True
    return both and a.get("type") == b.get("type") and norm(a) == norm(b)
