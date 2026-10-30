
import json
import math
import os
import re
import threading
import time

from tw.notifier import TwitchNotifier
from tw.store import TwitchStore


class SysAlerts:
    METRICS = (("temp", "CPU-Temperatur", "°C", 3, (40, 100), 65), ("cpu", "CPU-Auslastung", "%", 5, (20, 100), 75), ("ram", "RAM-Auslastung", "%", 5, (20, 100), 60))
    DEFAULT_ALERT = "Warnung: {Name} bei {Wert} (Grenze {Grenze})"
    DEFAULT_OK = "{Name} wieder normal ({Wert})"
    DEFAULT_TEMP = "Box-Temperatur: {Wert}"
    DEFAULT_FPS = "Warnung: {Name} liefert nur {Wert}"
    FPS_LOW = 20.0
    FPS_HOLD_S = 15.0
    FPS_REARM = 3.0
    DEFAULT_STATUS = {"ok": "🟢 Alles okay", "warn": "🟡 Es gibt eine Warnung", "crit": "🔴 Es gibt ein Problem"}
    DEFAULTS = {"enabled": False, "cooldown": 10, "recover": True, "only_live": True, "msg_alert": DEFAULT_ALERT, "msg_ok": DEFAULT_OK, "msg_temp": DEFAULT_TEMP, "msg_fps": DEFAULT_FPS, "msg_st_ok": DEFAULT_STATUS["ok"],
                "msg_st_warn": DEFAULT_STATUS["warn"], "msg_st_crit": DEFAULT_STATUS["crit"], "cmd_temp": False, "cmd_status": False, "cmd_vip": False, "cmd_pixel": False, "pixel_head_mod": False, "fps_on": True,
                **{k: {"on": True, "limit": dflt} for k, _n, _u, _h, _r, dflt in METRICS}}
    POLL_S = 10
    SUSTAIN = 3
    RETRY_S = 60
    COOLDOWN = (1, 120)
    CMDS = ("!temp", "!status", "!pixel")
    CMD_FLAG = {"!temp": "cmd_temp", "!status": "cmd_status", "!pixel": "cmd_pixel"}
    PIXEL_ON, PIXEL_OFF = ("an", "on"), ("aus", "off")
    PIXEL_HEAD = ("kopf", "köpfe", "koepfe", "kopfe", "head", "heads", "gesicht", "gesichter", "face", "faces")
    PIXEL_PLATE = ("kennz", "kennzeichen", "plate", "plates", "nummernschild", "nummernschilder")
    PIXEL_GAP_S = 10
    CMD_POLL_S = 2
    CMD_GAP_S = 30
    CMD_MAX_AGE_S = 60

    def __init__(self, path, notifier, sampler, demo=False, mono=time.monotonic, wall=time.time):
        self.path, self.notifier, self.sampler, self.demo = path, notifier, sampler, demo
        self.mono, self.wall = mono, wall
        self.lock = threading.Lock()
        self.data = json.loads(json.dumps(self.DEFAULTS))
        self.state = {k: {"over": 0, "alerting": False, "last": None, "retry": None, "pending_ok": False} for k, *_ in self.METRICS}
        self.last = None
        self.stop_ev = threading.Event()
        self.reader = None
        self.pixel_get = None
        self.pixel_set = None
        self.fps_fn = None
        self.fps_state = {}
        self.light_fn = None
        self.cmd_since = None
        self.cmd_last = {}
        try:
            with open(path) as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = None
        if isinstance(saved, dict):
            if saved.get("cmd_on") is True and "cmd_temp" not in saved and "cmd_status" not in saved:
                saved = dict(saved, cmd_temp=True, cmd_status=True)
            try:
                self.data = self._merge(self.data, saved, strict=False)
            except ValueError:
                pass

    @classmethod
    def _int(cls, v, lo, hi, error):
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise ValueError(error)
        return v

    @classmethod
    def _merge(cls, base, req, strict=True):
        new = json.loads(json.dumps(base))

        def take(fn):
            try:
                fn()
            except ValueError:
                if strict:
                    raise
        for key in ("enabled", "recover", "only_live", "cmd_temp", "cmd_status", "cmd_vip", "cmd_pixel", "pixel_head_mod", "fps_on"):
            if key in req:
                if isinstance(req[key], bool):
                    new[key] = req[key]
                elif strict:
                    raise ValueError("ja oder nein")
        if "cooldown" in req:
            def f():
                new["cooldown"] = cls._int(req["cooldown"], cls.COOLDOWN[0], cls.COOLDOWN[1], "Pause: Zahl von %d bis %d Minuten" % cls.COOLDOWN)
            take(f)
        for key, name, unit, _h, (lo, hi), _d in cls.METRICS:
            sub = req.get(key)
            if isinstance(sub, dict):
                if "on" in sub:
                    if isinstance(sub["on"], bool):
                        new[key]["on"] = sub["on"]
                    elif strict:
                        raise ValueError("ja oder nein")
                if "limit" in sub:
                    def g(key=key, sub=sub, name=name, unit=unit, lo=lo, hi=hi):
                        new[key]["limit"] = cls._int(sub["limit"], lo, hi, "%s: Zahl von %d bis %d" % (name, lo, hi))
                    take(g)
            elif sub is not None and strict:
                raise ValueError("Ungültige Anfrage")
        for key in ("msg_alert", "msg_ok", "msg_temp", "msg_fps", "msg_st_ok", "msg_st_warn", "msg_st_crit"):
            if key in req:
                def h(key=key):
                    new[key] = TwitchStore._message(req[key])
                take(h)
        return new

    def _write(self, data):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def save(self, req):
        if not isinstance(req, dict):
            raise ValueError("Ungültige Anfrage")
        with self.lock:
            new = self._merge(self.data, req, strict=True)
            try:
                self._write(new)
            except OSError:
                raise RuntimeError("Die Einstellungen konnten nicht gespeichert werden")
            self.data = new
            for st in self.state.values():
                st.update(over=0, retry=None)
        return self.status()

    @staticmethod
    def render(template, name, value, limit):
        vals = {"Name": name, "Wert": value, "Grenze": limit}
        return re.sub(r"\{(Name|Wert|Grenze)\}", lambda m: vals[m.group(1)], template)

    @staticmethod
    def fmt(v, unit):
        return "%d %s" % (round(v), unit)

    def values(self):
        try:
            s = self.sampler.sample() or {}
        except Exception:
            return {"temp": None, "cpu": None, "ram": None}

        def num(v):
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None
        return {"temp": num(s.get("temp_c")), "cpu": num((s.get("cpu") or {}).get("avg")), "ram": num((s.get("mem") or {}).get("used_pct"))}

    def _creds(self):
        cfg = self.notifier.store.notify_settings()
        return cfg if (cfg.get("login") and cfg.get("channel") and cfg.get("token")) else None

    def _live(self):
        return self.notifier._live()

    def _send(self, cfg, text):
        ok, msg = self.notifier._deliver(cfg, text, wait=True)
        with self.lock:
            self.last = {"ok": ok, "time": int(self.wall()), "text": text if ok else msg}
        return ok

    def tick(self):
        with self.lock:
            data = json.loads(json.dumps(self.data))
        if not data["enabled"]:
            self._reset()
            return
        cfg = self._creds()
        if cfg is None:
            self._reset()
            return
        if data["only_live"] and not self._live():
            self._reset(keep_alert=True)
            return
        vals = self.values()
        now = self.mono()
        pause = data["cooldown"] * 60
        for key, name, unit, hyst, _r, _d in self.METRICS:
            m, v = data[key], vals[key]
            st = self.state[key]
            if not m["on"] or v is None:
                st.update(over=0)
                continue
            limit = m["limit"]
            st["over"] = st["over"] + 1 if v >= limit else 0
            if st["retry"] is not None and now < st["retry"]:
                continue
            if st["over"] >= self.SUSTAIN and (st["last"] is None or now - st["last"] >= pause):
                text = self.render(data["msg_alert"], name, self.fmt(v, unit), self.fmt(limit, unit))
                if self._send(cfg, text):
                    st.update(alerting=True, last=now, retry=None, pending_ok=data["recover"])
                else:
                    st["retry"] = now + self.RETRY_S
            elif st["alerting"] and v < limit - hyst:
                st["alerting"] = False
                if st["pending_ok"] and data["recover"]:
                    text = self.render(data["msg_ok"], name, self.fmt(v, unit), self.fmt(limit, unit))
                    if not self._send(cfg, text):
                        st["alerting"], st["retry"] = True, now + self.RETRY_S
                        continue
                st["pending_ok"] = False
        self.fps_tick(data, cfg, now, pause)

    def fps_tick(self, data, cfg, now, pause):
        try:
            cams = (self.fps_fn() if (self.fps_fn and data.get("fps_on", True)) else None) or {}
        except Exception:
            cams = {}
        for key in [k for k in self.fps_state if k not in cams]:
            del self.fps_state[key]
        for key, c in cams.items():
            try:
                dmx, dec = float(c["rates"][0]), float(c["rates"][1])
            except (KeyError, TypeError, ValueError, IndexError):
                continue
            if 0 < dmx < self.FPS_LOW:
                kind, fps = "camera", dmx
            elif dmx > 0 and 0 < dec < self.FPS_LOW:
                kind, fps = "box", dec
            else:
                kind, fps = None, min(dmx, dec) if dec > 0 else dmx
            st = self.fps_state.setdefault(key, {"since": None, "alerting": False, "last": None, "retry": None, "pending_ok": False, "kind": None})
            name = str(c.get("name") or key)
            label = name
            limit = self.fmt(self.FPS_LOW, "fps")
            if kind is not None:
                if st["since"] is None or st["kind"] != kind:
                    st["since"], st["kind"] = now, kind
                if st["retry"] is not None and now < st["retry"]:
                    continue
                if now - st["since"] >= self.FPS_HOLD_S and (st["last"] is None or now - st["last"] >= pause):
                    text = self.render(data["msg_fps"], label, self.fmt(fps, "fps"), limit)
                    if self._send(cfg, text):
                        st.update(alerting=True, last=now, retry=None, pending_ok=data["recover"], name=label)
                    else:
                        st["retry"] = now + self.RETRY_S
            else:
                st["since"], st["kind"] = None, None
                if st["alerting"] and fps >= self.FPS_LOW + self.FPS_REARM:
                    st["alerting"] = False
                    if st["pending_ok"] and data["recover"]:
                        text = self.render(data["msg_ok"], st.get("name") or name, self.fmt(fps, "fps"), limit)
                        if not self._send(cfg, text):
                            st["alerting"], st["retry"] = True, now + self.RETRY_S
                            continue
                    st["pending_ok"] = False

    def _allowed(self, item, vip):
        badges = item.get("badges") or []
        return "broadcaster" in badges or "moderator" in badges or (vip and "vip" in badges)

    def _allowed_pixel(self, item, vip):
        badges = item.get("badges") or []
        return "broadcaster" in badges or "moderator" in badges or (vip and "vip" in badges)

    def _is_cmd(self, item):
        if item.get("type") != "msg":
            return None
        parts = str(item.get("text") or "").split()
        return parts[0].lower() if parts and parts[0].lower() in self.CMDS else None

    def _light_text(self, data):
        try:
            light = self.light_fn() if self.light_fn else None
        except Exception:
            light = None
        level = (light or {}).get("level")
        if level not in ("ok", "warn", "crit"):
            return "⚪ Status nicht verfügbar"
        text = data["msg_st_" + level] + (" (keine Sendung)" if level == "ok" and not light.get("live") else "")
        why = [r["text"] for r in (light.get("reasons") or []) if isinstance(r, dict) and r.get("kind") == "fps" and r.get("text")][:2]
        return text + (": " + " · ".join(why) if why else "")

    @staticmethod
    def _pixel_state(an, kopf, kennz):
        h, k = bool(an and kopf), bool(an and kennz)
        return "Verpixeln: Köpfe %s, Kennzeichen %s" % ("an" if h else "aus", "an" if k else "aus")

    def _pixel_text(self, item, data):
        if self.pixel_get is None or self.pixel_set is None:
            return "Verpixeln ist hier nicht verfügbar"
        try:
            st = self.pixel_get() or {}
        except Exception:
            return "Verpixeln: Stand nicht lesbar"
        an, kopf, kennz = bool(st.get("an")), st.get("kopf") is not False, st.get("kennz") is not False
        cur_h, cur_k = an and kopf, an and kennz
        words = [w.lower() for w in str(item.get("text") or "").split()[1:]]
        if not words:
            return self._pixel_state(an, kopf, kennz)
        on = [w for w in words if w in self.PIXEL_ON + self.PIXEL_OFF]
        target = [w for w in words if w in self.PIXEL_HEAD + self.PIXEL_PLATE]
        if len(on) != 1 or len(target) > 1 or len(words) != len(on) + len(target):
            return "Nutzung: !pixel an|aus, !pixel kopf an|aus, !pixel kennz an|aus"
        want = on[0] in self.PIXEL_ON
        new_h, new_k = cur_h, cur_k
        if not target:
            new_h = new_k = want
        elif target[0] in self.PIXEL_HEAD:
            new_h = want
        else:
            new_k = want
        if (new_h, new_k) == (cur_h, cur_k):
            return self._pixel_state(an, kopf, kennz) + " (schon so)"
        badges = item.get("badges") or []
        if cur_h and not new_h and "broadcaster" not in badges and not (data.get("pixel_head_mod") and "moderator" in badges):
            return "Köpfe ausschalten darf nur der Streamer"
        if new_h or new_k:
            args = (True, new_h, new_k)
        else:
            args = (False, kopf, kennz)
        try:
            ok, note = self.pixel_set(*args)
        except Exception:
            return "Verpixeln nicht geändert"
        if not ok:
            return "Verpixeln nicht geändert: " + str(note)
        return self._pixel_state(*args) + (" (%s)" % note if note else "")

    def _reply(self, cfg, item, cmd="!temp", data=None):
        data = data or self.data
        who = TwitchNotifier.camera_text(item.get("name"))
        if cmd == "!pixel":
            text = ("@%s " % who if who else "") + self._pixel_text(item, data)
        elif cmd == "!status":
            text = ("@%s " % who if who else "") + self._light_text(data)
        else:
            v = self.values()["temp"]
            text = ("@%s " % who if who else "") + data["msg_temp"].replace("{Wert}", "%d °C" % round(v) if v is not None else "nicht messbar")
        if self.demo:
            self.reader._add({"type": "msg", "name": "Box", "color": "#9146FF", "text": text, "emotes": []})
            return True
        return self._send(cfg, text)

    def cmd_tick(self):
        with self.lock:
            data = json.loads(json.dumps(self.data))
        cfg = self._creds()
        if not (data["cmd_temp"] or data["cmd_status"] or data["cmd_pixel"]) or self.reader is None or (cfg is None and not self.demo):
            self.cmd_since = None
            return
        r = self.reader.poll(self.cmd_since or 0)
        last = r.get("last", 0)
        if self.cmd_since is None:
            self.cmd_since = last
            return
        self.cmd_since = last
        now = self.wall()
        for it in r.get("items", []):
            cmd = self._is_cmd(it)
            if not cmd or not data[self.CMD_FLAG[cmd]] or now - it.get("t", now) > self.CMD_MAX_AGE_S:
                continue
            if not (self._allowed_pixel(it, data["cmd_vip"]) if cmd == "!pixel" else self._allowed(it, data["cmd_vip"])):
                continue
            last = self.cmd_last.get(cmd)
            if last is not None and self.mono() - last < (self.PIXEL_GAP_S if cmd == "!pixel" else self.CMD_GAP_S):
                continue
            self.cmd_last[cmd] = self.mono()
            self._reply(cfg, it, cmd, data)

    def run_cmd(self):
        while not self.stop_ev.is_set():
            try:
                self.cmd_tick()
            except Exception as e:
                print("sysalerts-cmd:", type(e).__name__)
            self.stop_ev.wait(self.CMD_POLL_S)

    def _reset(self, keep_alert=False):
        for st in self.state.values():
            st.update(over=0, retry=None)
            if not keep_alert:
                st.update(alerting=False, pending_ok=False)
        for st in self.fps_state.values():
            st.update(since=None, retry=None)
            if not keep_alert:
                st.update(alerting=False, pending_ok=False)

    def run(self):
        while not self.stop_ev.is_set():
            try:
                self.tick()
            except Exception as e:
                print("sysalerts:", type(e).__name__)
            self.stop_ev.wait(self.POLL_S)

    def status(self):
        with self.lock:
            d = json.loads(json.dumps(self.data))
            last = dict(self.last) if self.last else None
        vals = self.values()
        return dict(d, ready=self._creds() is not None,
                    values={k: (None if vals[k] is None else round(vals[k], 1)) for k in vals},
                    alerting=[k for k, st in self.state.items() if st["alerting"]] + (["fps"] if any(st["alerting"] for st in self.fps_state.values()) else []), last=last)
