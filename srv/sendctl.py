
import json
import os
import subprocess
import time
import pipbox_live

from srv.netinfo import net_conflict
from srv.pipeline import PipelineStore
from srv.plugin import SWAP_SELECT, SWAP_STATE, VIEW_FILE, VIEW_STATE
from srv.srtla import PENDING_LABELS, srtla_signature
from srv.versions import belacoder_running


class SendControl:
    STATUS = "/run/pipbox-send/status.json"
    SWAP_WAIT = 2.0

    def __init__(self, state_dir, srtla, pipeline, cams, demo=False):
        self.req = os.path.join(state_dir, "send-request")
        self.srtla, self.pipeline, self.cams, self.demo = srtla, pipeline, cams, demo
        self.block_fn = None

    def _active(self):
        if self.demo:
            return False
        hit = getattr(self, "_act", None)
        if hit is not None and time.monotonic() - hit[0] < 1.5:
            return hit[1]
        try:
            r = subprocess.run(["systemctl", "is-active", "pipbox-send.service"],
                               capture_output=True, text=True, timeout=4)
            val = r.stdout.strip() in ("active", "activating", "deactivating")
        except (OSError, subprocess.TimeoutExpired):
            try:
                with open(self.STATUS) as f:
                    st = json.load(f)
                val = time.time() - float(st.get("time", 0)) < 15 and st.get("state") not in ("stopping", "refused")
            except (OSError, ValueError, TypeError):
                val = False
        self._act = (time.monotonic(), val)
        return val

    def _detail(self):
        try:
            with open(self.STATUS) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def measured_fps(self):
        if self.demo:
            return {}
        d = self._detail()
        try:
            if time.time() - float(d.get("time", 0)) > 15:
                return {}
            return {k: float(v) for k, v in ((d.get("failover") or {}).get("fps") or {}).items() if isinstance(k, str) and isinstance(v, (int, float)) and v > 0}
        except (TypeError, ValueError):
            return {}

    def measured_rates(self):
        if self.demo:
            return {}
        d = self._detail()
        try:
            if time.time() - float(d.get("time", 0)) > 15:
                return {}
            return {k: (float(v[0]), float(v[1])) for k, v in ((d.get("failover") or {}).get("rates") or {}).items()
                    if isinstance(k, str) and isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v)}
        except (TypeError, ValueError):
            return {}

    def live_fast(self):
        if self.demo:
            return False
        st = self._detail()
        try:
            return time.time() - float(st.get("time", 0)) < 15 and st.get("state") not in ("stopping", "refused")
        except (TypeError, ValueError):
            return False

    def picture(self):
        if not self._active():
            return None
        fo = self._detail().get("failover")
        if not fo:
            return None
        layout, wait = set(fo.get("layout") or []), fo.get("wait") or {}
        out = {}
        for k in fo.get("configured") or []:
            if k in layout:
                out[k] = ("an", 0)
            elif k in wait:
                out[k] = ("wartet", int(wait[k]))
            else:
                out[k] = ("aus", 0)
        return out

    def reasons(self):
        r = []
        if self.block_fn:
            try:
                blocked = self.block_fn()
            except Exception:
                blocked = None
            if blocked:
                r.append(blocked)
        pub = self.srtla.public()
        if not pub["selected"]:
            r.append("Kein SRTLA-Server ausgewählt")
        cfg = self.pipeline.cfg
        if not cfg.get("main"):
            r.append("Kein Bildaufbau gespeichert")
        else:
            live = {c["key"]: c.get("state") for c in self.cams.listing("")}
            keys = [k for k in (cfg["main"], cfg.get("pip", ""), cfg.get("pip2", ""), cfg.get("pip3", "")) if k]
            if PipelineStore.always_plan(cfg) and PipelineStore.always_plan(cfg).get("live"):
                pass
            elif cfg.get("auto_failover", True) and len(set(keys)) > 1:
                off = set(cfg.get("inactive") or [])
                if not any(live.get(k) == "live" for k in keys if k == cfg["main"] or k not in off):
                    r.append("Es sendet nur eine deaktivierte Kamera" if any(live.get(k) == "live" for k in keys) else "Keine Kamera sendet gerade")
            else:
                for k in sorted(set(keys)):
                    if live.get(k) != "live":
                        r.append(f"Kamera {k} sendet gerade nicht")
        if cfg.get("type") == "pip" and not os.path.exists("/opt/pipbox/gst/libgstpbpip.so"):
            r.append("Bild-in-Bild: der Überlagerungs-Baustein ist noch nicht installiert")
        return r

    def status(self):
        active = self._active()
        d = self._detail()
        pub = self.srtla.public()
        sel = next((s for s in pub["servers"] if s["id"] == pub["selected"]), None)
        state = d.get("state") if active else ("refused" if d.get("state") == "refused" else "stopped")
        out = {"active": active, "state": state or "stopped", "message": d.get("message", ""), "last": d.get("last", ""), "last_age": d.get("last_age"),
               "since": d.get("since"), "restarts": d.get("restarts"), "delay_live": bool(d.get("delay_live")), "delay_live_pips": bool(d.get("delay_live_pips")),
               "server": sel and {"name": sel["name"], "host": sel["host"], "port": sel["port"]}}
        out["failover"] = d.get("failover") if active else None
        fc, fmain = self.pipeline.cfg.get("facecam"), ((d.get("failover") or {}).get("main") if active else None)
        out["facecam_main"] = bool(fc and fmain == fc and PipelineStore.datenschutz())
        out["view_live"], out["audio_live"], out["view"] = self.view_live(), self.audio_live(), self.view_state()
        out["footer"] = self.footer()
        sw = d.get("swap") if active else None
        out["swap_group"] = [k for k in sw["cams"][:int(sw.get("group", 0))] if isinstance(k, str)] if isinstance(sw, dict) and isinstance(sw.get("cams"), list) else []
        applied = d.get("applied") if active else None
        cur = srtla_signature(self.srtla.data)
        out["pending"] = [lab for k, lab in PENDING_LABELS if isinstance(applied, dict) and k in applied and applied[k] != cur[k]]
        running = d.get("buffer_ms") if active else None
        if isinstance(running, int) and not isinstance(running, bool):
            need = pipbox_live.buffer_need_ms(PipelineStore._safe_cfg(self.pipeline.cfg))
            if pipbox_live.buffer_mismatch(running, need):
                out["pending"].append("Verzögerung")
        out["reasons"] = [] if active else self.reasons()
        if not active and belacoder_running():
            out["reasons"].append("Es läuft schon ein belacoder (z. B. über die BELABOX-Oberfläche)")
        out["no_layout"] = not active and not self.pipeline.cfg.get("main")
        out["no_server"] = not active and not pub["selected"]
        out["net_conflict"] = net_conflict()
        out["can_start"] = not active and not out["reasons"]
        return out

    def request(self, action, confirm):
        if action == "start":
            if not confirm:
                raise ValueError("Bestätigung fehlt")
            st = self.status()
            if st["active"]:
                raise ValueError("Es wird schon gesendet")
            if not st["can_start"]:
                raise ValueError("Senden nicht möglich: " + "; ".join(st["reasons"]))
        elif action == "restart":
            why = [] if self.demo else self.reasons()
            if why:
                raise ValueError("Neustart nicht möglich: " + "; ".join(why))
        elif action != "stop":
            raise ValueError("Unbekannte Aktion")
        if action == "start" and not self.demo:
            self.reset_mute()
        self._act = None
        if self.demo:
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(action + "\n")


    def delay_live(self):
        d = self._detail()
        return bool(self._active() and d.get("delay_live") and not (d.get("failover") or {}).get("degraded"))

    def delay_live_pips(self):
        d = self._detail()
        return bool(self._active() and d.get("delay_live_pips") and not (d.get("failover") or {}).get("degraded"))

    def swap_live(self):
        if self.demo or not self._active():
            return False
        d = self._detail()
        sw = d.get("swap")
        if not isinstance(sw, dict) or not isinstance(sw.get("cams"), list) or (d.get("failover") or {}).get("degraded"):
            return False
        cfg = self.pipeline.cfg
        slots = [cfg.get(k) for k in ("main", "pip", "pip2", "pip3")]
        try:
            line = PipelineStore.swap_line(slots, sw["cams"], int(sw.get("group", 0)))
            if line is None:
                return False
            live = {c["key"]: c.get("state") for c in self.cams.listing("")}
            if live.get(cfg.get("main")) != "live":
                return False
        except (TypeError, ValueError, KeyError):
            return False
        folder = os.path.dirname(self.req)
        tmp = os.path.join(folder, SWAP_SELECT + ".tmp")
        t0 = time.time()
        try:
            with open(tmp, "w") as f:
                f.write(line + "\n")
            os.chmod(tmp, 0o644)
            os.replace(tmp, os.path.join(folder, SWAP_SELECT))
        except OSError:
            return False
        end = time.monotonic() + self.SWAP_WAIT
        while time.monotonic() < end:
            try:
                if os.stat(SWAP_STATE).st_mtime >= t0 - 0.2:
                    with open(SWAP_STATE) as f:
                        if f.read().split() == line.split():
                            return True
            except OSError:
                pass
            time.sleep(0.1)
        return False

    def reset_mute(self):
        folder = os.path.dirname(self.req)
        try:
            tmp = os.path.join(folder, VIEW_FILE + ".tmp")
            with open(tmp, "w") as f:
                f.write(PipelineStore.view_line(self.pipeline.cfg, 0) + "\n")
            os.chmod(tmp, 0o644)
            os.replace(tmp, os.path.join(folder, VIEW_FILE))
        except OSError:
            pass

    def view_live(self):
        if self.demo:
            return True
        d = self._detail()
        return bool(self._active() and d.get("view_live") and not (d.get("failover") or {}).get("degraded"))

    def always_live(self):
        if self.demo:
            return False
        return bool(self._active() and self._detail().get("always"))

    def view_degraded(self):
        if self.demo:
            return False
        return bool(self._active() and (self._detail().get("failover") or {}).get("degraded"))

    def audio_live(self):
        return True if self.demo else bool(self.view_live() and self._detail().get("audio_live"))

    def view_state(self):
        if self.demo:
            return getattr(self, "_fake_view", None)
        if not self._active():
            return None
        try:
            hide, audio, mute = (int(x) for x in open(VIEW_STATE).read().split()[:3])
        except (OSError, ValueError):
            return None
        return {"hide": hide, "audio": audio, "mute": bool(mute)}

    def footer(self):
        c = PipelineStore._safe_cfg(self.pipeline.cfg)
        pip, pip2, pip3, _ = PipelineStore._layout(c)
        if c["type"] != "pip" or not c.get("main") or not pip:
            return None
        order = ("main", "pip", "pip2", "pip3")
        place = {"main": c["main"], "pip": c["pip"], "pip2": c["pip2"] if pip2 else "", "pip3": c["pip3"] if pip3 else ""}
        st = self.view_state() if self.view_live() else None
        if st:
            hide, apos, mute = st["hide"], st["audio"], bool(st["mute"])
        else:
            (hide, apos), mute = PipelineStore.view_values(c), False
        src = {v: k for k, v in PipelineStore.AUDIO_POS.items()}.get(apos, "main")
        if not place.get(src) or (src != "main" and place[src] in (c.get("inactive") or [])):
            src = "main"
        slot_of = {}
        for i, k in enumerate(order):
            if place[k]:
                slot_of.setdefault(place[k], i)
        listed = {x["key"]: x for x in self.cams.listing("")}
        keys = [k for k in listed if k in slot_of] + [k for k in slot_of if k not in listed]
        cams = [{"key": k, "name": (listed.get(k) or {}).get("name") or k, "state": (listed.get(k) or {}).get("state", "unknown"),
                 "slot": slot_of[k], "main": slot_of[k] == 0, "hidden": slot_of[k] > 0 and bool(hide >> (slot_of[k] - 1) & 1),
                 "inactive": slot_of[k] > 0 and k in (c.get("inactive") or []), "face": bool(c.get("facecam")) and k == c.get("facecam")} for k in keys]
        off = set(c.get("inactive") or [])
        options = [k for k in order if place[k] and (k == "main" or place[k] not in off)]
        starting = self._detail().get("state") in ("starting", "restarting")
        if self.always_live() and ((self._detail().get("failover") or {}).get("live") or not starting):
            fo = self._detail().get("failover") or {}
            live = set(fo.get("live") or [])
            effmain = fo.get("main")
            cams = [dict(x, main=(x["key"] == effmain) if effmain else x["main"]) for x in cams if x["key"] in live or x["key"] == effmain]
            options = [k for k in options if place[k] in live or place[k] == effmain]
            if place.get(src) not in live and options:
                src = options[0]
            if src not in options:
                options = [src]
        aud_key = place[src]
        nxt = options[(options.index(src) + 1) % len(options)] if src in options else (options[0] if options else "main")
        return {"cams": cams,
                "audio": {"src": src, "key": aud_key, "name": (listed.get(aud_key) or {}).get("name") or aud_key, "mute": mute,
                          "next": nxt, "face": bool(c.get("facecam")) and aud_key == c.get("facecam")}}

    def apply_view(self, hide, audio, mute):
        line = f"{int(hide)} {int(audio)} {1 if mute else 0}"
        if self.demo:
            self._fake_view = {"hide": int(hide), "audio": int(audio), "mute": bool(mute)}
            return True
        if not self._active():
            return False
        folder = os.path.dirname(self.req)
        tmp = os.path.join(folder, VIEW_FILE + ".tmp")
        t0 = time.time()
        try:
            with open(tmp, "w") as f:
                f.write(line + "\n")
            os.chmod(tmp, 0o644)
            os.replace(tmp, os.path.join(folder, VIEW_FILE))
        except OSError:
            return False
        end = time.monotonic() + self.SWAP_WAIT
        while time.monotonic() < end:
            try:
                if os.stat(VIEW_STATE).st_mtime >= t0 - 0.2:
                    with open(VIEW_STATE) as f:
                        if f.read().split() == line.split():
                            print(f"Ansicht live umgestellt ({line}), bestätigt nach {time.time() - t0:.2f} s", flush=True)
                            return True
            except OSError:
                pass
            time.sleep(0.1)
        print(f"Ansicht ({line}) nicht bestätigt, Neustart der Sendung folgt", flush=True)
        return False

    def change_view(self, visible=None, audio=None, mute=None):
        if mute is not None and not isinstance(mute, bool):
            raise ValueError("Stumm: ja oder nein")
        if visible is None and audio is None and mute is None:
            raise ValueError("Nichts zu ändern")
        active = self._active() if not self.demo else True
        if mute is not None and not active:
            raise ValueError("Stumm schalten geht nur während der Sendung")
        before = self.view_state()
        old_hide, old_audio = PipelineStore.view_values(self.pipeline.cfg)
        hide, aud = self.pipeline.set_view(visible, audio) if (visible is not None or audio is not None) else (old_hide, old_audio)
        cur_mute = bool(before["mute"]) if before else False
        new_mute = cur_mute if mute is None else mute
        if not active:
            return {"live": False, "restarted": False, "note": "Gespeichert. Gilt ab dem nächsten Start der Sendung.", "view": None}
        needs_audio = audio is not None and aud != (before["audio"] if before else old_audio)
        if self.view_live() and (not needs_audio or self.audio_live() or self.demo):
            if self.apply_view(hide, aud, new_mute):
                return {"live": True, "restarted": False, "note": "", "view": self.view_state()}
        if self.view_degraded():
            print("Ansicht im Notbetrieb gespeichert, kein Neustart", flush=True)
            if mute is not None and visible is None and audio is None:
                raise ValueError("Stumm schalten geht im Notbetrieb nicht. Es gilt wieder, sobald alle Kameras da sind.")
            return {"live": False, "restarted": False, "note": "Gespeichert. Gilt, sobald alle Kameras wieder da sind (Notbetrieb: ohne Neustart).", "view": None}
        if mute is not None and visible is None and audio is None:
            raise ValueError("Stumm schalten ging gerade nicht (die Sendekette kennt das noch nicht). Bitte die Sendung einmal neu starten.")
        restarted, note = self.restart_if_live()
        return {"live": False, "restarted": restarted, "note": note, "view": None}

    def restart_if_live(self):
        if not self._active():
            return False, ""
        why = self.reasons()
        if why:
            return False, "Gespeichert, aber die laufende Übertragung wurde nicht neu gestartet: " + "; ".join(why)
        self.request("restart", True)
        return True, "Gespeichert. Die Übertragung wird jetzt kurz neu gestartet."
